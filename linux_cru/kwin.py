"""KDE Plasma's own screen control tool.

Plasma 6.6 taught kscreen-doctor to add a custom mode from a width, a
height and a refresh rate, and the compositor computed the timings
itself with libxcvt. Plasma 6.8 brought **kscreenctl**, which takes a
full modeline instead: the timings that reach the display can be
exactly the ones that were calculated, and adding the mode applies it
in the same step.

The JSON that "kscreenctl list-outputs --json" prints has the same
shape the rest of the tool already knows from kscreen-doctor, with
more in it (capabilities, the VRR policy, the custom modes).

The command surface used here (Plasma 6.8):

    kscreenctl list-outputs --json
    kscreenctl <output> add-custom-mode <clock> <h...> <v...> [flags]
    kscreenctl <output> add-custom-mode --reduced-blanking <WxH@Hz>
    kscreenctl <output> remove-custom-mode <index>
    kscreenctl <output> list-modes
    kscreenctl <output> list-custom-modes
    kscreenctl <output> set-mode <id|WxH@RR|WxH>
    kscreenctl <output> set-vrr-policy never|always|automatic
"""

import json
import re
import shutil
import subprocess

from . import hostenv

KSCREENCTL = "kscreenctl"

# The mode line kscreenctl prints: "<id>:<width>x<height>@<refresh>",
# with a '*' on the mode that is in use and a '!' on the preferred one.
_MODE_RE = re.compile(
    r"^(?P<id>[^:]+):(?P<w>\d+)x(?P<h>\d+)@(?P<r>[\d.]+)(?P<flags>[!*]*)\s*$")


def available():
    """True when kscreenctl is installed (Plasma 6.8 and newer)."""
    return shutil.which(KSCREENCTL) is not None


def _run(args, timeout=15):
    try:
        return subprocess.run([KSCREENCTL] + list(args),
                              capture_output=True, universal_newlines=True,
                              timeout=timeout, env=hostenv.subprocess_env())
    except (OSError, subprocess.SubprocessError) as e:
        class _Failed:
            returncode = 1
            stdout = ""
            stderr = str(e)
        return _Failed()


def supports_modelines():
    """True when this kscreenctl takes a full modeline.

    The modeline form arrived with kscreenctl itself in Plasma 6.8,
    but the help text says so, and that is what is asked here rather
    than the version number.
    """
    res = _run(["help"], timeout=10)
    text = (res.stdout or "") + (res.stderr or "")
    return "CVT timings" in text


# -- reading -------------------------------------------------------------------

def outputs():
    """Every connected output, as the JSON kscreenctl describes it."""
    if not available():
        return []
    res = _run(["list-outputs", "--json"])
    if res.returncode != 0 or not res.stdout.strip():
        return []
    try:
        data = json.loads(res.stdout)
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def output(name):
    """The output called `name` (by name or by uuid), or None."""
    for out in outputs():
        if out.get("name") == name or out.get("uuid") == name:
            return out
    return None


def modes(name):
    """The modes an output offers, as (width, height, refresh, current)."""
    out = output(name)
    if not out:
        return []
    result = []
    for mode in out.get("modes", []):
        size = mode.get("resolution", {})
        result.append((int(size.get("width", 0)), int(size.get("height", 0)),
                       float(mode.get("refreshRate", 0)),
                       bool(mode.get("current"))))
    return result


def current_mode(name):
    """(width, height, refresh) in use on `name`, or None."""
    out = output(name)
    if not out:
        return None
    mode = out.get("mode")
    if not mode:
        return None
    size = mode.get("resolution", {})
    width = int(size.get("width", 0))
    height = int(size.get("height", 0))
    refresh = float(mode.get("refreshRate", 0))
    if width <= 0 or height <= 0 or refresh <= 0:
        return None
    return width, height, refresh


def custom_modes(name):
    """The custom modes of an output, as the JSON describes them."""
    out = output(name)
    if not out:
        return []
    return out.get("customModes", [])


def vrr_policy(name):
    """The adaptive sync policy of an output, or ''."""
    out = output(name)
    if not out:
        return ""
    return out.get("vrrPolicy", "")


def supports_vrr(name):
    """True when the output can do adaptive sync."""
    out = output(name)
    if not out:
        return False
    return "vrr" in out.get("capabilities", [])


# -- changing -------------------------------------------------------------------

def add_custom_mode(name, modeline=None, width=None, height=None,
                     refresh=None, reduced=False):
    """Add a custom mode to an output. Returns (ok, message).

    With a modeline the timings are exactly the ones given; without one
    the compositor computes them, and `reduced` asks for reduced
    blanking.
    """
    if not available():
        return False, "kscreenctl is not installed"

    args = [name, "add-custom-mode"]
    if modeline:
        args += modeline.split()
    else:
        if reduced:
            args.append("--reduced-blanking")
        args.append(f"{width}x{height}@{refresh:.1f}Hz")

    res = _run(args)
    if res.returncode == 0:
        return True, ""
    return False, (res.stderr or res.stdout or "kscreenctl failed").strip()


def remove_custom_mode(name, index):
    """Remove the custom mode at `index`. Returns (ok, message)."""
    if not available():
        return False, "kscreenctl is not installed"
    res = _run([name, "remove-custom-mode", str(index)])
    if res.returncode == 0:
        return True, ""
    return False, (res.stderr or res.stdout or "kscreenctl failed").strip()


def find_custom_mode(name, width, height, refresh):
    """The index of the custom mode with these timings, or None."""
    for index, mode in enumerate(custom_modes(name)):
        size = mode.get("resolution", {})
        if (int(size.get("width", 0)) == width
                and int(size.get("height", 0)) == height
                and abs(float(mode.get("refreshRate", 0)) - refresh) < 0.05):
            return index
    return None


def set_mode(name, query):
    """Switch an output to a mode. Returns (ok, message).

    The query is a mode id, "WxH@RR" or "WxH".
    """
    if not available():
        return False, "kscreenctl is not installed"
    res = _run([name, "set-mode", str(query)])
    if res.returncode == 0:
        return True, ""
    return False, (res.stderr or res.stdout or "kscreenctl failed").strip()


def set_vrr_policy(name, policy):
    """Set the adaptive sync policy. Returns (ok, message)."""
    if not available():
        return False, "kscreenctl is not installed"
    res = _run([name, "set-vrr-policy", policy])
    if res.returncode == 0:
        return True, ""
    return False, (res.stderr or res.stdout or "kscreenctl failed").strip()


def apply_mode(name, modeline, width, height, refresh, reduced=False):
    """Add a custom mode and switch to it. Returns (ok, message).

    The mode is addressed by its id, and the id is found by looking at
    which one appeared -- the display may already advertise a mode with
    the same name, and that is not the one that was just added.

    If the switch does not work, the mode is taken away again. A custom
    mode the compositor keeps while the driver cannot produce it makes
    every configuration apply fail, and the desktop says only that the
    driver rejected it.
    """
    before = mode_ids(name)
    ok, message = add_custom_mode(name, modeline=modeline, width=width,
                                   height=height, refresh=refresh,
                                   reduced=reduced)
    if not ok:
        return False, message

    added = mode_ids(name) - before
    mode_id = added.pop() if len(added) == 1 else ""
    if not mode_id:
        mode_id = _mode_id(name, width, height, refresh)
    query = mode_id or f"{width}x{height}@{refresh:g}"
    ok, message = set_mode(name, query)
    if not ok:
        _remove_added(name, width, height, refresh)
        return False, message
    return True, ""


def _remove_added(name, width, height, refresh):
    """Take away a custom mode that could not be used."""
    index = find_custom_mode(name, width, height, refresh)
    if index is None:
        for i, mode in enumerate(custom_modes(name)):
            size = mode.get("resolution", {})
            if (int(size.get("width", 0)) == width
                    and int(size.get("height", 0)) == height):
                index = i
                break
    if index is not None:
        remove_custom_mode(name, index)


def mode_ids(name):
    """The ids of the modes an output has."""
    res = _run([name, "list-modes"], timeout=10)
    if res.returncode != 0:
        return set()
    ids = set()
    for line in (res.stdout or "").splitlines():
        m = _MODE_RE.match(line.strip())
        if m:
            ids.add(m.group("id"))
    return ids


def _mode_id(name, width, height, refresh):
    """The id of the mode with these timings, or ''.

    The tolerance is the one KDE matches modes with, so a mode that is
    a hundredth of a hertz away is not the one that was asked for.
    """
    res = _run([name, "list-modes"], timeout=10)
    if res.returncode != 0:
        return ""
    best = ""
    best_diff = None
    for line in (res.stdout or "").splitlines():
        m = _MODE_RE.match(line.strip())
        if not m:
            continue
        if int(m.group("w")) != width or int(m.group("h")) != height:
            continue
        diff = abs(float(m.group("r")) - refresh)
        if diff > 0.01:
            continue
        if best_diff is None or diff < best_diff:
            best, best_diff = m.group("id"), diff
    return best
