"""APP-6 update check: compare with GitHub's latest release, offer 'Download x.y.z', never install, silently
skip offline. The HTTP call is always faked: these tests never touch the network."""
import io
import json
import socket
import urllib.error

from pathlib import Path

import pytest

import updates
from ui import app as app_mod


def reply(payload):
    """A fake urlopen that answers with this JSON (and remembers the request it got)."""
    seen = []

    def opener(req, timeout=None):
        seen.append((req, timeout))
        return io.BytesIO(json.dumps(payload).encode())
    opener.seen = seen
    return opener


def failing(exc):
    def opener(req, timeout=None):
        raise exc
    return opener


ROOT = Path(__file__).resolve().parent.parent
RELEASE = {"tag_name": "v2.3.0", "html_url": "https://github.com/Zehnix/dolmi/releases/tag/v2.3.0",
           "draft": False, "prerelease": False}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any real request fails the test."""
    def boom(*a, **k):
        raise AssertionError("a test reached the network")
    monkeypatch.setattr(updates.urllib.request, "urlopen", boom)
    monkeypatch.setattr(updates.check, "__defaults__", (updates.TIMEOUT_S, boom))


@pytest.mark.parametrize("text,want", [("v2.3.0", (2, 3, 0)), ("2.3", (2, 3, 0)), ("Dolmi 2.10.1", (2, 10, 1)),
                                       ("", None), (None, None), ("latest", None)])
def test_parse_version(text, want):
    assert updates.parse_version(text) == want


def test_newer_release_is_offered():
    op = reply(RELEASE)
    r = updates.check("2.2.0", opener=op)
    assert r == {"available": True, "version": "2.3.0", "url": RELEASE["html_url"], "current": "2.2.0"}
    req, timeout = op.seen[0]
    assert req.full_url == updates.LATEST_URL and timeout == 5
    assert "Dolmi/2.2.0" in req.get_header("User-agent")


@pytest.mark.parametrize("current", ["2.3.0", "2.4.0", "2.10.0"])
def test_same_or_older_release_is_not_offered(current):
    assert updates.check(current, opener=reply(RELEASE))["available"] is False


def test_versions_compare_as_numbers_not_text():
    assert updates.check("2.9.0", opener=reply({**RELEASE, "tag_name": "v2.10.0"}))["available"] is True


@pytest.mark.parametrize("exc", [urllib.error.URLError("offline"), socket.timeout("timed out"),
                                 urllib.error.HTTPError(updates.LATEST_URL, 403, "rate limit", None, None),
                                 ValueError("bad json")])
def test_offline_or_errors_are_silent(exc):
    r = updates.check("2.2.0", opener=failing(exc))
    assert r["available"] is False and r["version"] == ""


@pytest.mark.parametrize("bad", [{**RELEASE, "draft": True}, {**RELEASE, "prerelease": True},
                                 {**RELEASE, "tag_name": "nightly", "name": ""}])
def test_drafts_prereleases_and_odd_tags_are_ignored(bad):
    assert updates.check("2.2.0", opener=reply(bad))["available"] is False


def test_a_foreign_link_in_the_reply_is_replaced_by_the_releases_page():
    r = updates.check("2.2.0", opener=reply({**RELEASE, "html_url": "https://evil.example/download"}))
    assert r["available"] and r["url"] == updates.RELEASES_PAGE


def test_repository_matches_the_readme_download_links():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"github.com/{updates.REPO}/releases" in readme


# ------------------------------------------------------------------ the bridge
@pytest.fixture
def latest(monkeypatch):
    calls = []

    def fake(current, *a, **k):
        calls.append(current)
        return {"available": True, "version": "9.0.0", "url": RELEASE["html_url"], "current": current}
    monkeypatch.setattr(updates, "check", fake)
    return calls


def test_check_update_tells_the_page(api, latest):
    r = api.check_update()
    assert r["available"] and latest == [app_mod.VERSION]
    assert "window.dolmi.onUpdate(" in api._window.js() and '"9.0.0"' in api._window.js()
    assert api.state()["update"]["version"] == "9.0.0"


def test_turned_off_means_no_request_unless_check_now(api, latest):
    assert api.state()["checkUpdates"] is True                     # on by default
    assert api.set_setting("check_updates", False)
    assert api.check_update()["off"] is True and latest == []
    api._start_update_check()
    assert latest == []
    assert api.check_update(True)["available"] and len(latest) == 1  # the user pressed Check now
    assert api.set_setting("check_updates", "yes") is False          # switches only take true/false


def test_open_release_opens_only_release_pages(api, monkeypatch):
    opened = []
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
    assert api.open_release(RELEASE["html_url"]) is True
    assert api.open_release("https://evil.example/x") is False
    assert api.open_release("https://github.com/Zehnix/dolmi/releases/../../other") is False
    assert api.open_release("https://github.com/Zehnix/dolmi/releases.evil.example/") is False
    assert api.open_release("file:///C:/Windows/system32/calc.exe") is False
    assert opened[0] == RELEASE["html_url"] and "evil" not in "".join(opened) and "file:" not in "".join(opened)


def test_privacy_policy_mentions_the_update_check():
    text = (ROOT / "PRIVACY.md").read_text(encoding="utf-8")
    assert "## Update check" in text and "Check for updates" in text
