"""APP-8 subtitle bar: click-through lock (Windows style bits checked on a real window; real click-through and
the Mac are manual checks) and the bar's place remembered per screen (keyed by the screen's geometry)."""
import sys
from types import SimpleNamespace as NS

import pytest

import config
import dolmi_platform
from dolmi_platform import mac, windows
from ui import app as app_mod

LAPTOP = NS(x=0, y=0, width=1920, height=1080)
MONITOR = NS(x=1920, y=0, width=2560, height=1440)


# ------------------------------------------------------------------ Windows style bits on a real HWND
@pytest.fixture
def hwnd():
    if sys.platform != "win32":
        pytest.skip("Win32 window styles")
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    u.CreateWindowExW.restype = wintypes.HWND
    u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    h = u.CreateWindowExW(0, "STATIC", "Dolmi click-through test", 0x80000000, 0, 0, 200, 80, None, None, None, None)
    assert h
    yield h
    u.DestroyWindow(h)


def _ex(h):
    import ctypes
    return ctypes.windll.user32.GetWindowLongW(h, windows.GWL_EXSTYLE)


def test_lock_sets_layered_and_transparent_and_unlock_restores(hwnd):
    before = _ex(hwnd)
    assert not before & windows.WS_EX_TRANSPARENT
    assert windows._set_click_through(hwnd, True) is True
    ex = _ex(hwnd)
    assert ex & windows.WS_EX_LAYERED and ex & windows.WS_EX_TRANSPARENT
    assert windows._click_through_bits(hwnd) is True
    assert windows._set_click_through(hwnd, False) is True
    assert _ex(hwnd) == before                  # layered was added by Dolmi, so it goes again


def test_unlock_keeps_a_window_that_was_already_layered_layered(hwnd):
    import ctypes
    u = ctypes.windll.user32
    u.SetWindowLongW(hwnd, windows.GWL_EXSTYLE, _ex(hwnd) | windows.WS_EX_LAYERED)   # e.g. a transparent bar
    assert windows._set_click_through(hwnd, True)
    assert windows._set_click_through(hwnd, False)
    ex = _ex(hwnd)
    assert ex & windows.WS_EX_LAYERED and not ex & windows.WS_EX_TRANSPARENT


def test_no_window_is_not_an_error():
    assert windows._set_click_through(0, True) is False
    assert mac.set_click_through(None, True) is False and mac.click_through(None) is False


def test_both_platforms_have_the_lock():
    for impl in (windows, mac):
        assert callable(impl.set_click_through) and callable(impl.click_through)
    assert "set_click_through" in dolmi_platform.API


# ------------------------------------------------------------------ the bridge
@pytest.fixture
def lock_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(dolmi_platform, "set_click_through", lambda win, on: calls.append((win, on)) or True)
    monkeypatch.setattr(dolmi_platform, "overlay_stealth", lambda api: None)
    return calls


class FakeOverlay:
    def __init__(self):
        self.window = NS(x=100, y=900, width=640, height=200)
        self.sent = []

    def emit(self, fn, *a):
        self.sent.append((fn, a))


def test_lock_is_off_by_default_and_saved(api, lock_calls):
    assert api.state()["barLocked"] is False
    r = api.set_bar_locked(True)
    assert r == {"locked": True, "applied": True}
    assert config.load_settings()["bar_locked"] is True
    assert "window.dolmi.onBarLock(true)" in api._window.js()
    assert lock_calls == []                     # no bar yet: applied when it opens
    assert api.toggle_bar_lock()["locked"] is False


def test_lock_applies_to_the_open_bar_and_survives_restyling(api, lock_calls):
    api._overlay = FakeOverlay()
    api.set_bar_locked(True)
    assert lock_calls[-1] == (api._overlay.window, True)
    api._stealth_overlay()                      # shown again / invisible mode toggled: lock re-applied last
    assert lock_calls[-1] == (api._overlay.window, True)
    api.set_bar_locked(False)
    assert lock_calls[-1] == (api._overlay.window, False)


def test_bar_place_is_saved_per_screen(api, monkeypatch):
    monkeypatch.setattr(app_mod.Api, "_screens", staticmethod(lambda: [LAPTOP, MONITOR]))
    api._overlay = FakeOverlay()
    assert api._save_bar_place() == "0,0,1920x1080"
    api._overlay.window = NS(x=2500, y=1200, width=700, height=180)
    assert api._save_bar_place() == "1920,0,2560x1440"
    s = config.load_settings()
    assert s["bar_positions"] == {"0,0,1920x1080": {"x": 100, "y": 900, "w": 640, "h": 200},
                                  "1920,0,2560x1440": {"x": 2500, "y": 1200, "w": 700, "h": 180}}
    assert s["bar_screen"] == "1920,0,2560x1440"


POS = {"0,0,1920x1080": {"x": 100, "y": 850, "w": 640, "h": 200},
       "1920,0,2560x1440": {"x": 2500, "y": 1200, "w": 700, "h": 180}}


def test_bar_opens_where_it_was_on_the_last_screen():
    p = app_mod._bar_place(POS, "1920,0,2560x1440", [LAPTOP, MONITOR], near=(500, 500))
    assert p == {"x": 2500, "y": 1200, "w": 700, "h": 180, "screen": "1920,0,2560x1440"}


def test_monitor_unplugged_uses_the_laptop_place():
    p = app_mod._bar_place(POS, "1920,0,2560x1440", [LAPTOP], near=(500, 500))
    assert p["screen"] == "0,0,1920x1080" and (p["x"], p["y"]) == (100, 850)


def test_a_screen_never_saved_gives_the_default_place():
    assert app_mod._bar_place(POS, "", [NS(x=0, y=0, width=1366, height=768)]) is None
    assert app_mod._bar_place({}, "", [LAPTOP]) is None
    assert app_mod._bar_place(None, None, []) is None


def test_saved_place_is_pulled_inside_its_screen_and_junk_is_ignored():
    pos = {"0,0,1920x1080": {"x": 1800, "y": 1050, "w": 640, "h": 200}, "1920,0,2560x1440": "junk"}
    p = app_mod._bar_place(pos, "1920,0,2560x1440", [LAPTOP, MONITOR])
    assert p == {"x": 1280, "y": 880, "w": 640, "h": 200, "screen": "0,0,1920x1080"}
    assert app_mod._bar_place({"0,0,1920x1080": {"x": True, "y": 0, "w": 1, "h": 1}}, "", [LAPTOP]) is None


def test_screen_at_picks_the_containing_or_nearest_screen():
    assert app_mod._screen_at(2000, 10, [LAPTOP, MONITOR]) is MONITOR
    assert app_mod._screen_at(-50, 10, [LAPTOP, MONITOR]) is LAPTOP
    assert app_mod._screen_at(0, 0, []) is None


def test_overlay_page_has_the_lock_mark():
    from pathlib import Path
    ROOT = Path(__file__).resolve().parent.parent
    html = (ROOT / "src" / "ui" / "web" / "overlay.html").read_text(encoding="utf-8")
    assert "locked:(on)=>" in html and 'id="lockMark"' in html
