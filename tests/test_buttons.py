"""按键映射与 HID 事件收敛测试。

校验逻辑移植自 ButtonPreset.swift / HIDButtonEventReducer.swift；
危险快捷键用例从 ⌘Q 适配为 Windows 等价物 Alt+F4。
"""

import pytest

from miao import ConfigurationError
from miao.buttons import (
    ButtonAction,
    ButtonBinding,
    ButtonPreset,
    ButtonPresetCatalog,
    HIDButtonEvent,
    HIDButtonEventReducer,
    HIDUsageKey,
    KeyboardShortcutSpec,
    RemoteButton,
    ShortcutModifier,
    default_pointer_preset,
    normalized_usage,
)


def _pointer_with(overrides: dict[RemoteButton, ButtonBinding]) -> dict[RemoteButton, ButtonBinding]:
    bindings = dict(default_pointer_preset().bindings)
    bindings.update(overrides)
    return bindings


def test_invalid_tv_target_is_rejected():
    preset = ButtonPreset(
        id="user.invalid",
        name="无效",
        bindings=_pointer_with({RemoteButton.TV: ButtonBinding.of_preset_switch("user.missing")}),
        required_buttons=default_pointer_preset().required_buttons,
        is_built_in=False,
    )
    with pytest.raises(ConfigurationError):
        ButtonPresetCatalog(user_presets=[preset]).validate()


def test_dangerous_shortcut_is_rejected():
    # Alt+F4 ≈ macOS 的 ⌘Q
    with pytest.raises(ConfigurationError):
        KeyboardShortcutSpec(vk_code=0x73, modifiers=[ShortcutModifier.ALT], key_label="F4")
    with pytest.raises(ConfigurationError):
        KeyboardShortcutSpec(vk_code=0x4C, modifiers=[ShortcutModifier.WIN], key_label="L")


def test_voice_key_must_stay_push_to_talk():
    preset = ButtonPreset(
        id="user.bad-voice",
        name="坏语音键",
        bindings=_pointer_with({RemoteButton.VOICE: ButtonBinding.of_action(ButtonAction.KEYBOARD_RETURN)}),
        required_buttons=default_pointer_preset().required_buttons,
        is_built_in=False,
    )
    with pytest.raises(ConfigurationError):
        ButtonPresetCatalog(user_presets=[preset]).validate()


def test_menu_key_cannot_be_remapped():
    preset = ButtonPreset(
        id="user.bad-menu",
        name="坏菜单键",
        bindings=_pointer_with({RemoteButton.MENU: ButtonBinding.of_action(ButtonAction.KEYBOARD_ESCAPE)}),
        required_buttons=default_pointer_preset().required_buttons,
        is_built_in=False,
    )
    with pytest.raises(ConfigurationError):
        ButtonPresetCatalog(user_presets=[preset]).validate()


def test_normalized_usage_extracts_byte_from_raw_value():
    assert normalized_usage(0xFFFFFFFF, [0x0128]) == 0x28
    assert normalized_usage(0xFFFFFFFF, [0]) is None
    assert normalized_usage(40, [0]) == 40
    assert normalized_usage(None, [1]) is None


def test_reducer_pairs_down_and_up():
    reducer = HIDButtonEventReducer()
    mapping = {HIDUsageKey(page=7, usage=40): RemoteButton.CENTER}

    events = reducer.reduce(page=7, element_usage=40, raw_value=1, buttons_by_usage=mapping)
    assert events == [(RemoteButton.CENTER, HIDButtonEvent.DOWN)]

    # 重复按下（同一个 key 仍处于活动状态）不产生新事件
    assert reducer.reduce(page=7, element_usage=40, raw_value=1, buttons_by_usage=mapping) == []

    events = reducer.reduce(page=7, element_usage=40, raw_value=0, buttons_by_usage=mapping)
    assert events == [(RemoteButton.CENTER, HIDButtonEvent.UP)]

    # 松手后再无状态
    assert reducer.reduce(page=7, element_usage=40, raw_value=0, buttons_by_usage=mapping) == []


def test_reducer_releases_previous_button_when_another_presses():
    reducer = HIDButtonEventReducer()
    mapping = {
        HIDUsageKey(page=7, usage=40): RemoteButton.CENTER,
        HIDUsageKey(page=7, usage=241): RemoteButton.BACK,
    }
    reducer.reduce(page=7, element_usage=40, raw_value=1, buttons_by_usage=mapping)
    events = reducer.reduce(page=7, element_usage=241, raw_value=1, buttons_by_usage=mapping)
    assert events == [
        (RemoteButton.CENTER, HIDButtonEvent.UP),
        (RemoteButton.BACK, HIDButtonEvent.DOWN),
    ]
