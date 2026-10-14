"""米遥 Windows 版 CLI。

用法：
  python -m miao panel      启动遥控器面板（左侧遥控器图形 + 右侧可编程按键）
  python -m miao scan       扫描附近 BLE 设备（确认能识别到遥控器）
  python -m miao keys       打印遥控器每个按键的真实 VK 码（按键校准）
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from .ble_bridge import scan_devices


def _scan() -> None:
    async def run() -> None:
        print("扫描 8 秒…")
        rows = await scan_devices()
        if not rows:
            print("未发现任何 BLE 设备")
            return
        for address, name, is_atvv in rows:
            marker = "  ← 广播 ATVV 服务" if is_atvv else ""
            print(f"{address}  {name or '(无名)'}{marker}")

    asyncio.run(run())


def _keys() -> None:
    """只观察不拦截：打印遥控器每个按键的真实 vk/scan，用于校准。"""
    import ctypes.wintypes as wintypes

    from .buttons import ButtonAction, ButtonBinding, ButtonPresetCatalog
    from .dispatcher import ButtonDispatcher
    from .keyhook import RemoteKeyHook

    def noop(*_args) -> None:
        return None

    dispatcher = ButtonDispatcher(
        catalog=ButtonPresetCatalog(),
        preset_id="pointer",
        executor=type(
            "NullExecutor",
            (),
            {name: noop for name in (
                "tap_key press_shortcut keys_down keys_up launch type_text "
                "start_pointer_move stop_pointer_move scroll focus_target "
                "launch_or_focus_target previous_task next_task on_mode_changed "
                "on_preset_changed"
            ).split()},
        )(),
    )
    hook = RemoteKeyHook(dispatcher, debug_keys=True)
    hook.start()
    print("按键探针已启动：按下遥控器任意键，会打印 vk/scan。Ctrl+C 退出。", flush=True)
    try:
        while True:
            asyncio.run(asyncio.sleep(3600))
    except KeyboardInterrupt:
        pass
    finally:
        hook.stop()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="miao", description="米遥 Windows 版")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("panel", help="启动遥控器面板（默认）")
    sub.add_parser("scan", help="扫描附近 BLE 设备")
    sub.add_parser("keys", help="打印遥控器按键的真实 VK 码")

    args = parser.parse_args()
    if args.command == "scan":
        _scan()
    elif args.command == "keys":
        _keys()
    else:
        from .remote_panel import run_panel

        run_panel()


if __name__ == "__main__":
    main()
