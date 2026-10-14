"""BLE 传输层（bleak）。

扫描/连接小米遥控器，驱动 VoiceSession；断线按 VoiceReconnectPolicy 重连。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice

from .atvv import CONTROL_UUID, RX_UUID, SERVICE_UUID, TX_UUID
from .connection_policy import (
    BLEDeviceCandidate,
    BLEDeviceSelectionPolicy,
    VoiceConnectionMode,
    VoiceReconnectPolicy,
    VoiceReconnectRetry,
)
from .session import AudioCompleted, FrameDropped, SendCommand, StateChanged, VoiceSession

log = logging.getLogger("miao.ble")

SCAN_TIMEOUT = 8.0
NEGOTIATION_TIMEOUT = 1.5
TICK_INTERVAL = 0.1


async def _request_gatt_access(address: str, attempts: int = 3) -> None:
    """已配对为 HID 的设备，Windows 会拒绝第三方访问其 GATT 服务。

    先用原生 WinRT 打开 ATVV 服务并 request_access_async()，授权在进程内生效，
    之后 bleak 枚举服务时就不会被 ACCESS_DENIED 挡住（否则 ATVV 服务会被静默跳过）。
    遥控器刚唤醒时服务表可能还没就绪，故重试几次。
    """
    import sys

    if sys.platform != "win32":
        return
    try:
        from winrt.windows.devices.bluetooth import (
            BluetoothAddressType,
            BluetoothLEDevice,
        )
    except ImportError:  # 非 Windows 依赖缺失时静默跳过
        return

    for attempt in range(1, attempts + 1):
        try:
            device = (
                await BluetoothLEDevice.from_bluetooth_address_with_bluetooth_address_type_async(
                    int(address.replace(":", ""), 16), BluetoothAddressType.PUBLIC
                )
            )
            if device is None:
                log.debug("GATT 预授权：设备对象为空（第 %d 次）", attempt)
                continue
            try:
                result = await device.get_gatt_services_async()
                if result.status != 0:  # GattCommunicationStatus.SUCCESS
                    log.debug("GATT 预授权：服务表不可用 status=%s", result.status)
                    continue
                granted = False
                for service in result.services:
                    if str(service.uuid).lower() == SERVICE_UUID.lower():
                        await service.request_access_async()
                        granted = True
                    service.close()
                if granted:
                    return
                log.debug("GATT 预授权：未找到 ATVV 服务（第 %d 次）", attempt)
            finally:
                device.close()
        except Exception as error:  # noqa: BLE001
            log.debug("GATT 访问预授权失败（第 %d 次）: %s", attempt, error)
        if attempt < attempts:
            await asyncio.sleep(0.8)


class BLEVoiceBridge:
    """保持与遥控器的 BLE 连接，把语音会话事件转发给上层回调。"""

    def __init__(
        self,
        selection: BLEDeviceSelectionPolicy,
        reconnect_policy: VoiceReconnectPolicy | None = None,
        on_state: Callable[[StateChanged], None] | None = None,
        on_audio: Callable[[AudioCompleted], None] | None = None,
        frame_sink: Callable[[list[int], int], None] | None = None,
    ) -> None:
        self.selection = selection
        self.reconnect_policy = reconnect_policy or VoiceReconnectPolicy(
            VoiceConnectionMode.ALWAYS_READY
        )
        self.on_state = on_state or (lambda _: None)
        self.on_audio = on_audio or (lambda _: None)
        # 实时音频出口：每解出一帧就回调，用于把遥控器音频送进虚拟麦克风
        self.frame_sink = frame_sink
        self.session = VoiceSession(frame_sink=frame_sink)
        self._client: BleakClient | None = None
        self._stop = asyncio.Event()
        self._tick_task: asyncio.Task | None = None

    async def run(self) -> None:
        """主循环：扫描→连接→会话→（断线）重连，直到 stop()。"""
        while not self._stop.is_set():
            try:
                await self._connect_and_serve()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001
                log.warning("连接中断：%s", error)
            self._emit_session_actions(self.session.on_disconnected())

            decision = self.reconnect_policy.next_decision()
            if isinstance(decision, VoiceReconnectRetry):
                log.info("连接暂不可用；%.0f 秒后自动重试", decision.delay)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=decision.delay)
                except asyncio.TimeoutError:
                    pass
            else:
                log.info("已进入智能休眠，等待手动唤醒")
                await self._stop.wait()
                return

    def stop(self) -> None:
        self._stop.set()

    def set_handsfree(self, enabled: bool) -> bool:
        """Called on the BLE event loop so session transitions remain serialized."""
        actions = self.session.set_handsfree(enabled)
        self._emit_session_actions(actions)
        return self.session.handsfree if enabled else True

    async def wake(self) -> None:
        """智能休眠后手动唤醒。"""
        self.reconnect_policy.reset()
        self._stop.set()  # 让当前休眠返回；外部重新调用 run()

    # ------------------------------------------------------------------

    async def _connect_and_serve(self) -> None:
        if self.selection.preferred_identifier:
            # 已绑定的遥控器不会对第三方应用广播，直接按地址连接。
            # 注意：同进程内先跑 BleakScanner 再直连会导致 WinRT GATT 会话挂死，故跳过扫描。
            await _request_gatt_access(self.selection.preferred_identifier)
            device = BLEDevice(self.selection.preferred_identifier, "绑定设备", None)
            log.info("按绑定地址直连 %s", device.address)
        else:
            device = await self._scan()
            if device is None:
                return
        log.info("连接 %s（%s）", device.name or "未知设备", device.address)

        self.session = VoiceSession(frame_sink=self.frame_sink)
        async with BleakClient(
            device, timeout=15.0, disconnected_callback=lambda _: log.info("设备已断开")
        ) as client:
            self._client = client
            loop = asyncio.get_running_loop()

            def on_notify(char: BleakGATTCharacteristic, data: bytearray) -> None:
                if char.uuid.lower() == RX_UUID.lower():
                    actions = self.session.handle_audio(bytes(data))
                else:
                    actions = self.session.handle_control(bytes(data))
                loop.call_soon(self._emit_session_actions, actions)

            try:
                await client.start_notify(RX_UUID, on_notify)
                await client.start_notify(CONTROL_UUID, on_notify)
            except Exception as error:  # noqa: BLE001
                if "Authentication" not in str(error) and "Not permitted" not in str(error):
                    raise
                log.info("音频特征要求加密连接，发起配对…")
                await client.pair()
                await client.start_notify(RX_UUID, on_notify)
                await client.start_notify(CONTROL_UUID, on_notify)

            self._emit_session_actions(self.session.on_connected())

            # 协商看门狗 + tick 循环
            negotiation_deadline = loop.time() + NEGOTIATION_TIMEOUT
            while client.is_connected and not self._stop.is_set():
                await asyncio.sleep(TICK_INTERVAL)
                actions = list(self.session.tick())
                if self.session.state.name == "NEGOTIATING" and loop.time() >= negotiation_deadline:
                    actions.extend(self.session.negotiation_timed_out())
                    negotiation_deadline = loop.time() + NEGOTIATION_TIMEOUT
                    if self.session.state.name == "DISCONNECTED":
                        self._emit_session_actions(actions)
                        return  # 触发外层重连
                self._emit_session_actions(actions)

    async def _scan(self) -> BLEDevice | None:
        log.info("扫描遥控器…")
        found: dict[str, BLEDevice] = {}
        adv_atvv: set[str] = set()

        def on_detect(device: BLEDevice, advertising_data) -> None:
            found[device.address] = device
            uuids = [u.lower() for u in (advertising_data.service_uuids or [])]
            if SERVICE_UUID.lower() in uuids:
                adv_atvv.add(device.address)

        scanner = BleakScanner(detection_callback=on_detect)
        await scanner.start()
        try:
            await asyncio.sleep(SCAN_TIMEOUT)
        finally:
            await scanner.stop()

        candidates = [
            BLEDeviceCandidate(
                identifier=address,
                name=device.name,
                rssi=-60,  # 新 bleak 的 advertisement_data.rssi；扫描回调里可取，这里用中性值
                advertises_atvv=address in adv_atvv,
            )
            for address, device in found.items()
        ]
        best = self.selection.best_candidate(candidates)
        return found.get(best.identifier) if best else None

    def _emit_session_actions(self, actions) -> None:
        for action in actions:
            if isinstance(action, SendCommand):
                asyncio.create_task(self._write(action.data))
            elif isinstance(action, StateChanged):
                self.on_state(action)
            elif isinstance(action, AudioCompleted):
                self.on_audio(action)
            elif isinstance(action, FrameDropped):
                log.warning("丢帧：期望 %d，收到 %d", action.expected, action.received)

    async def _write(self, data: bytes) -> None:
        if self._client is not None and self._client.is_connected:
            try:
                await self._client.write_gatt_char(TX_UUID, data, response=True)
            except Exception as error:  # noqa: BLE001
                log.warning("TX 写入失败：%s", error)


async def scan_devices(timeout: float = SCAN_TIMEOUT) -> list[tuple[str, str | None, bool]]:
    """列出附近 BLE 设备（地址、名称、是否广播 ATVV service）。"""
    results = await BleakScanner.discover(timeout=timeout, return_adv=True)
    rows = []
    for address, (device, adv) in sorted(results.items()):
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        rows.append((address, device.name, SERVICE_UUID.lower() in uuids))
    return rows
