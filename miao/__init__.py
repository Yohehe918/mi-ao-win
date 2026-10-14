"""米遥 Windows 移植版（mi-ao-win）。

把小米蓝牙遥控器 2 Pro 变成 Windows 上的语音输入工具。
协议核心移植自 fanxeon/mi-ao（macOS / Swift，MIT License）。
"""

from .errors import ConfigurationError, MiAoError, ProtocolError

__all__ = ["MiAoError", "ProtocolError", "ConfigurationError"]
