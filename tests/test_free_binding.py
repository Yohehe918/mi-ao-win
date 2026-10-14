"""可编程映射（自由绑定）测试：按住快捷键、启动程序、发送文本、自由校验、v1→v2 迁移。"""

import json

from miao.buttons import (
    ButtonBinding,
    ButtonPreset,
    ButtonPresetCatalog,
    ButtonAction,
    KeyboardShortcutSpec,
    RemoteButton,
    ShortcutModifier,
    default_pointer_preset,
)
from miao.dispatcher import ButtonDispatcher
from miao.preset_store import ButtonPresetStore, LoadState


class RecordingExecutor:
    """记录按键按下/抬起与其余动作的假执行器。"""

    def __init__(self):
        self.calls: list[tuple] = []

    def tap_key(self, vk): self.calls.append(("tap", vk))
    def press_shortcut(self, vk, modifiers): self.calls.append(("shortcut", vk, modifiers))
    def keys_down(self, vk, modifiers=()): self.calls.append(("keys_down", vk, modifiers))
    def keys_up(self, vk, modifiers=()): self.calls.append(("keys_up", vk, modifiers))
    def launch(self, program, arguments=()): self.calls.append(("launch", program, tuple(arguments)))
    def type_text(self, text): self.calls.append(("type_text", text))
    def start_pointer_move(self, dx, dy): self.calls.append(("move_start", dx, dy))
    def stop_pointer_move(self): self.calls.append(("move_stop",))
    def scroll(self, amount): self.calls.append(("scroll", amount))
    def focus_target(self): self.calls.append(("focus",))
    def launch_or_focus_target(self): self.calls.append(("launch_or_focus",))
    def previous_task(self): self.calls.append(("prev",))
    def next_task(self): self.calls.append(("next",))
    def on_mode_changed(self, mode): self.calls.append(("mode", mode))
    def on_preset_changed(self, preset): self.calls.append(("preset", preset.id))


def _shortcut(vk=0x51, modifiers=(ShortcutModifier.ALT,), label="Q"):
    return KeyboardShortcutSpec(vk_code=vk, modifiers=list(modifiers), key_label=label)


def _free_preset(preset_id="user.free") -> ButtonPreset:
    """任意按键 → 任意绑定，用于验证自由映射。"""
    pointer = default_pointer_preset()
    bindings = dict(pointer.bindings)
    bindings[RemoteButton.VOICE] = ButtonBinding.of_hold_shortcut(_shortcut())  # 豆包按住说话
    bindings[RemoteButton.MENU] = ButtonBinding.of_launch_program(
        r"C:\Windows\notepad.exe", ("readme.txt",)
    )
    bindings[RemoteButton.HOME] = ButtonBinding.of_send_text("你好，世界")
    bindings[RemoteButton.TV] = ButtonBinding.of_shortcut(_shortcut(0x54, (), "T"))
    bindings[RemoteButton.POWER] = ButtonBinding.of_none()
    return ButtonPreset(
        id=preset_id,
        name="自由映射",
        bindings=bindings,
        required_buttons=pointer.required_buttons,
        is_built_in=False,
    )


def _dispatcher(executor, catalog, preset_id="user.free"):
    return ButtonDispatcher(
        catalog=catalog, preset_id=preset_id, executor=executor, now=lambda: 0.0
    )


# -- 校验 -------------------------------------------------------------------


def test_free_mapping_requires_opt_in():
    catalog = ButtonPresetCatalog(user_presets=[_free_preset()])
    # 严格模式：语音键不是「按住说话」、菜单键被占用 → 拒绝
    try:
        catalog.validate()
    except Exception:
        pass
    else:
        raise AssertionError("严格校验应当拒绝自由映射")
    catalog.validate(allow_free_mapping=True)  # 自由模式通过


def test_free_mapping_still_blocks_dangerous_shortcut():
    try:
        _shortcut(0x73, (ShortcutModifier.ALT,), "F4")  # Alt+F4
    except Exception:
        pass
    else:
        raise AssertionError("Alt+F4 应当被黑名单拒绝")


def test_free_mapping_rejects_empty_program_and_text():
    catalog = ButtonPresetCatalog(
        user_presets=[
            ButtonPreset(
                id="user.bad",
                name="坏配置",
                bindings={RemoteButton.TV: ButtonBinding(kind="launch_program", program="  ")},
                required_buttons=frozenset(),
                is_built_in=False,
            )
        ]
    )
    try:
        catalog.validate(allow_free_mapping=True)
    except Exception:
        pass
    else:
        raise AssertionError("空程序路径应当被拒绝")


# -- 派发 -------------------------------------------------------------------


def test_hold_shortcut_presses_and_releases_pair():
    ex = RecordingExecutor()
    d = _dispatcher(ex, ButtonPresetCatalog(user_presets=[_free_preset()]))

    d.button_down(RemoteButton.VOICE)
    assert ("keys_down", 0x51, (0x12,)) in ex.calls  # Alt+Q 按下

    d.button_up(RemoteButton.VOICE)
    assert ("keys_up", 0x51, (0x12,)) in ex.calls  # 抬起时释放


def test_release_all_releases_stuck_shortcut():
    ex = RecordingExecutor()
    d = _dispatcher(ex, ButtonPresetCatalog(user_presets=[_free_preset()]))
    d.button_down(RemoteButton.VOICE)
    d.release_all()
    assert ("keys_up", 0x51, (0x12,)) in ex.calls
    assert d._held == {}


def test_launch_program_and_send_text_and_none():
    ex = RecordingExecutor()
    d = _dispatcher(ex, ButtonPresetCatalog(user_presets=[_free_preset()]))

    d.button_down(RemoteButton.MENU)
    assert ("launch", r"C:\Windows\notepad.exe", ("readme.txt",)) in ex.calls

    d.button_down(RemoteButton.HOME)
    assert ("type_text", "你好，世界") in ex.calls

    before = len(ex.calls)
    d.button_down(RemoteButton.POWER)  # none：不应该产生任何调用
    assert len(ex.calls) == before


def test_tap_shortcut_uses_press_shortcut():
    ex = RecordingExecutor()
    d = _dispatcher(ex, ButtonPresetCatalog(user_presets=[_free_preset()]))
    d.button_down(RemoteButton.TV)
    assert ("shortcut", 0x54, ()) in ex.calls


# -- 持久化 -----------------------------------------------------------------


def test_v1_file_loads_and_upgrades_on_save(tmp_path):
    path = tmp_path / "button-presets.json"
    legacy = {
        "schemaVersion": 1,
        "presets": [
            {
                "id": "user.legacy",
                "name": "旧配置",
                "bindings": {
                    "voice": {"kind": "action", "action": "voice.push_to_talk"},
                    "menu": {"kind": "action", "action": "unmapped"},
                    "back": {
                        "kind": "keyboard_shortcut",
                        "shortcut": {"vk_code": 27, "modifiers": [], "key_label": "ESC"},
                    },
                },
            }
        ],
    }
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    store = ButtonPresetStore(path)
    snapshot = store.load()
    assert snapshot.state is LoadState.LOADED
    assert snapshot.catalog.user_presets[0].binding_for(RemoteButton.BACK).shortcut.vk_code == 27

    store.save(snapshot.catalog, active_preset_id="user.legacy")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["schemaVersion"] == 3
    assert saved["activePresetId"] == "user.legacy"


def test_free_preset_roundtrip(tmp_path):
    path = tmp_path / "button-presets.json"
    store = ButtonPresetStore(path)
    catalog = ButtonPresetCatalog(user_presets=[_free_preset()])
    store.save(catalog, active_preset_id="user.free", allow_free_mapping=True)

    snapshot = store.load()
    assert snapshot.active_preset_id == "user.free"
    loaded = snapshot.catalog.user_presets[0]
    assert loaded.binding_for(RemoteButton.VOICE).kind == "hold_shortcut"
    assert loaded.binding_for(RemoteButton.MENU).program == r"C:\Windows\notepad.exe"
    assert loaded.binding_for(RemoteButton.MENU).arguments == ("readme.txt",)
    assert loaded.binding_for(RemoteButton.HOME).text == "你好，世界"
    assert loaded.binding_for(RemoteButton.POWER).kind == "none"


def test_free_binding_json_roundtrip():
    for binding in (
        ButtonBinding.of_hold_shortcut(_shortcut()),
        ButtonBinding.of_launch_program("prog.exe", ("a", "b")),
        ButtonBinding.of_send_text("文本"),
        ButtonBinding.of_none(),
        ButtonBinding.of_action(ButtonAction.KEYBOARD_RETURN),
    ):
        assert ButtonBinding.from_json(binding.to_json()) == binding
