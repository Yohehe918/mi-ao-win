"""虚拟麦克风：把遥控器音频送进 VB-CABLE，让豆包等应用把它当成麦克风。

VB-CABLE 的模型是「播放端点 CABLE Input → 驱动内部环回 → 录制端点 CABLE Output」。
我们把遥控器解出的 16 kHz 单声道 PCM 写进 CABLE Input，任何把麦克风选成
CABLE Output 的应用（豆包语音输入）就能听到遥控器采集的声音。

实现要点：
- 用 RawOutputStream + 裸 int16 字节，避免依赖 numpy；
- 写流放在独立线程，BLE 回调线程只做入队，绝不阻塞；
- 空闲时持续写静音，保证流一直处于打开状态，按下语音键时零启动延迟。
"""

from __future__ import annotations

import array
import logging
import queue
import threading
import time

from .audio import linear_resample

log = logging.getLogger("miao.mic")

# VB-CABLE 播放端点常见名称片段（不同版本/语言略有差异）
DEVICE_HINTS = ("CABLE Input", "CABLE In", "VB-Audio Virtual Cable", "CABLE 输入")
OUTPUT_RATE = 48_000
OUTPUT_CHANNELS = 2
SILENCE_CHUNK_MS = 100
MAX_QUEUE_CHUNKS = 50  # 约 5 秒，超出丢最旧的，避免内存无限涨


def _import_sounddevice():
    try:
        import sounddevice  # noqa: PLC0415 - 延迟导入，缺依赖时给出可读错误
        return sounddevice
    except Exception as error:  # noqa: BLE001
        raise RuntimeError(
            "未安装 sounddevice，无法把音频送进虚拟声卡。请运行：py -m pip install sounddevice"
        ) from error


def _device_hints(name_hint: str) -> list[str]:
    return [name_hint] if name_hint else list(DEVICE_HINTS)


def cable_input_candidates(name_hint: str = "") -> list[int]:
    """按推荐顺序返回 CABLE Input（播放端点）的设备索引。

    VB-CABLE 通过 WASAPI 写入时必须带 auto_convert（否则数据静默丢失），
    所以 WASAPI 端点排最前；MME 端点作为回退（实测可写入，兼容性最好）。
    """
    sounddevice = _import_sounddevice()
    hints = _device_hints(name_hint)
    hostapis = sounddevice.query_hostapis()
    ranked: list[tuple[int, int]] = []
    for index, device in enumerate(sounddevice.query_devices()):
        if device["max_output_channels"] < 1:
            continue
        if not any(hint.lower() in device["name"].lower() for hint in hints if hint):
            continue
        api = hostapis[device["hostapi"]]["name"] if device["hostapi"] < len(hostapis) else ""
        score = 0
        if "WASAPI" in api:
            score += 4
        elif "MME" in api:
            score += 2
        if device["max_output_channels"] == OUTPUT_CHANNELS:
            score += 1
        ranked.append((-score, index))
    ranked.sort()
    return [index for _, index in ranked]


def find_cable_input(name_hint: str = "") -> int | None:
    """返回首选的 CABLE Input 设备索引，找不到返回 None。"""
    candidates = cable_input_candidates(name_hint)
    return candidates[0] if candidates else None


def is_virtual_cable_installed() -> bool:
    try:
        return find_cable_input() is not None
    except RuntimeError:
        return False


class VirtualMicStream:
    """把 PCM 块写入 CABLE Input 的播放流。线程安全。"""

    def __init__(
        self,
        device: int | None = None,
        name_hint: str = "",
        sample_rate: int = OUTPUT_RATE,
        gain_db: float = 0.0,
    ) -> None:
        self._device = device
        self._name_hint = name_hint
        self._rate = sample_rate
        self._gain = 10.0 ** (gain_db / 20.0) if gain_db else 1.0
        self._queue: queue.Queue[bytes | threading.Event] = queue.Queue(maxsize=MAX_QUEUE_CHUNKS)
        self._stream = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._enqueue_lock = threading.Lock()
        self._samples_written = 0
        self._dropped_chunks = 0
        self._level = 0.0
        self._last_error: str | None = None
        self._silence = b"\x00" * (int(sample_rate * SILENCE_CHUNK_MS / 1000) * OUTPUT_CHANNELS * 2)

    # -- 生命周期 -----------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def level(self) -> float:
        """最近一块音频的 RMS（0~32767），供界面画电平。"""
        return self._level

    @property
    def device_index(self) -> int | None:
        """正在写入的设备索引（未启动时为 None）。"""
        return self._device

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def dropped_chunks(self) -> int:
        return self._dropped_chunks

    def start(self) -> None:
        if self.running:
            return
        sounddevice = _import_sounddevice()
        candidates = (
            [self._device] if self._device is not None else cable_input_candidates(self._name_hint)
        )
        if not candidates:
            raise RuntimeError(
                "没找到 VB-CABLE 的播放端点「CABLE Input」。请先安装 VB-CABLE 虚拟声卡"
                "（https://vb-audio.com/Cable/），装完重启系统再试。"
            )
        errors: list[str] = []
        for device in candidates:
            for rate in (self._rate, int(sounddevice.query_devices(device)["default_samplerate"])):
                for auto_convert in (True, False):
                    try:
                        self._stream = self._open_stream(
                            sounddevice, device, rate, auto_convert
                        )
                        self._stream.start()
                    except Exception as error:  # noqa: BLE001
                        errors.append(f"#{device}@{rate}Hz ac={auto_convert}: {error}")
                        self._stream = None
                        continue
                    self._device = device
                    self._rate = rate
                    self._silence = b"\x00" * (
                        int(rate * SILENCE_CHUNK_MS / 1000) * OUTPUT_CHANNELS * 2
                    )
                    self._stop.clear()
                    self._thread = threading.Thread(
                        target=self._writer_loop, name="miao-mic", daemon=True
                    )
                    self._thread.start()
                    log.info("虚拟麦克风已启动（设备 #%s @ %d Hz）", device, rate)
                    return
        raise RuntimeError("打开 CABLE Input 失败：" + "；".join(errors[-4:]))

    @staticmethod
    def _open_stream(sounddevice, device: int, rate: int, auto_convert: bool):
        """WASAPI 端点必须 auto_convert，否则采样率/声道与混音格式不符时数据会静默丢失。"""
        extra = None
        hostapis = sounddevice.query_hostapis()
        api = hostapis[sounddevice.query_devices(device)["hostapi"]]["name"]
        if "WASAPI" in api:
            extra = sounddevice.WasapiSettings(auto_convert=auto_convert)
        return sounddevice.RawOutputStream(
            samplerate=rate,
            channels=OUTPUT_CHANNELS,
            dtype="int16",
            device=device,
            blocksize=0,  # 交给 PortAudio 决定，延迟更低
            extra_settings=extra,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as error:  # noqa: BLE001
                log.debug("关闭音频流时出错（忽略）：%s", error)
            self._stream = None

    # -- 送数据 -------------------------------------------------------------

    def drain(self, timeout: float = 1.5) -> bool:
        """Wait on the speech worker, never the input/BLE thread, before stop."""
        if not self.running:
            return True
        barrier = threading.Event()
        deadline = time.monotonic() + timeout
        while True:
            with self._enqueue_lock:
                try:
                    self._queue.put_nowait(barrier)
                    break
                except queue.Full:
                    pass
            if time.monotonic() >= deadline or self._stop.wait(.005):
                return False
        return barrier.wait(max(0, deadline - time.monotonic()))

    def write_pcm(self, samples: list[int], sample_rate: int) -> None:
        """BLE 线程调用：把一帧解码后的 PCM 入队（不阻塞、不抛异常）。"""
        if not samples:
            return
        try:
            block = self._to_output_bytes(samples, sample_rate)
        except Exception as error:  # noqa: BLE001
            self._last_error = str(error)
            return
        with self._lock:
            self._level = _rms(samples)
        with self._enqueue_lock:
            try:
                self._queue.put_nowait(block)
            except queue.Full:
                try:
                    dropped = self._queue.get_nowait()
                    if isinstance(dropped, threading.Event):
                        self._queue.put_nowait(dropped)
                    else:
                        self._queue.put_nowait(block)
                except queue.Empty:  # pragma: no cover - writer won the race
                    self._queue.put_nowait(block)
                self._dropped_chunks += 1

    # -- 内部 ---------------------------------------------------------------

    def _to_output_bytes(self, samples: list[int], sample_rate: int) -> bytes:
        pcm = samples
        if sample_rate != self._rate:
            pcm = linear_resample(samples, sample_rate, self._rate)
        if self._gain != 1.0:
            pcm = [_clamp_int16(int(round(s * self._gain))) for s in pcm]
        mono = array.array("h", pcm)
        # 单声道复制为立体声（交错）
        stereo = array.array("h", bytes(len(mono) * 4))
        stereo[0::2] = mono
        stereo[1::2] = mono
        return stereo.tobytes()

    def _writer_loop(self) -> None:
        while not self._stop.is_set():
            try:
                block = self._queue.get(timeout=SILENCE_CHUNK_MS / 1000)
            except queue.Empty:
                block = self._silence
            if isinstance(block, threading.Event):
                # Blocking PortAudio writes can return before device playback.
                # Allow its reported latency plus a short tail guard to finish.
                latency = float(getattr(self._stream, "latency", .05))
                self._stop.wait(min(max(latency, 0), .2) + .05)
                block.set()
                continue
            try:
                if self._stream is not None:
                    self._stream.write(block)
                    self._samples_written += len(block) // 4
            except Exception as error:  # noqa: BLE001
                self._last_error = str(error)
                log.warning("写音频流失败：%s", error)
                time.sleep(0.2)


def _clamp_int16(value: int) -> int:
    return max(-32768, min(32767, value))


def _rms(samples: list[int]) -> float:
    if not samples:
        return 0.0
    total = sum(float(s) * float(s) for s in samples)
    return (total / len(samples)) ** 0.5
