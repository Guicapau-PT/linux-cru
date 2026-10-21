#!/usr/bin/env python3
"""Linux Custom Resolution Utility.

The graphical front-end lives in linux_cru.app; this file is the
launcher that the AppImage runs. It picks the tool for the session it
finds itself in, works out which mode-injection paths are available
(see docs/RESEARCH.md) and shows exactly what it would do before it
does it.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk

from linux_cru import app, detect, timings


def self_test():
    """Build the whole interface once and exit. Used to check a build."""
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


def main():
    if "--self-test" in sys.argv:
        self_test()
        return
    if "--version" in sys.argv:
        import linux_cru
        print(f"Linux CRU {linux_cru.__version__}")
        return
    if "--detect" in sys.argv:
        env = detect.detect()
        print(f"session:  {env.session_type}")
        print(f"desktop:  {env.desktop}")
        print(f"compositor: {env.compositor} {env.compositor_version}")
        print(f"drivers:  {', '.join(env.drivers)}")
        print(f"kernel:   {env.kernel_release}")
        print(f"outputs:  "
              f"{', '.join(c.name for c in env.connected_connectors())}")
        print(f"paths:    {detect.describe_paths(env)}")
        return
    root = tk.Tk()
    app.LinuxCRU(root)
    root.mainloop()


if __name__ == "__main__":
    main()
