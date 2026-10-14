"""对应 Swift 的 BridgeError：配置 / 协议两类错误。"""


class MiAoError(Exception):
    """米遥错误基类。"""


class ConfigurationError(MiAoError):
    """配置无效（对应 BridgeError.configuration）。"""


class ProtocolError(MiAoError):
    """协议失败（对应 BridgeError.protocolFailure）。"""
