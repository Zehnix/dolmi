"""VOICE-7 headphone mix and VOICE-8 natural voice: mixing, ducking, hold, split, name chime, setup checks,
catch-up speed, the Kokoro -> OS voice fallback, and the Settings bridge. Fake audio devices, fake
voices and a fake ONNX session stand in for real hardware and models."""
import io
import threading
import wave

import numpy as np
import pytest

import config
from ui import app as app_mod
from voice import kokoro, mixer, reader, tts

SR = mixer.SR


class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


def tone(seconds, level=0.5, sr=SR):
    return np.full(int(sr * seconds), level, np.float32)


def settle(m, clock, seconds, feed=None):
    """Render `seconds` of mix in 20 ms blocks (the clock moves with them); the last block."""
    out = None
    for _ in range(int(seconds / (mixer.BLOCK / SR))):
        if feed:
            feed()
        out = m.render()
        clock.t += mixer.BLOCK / SR
    return out


def german_level(m, clock, seconds=1.0, voice=False):
    def feed():
        m.feed_german(tone(mixer.BLOCK / SR, 1.0, 16000), 16000)
        if voice:
            m.play(tone(mixer.BLOCK / SR, 0.0), SR)    # silent voice samples still count as speaking
    return float(settle(m, clock, seconds, feed)[:, 1].max())


# ------------------------------------------------------------------ mixer
def test_safety_net_is_ten_percent_by_default_and_clamped():
    assert mixer.Mixer().safety == mixer.SAFETY_DEFAULT == 0.10
    assert mixer.Mixer(safety=0.9).safety == mixer.SAFETY_MAX == 0.30
    assert mixer.Mixer(safety=-1).safety == 0.0 and mixer.Mixer(safety="x").safety == 0.10


def test_german_is_ducked_while_the_voice_speaks_and_raised_when_idle():
    clock = Clock()
    m = mixer.Mixer(safety=0.10, clock=clock)
    speaking = german_level(m, clock, voice=True)
    assert speaking == pytest.approx(0.10 * mixer.DUCK, abs=1e-3)
    just_after = german_level(m, clock, seconds=0.3)        # within IDLE_HOLD: back to the safety level
    assert just_after == pytest.approx(0.10, abs=0.02)
    idle = german_level(m, clock, seconds=1.5)
    assert idle == pytest.approx(0.10 * mixer.IDLE_BOOST, abs=1e-3)


def test_zero_means_english_only_and_gains_ramp_without_jumps():
    clock = Clock()
    m = mixer.Mixer(safety=0.0, clock=clock)
    assert german_level(m, clock) == 0.0
    m.configure(safety=0.3)
    m.feed_german(tone(0.02, 1.0, 16000), 16000)
    first = m.render()[:, 0]
    assert first.max() < 0.3 * mixer.IDLE_BOOST and np.all(np.diff(first) >= -1e-6)   # ramps up, no click


def test_hold_plays_the_original_at_full_level_and_the_voice_quietly():
    clock = Clock()
    m = mixer.Mixer(safety=0.10, clock=clock)
    m.hold(True)
    out = None
    for _ in range(50):
        m.feed_german(tone(0.02, 0.5, 16000), 16000)
        m.play(tone(0.02, 0.5), SR)
        out = m.render()
        clock.t += 0.02
    assert float(out[:, 0].max()) == pytest.approx(0.5 + 0.5 * mixer.HOLD_VOICE, abs=0.01)
    m.hold(False)
    assert german_level(m, clock, voice=True) == pytest.approx(0.10 * mixer.DUCK, abs=1e-3)


def test_stereo_split_puts_english_left_and_german_right():
    clock = Clock()
    m = mixer.Mixer(safety=0.30, split=True, clock=clock)
    out = None
    for _ in range(50):
        m.play(tone(0.02, 0.5), SR)
        m.feed_german(tone(0.02, 1.0, 16000), 16000)
        out = m.render()
        clock.t += 0.02
    assert float(out[:, 0].max()) == pytest.approx(0.5, abs=1e-3)
    assert float(out[:, 1].max()) == pytest.approx(0.3 * mixer.DUCK, abs=1e-3)
    m.configure(split=False)
    both = m.render()
    assert np.array_equal(both[:, 0], both[:, 1])


def test_german_off_drops_the_safety_net_and_old_audio_is_not_kept():
    m = mixer.Mixer(german=False)
    m.feed_german(tone(1.0, 1.0, 16000), 16000)
    assert m.render().max() == 0.0
    m = mixer.Mixer()
    m.feed_german(tone(3.0, 1.0, 16000), 16000)
    assert m._german.size == int(mixer.GERMAN_MAX_LAG * SR)   # stays in sync with the meeting


def test_name_chime_matches_whole_words_without_case_or_accents_and_waits():
    clock = Clock()
    m = mixer.Mixer(names="Jürgen, Anni", clock=clock)
    assert not m.check_name("Wir planen den Annual Report.")
    assert m.check_name("Kannst du das übernehmen, JURGEN?")
    assert m._fx.size == mixer.chime().size
    assert not m.check_name("Anni, noch eine Frage")            # cooldown
    clock.t += mixer.CHIME_COOLDOWN + 1
    assert m.check_name("Anni, noch eine Frage")
    assert mixer.Mixer(names="").check_name("Anni") is False


def test_voice_wait_and_cut():
    m = mixer.Mixer(german=False)
    m.play(tone(0.1), SR)
    assert m.tts_busy() and not m.wait_tts(timeout=0.05)
    m.stop_tts()
    assert not m.tts_busy() and m.wait_tts(timeout=0.05)


class FakeSink:
    name = "fake-headphones"

    def __init__(self):
        self.blocks, self.closed = [], False
        self.got = threading.Event()

    def write(self, block):
        self.blocks.append(block)
        if len(self.blocks) > 3:
            self.got.set()
        threading.Event().wait(0.002)

    def close(self):
        self.closed = True


def test_output_thread_writes_stereo_blocks_and_closes_the_device():
    sink = FakeSink()
    m = mixer.Mixer().start(sink)
    m.play(tone(0.05), 16000)
    assert sink.got.wait(5) and m.wait_tts(5)
    m.stop()
    assert sink.closed and sink.blocks[0].shape == (mixer.BLOCK, 2) and sink.blocks[0].dtype == np.float32


def test_setup_check_warns_on_bluetooth_handsfree_speakers_and_the_captured_device():
    assert mixer.check_device("Headphones (WH-1000XM4 Stereo)")[0] == "ok"
    assert mixer.check_device("Headset (WH-1000XM4 Hands-Free AG Audio)")[0] == "warn"
    assert "Bluetooth" in mixer.check_device("Kopfhörer (AirPods Hands-Free)")[1]
    assert mixer.check_device("Speakers (Realtek(R) Audio)")[0] == "warn"
    level, msg = mixer.check_device("Headphones (USB)", "Headphones (USB)")
    assert level == "warn" and "own voice" in msg
    assert mixer.check_device("")[0] == "warn"


def test_test_tone_and_chime_are_quiet_and_click_free():
    for x in (mixer.test_tone(), mixer.chime()):
        assert 0 < np.abs(x).max() <= 0.3 and abs(x[0]) < 0.01 and abs(x[-1]) < 0.02


def test_resample_keeps_duration():
    assert mixer.resample(np.zeros(16000), 16000).size == SR
    assert mixer.resample(np.zeros(22050), 22050).size == SR


# ------------------------------------------------------------------ reader: speed-up + fallback chain
def test_catch_up_speed_starts_after_two_seconds_and_caps():
    assert reader.catch_up_speed(1.9) == 1.0 and reader.catch_up_speed(None) == 1.0
    assert reader.catch_up_speed(2.01) == pytest.approx(1.15, abs=0.01)
    assert reader.catch_up_speed(3.0) == pytest.approx(1.225, abs=0.01)
    assert reader.catch_up_speed(5.5) == 1.3


class SpeedTTS:
    name = "speedy"
    speeds = True

    def __init__(self, fail=False):
        self.fail, self.said, self.closed = fail, [], False
        self.done = threading.Event()

    def available(self):
        return True

    def speak(self, text, speed=1.0):
        if self.fail:
            raise RuntimeError("model broke")
        self.said.append((text, speed))
        self.done.set()

    def close(self):
        self.closed = True


def test_late_lines_are_spoken_faster_and_too_old_lines_still_skipped():
    clock = Clock(1000.0)
    tt = SpeedTTS()
    r = reader.Reader(tt, clock=clock)
    r.offer("late", end=997.0)          # 3 s behind
    r.offer("on time", end=1000.0)
    r._say(*r.next_item(0))
    r._say(*r.next_item(0))
    assert tt.said == [("late", pytest.approx(1.225, abs=0.01)), ("on time", 1.0)]
    r.offer("ancient", end=990.0)
    assert r.next_item(0) is None and r.skipped == 1


def test_a_failing_natural_voice_falls_back_to_the_os_voice_for_the_same_line():
    broken, os_voice = SpeedTTS(fail=True), SpeedTTS()
    r = reader.Reader(broken, clock=Clock(), fallbacks=[os_voice]).start()
    r.offer("Hello.")
    assert os_voice.done.wait(5) and os_voice.said == [("Hello.", 1.0)]
    assert r.backend is os_voice and broken.closed and r.spoken == 1
    r.stop()


class PcmTTS(SpeedTTS):
    pcm = True

    def synth_chunks(self, text, speed=1.0):
        self.said.append((text, speed))
        yield np.full(2400, 0.1, np.float32), 24000
        yield np.full(2400, 0.1, np.float32), 24000


def test_pcm_voices_play_through_the_mix():
    m = mixer.Mixer(german=False)
    tt = PcmTTS()
    r = reader.Reader(tt, clock=Clock(), mixer=m)
    r.offer("Hi.")
    done = threading.Thread(target=lambda: r._say(*r.next_item(0)))
    done.start()
    for _ in range(100):
        m.render()
        if not done.is_alive():
            break
        done.join(0.01)
    done.join(2)
    assert not done.is_alive() and tt.said == [("Hi.", 1.0)] and r.spoken == 1


def test_natural_chain_is_kokoro_then_the_os_voice():
    names = [b.name for b in tts.natural_backends()]
    assert names[0] == "kokoro" and names[1:] == [b.name for b in tts.os_backends()]


def test_windows_rate_and_wav_decoding():
    assert tts.rate_for(1.0) == 0 and tts.rate_for(1.15) == 3 and tts.rate_for(1.3) == 5
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(np.array([16384, 16384, -16384, -16384], "<i2").tobytes())
    x, sr = tts.wav_to_pcm(buf.getvalue())
    assert sr == 22050 and x.tolist() == [0.5, -0.5]


# ------------------------------------------------------------------ kokoro (fake model)
def test_first_piece_is_short_for_fast_first_audio():
    assert kokoro.split_chunks("Good morning everyone, the budget was approved.") == [
        "Good morning everyone,", "the budget was approved."]
    long = "We will move the release to next week because the tests are not ready yet"
    parts = kokoro.split_chunks(long)
    assert len(parts[0].split()) == kokoro.FIRST_WORDS and " ".join(parts) == long
    assert kokoro.split_chunks("  ") == []


def test_tokens_drop_unknown_symbols_and_cap_length():
    vocab = {"a": 1, "b": 2}
    assert kokoro.tokens("a?b", vocab) == [1, 2]
    assert len(kokoro.tokens("a" * 900, vocab)) == kokoro.MAX_TOKENS


class FakeSession:
    def __init__(self):
        self.calls = []

    def run(self, _, feeds):
        self.calls.append(feeds)
        return [np.full((1, 2400 * feeds["input_ids"].shape[1]), 0.1, np.float32)]


def make_folder(tmp_path):
    (tmp_path / "voices").mkdir()
    (tmp_path / "tokenizer.json").write_text('{"model": {"vocab": {"h": 1, "i": 2, " ": 3}}}', encoding="utf-8")
    np.arange(kokoro.MAX_TOKENS * 256, dtype=np.float32).tofile(tmp_path / "voices" / "af_heart.bin")
    return tmp_path


def test_kokoro_runs_the_model_without_espeak(tmp_path):
    sess = FakeSession()
    v = kokoro.KokoroVoice(folder=make_folder(tmp_path), g2p=lambda t: t.lower(), session=sess)
    pieces = list(v.synth_chunks("Hi, hi hi.", speed=1.2))
    assert len(pieces) == 2 and pieces[0][1] == kokoro.SR
    feeds = sess.calls[0]
    assert feeds["input_ids"].tolist() == [[0, 1, 2, 0]]                  # pads around 'hi' (',' unknown)
    assert feeds["style"].shape == (1, 256) and feeds["style"][0, 0] == 2 * 256   # row = token count
    assert feeds["speed"].tolist() == [pytest.approx(1.2)]
    first, whole = v.first_audio("Hi, hi.")
    assert 0 <= first <= whole


def test_kokoro_speaks_through_the_player_and_misaki_has_no_fallback():
    import inspect
    src = inspect.getsource(kokoro)
    assert "fallback=None" in src and "espeak" not in src.split('"""', 2)[2]
    v = kokoro.KokoroVoice(session=FakeSession())
    with pytest.raises(RuntimeError):
        v.speak("hi")                       # no player: the reader falls back to the OS voice


def test_kokoro_is_unavailable_until_downloaded(monkeypatch):
    monkeypatch.setattr(kokoro, "model_dir", lambda: None)
    assert kokoro.KokoroVoice().available() is False


def test_download_goes_through_netgate(monkeypatch):
    import netgate
    seen = []
    monkeypatch.setattr(netgate, "check", lambda what: seen.append(what))
    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda repo, allow_patterns: seen.append((repo, allow_patterns)))
    kokoro.download()
    assert seen == ["download", (kokoro.REPO, kokoro.FILES)]


# ------------------------------------------------------------------ app bridge
def test_new_settings_are_off_by_default_and_validated(api):
    d = config.DEFAULTS
    assert d["voice_natural"] is False and d["voice_mix"] is False and d["voice_safety"] == 0.10
    assert d["voice_split"] is False and d["voice_out_device"] == "" and d["voice_names"] == ""
    assert api.set_setting("voice_safety", 0.5) and api._settings["voice_safety"] == 0.30
    assert api.set_setting("voice_safety", True) is False and api.set_setting("voice_safety", "1") is False
    assert api.set_setting("voice_names", "  Anna " + "x" * 300) and len(api._settings["voice_names"]) == 120
    assert api.set_setting("voice_mix", "on") is False and api.set_setting("voice_out_device", 3) is False
    s = api.state()
    assert s["voiceSafety"] == 0.30 and s["voiceMix"] is False and s["shortcuts"]["hold_original"]


@pytest.fixture
def fake_out(api, monkeypatch):
    sinks = []

    def sink(device):
        s = FakeSink()
        s.device = device
        sinks.append(s)
        return s
    monkeypatch.setattr(api, "_voice_sink", sink)
    return sinks


def test_mix_feeds_german_only_when_dolmi_would_not_hear_itself(api, monkeypatch, fake_out):
    voice = SpeedTTS()
    monkeypatch.setattr(api, "_voice_backends", lambda: [voice])
    api.set_setting("voice_readout", True)
    api.set_setting("voice_mix", True)
    api._voice_offer("Hello.", {"end": None})
    m = api._voice_mix
    assert m is not None and m.german_on is False            # default speaker = captured speaker
    api._voice_feed(np.ones(1600, np.float32))
    assert m._german.size == 0
    api.set_setting("audio_device", "Speakers (Realtek)")
    api.set_setting("voice_out_device", "Headphones (USB)")    # restarts the voice
    assert api._voice_mix is None and fake_out[0].closed
    api._voice_offer("Hello again.", {"end": None})
    m = api._voice_mix
    assert m.german_on and fake_out[-1].device == "Headphones (USB)"
    api._voice_feed(np.ones(1600, np.float32))
    assert m._german.size > 0
    api.set_setting("voice_safety", 0.2)
    assert m.safety == 0.2
    api.set_setting("voice_readout", False)
    assert api._voice is None and api._voice_mix is None


def test_capture_queue_hands_chunks_to_the_mix_and_survives_its_errors():
    got = []
    q = app_mod._VoiceTapQueue(got.append)
    q.put(np.zeros(1600, np.float32))
    assert len(got) == 1 and q.get().size == 1600

    def boom(_):
        raise RuntimeError("x")
    q = app_mod._VoiceTapQueue(boom)
    q.put(np.zeros(10, np.float32))
    assert q.get().size == 10


def test_name_heard_chimes_and_tells_the_page(api, fake_out):
    emitted = []
    api._emit = lambda fn, *a: emitted.append(fn)
    api._voice_mix = mixer.Mixer(names="Anna")
    api._voice_name_check("Anna, kannst du das machen?")
    assert emitted == ["onNameHeard"]
    api._voice_mix = None
    api._voice_name_check("Anna?")                            # no mix running: nothing
    assert emitted == ["onNameHeard"]


def test_hold_original_shortcut_holds_until_the_key_is_released(api, monkeypatch):
    m = api._voice_mix = mixer.Mixer()
    monkeypatch.setattr(app_mod.dolmi_platform, "OS", "win")
    down = threading.Event()
    down.set()
    monkeypatch.setattr(app_mod, "_key_down", lambda key: down.is_set())
    api._on_shortcut("hold_original")
    assert m.held
    down.clear()
    for _ in range(100):
        if not m.held:
            break
        threading.Event().wait(0.02)
    assert not m.held
    monkeypatch.setattr(app_mod.dolmi_platform, "OS", "mac")   # no key-up event there: toggles
    api._on_shortcut("hold_original")
    assert m.held
    api._on_shortcut("hold_original")
    assert not m.held


def test_voice_setup_reports_headphones_and_the_natural_voice(api, monkeypatch, fake_out):
    monkeypatch.setattr(mixer, "headphone_devices", lambda: ["Headphones (USB)", "Speakers (Realtek)"])
    monkeypatch.setattr(kokoro, "is_installed", lambda: False)
    v = api.voice_setup()
    assert v["devices"] == ["Headphones (USB)", "Speakers (Realtek)"] and v["check"]["level"] == "warn"
    assert v["natural"]["installed"] is False and v["natural"]["sizeMb"] == kokoro.SIZE_MB
    api.set_setting("voice_out_device", "Headphones (USB)")
    assert api.voice_setup()["check"]["level"] == "ok"
    assert api.voice_test_tone() == {"ok": True}
    assert fake_out[-1].got.wait(5)


def test_settings_page_has_the_voice_setup_controls():
    js = (app_mod.Path(app_mod.__file__).parent / "web" / "js" / "settings.js").read_text(encoding="utf-8")
    for needle in ("swVoiceNatural", "swVoiceMix", "setVoiceOut", "setVoiceSafety", 'max="30"', "swVoiceSplit",
                   "setVoiceNames", "voiceTest", "download_voice_model", "hold_original"):
        assert needle in js, needle
