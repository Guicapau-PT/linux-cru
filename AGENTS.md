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
linux-cru.py              launcher: --self-test, --version, --detect
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
  settings.py             the settings kept between runs
  privileged.py           root escalation (pkexec, sudo, password prompt)
  hostenv.py              the environment system tools should see (AppImage)
tests/                    plain Python, no dependencies
docs/research/            the technical reports behind the design
build_appimage.sh         the AppImage build
```

## Running it

```bash
python3 linux-cru.py             # the interface
python3 linux-cru.py --detect    # what the tool sees of this machine
python3 linux-cru.py --self-test # build the whole interface and exit
./build_appimage.sh              # the AppImage (SKIP_APPIMAGE=1 stops at the AppDir)
```

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

### The NVIDIA rule has an exception

For years: "the NVIDIA driver does not accept custom modes from Wayland
compositors, use an EDID override". That is still true for most
compositors, **but not on Plasma 6.8**, where KWin hands the exact
timings over and the driver accepts the mode (verified with NVIDIA
615.78.08 and a 398.640 MHz CVT-RBv2 timing). That matters because the
EDID override it replaces breaks VRR.

### kscreenctl (KDE Plasma 6.8)

`kscreenctl` takes a **full modeline** and applies the mode in the
same step — the change that makes every timing standard usable on
KDE. Four things it does not put in its help text:

- `add-custom-mode` does **not** switch to the mode.
- The **mode ids are renumbered** every time the configuration is
  applied, so an id is only good for the moment it was read. Address a
  mode by what it is (`WxH@RR`), and find a just-added mode by which
  id *appeared*.
- A custom mode **that is in use cannot be removed**: the command
  exits 0 and does nothing. Switch away first, then remove.
- The refresh rate is matched with a tolerance of 0.01 Hz. A display
  that advertises 99.98 and a tool that asks for 100.00 are 0.02 Hz
  apart, and a looser comparison picks the wrong one.

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

- Version 0.3.0 (`linux_cru/__init__.py`). The CI checks that a `v*`
  tag matches it.
- Works: X11 (xrandr + xorg.conf), KWin/Plasma 6.6+ (kscreen-doctor)
  and 6.8+ (kscreenctl), sway, Hyprland, the wlroots family, COSMIC
  and GNOME through the EDID override.
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
- **Plasma 6.8's HDR controls** (`kscreenctl set-hdr`, `set-wcg`,
  `set-maxbpc`) are not exposed in the interface yet.
- The **mode list of an output** on KDE comes from
  `kscreenctl list-outputs --json`, which does not include the custom
  modes as entries with ids; the custom ones are listed separately.

## The machine this was developed on

CachyOS, kernel 7.2 (cachyos), NVIDIA 615.78.08, an AOC U34E2M
(3440x1440 @ 100 Hz, DisplayPort), COSMIC and KDE Plasma 6.8.0 on
Wayland. The display's own EDID advertises 2560x1440 only through
DisplayID, so it is a good test case for the EDID override.

## Making a release

```bash
git tag -a v0.3.0 -m "..." && git push origin main && git push origin v0.3.0
```

The workflow in `.github/workflows/build.yml` builds the AppImage on
every push to `main`, and publishes a release when a `v*` tag is
pushed. It fails if the tag does not match `linux_cru.__version__`.
