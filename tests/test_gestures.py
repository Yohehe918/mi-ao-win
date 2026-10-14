"""Behavioral tests for the reference UI's actual double/long press mappings."""
import dataclasses
import json

import pytest

from miao import ConfigurationError
from miao.buttons import (
    ButtonAction, ButtonBinding, ButtonPresetCatalog, KeyboardShortcutSpec,
    RemoteButton, ShortcutModifier, default_pointer_preset,
)
from miao.dispatcher import ButtonDispatcher
from miao.preset_store import ButtonPresetStore, LoadState


class Recorder:
    def __init__(self): self.calls = []
    def __getattr__(self, name):
        return lambda *args: self.calls.append((name, *args))


def setup(double=True, long=True, long_hold=False):
    base = default_pointer_preset()
    bindings = dict(base.bindings)
    bindings[RemoteButton.TV] = ButtonBinding.of_shortcut(KeyboardShortcutSpec(83, [], 'S'))
    gestures = {}
    if double: gestures['double'] = ButtonBinding.of_send_text('double')
    if long:
        gestures['long'] = (ButtonBinding.of_hold_shortcut(KeyboardShortcutSpec(81, [ShortcutModifier.ALT], 'Q'))
                            if long_hold else ButtonBinding.of_send_text('long'))
    preset = dataclasses.replace(base, id='user.gestures', name='Gestures', bindings=bindings,
                                 gestures={RemoteButton.TV: gestures}, is_built_in=False)
    catalog = ButtonPresetCatalog([preset])
    recorder, clock = Recorder(), [100.0]
    dispatcher = ButtonDispatcher(catalog, preset.id, recorder, now=lambda: clock[0])
    return dispatcher, recorder, clock, catalog


def test_single_fires_only_after_double_window():
    d, ex, clock, _ = setup()
    d.button_down(RemoteButton.TV)
    clock[0] += .05
    d.button_up(RemoteButton.TV)
    assert ex.calls == []
    clock[0] += .36
    d.tick()
    assert ex.calls == [('press_shortcut', 83, ())]


def test_double_suppresses_both_single_clicks():
    d, ex, clock, _ = setup()
    for _ in range(2):
        d.button_down(RemoteButton.TV)
        clock[0] += .05
        d.button_up(RemoteButton.TV)
        clock[0] += .05
    clock[0] += .4
    d.tick()
    assert ex.calls == [('type_text', 'double')]


def test_long_fires_once_even_with_keyboard_repeats():
    d, ex, clock, _ = setup()
    d.button_down(RemoteButton.TV)
    clock[0] += .4
    d.button_down(RemoteButton.TV)
    clock[0] += .21
    d.tick()
    d.tick()
    d.button_up(RemoteButton.TV)
    clock[0] += .4
    d.tick()
    assert ex.calls.count(('type_text', 'long')) == 1
    assert not any(call[0] == 'press_shortcut' for call in ex.calls)


def test_long_hold_is_released_on_up_and_on_shutdown():
    d, ex, clock, _ = setup(long_hold=True)
    d.button_down(RemoteButton.TV)
    clock[0] += .61
    d.tick()
    assert ('keys_down', 81, (18,)) in ex.calls
    d.button_up(RemoteButton.TV)
    assert ex.calls.count(('keys_up', 81, (18,))) == 1
    d.button_down(RemoteButton.TV)
    clock[0] += .61
    d.tick()
    d.release_all()
    assert ex.calls.count(('keys_up', 81, (18,))) == 2


def test_long_still_works_if_timer_has_not_ticked():
    d, ex, clock, _ = setup()
    d.button_down(RemoteButton.TV)
    clock[0] += .7
    d.button_up(RemoteButton.TV)
    assert ('type_text', 'long') in ex.calls
    assert not any(call[0] == 'press_shortcut' for call in ex.calls)


def test_release_all_discards_delayed_click_and_stops_pointer():
    d, ex, clock, _ = setup()
    d.button_down(RemoteButton.TV)
    d.button_up(RemoteButton.TV)
    d.release_all()
    clock[0] += 1
    d.tick()
    assert ex.calls == [('stop_pointer_move',)]


def test_gesture_roundtrip_preserves_single_and_long_hold(tmp_path):
    _, _, _, catalog = setup(long_hold=True)
    store = ButtonPresetStore(tmp_path / 'presets.json')
    store.save(catalog, 'user.gestures', allow_free_mapping=True)
    snapshot = store.load()
    assert snapshot.state is LoadState.LOADED
    preset = snapshot.catalog.preset(snapshot.active_preset_id)
    assert preset.binding_for(RemoteButton.TV).shortcut.vk_code == 83
    assert preset.binding_for(RemoteButton.TV, 'double').text == 'double'
    assert preset.binding_for(RemoteButton.TV, 'long').kind == 'hold_shortcut'


def test_v2_file_loads_without_gestures(tmp_path):
    path = tmp_path / 'presets.json'
    path.write_text(json.dumps({'schemaVersion': 2, 'presets': [
        {'id':'user.old','name':'Old','bindings':{'tv':{'kind':'send_text','text':'old'}}}
    ]}), encoding='utf-8')
    snapshot = ButtonPresetStore(path).load()
    assert snapshot.state is LoadState.LOADED
    assert snapshot.catalog.preset('user.old').binding_for(RemoteButton.TV).text == 'old'
    assert not snapshot.catalog.preset('user.old').has_gestures(RemoteButton.TV)


def test_voice_gestures_are_allowed_in_free_mapping_and_roundtrip(tmp_path):
    _, _, _, catalog = setup()
    base = catalog.user_presets[0]
    preset = dataclasses.replace(base, gestures={RemoteButton.VOICE: {
        'double': ButtonBinding.of_send_text('voice double'),
        'long': ButtonBinding.of_hold_shortcut(KeyboardShortcutSpec(66, [ShortcutModifier.RIGHT_ALT], 'B')),
    }})
    catalog = ButtonPresetCatalog([preset])
    catalog.validate(allow_free_mapping=True)
    store = ButtonPresetStore(tmp_path / 'voice.json')
    store.save(catalog, preset.id, allow_free_mapping=True)
    assert store.load().catalog.preset(preset.id).binding_for(RemoteButton.VOICE,'long').shortcut.display_name == '右Alt+B'
    ex, clock = Recorder(), [0.0]
    d = ButtonDispatcher(catalog, preset.id, ex, now=lambda:clock[0])
    d.button_down(RemoteButton.VOICE)
    clock[0] = .61
    d.tick()
    d.button_up(RemoteButton.VOICE)
    assert ('keys_down',66,(165,)) in ex.calls
    assert ('keys_up',66,(165,)) in ex.calls
    ex.calls.clear()
    for _ in range(2):
        d.button_down(RemoteButton.VOICE)
        clock[0] += .05
        d.button_up(RemoteButton.VOICE)
        clock[0] += .05
    clock[0] += .4
    d.tick()
    assert ex.calls == [('type_text','voice double')]


def test_voice_long_speech_releases_on_up_and_disconnect():
    base=default_pointer_preset()
    preset=dataclasses.replace(base,id='user.voice',is_built_in=False,gestures={RemoteButton.VOICE:{'long':ButtonBinding.of_action(ButtonAction.VOICE_PUSH_TO_TALK)}})
    clock, calls=[0.0],[]
    d=ButtonDispatcher(ButtonPresetCatalog([preset]),preset.id,Recorder(),now=lambda:clock[0],on_voice_hold=calls.append)
    d.button_down(RemoteButton.VOICE)
    clock[0]=.61
    d.tick(); d.tick()
    assert calls==[True]
    d.button_up(RemoteButton.VOICE)
    assert calls==[True,False]
    d.button_down(RemoteButton.VOICE)
    clock[0]+=1
    d.tick()
    d.release_all()
    assert calls==[True,False,True,False]


def test_invalid_gesture_program_is_rejected():
    _, _, _, catalog = setup()
    preset = dataclasses.replace(catalog.user_presets[0], gestures={RemoteButton.TV:{'long':ButtonBinding(kind='launch_program', program='')}})
    with pytest.raises(ConfigurationError):
        ButtonPresetCatalog([preset]).validate(allow_free_mapping=True)
