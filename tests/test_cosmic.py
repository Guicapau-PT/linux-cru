"""COSMIC support: parsing what cosmic-randr reports, and the commands.

The parser is the interesting part: cosmic-randr has no query command
that returns JSON, so the KDL output is parsed here. The samples are
the real output of cosmic-randr 1.10.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from linux_cru import cosmic

SAMPLE = """output "DP-2" enabled=#true {
  description make="PNP(AOC)" model="U34E2M"
  physical 800 330
  position 0 0
  scale 1.25
  transform "normal"
  adaptive_sync_support #false
  adaptive_sync #false
  xwayland_primary #true
  serial_number "VRSP4HA001183"
  modes {
    mode 3440 1440 99982 current=#true
    mode 3440 1440 74983
    mode 3440 1440 59973 preferred=#true
    mode 1920 1080 60000
  }
}
output "HDMI-A-1" enabled=#false {
  description make="Acme" model="Monitor"
  physical 600 340
  modes {
    mode 1920 1080 60000
  }
}
"""


def test_parse_kdl_reads_the_outputs():
    outputs = cosmic.parse_kdl(SAMPLE)
    assert len(outputs) == 2, outputs

    dp2 = outputs[0]
    assert dp2.name == "DP-2"
    assert dp2.enabled is True
    assert dp2.make == "PNP(AOC)"
    assert dp2.model == "U34E2M"
    assert dp2.serial == "VRSP4HA001183"
    assert dp2.width_mm == 800 and dp2.height_mm == 330
    assert abs(dp2.scale - 1.25) < 0.001
    assert dp2.adaptive_sync_supported is False
    assert dp2.adaptive_sync is False
    assert "34" in dp2.physical_size

    hdmi = outputs[1]
    assert hdmi.name == "HDMI-A-1"
    assert hdmi.enabled is False


def test_parse_kdl_reads_the_modes():
    dp2 = cosmic.parse_kdl(SAMPLE)[0]
    assert len(dp2.modes) == 4
    first = dp2.modes[0]
    assert (first.width, first.height) == (3440, 1440)
    assert abs(first.refresh - 99.982) < 0.001
    assert first.current is True
    assert dp2.current_mode is first

    preferred = dp2.modes[2]
    assert preferred.preferred is True
    assert preferred.current is False


def test_parse_kdl_ignores_junk():
    assert cosmic.parse_kdl("") == []
    assert cosmic.parse_kdl("not an output at all") == []
    assert cosmic.parse_kdl('output "DP-1" {') == []      # no closing brace


def test_current_mode_of_a_missing_output():
    # An output that cosmic-randr does not know about has no mode.
    real = cosmic.outputs
    cosmic.outputs = lambda: []
    try:
        assert cosmic.current_mode("DP-9") is None
        assert cosmic.find("DP-9") is None
    finally:
        cosmic.outputs = real


def test_set_mode_builds_the_right_command():
    calls = []

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd):
        calls.append(cmd)
        return _Result()

    cosmic._run = fake_run
    cosmic.available = lambda: True
    try:
        ok, message = cosmic.set_mode("DP-2", 2560, 1440, 144)
        assert ok, message
        assert calls == [["cosmic-randr", "mode", "DP-2", "2560", "1440",
                          "--refresh", "144"]], calls
    finally:
        del cosmic._run
        del cosmic.available


def test_set_mode_reports_the_failure():
    class _Result:
        returncode = 1
        stdout = ""
        stderr = "configuration failed"

    cosmic._run = lambda cmd: _Result()
    cosmic.available = lambda: True
    try:
        ok, message = cosmic.set_mode("DP-2", 1340, 800)
        assert not ok
        assert "configuration failed" in message
    finally:
        del cosmic._run
        del cosmic.available


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
