"""Per-session UI ownership, with a fixed wake event and no command channel."""
import ctypes as c
from ctypes import wintypes as w

MUTEX_NAME = r"Local\MiAoWin.UI.v1"
EVENT_NAME = r"Local\MiAoWin.Show.v1"


class SingleInstance:
    def __init__(self, name=MUTEX_NAME):
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        k = self.kernel
        k.CreateMutexW.argtypes = [c.c_void_p, w.BOOL, w.LPCWSTR]
        k.CreateMutexW.restype = w.HANDLE
        k.CreateEventW.argtypes = [c.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR]
        k.CreateEventW.restype = w.HANDLE
        k.SetEvent.argtypes = [w.HANDLE]
        k.SetEvent.restype = w.BOOL
        k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        k.WaitForSingleObject.restype = w.DWORD
        k.CloseHandle.argtypes = [w.HANDLE]
        k.CloseHandle.restype = w.BOOL
        self.event = k.CreateEventW(None, False, False, EVENT_NAME if name == MUTEX_NAME else name+".Show")
        if not self.event:
            raise c.WinError(c.get_last_error())
        self.mutex = k.CreateMutexW(None, False, name)
        if not self.mutex:
            k.CloseHandle(self.event)
            raise c.WinError(c.get_last_error())
        self.primary = c.get_last_error() != 183
        if not self.primary:
            k.SetEvent(self.event)

    def consume_show(self):
        return self.primary and bool(self.event) and self.kernel.WaitForSingleObject(self.event, 0) == 0

    def close(self):
        for name in ("mutex", "event"):
            handle = getattr(self, name, None)
            if handle:
                self.kernel.CloseHandle(handle)
                setattr(self, name, None)
