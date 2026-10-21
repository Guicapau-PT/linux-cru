"""The runtime EDID override: the script it runs and the waiting it does."""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from linux_cru import override, wayland


def test_the_test_script_arms_the_revert():
    script = override.build_test_script("card1", "DP-2", "/tmp/cru.bin", 35)
    assert "edid_override" in script
    assert "trigger_hotplug" in script
    assert "HOLD=35" in script
    assert "linux-cru-keep-DP-2" in script
    # the watchdog is owned by PID 1, so it survives the GUI
    assert "systemd-run" in script or "setsid" in script


def test_the_test_script_re_detects_the_connector():
    """A hotplug event alone is not enough: the driver has to read the
    EDID again and rebuild the connector's mode list."""
    script = override.build_test_script("card1", "DP-2", "/tmp/cru.bin", 35)
    assert "echo detect > \"$SYSFS/status\"" in script
    assert "echo 1 > \"$DBG/trigger_hotplug\"" in script
    # the detect comes first, the hotplug event after it
    assert script.index("echo detect") < script.index("echo 1 > \"$DBG/trigger_hotplug\"")


def test_the_revert_script_resets_the_override():
    script = override.build_revert_script("card1", "DP-2")
    # "reset" must not have a trailing newline
    assert "printf reset >" in script
    assert "echo reset" not in script


def test_waiting_for_the_compositor():
    """The compositor has to list the mode, not just the kernel."""
    from linux_cru.detect import Environment

    env = Environment(session_type="wayland", compositor="cosmic")
    calls = []

    def fake_output_modes(env, output):
        calls.append(output)
        # The compositor only learns about the mode on the third look.
        if len(calls) < 3:
            return [(3440, 1440, 99.982, True)]
        return [(3440, 1440, 99.982, True), (2560, 1440, 100.0, False)]

    import linux_cru.detect as detect_module
    real = detect_module.output_modes
    detect_module.output_modes = fake_output_modes
    try:
        assert wayland.wait_for_output_mode(env, "DP-2", 2560, 1440,
                                            timeout=5.0) is True
        assert len(calls) >= 3, calls
    finally:
        detect_module.output_modes = real


def test_waiting_for_the_compositor_gives_up():
    from linux_cru.detect import Environment

    env = Environment(session_type="wayland", compositor="cosmic")
    import linux_cru.detect as detect_module
    real = detect_module.output_modes
    detect_module.output_modes = lambda env, output: [(3440, 1440, 99.982, True)]
    try:
        start = time.monotonic()
        assert wayland.wait_for_output_mode(env, "DP-2", 2560, 1440,
                                            timeout=1.0) is False
        assert time.monotonic() - start < 4.0
    finally:
        detect_module.output_modes = real


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
