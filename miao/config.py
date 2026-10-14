"""应用配置（JSON，存 %APPDATA%\\mi-ao-win\\）。"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_DIR_NAME = "mi-ao-win"


def app_data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData/Roaming")
    return Path(base) / APP_DIR_NAME


@dataclass
class Config:
    """应用配置。

    项目已从「本地 whisper 转写」改为「遥控器 = 虚拟麦克风 + 可编程小键盘」：
    语音文字由豆包等输入法识别，本程序只负责把遥控器音频送进虚拟声卡、
    并把按键映射成快捷键。
    """

    device_address: str | None = None
    device_name_filter: str = "小米"
    voice_connection_mode: str = "always_ready"  # always_ready / smart_sleep
    silence_timeout: float = 1.5  # 松手信号的兜底：静音超时自动收口
    silence_threshold: float = 35.0  # RMS
    # 虚拟麦克风（遥控器音频 → VB-CABLE）
    mic_forward_enabled: bool = True
    virtual_mic_device: str = ""  # 设备名过滤词，空 = 自动匹配 "CABLE Input"
    mic_gain_db: float = 0.0
    # 豆包语音输入（按住说话）
    doubao_hotkey_vk: int = 66
    doubao_hotkey_modifiers: list[str] = field(default_factory=lambda: ["right_alt"])
    doubao_handsfree_hotkey_vk: int = 32
    doubao_handsfree_hotkey_modifiers: list[str] = field(default_factory=lambda: ["right_alt"])
    doubao_native_control: bool = True
    enhanced_keys_enabled: bool = False
    # 按键映射
    active_preset_id: str = "standard"
    setup_completed: bool = False
    submit_enabled: bool = True  # 「发送文本」动作是否回车
    press_enter_after_paste: bool = True

    @property
    def resolved_mic_name_hint(self) -> str:
        return self.virtual_mic_device.strip()


def config_path() -> Path:
    return app_data_dir() / "config.json"


def load_config(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.exists():
        return Config()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {f for f in Config.__dataclass_fields__}
        return Config(**{k: v for k, v in data.items() if k in known})
    except (ValueError, TypeError):
        return Config()


def save_config(config: Config, path: Path | None = None) -> Path:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(asdict(config), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)
    return path
