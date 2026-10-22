#!/usr/bin/env python3
"""Linux Custom Resolution Utility.

The graphical front-end lives in linux_cru.app; this file is the
launcher that the AppImage runs. It picks the tool for the session it
finds itself in, works out which mode-injection paths are available
(see docs/RESEARCH.md) and shows exactly what it would do before it
does it.

The command line also knows what to do when the interface cannot
start -- which is exactly the situation where it is needed most: a
frozen login screen, or a machine without a working Tk. Every flag
here runs without tkinter; only the window needs it, so the flags
finish their work from a plain TTY or over SSH.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def self_test():
    """Build the whole interface once and exit. Used to check a build."""
    import tkinter as tk

    from linux_cru import app, timings

    root = tk.Tk()
    root.withdraw()
    gui = app.LinuxCRU(root)
    assert gui.preview_text.get("1.0", "end").strip(), "the preview is empty"
    assert gui.display_combo.cget("values"), "no displays were listed"
    assert gui.status_var.get(), "no status was set"

    for standard in timings.STANDARDS:
        gui.standard_var.set(standard)
        gui.generate_preview()
        assert gui.status_var.get(), f"no status for {standard}"
        assert gui.preview_text.get("1.0", "end").strip(), \
            f"no preview for {standard}"

    # The mode list has to survive a display with no modes.
    gui.standard_var.set("cvt-rb2")
    gui.generate_preview()
    outputs = list(gui.display_combo.cget("values"))
    session = gui.env.session_type
    compositor = gui.env.compositor
    root.destroy()
    print(f"self-test passed: {session} session, "
          f"{compositor}, displays: {', '.join(outputs)}")


def detect_cli():
    """--detect: what the tool sees of this machine, and of itself."""
    from linux_cru import detect, rescue

    env = detect.detect()
    print(f"session:  {env.session_type}")
    print(f"desktop:  {env.desktop}")
    print(f"compositor: {env.compositor} {detect._fmt_ver(env.compositor_version)}")
    print(f"drivers:  {', '.join(env.drivers)}")
    print(f"kernel:   {env.kernel_release}")
    print(f"outputs:  "
          f"{', '.join(c.name for c in env.connected_connectors())}")
    print(f"paths:    {detect.describe_paths(env)}")
    state_lines = rescue.lines()
    for i, line in enumerate(state_lines):
        prefix = "state:    " if i == 0 else "          "
        print(prefix + line)


def remove_overrides_cli(argv):
    """--remove-overrides: undo everything this tool installed.

    For the moment when the interface cannot start: a display that
    broke the login with an EDID override in the boot path, a desktop
    that crashed at it, or just a TTY. Its only read of the system is
    of files this tool owns, and its only write goes through the
    same root escalation the interface uses.
    """
    from linux_cru import rescue

    yes = "--yes" in argv
    connector = None
    if "--connector" in argv:
        i = argv.index("--connector")
        if i + 1 < len(argv):
            connector = argv[i + 1]

    print("Installed (all of it will be removed if you confirm):")
    for line in rescue.lines():
        print(f"  {line}")
    print()

    outcome = rescue.undo(connector=connector, yes=yes)
    return {"done": 0, "nothing": 0, "refused": 2, "failed": 1}[outcome]


def main():
    argv = sys.argv[1:]
    if "--self-test" in argv:
        self_test()
        return
    if "--version" in argv:
        import linux_cru
        print(f"Linux CRU {linux_cru.__version__}")
        return
    if "--remove-overrides" in argv:
        sys.exit(remove_overrides_cli(argv))
    if "--detect" in argv:
        detect_cli()
        return

    # The interface. It is the only path that needs tkinter: a machine
    # without a working Tk still has the command line above.
    import tkinter as tk

    from linux_cru import app

    root = tk.Tk()
    app.LinuxCRU(root)
    root.mainloop()


if __name__ == "__main__":
    main()
