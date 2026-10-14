"""只读探针：打印所有键盘来源的 Raw Input（含设备名 + VK 码），不拦截任何按键。

用来真机校准遥控器每个按键的 VK 码、确认遥控器的 HID 设备名（VID/PID）。
用法：py -X utf8 scripts/probe_keys.py
按 Ctrl+C 退出。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import sys
import threading
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from miao.keyhook import (  # noqa: E402
    RAWINPUT,
    RAWINPUTDEVICE,
    RIDI_DEVICENAME,
    RIDEV_INPUTSINK,
    RIM_TYPEKEYBOARD,
    kernel32,
    user32,
)

WM_INPUT = 0x00FF
_WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t
)

user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
]
kernel32.GetModuleHandleW.restype = ctypes.c_void_p

seen: dict[tuple[str, int], int] = {}


def device_name(handle) -> str:
    length = wintypes.UINT(0)
    user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICENAME, None, ctypes.byref(length))
    if not length.value:
        return ""
    buffer = ctypes.create_unicode_buffer(length.value)
    user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICENAME, buffer, ctypes.byref(length))
    return buffer.value


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


def main() -> None:
    wndclass = WNDCLASSW()
    proc_ref = _WNDPROC(lambda hwnd, msg, wp, lp: _wnd_proc(hwnd, msg, wp, lp))
    wndclass.lpfnWndProc = ctypes.cast(proc_ref, ctypes.c_void_p)
    wndclass.lpszClassName = "MiAoKeyProbe"
    wndclass.hInstance = kernel32.GetModuleHandleW(None)
    if not user32.RegisterClassW(ctypes.cast(ctypes.byref(wndclass), ctypes.c_void_p)):
        error = kernel32.GetLastError()
        if error != 1410:
            raise RuntimeError(f"RegisterClassW 失败：{error}")

    hwnd = user32.CreateWindowExW(
        0, wndclass.lpszClassName, "MiAoKeyProbe", 0, 0, 0, 0, 0,
        ctypes.c_void_p(-3), None, wndclass.hInstance, None,
    )
    if not hwnd:
        raise RuntimeError(f"CreateWindowExW 失败：{kernel32.GetLastError()}")

    device = RAWINPUTDEVICE(
        usUsagePage=0x01, usUsage=0x06, dwFlags=RIDEV_INPUTSINK, hwndTarget=hwnd
    )
    if not user32.RegisterRawInputDevices(
        ctypes.byref(device), 1, ctypes.sizeof(RAWINPUTDEVICE)
    ):
        raise RuntimeError("RegisterRawInputDevices 失败")

    seconds = 180
    if len(sys.argv) > 1:
        seconds = int(sys.argv[1])

    print(f"探针已启动（只监听不拦截），{seconds} 秒后自动结束并汇总。", flush=True)
    print("请依次按下遥控器每个键，每次按一下、间隔 1 秒。", flush=True)
    threading.Timer(seconds, user32.PostMessageW, args=(hwnd, 0x0012, 0, 0)).start()

    msg = wintypes.MSG()
    try:
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    except KeyboardInterrupt:
        pass

    print("\n=== 汇总（设备名 / VK / 按下次数）===", flush=True)
    for (name, vk), count in sorted(seen.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        print(f"  {name}\n      VK=0x{vk:02X} ({vk})  按下 {count} 次", flush=True)
    if not seen:
        print("  （没有捕获到任何按键）", flush=True)


def _wnd_proc(hwnd, message, wparam, lparam):
    if message == WM_INPUT:
        size = wintypes.UINT(ctypes.sizeof(RAWINPUT))
        raw = RAWINPUT()
        result = user32.GetRawInputData(
            ctypes.c_void_p(lparam), 0x10000003,
            ctypes.byref(raw), ctypes.byref(size), ctypes.sizeof(raw.header),
        )
        if result != 0xFFFFFFFF and raw.header.dwType == RIM_TYPEKEYBOARD:
            name = device_name(raw.header.hDevice)
            vk = raw.keyboard.VKey
            is_up = bool(raw.keyboard.Flags & 0x01)  # RI_KEY_BREAK
            mark = "up  " if is_up else "down"
            print(f"[{time.strftime('%H:%M:%S')}] {mark} VK=0x{vk:02X} MakeCode=0x{raw.keyboard.MakeCode:02X}  {name}",
                  flush=True)
            if not is_up:
                key = (name, vk)
                seen[key] = seen.get(key, 0) + 1
        return 0
    return user32.DefWindowProcW(hwnd, message, wparam, lparam)


if __name__ == "__main__":
    main()
