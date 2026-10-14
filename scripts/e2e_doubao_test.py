"""豆包联调测试：启动应用，按住遥控器语音键时自动按住「豆包快捷键」。

脚本会把 executor 的 keys_down/keys_up 调用打印出来，方便确认快捷键是否真的发出。
用法：py -X utf8 scripts/e2e_doubao_test.py [秒数]
"""

from __future__ import annotations

import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from miao.app import MiAoApp  # noqa: E402


def main() -> None:
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 60

    app = MiAoApp()
    app.status.listeners.append(lambda s: print(f"[状态] {s.state} {s.detail}", flush=True))

    # 包一层，把「按住/释放豆包快捷键」的动作打出来
    original_down, original_up = app.executor.keys_down, app.executor.keys_up

    def logged_down(vk, modifiers=()):
        print(f"  >>> 按下豆包快捷键 vk=0x{vk:02X} modifiers={modifiers}", flush=True)
        original_down(vk, modifiers)

    def logged_up(vk, modifiers=()):
        print(f"  <<< 释放豆包快捷键 vk=0x{vk:02X} modifiers={modifiers}", flush=True)
        original_up(vk, modifiers)

    app.executor.keys_down = logged_down
    app.executor.keys_up = logged_up

    shortcut = app.doubao_shortcut()
    print("豆包快捷键配置:", shortcut.display_name if shortcut else "（未设置，语音键不会触发豆包）")
    print("虚拟麦克风:", "已启动 设备 #%s" % app.mic.device_index if app.mic.running else "未启动")

    app.start()
    print(f"\n请在光标处（豆包输入框或任意文本框）按住遥控器语音键说话，{seconds} 秒后结束…\n", flush=True)
    try:
        time.sleep(seconds)
    finally:
        app.stop()
    print("\n=== 结束 ===")
    print("虚拟声卡写入样本数:", app.mic._samples_written)
    print("麦克风流错误:", app.mic.last_error)


if __name__ == "__main__":
    main()
