"""IMA/DVI ADPCM 解码器。

移植自 mi-ao 的 ADPCMDecoder.swift（52 行纯位运算）。
遥控器语音帧的 nibble 顺序为高位在前。
"""

from __future__ import annotations

from collections.abc import Iterable

_INT16_MIN = -(2**15)
_INT16_MAX = 2**15 - 1

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


def _clamp_int16(value: int) -> int:
    return max(_INT16_MIN, min(_INT16_MAX, value))


class ADPCMDecoder:
    """逐字节解码 IMA/DVI ADPCM 为 16 位 PCM 样本。"""

    def __init__(self) -> None:
        self.predictor = 0
        self.step_index = 0

    def reset(self, predictor: int, step_index: int) -> None:
        self.predictor = _clamp_int16(predictor)
        self.step_index = min(88, step_index)

    def decode(self, data: Iterable[int]) -> list[int]:
        output: list[int] = []
        for byte in data:
            output.append(self._decode_nibble((byte >> 4) & 0x0F))
            output.append(self._decode_nibble(byte & 0x0F))
        return output

    def _decode_nibble(self, nibble: int) -> int:
        step = _STEP_TABLE[self.step_index]
        difference = step >> 3
        if nibble & 4:
            difference += step
        if nibble & 2:
            difference += step >> 1
        if nibble & 1:
            difference += step >> 2

        self.predictor = _clamp_int16(
            self.predictor - difference if nibble & 8 else self.predictor + difference
        )
        self.step_index = max(0, min(88, self.step_index + _INDEX_TABLE[nibble & 7]))
        return self.predictor
