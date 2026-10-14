"""First-run checks. Inspection never changes another application's settings."""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import platform
import subprocess
import sys
import zipfile

from .doubao_voice import DLL_SHA256

DOUBAO_WEBSITE = "https://shurufa.doubao.com/pc"
CABLE_WEBSITE = "https://vb-audio.com/Cable/"
CABLE_ARCHIVE = "VBCABLE_Driver_Pack45.zip"
CABLE_SHA256 = "b950e39f01af1d04ea623c8f6d8eb9b6ea5c477c637295fabf20631c85116bfb"


@dataclass(frozen=True)
class Check:
    key: str
    label: str
    ready: bool
    detail: str


def doubao_installations(roots=None):
    if roots is None:
        roots = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "DoubaoIME" / "versions",
                 Path(os.environ.get("LOCALAPPDATA", "")) / "DoubaoIME" / "versions"]
    result = []
    for root in roots:
        if root.is_dir():
            for folder in root.iterdir():
                dll = folder / "rpc.dll"
                if folder.is_dir() and dll.is_file():
                    verified = (folder.name == "v0.9.2.7" and hashlib.sha256(dll.read_bytes()).hexdigest() == DLL_SHA256)
                    result.append((folder, verified))
    return result


def check_doubao(roots=None):
    installs = doubao_installations(roots)
    verified = [folder for folder, valid in installs if valid]
    if verified:
        # Do not silently select an old ABI when a different version is running.
        return Check("provider", "语音软件", True, "已找到经验证的豆包输入法 v0.9.2.7；请启动该版本。")
    versions = ", ".join(folder.name for folder, _ in installs)
    return Check("provider", "语音软件", False,
                 "豆包版本尚未验证："+versions+"。可选择其他支持快捷键的语音软件。" if installs else "尚未找到兼容的豆包输入法。请先安装并启动语音软件。")


def check_audio(devices=None):
    if devices is None:
        import sounddevice
        devices = sounddevice.query_devices()
    outputs = any(d.get("max_output_channels", 0) > 0 and "cable input" in d["name"].lower() for d in devices)
    inputs = any(d.get("max_input_channels", 0) > 0 and "cable output" in d["name"].lower() for d in devices)
    ready = outputs and inputs
    return Check("audio", "虚拟麦克风", ready,
                 "CABLE Input / Output 已就绪；语音软件的麦克风请选择 CABLE Output。" if ready
                 else "需要安装 VB-CABLE；安装完成后可能需要重启 Windows。")


def inspect_environment():
    checks = [Check("system", "Windows 系统", sys.platform == "win32" and platform.machine().lower() in ("amd64", "x86_64"),
                    "支持 Windows 10 / 11 x64；其他架构尚未验证。")]
    try:
        checks.append(check_audio())
    except Exception:
        checks.append(Check("audio", "虚拟麦克风", False, "音频设备检测失败，请检查系统声音设备后重新检测。"))
    try:
        checks.append(check_doubao())
    except OSError:
        checks.append(Check("provider", "语音软件", False, "无法读取豆包安装信息。请重新检测或选择其他语音软件。"))
    return checks


def unpack_cable(archive, destination):
    """Extract only our pinned vendor archive, with path traversal rejection."""
    archive, destination = Path(archive), Path(destination).resolve()
    if hashlib.sha256(archive.read_bytes()).hexdigest() != CABLE_SHA256:
        raise RuntimeError("VB-CABLE 原始包校验失败，请重新下载米遥安装包。")
    with zipfile.ZipFile(archive) as package:
        for entry in package.infolist():
            target = (destination / entry.filename).resolve()
            if not target.is_relative_to(destination):
                raise RuntimeError("驱动包包含无效路径。")
        package.extractall(destination)
    executable = destination / "VBCABLE_Setup_x64.exe"
    if not executable.is_file():
        raise RuntimeError("驱动包缺少 Windows x64 安装程序。")
    return executable


def launch_cable_installer():
    from .config import app_data_dir
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).parent.parent))
    executable = unpack_cable(root / "assets" / "vendor" / CABLE_ARCHIVE,
                              app_data_dir() / "vendor" / "vb-cable-45")
    import ctypes
    execute = ctypes.windll.shell32.ShellExecuteW
    execute.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int]
    execute.restype = ctypes.c_void_p
    result = execute(None, "runas", str(executable), None, str(executable.parent), 1)
    if not result or result <= 32:
        raise RuntimeError("驱动安装未启动。请确认 Windows 授权，或稍后重试。")


def open_bluetooth():
    os.startfile("ms-settings:bluetooth")


def open_sound():
    subprocess.Popen(["control.exe", "mmsys.cpl", ",1"])
