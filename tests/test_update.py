"""Update checking tests.

No network is touched: the fetch — the one thing that talks to it —
is a stub the tests hand in, and the answers are canned bodies. What
this proves is the logic around the network: what counts as newer,
what the releases answer turns into, how a machine that is offline is
treated (silently, both in the interface and on the command line),
and that the automatic check only asks when the last one is old.
"""

import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from linux_cru import update


def _body(tag="v0.4.3",
          url="https://github.com/Guicapau-PT/linux-cru/releases/tag/v0.4.3"):
    return json.dumps({"tag_name": tag, "html_url": url,
                       "name": f"Linux CRU {tag.lstrip('v')}"}).encode()


_URL = "https://github.com/Guicapau-PT/linux-cru/releases/tag/v0.4.3"


def test_is_newer_compares_the_numeric_core():
    assert update.is_newer("0.4.3", "0.4.2")
    assert not update.is_newer("0.4.2", "0.4.2")
    assert not update.is_newer("0.4.2", "0.4.3")
    # Names may carry a v or carry a suffix; the numbers count.
    assert update.is_newer("v0.4.3", "0.4.2")
    assert not update.is_newer("v0.4.2-beta.1", "0.4.2")
    # Left padding: 0.10 comes after 0.9, not before it alphabetically.
    assert update.is_newer("0.10.0", "0.9.9")
    assert not update.is_newer("0.9.9", "0.10.0")


def test_parse_release_reads_the_releases_answer():
    parsed = update.parse_release(_body())
    assert parsed == ("v0.4.3",
                      "https://github.com/Guicapau-PT/linux-cru/releases/tag/v0.4.3")
    assert update.parse_release(b"not json") is None
    assert update.parse_release(b"[]") is None
    assert update.parse_release(b'{"noname": true}') is None


def test_latest_release_uses_the_fetch_it_is_given():
    seen = []

    def fetch(url, timeout=8.0):
        seen.append(url)
        return _body()

    assert update.latest_release(fetch) == ("v0.4.3", _URL)
    assert seen == [update.RELEASES_API], seen


def test_an_offline_machine_gets_none_and_exit_one():
    assert update.latest_release(lambda url, timeout=8: None) is None

    out = io.StringIO()
    assert update.command(out=out.write,
                          fetch=lambda url, timeout=8: None) == 1
    assert "Could not check" in out.getvalue(), out.getvalue()


def test_the_command_announces_a_new_release_with_its_url():
    out = io.StringIO()
    code = update.command(
        out=out.write,
        fetch=lambda url, timeout=8: _body(
            "v9.9.9",
            "https://github.com/Guicapau-PT/linux-cru/releases/tag/v9.9.9"))
    assert code == 0, out.getvalue()
    text = out.getvalue()
    assert "v9.9.9" in text and "update is available" in text, text
    assert "releases/tag/v9.9.9" in text, "the page has to be in the answer"


def test_the_command_says_so_when_there_is_nothing_newer():
    out = io.StringIO()
    code = update.command(out=out.write,
                          fetch=lambda url, timeout=8: _body("v0.0.1"))
    assert code == 0, out.getvalue()
    import linux_cru
    assert linux_cru.__version__ in out.getvalue(), out.getvalue()


def test_the_automatic_check_only_asks_when_the_last_is_old():
    now = time.time()
    assert update.should_check("", now), "a first run has no stored check"
    assert update.should_check("not a number", now)
    assert update.should_check("0", now)
    # Half the interval ago: quiet. More than the interval ago: ask.
    assert not update.should_check(str(now - update.CHECK_INTERVAL / 2), now)
    assert update.should_check(str(now - update.CHECK_INTERVAL - 1), now)


if __name__ == "__main__":
    for name, fun in sorted(globals().items()):
        if name.startswith("test_") and callable(fun):
            fun()
            print(f"PASS {name}")
