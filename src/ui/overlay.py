"""Dolmi's floating caption bar — a frameless, always-on-top window that mirrors the live
subtitles. Created on demand (Api.show_overlay); excluded from screen capture in invisible mode.
Its × button hides it; move/resize are handled in JS and applied here via the js_api.
"""
from pathlib import Path

import webview

WEB = Path(__file__).resolve().parent / "web"
MIN_W, MIN_H = 360, 110


class OverlayApi:
    """Exposed to overlay.html. State is private: pywebview exposes public attributes to the page."""

    def __init__(self, on_hide=None, on_toggle=None):
        self._window = None
        self._on_hide = on_hide
        self._on_toggle = on_toggle

    def toggle_source(self):
        """Corner button: switch between speaker and microphone."""
        return self._on_toggle() if self._on_toggle else False

    def geometry(self):
        """Window size in *logical* pixels — the same units resize() takes and that the page's
        mouse deltas use, so resizing stays correct at 125 %/150 % display scaling."""
        try:
            w = self._window
            if w and w.width:
                return {"w": int(w.width), "h": int(w.height)}
        except Exception:
            pass
        return {"w": 640, "h": 200}

    def resize(self, w, h):
        try:
            if self._window:
                self._window.resize(max(int(w), MIN_W), max(int(h), MIN_H))
        except Exception:
            pass
        return True

    def hide(self):
        try:
            if self._window:
                self._window.hide()
        except Exception:
            pass
        if self._on_hide:
            self._on_hide()
        return True


class Overlay:
    def __init__(self, background="#16150F", on_hide=None, on_toggle=None, place=None):
        """place: {x, y, w, h} where the bar last was on this screen (APP-8), else top centre."""
        self.api = OverlayApi(on_hide, on_toggle)
        p = place or {}
        self.window = webview.create_window(
            "Dolmi overlay", url=str((WEB / "overlay.html").as_uri()), js_api=self.api,
            width=max(int(p.get("w", 640)), MIN_W), height=max(int(p.get("h", 200)), MIN_H),
            x=p.get("x"), y=p.get("y", 48), frameless=True, easy_drag=False,
            on_top=True, background_color=background, resizable=True,
            transparent=True)   # the glass background's opacity is a setting; the desktop shows through
        self.api._window = self.window

    def show(self):
        try:
            self.window.show()
        except Exception as e:
            print(f"overlay show failed: {e}")

    def hide(self):
        try:
            self.window.hide()
        except Exception as e:
            print(f"overlay hide failed: {e}")

    def emit(self, fn, *args):
        import json
        try:
            self.window.evaluate_js(f"window.ov && window.ov.{fn}(" + ",".join(json.dumps(a) for a in args) + ")")
        except Exception:
            pass
