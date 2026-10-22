"""A fake kscreenctl, for the tests.

Plasma is not installed everywhere the tests run, so the tool's
Plasma 6.8 path is exercised against a stub that answers like the
real one: the same JSON, the same mode lines, and a record of the
commands it was given. The answers the backend keeps between calls
are dropped as well, so every stub starts clean.
"""

import json
import os
import shutil
import stat
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# The display settings 6.8 reports with the mode list, for an SDR
# display that cannot show HDR.
DISPLAY_SETTINGS = {
    "enabled": True,
    "connected": True,
    "scale": 1.0,
    "primary": True,
    "rotation": "normal",
    "position": {"x": 0, "y": 0},
    "overscan": 0,
    "hdr": False,
    "wcg": False,
    "maxbpc": 8,
    "rgbRange": "Automatic",
    "sharpness": 0,
    "priority": 1,
    "brightness": 1.0,
    "dimming": 1.0,
    "autoBrightness": False,
    "abm": 0,
    "ddcCiAllowed": True,
    "edrPolicy": "always",
    "colorProfileSource": "sRGB",
    "colorPowerTradeoff": "prefer-efficiency",
    "replicaOf": 0,
    "hdrColorProfileSource": "EDID",
    "hdrIccProfilePath": "",
    "iccProfilePath": "",
    "vrrPolicy": "automatic",
    "capabilities": ["vrr", "custom-modes", "hdr"],
}


def _output(**changes):
    """A JSON output for the stub, with the usual settings."""
    out = dict(DISPLAY_SETTINGS)
    out.update(changes)
    return out


OUTPUTS = [
    _output(
        name="DP-1",
        uuid="1-2",
        mode={"id": "1", "resolution": {"width": 3440, "height": 1440},
              "refreshRate": 99.982, "current": True, "preferred": False},
        modes=[
            {"id": "1", "resolution": {"width": 3440, "height": 1440},
             "refreshRate": 99.982, "current": True, "preferred": False},
            {"id": "2", "resolution": {"width": 3440, "height": 1440},
             "refreshRate": 59.973, "current": False, "preferred": True},
            {"id": "3", "resolution": {"width": 1920, "height": 1080},
             "refreshRate": 60.0, "current": False, "preferred": False},
            # The same timing twice: the JSON reports 1920x1080@60 once
            # per timing descriptor, as the real one does.
            {"id": "12", "resolution": {"width": 1920, "height": 1080},
             "refreshRate": 60.0, "current": False, "preferred": False},
            # A custom mode on 6.8 has an id like the others and is
            # listed with them, which is how a mode that was just
            # added can be found and switched to.
            {"id": "4", "resolution": {"width": 2560, "height": 1440},
             "refreshRate": 100.0, "current": False, "preferred": False},
        ],
        customModes=[
            {"resolution": {"width": 2560, "height": 1440},
             "refreshRate": 100.0},
        ],
    ),
]

# An answer like the ones before 6.8: the custom mode is reported as
# a custom one, but is not in the list of modes and has no id there.
OLD_STYLE_OUTPUTS = [
    _output(
        name="DP-1",
        uuid="1-2",
        mode={"id": "1", "resolution": {"width": 3440, "height": 1440},
              "refreshRate": 99.982, "current": True, "preferred": False},
        modes=[
            {"id": "1", "resolution": {"width": 3440, "height": 1440},
             "refreshRate": 99.982, "current": True, "preferred": False},
            {"id": "2", "resolution": {"width": 3440, "height": 1440},
             "refreshRate": 59.973, "current": False, "preferred": True},
            {"id": "3", "resolution": {"width": 1920, "height": 1080},
             "refreshRate": 60.0, "current": False, "preferred": False},
        ],
        customModes=[
            {"resolution": {"width": 2560, "height": 1440},
             "refreshRate": 100.0},
        ],
    ),
]

SCRIPT = """#!/bin/bash
echo "$@" >> {log}
case "$*" in
    "list-outputs --json")
        cat <<'JSON'
{outputs}
JSON
        ;;
    *" list-modes")
        echo "1:3440x1440@99.98*"
        echo "2:3440x1440@59.97!"
        echo "3:1920x1080@60.00"
        echo "4:2560x1440@100.00"
        ;;
    *" list-custom-modes")
        echo "0: 2560x1440@100.00Hz"
        ;;
    help)
        echo "Add a custom mode. A custom mode can be specified using either"
        echo "the resolution and the refresh rate or CVT timings."
        ;;
esac
if [ -n "{fail_on}" ]; then
    case "$*" in
        *{fail_on}*) echo "{stderr}" >&2; exit 1;;
    esac
elif [ "{exit_code}" != "0" ]; then
    echo "{stderr}" >&2
fi
exit {exit_code}
"""


class Stub:
    """Installs a fake kscreenctl for the length of a `with`.

    The lookup is patched as well, so the stub is the only kscreenctl
    the tool can find, whether or not Plasma is installed.
    """

    def __init__(self, outputs=None, exit_code=0, stderr="",
                 modelines=True, fail_on=""):
        self.dir = tempfile.mkdtemp(prefix="cru-kscreenctl-")
        self.log = os.path.join(self.dir, "calls.log")
        script = SCRIPT.format(
            log=self.log,
            outputs=json.dumps(outputs if outputs is not None else OUTPUTS),
            stderr=stderr or "the driver rejected the output configuration",
            fail_on=fail_on,
            exit_code=exit_code,
        )
        if not modelines:
            script = script.replace("or CVT timings.", "or nothing else.")
        self.path = os.path.join(self.dir, "kscreenctl")
        with open(self.path, "w") as f:
            f.write(script)
        os.chmod(self.path, os.stat(self.path).st_mode | stat.S_IEXEC)
        self._path = os.environ.get("PATH", "")
        self._which = shutil.which

    def __enter__(self):
        os.environ["PATH"] = self.dir + os.pathsep + self._path
        real = self._which

        def which(cmd, *args, **kwargs):
            if cmd == "kscreenctl":
                return self.path
            return real(cmd, *args, **kwargs)

        shutil.which = which
        _forget_cached_answers()
        return self

    def __exit__(self, *exc):
        shutil.which = self._which
        os.environ["PATH"] = self._path
        _forget_cached_answers()
        return False

    def calls(self):
        """Every command the stub was given."""
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [line.strip() for line in f if line.strip()]


def _forget_cached_answers():
    """Drop what was read from the fake tool that just went away."""
    from linux_cru import kwin
    kwin.reset()
