"""KDE Plasma's own screen control tool.

Plasma 6.6 taught kscreen-doctor to add a custom mode from a width, a
height and a refresh rate, and the compositor computed the timings
itself with libxcvt. Plasma 6.8 brought **kscreenctl**, which takes a
full modeline instead: the timings that reach the display can be
exactly the ones that were calculated.

The JSON that "kscreenctl list-outputs --json" prints has the same
shape the rest of the tool already knows from kscreen-doctor, with
more in it (capabilities, the VRR policy, the custom modes and the
display settings: scale, rotation, HDR, colour depth). On 6.8.1 the
custom modes are listed with the others and given an id as well, so
a mode that was just added can be addressed by it.

Every call is a process and a round trip to KWin over DBus, about
50 ms each, so the answers are kept for a moment: the interface asks
for the same thing again and again while it is being built, and a
mode that cannot be applied has to be taken away again, which needs
the answer once more. The interface asks off its own thread, so the
window keeps drawing meanwhile.

The command surface used here (Plasma 6.8):

    kscreenctl list-outputs --json
    kscreenctl <output> add-custom-mode <clock> <h...> <v...> [flags]
    kscreenctl <output> add-custom-mode --reduced-blanking <WxH@Hz>
    kscreenctl <output> remove-custom-mode <index>
    kscreenctl <output> list-modes
    kscreenctl <output> list-custom-modes
    kscreenctl <output> set-mode <id|WxH@RR|WxH>
    kscreenctl <output> set-vrr-policy never|always|automatic
    kscreenctl <output> set-hdr <true|false>
    kscreenctl <output> set-wcg <true|false>
    kscreenctl <output> set-maxbpc <bpc>
    kscreenctl <output> set-scale <percent|%>
    kscreenctl <output> set-rotation <rotation>

Things the help text does not say:

- `add-custom-mode` adds the mode; it does not switch to it. A
  `set-mode` is what applies it.
- The scale is a percentage ("125%"), and a bare number is read as a
  percentage too, so "1.25" is refused ("at least 50%").
- `set-maxbpc` takes whole numbers, 6 to 16.
- The refresh rates are matched with a tolerance of 0.01 Hz.
- An output reports what it can do in `capabilities` ("hdr", "vrr",
  "custom-modes", ...). "hdr-icc" is no proof of HDR: an SDR display
  reports it while saying HDR is incapable.
"""

import json
import re
import shutil
import subprocess
import threading
import time

from . import hostenv

KSCREENCTL = "kscreenctl"

# The mode line kscreenctl prints: "<id>:<width>x<height>@<refresh>",
# with a '*' on the mode that is in use and a '!' on the preferred one.
_MODE_RE = re.compile(
    r"^(?P<id>[^:]+):(?P<w>\d+)x(?P<h>\d+)@(?P<r>[\d.]+)(?P<flags>[!*]*)\s*$")

# The custom modes are printed as "<index>: <width>x<height>@<refresh>Hz".
# The index is the one "remove-custom-mode" takes.
_CUSTOM_RE = re.compile(
    r"^(?P<index>\d+):\s*(?P<w>\d+)x(?P<h>\d+)@(?P<r>[\d.]+)\s*Hz?\s*$")

# How long an answer is kept. Long enough that the interface does not
# ask the same question twice while it is being built, short enough
# that a change made in the display settings shows up right away.
CACHE_SECONDS = 1.0

# The refresh rates are matched the way KDE matches them: a hundredth of
# a hertz. A display that advertises 99.98 and a tool that asks for
# 100.00 are 0.02 Hz apart, and a looser comparison picks the wrong one.
REFRESH_TOLERANCE = 0.01

_lock = threading.Lock()
_cache = {"outputs": None, "at": 0.0, "modelines": None}


def available():
    """True when kscreenctl is installed (Plasma 6.8 and newer)."""
    return shutil.which(KSCREENCTL) is not None


def invalidate():
    """Forget the outputs that were read. Used after something changed."""
    with _lock:
        _cache["outputs"] = None
        _cache["at"] = 0.0


def reset():
    """Forget everything that was read, including what the tool can do."""
    invalidate()
    with _lock:
        _cache["modelines"] = None


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
    with _lock:
        cached = _cache["modelines"]
    if cached is not None:
        return cached
    res = _run(["help"], timeout=10)
    text = (res.stdout or "") + (res.stderr or "")
    answer = "CVT timings" in text
    with _lock:
        _cache["modelines"] = answer
    return answer


# -- reading -------------------------------------------------------------------

def outputs(force=False):
    """Every connected output, as the JSON kscreenctl describes it.

    The answer is kept for a moment: the interface asks for it again
    for every output it draws, and every call is a round trip to KWin.
    """
    if not available():
        return []
    now = time.monotonic()
    with _lock:
        if (not force and _cache["outputs"] is not None
                and now - _cache["at"] < CACHE_SECONDS):
            return _cache["outputs"]

    res = _run(["list-outputs", "--json"])
    if res.returncode != 0 or not res.stdout.strip():
        return []
    try:
        data = json.loads(res.stdout)
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    with _lock:
        _cache["outputs"] = data
        _cache["at"] = time.monotonic()
    return data


def output(name):
    """The output called `name` (by name or by uuid), or None."""
    for out in outputs():
        if out.get("name") == name or out.get("uuid") == name:
            return out
    return None


def mode_entries(name):
    """The modes an output has, with what is known about each.

    Each entry is a dictionary: the id, the size, the refresh rate,
    whether the mode is the one in use, whether the display prefers
    it, and whether it is a custom mode -- one that was added rather
    than advertised. Plasma 6.8 lists the custom modes with the others
    and gives them an id as well.

    The list is ordered largest to smallest, faster rates first, and
    the doubles are taken out: the JSON comes in the order 1, 10, 11,
    ... 2, 20 ... and reports the same timing once for each descriptor
    it reached the display through, which is a worse list than the
    display settings show.
    """
    out = output(name)
    if not out:
        return []
    custom = _custom_keys(out)
    entries = []
    for mode in out.get("modes", []):
        size = mode.get("resolution", {})
        width = int(size.get("width", 0))
        height = int(size.get("height", 0))
        refresh = float(mode.get("refreshRate", 0))
        entry = {
            "id": str(mode.get("id", "")),
            "width": width,
            "height": height,
            "refresh": refresh,
            "current": bool(mode.get("current")),
            "preferred": bool(mode.get("preferred")),
            "custom": _is_custom((width, height, refresh), custom),
        }
        twin = _same_timing(entry, entries)
        if twin:
            # One timing, reported more than once. Nothing is gained by
            # two rows; what one of them says about being used or made
            # up still applies to the survivor.
            for key in ("current", "preferred", "custom"):
                twin[key] = twin[key] or entry[key]
            continue
        entries.append(entry)
    entries.sort(key=lambda e: (-e["width"], -e["height"], -e["refresh"]))
    return entries


def _same_timing(entry, entries):
    """An entry already in the list that describes the same timing."""
    for other in entries:
        if other["width"] == entry["width"] \
                and other["height"] == entry["height"] \
                and abs(other["refresh"] - entry["refresh"]) < 0.01:
            return other
    return None


def _custom_keys(out):
    """The (width, height, refresh) of the custom modes of an output."""
    keys = []
    for mode in out.get("customModes", []):
        size = mode.get("resolution", {})
        keys.append((int(size.get("width", 0)), int(size.get("height", 0)),
                     float(mode.get("refreshRate", 0))))
    return keys


def _is_custom(key, custom):
    """True when a mode is one of the custom ones.

    The custom modes are listed apart from the others and without an
    id, so they are matched by what they are. The refresh rate is
    matched loosely: the JSON rounds it.
    """
    width, height, refresh = key
    for c_width, c_height, c_refresh in custom:
        if c_width != width or c_height != height:
            continue
        if abs(c_refresh - refresh) < 0.5:
            return True
    return False


def modes(name):
    """The modes an output offers, as (width, height, refresh, current)."""
    return [(entry["width"], entry["height"], entry["refresh"],
             entry["current"]) for entry in mode_entries(name)]


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


def custom_mode_list(name):
    """The custom modes of an output, as (index, width, height, refresh).

    The index is the one "remove-custom-mode" takes. It is asked for
    from the tool itself, because the index in the JSON is only the
    index in the list as long as nothing was added in between.
    """
    res = _run([name, "list-custom-modes"], timeout=10)
    if res.returncode != 0:
        return []
    result = []
    for line in (res.stdout or "").splitlines():
        m = _CUSTOM_RE.match(line.strip())
        if m:
            result.append((int(m.group("index")), int(m.group("w")),
                           int(m.group("h")), float(m.group("r"))))
    return result


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


def capabilities(name):
    """What the output says it can do, as a list of strings."""
    out = output(name)
    if not out:
        return []
    return list(out.get("capabilities", []))


def supports_hdr(name):
    """True when the output says it can show HDR.

    Only "hdr" counts: "hdr-icc" says a colour profile can be named
    for the HDR mode, and a display reports it while still saying HDR
    is incapable (an SDR panel does).
    """
    return "hdr" in capabilities(name)


def settings(name):
    """The display settings of an output, as the JSON describes them.

    Scale, rotation, position, HDR, wide colour gamut, the colour
    depth and the rest: the things Plasma's display settings can
    change, and the things that decide how the mode looks.
    """
    out = output(name)
    if not out:
        return {}
    keys = ("scale", "rotation", "position", "overscan", "hdr", "wcg",
            "maxbpc", "rgbRange", "sharpness", "enabled", "primary",
            "priority", "brightness", "dimming", "autoBrightness", "abm",
            "ddcCiAllowed", "edrPolicy", "colorProfileSource",
            "colorPowerTradeoff", "vrrPolicy", "replicaOf",
            "hdrColorProfileSource", "hdrIccProfilePath", "iccProfilePath")
    return {key: out.get(key) for key in keys}


# -- changing -------------------------------------------------------------------

def _set(name, command, *values):
    """Run a "set-" command on an output. Returns (ok, message)."""
    if not available():
        return False, "kscreenctl is not installed"
    args = [name, command] + [str(value) for value in values]
    res = _run(args)
    invalidate()
    if res.returncode == 0:
        return True, ""
    return False, (res.stderr or res.stdout or "kscreenctl failed").strip()


def add_custom_mode(name, modeline=None, width=None, height=None,
                     refresh=None, reduced=False):
    """Add a custom mode to an output. Returns (ok, message).

    With a modeline the timings are exactly the ones given; without one
    the compositor computes them, and `reduced` asks for reduced
    blanking. Adding a mode does not switch to it.
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
    invalidate()
    if res.returncode == 0:
        return True, ""
    return False, (res.stderr or res.stdout or "kscreenctl failed").strip()


def remove_custom_mode(name, index):
    """Remove the custom mode at `index`. Returns (ok, message)."""
    if not available():
        return False, "kscreenctl is not installed"
    res = _run([name, "remove-custom-mode", str(index)])
    invalidate()
    if res.returncode == 0:
        return True, ""
    return False, (res.stderr or res.stdout or "kscreenctl failed").strip()


def find_custom_mode(name, width, height, refresh):
    """The index of the custom mode with these timings, or None."""
    for index, w, h, rate in custom_mode_list(name):
        if w == width and h == height and abs(rate - refresh) < 0.05:
            return index
    # The list is not always there; the JSON knows the custom modes
    # too, and their order is the order they were added in.
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
    invalidate()
    if res.returncode == 0:
        return True, ""
    return False, (res.stderr or res.stdout or "kscreenctl failed").strip()


def set_vrr_policy(name, policy):
    """Set the adaptive sync policy. Returns (ok, message)."""
    if not available():
        return False, "kscreenctl is not installed"
    res = _run([name, "set-vrr-policy", policy])
    invalidate()
    if res.returncode == 0:
        return True, ""
    return False, (res.stderr or res.stdout or "kscreenctl failed").strip()


def set_hdr(name, enabled):
    """Turn HDR on or off. Returns (ok, message)."""
    return _set(name, "set-hdr", "true" if enabled else "false")


def set_wcg(name, enabled):
    """Turn the wide colour gamut on or off. Returns (ok, message)."""
    return _set(name, "set-wcg", "true" if enabled else "false")


def set_maxbpc(name, bpc):
    """Set the colour depth in bits per colour. Returns (ok, message)."""
    return _set(name, "set-maxbpc", int(bpc))


def set_scale(name, scale):
    """Set the scale of an output. Returns (ok, message)."""
    return _set(name, "set-scale", scale)


def set_rotation(name, rotation):
    """Rotate an output. Returns (ok, message)."""
    return _set(name, "set-rotation", rotation)


def set_position(name, x, y):
    """Place an output. Returns (ok, message)."""
    return _set(name, "set-position", int(x), int(y))


def set_enabled(name, enabled):
    """Turn an output on or off. Returns (ok, message)."""
    return _set(name, "set-enabled", "true" if enabled else "false")


def set_rgb_range(name, value):
    """Set the RGB range (full, limited, automatic). Returns (ok, message)."""
    return _set(name, "set-rgb-range", value)


def set_overscan(name, value):
    """Set the overscan (0-100). Returns (ok, message)."""
    return _set(name, "set-overscan", int(value))


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
    if not available():
        return False, "kscreenctl is not installed"

    before = {entry["id"] for entry in mode_entries(name) if entry["id"]}
    ok, message = add_custom_mode(name, modeline=modeline, width=width,
                                   height=height, refresh=refresh,
                                   reduced=reduced)
    if not ok:
        return False, message

    after = mode_entries(name)
    new = [entry for entry in after
           if entry["id"] and entry["id"] not in before]
    if len(new) == 1:
        query = new[0]["id"]
    else:
        query = (_custom_id(after, width, height, refresh)
                 or _new_mode_id(name, before)
                 or f"{width}x{height}@{refresh:g}")
    ok, message = set_mode(name, query)
    if not ok:
        _remove_added(name, width, height, refresh)
        return False, message
    return True, ""


def _custom_id(entries, width, height, refresh):
    """The id of the custom mode with these timings, or ''.

    Only the custom modes are looked at: the display's own modes with
    the same size are not the one that was just added.
    """
    best = ""
    best_diff = None
    for entry in entries:
        if not entry["custom"]:
            continue
        if entry["width"] != width or entry["height"] != height:
            continue
        diff = abs(entry["refresh"] - refresh)
        if diff > 0.5:
            continue
        if best_diff is None or diff < best_diff:
            best, best_diff = entry["id"], diff
    return best


def _new_mode_id(name, before):
    """The id of the mode that appeared, or ''.

    The JSON lists the custom modes with the others, but the list of
    modes is the one that always has them: it is asked for when the
    JSON did not show what was added.
    """
    ids = mode_ids(name)
    new = ids - set(before)
    if len(new) == 1:
        return new.pop()
    return ""


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
        if diff > REFRESH_TOLERANCE:
            continue
        if best_diff is None or diff < best_diff:
            best, best_diff = m.group("id"), diff
    return best
