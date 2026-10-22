"""Rescue tests.

The undo path is for a system whose login is broken, so it has to be
testable exactly there: the summaries describe only files this tool
owns, and the removal builds the same root scripts the Remove button
builds, without anyone needing a graphical session.

The paths are redirected into a temp directory (like the persistence
tests do) and run_as_root is stubbed, so nothing outside the sandbox
is ever touched and no root is involved. By the house rule the tests
never ask the real machine what is installed: the sandbox answers.
"""

import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from linux_cru import persist, privileged, rescue, stretch, xorg


def _sandbox():
    """Point every module path into a temp tree and build the dirs."""
    root = tempfile.mkdtemp(prefix="cru-rescue-")
    persist.FIRMWARE_SEARCH_PATH = f"{root}/var/lib/linux-cru/firmware"
    persist.FIRMWARE_DIR = f"{root}/var/lib/linux-cru/firmware/edid"
    persist.MKINITCPIO_DROPIN = f"{root}/etc/mkinitcpio.conf.d/99-linux-cru.conf"
    persist.DRACUT_DROPIN = f"{root}/etc/dracut.conf.d/99-linux-cru.conf"
    persist.INITRAMFS_TOOLS_HOOK = f"{root}/etc/initramfs-tools/hooks/linux-cru"
    persist.GRUB_DEFAULT = f"{root}/etc/default/grub"
    persist.LIMINE_DEFAULT = f"{root}/etc/default/limine"
    persist.KERNEL_CMDLINE = f"{root}/etc/kernel/cmdline"
    persist.SYSTEMD_UNIT = f"{root}/etc/systemd/system/linux-cru-edid.service"
    persist.SYSTEMD_HELPER = f"{root}/usr/lib/linux-cru/apply-edid.sh"
    persist.MKINITCPIO_CONF = f"{root}/etc/mkinitcpio.conf"
    xorg.XORG_CONF_DIR = f"{root}/etc/X11/xorg.conf.d"
    xorg.XORG_CONF = f"{xorg.XORG_CONF_DIR}/{xorg.XORG_CONF_NAME}"
    xorg.CUSTOMEDID_DIR = f"{root}/etc/X11"
    stretch.ENVIRONMENT_D_FILE = \
        f"{root}/home/.config/environment.d/linux-cru-stretch.conf"
    for d in (persist.FIRMWARE_DIR,
              f"{root}/etc/systemd/system/graphical.target.wants",
              xorg.XORG_CONF_DIR,
              os.path.dirname(stretch.ENVIRONMENT_D_FILE),
              f"{root}/etc/default"):
        os.makedirs(d, exist_ok=True)
    return root


def _unit_wants_link():
    return (f"{os.path.dirname(persist.SYSTEMD_UNIT)}"
            f"/graphical.target.wants/"
            f"{os.path.basename(persist.SYSTEMD_UNIT)}")


class _StubRoot:
    """Catch the root scripts instead of running them."""

    def __init__(self):
        self.calls = []
        self.scripts = []
        self.ok = True

    def __call__(self, argv, ask_password=None, allow_pkexec=True):
        with open(argv[1]) as f:
            self.scripts.append(f.read())
        argv = list(argv)
        argv[1] = "[script captured]"
        self.calls.append(argv)
        return privileged.Result(self.ok, "stub ran the script")


def _out():
    """A fresh buffer per test/section, so reports do not bleed over."""
    return io.StringIO()


def test_nothing_installed_says_so_and_asks_for_no_root():
    _sandbox()
    rescue.privileged.run_as_root = stub = _StubRoot()
    out = _out()

    assert rescue.lines() == ["nothing is installed"], rescue.lines()
    st = rescue.state()
    assert not st["installed"], st

    assert rescue.undo(out=out.write) == rescue.NOTHING
    assert stub.calls == [], "root was asked for when there was nothing to do"


def test_systemd_method_is_reported_and_the_scripts_are_built():
    _sandbox()
    rescue.privileged.run_as_root = stub = _StubRoot()
    out = _out()

    with open(f"{persist.FIRMWARE_DIR}/linux-cru-DP-1.bin", "wb") as f:
        f.write(b"\x00" * 256)
    with open(persist.SYSTEMD_UNIT, "w") as f:
        f.write("[Unit]\n")
    os.symlink(persist.SYSTEMD_UNIT, _unit_wants_link())
    with open(xorg.XORG_CONF, "w") as f:
        f.write("# generated\n")
    with open(stretch.ENVIRONMENT_D_FILE, "w") as f:
        f.write("KWIN_DRM_FORCE_SCALING_MODE=FULL\n")

    report = "\n".join(rescue.lines())
    assert "boot service" in report, report
    assert persist.SYSTEMD_UNIT in report, report
    assert "(enabled)" in report, report
    assert "DP-1" in report, report
    assert xorg.XORG_CONF in report, report
    assert "linux-cru-stretch.conf" in report, report

    outcome = rescue.undo(yes=True, out=out.write)
    assert outcome == rescue.DONE, (outcome, out.getvalue())

    # Two root scripts: the EDID overrides first, the X11 files second.
    assert len(stub.calls) == 2, stub.calls
    edid_script, xorg_script = stub.scripts
    assert "remove persistent EDID override" in edid_script, edid_script[:200]
    assert "update_systemd" in edid_script, \
        "the boot service must be taken away too"
    # No connector was named, so every override file goes (the glob form).
    assert 'rm -f "$FWDIR/$PREFIX"*.bin' in edid_script, edid_script[:400]
    assert f"rm -f '{xorg.XORG_CONF}'" in xorg_script, xorg_script[:200]

    # The user-level stretch setting needs no root and goes with the rest.
    assert not os.path.exists(stretch.ENVIRONMENT_D_FILE)


def test_cmdline_method_includes_the_bootloader_cleanup():
    _sandbox()
    rescue.privileged.run_as_root = stub = _StubRoot()
    out = _out()

    with open(f"{persist.FIRMWARE_DIR}/linux-cru-DP-1.bin", "wb") as f:
        f.write(b"\x00" * 256)
    with open(persist.LIMINE_DEFAULT, "w") as f:
        f.write('KERNEL_CMDLINE[default]+=" quiet "\n'
                f"{persist.MARKER}\n"
                'KERNEL_CMDLINE[default]+=" drm.edid_firmware=DP-1:edid/"\n')

    report = "\n".join(rescue.lines())
    assert "kernel command line" in report, report
    assert "DP-1" in report, report

    assert rescue.undo(yes=True, out=out.write) == rescue.DONE
    assert len(stub.calls) == 1, stub.calls
    script = stub.scripts[0]
    assert "update_bootloader" in script, "the managed cmdline line must go"
    assert "update_initramfs_config" in script, \
        "and the initramfs file with it"


def test_it_refuses_to_remove_without_confirmation():
    _sandbox()
    rescue.privileged.run_as_root = stub = _StubRoot()
    out = _out()

    with open(f"{persist.FIRMWARE_DIR}/linux-cru-DP-1.bin", "wb") as f:
        f.write(b"\x00" * 256)
    with open(persist.SYSTEMD_UNIT, "w") as f:
        f.write("[Unit]\n")

    # A script or a service answers, or it refuses with --yes; in both
    # cases the file must still be there afterwards.
    saved_tty = rescue._stdin_is_tty
    rescue._stdin_is_tty = lambda: False
    try:
        assert rescue.undo(out=out.write) == rescue.REFUSED
    finally:
        rescue._stdin_is_tty = saved_tty
    assert stub.calls == [], "it removed something without a confirmation"
    assert os.path.exists(f"{persist.FIRMWARE_DIR}/linux-cru-DP-1.bin")

    # A terminal that answers "no" refuses the same way.
    import builtins
    saved_tty = rescue._stdin_is_tty
    saved_input = builtins.input
    rescue._stdin_is_tty = lambda: True
    builtins.input = lambda *a, **kw: ""
    try:
        assert rescue.undo(out=out.write) == rescue.REFUSED
    finally:
        builtins.input = saved_input
        rescue._stdin_is_tty = saved_tty
    assert stub.calls == []


def test_a_boot_method_without_files_is_still_taken_away():
    """An install that went half-way: the unit exists, the EDID does not.

    The unit would then be re-enabled at nothing, and the cmdline line
    would stay in the bootloader until the next install rebuilds it.
    """
    _sandbox()
    rescue.privileged.run_as_root = stub = _StubRoot()
    out = _out()

    with open(persist.SYSTEMD_UNIT, "w") as f:
        f.write("[Unit]\n")

    assert "no override files" in rescue.lines()[0], rescue.lines()
    assert rescue.undo(yes=True, out=out.write) == rescue.DONE
    assert len(stub.calls) == 1, stub.calls


def test_a_root_failure_is_reported_as_failed():
    _sandbox()
    rescue.privileged.run_as_root = stub = _StubRoot()
    stub.ok = False
    out = _out()

    with open(f"{persist.FIRMWARE_DIR}/linux-cru-DP-1.bin", "wb") as f:
        f.write(b"\x00" * 256)

    outcome = rescue.undo(yes=True, out=out.write)
    assert outcome == rescue.FAILED, (outcome, out.getvalue())
    assert len(stub.calls) == 1


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    for name, fun in tests:
        fun()
        print(f"PASS {name}")
