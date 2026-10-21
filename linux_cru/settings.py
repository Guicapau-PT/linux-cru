"""The user's settings, kept between runs.

Stored as JSON under the user's config directory, so a crash or a
mistake never leaves the application unusable: a file that cannot be
read is ignored, and a file that cannot be written is not an error the
user has to deal with.
"""

import json
import os

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
    "linux-cru")
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.json")

DEFAULTS = {
    "display": "",          # the output name that was selected
    "width": "1920",
    "height": "1080",
    "refresh": "75",
    "standard": "cvt-rb2",
    "theme": "system",      # system / dark / light
    "geometry": "",         # the window size and position
}


def load():
    """The stored settings, with the defaults filled in."""
    settings = dict(DEFAULTS)
    try:
        with open(CONFIG_FILE) as f:
            stored = json.load(f)
    except (OSError, ValueError):
        return settings
    if isinstance(stored, dict):
        for key, value in stored.items():
            if key in settings:
                settings[key] = value
    return settings


def save(settings):
    """Write the settings. Returns True when they were stored."""
    data = {k: v for k, v in settings.items() if k in DEFAULTS}
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, CONFIG_FILE)
        return True
    except OSError:
        return False
