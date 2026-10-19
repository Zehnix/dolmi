"""APP-3: the 'nothing heard' warning while live.

The watcher is driven tick by tick with a fake clock (api._clock), so no test waits 20 real seconds.
Captions arrive through api._last_caption (set by the caption pump on every non-empty final/draft).
"""
import threading

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
