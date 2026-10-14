"""One application: normal UI and an authenticated elevated background worker."""
from __future__ import annotations

import ctypes
import json
import logging
import os
from pathlib import Path
import queue
import secrets
import socket
import subprocess
import sys
import threading
import time

from .config import app_data_dir

log = logging.getLogger('miao.enhancement')


def _read(stream):
    line = stream.readline(4097)
    if not line or len(line) > 4096:
        raise OSError('增强后台连接已关闭')
    packet = json.loads(line)
    if not isinstance(packet, dict):
        raise ValueError('增强后台消息格式无效')
    return packet


def _send(client, packet):
    client.sendall((json.dumps(packet, ensure_ascii=False) + '\n').encode())


def launch_elevated(arguments):
    args = list(arguments)
    if not getattr(sys, 'frozen', False):
        args.insert(0, str(Path(__file__).parent.parent / 'launcher_gui.py'))
    execute = ctypes.windll.shell32.ShellExecuteW
    execute.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                        ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int]
    execute.restype = ctypes.c_void_p
    result = execute(None, 'runas', sys.executable, subprocess.list2cmdline(args),
                     str(Path(sys.executable).parent), 0)
    if not result or result <= 32:
        raise RuntimeError('增强功能未启动，请确认 Windows 管理员授权后重试。')


class EnhancementManager:
    def __init__(self, on_status=None, launcher=launch_elevated):
        self.on_status = on_status or (lambda text: None)
        self.launcher = launcher
        self.token = secrets.token_hex(32)
        self.parent_pid = os.getpid()
        self.enabled = False
        self.generation = 0
        self.state = 'disabled'
        self.detail = '增强按键未启用'
        self._server = self._client = self._thread = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._launching = False

    def _status(self, state, detail):
        if (state, detail) == (self.state, self.detail):
            return
        self.state, self.detail = state, detail
        log.info('%s', detail)
        self.on_status(detail)

    def start(self):
        if self._server is not None:
            return
        self._server = socket.socket()
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        self._server.bind(('127.0.0.1', 0))
        self._server.listen(1)
        self._server.settimeout(.3)
        self._thread = threading.Thread(target=self._serve, name='miao-enhancement-control', daemon=True)
        self._thread.start()

    def _authorized(self, packet):
        token = packet.get('token')
        return (packet.get('type') == 'hello' and packet.get('parent_pid') == self.parent_pid
                and isinstance(token, str) and token.isascii() and len(token) == 64
                and secrets.compare_digest(token, self.token))

    def _command(self):
        return {'enabled': self.enabled, 'generation': self.generation, 'exit': self._stop.is_set()}

    def _serve(self):
        while not self._stop.is_set():
            try:
                client, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            authorized = False
            try:
                client.settimeout(3)
                with client, client.makefile('rb') as stream:
                    if not self._authorized(_read(stream)):
                        continue
                    authorized = True
                    self._client = client
                    self._launching = False
                    _send(client, self._command())
                    while not self._stop.is_set():
                        packet = _read(stream)
                        if packet.get('type') != 'status':
                            raise ValueError('未知增强后台消息')
                        state, detail = str(packet.get('state', 'error')), str(packet.get('detail', ''))[:500]
                        self._status(state, detail)
                        _send(client, self._command())
            except (OSError, ValueError, UnicodeError):
                pass
            finally:
                if authorized:
                    self._client = None
                    if not self._stop.is_set():
                        self._status('error', '增强后台已退出，点击“启用增强按键”可重试。')

    def set_enabled(self, enabled):
        with self._lock:
            self.enabled = bool(enabled)
            self.generation += 1
            if not enabled:
                self._status('pausing', '正在暂停增强按键' if self._client else '增强按键已暂停')
                return
            self.start()
            if self._client:
                self._status('starting', '正在启用增强按键')
                return
            if self._launching:
                return
            self._launching = True
            self._status('starting', '请确认 Windows 管理员授权，增强功能将在后台启动。')
            arguments = ['--enhanced-worker', '--parent-pid', str(self.parent_pid),
                         '--control-port', str(self._server.getsockname()[1]), '--control-token', self.token]
            def launch():
                try:
                    self.launcher(arguments)
                    if self._stop.wait(30):
                        return
                    if self._client is None and self._launching:
                        self._launching = False
                        self._status('error', '增强后台未连接，点击“启用增强按键”可重试。')
                except Exception as error:
                    self._launching = False
                    self._status('error', str(error))
            threading.Thread(target=launch, name='miao-enhancement-launch', daemon=True).start()

    def stop(self):
        self.enabled = False
        self._stop.set()
        if self._client:
            try:
                self._client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self._server:
            self._server.close()
        if self._thread:
            self._thread.join(1)


class ProbeWorker:
    """Lifecycle isolated from UI/admin APIs so pause/exit can be tested safely."""
    def __init__(self, factory):
        self.factory = factory
        self.probe = None
        self.starter = None
        self.events = queue.Queue()
        self.generation = None
        self._deferred = False
        self.state, self.detail = 'paused', '增强按键已暂停'

    def apply(self, command):
        generation = command['generation']
        if self.starter and self.starter.is_alive():
            if generation != self.generation or not command.get('enabled'):
                self._deferred = True
                self.state, self.detail = 'pausing', '正在等待增强启动完成后暂停'
            return
        self._deferred = False
        if command.get('exit') or not command.get('enabled'):
            self._retire()
            self.generation = generation
            self.state, self.detail = 'paused', '增强按键已暂停'
            return
        if generation == self.generation:
            return
        self._retire()
        self.generation = generation
        self.probe = self.factory(self.events.put)
        self.state, self.detail = 'starting', '正在启动增强按键'
        probe = self.probe
        def start():
            try:
                probe.start()
            except Exception as error:
                probe.stop()
                self.events.put({'event': 'error', 'detail': str(error)})
        self.starter = threading.Thread(target=start, name='miao-enhanced-start', daemon=True)
        self.starter.start()

    def status(self):
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event.get('event')
            if self._deferred:
                continue
            if kind == 'armed':
                self.state, self.detail = 'running', '增强按键已启用 · 语音/TV已接管'
            elif kind == 'error':
                self.state, self.detail = 'error', str(event.get('detail', '增强按键启动失败'))
            elif kind == 'ready':
                self.state, self.detail = 'starting', '增强按键已就绪，正在连接米遥'
            elif kind == 'status' and '断开' in str(event.get('detail', '')):
                self.state, self.detail = 'error', '增强捕获已断开，点击“启用增强按键”可重试。'
        return {'type': 'status', 'state': self.state, 'detail': self.detail}

    def _retire(self):
        if self.starter:
            # Let an already-authorized DLL load finish before closing resources.
            self.starter.join(22)
            if self.starter.is_alive():
                raise RuntimeError('增强启动仍在等待系统响应，请稍后重试。')
            self.starter = None
        if self.probe:
            self.probe.stop()
            self.probe = None
        while not self.events.empty():
            try: self.events.get_nowait()
            except queue.Empty: break

    def close(self):
        self._retire()


def run_worker_channel(parent_pid, port, token, worker, parent_alive, interval=.3):
    """Only fixed lifecycle commands cross the authenticated local channel."""
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=3) as client:
            client.settimeout(3)
            with client.makefile('rb') as stream:
                _send(client, {'type': 'hello', 'token': token, 'parent_pid': parent_pid})
                command = _read(stream)
                while parent_alive():
                    if command.get('exit'):
                        break
                    worker.apply(command)
                    _send(client, worker.status())
                    command = _read(stream)
                    time.sleep(interval)
    except (OSError, ValueError):
        pass
    finally:
        worker.close()


def worker_main(argv=None):
    import argparse
    from .driver_probe import Probe, is_admin, process_image, K
    import ctypes.wintypes as w
    parser = argparse.ArgumentParser()
    parser.add_argument('--enhanced-worker', action='store_true')
    parser.add_argument('--parent-pid', type=int, required=True)
    parser.add_argument('--control-port', type=int, required=True)
    parser.add_argument('--control-token', required=True)
    args = parser.parse_args(argv)
    if not is_admin() or not 0 < args.control_port < 65536:
        return 1
    if len(args.control_token) != 64 or not args.control_token.isascii():
        return 1
    try:
        parent_image = process_image(args.parent_pid).resolve()
    except OSError:
        return 1
    if parent_image != Path(sys.executable).resolve():
        return 1
    K.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    K.OpenProcess.restype = w.HANDLE
    K.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    K.WaitForSingleObject.restype = w.DWORD
    K.CloseHandle.argtypes = [w.HANDLE]
    parent = K.OpenProcess(0x100000 | 0x1000, False, args.parent_pid)
    if not parent:
        return 1
    worker = ProbeWorker(lambda emit: Probe(emit, app_data_dir() / 'enhanced-key-events.jsonl'))
    try:
        run_worker_channel(args.parent_pid, args.control_port, args.control_token, worker,
                           lambda: K.WaitForSingleObject(parent, 0) == 258)
    finally:
        K.CloseHandle(parent)
    return 0
