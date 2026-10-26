# AGENTS.md

Working notes for anyone (human or agent) continuing this project.
It describes what the project is, how it is put together, what was
learned the hard way, and what is still open.

**The upstream project is [PhialsBasement/linux-cru](https://github.com/PhialsBasement/linux-cru).**
This is a fork. The research, the VESA timing calculators, the EDID
engine, the KWin stretch work and the overall design are theirs, and
the copyright in `LICENSE` is theirs. See the Acknowledgments in the
README.

---

## What this is

A graphical utility for creating and applying custom display
resolutions and refresh rates on Linux — the Linux answer to the
Windows Custom Resolution Utility. Tkinter, no third-party Python
dependencies, shipped as an AppImage.

The hard part is not the arithmetic (that is `cvt`/`gtf`), it is
**working out which of the many display paths this machine uses** and
driving the right one: xrandr on X11, native custom modes on KWin and
the wlroots family, or a kernel EDID override everywhere else.

## Layout

```
linux-cru.py              launcher: --self-test, --version, --detect,
                          --remove-overrides, --check-updates
linux_cru/
  app.py                  the interface (the whole GUI)
  theme.py                the palette, the ttk styles, the fonts
  detect.py               session, compositor, GPU, connectors, capabilities
  timings.py              CVT, CVT-RB, CVT-RBv2, GTF, modeline parsing
  edid.py                 EDID parsing and patching (DTD + DisplayID)
  override.py             the runtime EDID override script
  persist.py              the boot-time EDID override (systemd, initramfs, cmdline)
  wayland.py              the compositor backends
  kwin.py                 kscreenctl (KDE Plasma 6.8 and newer)
  cosmic.py               cosmic-randr (COSMIC)
  stretch.py              stretched resolutions on KWin (a memory poke, see below)
  xorg.py                 the X11 configuration and its install scripts
  rescue.py               what is installed, and undoing it without the interface
  update.py               update checks against the fork's releases
  settings.py             the settings kept between runs
  privileged.py           root escalation (pkexec, sudo, password prompt)
  hostenv.py              the environment system tools should see (AppImage)
tests/                    plain Python, no dependencies
docs/guide.md             the plain-language guide for users
docs/research/            the technical reports behind the design
build_appimage.sh         the AppImage build
```

## Running it

```bash
python3 linux-cru.py             # the interface
python3 linux-cru.py --detect    # what the tool sees of this machine
python3 linux-cru.py --self-test # build the whole interface and exit
python3 linux-cru.py --remove-overrides   # undo everything installed, without a desktop
python3 linux-cru.py --check-updates      # ask the releases API once, now
./build_appimage.sh              # the AppImage (SKIP_APPIMAGE=1 stops at the AppDir)
```

The command-line flags run **before** tkinter is imported, so they
work wherever the interface cannot start: a TTY after a frozen login,
or a machine without Tk. `--detect` reports the session plus what the
tool has installed; `--remove-overrides` (`--yes`, `--connector`)
removes all of it and asks for root with a terminal password prompt
when no authentication agent is around. A non-interactive run without
`--yes` refuses instead of acting.

## The tests

```bash
for t in tests/test_*.py; do echo "== $t"; python3 "$t"; done
```

Plain Python, no framework, no fixtures. They print `PASS`/`FAIL` per
test and exit non-zero on a failure. The ones that build the interface
need a display; CI runs them under `xvfb-run`.

**Two rules the tests have to keep:**

1. **They must not read the state of the machine they run on.** A test
   that asks "what is installed?" has to stub the answer. This bit
   twice: the tests about the Remove button passed on a clean machine
   and failed as soon as an EDID override was really installed.
2. **They must not need Plasma, KDE or any other desktop.** The
   `kscreenctl` tests use `tests/kscreenctl_stub.py`, which puts a fake
   kscreenctl on the PATH *and* patches `shutil.which`, so the stub is
   the only kscreenctl the tool can find. The COSMIC tests do the same
   with `cosmic.outputs`.

## Conventions

- One module per concern, each with a docstring saying what it is
  for and what it refuses to do.
- Everything that touches the system goes through a generated script
  (`persist`, `override`, `xorg`) or a small backend module
  (`kwin`, `cosmic`, `wayland`) — so it can be read before it runs and
  tested without root.
- Subprocesses always go through `hostenv.subprocess_env()`: inside
  the AppImage the bundle's `PYTHONHOME`/`LD_LIBRARY_PATH` would
  otherwise be handed to system tools, which then load the bundle's
  libraries instead of their own.
- Comments explain *why*, not *what*. The commit messages are one
  line, imperative, and say what changed and why.
- The palette comes from `theme.py`; do not hard-code colours in the
  interface.

## What was learned the hard way

These are all verified on real hardware, not read off a web page. The
research notes in `docs/research/` have the details.

### The kernel having a mode and the desktop having it are two things

An EDID override reaches the kernel at once: the connector's mode list
has the new mode immediately. The compositor keeps **its own copy** of
that list and may not read it again while it runs. On COSMIC it does
not: neither `status=detect` nor `trigger_hotplug` makes cosmic-comp
look at the display again, and `cosmic-randr mode` answers
`ModeNotFound`. A tool has to wait for the *compositor's* list, and
say "the desktop has not read the display yet" — otherwise the failure
looks like "the driver refused the mode", which is a different problem
with a different fix.

### A mode the display advertises may still not exist

An AOC U34E2M advertises 2560x1440 — but only in its DisplayID
extension, and the NVIDIA driver does not turn DisplayID timings into
modes. The same timing written as a CTA-861 detailed timing descriptor
works at once. The tool writes the CTA DTD for this reason.

### The NVIDIA rule has an exception, but the driver has the last word

For years: "the NVIDIA driver does not accept custom modes from Wayland
compositors, use an EDID override". With Plasma 6.8's modeline support
the *timings* are no longer the problem: the exact timings reach the
driver and it applies them (verified with NVIDIA 615.78.08 and a
398.640 MHz CVT-RBv2 timing). What the driver still insists on is a
**resolution it already has** — it will not switch to a resolution that
is not in the connector's mode list, however the compositor offers it
(`add-custom-mode` succeeds, `set-mode` then fails with "the driver
rejected the output configuration").

So the tool asks the driver what it has — the connector's `modes` file
— and picks the method accordingly: the compositor when the resolution
is known, the EDID override when it is not. A resolution the display
only advertises through DisplayID is not in that list, so it needs the
override; that is the case for the AOC U34E2M and 2560x1440.

Two consequences worth remembering:

- The EDID override breaks VRR on NVIDIA, and the compositor route does
  not — so the route that does not need the override is the better one.
- A custom mode the compositor keeps in its configuration while the
  driver cannot produce it makes **every** configuration apply fail,
  and the desktop says only "the driver rejected the output
  configuration". Taking the override away therefore has to take the
  custom modes with it; the tool does that when it removes an override.

### kscreenctl (KDE Plasma 6.8)

`kscreenctl` takes a **full modeline** and applies the mode in the
same step — the change that makes every timing standard usable on
KDE. Six things it does not put in its help text:

- `add-custom-mode` does **not** switch to the mode. A `set-mode`
  is what applies it.
- The **mode ids are renumbered** every time the configuration is
  applied, so an id is only good for the moment it was read. Address a
  mode by what it is (`WxH@RR`), and find a just-added mode by which
  id *appeared*. On 6.8.1 the JSON answer lists the custom modes with
  the others and gives them an id, so the id can be read there — the
  `list-modes` fallback is for the answers that do not.
- A custom mode **that is in use cannot be removed**: the command
  exits 0 and does nothing. Switch away first, then remove.
- The refresh rate is matched with a tolerance of 0.01 Hz. A display
  that advertises 99.98 and a tool that asks for 100.00 are 0.02 Hz
  apart, and a looser comparison picks the wrong one.
- The **scale is a percentage** ("125%"); a bare decimal is read as a
  percentage too, so "1.25" is refused with "at least 50%". `set-maxbpc`
  takes whole numbers from 6 to 16, and 0 (the "unset" the JSON
  reports) is not one of them.
- The **mode list it reports is not sorted** (1, 10, 11 … 2, 20 …) and
  holds the same timing once for every descriptor it reached the
  display through; the backend sorts and takes the doubles out
  (`kwin.mode_entries`).

The JSON answer is also where the display settings live: scale,
rotation, position, HDR, wide colour gamut, the colour depth
(`maxbpc`), the colour range and the VRR policy, plus a
`capabilities` list ("hdr", "vrr", "custom-modes", "hdr-icc"…).
`hdr-icc` is **no proof of HDR** — an SDR panel reports it while
saying HDR is incapable — so the tool looks for `hdr` and nothing
else.

Every kscreenctl call is a process and a DBus round trip, ~50 ms on
this machine. The interface therefore asks off its own thread
(worker + a queue drained by a timer), and the backend keeps the
answers for a second (`kwin.invalidate()` on any change,
`kwin.reset()` for the tests). A window that calls kscreenctl on its
main thread stops drawing, which is the stutter the tool was built to
remove. Note for anyone repeating the experiments above: a Tk
variable may only be read on the interface's thread — a worker that
reaches into a `StringVar` dies with "main thread is not in main
loop".

### The EDID override shortens the display's EDID on NVIDIA

Verified live (NVIDIA 615, AOC U34E2M): the override replaces the
display's own three-block EDID (base + CTA + DisplayID) with the
patched one — two blocks, no DisplayID — because the driver reads
only 256 bytes. The display then reports "VRR: incapable" where it
said "capable" before: adaptive sync is lost for as long as the
override is in place, and HDR with it. The compositor's own path
(kscreenctl custom modes) keeps both, which is one more reason to
prefer it when the driver has the resolution.

### COSMIC

Reads everything through `cosmic-randr` (the KDL output is parsed in
`cosmic.py`). COSMIC cannot create a mode of its own (upstream bug
#2577), so the mode goes in through the EDID — and because COSMIC
reads the modes when it starts, that has to happen at boot, which is
what the systemd service is for.

### The stretch feature is a hack

`stretch.py` patches the running KWin's memory to force the DRM
scaler, because KWin refuses to scale external monitors. It finds the
target by byte signature, so it does nothing rather than corrupting a
KWin it does not recognise — but it will stop working when KWin
changes. It is marked as a hack in the source; do not build on it.

## Current state

- Version 0.4.0 (`linux_cru/__init__.py`). The CI checks that a `v*`
  tag matches it.
- Works: X11 (xrandr + xorg.conf), KWin/Plasma 6.6+ (kscreen-doctor)
  and 6.8+ (kscreenctl), sway, Hyprland, the wlroots family, COSMIC
  and GNOME through the EDID override. On GNOME 48+ the modes Mutter
  has and the display settings (scale, rotation, refresh rate, VRR) are
  read and set through gdctl, so the patched mode can be selected
  without leaving the tool.
- On Plasma 6.8 the interface also sets what the display settings
  hold: HDR, wide colour gamut, the colour depth, the scale and the
  rotation (`kscreenctl set-hdr/set-wcg/set-maxbpc/set-scale/
  set-rotation`), and it shows what the desktop reports. The VRR
  control follows the policy the output already has and is switched
  off for a display that cannot do it.
- Everything the interface asks from kscreenctl is asked off the
  interface's thread (worker → queue → timer), and `kwin` keeps its
  answers briefly; `kwin.invalidate()` drops them after a change.
- The EDID override engine is the universal fallback: patched EDID,
  applied live through DRM debugfs, or kept across boots by a systemd
  service (the default) or the kernel command line.

## Open things

- **AppStream metadata** is missing (`usr/share/metainfo/*.appdata.xml`),
  which appimagetool warns about. Cosmetic — it only affects how the
  app is listed in software centres.
- **The stretch hack** should be deleted if KWin ever grows a setting
  for it.
- **COSMIC**: a live EDID override needs a real replug to become
  visible; the software replug (`disconnect` then `detect` in the
  connector's `status`) is offered by the tool but has not been
  verified against COSMIC.
- **VRR vs. the EDID override on NVIDIA**: while an override is in
  place the display loses adaptive sync (a shortened, two-block EDID).
  The interface says so, but a nicer answer — putting the DisplayID
  block back into the patched EDID — has not been built. See
  "The EDID override shortens the display's EDID" above.

## The machine this was developed on

CachyOS, kernel 7.3.0 (cachyos) — reinstalled fresh with KDE Plasma
6.8.1 on Wayland. NVIDIA 615.78.08, an AOC U34E2M (3440x1440 @ 100 Hz,
DisplayPort). The display's own EDID advertises 2560x1440 only through
DisplayID, so it is a good test case for the EDID override.

Before the reinstall the machine ran kernel 7.2 and froze at the
Plasma login screen after the 7.2→7.3 kernel update; force-offs did
not help. The fresh install came back — the tool can install things
near the boot path, so the lesson it took from that is a lesson for
the tool as well: recovery must not depend on the desktop or on Tk.
That is what `linux_cru/rescue.py` and the two CLI flags are for, and
the launcher now dispatches all flags before tkinter is imported.
Remember the tool's state when a system stops booting properly:
`--remove-overrides` from a TTY is the undo.

The system Python here has no `tk`; the GUI tests run after unpacking
an Arch `tk` package into a plain directory and pointing the
environment at it, which needs no root (tk sits in `extra` these
days; tcl is already installed):

```bash
curl -fsSLo /tmp/opencode/tk.pkg.tar.zst \
  https://geo.mirror.pkgbuild.com/extra/os/x86_64/tk-8.6.16-1-x86_64.pkg.tar.zst
tar -xf /tmp/opencode/tk.pkg.tar.zst -C /tmp/opencode/tkroot
export LD_LIBRARY_PATH=/tmp/opencode/tkroot/usr/lib \
       TK_LIBRARY=/tmp/opencode/tkroot/usr/lib/tk8.6
```

## Making a release

Bump the version, write the notes, then tag. The publish step reads
the notes from the repository (`docs/releases/vX.Y.Z.md`), and a
missing file fails the release after the AppImage has been built --
it happened with 0.4.0.

```bash
$EDITOR docs/releases/v0.4.1.md     # the notes, in the style of the last ones
sed -i 's/__version__ = "0.4.0"/__version__ = "0.4.1"/' linux_cru/__init__.py
git commit -am "Prepare the 0.4.1 release"
git tag -a v0.4.1 -m "..." && git push origin main && git push origin v0.4.1
```

The workflow in `.github/workflows/build.yml` builds the AppImage on
every push to `main`, and publishes a release when a `v*` tag is
pushed. It fails if the tag does not match `linux_cru.__version__`.
