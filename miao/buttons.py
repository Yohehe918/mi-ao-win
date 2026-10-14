"""按键映射数据模型与 HID 事件收敛。

移植自 mi-ao 的 ButtonPreset.swift 与 HIDButtonEventReducer.swift。

与 macOS 版的差异：
- 快捷键用 Windows 虚拟键码（VK_*）而不是 macOS keyCode；
- 危险快捷键黑名单对应改为 Alt+F4（≈⌘Q，关闭当前窗口）和 Win+L（锁屏）；
- "菜单键保留 macOS 原生右键"相应改为保留系统原生行为。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from . import ConfigurationError

# ---------------------------------------------------------------------------
# 按键与动作


class RemoteButton(enum.Enum):
    VOICE = "voice"
    DPAD_UP = "dpad_up"
    DPAD_DOWN = "dpad_down"
    DPAD_LEFT = "dpad_left"
    DPAD_RIGHT = "dpad_right"
    CENTER = "center"
    BACK = "back"
    HOME = "home"
    MENU = "menu"
    VOLUME_UP = "volume_up"
    VOLUME_DOWN = "volume_down"
    TV = "tv"
    POWER = "power"

    @property
    def display_name(self) -> str:
        return _BUTTON_DISPLAY_NAMES[self]

    @property
    def is_user_editable(self) -> bool:
        return self not in (RemoteButton.VOICE, RemoteButton.MENU)


_BUTTON_DISPLAY_NAMES = {
    RemoteButton.VOICE: "语音",
    RemoteButton.DPAD_UP: "方向上",
    RemoteButton.DPAD_DOWN: "方向下",
    RemoteButton.DPAD_LEFT: "方向左",
    RemoteButton.DPAD_RIGHT: "方向右",
    RemoteButton.CENTER: "中间确认",
    RemoteButton.BACK: "返回",
    RemoteButton.HOME: "HOME",
    RemoteButton.MENU: "菜单",
    RemoteButton.VOLUME_UP: "音量 +",
    RemoteButton.VOLUME_DOWN: "音量 -",
    RemoteButton.TV: "TV",
    RemoteButton.POWER: "电源",
}


class ButtonAction(enum.Enum):
    VOICE_PUSH_TO_TALK = "voice.push_to_talk"
    VOICE_HANDSFREE_TOGGLE = "voice.handsfree_toggle"
    POINTER_MOVE_UP = "pointer.move_up"
    POINTER_MOVE_DOWN = "pointer.move_down"
    POINTER_MOVE_LEFT = "pointer.move_left"
    POINTER_MOVE_RIGHT = "pointer.move_right"
    POINTER_SCROLL_UP = "pointer.scroll_up"
    POINTER_SCROLL_DOWN = "pointer.scroll_down"
    KEYBOARD_ARROW_UP = "keyboard.arrow_up"
    KEYBOARD_ARROW_DOWN = "keyboard.arrow_down"
    KEYBOARD_ARROW_LEFT = "keyboard.arrow_left"
    KEYBOARD_ARROW_RIGHT = "keyboard.arrow_right"
    KEYBOARD_RETURN = "keyboard.return"
    KEYBOARD_ESCAPE = "keyboard.escape"
    KEYBOARD_PAGE_UP = "keyboard.page_up"
    KEYBOARD_PAGE_DOWN = "keyboard.page_down"
    SYSTEM_VOLUME_UP = "system.volume_up"
    SYSTEM_VOLUME_DOWN = "system.volume_down"
    HOME_PAGE_NAVIGATION = "home.page_navigation"
    MODE_TOGGLE_POINTER_DIRECTIONAL = "mode.toggle_pointer_directional"
    TARGET_FOCUS = "target.focus"
    TARGET_LAUNCH_OR_FOCUS = "target.launch_or_focus"
    TARGET_PREVIOUS_TASK = "target.previous_task"
    TARGET_NEXT_TASK = "target.next_task"
    UNMAPPED = "unmapped"

    @property
    def display_name(self) -> str:
        return _ACTION_DISPLAY_NAMES[self]


_ACTION_DISPLAY_NAMES = {
    ButtonAction.VOICE_PUSH_TO_TALK: "按住语音说话",
    ButtonAction.VOICE_HANDSFREE_TOGGLE: "豆包 · 免按输入开关",
    ButtonAction.POINTER_MOVE_UP: "移动鼠标 · 上",
    ButtonAction.POINTER_MOVE_DOWN: "移动鼠标 · 下",
    ButtonAction.POINTER_MOVE_LEFT: "移动鼠标 · 左",
    ButtonAction.POINTER_MOVE_RIGHT: "移动鼠标 · 右",
    ButtonAction.POINTER_SCROLL_UP: "滚动 · 向上",
    ButtonAction.POINTER_SCROLL_DOWN: "滚动 · 向下",
    ButtonAction.KEYBOARD_ARROW_UP: "键盘 · 上方向",
    ButtonAction.KEYBOARD_ARROW_DOWN: "键盘 · 下方向",
    ButtonAction.KEYBOARD_ARROW_LEFT: "键盘 · 左方向",
    ButtonAction.KEYBOARD_ARROW_RIGHT: "键盘 · 右方向",
    ButtonAction.KEYBOARD_RETURN: "键盘 · Return",
    ButtonAction.KEYBOARD_ESCAPE: "键盘 · Escape",
    ButtonAction.KEYBOARD_PAGE_UP: "键盘 · Page Up",
    ButtonAction.KEYBOARD_PAGE_DOWN: "键盘 · Page Down",
    ButtonAction.SYSTEM_VOLUME_UP: "系统音量＋",
    ButtonAction.SYSTEM_VOLUME_DOWN: "系统音量－",
    ButtonAction.HOME_PAGE_NAVIGATION: "HOME · 单击下翻 / 双击上翻",
    ButtonAction.MODE_TOGGLE_POINTER_DIRECTIONAL: "切换方向环模式",
    ButtonAction.TARGET_FOCUS: "聚焦目标窗口",
    ButtonAction.TARGET_LAUNCH_OR_FOCUS: "启动或聚焦目标窗口",
    ButtonAction.TARGET_PREVIOUS_TASK: "目标应用 · 上一个会话",
    ButtonAction.TARGET_NEXT_TASK: "目标应用 · 下一个会话",
    ButtonAction.UNMAPPED: "不执行动作",
}


# ---------------------------------------------------------------------------
# 自定义快捷键（Windows 虚拟键码）

VK_F4 = 0x73
VK_L = 0x4C


class ShortcutModifier(enum.Enum):
    WIN = "win"
    CTRL = "ctrl"
    ALT = "alt"
    RIGHT_ALT = "right_alt"
    SHIFT = "shift"

    @property
    def symbol(self) -> str:
        return {
            ShortcutModifier.WIN: "Win+",
            ShortcutModifier.CTRL: "Ctrl+",
            ShortcutModifier.ALT: "Alt+",
            ShortcutModifier.RIGHT_ALT: "右Alt+",
            ShortcutModifier.SHIFT: "Shift+",
        }[self]

    @property
    def vk_code(self) -> int:
        return {
            ShortcutModifier.WIN: 0x5B,  # VK_LWIN
            ShortcutModifier.CTRL: 0x11,  # VK_CONTROL
            ShortcutModifier.ALT: 0x12,  # VK_MENU
            ShortcutModifier.RIGHT_ALT: 0xA5,  # VK_RMENU
            ShortcutModifier.SHIFT: 0x10,  # VK_SHIFT
        }[self]


_MODIFIER_ORDER = (ShortcutModifier.WIN, ShortcutModifier.CTRL, ShortcutModifier.ALT, ShortcutModifier.RIGHT_ALT, ShortcutModifier.SHIFT)


@dataclass(frozen=True)
class KeyboardShortcutSpec:
    vk_code: int
    modifiers: frozenset[ShortcutModifier]
    key_label: str

    def __init__(self, vk_code: int, modifiers, key_label: str) -> None:
        if not 0 < vk_code < 256:
            raise ConfigurationError("快捷键按键码无效")
        label = key_label.strip()
        if not label or len(label) > 12:
            raise ConfigurationError("快捷键按键名称无效")
        modifier_set = frozenset(modifiers)
        if self._is_dangerous(vk_code, modifier_set):
            raise ConfigurationError("为避免意外关闭窗口或锁屏，不能保存这个快捷键")
        object.__setattr__(self, "vk_code", vk_code)
        object.__setattr__(self, "modifiers", modifier_set)
        object.__setattr__(self, "key_label", label.upper())

    @staticmethod
    def _is_dangerous(vk_code: int, modifiers: frozenset[ShortcutModifier]) -> bool:
        alt = bool({ShortcutModifier.ALT, ShortcutModifier.RIGHT_ALT} & modifiers)
        win = ShortcutModifier.WIN in modifiers
        return (alt and vk_code == VK_F4) or (win and vk_code == VK_L)  # Alt+F4 / Win+L

    @property
    def display_name(self) -> str:
        prefix = "".join(m.symbol for m in _MODIFIER_ORDER if m in self.modifiers)
        return prefix + self.key_label

    def to_json(self) -> dict:
        return {
            "vk_code": self.vk_code,
            "modifiers": [m.value for m in _MODIFIER_ORDER if m in self.modifiers],
            "key_label": self.key_label,
        }

    @classmethod
    def from_json(cls, data: dict) -> KeyboardShortcutSpec:
        return cls(
            vk_code=data["vk_code"],
            modifiers=[ShortcutModifier(m) for m in data["modifiers"]],
            key_label=data["key_label"],
        )


# ---------------------------------------------------------------------------
# 绑定与预设


@dataclass(frozen=True)
class ButtonBinding:
    """按键绑定：内置动作 / 自定义快捷键 / 按住快捷键 / 启动程序 / 发送文本 / 切换预设。"""

    kind: str  # "action" | "keyboard_shortcut" | "hold_shortcut" | "launch_program" | "send_text" | "preset_switch" | "none"
    action: ButtonAction | None = None
    shortcut: KeyboardShortcutSpec | None = None
    target_preset_id: str | None = None
    program: str = ""
    arguments: tuple[str, ...] = ()
    text: str = ""

    @staticmethod
    def of_action(action: ButtonAction) -> ButtonBinding:
        return ButtonBinding(kind="action", action=action)

    @staticmethod
    def of_shortcut(shortcut: KeyboardShortcutSpec) -> ButtonBinding:
        return ButtonBinding(kind="keyboard_shortcut", shortcut=shortcut)

    @staticmethod
    def of_hold_shortcut(shortcut: KeyboardShortcutSpec) -> ButtonBinding:
        """按住按键期间持续按下该快捷键（用于豆包「按住说话」）。"""
        return ButtonBinding(kind="hold_shortcut", shortcut=shortcut)

    @staticmethod
    def of_launch_program(program: str, arguments: tuple[str, ...] = ()) -> ButtonBinding:
        if not program.strip():
            raise ConfigurationError("启动程序需要填写程序路径")
        return ButtonBinding(kind="launch_program", program=program.strip(), arguments=tuple(arguments))

    @staticmethod
    def of_send_text(text: str) -> ButtonBinding:
        if not text:
            raise ConfigurationError("发送文本不能为空")
        return ButtonBinding(kind="send_text", text=text)

    @staticmethod
    def of_none() -> ButtonBinding:
        return ButtonBinding(kind="none")

    @staticmethod
    def of_preset_switch(target_preset_id: str) -> ButtonBinding:
        return ButtonBinding(kind="preset_switch", target_preset_id=target_preset_id)

    @property
    def resolved_action(self) -> ButtonAction:
        return self.action if self.kind == "action" and self.action is not None else ButtonAction.UNMAPPED

    @property
    def display_name(self) -> str:
        if self.kind == "action" and self.action is not None:
            return self.action.display_name
        if self.kind in ("keyboard_shortcut", "hold_shortcut") and self.shortcut is not None:
            prefix = "按住" if self.kind == "hold_shortcut" else "自定义快捷键"
            return f"{prefix} · {self.shortcut.display_name}"
        if self.kind == "launch_program":
            suffix = " " + " ".join(self.arguments) if self.arguments else ""
            return f"启动程序 · {self.program}{suffix}"
        if self.kind == "send_text":
            preview = self.text if len(self.text) <= 16 else self.text[:16] + "…"
            return f"发送文本 · {preview}"
        if self.kind == "none":
            return "不执行动作"
        return "切换到另一配置"

    def to_json(self) -> dict:
        if self.kind == "action":
            return {"kind": "action", "action": self.action.value}
        if self.kind in ("keyboard_shortcut", "hold_shortcut"):
            return {"kind": self.kind, "shortcut": self.shortcut.to_json()}
        if self.kind == "launch_program":
            return {
                "kind": "launch_program",
                "program": self.program,
                "arguments": list(self.arguments),
            }
        if self.kind == "send_text":
            return {"kind": "send_text", "text": self.text}
        if self.kind == "none":
            return {"kind": "none"}
        return {"kind": "preset_switch", "target_preset_id": self.target_preset_id}

    @classmethod
    def from_json(cls, data: dict) -> ButtonBinding:
        kind = data["kind"]
        if kind == "action":
            return cls.of_action(ButtonAction(data["action"]))
        if kind == "keyboard_shortcut":
            return cls.of_shortcut(KeyboardShortcutSpec.from_json(data["shortcut"]))
        if kind == "hold_shortcut":
            return cls.of_hold_shortcut(KeyboardShortcutSpec.from_json(data["shortcut"]))
        if kind == "launch_program":
            return cls.of_launch_program(
                data["program"], tuple(data.get("arguments") or ())
            )
        if kind == "send_text":
            return cls.of_send_text(data["text"])
        if kind == "none":
            return cls.of_none()
        if kind == "preset_switch":
            return cls.of_preset_switch(data["target_preset_id"])
        raise ConfigurationError(f"未知的按键绑定类型：{kind}")


@dataclass(frozen=True)
class ButtonPreset:
    id: str
    name: str
    bindings: dict[RemoteButton, ButtonBinding]
    required_buttons: frozenset[RemoteButton]
    is_built_in: bool
    gestures: dict[RemoteButton, dict[str, ButtonBinding]] = field(default_factory=dict)

    def binding_for(self, button: RemoteButton, trigger: str = "single") -> ButtonBinding:
        if trigger != "single":
            return self.gestures.get(button, {}).get(trigger, ButtonBinding.of_none())
        return self.bindings.get(button, ButtonBinding.of_action(ButtonAction.UNMAPPED))

    def has_gestures(self, button: RemoteButton) -> bool:
        return any(b.kind != "none" for b in self.gestures.get(button, {}).values())

    def action_for(self, button: RemoteButton) -> ButtonAction:
        return self.binding_for(button).resolved_action


_POINTER_REQUIRED = frozenset(
    {
        RemoteButton.DPAD_UP,
        RemoteButton.DPAD_DOWN,
        RemoteButton.DPAD_LEFT,
        RemoteButton.DPAD_RIGHT,
        RemoteButton.CENTER,
        RemoteButton.BACK,
    }
)


def default_pointer_preset() -> ButtonPreset:
    return ButtonPreset(
        id="pointer",
        name="默认 · 鼠标指针",
        bindings={
            RemoteButton.VOICE: ButtonBinding.of_action(ButtonAction.VOICE_PUSH_TO_TALK),
            RemoteButton.DPAD_UP: ButtonBinding.of_action(ButtonAction.POINTER_MOVE_UP),
            RemoteButton.DPAD_DOWN: ButtonBinding.of_action(ButtonAction.POINTER_MOVE_DOWN),
            RemoteButton.DPAD_LEFT: ButtonBinding.of_action(ButtonAction.POINTER_MOVE_LEFT),
            RemoteButton.DPAD_RIGHT: ButtonBinding.of_action(ButtonAction.POINTER_MOVE_RIGHT),
            RemoteButton.CENTER: ButtonBinding.of_action(ButtonAction.KEYBOARD_RETURN),
            RemoteButton.BACK: ButtonBinding.of_action(ButtonAction.KEYBOARD_ESCAPE),
            RemoteButton.HOME: ButtonBinding.of_action(ButtonAction.HOME_PAGE_NAVIGATION),
            RemoteButton.MENU: ButtonBinding.of_action(ButtonAction.UNMAPPED),
            RemoteButton.VOLUME_UP: ButtonBinding.of_action(ButtonAction.SYSTEM_VOLUME_UP),
            RemoteButton.VOLUME_DOWN: ButtonBinding.of_action(ButtonAction.SYSTEM_VOLUME_DOWN),
            RemoteButton.TV: ButtonBinding.of_action(ButtonAction.MODE_TOGGLE_POINTER_DIRECTIONAL),
            RemoteButton.POWER: ButtonBinding.of_action(ButtonAction.TARGET_LAUNCH_OR_FOCUS),
        },
        required_buttons=_POINTER_REQUIRED,
        is_built_in=True,
    )


def default_standard_preset() -> ButtonPreset:
    """New installations use navigation keys; existing pointer presets retain their meaning."""
    base = default_pointer_preset()
    bindings = dict(base.bindings)
    for button, action in ((RemoteButton.DPAD_UP, ButtonAction.KEYBOARD_ARROW_UP),
                           (RemoteButton.DPAD_DOWN, ButtonAction.KEYBOARD_ARROW_DOWN),
                           (RemoteButton.DPAD_LEFT, ButtonAction.KEYBOARD_ARROW_LEFT),
                           (RemoteButton.DPAD_RIGHT, ButtonAction.KEYBOARD_ARROW_RIGHT)):
        bindings[button] = ButtonBinding.of_action(action)
    bindings[RemoteButton.TV] = ButtonBinding.of_none()
    return ButtonPreset("standard", "默认 · 普通方向键", bindings, base.required_buttons, True)


@dataclass(frozen=True)
class ButtonPresetCatalog:
    user_presets: list[ButtonPreset] = field(default_factory=list)

    @property
    def all_presets(self) -> list[ButtonPreset]:
        return [default_standard_preset(), default_pointer_preset(), *self.user_presets]

    def preset(self, preset_id: str) -> ButtonPreset:
        for preset in self.all_presets:
            if preset.id == preset_id:
                return preset
        raise ConfigurationError(f"找不到按键配置「{preset_id}」。请选择仍存在的配置")

    def validate(self, allow_free_mapping: bool = False) -> None:
        """校验配置集合。

        allow_free_mapping=True 用于「遥控器当可编程小键盘」模式：任意按键可绑定任意
        动作/快捷键，只保留危险快捷键黑名单与结构性校验；内置预设的严格规则不变。
        """
        identifiers = [p.id for p in self.all_presets]
        if len(set(identifiers)) != len(identifiers):
            raise ConfigurationError("按键配置标识重复，无法安全保存")
        known_ids = set(identifiers)
        for preset in self.user_presets:
            if not preset.id.startswith("user.") or len(preset.id) <= 5:
                raise ConfigurationError("自定义配置标识无效")
            name = preset.name.strip()
            if not name or len(name) > 40:
                raise ConfigurationError("配置名称需为 1–40 个字符")
            for button, gestures in preset.gestures.items():
                if not allow_free_mapping and button is RemoteButton.VOICE and any(b.kind != "none" for b in gestures.values()):
                    raise ConfigurationError("语音键使用按住说话，不支持双击或长按映射")
                if any(trigger not in ("double", "long") for trigger in gestures):
                    raise ConfigurationError("未知的按键触发方式")
                for binding in gestures.values():
                    self._validate_free_preset(
                        ButtonPreset(preset.id, name, {button: binding}, frozenset(), False), known_ids
                    )
            if allow_free_mapping:
                self._validate_free_preset(preset, known_ids)
                continue
            if preset.binding_for(RemoteButton.VOICE) != ButtonBinding.of_action(
                ButtonAction.VOICE_PUSH_TO_TALK
            ):
                raise ConfigurationError("语音键必须保留为按住说话")
            if preset.binding_for(RemoteButton.MENU) != ButtonBinding.of_action(
                ButtonAction.UNMAPPED
            ):
                raise ConfigurationError("菜单键保留系统原生行为，不能由米遥覆盖")
            for button, binding in preset.bindings.items():
                if binding.kind == "preset_switch":
                    if button is not RemoteButton.TV:
                        raise ConfigurationError("只有 TV 键可以切换按键配置")
                    if binding.target_preset_id == preset.id or binding.target_preset_id not in known_ids:
                        raise ConfigurationError("TV 键的目标配置不存在或与当前配置相同")
                elif binding.kind == "action":
                    action = binding.action
                    if action is ButtonAction.VOICE_PUSH_TO_TALK and button is not RemoteButton.VOICE:
                        raise ConfigurationError("只有语音键可以使用按住说话")
                    if action is ButtonAction.HOME_PAGE_NAVIGATION and button is not RemoteButton.HOME:
                        raise ConfigurationError("HOME 双击导航只可分配给 HOME 键")
                    if (
                        action is ButtonAction.MODE_TOGGLE_POINTER_DIRECTIONAL
                        and button is not RemoteButton.TV
                    ):
                        raise ConfigurationError("方向环模式切换只可分配给 TV 键")
                elif binding.kind == "keyboard_shortcut":
                    if not button.is_user_editable:
                        raise ConfigurationError("语音键与菜单键不能使用自定义快捷键")

    @staticmethod
    def _validate_free_preset(preset: ButtonPreset, known_ids: set[str]) -> None:
        for button, binding in preset.bindings.items():
            if binding.kind == "preset_switch":
                if binding.target_preset_id == preset.id:
                    raise ConfigurationError("不能把按键切换到自己这套配置")
                if binding.target_preset_id not in known_ids:
                    raise ConfigurationError("目标配置不存在")
            elif binding.kind in ("keyboard_shortcut", "hold_shortcut"):
                if binding.shortcut is None:
                    raise ConfigurationError(f"{button.display_name} 的快捷键为空")
            elif binding.kind == "launch_program":
                if not binding.program.strip():
                    raise ConfigurationError(f"{button.display_name} 没有填写程序路径")
            elif binding.kind == "send_text":
                if not binding.text:
                    raise ConfigurationError(f"{button.display_name} 的文本为空")
            elif binding.kind == "action" and binding.action is None:
                raise ConfigurationError(f"{button.display_name} 的动作无效")


# ---------------------------------------------------------------------------
# HID 事件收敛


def normalized_usage(element_usage: int | None, raw_values: list[int]) -> int | None:
    """部分 HID 栈把 usage 报为 0xFFFFFFFF，需从原始值里取第一个非零字节。"""
    if element_usage is None:
        return None
    if element_usage != 0xFFFFFFFF:
        return element_usage
    for value in raw_values:
        if value == 0:
            continue
        remaining = value & 0xFFFFFFFFFFFFFFFF
        while remaining:
            byte = remaining & 0xFF
            if byte != 0:
                return byte
            remaining >>= 8
    return None


@dataclass(frozen=True)
class HIDUsageKey:
    page: int
    usage: int


class HIDButtonEvent(enum.Enum):
    DOWN = "down"
    UP = "up"


class HIDButtonEventReducer:
    """把原始 HID 按下/重复/松手收敛为成对的 (button, DOWN/UP) 事件。"""

    def __init__(self) -> None:
        self._active_key: HIDUsageKey | None = None
        self._active_element: HIDUsageKey | None = None
        self._active_button: RemoteButton | None = None

    def reduce(
        self,
        page: int,
        element_usage: int,
        raw_value: int,
        buttons_by_usage: dict[HIDUsageKey, RemoteButton],
    ) -> list[tuple[RemoteButton, HIDButtonEvent]]:
        signature = HIDUsageKey(page=page, usage=element_usage)
        if raw_value == 0:
            if signature != self._active_element:
                return []
            button = self._active_button
            self.clear()
            return [(button, HIDButtonEvent.UP)] if button is not None else []

        usage = normalized_usage(element_usage, [raw_value])
        if usage is None:
            return []
        key = HIDUsageKey(page=page, usage=usage)
        if key == self._active_key:
            return []

        events: list[tuple[RemoteButton, HIDButtonEvent]] = []
        if self._active_button is not None:
            events.append((self._active_button, HIDButtonEvent.UP))
        self._active_key = key
        self._active_element = signature
        self._active_button = buttons_by_usage.get(key)
        if self._active_button is not None:
            events.append((self._active_button, HIDButtonEvent.DOWN))
        return events

    def clear(self) -> None:
        self._active_key = None
        self._active_element = None
        self._active_button = None
