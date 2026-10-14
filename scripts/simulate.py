"""无硬件端到端模拟：正弦波 → IMA ADPCM 编码 → 灌入 VoiceSession → 解码 → WAV。

验证从协议字节流到解码 WAV 的完整链路（whisper 转写除外）。

用法：python scripts/simulate.py [输出.wav]
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from miao.audio import root_mean_square, write_wav
from miao.session import AudioCompleted, VoiceSession

_SAMPLE_RATE = 16_000
_FRAME_BYTES = 120  # 固件 2671 的 v1.0 音频帧

_INDEX_TABLE = (-1, -1, -1, -1, 2, 4, 6, 8)
_STEP_TABLE = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17,
    19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118,
    130, 143, 157, 173, 190, 209, 230, 253, 279, 307,
    337, 371, 408, 449, 494, 544, 598, 658, 724, 796,
    876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066,
    2272, 2494, 2740, 3008, 3307, 3638, 4002, 4402, 4842, 5327,
    5860, 6446, 7091, 7800, 8580, 9438, 10382, 11420, 12562, 13818,
    15200, 16720, 18392, 20231, 22254, 24479, 26927, 29620, 32767,
)


class _Encoder:
    """IMA/DVI ADPCM 编码器（解码器的镜像，仅用于测试注入）。"""

    def __init__(self) -> None:
        self.predictor = 0
        self.step_index = 0

    def encode(self, samples: list[int]) -> bytes:
        out = bytearray()
        nibbles: list[int] = []
        for sample in samples:
            nibbles.append(self._encode_nibble(sample))
        for i in range(0, len(nibbles), 2):
            hi = nibbles[i]
            lo = nibbles[i + 1] if i + 1 < len(nibbles) else 0
            out.append(hi << 4 | lo)  # 高位在前，与解码器一致
        return bytes(out)

    def _encode_nibble(self, sample: int) -> int:
        step = _STEP_TABLE[self.step_index]
        diff = sample - self.predictor
        nibble = 0x8 if diff < 0 else 0
        diff = abs(diff)
        if diff >= step:
            nibble |= 4
            diff -= step
        if diff >= step >> 1:
            nibble |= 2
            diff -= step >> 1
        if diff >= step >> 2:
            nibble |= 1

        delta = step >> 3
        if nibble & 4:
            delta += step
        if nibble & 2:
            delta += step >> 1
        if nibble & 1:
            delta += step >> 2
        self.predictor += -delta if nibble & 8 else delta
        self.predictor = max(-32768, min(32767, self.predictor))
        self.step_index = max(0, min(88, self.step_index + _INDEX_TABLE[nibble & 7]))
        return nibble


def simulate(output: Path) -> None:
    # 1 秒 440Hz 正弦，模拟一段"人声"
    source = [int(6000 * math.sin(2 * math.pi * 440 * i / _SAMPLE_RATE)) for i in range(_SAMPLE_RATE)]
    encoded = _Encoder().encode(source)

    session = VoiceSession(silence_timeout=0)
    session.on_connected()
    session.handle_control(bytes([0x0B, 0x01, 0x00, 0x03, 0x03, 0x00, 0x78]))  # v1.0 caps
    session.handle_control(bytes([0x08]))  # START_SEARCH
    session.handle_control(bytes([0x04, 0x00, 0x02, 0x01]))  # AUDIO_START 16k streamID=1

    completed: list[AudioCompleted] = []
    for offset in range(0, len(encoded), _FRAME_BYTES):
        session.handle_audio(encoded[offset : offset + _FRAME_BYTES])
    for action in session.handle_control(bytes([0x00, 0x00])):  # AUDIO_STOP
        if isinstance(action, AudioCompleted):
            completed.append(action)

    assert completed, "会话没有产出音频"
    event = completed[0]
    write_wav(event.samples, event.sample_rate, output)
    print(f"模拟完成：{output}")
    print(f"  原始 RMS：{root_mean_square(source):.1f}")
    print(f"  解码 RMS：{root_mean_square(event.samples):.1f}（ADPCM 有损，应同量级）")
    print(f"  样本数：{len(event.samples)}，结束原因：{event.reason}")


if __name__ == "__main__":
    simulate(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("simulated.wav"))
