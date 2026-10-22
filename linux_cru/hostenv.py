"""Environment for running system tools.

When this runs from an AppImage, the process has PYTHONHOME,
PYTHONPATH and LD_LIBRARY_PATH pointing into the bundle. Handing that
environment to a system tool (xrandr, kscreen-doctor, systemctl,
pkexec) makes it load the bundle's libraries instead of its own, which
can break it in ways that are hard to diagnose.

subprocess_env() returns the environment a system tool should see: the
bundle's variables removed, and whatever the user had before the
AppImage started put back.
"""

import os
import shutil
import subprocess

# AppRun stores the caller's values here before overwriting them.
_SAVED_PREFIX = "LINUX_CRU_SAVED_"
_BUNDLE_VARS = ("PYTHONHOME", "PYTHONPATH", "LD_LIBRARY_PATH",
                "TCL_LIBRARY", "TK_LIBRARY", "TKPATH")


def in_appimage(env=None):
    env = os.environ if env is None else env
    return bool(env.get("APPDIR") or env.get("APPIMAGE")
                or env.get(_SAVED_PREFIX + "MARKER"))


def subprocess_env(base=None):
    """A copy of the environment that is safe to give to system tools."""
    env = dict(base if base is not None else os.environ)
    if not in_appimage(env):
        return env

    for name in _BUNDLE_VARS:
        saved = env.pop(_SAVED_PREFIX + name, None)
        if saved:
            env[name] = saved
        else:
            env.pop(name, None)

    # The bundle's bin directory must not shadow the system's tools.
    appdir = env.get("APPDIR")
    if appdir:
        parts = [p for p in env.get("PATH", "").split(os.pathsep)
                 if p and not p.startswith(appdir)]
        env["PATH"] = os.pathsep.join(parts) or "/usr/bin:/bin"

    for key in list(env):
        if key.startswith(_SAVED_PREFIX):
            del env[key]
    return env


def open_url(url, webbrowser_open=None):
    """Open a URL in the desktop's browser. True when something claims success.

    The launcher is spawned with the environment system tools see: an
    AppImage's LD_LIBRARY_PATH must not be handed to the browser for
    the same reason it must not be handed to xrandr. xdg-open goes
    first, because it knows the desktop's own choice of browser;
    webbrowser answers for the rest (it reads $BROWSER, too).

    webbrowser_open is the seam the tests use to stay away from real
    browsers. Both answers are checked instead of being believed: a
    tool that says "the guide is open" when nothing opened is a lie
    the user pays for twice.
    """
    opener = shutil.which("xdg-open")
    if opener:
        try:
            p = subprocess.run([opener, url], env=subprocess_env(),
                               capture_output=True, timeout=15)
            if p.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            pass
    if webbrowser_open is None:
        import webbrowser
        webbrowser_open = webbrowser.open
    try:
        return bool(webbrowser_open(url))
    except OSError:
        return False
