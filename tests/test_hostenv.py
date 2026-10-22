"""Environment scrubbing for system tools launched from the AppImage."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from linux_cru import hostenv


BUNDLED = {
    "APPDIR": "/tmp/.mount_abc",
    "PATH": "/tmp/.mount_abc/usr/bin:/usr/bin:/bin",
    "PYTHONHOME": "/tmp/.mount_abc/usr",
    "PYTHONPATH": "/tmp/.mount_abc/usr/lib/python3.12",
    "LD_LIBRARY_PATH": "/tmp/.mount_abc/usr/lib",
    "TCL_LIBRARY": "/tmp/.mount_abc/usr/lib/tcl8.6",
    "TK_LIBRARY": "/tmp/.mount_abc/usr/lib/tk8.6",
    "HOME": "/home/user",
    "LINUX_CRU_SAVED_MARKER": "1",
}


def test_outside_appimage_is_unchanged():
    base = {"PATH": "/usr/bin", "LD_LIBRARY_PATH": "/opt/mine/lib"}
    assert hostenv.subprocess_env(base) == base


def test_bundle_variables_removed():
    env = hostenv.subprocess_env(BUNDLED)
    for name in ("PYTHONHOME", "PYTHONPATH", "LD_LIBRARY_PATH",
                 "TCL_LIBRARY", "TK_LIBRARY"):
        assert name not in env, f"{name} leaked into the child environment"
    assert env["HOME"] == "/home/user", "unrelated variables must survive"


def test_bundle_bin_removed_from_path():
    env = hostenv.subprocess_env(BUNDLED)
    assert "/tmp/.mount_abc" not in env["PATH"], env["PATH"]
    assert "/usr/bin" in env["PATH"] and "/bin" in env["PATH"], env["PATH"]


# -- open_url -----------------------------------------------------------------

_URL = "https://example.test/guide"


class _Result:
    def __init__(self, rc=0):
        self.returncode = rc
        self.stdout = ""
        self.stderr = ""


def _with_fakes(xdg_open, rc=0, raises=None, web_answer=None, expect_web=False):
    """Run open_url with the launcher and the fallback faked.

    xdg_open: what shutil.which answers for the launcher (a path, or
    None = not installed). rc/raises: what the spawned launcher does.
    expect_web/web_answer: the fallback is wired only when expected,
    and web_answer is what it reports. Returns (answer, launcher argv
    it saw, [urls the fallback was asked about]).
    """
    launched, asked = [], []

    def fake_run(argv, env=None, **kw):
        launched.append(argv)
        if raises:
            raise raises
        return _Result(rc)

    import subprocess
    saved_run, saved_which = hostenv.subprocess.run, hostenv.shutil.which
    hostenv.subprocess.run = fake_run
    hostenv.shutil.which = (lambda *a, **k: xdg_open) \
        if xdg_open else (lambda *a, **k: None)
    try:
        if expect_web:
            def browser(url):
                asked.append(url)
                if isinstance(web_answer, BaseException):
                    raise web_answer
                return web_answer
            answer = hostenv.open_url(_URL, webbrowser_open=browser)
        else:
            answer = hostenv.open_url(_URL)
        return answer, launched, asked
    finally:
        hostenv.subprocess.run = saved_run
        hostenv.shutil.which = saved_which


def test_open_url_xdg_open_success():
    answer, launched, asked = _with_fakes("/usr/bin/xdg-open", rc=0)
    assert answer is True, answer
    assert launched == [["/usr/bin/xdg-open", _URL]], launched
    assert asked == [], "the fallback must not be asked when it opened"


def test_open_url_asks_the_webbrowser_when_the_launcher_fails():
    answer, launched, asked = _with_fakes("/usr/bin/xdg-open", rc=3,
                                          expect_web=True, web_answer=True)
    assert answer is True
    assert launched == [["/usr/bin/xdg-open", _URL]], launched
    assert asked == [_URL], asked


def test_open_url_nothing_exists_says_false():
    answer, launched, asked = _with_fakes(None, expect_web=True,
                                          web_answer=False)
    assert answer is False
    assert launched == [], "nothing should be spawned without an opener"
    assert asked == [_URL], asked


def test_open_url_webbrowser_dying_is_not_a_crash():
    answer, _, _ = _with_fakes(None, expect_web=True,
                               web_answer=OSError("boom"))
    assert answer is False


def test_users_own_values_are_restored():
    base = dict(BUNDLED)
    base["LINUX_CRU_SAVED_LD_LIBRARY_PATH"] = "/opt/user/lib"
    base["LINUX_CRU_SAVED_PYTHONPATH"] = "/home/user/pylibs"
    env = hostenv.subprocess_env(base)
    assert env["LD_LIBRARY_PATH"] == "/opt/user/lib"
    assert env["PYTHONPATH"] == "/home/user/pylibs"
    assert "PYTHONHOME" not in env, "nothing was saved for it, so it must go"


def test_saved_variables_do_not_leak():
    base = dict(BUNDLED, LINUX_CRU_SAVED_LD_LIBRARY_PATH="/opt/user/lib")
    env = hostenv.subprocess_env(base)
    assert not any(k.startswith("LINUX_CRU_SAVED_") for k in env), sorted(env)


def test_path_never_ends_up_empty():
    base = {"APPDIR": "/tmp/.mount_x", "PATH": "/tmp/.mount_x/usr/bin",
            "LINUX_CRU_SAVED_MARKER": "1"}
    env = hostenv.subprocess_env(base)
    assert env["PATH"], "a child with no PATH could not find any tool"


def test_detection():
    assert hostenv.in_appimage() is False or "APPDIR" in os.environ


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if failures else 0)
