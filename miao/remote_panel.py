"""Native dark remote editor. Selecting the remote never executes its mapping."""
from __future__ import annotations

import dataclasses
import logging
from logging.handlers import RotatingFileHandler
import ctypes
import sys
import queue
import shlex
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageTk

from . import ConfigurationError
from .app import MiAoApp
from .config import app_data_dir
from .buttons import ButtonAction, ButtonBinding, RemoteButton, default_pointer_preset, default_standard_preset
from .panel_widgets import (
    BG, CARD, FIELD, BORDER, TEXT, MUTED, BLUE, ERROR, GREEN, FONT,
    SoftButton, RoundedEntry, RoundedSelect, ShortcutRecorder, configure_theme, rounded, _modifiers_from_state,
)
from .tray import TrayApp
from .version import VERSION

log = logging.getLogger("miao.panel")
WINDOW_TITLE = "米遥 · 遥控器映射"
UNREPORTED_BUTTONS = frozenset({RemoteButton.BACK, RemoteButton.VOLUME_UP, RemoteButton.VOLUME_DOWN})
TRIGGERS = {"single": "单击", "double": "双击", "long": "长按"}
TYPE_ACTION, TYPE_SHORTCUT, TYPE_HOLD = "内置动作", "键盘快捷键", "按住快捷键"
TYPE_PROGRAM, TYPE_TEXT, TYPE_NONE = "启动程序", "发送文本", "无操作"
BINDING_TYPES = (TYPE_ACTION, TYPE_SHORTCUT, TYPE_HOLD, TYPE_PROGRAM, TYPE_TEXT, TYPE_NONE)
KIND_TO_TYPE = dict(zip(("action", "keyboard_shortcut", "hold_shortcut", "launch_program", "send_text", "none"), BINDING_TYPES))
ACTION_BY_NAME = {action.display_name: action for action in ButtonAction}
ACTION_CUSTOM = "自定义快捷键"
BUTTON_NAMES = {b: b.display_name + "键" for b in RemoteButton}
BUTTON_NAMES.update({RemoteButton.CENTER: "确认键", RemoteButton.HOME: "Home 键", RemoteButton.TV: "TV 键"})
# Measured normalized centers on the transparent product cutout.
BUTTON_CENTERS = {
    RemoteButton.POWER: (.239, .068, .114), RemoteButton.VOICE: (.761, .068, .114),
    RemoteButton.CENTER: (.497, .222, .216), RemoteButton.DPAD_UP: (.497, .132, .115),
    RemoteButton.DPAD_DOWN: (.497, .311, .115), RemoteButton.DPAD_LEFT: (.126, .221, .115),
    RemoteButton.DPAD_RIGHT: (.862, .221, .115), RemoteButton.BACK: (.285, .376, .175),
    RemoteButton.HOME: (.285, .470, .175), RemoteButton.VOLUME_UP: (.706, .375, .175),
    RemoteButton.VOLUME_DOWN: (.706, .470, .175), RemoteButton.MENU: (.285, .566, .175),
    RemoteButton.TV: (.706, .566, .175),
}
EDITOR_ORDER = (
    RemoteButton.VOICE, RemoteButton.CENTER, RemoteButton.BACK, RemoteButton.HOME,
    RemoteButton.MENU, RemoteButton.TV, RemoteButton.POWER, RemoteButton.DPAD_UP,
    RemoteButton.DPAD_DOWN, RemoteButton.DPAD_LEFT, RemoteButton.DPAD_RIGHT,
    RemoteButton.VOLUME_UP, RemoteButton.VOLUME_DOWN,
)
# Windows' installed Segoe MDL2 icon library, not hand-drawn pictograms.
ICONS = {
    RemoteButton.VOICE: "\ue720", RemoteButton.CENTER: "\uecca", RemoteButton.BACK: "\ue76b",
    RemoteButton.HOME: "\ue80f", RemoteButton.MENU: "\ue700", RemoteButton.TV: "\ue7f4",
    RemoteButton.POWER: "\ue7e8", RemoteButton.DPAD_UP: "\ue70e", RemoteButton.DPAD_DOWN: "\ue70d",
    RemoteButton.DPAD_LEFT: "\ue76b", RemoteButton.DPAD_RIGHT: "\ue76c",
    RemoteButton.VOLUME_UP: "\ue710", RemoteButton.VOLUME_DOWN: "\ue738",
}


def _modifier_values(spec):
    return [value for value in ("win", "ctrl", "alt", "right_alt", "shift") if any(m.value == value for m in spec.modifiers)]


def _split_arguments(raw):
    try:
        return tuple(arg[1:-1] if len(arg) > 1 and arg[0] == arg[-1] == '"' else arg
                     for arg in shlex.split(raw, posix=False))
    except ValueError as error:
        raise ConfigurationError("程序参数中的引号不成对") from error


class RemotePanel:
    def __init__(self, root, app):
        self.root, self.app = root, app
        self._alive, self._shutting = True, False
        self._tray = None
        self._events, self._statuses = queue.Queue(), queue.Queue()
        self._quit_requested, self._show_requested = threading.Event(), threading.Event()
        self._down = set()
        self._connection_state = app.status.state
        self._selected, self._trigger = RemoteButton.TV, "single"
        self._drafts = {}
        self._loading = False
        self._after_id = self._layout_id = None
        self._settings_window = None
        self._setup_wizard = None
        self._instance_guard = None
        self._enhancement_var = None
        self._placements = []
        self._s = 1.0
        self._ox = self._oy = self._list_offset = 0
        configure_theme(root)
        self.fonts = {name: tkfont.Font(root, family=FONT, size=-size, weight=weight)
                      for name, size, weight in (("hero", 44, "bold"), ("title", 34, "bold"),
                      ("body", 21, "normal"), ("input", 23, "bold"), ("small", 18, "normal"),
                      ("tiny", 15, "normal"), ("device", 26, "normal"), ("button", 21, "bold"))}
        self.icon_font = tkfont.Font(root, family="Segoe MDL2 Assets", size=-26)
        self.canvas = tk.Canvas(root, bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        with Image.open(Path(__file__).parent / "resources" / "remote-product.png") as asset:
            self._product = asset.convert("RGBA").crop((193, 83, 628, 1851))
        self._product_photo = None
        self._make_controls()
        self.canvas.bind("<Configure>", self._queue_layout)
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.root.bind("<Control-s>", lambda _: self._save_mapping())
        self.root.bind("<Escape>", lambda _: self._cancel_recording())
        app.button_listeners.append(self._on_button_event)
        app.status.listeners.append(self._on_status)
        self._load_selection()
        self._after_id = root.after(50, self._poll_events)

    def _place(self, widget, x, y, w, h):
        self._placements.append((widget, (x, y, w, h)))
        return widget

    def _button(self, text, command, box, primary=False):
        return self._place(SoftButton(self.canvas, text, command, primary=primary,
                                     font=self.fonts["button"]), *box)

    def _entry(self, variable=None):
        return RoundedEntry(self.canvas, textvariable=variable, font=self.fonts["input"])

    def _make_controls(self):
        self._button("方案", self._open_presets, (1230, 78, 78, 43))
        self._button("设置", self.open_settings, (1324, 78, 78, 43))
        self._tabs = {trigger: self._button(label, lambda t=trigger: self._select_trigger(t),
                      (662 + i * 247.5, 177, 247.5, 60), trigger == "single")
                      for i, (trigger, label) in enumerate(TRIGGERS.items())}
        for tab in self._tabs.values():
            tab.flat = True
        self._type_var = tk.StringVar()
        self._type_box = self._place(RoundedSelect(self.canvas, textvariable=self._type_var,
                         values=BINDING_TYPES, font=self.fonts["input"]), 662, 296, 742, 58)
        self._type_box.bind("<<ComboboxSelected>>", lambda _: self._type_changed())
        self._shortcut_entry = self._entry()
        self._recorder = ShortcutRecorder(self._shortcut_entry, self._recorded, lambda text: self._hint(text, True))
        self._record_button = SoftButton(self.canvas, "录制", self._arm_recording, font=self.fonts["button"])
        self._action_var = tk.StringVar()
        self._action_box = RoundedSelect(self.canvas, textvariable=self._action_var,
                           values=[*ACTION_BY_NAME, ACTION_CUSTOM], font=self.fonts["body"])
        self._action_box.bind("<<ComboboxSelected>>", lambda _: self._action_changed())
        self._program_var, self._args_var, self._text_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self._program_entry, self._args_entry = self._entry(self._program_var), self._entry(self._args_var)
        self._text_entry = self._entry(self._text_var)
        self._browse_button = SoftButton(self.canvas, "浏览", self._browse_program, font=self.fonts["button"])
        for var in (self._program_var, self._args_var, self._text_var):
            var.trace_add("write", lambda *_: self._editor_changed())
        self._save_button = self._button("保存映射", self._save_mapping, (662, 634, 448, 65), True)
        self._reset_button = self._button("恢复默认", self._restore_default, (1130, 634, 274, 65))
        self._prev_button = self._button("上一组", lambda: self._scroll_list(-1), (1235, 751, 78, 30))
        self._next_button = self._button("下一组", lambda: self._scroll_list(1), (1326, 751, 78, 30))
        self._detail_widgets = (self._shortcut_entry, self._record_button, self._action_box,
                               self._program_entry, self._args_entry, self._browse_button, self._text_entry)
        self._hint_text, self._hint_error = "", False

    def _queue_layout(self, _event=None):
        if self._layout_id:
            self.root.after_cancel(self._layout_id)
        self._layout_id = self.root.after(20, self._layout)

    def _layout(self):
        self._layout_id = None
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        self._s = min(w / 1487, h / 1058)
        self._ox, self._oy = (w - 1487 * self._s) / 2, (h - 1058 * self._s) / 2
        for name, size in (("hero", 44), ("title", 34), ("body", 21), ("input", 23),
                           ("small", 18), ("tiny", 15), ("button", 21)):
            self.fonts[name].configure(size=-max(10, round(size * self._s)))
        self.fonts["device"].configure(size=-max(13,round(26*self._s)))
        self.icon_font.configure(size=-max(13, round(26 * self._s)))
        for widget, box in self._placements:
            self._locate(widget, box)
        self._show_detail()
        self._product_photo = ImageTk.PhotoImage(self._product.resize(
            (max(1, round(194 * self._s)), max(1, round(790 * self._s))), Image.Resampling.LANCZOS))
        self._draw()

    def _locate(self, widget, box):
        x, y, w, h = box
        widget.place(x=round(self._ox + x * self._s), y=round(self._oy + y * self._s),
                     width=round(w * self._s), height=round(h * self._s))

    def _coords(self, *coords):
        return [value * self._s + (self._ox if i % 2 == 0 else self._oy) for i, value in enumerate(coords)]

    def _text(self, x, y, text, kind="body", color=TEXT, anchor="nw"):
        return self.canvas.create_text(*self._coords(x, y), text=text, font=self.fonts[kind], fill=color, anchor=anchor)

    def _box(self, box, fill=FIELD, outline=BORDER, radius=14):
        return rounded(self.canvas, self._coords(*box), radius * self._s, fill=fill, outline=outline)

    def _current_preset(self):
        return self.app.catalog.preset(self.app.active_preset_id)

    def _needs_extra_capture(self, button):
        return button in UNREPORTED_BUTTONS and not (
            getattr(self.app,"extra_keys_connected",False) and
            button in getattr(self.app,"extra_keys_seen",set())
        )

    def _key(self):
        return self.app.active_preset_id, self._selected, self._trigger

    def _binding(self, button=None, trigger=None):
        button, trigger = button or self._selected, trigger or self._trigger
        return self._drafts.get((self.app.active_preset_id, button, trigger),
                                self._current_preset().binding_for(button, trigger))

    def _display(self, binding):
        if binding.kind in ("keyboard_shortcut", "hold_shortcut"):
            return binding.shortcut.display_name.replace("+", " + ")
        if binding.kind == "action":
            return {ButtonAction.KEYBOARD_RETURN: "Enter", ButtonAction.KEYBOARD_ESCAPE: "Esc",
                    ButtonAction.VOICE_PUSH_TO_TALK: "语音输入"}.get(binding.action, binding.display_name)
        return binding.display_name

    def _draw(self):
        c = self.canvas
        c.delete("all")
        self._box((626, 42, 1439, 1016), CARD, "#2b3746", 20)
        self._text(76, 31, "遥控器映射", "hero")
        self._text(77, 99, "小米蓝牙遥控器 2 Pro", "device", MUTED)
        state = self._connection_state
        connected = state in ("ready", "opening", "streaming")
        label = {"ready": "已连接", "opening": "准备收音", "streaming": "收音中", "negotiating": "连接中"}.get(state, "未连接")
        color = GREEN if connected else ("#ffc36a" if state == "negotiating" else "#7c8ca2")
        c.create_oval(*self._coords(359, 104, 372, 117), fill=color, outline="")
        self._text(382, 100, label, "small", MUTED)
        if self._product_photo:
            c.create_image(*self._coords(224, 160), image=self._product_photo, anchor="nw")
        selected = self._selected
        for button in self._down | {selected}:
            cx, cy, r = self._button_geometry(button)
            for spread, outline, width in ((4, "#143a64", 3), (2, "#2189ff", 2), (0, "#80d3ff", 2)):
                c.create_oval(*self._coords(cx-r-spread, cy-r-spread, cx+r+spread, cy+r+spread),
                              outline=outline, width=max(1, width*self._s))
        cx, cy, r = self._button_geometry(selected)
        c.create_line(*self._coords(cx+r, cy, 439, cy, 626, max(390, min(650, cy-150))),
                      fill="#3297ff", width=max(1, 2*self._s))
        self._text(76, 982, "点击遥控器上的按键以编辑映射。", "small", MUTED)
        self._text(662, 75, BUTTON_NAMES[selected] + " / 自定义映射", "title")
        self._text(662, 130, "选择触发方式并设置对应操作。", "body", MUTED)
        self._text(662, 264, "操作类型", "body", MUTED)
        self._type_box.configure(state="readonly")
        for trigger, tab in self._tabs.items():
            tab.set(primary=trigger == self._trigger, enabled=True)
        self._save_button.set(enabled=True)
        self._reset_button.set(enabled=True)
        label = {TYPE_ACTION: "内置动作", TYPE_SHORTCUT: "快捷键", TYPE_HOLD: "按住期间持续按下",
                 TYPE_PROGRAM: "程序路径 / 启动参数", TYPE_TEXT: "文本内容", TYPE_NONE: "无需设置其他参数"}.get(self._type_var.get(), "操作")
        self._text(662, 382, label, "body", MUTED)
        self._text(662, 504, "映射预览", "body", MUTED)
        self._box((662, 540, 1404, 612))
        self._box((686, 551, 734, 600), "#354152", "#354152", 12)
        c.create_text(*self._coords(710, 575), text=ICONS[selected], font=self.icon_font, fill=TEXT)
        self._text(770, 575, "→", "input", MUTED, anchor="center")
        preview = self._display(self._binding())
        self._text(825, 575, preview[:30]+"…" if len(preview)>31 else preview, "input", anchor="w")
        if self._hint_text:
            hint = self._hint_text
        elif selected is RemoteButton.TV and self._binding().kind == 'action' and self._binding().action is ButtonAction.VOICE_HANDSFREE_TOGGLE:
            hint = "TV 免按遥控器收音尚未验证可用；建议使用长按语音键。"
        elif selected in (RemoteButton.VOICE, RemoteButton.TV) and not getattr(self.app, "extra_keys_connected", False):
            hint = "使用映射前请在设置中启用增强按键，接管原生 F5 / TV。"
        elif self._needs_extra_capture(selected):
            if getattr(self.app, "extra_keys_connected", False):
                hint = "映射可编辑保存；请按一次此键，确认增强捕获。"
            else:
                hint = "映射可编辑保存；实际触发需在设置中启用增强按键。"
        elif selected is RemoteButton.VOICE:
            hint = ("配置手势后按对应手势触发；按住说话可设在长按。"
                    if self._current_preset().has_gestures(selected) or self._trigger != "single"
                    else "默认按住说话；也可设置单击、双击、长按快捷键。")
        elif self._trigger == "double":
            hint = "350 ms 内连按两次；设置后单击会稍后触发。"
        elif self._trigger == "long":
            hint = "按住至少 600 ms 触发；按住快捷键会在松手时释放。"
        else:
            hint = "已修改，尚未保存" if self._key() in self._drafts else ""
        self._text(662, 707, hint, "tiny", ERROR if self._hint_error else MUTED)
        c.create_line(*self._coords(662, 738, 1404, 738), fill=BORDER)
        self._text(662, 758, "其他按键", "body")
        others = [b for b in EDITOR_ORDER if b is not selected]
        self._list_offset = min(self._list_offset, max(0, len(others)-3))
        self._list_buttons = others[self._list_offset:self._list_offset+3]
        self._prev_button.set(enabled=self._list_offset > 0)
        self._next_button.set(enabled=self._list_offset+3 < len(others))
        self._box((662, 794, 1404, 987), CARD, "#303e50", 13)
        for i, button in enumerate(self._list_buttons):
            y = 794+i*64
            if i: c.create_line(*self._coords(662, y, 1404, y), fill="#303e50")
            self._box((678, y+8, 726, y+56), "#354152", "#354152", 12)
            c.create_text(*self._coords(702, y+32), text=ICONS[button], font=self.icon_font, fill=TEXT)
            self._text(750, y+32, BUTTON_NAMES[button], "body", anchor="w")
            value = self._display(self._binding(button, "single"))
            self._text(962, y+32, value[:17]+"…" if len(value)>18 else value, "small", MUTED, anchor="w")
            c.create_text(*self._coords(1370, y+32), text="\ue76c", font=self.icon_font, fill=MUTED)
        mic_text = "虚拟麦克风运行中" if self.app.mic.running else ("麦克风已关闭" if not self.app.config.mic_forward_enabled else "虚拟麦克风未启动")
        self._text(77, 1016, mic_text, "tiny", MUTED)
        enhancement = getattr(self.app, 'enhancement', None)
        detail = enhancement.detail if enhancement else ('增强按键已连接' if getattr(self.app, 'extra_keys_connected', False) else '增强按键未启用')
        self._text(662, 1016, detail[:46], "tiny", MUTED)

    def _button_geometry(self, button):
        x, y, r = BUTTON_CENTERS[button]
        return 224+x*194, 160+y*790, r*194

    def _hit_button(self, x, y):
        candidates = []
        for button in BUTTON_CENTERS:
            cx, cy, r = self._button_geometry(button)
            distance = ((x-cx)**2+(y-cy)**2)**.5
            if distance <= r: candidates.append((distance/r, button))
        return min(candidates, key=lambda item: item[0])[1] if candidates else None

    def _on_canvas_click(self, event):
        x, y = (event.x-self._ox)/self._s, (event.y-self._oy)/self._s
        button = self._hit_button(x, y)
        if button is not None: self._select_button(button)
        elif 662 <= x <= 1404 and 794 <= y < 986:
            index = int((y-794)//64)
            if index < len(self._list_buttons): self._select_button(self._list_buttons[index])

    def _on_motion(self, event):
        x, y = (event.x-self._ox)/self._s, (event.y-self._oy)/self._s
        self.canvas.configure(cursor="hand2" if self._hit_button(x, y) is not None or (662<x<1404 and 794<y<987) else "arrow")

    def _on_wheel(self, event):
        if (event.x-self._ox)/self._s > 626: self._scroll_list(1 if event.delta<0 else -1)

    def _scroll_list(self, direction):
        self._list_offset = max(0, min(9, self._list_offset+direction*3))
        self._draw()

    def _select_button(self, button):
        self._selected = button
        self._hint_text, self._hint_error = "", False
        self._load_selection()

    def _select_trigger(self, trigger):
        self._trigger = trigger
        self._hint_text, self._hint_error = "", False
        self._load_selection()

    def _load_selection(self):
        self._loading = True
        binding = self._binding()
        self._spec = binding.shortcut
        custom = binding.kind == "keyboard_shortcut" or (binding.kind == "hold_shortcut" and self._selected is RemoteButton.VOICE and self._trigger == "long")
        self._type_var.set(TYPE_ACTION if custom else KIND_TO_TYPE.get(binding.kind, TYPE_NONE))
        self._recorder.armed = False
        self._recorder.show(self._spec)
        self._action_var.set(ACTION_CUSTOM if custom else
                             binding.action.display_name if binding.action else ButtonAction.UNMAPPED.display_name)
        self._program_var.set(binding.program)
        self._args_var.set(" ".join('"'+arg+'"' if ' ' in arg else arg for arg in binding.arguments))
        self._text_var.set(binding.text)
        self._loading = False
        self._show_detail()
        self._draw()

    def _show_detail(self):
        for widget in self._detail_widgets: widget.place_forget()
        kind = self._type_var.get()
        if kind in (TYPE_SHORTCUT, TYPE_HOLD):
            self._locate(self._shortcut_entry, (662, 416, 586, 62))
            self._locate(self._record_button, (1264, 416, 140, 62))
        elif kind == TYPE_ACTION:
            if self._action_var.get() == ACTION_CUSTOM:
                self._locate(self._action_box, (662, 416, 220, 62))
                self._locate(self._shortcut_entry, (896, 416, 352, 62))
                self._locate(self._record_button, (1264, 416, 140, 62))
            else:
                self._locate(self._action_box, (662, 416, 742, 62))
        elif kind == TYPE_PROGRAM:
            self._locate(self._program_entry, (662, 416, 390, 62))
            self._locate(self._args_entry, (1066, 416, 194, 62))
            self._locate(self._browse_button, (1276, 416, 128, 62))
        elif kind == TYPE_TEXT:
            self._locate(self._text_entry, (662, 416, 742, 62))
        for widget in (self._shortcut_entry, self._program_entry, self._args_entry, self._text_entry):
            widget.configure(state="normal")
        self._action_box.configure(state="readonly")
        self._record_button.set(enabled=True)
        self._browse_button.set(enabled=True)

    def _type_changed(self):
        self._show_detail()
        self._editor_changed()
        self._draw()

    def _action_changed(self):
        self._show_detail()
        self._editor_changed()
        self._draw()
        if self._action_var.get() == ACTION_CUSTOM:
            self._arm_recording()

    def _binding_from_editor(self):
        kind = self._type_var.get()
        if kind == TYPE_ACTION:
            if self._action_var.get() == ACTION_CUSTOM:
                if self._spec is None: raise ConfigurationError("请先录制快捷键。")
                factory = ButtonBinding.of_hold_shortcut if self._selected is RemoteButton.VOICE and self._trigger == "long" else ButtonBinding.of_shortcut
                return factory(self._spec)
            return ButtonBinding.of_action(ACTION_BY_NAME[self._action_var.get()])
        if kind in (TYPE_SHORTCUT, TYPE_HOLD):
            if self._spec is None: raise ConfigurationError("请先录制快捷键。")
            hold = kind == TYPE_HOLD or (self._selected is RemoteButton.VOICE and self._trigger == "long")
            return (ButtonBinding.of_hold_shortcut if hold else ButtonBinding.of_shortcut)(self._spec)
        if kind == TYPE_PROGRAM:
            if not self._program_var.get().strip(): raise ConfigurationError("请选择要启动的程序。")
            return ButtonBinding.of_launch_program(self._program_var.get().strip(), _split_arguments(self._args_var.get()))
        if kind == TYPE_TEXT:
            if not self._text_var.get(): raise ConfigurationError("发送文本不能为空。")
            return ButtonBinding.of_send_text(self._text_var.get())
        return ButtonBinding.of_none()

    def _editor_changed(self):
        if self._loading: return
        try: self._drafts[self._key()] = self._binding_from_editor()
        except (ConfigurationError, KeyError): return
        self._hint("已修改，尚未保存。")

    def _arm_recording(self):
        self._recorder.arm()
        self._hint("请按下组合键；Esc 取消。Win 组合键暂不支持录制。")

    def _cancel_recording(self):
        if self._recorder.armed:
            self._recorder.armed = False
            self._recorder.show(self._spec)
            self._hint("已取消录制。")

    def _recorded(self, spec):
        self._spec = spec
        self._recorder.show(spec)
        self._editor_changed()

    def _browse_program(self):
        path = filedialog.askopenfilename(parent=self.root, title="选择要启动的程序", filetypes=[("程序", "*.exe"), ("所有文件", "*.*")])
        if path: self._program_var.set(path)

    def _save_mapping(self):
        try:
            binding, base = self._binding_from_editor(), self._current_preset()
            preset = dataclasses.replace(base, bindings=dict(base.bindings), gestures={b: dict(g) for b, g in base.gestures.items()})
            if base.is_built_in:
                preset = dataclasses.replace(preset, id="user."+str(time.time_ns()), name="我的遥控器", is_built_in=False)
            if self._trigger == "single": preset.bindings[self._selected] = binding
            elif binding.kind == "none": preset.gestures.get(self._selected, {}).pop(self._trigger, None)
            else: preset.gestures.setdefault(self._selected, {})[self._trigger] = binding
            users = [p for p in self.app.catalog.user_presets if p.id != preset.id]+[preset]
            catalog = dataclasses.replace(self.app.catalog, user_presets=users)
            catalog.validate(allow_free_mapping=True)
            # A disk failure must not change the running mapping.
            self.app.preset_store.save(catalog, preset.id, allow_free_mapping=True)
            old_key = self._key()
            self.app.reload_catalog(catalog, preset.id)
            self._drafts.pop(old_key, None)
            if base.is_built_in:
                for key, value in list(self._drafts.items()):
                    if key[0] == base.id:
                        self._drafts[(preset.id, key[1], key[2])] = value
                        del self._drafts[key]
            self._load_selection()
            self._hint("映射已保存 · "+preset.name)
        except (ConfigurationError, OSError, KeyError) as error:
            self._hint("保存失败："+str(error), True)

    def _restore_default(self):
        default = default_pointer_preset() if self._current_preset().id == 'pointer' else default_standard_preset()
        self._drafts[self._key()] = default.binding_for(self._selected, self._trigger)
        self._load_selection()
        self._hint("已恢复默认值，点击保存映射后生效。")

    def _hint(self, text, error=False):
        self._hint_text, self._hint_error = text, error
        self._draw()

    def _dialog(self, title, width=610, height=510):
        dialog = tk.Toplevel(self.root, bg=CARD)
        dialog.title(title)
        width = min(width, self.root.winfo_screenwidth()-70)
        height = min(height, self.root.winfo_screenheight()-90)
        x = max(20, (self.root.winfo_screenwidth()-width)//2)
        y = max(20, (self.root.winfo_screenheight()-height)//2)
        dialog.geometry(f"{width}x{height}+{x}+{y}")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        return dialog

    def _open_presets(self):
        dialog = self._dialog("方案管理", 610, 360)
        frame = ttk.Frame(dialog, padding=28)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="按键方案", font=(FONT, -25, "bold")).pack(anchor="w", pady=(0, 12))
        presets, current = self.app.catalog.all_presets, self._current_preset()
        labels = [p.name+(" · 内置" if p.is_built_in else "") for p in presets]
        box = ttk.Combobox(frame, values=labels, state="readonly", font=(FONT, -18))
        box.current(presets.index(current))
        box.pack(fill="x", pady=6)
        ttk.Label(frame, text="保存内置方案的修改时，会自动创建“我的遥控器”。", foreground=MUTED).pack(anchor="w", pady=10)
        def switch():
            preset = presets[box.current()]
            try:
                self.app.preset_store.save(self.app.catalog, preset.id, allow_free_mapping=True)
                self.app.apply_preset(preset.id)
                self._load_selection()
                dialog.destroy()
            except (ConfigurationError, OSError) as error:
                messagebox.showerror("切换失败", str(error), parent=dialog)
        def clone():
            name = simpledialog.askstring("复制方案", "新方案名称", initialvalue="我的遥控器", parent=dialog)
            if name is None: return
            try:
                base = presets[box.current()]
                preset = dataclasses.replace(base, id="user."+str(time.time_ns()), name=name.strip(),
                         bindings=dict(base.bindings), gestures={b: dict(g) for b, g in base.gestures.items()}, is_built_in=False)
                catalog = dataclasses.replace(self.app.catalog, user_presets=[*self.app.catalog.user_presets, preset])
                self.app.preset_store.save(catalog, preset.id, allow_free_mapping=True)
                self.app.reload_catalog(catalog, preset.id)
                self._load_selection()
                dialog.destroy()
            except (ConfigurationError, OSError) as error:
                messagebox.showerror("复制失败", str(error), parent=dialog)
        for text, command in (("使用选中方案", switch), ("复制为新方案", clone)):
            button = SoftButton(frame, text, command, font=(FONT, -18))
            button.pack(fill="x", pady=6)

    def open_setup(self):
        if self._settings_window and self._settings_window.winfo_exists():
            self._settings_window.destroy()
            self._settings_window = None
        if self._setup_wizard:
            self._setup_wizard.show()
        else:
            from .setup_wizard import SetupWizard
            self._setup_wizard = SetupWizard(self)

    def open_settings(self):
        if self._settings_window and self._settings_window.winfo_exists():
            self._settings_window.lift()
            return
        dialog = self._dialog("语音与连接设置", 650, 940)
        self._settings_window = dialog
        viewport = tk.Canvas(dialog, bg=CARD, highlightthickness=0)
        scrollbar = ttk.Scrollbar(dialog, orient='vertical', command=viewport.yview)
        scrollbar.pack(side='right', fill='y')
        viewport.pack(fill='both', expand=True)
        viewport.configure(yscrollcommand=scrollbar.set)
        frame = ttk.Frame(viewport, padding=28)
        body_id = viewport.create_window(0, 0, window=frame, anchor='nw')
        frame.bind('<Configure>', lambda _: viewport.configure(scrollregion=viewport.bbox('all')))
        viewport.bind('<Configure>', lambda e: viewport.itemconfigure(body_id, width=e.width))
        dialog.bind('<MouseWheel>', lambda e: viewport.yview_scroll(-int(e.delta/120), 'units'))
        heading = ttk.Frame(frame)
        heading.pack(fill='x')
        SoftButton(heading, '设置与自检', self.open_setup, font=(FONT, -15)).pack(side='right')
        ttk.Label(heading, text="语音与连接", font=(FONT, -25, "bold")).pack(side='left')
        ttk.Label(frame, text="在语音输入法中选择 CABLE Output，并启用按住说话。", foreground=MUTED, font=(FONT, -15)).pack(anchor="w", pady=(8, 20))
        ttk.Label(frame, text="按住输入快捷键").pack(anchor="w")
        hotkey = ttk.Entry(frame, font=(FONT, -20))
        hotkey.pack(fill="x", pady=(8, 12))
        spec, feedback = [self.app.doubao_shortcut()], tk.StringVar()
        def recorded(value):
            spec[0] = value
            feedback.set("已录制，保存设置后生效。")
        recorder = ShortcutRecorder(hotkey, recorded, feedback.set)
        recorder.show(spec[0])
        ttk.Label(frame, text="免按输入快捷键（TV 开关）").pack(anchor="w")
        handsfree_entry = ttk.Entry(frame, font=(FONT, -20))
        handsfree_entry.pack(fill="x", pady=(8, 12))
        handsfree_spec = [self.app.handsfree_shortcut()]
        def recorded_handsfree(value):
            handsfree_spec[0] = value
            feedback.set("已录制免按快捷键，保存设置后生效。")
        handsfree_recorder = ShortcutRecorder(handsfree_entry, recorded_handsfree, feedback.set)
        handsfree_recorder.show(handsfree_spec[0])
        ttk.Label(frame, text="点击输入框录制组合键；暂不支持 Win 组合键。", foreground=MUTED, font=(FONT, -14)).pack(anchor="w")
        address = tk.StringVar(value=self.app.config.device_address or "")
        gain = tk.StringVar(value=str(self.app.config.mic_gain_db))
        for label, var in (("遥控器蓝牙地址（留空自动查找）", address), ("麦克风增益 / dB", gain)):
            ttk.Label(frame, text=label).pack(anchor="w", pady=(16, 6))
            ttk.Entry(frame, textvariable=var, font=(FONT, -18)).pack(fill="x")
        enabled = tk.BooleanVar(value=self.app.config.mic_forward_enabled)
        ttk.Checkbutton(frame, text="启用虚拟麦克风转发", variable=enabled).pack(anchor="w", pady=(18, 4))
        submit = tk.BooleanVar(value=self.app.config.press_enter_after_paste)
        ttk.Checkbutton(frame, text="发送固定文本后自动回车", variable=submit).pack(anchor="w", pady=4)
        native = tk.BooleanVar(value=self.app.config.doubao_native_control)
        ttk.Checkbutton(frame, text="豆包语音兼容模式（解决模拟快捷键无效）", variable=native).pack(anchor="w", pady=4)
        ttk.Label(frame, textvariable=feedback, foreground=MUTED, wraplength=575, font=(FONT, -14)).pack(anchor="w", pady=10)
        def save():
            try:
                value = float(gain.get())
                if not -30 <= value <= 30: raise ValueError("增益范围为 -30 至 30 dB")
                config = self.app.config
                self.app._release_doubao()
                self.app._stop_handsfree()
                config.device_address = address.get().strip() or None
                config.mic_gain_db, config.mic_forward_enabled = value, enabled.get()
                config.press_enter_after_paste = submit.get()
                config.doubao_native_control = native.get()
                if spec[0]:
                    config.doubao_hotkey_vk = spec[0].vk_code
                    config.doubao_hotkey_modifiers = _modifier_values(spec[0])
                if handsfree_spec[0]:
                    config.doubao_handsfree_hotkey_vk = handsfree_spec[0].vk_code
                    config.doubao_handsfree_hotkey_modifiers = _modifier_values(handsfree_spec[0])
                self.app.save()
                self.app.restart_mic()
                if config.mic_forward_enabled and not self.app.mic.running:
                    feedback.set("设置已保存；虚拟麦克风未启动，请检查 VB-CABLE。")
                else:
                    feedback.set("设置已保存，麦克风设置已应用。地址修改后请手动重连。")
                self._draw()
            except (ValueError, OSError, RuntimeError) as error:
                feedback.set("保存失败："+str(error))
        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=6)
        for text, command in (("保存设置", save), ("手动重连", lambda: (self.app.reconnect_now(), feedback.set("已请求重连。")))):
            button = SoftButton(controls, text, command, font=(FONT, -18), primary=text == "保存设置")
            button.pack(side="left", fill="x", expand=True, padx=4)
        def launch_extra():
            self.app.set_enhanced_keys(True)
        enhancement = getattr(self.app, 'enhancement', None)
        self._enhancement_var = tk.StringVar(value=enhancement.detail if enhancement else '增强按键未启用')
        ttk.Label(frame, textvariable=self._enhancement_var, foreground=MUTED, wraplength=580).pack(anchor='w', pady=(10, 3))
        ttk.Label(frame, text='开启后随米遥启动；Windows 管理员授权仍需确认。', foreground=MUTED, font=(FONT,-13)).pack(anchor='w')
        enhanced_controls = ttk.Frame(frame)
        enhanced_controls.pack(fill='x', pady=(8, 0))
        for text, command in (("启用增强按键", launch_extra), ("暂停增强按键", lambda: self.app.set_enhanced_keys(False))):
            extra=SoftButton(enhanced_controls,text,command,font=(FONT,-17))
            extra.pack(side='left',fill='x',expand=True,padx=4)

    def _on_button_event(self, button, down):
        self._events.put((button, down))

    def _on_status(self, status):
        self._statuses.put((status.state, status.detail))

    def _poll_events(self):
        if not self._alive: return
        changed = False
        while True:
            try: button, down = self._events.get_nowait()
            except queue.Empty: break
            if down: self._down.add(button)
            else: self._down.discard(button)
            changed = True
        while True:
            try: state, detail = self._statuses.get_nowait()
            except queue.Empty: break
            if state in ("disconnected", "negotiating", "ready", "opening", "streaming"):
                self._connection_state = state
            if state == 'enhancement' and self._enhancement_var is not None:
                self._enhancement_var.set(detail)
            changed = True
        if self._instance_guard and self._instance_guard.consume_show():
            self._show_requested.set()
        if self._show_requested.is_set():
            self._show_requested.clear()
            self.root.deiconify()
            self.root.lift()
            if self._setup_wizard:
                self._setup_wizard.show()
        if self._quit_requested.is_set():
            self._do_quit()
            return
        if changed: self._draw()
        self._after_id = self.root.after(50, self._poll_events)

    def attach_tray(self, tray): self._tray = tray
    def hide_to_tray(self): self.root.withdraw()
    def request_show(self): self._show_requested.set()
    def request_quit(self): self._quit_requested.set()

    def shutdown(self):
        if self._shutting: return
        self._shutting, self._alive = True, False
        if self._setup_wizard:
            self._setup_wizard.close()
        for after_id in (self._after_id, self._layout_id):
            if after_id:
                try: self.root.after_cancel(after_id)
                except tk.TclError: pass
        if self._on_button_event in self.app.button_listeners: self.app.button_listeners.remove(self._on_button_event)
        if self._on_status in self.app.status.listeners: self.app.status.listeners.remove(self._on_status)
        try: self.app.stop()
        finally:
            if self._tray: self._tray.stop()

    def _do_quit(self):
        self.shutdown()
        self.root.destroy()


class _PanelTray(TrayApp):
    def __init__(self, app, on_open_settings, on_quit):
        super().__init__(app, on_open_settings)
        self._on_quit = on_quit

    def _quit(self, _item): self._on_quit()


def _ensure_valid_preset(app):
    try: app.catalog.preset(app.active_preset_id)
    except ConfigurationError: app.active_preset_id = app.config.active_preset_id = "standard"


def run_panel():
    from .single_instance import SingleInstance
    guard = SingleInstance()
    if not guard.primary:
        guard.close()
        return
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        app_data_dir().mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(app_data_dir()/"runtime.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(handler)
    except OSError:
        pass
    app = MiAoApp()
    _ensure_valid_preset(app)
    root = tk.Tk()
    root.tk.call("tk", "scaling", 1.0)
    root.title(WINDOW_TITLE)
    scale = min(1, (root.winfo_screenwidth()-100)/1487, (root.winfo_screenheight()-110)/1058)
    root.geometry(f"{round(1487*scale)}x{round(1058*scale)}")
    root.minsize(1000, 720)
    root.configure(bg=BG)
    try:
        panel = RemotePanel(root, app)
        panel._instance_guard = guard
        app.start()
        tray = _PanelTray(app, panel.request_show, panel.request_quit)
        threading.Thread(target=tray.run, name="miao-tray", daemon=True).start()
        panel.attach_tray(tray)
        root.protocol("WM_DELETE_WINDOW", panel.hide_to_tray)
        if not app.config.setup_completed:
            root.after(400, panel.open_setup)
        root.mainloop()
    except Exception as error:
        log.exception("面板启动失败")
        messagebox.showerror(WINDOW_TITLE, str(error), parent=root)
    finally:
        app.stop()
        guard.close()
