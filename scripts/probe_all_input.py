"""Read-only full Raw Input probe: dynamic buffers and actual RAWHID logging."""
from __future__ import annotations
import argparse
import ctypes as c
import ctypes.wintypes as w
import json
import re
import time
from pathlib import Path

U = c.WinDLL('user32', use_last_error=True)
K = c.WinDLL('kernel32', use_last_error=True)
WNDPROC = c.WINFUNCTYPE(c.c_ssize_t,w.HWND,w.UINT,w.WPARAM,w.LPARAM)

class WNDCLASS(c.Structure):
    _fields_=[('style',w.UINT),('proc',WNDPROC),('extra1',c.c_int),('extra2',c.c_int),
              ('instance',w.HINSTANCE),('icon',w.HICON),('cursor',w.HANDLE),('brush',w.HBRUSH),
              ('menu',w.LPCWSTR),('name',w.LPCWSTR)]
class HEADER(c.Structure):
    _fields_=[('kind',w.DWORD),('size',w.DWORD),('device',w.HANDLE),('wparam',w.WPARAM)]
class KEYBOARD(c.Structure):
    _fields_=[('make',w.USHORT),('flags',w.USHORT),('reserved',w.USHORT),('vk',w.USHORT),
              ('message',w.UINT),('extra',w.ULONG)]
class DEVICE(c.Structure):
    _fields_=[('page',w.USHORT),('usage',w.USHORT),('flags',w.DWORD),('window',w.HWND)]
class DEVICELIST(c.Structure):
    _fields_=[('device',w.HANDLE),('kind',w.DWORD)]

K.GetModuleHandleW.argtypes=[w.LPCWSTR]; K.GetModuleHandleW.restype=w.HMODULE
U.RegisterClassW.argtypes=[c.POINTER(WNDCLASS)]; U.RegisterClassW.restype=w.ATOM
U.CreateWindowExW.argtypes=[w.DWORD,w.LPCWSTR,w.LPCWSTR,w.DWORD,c.c_int,c.c_int,c.c_int,c.c_int,
                            w.HWND,w.HMENU,w.HINSTANCE,c.c_void_p]
U.CreateWindowExW.restype=w.HWND
U.DefWindowProcW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM]; U.DefWindowProcW.restype=c.c_ssize_t
U.GetRawInputData.argtypes=[w.HANDLE,w.UINT,c.c_void_p,c.POINTER(w.UINT),w.UINT]; U.GetRawInputData.restype=w.UINT
U.GetRawInputDeviceInfoW.argtypes=[w.HANDLE,w.UINT,c.c_void_p,c.POINTER(w.UINT)]; U.GetRawInputDeviceInfoW.restype=w.UINT
U.GetRawInputDeviceList.argtypes=[c.c_void_p,c.POINTER(w.UINT),w.UINT]; U.GetRawInputDeviceList.restype=w.UINT
U.RegisterRawInputDevices.argtypes=[c.POINTER(DEVICE),w.UINT,w.UINT]; U.RegisterRawInputDevices.restype=w.BOOL
U.SetTimer.argtypes=[w.HWND,c.c_size_t,w.UINT,c.c_void_p]; U.SetTimer.restype=c.c_size_t
U.GetMessageW.argtypes=[c.POINTER(w.MSG),w.HWND,w.UINT,w.UINT]; U.GetMessageW.restype=w.BOOL
U.DispatchMessageW.argtypes=[c.POINTER(w.MSG)]; U.DispatchMessageW.restype=c.c_ssize_t
U.PostQuitMessage.argtypes=[c.c_int]
U.DestroyWindow.argtypes=[w.HWND]; U.DestroyWindow.restype=w.BOOL

parser=argparse.ArgumentParser()
parser.add_argument('--seconds',type=int,default=90)
parser.add_argument('--log',type=Path,required=True)
args=parser.parse_args()
args.log.parent.mkdir(parents=True,exist_ok=True)
log=args.log.open('w',encoding='utf-8',buffering=1)
started=time.monotonic()
counts={}
names={}

def emit(kind,**values):
    event={'event':kind,'elapsed':round(time.monotonic()-started,3),**values}
    line=json.dumps(event,ensure_ascii=False)
    log.write(line+'\n')
    print(line,flush=True)

def name_of(handle):
    if handle in names: return names[handle]
    length=w.UINT()
    U.GetRawInputDeviceInfoW(handle,0x20000007,None,c.byref(length))
    if not length.value: return ''
    buffer=c.create_unicode_buffer(length.value+1)
    if U.GetRawInputDeviceInfoW(handle,0x20000007,buffer,c.byref(length))==0xffffffff: return ''
    names[handle]=buffer.value
    return buffer.value

def remote(name):
    return bool(re.search(r'VID[&_](?:01)?2717',name,re.I) and re.search(r'PID[&_](?:01)?32B8',name,re.I))

@WNDPROC
def proc(hwnd,msg,wp,lp):
    try:
        if msg==0xff:
            size=w.UINT()
            U.GetRawInputData(w.HANDLE(lp),0x10000003,None,c.byref(size),c.sizeof(HEADER))
            if size.value>=c.sizeof(HEADER):
                buffer=c.create_string_buffer(size.value)
                result=U.GetRawInputData(w.HANDLE(lp),0x10000003,buffer,c.byref(size),c.sizeof(HEADER))
                if result!=0xffffffff:
                    head=HEADER.from_buffer_copy(buffer)
                    name=name_of(head.device)
                    if remote(name):
                        payload=buffer.raw[c.sizeof(HEADER):result]
                        if head.kind==1 and len(payload)>=c.sizeof(KEYBOARD):
                            key=KEYBOARD.from_buffer_copy(payload)
                            label=f'VK{key.vk:02X}/SC{key.make:02X}'
                            if not key.flags&1: counts[label]=counts.get(label,0)+1
                            emit('keyboard',vk=key.vk,scan=key.make,flags=key.flags,
                                 edge='up' if key.flags&1 else 'down',device=name)
                        elif head.kind==2:
                            emit('hid',payload=payload.hex(),device=name)
                        else: emit('other',raw_type=head.kind,payload=payload.hex(),device=name)
            return U.DefWindowProcW(hwnd,msg,wp,lp)
        if msg==0x113 and time.monotonic()-started>=args.seconds:
            U.PostQuitMessage(0)
            return 0
    except Exception as error: emit('error',detail=str(error))
    return U.DefWindowProcW(hwnd,msg,wp,lp)

instance=K.GetModuleHandleW(None)
classname='MiAoCompleteInputProbe'
wc=WNDCLASS(proc=proc,instance=instance,name=classname)
if not U.RegisterClassW(c.byref(wc)): raise c.WinError(c.get_last_error())
window=U.CreateWindowExW(0,classname,'MiAo read-only input probe',0,0,0,0,0,None,None,instance,None)
if not window: raise c.WinError(c.get_last_error())
devices=(DEVICE*3)(DEVICE(1,6,0x100,window),DEVICE(1,0x80,0x100,window),DEVICE(0xc,1,0x100,window))
if not U.RegisterRawInputDevices(devices,3,c.sizeof(DEVICE)): raise c.WinError(c.get_last_error())
count=w.UINT()
U.GetRawInputDeviceList(None,c.byref(count),c.sizeof(DEVICELIST))
items=(DEVICELIST*count.value)()
U.GetRawInputDeviceList(items,c.byref(count),c.sizeof(DEVICELIST))
for item in items:
    name=name_of(item.device)
    if remote(name): emit('device',raw_type=item.kind,name=name)
emit('ready',seconds=args.seconds,prompt='确认 → 返回 → 音量+ → 音量-，每键按 2-3 次，间隔 1 秒')
U.SetTimer(window,1,100,None)
message=w.MSG()
try:
    while U.GetMessageW(c.byref(message),None,0,0)>0:
        U.TranslateMessage(c.byref(message))
        U.DispatchMessageW(c.byref(message))
finally:
    U.DestroyWindow(window)
    emit('summary',counts=counts)
    log.close()
