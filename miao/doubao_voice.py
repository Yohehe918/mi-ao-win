"""Control the installed Doubao voice service on a dedicated worker.

The private RPC ABI is restricted to the locally verified release. No Doubao
binary is distributed with MiAo, and no recognition model is loaded here.
"""
from __future__ import annotations

import ctypes
import hashlib
import logging
import os
import queue
import threading
from pathlib import Path

log = logging.getLogger("miao.doubao_voice")
PIPE = br"\\.\pipe\ObricIme\oime-server"
DLL_SHA256 = "e3ee88527bf6bd38156e86b93413ca004a829809ad51f064a42db44814e9a5d5"


class InstalledDoubaoAPI:
    def __init__(self) -> None:
        from .readiness import doubao_installations
        verified = [folder for folder, valid in doubao_installations() if valid]
        if not verified:
            raise RuntimeError("豆包语音兼容模式需要已验证的豆包输入法 v0.9.2.7；请打开设置与自检。")
        folder = verified[0]
        dll = folder / "rpc.dll"
        if not dll.is_file() or hashlib.sha256(dll.read_bytes()).hexdigest() != DLL_SHA256:
            raise RuntimeError("豆包语音兼容模式需要已验证的豆包输入法 v0.9.2.7")
        with os.add_dll_directory(str(folder)):
            self._library = ctypes.WinDLL(str(dll))
        self._send = self._library.RpcPipe_SimpleMessage
        self._send.argtypes = [ctypes.c_char_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32]
        self._send.restype = ctypes.c_int32

    def send(self, message: int, mode: int = 0) -> None:
        result = self._send(PIPE, message, mode, 0)
        if result != 0:
            raise RuntimeError(f"豆包控制接口返回 {result}（消息 {message}）")


class DoubaoVoiceController:
    """Serialize start/stop without blocking the keyboard hook or BLE thread."""

    def __init__(self, api_factory=InstalledDoubaoAPI, on_error=None, before_stop=None) -> None:
        self._api_factory = api_factory
        self._on_error = on_error or (lambda message: None)
        self._before_stop = before_stop or (lambda: None)
        self._queue = queue.Queue()
        self._lock = threading.Lock()
        self._thread = None
        self._closed = False

    def start(self) -> None:
        with self._lock:
            self._start_locked()

    def _start_locked(self) -> None:
        if not self._closed and self._thread is None:
            self._thread = threading.Thread(target=self._run, name="miao-doubao", daemon=True)
            self._thread.start()

    def _enqueue(self, command) -> bool:
        with self._lock:
            if self._closed:
                return False
            self._start_locked()
            self._queue.put(command)
            return True

    def start_voice(self, mode: str) -> None:
        if mode not in ("hold", "handsfree"):
            raise ValueError(mode)
        self._enqueue(("start", mode))

    def stop_voice(self) -> None:
        self._enqueue(("stop", None))

    def flush(self, timeout=3.0) -> bool:
        barrier = threading.Event()
        return self._enqueue(("barrier", barrier)) and barrier.wait(timeout)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            thread = self._thread
            if thread is not None:
                self._queue.put(("close", None))
        if thread is not None:
            thread.join(timeout=3)

    def _run(self) -> None:
        api, active = None, None
        while True:
            command, value = self._queue.get()
            if command == "barrier":
                value.set()
                continue
            try:
                if command == "start" and value != active:
                    if api is None:
                        api = self._api_factory()
                        log.info("豆包本地控制接口已就绪（v0.9.2.7）")
                    if active:
                        self._before_stop()
                        api.send(1008)
                        active = None
                    # Mark before starting so a failed show is also balanced.
                    active = value
                    api.send(1007)
                    api.send(1012, 1 if value == "handsfree" else 0)
                    log.info("豆包原生语音已启动 mode=%s", value)
                elif command in ("stop", "close") and active:
                    self._before_stop()
                    api.send(1008)
                    active = None
                    log.info("豆包原生语音已停止")
            except Exception as error:
                log.warning("豆包原生语音控制失败：%s", error)
                if active and api:
                    try:
                        api.send(1008)
                    except Exception:
                        log.exception("豆包语音清理失败")
                active = None
                try:
                    self._on_error(str(error))
                except Exception:
                    log.exception("豆包状态更新失败")
            if command == "close":
                return
