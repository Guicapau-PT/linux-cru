"""GNOME / Mutter display control, through gdctl.

Mutter has no way to *create* a mode.  What GNOME 48 and newer do have
is ``gdctl``, the official client for the same D-Bus API the Settings
application uses: it can list the modes Mutter knows about, switch to
one, and rewrite the monitor layout while doing so.  A custom mode
therefore still arrives through an EDID override; gdctl is how the tool
then selects it without sending the user to the Displays panel.

The important difference from the KDE backend is that gdctl speaks in
whole layouts, not in one output.  A ``gdctl set`` without every
logical monitor and every connected output drops the rest of the desk
setup, so this module always rebuilds the complete layout from
``gdctl show`` and changes one thing in it.

GNOME 51 reports variable refresh rate as a second mode with a
``+vrr`` suffix, not as an output property.  Turning adaptive sync on
is therefore a mode switch, and a display that does not offer the
suffixed mode cannot do it (the ``min-refresh-rate`` property is
reported in that case, but no variable timing is).

The commands used here:

    gdctl show --verbose
    gdctl set [--persistent] --logical-monitor --monitor DP-1 --mode ... \
              --scale 1.25 --transform normal --x 0 --y 0 ...

All functions are best-effort and return (ok, message) or data; they
never raise on tool failure.
"""

import copy
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field

from . import hostenv

GDCTL = "gdctl"

# How long a `gdctl show` answer is kept.  One call is a process and a
# D-Bus round trip; the interface asks for the same answer while it
# builds several cards.
CACHE_SECONDS = 1.0

# gdctl and Mutter both match modes to a thousandth of a hertz, but a
# user asking for 100 Hz should find the display's 99.982 Hz mode.
REFRESH_TOLERANCE = 0.05

# The transforms Mutter accepts, in the spelling gdctl prints.
TRANSFORMS = ("normal", "90", "180", "270",
              "flipped", "flipped-90", "flipped-180", "flipped-270")

# What the interface calls them, mapped to gdctl's spelling.
_ROTATIONS = {
    "none": "normal",
    "normal": "normal",
    "90": "90",
    "180": "180",
    "270": "270",
    "flipped": "flipped",
    "flipped90": "flipped-90",
    "flipped-90": "flipped-90",
    "flipped180": "flipped-180",
    "flipped-180": "flipped-180",
    "flipped270": "flipped-270",
    "flipped-270": "flipped-270",
}

_TREE = re.compile(r"^(?P<prefix>[│ ]*)(?:[├└]──)?(?P<text>.*)$")
_MODE = re.compile(
    r"^(?P<w>\d+)x(?P<h>\d+)(?P<interlaced>i?)@"
    r"(?P<r>\d+(?:\.\d+)?)(?P<vrr>\+vrr)?$")
_PROPERTY = re.compile(r"^(?P<key>[A-Za-z0-9_-]+)\s*(?:⇒|=>)\s*(?P<value>.*)$")

_lock = threading.Lock()
_cache = {"state": None, "at": 0.0}


class Mode:
    """One mode as Mutter lists it."""

    def __init__(self, width, height, refresh, vrr=False, interlaced=False):
        self.width = int(width)
        self.height = int(height)
        self.refresh = float(refresh)
        self.vrr = bool(vrr)
        self.interlaced = bool(interlaced)
        self.preferred_scale = 0.0
        self.scales = []
        self.current = False
        self.preferred = False

    @property
    def id(self):
        suffix = "i" if self.interlaced else ""
        vrr = "+vrr" if self.vrr else ""
        return f"{self.width}x{self.height}{suffix}@{self.refresh:.3f}{vrr}"

    @property
    def timing(self):
        return (self.width, self.height, round(self.refresh, 3))

    def same_timing(self, other):
        return (self.width == other.width and self.height == other.height
                and abs(self.refresh - other.refresh) <= REFRESH_TOLERANCE)


class Monitor:
    """A physical output and the modes Mutter offers for it."""

    def __init__(self, name, display_name=""):
        self.name = name
        self.display_name = display_name
        self.vendor = ""
        self.product = ""
        self.serial = ""
        self.modes = []
        self.current_mode_id = ""
        self.properties = {}
        self.min_refresh_rate = 0

    @property
    def current_mode(self):
        for mode in self.modes:
            if mode.id == self.current_mode_id:
                return mode
        for mode in self.modes:
            if mode.current:
                return mode
        return None

    @property
    def vrr_capable(self):
        """True when Mutter offered a variable-refresh variant.

        The ``min-refresh-rate`` property alone is not enough: it says
        the panel can go low, but without a ``+vrr`` mode there is no
        timing to switch to.
        """
        return any(mode.vrr for mode in self.modes)

    def mode_for(self, width, height, refresh, vrr=None):
        candidates = [mode for mode in self.modes
                      if mode.width == width and mode.height == height
                      and abs(mode.refresh - refresh) <= REFRESH_TOLERANCE]
        if not candidates:
            return None
        if vrr is not None:
            preferred = [mode for mode in candidates if mode.vrr == vrr]
            if preferred:
                return preferred[0]
        current = self.current_mode
        if current:
            preferred = [mode for mode in candidates
                         if mode.vrr == current.vrr]
            if preferred:
                return preferred[0]
        return candidates[0]

    def add_mode(self, mode, current=False):
        for known in self.modes:
            if known.id == mode.id:
                known.current = known.current or current
                known.preferred = known.preferred or mode.preferred
                known.vrr = known.vrr or mode.vrr
                if mode.scales:
                    known.scales = mode.scales
                if mode.preferred_scale:
                    known.preferred_scale = mode.preferred_scale
                return known
        mode.current = mode.current or current
        self.modes.append(mode)
        return mode


class LogicalMonitor:
    """One logical monitor: a position, a scale and the outputs in it."""

    def __init__(self, index=0):
        self.index = index
        self.x = 0
        self.y = 0
        self.scale = 1.0
        self.transform = "normal"
        self.primary = False
        self.monitors = []
        self.properties = {}

    def contains(self, name):
        return name in self.monitors


class State:
    """Everything `gdctl show --verbose` says about the current layout."""

    def __init__(self):
        self.monitors = []
        self.logical_monitors = []
        self.properties = {}

    def monitor(self, name):
        for monitor in self.monitors:
            if monitor.name == name:
                return monitor
        return None

    def logical_for(self, name):
        for logical in self.logical_monitors:
            if logical.contains(name):
                return logical
        return None


def available():
    """True when gdctl is installed (Mutter ships it from GNOME 48)."""
    return shutil.which(GDCTL) is not None


def invalidate():
    """Forget the last `gdctl show` answer, after something changed."""
    with _lock:
        _cache["state"] = None
        _cache["at"] = 0.0


def reset():
    """Forget everything, for the tests."""
    invalidate()


def _run(args, timeout=12):
    try:
        return subprocess.run([GDCTL] + list(args),
                              capture_output=True, universal_newlines=True,
                              timeout=timeout, env=hostenv.subprocess_env())
    except (OSError, subprocess.SubprocessError) as e:
        class _Failed:
            returncode = 1
            stdout = ""
            stderr = str(e)
        return _Failed()


# -- parsing -----------------------------------------------------------------

def _float(text, default=0.0):
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def _int(text, default=0):
    try:
        return int(text)
    except (TypeError, ValueError):
        return default


def _scales(text):
    """[1.0, 1.25, ...] from gdctl's Supported scales line."""
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return [value for value in
            (_float(part.strip()) for part in text.split(","))
            if value > 0]


def _yes(value):
    return value.strip().lower() in ("yes", "true", "1")


def _tree_lines(text):
    """(depth, text) for every line of gdctl's tree output."""
    for raw in text.splitlines():
        if not raw.strip():
            continue
        match = _TREE.match(raw)
        if not match:
            continue
        prefix = match.group("prefix")
        yield (len(prefix) + 1) // 4, match.group("text").strip()


def parse_show(text):
    """Parse `gdctl show --verbose` into a State.

    The format is a tree with one space and a box-drawing prefix per
    level, so depth is counted rather than read from the labels.  A
    truncated answer yields what could be read and no exception.
    """
    state = State()
    section = ""
    monitor = None
    mode = None
    logical = None
    prop_target = state
    mode_depth = 0
    current_section = False

    for depth, line in _tree_lines(text):
        if line == "Monitors:":
            section = "monitors"
            current_section = False
            monitor = None
            mode = None
            prop_target = state
            continue
        if line == "Logical monitors:":
            section = "logical"
            current_section = False
            logical = None
            prop_target = state
            continue

        if line.startswith("Properties:"):
            if section == "monitors" and monitor:
                prop_target = (mode if mode and depth > mode_depth
                               else monitor)
            elif section == "logical" and logical and depth > 0:
                prop_target = logical
            else:
                prop_target = state
            continue

        prop = _PROPERTY.match(line)
        if prop:
            key = prop.group("key")
            value = prop.group("value").strip()
            target = getattr(prop_target, "properties", None)
            if target is not None:
                target[key] = value
            if isinstance(prop_target, Mode):
                if key == "is-current" and _yes(value):
                    monitor.current_mode_id = prop_target.id
                elif key == "is-preferred" and _yes(value):
                    prop_target.preferred = True
                elif key == "refresh-rate-mode":
                    prop_target.vrr = value.lower() == "variable"
            elif isinstance(prop_target, Monitor):
                if key == "min-refresh-rate":
                    monitor.min_refresh_rate = _int(value)
            continue

        if section == "monitors":
            lead = re.match(r'^Monitor\s+(\S+)(?:\s+\((.*)\))?$', line)
            if lead:
                monitor = Monitor(lead.group(1), lead.group(2) or "")
                state.monitors.append(monitor)
                mode = None
                current_section = False
                prop_target = monitor
                continue
            if not monitor:
                continue

            if line.startswith("Vendor:"):
                monitor.vendor = line.split(":", 1)[1].strip()
            elif line.startswith("Product:"):
                monitor.product = line.split(":", 1)[1].strip()
            elif line.startswith("Serial:"):
                monitor.serial = line.split(":", 1)[1].strip()
            elif line.startswith("Current mode"):
                current_section = True
                mode = None
                prop_target = monitor
            elif line.startswith("Modes ("):
                current_section = False
                mode = None
                prop_target = monitor
            elif line.startswith("Preferences"):
                current_section = False
                mode = None
                prop_target = monitor
            elif line.startswith("Dimension:"):
                if mode:
                    size = line.split(":", 1)[1].strip()
                    width, _, height = size.partition("x")
                    mode.width = _int(width, mode.width)
                    mode.height = _int(height, mode.height)
            elif line.startswith("Refresh rate:"):
                if mode:
                    mode.refresh = _float(line.split(":", 1)[1],
                                          mode.refresh)
            elif line.startswith("Preferred scale:"):
                if mode:
                    mode.preferred_scale = _float(
                        line.split(":", 1)[1], mode.preferred_scale)
            elif line.startswith("Supported scales:"):
                if mode:
                    mode.scales = _scales(line.split(":", 1)[1])
            else:
                parsed = _MODE.match(line)
                if parsed:
                    mode = Mode(parsed.group("w"), parsed.group("h"),
                                parsed.group("r"),
                                bool(parsed.group("vrr")),
                                bool(parsed.group("interlaced")))
                    mode_depth = depth
                    known = monitor.add_mode(mode, current_section)
                    mode = known
                    if current_section:
                        monitor.current_mode_id = known.id

        elif section == "logical":
            lead = re.match(r"^Logical monitor #(\d+)$", line)
            if lead:
                logical = LogicalMonitor(_int(lead.group(1)))
                state.logical_monitors.append(logical)
                prop_target = logical
                continue
            if not logical:
                continue
            if line.startswith("Position:"):
                text = line.split(":", 1)[1].strip().strip("()")
                left, _, right = text.partition(",")
                logical.x = _int(left.strip())
                logical.y = _int(right.strip())
            elif line.startswith("Scale:"):
                logical.scale = _float(line.split(":", 1)[1], logical.scale)
            elif line.startswith("Transform:"):
                logical.transform = line.split(":", 1)[1].strip()
            elif line.startswith("Primary:"):
                logical.primary = _yes(line.split(":", 1)[1])
            elif line.startswith("Monitors ("):
                pass
            elif line.startswith("Monitors:"):
                pass
            elif log := re.match(r'^(\S+)(?:\s+\(.*\))?$', line):
                # The child of "Monitors (N)" is "DP-2 (AOC 34")".
                if log.group(1) not in logical.monitors:
                    logical.monitors.append(log.group(1))
        else:
            prop = _PROPERTY.match(line)
            if prop:
                state.properties[prop.group("key")] = \
                    prop.group("value").strip()

    return state


# -- reading -----------------------------------------------------------------

def state(force=False):
    """The current GNOME layout, or an empty State if it cannot be read."""
    now = time.monotonic()
    with _lock:
        if (not force and _cache["state"] is not None
                and now - _cache["at"] < CACHE_SECONDS):
            return _cache["state"]
    if not available():
        return State()

    result = _run(["show", "--verbose"])
    text = (result.stdout or "")
    if result.returncode != 0 and not text.strip():
        return State()
    parsed = parse_show(text)
    with _lock:
        _cache["state"] = parsed
        _cache["at"] = time.monotonic()
    return parsed


def monitor(name):
    return state().monitor(name)


def mode_entries(name):
    """The modes of an output in the shape the interface already knows."""
    output = monitor(name)
    if not output:
        return []
    entries = []
    for mode in output.modes:
        entry = {
            "id": mode.id,
            "width": mode.width,
            "height": mode.height,
            "refresh": mode.refresh,
            "current": mode.id == output.current_mode_id,
            "preferred": mode.preferred,
            "custom": False,
            "vrr": mode.vrr,
        }
        twin = next((known for known in entries
                     if known["width"] == entry["width"]
                     and known["height"] == entry["height"]
                     and abs(known["refresh"] - entry["refresh"]) < 0.001),
                    None)
        if twin:
            twin["current"] = twin["current"] or entry["current"]
            twin["preferred"] = twin["preferred"] or entry["preferred"]
            twin["vrr"] = twin["vrr"] or entry["vrr"]
            if not twin.get("id"):
                twin["id"] = entry["id"]
            continue
        entries.append(entry)
    entries.sort(key=lambda e: (-e["width"], -e["height"], -e["refresh"]))
    return entries


def current_mode(name):
    """(width, height, refresh) on `name`, or None."""
    output = monitor(name)
    mode = output.current_mode if output else None
    if not mode or mode.refresh <= 0:
        return None
    return mode.width, mode.height, mode.refresh


def settings(name):
    """Scale, rotation, refresh and VRR of one output, for the cards.

    Returns None when the output or its logical monitor is not known.
    """
    current = state()
    output = current.monitor(name)
    logical = current.logical_for(name)
    mode = output.current_mode if output else None
    if not output or not logical:
        return None
    return {
        "scale": logical.scale,
        "rotation": logical.transform,
        "position": (logical.x, logical.y),
        "primary": logical.primary,
        "width": mode.width if mode else 0,
        "height": mode.height if mode else 0,
        "refresh": mode.refresh if mode else 0.0,
        "vrr": bool(mode and mode.vrr),
        "vrr_capable": output.vrr_capable,
        "min_refresh_rate": output.min_refresh_rate,
        "scales": list(mode.scales) if mode else [],
        "monitors": list(logical.monitors),
    }


# -- changing ----------------------------------------------------------------

def _number(value):
    return f"{float(value):g}"


def _layout_args(current, persistent=False):
    args = ["set"]
    if persistent:
        args.append("--persistent")
    for logical in current.logical_monitors:
        args.append("--logical-monitor")
        if logical.primary:
            args.append("--primary")
        for name in logical.monitors:
            output = current.monitor(name)
            mode = output.current_mode if output else None
            if not mode:
                return None
            args += ["--monitor", name, "--mode", mode.id]
        args += ["--scale", _number(logical.scale),
                 "--transform", logical.transform,
                 "--x", str(logical.x), "--y", str(logical.y)]
    return args


def _apply(current, persistent=False):
    args = _layout_args(current, persistent)
    if not args:
        return False, "GNOME did not report a complete monitor layout."
    result = _run(args)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or
                       "gdctl did not apply the layout")
    invalidate()
    return True, ""


def _copy_state():
    return copy.deepcopy(state(force=True))


def set_mode(output, width, height, refresh, persistent=False):
    """Switch one output to a mode Mutter already lists.

    The rest of the layout is carried over unchanged.  A display that
    only has the custom timing in its kernel mode list, not in Mutter's,
    has to be detected again first; this returns a message saying so
    rather than altering the other outputs.
    """
    current = _copy_state()
    physical = current.monitor(output)
    logical = current.logical_for(output)
    if not physical or not logical:
        return False, f"GNOME does not report an output called {output}"

    mode = physical.mode_for(width, height, refresh)
    if not mode:
        return False, (f"GNOME does not have {width}x{height} at {refresh:g} "
                       "Hz. A mode added to the EDID has to be detected by "
                       "GNOME first; log out and back in, or replug the "
                       "display, then try again.")
    physical.current_mode_id = mode.id
    return _apply(current, persistent)


def set_scale(output, scale, persistent=True):
    """Change the scale of the logical monitor that holds `output`."""
    current = _copy_state()
    physical = current.monitor(output)
    logical = current.logical_for(output)
    if not physical or not logical:
        return False, f"GNOME does not report an output called {output}"
    mode = physical.current_mode
    if mode and mode.scales \
            and not any(abs(float(scale) - value) < 0.001
                        for value in mode.scales):
        return False, (f"GNOME does not offer a scale of {scale:g} for this "
                       f"mode (it offers {_scale_list(mode.scales)}).")
    logical.scale = float(scale)
    return _apply(current, persistent)


def set_rotation(output, rotation, persistent=True):
    """Rotate the logical monitor that holds `output`."""
    transform = _ROTATIONS.get(str(rotation).strip().lower())
    if not transform:
        return False, f"unknown rotation: {rotation}"
    current = _copy_state()
    logical = current.logical_for(output)
    if not logical:
        return False, f"GNOME does not report an output called {output}"
    logical.transform = transform
    return _apply(current, persistent)


def vrr_policy(output):
    """'always' when the mode in use is a variable-refresh one."""
    output_state = monitor(output)
    mode = output_state.current_mode if output_state else None
    return "always" if mode and mode.vrr else "never"


def supports_vrr(output):
    output_state = monitor(output)
    return bool(output_state and output_state.vrr_capable)


def set_vrr(output, policy):
    """Turn adaptive sync on or off by switching to the matching mode.

    GNOME has no separate VRR policy: a variable-refresh mode is a mode
    with the same timing and a ``+vrr`` suffix.  "automatic" is treated
    as "always", because that is the only on state Mutter offers.
    """
    if policy not in ("never", "always", "automatic"):
        return False, f"unknown adaptive sync policy: {policy}"
    current = _copy_state()
    physical = current.monitor(output)
    mode = physical.current_mode if physical else None
    if not physical or not mode:
        return False, f"GNOME does not report a current mode for {output}"

    wanted = policy in ("always", "automatic")
    candidates = [candidate for candidate in physical.modes
                  if candidate.same_timing(mode)]
    target = next((candidate for candidate in candidates
                   if candidate.vrr == wanted), None)
    if not target:
        if wanted:
            return False, ("GNOME did not report a variable refresh rate "
                           "mode for this display. Adaptive sync needs a "
                           "display, cable and driver that report it.")
        return False, "this display has no fixed-refresh variant to return to"

    physical.current_mode_id = target.id
    return _apply(current, persistent=True)


def _scale_list(scales):
    return ", ".join(f"{scale:g}" for scale in scales)
