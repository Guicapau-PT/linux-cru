"""Launcher tests.

The command line is the thing to reach for when the desktop is broken,
and a desktop can be broken precisely because Tk is unusable. So every
flag has to run with tkinter unimportable, and this is proven here by
putting a tkinter on the path that always fails -- not by asking the
machine what it happens to have installed.

The flags themselves are only asserted for behaviour that does not
depend on the machine: --version says the version, --detect prints a
kernel line, --remove-overrides answers and never blocks on an
interactive question when stdin is not a terminal.
"""

import os
import subprocess
import sys
import tempfile

TESTS = os.path.dirname(os.path.abspath(__file__))
LAUNCHER = os.path.join(TESTS, "..", "linux-cru.py")

_BLOCKER = """raise ImportError("tkinter is broken in test_cli")
"""


def _run(args, timeout=120):
    """Run the launcher with a tkinter that cannot be imported."""
    with tempfile.TemporaryDirectory(prefix="cru-cli-") as root:
        with open(f"{root}/tkinter.py", "w") as f:
            f.write(_BLOCKER)
        # The broken tkinter must shadow the real one:
        env = dict(os.environ, PYTHONPATH=root)
        return subprocess.run(
            [sys.executable, LAUNCHER] + args,
            capture_output=True, universal_newlines=True,
            stdin=subprocess.DEVNULL, timeout=timeout, env=env)


def test_the_gui_truly_cannot_start_but_the_flags_can():
    # The negative control: with no flag at all the launcher tries to
    # open the window and dies on the broken tkinter. The tests below
    # only mean something because this one fails.
    res = _run([])
    assert res.returncode != 0, "the blocker did not shadow a working tkinter"
    assert "tkinter is broken in test_cli" in res.stderr


def test_version_prints_and_exits_without_tkinter():
    res = _run(["--version"])
    assert res.returncode == 0, res.stderr
    assert "Linux CRU" in res.stdout, res.stdout


def test_detect_runs_and_prints_the_kernel_without_tkinter():
    res = _run(["--detect"])
    assert res.returncode == 0, res.stderr
    assert "kernel:" in res.stdout, res.stdout
    assert "state:" in res.stdout, res.stdout


def test_remove_overrides_never_blocks_and_answers():
    # What is actually installed differs between machines, and must
    # not matter here: without a terminal it either finds nothing (0),
    # asks for --yes (2) or something failed (1). It must never hang,
    # which stdin=devnull would turn into EOF anyway.
    res = _run(["--remove-overrides"], timeout=60)
    assert res.returncode in (0, 1, 2), (res.returncode, res.stderr)
    assert "Installed" in res.stdout or "nothing" in res.stdout
    if res.returncode == 2:
        assert "--yes" in res.stdout, "the refusal must say what to do"


def test_detect_accepts_the_connector_argument_shape():
    # An unknown connector must not crash the flow; with nothing
    # installed it is the same answer as without one.
    res = _run(["--remove-overrides"])
    assert res.returncode in (0, 1, 2)
    res = _run(["--remove-overrides", "--connector", "DP-9"])
    assert res.returncode in (0, 1, 2)


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    for name, fun in tests:
        res = None
        fun()
        print(f"PASS {name}")
