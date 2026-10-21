"""The interface itself: it builds, it reacts, and it says the right thing.

Needs a display; CI runs the tests under Xvfb.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import tkinter as tk

from kscreenctl_stub import Stub
from linux_cru import app, cosmic, kwin, persist, privileged, theme, xorg
from linux_cru.detect import Environment, GPU, Connector
from linux_cru import detect as dm


COSMIC_OUTPUTS = [
    cosmic.CosmicOutput(
        name="DP-2", make="PNP(AOC)", model="U34E2M", width_mm=800,
        height_mm=330,
        modes=[cosmic.CosmicMode(3440, 1440, 99982, current=True),
               cosmic.CosmicMode(3440, 1440, 59973, preferred=True),
               cosmic.CosmicMode(1920, 1080, 60000)]),
]


def _cosmic_env():
    return Environment(
        session_type="wayland", desktop="COSMIC", compositor="cosmic",
        gpus=[GPU("card1", "nvidia")], kernel_release="6.12.0-cachyos",
        connectors=[Connector("card1", "DP-2", "connected", "enabled", True)])


def _gui(env):
    real = dm.detect
    dm.detect = lambda: env
    try:
        root = tk.Tk()
        root.withdraw()
        return root, app.LinuxCRU(root)
    finally:
        dm.detect = real


def test_the_window_builds_and_shows_a_preview():
    root, g = _gui(_cosmic_env())
    try:
        assert g.preview_text.get("1.0", "end").strip(), "no preview"
        assert g.status_var.get(), "no status"
        assert g.method_var.get() == "EDID override", g.method_var.get()
    finally:
        root.destroy()


def test_the_mode_list_comes_from_the_compositor():
    real = cosmic.outputs
    cosmic.outputs = lambda: COSMIC_OUTPUTS
    try:
        root, g = _gui(_cosmic_env())
        try:
            rows = g.mode_tree.get_children()
            assert len(rows) == 3, rows
            first = g.mode_tree.item(rows[0], "values")
            assert first[0].startswith("3440x1440"), first
            assert "active" in first[0]
            assert first[1] == "99.982 Hz"
        finally:
            root.destroy()
    finally:
        cosmic.outputs = real


def test_clicking_a_mode_fills_the_fields():
    real = cosmic.outputs
    cosmic.outputs = lambda: COSMIC_OUTPUTS
    try:
        root, g = _gui(_cosmic_env())
        try:
            rows = g.mode_tree.get_children()
            g.mode_tree.selection_set(rows[2])
            g.on_mode_selected()
            assert g.width_var.get() == "1920"
            assert g.height_var.get() == "1080"
            assert g.refresh_var.get() == "60"
        finally:
            root.destroy()
    finally:
        cosmic.outputs = real


def test_bad_input_is_explained_not_crashed():
    root, g = _gui(_cosmic_env())
    try:
        g.width_var.set("wide")
        assert "whole number" in g.info_var.get(), g.info_var.get()
        g.width_var.set("1920")
        g.height_var.set("1080")
        g.refresh_var.set("fast")
        assert "number" in g.info_var.get(), g.info_var.get()
    finally:
        root.destroy()


def test_the_theme_can_be_switched():
    root, g = _gui(_cosmic_env())
    try:
        before = g.palette.name
        g.toggle_theme()
        assert g.palette.name != before
        g.toggle_theme()
        assert g.palette.name == before
    finally:
        root.destroy()


def test_the_removed_button_follows_what_is_installed():
    root, g = _gui(_cosmic_env())
    try:
        from linux_cru import persist
        real_edid = persist.installed_connectors
        real_xorg = xorg.installed
        persist.installed_connectors = lambda: []
        xorg.installed = lambda: False
        try:
            g.refresh_remove_button()
            assert "disabled" in g.remove_btn.state()

            persist.installed_connectors = lambda: ["DP-2"]
            g.refresh_remove_button()
            assert "disabled" not in g.remove_btn.state()

            persist.installed_connectors = lambda: []
            xorg.installed = lambda: True
            g.refresh_remove_button()
            assert "disabled" not in g.remove_btn.state()
        finally:
            persist.installed_connectors = real_edid
            xorg.installed = real_xorg
    finally:
        root.destroy()


def test_the_theme_follows_the_desktop():
    assert theme.detect_theme("KDE") in ("dark", "light")
    saved = os.environ.get("LINUX_CRU_THEME")
    os.environ["LINUX_CRU_THEME"] = "light"
    try:
        assert theme.detect_theme("COSMIC") == "light"
    finally:
        if saved is None:
            os.environ.pop("LINUX_CRU_THEME", None)
        else:
            os.environ["LINUX_CRU_THEME"] = saved


def test_plasma_6_8_takes_the_timings_as_they_are():
    """From 6.8 on, KWin gets the modeline instead of computing it."""
    env = Environment(session_type="wayland", desktop="KDE", compositor="kwin",
                      compositor_version=(6, 8, 1),
                      gpus=[GPU("card0", "amdgpu")],
                      connectors=[Connector("card0", "DP-1", "connected",
                                            "enabled", True)])
    with Stub():
        root, g = _gui(env)
        try:
            for standard in ("cvt", "cvt-rb", "cvt-rb2", "gtf"):
                g.standard_var.set(standard)
                g.generate_preview()
                assert g.use_edid_method() is False, \
                    f"{standard} should not need an EDID override on Plasma 6.8"
                assert "kscreenctl" in g.preview_text.get("1.0", "end")
        finally:
            root.destroy()


def test_plasma_6_8_works_on_the_nvidia_driver():
    """The exact timings are accepted by the driver, so no EDID override.

    Verified on Plasma 6.8.0 with the NVIDIA 615 driver: KWin hands the
    timings over and the display runs them.
    """
    env = Environment(session_type="wayland", desktop="KDE", compositor="kwin",
                      compositor_version=(6, 8, 0),
                      gpus=[GPU("card0", "nvidia")],
                      connectors=[Connector("card0", "DP-1", "connected",
                                            "enabled", True)])
    with Stub():
        root, g = _gui(env)
        try:
            g.standard_var.set("cvt-rb2")
            g.generate_preview()
            assert g.use_edid_method() is False, \
                "Plasma 6.8 hands the timings over, so no EDID override is needed"
            assert g.method_var.get() == "kwin native", g.method_var.get()
        finally:
            root.destroy()


def test_without_kscreenctl_the_edid_override_is_used():
    """Plasma 6.8 without kscreenctl cannot take the timings as they are."""
    env = Environment(session_type="wayland", desktop="KDE", compositor="kwin",
                      compositor_version=(6, 8, 0),
                      gpus=[GPU("card0", "amdgpu")],
                      connectors=[Connector("card0", "DP-1", "connected",
                                            "enabled", True)])
    real = kwin.available
    kwin.available = lambda: False
    try:
        root, g = _gui(env)
        try:
            g.standard_var.set("cvt-rb2")
            g.generate_preview()
            assert g.use_edid_method() is True, \
                "without kscreenctl the timings have to go in through the EDID"
        finally:
            root.destroy()
    finally:
        kwin.available = real


def test_plasma_6_6_only_takes_what_it_can_compute():
    env = Environment(session_type="wayland", desktop="KDE", compositor="kwin",
                      compositor_version=(6, 7, 4),
                      gpus=[GPU("card0", "amdgpu")],
                      connectors=[Connector("card0", "DP-1", "connected",
                                            "enabled", True)])
    root, g = _gui(env)
    try:
        g.standard_var.set("cvt")
        g.generate_preview()
        assert g.use_edid_method() is False
        assert "kscreen-doctor" in g.preview_text.get("1.0", "end")
        g.standard_var.set("cvt-rb2")
        g.generate_preview()
        assert g.use_edid_method() is True, \
            "CVT-RBv2 needs an EDID override before Plasma 6.8"
        assert g.method_var.get() == "EDID override", g.method_var.get()
    finally:
        root.destroy()


def test_x11_apply_writes_the_configuration():
    """The X11 route installs a snippet and can take it away again."""
    env = Environment(session_type="x11", compositor="x11",
                      gpus=[GPU("card0", "amdgpu")],
                      connectors=[Connector("card0", "DP-1", "connected",
                                            "enabled", True)])
    root, g = _gui(env)
    recorded = {}

    class _Result:
        ok = True
        message = "done"
        cancelled = False

    def fake_run_as_root(argv, ask_password=None):
        recorded["argv"] = argv
        recorded["script"] = open(argv[1]).read()
        return _Result()

    real = privileged.run_as_root
    privileged.run_as_root = fake_run_as_root
    try:
        g.display_var.set("DP-1")
        g.width_var.set("2560")
        g.height_var.set("1440")
        g.refresh_var.set("144")
        g.apply_configuration()

        assert "argv" in recorded, "nothing was installed"
        script = recorded["script"]
        assert "10-linux-cru.conf" in script
        assert "mkdir -p '/etc/X11/xorg.conf.d'" in script
    finally:
        privileged.run_as_root = real
        root.destroy()


def test_remove_takes_the_x11_configuration_away_again():
    env = Environment(session_type="x11", compositor="x11",
                      gpus=[GPU("card0", "amdgpu")])
    root, g = _gui(env)
    recorded = {}

    class _Result:
        ok = True
        message = "done"
        cancelled = False

    def fake_run_as_root(argv, ask_password=None):
        recorded.setdefault("scripts", []).append(open(argv[1]).read())
        return _Result()

    real = privileged.run_as_root
    real_installed = xorg.installed
    real_connectors = persist.installed_connectors
    privileged.run_as_root = fake_run_as_root
    xorg.installed = lambda: True
    # No EDID override installed, so only the X11 configuration is removed.
    persist.installed_connectors = lambda: []
    try:
        g.display_var.set("DP-1")
        g.remove_configuration()
        scripts = recorded.get("scripts", [])
        assert scripts, "nothing was removed"
        assert "rm -f '/etc/X11/xorg.conf.d/10-linux-cru.conf'" in scripts[0]
    finally:
        privileged.run_as_root = real
        xorg.installed = real_installed
        persist.installed_connectors = real_connectors
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
