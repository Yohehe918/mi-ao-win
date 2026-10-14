"""音频工具：RMS、线性重采样。

（本地 whisper 转写已移除；这里只保留虚拟麦克风通路需要的通用工具。）
"""

from __future__ import annotations

import math

_INT16_MIN = -(2**15)
_INT16_MAX = 2**15 - 1


def _round_half_away(value: float) -> int:
    """与 Swift 的 Double.rounded() 一致：半数向远离零方向取整。"""
    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)


def _clamp_int16(value: int) -> int:
    return max(_INT16_MIN, min(_INT16_MAX, value))


def root_mean_square(samples: list[int]) -> float:
    if not samples:
        return 0.0
    power = sum(float(s) * float(s) for s in samples)
    return math.sqrt(power / len(samples))


def apply_gain(samples: list[int], gain_db: float) -> list[int]:
    if not gain_db or not samples:
        return list(samples)
    gain = 10.0 ** (gain_db / 20.0)
    return [_clamp_int16(_round_half_away(s * gain)) for s in samples]


def linear_resample(samples: list[int], source_rate: int, destination_rate: int) -> list[int]:
    if source_rate <= 0 or destination_rate <= 0 or len(samples) <= 1:
        return list(samples)
    output_count = _round_half_away(len(samples) * destination_rate / source_rate)
    ratio = source_rate / destination_rate
    output: list[int] = []
    for i in range(output_count):
        position = i * ratio
        lower = min(len(samples) - 1, int(position))
        upper = min(len(samples) - 1, lower + 1)
        fraction = position - lower
        value = samples[lower] * (1 - fraction) + samples[upper] * fraction
        output.append(_clamp_int16(_round_half_away(value)))
    return output
