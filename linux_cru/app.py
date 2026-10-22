"""The graphical interface.

One screen, in the order things happen: which display, which mode,
what will be done, and the buttons that do it. Everything the tool
would run is shown before it runs, and everything it did is kept in the
log pane next to the preview.

The widgets are plain ttk with the styles from linux_cru.theme, so the
window follows the desktop's light or dark palette without any extra
work on the user's part.
"""

import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

from . import (detect, edid, hostenv, kwin, override, persist, privileged,
               settings, stretch, theme, timings, wayland, xorg)

TEST_REVERT_SECONDS = 15

# The refresh rates the display settings of Plasma offer, as percent.
# Integer ones and the quarters are what Plasma itself offers.
KDE_SCALES = ("100%", "125%", "150%", "175%", "200%")

# Rotations the display settings of Plasma speak.
KDE_ROTATIONS = ("none", "90", "180", "270", "flipped", "flipped90",
                 "flipped180", "flipped270")

# Frame buffer depths Plasma offers, in bits per colour.
KDE_BPCS = (8, 10, 12, 14, 16)

# The resolutions people reach for most often. The chips are a shortcut,
# not a limit: the fields below take anything.
PRESETS = (
    ("1280x720", 1280, 720),
    ("1600x900", 1600, 900),
    ("1920x1080", 1920, 1080),
    ("2560x1080", 2560, 1080),
    ("2560x1440", 2560, 1440),
    ("3440x1440", 3440, 1440),
    ("3840x2160", 3840, 2160),
)

TIMING_OPTIONS = (
    ("CVT-RBv2", "cvt-rb2", "recommended for modern displays"),
    ("CVT-RB", "cvt-rb", "reduced blanking v1"),
    ("CVT", "cvt", "full blanking, for CRTs"),
    ("GTF", "gtf", "for CRT monitors"),
    ("Manual", "manual", "paste an exact modeline"),
)


class PasswordPrompt:
    """Modal password dialog, used when no polkit agent is available."""

    def __init__(self, parent, message, retry=False):
        self.password = None
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Administrator access required")
        self.dialog.transient(parent)
        self.dialog.resizable(False, False)
        self.dialog.grab_set()

        body = ttk.Frame(self.dialog, padding=14)
        body.pack(fill=tk.BOTH, expand=True)

        ttk.Label(body, text=message, wraplength=380,
                  justify="left").pack(anchor="w")
        if retry:
            ttk.Label(body, text="Incorrect password, try again.",
                      foreground="#b00000").pack(anchor="w", pady=(6, 0))

        ttk.Label(body, text="Password:").pack(anchor="w", pady=(10, 2))
        self.entry = ttk.Entry(body, show="•", width=32)
        self.entry.pack(fill=tk.X)

        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X, pady=(14, 0))
        ttk.Button(buttons, text="Cancel",
                   command=self.cancel).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="OK",
                   command=self.accept).pack(side=tk.RIGHT, padx=(0, 6))

        self.dialog.bind("<Return>", lambda e: self.accept())
        self.dialog.bind("<Escape>", lambda e: self.cancel())
        self.dialog.protocol("WM_DELETE_WINDOW", self.cancel)

        parent.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() // 2) - 180
        y = parent.winfo_rooty() + (parent.winfo_height() // 3)
        self.dialog.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.entry.focus_set()

    def accept(self):
        self.password = self.entry.get()
        self.dialog.destroy()

    def cancel(self):
        self.password = None
        self.dialog.destroy()

    def run(self):
        self.dialog.wait_window()
        return self.password


class ScrollFrame(ttk.Frame):
    """A frame that scrolls when it does not fit."""

    def __init__(self, parent, palette):
        super().__init__(parent, style="TFrame")
        self.palette = palette
        self.canvas = tk.Canvas(self, bg=palette.bg, highlightthickness=0,
                                 bd=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical",
                                       command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.body = ttk.Frame(self.canvas, style="TFrame")
        self._window = self.canvas.create_window((0, 0), window=self.body,
                                                 anchor="nw")
        self.body.bind("<Configure>", self._on_body)
        self.canvas.bind("<Configure>", self._on_canvas)
        for widget in (self.canvas, self.body):
            widget.bind("<Enter>", self._bind_wheel)
            widget.bind("<Leave>", self._unbind_wheel)

    def _on_body(self, _event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event):
        self.canvas.itemconfigure(self._window, width=event.width)

    def _bind_wheel(self, _event):
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self.canvas.bind_all("<Button-4>", self._on_wheel)
        self.canvas.bind_all("<Button-5>", self._on_wheel)

    def _unbind_wheel(self, _event):
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _on_wheel(self, event):
        # The mode list and the text panes scroll themselves; they are
        # inside this frame, so the wheel would move both.
        widget = getattr(event, "widget", None)
        if isinstance(widget, (ttk.Treeview, tk.Text)):
            return
        if getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        else:
            delta = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(delta, "units")

    def repaint(self):
        """Follow the palette after a theme change."""
        self.canvas.configure(bg=self.palette.bg)


class LinuxCRU:
    def __init__(self, root):
        self.root = root
        self.root.title("Linux Custom Resolution Utility")
        self.settings = settings.load()
        self.palette = theme.apply(root, self.settings.get("theme", "system"))
        self.env = detect.detect()

        self._cards = []          # tk.Frames that carry the border colour
        self._texts = []          # tk.Text widgets
        self._workdir_path = None
        self._modes = []          # the modes of the selected output
        self._modes_output = None
        self._mode_entries = []   # the same modes, with what is known about each
        self._kde_settings = None
        self._closing = False     # the interface is going away
        self._answers = queue.Queue()   # answers from the worker threads
        self._pump_id = None      # the timer that drains the answers

        self.root.geometry(self.settings.get("geometry") or "1240x820")
        self.root.minsize(980, 640)
        self.root.configure(bg=self.palette.bg)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.container = ttk.Frame(root, style="TFrame", padding=0)
        self.container.pack(fill=tk.BOTH, expand=True)
        self.container.grid_columnconfigure(0, weight=1)
        self.container.grid_rowconfigure(1, weight=1)

        self.build_header()
        self.build_body()
        self.build_footer()

        self.load_settings_into_form()
        self.refresh_outputs()
        self.generate_preview()
        self._schedule_pump()

    # -- layout -----------------------------------------------------------------

    def build_header(self):
        header = tk.Frame(self.container, bg=self.palette.surface,
                          highlightbackground=self.palette.border,
                          highlightthickness=1, bd=0)
        header.grid(row=0, column=0, sticky="ew")
        self._cards.append((header, "surface"))

        inner = ttk.Frame(header, style="Card.TFrame", padding=(18, 14))
        inner.pack(fill=tk.X)
        inner.grid_columnconfigure(1, weight=1)

        title_box = ttk.Frame(inner, style="Card.TFrame")
        title_box.grid(row=0, column=0, sticky="w")
        ttk.Label(title_box, text="Linux CRU", style="Title.TLabel").pack(
            anchor="w")
        ttk.Label(title_box, text="Custom display resolutions and refresh rates",
                  style="Muted.TLabel").pack(anchor="w")

        badges = ttk.Frame(inner, style="Card.TFrame")
        badges.grid(row=0, column=1, sticky="e")

        session = self.env.session_type.upper() \
            if self.env.session_type != "unknown" else "unknown"
        comp = self.env.compositor
        if self.env.compositor_version:
            comp += " " + ".".join(str(x) for x in self.env.compositor_version[:2])
        gpu = ", ".join(self.env.drivers) or "unknown GPU"

        labels = [session, comp, gpu]
        if self.env.distro:
            labels.append(self.env.distro)
        labels.append(self.env.kernel_release)

        for text in labels:
            ttk.Label(badges, text=text, style="Badge.TLabel",
                      padding=(9, 4)).pack(side=tk.LEFT, padx=(6, 0))

        self.theme_button = ttk.Button(
            badges, text="Light" if self.palette.name == "dark" else "Dark",
            style="Ghost.TButton", command=self.toggle_theme)
        self.theme_button.pack(side=tk.LEFT, padx=(12, 0))

    def build_body(self):
        panes = ttk.Panedwindow(self.container, orient=tk.HORIZONTAL)
        panes.grid(row=1, column=0, sticky="nsew", padx=14, pady=14)
        self.panes = panes

        left = ScrollFrame(panes, self.palette)
        right = ttk.Frame(panes, style="TFrame")
        panes.add(left, weight=2)
        panes.add(right, weight=3)
        self.scroll_frame = left

        self.build_display_card(left.body)
        self.build_display_controls_card(left.body)
        self.build_mode_card(left.body)
        self.build_timing_card(left.body)
        self.build_options_card(left.body)

        self.build_preview_pane(right)

        # The form on the left needs a fixed, sensible width; the
        # preview takes whatever is left.
        self.root.update_idletasks()
        try:
            self.panes.sashpos(0, 430)
        except tk.TclError:
            pass

    def build_footer(self):
        footer = tk.Frame(self.container, bg=self.palette.surface,
                          highlightbackground=self.palette.border,
                          highlightthickness=1, bd=0)
        footer.grid(row=2, column=0, sticky="ew")
        self._cards.append((footer, "surface"))

        inner = ttk.Frame(footer, style="Card.TFrame", padding=(18, 12))
        inner.pack(fill=tk.X)
        inner.grid_columnconfigure(1, weight=1)

        buttons = ttk.Frame(inner, style="Card.TFrame")
        buttons.grid(row=0, column=0, sticky="w")

        self.test_btn = ttk.Button(
            buttons, text=f"Test mode ({TEST_REVERT_SECONDS}s)",
            style="Accent.TButton", command=self.test_mode)
        self.test_btn.pack(side=tk.LEFT)

        self.apply_btn = ttk.Button(buttons, text="Apply",
                                    command=self.apply_configuration)
        self.apply_btn.pack(side=tk.LEFT, padx=(8, 0))

        self.remove_btn = ttk.Button(buttons, text="Remove",
                                     style="Danger.TButton",
                                     command=self.remove_configuration)
        self.remove_btn.pack(side=tk.LEFT, padx=(8, 0))

        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(inner, textvariable=self.status_var,
                  style="Muted.TLabel").grid(row=0, column=1, sticky="e")

    # -- cards -------------------------------------------------------------------

    def card(self, parent, title, subtitle=None):
        """A titled panel. Returns the frame to put things in."""
        outer = tk.Frame(parent, bg=self.palette.surface,
                         highlightbackground=self.palette.border,
                         highlightthickness=1, bd=0)
        outer.pack(fill=tk.X, pady=(0, 12))
        self._cards.append((outer, "surface"))

        inner = ttk.Frame(outer, style="Card.TFrame", padding=(16, 14))
        inner.pack(fill=tk.BOTH, expand=True)

        head = ttk.Frame(inner, style="Card.TFrame")
        head.pack(fill=tk.X)
        ttk.Label(head, text=title, style="SectionTitle.TLabel").pack(side=tk.LEFT)
        if subtitle:
            ttk.Label(head, text=subtitle, style="Muted.TLabel").pack(side=tk.RIGHT)

        body = ttk.Frame(inner, style="Card.TFrame")
        body.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        return body

    # -- display ------------------------------------------------------------------

    def build_display_card(self, parent):
        body = self.card(parent, "Display", "the output you want to change")

        row = ttk.Frame(body, style="Card.TFrame")
        row.pack(fill=tk.X)
        row.grid_columnconfigure(0, weight=1)

        self.display_var = tk.StringVar()
        self.display_combo = ttk.Combobox(row, textvariable=self.display_var,
                                          state="readonly")
        self.display_combo.grid(row=0, column=0, sticky="ew")
        self.display_combo.bind("<<ComboboxSelected>>",
                                lambda e: self.on_display_changed())

        ttk.Button(row, text="Rescan", style="Ghost.TButton",
                   command=self.refresh_outputs).grid(row=0, column=1, padx=(8, 0))

        self.monitor_var = tk.StringVar()
        ttk.Label(body, textvariable=self.monitor_var, style="Muted.TLabel",
                  wraplength=340, justify="left").pack(fill=tk.X, pady=(10, 0))

        self.desktop_var = tk.StringVar()
        ttk.Label(body, textvariable=self.desktop_var, style="Muted.TLabel",
                  wraplength=340, justify="left").pack(fill=tk.X)

        modes_head = ttk.Frame(body, style="Card.TFrame")
        modes_head.pack(fill=tk.X, pady=(14, 0))
        ttk.Label(modes_head, text="Modes this display offers",
                  style="Card.TLabel").pack(side=tk.LEFT)
        ttk.Label(modes_head, text="click one to use it",
                  style="Muted.TLabel").pack(side=tk.RIGHT)

        list_frame = ttk.Frame(body, style="Card.TFrame")
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(6, 0))

        self.mode_tree = ttk.Treeview(list_frame, columns=("mode", "refresh"),
                                      show="headings", height=7,
                                      selectmode="browse")
        self.mode_tree.heading("mode", text="Resolution", anchor="w")
        self.mode_tree.heading("refresh", text="Refresh", anchor="w")
        self.mode_tree.column("mode", width=175, anchor="w", stretch=False)
        self.mode_tree.column("refresh", width=90, anchor="w", stretch=False)
        self.mode_tree.bind("<<TreeviewSelect>>", self.on_mode_selected)

        scroll = ttk.Scrollbar(list_frame, orient="vertical",
                                command=self.mode_tree.yview)
        self.mode_tree.configure(yscrollcommand=scroll.set)
        self.mode_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill="y")

    def build_display_controls_card(self, parent):
        """What Plasma 6.8 and newer can change about a display.

        These are the settings that decide how a mode looks once it is
        up: the colour depth, the HDR state, the scale and the
        rotation. They are changed with the output that is selected
        above, and they are read back and shown here.
        """
        if self.env.compositor != "kwin" or not kwin.available():
            return

        body = self.card(parent, "Display settings", "Plasma 6.8 and newer")

        self.hdr_var = tk.BooleanVar(value=False)
        self.wcg_var = tk.BooleanVar(value=False)
        self.hdr_check = ttk.Checkbutton(
            body, text="High dynamic range (HDR)", variable=self.hdr_var,
            command=self.apply_hdr)
        self.hdr_check.pack(anchor="w")
        self.wcg_check = ttk.Checkbutton(
            body, text="Wide colour gamut", variable=self.wcg_var,
            command=self.apply_wcg)
        self.wcg_check.pack(anchor="w", pady=(4, 0))

        row = ttk.Frame(body, style="Card.TFrame")
        row.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(row, text="Colour depth",
                  style="Card.TLabel").pack(side=tk.LEFT)
        self.bpc_var = tk.StringVar()
        self.bpc_combo = ttk.Combobox(
            row, textvariable=self.bpc_var, width=8, state="readonly",
            values=[f"{bpc} bpc" for bpc in KDE_BPCS])
        self.bpc_combo.pack(side=tk.LEFT, padx=(8, 16))
        self.bpc_combo.bind("<<ComboboxSelected>>", self.apply_bpc)

        row = ttk.Frame(body, style="Card.TFrame")
        row.pack(fill=tk.X)
        ttk.Label(row, text="Scale", style="Card.TLabel").pack(side=tk.LEFT)
        self.scale_var = tk.StringVar()
        self.scale_combo = ttk.Combobox(
            row, textvariable=self.scale_var, width=6, state="readonly",
            values=list(KDE_SCALES))
        self.scale_combo.pack(side=tk.LEFT, padx=(8, 16))
        self.scale_combo.bind("<<ComboboxSelected>>", self.apply_scale)

        ttk.Label(row, text="Rotation", style="Card.TLabel").pack(side=tk.LEFT)
        self.rotation_var = tk.StringVar()
        self.rotation_combo = ttk.Combobox(
            row, textvariable=self.rotation_var, width=10, state="readonly",
            values=list(KDE_ROTATIONS))
        self.rotation_combo.pack(side=tk.LEFT, padx=(8, 0))
        self.rotation_combo.bind("<<ComboboxSelected>>", self.apply_rotation)

        self.controls_state_var = tk.StringVar()
        ttk.Label(body, textvariable=self.controls_state_var,
                  style="Muted.TLabel", wraplength=340,
                  justify="left").pack(fill=tk.X, pady=(8, 0))

    def build_mode_card(self, parent):
        body = self.card(parent, "Custom mode")

        presets = ttk.Frame(body, style="Card.TFrame")
        presets.pack(fill=tk.X)
        for index, (label, width, height) in enumerate(PRESETS):
            button = ttk.Button(presets, text=label, style="Chip.TButton",
                                command=lambda w=width, h=height:
                                self.set_resolution(w, h))
            button.grid(row=index // 3, column=index % 3, sticky="w",
                        padx=(0, 6), pady=(0, 6))
        for column in range(3):
            presets.grid_columnconfigure(column, weight=1)

        grid = ttk.Frame(body, style="Card.TFrame")
        grid.pack(fill=tk.X, pady=(12, 0))
        grid.grid_columnconfigure(1, weight=1)
        grid.grid_columnconfigure(3, weight=1)

        self.width_var = tk.StringVar(value="1920")
        self.height_var = tk.StringVar(value="1080")
        self.refresh_var = tk.StringVar(value="75")

        ttk.Label(grid, text="Width", style="Card.TLabel").grid(
            row=0, column=0, sticky="w")
        ttk.Entry(grid, textvariable=self.width_var, width=8).grid(
            row=0, column=1, sticky="w", padx=(8, 14))
        ttk.Label(grid, text="Height", style="Card.TLabel").grid(
            row=0, column=2, sticky="w")
        ttk.Entry(grid, textvariable=self.height_var, width=8).grid(
            row=0, column=3, sticky="w", padx=(8, 0))

        ttk.Label(grid, text="Refresh", style="Card.TLabel").grid(
            row=1, column=0, sticky="w", pady=(8, 0))
        self.refresh_combo = ttk.Combobox(grid, textvariable=self.refresh_var,
                                          width=9)
        self.refresh_combo.grid(row=1, column=1, sticky="w", padx=(8, 14),
                                pady=(8, 0))
        ttk.Label(grid, text="Hz", style="Muted.TLabel").grid(
            row=1, column=2, sticky="w", pady=(8, 0))

        self.info_var = tk.StringVar()
        ttk.Label(body, textvariable=self.info_var, style="Muted.TLabel",
                  justify="left", wraplength=340).pack(fill=tk.X, pady=(12, 0))

    def build_timing_card(self, parent):
        body = self.card(parent, "Timing")

        self.standard_var = tk.StringVar(value="cvt-rb2")
        for i, (label, value, note) in enumerate(TIMING_OPTIONS):
            row = ttk.Frame(body, style="Card.TFrame")
            row.pack(fill=tk.X, pady=(0, 2))
            row.grid_columnconfigure(1, weight=1)
            ttk.Radiobutton(row, text=label, value=value,
                            variable=self.standard_var,
                            command=self.on_standard_changed).grid(row=0, column=0,
                                                                  sticky="w")
            ttk.Label(row, text=note, style="Muted.TLabel").grid(
                row=0, column=1, sticky="e")

        self.manual_frame = ttk.Frame(body, style="Card.TFrame")
        ttk.Label(self.manual_frame, text="Modeline", style="Card.TLabel").pack(
            anchor="w")
        self.modeline_var = tk.StringVar()
        ttk.Entry(self.manual_frame, textvariable=self.modeline_var).pack(
            fill=tk.X, pady=(4, 0))
        ttk.Label(self.manual_frame,
                  text="clock, then the eight timing values, then the sync flags",
                  style="Muted.TLabel").pack(anchor="w")

        self.modeline_var.trace_add("write", lambda *a: self.generate_preview())
        self.width_var.trace_add("write", lambda *a: self.generate_preview())
        self.height_var.trace_add("write", lambda *a: self.generate_preview())
        self.refresh_var.trace_add("write", lambda *a: self.generate_preview())

    def build_options_card(self, parent):
        body = self.card(parent, "Options")

        nvidia_x11 = self.env.has_nvidia_proprietary and self.env.session_type == "x11"
        self.relax_validation = tk.BooleanVar(value=nvidia_x11)
        self.nvidia_customedid = tk.BooleanVar(value=nvidia_x11)
        if nvidia_x11:
            ttk.Checkbutton(
                body,
                text="Skip the NVIDIA EDID and pixel clock checks",
                variable=self.relax_validation,
                command=self.generate_preview).pack(fill=tk.X)
            ttk.Checkbutton(
                body,
                text="Inject a patched EDID (CustomEDID)",
                variable=self.nvidia_customedid,
                command=self.generate_preview).pack(fill=tk.X, pady=(4, 0))

        self.boot_method_var = tk.BooleanVar(value=False)
        self.boot_method_check = ttk.Checkbutton(
            body,
            text="Apply from the kernel command line",
            variable=self.boot_method_var,
            command=self.generate_preview)
        self.boot_method_check.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(body,
                  text="Only needed for the boot console and the login screen. "
                       "It changes your boot configuration.",
                  style="Muted.TLabel", wraplength=340,
                  justify="left").pack(fill=tk.X, pady=(4, 0))

        if wayland.vrr_supported(self.env):
            ttk.Label(body, text="Adaptive sync (VRR)",
                      style="SectionTitle.TLabel").pack(anchor="w",
                                                        pady=(14, 4))
            vrr_row = ttk.Frame(body, style="Card.TFrame")
            vrr_row.pack(fill=tk.X)
            self.vrr_var = tk.StringVar(value="automatic")
            self.vrr_combo = ttk.Combobox(
                vrr_row, textvariable=self.vrr_var, width=12,
                values=list(wayland.VRR_POLICIES), state="readonly")
            self.vrr_combo.pack(side=tk.LEFT)
            ttk.Button(vrr_row, text="Apply", style="Ghost.TButton",
                       command=self.apply_vrr).pack(side=tk.LEFT, padx=(8, 0))
            self.vrr_status_var = tk.StringVar()
            ttk.Label(body, textvariable=self.vrr_status_var,
                      style="Muted.TLabel", wraplength=340,
                      justify="left").pack(fill=tk.X, pady=(4, 0))

        self.stretch_mode_var = tk.StringVar(value="off")
        if self.env.compositor == "kwin":
            ttk.Label(body, text="Stretch to fill the screen",
                      style="SectionTitle.TLabel").pack(anchor="w",
                                                        pady=(14, 4))
            for label, value in (("Don't force scaling", "off"),
                                 ("Stretch to fill", "full"),
                                 ("Fill keeping aspect", "full_aspect")):
                ttk.Radiobutton(body, text=label, value=value,
                                variable=self.stretch_mode_var).pack(anchor="w")
            row = ttk.Frame(body, style="Card.TFrame")
            row.pack(fill=tk.X, pady=(8, 0))
            ttk.Button(row, text="Apply", style="Ghost.TButton",
                       command=self.apply_stretch).pack(side=tk.LEFT)
            self.stretch_revert_btn = ttk.Button(row, text="Revert",
                                                 style="Ghost.TButton",
                                                 command=self.revert_stretch)
            self.stretch_status_var = tk.StringVar()
            ttk.Label(body, textvariable=self.stretch_status_var,
                      style="Muted.TLabel", wraplength=340,
                      justify="left").pack(fill=tk.X, pady=(6, 0))
            self.refresh_stretch_status()

    def build_preview_pane(self, parent):
        notebook = ttk.Notebook(parent)
        notebook.pack(fill=tk.BOTH, expand=True)

        preview_tab = ttk.Frame(notebook, style="Card.TFrame")
        log_tab = ttk.Frame(notebook, style="Card.TFrame")
        notebook.add(preview_tab, text="Preview")
        notebook.add(log_tab, text="Log")

        summary = tk.Frame(preview_tab, bg=self.palette.surface_alt,
                           highlightbackground=self.palette.border,
                           highlightthickness=1, bd=0)
        summary.pack(fill=tk.X, padx=14, pady=(14, 0))
        self._cards.append((summary, "alt"))

        summary_inner = ttk.Frame(summary, style="Sub.TFrame", padding=(14, 12))
        summary_inner.pack(fill=tk.X)
        self.method_var = tk.StringVar()
        self.effect_var = tk.StringVar()
        ttk.Label(summary_inner, textvariable=self.method_var,
                  style="Accent.TLabel").pack(anchor="w")
        ttk.Label(summary_inner, textvariable=self.effect_var,
                  style="Sub.TLabel", wraplength=620,
                  justify="left").pack(anchor="w", pady=(4, 0))

        code_frame = tk.Frame(preview_tab, bg=self.palette.surface_alt,
                              highlightbackground=self.palette.border,
                              highlightthickness=1, bd=0)
        code_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=12)
        self._cards.append((code_frame, "alt"))

        self.preview_text = tk.Text(code_frame, wrap=tk.NONE, bd=0,
                                    highlightthickness=0, padx=14, pady=12,
                                    font=theme.mono_font(self.root, 10),
                                    bg=self.palette.surface_alt,
                                    fg=self.palette.text,
                                    insertbackground=self.palette.text,
                                    selectbackground=self.palette.selection,
                                    selectforeground=self.palette.text)
        self._texts.append(self.preview_text)
        y = ttk.Scrollbar(code_frame, orient="vertical",
                           command=self.preview_text.yview)
        x = ttk.Scrollbar(code_frame, orient="horizontal",
                           command=self.preview_text.xview)
        self.preview_text.configure(yscrollcommand=y.set, xscrollcommand=x.set)
        self.preview_text.grid(row=0, column=0, sticky="nsew")
        y.grid(row=0, column=1, sticky="ns")
        x.grid(row=1, column=0, sticky="ew")
        code_frame.grid_rowconfigure(0, weight=1)
        code_frame.grid_columnconfigure(0, weight=1)

        log_frame = tk.Frame(log_tab, bg=self.palette.surface_alt,
                             highlightbackground=self.palette.border,
                             highlightthickness=1, bd=0)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)
        self._cards.append((log_frame, "alt"))
        self.log_text = tk.Text(log_frame, wrap=tk.WORD, bd=0,
                                highlightthickness=0, padx=14, pady=12,
                                font=theme.mono_font(self.root, 10),
                                bg=self.palette.surface_alt,
                                fg=self.palette.text_muted,
                                insertbackground=self.palette.text,
                                selectbackground=self.palette.selection,
                                selectforeground=self.palette.text,
                                state=tk.DISABLED)
        self._texts.append(self.log_text)
        log_frame.grid_rowconfigure(0, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)
        self.log_text.grid(row=0, column=0, sticky="nsew")

        actions = ttk.Frame(log_tab, style="Card.TFrame")
        actions.pack(fill=tk.X, padx=14)
        ttk.Button(actions, text="Copy preview", style="Ghost.TButton",
                   command=self.copy_preview).pack(side=tk.LEFT)
        ttk.Button(actions, text="Clear log", style="Ghost.TButton",
                   command=self.clear_log).pack(side=tk.LEFT, padx=(8, 0))

    # -- theme --------------------------------------------------------------------

    def toggle_theme(self):
        mode = "light" if self.palette.name == "dark" else "dark"
        self.settings["theme"] = mode
        settings.save(self.settings)
        self.palette = theme.apply(self.root, mode)
        self.repaint()

    def repaint(self):
        """Follow the palette after a theme change."""
        pal = self.palette
        self.root.configure(bg=pal.bg)
        for card, role in self._cards:
            card.configure(bg=pal.surface if role == "surface" else pal.surface_alt,
                           highlightbackground=pal.border)
        for text in self._texts:
            is_preview = text is self.preview_text
            text.configure(bg=pal.surface_alt,
                           fg=pal.text if is_preview else pal.text_muted,
                           insertbackground=pal.text,
                           selectbackground=pal.selection,
                           selectforeground=pal.text)
        if hasattr(self, "scroll_frame"):
            self.scroll_frame.repaint()
        self.theme_button.configure(
            text="Light" if pal.name == "dark" else "Dark")

    # -- outputs ------------------------------------------------------------------

    def refresh_outputs(self):
        """Fill the output list and everything that depends on it."""
        outputs = self.get_outputs()
        current = self.display_var.get()
        self.display_combo.configure(values=outputs)
        if current in outputs:
            self.display_var.set(current)
        elif outputs:
            self.display_var.set(outputs[0])
        else:
            self.display_var.set("")

        self.describe_monitor()
        self.refresh_mode_list()
        self.update_refresh_values()
        self.refresh_desktop_state()

    def get_outputs(self):
        """Output names: xrandr names on X11, DRM connector names on Wayland."""
        if self.env.session_type == "x11":
            names = []
            try:
                out = subprocess.check_output(["xrandr", "-q"],
                                              universal_newlines=True,
                                              stderr=subprocess.DEVNULL,
                                              env=hostenv.subprocess_env())
                for line in out.splitlines():
                    if " connected" in line:
                        names.append(line.split()[0])
            except (OSError, subprocess.SubprocessError):
                pass
            if names:
                return names
        connected = [c.name for c in self.env.connected_connectors()]
        others = [c.name for c in self.env.connectors if c.status != "connected"]
        return connected + others if (connected or others) else ["DP-1"]

    def on_display_changed(self):
        self._modes_output = None
        self.describe_monitor()
        self.refresh_mode_list()
        self.update_refresh_values()
        self.refresh_desktop_state()
        self.generate_preview()

    def describe_monitor(self):
        """Identity and declared limits of the selected display."""
        try:
            info = edid.Edid.from_connector(
                self.drm_connector_for(self.display_var.get())).info()
        except edid.EdidError:
            self._edid_info = None
            self.monitor_var.set("No EDID available for this output.")
            return
        self._edid_info = info
        self.monitor_var.set(info.summary())

    def refresh_mode_list(self):
        """List the modes the selected display offers.

        The modes are asked for off the interface's thread: on KDE the
        answer is a round trip to KWin, and a window that waits for one
        stops drawing.
        """
        for item in self.mode_tree.get_children():
            self.mode_tree.delete(item)
        out = self.display_var.get()
        if not out:
            return
        if self._modes_output == out and self._modes:
            self.fill_mode_list(self._modes)
            return
        self._modes_output = out
        self._modes = []
        self.run_async(lambda: detect.output_mode_entries(self.env, out),
                       self.on_mode_entries_loaded)

    def on_mode_entries_loaded(self, entries):
        """The modes came in: show them and offer their refresh rates."""
        if self._closing or self._modes_output != self.display_var.get():
            return
        self._mode_entries = entries or []
        self._modes = [(entry["width"], entry["height"],
                        entry["refresh"], entry["current"])
                       for entry in self._mode_entries]
        self.fill_mode_list(self._modes)
        self.update_refresh_values()

    def fill_mode_list(self, modes):
        """One row per mode, with what the desktop says about it."""
        self.mode_tree.delete(*self.mode_tree.get_children())
        for index, (width, height, refresh, current) in enumerate(modes):
            label = f"{width}x{height}"
            if current:
                label += "  (active)"
            elif index < len(self._mode_entries) \
                    and self._mode_entries[index].get("custom"):
                label += "  (custom)"
            rate = f"{refresh:.3f} Hz" if refresh else "-"
            self.mode_tree.insert("", "end", values=(label, rate))

    def output_modes(self):
        """The modes that were loaded, which may be none yet.

        The mode list is read off the interface's thread, so right
        after a display is chosen there is nothing to show yet.
        """
        if self._modes_output != self.display_var.get():
            return []
        return self._modes

    def update_refresh_values(self):
        """Offer the refresh rates the display already runs at."""
        rates = []
        for width, height, refresh, _current in self.output_modes():
            if refresh and refresh not in rates:
                rates.append(refresh)
        rates.sort()
        self.refresh_combo.configure(values=[f"{r:g}" for r in rates])

    def on_mode_selected(self, _event=None):
        """A mode picked from the list fills the fields."""
        selection = self.mode_tree.selection()
        if not selection:
            return
        values = self.mode_tree.item(selection[0], "values")
        if not values:
            return
        label = values[0].split()[0]
        try:
            width, height = (int(x) for x in label.split("x"))
        except ValueError:
            return
        self.width_var.set(str(width))
        self.height_var.set(str(height))
        rate = values[1].replace("Hz", "").strip() if values[1] else ""
        try:
            self.refresh_var.set(f"{float(rate):g}")
        except ValueError:
            pass
        self.generate_preview()

    def set_resolution(self, width, height):
        self.width_var.set(str(width))
        self.height_var.set(str(height))
        self.generate_preview()

    # -- background work ------------------------------------------------------------

    def run_async(self, work, on_done):
        """Run `work` off the interface's thread.

        Every call to kscreenctl is a process and a round trip to KWin
        over DBus, and a window that waits for one stops drawing, which
        the desktop shows as a stutter. So the questions are asked from
        a worker and the answers go through a queue, which the timer
        drains on the interface's thread -- that is where a widget has
        to be touched, and Tk refuses to hear from anywhere else.
        """
        def in_background():
            try:
                answer = (on_done, work(), None)
            except Exception as e:   # the callback decides what to do
                answer = (on_done, None, e)
            # A plain queue: a worker may not touch Tk itself.
            self._answers.put(answer)

        thread = threading.Thread(target=in_background, daemon=True)
        thread.start()
        return thread

    def pump_answers(self):
        """Deliver the answers that came in since the last look."""
        if self._closing:
            return
        while True:
            try:
                on_done, result, error = self._answers.get_nowait()
            except queue.Empty:
                break
            if error is not None:
                self._async_failure(on_done, error)
                continue
            try:
                on_done(result)
            except tk.TclError:
                pass  # a widget that was closed meanwhile
        self._schedule_pump()

    def _schedule_pump(self, ms=50):
        """Set the next look for answers, unless the interface is gone."""
        if self._closing:
            return
        try:
            self._pump_id = self.root.after(ms, self.pump_answers)
        except (tk.TclError, RuntimeError):
            self._pump_id = None

    def _post(self, callback, ms=0):
        """Schedule a callback on the interface's thread, if it lives."""
        try:
            self.root.after(ms, callback)
        except (tk.TclError, RuntimeError):
            pass  # the window went away while the answer was coming

    def cancel_pump(self):
        """Stop looking for answers; the interface is going away."""
        tid = self._pump_id
        self._pump_id = None
        if not tid:
            return
        try:
            self.root.after_cancel(tid)
        except (KeyError, ValueError, tk.TclError, RuntimeError):
            pass

    def _async_failure(self, on_done, error):
        """A background question failed, which is not a user action.

        The questions that go this way are the ones the interface asks
        by itself (the mode list, the settings). The message and an
        empty answer are enough: a modal dialog on every failed refresh
        would make the tool nag about things nobody asked for.
        """
        if self._closing:
            return
        self.log(f"could not ask the display: {error}")
        try:
            on_done(None)
        except tk.TclError:
            pass

    # -- what Plasma says about a display ------------------------------------------

    def refresh_desktop_state(self):
        """Ask Plasma what it does with the selected output.

        Each answer is a round trip to KWin, so it is asked for off the
        interface's thread. One JSON answer holds the display settings
        and the custom modes; it fills the summary in the display card,
        the display settings card and the adaptive-sync control.
        """
        out = self.display_var.get()
        self.desktop_var.set("")
        self._kde_settings = None
        if self.env.compositor != "kwin" or not kwin.available() or not out:
            return

        def work():
            return kwin.settings(out), len(kwin.custom_modes(out))

        self.run_async(work, self.show_desktop_state)

    def show_desktop_state(self, values):
        """Put what Plasma reported into the cards."""
        if self._closing or not isinstance(values, tuple) or len(values) != 2:
            return
        settings, custom_count = values
        if not isinstance(settings, dict):
            return
        self._kde_settings = settings
        if self.display_var.get():
            self.desktop_var.set(self.desktop_summary(settings, custom_count))
        self.show_display_controls(settings)
        self.update_vrr_state(self.display_var.get())

    def desktop_summary(self, values, custom_count=0):
        """One line about how the desktop is running this display.

        The scale and the colour depth decide how sharp and how smooth
        the desktop looks; the HDR state and the custom modes are what
        the display settings hold for it.
        """
        parts = []
        scale = values.get("scale") or 1
        if scale != 1:
            parts.append(f"scaled {scale:g} ({round(scale * 100)}%)")
        depth = values.get("maxbpc") or 0
        if depth:
            parts.append(f"{depth} bpc")
        if values.get("hdr"):
            parts.append("HDR on")
        if custom_count:
            parts.append(f"{custom_count} custom mode(s) added")
        return f"Desktop: {' and '.join(parts)}." if parts else ""

    def show_display_controls(self, values):
        """Fill the display settings card from what Plasma reported."""
        if self._closing or not values:
            return
        hdr_capable = self._hdr_capable()

        depth = values.get("maxbpc") or 8
        label = f"{depth} bpc"
        self.bpc_var.set(label if label in self.bpc_combo.cget("values") else "")

        scale = values.get("scale") or 1
        percent = f"{round(scale * 100)}%"
        self.scale_var.set(percent if percent in KDE_SCALES else "")

        rotation = str(values.get("rotation", "none"))
        if rotation in ("normal", "0"):
            rotation = "none"
        self.rotation_var.set(rotation if rotation in KDE_ROTATIONS else "none")

        for widget in (self.hdr_check, self.wcg_check):
            widget.state(["!disabled"] if hdr_capable else ["disabled"])
        self.hdr_var.set(bool(values.get("hdr")))
        self.wcg_var.set(bool(values.get("wcg")))

        notes = []
        if not hdr_capable:
            notes.append("This display does not say it can do HDR.")
        self.controls_state_var.set(" ".join(notes))

    def _hdr_capable(self):
        """True when the output says it can do HDR.

        The capability that matters is "hdr"; "hdr-icc" is about the
        colour profile of an HDR mode, and a display that says only
        that still cannot show one.
        """
        return "hdr" in kwin.capabilities(self.display_var.get())

    def apply_hdr(self):
        out, on = self.display_var.get(), self.hdr_var.get()
        self._set_display_setting(
            lambda: kwin.set_hdr(out, on),
            "HDR" if on else "HDR off")

    def apply_wcg(self):
        out, on = self.display_var.get(), self.wcg_var.get()
        self._set_display_setting(
            lambda: kwin.set_wcg(out, on), "the wide colour gamut")

    def apply_bpc(self, _event=None):
        text = self.bpc_var.get().split()[0]
        try:
            depth = int(text)
        except (ValueError, IndexError):
            return
        out = self.display_var.get()
        previous = self.bpc_var.get()

        def restore():
            self.bpc_var.set(previous)

        self._set_display_setting(
            lambda: kwin.set_maxbpc(out, depth),
            f"the colour depth {depth} bpc", restore=restore)

    def apply_scale(self, _event=None):
        try:
            percent = int(self.scale_var.get().removesuffix("%"))
        except ValueError:
            return
        out = self.display_var.get()
        previous = self.scale_var.get()

        def restore():
            self.scale_var.set(previous)

        self._set_display_setting(
            lambda: kwin.set_scale(out, f"{percent}%"),
            f"the scale {percent}", restore=restore)

    def apply_rotation(self, _event=None):
        rotation = self.rotation_var.get()
        out = self.display_var.get()
        previous = self.rotation_var.get()

        def restore():
            self.rotation_var.set(previous)

        self._set_display_setting(
            lambda: kwin.set_rotation(out, rotation),
            f"the rotation {rotation}", restore=restore)

    def _set_display_setting(self, change, what, restore=None):
        """Change a display setting and read what came of it.

        The change runs off the interface's thread. When the desktop
        does not take the new value, what was shown before is put back
        and the message Plasma gave is shown; when it does, the
        settings are read again and the cards follow.
        """
        def work():
            ok, message = change()
            if not ok:
                return (False, message)
            return (True, "")

        def done(result):
            if self._closing or not result:
                return
            ok, message = result
            if ok:
                self.set_status(f"{what[0].upper()}{what[1:]} set.")
                self.refresh_desktop_state()
            else:
                if restore:
                    restore()
                self.set_status(f"Could not set {what}: {message}")
                messagebox.showerror("Error",
                                     f"Plasma would not set {what}:\n{message}")

        self.run_async(work, done)

    # -- inputs -------------------------------------------------------------------

    def read_inputs(self):
        """Returns (width, height, refresh) or raises ValueError."""
        w = self._read_int(self.width_var.get(), "width")
        h = self._read_int(self.height_var.get(), "height")
        try:
            r = float(self.refresh_var.get().strip())
        except ValueError:
            raise ValueError("the refresh rate must be a number")
        if w <= 0 or h <= 0 or r <= 0:
            raise ValueError("width, height and refresh rate must be positive")
        return w, h, r

    @staticmethod
    def _read_int(text, name):
        try:
            return int(text.strip())
        except ValueError:
            raise ValueError(f"the {name} must be a whole number of pixels")

    def current_modeline(self):
        if self.standard_var.get() == "manual":
            ml = timings.parse_modeline(self.modeline_var.get())
            name = f"{ml.hdisplay}x{ml.vdisplay}_{ml.actual_refresh:g}"
            return name, ml
        w, h, r = self.read_inputs()
        ml = timings.calc(w, h, r, self.standard_var.get())
        name = f"{w}x{h}_{r:g}"
        return name, ml

    def on_standard_changed(self):
        manual = self.standard_var.get() == "manual"
        if manual:
            self.manual_frame.pack(fill=tk.X, pady=(8, 0))
            if not self.modeline_var.get().strip():
                try:
                    w, h, r = self.read_inputs()
                    self.modeline_var.set(timings.calc(w, h, r, "cvt-rb2")
                                          .timing_string())
                except ValueError:
                    pass
        else:
            self.manual_frame.pack_forget()
        self.generate_preview()

    # -- the preview ---------------------------------------------------------------

    def generate_preview(self):
        try:
            name, ml = self.current_modeline()
        except ValueError as e:
            self.info_var.set(str(e))
            self.method_var.set("Waiting for a valid mode")
            self.effect_var.set("Fill in the resolution and the refresh rate "
                                "to see what would happen.")
            self.preview_text.delete("1.0", tk.END)
            self.preview_text.insert("1.0", self._input_hint(e))
            return
        out = self.display_var.get()

        header = (f"# {name}: {ml.clock_mhz:.3f} MHz pixel clock, "
                  f"{ml.actual_refresh:.3f} Hz actual "
                  f"({self.standard_var.get().upper()})\n"
                  f"# {ml.xorg_modeline(name)}\n\n")
        warning = self.limits_warning(ml)
        if warning:
            header += warning + "\n"

        if self.env.session_type == "x11":
            body = self.build_x11_preview(out, name, ml)
        elif self.env.session_type == "wayland":
            body = self.build_wayland_preview(out, name, ml)
        else:
            body = "Could not detect the session type. Showing the modeline only.\n"

        self.preview_text.delete("1.0", tk.END)
        self.preview_text.insert("1.0", header + body)

        if hasattr(self, "boot_method_check"):
            self.boot_method_check.state(
                ["!disabled"] if self.use_edid_method() else ["disabled"])

        self.method_var.set(self.method_summary())
        self.effect_var.set(self.effect_summary())
        self.info_var.set(f"{ml.hdisplay}x{ml.vdisplay} at "
                          f"{ml.actual_refresh:.3f} Hz, {ml.clock_mhz:.2f} MHz "
                          f"pixel clock")
        self.status_var.set(self.status_summary())

    def _input_hint(self, error):
        if self.standard_var.get() == "manual":
            return ("# Enter a modeline like:\n"
                    "#   746.064 2560 2568 2600 2640 1440 1556 1564 1570 "
                    "+hsync -vsync\n"
                    "# (clock in MHz, then the eight timing values, then "
                    "the sync flags)\n"
                    f"#\n# {error}\n")
        return f"# {error}\n"

    def method_summary(self):
        if self.env.session_type == "x11":
            return "xrandr and xorg.conf"
        if self.use_edid_method():
            return "EDID override"
        return f"{self.env.compositor} native"

    def edid_reason(self):
        """Why the EDID is the way this mode has to go in."""
        reason = detect.why_edid_needed(self.env, self.standard_var.get())
        if self.env.session_type == "wayland" and detect.compositor_can_apply(
                self.env, self.standard_var.get()):
            try:
                width, height, _refresh = self.read_inputs()
            except ValueError:
                return reason
            if not detect.driver_knows_resolution(self.env,
                                                  self.display_var.get(),
                                                  width, height):
                return (f"the driver does not have {width}x{height} yet, and "
                        "it will not switch to a resolution that is not in the "
                        "display's list")
        return reason

    def effect_summary(self):
        if self.env.session_type == "x11":
            return ("Test Mode applies the mode now and reverts it by itself. "
                    "Apply writes it to /etc/X11/xorg.conf.d, where it "
                    "survives a restart.")
        if self.use_edid_method():
            reason = self.edid_reason()
            return (f"The mode is added to the display's EDID, because {reason}. "
                    "It then shows up in the display settings and can be "
                    "selected there.")
        if self.env.kde_kscreenctl_available:
            return ("The mode is handed to KWin with the exact timings, and "
                    "applied there. Nothing is written to the system, so "
                    "adaptive sync keeps working.")
        return ("The mode is handed to the compositor, which applies it "
                "immediately. Nothing is written to the system.")

    def status_summary(self):
        if self.env.session_type == "x11":
            return "Use Test mode to try it, then Apply to keep it."
        if self.use_edid_method():
            return "Test mode tries it for a moment and reverts on its own."
        return "Test mode applies it now; the compositor keeps it."

    def limits_warning(self, ml):
        """Note anything the display says it cannot do. Not a refusal:
        monitors routinely run past what they declare, which is the whole
        point of this tool, but it explains a mode that gets rejected."""
        info = getattr(self, "_edid_info", None)
        if not info:
            return ""
        notes = []
        if info.max_pixel_clock_mhz and ml.clock_mhz > info.max_pixel_clock_mhz:
            notes.append(f"pixel clock {ml.clock_mhz:.0f} MHz is above the "
                         f"{info.max_pixel_clock_mhz} MHz this display declares")
        if info.max_vrefresh and ml.actual_refresh > info.max_vrefresh + 0.5:
            notes.append(f"{ml.actual_refresh:.0f} Hz is above its declared "
                         f"maximum of {info.max_vrefresh} Hz")
        hsync_khz = ml.clock_khz / ml.htotal
        if info.max_hsync_khz and hsync_khz > info.max_hsync_khz + 0.5:
            notes.append(f"horizontal frequency {hsync_khz:.0f} kHz is above "
                         f"its declared maximum of {info.max_hsync_khz} kHz")
        if not notes:
            return ""
        lines = ["# This mode goes beyond what the display reports:"]
        lines += [f"#   - {n}" for n in notes]
        lines.append("# It may still work. If the driver refuses it, an EDID "
                     "override raises")
        lines.append("# these declared limits, which is what the checks are "
                     "made against.")
        return "\n".join(lines) + "\n"

    def build_x11_preview(self, out, name, ml):
        parts = ["# Test commands (the Test Mode button runs these):\n",
                 f"xrandr --newmode \"{name}\" {ml.timing_string()}\n",
                 f"xrandr --addmode {out} \"{name}\"\n",
                 f"xrandr --output {out} --mode \"{name}\"\n\n",
                 "# Configuration file (Apply writes this to "
                 f"{xorg.XORG_CONF}):\n\n",
                 self.build_xorg_config(out, name, ml)]
        return "".join(parts)

    def build_xorg_config(self, out, name, ml):
        customedid = None
        if self.use_customedid(out):
            customedid = xorg.customedid_path(out)
        return xorg.build_config(
            out, name, ml,
            nvidia=self.env.has_nvidia_proprietary,
            relax_validation=self.relax_validation.get(),
            customedid=customedid)

    def build_wayland_preview(self, out, name, ml):
        env = self.env
        w, h, r = self.read_inputs()

        if self.use_edid_method():
            reason = self.edid_reason()
            return self.edid_override_notes(
                out, name, ml, reason=f"An EDID override is used because {reason}.")

        if env.compositor == "sway":
            return ("# Run now:\n"
                    f"swaymsg 'output {out} modeline {ml.timing_string()}'\n\n"
                    "# To make it permanent, add to ~/.config/sway/config:\n"
                    f"output {out} modeline {ml.timing_string()}\n")

        if env.compositor == "hyprland":
            return ("# Run now:\n"
                    f"hyprctl keyword monitor \"{out}, modeline {ml.timing_string()}, 0x0, 1\"\n\n"
                    "# To make it permanent, add to ~/.config/hypr/hyprland.conf:\n"
                    f"monitor = {out}, modeline {ml.timing_string()}, 0x0, 1\n")

        if env.compositor == "kwin":
            if env.kde_kscreenctl_available:
                return ("# Plasma 6.8 and newer take the timings as they are,\n"
                        "# so the mode on the display is exactly the one above.\n"
                        "# Adding the mode also switches to it:\n"
                        f"kscreenctl {out} add-custom-mode {ml.timing_string()}\n\n"
                        "# Switch back to a mode the display already had:\n"
                        f"kscreenctl {out} set-mode {w}x{h}@{r:g}\n\n"
                        "# Take the custom mode away again:\n"
                        f"kscreenctl {out} remove-custom-mode 0\n")
            mhz = int(round(r * 1000))
            blanking = "full" if self.standard_var.get() == "cvt" else "reduced"
            return ("# KWin takes resolution, refresh rate and a blanking\n"
                    "# choice, then computes the timings itself. They come out\n"
                    "# the same as the ones above, because both use libxcvt.\n"
                    "# Adding a mode and switching to it are separate steps:\n"
                    f"kscreen-doctor output.{out}.addCustomMode.{w}.{h}.{mhz}.{blanking}\n"
                    f"kscreen-doctor output.{out}.mode.{w}x{h}@{r:g}\n\n"
                    "# Test Mode runs both for you and reverts if you do not keep it.\n")

        if env.compositor == "cosmic":
            return ("# COSMIC cannot create a mode of its own, so the mode is\n"
                    "# added to the display's EDID first. After that it is an\n"
                    "# ordinary mode and can be selected with:\n"
                    f"cosmic-randr mode {out} {w} {h} --refresh {r:g}\n\n"
                    "# or in Settings > Displays.\n")

        if env.is_wlroots_family:
            return ("# Run now:\n"
                    f"wlr-randr --output {out} --custom-mode {w}x{h}@{r:g}Hz\n\n"
                    "# The compositor computes CVT full-blanking timings, which\n"
                    "# match the ones above.\n")

        return self.edid_override_notes(
            out, name, ml,
            reason=f"An EDID override is used because "
                   f"{detect.why_edid_needed(env, self.standard_var.get())}.")

    def edid_override_notes(self, out, name, ml, reason):
        conn = self.drm_connector_for(out)
        short = self.strip_card(conn)
        fw = persist.firmware_path(short)
        minor = self.drm_minor(conn)
        env = self.env

        try:
            e = edid.Edid.from_connector(conn)
            placement = "detailed timing descriptor" \
                if e.fits_dtd(ml) else "DisplayID timing record"
        except edid.EdidError:
            placement = "detailed timing / DisplayID record"

        method = (persist.METHOD_CMDLINE if self.boot_method_var.get()
                  else persist.METHOD_SYSTEMD)
        installed = persist.installed_connectors()

        parts = [
            f"# {reason}",
            f"# The {name} timing above is written into a copy of {short}'s EDID",
            f"# as a {placement}, installed at {fw}.",
            "",
            "# --- Test Mode runs, as root (no reboot, auto-reverts) ---",
            "# The patched EDID is written to a temporary file first, and",
            "# that copy is what goes over the connector:",
            f"cat {os.path.join('/tmp/linux-cru-XXXXXX', short + '.bin')} "
            f"> /sys/kernel/debug/dri/{minor}/{short}/edid_override",
            f"echo 1 > /sys/kernel/debug/dri/{minor}/{short}/trigger_hotplug",
            "",
        ]

        if env.has_nvidia_proprietary:
            parts += [
                "# Note: while an EDID override is in place on the NVIDIA",
                "# driver, adaptive sync (VRR) does not work.",
                "",
            ]

        if method == persist.METHOD_SYSTEMD:
            parts += [
                "# --- Apply installs this boot service ---",
                f"# {persist.SYSTEMD_UNIT}",
                persist.systemd_unit_text().rstrip(),
                "",
                f"# it runs this helper ({persist.SYSTEMD_HELPER}):",
                persist.systemd_helper_text().rstrip(),
            ]
        else:
            param = (f"firmware_class.path={persist.FIRMWARE_SEARCH_PATH} "
                     f"drm.edid_firmware={short}:{persist.firmware_rel(short)}")
            boot, detail = persist.detect_bootloader()
            initrd, _ = persist.detect_initramfs()
            parts += [
                "# --- Apply adds this to the kernel command line ---",
                f"#   bootloader: {boot} ({detail})",
                param,
                f"# and adds {os.path.basename(fw)} to the initramfs ({initrd}).",
            ]

        parts += [
            "",
            "# Currently installed overrides: "
            + (", ".join(installed) if installed else "none"),
        ]
        return "\n".join(parts) + "\n"

    # -- helpers -------------------------------------------------------------------

    def drm_minor(self, conn):
        card = conn.partition("-")[0]
        try:
            with open(f"/sys/class/drm/{card}/dev") as f:
                return f.read().strip().split(":")[1]
        except OSError:
            return "N"

    def drm_connector_for(self, out):
        for c in self.env.connectors:
            if c.name == out:
                return f"{c.card}-{c.name}"
        return f"cardX-{out}"

    @staticmethod
    def strip_card(conn):
        return conn.partition("-")[2] if conn.startswith("card") else conn

    def use_customedid(self, out):
        return (self.env.has_nvidia_proprietary and self.env.session_type == "x11"
                and self.nvidia_customedid.get())

    def use_edid_method(self):
        """True when the timings can only be delivered through the EDID."""
        if self.env.session_type == "x11":
            return False
        if not detect.compositor_can_apply(self.env, self.standard_var.get()):
            return True
        # The compositor can produce the timings, but the driver has to
        # know the resolution before it will switch to it: it refuses a
        # resolution that is not in the connector's mode list, however
        # the compositor offers it. So a resolution the driver does not
        # have yet goes in through the EDID, which is what adds it.
        try:
            width, height, _refresh = self.read_inputs()
        except ValueError:
            return False
        if not detect.driver_knows_resolution(self.env,
                                              self.display_var.get(),
                                              width, height):
            return True
        return False

    # -- log --------------------------------------------------------------------

    def log(self, message):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{stamp}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def clear_log(self):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def copy_preview(self):
        text = self.preview_text.get("1.0", tk.END)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.log("Preview copied to the clipboard.")

    # -- stretch (KWin) ------------------------------------------------------------

    def apply_vrr(self):
        """Turn adaptive sync on, off or leave it to the display."""
        policy = self.vrr_var.get()
        out = self.display_var.get()
        ok, message = wayland.set_vrr(self.env, out, policy)
        kwin.invalidate()
        if ok:
            self.set_status(f"Adaptive sync set to {policy} on {out}.")
            self.vrr_status_var.set(f"Adaptive sync is {policy}.")
        else:
            self.vrr_status_var.set("")
            messagebox.showerror(
                "Error",
                f"Could not set adaptive sync on {out}:\n{message}")
            self.set_status("Could not set adaptive sync.")

    def update_vrr_state(self, out):
        """Show what adaptive sync is set to, and whether it can be.

        A display that cannot do it is told so: there is no point in
        offering a policy that the compositor will refuse, and the
        reason a display cannot do it -- an EDID override that replaced
        its own EDID -- is worth knowing about.
        """
        if not hasattr(self, "vrr_combo"):
            return
        policy = ""
        capable = None
        if self.env.compositor == "kwin" and kwin.available() and out:
            policy = kwin.vrr_policy(out)
            capable = kwin.supports_vrr(out)
        if policy in wayland.VRR_POLICIES:
            self.vrr_var.set(policy)
        elif capable is not None:
            self.vrr_var.set("automatic")

        if capable is False:
            self.vrr_combo.state(["disabled"])
            self.vrr_status_var.set(
                "This display does not report adaptive sync, so the frame "
                "rate is locked to whatever the mode says.")
        else:
            self.vrr_combo.state(["!disabled"])
            if policy in wayland.VRR_POLICIES:
                self.vrr_status_var.set(
                    f"Adaptive sync is set to {policy} right now.")
            else:
                self.vrr_status_var.set("")

    def refresh_stretch_status(self):
        if self.env.compositor != "kwin":
            return
        saved = stretch.persistent_value()
        if saved:
            self.stretch_revert_btn.pack(side=tk.LEFT, padx=(8, 0))
            self.stretch_status_var.set(
                f"Saved across reboots: {saved}. Apply takes effect now; "
                "the saved setting also applies at every login. Revert turns "
                "it off and removes the saved setting.")
        else:
            self.stretch_revert_btn.pack_forget()
            self.stretch_status_var.set(
                "Apply takes effect immediately (needs root); it is lost when "
                "you log out unless you keep it.")

    def revert_stretch(self):
        result = self.run_root_script(stretch.build_live_patch_script("none"),
                                       "stretch-live.py",
                                       interpreter="/usr/bin/python3")
        if result.cancelled:
            self.set_status("Cancelled.")
            return
        stretch.remove_persistent()
        self.stretch_mode_var.set("off")
        self.refresh_stretch_status()
        self.set_status("Stretch turned off and the saved setting removed."
                        if result.ok else
                        "Saved setting removed; could not change the live "
                        "session: " + result.message)

    def apply_stretch(self):
        value = self.stretch_mode_var.get()

        script = stretch.build_live_patch_script(
            "none" if value == "off" else value)
        result = self.run_root_script(script, "stretch-live.py",
                                      interpreter="/usr/bin/python3")
        if result.cancelled:
            self.set_status("Cancelled.")
            return
        if not result.ok:
            messagebox.showerror(
                "Error",
                f"Could not apply the live stretch:\n{result.message}\n\n"
                "This pokes the running KWin's memory and needs root. If KWin "
                "was updated the signature may need refreshing.")
            return

        if value == "off":
            stretch.remove_persistent()
            self.refresh_stretch_status()
            self.set_status("Stretch turned off.")
            return

        self.set_status("Stretch is active now. Set a smaller resolution "
                        "and it will fill the screen.")

        keep = messagebox.askyesno(
            "Keep the setting?",
            "Keep this scaling setting as a systemd service?\n\n"
            "It will be lost when you log out if you don't. Keeping it makes "
            "it apply at every login from now on.")
        if keep:
            stretch.write_persistent(value.upper())
            self.set_status("Stretch active now and kept for future logins.")
        else:
            stretch.remove_persistent()
        self.refresh_stretch_status()

    # -- actions -------------------------------------------------------------------

    def set_status(self, message):
        self.status_var.set(message)
        self.log(message)

    def test_mode(self):
        try:
            name, ml = self.current_modeline()
        except ValueError as e:
            messagebox.showerror("Error", f"Invalid input: {e}")
            return
        out = self.display_var.get()

        if self.env.session_type == "x11":
            self.test_mode_x11(out, name + "_test", ml)
        elif self.wayland_testable() and not self.use_edid_method():
            if self.env.compositor == "kwin":
                self.test_mode_kwin(out)
            else:
                self.test_mode_wlroots(out, ml)
        else:
            self.test_mode_edid(out, ml)

    def wayland_testable(self):
        """True when the compositor can apply the selected timings itself."""
        return (self.env.session_type == "wayland"
                and detect.compositor_can_apply(self.env, self.standard_var.get()))

    def test_mode_x11(self, out, name, ml):
        previous = self.current_mode_x11(out)

        res = self.xrandr("--newmode", name, *ml.xrandr_args())
        if res.returncode != 0 and "already" not in res.stderr.lower():
            messagebox.showerror("Error", f"xrandr --newmode failed:\n{res.stderr}")
            return

        res = self.xrandr("--addmode", out, name)
        if res.returncode != 0:
            self.xrandr("--rmmode", name)
            hint = ""
            if self.env.has_nvidia_proprietary:
                hint = ("\n\nOn NVIDIA this usually means ModeValidation is not relaxed "
                        "yet: click Apply first, restart X, then test.")
            messagebox.showerror("Error", f"xrandr --addmode failed (driver rejected the "
                                          f"mode):\n{res.stderr}{hint}")
            return

        res = self.xrandr("--output", out, "--mode", name)
        if res.returncode != 0:
            self.cleanup_test_mode(out, name)
            messagebox.showerror(
                "Error",
                f"Could not switch to the mode:\n{res.stderr}\n\n"
                "'Configure crtc failed' usually means the pixel clock is over the "
                "link or EDID limit. Try CVT-RBv2 or a lower refresh rate, and "
                "check dmesg.")
            return

        def revert():
            if previous:
                self.xrandr("--output", out, "--mode", previous[0],
                            "--rate", previous[1])
            else:
                self.xrandr("--output", out, "--auto")
            self.cleanup_test_mode(out, name)

        self.show_revert_dialog(
            out, revert,
            keep_status="Mode kept for this session. Use Apply to keep it "
                        "after a restart.")

    def test_mode_kwin(self, out):
        w, h, r = self.read_inputs()
        name, ml = self.current_modeline()
        reduced = self.standard_var.get() != "cvt"
        state = wayland.kwin_state(out)
        if not state:
            if kwin.available():
                messagebox.showerror(
                    "Error",
                    "kscreenctl could not talk to KWin.\n\n"
                    "It only works inside a Plasma session. If this is a "
                    "Plasma session, the display server may not be ready "
                    "yet — try again in a moment.")
            else:
                messagebox.showerror(
                    "Error", "Could not read the display configuration.")
            return
        # Remember what the display was running, not the id of the mode:
        # the ids are renumbered every time the configuration is applied.
        previous = detect.current_mode(self.env, out)

        if previous and previous[0] == w and previous[1] == h \
                and abs(previous[2] - r) < 0.01:
            self.set_status(f"{w}x{h} at {r:g} Hz is what {out} is running "
                            "already; nothing to apply.")
            messagebox.showinfo(
                "Already in use",
                f"{w}x{h} at {r:g} Hz is what {out} runs now, so nothing "
                "was changed.")
            return

        ok, msg = wayland.kwin_apply_custom_mode(out, ml, w, h, r, reduced)
        if not ok:
            messagebox.showerror("Error", f"Could not apply the mode:\n{msg}")
            return

        def revert():
            # The display has to leave the mode before it can be taken
            # away: KWin keeps a custom mode that is in use.
            if previous:
                wayland.kwin_switch_to(out, *previous)
            ok, message = wayland.kwin_revert_custom_mode(out, w, h, r)
            if not ok:
                self.log("the custom mode was not removed: " + message)

        self.show_revert_dialog(
            out, revert,
            keep_status="Mode kept. KDE saves display settings automatically.")

    def test_mode_wlroots(self, out, ml):
        w, h, r = self.read_inputs()
        previous = detect.current_mode(self.env, out)

        if self.env.compositor == "sway":
            apply_modeline, set_mode = wayland.sway_apply_modeline, wayland.sway_set_mode
        else:
            apply_modeline, set_mode = (wayland.hyprland_apply_modeline,
                                        wayland.hyprland_set_mode)

        ok, msg = apply_modeline(out, ml.timing_string())
        if not ok:
            messagebox.showerror(
                "Error",
                f"The compositor rejected the mode:\n{msg}\n\n"
                "The driver may not accept these timings. Check the compositor "
                "log and dmesg.")
            return

        def revert():
            if previous:
                pw, ph, pr = previous
                set_mode(out, pw, ph, pr)

        self.show_revert_dialog(
            out, revert,
            keep_status="Mode applied for this session. To keep it permanently, "
                        "add the line from the preview to your compositor config.")

    def test_mode_edid(self, out, ml):
        conn = self.drm_connector_for(out)
        card = conn.partition("-")[0]
        connector = self.strip_card(conn)
        w, h, r = self.read_inputs()

        try:
            patched, placement = self.build_patched_edid(out, ml)
        except edid.EdidError as e:
            messagebox.showerror("Error", f"Could not build the EDID:\n{e}")
            return

        edid_path = os.path.join(self.work_dir(), f"{connector}.bin")
        with open(edid_path, "wb") as f:
            f.write(patched)
        os.chmod(edid_path, 0o644)

        previous = detect.current_mode(self.env, out)

        script = override.build_test_script(card, connector, edid_path,
                                            TEST_REVERT_SECONDS + 20)
        result = self.run_root_script(script, "test-override.sh")
        if result.cancelled:
            self.set_status("Cancelled.")
            return
        if not result.ok:
            messagebox.showerror(
                "Error",
                f"Could not apply the EDID override:\n{result.message}\n\n"
                "This needs root access, and debugfs must not be restricted "
                "by kernel lockdown.")
            return

        def remove_override():
            self.run_root_script(override.build_revert_script(card, connector),
                                 "revert-override.sh")

        # The mode only exists once the kernel has re-read the EDID.
        if not wayland.wait_for_mode(card, connector, w, h):
            remove_override()
            messagebox.showerror(
                "Error",
                f"The kernel did not pick up {w}x{h} after the EDID was "
                "applied.\n\nThe driver most likely rejected it: the pixel "
                "clock may be beyond what the connection can carry. Check "
                "dmesg.")
            return

        # The kernel having it is not enough: the desktop keeps its own
        # copy of the mode list, and a mode it has not read yet cannot
        # be selected.
        if not wayland.wait_for_output_mode(self.env, out, w, h):
            # Ask the display to be detected again. This is what a
            # replug does, and it is the one thing that makes every
            # compositor look at the display's modes again. The screen
            # goes blank for a moment, so ask first.
            asked = messagebox.askyesno(
                "Ask the display again?",
                f"{self.env.compositor} has not read the new mode yet. It "
                "only reads the display's modes when the display is detected "
                "again.\n\n"
                "This tool can ask for that now. The screen goes blank for a "
                "moment while the display is detected again.\n\n"
                "If the screen does not come back by itself, unplug the "
                "display and plug it back in.\n\n"
                "Do it now?")
            if asked:
                self.log(f"{self.env.compositor} has not read the mode yet; "
                         "asking the display to be detected again.")
                result = self.run_root_script(
                    override.build_reprobe_script(card, connector),
                    "reprobe.sh")
                if not result.ok:
                    self.log("the display could not be detected again: "
                             + result.message)
                if wayland.wait_for_output_mode(self.env, out, w, h,
                                                timeout=8.0):
                    self.log(f"{self.env.compositor} has the mode now.")
                else:
                    asked = False

            if not asked:
                remove_override()
                messagebox.showerror(
                    "Error",
                    f"The mode was added to the display's EDID, and the kernel "
                    f"knows about {w}x{h}, but {self.env.compositor} did not "
                    "pick it up.\n\n"
                    "Some compositors only read the display's modes when the "
                    "display is plugged in again: unplug it and plug it back "
                    "in, or log out and back in, and the mode will be there.\n\n"
                    "Apply (instead of Test mode) puts the mode in the "
                    "display's EDID at every boot, which is the way to make it "
                    "permanent.")
                self.set_status(f"The mode was added, but {self.env.compositor} "
                                "has not read it yet.")
                return

        ok, message = wayland.set_mode(self.env, out, w, h, r)
        if not ok:
            self.set_status(f"Mode added to {out}, but it could not be "
                            f"selected automatically: {message}")
            keep = messagebox.askyesno(
                "Added, but not applied",
                f"{w}x{h} at {r:g} Hz was added to {out}, but this tool could "
                f"not switch to it:\n\n{message}\n\nKeep the mode available "
                f"so you can select it yourself?")
            if keep:
                self.run_root_script(
                    f"#!/bin/bash\ntouch '{override.keep_flag_path(connector)}'\n",
                    "keep-override.sh")
                self.set_status("Mode kept for this session.")
            else:
                remove_override()
                self.set_status("Reverted.")
            return

        def revert():
            # Order matters: leave the custom mode before taking away the
            # EDID that defines it, or the output is left on a mode that
            # no longer exists.
            if previous:
                pw, ph, pr = previous
                wayland.set_mode(self.env, out, pw, ph, pr)
                time.sleep(1.0)
            remove_override()

        def keep():
            self.run_root_script(
                f"#!/bin/bash\ntouch '{override.keep_flag_path(connector)}'\n",
                "keep-override.sh")

        self.set_status(f"Testing {w}x{h} at {r:g} Hz on {out}.")
        self.show_revert_dialog(
            out, revert,
            keep_status="Mode kept for this session. Use Apply to keep it "
                        "after a reboot.",
            on_keep=keep,
            message=f"{out} is now running at {w}x{h}, {r:g} Hz\n"
                    f"(added to the EDID as {placement}).")

    # -- the X11 route --------------------------------------------------------------

    def xrandr(self, *args):
        return subprocess.run(["xrandr"] + list(args), capture_output=True,
                              universal_newlines=True,
                              env=hostenv.subprocess_env())

    def current_mode_x11(self, out):
        """(mode_name, rate) currently active on `out`, or None."""
        res = self.xrandr("-q")
        if res.returncode != 0:
            return None
        in_block = False
        for line in res.stdout.splitlines():
            if not line.startswith((" ", "\t")):
                in_block = line.split()[0] == out if line.split() else False
                continue
            if in_block and "*" in line:
                tokens = line.split()
                mode = tokens[0]
                for tok in tokens[1:]:
                    if "*" in tok:
                        return mode, tok.replace("*", "").replace("+", "")
        return None

    def cleanup_test_mode(self, out, name):
        self.xrandr("--delmode", out, name)
        self.xrandr("--rmmode", name)

    def apply_configuration(self):
        if self.env.session_type != "x11":
            if self.wayland_testable() and not self.use_edid_method():
                if self.env.compositor == "kwin":
                    messagebox.showinfo(
                        "Wayland",
                        "Use Test mode to apply the mode. If you keep it,\n"
                        "KDE saves it automatically.")
                else:
                    messagebox.showinfo(
                        "Wayland",
                        "Use Test mode to apply the mode now. To keep it\n"
                        "permanently, add the line from the preview to your\n"
                        "compositor config file.")
            else:
                try:
                    _, ml = self.current_modeline()
                except ValueError as e:
                    messagebox.showerror("Error", f"Invalid input: {e}")
                    return
                self.apply_edid_override(self.display_var.get(), ml)
            return
        try:
            name, ml = self.current_modeline()
        except ValueError as e:
            messagebox.showerror("Error", f"Invalid input: {e}")
            return
        out = self.display_var.get()

        # Stage the CustomEDID .bin before writing anything.
        edid_bin = None
        if self.use_customedid(out):
            try:
                e = self.edid_for_output(out)
                e.add_mode(ml)
                edid_bin = e.to_bytes()
            except edid.EdidError as err:
                if not messagebox.askyesno(
                        "CustomEDID unavailable",
                        f"Could not build a patched EDID:\n{err}\n\n"
                        "Continue with ModeValidation only (may be ignored on "
                        "driver 580.105.08+)?"):
                    return
                self.nvidia_customedid.set(False)

        config = self.build_xorg_config(out, name, ml)

        tmp_dir = f"/tmp/linux_cru_{os.getpid()}"
        os.makedirs(tmp_dir, exist_ok=True)
        conf_path = os.path.join(tmp_dir, "10-linux-cru.conf")
        with open(conf_path, "w") as f:
            f.write(config)

        edid_path = None
        if edid_bin is not None:
            edid_path = os.path.join(tmp_dir, f"{out}.bin")
            with open(edid_path, "wb") as f:
                f.write(edid_bin)
            os.chmod(edid_path, 0o644)

        script = xorg.build_install_script(
            conf_path, edid_path, xorg.customedid_path(out) if edid_path else None)

        script_path = os.path.join(tmp_dir, "apply.sh")
        with open(script_path, "w") as f:
            f.write(script)
        os.chmod(script_path, 0o755)

        result = privileged.run_as_root(["/bin/bash", script_path],
                                        ask_password=self.ask_password)
        try:
            shutil.rmtree(tmp_dir)
        except OSError:
            pass

        if result.cancelled:
            self.set_status("Cancelled.")
        elif result.ok:
            extra = (f"\nPatched EDID: {xorg.customedid_path(out)}"
                     if edid_bin is not None else "")
            messagebox.showinfo(
                "Success",
                f"Configuration saved to {xorg.XORG_CONF}."
                f"{extra}\n\n"
                "The new mode will be available after you restart X\n"
                "(log out and back in). To undo it, use Remove.")
            self.set_status("Configuration applied successfully.")
        else:
            messagebox.showerror("Error",
                                 f"Failed to apply configuration:\n{result.message}")
            self.set_status("Error: failed to apply the configuration")

    # -- the EDID route ---------------------------------------------------------------

    def build_patched_edid(self, out, ml):
        """(patched_bytes, placement) for the selected display, or raises."""
        conn = self.drm_connector_for(out)
        e = edid.Edid.from_connector(conn)
        placement = e.add_mode(ml)
        return e.to_bytes(), placement

    def edid_for_output(self, out):
        """Read the source EDID for `out`.

        On NVIDIA X11 the xrandr name (DP-0) differs from the DRM name
        (DP-1), so fall back to the sole connected connector.
        """
        try:
            return edid.Edid.from_connector(self.drm_connector_for(out))
        except edid.EdidError:
            pass
        connected = [c for c in self.env.connectors
                     if c.status == "connected" and c.has_edid]
        if len(connected) == 1:
            return edid.Edid.from_connector(
                f"{connected[0].card}-{connected[0].name}")
        raise edid.EdidError(
            "could not read this display's EDID to patch it "
            f"(connected displays with an EDID: {len(connected)})")

    def apply_edid_override(self, out, ml):
        conn = self.drm_connector_for(out)
        connector = self.strip_card(conn)
        try:
            patched, placement = self.build_patched_edid(out, ml)
        except edid.EdidError as e:
            messagebox.showerror("Error", f"Could not build the EDID:\n{e}")
            return

        method = (persist.METHOD_CMDLINE if self.boot_method_var.get()
                  else persist.METHOD_SYSTEMD)
        steps = "\n".join(f"  {i}. {s}" for i, s in
                          enumerate(persist.describe_plan(connector, method), 1))

        if method == persist.METHOD_CMDLINE:
            warning = ("\nThis changes your boot configuration. If the display "
                       "ever fails to come up, remove the drm.edid_firmware "
                       "parameter from the kernel command line in your "
                       "bootloader menu.\n")
        else:
            warning = ("\nYour boot configuration is not touched, so this "
                       "cannot stop the machine from booting.\n")

        if not messagebox.askyesno(
                "Make permanent",
                "This keeps the mode in the display's list at every boot. It "
                "does not change your resolution -- you still select the mode "
                f"yourself in your display settings.\n\n{steps}\n{warning}\n"
                "Continue?"):
            return

        edid_path = os.path.join(self.work_dir(), f"{connector}-persist.bin")
        with open(edid_path, "wb") as f:
            f.write(patched)
        os.chmod(edid_path, 0o644)

        result = self.run_root_script(
            persist.build_install_script(connector, edid_path, method),
            "install.sh")
        self.refresh_remove_button()
        if result.cancelled:
            self.set_status("Cancelled.")
            return
        if result.ok:
            if method == persist.METHOD_CMDLINE:
                when = ("After a reboot the mode is in the display's list. "
                        "Select it in your display settings.")
            else:
                when = ("The mode is in the display's list now and is re-added "
                        "at every boot. Select it in your display settings -- "
                        "your resolution is not changed automatically.")
            if self.env.compositor in ("cosmic", "mutter"):
                # These compositors read the display's modes when they
                # start, not while they run, so the mode shows up after
                # the next login.
                when += ("\n\nYour desktop only reads the display's modes when "
                         "it starts, so log out and back in (or reboot) for "
                         "the mode to appear in the list.")
            messagebox.showinfo(
                "Installed",
                f"The mode was added to {out}'s EDID as a {placement}.\n\n{when}\n\n"
                "Use Remove to undo it.")
            self.set_status("Custom mode installed and kept across reboots.")
        else:
            messagebox.showerror("Error", f"Installation failed:\n{result.message}")

    def remove_configuration(self):
        """Undo whatever this tool installed: the EDID overrides and the
        X11 configuration."""
        installed = persist.installed_connectors()
        xorg_installed = xorg.installed()
        if not installed and not xorg_installed:
            self.set_status("There is nothing installed to remove.")
            return

        out = self.strip_card(self.drm_connector_for(self.display_var.get()))
        target = out if out in installed else None
        what = []
        if target:
            what.append(f"the EDID override for {target}")
        elif installed:
            what.append(f"all EDID overrides ({', '.join(installed)})")
        if xorg_installed:
            what.append(f"the X11 configuration ({xorg.XORG_CONF})")

        if not messagebox.askyesno("Remove", f"Remove {', '.join(what)}?"):
            return

        ok = True
        message = ""
        cancelled = False
        if installed:
            result = self.run_root_script(
                persist.build_uninstall_script(target), "uninstall.sh")
            ok = ok and result.ok
            cancelled = cancelled or result.cancelled
            message = result.message or message
            if result.ok:
                self.clean_up_stale_custom_modes()
        if xorg_installed:
            result = self.run_root_script(
                xorg.build_uninstall_script(out if target else None),
                "uninstall-xorg.sh")
            ok = ok and result.ok
            cancelled = cancelled or result.cancelled
            message = result.message or message

        self.refresh_remove_button()
        if cancelled:
            self.set_status("Cancelled.")
            return
        if ok:
            messagebox.showinfo(
                "Removed",
                "Removed. A mode that is already in the list stays there until "
                "you reboot or replug the display.")
            self.set_status("Everything this tool installed was removed.")
        else:
            messagebox.showerror("Error", f"Could not remove it:\n{message}")
            self.set_status("Error: could not remove the configuration")

    def clean_up_stale_custom_modes(self):
        """Take away the custom modes the driver can no longer provide.

        A custom mode that the compositor keeps in its configuration but
        the driver cannot produce makes every configuration apply fail,
        and the desktop says "the driver rejected the output
        configuration" with no hint of what is wrong. That happens when
        the EDID override that made the resolution exist is taken away
        while the custom mode is still in the configuration.
        """
        if self.env.compositor != "kwin" or not kwin.available():
            return
        for out in self.env.connectors:
            modes = kwin.custom_modes(out.name)
            if not modes:
                continue
            stale = []
            for index, mode in enumerate(modes):
                size = mode.get("resolution", {})
                width = int(size.get("width", 0))
                height = int(size.get("height", 0))
                if detect.driver_knows_resolution(self.env, out.name,
                                                  width, height):
                    continue
                stale.append(index)
            for index in reversed(stale):
                ok, message = kwin.remove_custom_mode(out.name, index)
                if ok:
                    self.log(f"removed the custom mode {out.name} "
                             f"{index}, which the driver can no longer "
                             "provide.")
                else:
                    self.log(f"could not remove the custom mode "
                             f"{out.name} {index}: {message}")

    def refresh_remove_button(self):
        if persist.installed_connectors() or xorg.installed():
            self.remove_btn.state(["!disabled"])
        else:
            self.remove_btn.state(["disabled"])

    # -- privileged helpers ---------------------------------------------------------

    def ask_password(self, message, retry):
        return PasswordPrompt(self.root, message, retry).run()

    def run_root_script(self, script, name, interpreter="/bin/bash"):
        """Write `script` to the work dir and run it as root."""
        path = os.path.join(self.work_dir(), name)
        with open(path, "w") as f:
            f.write(script)
        os.chmod(path, 0o755)
        result = privileged.run_as_root([interpreter, path],
                                        ask_password=self.ask_password)
        self.log(f"{name}: " + (result.message.strip() or
                                ("done" if result.ok else "failed")))
        return result

    def work_dir(self):
        if not self._workdir_path:
            self._workdir_path = tempfile.mkdtemp(prefix="linux-cru-")
            os.chmod(self._workdir_path, 0o755)
        return self._workdir_path

    # -- the countdown dialog ---------------------------------------------------------

    @staticmethod
    def center_dialog(dialog):
        """Size the window to its content and centre it on the parent."""
        dialog.update_idletasks()
        w = dialog.winfo_reqwidth()
        h = dialog.winfo_reqheight()
        parent = dialog.master
        x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - h) // 3
        dialog.geometry(f"{w}x{h}+{max(x, 0)}+{max(y, 0)}")

    def show_revert_dialog(self, out, on_revert, keep_status,
                           on_keep=None, message=None):
        dialog = tk.Toplevel(self.root)
        dialog.title("Testing mode")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.grab_set()
        dialog.protocol("WM_DELETE_WINDOW", lambda: None)

        remaining = tk.IntVar(value=TEST_REVERT_SECONDS)
        label = ttk.Label(dialog, justify="center", wraplength=440, text="")
        label.pack(padx=16, pady=12)

        state = {"done": False}

        def revert():
            if state["done"]:
                return
            state["done"] = True
            on_revert()
            dialog.destroy()
            self.set_status("Reverted to the previous mode.")

        def keep():
            if state["done"]:
                return
            state["done"] = True
            if on_keep:
                on_keep()
            dialog.destroy()
            self.set_status(keep_status)

        head = message or f"Testing the new mode on {out}."

        def tick():
            if state["done"]:
                return
            n = remaining.get()
            label.config(text=f"{head}\n\n"
                              f"Reverting in {n} seconds.\n"
                              f"Press Enter or click Keep to keep it.")
            if n <= 0:
                revert()
                return
            remaining.set(n - 1)
            dialog.after(1000, tick)

        btns = ttk.Frame(dialog)
        btns.pack(pady=(0, 12))
        keep_btn = ttk.Button(btns, text="Keep", command=keep)
        keep_btn.pack(side=tk.LEFT, padx=8)
        ttk.Button(btns, text="Revert now", command=revert).pack(side=tk.LEFT,
                                                                   padx=8)
        dialog.bind("<Return>", lambda e: keep())
        keep_btn.focus()
        tick()
        self.center_dialog(dialog)

    # -- settings ---------------------------------------------------------------------

    def load_settings_into_form(self):
        """Put the remembered values back into the form."""
        if self.settings.get("display"):
            self.display_var.set(self.settings["display"])
        self.width_var.set(str(self.settings.get("width", "1920")))
        self.height_var.set(str(self.settings.get("height", "1080")))
        self.refresh_var.set(str(self.settings.get("refresh", "75")))
        standard = self.settings.get("standard", "cvt-rb2")
        if standard in [value for _, value, _ in TIMING_OPTIONS]:
            self.standard_var.set(standard)
        self.on_standard_changed()

    def save_settings(self):
        self.settings.update({
            "display": self.display_var.get(),
            "width": self.width_var.get(),
            "height": self.height_var.get(),
            "refresh": self.refresh_var.get(),
            "standard": self.standard_var.get(),
            "theme": self.settings.get("theme", "system"),
        })
        try:
            self.settings["geometry"] = self.root.geometry()
        except tk.TclError:
            pass
        settings.save(self.settings)

    def on_close(self):
        self._closing = True
        self.cancel_pump()
        self.save_settings()
        self.root.destroy()
