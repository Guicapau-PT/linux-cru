"""The settings that are kept between runs."""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from linux_cru import settings


def _sandbox():
    """Point the module at a temporary directory."""
    root = tempfile.mkdtemp(prefix="linux-cru-test-")
    settings.CONFIG_DIR = root
    settings.CONFIG_FILE = os.path.join(root, "settings.json")
    return root


def test_defaults_when_nothing_was_stored():
    _sandbox()
    loaded = settings.load()
    assert loaded["width"] == "1920"
    assert loaded["standard"] == "cvt-rb2"
    assert loaded["theme"] == "system"


def test_values_survive_a_round_trip():
    _sandbox()
    assert settings.save({"width": "2560", "height": "1440",
                          "standard": "cvt-rb2", "display": "DP-2"})
    loaded = settings.load()
    assert loaded["width"] == "2560"
    assert loaded["height"] == "1440"
    assert loaded["display"] == "DP-2"


def test_a_broken_file_is_ignored():
    _sandbox()
    with open(settings.CONFIG_FILE, "w") as f:
        f.write("{ this is not json")
    loaded = settings.load()
    assert loaded["width"] == "1920"


def test_unknown_keys_are_dropped():
    _sandbox()
    with open(settings.CONFIG_FILE, "w") as f:
        json.dump({"width": "800", "nonsense": True}, f)
    loaded = settings.load()
    assert loaded["width"] == "800"
    assert "nonsense" not in loaded


def test_a_file_that_cannot_be_written_is_not_fatal():
    _sandbox()
    settings.CONFIG_DIR = "/proc/nonexistent/linux-cru"
    assert settings.save({"width": "800"}) is False


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
