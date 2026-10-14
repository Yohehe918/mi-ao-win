"""Hold vs handsfree routing, tested without keyboard injection or BLE access."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from miao import ConfigurationError
from miao.app import MiAoApp, AppStatus
from miao.buttons import ButtonAction, ButtonBinding, ButtonPresetCatalog, KeyboardShortcutSpec, RemoteButton, ShortcutModifier, default_pointer_preset
from miao.config import Config
from miao.dispatcher import ButtonDispatcher
from miao.executor import _key_input
from miao.session import SendCommand, SessionState, StateChanged, VoiceSession

CAPS = bytes([0x0B, 0x01, 0x00, 0x03, 0x03, 0x00, 0x78])
START = bytes([0x04, 0x00, 0x02, 0x07])

class Recorder:
    def __init__(self): self.calls=[]
    def __getattr__(self,name): return lambda *args:self.calls.append((name,*args))

def app_fixture():
    app=MiAoApp.__new__(MiAoApp)
    app.config=Config(doubao_hotkey_vk=66,doubao_hotkey_modifiers=['right_alt'])
    app.executor=Recorder()
    app.status=AppStatus()
    app._doubao_held=False
    app._doubao_keys=None
    app._handsfree_requested=False
    app._handsfree_active=False
    app.mic=SimpleNamespace(running=True)
    app._loop=None
    base=default_pointer_preset()
    bindings={**base.bindings,RemoteButton.VOICE:ButtonBinding.of_none(),RemoteButton.TV:ButtonBinding.of_action(ButtonAction.VOICE_HANDSFREE_TOGGLE)}
    preset=replace(base,id='user.doubao',is_built_in=False,bindings=bindings,gestures={RemoteButton.VOICE:{'long':ButtonBinding.of_action(ButtonAction.VOICE_PUSH_TO_TALK)}})
    clock=[0.0]
    app.dispatcher=ButtonDispatcher(ButtonPresetCatalog([preset]),preset.id,app.executor,now=lambda:clock[0],
        on_voice_hold=lambda down:app._press_doubao(mapped=True) if down else app._release_doubao(),
        on_handsfree_toggle=app._toggle_handsfree_on_loop)
    session=VoiceSession(now=lambda:clock[0])
    session.on_connected();session.handle_control(CAPS)
    writes=[]
    def emit(actions):
        for action in actions:
            if isinstance(action,StateChanged):app._on_state(action)
            elif isinstance(action,SendCommand):writes.append(action.data)
    def set_handsfree(enabled):
        emit(session.set_handsfree(enabled))
        return session.handsfree if enabled else True
    app._bridge=SimpleNamespace(set_handsfree=set_handsfree)
    return app,clock,session,emit,writes

def test_voice_long_holds_right_alt_b_and_releases_on_up():
    app,clock,_,_,_=app_fixture()
    app.dispatcher.button_down(RemoteButton.VOICE)
    app._on_state(StateChanged(SessionState.STREAMING))
    assert app.executor.calls==[]
    clock[0]=.61
    app.dispatcher.tick()
    app.dispatcher.tick()
    app.dispatcher.button_up(RemoteButton.VOICE)
    assert app.executor.calls[:2]==[('keys_down',66,(165,)),('keys_up',66,(165,))]

def test_tv_waits_for_mic_confirmation_then_toggles_and_closes():
    app,clock,session,emit,writes=app_fixture()
    app.dispatcher.button_down(RemoteButton.TV)
    app.dispatcher.button_up(RemoteButton.TV)
    assert writes==[bytes([0x0C,0x00])]
    assert app._handsfree_requested and not app._handsfree_active
    assert not app.executor.calls
    emit(session.handle_control(START))
    emit([StateChanged(SessionState.STREAMING)])
    assert app.executor.calls==[('press_shortcut',32,(165,))]
    session.handle_audio(bytes(120))  # A real (possibly silent) frame confirms capture.
    clock[0]=30
    assert not any(isinstance(x,StateChanged) for x in session.tick())
    app.dispatcher.button_down(RemoteButton.TV)
    app.dispatcher.button_up(RemoteButton.TV)
    assert app.executor.calls==[('press_shortcut',32,(165,)),('tap_key',0x1B)]
    assert writes[-1]==bytes([0x0D,0x07])
    assert not app._handsfree_requested and not session.handsfree

def test_handsfree_open_timeout_never_toggles_doubao():
    app,clock,session,emit,_=app_fixture()
    app._toggle_handsfree_on_loop()
    clock[0]=5.1
    emit(session.tick())
    assert not app._handsfree_requested
    assert not app.executor.calls
    assert session.state is SessionState.READY


def test_handsfree_ack_without_audio_times_out_and_ends_doubao():
    app,clock,session,emit,writes=app_fixture()
    app._toggle_handsfree_on_loop()
    emit(session.handle_control(START))
    clock[0]=2.1
    emit(session.tick())
    assert session.state is SessionState.READY
    assert not session.handsfree and not app._handsfree_active
    assert writes[-1]==bytes([0x0D,0x07])
    assert app.executor.calls[-1]==('tap_key',0x1B)
    assert app.status.detail=='遥控器未传回免按音频；请使用长按语音键。'

def test_disconnect_ends_handsfree_once():
    app,_,session,emit,_=app_fixture()
    app._toggle_handsfree_on_loop()
    emit(session.handle_control(START))
    emit(session.on_disconnected())
    emit([StateChanged(SessionState.DISCONNECTED)])
    assert app.executor.calls.count(('press_shortcut',32,(165,)))==1
    assert app.executor.calls.count(('tap_key',0x1B))==1
    assert not app._handsfree_active

def test_handsfree_does_not_keep_unbounded_audio_buffer():
    app,_,session,emit,_=app_fixture()
    app._toggle_handsfree_on_loop()
    emit(session.handle_control(START))
    for _ in range(200):session.handle_audio(bytes(120))
    assert session._samples==[]

def test_right_alt_is_persisted_extended_and_validated():
    spec=KeyboardShortcutSpec(32,[ShortcutModifier.RIGHT_ALT],'空格')
    assert KeyboardShortcutSpec.from_json(spec.to_json())==spec
    assert spec.display_name=='右Alt+空格'
    assert _key_input(165,False).union.ki.dwFlags==9
    assert _key_input(165,True).union.ki.dwFlags==11
    assert _key_input(165,False).union.ki.wScan==0x38
    assert _key_input(66,False).union.ki.wScan==0x30
    assert _key_input(32,False).union.ki.wScan==0x39
    assert _key_input(0x25,False).union.ki.dwFlags & 1
    with pytest.raises(ConfigurationError):KeyboardShortcutSpec(0x73,[ShortcutModifier.RIGHT_ALT],'F4')
