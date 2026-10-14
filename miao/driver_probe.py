"""Opt-in administrator-only RC003 key remapping helper.

This helper never injects keyboard events, installs drivers,
changes registry/security settings, or restarts/terminates a driver process.
It loads a hash-verified official Frida Gadget into one verified RC003 host.
Actual activation is a separate, explicit button in the diagnostic window.
While the authenticated app heartbeat is live, only Voice/TV usages are masked
after their original reports are forwarded. Stale heartbeat restores native input.
"""
from __future__ import annotations
import argparse
import ctypes as c
import ctypes.wintypes as w
import hashlib
import json
import lzma
import os
import queue
import secrets
import socket
import sys
import threading
import time
import tkinter as tk
import winreg
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk

from .dead_key_report import report_usages
from .extra_keys import ExtraKeyForwarder

VERSION = '17.15.3'
ARCHIVE_HASH = 'b566d70189b6d551ad8f4e0bea24de08a3d4c0f559bb35b2bdb67d45182240c2'
DLL_HASH = '6fca4007b2284c765a6c15c967a741f536b5865bf83867326a54029a3b752748'
HID_PREFIX = '{00001812-0000-1000-8000-00805f9b34fb}'
DEVICE_TOKEN = 'dev_vid&012717_pid&32b8'
ENUM_ROOT = r'SYSTEM\CurrentControlSet\Enum\BTHLEDevice'
KEY_NAMES = {0x3E: '语音', 0x35: 'TV', 0xF1: '返回', 0x80: '音量＋', 0x81: '音量－'}
K = c.WinDLL('kernel32', use_last_error=True)


@dataclass(frozen=True)
class DriverTarget:
    pid: int
    service: str
    instance: str


def child_names(key):
    index = 0
    while True:
        try: yield winreg.EnumKey(key, index)
        except OSError: return
        index += 1


def hid_hosts():
    result = []
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, ENUM_ROOT) as root:
        for service in child_names(root):
            if not service.casefold().startswith(HID_PREFIX): continue
            try:
                with winreg.OpenKey(root, service) as key:
                    for instance in child_names(key):
                        try:
                            with winreg.OpenKey(key, instance+r'\Device Parameters\WUDFDiagnosticInfo') as diagnostic:
                                pid = int(winreg.QueryValueEx(diagnostic, 'HostPid')[0])
                            if pid > 0: result.append(DriverTarget(pid, service, instance))
                        except (OSError, ValueError, TypeError): continue
            except OSError: continue
    return result


def find_target():
    hosts = hid_hosts()
    selected = [host for host in hosts if DEVICE_TOKEN in host.service.casefold()]
    if len(selected) != 1:
        raise RuntimeError(f'应当只有一只 RC003 HID 设备，实际找到 {len(selected)} 个；请检查连接。')
    target = selected[0]
    if any(host.pid == target.pid and host.service != target.service for host in hosts):
        raise RuntimeError('目标宿主与其他蓝牙 HID 设备共享，已拒绝加载，避免混入其他键盘。')
    return target


def is_admin():
    return bool(c.windll.shell32.IsUserAnAdmin())


def process_image(pid):
    K.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    K.OpenProcess.restype = w.HANDLE
    K.CloseHandle.argtypes = [w.HANDLE]
    K.CloseHandle.restype = w.BOOL
    K.QueryFullProcessImageNameW.argtypes = [w.HANDLE,w.DWORD,w.LPWSTR,c.POINTER(w.DWORD)]
    K.QueryFullProcessImageNameW.restype = w.BOOL
    process = K.OpenProcess(0x1000, False, pid)
    if not process: raise c.WinError(c.get_last_error())
    try:
        size = w.DWORD(32768)
        buffer = c.create_unicode_buffer(size.value)
        if not K.QueryFullProcessImageNameW(process,0,buffer,c.byref(size)):
            raise c.WinError(c.get_last_error())
        return Path(buffer.value)
    finally: K.CloseHandle(process)


def verify_target(target):
    current = find_target()
    if current != target: raise RuntimeError('遥控器驱动进程已变化，请重新打开检测助手。')
    expected = Path(os.environ['SystemRoot']) / 'System32' / 'WUDFHost.exe'
    actual = process_image(target.pid)
    if str(actual).casefold() != str(expected).casefold():
        raise RuntimeError('目标进程不是系统 WUDFHost.exe，已拒绝加载。')


def enable_debug_privilege():
    class LUID(c.Structure):
        _fields_ = [('low',w.DWORD),('high',w.LONG)]
    class PRIVILEGE(c.Structure):
        _fields_ = [('luid',LUID),('attributes',w.DWORD)]
    class TOKEN(c.Structure):
        _fields_ = [('count',w.DWORD),('privileges',PRIVILEGE*1)]
    A = c.WinDLL('advapi32',use_last_error=True)
    K.GetCurrentProcess.argtypes=[]; K.GetCurrentProcess.restype=w.HANDLE
    A.OpenProcessToken.argtypes=[w.HANDLE,w.DWORD,c.POINTER(w.HANDLE)]; A.OpenProcessToken.restype=w.BOOL
    A.LookupPrivilegeValueW.argtypes=[w.LPCWSTR,w.LPCWSTR,c.POINTER(LUID)]; A.LookupPrivilegeValueW.restype=w.BOOL
    A.AdjustTokenPrivileges.argtypes=[w.HANDLE,w.BOOL,c.POINTER(TOKEN),w.DWORD,c.c_void_p,c.c_void_p]
    A.AdjustTokenPrivileges.restype=w.BOOL
    token=w.HANDLE()
    if not A.OpenProcessToken(K.GetCurrentProcess(),0x28,c.byref(token)): raise c.WinError(c.get_last_error())
    try:
        value=LUID()
        if not A.LookupPrivilegeValueW(None,'SeDebugPrivilege',c.byref(value)): raise c.WinError(c.get_last_error())
        privileges=TOKEN(1,(PRIVILEGE*1)(PRIVILEGE(value,2)))
        c.set_last_error(0)
        ok=A.AdjustTokenPrivileges(token,False,c.byref(privileges),0,None,None)
        if not ok or c.get_last_error(): raise c.WinError(c.get_last_error())
    finally: K.CloseHandle(token)


class MODULE(c.Structure):
    _fields_=[('size',w.DWORD),('moduleID',w.DWORD),('pid',w.DWORD),('globalUsage',w.DWORD),
        ('processUsage',w.DWORD),('base',c.c_void_p),('baseSize',w.DWORD),('handle',w.HMODULE),
        ('name',w.WCHAR*256),('path',w.WCHAR*260)]


def process_modules(pid):
    K.CreateToolhelp32Snapshot.argtypes=[w.DWORD,w.DWORD]; K.CreateToolhelp32Snapshot.restype=w.HANDLE
    K.Module32FirstW.argtypes=[w.HANDLE,c.POINTER(MODULE)]; K.Module32FirstW.restype=w.BOOL
    K.Module32NextW.argtypes=[w.HANDLE,c.POINTER(MODULE)]; K.Module32NextW.restype=w.BOOL
    snapshot=K.CreateToolhelp32Snapshot(0x18,pid)
    if snapshot in (None,c.c_void_p(-1).value): raise c.WinError(c.get_last_error())
    try:
        module=MODULE(); module.size=c.sizeof(module)
        result=[]
        if K.Module32FirstW(snapshot,c.byref(module)):
            while True:
                result.append((module.name,module.path,module.base))
                if not K.Module32NextW(snapshot,c.byref(module)): break
        return result
    finally: K.CloseHandle(snapshot)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bundled_root():
    return Path(getattr(sys,'_MEIPASS',Path(__file__).parent.parent))


def prepare_runtime(session):
    root=bundled_root()
    archive=root/'assets'/'key-recovery'/f'frida-gadget-{VERSION}-windows-x86_64.dll.xz'
    if sha256(archive) != ARCHIVE_HASH: raise RuntimeError('官方 Gadget 压缩包校验失败，未执行。')
    dll=lzma.decompress(archive.read_bytes())
    if hashlib.sha256(dll).hexdigest() != DLL_HASH: raise RuntimeError('Gadget DLL 校验失败，未执行。')
    runtime=Path(os.environ.get('PROGRAMDATA',r'C:\ProgramData'))/'MiAoWin'/'KeyProbe'/VERSION
    runtime.mkdir(parents=True,exist_ok=True)
    dll_path=runtime/f'frida-gadget-{VERSION}.dll'
    if dll_path.exists() and sha256(dll_path) != DLL_HASH:
        raise RuntimeError('运行时 DLL 被修改，已拒绝使用。')
    if not dll_path.exists(): dll_path.write_bytes(dll)
    script=(root/'miao'/'resources'/'driver-probe.js').read_text(encoding='utf-8')
    script=script.replace('__MIAO_SESSION__',json.dumps(session))
    js_path=runtime/'rc003-probe.js'
    temp=runtime/'rc003-probe.js.tmp'
    temp.write_text(script,encoding='utf-8')
    os.replace(temp,js_path)
    config={'interaction':{'type':'script','path':str(js_path),'on_change':'reload'},
            'runtime':'qjs','teardown':'full'}
    dll_path.with_suffix('.config').write_text(json.dumps(config),encoding='utf-8')
    return dll_path


def load_observer(target,dll):
    verify_target(target)
    modules=process_modules(target.pid)
    if any(str(Path(path)).casefold()==str(dll).casefold() for _,path,_ in modules):
        return '观察模块已存在，已更新本次检测脚本。'
    if any('gadget' in name.casefold() or 'miremote-tap' in name.casefold() for name,_,_ in modules):
        raise RuntimeError('该宿主已有其他 Gadget 模块，已拒绝重复加载。')
    K.GetModuleHandleW.argtypes=[w.LPCWSTR]; K.GetModuleHandleW.restype=w.HMODULE
    K.GetProcAddress.argtypes=[w.HMODULE,c.c_char_p]; K.GetProcAddress.restype=c.c_void_p
    K.GetModuleHandleExW.argtypes=[w.DWORD,c.c_void_p,c.POINTER(w.HMODULE)]; K.GetModuleHandleExW.restype=w.BOOL
    K.GetModuleFileNameW.argtypes=[w.HMODULE,w.LPWSTR,w.DWORD]; K.GetModuleFileNameW.restype=w.DWORD
    address=K.GetProcAddress(K.GetModuleHandleW('kernel32.dll'),b'LoadLibraryW')
    owner=w.HMODULE()
    if not address or not K.GetModuleHandleExW(6,address,c.byref(owner)): raise c.WinError(c.get_last_error())
    owner_name=c.create_unicode_buffer(32768)
    if not K.GetModuleFileNameW(owner,owner_name,len(owner_name)): raise c.WinError(c.get_last_error())
    remote_base=next((base for name,_,base in modules if name.casefold()==Path(owner_name.value).name.casefold()),None)
    if remote_base is None: raise RuntimeError('无法定位系统 LoadLibraryW，未执行。')
    remote_loader=remote_base+(address-owner.value)
    K.VirtualAllocEx.argtypes=[w.HANDLE,c.c_void_p,c.c_size_t,w.DWORD,w.DWORD]; K.VirtualAllocEx.restype=c.c_void_p
    K.WriteProcessMemory.argtypes=[w.HANDLE,c.c_void_p,c.c_void_p,c.c_size_t,c.POINTER(c.c_size_t)]; K.WriteProcessMemory.restype=w.BOOL
    K.VirtualFreeEx.argtypes=[w.HANDLE,c.c_void_p,c.c_size_t,w.DWORD]; K.VirtualFreeEx.restype=w.BOOL
    K.CreateRemoteThread.argtypes=[w.HANDLE,c.c_void_p,c.c_size_t,c.c_void_p,c.c_void_p,w.DWORD,c.c_void_p]; K.CreateRemoteThread.restype=w.HANDLE
    K.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD]; K.WaitForSingleObject.restype=w.DWORD
    K.GetExitCodeThread.argtypes=[w.HANDLE,c.POINTER(w.DWORD)]; K.GetExitCodeThread.restype=w.BOOL
    process=K.OpenProcess(0x43a,False,target.pid)
    if not process: raise c.WinError(c.get_last_error())
    memory=thread=None
    completed=False
    try:
        encoded=(str(dll)+'\0').encode('utf-16-le')
        memory=K.VirtualAllocEx(process,None,len(encoded),0x3000,4)
        if not memory: raise c.WinError(c.get_last_error())
        buffer=c.create_string_buffer(encoded); written=c.c_size_t()
        if not K.WriteProcessMemory(process,memory,buffer,len(encoded),c.byref(written)) or written.value!=len(encoded):
            raise RuntimeError('写入模块路径失败。')
        thread=K.CreateRemoteThread(process,None,0,remote_loader,memory,0,None)
        if not thread: raise c.WinError(c.get_last_error())
        if K.WaitForSingleObject(thread,20000)!=0:
            raise TimeoutError('加载等待超时；未结束或重启任何系统进程。')
        completed=True
        result=w.DWORD()
        if not K.GetExitCodeThread(thread,c.byref(result)) or result.value==0:
            raise RuntimeError('Windows 未成功加载观察模块。')
        return '观察模块已加载，正在等待认证连接。'
    finally:
        if memory and (completed or thread is None): K.VirtualFreeEx(process,memory,0,0x8000)
        if thread: K.CloseHandle(thread)
        K.CloseHandle(process)


class Probe:
    def __init__(self,emit,log_path):
        self.emit,self.log_path=emit,log_path
        self.stop_event=threading.Event()
        self.server=self.client=None
        self.target=None
        self.mutex=None
        self.token=secrets.token_hex(32)
        self.counts={usage:0 for usage in KEY_NAMES}
        self.held=frozenset()
        self.log=None
        self.forwarder=None
        self.control_lock=threading.Lock()
        self.log_lock=threading.Lock()

    def control(self, packet):
        with self.control_lock:
            if self.client:
                self.client.sendall((json.dumps(packet)+'\n').encode())

    def heartbeat(self):
        while not self.stop_event.is_set():
            try:
                alive=self.forwarder.send({'type':'ping'})
                self.control({'type':'arm' if alive else 'disarm', 'ttl_ms':1200})
            except OSError:
                break
            self.stop_event.wait(.3)

    def record(self,event,**values):
        data={'event':event,'time':time.time(),**values}
        with self.log_lock:
            if self.log: self.log.write(json.dumps(data,ensure_ascii=False)+'\n')
        self.emit(data)

    def start(self):
        if not is_admin(): raise PermissionError('需要管理员权限；请以管理员身份运行此助手。')
        if c.sizeof(c.c_void_p)!=8: raise RuntimeError('此检测模块仅支持 Windows x64。')
        K.CreateMutexW.argtypes=[c.c_void_p,w.BOOL,w.LPCWSTR]; K.CreateMutexW.restype=w.HANDLE
        K.CloseHandle.argtypes=[w.HANDLE]; K.CloseHandle.restype=w.BOOL
        c.set_last_error(0)
        self.mutex=K.CreateMutexW(None,False,'Local\\MiAoRC003DriverProbe')
        if not self.mutex: raise c.WinError(c.get_last_error())
        if c.get_last_error()==183:
            K.CloseHandle(self.mutex); self.mutex=None
            raise RuntimeError('已有增强检测助手正在运行，请先关闭另一个检测。')
        self.target=find_target()
        self.forwarder=ExtraKeyForwarder(self.target.service.rsplit('_',1)[-1])
        enable_debug_privilege()
        verify_target(self.target)
        self.log_path.parent.mkdir(parents=True,exist_ok=True)
        self.log=self.log_path.open('w',encoding='utf-8',buffering=1)
        self.server=socket.socket()
        self.server.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        self.server.bind(('127.0.0.1',0))
        self.server.listen(1); self.server.settimeout(.5)
        session={'port':self.server.getsockname()[1],'token':self.token}
        dll=prepare_runtime(session)
        self.record('status',detail=f'已验证遥控器专用 WUDFHost，PID {self.target.pid}。')
        threading.Thread(target=self.receive,name='miao-driver-probe',daemon=True).start()
        self.record('status',detail=load_observer(self.target,dll))

    def receive(self):
        try:
            while not self.stop_event.is_set():
                try: client,_=self.server.accept()
                except socket.timeout: continue
                self.client=client
                client.settimeout(30)
                with client,client.makefile('rb') as stream:
                    hello=stream.readline(4097)
                    try: message=json.loads(hello)
                    except (ValueError,UnicodeDecodeError): continue
                    if not isinstance(message,dict) or message.get('type')!='hello' or \
                            message.get('pid')!=self.target.pid or message.get('protocol')!=1 or \
                            not secrets.compare_digest(str(message.get('token','')),self.token):
                        continue
                    client.settimeout(None)
                    while not self.stop_event.is_set():
                        line=stream.readline(4097)
                        if not line: break
                        if len(line)>4096: raise RuntimeError('诊断报文超长，已关闭连接。')
                        packet=json.loads(line)
                        if not isinstance(packet,dict): continue
                        if packet.get('type')=='ready' and packet.get('hook_installed') is True:
                            self.forwarder.send({'type':'ready'})
                            threading.Thread(target=self.heartbeat,name='miao-driver-heartbeat',daemon=True).start()
                            self.record('ready',detail='驱动捕获已就绪；正在等待米遥确认语音/TV接管。')
                        elif packet.get('type')=='armed':
                            self.record('armed',detail='米遥已确认：语音/TV原生事件已接管，电脑键盘不受影响。')
                        elif packet.get('type')=='report':
                            try: report=bytes.fromhex(packet.get('raw',''))
                            except (TypeError,ValueError): continue
                            usages=report_usages(report)
                            if usages is None: continue
                            self.forwarder.send({'type':'report','raw':report.hex()})
                            for usage in usages-self.held:
                                if usage in self.counts: self.counts[usage]+=1
                            self.held=usages
                            self.record('report',raw=report.hex(),usages=sorted(usages),suppressed=packet.get('suppressed',False),
                                counts={KEY_NAMES[u]:count for u,count in self.counts.items()})
                        elif packet.get('type')=='error': self.record('error',detail=str(packet.get('detail','')))
                self.client=None
                if not self.stop_event.is_set(): self.record('status',detail='观察模块已断开；需要重新开启一次检测。')
                break
        except (OSError,ValueError,RuntimeError) as error:
            if not self.stop_event.is_set(): self.record('error',detail=str(error))

    def stop(self):
        if self.stop_event.is_set():
            return
        self.stop_event.set()
        if self.forwarder:
            self.forwarder.ready=False
            self.forwarder.close()
        if self.client:
            try: self.client.sendall(b'STOP\n'); self.client.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        if self.server:
            try: self.server.close()
            except OSError: pass
        if self.mutex:
            K.CloseHandle(self.mutex)
            self.mutex=None
        self.record('summary',counts={KEY_NAMES[u]:n for u,n in self.counts.items()})
        with self.log_lock:
            if self.log:
                self.log.close()
                self.log=None
        # Loaded DLL remains mapped until its Windows host exits. EOF disables
        # its observer hook; this helper never kills a driver to unload it.


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--inspect',action='store_true')
    args=parser.parse_args()
    if args.inspect:
        target=find_target()
        print(json.dumps({'pid':target.pid,'service':target.service,'instance':target.instance,
                          'admin':is_admin()},ensure_ascii=False))
        return
    root=tk.Tk(); root.title('米遥 · 增强按键与语音接管')
    root.geometry('720x600'); root.resizable(False,False)
    frame=ttk.Frame(root,padding=24); frame.pack(fill='both',expand=True)
    ttk.Label(frame,text='增强按键 · 语音键与 TV 接管',font=('Microsoft YaHei UI',19,'bold')).pack(anchor='w')
    explanation=('只接管已验证的小米遥控器：语音、TV、返回与音量键转交米遥。\n'
                 '语音和 TV 的原生键先转发再屏蔽，避免 F5 等原生事件传给豆包。\n'
                 '电脑键盘不受影响；不安装驱动，不重启系统进程。\n'
                 '暂停或米遥断开后恢复原生事件；DLL 随驱动宿主退出后清除。')
    ttk.Label(frame,text=explanation,wraplength=670,justify='left').pack(anchor='w',pady=14)
    counters=tk.StringVar(value='语音  0     TV  0     返回  0     音量＋  0     音量－  0')
    ttk.Label(frame,textvariable=counters,font=('Microsoft YaHei UI',17,'bold')).pack(anchor='w',pady=12)
    messages=queue.Queue()
    log_path=(Path(sys.executable).parent if getattr(sys,'frozen',False) else Path.cwd())/'按键检测结果.jsonl'
    probe=[None]
    starting=[False]
    text=tk.Text(frame,height=12,wrap='word',font=('Microsoft YaHei UI',10),state='disabled')
    text.pack(fill='both',expand=True,pady=8)
    def append(line):
        text.configure(state='normal'); text.insert('end',line+'\n'); text.see('end'); text.configure(state='disabled')
    controls=ttk.Frame(frame); controls.pack(fill='x',pady=10)
    def start():
        starting[0]=True
        start_button.configure(state='disabled')
        probe[0]=Probe(messages.put,log_path)
        def run():
            try: probe[0].start()
            except Exception as error:
                probe[0].stop()
                messages.put({'event':'error','detail':str(error)})
            finally:
                messages.put({'event':'start_finished'})
        threading.Thread(target=run,name='miao-probe-start',daemon=True).start()
    start_button=ttk.Button(controls,text='启动增强按键',command=start); start_button.pack(side='left',ipadx=26,ipady=8)
    def stop():
        if probe[0]: probe[0].stop()
        if not starting[0] and is_admin(): start_button.configure(state='normal')
        append('增强按键已暂停，点击“启动增强按键”可恢复。')
    ttk.Button(controls,text='暂停增强按键',command=stop).pack(side='left',padx=12,ipadx=26,ipady=8)
    ttk.Label(frame,text='检测记录：'+str(log_path),wraplength=670).pack(anchor='w')
    def poll():
        while True:
            try: event=messages.get_nowait()
            except queue.Empty: break
            if 'counts' in event:
                counters.set('     '.join(f'{name}  {count}' for name,count in event['counts'].items()))
            if event['event']=='start_finished':
                starting[0]=False
                if probe[0] and probe[0].stop_event.is_set() and is_admin():
                    start_button.configure(state='normal')
                continue
            if event['event']!='report': append(event.get('detail',event['event']))
        root.after(100,poll)
    def close():
        if probe[0]: probe[0].stop()
        root.destroy()
    append('尚未加载观察模块。点击“启动增强按键”后才会执行。')
    if not is_admin():
        append('当前不是管理员，请用管理员身份重新运行。')
        start_button.configure(state='disabled')
    root.after(100,poll); root.protocol('WM_DELETE_WINDOW',close); root.mainloop()


if __name__=='__main__': main()
