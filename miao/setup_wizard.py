"""A first-run checklist using real device and runtime observations."""
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox
import webbrowser

from . import ConfigurationError
from .panel_widgets import BG, CARD, TEXT, MUTED, GREEN, ERROR, FONT, SoftButton, ShortcutRecorder
from .readiness import (inspect_environment, launch_cable_installer, open_bluetooth,
                        open_sound, DOUBAO_WEBSITE, Check)
from .version import VERSION

PROVIDERS = ("豆包输入法（已验证版本）", "其他语音软件（自定义快捷键）", "仅使用按键映射")


def completion_errors(checks, provider, remote_ready, enhanced_ready, mic_running, shortcut, confirmed):
    errors = []
    if not checks.get("system", Check("", "", False, "")).ready:
        errors.append("请使用 Windows 10 / 11 x64")
    if not remote_ready and provider != PROVIDERS[2]:
        errors.append("遥控器尚未完成语音连接")
    if not enhanced_ready:
        errors.append("增强按键尚未接管")
    if provider != PROVIDERS[2]:
        if not checks.get("audio", Check("", "", False, "")).ready or not mic_running:
            errors.append("虚拟麦克风尚未就绪")
        if provider == PROVIDERS[0] and not checks.get("provider", Check("", "", False, "")).ready:
            errors.append("豆包版本尚未通过兼容性检查")
        if shortcut is None:
            errors.append("请先录制语音软件的按住输入快捷键")
    if not confirmed:
        errors.append("请先完成一次实际按键 / 语音测试并勾选确认")
    return errors


class SetupWizard:
    def __init__(self, panel):
        self.panel, self.app = panel, panel.app
        self.window = tk.Toplevel(panel.root, bg=BG)
        self.window.title("米遥 · 首次设置与自检")
        width, height = min(900, panel.root.winfo_screenwidth()-70), min(820, panel.root.winfo_screenheight()-100)
        self.window.geometry(f"{width}x{height}+40+40")
        self.window.minsize(700, 600)
        self.window.transient(panel.root)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self._queue = queue.Queue()
        self._checks, self._labels = {}, {}
        self._after = None
        self._closed = False
        self._busy = False
        self._provider = tk.StringVar(value=PROVIDERS[2] if not self.app.config.mic_forward_enabled else
                                     PROVIDERS[0] if self.app.config.doubao_native_control else PROVIDERS[1])
        self._confirmed = tk.BooleanVar(value=False)
        self.feedback = tk.StringVar(value="配对遥控器并按顺序完成检查；设置不会覆盖已有按键映射。")
        header = ttk.Frame(self.window, padding=(24, 18))
        header.pack(fill="x")
        ttk.Label(header, text="几步，准备好你的遥控器", font=(FONT, -27, "bold")).pack(anchor="w")
        ttk.Label(header, text=f"米遥 {VERSION} · Windows 10 / 11 x64 · RC003 固件 2671", foreground=MUTED).pack(anchor="w", pady=(5, 0))
        footer = ttk.Frame(self.window, padding=(24, 12))
        footer.pack(side="bottom", fill="x")
        ttk.Checkbutton(footer, text="我已在普通输入框测试：按键正常；启用语音时，长按说话能出字。",
                        variable=self._confirmed).pack(anchor="w")
        ttk.Label(footer, textvariable=self.feedback, foreground=MUTED, wraplength=800).pack(anchor="w", pady=8)
        buttons = ttk.Frame(footer)
        buttons.pack(fill="x")
        SoftButton(buttons, "完成设置", self.finish, primary=True, font=(FONT, -18)).pack(side="right", padx=4)
        SoftButton(buttons, "稍后继续", self.close, font=(FONT, -18)).pack(side="right", padx=4)
        SoftButton(buttons, "重新检测", self.refresh, font=(FONT, -18)).pack(side="left")
        viewport = tk.Canvas(self.window, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.window, orient="vertical", command=viewport.yview)
        scrollbar.pack(side="right", fill="y")
        viewport.pack(fill="both", expand=True)
        viewport.configure(yscrollcommand=scrollbar.set)
        body = ttk.Frame(viewport, padding=(24, 0, 24, 10))
        body_id = viewport.create_window(0, 0, window=body, anchor="nw")
        body.bind("<Configure>", lambda _: viewport.configure(scrollregion=viewport.bbox("all")))
        viewport.bind("<Configure>", lambda e: viewport.itemconfigure(body_id, width=e.width))
        self.window.bind("<MouseWheel>", lambda e: viewport.yview_scroll(-int(e.delta/120), "units"))
        self._row(body, "remote", "1  连接遥控器", "长按菜单键 + Home 配对，然后等待米遥连接。", "打开蓝牙设置", open_bluetooth)
        self._row(body, "audio", "2  准备虚拟麦克风", "正在检测 CABLE Input / Output…", "安装 VB-CABLE", self.install_cable)
        ttk.Label(body, text="VB-CABLE 来自 VB-Audio，是 donationware；个人用户可按用途捐赠，专业使用请遵守其授权。",
                  foreground=MUTED, wraplength=790, font=(FONT, -12)).pack(anchor="w", pady=(0, 10))
        provider_frame = ttk.Frame(body)
        provider_frame.pack(fill="x", pady=(2, 8))
        ttk.Label(provider_frame, text="3  选择语音输入方式", font=(FONT, -19, "bold")).pack(anchor="w")
        chooser = ttk.Combobox(provider_frame, values=PROVIDERS, textvariable=self._provider, state="readonly", font=(FONT,-16))
        chooser.pack(fill="x", pady=6)
        chooser.bind("<<ComboboxSelected>>", lambda _: self._confirmed.set(False))
        provider_status = tk.StringVar(value="正在检测语音软件…")
        self._labels["provider"] = provider_status
        ttk.Label(provider_frame, textvariable=provider_status, foreground=MUTED, wraplength=790).pack(anchor="w")
        ttk.Label(provider_frame, text="在语音软件里把麦克风设为 CABLE Output，模式设为按住说话。",
                  foreground=MUTED, wraplength=790).pack(anchor="w", pady=5)
        controls = ttk.Frame(provider_frame)
        controls.pack(fill="x")
        SoftButton(controls, "豆包官网", lambda: webbrowser.open(DOUBAO_WEBSITE), font=(FONT, -15)).pack(side="left", padx=(0, 6))
        SoftButton(controls, "系统录音设备", open_sound, font=(FONT, -15)).pack(side="left")
        entry = ttk.Entry(provider_frame, font=(FONT, -18))
        entry.pack(fill="x", pady=(8, 4))
        self._shortcut = self.app.doubao_shortcut()
        self._applied_provider = self._provider.get()
        self._applied_shortcut = self._shortcut
        self._recorder = ShortcutRecorder(entry, self._recorded, self.feedback.set)
        self._recorder.show(self._shortcut)
        ttk.Label(provider_frame, text="点击上方录制按住输入快捷键。豆包已验证版本使用原生兼容模式；其他软件需实测快捷键。",
                  foreground=MUTED, wraplength=790, font=(FONT, -12)).pack(anchor="w")
        SoftButton(provider_frame, "应用语音设置", self.apply_voice, font=(FONT, -16)).pack(anchor="w", pady=6)
        self._row(body, "enhancement", "4  开启增强按键", "接管语音、TV、返回和音量键；Windows 授权需要你确认。", "启用增强按键", lambda: self.app.set_enhanced_keys(True))
        self._row(body, "signal", "5  试说一句话", "把焦点放到普通输入框，长按语音键，说完后松手。", "回到主界面测试", self.test_in_main)
        ttk.Label(body, text="当前限制：TV 免按遥控器收音尚不可用；新用户默认不为 TV 配置语音动作。",
                  foreground=MUTED, wraplength=790).pack(anchor="w", pady=8)
        self.refresh()
        self._poll()

    def _row(self, body, key, title, detail, button, command):
        frame = ttk.Frame(body, padding=(12, 10))
        frame.pack(fill="x", pady=5)
        SoftButton(frame, button, lambda: self._action(command), font=(FONT, -15)).pack(side="right", padx=(12, 0))
        content = ttk.Frame(frame)
        content.pack(fill="x", expand=True)
        ttk.Label(content, text=title, font=(FONT, -18, "bold")).pack(anchor="w")
        var = tk.StringVar(value=detail)
        self._labels[key] = var
        ttk.Label(content, textvariable=var, foreground=MUTED, wraplength=560).pack(anchor="w", pady=(4, 0))

    def _action(self, command):
        try:
            command()
        except (OSError, RuntimeError, ConfigurationError) as error:
            self.feedback.set(str(error))

    def _recorded(self, shortcut):
        self._shortcut = shortcut
        self._confirmed.set(False)
        self.feedback.set("快捷键已录制，请点击应用语音设置。")

    def apply_voice(self):
        from .remote_panel import _modifier_values
        self.app._release_doubao()
        self.app._stop_handsfree()
        config = self.app.config
        config.mic_forward_enabled = self._provider.get() != PROVIDERS[2]
        config.doubao_native_control = self._provider.get() == PROVIDERS[0]
        if self._shortcut:
            config.doubao_hotkey_vk = self._shortcut.vk_code
            config.doubao_hotkey_modifiers = _modifier_values(self._shortcut)
        self.app.save()
        self.app.restart_mic()
        self._applied_provider, self._applied_shortcut = self._provider.get(), self._shortcut
        self._confirmed.set(False)
        self.feedback.set("语音设置已应用。请在语音软件选择 CABLE Output，再完成一次实际输入。")

    def install_cable(self):
        if self._checks.get("audio") and self._checks["audio"].ready:
            self.feedback.set("VB-CABLE 已安装，跳过重复安装。请在语音软件里选择 CABLE Output。")
            return
        if messagebox.askokcancel("安装 VB-CABLE", "即将打开 VB-Audio 原始安装程序。\nVB-CABLE 是 donationware，来源：https://vb-audio.com/Cable/\n请阅读原厂授权并确认 Windows 管理员授权。安装后可能需要重启。", parent=self.window):
            launch_cable_installer()
            self.feedback.set("完成原厂安装后点击重新检测；如果设备仍未出现，请先重启 Windows。")

    def refresh(self):
        if self._busy:
            return
        self._busy = True
        def inspect():
            try:
                self._queue.put(inspect_environment())
            finally:
                self._busy = False
        threading.Thread(target=inspect, daemon=True, name="miao-readiness").start()

    def _ready_state(self):
        session = getattr(getattr(self.app, "_bridge", None), "session", None)
        remote = getattr(getattr(session, "state", None), "value", "") in ("ready", "opening", "streaming")
        enhanced = self.app.extra_keys_connected and self.app.enhancement.state == "running"
        return remote, enhanced

    def _poll(self):
        if self._closed:
            return
        try:
            while True:
                for check in self._queue.get_nowait():
                    self._checks[check.key] = check
                    if check.key in self._labels:
                        self._labels[check.key].set(("✓ " if check.ready else "待完成 · ")+check.detail)
        except queue.Empty:
            pass
        remote, enhanced = self._ready_state()
        self._labels["remote"].set("✓ 遥控器语音连接已就绪。" if remote else "长按菜单键 + Home 配对；连接失败时可在主界面设置里重连。")
        if self._provider.get() == PROVIDERS[2] and enhanced:
            self._labels["remote"].set("增强按键已接管；请实际按键确认连接。仅按键模式不要求语音连接。")
        self._labels["enhancement"].set(self.app.enhancement.detail)
        level = self.app.mic.level
        frames = getattr(self.app, "_audio_frames", 0)
        self._labels["signal"].set(f"本段收到 {frames} 个音频帧 · 当前电平 {level:.0f}；是否出字仍需你实际确认。")
        if self._provider.get() != PROVIDERS[0]:
            self._labels["provider"].set("仅按键模式无需语音软件。" if self._provider.get() == PROVIDERS[2] else
                                         "使用其他语音软件：录制其按住输入快捷键，并实际确认能出字。")
        elif "provider" in self._checks:
            check = self._checks["provider"]
            self._labels["provider"].set(("✓ " if check.ready else "待完成 · ")+check.detail)
        self._after = self.window.after(500, self._poll)

    def test_in_main(self):
        self.window.withdraw()
        self.panel.root.deiconify()
        self.panel.root.lift()
        self.panel._hint("请到普通输入框长按语音键测试，完成后从设置打开首次设置与自检。")

    def finish(self):
        if (self._applied_provider, self._applied_shortcut) != (self._provider.get(), self._shortcut):
            self.feedback.set("请先应用当前语音设置，再重新完成一次实际输入测试。")
            return
        remote, enhanced = self._ready_state()
        errors = completion_errors(self._checks, self._provider.get(), remote, enhanced,
                                   self.app.mic.running, self._shortcut, self._confirmed.get())
        if errors:
            self.feedback.set("；".join(errors))
            return
        self.app.config.setup_completed = True
        self.app.save()
        self.panel._hint("设置已完成。按住语音键说话，松手结束。")
        self.close()

    def show(self):
        self.window.deiconify()
        self.window.lift()
        self.refresh()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._after:
            self.window.after_cancel(self._after)
        self.window.destroy()
        self.panel._setup_wizard = None
