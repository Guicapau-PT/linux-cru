"""COSMIC Desktop (cosmic-comp) support.

COSMIC implements the wlr output management protocols, so its own CLI
(cosmic-randr) and the generic wlr-randr can both list and select
modes. What it cannot do is *compute* new timings: cosmic-comp rejects
set_custom_mode for anything that is not in the display's EDID list
(pop-os/cosmic-epoch#2577), so a custom resolution has to reach the
kernel through an EDID override and the compositor is then only used to
switch to the mode that appeared in the list.

This module covers the two jobs COSMIC is good at: reporting what the
outputs and their modes are, and switching to a mode that is already
in the list.
"""

import re
import shutil
import subprocess
from dataclasses import dataclass, field

from . import hostenv

COSMIC_RANDR = "cosmic-randr"


def available():
    """True when the COSMIC CLI is installed."""
    return shutil.which(COSMIC_RANDR) is not None


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


@dataclass
class CosmicMode:
    width: int
    height: int
    refresh_mhz: int
    current: bool = False
    preferred: bool = False

    @property
    def refresh(self):
        """Refresh rate in Hz."""
        return self.refresh_mhz / 1000.0

    @property
    def label(self):
        flags = []
        if self.current:
            flags.append("active")
        if self.preferred:
            flags.append("preferred")
        suffix = f" ({', '.join(flags)})" if flags else ""
        return f"{self.width}x{self.height} @ {self.refresh:.3f} Hz{suffix}"


@dataclass
class CosmicOutput:
    name: str
    enabled: bool = True
    make: str = ""
    model: str = ""
    serial: str = ""
    width_mm: int = 0
    height_mm: int = 0
    scale: float = 1.0
    transform: str = "normal"
    adaptive_sync_supported: bool = False
    adaptive_sync: bool = False
    modes: list = field(default_factory=list)

    @property
    def display_name(self):
        parts = [p for p in (self.make, self.model) if p]
        return " ".join(parts) or self.name

    @property
    def current_mode(self):
        for m in self.modes:
            if m.current:
                return m
        return None

    @property
    def physical_size(self):
        if self.width_mm and self.height_mm:
            inches = ((self.width_mm ** 2 + self.height_mm ** 2) ** 0.5) / 25.4
            return f"{self.width_mm}x{self.height_mm} mm ({inches:.1f} in)"
        return ""


# -- parsing -----------------------------------------------------------------

_KDL_TRUE = ("#true", "true")
_KDL_FALSE = ("#false", "false")


def _kdl_bool(value, default=False):
    v = (value or "").strip().strip('"').lower()
    if v in _KDL_TRUE:
        return True
    if v in _KDL_FALSE:
        return False
    return default


def _kdl_string(value):
    value = (value or "").strip()
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        return value[1:-1]
    return value


def _blocks(text, keyword):
    """[(header, body)] for every complete `keyword ... { ... }` block.

    A block that is never closed is ignored: a truncated answer from
    the tool is not worth guessing about.
    """
    blocks = []
    pos = 0
    pattern = re.compile(rf"^\s*{re.escape(keyword)}\b", re.M)
    while True:
        m = pattern.search(text, pos)
        if not m:
            break
        start = m.start()
        opening = text.find("{", start)
        if opening < 0:
            break
        depth = 0
        closing = None
        for i in range(opening, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    closing = i
                    break
        if closing is None:
            break
        blocks.append((text[start:opening], text[opening + 1:closing]))
        pos = closing + 1
    return blocks


def _split_modes(body):
    """(properties, modes) of an output body."""
    m = re.search(r"^\s*modes\s*\{", body, re.M)
    if not m:
        return body, ""
    depth = 0
    for i in range(m.end() - 1, len(body)):
        if body[i] == "{":
            depth += 1
        elif body[i] == "}":
            depth -= 1
            if depth == 0:
                return body[:m.start()], body[m.end():i]
    return body[:m.start()], body[m.end():]


_MODE_RE = re.compile(r"^\s*mode\s+(\d+)\s+(\d+)\s+(\d+)\s*(.*)$")
_PROP_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s+(.+?)\s*$")


def parse_kdl(text):
    """Parse `cosmic-randr list --kdl` into CosmicOutput objects."""
    outputs = []
    for header, body in _blocks(text, "output"):
        name_match = re.search(r'"([^"]*)"', header)
        if not name_match:
            continue
        out = CosmicOutput(name=name_match.group(1))

        enabled = re.search(r"enabled=(#?\w+)", header)
        if enabled:
            out.enabled = _kdl_bool(enabled.group(1), True)

        props, modes_body = _split_modes(body)
        for line in props.splitlines():
            m = _PROP_RE.match(line)
            if not m:
                continue
            key, value = m.group(1), m.group(2)
            if key == "description":
                make = re.search(r'make="([^"]*)"', value)
                model = re.search(r'model="([^"]*)"', value)
                out.make = make.group(1) if make else ""
                out.model = model.group(1) if model else ""
            elif key == "physical":
                parts = value.split()
                if len(parts) >= 2:
                    out.width_mm = int(float(parts[0]))
                    out.height_mm = int(float(parts[1]))
            elif key == "scale":
                try:
                    out.scale = float(value)
                except ValueError:
                    pass
            elif key == "transform":
                out.transform = _kdl_string(value)
            elif key == "adaptive_sync_support":
                out.adaptive_sync_supported = _kdl_bool(value)
            elif key == "adaptive_sync":
                out.adaptive_sync = _kdl_bool(value)
            elif key == "serial_number":
                out.serial = _kdl_string(value)

        for line in modes_body.splitlines():
            m = _MODE_RE.match(line)
            if not m:
                continue
            out.modes.append(CosmicMode(
                width=int(m.group(1)),
                height=int(m.group(2)),
                refresh_mhz=int(m.group(3)),
                current="current=#true" in m.group(4),
                preferred="preferred=#true" in m.group(4),
            ))
        outputs.append(out)
    return outputs


def outputs():
    """Every output COSMIC knows about, or [] if it cannot be reached."""
    if not available():
        return []
    res = _run([COSMIC_RANDR, "list", "--kdl"])
    if res.returncode != 0 or not res.stdout:
        return []
    try:
        return parse_kdl(res.stdout)
    except (ValueError, IndexError):
        return []


def find(name):
    """The CosmicOutput called `name`, or None."""
    for out in outputs():
        if out.name == name:
            return out
    return None


def current_mode(name):
    """(width, height, refresh) active on `name`, or None."""
    out = find(name)
    if not out:
        return None
    mode = out.current_mode
    if not mode:
        return None
    return mode.width, mode.height, mode.refresh


def set_mode(name, width, height, refresh=None, adaptive_sync=None):
    """Switch `name` to a mode. Returns (ok, message).

    The mode has to exist in the output's list already: COSMIC will not
    create one, it only selects.
    """
    if not available():
        return False, "cosmic-randr is not installed"
    cmd = [COSMIC_RANDR, "mode", name, str(width), str(height)]
    if refresh is not None:
        cmd += ["--refresh", f"{refresh:g}"]
    if adaptive_sync is not None:
        cmd += ["--adaptive-sync", str(adaptive_sync)]
    res = _run(cmd)
    if res.returncode == 0:
        return True, ""
    return False, (res.stderr or res.stdout or "cosmic-randr failed").strip()


def set_adaptive_sync(name, policy="automatic"):
    """Set adaptive sync. Returns (ok, message).

    `policy` is "true", "false" or "automatic", the three values
    cosmic-randr accepts. The mode is re-applied with the same
    resolution and refresh, because that is how the setting is carried.
    """
    out = find(name)
    if not out:
        return False, "could not read the output configuration"
    mode = out.current_mode
    if not mode:
        return False, "the output has no active mode"
    return set_mode(name, mode.width, mode.height, mode.refresh,
                    adaptive_sync=policy)
