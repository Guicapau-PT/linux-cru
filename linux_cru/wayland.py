"""Wayland compositor apply backends.

KDE (Plasma 6.6+): kscreen-doctor addCustomMode / mode / removeCustomMode.
sway / Hyprland: native modeline commands over their IPC tools.

All functions are best-effort and return (ok, message) or data/None;
they never raise on tool failure.
"""

import json
import re
import subprocess
import time

from . import cosmic, detect, hostenv, kwin

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, universal_newlines=True,
                              timeout=10, env=hostenv.subprocess_env())
    except (OSError, subprocess.SubprocessError) as e:
        class _Failed:
            returncode = 1
            stdout = ""
            stderr = str(e)
        return _Failed()


# -- switching to a mode, whatever is running --------------------------------

def kernel_has_mode(card, connector, width, height):
    """True once the kernel lists the resolution for this connector.

    The compositor cannot switch to a mode it has not seen yet, and an
    EDID override only takes effect on the next detect, so this is the
    gate to wait on before trying.
    """
    try:
        with open(f"/sys/class/drm/{card}-{connector}/modes") as f:
            return f"{width}x{height}" in f.read().split()
    except OSError:
        return False


def wait_for_mode(card, connector, width, height, timeout=6.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if kernel_has_mode(card, connector, width, height):
            return True
        time.sleep(0.25)
    return False


def wait_for_output_mode(env, output, width, height, timeout=8.0):
    """Wait until the compositor lists this mode for the output.

    The kernel knows about a mode as soon as the EDID is patched, but
    the compositor keeps its own copy of the mode list and only reads
    it again when the connector is detected anew. Waiting for the
    compositor is what tells "the driver refused it" apart from "the
    desktop has not looked yet".
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for w, h, _refresh, _current in detect.output_modes(env, output):
            if w == width and h == height:
                return True
        time.sleep(0.4)
    return False


def set_mode(env, output, width, height, refresh):
    """Switch `output` to this mode. Returns (ok, message)."""
    session = env.session_type
    compositor = env.compositor

    if session == "x11":
        res = _run(["xrandr", "--output", output, "--mode",
                    f"{width}x{height}", "--rate", f"{refresh:g}"])
        return res.returncode == 0, (res.stderr or res.stdout)

    if compositor == "kwin":
        if kwin.available():
            return kwin.set_mode(output, f"{width}x{height}@{refresh:g}")
        return kwin_set_mode(output, f"{width}x{height}@{refresh:g}")

    if compositor == "sway":
        return sway_set_mode(output, width, height, refresh)

    if compositor == "hyprland":
        return hyprland_set_mode(output, width, height, refresh)

    if compositor == "mutter":
        return gnome_set_mode(env, output, width, height, refresh)

    if compositor == "cosmic":
        return cosmic_set_mode(output, width, height, refresh)

    if env.supports_wlr_output_management:
        res = _run(["wlr-randr", "--output", output, "--mode",
                    f"{width}x{height}@{refresh:g}Hz"])
        return res.returncode == 0, (res.stderr or res.stdout)

    return False, f"no way to change the mode on {compositor}"


def cosmic_set_mode(output, width, height, refresh):
    """Switch a COSMIC output to a mode it already advertises.

    COSMIC will not create a mode (pop-os/cosmic-epoch#2577), so this
    only selects one that an EDID override has already put in the list.
    cosmic-randr is tried first, then the generic wlr-randr, which talks
    to the same protocol.
    """
    ok, message = cosmic.set_mode(output, width, height, refresh)
    if ok:
        return True, ""
    if cosmic.available():
        return False, message
    res = _run(["wlr-randr", "--output", output, "--mode",
                f"{width}x{height}@{refresh:g}Hz"])
    if res.returncode == 0:
        return True, ""
    return False, (res.stderr or res.stdout or message)


def gnome_set_mode(env, output, width, height, refresh):
    """GNOME, through gdctl (shipped with Mutter since GNOME 48).

    gdctl replaces the whole monitor layout, so with more than one
    display connected this would rearrange the others. Refuse rather
    than wreck someone's desk setup.
    """
    connected = [c for c in env.connectors if c.status == "connected"]
    if len(connected) > 1:
        return False, ("gdctl rewrites the whole monitor layout, so this tool "
                       "will not switch modes automatically with more than one "
                       "display connected. The mode has been added -- select it "
                       "in Settings > Displays.")
    res = _run(["gdctl", "set", "--logical-monitor", "--primary",
                "--monitor", output, "--mode",
                f"{width}x{height}@{refresh:.3f}"])
    if res.returncode == 0:
        return True, ""
    # Older GNOME has no gdctl.
    if "not found" in (res.stderr or "").lower():
        return False, ("gdctl is not available (it ships with GNOME 48 and "
                       "later). The mode has been added -- select it in "
                       "Settings > Displays.")
    return False, (res.stderr or res.stdout)


# -- KDE / kscreen-doctor ----------------------------------------------------

def kwin_state(output):
    """(current_mode_id, set_of_all_mode_ids) for `output`, or None.

    kscreenctl (Plasma 6.8 and newer) is asked first; kscreen-doctor is
    the fallback for the releases before it.
    """
    if kwin.available():
        out = kwin.output(output)
        if out:
            ids = {str(m.get("id")) for m in out.get("modes", [])}
            current = out.get("mode") or {}
            return str(current.get("id", "")), ids

    res = _run(["kscreen-doctor", "-j"])
    if res.returncode != 0:
        return None
    try:
        data = json.loads(res.stdout)
    except ValueError:
        return None
    for o in data.get("outputs", []):
        if o.get("name") == output:
            ids = {str(m.get("id")) for m in o.get("modes", [])}
            return str(o.get("currentModeId")), ids
    return None


def kwin_apply_custom_mode(output, ml, width, height, refresh, reduced=True):
    """Add a custom mode to a KWin output and switch to it.

    Returns (ok, message). Plasma 6.8 and newer get the modeline as it
    is, so the timings on the display are exactly the ones that were
    calculated; before that the compositor computed them itself. The
    refresh rate that is passed on is the one the timing comes out at,
    which is what the mode is addressed by.

    Nothing is done when the display is already running the mode: the
    compositor would take the picture apart only to put it back the way
    it was, and a test of a mode that is in use would look like a
    flicker that changed nothing.
    """
    current = kwin.current_mode(output)
    if current and current[0] == width and current[1] == height \
            and abs(current[2] - refresh) <= kwin.REFRESH_TOLERANCE:
        return True, ""

    if kwin.available() and kwin.supports_modelines():
        return kwin.apply_mode(output, ml.timing_string(), width, height,
                               refresh, reduced)

    if not kwin.available():
        # kscreen-doctor: add the mode, then switch to it.
        state = kwin_state(output)
        if not state:
            return False, "could not read the display configuration"
        _, before = state
        new_id, err = kwin_add_custom_mode(output, width, height, refresh,
                                            reduced)
        if not new_id:
            return False, err
        ok, msg = kwin_set_mode(output, new_id)
        if not ok:
            kwin_remove_custom_mode(output, width, height)
            return False, msg
        return True, ""

    # kscreenctl without modeline support: the plain form.
    ok, message = kwin.add_custom_mode(output, width=width, height=height,
                                       refresh=refresh, reduced=reduced)
    if not ok:
        return False, message
    ok, message = kwin.set_mode(output, f"{width}x{height}@{refresh:g}")
    if not ok:
        return False, message
    return True, ""


def kwin_revert_custom_mode(output, width, height, refresh):
    """Take a custom mode away again. Returns (ok, message).

    The mode has to be left before it can be taken away: KWin keeps a
    custom mode that is in use, and removing it then says nothing at
    all. So the mode that is on the display has to be changed first.
    """
    if kwin.available():
        index = kwin.find_custom_mode(output, width, height, refresh)
        if index is None:
            # Fall back to the first custom mode with this resolution.
            for i, mode in enumerate(kwin.custom_modes(output)):
                size = mode.get("resolution", {})
                if (int(size.get("width", 0)) == width
                        and int(size.get("height", 0)) == height):
                    index = i
                    break
        if index is None:
            return True, "the custom mode is already gone"
        ok, message = kwin.remove_custom_mode(output, index)
        if not ok:
            return False, message
        if kwin.find_custom_mode(output, width, height, refresh) is not None:
            return False, ("the mode is still in use -- the display has to "
                           "leave it first")
        return True, ""
    return kwin_remove_custom_mode(output, width, height)


def kwin_switch_mode(output, mode_id):
    """Switch a KWin output to a mode, by its id."""
    if kwin.available():
        return kwin.set_mode(output, mode_id)
    return kwin_set_mode(output, mode_id)


def kwin_switch_to(output, width, height, refresh):
    """Switch a KWin output to a mode, by what it is.

    The mode ids are renumbered every time the output configuration is
    applied, so an id that was read a moment ago can point at a
    different mode by the time it is used. A mode is addressed by its
    size and refresh rate instead.
    """
    query = f"{width}x{height}@{refresh:g}"
    if kwin.available():
        return kwin.set_mode(output, query)
    return kwin_set_mode(output, query)


def kwin_add_custom_mode(output, width, height, refresh, reduced=True):
    """Add a custom mode. Returns (new_mode_id, "") or (None, error)."""
    state = kwin_state(output)
    if not state:
        return None, "could not read the display configuration"
    _, before = state

    mhz = int(round(refresh * 1000))
    blanking = "reduced" if reduced else "full"
    res = _run(["kscreen-doctor",
                f"output.{output}.addCustomMode.{width}.{height}.{mhz}.{blanking}"])
    if res.returncode != 0:
        return None, res.stderr or res.stdout or "kscreen-doctor failed"

    state = kwin_state(output)
    if not state:
        return None, "could not re-read the display configuration"
    _, after = state
    new = after - before
    if len(new) == 1:
        return new.pop(), ""
    return None, ("the compositor did not add a new mode "
                  "(an identical mode may already exist)")


def kwin_set_mode(output, mode_id):
    res = _run(["kscreen-doctor", f"output.{output}.mode.{mode_id}"])
    return res.returncode == 0, (res.stderr or res.stdout)


def kwin_remove_custom_mode(output, width, height):
    """Remove the first custom mode matching width x height. True on success."""
    res = _run(["kscreen-doctor", "-o"])
    if res.returncode != 0:
        return False
    text = _ANSI.sub("", res.stdout)
    in_output = False
    in_custom = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("Output:"):
            parts = s.split()
            in_output = len(parts) > 2 and parts[2] == output
            in_custom = False
            continue
        if in_output and s.startswith("Custom modes"):
            in_custom = "None" not in s
            continue
        if in_output and in_custom:
            m = re.match(r"(\d+):\s*(\d+)x(\d+)@", s)
            if not m:
                in_custom = False
                continue
            if int(m.group(2)) == width and int(m.group(3)) == height:
                res = _run(["kscreen-doctor",
                            f"output.{output}.removeCustomMode.{m.group(1)}"])
                return res.returncode == 0
    return False


# -- adaptive sync (VRR) --------------------------------------------------------

# The policies the compositors agree on. KWin calls them vrrpolicy,
# COSMIC calls it adaptive sync, and both mean the same thing.
VRR_POLICIES = ("never", "always", "automatic")

_COSMIC_VRR = {"never": "false", "always": "true", "automatic": "automatic"}


def vrr_supported(env):
    """True when this compositor can be told to use adaptive sync."""
    return env.compositor in ("kwin", "cosmic")


def set_vrr(env, output, policy):
    """Set the adaptive sync policy of an output. Returns (ok, message)."""
    if policy not in VRR_POLICIES:
        return False, f"unknown adaptive sync policy: {policy}"
    if env.compositor == "kwin":
        if kwin.available():
            return kwin.set_vrr_policy(output, policy)
        res = _run(["kscreen-doctor", f"output.{output}.vrrpolicy.{policy}"])
        return res.returncode == 0, (res.stderr or res.stdout)
    if env.compositor == "cosmic":
        return cosmic.set_adaptive_sync(output, _COSMIC_VRR[policy])
    return False, f"{env.compositor} cannot be told to use adaptive sync"


# -- sway ---------------------------------------------------------------------

def sway_apply_modeline(output, timing_string):
    res = _run(["swaymsg", f"output {output} modeline {timing_string}"])
    return res.returncode == 0, (res.stderr or res.stdout)


def sway_set_mode(output, width, height, refresh):
    res = _run(["swaymsg", f"output {output} mode {width}x{height}@{refresh:g}Hz"])
    return res.returncode == 0, (res.stderr or res.stdout)


# -- Hyprland -------------------------------------------------------------------

def hyprland_apply_modeline(output, timing_string):
    res = _run(["hyprctl", "keyword", "monitor",
                f"{output}, modeline {timing_string}, 0x0, 1"])
    ok = res.returncode == 0 and "ok" in (res.stdout or "").lower()
    return ok, (res.stderr or res.stdout)


def hyprland_set_mode(output, width, height, refresh):
    res = _run(["hyprctl", "keyword", "monitor",
                f"{output}, {width}x{height}@{refresh:g}, 0x0, 1"])
    ok = res.returncode == 0 and "ok" in (res.stdout or "").lower()
    return ok, (res.stderr or res.stdout)
