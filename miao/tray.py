"""系统托盘（pystray）。

状态着色：灰=未连接，黄=协商/重连中，绿=就绪，红=收音中。
菜单：状态 / 手动重连 / 发送文本是否回车 / 麦克风状态 / 打开面板 / 退出。
"""

from __future__ import annotations

import logging
import threading

import pystray
from PIL import Image, ImageDraw

from .app import MiAoApp

log = logging.getLogger("miao.tray")

_STATE_COLORS = {
    "disconnected": (128, 128, 128),
    "negotiating": (230, 180, 40),
    "ready": (60, 180, 80),
    "opening": (230, 180, 40),
    "streaming": (220, 60, 60),
    "command": (80, 140, 240),
    "mic_off": (128, 128, 128),
    "mic_error": (220, 60, 60),
    "error": (220, 60, 60),
}


def _icon_image(color: tuple[int, int, int]) -> Image.Image:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((8, 8, 56, 56), fill=color + (255,))
    draw.ellipse((24, 24, 40, 40), fill=(255, 255, 255, 255))
    return image


class TrayApp:
    def __init__(self, app: MiAoApp, on_open_settings=None) -> None:
        self.app = app
        self._on_open_settings = on_open_settings
        self._icon: pystray.Icon | None = None
        app.status.listeners.append(self._on_status)

    def run(self) -> None:
        """在当前线程运行托盘消息循环（调用方负责放在合适线程）。"""
        menu = pystray.Menu(
            pystray.MenuItem(self._status_text, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("手动重连", self._reconnect),
            pystray.MenuItem(
                "发送文本时自动回车",
                self._toggle_submit,
                checked=lambda _: self.app.config.submit_enabled,
            ),
            pystray.MenuItem(self._mic_text, None, enabled=False),
            pystray.MenuItem("设置…", self._open_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._quit),
        )
        self._icon = pystray.Icon("miao-win", _icon_image(_STATE_COLORS["disconnected"]), "米遥", menu)
        self._icon.run()

    def stop(self) -> None:
        if self._icon:
            self._icon.stop()

    # ------------------------------------------------------------------

    def _status_text(self, _) -> str:
        status = self.app.status
        labels = {
            "disconnected": "未连接",
            "negotiating": "协商中",
            "ready": "就绪 · 按住语音键说话",
            "opening": "打开麦克风",
            "streaming": "收音中 · 松手结束",
            "command": "按键指令",
            "mic_off": "麦克风转发已关闭",
            "mic_error": "虚拟麦克风不可用",
            "error": "错误",
        }
        label = labels.get(status.state, status.state)
        return f"米遥：{label}" + (f"（{status.detail}）" if status.detail else "")

    def _on_status(self, status) -> None:
        if self._icon:
            color = _STATE_COLORS.get(status.state, (128, 128, 128))
            self._icon.icon = _icon_image(color)
            self._icon.update_menu()

    def _reconnect(self, _) -> None:
        self.app.reconnect_now()

    def _toggle_submit(self, _) -> None:
        self.app.config.submit_enabled = not self.app.config.submit_enabled
        self.app.save()

    def _mic_text(self, _) -> str:
        mic = self.app.mic
        if not self.app.config.mic_forward_enabled:
            return "虚拟麦克风：已关闭"
        if mic.running:
            return f"虚拟麦克风：运行中（设备 #{mic.device_index}）"
        return "虚拟麦克风：未启动（缺 VB-CABLE？）"

    def _open_settings(self, _) -> None:
        if self._on_open_settings:
            self._on_open_settings()

    def _quit(self, _) -> None:
        self.app.stop()
        self.stop()
