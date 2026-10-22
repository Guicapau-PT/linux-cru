"""Checking for updates.

One URL is asked: the releases API of the repository this fork is
published to. No telemetry, nothing installed without someone clicking,
and no failure is louder than the status line: a machine that is
offline simply stays as it was. The answer is compared on the numeric
core of the release name, so "v0.4.3" counts against "0.4.2" and "0.10"
lands after "0.9".

The interface asks on its own thread (like everything slow) and keeps
the last check time in the settings, so the check happens at most
twice a day rather than on every launch. `--check-updates` asks once,
on demand, from the command line.
"""

import json
import re
import time
import urllib.request

# Where the releases are published. The comparison runs against what
# is published here, not against upstream.
REPO_PAGE = "https://github.com/Guicapau-PT/linux-cru"
RELEASES_API = REPO_PAGE.replace("github.com", "api.github.com/repos") \
                        + "/releases/latest"
GUIDE_URL = REPO_PAGE + "/blob/main/docs/guide.md"

# A fresh question at most twice a day; a click on the version label
# ignores this and asks straight away.
CHECK_INTERVAL = 12 * 3600.


def _version_core(version):
    """The numeric core of a release label: ("0.4.3", "v0.4.3", "0.5rc1"...).

    Everything non-numeric past the numbers is ignored, which is also
    what keeps a plain tuple comparison tolerant of the shapes a tag
    name can arrive in.
    """
    match = re.search(r"(\d+(?:\.\d+)*)", str(version))
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(candidate, current):
    """True when `candidate` is a release newer than `current`.

    The cores are left-padded, so 0.10 counts as newer than 0.9, and
    equal cores (0.4.2 against 0.4.2) are the same release.
    """
    newer, older = _version_core(candidate), _version_core(current)
    width = max(len(newer), len(older))
    newer += (0,) * (width - len(newer))
    older += (0,) * (width - len(older))
    return newer > older


def should_check(stored, now):
    """True when the time of the last check is old enough for another.

    stored: what the settings hold ("update_last_check"), missing or
    unreadable on a first run.
    """
    try:
        last = float(stored)
    except (TypeError, ValueError):
        return True
    if last <= 0.0:
        return True
    return now - last >= CHECK_INTERVAL


def _fetch(url, timeout=8.0):
    """The body of a URL, or None when the network does not answer."""
    try:
        request = urllib.request.Request(
            url, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return answer.read()
    except OSError:          # urllib errors and socket timeouts are OSError
        return None


def parse_release(body):
    """(label, url) out of the releases API's "latest" answer, or None.

    label is the tag as it is published ("v0.4.3"), so it reads the
    way the release page does.
    """
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    label = data.get("tag_name") or data.get("name")
    if not label:
        return None
    return str(label), data.get("html_url") or RELEASES_API


def latest_release(fetch=_fetch):
    """The newest published release as (label, url), or None.

    The releases API answers with the most recent non-prerelease,
    non-draft release, so a pre-release never walks in as an update.
    """
    body = fetch(RELEASES_API)
    if not body:
        return None
    return parse_release(body)


# -- the command line ------------------------------------------------------------

def command(out=print, fetch=_fetch):
    """`--check-updates`: ask once, now, and print the answer.

    Returns the exit code.
    """
    import linux_cru

    result = latest_release(fetch)
    if not result:
        out("Could not check for updates (the network did not answer?).")
        return 1
    label, url = result
    if is_newer(label, linux_cru.__version__):
        out(f"An update is available: {label}")
        out(url)
        return 0
    out(f"Linux CRU {linux_cru.__version__} is the newest release.")
    return 0
