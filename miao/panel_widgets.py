"""Native dark UI controls and shortcut recording for the remote panel."""
from __future__ import annotations

import tkinter as tk
import math
import ctypes
from tkinter import ttk

from . import ConfigurationError
from .buttons import KeyboardShortcutSpec, ShortcutModifier

BG = "#141a22"
CARD = "#191f28"
FIELD = "#1e2631"
BORDER = "#354252"
TEXT = "#f5f7fb"
MUTED = "#aeb9c9"
BLUE = "#087bff"
ERROR = "#ff969b"
GREEN = "#56df75"
FONT = "Microsoft YaHei UI"


def rounded(canvas, box, radius=12, **options):
    x1, y1, x2, y2 = box
    r = min(radius, (x2-x1)/2, (y2-y1)/2)
    points = []
    for cx, cy, start in ((x2-r,y1+r,-90), (x2-r,y2-r,0),
                           (x1+r,y2-r,90), (x1+r,y1+r,180)):
        for step in range(9):
            angle = math.radians(start+step*90/8)
            points.extend((cx+r*math.cos(angle), cy+r*math.sin(angle)))
    return canvas.create_polygon(points, smooth=True, splinesteps=4, **options)


class SoftButton(tk.Canvas):
    def __init__(self, parent, text, command, *, primary=False, font=None, background=CARD, flat=False):
        super().__init__(parent, bg=background, width=200, height=48,
                         highlightthickness=0, takefocus=1, cursor="hand2")
        self.text, self.command, self.primary = text, command, primary
        self.font = font or (FONT, -18)
        self.flat = flat
        self.enabled, self.hover = True, False
        self.bind("<Configure>", self.draw)
        self.bind("<Enter>", lambda _: self._hover(True))
        self.bind("<Leave>", lambda _: self._hover(False))
        self.bind("<Button-1>", self._invoke)
        self.bind("<Return>", self._invoke)
        self.bind("<space>", self._invoke)
        self.bind("<FocusIn>", self.draw)
        self.bind("<FocusOut>", self.draw)

    def _hover(self, value):
        self.hover = value
        self.draw()

    def _invoke(self, _event=None):
        if self.enabled:
            self.command()
        return "break"

    def set(self, *, text=None, enabled=None, primary=None):
        if text is not None: self.text = text
        if enabled is not None: self.enabled = enabled
        if primary is not None: self.primary = primary
        self.configure(cursor="hand2" if self.enabled else "arrow")
        self.draw()

    def draw(self, _event=None):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        fill = BLUE if self.primary else ("#283241" if self.hover else (CARD if self.flat else "#2c3645"))
        if not self.enabled: fill = "#222a35"
        outline = "#6bb5ff" if self.focus_get() is self else ("#3292ff" if self.primary else BORDER)
        rounded(self, (1,1,w-1,h-1), min(12,h/5), fill=fill, outline=outline)
        self.create_text(w/2,h/2, text=self.text, fill=TEXT if self.enabled else "#718094", font=self.font)


class RoundedEntry(tk.Canvas):
    """A real editable entry inside the reference's rounded field boundary."""
    def __init__(self, parent, *, textvariable=None, font=None):
        super().__init__(parent, bg=CARD, highlightthickness=0)
        self.entry = tk.Entry(self, textvariable=textvariable, font=font,
            bg=FIELD, fg=TEXT, insertbackground=TEXT, relief="flat", bd=0,
            disabledbackground=FIELD, disabledforeground=MUTED, selectbackground=BLUE)
        self.entry.place(x=24,y=8,relwidth=1,width=-48,relheight=1,height=-16)
        self.bind("<Configure>", self.draw)
        self.entry.bind("<FocusIn>", self.draw)
        self.entry.bind("<FocusOut>", self.draw)

    def draw(self, _event=None):
        super().delete("all")
        rounded(self,(1,1,self.winfo_width()-1,self.winfo_height()-1),12,
                fill=FIELD,outline=BLUE if self.focus_get() is self.entry else BORDER)
        self.entry.lift()

    def bind(self, sequence=None, func=None, add=None):
        if sequence == "<Configure>": return super().bind(sequence,func,add)
        return self.entry.bind(sequence,func,add)

    def configure(self, cnf=None, **kw):
        if "state" in kw: self.entry.configure(state=kw.pop("state"))
        if cnf or kw: return super().configure(cnf,**kw)

    config = configure
    def focus_set(self): return self.entry.focus_set()
    def delete(self,*args): return self.entry.delete(*args)
    def insert(self,*args): return self.entry.insert(*args)
    def selection_range(self,*args): return self.entry.selection_range(*args)
    def selection_clear(self): return self.entry.selection_clear()
    def get(self): return self.entry.get()


class RoundedSelect(tk.Canvas):
    """Keyboard-accessible native menu with a rounded, full-width selector."""
    def __init__(self,parent,*,textvariable,values,font):
        super().__init__(parent,bg=CARD,highlightthickness=0,takefocus=1,cursor="hand2")
        self.variable,self.values,self.font = textvariable,list(values),font
        self.enabled=True
        self._selected_callback=None
        self.variable.trace_add("write",lambda *_:self.draw())
        super().bind("<Configure>",self.draw)
        super().bind("<Button-1>",self._open)
        super().bind("<Return>",self._open)
        super().bind("<space>",self._open)
        super().bind("<Down>",self._open)
        super().bind("<FocusIn>",self.draw)
        super().bind("<FocusOut>",self.draw)

    def configure(self,cnf=None,**kw):
        if "state" in kw:
            self.enabled=kw.pop("state")!="disabled"
            super().configure(cursor="hand2" if self.enabled else "arrow")
            self.draw()
        if cnf or kw: return super().configure(cnf,**kw)
    config=configure

    def bind(self,sequence=None,func=None,add=None):
        if sequence=="<<ComboboxSelected>>":
            self._selected_callback=func
            return
        return super().bind(sequence,func,add)

    def draw(self,_event=None):
        self.delete("all")
        w,h=self.winfo_width(),self.winfo_height()
        rounded(self,(1,1,w-1,h-1),12,fill=FIELD,
                outline=BLUE if self.focus_get() is self else BORDER)
        self.create_text(24,h/2,anchor="w",text=self.variable.get(),font=self.font,
                         fill=TEXT if self.enabled else MUTED)
        self.create_text(w-27,h/2,text="\ue70d",font=("Segoe MDL2 Assets",-16),fill=MUTED)

    def _open(self,_event=None):
        if not self.enabled: return "break"
        self.focus_set()
        menu=tk.Menu(self,tearoff=False,bg=FIELD,fg=TEXT,activebackground=BLUE,
                     activeforeground=TEXT,font=self.font,bd=1,relief="flat")
        def select(value):
            self.variable.set(value)
            if self._selected_callback: self._selected_callback(None)
        for value in self.values:
            menu.add_command(label=value,command=lambda v=value:select(v))
        try: menu.tk_popup(self.winfo_rootx(),self.winfo_rooty()+self.winfo_height())
        finally: menu.grab_release()
        return "break"


def configure_theme(root):
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TFrame", background=CARD)
    style.configure("TLabel", background=CARD, foreground=TEXT, font=(FONT, -17))
    style.configure("TCheckbutton", background=CARD, foreground=TEXT, font=(FONT, -16))
    style.map("TCheckbutton", background=[("active",CARD)])
    style.configure("TEntry", fieldbackground=FIELD, foreground=TEXT, insertcolor=TEXT,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, padding=10)
    style.map("TEntry", bordercolor=[("focus",BLUE)])
    style.configure("TCombobox", fieldbackground=FIELD, background=FIELD, foreground=TEXT,
                    arrowcolor=TEXT, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, padding=10)
    style.map("TCombobox", fieldbackground=[("readonly",FIELD)], foreground=[("readonly",TEXT), ("disabled",MUTED)],
              selectbackground=[("readonly",FIELD)], selectforeground=[("readonly",TEXT)])
    root.option_add("*TCombobox*Listbox.background", FIELD)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", BLUE)
    root.option_add("*TCombobox*Listbox.font", (FONT, -17))


PLACEHOLDER = "点击录制，再按组合键"
_MODIFIER_KEYSYMS = {"Shift_L","Shift_R","Control_L","Control_R","Alt_L","Alt_R",
                     "Win_L","Win_R","Super_L","Super_R","Caps_Lock","Num_Lock"}


def _modifiers_from_state(state):
    modifiers = [modifier for bit, modifier in ((1,ShortcutModifier.SHIFT), (4,ShortcutModifier.CTRL),
            (0x20000,ShortcutModifier.ALT)) if state & bit]
    if ctypes.windll.user32.GetKeyState(0xA5) & 0x8000:
        modifiers = [m for m in modifiers if m is not ShortcutModifier.ALT]
        modifiers.append(ShortcutModifier.RIGHT_ALT)
    return modifiers


class ShortcutRecorder:
    def __init__(self, entry, on_recorded, on_error):
        self.entry, self.on_recorded, self.on_error = entry, on_recorded, on_error
        self.armed = False
        entry.bind("<KeyPress>", self._on_key_press)
        entry.bind("<Button-1>", lambda _: self.arm())

    def arm(self):
        self.armed = True
        self.entry.focus_set()
        self.entry.delete(0,"end")
        self.entry.insert(0,"请按下组合键…")
        self.entry.selection_range(0,"end")

    def show(self, spec):
        self.entry.delete(0,"end")
        self.entry.insert(0, spec.display_name.replace("+"," + ") if spec else PLACEHOLDER)

    def _on_key_press(self, event):
        if not self.armed or event.keysym in _MODIFIER_KEYSYMS:
            return "break"
        if event.keysym == "Escape":
            self.armed = False
            self.on_error("已取消录制；点击录制可重新开始。")
            return "break"
        try:
            spec = KeyboardShortcutSpec(event.keycode, _modifiers_from_state(event.state), event.keysym[:12])
        except ConfigurationError as error:
            self.on_error(str(error))
            return "break"
        self.armed = False
        self.show(spec)
        self.on_recorded(spec)
        return "break"
