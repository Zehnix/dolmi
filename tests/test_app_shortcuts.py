"""APP-7 global shortcuts: start/stop captions, show/hide the bar, switch speaker/mic, lock the bar. Configurable,
validated, re-registered on change; Windows RegisterHotKey is exercised for real (a key press is synthesised).
That they fire while Teams/Zoom has the focus, and the Mac, are manual checks."""
import sys
import threading

import pytest

import config
import dolmi_platform
from dolmi_platform import mac, windows
from ui import app as app_mod


@pytest.mark.parametrize("text,want", [
    ("ctrl+alt+shift+s", "ctrl+alt+shift+s"), ("Shift+Ctrl+Alt+S", "ctrl+alt+shift+s"),
    ("ctrl+alt+7", "ctrl+alt+7"), ("Cmd+Option+K", "alt+meta+k"), ("ctrl+shift+F9", "ctrl+shift+f9"),
    ("ctrl+s", None), ("shift+a", None), ("ctrl+alt", None), ("ctrl+alt+space", None), ("ctrl+alt+é", None),
    ("ctrl+ctrl+a", None), ("hyper+alt+a", None), ("ctrl+alt+f25", None), ("ctrl+alt+f0", None), ("", None),
    (None, None), (42, None), ("ctrl++a", None)])
def test_normalize_shortcut(text, want):
    assert app_mod.normalize_shortcut(text) == want


def test_defaults_and_state(api):
    s = api.state()
    assert s["shortcutsOn"] is True
    assert s["shortcuts"] == app_mod.DEFAULT_SHORTCUTS
    assert "ctrl+alt+shift+d" not in s["shortcuts"].values()      # the show-Dolmi key stays its own
    assert len(set(s["shortcuts"].values())) == 4


def test_set_shortcut_saves_only_changes(api):
    r = api.set_shortcut("toggle_live", "Ctrl+Alt+K")
    assert r["ok"] and r["shortcuts"]["toggle_live"] == "ctrl+alt+k"
    assert config.load_settings()["shortcuts"] == {"toggle_live": "ctrl+alt+k"}
    assert api.set_shortcut("toggle_live", "")["shortcuts"]["toggle_live"] == "ctrl+alt+shift+s"
    assert config.load_settings()["shortcuts"] == {}


@pytest.mark.parametrize("name,combo,why", [
    ("toggle_live", "ctrl+s", "two or more"), ("toggle_live", "ctrl+alt+shift+d", "showing Dolmi"),
    ("toggle_live", "ctrl+alt+shift+b", "Another Dolmi shortcut"), ("nope", "ctrl+alt+k", "Unknown")])
def test_set_shortcut_refuses(api, name, combo, why):
    r = api.set_shortcut(name, combo)
    assert not r["ok"] and why in r["message"]
    assert config.load_settings().get("shortcuts", {}) == {}


def test_bad_saved_shortcuts_fall_back_to_defaults(api):
    api._settings["shortcuts"] = {"toggle_live": "ctrl+s", "toggle_bar": "ctrl+alt+9", "evil": "x"}
    assert api._shortcut_map() == {**app_mod.DEFAULT_SHORTCUTS, "toggle_bar": "ctrl+alt+9"}
    api._settings["shortcuts"] = "junk"
    assert api._shortcut_map() == app_mod.DEFAULT_SHORTCUTS


class FakeHandle:
    def __init__(self, bindings, on_press):
        self.bindings, self.on_press, self.stopped = bindings, on_press, False
        self.status = {n: n != "toggle_lock" for n in bindings}

    def stop(self):
        self.stopped = True


@pytest.fixture
def handles(monkeypatch):
    made = []

    def start(bindings, on_press):
        made.append(FakeHandle(bindings, on_press))
        return made[-1]
    monkeypatch.setattr(dolmi_platform, "start_shortcuts", start)
    return made


def test_start_registers_every_action_and_reports_status(api, handles):
    status = api._start_shortcuts()
    assert handles[0].bindings["toggle_live"] == (("ctrl", "alt", "shift"), "s")
    assert set(handles[0].bindings) == set(app_mod.DEFAULT_SHORTCUTS)
    assert status["toggle_lock"] is False and api.state()["shortcutStatus"]["toggle_live"] is True


def test_change_or_switch_off_re_registers(api, handles):
    assert api.set_shortcut("toggle_bar", "ctrl+alt+9")["ok"] and handles == []   # not started yet: nothing
    api._start_shortcuts()
    api.set_shortcut("toggle_bar", "ctrl+alt+8")
    assert handles[0].stopped and handles[1].bindings["toggle_bar"] == (("ctrl", "alt"), "8")
    api.set_setting("shortcuts_on", False)
    assert handles[1].stopped and len(handles) == 2 and api.state()["shortcutStatus"] == {}
    api.set_setting("shortcuts_on", True)
    assert len(handles) == 3


def test_pressed_shortcuts(api, handles, monkeypatch):
    api._start_shortcuts()
    monkeypatch.setattr(dolmi_platform, "set_click_through", lambda w, on: True)
    press = handles[0].on_press
    press("toggle_lock")
    assert config.load_settings()["bar_locked"] is True and "onBarLock(true)" in api._window.js()
    press("toggle_lock")
    assert config.load_settings()["bar_locked"] is False
    for name in ("toggle_live", "toggle_bar", "toggle_source"):
        press(name)
        assert f'window.dolmi.onShortcut("{name}")' in api._window.js()
    press("unknown")
    assert "unknown" not in api._window.js()


def test_both_platforms_map_every_allowed_key():
    keys = [chr(c) for c in range(ord("a"), ord("z") + 1)] + [str(d) for d in range(10)]
    assert all(windows._shortcut_vk(k) for k in keys + [f"f{n}" for n in range(1, 25)])
    assert all(k in mac._SC_KEYS for k in keys + [f"f{n}" for n in range(1, 13)])
    assert len(set(mac._SC_KEYS.values())) == len(mac._SC_KEYS)
    assert windows._shortcut_vk("f1") == 0x70 and windows._shortcut_vk("space") is None
    assert "start_shortcuts" in dolmi_platform.API


@pytest.mark.skipif(sys.platform != "win32", reason="RegisterHotKey")
def test_windows_registers_fires_and_unregisters():
    import ctypes
    fired = threading.Event()
    got = []
    h = windows.start_shortcuts({"t": (("ctrl", "alt", "shift"), "f24")}, lambda n: (got.append(n), fired.set()))
    try:
        assert h.status == {"t": True}
        twin = windows.start_shortcuts({"t2": (("ctrl", "alt", "shift"), "f24")}, lambda n: None)
        assert twin.status == {"t2": False}          # taken (here by the first): reported, not raised
        twin.stop()
        u = ctypes.windll.user32
        keys = (0x11, 0x12, 0x10, 0x87)              # Ctrl, Alt, Shift, F24
        for vk in keys:
            u.keybd_event(vk, 0, 0, 0)
        for vk in reversed(keys):
            u.keybd_event(vk, 0, 2, 0)               # KEYEVENTF_KEYUP
        assert fired.wait(3), "the synthesised Ctrl+Alt+Shift+F24 did not reach the shortcut"
        assert got == ["t"]
    finally:
        h.stop()
    assert not h.is_alive()
    again = windows.start_shortcuts({"t": (("ctrl", "alt", "shift"), "f24")}, lambda n: None)
    assert again.status == {"t": True}               # stop() really unregistered it
    again.stop()
