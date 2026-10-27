"""Local hardware reconnect check; no recording, input injection or settings changes."""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from miao.ble_bridge import BLEVoiceBridge
from miao.connection_policy import BLEDeviceSelectionPolicy
from miao.session import SessionState


async def main():
    address = json.loads((Path(os.environ['APPDATA']) / 'mi-ao-win/config.json').read_text(encoding='utf-8-sig'))['device_address']
    for cycle in range(1, 4):
        ready = asyncio.Event()
        worker = BLEVoiceBridge(
            BLEDeviceSelectionPolicy(preferred_identifier=address),
            on_state=lambda event: ready.set() if event.state is SessionState.READY else None)
        task = asyncio.create_task(worker.run())
        try:
            await asyncio.wait_for(ready.wait(), 50)
            print(f'Cycle {cycle}: ATVV READY', flush=True)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        assert worker._client is None
        print(f'Cycle {cycle}: disconnected and resources released', flush=True)
        await asyncio.sleep(1)


if __name__ == '__main__':
    asyncio.run(main())
