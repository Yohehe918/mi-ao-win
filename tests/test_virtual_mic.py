"""虚拟麦克风转换逻辑测试（不打开真实音频设备）。"""

import array
import math
import threading

from miao.virtual_mic import OUTPUT_CHANNELS, VirtualMicStream


def _to_pcm(raw: bytes) -> list[int]:
    pcm = array.array("h")
    pcm.frombytes(raw)
    return list(pcm)


def test_resample_and_stereo_interleave():
    mic = VirtualMicStream(sample_rate=48000)
    raw = mic._to_output_bytes([100, -100, 300, 0], 16000)
    pcm = _to_pcm(raw)
    # 16 kHz 4 个样本 → 48 kHz 12 个样本 → 立体声 24 个短整数
    assert len(pcm) == 12 * OUTPUT_CHANNELS
    assert pcm[0] == 100 and pcm[1] == 100  # 左右声道相同
    # 线性插值：100 与 -100 之间取 1/3 处 → 33（左右声道一致）
    assert pcm[2] == 33 and pcm[3] == 33


def test_no_resample_when_rate_matches():
    mic = VirtualMicStream(sample_rate=48000)
    raw = mic._to_output_bytes([1, 2, 3], 48000)
    assert len(_to_pcm(raw)) == 3 * OUTPUT_CHANNELS


def test_gain_is_applied_and_clamped():
    mic = VirtualMicStream(sample_rate=48000, gain_db=20.0)  # ×10
    raw = mic._to_output_bytes([100, 20000], 48000)
    pcm = _to_pcm(raw)
    assert pcm[0] == 1000
    assert pcm[2] == 32767  # 20000×10 超界被裁剪


def test_write_pcm_ignores_empty_and_records_stats():
    mic = VirtualMicStream(sample_rate=48000)
    mic.write_pcm([], 16000)
    assert mic._queue.qsize() == 0
    mic.write_pcm([10, -10], 16000)
    assert mic._queue.qsize() == 1
    assert mic.level > 0


def test_level_is_rms_of_last_block():
    mic = VirtualMicStream(sample_rate=48000)
    mic.write_pcm([1000, -1000, 1000, -1000], 16000)
    assert math.isclose(mic.level, 1000.0, rel_tol=1e-6)


def test_queue_drops_oldest_when_full():
    mic = VirtualMicStream(sample_rate=48000)
    for _ in range(mic._queue.maxsize + 5):
        mic.write_pcm([100] * 10, 16000)
    assert mic._queue.qsize() <= mic._queue.maxsize
    assert mic.dropped_chunks == 5


def test_drain_waits_for_queued_audio_before_acknowledging():
    written = []
    class Stream:
        latency = 0
        def write(self, block): written.append(block)
    mic = VirtualMicStream()
    mic._stream = Stream()
    mic.write_pcm([123] * 10, 48000)
    expected = mic._queue.queue[0]
    mic._thread = threading.Thread(target=mic._writer_loop)
    mic._thread.start()
    try:
        assert mic.drain(.5)
        assert expected in written
    finally:
        mic._stop.set()
        mic._thread.join(1)
