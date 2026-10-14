"""Google ATVV（ATV Voice over BLE）协议。

移植自 mi-ao 的 ATVVProtocol.swift。
小米蓝牙遥控器 2 Pro（固件 2671）实测：ATVV v1.0 + ADPCM 16 kHz + 120 字节音频帧。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from . import ProtocolError
from .adpcm import ADPCMDecoder

SERVICE_UUID = "AB5E0001-5A21-4F05-BC7D-AF01F617B664"
TX_UUID = "AB5E0002-5A21-4F05-BC7D-AF01F617B664"  # 主机写入（发命令）
RX_UUID = "AB5E0003-5A21-4F05-BC7D-AF01F617B664"  # 音频 notify
CONTROL_UUID = "AB5E0004-5A21-4F05-BC7D-AF01F617B664"  # 控制事件 notify

GET_CAPABILITIES_COMMAND = bytes([0x0A, 0x01, 0x00, 0x00, 0x03, 0x03])


class ATVVVersion(enum.Enum):
    V04 = "0.4"
    V10 = "1.0"


class ATVVCodec(enum.IntEnum):
    ADPCM_8K = 0x01
    ADPCM_16K = 0x02

    @property
    def sample_rate(self) -> int:
        return 8_000 if self is ATVVCodec.ADPCM_8K else 16_000


@dataclass(frozen=True)
class ATVVCapabilities:
    version: ATVVVersion
    codecs: int
    interaction_model: int
    frame_size: int

    @property
    def selected_codec(self) -> ATVVCodec | None:
        if self.codecs & ATVVCodec.ADPCM_16K:
            return ATVVCodec.ADPCM_16K
        if self.codecs & ATVVCodec.ADPCM_8K:
            return ATVVCodec.ADPCM_8K
        return None


# 控制事件
@dataclass(frozen=True)
class AudioStop:
    reason: int


@dataclass(frozen=True)
class AudioStart:
    reason: int
    codec: ATVVCodec
    stream_id: int


@dataclass(frozen=True)
class StartSearch:
    pass


@dataclass(frozen=True)
class AudioSync:
    codec: ATVVCodec
    sequence: int
    predictor: int
    step_index: int


@dataclass(frozen=True)
class CapabilitiesEvent:
    capabilities: ATVVCapabilities


@dataclass(frozen=True)
class MicOpenError:
    code: int


@dataclass(frozen=True)
class UnknownEvent:
    data: bytes


ControlEvent = (
    AudioStop | AudioStart | StartSearch | AudioSync | CapabilitiesEvent | MicOpenError | UnknownEvent
)


def _int16(bits: int) -> int:
    return bits - 0x10000 if bits & 0x8000 else bits


class ATVVProtocol:
    """ATVV 会话状态与帧编解码（不含 BLE 传输层）。"""

    def __init__(self) -> None:
        self.capabilities: ATVVCapabilities | None = None
        self.codec: ATVVCodec | None = None
        self._decoder = ADPCMDecoder()
        self._v10_sequence = 0

    @property
    def get_capabilities_command(self) -> bytes:
        return GET_CAPABILITIES_COMMAND

    def accept_capabilities(self, capabilities: ATVVCapabilities) -> None:
        selected = capabilities.selected_codec
        if selected is None:
            raise ProtocolError("遥控器不支持 ADPCM 8/16 kHz")
        self.capabilities = capabilities
        self.codec = selected
        self._decoder.reset(0, 0)
        self._v10_sequence = 0

    def mic_open_command(self) -> bytes:
        if self.capabilities is None or self.codec is None:
            raise ProtocolError("尚未完成 ATVV capabilities 协商")
        if self.capabilities.version is ATVVVersion.V04:
            return bytes([0x0C, 0x00, int(self.codec)])
        return bytes([0x0C, 0x00])  # playback/realtime mode

    def begin_audio_stream(self, codec: ATVVCodec) -> None:
        """Each AUDIO_START begins a new encoder state; AUDIO_SYNC may refine it."""
        self.codec = codec
        self._decoder.reset(0, 0)
        self._v10_sequence = 0

    def mic_close_command(self, stream_id: int) -> bytes:
        if self.capabilities is None:
            raise ProtocolError("尚未完成 ATVV capabilities 协商")
        if self.capabilities.version is ATVVVersion.V04:
            return bytes([0x0D])
        return bytes([0x0D, stream_id & 0xFF])

    def keep_alive_command(self, stream_id: int) -> bytes:
        if self.capabilities is None:
            raise ProtocolError("尚未完成 ATVV capabilities 协商")
        if self.capabilities.version is ATVVVersion.V04:
            return self.mic_open_command()
        return bytes([0x0E, stream_id & 0xFF])

    def parse_control(self, data: bytes) -> ControlEvent:
        if not data:
            return UnknownEvent(data)
        opcode = data[0]
        if opcode == 0x00:
            return AudioStop(reason=data[1] if len(data) > 1 else 0)
        if opcode == 0x04:
            if (
                self.capabilities is not None
                and self.capabilities.version is ATVVVersion.V10
                and len(data) >= 4
            ):
                try:
                    codec = ATVVCodec(data[2])
                except ValueError:
                    codec = None  # type: ignore[assignment]
                if codec is not None:
                    return AudioStart(reason=data[1], codec=codec, stream_id=data[3])
            return AudioStart(reason=0, codec=self.codec or ATVVCodec.ADPCM_8K, stream_id=0)
        if opcode == 0x08:
            return StartSearch()
        if opcode == 0x0A:
            if (
                self.capabilities is not None
                and self.capabilities.version is ATVVVersion.V10
                and len(data) >= 7
            ):
                try:
                    codec = ATVVCodec(data[1])
                except ValueError:
                    return UnknownEvent(data)
                sequence = data[2] << 8 | data[3]
                predictor = _int16(data[4] << 8 | data[5])
                return AudioSync(codec=codec, sequence=sequence, predictor=predictor, step_index=data[6])
            return UnknownEvent(data)
        if opcode == 0x0B:
            capabilities = self.parse_capabilities(data)
            if capabilities is None:
                return UnknownEvent(data)
            return CapabilitiesEvent(capabilities)
        if opcode == 0x0C:
            if len(data) < 3:
                return MicOpenError(0xFFFF)
            return MicOpenError(data[1] << 8 | data[2])
        return UnknownEvent(data)

    @staticmethod
    def parse_capabilities(data: bytes) -> ATVVCapabilities | None:
        if len(data) < 3 or data[0] != 0x0B:
            return None
        version = data[1] << 8 | data[2]
        if version == 0x0004:
            if len(data) < 9:
                return None
            return ATVVCapabilities(
                version=ATVVVersion.V04,
                codecs=data[4],
                interaction_model=0,
                frame_size=data[5] << 8 | data[6],
            )
        if version == 0x0100:
            if len(data) < 7:
                return None
            standard_codecs = data[3]
            standard_interaction = data[4]
            # 小米固件 2671 偶尔把 codec / interaction 两个字节对调；
            # 仅当标准 codec 字节完全无有效位时才接受对调布局。
            has_known_standard = bool(standard_codecs & 0x03)
            has_known_swapped = bool(standard_interaction & 0x03)
            swapped = not has_known_standard and has_known_swapped
            return ATVVCapabilities(
                version=ATVVVersion.V10,
                codecs=standard_interaction if swapped else standard_codecs,
                interaction_model=standard_codecs if swapped else standard_interaction,
                frame_size=data[5] << 8 | data[6],
            )
        return None

    def apply_audio_sync(self, sync: AudioSync) -> None:
        self.codec = sync.codec
        self._v10_sequence = sync.sequence
        self._decoder.reset(sync.predictor, sync.step_index)

    def decode_audio(self, data: bytes) -> tuple[int, list[int]] | None:
        """解码一帧音频，返回 (sequence, samples)；帧无效时返回 None。"""
        if self.capabilities is None or self.codec is None:
            return None
        if self.capabilities.version is ATVVVersion.V04:
            if len(data) != self.capabilities.frame_size or len(data) < 6:
                return None
            sequence = data[0] << 8 | data[1]
            predictor = _int16(data[3] << 8 | data[4])
            self._decoder.reset(predictor, data[5])
            return sequence, [predictor] + self._decoder.decode(data[6:])
        # v1.0：纯 ADPCM payload，sequence 本地自增，状态靠 AUDIO_SYNC
        if not data:
            return None
        sequence = self._v10_sequence
        self._v10_sequence = (self._v10_sequence + 1) & 0xFFFF
        return sequence, self._decoder.decode(data)
