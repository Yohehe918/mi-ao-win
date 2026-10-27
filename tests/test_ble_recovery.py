import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import winrt.windows.devices.bluetooth as bt
from winrt.windows.devices.enumeration import DeviceAccessStatus

from miao import ble_bridge as bridge
from miao.connection_policy import BLEDeviceSelectionPolicy
from miao.session import StateChanged, SessionState


def install_device(monkeypatch, access=DeviceAccessStatus.ALLOWED):
    service = SimpleNamespace(uuid=bridge.SERVICE_UUID, closed=False)
    service.close = lambda: setattr(service, 'closed', True)
    service.request_access_async = AsyncMock(return_value=access)
    device = SimpleNamespace(closed=False)
    device.close = lambda: setattr(device, 'closed', True)
    device.get_gatt_services_for_uuid_with_cache_mode_async = AsyncMock(
        return_value=SimpleNamespace(status=0, services=[service]))
    monkeypatch.setattr(bt, 'BluetoothLEDevice', SimpleNamespace(
        from_bluetooth_address_with_bluetooth_address_type_async=AsyncMock(return_value=device)))
    return device, service


def test_access_is_uncached_and_lease_stays_alive(monkeypatch):
    device, service = install_device(monkeypatch)
    lease = asyncio.run(bridge._request_gatt_access('AA:BB:CC:DD:EE:FF', attempts=1))
    assert lease == (device, [service])
    assert not device.closed and not service.closed
    assert device.get_gatt_services_for_uuid_with_cache_mode_async.call_args.args[1] == bt.BluetoothCacheMode.UNCACHED
    service.close()
    device.close()


def test_access_denied_closes_resources_and_does_not_connect(monkeypatch):
    device, service = install_device(monkeypatch, DeviceAccessStatus.DENIED_BY_SYSTEM)
    with pytest.raises(RuntimeError, match='尚未就绪'):
        asyncio.run(bridge._request_gatt_access('AA:BB:CC:DD:EE:FF', attempts=1))
    assert device.closed and service.closed


def test_failed_subscription_releases_access_lease(monkeypatch):
    device, service = install_device(monkeypatch)
    worker = bridge.BLEVoiceBridge(BLEDeviceSelectionPolicy(preferred_identifier='AA:BB:CC:DD:EE:FF'))
    worker._serve_device = AsyncMock(side_effect=RuntimeError('missing characteristic'))
    with pytest.raises(RuntimeError, match='missing characteristic'):
        asyncio.run(worker._connect_and_serve())
    assert device.closed and service.closed


def test_cancellation_releases_access_resources(monkeypatch):
    device, service = install_device(monkeypatch)
    service.request_access_async = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(bridge._request_gatt_access('AA:BB:CC:DD:EE:FF', attempts=1))
    assert device.closed and service.closed


def test_ready_resets_reconnect_delay():
    worker = bridge.BLEVoiceBridge(BLEDeviceSelectionPolicy())
    for _ in range(9):
        worker.reconnect_policy.next_decision()
    worker._emit_session_actions([StateChanged(SessionState.READY)])
    assert worker.reconnect_policy.next_decision().delay == 1


def test_retry_after_subscription_failure_uses_fresh_services(monkeypatch):
    calls = []
    worker = bridge.BLEVoiceBridge(BLEDeviceSelectionPolicy())

    class Client:
        def __init__(self, device, **kwargs):
            calls.append(kwargs)
            self.is_connected = False
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def start_notify(self, *args):
            if len(calls) == 1:
                raise RuntimeError('missing characteristic')

    monkeypatch.setattr(bridge, 'BleakClient', Client)
    worker._emit_session_actions = lambda actions: None
    async def run():
        with pytest.raises(RuntimeError, match='missing characteristic'):
            await worker._serve_device(SimpleNamespace(name='test', address='test'))
        await worker._serve_device(SimpleNamespace(name='test', address='test'))
    asyncio.run(run())
    assert len(calls) == 2
    assert all(call['winrt']['use_cached_services'] is False for call in calls)
