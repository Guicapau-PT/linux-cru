"""The look of the application.

Tkinter's default widgets look like 1998, so the interface is built on
the "clam" theme with a palette of its own: flat surfaces, one accent
colour, and readable monospace for the commands. Two palettes are
provided, light and dark, and the one to use is worked out from the
desktop's own settings so the window matches the rest of the system.

Colours are set on the ttk styles rather than on the widgets, so
everything follows the palette, including the scrollbars, the tree view
and the notebook.
"""

import os
import re
import tkinter as tk
from dataclasses import dataclass
from tkinter import font as tkfont
from tkinter import ttk


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str              # the window
    surface: str         # cards and panels
    surface_alt: str     # inputs, code, headers
    border: str
    text: str
    text_muted: str
    accent: str
    accent_text: str
    accent_hover: str
    danger: str
    warning: str
    success: str
    selection: str


LIGHT = Palette(
    name="light",
    bg="#eef1f6",
    surface="#ffffff",
    surface_alt="#f1f4f8",
    border="#d5dbe5",
    text="#1a1f29",
    text_muted="#5d6878",
    accent="#2f6feb",
    accent_text="#ffffff",
    accent_hover="#2459c8",
    danger="#c0392b",
    warning="#a05a00",
    success="#1a7f4b",
    selection="#d8e6fb",
)

DARK = Palette(
    name="dark",
    bg="#0f1216",
    surface="#181c23",
    surface_alt="#11151a",
    border="#2a313c",
    text="#e6eaf2",
    text_muted="#949fb0",
    accent="#4c8dff",
    accent_text="#0b0e13",
    accent_hover="#6ea3ff",
    danger="#ff6b6b",
    warning="#f0b429",
    success="#4ade80",
    selection="#22344d",
)


# -- finding out which palette to use ----------------------------------------

def _kde_is_dark():
    """The Plasma colour scheme, read from kdeglobals."""
    try:
        with open(os.path.expanduser("~/.config/kdeglobals")) as f:
            text = f.read()
    except OSError:
        return None
    m = re.search(r"^ColorScheme=([^\n]+)", text, re.M)
    if not m:
        return None
    return "dark" in m.group(1).lower()


def _cosmic_is_dark():
    """The COSMIC theme, read from its own settings file."""
    path = os.path.expanduser("~/.config/cosmic/com.system76.CosmicTheme.Mode")
    try:
        with open(path) as f:
            value = f.read().strip().strip('"').lower()
    except OSError:
        return None
    if not value:
        return None
    return "dark" in value


def _gnome_is_dark():
    """The GNOME colour scheme, read through gsettings."""
    import subprocess
    try:
        out = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            capture_output=True, universal_newlines=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    out = (out or "").strip().strip("'").lower()
    if not out:
        return None
    if "dark" in out:
        return True
    if "light" in out:
        return False
    return None


def detect_theme(desktop=None):
    """'dark' or 'light', following the desktop's own settings."""
    forced = os.environ.get("LINUX_CRU_THEME", "").strip().lower()
    if forced in ("dark", "light"):
        return forced

    desktop = (desktop or os.environ.get("XDG_CURRENT_DESKTOP", "")).lower()
    if "kde" in desktop or "plasma" in desktop:
        answer = _kde_is_dark()
        if answer is not None:
            return "dark" if answer else "light"
    if "cosmic" in desktop:
        answer = _cosmic_is_dark()
        if answer is not None:
            return "dark" if answer else "light"
    if "gnome" in desktop or "cosmic" in desktop:
        answer = _gnome_is_dark()
        if answer is not None:
            return "dark" if answer else "light"

    # Nothing said: COSMIC ships dark, everything else ships light.
    if "cosmic" in desktop:
        return "dark"
    return "light"


def palette_for(name):
    return DARK if name == "dark" else LIGHT


# -- fonts --------------------------------------------------------------------

def _pick_family(root, candidates, fallback="TkDefaultFont"):
    """The first of `candidates` the system actually has."""
    import tkinter.font as tkf
    families = set(tkf.families(root))
    for name in candidates:
        if name in families:
            return name
    return fallback


def install_fonts(root):
    """Point the named fonts at something readable. Returns the names used.

    Sizes are in points, so they follow the screen's DPI on a HiDPI
    display without any extra work.
    """
    ui_family = _pick_family(root, ["Inter", "Noto Sans", "DejaVu Sans",
                                    "Cantarell", "Ubuntu"])
    mono_family = _pick_family(root, ["JetBrains Mono", "Cascadia Mono",
                                       "Noto Sans Mono", "DejaVu Sans Mono",
                                       "Liberation Mono"], "TkFixedFont")

    tkfont.nametofont("TkDefaultFont").configure(family=ui_family, size=10)
    tkfont.nametofont("TkTextFont").configure(family=mono_family, size=10)
    tkfont.nametofont("TkMenuFont").configure(family=ui_family, size=10)
    tkfont.nametofont("TkHeadingFont").configure(family=ui_family, size=10,
                                                 weight="bold")
    tkfont.nametofont("TkIconFont").configure(family=ui_family, size=10)
    tkfont.nametofont("TkTooltipFont").configure(family=ui_family, size=9)
    return {"ui": ui_family, "mono": mono_family}


# -- the styles ----------------------------------------------------------------

def apply(root, mode="system"):
    """Set up the ttk styles. Returns the palette in use."""
    name = detect_theme() if mode in (None, "system") else mode
    pal = palette_for(name)
    style = ttk.Style(root)
    style.theme_use("clam")
    families = install_fonts(root)

    def font_of(size=10, weight=None):
        f = tkfont.Font(family=families["ui"], size=size)
        if weight:
            f.configure(weight=weight)
        return f

    style.configure(".", background=pal.bg, foreground=pal.text,
                    fieldbackground=pal.surface_alt, borderwidth=0,
                    focuscolor=pal.accent, font=font_of())
    style.map(".",
              foreground=[("disabled", pal.text_muted)],
              background=[("disabled", pal.bg)])

    # Frames and cards.
    style.configure("TFrame", background=pal.bg)
    style.configure("Card.TFrame", background=pal.surface)
    style.configure("Header.TFrame", background=pal.surface)
    style.configure("Sub.TFrame", background=pal.surface_alt)
    style.configure("Toolbar.TFrame", background=pal.surface)
    style.configure("Code.TFrame", background=pal.surface_alt)

    # Labels.
    style.configure("TLabel", background=pal.bg, foreground=pal.text)
    style.configure("Card.TLabel", background=pal.surface, foreground=pal.text)
    style.configure("Muted.TLabel", background=pal.surface,
                    foreground=pal.text_muted)
    style.configure("MutedBg.TLabel", background=pal.bg,
                    foreground=pal.text_muted)
    style.configure("Sub.TLabel", background=pal.surface_alt,
                    foreground=pal.text_muted)
    style.configure("Title.TLabel", background=pal.surface, foreground=pal.text,
                    font=font_of(15, "bold"))
    style.configure("SectionTitle.TLabel", background=pal.surface,
                    foreground=pal.text, font=font_of(11, "bold"))
    style.configure("Badge.TLabel", background=pal.surface_alt,
                    foreground=pal.text_muted, font=font_of(9))
    style.configure("Accent.TLabel", background=pal.surface,
                    foreground=pal.accent, font=font_of(11, "bold"))
    style.configure("Danger.TLabel", background=pal.surface,
                    foreground=pal.danger)
    style.configure("Success.TLabel", background=pal.surface,
                    foreground=pal.success)
    style.configure("Warning.TLabel", background=pal.surface,
                    foreground=pal.warning)
    style.configure("Code.TLabel", background=pal.surface_alt,
                    foreground=pal.text_muted, font=font_of(9))

    # Group boxes.
    style.configure("TLabelframe", background=pal.surface,
                    bordercolor=pal.border, borderwidth=1, relief="solid")
    style.configure("TLabelframe.Label", background=pal.surface,
                    foreground=pal.text, font=font_of(10, "bold"))

    # Buttons.
    style.configure("TButton", background=pal.surface_alt, foreground=pal.text,
                    bordercolor=pal.border, borderwidth=1, relief="flat",
                    padding=(12, 7), focusthickness=0)
    style.map("TButton",
              background=[("pressed", pal.border), ("active", pal.border)],
              foreground=[("disabled", pal.text_muted)])
    style.configure("Accent.TButton", background=pal.accent,
                    foreground=pal.accent_text, bordercolor=pal.accent,
                    borderwidth=1, relief="flat", padding=(16, 8),
                    font=font_of(10, "bold"))
    style.map("Accent.TButton",
              background=[("pressed", pal.accent_hover),
                          ("active", pal.accent_hover),
                          ("disabled", pal.surface_alt)],
              foreground=[("disabled", pal.text_muted)],
              bordercolor=[("disabled", pal.border)])
    style.configure("Ghost.TButton", background=pal.surface,
                    foreground=pal.text_muted, bordercolor=pal.surface,
                    borderwidth=1, relief="flat", padding=(10, 5))
    style.map("Ghost.TButton",
              background=[("active", pal.surface_alt), ("pressed", pal.surface_alt)],
              foreground=[("active", pal.text), ("disabled", pal.text_muted)])
    style.configure("Danger.TButton", background=pal.surface,
                    foreground=pal.danger, bordercolor=pal.danger,
                    borderwidth=1, relief="flat", padding=(12, 7))
    style.map("Danger.TButton",
              background=[("active", pal.surface_alt), ("pressed", pal.surface_alt)],
              foreground=[("disabled", pal.text_muted)],
              bordercolor=[("disabled", pal.border)])
    style.configure("Chip.TButton", background=pal.surface_alt,
                    foreground=pal.text_muted, bordercolor=pal.border,
                    borderwidth=1, relief="flat", padding=(6, 3),
                    font=font_of(9))
    style.map("Chip.TButton",
              background=[("active", pal.border), ("pressed", pal.border)],
              foreground=[("active", pal.text), ("disabled", pal.text_muted)])

    # Entries and combos.
    style.configure("TEntry", fieldbackground=pal.surface_alt,
                    foreground=pal.text, bordercolor=pal.border,
                    lightcolor=pal.border, darkcolor=pal.border,
                    insertcolor=pal.text, borderwidth=1, relief="solid",
                    padding=5)
    style.map("TEntry",
              bordercolor=[("focus", pal.accent)],
              lightcolor=[("focus", pal.accent)],
              darkcolor=[("focus", pal.accent)],
              fieldbackground=[("readonly", pal.surface_alt),
                               ("disabled", pal.surface_alt)],
              foreground=[("disabled", pal.text_muted)])
    style.configure("TCombobox", fieldbackground=pal.surface_alt,
                    foreground=pal.text, background=pal.surface_alt,
                    bordercolor=pal.border, lightcolor=pal.border,
                    darkcolor=pal.border, arrowcolor=pal.text_muted,
                    arrowsize=13, borderwidth=1, relief="solid", padding=4)
    style.map("TCombobox",
              fieldbackground=[("readonly", pal.surface_alt)],
              bordercolor=[("focus", pal.accent)],
              lightcolor=[("focus", pal.accent)],
              darkcolor=[("focus", pal.accent)],
              arrowcolor=[("disabled", pal.text_muted)])

    # Radio and check buttons. The clam theme draws the indicator with
    # -indicatorbackground and the mark with -indicatorforeground; a
    # check button that is on is in the "selected" state and one that
    # is off in the "alternate" state.
    style.configure("TRadiobutton", background=pal.surface,
                    foreground=pal.text, focuscolor="",
                    indicatorbackground=pal.surface_alt,
                    indicatorforeground=pal.accent)
    style.map("TRadiobutton",
              background=[("active", pal.surface)],
              foreground=[("disabled", pal.text_muted)],
              indicatorbackground=[("selected", pal.accent),
                                   ("alternate", pal.surface_alt),
                                   ("pressed", pal.border)],
              indicatorforeground=[("selected", pal.accent_text),
                                   ("disabled", pal.text_muted)])
    style.configure("TCheckbutton", background=pal.surface,
                    foreground=pal.text, focuscolor="",
                    indicatorbackground=pal.surface_alt,
                    indicatorforeground=pal.text)
    style.map("TCheckbutton",
              background=[("active", pal.surface)],
              foreground=[("disabled", pal.text_muted)],
              indicatorbackground=[("selected", pal.accent),
                                   ("alternate", pal.surface_alt),
                                   ("pressed", pal.border)],
              indicatorforeground=[("selected", pal.accent_text),
                                   ("disabled", pal.text_muted)])
    style.configure("Bg.TRadiobutton", background=pal.bg, foreground=pal.text)
    style.map("Bg.TRadiobutton", background=[("active", pal.bg)])
    style.configure("Bg.TCheckbutton", background=pal.bg, foreground=pal.text)
    style.map("Bg.TCheckbutton", background=[("active", pal.bg)])

    # Notebook.
    style.configure("TNotebook", background=pal.bg, borderwidth=0,
                    tabmargins=(0, 0, 0, 0))
    style.configure("TNotebook.Tab", background=pal.surface_alt,
                    foreground=pal.text_muted, borderwidth=0, padding=(14, 7))
    style.map("TNotebook.Tab",
              background=[("selected", pal.surface), ("active", pal.surface)],
              foreground=[("selected", pal.text), ("active", pal.text)])

    # Tree view.
    style.configure("Treeview", background=pal.surface,
                    fieldbackground=pal.surface, foreground=pal.text,
                    bordercolor=pal.border, borderwidth=1, relief="solid",
                    rowheight=26)
    style.map("Treeview",
              background=[("selected", pal.selection)],
              foreground=[("selected", pal.text)])
    style.configure("Treeview.Heading", background=pal.surface_alt,
                    foreground=pal.text_muted, bordercolor=pal.border,
                    borderwidth=0, relief="flat", padding=(8, 6),
                    font=font_of(9, "bold"))
    style.map("Treeview.Heading", background=[("active", pal.border)])

    # Scrollbars.
    style.configure("Vertical.TScrollbar", background=pal.surface_alt,
                    troughcolor=pal.bg, bordercolor=pal.bg, arrowcolor=pal.text_muted,
                    borderwidth=0, relief="flat", arrowsize=13)
    style.configure("Horizontal.TScrollbar", background=pal.surface_alt,
                    troughcolor=pal.bg, bordercolor=pal.bg, arrowcolor=pal.text_muted,
                    borderwidth=0, relief="flat", arrowsize=13)

    # Separators and panes.
    style.configure("TSeparator", background=pal.border)
    style.configure("TPanedwindow", background=pal.bg)
    style.configure("Sash", sashthickness=6, gripcount=0)

    # Progress and scale.
    style.configure("TProgressbar", background=pal.surface_alt,
                    troughcolor=pal.surface_alt, bordercolor=pal.border,
                    lightcolor=pal.accent, darkcolor=pal.accent)
    style.configure("Accent.Horizontal.TProgressbar", background=pal.accent,
                    troughcolor=pal.surface_alt)

    # The code / preview text.
    style.configure("Code.TText", background=pal.surface_alt,
                    foreground=pal.text, fieldbackground=pal.surface_alt,
                    borderwidth=0, relief="flat", insertcolor=pal.text,
                    spacing1=4, spacing3=4)

    root.option_add("*tearOff", False)
    return pal


def mono_font(root, size=10):
    """A monospace font for the preview pane."""
    family = _pick_family(root, ["JetBrains Mono", "Cascadia Mono",
                                 "Noto Sans Mono", "DejaVu Sans Mono",
                                 "Liberation Mono"], "TkFixedFont")
    return tkfont.Font(family=family, size=size)


def scale_font(font, factor):
    """A copy of `font` scaled by `factor`."""
    new = font.copy()
    new.configure(size=max(6, int(round(font.cget("size") * factor))))
    return new
