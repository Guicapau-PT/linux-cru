# The Linux CRU guide

A plain-language guide to the program: what it does, what every part
of it means, and how to do the two things people want — *a resolution
your monitor doesn't advertise*, and *stretching a smaller picture to
fill the whole screen*.

---

## 1. What this program is

Linux can drive a display with **any** timings its hardware can carry,
but most desktops only offer you the ones your monitor declares in its
EDID (the little data sheet the display hands to your PC). If a
resolution or refresh rate is not in that list, your desktop's display
settings simply never show it — and Linux gives you no control panel
to add it.

Linux CRU is that control panel. It computes exact timings for any
width/height/refresh combination and, depending on your desktop, adds
it **as a full modeline straight into the display server** (KDE Plasma
6.8), writes it into configuration files (X11 / the NVIDIA setup), or
slips it into your display's EDID so the mode appears like any other
everywhere.

It does not force anything: applying a mode makes it *available to
select*. Your desktop stays at whatever mode it was until you (or the
desktop) choose it.

## 2. The three layers you need to understand

Everything in this program follows from this picture:

```
  ┌──────────────┐   hands the desktop its mode list   ┌──────────────┐
  │   kernel /   │ ─────────────────────────────────▶ │  desktop     │
  │ GPU driver   │   (the connector's "modes" file)   │ (KWin, sway…)│
  └──────────────┘                                    └──────┬───────┘
        ▲                                                    │ renders onto
        │ monitor announces itself here (EDID: what the      ▼
        │ display says it can do)                    ┌──────────────┐
  ┌─────┴─────┐                                      │ the monitor  │
  │  display  │                                      │ (panel + OSD)│
  └───────────┘                                      └──────────────┘
```

- **The display** — your monitor — tells the driver what it can do
  (the EDID list) and how it scales a picture that is smaller or
  bigger than the panel.
- **The kernel / GPU driver** accepts timings that obey the link's
  limits and keeps its own copy of the mode list.
- **The desktop** (the compositor) reads the driver's list — usually
  *once*, when it starts — and shows it to you in display settings.

The rule the whole tool follows: *a mode that is not in these lists
does not exist*, and the tool's job is to put it there — and each
layer must be able to produce it for it to actually appear.

## 3. The window, from top to bottom

### Header
Shows your session type (Wayland/X11), desktop and version, and GPU
drivers. It also gives the one-line summary of which method the tool
will use — if this says "KDE Plasma takes the timings as they are",
you are on the easiest path there is.

### "Display" card (top left)
- **Dropdown** — the outputs that are connected. Pick the display you
  want to change. **Rescan** re-reads the connected displays after you
  replug one.
- **"Modes this display offers"** — the list the desktop currently
  has. Rows with higher refresh rates for the same resolution are
  what you are missing. Clicking a row fills the fields below with
  that resolution/refresh — a good starting point to *copy* and then
  edit.
- The small text under the dropdown repeats what the monitor and
  desktop report (including VRR/HDR capability when Plasma 6.8+
  reports it).

### "Display settings" card (Plasma 6.8 and newer only)
Turns HDR, wide colour gamut, colour depth (bits per colour), the
scale (100–200 %) and rotation on for the selected output. These are
the same settings Plasma's own screen settings section changes — they
are here so the mode *and its look* can be set in one place.

### "Custom mode" card
- **Preset chips** — one-click: 1280x720 … 2560x1440, and so on.
- **Width / Height / Refresh** — type anything the link can carry
  (e.g. `2560`, `1440`, `100`). The refresh dropdown suggests what
  your display already advertises.
- The note under the fields tells you whether the timing the tool
  calculated is a known easy one or something unusual.

### "Timing" card
This is the *shape of the sync signal* for "width by height at N Hz" —
there is more than one way to draw it:

| Standard | Note under the button | What it means |
|---|---|---|
| **CVT-RBv2** | recommended for modern displays | The modern reduced-blanking timing. Lowest signal overhead, what new LCD/OLED panels accept. Start here. |
| **CVT-RB** | reduced blanking v1 | The older reduced-blanking. Use it if a display rejects CVT-RBv2. |
| **CVT** | full blanking, for CRTs | The big-blanking classic; anything TVs/old devices expect. |
| **GTF** | for CRT monitors | The CRT-era formula. |
| **Manual** | paste an exact modeline | You have a modeline from somebody else (`cvt`, another forum, a database) and want those *exact* numbers. |

Rule of thumb: **CVT-RBv2**. If **Test mode** fails, the signal was
probably outside what your display or cable can carry — try CVT-RB,
or a lower refresh rate, or a smaller pixel clock.

### "Options" card
- **"Apply from the kernel command line"** — only used together with
  an EDID override (see §6). It makes the mode known *before* the
  desktop and even the boot console exist; it touches your boot
  configuration, and that is what the hint warns about.
- **Adaptive sync (VRR)** — set the VRR policy of the selected
  display, when the desktop reports VRR support (Plasma 6.8).
- **"Stretch to fill the screen"** (KDE on Wayland) — see §5 below.

### Right side: "Preview" and "Log"
- **Preview** shows *exactly* what clicking Apply would do: the
  calculated modeline and, per environment, the real commands or
  files. Nothing in this program happens that was not shown here
  first.
- **Log** is the record of what the tool actually did and answered —
  useful when something fails.

### Footer
- **Test mode (15s)** — applies the mode, waits 15 seconds, and
  reverts *automatically*. Black screen or garbage? It is gone before
  you can reach a foot switch.
- **Apply** — keeps it: adds the mode permanently (see §6 for what
  "permanently" means per desktop).
- **Remove** — undoes everything this tool ever installed: overrides
  and configuration files.
- **Guide** — opens this document.
- Bottom right: **the version**. A newer release is announced next to
  the version automatically; clicking it asks again now, and opens
  the release page when there is news. (Or run
  `python3 linux-cru.py --check-updates` from the terminal.)

## 4. First: what *not* to use the tool for

If you want your display's native resolution and refresh rate and your
desktop already offers it — **you do not need this tool at all.**
Pick it in your desktop's display settings and enjoy.

The tool earns its place in three cases:
1. a mode the display never advertised (§4.1 below),
2. a refresh rate that is missing even though the panel can do it,
3. stretch/scaling behaviour the desktop refuses (§5).

### 4.1 You want 2560x1440 on your 3440x1440 display, black bars are fine

*(this walkthrough uses the AOC U34E2M defaults; the numbers change,
the steps don't)*

1. In **Display**, pick your output (DP-1 here).
2. In **Custom mode**, click the **2560x1440** chip (or type
   2560/1440).
3. **Refresh**: your panel's real rate — type `100` (or whatever the
   display runs at; the chip list of the current mode tells you).
4. **Timing**: leave **CVT-RBv2**.
5. Read the **Preview**. On Plasma 6.8 with the NVIDIA driver it says
   the exact timings go to the driver through kscreenctl, nothing is
   written, and it shows you the two commands (`add-custom-mode`,
   then `set-mode`).
6. Click **Test mode**. You have 15 seconds to look at it.
7. If the picture looked right, click **Apply**.

You now have a 2560x1440 area inside your 3440x1440 panel. Whether it
has black bars depends on **who scales it**:

- The **display itself** may upscale it to fill the panel — look at
  your monitor's OSD: a "scaling" or "aspect/full/1:1" setting decides
  it (the OSD setting survives everything; set it here and you're
  done).
- If the picture is **stretched** instead and you want **bars**, use
  the "Stretch to fill the screen" section further down and pick
  **Fill keeping aspect** — see §5.

On Plasma 6.8 the mode is saved by the desktop in
`~/.config/kwinoutputconfig.json`, so 2560x1440 stays in your mode
list after reboots. To get rid of it again: switch to another mode in
your display settings, then **Remove** (the tool takes custom modes
away when it removes the override; a custom mode you added for
KScreen stays until removed the same way).

## 5. Stretch to fill the screen (or: the black-bar control)

The classic use: a small 4:3-ish picture widened across the whole
panel, so shapes look wider — or the opposite of what §4.1 did: a
16:9 picture *pillarboxed*: whole height with bars at the sides —
that's what "Fill keeping aspect" is.

Each small mode shows up in one of two ways: **the display upscales
it to fill the panel** (no bars, the shape of the picture gets
stretched), or the **panel shows it 1:1 centred** with bars around it:
what happens depends on the monitor's OSD as well as on the desktop.

The desktop's own screen scaling (KWin): on external displays KWin
comes out of the box with the scaling *off* (it leaves the pixels 1:1
within the mode), and so the picture at e.g. 1280x960 shows *small*,
not stretched. Its behaviour is not a user-facing option in Plasma
settings — it is controlled by the environment variable
`KWIN_DRM_FORCE_SCALING_MODE`, which this tool writes for you.

So the flow to test the stretch is:

1. **Apply a smaller custom mode first** (§4.1's steps — e.g.
   1440x1080 at your panel's rate).
2. In the "Stretch to fill the screen" section pick:
   - **Stretch to fill** → the picture covers the *whole* panel, wider
     and taller: the classic "stretched" gaming look. Use this when
     the object of the game is a wider view.
   - **Fill keeping aspect** → the picture keeps its own shape and
     shows with **bars**; the rest is left empty. This is the
     **black-bars** answer from §4.1, in tool form.
   - **Don't force scaling** → off (leave your OSD to do it).
3. Click **Apply**. 
   The first thing that happens is a *live* change: the running KWin
   is told to scale, needs root for a second (the tool explains why),
   and the effect is visible **immediately** with whatever mode is
   active — no logout needed.
4. The tool then asks: **keep this for future sessions?**
   - **Yes** → the setting goes to `~/.config/environment.d/linux-cru-stretch.conf`,
     read by your session at every login from now on. It survives
     reboots; it lasts until you turn it off again.
   - **No** → it only lasts until you log out. The live poke does not
     survive a KWin restart (logout/login); only the saved setting does.
5. **Revert** — turns it off now and removes the saved setting.

Notes:
- This tool pokes at KWin's *method of deciding*, not at it: the
  change only stops at a KWin update. When an update breaks it, the
  preview will visibly say so and the Apply will not pretend to work.
- VRR (the tear-free variable-rate) and the stretch have nothing to
  do with each other; they coexist without issue.
- If you want to confine it to the monitor OSD instead: set the OSD's
  "display scaling" to the picture shape you like and leave the tool's
  stretch off.

## 6. What "Apply" actually does, per environment

| Your setup | What Apply does | Where the mode lives after reboot |
|---|---|---|
| **KDE Plasma 6.8** (Wayland, any driver incl. NVIDIA) | Adds the exact timed modeline to KWin (kscreenctl), then switches to it. Nothing is written system-wide, and **VRR and HDR keep working**. | KWin's own config: `~/.config/kwinoutputconfig.json`. |
| **KDE Plasma 6.6–6.7** | kscreen-doctor gets width/height/refresh and computes CVT or CVT-RB itself. | Same config file. |
| **sway / Hyprland** | The compositor is told the exact modeline. | The compositor's own config. |
| **GNOME / COSMIC / anything else on Wayland** | The desktop cannot add modes; the mode is slipped into your display's **EDID** instead (an "override"). | The override is re-applied at boot by a small systemd service (default), or, with the **"kernel command line"** option, early enough for the boot console. |

On GNOME 48 and newer the tool also reads what Mutter knows through `gdctl` and can switch to a mode it already has -- including the one the override added, once the desktop has read it -- while keeping the scale, rotation and the other displays. GNOME's adaptive sync is a mode variant there, so the tool switches to the `+vrr` variant instead of offering a separate policy. The refresh rate of the selected display can be changed from the Display settings card, which is the setting that makes the desktop feel smooth. |
| **Any X11 session** | An xorg.conf snippet in `/etc/X11/xorg.conf.d/` with the modeline; on NVIDIA with checks relaxed / CustomEDID as per the Options card. | The xorg.conf file, until Remove or reconfiguration. |

About the EDID override: your monitor's data sheet is replaced by a
patched one that contains the extra mode. Two caveats the tool will
warn you about when they apply:
- On **NVIDIA**, while the override is in place the display loses
  VRR/HDR reporting (the patched EDID is shorter than the display's
  own). This is why the tool prefers the compositor path when it can.
- The kernel-command-line form matters at **boot**: removing it (or
  the boot service) is always done by **Remove** — and if your
  desktop refuses to start, see §7.

## 7. If something goes wrong

- **Test mode** always reverts itself in 15 seconds; keep using it
  until a mode is proven.
- Double vision, mode not selectable after a replug: some desktops
  only read the monitor's list when they start. Log out/in — or use
  the tool's rescan, which asks the desktop again.
- "The display/panel went black permanently": replug or
  reboot — the mode then stops being active even if it is still in
  the list.
- The Guide button (and a click on the version when a newer release
  exists) asks the desktop's default browser to open the page. When
  nothing answers, the tool says so instead of claiming victory, and
  the address is in the status line and the log — set your default
  browser in your desktop's settings (KDE: System Settings →
  Applications → Default Applications → Web Browser), or $BROWSER,
  and try again.
- If a whole *session* refuses to come up (login screen frozen),
  boot, then press **Ctrl+Alt+F3** for a text terminal (or SSH in),
  and run:
  ```
  python3 linux-cru.py --remove-overrides
  ```
  It undoes everything the tool installed and needs no graphical
  interface; add `--yes` to skip the confirmation, or
  `--connector DP-1` to only do one display. **Reboot** afterwards.
- A mode from the kernel command line can also be taken off at the
  boot menu, when the display dies before the tool's Remove can run:
  the tool's entry is visibly marked in your bootloader config.

## 8. Doors and drawers (where things live)

| What | Where |
|---|---|
| The tool's own settings | `~/.config/linux-cru/settings.json` |
| KWin's display config (Plasma's own mode list) | `~/.config/kwinoutputconfig.json` |
| Patched EDIDs (override method) | `/var/lib/linux-cru/firmware/edid/` |
| Boot service for overrides | `/etc/systemd/system/linux-cru-edid.service` |
| Kernel cmdline, if that option was used | your bootloader's config, marked `# linux-cru: managed line` |
| The stretch setting | `~/.config/environment.d/linux-cru-stretch.conf` |
| xorg.conf (X11 route) | `/etc/X11/xorg.conf.d/10-linux-cru.conf` |

Most pieces only matter while they are in use; Remove tidies them up
in one go, `--detect` shows what is where from the command line.

## 9. Glossary, ultra-short

- **mode** — one width × height at one refresh rate with its exact
  sync shape.
- **EDID** — the data sheet your monitor broadcasts; the tool can
  *replace* it (an **override**) to add modes.
- **override** — the patched EDID, live or at boot.
- **modeline** — a single line of numbers: pixel clock plus the eight
  timing fields and sync flags.
- **CVT / GTF** — the two families of formulas that "shape" a mode
  out of width × height × refresh; **RB** variants use reduced
  blanking (modern flat panels).
- **compositor** — the desktop's display server process (KWin, sway,
  Hyprland…).
- **VRR / adaptive sync** — variable refresh rate, the tear-free
  display trick; reportable per monitor and per override state.
- **DRM scaler** — the GPU's fixed-fuction scaling path; what
  "stretch to fill" turns on.

The deeper technical write-ups live in [docs/research](docs/research/).
