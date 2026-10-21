"""NVIDIA X11 xorg.conf generation: default bypass flags and CustomEDID.

Loads the real GUI (needs a display; CI runs it under Xvfb) with a
faked NVIDIA X11 environment and checks the generated xorg.conf.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tkinter as tk

from linux_cru.detect import Environment, GPU, Connector
from linux_cru import app
from linux_cru import detect as dm


def _gui(env):
    real = dm.detect
    dm.detect = lambda: env
    try:
        root = tk.Tk()
        root.withdraw()
        return root, app.LinuxCRU(root)
    finally:
        dm.detect = real


def _nvidia_env():
    return Environment(
        session_type="x11", compositor="x11", gpus=[GPU("card0", "nvidia")],
        nvidia_version=(580, 126), kernel_version=(6, 17),
        connectors=[Connector("card0", "DP-1", "connected", "enabled", True)])


def test_nvidia_defaults_and_customedid():
    root, g = _gui(_nvidia_env())
    try:
        g.display_var.set("DP-0")      # X name, different from DRM DP-1
        assert g.relax_validation.get(), "bypass flags should default ON"
        assert g.nvidia_customedid.get(), "CustomEDID should default ON"

        g.width_var.set("1024"); g.height_var.set("768"); g.refresh_var.set("100")
        g.standard_var.set("gtf")
        name, ml = g.current_modeline()

        conf = g.build_xorg_config("DP-0", name, ml)
        for flag in ("AllowNonEdidModes", "NoEdidMaxPClkCheck", "NoMaxPClkCheck",
                     "NoMaxSizeCheck", "NoHorizSyncCheck", "NoVertRefreshCheck"):
            assert flag in conf, f"{flag} missing from ModeValidation"
        assert 'Option "CustomEDID" "DP-0:/etc/X11/linux-cru-DP-0.bin"' in conf
        # the dead IgnoreEDID option must never be emitted
        assert "IgnoreEDID" not in conf

        # unticking each removes its part
        g.nvidia_customedid.set(False)
        assert "CustomEDID" not in g.build_xorg_config("DP-0", name, ml)
        g.relax_validation.set(False)
        minimal = g.build_xorg_config("DP-0", name, ml)
        assert "AllowNonEdidModes" in minimal
        assert "NoMaxPClkCheck" not in minimal
    finally:
        root.destroy()


def test_non_nvidia_has_no_bypass_or_customedid():
    env = Environment(session_type="x11", compositor="x11",
                      gpus=[GPU("card0", "amdgpu")])
    root, g = _gui(env)
    try:
        assert g.relax_validation.get() is False
        assert g.nvidia_customedid.get() is False
        name, ml = g.current_modeline()
        conf = g.build_xorg_config("DP-1", name, ml)
        assert "ModeValidation" not in conf
        assert "CustomEDID" not in conf
    finally:
        root.destroy()


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if failures else 0)
