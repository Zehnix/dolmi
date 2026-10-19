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
        return f"Satz mit {len(audio)} Samples.", "de"

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


def test_speech_ending_just_before_the_cap_leaves_no_silent_follow_up():
    for words in (11.3, 11.5, 11.6):                          # the cap lands in the pause after the words
        _, utts = segment(np.concatenate([silence(1), speech(words), silence(2)]))
        assert len(utts) == 1, words                          # before: + a 0.5 s 'pause' of silence only
        assert abs((utts[0].end - utts[0].onset) / SR - words) < 0.11, words


def test_an_utterance_without_new_speech_is_dropped_before_whisper(fake_engine):
    # 11.7 s: the hard cut lands on the last word, so only the 1 s overlap is carried (nothing new)
    _, utts = segment(np.concatenate([silence(1), speech(11.7), silence(2)]))
    assert [u.reason for u in utts] == ["cap_hard", "pause"] and utts[1].end <= utts[1].onset
    drops, eng = [], fake_engine()
    _, tr = run_worker(eng, np.concatenate([silence(1), speech(11.7), silence(2)]), wait=2, finals=1,
                       on_drop=lambda *a: drops.append(a))
    assert [d[0] for d in drops] == ["no_speech"] and len(tr.rows) == 1   # no second final, no decode


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


# ---------------------------------------------------------------- SEG-1: Stop keeps what was captured
class Slow:
    cloud, language, context = False, "de", ""

    def __init__(self, delay):
        self.delay, self.n = delay, 0

    def transcribe(self, audio):
        time.sleep(self.delay)
        self.n += 1
        return f"Satz {self.n}.", "de"

    def translate(self, text, lang):
        return text


def wait_rows(tr, n, timeout):
    deadline = time.time() + timeout
    while len(tr.rows) < n and time.time() < deadline:
        time.sleep(0.05)
    return tr.rows


def test_stop_with_a_backlog_still_writes_the_queued_finals_to_the_transcript():
    sentence = np.concatenate([speech(1), silence(1)])
    eng = Slow(0.6)
    items, tr = run_worker(eng, np.concatenate([sentence] * 4), wait=0.3)   # Stop long before Whisper is done
    assert len(wait_rows(tr, 4, 6)) == 4                       # before: 'stopped', 1-2 rows
    assert not [i for i in items if i[0] == "final" and i[3]["id"] > len(tr.rows)]


def test_stop_mid_sentence_transcribes_what_was_said():
    drops = []
    items, tr = run_worker(Slow(0.0), np.concatenate([silence(0.5), speech(2)]), wait=0.5,
                           on_drop=lambda *a: drops.append(a))
    assert len(wait_rows(tr, 1, 3)) == 1 and not drops
    assert abs(tr.rows[0][1] - tr.rows[0][0] - 2.0) < 0.21


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
    clears = [i for i in items if i[0] == "draft" and not i[1] and not i[2]]
    assert clears and all(set(c[3]) == {"session", "utt", "rev"} for c in clears)    # STR-3 contract
    for d in (i for i in items if i[0] == "draft" and i[1]):
        assert {"start", "end", "emitted", "session", "utt", "rev", "committed", "tentative"} <= set(d[3])
    assert {a["session"], b["session"]} == {clears[0][3]["session"]} and a["utt"] < b["utt"] and a["rev"] < b["rev"]


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


def test_credit_lines_and_broadcaster_ghosts_are_junk():
    for line in ("Untertitelung des ZDF für funk, 2017", "Untertitelung im Auftrag des ZDF, 2018",
                 "Untertitel von Stephanie Geiges", "Vielen Dank fürs Zuschauen und bis zum nächsten Mal!",
                 "Danke fürs Zuschauen und bis zum nächsten Mal.", "SWR 2020", "Copyright WDR 2020/2021",
                 "Thank you for watching and see you next time!", "Sous-titrage Société Radio-Canada",
                 "Sous-titres réalisés par la communauté d'Amara.org", "Abonniert den Kanal!"):
        assert m.junk_reason(line, lang="de") == "hallucination", line
    for line in ("Tschüss, bis morgen alle!", "Ja.", "OK", "Im Jahr 2020 haben wir angefangen.",
                 "Wir schauen heute ZDF.", "Bis zum nächsten Mal!"):
        assert m.junk_reason(line, lang="de") is None, line


def test_real_sentences_about_subtitles_are_kept():
    for line in ("Untertitel sind heute wieder sehr verzögert.", "Untertitel bitte auf Englisch.",
                 "Untertitelung kostet extra, sagt Anna.", "Sous-titres en anglais, s'il vous plaît.",
                 "Untertitel funktionieren jetzt.", "Untertitel sind im Meeting an, oder?",
                 "Untertitelung bitte einschalten.", "Untertitel der Präsentation fehlen noch."):
        assert m.junk_reason(line, energy=0.1, lang="de") is None, line
        assert m.junk_reason(line, energy=0.001, lang="de") is None, line
    for line in ("Untertitel der Amara.org-Community", "Untertitel: Max Muster", "Sous-titres par Jean Dupont"):
        assert m.junk_reason(line, lang="de") == "hallucination", line


def test_credit_ghosts_without_a_credit_word_are_junk():
    for line in ("Untertitelung aufgrund der Amara.org-Community", "Untertitelung durch die Amara.org-Community",
                 "Untertitelung des Bayerischen Rundfunks", "Untertitel des Bayerischen Rundfunks, 2018",
                 "Untertitel Stephanie Geiges", "Sous-titres realises par la communaute d'Amara.org",
                 "Sous-titres fait par la communauté d'Amara.org", "Untertitel. ZDF 2020"):
        assert m.junk_reason(line, energy=0.1, lang="de") == "hallucination", line
    for line in ("Untertitel vom Meeting speichern.", "Untertitel des Kunden sind falsch.",
                 "Untertitel funktionieren jetzt.", "Untertitel für alle, bitte."):
        assert m.junk_reason(line, energy=0.1, lang="de") is None, line


def test_other_scripts_are_kept_when_auto_detect_hears_them():
    for line, lang in (("שלום לכולם, נתחיל את הפגישה", "he"), ("Добар дан свима", "sr"),
                       ("สวัสดีทุกคน", "th"), ("Բարեւ բոլորին", "hy")):
        assert m.junk_reason(line, energy=0.1, lang=lang) is None, lang
    assert m.junk_reason("Продолжение следует...", lang="fr") == "non_latin"


def test_auto_detect_shows_a_hebrew_line(fake_engine):
    eng = fake_engine(text="שלום לכולם, נתחיל את הפגישה", lang="he")
    eng.language = "auto"
    items, tr = run_worker(eng, np.concatenate([speech(1), silence(1)]), wait=1.5, finals=1)
    assert [i[1] for i in items if i[0] == "final"] == ["שלום לכולם, נתחיל את הפגישה"] and tr.rows


def test_quiet_real_speech_is_not_a_silence_ghost():
    assert m.junk_reason("Vielen Dank.", energy=0.012, lang="de") is None
    assert m.junk_reason("Vielen Dank.", energy=0.004, lang="de") == "silence_ghost"


def test_dedupe_keeps_a_short_word_said_again():
    assert m.dedupe_overlap("das ist so", "so ist das") == "so ist das"
    assert m.dedupe_overlap("wir gehen nach Hause", "Hause und dann") == "und dann"   # a long word still counts


def test_overlap_is_not_deduplicated_against_a_dropped_part():
    class Eng:
        cloud, language, context = False, "de", ""
        texts = iter(["Bis heute Abend.", "Untertitel im Auftrag des ZDF", "heute Abend gehen wir."])

        def transcribe(self, audio):
            return next(self.texts), "de"

        def transcribe_draft(self, audio):                  # drafts show nothing here
            return "", "de"

        def translate(self, text, lang):
            return text

    audio = np.concatenate([speech(2), silence(1), speech(13), silence(1)])
    items, _ = run_worker(Eng(), audio, wait=3, finals=2)
    assert [i[1] for i in items if i[0] == "final"] == ["Bis heute Abend.", "heute Abend gehen wir."]


def test_mark_sentence_end_and_a_cut_do_not_interleave():
    seg = m.Segmenter(energy_gap)
    assert isinstance(seg._lock, type(threading.Lock()))
    seg.feed(speech(1))
    with seg._lock:                                         # a cut in progress holds the lock
        t = threading.Thread(target=seg.mark_sentence_end, args=(seg.uid, seg.samples))
        t.start()
        t.join(0.2)
        assert t.is_alive() and seg.sentence_end is None   # waits for the cut to finish
    t.join(1)
    assert seg.sentence_end == seg.samples


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


def test_an_echo_split_over_two_segments_is_dropped():
    e = object.__new__(m.Engine)
    e.language, e.terms = "de", TERMS
    for segs in ([NS(text="Acme, Alice, Bob, Carol,"), NS(text="Dave, Falcon, Zeta, Orion.")],
                 [NS(text="Acme, Alice, Bob,"), NS(text="Carol, Dave, Falcon, Zeta.")]):
        e._asr = lambda audio, language, retry=False, segs=segs: (segs, NS(language="de"))
        assert e.transcribe(None) == ("", "de") and e.last_info["echo"]
    sure = [NS(text="Acme, Alice, Bob,", no_speech_prob=0.05, avg_logprob=-0.2),
            NS(text="Carol, Dave, Falcon.", no_speech_prob=0.05, avg_logprob=-0.2)]
    e._asr = lambda audio, language, retry=False: (sure, NS(language="de"))
    assert e.transcribe(None)[0] == "Acme, Alice, Bob, Carol, Dave, Falcon."   # read out on purpose


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

    def fake_asr(audio, language, retry=False, **kw):
        calls.append(retry)
        return [], NS(language="de")

    e = object.__new__(m.Engine)
    e.language, e.terms, e._asr = "de", set(), fake_asr
    assert e.transcribe_draft(np.zeros(SR, np.float32)) == ("", "de") and calls == [False]


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


# ---------------------------------------------------------------- SEG-5: cheaper drafts
def test_a_draft_decodes_only_the_last_seconds_greedily():
    seen = []

    def fake_asr(audio, language, retry=False, beam=m.FINAL_BEAM):
        seen.append((len(audio) / SR, beam))
        return [NS(text="Wir sind")], NS(language="de")

    e = object.__new__(m.Engine)
    e.language, e.terms, e._asr = "de", set(), fake_asr
    e.transcribe_draft(np.zeros(11 * SR, np.float32))
    e.transcribe(np.zeros(11 * SR, np.float32))
    assert seen == [(m.DRAFT_WINDOW, m.DRAFT_BEAM), (11.0, m.FINAL_BEAM)]


class SlowDrafts:
    """Drafts take longer than the speaker needs for the next words (a busy CPU)."""
    cloud, language, context = False, "de", ""

    def __init__(self):
        self.translated = []

    def transcribe_draft(self, audio):
        time.sleep(2.3)
        return "Wir sind noch", "de"

    def transcribe(self, audio):
        return "Wir sind noch nicht fertig.", "de"

    def translate(self, text, lang):
        self.translated.append(text)
        return "We are not done yet."


def test_a_draft_far_behind_the_speaker_skips_its_translation():
    eng = SlowDrafts()
    items, _ = run_worker(eng, np.concatenate([speech(4), silence(1)]), realtime=True, wait=4, finals=1)
    drafts = [i for i in items if i[0] == "draft" and i[1]]
    assert drafts and all(d[2] == "" for d in drafts)          # German only: it would be stale anyway
    assert eng.translated == ["Wir sind noch nicht fertig."]   # only the final went to the translator


# ---------------------------------------------------------------- SEG-9 / SEG-16: context
class Scripted:
    """Finals hear the given texts in order; drafts hear nothing."""
    cloud, language, context = False, "de", ""

    def __init__(self, *texts):
        self.texts = iter(texts)

    def transcribe(self, audio):
        return next(self.texts), "de"

    def transcribe_draft(self, audio):
        return "", "de"

    def translate(self, text, lang):
        return f"EN({text})"


def finals_of(items):
    return [i for i in items if i[0] == "final"]


def test_a_final_without_full_stop_joins_the_next_one():
    audio = np.concatenate([silence(0.5), speech(1), silence(1), speech(1), silence(1)])
    eng = Scripted("Wir haben das Projekt", "verschoben, weil der Kunde abgesagt hat.")
    items, tr = run_worker(eng, audio, wait=2, finals=1, clock=lambda: 1000.0)
    [f] = finals_of(items)
    assert f[1] == "Wir haben das Projekt verschoben, weil der Kunde abgesagt hat."
    assert f[2] == "EN(Wir haben das Projekt verschoben, weil der Kunde abgesagt hat.)"   # translated whole
    t0 = 1000.0 - 0.1
    assert abs(f[3]["start"] - (t0 + 0.5)) < 0.11 and abs(f[3]["end"] - (t0 + 3.5)) < 0.11
    assert f[3]["id"] == 1 and len(tr.rows) == 1
    held = [i for i in items if i[0] == "draft" and i[1] == "Wir haben das Projekt"]
    assert held and held[0][2] == "EN(Wir haben das Projekt)"   # the held words stay on the live line


def test_a_held_final_is_shown_alone_after_the_hold_time():
    audio = np.concatenate([speech(1), silence(m.HOLD_MAX + 1.5)])
    items, tr = run_worker(Scripted("Wir haben das Projekt"), audio, wait=3, finals=1, clock=lambda: 1000.0)
    assert [f[1] for f in finals_of(items)] == ["Wir haben das Projekt"] and len(tr.rows) == 1


def test_a_final_far_after_the_held_one_is_its_own_line():
    audio = np.concatenate([speech(1), silence(m.HOLD_MAX + 1), speech(1), silence(1)])
    items, _ = run_worker(Scripted("Also gut", "Wir fangen an."), audio, wait=3, finals=2, clock=lambda: 1000.0)
    assert [f[1] for f in finals_of(items)] == ["Also gut", "Wir fangen an."]


def test_a_held_final_is_not_joined_with_another_speaker():
    class Two:
        def __init__(self):
            self.n = 0

        def assign(self, audio, start, end):
            self.n += 1
            return f"S{self.n}", 0.9

    audio = np.concatenate([speech(1), silence(1), speech(1), silence(1)])
    items, _ = run_worker(Scripted("Und du", "Ich komme mit."), audio, wait=3, finals=2, diarizer=Two())
    assert [(f[1], f[3]["speaker"]) for f in finals_of(items)] == [("Und du", "S1"), ("Ich komme mit.", "S2")]


def test_stop_flushes_a_held_final_into_the_transcript():
    _, tr = run_worker(Scripted("Wir haben das Projekt"), np.concatenate([speech(1), silence(1)]), wait=0.5,
                       clock=lambda: 1000.0)
    assert [r[2] for r in wait_rows(tr, 1, 2)] == ["Wir haben das Projekt"]


def test_a_leading_copy_of_the_previous_sentence_is_removed():
    assert m.strip_repeat("Wir starten am Montag.", "Wir starten am Montag. Und dann testen wir.") == \
        "Und dann testen wir."
    assert m.strip_repeat("Kannst du mich hören?", "Kannst du mich hören?") == "Kannst du mich hören?"  # said again
    assert m.strip_repeat("Ja, genau.", "Ja, genau. Gut.") == "Ja, genau. Gut."   # too short to tell
    audio = np.concatenate([speech(1), silence(1), speech(1), silence(1)])
    eng = Scripted("Wir starten am Montag.", "Wir starten am Montag. Und dann testen wir.")
    items, _ = run_worker(eng, audio, wait=3, finals=2)
    assert [f[1] for f in finals_of(items)] == ["Wir starten am Montag.", "Und dann testen wir."]


def test_context_translation_keeps_only_the_new_part():
    e = object.__new__(m.Engine)
    out = {"Er kommt.": "He is coming.", "Peter ist krank. Er kommt.": "Peter is sick. He's coming."}
    e.translate = lambda text, lang: out[text]
    assert e.translate_context("Er kommt.", "de", "Peter ist krank.", "Peter is sick.") == "He's coming."
    assert e.translate_context("Er kommt.", "de", "Peter ist krank.", "Peter is ill.") == "He is coming."
    assert e.translate_context("Er kommt.", "de", "", "") == "He is coming."


class Fragmenting(Scripted):
    """Opus-MT's habit: a German piece that starts with words left over from the previous one
    comes out without them. The table holds what the real model gives for each input."""

    def translate(self, text, lang):
        return EN.get(text, "")


# Monologue syn-s1-u070 of the bench (fictional company), cut by the 12 s cap at a pause mid-sentence.
MONO_A = "Kurz zum Stand bei Brandtwerk: Wir haben letzte Woche die Schnittstelle zum Nimbus Gateway"
MONO_B = "fertiggestellt, die ersten Tests auf Staging sind gut gelaufen."
EN = {MONO_A: "Briefly on the status at Brandtwerk: last week we had the interface to the Nimbus Gateway",
      MONO_B: "the first tests on staging went well.",
      f"{MONO_A} {MONO_B}": "Briefly on the status at Brandtwerk: last week we completed the interface to the "
                            "Nimbus Gateway, the first tests on staging went well."}


def test_a_monologue_cut_mid_sentence_keeps_the_carried_words_in_the_english():
    pieces = []
    for _ in range(16):                                    # ~12.8 s of words with short pauses
        pieces += [speech(0.6), silence(0.2)]
    audio = np.concatenate(pieces + [silence(1)])
    _, utts = segment(audio)
    assert [u.reason for u in utts] == ["cap_pause", "pause"]   # the cap cut lands in a pause
    items, _ = run_worker(Fragmenting(MONO_A, MONO_B), audio, wait=3, finals=1, clock=lambda: 1000.0)
    [f] = finals_of(items)                                 # before: two lines, 'fertiggestellt' lost
    assert f[1] == f"{MONO_A} {MONO_B}" and "completed" in f[2]


# ---------------------------------------------------------------- SEG-14: loops in the translation
class FakeMarian:
    """Stands in for (ctranslate2 translator, source spm, target spm); decodes to `out`."""

    def __init__(self, out):
        self.out = out

    def translate_batch(self, batch, **kw):
        return [NS(hypotheses=[["x"]])] * len(batch)

    def encode(self, text, out_type=str):
        return text.split()

    def decode(self, toks):
        return self.out


def engine_with_mt(out, language="de"):
    e = object.__new__(m.Engine)
    e.language, e.vocab = language, m.Vocabulary(text="")
    fake = FakeMarian(out)
    e._translator = lambda key, download=False: (fake, fake, fake)
    return e


def test_a_translation_loop_shows_once():
    looped = " ".join(["We will move the release to Thursday."] * 20)
    assert engine_with_mt(looped).translate("Wir verschieben das Release auf Donnerstag.", "de") == \
        "We will move the release to Thursday."
    assert engine_with_mt("Yes. Yes. Yes.").translate("Ja. Ja. Ja.", "de") == "Yes."
    assert engine_with_mt("I'm going to do that, I'm going to do that, I'm going to").translate(
        "Das mache ich, das mache ich.", "de") == "I'm going to do that"


def test_short_loops_collapse_but_a_double_word_stays():
    assert m.collapse_repeats("Ja. Ja. Ja.") == "Ja."
    assert m.collapse_repeats("Ja, ja, ja, genau.") == "Ja, genau."
    assert m.collapse_repeats("Yes. Yes. Yes. Yes. We start Monday.") == "Yes. We start Monday."
    assert m.collapse_repeats("Das ist sehr sehr gut.") == "Das ist sehr sehr gut."
    assert m.collapse_repeats("Ja, ja.") == "Ja, ja."
    assert m.collapse_repeats("Wir sind auf Version 1.1.1 und 10.10.10.") == "Wir sind auf Version 1.1.1 und 10.10.10."


# ---------------------------------------------------------------- SEG-15: German called English
def test_a_german_line_detected_as_english_is_translated():
    e = engine_with_mt("I don't think that's right.")
    assert e.translate("Ich glaube, das ist nicht richtig.", "en") == "I don't think that's right."
    assert e.translate("Okay, genau.", "en") == "I don't think that's right."     # a short German reply
    assert e.translate("Let's start with the Kanban board.", "en") == "Let's start with the Kanban board."
    assert e.translate("We need the Vertrieb und the marketing team today.", "en") == \
        "We need the Vertrieb und the marketing team today."                       # one German word: English


def test_transcribe_reports_german_when_whisper_detected_english_on_german_words():
    e = object.__new__(m.Engine)
    e.language, e.terms = "de", set()
    e._asr = lambda audio, language, **kw: ([NS(text="Ich habe das nicht gesehen.")], NS(language="en"))
    assert e.transcribe(None) == ("Ich habe das nicht gesehen.", "de")
    e._asr = lambda audio, language, **kw: ([NS(text="I have not seen it.")], NS(language="en"))
    assert e.transcribe(None) == ("I have not seen it.", "en")


# ---------------------------------------------------------------- SEG-11: cloud path
class FakeCloud:
    cloud, DRAFT_EVERY, language = True, 0.5, "de"

    def __init__(self):
        self.sent = []

    def submit(self, audio, start, end, ui_q, tr, meta=None):
        self.sent.append((len(audio) / SR, end - start, meta))

    def draft(self, audio, ui_q, meta=None):
        pass


def test_a_short_cloud_utterance_is_kept():
    drops, eng = [], FakeCloud()
    run_worker(eng, np.concatenate([silence(1), speech(0.4), silence(1.5)]), wait=0.5,
               on_drop=lambda *a: drops.append(a))
    assert len(eng.sent) == 1 and not drops                # before SEG-11: 'too_short' (0.8 s minimum)
    assert abs(eng.sent[0][1] - 0.4) < 0.11 and eng.sent[0][2]["overlap"] == 0
    run_worker(eng, np.concatenate([silence(1), speech(0.2), silence(1.5)]), wait=0.5,
               on_drop=lambda *a: drops.append(a))
    assert [d[0] for d in drops] == ["too_short"]


def test_a_cloud_cut_without_a_pause_carries_a_short_overlap():
    _, utts = segment(np.concatenate([speech(7), silence(1)]), cloud=True)
    assert utts[0].reason == "cap_hard" and utts[1].overlap == m.CLOUD_OVERLAP


def cloud_engine(provider="openai"):
    import cloud_speech
    eng = cloud_speech.CloudEngine.__new__(cloud_speech.CloudEngine)
    eng._jobs, eng._lock, eng._requests = queue.Queue(), threading.Lock(), 0
    eng._utterance = eng._shown = 0
    eng._pending, eng._drafting, eng._last_error = {}, 0, 0.0
    eng.context, eng.provider, eng.language = "", provider, "de"
    eng.line_ids = iter(range(1, 100))
    eng.translate = lambda text, lang: f"EN({text})"
    return eng


def submit_now(eng, audio, ui_q, tr, meta=None, start=0.0, end=1.0):
    """CloudEngine.submit, run on this thread (no request threads in tests)."""
    eng.submit(audio, start, end, ui_q, tr, meta)
    fn, args = eng._jobs.get_nowait()
    fn(*args)


def test_a_failed_cloud_request_is_retried_once(monkeypatch):
    import cloud_speech
    monkeypatch.setattr(cloud_speech, "RETRY_DELAY", 0.0)
    eng, calls, drops = cloud_engine(), [], []
    eng.on_drop = lambda *a: drops.append(a)

    def flaky(audio):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("connection reset")
        return "Ja, genau.", "Yes, exactly."

    eng.recognize = flaky
    ui_q, tr = queue.Queue(), Collect()
    submit_now(eng, speech(0.4), ui_q, tr, meta={"speaker": "S1", "speaker_conf": 0.8, "overlap": 0.0})
    items = list(ui_q.queue)
    assert len(calls) == 2 and not drops and not [i for i in items if i[0] == "error"]
    [f] = [i for i in items if i[0] == "final"]
    assert f[1:3] == ("Ja, genau.", "Yes, exactly.") and len(tr.rows) == 1
    assert {k: f[3][k] for k in ("id", "start", "end", "speaker", "speaker_conf", "lang")} == \
        {"id": 1, "start": 0.0, "end": 1.0, "speaker": "S1", "speaker_conf": 0.8, "lang": "de"}
    assert "overlap" not in f[3]


def test_a_cloud_request_failing_twice_is_dropped_and_reported(monkeypatch):
    import cloud_speech
    monkeypatch.setattr(cloud_speech, "RETRY_DELAY", 0.0)
    eng, calls, drops = cloud_engine(), [], []
    eng.on_drop = lambda *a: drops.append(a)

    def down(audio):
        calls.append(1)
        raise RuntimeError("HTTP 503: busy")

    eng.recognize = down
    ui_q, tr = queue.Queue(), Collect()
    submit_now(eng, speech(1), ui_q, tr, start=5.0, end=6.0)
    assert len(calls) == 2 and [d[:3] for d in drops] == [("error", 5.0, 6.0)] and not tr.rows
    assert [i[0] for i in ui_q.queue] == ["error", "draft"]


def test_cloud_overlap_words_are_not_shown_twice():
    eng = cloud_engine()
    texts = iter([("Wir gehen heute zusammen", ""), ("zusammen nach Hause.", "")])
    eng.recognize = lambda audio: next(texts)
    ui_q, tr = queue.Queue(), Collect()
    submit_now(eng, speech(1), ui_q, tr, meta={"overlap": 0.0})
    submit_now(eng, speech(1), ui_q, tr, meta={"overlap": m.CLOUD_OVERLAP})
    assert [r[2:4] for r in tr.rows] == [("Wir gehen heute zusammen", ""), ("nach Hause.", "EN(nach Hause.)")]
    gem = cloud_engine("gemini")
    texts = iter([("Wir gehen heute zusammen", "Today we go together"), ("zusammen nach Hause.", "together home.")])
    gem.recognize = lambda audio: next(texts)
    tr = Collect()
    submit_now(gem, speech(1), ui_q, tr, meta={"overlap": 0.0})
    submit_now(gem, speech(1), ui_q, tr, meta={"overlap": m.CLOUD_OVERLAP})
    assert tr.rows[1][2:4] == ("nach Hause.", "home.")
