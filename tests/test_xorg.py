"""The X11 route: the xorg.conf snippet and the scripts that install it."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from linux_cru import timings, xorg


def _modeline():
    return timings.calc(2560, 1440, 144, "cvt-rb2")


def test_config_has_the_monitor_section():
    ml = _modeline()
    conf = xorg.build_config("DP-1", "2560x1440_144", ml,
                             timestamp="2026-01-01 00:00:00")
    assert 'Section "Monitor"' in conf
    assert 'Identifier "DP-1"' in conf
    assert ml.xorg_modeline("2560x1440_144") in conf
    assert 'Option "PreferredMode" "2560x1440_144"' in conf
    assert "2026-01-01 00:00:00" in conf
    # no NVIDIA section without an NVIDIA GPU
    assert "ModeValidation" not in conf


def test_nvidia_flags_only_when_asked_for():
    ml = _modeline()
    plain = xorg.build_config("DP-1", "m", ml, nvidia=True)
    assert 'Option "ModeValidation" "AllowNonEdidModes"' in plain
    assert "NoMaxPClkCheck" not in plain

    relaxed = xorg.build_config("DP-1", "m", ml, nvidia=True,
                                 relax_validation=True)
    for flag in xorg.NVIDIA_RELAX_FLAGS:
        assert flag in relaxed, f"{flag} missing"


def test_customedid_points_at_the_installed_file():
    ml = _modeline()
    conf = xorg.build_config("DP-0", "m", ml, nvidia=True,
                             customedid=xorg.customedid_path("DP-0"))
    assert 'Option "CustomEDID" "DP-0:/etc/X11/linux-cru-DP-0.bin"' in conf


def test_install_script_copies_both_files():
    script = xorg.build_install_script("/tmp/cru.conf", "/tmp/cru.bin",
                                       "/etc/X11/linux-cru-DP-0.bin")
    assert "install -m 644 '/tmp/cru.conf' '/etc/X11/xorg.conf.d/10-linux-cru.conf'" in script
    assert "install -m 644 '/tmp/cru.bin' '/etc/X11/linux-cru-DP-0.bin'" in script


def test_install_script_without_the_edid():
    script = xorg.build_install_script("/tmp/cru.conf")
    assert "linux-cru-DP-0.bin" not in script


def test_uninstall_script_removes_everything():
    one = xorg.build_uninstall_script("DP-0")
    assert "rm -f '/etc/X11/xorg.conf.d/10-linux-cru.conf'" in one
    assert "rm -f '/etc/X11/linux-cru-DP-0.bin'" in one
    assert "*" not in one

    every = xorg.build_uninstall_script()
    assert "rm -f '/etc/X11'/linux-cru-*.bin" in every


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
