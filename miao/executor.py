"""Windows 按键执行器：SendInput 键鼠注入 + 指针持续移动 + 目标窗口聚焦。"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
import subprocess
import threading

from .dispatcher import ControlMode

log = logging.getLogger("miao.executor")

POINTER_STEP_PX = 14
POINTER_INTERVAL = 0.016  # 60Hz

WHEEL_DELTA = 120


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUT_UNION)]


def _send(inputs: list[_INPUT]) -> None:
    array = (_INPUT * len(inputs))(*inputs)
    sent = ctypes.windll.user32.SendInput(len(inputs), array, ctypes.sizeof(_INPUT))
    if sent != len(inputs):
        log.warning("Windows 未完整接受模拟按键：%d/%d，error=%d", sent, len(inputs), ctypes.windll.kernel32.GetLastError())


def _key_input(vk: int, key_up: bool) -> _INPUT:
    mapped = ctypes.windll.user32.MapVirtualKeyW(vk, 4)
    scan = mapped & 0xFF
    extended = (mapped >> 8) in (0xE0, 0xE1) or vk in (0xA5, 0xA3, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x6F)
    flags = (0x0002 if key_up else 0) | (0x0001 if extended else 0) | (0x0008 if scan else 0)
    return _INPUT(
        type=1,
        union=_INPUT_UNION(
            ki=_KEYBDINPUT(0 if scan else vk, scan, flags, 0, 0)
        ),
    )


class WindowsExecutor:
    """dispatcher.Executor 的 Windows 实现。"""

    def __init__(
        self,
        target_window_title: str = "",
        target_launch_command: str = "",
        previous_task_shortcut: tuple[int, tuple[int, ...]] = (0x09, (0x11, 0x10)),  # Ctrl+Shift+Tab
        next_task_shortcut: tuple[int, tuple[int, ...]] = (0x09, (0x11,)),  # Ctrl+Tab
        submit_text: bool = False,
        press_enter: bool = False,
        on_status=None,
        shortcut_bridge=None,
    ) -> None:
        self.target_window_title = target_window_title
        self.target_launch_command = target_launch_command
        self._prev = previous_task_shortcut
        self._next = next_task_shortcut
        self._submit_text = submit_text
        self._press_enter = press_enter
        self._on_status = on_status or (lambda text: None)
        self._shortcut_bridge = shortcut_bridge or (lambda phase, vk, modifiers: False)
        self._move_direction: tuple[int, int] | None = None
        self._move_lock = threading.Lock()
        self._move_thread: threading.Thread | None = None
        self._move_stop = threading.Event()

    # -- 键盘 ---------------------------------------------------------------

    def tap_key(self, vk: int) -> None:
        _send([_key_input(vk, False), _key_input(vk, True)])

    def press_shortcut(self, vk: int, modifiers: tuple[int, ...]) -> None:
        if self._shortcut_bridge("tap", vk, modifiers):
            return
        events = [_key_input(m, False) for m in modifiers]
        events += [_key_input(vk, False), _key_input(vk, True)]
        events += [_key_input(m, True) for m in reversed(modifiers)]
        _send(events)

    def keys_down(self, vk: int, modifiers: tuple[int, ...] = ()) -> None:
        """只按下（不抬起）：用于「按住说话」这类需要持续按住的快捷键。"""
        if self._shortcut_bridge("down", vk, modifiers):
            return
        _send([_key_input(m, False) for m in modifiers] + [_key_input(vk, False)])

    def keys_up(self, vk: int, modifiers: tuple[int, ...] = ()) -> None:
        """释放之前按下的组合（逆序释放修饰键）。"""
        if self._shortcut_bridge("up", vk, modifiers):
            return
        _send([_key_input(vk, True)] + [_key_input(m, True) for m in reversed(modifiers)])

    def launch(self, program: str, arguments: tuple[str, ...] = ()) -> None:
        """启动程序（不经过 shell，参数原样传递）。"""
        if not program.strip():
            log.warning("未配置要启动的程序")
            return
        try:
            subprocess.Popen([program, *arguments])
            self._on_status(f"已启动 {program}")
        except OSError as error:
            log.warning("启动程序失败：%s", error)
            self._on_status(f"启动失败：{error}")

    def type_text(self, text: str) -> None:
        """把文本注入当前焦点窗口（复用剪贴板 + Ctrl+V 注入器）。"""
        from .injector import inject_text

        inject_text(text, submit=self._submit_text, press_enter=self._press_enter)

    # -- 鼠标 ---------------------------------------------------------------

    def start_pointer_move(self, dx: int, dy: int) -> None:
        with self._move_lock:
            if self._move_direction == (dx, dy):
                return
            self._stop_move_locked()
            self._move_direction = (dx, dy)
            # A new worker must never clear the previous worker's stop signal.
            self._move_stop = threading.Event()
            self._move_thread = threading.Thread(
                target=self._move_loop, args=(self._move_stop,), name="miao-pointer", daemon=True
            )
            self._move_thread.start()
        label = {(0, -1): "上", (0, 1): "下", (-1, 0): "左", (1, 0): "右"}[(dx, dy)]
        self._on_status(f"移动鼠标 · {label}")

    def stop_pointer_move(self) -> None:
        with self._move_lock:
            self._stop_move_locked()

    def _stop_move_locked(self) -> None:
        self._move_direction = None
        self._move_stop.set()

    def _move_loop(self, stop: threading.Event) -> None:
        while not stop.wait(POINTER_INTERVAL):
            with self._move_lock:
                if stop is not self._move_stop or stop.is_set():
                    return
                direction = self._move_direction
            if direction is None:
                return
            dx, dy = direction[0] * POINTER_STEP_PX, direction[1] * POINTER_STEP_PX
            extra = ctypes.c_ulong(0)
            event = _INPUT(
                type=0,
                union=_INPUT_UNION(
                    mi=_MOUSEINPUT(dx, dy, 0, 0x0001, 0, ctypes.pointer(extra))  # MOUSEEVENTF_MOVE
                ),
            )
            _send([event])

    def scroll(self, amount: int) -> None:
        extra = ctypes.c_ulong(0)
        event = _INPUT(
            type=0,
            union=_INPUT_UNION(
                mi=_MOUSEINPUT(0, 0, WHEEL_DELTA * amount, 0x0800, 0, ctypes.pointer(extra))
            ),
        )
        _send([event])

    # -- 目标窗口 -------------------------------------------------------------

    def focus_target(self) -> None:
        if self.target_window_title and _focus_window(self.target_window_title):
            return
        log.info("未配置目标窗口或未找到匹配窗口")

    def launch_or_focus_target(self) -> None:
        if self.target_window_title and _focus_window(self.target_window_title):
            return
        if self.target_launch_command:
            subprocess.Popen(self.target_launch_command, shell=True)

    def previous_task(self) -> None:
        vk, modifiers = self._prev
        self.press_shortcut(vk, modifiers)

    def next_task(self) -> None:
        vk, modifiers = self._next
        self.press_shortcut(vk, modifiers)

    # -- 状态回调 -------------------------------------------------------------

    def on_mode_changed(self, mode: ControlMode) -> None:
        label = "移动指针" if mode is ControlMode.POINTER else "方向键"
        self._on_status(f"方向环模式：{label}")

    def on_preset_changed(self, preset) -> None:
        self._on_status(f"按键配置：{preset.name}")


def _focus_window(title_substring: str) -> bool:
    """按标题子串找窗口并置前。返回是否成功。"""
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_proc(hwnd, _):
        if ctypes.windll.user32.IsWindowVisible(hwnd):
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            if length:
                buffer = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buffer, length + 1)
                if title_substring.casefold() in buffer.value.casefold():
                    found.append(hwnd)
        return True

    ctypes.windll.user32.EnumWindows(enum_proc, 0)
    if not found:
        return False
    hwnd = found[0]
    # SetForegroundWindow 常被系统拒绝；先模拟一次 Alt 按下取得前台权限
    _send([_key_input(0x12, False), _key_input(0x12, True)])  # VK_MENU
    ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    return bool(ctypes.windll.user32.SetForegroundWindow(hwnd))
