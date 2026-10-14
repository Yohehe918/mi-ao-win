"""真机探查 v4：配对模式下完成 ATVV 握手，按语音键收音并保存 WAV。

前置：Windows 设置里删除遥控器绑定，然后长按 菜单+HOME 进入配对模式。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bleak import BleakClient, BleakScanner

from miao.atvv import CONTROL_UUID, RX_UUID, TX_UUID
from miao.audio import prepare_for_whisper, write_wav
from miao.session import AudioCompleted, SendCommand, StateChanged, VoiceSession

TARGET_SVC = "ab5e0001-5a21-4f05-bc7d-af01f617b664"
OUT_WAV = Path(__file__).resolve().parent / "probe_voice.wav"

found = {}


def on_detect(dev, adv):
    name = dev.name or ""
    if "XL" in name or "遥控" in name or "xiaomi" in name.lower() or "MI RC" in name:
        found.setdefault(dev.address, dev)
        print(f"[候选] {dev.address} {name} rssi={adv.rssi}", flush=True)


async def main():
    scanner = BleakScanner(detection_callback=on_detect)
    await scanner.start()
    print("扫描中（3 分钟），请长按 菜单+HOME …", flush=True)
    dev = None
    for _ in range(360):
        await asyncio.sleep(0.5)
        if found:
            dev = next(iter(found.values()))
            break
    await scanner.stop()
    if dev is None:
        print("未发现遥控器广播")
        return

    session = VoiceSession()
    client_holder = {}

    def emit(actions):
        for a in actions:
            if isinstance(a, SendCommand):
                print(f"[TX] {a.data.hex()}", flush=True)
                asyncio.get_running_loop().create_task(write(a.data))
            elif isinstance(a, StateChanged):
                print(f"[状态] {a.state} {a.detail}", flush=True)
            elif isinstance(a, AudioCompleted):
                print(f"[音频完成] {len(a.samples)} 采样 @ {a.sample_rate}Hz reason={a.reason}", flush=True)
                prepared = prepare_for_whisper(a.samples, a.sample_rate, 20.0)
                write_wav(prepared, 16000, OUT_WAV)
                print(f"[WAV 已保存] {OUT_WAV}", flush=True)

    async def write(data):
        c = client_holder.get("c")
        if c and c.is_connected:
            try:
                await c.write_gatt_char(TX_UUID, data, response=True)
            except Exception as e:
                print("[TX 失败]", e, flush=True)

    def on_notify(char, data: bytearray):
        uuid = char.uuid.lower()
        if uuid == RX_UUID.lower():
            actions = session.handle_audio(bytes(data))
        else:
            print(f"[CTRL] {data.hex()}", flush=True)
            actions = session.handle_control(bytes(data))
        emit(actions)

    print(f"连接 {dev.address} {dev.name} …", flush=True)
    async with BleakClient(dev, timeout=20) as client:
        client_holder["c"] = client
        print("已连接，先配对加密…", flush=True)
        try:
            await client.pair()
            print("配对成功", flush=True)
        except Exception as e:
            print("配对异常:", type(e).__name__, e, flush=True)
        print("订阅并发起 ATVV 握手…", flush=True)
        await client.start_notify(RX_UUID, on_notify)
        await client.start_notify(CONTROL_UUID, on_notify)
        emit(session.on_connected())  # 发送 GET_CAPS
        print("握手完成等待中。请按住语音键说话！（监听 150 秒）", flush=True)
        for i in range(150):
            await asyncio.sleep(1)
            if not client.is_connected:
                print(f"第 {i+1} 秒掉线", flush=True)
                return
            emit(session.tick())


asyncio.run(main())
