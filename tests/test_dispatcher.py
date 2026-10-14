"""按键派发器测试（纯逻辑，假执行器记录调用）。"""

from miao.buttons import (
    ButtonAction,
    ButtonBinding,
    ButtonPreset,
    ButtonPresetCatalog,
    RemoteButton,
    default_pointer_preset,
)
from miao.dispatcher import ButtonDispatcher, ControlMode


class FakeExecutor:
    def __init__(self):
        self.calls: list[tuple] = []

    def tap_key(self, vk): self.calls.append(("tap", vk))
    def press_shortcut(self, vk, modifiers): self.calls.append(("shortcut", vk, modifiers))
    def start_pointer_move(self, dx, dy): self.calls.append(("move_start", dx, dy))
    def stop_pointer_move(self): self.calls.append(("move_stop",))
    def scroll(self, amount): self.calls.append(("scroll", amount))
    def focus_target(self): self.calls.append(("focus",))
    def launch_or_focus_target(self): self.calls.append(("launch_or_focus",))
    def previous_task(self): self.calls.append(("prev",))
    def next_task(self): self.calls.append(("next",))
    def on_mode_changed(self, mode): self.calls.append(("mode", mode))
    def on_preset_changed(self, preset): self.calls.append(("preset", preset.id))


def _dispatcher(executor, preset_id="pointer", now=None):
    return ButtonDispatcher(
        catalog=ButtonPresetCatalog(),
        preset_id=preset_id,
        executor=executor,
        now=now or (lambda: 0.0),
    )


def test_pointer_preset_default_actions():
    ex = FakeExecutor()
    d = _dispatcher(ex)

    d.button_down(RemoteButton.DPAD_UP)
    assert ("move_start", 0, -1) in ex.calls
    d.button_up(RemoteButton.DPAD_UP)
    assert ("move_stop",) in ex.calls

    d.button_down(RemoteButton.CENTER)
    assert ("tap", 0x0D) in ex.calls  # Return
    d.button_down(RemoteButton.BACK)
    assert ("tap", 0x1B) in ex.calls  # Escape
    d.button_down(RemoteButton.VOLUME_UP)
    assert ("tap", 0xAF) in ex.calls
    d.button_down(RemoteButton.VOLUME_DOWN)
    assert ("tap", 0xAE) in ex.calls
    d.button_down(RemoteButton.POWER)
    assert ("launch_or_focus",) in ex.calls


def test_tv_toggles_directional_mode():
    ex = FakeExecutor()
    d = _dispatcher(ex)

    d.button_down(RemoteButton.TV)
    assert ("mode", ControlMode.DIRECTIONAL) in ex.calls

    ex.calls.clear()
    d.button_down(RemoteButton.DPAD_LEFT)
    assert ("tap", 0x25) in ex.calls  # VK_LEFT，不再移动指针
    assert not any(c[0] == "move_start" for c in ex.calls)
    d.button_up(RemoteButton.DPAD_LEFT)
    assert ("move_stop",) not in ex.calls

    d.button_down(RemoteButton.TV)
    assert ("mode", ControlMode.POINTER) in ex.calls


def test_home_single_click_page_down_double_click_page_up():
    clock = [1000.0]
    ex = FakeExecutor()
    d = _dispatcher(ex, now=lambda: clock[0])

    d.button_down(RemoteButton.HOME)
    d.tick()
    assert not any(c == ("tap", 0x22) for c in ex.calls)  # 350ms 内不发出
    clock[0] += 0.4
    d.tick()
    assert ("tap", 0x22) in ex.calls  # PageDown

    ex.calls.clear()
    clock[0] += 1.0
    d.button_down(RemoteButton.HOME)
    clock[0] += 0.2
    d.button_down(RemoteButton.HOME)  # 350ms 内双击
    assert ("tap", 0x21) in ex.calls  # PageUp
    clock[0] += 0.4
    d.tick()
    assert ("tap", 0x22) not in ex.calls  # 双击后不再补发 PageDown


def test_preset_switch_via_tv():
    ex = FakeExecutor()
    pointer = default_pointer_preset()

    a_bindings = dict(pointer.bindings)
    a_bindings[RemoteButton.TV] = ButtonBinding.of_preset_switch("user.alt")
    preset_a = ButtonPreset(
        id="user.a", name="写作", bindings=a_bindings,
        required_buttons=pointer.required_buttons, is_built_in=False,
    )
    alt_bindings = dict(pointer.bindings)
    alt_bindings[RemoteButton.TV] = ButtonBinding.of_preset_switch("user.a")
    alt_bindings[RemoteButton.VOLUME_UP] = ButtonBinding.of_action(ButtonAction.KEYBOARD_PAGE_UP)
    alt = ButtonPreset(
        id="user.alt", name="备用", bindings=alt_bindings,
        required_buttons=pointer.required_buttons, is_built_in=False,
    )
    catalog = ButtonPresetCatalog(user_presets=[preset_a, alt])
    catalog.validate()

    d = ButtonDispatcher(catalog=catalog, preset_id="user.a", executor=ex, now=lambda: 0.0)
    d.button_down(RemoteButton.TV)
    assert d.preset.id == "user.alt"
    assert ("preset", "user.alt") in ex.calls

    d.button_down(RemoteButton.VOLUME_UP)
    assert ("tap", 0x21) in ex.calls  # PageUp（user.alt 的音量+）
