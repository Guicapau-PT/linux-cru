"""The KDE backend: kscreenctl (Plasma 6.8 and newer).

A fake kscreenctl stands in for the real one: it prints the same JSON
and records the commands it was given, so the command lines the tool
builds can be checked without a Plasma installation.
"""

import json
import os
import stat
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from linux_cru import kwin

OUTPUTS = [
    {
        "name": "DP-1",
        "uuid": "1-2",
        "enabled": True,
        "connected": True,
        "scale": 1.0,
        "primary": True,
        "vrrPolicy": "automatic",
        "capabilities": ["vrr", "custom-modes", "hdr"],
        "mode": {"id": "1", "resolution": {"width": 3440, "height": 1440},
                 "refreshRate": 99.982, "current": True, "preferred": False},
        "modes": [
            {"id": "1", "resolution": {"width": 3440, "height": 1440},
             "refreshRate": 99.982, "current": True, "preferred": False},
            {"id": "2", "resolution": {"width": 3440, "height": 1440},
             "refreshRate": 59.973, "current": False, "preferred": True},
            {"id": "3", "resolution": {"width": 1920, "height": 1080},
             "refreshRate": 60.0, "current": False, "preferred": False},
        ],
        "customModes": [
            {"resolution": {"width": 2560, "height": 1440},
             "refreshRate": 100.0},
        ],
    },
]


def _fake_kscreenctl(directory, exit_code=0, stderr=""):
    """A kscreenctl that answers like the real one and records calls."""
    log = os.path.join(directory, "calls.log")
    script = f"""#!/bin/bash
echo "$@" >> {log}
case "$*" in
    "list-outputs --json")
        cat <<'JSON'
{json.dumps(OUTPUTS)}
JSON
        ;;
    *" list-modes")
        echo "1:3440x1440@99.98*"
        echo "2:3440x1440@59.97!"
        echo "3:1920x1080@60.00"
        echo "4:2560x1440@100.00"
        ;;
    help)
        echo "Add a custom mode. A custom mode can be specified using either"
        echo "the resolution and the refresh rate or CVT timings."
        ;;
esac
"""
    if stderr:
        script += f'echo "{stderr}" >&2\n'
    script += f"exit {exit_code}\n"
    path = os.path.join(directory, "kscreenctl")
    with open(path, "w") as f:
        f.write(script)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return log


class _Sandbox:
    def __init__(self, exit_code=0, stderr=""):
        self.dir = tempfile.mkdtemp(prefix="cru-kwin-")
        self.log = _fake_kscreenctl(self.dir, exit_code, stderr)
        self._path = os.environ.get("PATH", "")
        os.environ["PATH"] = self.dir + os.pathsep + self._path

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [line.strip() for line in f if line.strip()]

    def close(self):
        os.environ["PATH"] = self._path


def test_reading_the_outputs():
    box = _Sandbox()
    try:
        assert kwin.available() is True
        outputs = kwin.outputs()
        assert len(outputs) == 1
        assert outputs[0]["name"] == "DP-1"
    finally:
        box.close()


def test_the_modes_of_an_output():
    box = _Sandbox()
    try:
        modes = kwin.modes("DP-1")
        assert modes[0] == (3440, 1440, 99.982, True)
        assert modes[1] == (3440, 1440, 59.973, False)
        assert kwin.current_mode("DP-1") == (3440, 1440, 99.982)
    finally:
        box.close()


def test_an_output_that_is_not_there():
    box = _Sandbox()
    try:
        assert kwin.output("HDMI-A-1") is None
        assert kwin.modes("HDMI-A-1") == []
        assert kwin.current_mode("HDMI-A-1") is None
    finally:
        box.close()


def test_adding_a_mode_with_the_modeline():
    box = _Sandbox()
    try:
        ok, message = kwin.add_custom_mode(
            "DP-1", modeline="398.64 2560 2568 2600 2640 1440 1496 1504 1510 "
                             "+hsync -vsync")
        assert ok, message
        call = box.calls()[-1]
        assert call.startswith("DP-1 add-custom-mode 398.64 2560 "), call
        assert call.endswith("+hsync -vsync"), call
    finally:
        box.close()


def test_adding_a_mode_without_the_modeline():
    box = _Sandbox()
    try:
        ok, message = kwin.add_custom_mode("DP-1", width=2560, height=1440,
                                           refresh=100.0, reduced=True)
        assert ok, message
        call = box.calls()[-1]
        assert call == "DP-1 add-custom-mode --reduced-blanking 2560x1440@100.0Hz", call
    finally:
        box.close()


def test_finding_a_custom_mode():
    box = _Sandbox()
    try:
        assert kwin.find_custom_mode("DP-1", 2560, 1440, 100.0) == 0
        assert kwin.find_custom_mode("DP-1", 1280, 720, 60.0) is None
    finally:
        box.close()


def test_the_mode_is_addressed_by_its_id():
    """Picking the id keeps a mode the display already has from being
    chosen instead of the custom one."""
    box = _Sandbox()
    try:
        ok, message = kwin.apply_mode(
            "DP-1", "398.64 2560 2568 2600 2640 1440 1496 1504 1510 "
                    "+hsync -vsync", 2560, 1440, 100.0)
        assert ok, message
        calls = box.calls()
        assert any(c.startswith("DP-1 add-custom-mode 398.64 ") for c in calls), calls
        # The mode list is asked for, so the id can be used.
        assert "DP-1 list-modes" in calls, calls
        # And the mode is switched to by that id.
        assert "DP-1 set-mode 4" in calls, calls
    finally:
        box.close()


def test_setting_the_mode_and_the_vrr_policy():
    box = _Sandbox()
    try:
        assert kwin.set_mode("DP-1", "2560x1440@100")[0] is True
        assert box.calls()[-1] == "DP-1 set-mode 2560x1440@100"
        assert kwin.set_vrr_policy("DP-1", "always")[0] is True
        assert box.calls()[-1] == "DP-1 set-vrr-policy always"
        assert kwin.remove_custom_mode("DP-1", 0)[0] is True
        assert box.calls()[-1] == "DP-1 remove-custom-mode 0"
    finally:
        box.close()


def test_a_failure_is_reported():
    box = _Sandbox(exit_code=1, stderr="Unknown output: DP-9")
    try:
        ok, message = kwin.set_mode("DP-9", "1920x1080@60")
        assert not ok
        assert "Unknown output" in message, message
    finally:
        box.close()


def test_the_help_text_says_whether_modelines_are_taken():
    box = _Sandbox()
    try:
        assert kwin.supports_modelines() is True
    finally:
        box.close()


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
