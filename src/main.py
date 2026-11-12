"""Dolmi (webview UI) — entry point, Windows and macOS.

A pywebview window (WebView2 on Windows, WKWebView on macOS) hosting ui/web/index.html, bridged to
the core modules at the repo root (live_subs, assistant, inbox, models, dolmi_platform).

Run:  venv\\Scripts\\pythonw.exe src\\main.py   (Windows; or python for a console)
      Mac/start.command                         (macOS; or Mac/venv/bin/python src/main.py)
"""
import atexit
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # repo root: core modules + assets live here
sys.path.insert(0, str(ROOT))                          # reuse live_subs, assistant, inbox, dolmi_platform, ...
sys.path.insert(0, str(Path(__file__).resolve().parent))  # src/ for the ui package

import dolmi_platform

if dolmi_platform.is_packaged():
    sys.dont_write_bytecode = True   # Store package: read-only folder, every .pyc ships precompiled

import webview

WEB = Path(__file__).resolve().parent / "ui" / "web"


def _setup_output():
    """The core prints status lines with emoji (📥, 🎧). Without a console (pythonw on Windows, Dolmi.app
    launched from Finder) there's nowhere to print, and a Windows console defaults to cp1252 — either
    way an emoji print raises 'charmap can't encode' and aborts model loading. Log to a UTF-8 file
    without a console; force UTF-8 with one."""
    if dolmi_platform.needs_log_file():
        logs = dolmi_platform.log_dir()
        logs.mkdir(parents=True, exist_ok=True)
        log = open(logs / "dolmi.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
    else:
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass
    print(f"\n--- Dolmi (webview) started {datetime.now():%Y-%m-%d %H:%M:%S}")


def _instance_name():
    """APP-5: the installed Dolmi and a copy run from source share one instance name, so while one runs
    the other only brings it forward. Developers set DOLMI_INSTANCE (letters, digits, - or _) to run a
    second copy next to the installed one; both still use the same settings folder."""
    suffix = os.environ.get("DOLMI_INSTANCE", "").strip()
    if suffix and len(suffix) <= 32 and all(c.isascii() and (c.isalnum() or c in "-_") for c in suffix):
        return "Dolmi-" + suffix
    return "Dolmi"


class _Starting:
    """The start page's bridge: nothing to call yet."""


def main():
    _setup_output()
    instance = dolmi_platform.single_instance(_instance_name())   # APP-5: one Dolmi at a time
    if instance is None:
        print("Dolmi is already running: asked it to come to the front")
        return
    os.chdir(ROOT)                                            # core resolves relative paths from here
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    dolmi_platform.register_app()                             # Windows: own taskbar entry + icon

    # The window comes up first, on a light start page, and the heavy imports (speech, translation,
    # audio) follow inside it. On the first start of the day the antivirus scans every one of those
    # files, which took over a minute with nothing on screen, so people clicked again and again.
    state = {"api": None}

    def make_window(x=None, y=None, width=1180, height=780, page="index.html", js_api=None):
        w = webview.create_window(
            "Dolmi", url=str((WEB / page).as_uri()), js_api=js_api or state["api"], x=x, y=y,
            width=width, height=height, min_size=(900, 600), background_color="#F2ECDF",
            text_select=True)   # captions, transcripts and answers must be selectable/copyable
        w.events.closed += lambda *_: _closed(w)   # closing the window quits Dolmi
        return w

    def _closed(w):
        print("Dolmi: the main window closed")
        api = state["api"]
        if api is None:                  # closed while still starting: nothing to save or restore
            os._exit(0)
        api._on_main_closed(w)

    def bring_forward():
        api = state["api"]
        if api is not None:
            api._bring_to_front()
        else:
            dolmi_platform.bring_to_front(window)

    window = make_window(page="starting.html", js_api=_Starting())

    def on_start():
        instance.listen(bring_forward)   # APP-5: a second launch brings this window forward, even while starting
        from ui import app as app_mod
        api = app_mod.Api()
        api._window = window
        api._make_window = make_window   # Mac: invisible off rebuilds the window (see dolmi_platform/mac.py)
        state["api"] = api
        atexit.register(api._routing_end)
        window._js_api = api             # the real page gets the full bridge when it loads
        window.load_url(str((WEB / "index.html").as_uri()))
        # apply a saved "invisible" setting once the window really exists (no HWND/NSWindow before)
        try:
            window.events.shown.wait(10)
        except Exception:
            pass
        if api._settings.get("invisible"):
            api.toggle_invisible(True)
        api._start_background()          # APP-4: load the downloaded models now, so Start is instant
        api._start_update_check()        # APP-6: is a newer release out? (setting; silent when offline)
        # a system-wide hotkey always brings Dolmi back, even hidden from the taskbar / Dock
        dolmi_platform.start_recover_hotkey(api.recover)
        api._start_shortcuts()           # APP-7: start/stop, bar, source, lock from any app (after the recover key)

    icon = dolmi_platform.app_icon(ROOT)
    webview.start(on_start, gui=dolmi_platform.WEBVIEW_GUI, private_mode=False, icon=str(icon))


if __name__ == "__main__":
    main()
