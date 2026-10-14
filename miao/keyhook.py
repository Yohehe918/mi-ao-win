"""Windows 遥控器按键拦截：Raw Input 设备识别 + WH_KEYBOARD_LL 钩子。

原理（LuaMacros 式）：遥控器是系统 BLE 键盘，按键会直接进入系统。
- RegisterRawInputDevices 订阅原始键盘输入，WM_INPUT 里用
  GetRawInputDeviceInfo(RIDI_DEVICENAME) 识别遥控器（真机设备名里的
  `VID&012717_PID&32b8` 片段，见 _matches_remote）；
- WH_KEYBOARD_LL 低级钩子收到按键时，对照"最近一次 Raw Input 是否来自遥控器"，
  是则吞掉（返回 1）并转交 ButtonDispatcher，否则放行，不影响其他键盘。

注意：承载 Raw Input 的窗口必须是普通窗口，**不能用 message-only 窗口**
（HWND_MESSAGE），否则收不到 WM_INPUT。

真机实测（小米蓝牙语音遥控器 2 Pro / 固件 2671）：
- 可拦截按键：电源(0xFF)、语音(0x74)、方向环(0x25~0x28)、确认(0x0D)、
  HOME(0x24)、菜单(0x5D)、TV(0xC0)；
- **返回键与音量±不产生任何键盘页事件**，无法通过钩子拦截或重映射。

调试：`python -m miao keys` 打印每个按键的真实 vk/scan。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
import queue
import re
import threading
import time

from .buttons import HIDButtonEvent, RemoteButton
from .dispatcher import ButtonDispatcher

log = logging.getLogger("miao.keyhook")

WH_KEYBOARD_LL = 13
WM_INPUT = 0x00FF
WM_TIMER = 0x0113
RIDEV_INPUTSINK = 0x00000100
RIM_TYPEKEYBOARD = 1
RIDI_DEVICENAME = 0x20000007
LLKHF_UP = 0x80
LLKHF_INJECTED = 0x10
HWND_MESSAGE = -3

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# 默认 VK → 按键映射（真机实测于小米蓝牙语音遥控器 2 Pro，固件 2671）
DEFAULT_VK_MAP: dict[int, RemoteButton] = {
    0x26: RemoteButton.DPAD_UP,  # VK_UP      实测
    0x28: RemoteButton.DPAD_DOWN,  # VK_DOWN    实测
    0x25: RemoteButton.DPAD_LEFT,  # VK_LEFT    实测
    0x27: RemoteButton.DPAD_RIGHT,  # VK_RIGHT   实测
    0x0D: RemoteButton.CENTER,  # VK_RETURN  实测
    0x24: RemoteButton.HOME,  # VK_HOME    实测
    0x5D: RemoteButton.MENU,  # VK_SLEEP   实测（☰ 菜单键）
    0xC0: RemoteButton.TV,  # VK_OEM_3   实测
    0xFF: RemoteButton.POWER,  # 无 VK，实测为 0xFF（电源键）
    0x74: RemoteButton.VOICE,  # VK_F5 实测；语音音频仍走 BLE ATVV 链路，这里只吞掉残留按键
    # 实测结论：返回键(←) 与 音量± 不产生任何键盘页事件，无法通过钩子拦截/重映射
}


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    ]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [
        ("MakeCode", wintypes.USHORT),
        ("Flags", wintypes.USHORT),
        ("Reserved", wintypes.USHORT),
        ("VKey", wintypes.USHORT),
        ("Message", wintypes.UINT),
        ("ExtraInformation", wintypes.ULONG),
    ]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("keyboard", RAWKEYBOARD)]


_LLKP = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t
)

user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t]
user32.GetRawInputData.restype = wintypes.UINT
kernel32.GetModuleHandleW.restype = ctypes.c_void_p
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
user32.RegisterClassW.argtypes = [ctypes.c_void_p]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


class RemoteKeyHook:
    """在独立线程运行消息循环；拦截遥控器按键并喂给 ButtonDispatcher。"""

    def __init__(
        self,
        dispatcher: ButtonDispatcher,
        vid: int = 0x2717,
        pid: int = 0x32B8,
        vk_map: dict[int, RemoteButton] | None = None,
        debug_keys: bool = False,
    ) -> None:
        self.dispatcher = dispatcher
        # 真机设备名形如（注意大小写与 &01 来源前缀）：
        # \\?\HID#{00001812-...}_Dev_VID&012717_PID&32b8_REV&00a4_aabbccddeeff#...
        # 所以不能只按 "VID_2717&PID_32B8" 精确匹配，要分别匹配 VID 与 PID。
        self._vid_pattern = re.compile(rf"VID[&_](?:[0-9A-F]{{2}})?{vid:04X}", re.IGNORECASE)
        self._pid_pattern = re.compile(rf"PID[&_](?:[0-9A-F]{{2}})?{pid:04X}", re.IGNORECASE)
        self.device_marker = f"VID&01{vid:04X}_PID&{pid:04x}"
        self.vk_map = dict(vk_map or DEFAULT_VK_MAP)
        self.debug_keys = debug_keys
        self._last_raw: tuple[int, bool, int] | None = None  # (vkey, is_remote, tick)
        self._thread: threading.Thread | None = None
        self._hook = None
        self._hwnd = None
        self._ready = threading.Event()
        self._failed: list[str] = []
        self._extra_events = queue.Queue()
        self.driver_owned = frozenset()
        self._driver_condition = threading.Condition()
        self._driver_edges = {}
        self._native_block_held = set()

    def set_driver_owned(self, connected: bool) -> None:
        with self._driver_condition:
            self.driver_owned = frozenset({RemoteButton.VOICE, RemoteButton.TV}) if connected else frozenset()
            self._driver_edges.clear()
            self._native_block_held.clear()
            self._driver_condition.notify_all()

    def queue_button_event(self, button: RemoteButton, down: bool) -> None:
        """Serialize helper events on the existing input thread."""
        with self._driver_condition:
            if button in self.driver_owned:
                self._driver_edges[button] = (down, time.monotonic())
                self._driver_condition.notify_all()
        self._extra_events.put((button, down))

    def _driver_native_matches(self, button: RemoteButton, down: bool) -> bool:
        """Native HID can precede the authenticated driver report by a few ms.

        Wait at most 30 ms for that independently received edge; no matching
        driver report means normal keyboard passthrough. Held remote repeats
        share the authenticated press state until its release edge arrives.
        """
        began = time.monotonic()
        with self._driver_condition:
            while button in self.driver_owned:
                edge = self._driver_edges.get(button)
                if edge and edge[0] == down and (down or edge[1] >= began - .04):
                    return True
                remaining = .03 - (time.monotonic() - began)
                if remaining <= 0:
                    break
                self._driver_condition.wait(remaining)
        return False

    def _matches_remote(self, name: str) -> bool:
        return bool(self._vid_pattern.search(name) and self._pid_pattern.search(name))

    def start(self, timeout: float = 5.0) -> None:
        self._thread = threading.Thread(target=self._run, name="miao-keyhook", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError(self._failed[0] if self._failed else "按键钩子线程启动超时")
        if self._failed:
            raise RuntimeError(self._failed[0])

    def stop(self) -> None:
        if self._hwnd:
            user32.PostMessageW(self._hwnd, 0x0012, 0, 0)  # WM_QUIT

    # ------------------------------------------------------------------

    def _run(self) -> None:
        try:
            self._setup()
        except Exception as error:  # noqa: BLE001
            self._failed.append(str(error))
            log.error("按键钩子初始化失败：%s", error)
            self._ready.set()
            return
        self._ready.set()

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)

    def _setup(self) -> None:
        wndclass = WNDCLASSW()
        self._wnd_proc_ref = WNDPROC(self._wnd_proc)  # 必须持有引用，防止被 GC
        wndclass.lpfnWndProc = ctypes.cast(self._wnd_proc_ref, ctypes.c_void_p)
        wndclass.lpszClassName = "MiAoKeyHookWindow"
        wndclass.hInstance = kernel32.GetModuleHandleW(None)
        if not user32.RegisterClassW(ctypes.cast(ctypes.byref(wndclass), ctypes.c_void_p)):
            error = kernel32.GetLastError()
            if error != 1410:  # ERROR_CLASS_ALREADY_EXISTS
                raise RuntimeError(f"RegisterClassW 失败：{error}")
        self._hwnd = user32.CreateWindowExW(
            # 必须是普通隐藏窗口：message-only 窗口（HWND_MESSAGE）收不到 WM_INPUT
            0, wndclass.lpszClassName, "MiAoKeyHook", 0, 0, 0, 0, 0,
            None, None, wndclass.hInstance, None,
        )
        if not self._hwnd:
            raise RuntimeError(f"CreateWindowExW 失败：{kernel32.GetLastError()}")

        device = RAWINPUTDEVICE(
            usUsagePage=0x01, usUsage=0x06,  # Generic Desktop / Keyboard
            dwFlags=RIDEV_INPUTSINK, hwndTarget=self._hwnd,
        )
        if not user32.RegisterRawInputDevices(
            ctypes.byref(device), 1, ctypes.sizeof(RAWINPUTDEVICE)
        ):
            raise RuntimeError("RegisterRawInputDevices 失败")

        self._hook_proc = _LLKP(self._hook_callback)
        self._hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, ctypes.cast(self._hook_proc, ctypes.c_void_p), None, 0
        )
        if not self._hook:
            raise RuntimeError("SetWindowsHookExW 失败")

        user32.SetTimer(self._hwnd, 1, 50, None)  # dispatcher.tick（HOME 双击判定）
        log.info("按键钩子已安装，监听 %s", self.device_marker)

    def _wnd_proc(self, hwnd, message, wparam, lparam):
        if message == WM_INPUT:
            self._on_raw_input(lparam)
            return 0
        if message == WM_TIMER:
            while True:
                try:
                    button, down = self._extra_events.get_nowait()
                except queue.Empty:
                    break
                if down:
                    self.dispatcher.button_down(button)
                else:
                    self.dispatcher.button_up(button)
            self.dispatcher.tick()
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _on_raw_input(self, hrawinput) -> None:
        size = wintypes.UINT(ctypes.sizeof(RAWINPUT))
        raw = RAWINPUT()
        result = user32.GetRawInputData(
            ctypes.c_void_p(hrawinput), 0x10000003,  # RID_INPUT
            ctypes.byref(raw), ctypes.byref(size), ctypes.sizeof(RAWINPUTHEADER),
        )
        if result == 0xFFFFFFFF or raw.header.dwType != RIM_TYPEKEYBOARD:
            return
        is_remote = self._matches_remote(self._device_name(raw.header.hDevice))
        self._last_raw = (raw.keyboard.VKey, is_remote, kernel32.GetTickCount())

    @staticmethod
    def _device_name(handle) -> str:
        length = wintypes.UINT(0)
        user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICENAME, None, ctypes.byref(length))
        if not length.value:
            return ""
        buffer = ctypes.create_unicode_buffer(length.value)
        user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICENAME, buffer, ctypes.byref(length))
        return buffer.value

    def _hook_callback(self, ncode, wparam, lparam) -> int:
        if ncode == 0:  # HC_ACTION
            kb = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if kb.flags & LLKHF_INJECTED:
                return user32.CallNextHookEx(self._hook, ncode, wparam, lparam)
            button = self.vk_map.get(kb.vkCode)
            if button in self.driver_owned:
                if self._driver_native_matches(button, not bool(kb.flags & LLKHF_UP)):
                    if kb.flags & LLKHF_UP:
                        self._native_block_held.discard(button)
                        log.info("已拦截原生 %s up vk=0x%02X", button.value, kb.vkCode)
                    elif button not in self._native_block_held:
                        self._native_block_held.add(button)
                        log.info("已拦截原生 %s down vk=0x%02X", button.value, kb.vkCode)
                    return 1
                return user32.CallNextHookEx(self._hook, ncode, wparam, lparam)
            if self._is_remote_key(kb.vkCode):
                if self.debug_keys:
                    log.info("遥控器按键 vk=0x%02X scan=0x%02X %s",
                             kb.vkCode, kb.scanCode, "up" if kb.flags & LLKHF_UP else "down")
                button = self.vk_map.get(kb.vkCode)
                if button is not None:
                    if kb.flags & LLKHF_UP:
                        self.dispatcher.button_up(button)
                    else:
                        self.dispatcher.button_down(button)
                return 1  # 吞掉，不传给系统
        return user32.CallNextHookEx(self._hook, ncode, wparam, lparam)

    def _is_remote_key(self, vk_code: int) -> bool:
        """对照最近一次 Raw Input：同 vk、来自遥控器、50ms 内。"""
        if self._last_raw is None:
            return False
        vkey, is_remote, tick = self._last_raw
        if vkey != vk_code or not is_remote:
            return False
        elapsed = (kernel32.GetTickCount() - tick) & 0xFFFFFFFF
        return elapsed < 50


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.c_void_p),
        ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t
)
