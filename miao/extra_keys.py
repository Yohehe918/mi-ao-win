"""Authenticated loopback relay from the opt-in driver helper to MiAo.

The normal application never elevates, injects a DLL, or touches the driver.
The current protocol also relays Voice/TV before their native HID usages are masked.
"""
from __future__ import annotations
import json
import os
import secrets
import socket
import threading
from pathlib import Path

from .config import app_data_dir
from .dead_key_report import ExtraKeyReducer


def manifest_path():
    return app_data_dir() / 'extra-key-session.json'


def normalize_address(value):
    return str(value or '').replace(':','').replace('-','').casefold()


class ExtraKeyBridge:
    def __init__(self, on_button, on_connection, address=None, path=None):
        self.on_button, self.on_connection = on_button, on_connection
        self.address = normalize_address(address)
        self.path = path or manifest_path()
        self.token = secrets.token_hex(32)
        self._stop = threading.Event()
        self._server = self._client = self._thread = None

    def start(self):
        self._server = socket.socket()
        self._server.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        self._server.bind(('127.0.0.1',0))
        self._server.listen(1)
        self._server.settimeout(.5)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        document={'protocol':2,'port':self._server.getsockname()[1],
                  'token':self.token,'address':self.address,'app_pid':os.getpid()}
        temp=self.path.with_name(self.path.name+f'.{os.getpid()}.tmp')
        temp.write_text(json.dumps(document),encoding='utf-8')
        os.replace(temp,self.path)
        self._thread=threading.Thread(target=self._receive,name='miao-extra-keys',daemon=True)
        self._thread.start()

    def _authorized(self,hello):
        token=hello.get('token') if isinstance(hello,dict) else None
        return isinstance(token,str) and len(token)==64 and token.isascii() and hello.get('protocol')==2 and \
            hello.get('type')=='hello' and \
            secrets.compare_digest(token,self.token) and \
            (not self.address or normalize_address(hello.get('address'))==self.address)

    def _receive(self):
        while not self._stop.is_set():
            try: client,_=self._server.accept()
            except socket.timeout: continue
            except OSError: break
            reducer=ExtraKeyReducer(include_voice_tv=True)
            connected=False
            self._client=client
            try:
                client.settimeout(3)
                with client,client.makefile('rb') as stream:
                    line=stream.readline(4097)
                    if len(line)>4096 or not self._authorized(json.loads(line)): continue
                    client.sendall(b'{"type":"ack","protocol":2}\n')
                    client.settimeout(None)
                    while not self._stop.is_set():
                        line=stream.readline(4097)
                        if not line or len(line)>4096: break
                        packet=json.loads(line)
                        if not isinstance(packet,dict): continue
                        if packet.get('type')=='ready' and not connected:
                            connected=True
                            self.on_connection(True)
                        elif connected and packet.get('type')=='report':
                            try: data=bytes.fromhex(packet.get('raw',''))
                            except (TypeError,ValueError): continue
                            for button,down in reducer.feed(data): self.on_button(button,down)
                        client.sendall(b'{"type":"ack","protocol":2}\n')
            except (OSError,ValueError,UnicodeDecodeError): pass
            finally:
                for button,down in reducer.reset(): self.on_button(button,down)
                if connected: self.on_connection(False)
                self._client=None

    def stop(self):
        self._stop.set()
        if self._client:
            try: self._client.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        if self._server:
            try: self._server.close()
            except OSError: pass
        if self._thread: self._thread.join(timeout=1)
        try:
            if json.loads(self.path.read_text(encoding='utf-8')).get('token')==self.token:
                self.path.unlink()
        except (OSError,ValueError): pass


class ExtraKeyForwarder:
    def __init__(self,address,path=None):
        self.address=normalize_address(address)
        self.path=path or manifest_path()
        self.client=None
        self.stream=None
        self.ready=False
        self.lock=threading.RLock()

    def _ack(self):
        line=self.stream.readline(4097)
        if not line or len(line)>4096:
            raise OSError('米遥确认通道已断开')
        packet=json.loads(line)
        if packet.get('type')!='ack' or packet.get('protocol')!=2:
            raise ValueError('请同时更新米遥主程序与增强助手')

    def _send(self,packet):
        if self.client is None:
            doc=json.loads(self.path.read_text(encoding='utf-8'))
            if doc.get('protocol')!=2 or (doc.get('address') and normalize_address(doc['address'])!=self.address):
                raise ValueError('米遥正在使用另一只遥控器，未转发。')
            self.client=socket.create_connection(('127.0.0.1',int(doc['port'])),timeout=1)
            self.client.settimeout(.5)
            self.stream=self.client.makefile('rb')
            hello={'type':'hello','protocol':2,'token':doc['token'],'address':self.address}
            self.client.sendall((json.dumps(hello)+'\n').encode())
            self._ack()
            if self.ready and packet.get('type')!='ready':
                self.client.sendall(b'{"type":"ready"}\n')
                self._ack()
        self.client.sendall((json.dumps(packet)+'\n').encode())
        self._ack()

    def send(self,packet):
        with self.lock:
            if packet.get('type')=='ready': self.ready=True
            if not self.ready: return False
            try:
                self._send(packet)
                return True
            except (OSError,ValueError,KeyError,TypeError):
                self.close()
                return False

    def close(self):
        with self.lock:
            if self.stream:
                try: self.stream.close()
                except OSError: pass
            if self.client:
                try: self.client.close()
                except OSError: pass
            self.stream=self.client=None
