"""Capture -> segmentation -> Whisper -> ui_q: no speech lost, cuts in the right places, honest filters.

Speech is a sine (loud) and silence is zeros; `energy_gap` stands in for Silero VAD (speech_gap)."""
import queue
import threading
import time
from types import SimpleNamespace as NS

import numpy as np

import live_subs as m

SR = m.SR


def speech(sec, amp=0.1):
    return (amp * np.sin(np.arange(int(sec * SR)) / 5)).astype(np.float32)


def silence(sec):
    return np.zeros(int(sec * SR), np.float32)


def energy_gap(audio, frame=160):
    """speech_gap stand-in: seconds since the last loud 10 ms frame, None when nothing is loud."""
    n = len(audio) // frame * frame
    if not n:
        return None
    rms = np.sqrt((audio[len(audio) - n:].reshape(-1, frame) ** 2).mean(axis=1))
    loud = np.nonzero(rms > m.SILENCE_RMS)[0]
    return None if not len(loud) else (len(rms) - 1 - loud[-1]) * frame / SR


def chunks(audio):
    return [audio[i:i + m.STEP] for i in range(0, len(audio), m.STEP)]


def segment(audio, **kw):
    seg = m.Segmenter(energy_gap, **kw)
    out = []
    for c in chunks(audio):
        out += seg.feed(c)
    return seg, out


class Collect:
    """Transcript stand-in that keeps what worker() writes."""

    def __init__(self):
        self.rows = []

    def add(self, start, end, de, en, meta=None):
        self.rows.append((start, end, de, en, meta))


def run_worker(eng, audio, realtime=False, wait=3.0, finals=None, **kw):
    """Feed `audio` in 100 ms chunks (in real time if asked), then wait for `finals` finals."""
    aq, uq, stop, tr = queue.Queue(), queue.Queue(), threading.Event(), Collect()
    th = threading.Thread(target=m.worker, args=(eng, aq, uq, tr, stop, energy_gap), kwargs=kw, daemon=True)
    th.start()
    for c in chunks(audio):
        aq.put(c)
        if realtime:
            time.sleep(len(c) / SR)
    items, deadline = [], time.time() + wait
    while time.time() < deadline:
        try:
            items.append(uq.get(timeout=0.05))
        except queue.Empty:
            pass
        if finals is not None and sum(i[0] == "final" for i in items) >= finals:
            break
    time.sleep(0.2)
    stop.set()
    th.join(2)
    while not uq.empty():
        items.append(uq.get())
    return items, tr


def test_real_short_lines_and_goodbyes_are_kept():
    for line in ("Tschüss, bis morgen alle!", "Ja.", "OK", "Nein.", "Danke.", "Danke schön.",
                 "Wir schauen heute ZDF.", "Schau mal auf example.com nach dem Termin."):
        assert not m.is_junk(line, energy=0.1, lang="de"), line
    assert not m.is_junk("Danke.", energy=0.001, lang="de")       # real 'Danke' even when quiet
    assert not m.is_junk("Thank you.", energy=0.1, lang="de")     # said out loud: kept


def test_whisper_ghosts_are_junk():
    for line in ("Untertitel im Auftrag des ZDF, 2020", "Vielen Dank fürs Zuschauen!", "..", "-",
                 "Untertitel der Amara.org-Community", "Продолжение следует...", "감사합니다.",
                 "字幕由Amara.org社区提供", "www.zdf.de", "https://example.com/abc"):
        assert m.is_junk(line, energy=0.1, lang="de"), line
    assert m.junk_reason("Продолжение следует...", lang="de") == "non_latin"
    assert not m.is_junk("Продолжение следует...", lang="ru")      # the user picked Russian
    assert not m.is_junk("Продолжение следует...", lang="auto")
    assert m.junk_reason("Thank you.", energy=0.005) == "silence_ghost"
    assert m.junk_reason("Vielen Dank.", energy=0.005) == "silence_ghost"


TERMS = {"crm", "alice", "bob", "carol", "acme", "falcon", "dave"}


ECHO = "CRM, Perketer, Luzer, Erdmann, Kolor, Alice, Alice, Bob, Carol, Michel, Zellow, und Marseille."


def test_echoes_terms_keeps_a_real_list_of_names():
    names = "Alice, Bob, Carol, Dave, Eve und Frank kommen morgen."
    assert m.echoes_terms(names, TERMS) == names
    assert m.echoes_terms(ECHO, TERMS, no_speech_prob=0.05, avg_logprob=-0.2) == ECHO   # Whisper is sure
    assert m.echoes_terms(ECHO, TERMS, no_speech_prob=0.7, avg_logprob=-0.9) == ""


def test_echoes_terms_removes_only_the_echoed_list():
    assert m.echoes_terms("Okay, dann fangen wir an. " + ECHO, TERMS) == "Okay, dann fangen wir an."


def test_transcribe_drops_the_echo_segment_and_says_so():
    e = object.__new__(m.Engine)
    e.language, e.terms = "de", TERMS
    segs = [NS(text=" Okay, dann fangen wir an.", no_speech_prob=0.05, avg_logprob=-0.3),
            NS(text=ECHO, no_speech_prob=0.6, avg_logprob=-1.1)]
    e._asr = lambda audio, language, retry=False: (segs, NS(language="de"))
    assert e.transcribe(None) == ("Okay, dann fangen wir an.", "de") and e.last_info["echo"]


def test_empty_decode_is_retried_once_without_whispers_vad():
    calls = []

    def fake_asr(audio, language, retry=False):
        calls.append(retry)
        return ([NS(text="Ja, genau.")] if retry else []), NS(language="de")

    e = object.__new__(m.Engine)
    e.language, e.terms, e._asr = "de", set(), fake_asr
    assert e.transcribe(None) == ("Ja, genau.", "de") and calls == [False, True]
    assert e.last_info == {"retried": True, "echo": False}


def test_whisper_vad_runs_at_the_lower_threshold_and_not_on_the_retry():
    seen = []

    class Asr:
        def transcribe(self, audio, **kw):
            seen.append(kw)
            return iter(()), NS(language="de")

    e = object.__new__(m.Engine)
    e.asr, e.glossary, e.context = Asr(), "", ""
    e._asr(None, "de")
    e._asr(None, "de", retry=True)
    assert seen[0]["vad_filter"] and seen[0]["vad_parameters"] == {"threshold": m.ASR_VAD_THRESHOLD}
    assert seen[1]["vad_filter"] is False
