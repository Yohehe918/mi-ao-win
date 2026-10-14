"""ATVV 语音会话（纯逻辑，无 BLE 依赖，可单测）。

把 BLEVoiceBridge.swift 中"字节进 / 字节出"的协议驱动逻辑收敛到本模块，
bleak 传输层（ble_bridge.py）只负责收发字节和定时 tick。

结束信令三路兜底（与原项目一致）：
- AUDIO_STOP(0x00)：松手 → reason="remote-release"
- 第二次 START_SEARCH(0x08)：短按结束 → reason="second-press"
- 本地静音超时：RMS 低于阈值持续 silence_timeout 秒 → reason="silence"
"""

from __future__ import annotations

import enum
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .atvv import (
    ATVVCodec,
    ATVVProtocol,
    ATVVVersion,
    AudioStart,
    AudioStop,
    AudioSync,
    CapabilitiesEvent,
    MicOpenError,
    StartSearch,
)
from .audio import root_mean_square
from .connection_policy import CapabilityNegotiationDecision, CapabilityNegotiationPolicy

log = logging.getLogger("miao.session")

DEFAULT_SILENCE_TIMEOUT = 1.5  # 秒
DEFAULT_SILENCE_THRESHOLD = 35.0  # RMS
KEEP_ALIVE_INTERVAL = 10.0  # 秒，仅录音期间


class SessionState(enum.Enum):
    DISCONNECTED = "disconnected"
    NEGOTIATING = "negotiating"
    READY = "ready"
    OPENING = "opening"
    STREAMING = "streaming"


@dataclass(frozen=True)
class SendCommand:
    data: bytes


@dataclass(frozen=True)
class StateChanged:
    state: SessionState
    detail: str = ""


@dataclass(frozen=True)
class AudioCompleted:
    samples: list[int]
    sample_rate: int
    reason: str


@dataclass(frozen=True)
class FrameDropped:
    expected: int
    received: int


SessionAction = SendCommand | StateChanged | AudioCompleted | FrameDropped


class VoiceSession:
    """驱动一次 BLE 连接的 ATVV 语音会话。"""

    def __init__(
        self,
        negotiation_policy: CapabilityNegotiationPolicy | None = None,
        silence_timeout: float = DEFAULT_SILENCE_TIMEOUT,
        silence_threshold: float = DEFAULT_SILENCE_THRESHOLD,
        now: Callable[[], float] = time.monotonic,
        frame_sink: Callable[[list[int], int], None] | None = None,
    ) -> None:
        self.protocol = ATVVProtocol()
        self.state = SessionState.DISCONNECTED
        self.negotiation_policy = negotiation_policy or CapabilityNegotiationPolicy()
        self.silence_timeout = silence_timeout
        self.silence_threshold = silence_threshold
        # 实时音频出口：每解出一帧就回调（用于把音频送进虚拟麦克风）
        self.frame_sink = frame_sink
        self._now = now
        self._negotiation_requests = 0
        self._stream_id = 0
        self._samples: list[int] = []
        self._sample_rate = 16_000
        self._last_speech_at = 0.0
        self._last_keep_alive_at = 0.0
        self._last_sequence: int | None = None
        self._recording = False
        self.handsfree = False
        self._opening_at = 0.0
        self._stream_started_at = 0.0
        self._received_audio = False
        self._physical_hold = False

    def set_handsfree(self, enabled: bool) -> list[SessionAction]:
        """Request mic audio without a physical voice-key hold."""
        if enabled:
            if self.state is not SessionState.READY:
                return []
            self.handsfree = True
            self._opening_at = self._now()
            self.state = SessionState.OPENING
            return [StateChanged(SessionState.OPENING, "正在启动免按麦克风"),
                    SendCommand(self.protocol.mic_open_command())]
        if not self.handsfree:
            return []
        self.handsfree = False
        return self._finalize("handsfree-stop")

    # -- 连接生命周期 ------------------------------------------------------

    def on_connected(self) -> list[SessionAction]:
        """GATT 就绪（RX/Control 已订阅、TX 可写）后调用，开始能力协商。"""
        self.state = SessionState.NEGOTIATING
        self._negotiation_requests = 1
        return [
            StateChanged(SessionState.NEGOTIATING),
            SendCommand(self.protocol.get_capabilities_command),
        ]

    def negotiation_timed_out(self) -> tuple[SessionAction, ...]:
        """1.5 秒未收到 capabilities 时由传输层调用。

        返回 (动作,) —— retry 时重发 GET_CAPS；reconnect 时返回 StateChanged(DISCONNECTED)
        让传输层断开重连。
        """
        decision = self.negotiation_policy.decision_after(self._negotiation_requests)
        if decision is CapabilityNegotiationDecision.RETRY:
            self._negotiation_requests += 1
            return (SendCommand(self.protocol.get_capabilities_command),)
        self.state = SessionState.DISCONNECTED
        return (
            StateChanged(
                SessionState.DISCONNECTED,
                detail=f"ATVV 能力协商超时（已尝试 {self._negotiation_requests} 次）",
            ),
        )

    # -- 数据入口 ----------------------------------------------------------

    def handle_control(self, data: bytes) -> list[SessionAction]:
        event = self.protocol.parse_control(data)
        actions: list[SessionAction] = []

        if isinstance(event, CapabilitiesEvent):
            self.protocol.accept_capabilities(event.capabilities)
            self.state = SessionState.READY
            version = "1.0" if event.capabilities.version is ATVVVersion.V10 else "0.4"
            actions.append(
                StateChanged(SessionState.READY, detail=f"ATVV {version} 协商完成")
            )
        elif isinstance(event, StartSearch):
            if self._recording:
                actions.extend(self._finalize("second-press"))
            elif self.state is SessionState.READY:
                self.state = SessionState.OPENING
                actions.append(StateChanged(SessionState.OPENING))
                actions.append(SendCommand(self.protocol.mic_open_command()))
        elif isinstance(event, AudioStart):
            self._stream_id = event.stream_id
            self._physical_hold = event.reason == 0x03
            self.protocol.begin_audio_stream(event.codec)
            self._begin_streaming(event.codec, actions)
        elif isinstance(event, AudioSync):
            self.protocol.apply_audio_sync(event)
            self._last_sequence = None
        elif isinstance(event, AudioStop):
            if self._recording:
                # The remote has already closed its stream. A redundant close
                # can invalidate audio notifications on Xiaomi firmware.
                actions.extend(self._finalize("remote-release", close_remote=False))
        elif isinstance(event, MicOpenError):
            self.handsfree = False
            self.state = SessionState.READY
            actions.append(StateChanged(SessionState.READY, detail=f"MIC_OPEN 被拒（{event.code:#06x}）"))
        return actions

    def handle_audio(self, data: bytes) -> list[SessionAction]:
        if not self._recording:
            return []
        decoded = self.protocol.decode_audio(data)
        if decoded is None:
            return []
        sequence, samples = decoded
        if samples:
            self._received_audio = True
        actions: list[SessionAction] = []
        if self._last_sequence is not None:
            expected = (self._last_sequence + 1) & 0xFFFF
            if sequence != expected:
                actions.append(FrameDropped(expected=expected, received=sequence))
        self._last_sequence = sequence
        if not self.handsfree:
            self._samples.extend(samples)
        if self.frame_sink is not None and samples:
            try:
                self.frame_sink(samples, self._sample_rate)
            except Exception as error:  # noqa: BLE001 - 送流出错不能影响会话
                log.debug("音频帧送出失败（忽略）：%s", error)
        if root_mean_square(samples) >= self.silence_threshold:
            self._last_speech_at = self._now()
        return actions

    def tick(self) -> list[SessionAction]:
        """传输层每 100ms 调用一次：静音超时收口 + 录音期 keep-alive。"""
        if self.handsfree and self.state is SessionState.OPENING and self._now() - self._opening_at >= 5.0:
            return self._finalize("handsfree-open-timeout")
        if not self._recording:
            return []
        now = self._now()
        # Some RC003 firmware acknowledges MIC_OPEN with AUDIO_START but never
        # transmits audio. Do not keep a false recording session alive forever.
        if self.handsfree and not self._received_audio and now - self._stream_started_at >= 2.0:
            return self._finalize("handsfree-no-audio")
        if not self.handsfree and not self._physical_hold and self.silence_timeout > 0 and now - self._last_speech_at >= self.silence_timeout:
            return self._finalize("silence")
        if (
            self.protocol.capabilities is not None
            and self.protocol.capabilities.version is ATVVVersion.V10
            and now - self._last_keep_alive_at >= KEEP_ALIVE_INTERVAL
        ):
            self._last_keep_alive_at = now
            return [SendCommand(self.protocol.keep_alive_command(self._stream_id))]
        return []

    def on_disconnected(self) -> list[SessionAction]:
        actions: list[SessionAction] = []
        if self._recording:
            actions.extend(self._finalize("device-disconnected"))
        self.state = SessionState.DISCONNECTED
        self.handsfree = False
        actions.append(StateChanged(SessionState.DISCONNECTED))
        return actions

    # -- 内部 ---------------------------------------------------------------

    def _begin_streaming(self, codec: ATVVCodec, actions: list[SessionAction]) -> None:
        self._recording = True
        self._samples = []
        self._sample_rate = codec.sample_rate
        self._last_sequence = None
        now = self._now()
        self._stream_started_at = now
        self._received_audio = False
        self._last_speech_at = now
        self._last_keep_alive_at = now
        self.state = SessionState.STREAMING
        actions.append(StateChanged(SessionState.STREAMING, detail=f"AUDIO_START ADPCM {codec.sample_rate // 1000} kHz"))

    def _finalize(self, reason: str, *, close_remote: bool = True) -> list[SessionAction]:
        self._recording = False
        self.handsfree = False
        samples, self._samples = self._samples, []
        actions: list[SessionAction] = []
        if close_remote and self.protocol.capabilities is not None:
            try:
                actions.append(SendCommand(self.protocol.mic_close_command(self._stream_id)))
            except Exception:
                pass
        self.state = SessionState.READY
        actions.append(StateChanged(SessionState.READY, detail=f"AUDIO_STOP reason={reason}"))
        if samples:
            actions.append(
                AudioCompleted(samples=samples, sample_rate=self._sample_rate, reason=reason)
            )
        return actions
