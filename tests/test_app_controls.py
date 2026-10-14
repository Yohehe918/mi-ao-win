"""Configuration changes must release exactly the keys previously pressed."""
import dataclasses
from types import SimpleNamespace

from miao.app import MiAoApp
from miao.buttons import ButtonBinding, ButtonPresetCatalog, RemoteButton, default_pointer_preset
from miao.config import Config
from miao.dispatcher import ButtonDispatcher
from miao.session import SessionState, StateChanged
from miao.virtual_mic import VirtualMicStream


class Recorder:
    def __init__(self): self.calls = []
    def __getattr__(self, name): return lambda *args: self.calls.append((name, *args))


def app_fixture():
    app = MiAoApp.__new__(MiAoApp)
    app.config = Config(doubao_hotkey_vk=66, doubao_hotkey_modifiers=['alt'])
    app.executor = Recorder()
    app._doubao_held, app._doubao_keys = False, None
    app.dispatcher = None
    app.status = SimpleNamespace(update=lambda *_: None)
    return app


def test_changing_hotkey_releases_original_combination():
    app = app_fixture()
    assert app.doubao_shortcut().display_name == 'Alt+B'
    app._press_doubao()
    app.config.doubao_hotkey_vk = 81
    app.config.doubao_hotkey_modifiers = ['ctrl']
    app._release_doubao()
    assert app.executor.calls == [('keys_down', 66, (18,)), ('keys_up', 66, (18,))]


def test_streaming_does_not_override_custom_voice_binding():
    app = app_fixture()
    base = default_pointer_preset()
    bindings = dict(base.bindings)
    bindings[RemoteButton.VOICE] = ButtonBinding.of_none()
    preset = dataclasses.replace(base, id='user.voice', is_built_in=False, bindings=bindings)
    app.dispatcher = ButtonDispatcher(ButtonPresetCatalog([preset]), preset.id, app.executor)
    app._on_state(StateChanged(SessionState.STREAMING))
    assert app.executor.calls == []


def test_repeated_streaming_and_disconnect_balance_keys():
    app = app_fixture()
    app._on_state(StateChanged(SessionState.STREAMING))
    app._on_state(StateChanged(SessionState.STREAMING))
    app._on_state(StateChanged(SessionState.DISCONNECTED))
    assert app.executor.calls == [('keys_down', 66, (18,)), ('keys_up', 66, (18,))]


def test_mic_restart_applies_updated_gain_without_hardware():
    app = app_fixture()
    app.mic = VirtualMicStream(gain_db=0)
    app.config.mic_gain_db = 20
    app._start_mic = lambda: None
    app.restart_mic()
    assert app.mic._gain == 10
