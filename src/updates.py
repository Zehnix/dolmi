"""Update check (APP-6): is there a newer Dolmi release than this one?

One HTTPS request to GitHub's public API for the latest release of the repository the README's download
links point to. Nothing about the user or this computer is sent beyond what any web request carries
(IP address, a User-Agent with the version). Nothing is downloaded or installed: the page offers
"Download x.y.z", which opens the release page in the browser. Offline, slow (over 5 s) or any error:
silently no update.
"""
import json
import re
import urllib.request

REPO = "Zehnix/dolmi"                                   # README: github.com/Zehnix/dolmi/releases
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
TIMEOUT_S = 5


def parse_version(text):
    """'v2.3.0' / '2.3' / 'Dolmi 2.3.1' -> (2, 3, 0) / (2, 3, 0) / (2, 3, 1); None when there is no version."""
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", str(text or ""))
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)) if m else None


_PAGE_RE = re.compile(re.escape(RELEASES_PAGE) + r"(/[A-Za-z0-9._/-]*)?")


def is_release_page(url):
    """Only this repository's release pages may be opened from the page (no '..' out of them)."""
    return isinstance(url, str) and bool(_PAGE_RE.fullmatch(url)) and ".." not in url


def fetch_latest(current, timeout=TIMEOUT_S, opener=urllib.request.urlopen):
    """GitHub's latest release as {"version", "url"}; raises on any network or format problem."""
    import netgate
    netgate.check("update")   # STR-7: local-only mode = "offline"
    req = urllib.request.Request(LATEST_URL, headers={
        "Accept": "application/vnd.github+json", "User-Agent": f"Dolmi/{current} (update check)"})
    with opener(req, timeout=timeout) as r:
        data = json.loads(r.read(200_000).decode("utf-8"))
    if data.get("draft") or data.get("prerelease"):
        raise ValueError("latest release is a draft or pre-release")
    version = parse_version(data.get("tag_name") or data.get("name"))
    if not version:
        raise ValueError(f"no version in {data.get('tag_name')!r}")
    url = data.get("html_url")
    return {"version": ".".join(map(str, version)), "url": url if is_release_page(url) else RELEASES_PAGE}


def check(current, timeout=TIMEOUT_S, opener=urllib.request.urlopen):
    """{"available": bool, "version", "url", "current"}; available False (and no version) when offline."""
    out = {"available": False, "version": "", "url": RELEASES_PAGE, "current": current}
    mine = parse_version(current)
    try:
        latest = fetch_latest(current, timeout, opener)
    except Exception as e:                       # offline, timeout, rate limit, bad JSON: no update
        print(f"Update check skipped: {type(e).__name__}: {e}")
        return out
    out.update(latest, available=bool(mine) and parse_version(latest["version"]) > mine)
    return out
