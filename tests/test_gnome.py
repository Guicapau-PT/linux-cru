"""GNOME / gdctl backend tests.

The parser is the interesting part, and it is exercised against a
frozen sample of `gdctl show --verbose`.  No gdctl is run by these
tests.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from linux_cru import gnome

SAMPLE = open(os.path.join(os.path.dirname(__file__),
                           "fixtures", "gnome_show.txt")).read()


class _Result:
    def __init__(self, rc=0, stdout="", stderr=""):
        self.returncode = rc
        self.stdout = stdout
        self.stderr = stderr


def test_parse_show_reads_the_output():
    gnome.reset()
    state = gnome.parse_show(SAMPLE)
    assert len(state.monitors) == 1
    monitor = state.monitors[0]
    assert monitor.name == "DP-2"
    assert monitor.display_name == "AOC 34\""
    assert monitor.min_refresh_rate == 48
    assert len(monitor.modes) == 27
    assert monitor.current_mode_id == "3440x1440@99.982"
    assert monitor.current_mode.width == 3440
    assert monitor.current_mode.refresh == 99.982


def test_parse_show_reads_the_logical_layout():
    gnome.reset()
    state = gnome.parse_show(SAMPLE)
    assert len(state.logical_monitors) == 1
    logical = state.logical_monitors[0]
    assert logical.monitors == ["DP-2"]
    assert logical.scale == 1.25
    assert logical.transform == "normal"
    assert logical.primary is True
    assert logical.x == 0 and logical.y == 0


def test_mode_entries_dedupe_and_mark_current():
    gnome.reset()
    gnome._run = lambda args, timeout=12: _Result(0, SAMPLE, "")
    gnome.invalidate()
    # Force the cache to hold the parsed sample.
    gnome._cache["state"] = gnome.parse_show(SAMPLE)
    gnome._cache["at"] = __import__("time").monotonic()
    entries = gnome.mode_entries("DP-2")
    assert entries[0]["current"] is True
    assert entries[0]["width"] == 3440
    # 27 modes, no duplicate ids.
    assert len({entry["id"] for entry in entries}) == len(entries)


def test_current_mode():
    gnome.reset()
    gnome._cache["state"] = gnome.parse_show(SAMPLE)
    gnome._cache["at"] = __import__("time").monotonic()
    assert gnome.current_mode("DP-2") == (3440, 1440, 99.982)


def test_layout_args_preserve_the_layout():
    gnome.reset()
    state = gnome.parse_show(SAMPLE)
    args = gnome._layout_args(state)
    assert args[:3] == ["set", "--logical-monitor", "--primary"]
    assert "--monitor" in args
    assert "--mode" in args
    assert "3440x1440@99.982" in args
    assert "--scale" in args
    assert "1.25" in args


def test_set_mode_uses_the_matching_mode():
    gnome.reset()
    gnome._cache["state"] = gnome.parse_show(SAMPLE)
    gnome._cache["at"] = __import__("time").monotonic()

    calls = []

    def fake_run(args, timeout=12):
        calls.append(args)
        if args and args[0] == "show":
            return _Result(0, SAMPLE, "")
        return _Result(0, "", "")

    gnome._run = fake_run
    ok, message = gnome.set_mode("DP-2", 3440, 1440, 99.982)
    assert ok, message
    set_calls = [call for call in calls if call and call[0] == "set"]
    assert set_calls
    assert "3440x1440@99.982" in set_calls[0]


def test_set_mode_reports_a_missing_mode():
    gnome.reset()
    gnome._cache["state"] = gnome.parse_show(SAMPLE)
    gnome._cache["at"] = __import__("time").monotonic()

    def fake_run(args, timeout=12):
        if args and args[0] == "show":
            return _Result(0, SAMPLE, "")
        return _Result(1, "", "no such mode")

    gnome._run = fake_run
    ok, message = gnome.set_mode("DP-2", 1234, 5678, 90)
    assert not ok
    assert "does not have" in message


def test_set_scale_rejects_an_unsupported_scale():
    gnome.reset()

    def fake_run(args, timeout=12):
        if args and args[0] == "show":
            return _Result(0, SAMPLE, "")
        return _Result(0, "", "")

    gnome._run = fake_run
    ok, message = gnome.set_scale("DP-2", 1.7, persistent=False)
    assert not ok
    assert "does not offer" in message


def test_vrr_unavailable_on_this_sample():
    gnome.reset()
    gnome._cache["state"] = gnome.parse_show(SAMPLE)
    gnome._cache["at"] = __import__("time").monotonic()
    assert gnome.supports_vrr("DP-2") is False
    assert gnome.vrr_policy("DP-2") == "never"


def test_settings_round_trip():
    gnome.reset()
    gnome._cache["state"] = gnome.parse_show(SAMPLE)
    gnome._cache["at"] = __import__("time").monotonic()
    result = gnome.settings("DP-2")
    assert result["scale"] == 1.25
    assert result["rotation"] == "normal"
    assert result["refresh"] == 99.982
    assert result["vrr"] is False


if __name__ == "__main__":
    test_parse_show_reads_the_output()
    test_parse_show_reads_the_logical_layout()
    test_mode_entries_dedupe_and_mark_current()
    test_current_mode()
    test_layout_args_preserve_the_layout()
    test_set_mode_uses_the_matching_mode()
    test_set_mode_reports_a_missing_mode()
    test_set_scale_rejects_an_unsupported_scale()
    test_vrr_unavailable_on_this_sample()
    test_settings_round_trip()
    print("gnome tests passed")