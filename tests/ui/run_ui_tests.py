r"""Headless UI tests for Dolmi's web UI (APP-11): every screen, every Settings section and control, and the
layout at the window sizes and display scales people really use. Standard library only: it drives the
Edge (or Chrome) that is already installed, nothing is downloaded.

    python tests/ui/run_ui_tests.py                  # all sizes; exit code 1 on a failure
    python tests/ui/run_ui_tests.py --shots DIR      # also save a screenshot of every screen per size

How it works: the real src/ui/web/index.html is copied with tests/ui/mock_bridge.js (a fake
window.pywebview.api that records every call) in front of the page's scripts and tests/ui/checks.js after
them. Edge opens it headless (--dump-dom) and checks.js writes its findings into pre#result.
The subtitle bar (overlay.html) is checked the same way with tests/ui/overlay_checks.js.
"""
import argparse
import html as html_lib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src" / "ui" / "web"
HERE = Path(__file__).resolve().parent

# (width, height, device scale): the smallest window Dolmi allows (main.py min_size) and a common laptop
# window, each at 100 % and 150 % Windows display scaling
SIZES = [(900, 600, 1), (900, 600, 1.5), (1360, 860, 1), (1360, 860, 1.5)]
SCREENS = ["live", "meetings", "assistant", "vocab", "onboarding",
           "settings:audio", "settings:captions", "settings:speech", "settings:vocab", "settings:assistant",
           "settings:bar", "settings:privacy", "settings:appearance", "settings:about"]


def find_browser():
    """Installed Edge (or Chrome) for headless runs, or None."""
    for name in ("msedge", "chrome", "google-chrome", "chromium", "chromium-browser"):
        if shutil.which(name):
            return shutil.which(name)
    for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
              "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
        if os.path.exists(p):
            return p
    return None


def build_page(tmp, page="index.html", checks="checks.js", extra_css=""):
    """The real page with the mock bridge before its scripts and the checks after them."""
    text = (WEB / page).read_text(encoding="utf-8")
    mock = (HERE / "mock_bridge.js").read_text(encoding="utf-8")
    head = f'<head>\n<base href="{WEB.as_uri()}/">\n<script>\n{mock}\n</script>'
    text = text.replace("<head>", head, 1)
    if extra_css:   # after the page's own stylesheet, so it wins like a real regression would
        text = text.replace("</head>", f"<style>{extra_css}</style>\n</head>", 1)
    text = text.replace("</body>", f"<script>\n{(HERE / checks).read_text(encoding='utf-8')}\n</script>\n</body>", 1)
    out = Path(tmp) / f"ui_{Path(page).stem}.html"
    out.write_text(text, encoding="utf-8")
    return out


def _edge(exe, profile, size, extra):
    w, h, scale = size
    return [exe, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            "--hide-scrollbars", "--allow-file-access-from-files", f"--user-data-dir={profile}", f"--window-size={w},{h}",
            f"--force-device-scale-factor={scale}", "--virtual-time-budget=20000", *extra]


def _run(cmd, tries=3):
    # headless Edge with --dump-dom now and then never exits: retry instead of hanging
    for attempt in range(tries):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=60).stdout
        except subprocess.TimeoutExpired:
            if attempt == tries - 1:
                raise
    return ""


def run_page(exe, page, size, tmp):
    """Open page at size, return the checks' JSON report."""
    out = _run(_edge(exe, Path(tmp) / "profile", size, ["--dump-dom", page.as_uri()]))
    found = re.findall(r'<pre id="result">(\x7b.*?)</pre>', out, re.S)
    if not found:
        return {"ok": False, "failures": [f"the page did not report: {out[-600:]}"], "checks": 0}
    return json.loads(html_lib.unescape(found[-1]))


def screenshot(exe, page, size, spec, path, tmp):
    _run(_edge(exe, Path(tmp) / "profile", size, [f"--screenshot={path}", page.as_uri() + "?shot=" + spec]))
    return Path(path).exists()


def run_all(exe=None, sizes=SIZES, extra_css="", shots=None, overlay=True):
    """Every size: {label: report}. With shots, also a PNG per screen and size in that folder."""
    exe = exe or find_browser()
    if not exe:
        raise RuntimeError("no Edge/Chrome found")
    results = {}
    with tempfile.TemporaryDirectory(prefix="dolmi-ui-") as tmp:
        page = build_page(tmp, extra_css=extra_css)
        bar = build_page(tmp, "overlay.html", "overlay_checks.js") if overlay else None
        for size in sizes:
            label = f"{size[0]}x{size[1]}@{size[2]}"
            results[label] = run_page(exe, page, size, tmp)
            if shots:
                Path(shots).mkdir(parents=True, exist_ok=True)
                for spec in SCREENS:
                    screenshot(exe, page, size, spec, Path(shots) / f"{label}-{spec.replace(':', '-')}.png", tmp)
        if bar:
            results["subtitle bar"] = run_page(exe, bar, (640, 200, 1), tmp)
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--shots", help="folder for a screenshot of every screen at every size")
    ap.add_argument("--extra-css", default="", help="CSS added after the page's own (to prove a check catches a regression)")
    args = ap.parse_args(argv)
    try:
        results = run_all(extra_css=args.extra_css, shots=args.shots)
    except RuntimeError as e:
        print(f"SKIP: {e}")
        return 0
    failed = 0
    for label, r in results.items():
        vp = r.get("viewport") or {}
        print(f"{'PASS' if r['ok'] else 'FAIL'} {label}  ({r.get('checks', 0)} checks, viewport {vp.get('w')}x{vp.get('h')} @{vp.get('dpr')})")
        for f in r.get("failures", []):
            print(f"   - {f}")
        if r.get("unknown"):
            print(f"   note: bridge calls without canned data: {', '.join(r['unknown'])}")
        failed += not r["ok"]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
