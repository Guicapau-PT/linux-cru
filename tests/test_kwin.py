"""The KDE backend: kscreenctl (Plasma 6.8 and newer).

A stub stands in for the real kscreenctl: it prints the same JSON and
records the commands it was given, so the command lines the tool
builds can be checked without a Plasma installation.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from kscreenctl_stub import Stub
from linux_cru import kwin


def test_reading_the_outputs():
    with Stub() as stub:
        assert kwin.available() is True
        outputs = kwin.outputs()
        assert len(outputs) == 1
        assert outputs[0]["name"] == "DP-1"


def test_the_modes_of_an_output():
    with Stub():
        modes = kwin.modes("DP-1")
        assert modes[0] == (3440, 1440, 99.982, True)
        assert modes[1] == (3440, 1440, 59.973, False)
        assert kwin.current_mode("DP-1") == (3440, 1440, 99.982)


def test_an_output_that_is_not_there():
    with Stub():
        assert kwin.output("HDMI-A-1") is None
        assert kwin.modes("HDMI-A-1") == []
        assert kwin.current_mode("HDMI-A-1") is None


def test_adding_a_mode_with_the_modeline():
    with Stub() as stub:
        ok, message = kwin.add_custom_mode(
            "DP-1", modeline="398.64 2560 2568 2600 2640 1440 1496 1504 1510 "
                             "+hsync -vsync")
        assert ok, message
        call = stub.calls()[-1]
        assert call.startswith("DP-1 add-custom-mode 398.64 2560 "), call
        assert call.endswith("+hsync -vsync"), call


def test_adding_a_mode_without_the_modeline():
    with Stub() as stub:
        ok, message = kwin.add_custom_mode("DP-1", width=2560, height=1440,
                                           refresh=100.0, reduced=True)
        assert ok, message
        call = stub.calls()[-1]
        assert call == "DP-1 add-custom-mode --reduced-blanking 2560x1440@100.0Hz", call


def test_finding_a_custom_mode():
    with Stub():
        assert kwin.find_custom_mode("DP-1", 2560, 1440, 100.0) == 0
        assert kwin.find_custom_mode("DP-1", 1280, 720, 60.0) is None


def test_the_mode_is_addressed_by_its_id():
    """Picking the id keeps a mode the display already has from being
    chosen instead of the custom one."""
    with Stub() as stub:
        ok, message = kwin.apply_mode(
            "DP-1", "398.64 2560 2568 2600 2640 1440 1496 1504 1510 "
                    "+hsync -vsync", 2560, 1440, 100.0)
        assert ok, message
        calls = stub.calls()
        assert any(c.startswith("DP-1 add-custom-mode 398.64 ") for c in calls), calls
        # The mode list is asked for, so the id can be used.
        assert "DP-1 list-modes" in calls, calls
        # And the mode is switched to by that id.
        assert "DP-1 set-mode 4" in calls, calls


def test_setting_the_mode_and_the_vrr_policy():
    with Stub() as stub:
        assert kwin.set_mode("DP-1", "2560x1440@100")[0] is True
        assert stub.calls()[-1] == "DP-1 set-mode 2560x1440@100"
        assert kwin.set_vrr_policy("DP-1", "always")[0] is True
        assert stub.calls()[-1] == "DP-1 set-vrr-policy always"
        assert kwin.remove_custom_mode("DP-1", 0)[0] is True
        assert stub.calls()[-1] == "DP-1 remove-custom-mode 0"


def test_a_failure_is_reported():
    with Stub(exit_code=1, stderr="Unknown output: DP-9"):
        ok, message = kwin.set_mode("DP-9", "1920x1080@60")
        assert not ok
        assert "Unknown output" in message, message


def test_the_help_text_says_whether_modelines_are_taken():
    with Stub():
        assert kwin.supports_modelines() is True
    with Stub(modelines=False):
        assert kwin.supports_modelines() is False


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
