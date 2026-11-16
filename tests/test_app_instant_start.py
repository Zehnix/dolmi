"""APP-4 Instant Start: the models load in the background when Dolmi opens (only when already downloaded),
stay loaded, and Start reuses them. The page says 'Ready' only once they are loaded."""
import threading
import time

import pytest

import live_subs


class FakeAsr:
    def __init__(self):
        self.runs = []

    def transcribe(self, audio, **kw):
        self.runs.append((len(audio), kw))
        return iter([]), None


@pytest.fixture
def engines(monkeypatch, fake_engine):
    """live_subs.Engine replaced by FakeEngines (with a fake Whisper); the list holds every one built."""
    built = []

    def make(model, device, language="de"):
        e = fake_engine(lang=language)
        e.asr, e.args = FakeAsr(), (model, device, language)
        built.append(e)
        return e
    monkeypatch.setattr(live_subs, "Engine", make)
    return built


@pytest.fixture(autouse=True)
def _no_subtitle_window(api):
    """Start opens the subtitle bar; a real webview window cannot be created from the test's Start thread."""
    api._settings["overlay"] = False


@pytest.fixture
def installed(api, monkeypatch):
    monkeypatch.setattr(api, "missing_models", lambda: [])
    return api


def _states(api):
    return [c for c in api._window.calls if isinstance(c, str) and "onEngine(" in c]


def test_warm_up_loads_and_primes_the_models(installed, engines):
    api = installed
    assert api.state()["engineState"] == "idle"
    assert api._warm_once() is True
    assert len(engines) == 1 and api._engine is engines[0]
    assert engines[0].asr.runs and engines[0].asr.runs[0][1]["vad_filter"] is False   # decoder really ran
    assert api.state()["engineState"] == "ready"
    js = _states(api)
    assert '"loading")' in js[0] and '"ready")' in js[-1]
    assert api._warm_once() is True and len(engines) == 1                           # kept, not reloaded


def test_warm_up_never_downloads(api, engines, monkeypatch):
    monkeypatch.setattr(api, "missing_models", lambda: [{"key": "large-v3-turbo", "name": "Turbo", "sizeMb": 1600}])
    assert api._warm_once() is False
    assert engines == [] and api._engine is None
    assert api.state()["engineState"] == "missing"


def test_warm_up_off_by_setting(installed, engines):
    api = installed
    assert api.set_setting("preload_models", False)
    assert api._warm_once() is False and engines == []
    assert not api.set_setting("preload_models", "yes")                              # true/false only


def test_turning_preload_off_frees_the_models(installed, engines):
    api = installed
    api._warm_once()
    api.set_setting("preload_models", False)
    assert api._engine is None and api.state()["engineState"] == "idle"


def _fake_audio(monkeypatch):
    monkeypatch.setattr(live_subs, "loopback_stream", lambda q, stop, *a, **k: stop.wait())
    monkeypatch.setattr(live_subs, "worker", lambda eng, audio_q, ui_q, tr, stop, *a, **k: stop.wait())


def _wait(cond, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_start_reuses_the_warm_models(installed, engines, monkeypatch):
    api = installed
    _fake_audio(monkeypatch)
    api._warm_once()
    assert api.start_listening() == {"ok": True}
    assert _wait(lambda: api._session and api._session.get("stop"))
    assert len(engines) == 1 and "onListening(true" in api._window.js()
    api.stop_listening()
    assert api._engine is engines[0]                                                 # still warm after Stop
    assert api.start_listening() == {"ok": True}
    assert _wait(lambda: api._session and api._session.get("stop"))
    assert len(engines) == 1
    api.stop_listening()


def test_start_during_warm_up_waits_and_reuses(installed, engines, monkeypatch, fake_engine):
    api = installed
    _fake_audio(monkeypatch)
    gate, started = threading.Event(), threading.Event()

    def slow(model, device, language="de"):
        started.set()
        gate.wait(5)
        e = fake_engine()
        engines.append(e)
        return e
    monkeypatch.setattr(live_subs, "Engine", slow)
    warm = threading.Thread(target=api._warm_once)
    warm.start()
    assert started.wait(5)
    api.start_listening()                     # waits for the background load instead of loading again
    time.sleep(0.2)
    assert len(engines) == 0 and not api._session.get("stop")
    gate.set()
    warm.join(5)
    assert _wait(lambda: api._session and api._session.get("stop"))
    assert len(engines) == 1
    api.stop_listening()


def test_a_setting_change_reloads_in_the_background(installed, engines):
    api = installed
    api._warm_once()
    api._warm_req.clear()
    assert api.set_setting("model", "small")
    assert api._engine is None and api._warm_req.is_set()
    assert api.state()["engineState"] == "idle"
    api._warm_once()
    assert len(engines) == 2 and engines[1].args[0] == "small"


def test_a_setting_changed_mid_load_discards_the_stale_models(installed, monkeypatch, fake_engine):
    api = installed
    built = []

    def make(model, device, language="de"):
        if not built:
            api._settings["language"] = "en"     # the user picks another language while it loads
            api._settings_gen += 1
        e = fake_engine()
        built.append((model, language))
        return e
    monkeypatch.setattr(live_subs, "Engine", make)
    api._warm_req.clear()
    assert api._warm_once() is False and api._engine is None and api._warm_req.is_set()
    assert api._warm_once() is True and built[-1][1] == "en"


def test_a_setting_changed_while_priming_discards_the_stale_models(installed, engines, monkeypatch):
    """Priming runs Whisper, the VAD and the translator for seconds; the user often opens Settings then."""
    api = installed
    real_prime = api._prime
    changed = []

    def prime(engine):
        real_prime(engine)
        if not changed:
            changed.append(True)
            assert api.set_setting("model", "small")
    monkeypatch.setattr(api, "_prime", prime)
    api._warm_req.clear()
    assert api._warm_once() is False and api._engine is None and api._warm_req.is_set()
    assert api.state()["engineState"] != "ready"
    assert api._warm_once() is True and api._engine.args[0] == "small"
    assert api.state()["engineState"] == "ready"


def test_no_warm_up_while_captions_run(installed, engines):
    api = installed
    api._session = {"token": object(), "stop": threading.Event(), "transcript": None}
    assert api._warm_once() is False and engines == []


def test_a_finished_download_triggers_a_warm_up(api, monkeypatch):
    import models
    monkeypatch.setattr(models, "download", lambda m: None)
    monkeypatch.setattr(models, "downloaded_mb", lambda m: 0)
    api._warm_req.clear()
    assert api.download_model("small")
    assert _wait(lambda: api._warm_req.is_set())


def test_cloud_source_without_a_key_stays_idle_quietly(installed):
    api = installed
    api._settings["speech_source"] = "openai"
    assert api._warm_once() is False
    assert api.state()["engineState"] == "idle" and "onError" not in api._window.js()


def test_warm_up_also_loads_the_vad_and_the_real_decoder(installed, engines, monkeypatch):
    """Bench (RTX 4060): without these the first caption after Start took ~4 s longer than the next ones."""
    gaps = []
    monkeypatch.setattr(live_subs, "speech_gap", lambda audio: gaps.append(float(abs(audio).max())) or None)
    assert installed._warm_once() is True
    runs = engines[0].asr.runs
    assert gaps and gaps[0] > live_subs.SILENCE_RMS                    # loud enough to pass the early-out
    assert any(kw["beam_size"] == 5 and kw["vad_filter"] is True for _, kw in runs)


class BrokenGpuAsr(FakeAsr):
    """The first start after a boot: CTranslate2 cannot load the CUDA libraries."""
    def transcribe(self, audio, **kw):
        raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")


def test_gpu_libraries_that_never_load_switch_to_the_cpu_and_say_so(installed, engines, monkeypatch):
    import dolmi_platform
    prepared = []
    monkeypatch.setattr(dolmi_platform, "prepare_gpu", lambda: prepared.append(1))
    monkeypatch.setattr(dolmi_platform, "auto_speech", lambda gpu=None: "small")
    real_make = live_subs.Engine

    def make(model, device, language="de"):
        e = real_make(model, device, language)
        e.device = device
        if device != "cpu":
            e.asr = BrokenGpuAsr()
        return e
    monkeypatch.setattr(live_subs, "Engine", make)
    api = installed
    api._settings.update(model="large-v3-turbo", device="cuda")
    assert api._warm_once() is True
    assert prepared                                       # wired up the libraries again before giving up
    assert api._engine.device == "cpu" and api._engine.args[0] == "small"
    assert any("onError(" in c and "processor" in c for c in api._window.calls if isinstance(c, str))


def test_a_gpu_warm_up_that_works_on_the_retry_keeps_the_gpu(installed, engines, monkeypatch):
    import dolmi_platform
    real_make = live_subs.Engine
    fails = {"n": 1}

    class FlakyAsr(FakeAsr):
        def transcribe(self, audio, **kw):
            if fails["n"]:
                fails["n"] -= 1
                raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
            return super().transcribe(audio, **kw)

    def make(model, device, language="de"):
        e = real_make(model, device, language)
        e.device, e.asr = device, FlakyAsr()
        return e
    monkeypatch.setattr(live_subs, "Engine", make)
    monkeypatch.setattr(dolmi_platform, "prepare_gpu", lambda: None)
    api = installed
    api._settings.update(model="large-v3-turbo", device="cuda")
    assert api._warm_once() is True
    assert len(engines) == 1 and api._engine.device == "cuda"


def test_other_warm_up_errors_do_not_drop_the_gpu(installed, engines):
    from ui import app as app_mod
    assert not app_mod._cuda_library_error(RuntimeError("out of memory"))
    assert not app_mod._cuda_library_error(None)
    assert app_mod._cuda_library_error(RuntimeError("Could not load library cudnn_ops64_9.dll"))
