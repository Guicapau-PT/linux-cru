"""What this tool has on the system, and undoing it without the interface.

The interface can remove everything (Remove), but the interface is a
thing that cannot start when the display is broken: a frozen login
screen, a black desktop, a compositor crash loop. Worse, the interface
needs a working Tk, which a plain recovery TTY does not offer either.

Everything here works from a terminal or over SSH: the reads need no
root and only look at files this tool owns, and the removal asks for
root the usual way -- with a terminal password prompt when there is no
authentication agent to talk to.

The removal takes everything the tool writes away in one go: the
persistent EDID overrides (whichever method was used), the X11
configuration and the stretch setting -- what the Remove button does
in the interface, from the command line instead.
"""

import os
import tempfile

from . import persist, privileged, stretch, xorg

DONE = "done"          # everything was removed (or there was nothing to remove)
NOTHING = "nothing"    # nothing installed, nothing to undo
REFUSED = "refused"    # nothing was removed: confirmation was not given
FAILED = "failed"      # something was not removed

REBOOT_HINT = ("Reboot when you can: a mode a driver stops providing, an "
               "override taken away, both only really leave at the next "
               "reboot or replug.")


def state():
    """What this tool has on the system, as a plain dictionary.

    Only reads, and only of what this tool writes: files and units
    with its own names, the kernel command line and its own xorg.conf
    names. A system where the tool never ran reports "not installed".
    """
    st = {
        "edid_method": persist.installed_method(),
        "edid_connectors": persist.installed_connectors(),
        "cmdline_value": persist.active_on_cmdline(),
        "xorg": xorg.installed(),
        "xorg_customedids": xorg.installed_customedids(),
        "stretch": stretch.persistent_value(),
    }
    st["installed"] = bool(st["edid_method"] or st["edid_connectors"]
                           or st["xorg"] or st["xorg_customedids"]
                           or st["stretch"])
    return st


def _unit_enabled():
    """True when the boot service is in graphical.target.wants.

    systemd makes that directory of symlinks when a unit is enabled;
    it is next to the unit and holds a link with its name.
    """
    wants = os.path.join(os.path.dirname(persist.SYSTEMD_UNIT),
                         "graphical.target.wants",
                         os.path.basename(persist.SYSTEMD_UNIT))
    return os.path.exists(wants)


def lines():
    """The installed things, one per line, for a report."""
    st = state()
    out = []

    if st["edid_method"] == persist.METHOD_SYSTEMD:
        connectors = ", ".join(st["edid_connectors"]) or "no override files"
        enabled = " (enabled)" if _unit_enabled() else ""
        out.append(f"EDID override: boot service {persist.SYSTEMD_UNIT}"
                   f"{enabled}, for {connectors}")
    elif st["edid_method"] == persist.METHOD_CMDLINE:
        connectors = ", ".join(st["edid_connectors"]) or "none"
        now = st["cmdline_value"] or "not in use on the running kernel"
        out.append(f"EDID override: kernel command line / initramfs, for "
                   f"{connectors} (running kernel: {now})")

    if st["xorg"]:
        out.append(f"X11 configuration: {xorg.XORG_CONF}")
    if st["xorg_customedids"]:
        out.append("X11 patched EDIDs (NVIDIA): "
                   + ", ".join(st["xorg_customedids"]))
    if st["stretch"]:
        out.append(f"Stretch setting: {stretch.ENVIRONMENT_D_FILE} "
                   f"({st['stretch']})")

    if not out:
        out.append("nothing is installed")
    return out


def confirm(question, yes=False):
    """True when the removal may go ahead.

    --yes answers for itself. A terminal answers with a keypress; a
    pipe or a script (stdin not a terminal) cannot confirm, and gets a
    refusal -- a rescue command that barrels ahead on a maybe is the
    wrong tool to have in a broken system.
    """
    if yes:
        return True
    if not _stdin_is_tty():
        return False
    answer = input(f"{question} [y/N] ").strip().lower()
    return answer in ("y", "yes")


def _stdin_is_tty():
    try:
        return bool(os.isatty(0))
    except OSError:
        return False


def _ask_password(prompt, retry=False):
    """A terminal password prompt, for when there is no auth agent."""
    import getpass
    prefix = "Wrong password, try again: " if retry else (
        f"{prompt}\nPassword: ")
    return getpass.getpass(prefix)


def _run_root_script(script, out):
    """Write a generated root script to a temp file and run it as root."""
    path = os.path.join(tempfile.mkdtemp(prefix="linux-cru-"), "undo.sh")
    try:
        with open(path, "w") as f:
            f.write(script)
        os.chmod(path, 0o755)
        result = privileged.run_as_root(["/bin/bash", path],
                                        ask_password=_ask_password)
    finally:
        try:
            os.remove(path)
            os.rmdir(os.path.dirname(path))
        except OSError:
            pass
    message = result.message.strip()
    if message:
        out(message)
    if not result.ok and not result.cancelled:
        return False
    return True


def undo(connector=None, yes=False, out=print):
    """Remove everything this tool installed. Returns DONE/NOTHING/... .

    connector: remove that one display's EDID override only, when one
    is installed for it; otherwise everything. The X11 configuration
    and the stretch setting are user-wide, so they always go when they
    exist.
    """
    st = state()

    if not st["installed"]:
        out("Nothing this tool installed was found; "
            "nothing here could have broken the login.")
        return NOTHING

    if not confirm("Remove everything listed above?", yes):
        out("Nothing was removed. Run again with --yes to remove "
            "everything without asking.")
        return REFUSED

    ok = True

    edid_targets = st["edid_connectors"]
    if st["edid_method"] or edid_targets:
        # Run the uninstall even without files: the boot service can be
        # there with none left, and the command line method leaves its
        # managed line behind until the script strips it.
        target = connector if connector in edid_targets else None
        what = target or "all of them (" + ", ".join(edid_targets) + ")"
        out(f"Removing the EDID override(s): {what}")
        ok = _run_root_script(persist.build_uninstall_script(target), out) and ok

    if st["xorg"] or st["xorg_customedids"]:
        target = connector if connector in st["xorg_customedids"] else None
        out("Removing the X11 configuration and patched EDIDs")
        ok = _run_root_script(xorg.build_uninstall_script(target), out) and ok

    if st["stretch"]:
        if stretch.remove_persistent():
            out(f"Removed {stretch.ENVIRONMENT_D_FILE}")
        else:
            out(f"Could not remove {stretch.ENVIRONMENT_D_FILE}")
            ok = False

    out("")
    out(REBOOT_HINT)
    return DONE if ok else FAILED
