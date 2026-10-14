"""应用编排：BLE 遥控器音频 → 虚拟麦克风（供豆包识别）+ 按键 → 可编程快捷键。

数据流：
  遥控器按住语音键
    → BLE ATVV 音频（miao.ble_bridge / miao.session）
    → 每帧 PCM 经 frame_sink 写进 VB-CABLE（miao.virtual_mic）
    → 豆包等应用从 CABLE Output 收音并转成文字
  同时：兼容模式通过已安装豆包的本地控制接口启动语音，松手停止。

按键流：
  遥控器按键（miao.keyhook 识别设备并吞掉）
    → miao.dispatcher 查当前配置的绑定
    → miao.executor 执行（快捷键 / 按住快捷键 / 启动程序 / 发送文本）
"""

from __future__ import annotations

import asyncio
import logging
import json
import time
import threading
from collections.abc import Callable

from .ble_bridge import BLEVoiceBridge
from . import ConfigurationError
from .buttons import (
    ButtonAction,
    ButtonPresetCatalog,
    KeyboardShortcutSpec,
    RemoteButton,
    ShortcutModifier,
)
from .config import Config, app_data_dir, load_config, save_config
from .connection_policy import (
    BLEDeviceSelectionPolicy,
    VoiceConnectionMode,
    VoiceReconnectPolicy,
)
from .dispatcher import ButtonDispatcher
from .executor import WindowsExecutor
from .doubao_voice import DoubaoVoiceController
from .enhancement import EnhancementManager
from .extra_keys import ExtraKeyBridge
from .keyhook import RemoteKeyHook
from .preset_store import ButtonPresetStore, LoadState
from .session import SessionState, StateChanged
from .virtual_mic import VirtualMicStream

log = logging.getLogger("miao.app")


class AppStatus:
    """界面状态：state 是机器可读的状态名，detail 是给人看的说明。"""

    def __init__(self) -> None:
        self.state = "disconnected"
        self.detail = ""
        self.listeners: list[Callable[[AppStatus], None]] = []

    def update(self, state: str, detail: str = "") -> None:
        self.state, self.detail = state, detail
        for listener in self.listeners:
            listener(self)


class MiAoApp:
    def __init__(self) -> None:
        self.config: Config = load_config()
        self.status = AppStatus()
        self.preset_store = ButtonPresetStore(app_data_dir() / "button-presets.json")
        snapshot = self.preset_store.load()
        if snapshot.state is LoadState.RECOVERED_INVALID:
            log.warning("按键配置已损坏，已隔离到 %s", snapshot.detail)
        self.catalog: ButtonPresetCatalog = snapshot.catalog
        self.active_preset_id: str = snapshot.active_preset_id or self.config.active_preset_id
        self.dispatcher: ButtonDispatcher | None = None
        self._native_doubao_keys = None
        self._native_handsfree = False
        self._doubao_native = DoubaoVoiceController(
            on_error=lambda text: self.status.update("command", text),
            before_stop=lambda: self.mic.drain(),
        )
        self.executor = WindowsExecutor(
            submit_text=self.config.submit_enabled,
            press_enter=self.config.press_enter_after_paste,
            on_status=lambda text: self.status.update("command", text),
            shortcut_bridge=self._bridge_voice_shortcut,
        )
        self.mic = VirtualMicStream(
            name_hint=self.config.resolved_mic_name_hint,
            gain_db=self.config.mic_gain_db,
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ble_thread: threading.Thread | None = None
        self._bridge: BLEVoiceBridge | None = None
        self._keyhook: RemoteKeyHook | None = None
        self._running = threading.Event()
        self._doubao_held = False
        self._doubao_keys: tuple[int, tuple[int, ...]] | None = None
        self._handsfree_requested = False
        self._handsfree_active = False
        self.extra_keys_connected = False
        self.extra_keys_seen: set[RemoteButton] = set()
        self._extra_key_bridge: ExtraKeyBridge | None = None
        self.enhancement = EnhancementManager(
            on_status=lambda text: self.status.update('enhancement', text)
        )
        # 界面可以挂载的按键事件监听（遥控器按下/抬起 → 高亮）
        self.button_listeners: list[Callable[[RemoteButton, bool], None]] = []

    # -- 生命周期 ------------------------------------------------------------

    def start(self) -> None:
        if self._running.is_set():
            return
        self._running.set()
        self._doubao_native.start()
        self._start_keyhook()
        self._extra_key_bridge = ExtraKeyBridge(
            self._on_extra_key, self._on_extra_connection, self.config.device_address
        )
        try:
            self._extra_key_bridge.start()
        except OSError as error:
            log.warning("增强按键接收器未启动：%s", error)
        try:
            self.enhancement.start()
            if self.config.enhanced_keys_enabled:
                self.enhancement.set_enabled(True)
        except OSError as error:
            log.warning('增强后台控制未启动：%s', error)
        self._start_mic()
        self._ble_thread = threading.Thread(target=self._ble_main, name="miao-ble", daemon=True)
        self._ble_thread.start()

    def stop(self) -> None:
        self._running.clear()
        self.enhancement.stop()
        if self._extra_key_bridge:
            self._extra_key_bridge.stop()
            self._extra_key_bridge = None
        if self.dispatcher is not None:
            self.dispatcher.release_all()  # 别把按住的修饰键留在按下状态
        self._release_doubao()
        self._stop_handsfree()
        self._doubao_native.close()
        if self._bridge:
            self._bridge.stop()
        if self._keyhook:
            self._keyhook.stop()
        self.mic.stop()

    @property
    def running(self) -> bool:
        return self._running.is_set()

    def reconnect_now(self) -> None:
        """托盘/界面「手动重连」：让当前连接循环立即重试。"""
        if self._bridge and self._loop:
            self._bridge.reconnect_policy.reset()
            self._loop.call_soon_threadsafe(self._bridge.stop)

    # -- 虚拟麦克风 -----------------------------------------------------------

    def _start_mic(self) -> None:
        if not self.config.mic_forward_enabled:
            self.status.update("mic_off", "虚拟麦克风转发已关闭")
            return
        try:
            self.mic.start()
        except Exception as error:  # noqa: BLE001 - 缺驱动不应导致整个程序起不来
            log.warning("虚拟麦克风不可用：%s", error)
            self.status.update("mic_error", str(error))

    # -- BLE 线程 ------------------------------------------------------------

    def _ble_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        while self._running.is_set():
            self._bridge = BLEVoiceBridge(
                selection=BLEDeviceSelectionPolicy(
                    preferred_identifier=self.config.device_address,
                    name_filter=self.config.device_name_filter,
                ),
                reconnect_policy=VoiceReconnectPolicy(
                    VoiceConnectionMode(self.config.voice_connection_mode)
                ),
                on_state=self._on_state,
                frame_sink=self._on_audio_frame,
            )
            self._bridge.reconnect_policy.reset()
            try:
                self._loop.run_until_complete(self._bridge.run())
            except Exception as error:  # noqa: BLE001
                log.warning("BLE 循环退出：%s", error)
            if not self._running.is_set():
                break
            self._running.wait(1.0)  # 手动重连后快速重启一轮

    def _on_audio_frame(self, samples: list[int], sample_rate: int) -> None:
        """BLE 线程：每一帧 PCM 直接送进虚拟麦克风（内部只入队，不阻塞）。"""
        self._audio_frames = getattr(self, "_audio_frames", 0) + 1
        if self._audio_frames in (1, 50):
            mean = sum(samples) / len(samples) if samples else 0
            rms = (sum(x*x for x in samples) / len(samples)) ** .5 if samples else 0
            log.info("语音音频已收到 frame=%d samples=%d rate=%d rms=%.1f mean=%.1f", self._audio_frames, len(samples), sample_rate, rms, mean)
        self.mic.write_pcm(samples, sample_rate)

    def _on_state(self, event: StateChanged) -> None:
        log.info("语音状态 %s %s", event.state.value, event.detail)
        self.status.update(event.state.value, event.detail)
        if event.state is SessionState.STREAMING:
            self._audio_frames = 0
            if getattr(self, "_handsfree_requested", False):
                if not self._handsfree_active:
                    shortcut = self.handsfree_shortcut()
                    if shortcut:
                        self.executor.press_shortcut(shortcut.vk_code, tuple(sorted(m.vk_code for m in shortcut.modifiers)))
                        self._handsfree_active = True
                        self.status.update("streaming", "免按输入中 · 再按 TV 结束")
                return
            # 语音键按下：开始喂音频 + 按住豆包快捷键
            self._press_doubao()
        elif event.state is SessionState.READY and event.detail.startswith("AUDIO_STOP"):
            self._release_doubao()
            self._finish_handsfree()
            if getattr(self, "_native_doubao_keys", None) is not None:
                self._doubao_native.stop_voice()
            log.info("本段音频 frames=%d mic_error=%s", getattr(self, "_audio_frames", 0), getattr(self.mic, "last_error", None))
            if event.detail == "AUDIO_STOP reason=handsfree-no-audio":
                self.status.update("command", "遥控器未传回免按音频；请使用长按语音键。")
        elif event.state is SessionState.READY and event.detail.startswith("MIC_OPEN"):
            self._finish_handsfree()
        elif event.state is SessionState.DISCONNECTED:
            self._release_doubao()
            self._finish_handsfree()
            if self.dispatcher:
                self.dispatcher.release_all()

    # -- 豆包「按住说话」------------------------------------------------------

    def _bridge_voice_shortcut(self, phase: str, vk: int, modifiers: tuple[int, ...]) -> bool:
        keys = (vk, tuple(sorted(modifiers)))
        # Always release the original combination, including after settings change.
        if phase == "up" and keys == self._native_doubao_keys:
            self._native_doubao_keys = None
            if not self._native_handsfree:
                self._doubao_native.stop_voice()
            return True
        if not self.config.doubao_native_control:
            return False
        shortcut = self.doubao_shortcut() if phase == "down" else self.handsfree_shortcut() if phase == "tap" else None
        if shortcut is None or keys != (shortcut.vk_code, tuple(sorted(m.vk_code for m in shortcut.modifiers))):
            return False
        if phase == "down":
            self._native_doubao_keys = keys
            if not self._handsfree_requested:
                self._doubao_native.start_voice("hold")
        else:
            self._native_handsfree = True
            self._doubao_native.start_voice("handsfree")
        return True

    def doubao_shortcut(self) -> KeyboardShortcutSpec | None:
        vk = int(self.config.doubao_hotkey_vk or 0)
        if not 0 < vk < 256:
            return None
        modifiers = []
        for raw in self.config.doubao_hotkey_modifiers:
            try:
                modifiers.append(
                    raw if isinstance(raw, ShortcutModifier) else ShortcutModifier(str(raw).lower())
                )
            except ValueError:
                log.warning("忽略无法识别的修饰键：%r", raw)
        try:
            if 0x30 <= vk <= 0x5A:
                label = chr(vk)
            elif 0x70 <= vk <= 0x87:
                label = f"F{vk - 0x6F}"
            else:
                label = f"VK{vk:02X}"
            return KeyboardShortcutSpec(vk_code=vk, modifiers=modifiers, key_label=label)
        except Exception as error:  # noqa: BLE001
            log.warning("豆包快捷键配置无效：%s", error)
            return None

    def _press_doubao(self, *, mapped: bool = False) -> None:
        if not self.config.mic_forward_enabled:
            return
        if getattr(self, "_handsfree_requested", False):
            return
        if self._doubao_held:
            return
        if self.dispatcher is not None and not mapped:
            if self.dispatcher.preset.has_gestures(RemoteButton.VOICE):
                return
            binding = self.dispatcher.preset.binding_for(RemoteButton.VOICE)
            if binding.kind != "action" or binding.action is not ButtonAction.VOICE_PUSH_TO_TALK:
                return
        shortcut = self.doubao_shortcut()
        if shortcut is None:
            return
        modifiers = tuple(sorted(m.vk_code for m in shortcut.modifiers))
        self.executor.keys_down(shortcut.vk_code, modifiers)
        self._doubao_keys = (shortcut.vk_code, modifiers)
        self._doubao_held = True

    def _release_doubao(self) -> None:
        if not self._doubao_held:
            return
        self._doubao_held = False
        keys, self._doubao_keys = self._doubao_keys, None
        if keys:
            self.executor.keys_up(*keys)

    def handsfree_shortcut(self) -> KeyboardShortcutSpec | None:
        try:
            vk = self.config.doubao_handsfree_hotkey_vk
            label = "空格" if vk == 32 else chr(vk) if 0x30 <= vk <= 0x5A else f"VK{vk:02X}"
            return KeyboardShortcutSpec(vk,
                [ShortcutModifier(m) for m in self.config.doubao_handsfree_hotkey_modifiers], label)
        except (ValueError, ConfigurationError):
            return None

    def _finish_handsfree(self) -> None:
        self._handsfree_requested = False
        if getattr(self, "_handsfree_active", False):
            self._handsfree_active = False
            # Doubao handsfree ends on any key; re-sending its start chord can
            # close and immediately reopen it. Escape is a single finish key.
            if getattr(self, "_native_handsfree", False):
                self._native_handsfree = False
                self._doubao_native.stop_voice()
            else:
                self.executor.tap_key(0x1B)

    def _stop_handsfree(self) -> None:
        pending = getattr(self, "_handsfree_requested", False)
        self._finish_handsfree()
        loop, bridge = getattr(self, "_loop", None), getattr(self, "_bridge", None)
        if pending and loop is not None and loop.is_running() and bridge is not None:
            loop.call_soon_threadsafe(bridge.set_handsfree, False)

    def _toggle_handsfree(self) -> None:
        if self._loop is None or not self._loop.is_running() or self._bridge is None:
            self.status.update("command", "遥控器未连接，无法启动免按麦克风。")
            return
        self._loop.call_soon_threadsafe(self._toggle_handsfree_on_loop)

    def _toggle_handsfree_on_loop(self) -> None:
        if getattr(self, "_handsfree_requested", False):
            self._finish_handsfree()
            self._bridge.set_handsfree(False)
            return
        if not self.config.mic_forward_enabled or not self.mic.running:
            self.status.update("command", "请先启用并启动虚拟麦克风。")
            return
        if self.handsfree_shortcut() is None:
            self.status.update("command", "请先设置免按输入快捷键。")
            return
        self._release_doubao()
        self._handsfree_requested = True
        if not self._bridge.set_handsfree(True):
            self._handsfree_requested = False
            self.status.update("command", "请等遥控器就绪并松开语音键，再按 TV。")

    # -- 按键钩子 -------------------------------------------------------------

    def _start_keyhook(self) -> None:
        self.dispatcher = ButtonDispatcher(
            catalog=self.catalog,
            preset_id=self.active_preset_id,
            executor=self.executor,
            on_button_event=self._on_button_event,
            on_voice_hold=lambda down: self._press_doubao(mapped=True) if down else self._release_doubao(),
            on_handsfree_toggle=self._toggle_handsfree,
        )
        try:
            self._keyhook = RemoteKeyHook(self.dispatcher)
            self._keyhook.start()
        except RuntimeError as error:
            log.error("按键钩子不可用：%s", error)
            self._keyhook = None

    def _on_button_event(self, button: RemoteButton, down: bool) -> None:
        if button in {RemoteButton.BACK, RemoteButton.VOLUME_UP, RemoteButton.VOLUME_DOWN}:
            try:
                with (app_data_dir()/"extra-key-events.jsonl").open("a",encoding="utf-8") as output:
                    output.write(json.dumps({"time":time.time(),"button":button.value,"down":down})+"\n")
            except OSError:
                pass
        for listener in list(self.button_listeners):
            try:
                listener(button, down)
            except Exception as error:  # noqa: BLE001 - 界面出错不能影响按键链路
                log.debug("按键事件监听失败：%s", error)

    def _on_extra_connection(self, connected: bool) -> None:
        self.extra_keys_connected = connected
        if self._keyhook is not None:
            self._keyhook.set_driver_owned(connected)
        self.status.update("extra_keys", "增强按键已连接" if connected else "增强按键已断开")

    def _on_extra_key(self, button: RemoteButton, down: bool) -> None:
        if down:
            self.extra_keys_seen.add(button)
        if self._keyhook is not None:
            self._keyhook.queue_button_event(button, down)
        elif self.dispatcher is not None:
            if down:
                self.dispatcher.button_down(button)
            else:
                self.dispatcher.button_up(button)
        log.info("增强按键 %s %s", button.value, "down" if down else "up")

    # -- 供界面调用 -----------------------------------------------------------

    def set_enhanced_keys(self, enabled: bool) -> None:
        self.config.enhanced_keys_enabled = bool(enabled)
        save_config(self.config)
        self.enhancement.set_enabled(enabled)

    def trigger_binding(self, button: RemoteButton) -> None:
        """界面点击某个按键 = 触发它的绑定（与物理按下走同一条链路）。"""
        if self.dispatcher is None:
            return
        binding = self.dispatcher.preset.binding_for(button)
        self._on_button_event(button, True)
        self.dispatcher.button_down(button)
        if binding.kind in ("hold_shortcut",):
            # 按住类绑定无法靠单击维持，立即释放，让用户看到完整效果
            self.dispatcher.button_up(button)
        else:
            self.dispatcher.button_up(button)

    def apply_preset(self, preset_id: str) -> None:
        if self.dispatcher is None:
            return
        self.dispatcher.release_all()
        self._release_doubao()
        self._stop_handsfree()
        self.active_preset_id = preset_id
        self.config.active_preset_id = preset_id
        self.dispatcher.preset = self.catalog.preset(preset_id)

    def reload_catalog(self, catalog: ButtonPresetCatalog, active_preset_id: str) -> None:
        self._release_doubao()
        self._stop_handsfree()
        self.catalog = catalog
        if self.dispatcher is not None:
            self.dispatcher.release_all()
            self.dispatcher.catalog = catalog
            self.dispatcher.preset = catalog.preset(active_preset_id)
        self.active_preset_id = active_preset_id
        self.config.active_preset_id = active_preset_id

    def save_presets(self) -> None:
        self.preset_store.save(
            self.catalog, self.active_preset_id, allow_free_mapping=True
        )

    def restart_mic(self) -> None:
        self.mic.stop()
        self.mic = VirtualMicStream(
            name_hint=self.config.resolved_mic_name_hint,
            gain_db=self.config.mic_gain_db,
        )
        self._start_mic()

    # -- 配置 ------------------------------------------------------------------

    def save(self) -> None:
        self.executor._submit_text = self.config.submit_enabled
        self.executor._press_enter = self.config.press_enter_after_paste
        save_config(self.config)
