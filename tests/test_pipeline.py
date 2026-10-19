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


# ---------------------------------------------------------------- SEG-1: nothing lost while busy
class SlowWhisper:
    """Whisper on a slow CPU: every transcribe takes 2.5 s."""
    cloud, language, context = False, "de", ""

    def __init__(self):
        self.heard = []

    def transcribe(self, audio):
        time.sleep(2.5)
        self.heard.append(len(audio) / SR)
        return f"Satz mit {len(audio)} Samples", "de"

    def translate(self, text, lang):
        return "Sentence"


def test_sentence_spoken_while_whisper_is_busy_is_not_lost():
    audio = np.concatenate([speech(1), silence(2.5), speech(1), silence(4.5)])
    items, tr = run_worker(SlowWhisper(), audio, realtime=True, wait=12, finals=2)
    finals = [i for i in items if i[0] == "final"]
    assert len(finals) == 2 and len(tr.rows) == 2      # before SEG-1: 1 (the second sentence was lost)


def test_continuous_speech_loses_no_samples():
    lead = 1.0
    audio = np.concatenate([silence(lead), speech(15), silence(1)])
    seg, utts = segment(audio)
    assert len(utts) >= 2
    first = utts[0].first
    assert first == int((lead - m.PREROLL) * SR)        # starts with the 300 ms pre-roll
    for a, b in zip(utts, utts[1:]):                    # each one starts where the previous one ended ...
        assert b.first + b.overlap * SR == a.first + len(a.audio)
    fresh = sum(len(u.audio) - int(round(u.overlap * SR)) for u in utts)
    last = utts[-1]
    assert fresh == last.first + len(last.audio) - first  # ... so no sample is missing or counted twice
    assert last.first + len(last.audio) >= int((lead + 15) * SR)   # all voiced samples are in
    assert all(len(u.audio) / SR <= m.MAX_UTTERANCE + 0.11 for u in utts)


def test_soft_start_keeps_the_first_300_ms():
    soft = speech(0.3, amp=0.004)                       # below the VAD threshold
    assert energy_gap(soft) is None
    audio = np.concatenate([silence(1), soft, speech(1), silence(1)])
    _, [u] = segment(audio)
    assert u.first == SR and np.array_equal(u.audio[:len(soft)], soft)


# ---------------------------------------------------------------- SEG-3: cut at a pause
def test_sixty_second_monologue_is_never_cut_inside_speech():
    rng = np.random.default_rng(7)
    pieces, voiced = [], []
    pos = 0
    while pos < 60 * SR:
        word, gap = speech(rng.uniform(0.3, 0.9)), silence(rng.uniform(0.15, 0.4))
        voiced.append((pos, pos + len(word)))
        pieces += [word, gap]
        pos += len(word) + len(gap)
    audio = np.concatenate(pieces + [silence(1)])
    _, utts = segment(audio)
    cuts = [u.first + len(u.audio) for u in utts[:-1]]
    assert len(cuts) >= 4 and all(u.reason == "cap_pause" for u in utts[:-1])
    for c in cuts:
        assert not any(a < c < b for a, b in voiced), c  # every cut is in a pause between words
    assert all(u.overlap == 0 for u in utts)


def test_no_pause_at_the_cap_carries_an_overlap_that_is_deduplicated():
    _, utts = segment(np.concatenate([speech(13), silence(1)]))
    assert utts[0].reason == "cap_hard" and utts[1].overlap == m.OVERLAP
    assert m.dedupe_overlap("wir gehen heute nach", "heute nach Hause.") == "Hause."
    assert m.dedupe_overlap("Das ist gut.", "Morgen wieder.") == "Morgen wieder."


# ---------------------------------------------------------------- SEG-4: punctuation + pause
def test_punctuated_draft_needs_a_real_pause_to_finalize():
    seg = m.Segmenter(energy_gap)
    out = []
    for i in range(10):                                  # 6 s of words with 0.15 s comma pauses
        for c in chunks(np.concatenate([speech(0.45), silence(0.15)])):
            out += seg.feed(c)
        if i == 6:                                       # a draft says the sentence is over
            seg.mark_sentence_end(seg.uid, seg.samples)
    assert out == []                                     # comma pauses never end it
    seg.mark_sentence_end(seg.uid, seg.samples)
    for c in chunks(silence(0.3)):
        out += seg.feed(c)
    assert len(out) == 1 and out[0].reason == "sentence"   # 0.3 s after a full stop does


class Punctuating:
    """Whisper punctuates every draft, so drafts of an unfinished sentence end with '.'."""
    cloud, language, context = False, "de", ""

    def __init__(self):
        self.translated = []

    def transcribe(self, audio):
        return f"Wir haben, wie besprochen, {len(audio)} Samples verschoben.", "de"

    def translate(self, text, lang):
        self.translated.append(text)
        return "We postponed it."


def test_long_verb_final_sentence_with_commas_reaches_mt_whole():
    words = np.concatenate([np.concatenate([speech(0.45), silence(0.15)]) for _ in range(10)])
    eng = Punctuating()
    items, _ = run_worker(eng, np.concatenate([words, silence(1)]), realtime=True, wait=3, finals=1)
    finals = [i for i in items if i[0] == "final"]
    assert len(finals) == 1                               # before SEG-4: cut into 2 at ~3 s
    meta = finals[0][3]
    assert meta["end"] - meta["start"] >= 5.5


# ---------------------------------------------------------------- ui_q contract
def test_finals_carry_meta_with_audio_times_and_increasing_ids(fake_engine):
    audio = np.concatenate([silence(1), speech(1), silence(1), speech(1.5), silence(1)])
    calls = []

    class Diarizer:
        def assign(self, a, start, end):
            calls.append((len(a), start, end))
            return "S1", 0.9

    items, tr = run_worker(fake_engine(), audio, wait=3, finals=2, clock=lambda: 1000.0, diarizer=Diarizer())
    finals = [i for i in items if i[0] == "final"]
    assert [f[3]["id"] for f in finals] == [1, 2]
    t0 = 1000.0 - 0.1                                     # the first chunk ended at clock()
    (a, b) = (f[3] for f in finals)
    assert abs(a["start"] - (t0 + 1.0)) < 0.11 and abs((a["end"] - a["start"]) - 1.0) < 0.11
    assert abs(b["start"] - (t0 + 3.0)) < 0.11 and abs((b["end"] - b["start"]) - 1.5) < 0.11
    assert a["emitted"] == 1000.0 and a["lang"] == "de" and (a["speaker"], a["speaker_conf"]) == ("S1", 0.9)
    assert len(calls) == 2 and [r[4] for r in tr.rows] == [a, b]
    assert all(len(i) == 4 for i in items if i[0] in ("final", "draft"))
    assert ("draft", "", "", {}) in items
    for d in (i for i in items if i[0] == "draft" and i[1]):
        assert set(d[3]) == {"start", "end", "emitted"}


class Forgetful:
    """Drafts hear words; the final hears nothing (what a soft voice under Whisper's VAD looks like)."""
    cloud, language, context = False, "de", ""

    def transcribe(self, audio):
        return ("Wir haben" if len(audio) < 1.3 * SR else ""), "de"

    def translate(self, text, lang):
        return "We have"


def test_every_drop_is_reported_with_a_reason():
    drops = []
    audio = np.concatenate([speech(1.5), silence(1.5)])
    items, _ = run_worker(Forgetful(), audio, realtime=True, wait=1.5,
                          on_drop=lambda *a: drops.append(a))
    assert [d[0] for d in drops] == ["empty"]
    reason, start, end, text = drops[0]
    assert end > start and text == ""
    gone = [i for i in items if i[0] == "draft" and i[3].get("dropped")]
    assert gone and gone[-1][3]["dropped"] == "empty" and {"start", "end"} <= set(gone[-1][3])


def test_junk_is_dropped_with_its_reason(fake_engine):
    drops = []
    items, tr = run_worker(fake_engine(text="Untertitel im Auftrag des ZDF, 2020"),
                           np.concatenate([speech(1), silence(1)]), wait=1.5, on_drop=lambda *a: drops.append(a))
    assert [d[0] for d in drops] == ["hallucination"] and not tr.rows
    assert not [i for i in items if i[0] == "final"]


# ---------------------------------------------------------------- SEG-6 / SEG-13: filters
def test_real_short_lines_and_goodbyes_are_kept():
    for line in ("Tschüss, bis morgen alle!", "Ja.", "OK", "Nein.", "Danke.", "Danke schön.",
                 "Wir schauen heute ZDF.", "Schau mal auf example.com nach dem Termin.",
                 "Die Anmeldung läuft über www.firma.de, bitte bis Freitag.",
                 "Die Folien liegen unter https://example.com/q3, schaut sie euch an.",
                 "Bis zum nächsten Mal, Leute...", "Bis zum nächsten Mal!"):
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


# ---------------------------------------------------------------- SEG-7: term echoes
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


# ---------------------------------------------------------------- SEG-8: retry before dropping
def test_empty_decode_is_retried_once_without_whispers_vad():
    calls = []

    def fake_asr(audio, language, retry=False):
        calls.append(retry)
        return ([NS(text="Ja, genau.")] if retry else []), NS(language="de")

    e = object.__new__(m.Engine)
    e.language, e.terms, e._asr = "de", set(), fake_asr
    assert e.transcribe(None) == ("Ja, genau.", "de") and calls == [False, True]
    assert e.last_info == {"retried": True, "echo": False}


def test_a_draft_is_not_retried_without_vad():
    calls = []

    def fake_asr(audio, language, retry=False):
        calls.append(retry)
        return [], NS(language="de")

    e = object.__new__(m.Engine)
    e.language, e.terms, e._asr = "de", set(), fake_asr
    assert e.transcribe_draft(None) == ("", "de") and calls == [False]


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


def test_speech_gap_early_out_looks_only_at_the_last_300_ms():
    loud_then_quiet = np.concatenate([speech(0.7), silence(0.3)])
    assert m.speech_gap(loud_then_quiet) is None         # no VAD call needed: the end is silent
