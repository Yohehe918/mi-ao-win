"""端到端验证：遥控器麦克风 → 虚拟声卡。

同时做两件事：
1. 启动 MiAoApp（BLE 连接 + 按键钩子 + 虚拟麦克风转发）；
2. 从 CABLE Output（MME 端点）录音，测量收到的电平。

运行后请按住遥控器语音键说话，脚本会实时打印收到的电平。
用法：py -X utf8 scripts/e2e_mic_test.py [秒数]
"""

from __future__ import annotations

import array
import sys
import time

import sounddevice as sd

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from miao.app import MiAoApp  # noqa: E402

RECORD_SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else 45


def find_cable_output_mme():
    """MME 的 CABLE Output（实测 MME 端点读取最可靠）。"""
    hostapis = sd.query_hostapis()
    for i, d in enumerate(sd.query_devices()):
        if "CABLE Output" in d["name"] and d["max_input_channels"] > 0:
            if "MME" in hostapis[d["hostapi"]]["name"]:
                return i
    return None


def main() -> None:
    dev = find_cable_output_mme()
    if dev is None:
        print("找不到 CABLE Output 录制端点，先确认 VB-CABLE 已安装")
        return
    print("录音端点: #%d %s" % (dev, sd.query_devices(dev)["name"]))

    app = MiAoApp()
    app.status.listeners.append(lambda s: print(f"  [状态] {s.state} {s.detail}", flush=True))
    app.start()
    print("应用已启动（虚拟麦克风设备 #%s）" % app.mic.device_index, flush=True)
    print(f"请按住遥控器语音键说话，持续 {RECORD_SECONDS} 秒…", flush=True)

    rec = sd.RawInputStream(samplerate=44100, channels=2, dtype="int16", device=dev)
    rec.start()
    peak = 0.0
    loud_frames = 0
    deadline = time.monotonic() + RECORD_SECONDS
    try:
        while time.monotonic() < deadline:
            data, _ = rec.read(4410)  # 100ms
            pcm = array.array("h")
            pcm.frombytes(bytes(data))
            rms = (sum(float(s) * s for s in pcm) / max(1, len(pcm))) ** 0.5
            peak = max(peak, rms)
            if rms > 300:
                loud_frames += 1
                print(f"  ♪ 收到音频 RMS={rms:.0f}（麦克风电平 {app.mic.level:.0f}）", flush=True)
    finally:
        rec.stop()
        rec.close()
        app.stop()

    print("\n=== 结果 ===", flush=True)
    print(f"虚拟声卡写入样本数: {app.mic._samples_written}")
    print(f"队列丢弃块数: {app.mic.dropped_chunks}")
    print(f"最后一帧电平: {app.mic.level:.0f}")
    print(f"流错误: {app.mic.last_error}")
    print(f"CABLE Output 收到音频的帧数: {loud_frames}，峰值 RMS={peak:.0f}")
    print("结论:", "✅ 遥控器音频已经流进虚拟声卡" if loud_frames > 3 else "❌ 没收到遥控器音频")


if __name__ == "__main__":
    main()
