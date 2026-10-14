import json
import socket
import threading
import time

from miao.buttons import RemoteButton
from miao.buttons import ButtonPresetCatalog
from miao.dispatcher import ButtonDispatcher
from miao.dead_key_report import ExtraKeyReducer
from miao.extra_keys import ExtraKeyBridge, ExtraKeyForwarder


def test_authenticated_relay_filters_regular_keys_and_releases_on_disconnect(tmp_path):
    events,states=[],[]
    ready=threading.Event()
    def state(connected):
        states.append(connected)
        if connected: ready.set()
    bridge=ExtraKeyBridge(lambda b,d:events.append((b,d)),state,'AA:BB:CC:DD:EE:FF',tmp_path/'session.json')
    bridge.start()
    forwarder=ExtraKeyForwarder('aabbccddeeff',bridge.path)
    try:
        assert forwarder.send({'type':'ready'})
        assert ready.wait(2)
        forwarder.send({'type':'report','raw':'010000280000000000'})
        forwarder.send({'type':'report','raw':'010000f10000000000'})
        forwarder.send({'type':'report','raw':'010000f10000000000'})
        forwarder.send({'type':'report','raw':'010000000000000000'})
        forwarder.send({'type':'report','raw':'010000800000000000'})
        limit=time.monotonic()+2
        while len(events)<3 and time.monotonic()<limit: time.sleep(.01)
        forwarder.close()
        limit=time.monotonic()+2
        while len(events)<4 and time.monotonic()<limit: time.sleep(.01)
        assert events==[(RemoteButton.BACK,True),(RemoteButton.BACK,False),
                       (RemoteButton.VOLUME_UP,True),(RemoteButton.VOLUME_UP,False)]
        assert states==[True,False]
    finally:
        forwarder.close(); bridge.stop()
    assert not bridge.path.exists()


def test_wrong_address_and_wrong_token_cannot_activate_mapping(tmp_path):
    bridge=ExtraKeyBridge(lambda *_:None,lambda *_:None,'AA:BB:CC:DD:EE:FF',tmp_path/'session.json')
    bridge.start()
    try:
        doc=json.loads(bridge.path.read_text())
        assert not bridge._authorized({'type':'hello','protocol':2,'token':'0'*64,'address':doc['address']})
        assert not bridge._authorized({'type':'hello','protocol':2,'token':'é'*64,'address':doc['address']})
        assert not bridge._authorized({'type':'hello','protocol':2,'token':doc['token'],'address':'other'})
        forwarder=ExtraKeyForwarder('other',bridge.path)
        assert not forwarder.send({'type':'ready'})
        assert forwarder.client is None
    finally: bridge.stop()


def test_old_bridge_cannot_delete_replacement_session(tmp_path):
    bridge=ExtraKeyBridge(lambda *_:None,lambda *_:None,path=tmp_path/'session.json')
    bridge.start()
    bridge.path.write_text(json.dumps({'token':'replacement'}))
    bridge.stop()
    assert json.loads(bridge.path.read_text())['token']=='replacement'


def test_observed_driver_reports_execute_default_escape_and_system_volume():
    calls=[]
    class Executor:
        def tap_key(self,vk): calls.append(vk)
    dispatcher=ButtonDispatcher(ButtonPresetCatalog(),'pointer',Executor())
    reducer=ExtraKeyReducer()
    # Exact down/release formats observed on the user's RC003, not guessed VKs.
    for raw in ('010000f10000000000','010000000000000000',
                '010000800000000000','010000000000000000',
                '010000810000000000','010000000000000000'):
        for button,down in reducer.feed(bytes.fromhex(raw)):
            (dispatcher.button_down if down else dispatcher.button_up)(button)
    assert calls==[0x1B,0xAF,0xAE]


def test_driver_voice_tv_are_relayed_and_heartbeat_detects_app_exit(tmp_path):
    events=[]
    bridge=ExtraKeyBridge(lambda b,d:events.append((b,d)),lambda _:None,path=tmp_path/'session.json')
    bridge.start()
    forwarder=ExtraKeyForwarder('',bridge.path)
    try:
        assert forwarder.send({'type':'ready'})
        assert forwarder.send({'type':'ping'})
        assert forwarder.send({'type':'report','raw':'0100003e0035000000'})
        assert events==[(RemoteButton.TV,True),(RemoteButton.VOICE,True)]
        assert forwarder.send({'type':'report','raw':'010000000000000000'})
        assert events[-2:]==[(RemoteButton.TV,False),(RemoteButton.VOICE,False)]
        bridge.stop()
        assert not forwarder.send({'type':'ping'})
    finally:
        forwarder.close();bridge.stop()
