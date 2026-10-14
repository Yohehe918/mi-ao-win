# 米遥 Windows

把小米蓝牙遥控器 2 Pro（RC003）变成 Windows 上的按住语音输入工具和可编程小键盘。

当前版本：**0.2.0**。支持范围：Windows 10 / 11 x64、RC003 固件 2671。
已在开发者的 RC003 设备上验证按住语音、连续录音、普通方向键、返回和音量按键；其他电脑和蓝牙适配器仍需各自验收。

## 安装

在本仓库 Releases 下载 `mi-ao-win-0.2.0-setup-x64.exe`，核对同批 `SHA256SUMS.txt` 后运行。无需 Python。
首次设置引导你完成：

1. Windows 蓝牙配对遥控器（菜单键 + Home）。
2. 检查虚拟音频设备；缺少 VB-CABLE 时由你确认并打开原厂安装程序。
3. 选择语音输入软件，把麦克风设为 CABLE Output，并录制按住输入快捷键。
4. 启用增强按键，亲自确认 Windows 管理员授权。
5. 在普通输入框测试语音和按键，确认成功后完成设置。

VB-CABLE 为 VB-Audio 的 donationware，原始未修改驱动包随应用附带。请阅读原厂许可，个人用户可捐赠支持；专业、公司和组织用途应遵守其授权要求。
官网：https://vb-audio.com/Cable/ 。授权：https://vb-audio.com/Services/licensing.htm 。安装后可能需要重启 Windows。

## 使用与边界

- 按住语音键说话，松手结束；语音由你选择的输入法识别，米遥不运行本地识别模型。
- **豆包原生兼容模式仅验证输入法 v0.9.2.7**，会校验已安装 DLL。未知版本不自动放行；不附带或自动安装豆包。
- 其他支持快捷键的语音软件可在首次设置中配置，必须实际确认能出字。
- 新安装四方向为普通上下左右，TV 默认为无动作。旧用户已有配置不会被安装程序覆盖。
- **TV 免按遥控器收音尚不可用**，不要把能显示语音窗口理解为已经能免按输入。
- 13 键均可编辑单击、双击、长按。返回、音量及语音、TV 原生事件接管依赖增强后台。
- 增强功能整合在同一个应用里，主窗口保持普通权限，专用后台由用户授权运行。
- 增强捕获要求可验证的 RC003 独立驱动宿主，共享宿主时会拒绝接管。
- 重复打开只显示已有窗口。关闭窗口缩到托盘，托盘右键退出会停止主程序与增强后台。
- 发布文件没有商业 Authenticode 签名；尚未完成全新电脑及所有蓝牙适配器的验收。

完整说明见 [使用说明](docs/使用说明.txt)、[隐私与本地数据](docs/PRIVACY.md)。

## 升级和卸载

先从托盘退出米遥，再运行新版安装包。默认安装于当前用户的 LocalAppData/Programs/MiAoWin。
Windows“已安装的应用”中可以卸载米遥。安装和卸载都保留 AppData/Roaming/mi-ao-win 中的用户配置。
卸载不会移除共享的 VB-CABLE 或语音软件。没有自动静默升级或遥测上传。

## 从源码构建

需要 Windows x64、Python 3.11+ 、PowerShell 7 和 Inno Setup 6（也可使用其便携编译器）。

```powershell
py -m pip install -r requirements-build.txt
py -m pytest -q -k 'not test_clipboard'
pwsh -File scripts/build_release.ps1 -Compiler 'PATH/TO/ISCC.exe'
```

构建脚本下载并验证固定散列的 VB-CABLE / Frida 官方包，生成图标、版本元数据、组件许可证及隐私检查报告。
本机用户配置、开发日志、录音和凭据不进入构建。源码构建不需要个人 GitHub token。
也可先运行 `py scripts/prepare_release.py`，再用 `py launcher_gui.py` 启动源码版。

## 来源与许可证

ATVV 协议、ADPCM 解码、连接策略和按键模型参考及移植自 [fanxeon/mi-ao](https://github.com/fanxeon/mi-ao)（Mac 原版，MIT）。
原作者署名：Copyright (c) 2026 FanXeon@Poemcoder with Codex。
Windows 传输、输入、音频与本界面由本项目实现。见 [LICENSE](LICENSE)、[NOTICE](NOTICE)、[第三方组件](THIRD_PARTY_NOTICES.md)。
本项目不代表小米、VB-Audio、豆包或 OpenAI，也不表示这些厂商为本项目背书。
