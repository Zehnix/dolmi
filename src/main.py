"""Dolmi (webview UI) — entry point, Windows and macOS.

A pywebview window (WebView2 on Windows, WKWebView on macOS) hosting ui/web/index.html, bridged to
the core modules at the repo root (live_subs, assistant, inbox, models, dolmi_platform).

Run:  venv\\Scripts\\pythonw.exe src\\main.py   (Windows; or python for a console)
      Mac/start.command                         (macOS; or Mac/venv/bin/python src/main.py)
"""
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # repo root: core modules + assets live here
sys.path.insert(0, str(ROOT))                          # reuse live_subs, assistant, inbox, dolmi_platform, ...
sys.path.insert(0, str(Path(__file__).resolve().parent))  # src/ for the ui package

import webview

import dolmi_platform

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


def main():
    _setup_output()
    os.chdir(ROOT)                                            # core resolves relative paths from here
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    dolmi_platform.register_app()                             # Windows: own taskbar entry + icon

    from ui import app as app_mod

    api = app_mod.Api()

    def make_window(x=None, y=None, width=1180, height=780):
        return webview.create_window(
            "Dolmi", url=str((WEB / "index.html").as_uri()), js_api=api, x=x, y=y,
            width=width, height=height, min_size=(900, 600), background_color="#F2ECDF",
            text_select=True)   # captions, transcripts and answers must be selectable/copyable

    window = make_window()
    api._window = window
    api._make_window = make_window   # Mac: invisible off rebuilds the window (see dolmi_platform/mac.py)

    def on_start():
        # apply a saved "invisible" setting once the window really exists (no HWND/NSWindow before)
        try:
            window.events.shown.wait(10)
        except Exception:
            pass
        if api._settings.get("invisible"):
            api.toggle_invisible(True)
        # a system-wide hotkey always brings Dolmi back, even hidden from the taskbar / Dock
        dolmi_platform.start_recover_hotkey(api.recover)

    icon = dolmi_platform.app_icon(ROOT)
    webview.start(on_start, gui=dolmi_platform.WEBVIEW_GUI, private_mode=False, icon=str(icon))


if __name__ == "__main__":
    main()
