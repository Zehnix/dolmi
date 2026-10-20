"""VOICE-0a timing harness and VOICE-1 read-aloud prototype: queue/backlog, backend choice, settings.
A fake TTS stands in for the system voice, so no audio device is needed."""
import json
import threading

import config
from ui import app as app_mod
from voice import reader, timing, tts


class FakeTTS:
    name = "fake"

    def __init__(self, ok=True):
        self.ok, self.said, self.done = ok, [], threading.Event()

    def available(self):
        return self.ok

    def speak(self, text):
        self.said.append(text)
        self.done.set()

    def first_audio(self, text):
        return 0.05, 0.08


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


# ------------------------------------------------------------------ reader (backlog control)
def test_queue_keeps_only_the_newest_lines():
    r = reader.Reader(FakeTTS(), clock=Clock())
    for t in ("one", "two", "three"):
        r.offer(t)
    assert r.skipped == 2 and r.next_line(wait=0) == "three"
    assert r.next_line(wait=0) is None


def test_lines_older_than_max_age_are_skipped():
    clock = Clock()
    r = reader.Reader(FakeTTS(), clock=clock)
    r.offer("old", end=clock.t - reader.MAX_AGE - 0.1)
    r.offer("fresh", end=clock.t - 1)
    assert r.next_line(wait=0) == "fresh" and r.skipped == 1


def test_empty_lines_and_lines_after_stop_are_ignored():
    r = reader.Reader(FakeTTS(), clock=Clock())
    r.offer("  ")
    r.stop()
    r.offer("late")
    assert r.next_line(wait=0) is None and r.skipped == 0


def test_the_worker_speaks_and_a_failing_voice_does_not_stop_it():
    fake = FakeTTS()
    calls = []

    def speak(text):
        calls.append(text)
        if text == "bad":
            raise RuntimeError("voice broke")
        fake.done.set()
    fake.speak = speak
    r = reader.Reader(fake, max_queue=2).start()
    r.offer("bad")
    r.offer("good")
    assert fake.done.wait(5) and calls == ["bad", "good"]
    r.stop()


class SlowTTS(FakeTTS):
    """Speaks until cancelled, like a long line on the real voice."""

    def __init__(self):
        super().__init__()
        self.started, self.cut, self.closed = threading.Event(), threading.Event(), 0

    def speak(self, text):
        self.started.set()
        if self.cut.wait(5):
            raise RuntimeError("cancelled")
        self.said.append(text)

    def cancel(self):
        self.cut.set()

    def close(self):
        self.closed += 1


def test_stop_cuts_the_line_being_spoken_and_closes_the_voice():
    slow = SlowTTS()
    r = reader.Reader(slow).start()
    r.offer("a long line")
    assert slow.started.wait(5)
    r.stop()
    r._thread.join(5)
    assert not r._thread.is_alive() and slow.said == [] and slow.closed == 1 and r.spoken == 0


def test_stop_before_start_still_closes_the_voice():
    slow = SlowTTS()
    reader.Reader(slow).stop()
    assert slow.closed == 1


# ------------------------------------------------------------------ backend choice
def test_select_backend_takes_the_first_available():
    a, b = FakeTTS(ok=False), FakeTTS()
    assert tts.select_backend([a, b]) is b
    assert tts.select_backend([FakeTTS(ok=False)]) is None


def test_a_backend_whose_check_fails_is_passed_over():
    class Broken(FakeTTS):
        def available(self):
            raise OSError("no powershell")
    ok = FakeTTS()
    assert tts.select_backend([Broken(), ok]) is ok


def test_there_is_an_os_voice_for_windows_and_mac():
    names = {b.name for b in tts.os_backends()}
    assert names == {"windows", "mac-say"}


# ------------------------------------------------------------------ timing harness
def test_record_has_utc_iso_timestamps_in_stage_order():
    rec = timing.record("u1", {"capture": 0.1, "vad_close": 0.6, "asr": 0.3, "mt": 0.02}, 0.0)
    assert rec["timestamps"]["speech_end"] == "1970-01-01T00:00:00.000Z"
    assert rec["timestamps"]["mt"] == "1970-01-01T00:00:01.020Z"
    assert "tts_first" not in rec["timestamps"] and rec["total"] == 1.02


def test_run_and_summary_with_fake_engine():
    class Eng:
        def transcribe(self, audio):
            return "Hallo zusammen.", "de"

        def translate(self, text, lang):
            return "Hello everyone."
    ticks = iter(range(100))
    items = [(f"u{i}", [0.0] * 16000, {"en": "Hello everyone."}) for i in range(5)]
    recs = timing.run(items, Eng(), FakeTTS(), clock=lambda: 0.0, perf=lambda: next(ticks) * 0.1)
    assert len(recs) == 5 and recs[0]["en"] == "Hello everyone."
    s = timing.summary(recs)
    assert set(s) == {*timing.STAGES, "total"} and s["tts_first"]["p50"] == 0.05
    assert s["capture"]["simulated"] and not s["asr"]["simulated"]
    json.dumps(s)


# ------------------------------------------------------------------ app (bridge + pump)
def test_read_aloud_is_off_by_default_and_needs_a_bool(api):
    assert config.DEFAULTS["voice_readout"] is False and api.state()["voiceReadout"] is False
    assert api.set_setting("voice_readout", "yes") is False
    assert api.set_setting("voice_readout", True) and config.load_settings()["voice_readout"] is True


def test_finals_are_read_only_when_on(api, monkeypatch):
    fake = FakeTTS()
    monkeypatch.setattr(tts, "select_backend", lambda backends=None: fake)
    api._voice_offer("Hello.", {"end": None})
    assert getattr(api, "_voice", None) is None          # off: no reader, no voice process
    api.set_setting("voice_readout", True)
    api._voice_offer("Hello.", {"end": None})
    assert fake.done.wait(5) and fake.said == ["Hello."]
    assert api.voice_state()["backend"] == "fake"
    api.set_setting("voice_readout", False)
    assert api._voice is None


def test_no_voice_on_this_computer_keeps_captions(api, monkeypatch):
    monkeypatch.setattr(tts, "select_backend", lambda backends=None: None)
    api.set_setting("voice_readout", True)
    api._voice_offer("Hello.", {})
    assert api.voice_state() == {"on": True, "available": False, "backend": "", "spoken": 0, "skipped": 0,
                                 "mix": False}


def test_settings_page_has_the_switch_and_says_it_does_not_mute():
    js = (app_mod.Path(app_mod.__file__).parent / "web" / "js" / "settings.js").read_text(encoding="utf-8")
    assert "settingsMount('audio.voice')" in js and "voice_readout" in js and "not muted" in js
