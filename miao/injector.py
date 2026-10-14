"""通用焦点窗口文本注入。

流程（对齐原项目 CodexSubmitter 的安全边界）：
1. 快照剪贴板 → 2. 写入转写文本 → 3. SendInput 发 Ctrl+V → 4. 可选回车 Return
→ 5. 0.35s 后若剪贴板未被用户改动则恢复快照。

空文本不发送；submit=False 时只复制不按键；press_enter=False 时粘贴后不回车。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
import time

import win32clipboard
import win32con

log = logging.getLogger("miao.inject")

VK_CONTROL = 0x11
VK_V = 0x56
VK_RETURN = 0x0D
KEYEVENTF_KEYUP = 0x0002

PASTE_DELAY = 0.2
RESTORE_DELAY = 0.35


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUT_UNION(ctypes.Union):
    # 必须包含 MOUSEINPUT，否则结构体小于系统期望的 40 字节，SendInput 会静默失败
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUT_UNION)]


def _send_key(vk: int, key_up: bool = False) -> None:
    extra = ctypes.c_ulong(0)
    event = _INPUT(
        type=1,  # INPUT_KEYBOARD
        union=_INPUT_UNION(
            ki=_KEYBDINPUT(
                wVk=vk,
                wScan=0,
                dwFlags=KEYEVENTF_KEYUP if key_up else 0,
                time=0,
                dwExtraInfo=ctypes.pointer(extra),
            )
        ),
    )
    ctypes.windll.user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(event))


def send_keystroke(vk: int, *modifiers: int) -> None:
    """按下修饰键 → 按目标键 → 逆序全部释放。"""
    try:
        for modifier in modifiers:
            _send_key(modifier)
        _send_key(vk)
        _send_key(vk, key_up=True)
    finally:
        for modifier in reversed(modifiers):
            _send_key(modifier, key_up=True)


def read_clipboard_text() -> str | None:
    try:
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            return None
        finally:
            win32clipboard.CloseClipboard()
    except Exception:  # noqa: BLE001 - 剪贴板可能被其他程序短暂占用
        return None


def write_clipboard_text(text: str) -> None:
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


def should_restore_clipboard(expected: str | None, current: str | None) -> bool:
    """只有剪贴板仍是我们写入的内容时才恢复（对齐 PasteboardRestorePolicy）。"""
    return expected == current


def inject_text(text: str, submit: bool = True, press_enter: bool = True) -> bool:
    """把文本注入当前焦点窗口。返回是否执行了按键发送。

    press_enter=False 时只粘贴不回车，便于用户在输入框中修改后再发送。
    """
    if not text.strip():
        log.info("转写为空，不发送")
        return False

    snapshot = read_clipboard_text()
    write_clipboard_text(text)
    if not submit:
        log.info("no-submit 模式：仅复制到剪贴板")
        return False

    send_keystroke(VK_V, VK_CONTROL)
    if press_enter:
        time.sleep(PASTE_DELAY)
        send_keystroke(VK_RETURN)
    time.sleep(RESTORE_DELAY)

    if should_restore_clipboard(text, read_clipboard_text()):
        try:
            if snapshot is not None:
                write_clipboard_text(snapshot)
            else:
                win32clipboard.OpenClipboard()
                try:
                    win32clipboard.EmptyClipboard()
                finally:
                    win32clipboard.CloseClipboard()
        except Exception:  # noqa: BLE001
            log.warning("剪贴板快照恢复失败", exc_info=True)
    return True
