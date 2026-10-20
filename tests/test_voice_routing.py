"""VOICE-5 'English only' routing with a fake Windows audio policy: no real device, route or volume is
ever touched here. The real calls (voice/win_audio.py) are measured by python -m voice.routing_spike."""
import json
import queue
import threading

import numpy as np
import pytest

import config
import dolmi_platform
from voice import routing_win as rw

SPK, HEAD = "{spk}", "{head}"


class FakePolicy:
    def __init__(self):
        self.devs = [{"id": HEAD, "name": "Headphones", "default": True},
                     {"id": SPK, "name": "Speakers", "default": False}]
        self.procs = [(1, 0, "explorer.exe"), (10, 1, "ms-teams.exe"), (11, 10, "msedgewebview2.exe"),
                      (20, 1, "chrome.exe"), (21, 20, "chrome.exe"), (30, 1, "spotify.exe"),
                      (40, 1, "msedgewebview2.exe")]          # a WebView2 that is not Teams'
        self.routes = {"chrome.exe": "{user-choice}"}          # the user had routed Chrome themselves
        self.ep = {SPK: {"muted": False, "level": 0.4}, HEAD: {"muted": False, "level": 0.6}}
        self.fail_set = False

    def _exe(self, pid):
        return next(e for p, _, e in self.procs if p == pid)

    def devices(self):
        return [dict(d) for d in self.devs]

    def processes(self):
        return list(self.procs)

    def get_route(self, pid):
        return self.routes.get(self._exe(pid), "")

    def set_route(self, pid, device_id):
        if self.fail_set:
            raise OSError("policy refused")
        exe = self._exe(pid)
        if device_id:
            self.routes[exe] = device_id
        else:
            self.routes.pop(exe, None)

    def endpoint(self, device_id):
        return dict(self.ep[device_id])

    def set_endpoint(self, device_id, muted=None, level=None):
        if muted is not None:
            self.ep[device_id]["muted"] = muted
        if level is not None:
            self.ep[device_id]["level"] = level


ORIGINAL = {"routes": {"chrome.exe": "{user-choice}"}, SPK: {"muted": False, "level": 0.4}}


def assert_untouched(p):
    assert p.routes == ORIGINAL["routes"] and p.ep[SPK] == ORIGINAL[SPK]
    return True


def test_meeting_families_include_teams_webview_but_not_other_apps():
    found = rw.meeting_processes(FakePolicy().procs)
    assert {(pid, f) for pid, _, f in found} == {(10, "teams"), (11, "teams"), (20, "chrome"), (21, "chrome")}


def test_apply_journals_first_routes_and_mutes_then_restore_puts_everything_back(tmp_path):
    p = FakePolicy()
    r = rw.Router(p, tmp_path, log=lambda m: None)
    assert r.apply(SPK) and r.active
    assert p.routes == {"ms-teams.exe": SPK, "msedgewebview2.exe": SPK, "chrome.exe": SPK}
    assert p.ep[SPK]["muted"] and p.ep[HEAD] == {"muted": False, "level": 0.6}   # headphones untouched
    j = json.loads((tmp_path / rw.JOURNAL).read_text(encoding="utf-8"))
    assert j["apps"]["chrome.exe"]["original"] == "{user-choice}" and j["endpoints"][SPK]["muted"] is False
    assert sorted(r.capture_pids()) == [10, 20]       # one process-loopback tree per app
    assert r.restore() and not r.active
    assert assert_untouched(p) and not (tmp_path / rw.JOURNAL).exists()


def test_no_second_speaker_or_same_speaker_falls_back_to_ducking_with_a_notice(tmp_path):
    p = FakePolicy()
    r = rw.Router(p, tmp_path, log=lambda m: None)
    assert not r.apply("") and r.notice == rw.FALLBACK_NO_DEVICE
    assert not r.apply(HEAD) and r.notice == rw.FALLBACK_SAME_DEVICE     # the default = where you listen
    assert not r.apply("{gone}") and r.notice == rw.FALLBACK_NO_DEVICE
    assert assert_untouched(p) and not r.pending()


def test_a_policy_error_restores_and_falls_back(tmp_path):
    p = FakePolicy()
    p.fail_set = True
    r = rw.Router(p, tmp_path, log=lambda m: None)
    assert not r.apply(SPK) and r.notice == rw.FALLBACK_ERROR
    assert assert_untouched(p) and not r.pending()


def test_crash_then_next_launch_restores_from_the_journal(tmp_path):
    p = FakePolicy()
    rw.Router(p, tmp_path, log=lambda m: None).apply(SPK)       # ... and Dolmi is force-killed here
    fresh = rw.Router(p, tmp_path, log=lambda m: None)          # next launch: a new Router, same journal
    assert fresh.pending() and fresh.restore()
    assert_untouched(p)


def test_an_app_closed_before_restore_is_restored_when_it_runs_again(tmp_path):
    p = FakePolicy()
    r = rw.Router(p, tmp_path, log=lambda m: None)
    r.apply(SPK)
    p.procs = [x for x in p.procs if x[2] != "chrome.exe"]       # Chrome closed (its route persists)
    assert not r.restore() and r.pending()
    p.procs.append((50, 1, "chrome.exe"))
    r.tick()
    assert assert_untouched(p) and not r.pending()


def test_tick_routes_new_helpers_remutes_and_restores_when_the_speaker_disappears(tmp_path):
    p = FakePolicy()
    r = rw.Router(p, tmp_path, log=lambda m: None)
    r.apply(SPK)
    p.procs.append((12, 1, "zoom.exe"))
    p.ep[SPK]["muted"] = False                                   # Windows reset the speaker on replug
    r.tick()
    assert p.routes["zoom.exe"] == SPK and p.ep[SPK]["muted"]
    p.devs = [d for d in p.devs if d["id"] != SPK]               # unplugged / undocked
    p.ep[SPK] = {"muted": True, "level": 0.4}                    # its saved state is still restorable
    r.tick()
    assert not r.active and r.notice == rw.FALLBACK_LOST
    assert p.routes == {"chrome.exe": "{user-choice}"}           # zoom/teams back to the Windows default


def test_process_loopback_stream_mixes_trees_to_float():
    class FakeCap:
        def __init__(self, pid, include_tree, rate, channels):
            assert include_tree and rate == 16000 and channels == 1
            self.closed = False

        def read(self, timeout):
            return (np.full(160, 16384, dtype=np.int16)).tobytes()

        def close(self):
            self.closed = True
    q, stop = queue.Queue(), threading.Event()
    levels = []

    def on_level(v):
        levels.append(v)
        if len(levels) >= 3:
            stop.set()
    rw.process_loopback_stream(q, stop, [10, 20], on_level=on_level, loopback_cls=FakeCap)
    chunk = q.get_nowait()
    assert chunk.dtype == np.float32 and np.allclose(chunk, 1.0)        # 0.5 + 0.5, clipped at 1
    with pytest.raises(OSError):
        rw.process_loopback_stream(q, stop, [], loopback_cls=FakeCap)


def test_process_loopback_stream_follows_roots_that_start_or_exit_after_start():
    opened, closed = [], []

    class FakeCap:
        def __init__(self, pid, include_tree, rate, channels):
            self.pid = pid
            opened.append(pid)

        def read(self, timeout):
            return np.full(160, 1000, dtype=np.int16).tobytes()

        def close(self):
            closed.append(self.pid)
    roots = [[10], [10, 30], [30]]
    t = [0.0]
    q, stop = queue.Queue(), threading.Event()

    def clock():
        t[0] += 1.0
        return t[0]

    def get():
        return roots[0] if t[0] < 4 else roots[1] if t[0] < 8 else roots[2]

    def on_level(_):
        if t[0] > 12:
            stop.set()
    rw.process_loopback_stream(q, stop, get, on_level=on_level, loopback_cls=FakeCap,
                               recheck_s=1.0, clock=clock)
    assert opened == [10, 30] and closed[0] == 10 and set(closed) == {10, 30}


def test_switching_speaker_after_a_crash_saves_and_restores_both(tmp_path):
    p = FakePolicy()
    r = rw.Router(p, tmp_path, log=lambda m: None)
    assert r.apply(SPK)
    other = "{tv}"
    p.devs.append({"id": other, "name": "TV", "default": False})
    p.ep[other] = {"muted": False, "level": 0.9}
    r2 = rw.Router(p, tmp_path, log=lambda m: None)      # "crash": journal left, user picks the TV
    before = dict(p.ep[other])
    assert r2.apply(other)
    assert r2.restore()
    assert p.ep[other] == before and assert_untouched(p)


# ------------------------------------------------------------------ app (bridge)
@pytest.fixture
def win_api(api, monkeypatch):
    fake = FakePolicy()
    monkeypatch.setattr(dolmi_platform, "OS", "win")
    api._routing_policy = lambda: fake
    api._fake = fake
    return api


def test_english_only_is_off_by_default_and_validated(api):
    assert config.DEFAULTS["english_only"] is False and config.DEFAULTS["english_only_device"] == ""
    assert api.set_setting("english_only", "yes") is False
    assert api.set_setting("english_only_device", 3) is False


def test_bridge_state_restore_and_crash_recovery(win_api):
    a, p = win_api, win_api._fake
    s = a.english_only_state()
    assert s["available"] and not s["on"] and [d["name"] for d in s["devices"]] == ["Headphones", "Speakers"]
    a.set_setting("english_only", True)
    a.set_setting("english_only_device", SPK)
    assert a._routing().apply(SPK) and a.english_only_state()["active"]
    a._router = None                                  # Dolmi crashed: next launch
    a._routing_recover()
    assert assert_untouched(p) and not a.english_only_state()["pending"]
    a._routing().apply(SPK)
    assert a.restore_audio() == {"ok": True, "left": False}
    assert_untouched(p)
    a._routing().apply(SPK)
    a.set_setting("english_only", False)              # switching it off restores at once
    assert_untouched(p)


def test_capture_falls_back_to_the_speaker_when_there_is_no_second_device(win_api):
    a = win_api
    a.set_setting("english_only", True)                 # no device picked
    assert a._english_only_capture(queue.Queue(), threading.Event()) is False
    assert "onRouting" in a._window.js() and a._router.notice == rw.FALLBACK_NO_DEVICE
    assert_untouched(a._fake)


def test_settings_page_has_english_only_and_restore():
    from ui import app as app_mod
    js = (app_mod.Path(app_mod.__file__).parent / "web" / "js" / "settings.js").read_text(encoding="utf-8")
    assert "english_only" in js and "restore_audio" in js and "Restore audio" in js and "onRouting" in js
