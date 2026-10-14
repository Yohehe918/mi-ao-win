"""Replay native-first ordering without installing a hook or injecting keys."""
import ctypes
import threading
import time
from types import SimpleNamespace

from miao.buttons import RemoteButton
from miao.keyhook import KBDLLHOOKSTRUCT, LLKHF_INJECTED, LLKHF_UP, RemoteKeyHook, user32


def test_native_first_edge_waits_for_driver_report_and_blocks(monkeypatch):
    hook=RemoteKeyHook(SimpleNamespace())
    hook.set_driver_owned(True)
    monkeypatch.setattr(user32,'CallNextHookEx',lambda *_:99)
    native=KBDLLHOOKSTRUCT(vkCode=0x74,scanCode=63,flags=0)
    def report():
        time.sleep(.003)
        hook.queue_button_event(RemoteButton.VOICE,True)
    thread=threading.Thread(target=report)
    thread.start()
    assert hook._hook_callback(0,0,ctypes.addressof(native))==1
    thread.join()
    # Repeat shares the authenticated hold, without dispatching a second action.
    assert hook._hook_callback(0,0,ctypes.addressof(native))==1
    assert hook._extra_events.qsize()==1
    native.flags=LLKHF_UP
    hook.queue_button_event(RemoteButton.VOICE,False)
    assert hook._hook_callback(0,0,ctypes.addressof(native))==1


def test_keyboard_without_driver_edge_and_injected_shortcuts_pass(monkeypatch):
    hook=RemoteKeyHook(SimpleNamespace())
    hook.set_driver_owned(True)
    monkeypatch.setattr(user32,'CallNextHookEx',lambda *_:99)
    native=KBDLLHOOKSTRUCT(vkCode=0xC0,scanCode=41,flags=0)
    assert hook._hook_callback(0,0,ctypes.addressof(native))==99
    assert hook._extra_events.empty()
    hook.queue_button_event(RemoteButton.TV,True)
    native.flags=LLKHF_INJECTED
    assert hook._hook_callback(0,0,ctypes.addressof(native))==99


def test_disconnect_clears_driver_hold_and_stale_release_does_not_block():
    hook=RemoteKeyHook(SimpleNamespace())
    hook.set_driver_owned(True)
    hook.queue_button_event(RemoteButton.VOICE,True)
    hook.set_driver_owned(False)
    assert not hook._driver_native_matches(RemoteButton.VOICE,True)
    hook.set_driver_owned(True)
    hook._driver_edges[RemoteButton.VOICE]=(False,time.monotonic()-1)
    assert not hook._driver_native_matches(RemoteButton.VOICE,False)
