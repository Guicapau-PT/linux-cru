"""The KDE backend: kscreenctl (Plasma 6.8 and newer).

A stub stands in for the real kscreenctl: it prints the same JSON and
records the commands it was given, so the command lines the tool
builds can be checked without a Plasma installation.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from kscreenctl_stub import OLD_STYLE_OUTPUTS, Stub
from linux_cru import kwin, timings, wayland


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
        # On 6.8 the JSON answer lists the custom modes with ids, so
        # the id is known without asking again.
        assert "DP-1 list-modes" not in calls, calls
        # And the mode is switched to by that id.
        assert "DP-1 set-mode 4" in calls, calls


def test_a_mode_without_an_id_is_searched_for():
    """Older answers hide the custom mode, and the mode list is read.

    The custom mode has an id only where 6.8 reports it. Against an
    answer that does not, the tool asks for the mode list and takes
    the id that appeared -- the one that was not in the list before
    the mode was added.
    """
    with Stub(outputs=OLD_STYLE_OUTPUTS) as stub:
        ok, message = kwin.apply_mode(
            "DP-1", "398.64 2560 2568 2600 2640 1440 1496 1504 1510 "
                    "+hsync -vsync", 2560, 1440, 100.0)
        assert ok, message
        calls = stub.calls()
        assert "DP-1 list-modes" in calls, calls
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


def test_the_modes_carry_what_is_known_about_them():
    """An entry knows its id and whether it is a custom mode."""
    with Stub():
        entries = kwin.mode_entries("DP-1")
        by_id = {entry["id"]: entry for entry in entries}
        assert by_id["1"]["current"] is True
        assert by_id["2"]["preferred"] is True
        assert by_id["1"]["custom"] is False
        # The mode Plasma was given by this tool is marked as a custom
        # one, though it appears in the list of modes like the others.
        assert by_id["4"]["custom"] is True


def test_the_mode_list_is_in_a_readable_order():
    """Largest first, faster rates first, and every timing once.

    The JSON comes out in the order 1, 10, 11 ... 2, 20 ... and
    reports one timing through every descriptor that reached the
    display; both are a worse list than the display settings show.
    """
    with Stub():
        modes = kwin.modes("DP-1")
        assert modes == [
            (3440, 1440, 99.982, True),
            (3440, 1440, 59.973, False),
            (2560, 1440, 100.0, False),
            (1920, 1080, 60.0, False),
        ], modes


def test_the_custom_mode_list_names_the_values():
    """list-custom-modes prints "index: WxH@rateHz"."""
    with Stub():
        found = kwin.custom_mode_list("DP-1")
        assert found == [(0, 2560, 1440, 100.0)], found


def test_the_display_settings_are_read_and_set():
    """The settings come with the JSON answer, and each is set alone."""
    with Stub() as stub:
        settings = kwin.settings("DP-1")
        assert settings["scale"] == 1.0
        assert settings["rotation"] == "normal"
        assert settings["vrrPolicy"] == "automatic"

        assert kwin.set_hdr("DP-1", True)[0] is True
        assert stub.calls()[-1] == "DP-1 set-hdr true"
        assert kwin.set_wcg("DP-1", True)[0] is True
        assert stub.calls()[-1] == "DP-1 set-wcg true"
        assert kwin.set_maxbpc("DP-1", 10)[0] is True
        assert stub.calls()[-1] == "DP-1 set-maxbpc 10"
        # kscreenctl takes the scale as a percent.
        assert kwin.set_scale("DP-1", "150%")[0] is True
        assert stub.calls()[-1] == "DP-1 set-scale 150%"
        assert kwin.set_rotation("DP-1", "90")[0] is True
        assert stub.calls()[-1] == "DP-1 set-rotation 90"


def test_a_mode_that_is_in_use_is_not_applied_again():
    """Asking for the mode the display runs must not touch it.

    The compositor would take the picture apart only to put it back
    the way it was, and the tool would call that a change.
    """
    ml = timings.parse_modeline(
        "398.60 3440 3456 3480 3548 1440 1444 1448 1476 -hsync +vsync")
    with Stub() as stub:
        # 99.98 is what the display runs; kscreenctl reports 99.982.
        ok, message = wayland.kwin_apply_custom_mode(
            "DP-1", ml, 3440, 1440, 99.98, True)
        assert ok, message
        calls = stub.calls()
        assert not any("add-custom-mode" in c for c in calls), calls
        assert not any("set-mode" in c for c in calls), calls


def test_a_mode_that_is_not_in_use_is_applied():
    with Stub() as stub:
        ml = timings.parse_modeline(
            "341.92 3440 3456 3480 3548 1440 1444 1448 1476 -hsync +vsync")
        ok, message = wayland.kwin_apply_custom_mode(
            "DP-1", ml, 3440, 1440, 59.973, True)
        assert ok, message
        assert any("add-custom-mode" in c for c in stub.calls()), stub.calls()


def test_a_mode_that_cannot_be_used_is_taken_away_again():
    """The driver may refuse the mode.

    The custom mode must not be left behind: one the driver cannot
    produce makes every configuration apply fail, and the desktop says
    only that the driver rejected it.
    """
    with Stub(fail_on="set-mode") as stub:
        ok, message = kwin.apply_mode(
            "DP-1", "398.64 2560 2568 2600 2640 1440 1496 1504 1510 "
                    "+hsync -vsync", 2560, 1440, 100.0)
        assert not ok
        assert "rejected" in message, message
        calls = stub.calls()
        assert any(c.startswith("DP-1 remove-custom-mode") for c in calls), calls


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
