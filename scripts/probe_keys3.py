"""只读探针 v3：普通隐藏窗口 + 多页 Raw Input + 低级钩子，专门排查"不发键盘事件的键"。

v2 用了 message-only 窗口，收不到 WM_INPUT。v3 改用普通隐藏窗口，并把每个 Raw Input
事件的设备名 / VKey / MakeCode 全打出来，用于确认 返回键 与 音量± 是否走消费类页。

用法：py -X utf8 scripts/probe_keys3.py [秒数]
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import sys
import threading
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from miao.keyhook import (  # noqa: E402
    KBDLLHOOKSTRUCT,
    LLKHF_UP,
    RAWINPUT,
    RAWINPUTDEVICE,
    RIDI_DEVICENAME,
    RIDEV_INPUTSINK,
    RIM_TYPEKEYBOARD,
    WH_KEYBOARD_LL,
    kernel32,
    user32,
)

WM_INPUT = 0x00FF
RID_INPUT = 0x10000003

_WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t
)
_LLKP = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t
)

user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
]
kernel32.GetModuleHandleW.restype = ctypes.c_void_p


def device_name(handle) -> str:
    length = wintypes.UINT(0)
    user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICENAME, None, ctypes.byref(length))
    if not length.value:
        return ""
    buffer = ctypes.create_unicode_buffer(length.value)
    user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICENAME, buffer, ctypes.byref(length))
    return buffer.value


def is_remote(name: str) -> bool:
    return "2717" in name.upper()


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.c_void_p), ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]


def _wnd_proc(hwnd, message, wparam, lparam):
    if message == WM_INPUT:
        size = wintypes.UINT(ctypes.sizeof(RAWINPUT))
        raw = RAWINPUT()
        result = user32.GetRawInputData(
            ctypes.c_void_p(lparam), RID_INPUT,
            ctypes.byref(raw), ctypes.byref(size), ctypes.sizeof(raw.header),
        )
        if result != 0xFFFFFFFF and raw.header.dwType == RIM_TYPEKEYBOARD:
            name = device_name(raw.header.hDevice)
            tag = "遥控器" if is_remote(name) else "其他设备"
            print(
                f"[{time.strftime('%H:%M:%S')}] RAW  {tag} "
                f"VK=0x{raw.keyboard.VKey:02X} MakeCode=0x{raw.keyboard.MakeCode:02X} "
                f"Flags=0x{raw.keyboard.Flags:02X} Msg=0x{raw.keyboard.Message:04X}",
                flush=True,
            )
        return 0
    return user32.DefWindowProcW(hwnd, message, wparam, lparam)


def _hook_callback(ncode, wparam, lparam):
    if ncode == 0:
        kb = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        up = bool(kb.flags & LLKHF_UP)
        print(
            f"[{time.strftime('%H:%M:%S')}] HOOK {'up  ' if up else 'down'} "
            f"VK=0x{kb.vkCode:02X} scan=0x{kb.scanCode:02X}",
            flush=True,
        )
    return user32.CallNextHookEx(None, ncode, wparam, lparam)


def main() -> None:
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 120

    wndclass = WNDCLASSW()
    proc_ref = _WNDPROC(_wnd_proc)
    wndclass.lpfnWndProc = ctypes.cast(proc_ref, ctypes.c_void_p)
    wndclass.lpszClassName = "MiAoKeyProbe3"
    wndclass.hInstance = kernel32.GetModuleHandleW(None)
    if not user32.RegisterClassW(ctypes.cast(ctypes.byref(wndclass), ctypes.c_void_p)):
        if kernel32.GetLastError() != 1410:
            raise RuntimeError(f"RegisterClassW 失败：{kernel32.GetLastError()}")

    # 普通隐藏窗口（不是 message-only），Raw Input 才能投递 WM_INPUT
    hwnd = user32.CreateWindowExW(
        0, wndclass.lpszClassName, "MiAoKeyProbe3", 0, 0, 0, 0, 0,
        None, None, wndclass.hInstance, None,
    )
    if not hwnd:
        raise RuntimeError(f"CreateWindowExW 失败：{kernel32.GetLastError()}")

    devices = [
        RAWINPUTDEVICE(0x01, 0x06, RIDEV_INPUTSINK, hwnd),  # 键盘
        RAWINPUTDEVICE(0x01, 0x80, RIDEV_INPUTSINK, hwnd),  # 系统控制
        RAWINPUTDEVICE(0x0C, 0x01, RIDEV_INPUTSINK, hwnd),  # 消费类控制
    ]
    if not user32.RegisterRawInputDevices(
        ctypes.byref((RAWINPUTDEVICE * len(devices))(*devices)),
        len(devices), ctypes.sizeof(RAWINPUTDEVICE),
    ):
        raise RuntimeError(f"RegisterRawInputDevices 失败：{kernel32.GetLastError()}")

    hook_proc = _LLKP(_hook_callback)
    hook = user32.SetWindowsHookExW(
        WH_KEYBOARD_LL, ctypes.cast(hook_proc, ctypes.c_void_p), None, 0
    )
    if not hook:
        raise RuntimeError(f"SetWindowsHookExW 失败：{kernel32.GetLastError()}")

    print(f"探针 v3 已启动（观察模式），{seconds} 秒后自动结束。", flush=True)
    print("请依次按：返回(←) → 音量+ → 音量- → 电源，每个停 2 秒。", flush=True)
    threading.Timer(seconds, user32.PostMessageW, args=(hwnd, 0x0012, 0, 0)).start()

    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))

    user32.UnhookWindowsHookEx(hook)
    print("结束。", flush=True)


if __name__ == "__main__":
    main()
