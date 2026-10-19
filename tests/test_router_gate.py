"""STR-6 translation router, STR-7 local-only gate, STR-8 uncertainty, STR-9 cloud failover,
STR-12 adaptive endpointing."""
import queue
import socket
from types import SimpleNamespace as NS

import numpy as np
import pytest

import cloud_speech
import live_subs as m
import netgate
import streaming
import translation_router as tr_router
import uncertainty
from test_pipeline import Collect, cloud_engine, energy_gap, silence, speech, submit_now


@pytest.fixture(autouse=True)
def _network_on():
    yield
    netgate.set_local_only(False)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


# ---- STR-7
def test_local_only_blocks_every_outbound_path():
    import assistant
    import models
    from src import updates
    netgate.set_local_only(True)
    with pytest.raises(netgate.LocalOnly):
        cloud_speech._post_json("https://api.example.com/v1/x", {}, {})
    with pytest.raises(assistant.AssistantError, match="Local-only"):
        list(assistant.stream_answer("claude", "m", "key", "Hi?"))
    with pytest.raises(assistant.AssistantError, match="Local-only"):
        list(assistant.stream_summary("claude", "m", "key", "transcript"))
    with pytest.raises(assistant.AssistantError, match="Local-only"):
        assistant.list_models("claude", "key")
    with pytest.raises(netgate.LocalOnly):
        models.download(models.BY_KEY["opus-de"])
    assert updates.check("1.0.0")["available"] is False      # the offline answer, no crash
    s = socket.socket()
    try:
        with pytest.raises(netgate.LocalOnly):
            s.connect(("93.184.216.34", 443))                # a library's own socket is stopped too
    finally:
        s.close()
    assert {p for p, _ in netgate.blocked()} >= {"captions", "assistant", "models", "download", "update",
                                                  "network"}


def test_local_only_allows_loopback_and_turns_off():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    netgate.set_local_only(True)
    c = socket.socket()
    try:
        c.connect(srv.getsockname())                         # this computer is fine
    finally:
        c.close()
        srv.close()
    netgate.set_local_only(False)
    netgate.check("captions")                                # no raise


def test_a_session_in_local_only_mode_opens_no_connection(monkeypatch):
    """Cloud captions in local-only mode: refused before any socket is opened; with failover on the
    line is captioned on this PC instead."""
    attempts = []
    netgate.set_local_only(True)
    real = socket.socket.connect

    def spy(sock, address, *a):
        attempts.append(address)
        return real(sock, address, *a)

    monkeypatch.setattr(socket.socket, "connect", spy)
    monkeypatch.setattr(cloud_speech, "RETRY_DELAY", 0.0)
    eng = cloud_engine()
    eng.recognize = lambda audio: cloud_speech._post_json("https://api.openai.com/v1/x", {}, {})
    eng.fallback_factory = lambda: NS(transcribe=lambda a: ("Hallo", "de"), translate=lambda t, l: "Hello")
    ui_q = queue.Queue()
    submit_now(eng, speech(0.5), ui_q, Collect(), meta={"overlap": 0.0})
    assert not attempts
    assert [i[1:3] for i in ui_q.queue if i[0] == "final"] == [("Hallo", "Hello")]


# ---- STR-6
def test_router_prefers_provider_and_falls_back_with_cooldown():
    clock, calls = Clock(), []

    def cloud(text, src, dst, ctx, timeout):
        calls.append(text)
        raise RuntimeError("HTTP 429: slow down")

    r = tr_router.Router(lambda t, l: f"local({t})", [("gemini", cloud)], clock=clock,
                         cooldown=tr_router.Cooldown(60, clock))
    assert r.route("a", "de") == tr_router.Result("local(a)", "local", "fallback")
    r.route("b", "de")
    assert calls == ["a"]                                     # cooled down: not asked again
    clock.t = 61
    r.providers = [("gemini", lambda *a: "cloud")]
    assert r.route("c", "de").provider == "gemini"
    assert r.route("d", "de", final=False).provider == "local"   # drafts stay local


def test_router_respects_local_only():
    r = tr_router.Router(lambda t, l: "local", [("gemini", lambda *a: "cloud")])
    netgate.set_local_only(True)
    assert r.route("x", "de").provider == "local"
    assert r.cooldown.ready("gemini")                         # not the provider's fault


def test_cooling_reasons():
    assert tr_router.cooling_reason(RuntimeError("HTTP 503: down")) == "5xx"
    assert tr_router.cooling_reason(TimeoutError("timed out")) == "timeout"
    assert tr_router.cooling_reason(RuntimeError("HTTP 400: bad request")) == ""


def test_pair_table_defaults():
    assert tr_router.pair_default("de") == "opus-de"
    assert tr_router.pair_default("pt") == "opus-mul" and tr_router.pair_tier("pt") == "fair"


def test_cloud_translator_cools_down_after_429():
    import cloud_translate
    clock = Clock()

    def post(*a, **k):
        raise RuntimeError("HTTP 429: quota")

    tr = cloud_translate.CloudTranslator("deepl", "k", post=post, clock=clock, workers=0)
    q = queue.Queue()
    assert tr.submit(1, "Hallo", "Hello", "de", "de", [], q)
    with pytest.raises(RuntimeError):
        tr._run(*tr._jobs.get_nowait())
    assert tr.submit(2, "Hallo", "Hello", "de", "de", [], q) is False and tr.stats["skipped"] == 1
    clock.t = 61
    assert tr.submit(3, "Hallo", "Hello", "de", "de", [], q)


# ---- STR-8
def seg(text, lp=-0.2, ns=0.05, cr=1.2, words=()):
    return NS(text=text, start=0.0, end=2.0, avg_logprob=lp, no_speech_prob=ns, compression_ratio=cr,
              words=[NS(word=w, probability=p) for w, p in words])


def test_uncertainty_flags_weak_lines_and_words():
    sure = uncertainty.line_uncertainty([seg("Guten Morgen", words=[("Guten", 0.9), ("Morgen", 0.95)])])
    assert sure["unsure"] is False and sure["words"] == []
    weak = uncertainty.line_uncertainty([seg("Der Kubernetes Cluster", lp=-1.1,
                                             words=[("Der", 0.9), ("Kubernetes", 0.2), ("Cluster", 0.7)])])
    assert weak["unsure"] and weak["why"] == ["logprob"] and weak["words"] == ["Kubernetes"]
    assert uncertainty.line_uncertainty([seg("ja ja ja", cr=3.0)])["why"] == ["repeats"]
    assert uncertainty.line_uncertainty([]) is None


def test_unsure_marks_only_when_switched_on():
    eng = NS(last_info={"uncertainty": {"unsure": True, "words": []}}, unsure_marks=False)
    assert m._unsure(eng) is None
    eng.unsure_marks = True
    assert m._unsure(eng) == ["?"]


# ---- STR-9
def test_failed_cloud_line_is_captioned_locally_then_cloud_returns(monkeypatch):
    monkeypatch.setattr(cloud_speech, "RETRY_DELAY", 0.0)
    now = [1000.0]
    monkeypatch.setattr(cloud_speech.time, "time", lambda: now[0])
    eng, calls, up = cloud_engine(), [], [False]

    def recognize(audio):
        calls.append(1)
        if not up[0]:
            raise OSError("network unreachable")
        return "Cloud", "Cloud EN"

    eng.recognize = recognize
    eng.fallback_factory = lambda: NS(transcribe=lambda a: ("Lokal", "de"), translate=lambda t, l: "Local")
    ui_q, tr = queue.Queue(), Collect()
    submit_now(eng, speech(0.5), ui_q, tr, meta={"overlap": 0.0})
    assert len(calls) == 2                                   # SEG-11's retry first
    up[0] = True
    now[0] += 5
    submit_now(eng, speech(0.5), ui_q, tr, meta={"overlap": 0.0})
    assert len(calls) == 2                                   # within 30 s: this PC, no probe
    now[0] += 31
    submit_now(eng, speech(0.5), ui_q, tr, meta={"overlap": 0.0})
    items = list(ui_q.queue)
    assert [i[1] for i in items if i[0] == "final"] == ["Lokal", "Lokal", "Cloud"]
    assert [i[1] for i in items if i[0] == "cloud_status"] == ["local", "cloud"]


def test_auth_error_does_not_retry_and_says_check_key(monkeypatch):
    monkeypatch.setattr(cloud_speech, "RETRY_DELAY", 0.0)
    eng, calls = cloud_engine(), []
    eng.on_drop = lambda *a: None

    def bad(audio):
        calls.append(1)
        raise RuntimeError("HTTP 401: invalid key")

    eng.recognize = bad
    ui_q = queue.Queue()
    submit_now(eng, speech(0.5), ui_q, Collect(), meta={"overlap": 0.0})
    [err] = [i for i in ui_q.queue if i[0] == "error"]
    assert len(calls) == 1 and "check your key" in err[1]


# ---- STR-12
def test_adaptive_pause_follows_the_speaker():
    fast, slow = streaming.AdaptivePause(), streaming.AdaptivePause()
    for _ in range(10):
        fast.observe(0.2)
        slow.observe(0.9)
    assert fast.limit(0, 12) == pytest.approx(0.35)          # clamped low
    assert slow.limit(0, 12) == pytest.approx(1.0)           # clamped high
    assert slow.limit(12, 12) < slow.limit(1, 12)            # long buffer: a shorter pause is enough
    assert streaming.AdaptivePause().limit(0, 12) == pytest.approx(0.6)   # no history: the old value


def test_deepest_valley():
    assert streaming.deepest_valley([5, 5, 1, 5, 5, 0.5, 6], start=0, smooth=1) == 5
    assert streaming.deepest_valley([], 0) is None


def _run_segmenter(audio, adaptive):
    s = m.Segmenter(energy_gap, adaptive=adaptive)
    out = []
    for i in range(0, len(audio), m.STEP):
        out += s.feed(audio[i:i + m.STEP])
    return out


def test_fast_talker_finalizes_on_shorter_pauses_when_adaptive():
    parts = []
    for _ in range(8):                                        # short 0.25 s breaths between phrases
        parts += [speech(0.8), silence(0.25)]
    parts += [speech(0.8), silence(0.45), speech(0.8), silence(1.2)]
    audio = np.concatenate(parts)
    assert len(_run_segmenter(audio, True)) > len(_run_segmenter(audio, False))


def test_no_pause_long_speech_cuts_at_the_quietest_moment():
    loud = speech(5, 0.2)
    quiet = speech(0.3, 0.03)                                 # softer, but still speech
    utts = _run_segmenter(np.concatenate([loud, quiet, loud, loud, silence(1)]), True)
    assert utts[0].reason == "cap_valley"
    assert 4.9 < len(utts[0].audio) / m.SR < 5.6
