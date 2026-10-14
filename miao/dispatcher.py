"""按键动作派发（纯逻辑，可单测）。

把 (RemoteButton, DOWN/UP) 事件按当前预设翻译成执行器调用。
特殊语义（对齐原项目）：
- HOME：单击 PageDown，350ms 内双击 PageUp；
- TV：切换方向环"移动指针 / 方向键"模式，或跳转到目标预设；
- 方向环：指针模式下按住持续移动，方向键模式下单次按键。
"""

from __future__ import annotations

import enum
import time
from collections.abc import Callable
from typing import Protocol

from .buttons import (
    ButtonAction,
    ButtonPreset,
    ButtonPresetCatalog,
    RemoteButton,
)

HOME_DOUBLE_CLICK_MS = 0.350
LONG_PRESS_SECONDS = 0.600

VK_BY_ACTION = {
    ButtonAction.KEYBOARD_ARROW_UP: 0x26,  # VK_UP
    ButtonAction.KEYBOARD_ARROW_DOWN: 0x28,
    ButtonAction.KEYBOARD_ARROW_LEFT: 0x25,
    ButtonAction.KEYBOARD_ARROW_RIGHT: 0x27,
    ButtonAction.KEYBOARD_RETURN: 0x0D,
    ButtonAction.KEYBOARD_ESCAPE: 0x1B,
    ButtonAction.KEYBOARD_PAGE_UP: 0x21,
    ButtonAction.KEYBOARD_PAGE_DOWN: 0x22,
    ButtonAction.SYSTEM_VOLUME_UP: 0xAF,
    ButtonAction.SYSTEM_VOLUME_DOWN: 0xAE,
}

POINTER_DIRECTIONS = {
    ButtonAction.POINTER_MOVE_UP: (0, -1),
    ButtonAction.POINTER_MOVE_DOWN: (0, 1),
    ButtonAction.POINTER_MOVE_LEFT: (-1, 0),
    ButtonAction.POINTER_MOVE_RIGHT: (1, 0),
}

DIRECTIONAL_BY_POINTER = {
    ButtonAction.POINTER_MOVE_UP: ButtonAction.KEYBOARD_ARROW_UP,
    ButtonAction.POINTER_MOVE_DOWN: ButtonAction.KEYBOARD_ARROW_DOWN,
    ButtonAction.POINTER_MOVE_LEFT: ButtonAction.KEYBOARD_ARROW_LEFT,
    ButtonAction.POINTER_MOVE_RIGHT: ButtonAction.KEYBOARD_ARROW_RIGHT,
}


class ControlMode(enum.Enum):
    POINTER = "pointer"  # 方向环移动鼠标
    DIRECTIONAL = "directional"  # 方向环发方向键


class Executor(Protocol):
    def tap_key(self, vk: int) -> None: ...
    def press_shortcut(self, vk: int, modifiers: tuple[int, ...]) -> None: ...
    def start_pointer_move(self, dx: int, dy: int) -> None: ...
    def stop_pointer_move(self) -> None: ...
    def scroll(self, amount: int) -> None: ...
    def focus_target(self) -> None: ...
    def launch_or_focus_target(self) -> None: ...
    def previous_task(self) -> None: ...
    def next_task(self) -> None: ...
    def on_mode_changed(self, mode: ControlMode) -> None: ...
    def on_preset_changed(self, preset: ButtonPreset) -> None: ...


class HoldShortcutExecutor(Protocol):
    """可选能力：按住/释放快捷键、启动程序、注入文本。"""

    def keys_down(self, vk: int, modifiers: tuple[int, ...] = ()) -> None: ...
    def keys_up(self, vk: int, modifiers: tuple[int, ...] = ()) -> None: ...
    def launch(self, program: str, arguments: tuple[str, ...] = ()) -> None: ...
    def type_text(self, text: str) -> None: ...


class ButtonDispatcher:
    def __init__(
        self,
        catalog: ButtonPresetCatalog,
        preset_id: str,
        executor: Executor,
        now: Callable[[], float] = time.monotonic,
        on_button_event: Callable[[RemoteButton, bool], None] | None = None,
        on_voice_hold: Callable[[bool], None] | None = None,
        on_handsfree_toggle: Callable[[], None] | None = None,
    ) -> None:
        self.catalog = catalog
        self.preset = catalog.preset(preset_id)
        self.executor = executor
        self._now = now
        self.on_button_event = on_button_event or (lambda _button, _down: None)
        self.on_voice_hold = on_voice_hold or (lambda _down: None)
        self.on_handsfree_toggle = on_handsfree_toggle or (lambda: None)
        self._voice_held: set[RemoteButton] = set()
        self.mode = ControlMode.POINTER
        self._last_home_down = -1.0
        self._home_click_pending = False
        self._held: dict[RemoteButton, tuple[int, tuple[int, ...]]] = {}
        self._presses: dict[RemoteButton, tuple[float, ButtonPreset]] = {}
        self._long_fired: set[RemoteButton] = set()
        self._pending_clicks: dict[RemoteButton, tuple[float, ButtonPreset]] = {}

    # -- 事件入口 ----------------------------------------------------------

    @staticmethod
    def _modifiers_of(shortcut) -> tuple[int, ...]:
        return tuple(sorted(m.vk_code for m in shortcut.modifiers))

    def button_down(self, button: RemoteButton) -> None:
        self.on_button_event(button, True)
        if self.preset.has_gestures(button):
            if button not in self._presses:
                self._presses[button] = (self._now(), self.preset)
            return
        binding = self.preset.binding_for(button)
        self._execute_binding(button, binding)

    def _execute_binding(self, button, binding) -> None:
        if (button is RemoteButton.VOICE and self.preset.has_gestures(button)
                and binding.kind == "action" and binding.action is ButtonAction.VOICE_PUSH_TO_TALK):
            if button not in self._voice_held:
                self._voice_held.add(button)
                self.on_voice_hold(True)
            return
        if binding.kind == "preset_switch":
            self._switch_preset(binding.target_preset_id)
            return
        if binding.kind == "keyboard_shortcut" and binding.shortcut is not None:
            self.executor.press_shortcut(
                binding.shortcut.vk_code, self._modifiers_of(binding.shortcut)
            )
            return
        if binding.kind == "hold_shortcut" and binding.shortcut is not None:
            # 记下来，抬起时释放；中途换配置/断开也要能释放干净
            self._held[button] = (
                binding.shortcut.vk_code,
                self._modifiers_of(binding.shortcut),
            )
            keys_down = getattr(self.executor, "keys_down", None)
            if keys_down is not None:
                keys_down(binding.shortcut.vk_code, self._modifiers_of(binding.shortcut))
            return
        if binding.kind == "launch_program":
            launch = getattr(self.executor, "launch", None)
            if launch is not None:
                launch(binding.program, binding.arguments)
            return
        if binding.kind == "send_text":
            type_text = getattr(self.executor, "type_text", None)
            if type_text is not None:
                type_text(binding.text)
            return
        if binding.kind == "none":
            return
        self._dispatch_action(binding.resolved_action, down=True)

    def button_up(self, button: RemoteButton) -> None:
        self.on_button_event(button, False)
        press = self._presses.pop(button, None)
        if press is not None:
            began, preset = press
            if button in self._long_fired:
                self._long_fired.discard(button)
                self._release_button(button)
                self.executor.stop_pointer_move()
                return
            if self._now() - began >= LONG_PRESS_SECONDS and preset.binding_for(button, "long").kind != "none":
                self._pending_clicks.pop(button, None)
                self._tap_binding(button, preset.binding_for(button, "long"))
                return
            double = preset.binding_for(button, "double")
            if double.kind != "none":
                pending = self._pending_clicks.pop(button, None)
                if pending and pending[1] is preset and self._now() - pending[0] < HOME_DOUBLE_CLICK_MS:
                    self._tap_binding(button, double)
                else:
                    if pending:
                        self._tap_binding(button, pending[1].binding_for(button))
                    self._pending_clicks[button] = (self._now(), preset)
            else:
                self._tap_binding(button, preset.binding_for(button))
            return
        if self._release_button(button):
            return
        binding = self.preset.binding_for(button)
        if binding.kind != "action" or binding.action is None:
            return
        if binding.action in POINTER_DIRECTIONS and self.mode is ControlMode.POINTER:
            self.executor.stop_pointer_move()

    def _release_button(self, button: RemoteButton) -> bool:
        if button in self._voice_held:
            self._voice_held.discard(button)
            self.on_voice_hold(False)
            return True
        held = self._held.pop(button, None)
        if held is not None:
            keys_up = getattr(self.executor, "keys_up", None)
            if keys_up is not None:
                keys_up(*held)
            return True
        return False

    def _tap_binding(self, button, binding) -> None:
        # Speech needs a held gesture; a click ending now has no audio interval.
        if button is RemoteButton.VOICE and binding.kind == "action" and binding.action is ButtonAction.VOICE_PUSH_TO_TALK:
            return
        self._execute_binding(button, binding)
        self._release_button(button)
        if binding.kind == "action" and binding.action in POINTER_DIRECTIONS:
            self.executor.stop_pointer_move()

    def release_all(self) -> None:
        """释放所有仍处于按下状态的快捷键（停止服务/断开连接时调用）。"""
        keys_up = getattr(self.executor, "keys_up", None)
        for _button, (vk, modifiers) in list(self._held.items()):
            if keys_up is not None:
                keys_up(vk, modifiers)
        self._held.clear()
        if self._voice_held:
            self._voice_held.clear()
            self.on_voice_hold(False)
        self._presses.clear()
        self._long_fired.clear()
        self._pending_clicks.clear()
        self._home_click_pending = False
        self.executor.stop_pointer_move()

    def tick(self) -> None:
        """处理 HOME 单击延迟（双击窗口过后才发 PageDown）。"""
        now = self._now()
        for button, (began, preset) in list(self._presses.items()):
            binding = preset.binding_for(button, "long")
            if button not in self._long_fired and binding.kind != "none" and now - began >= LONG_PRESS_SECONDS:
                self._long_fired.add(button)
                self._pending_clicks.pop(button, None)
                self._execute_binding(button, binding)
        for button, (ended, preset) in list(self._pending_clicks.items()):
            if button not in self._presses and now - ended >= HOME_DOUBLE_CLICK_MS:
                self._pending_clicks.pop(button, None)
                self._tap_binding(button, preset.binding_for(button))
        if self._home_click_pending and self._now() - self._last_home_down >= HOME_DOUBLE_CLICK_MS:
            self._home_click_pending = False
            self.executor.tap_key(0x22)  # VK_NEXT = PageDown

    # -- 内部 ---------------------------------------------------------------

    def _dispatch_action(self, action: ButtonAction, down: bool) -> None:
        if action is ButtonAction.VOICE_HANDSFREE_TOGGLE:
            self.on_handsfree_toggle()
        elif action in POINTER_DIRECTIONS:
            if self.mode is ControlMode.POINTER:
                dx, dy = POINTER_DIRECTIONS[action]
                self.executor.start_pointer_move(dx, dy)
            else:
                self.executor.tap_key(VK_BY_ACTION[DIRECTIONAL_BY_POINTER[action]])
        elif action is ButtonAction.HOME_PAGE_NAVIGATION:
            now = self._now()
            if self._home_click_pending and now - self._last_home_down < HOME_DOUBLE_CLICK_MS:
                self._home_click_pending = False
                self.executor.tap_key(0x21)  # VK_PRIOR = PageUp
            else:
                self._home_click_pending = True
            self._last_home_down = now
        elif action is ButtonAction.MODE_TOGGLE_POINTER_DIRECTIONAL:
            self.mode = (
                ControlMode.DIRECTIONAL if self.mode is ControlMode.POINTER else ControlMode.POINTER
            )
            self.executor.on_mode_changed(self.mode)
        elif action in VK_BY_ACTION:
            self.executor.tap_key(VK_BY_ACTION[action])
        elif action is ButtonAction.POINTER_SCROLL_UP:
            self.executor.scroll(1)
        elif action is ButtonAction.POINTER_SCROLL_DOWN:
            self.executor.scroll(-1)
        elif action is ButtonAction.TARGET_FOCUS:
            self.executor.focus_target()
        elif action is ButtonAction.TARGET_LAUNCH_OR_FOCUS:
            self.executor.launch_or_focus_target()
        elif action is ButtonAction.TARGET_PREVIOUS_TASK:
            self.executor.previous_task()
        elif action is ButtonAction.TARGET_NEXT_TASK:
            self.executor.next_task()

    def _switch_preset(self, target_id: str | None) -> None:
        if target_id is None:
            return
        try:
            self.release_all()
            self.preset = self.catalog.preset(target_id)
        except Exception:
            return
        self.executor.on_preset_changed(self.preset)
