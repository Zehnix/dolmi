"""APP-3: the 'nothing heard' warning while live.

The watcher is driven tick by tick with a fake clock (api._clock), so no test waits 20 real seconds.
Most tests set api._last_caption directly; the pump tests at the end check that the real caption
pump (_ui_q -> _pump) sets it on every non-empty final/draft and that the watcher sees it.
"""
import itertools
import threading
import time

import pytest

from ui import app as app_mod

QUIET = 0.0
LOUD = 0.05      # well above the silence threshold


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def live(api):
    """An Api in a live session with a fake clock; returns (api, clock, tick)."""
    clock = Clock()
    api._clock = clock
    api._session = {"token": object(), "stop": threading.Event(), "transcript": None}

    def tick(seconds=1, level=QUIET, caption=False):
        """Advance the clock second by second; each second the input level is `level`
        and, with caption=True, a caption arrives."""
        for _ in range(seconds):
            clock.t += 1
            api._on_level(level)
            if caption:
                api._last_caption = clock.t
            api._heard_tick()
        return api._heard_state

    return api, clock, tick


def warnings(api):
    """Every onHeardWarn(...) argument Python sent to the page, in order."""
    out = []
    for js in api._window.calls:
        if isinstance(js, str) and "onHeardWarn(" in js:
            out.append(js.split("onHeardWarn(", 1)[1].rstrip(")"))
    return out


def test_silence_warns_within_20_seconds(live):
    api, _, tick = live
    assert tick(19) is None
    assert tick(1) == "silent"
    assert warnings(api) == ['"silent"']


def test_captions_every_3_seconds_never_warn(live):
    api, _, tick = live
    for _ in range(30):                      # 90 s of a quiet speaker who is still captioned
        tick(2)
        tick(1, caption=True)
    assert warnings(api) == []


def test_sound_without_captions_warns_no_captions(live):
    api, _, tick = live
    assert tick(19, level=LOUD) is None
    assert tick(1, level=LOUD) == "no_captions"
    assert warnings(api) == ['"no_captions"']


def test_a_caption_clears_the_warning_at_once(live):
    api, _, tick = live
    tick(20)
    assert api._heard_state == "silent"
    assert tick(1, level=LOUD, caption=True) is None
    assert warnings(api) == ['"silent"', "null"]


def test_stop_clears_and_nothing_shows_when_not_live(live):
    api, _, tick = live
    tick(20)
    api._session = None                      # Stop
    assert tick(1) is None
    assert warnings(api)[-1] == "null"
    tick(60)
    assert warnings(api) == ['"silent"', "null"]


def test_a_short_sound_restarts_the_silence_count(live):
    _, _, tick = live
    tick(15)
    tick(1, level=LOUD)
    assert tick(15) is None                  # 15 s after the last sound, but sound and no caption for 31 s
    assert tick(5) in ("silent", "no_captions")


def test_switching_source_restarts_the_count(live):
    api, _, tick = live
    tick(20)
    assert api._heard_state == "silent"
    api.set_source("mic")
    assert tick(1) is None                   # new device gets its own 20 s
    assert tick(19) == "silent"


def test_turned_off_in_settings_never_warns(live):
    api, _, tick = live
    assert api.set_setting("warn_silence", False)
    tick(60)
    assert warnings(api) == []


def test_turning_it_off_hides_a_shown_warning(live):
    api, _, tick = live
    tick(20)
    api.set_setting("warn_silence", False)
    assert tick(1) is None
    assert warnings(api) == ['"silent"', "null"]


def test_not_counted_while_models_load(api):
    """A session without a stop event is still loading models: nothing is heard yet by design."""
    clock = Clock()
    api._clock = clock
    api._session = {"token": object(), "stop": None, "transcript": None}
    for _ in range(40):
        clock.t += 1
        api._heard_tick()
    assert warnings(api) == []


def test_watch_loop_ticks_every_second(api, monkeypatch):
    sleeps, ticks = [], []

    def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) > 3:
            raise SystemExit

    monkeypatch.setattr(api, "_sleep", fake_sleep)
    monkeypatch.setattr(api, "_heard_tick", lambda: ticks.append(1))
    with pytest.raises(SystemExit):
        api._heard_watch_loop()
    assert sleeps[:3] == [1.0, 1.0, 1.0] and len(ticks) == 3


def test_open_mic_privacy_only_opens_the_os_page(api, monkeypatch):
    opened = []
    monkeypatch.setattr(app_mod.dolmi_platform, "open_path", lambda p: opened.append(p))
    before = dict(api._settings)
    assert api.open_mic_privacy() is True
    assert opened == [app_mod.dolmi_platform.mic_privacy_url]
    assert api._settings == before


def test_open_mic_privacy_failure_is_reported(api, monkeypatch):
    def boom(p):
        raise OSError("no handler")
    monkeypatch.setattr(app_mod.dolmi_platform, "open_path", boom)
    assert api.open_mic_privacy() is False


def test_both_platforms_have_a_mic_privacy_page_and_words():
    from dolmi_platform import mac, windows
    assert windows.mic_privacy_url.startswith("ms-settings:")
    assert mac.mic_privacy_url.startswith("x-apple.systempreferences:")
    for words in (windows.WORDS, mac.WORDS):
        assert words["mic_privacy"]


_marks = itertools.count()


def _through_pump(api, *items):
    """Put items on the real caption queue and wait until the pump has handled all of them
    (a summary marker is queued last; the pump handles items in order)."""
    for it in items:
        api._ui_q.put(it)
    marker = f"mark-{next(_marks)}"
    api._ui_q.put(("sum_done", marker, None))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if any(isinstance(js, str) and marker in js for js in list(api._window.calls)):
            return
        time.sleep(0.01)
    raise AssertionError("caption pump did not handle the items")


def test_pump_sets_last_caption_on_non_empty_final_and_draft(live):
    api, clock, _ = live
    clock.t = 2000.0
    _through_pump(api, ("final", "Hallo", "Hello"))
    assert api._last_caption == 2000.0
    clock.t = 2005.0
    _through_pump(api, ("draft", "", "Hel"))
    assert api._last_caption == 2005.0
    clock.t = 2010.0
    _through_pump(api, ("draft", "", ""), ("final", "", ""))
    assert api._last_caption == 2005.0          # empty results are not captions


def test_real_pump_captions_prevent_no_captions(live):
    api, clock, tick = live
    for second in range(40):
        if second % 3 == 0:
            _through_pump(api, ("final", "Hallo", "Hello"))
        tick(1, level=LOUD)
    assert warnings(api) == []
    assert api._heard_state is None
    tick(int(app_mod.HEARD_AFTER_S) + 1, level=LOUD)   # captions stop, sound goes on
    assert api._heard_state == "no_captions"
    _through_pump(api, ("final", "Hallo", "Hello"))
    assert tick(1, level=LOUD) is None          # Python's state follows the caption too


def test_mac_open_path_reports_a_failure(monkeypatch):
    """macOS 'open' exits non-zero when it cannot open the page; that must reach open_mic_privacy."""
    import subprocess
    from dolmi_platform import mac
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "no application"))
    with pytest.raises(OSError):
        mac.open_path(mac.mic_privacy_url)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    mac.open_path(mac.mic_privacy_url)
