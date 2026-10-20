"""VOICE-9: clause cuts, prefix translation, no re-speaking, timing records, the threaded runner, settings.
A fake translator (word-by-word dictionary, honours the prefix) stands in for Opus-MT."""
import threading
import time

import config
from ui import app as app_mod
from voice import clauses, timing
from voice.clauses import ClauseSpeaker, clause_cuts

WORDS = {"ich": "I", "habe": "have", "den": "the", "bericht": "report", "geschickt": "sent", "aber": "but",
         "die": "the", "zahlen": "numbers", "fehlen": "are", "noch": "missing", "wir": "we",
         "kommen": "come", "weil": "because", "es": "it", "regnet": "rains", "morgen": "tomorrow"}


def fake_translate(src, prefix):
    """English for src that starts with `prefix` (as CTranslate2 target_prefix does)."""
    out = [WORDS.get(clauses._bare(w), w) + (w[-1] if w[-1] in ",." else "") for w in src.split()]
    head = prefix.split()
    return " ".join(head + out[len(head):]) if head else " ".join(out)


def cut_texts(text, **kw):
    words, start, out = text.split(), 0, []
    for c in clause_cuts(words, **kw):
        out.append(" ".join(words[start:c]))
        start = c
    return out


# ------------------------------------------------------------------ clause cuts
def test_cuts_at_comma_before_conjunction_only_when_next_word_known():
    assert cut_texts("Ich habe den Bericht geschickt, aber") == ["Ich habe den Bericht geschickt,"]
    assert cut_texts("Ich habe den Bericht geschickt,") == []          # the comma may still change


def test_short_comma_parts_are_a_list_not_a_clause():
    assert cut_texts("Äpfel, Birnen, Bananen und Kiwis") == []


def test_sentence_end_and_final_flush():
    assert cut_texts("Wir kommen. Es regnet") == ["Wir kommen."]
    assert cut_texts("Wir kommen. Es regnet", final=True) == ["Wir kommen.", "Es regnet"]


def test_cut_before_aber_without_comma():
    assert cut_texts("Ich habe den Bericht aber die Zahlen") == ["Ich habe den Bericht"]


def test_pause_flushes_a_main_clause_but_not_an_open_subclause():
    assert cut_texts("Wir kommen morgen", pause=True) == ["Wir kommen morgen"]
    assert cut_texts("Wir kommen, weil es", pause=True) == []               # verb of 'weil ...' still to come
    assert cut_texts("Wir kommen morgen, weil es", pause=True) == ["Wir kommen morgen,"]


# ------------------------------------------------------------------ speaker
def make_speaker():
    said = []
    return ClauseSpeaker(fake_translate, lambda en, end: said.append(en)), said


def test_clauses_are_spoken_as_they_commit_and_final_adds_only_the_rest():
    sp, said = make_speaker()
    text = "Ich habe den Bericht geschickt, aber die Zahlen fehlen noch."
    words = text.split()
    for i in range(1, len(words) + 1):
        sp.draft(7, " ".join(words[:i]))
    assert said == ["I have the report sent,"]
    sp.final(7, text, "I sent the report, but the numbers are still missing.")
    assert said == ["I have the report sent,", "but the numbers are missing."]


def test_final_without_clauses_speaks_the_whole_line():
    sp, said = make_speaker()
    sp.draft(1, "Wir kommen")
    sp.final(1, "Wir kommen.", "We are coming.")
    assert said == ["We are coming."]


def test_withdrawn_committed_words_are_not_spoken_again():
    sp, said = make_speaker()
    sp.draft(2, "Ich habe den Bericht geschickt, aber")
    sp.draft(2, "Ich habe den Bericht")                     # STR-1 withdrew words (known bug)
    sp.draft(2, "Ich habe den Bericht geschickt, aber die")
    assert said == ["I have the report sent,"]
    sp.final(2, "Ich habe den Bericht geschickt, aber die Zahlen.", "")
    assert said[-1] == "but the numbers."


def test_final_with_nothing_new_says_nothing_more():
    sp, said = make_speaker()
    sp.draft(3, "Wir kommen. Es")
    sp.final(3, "Wir kommen.", "We are coming.")
    assert said == ["we come."]


def test_engine_ignoring_the_prefix_does_not_repeat_spoken_words():
    said = []
    sp = ClauseSpeaker(lambda src, pre: "totally different words here now", lambda en, end: said.append(en))
    sp.draft(4, "Wir kommen. Es regnet. Morgen")
    sp.final(4, "Wir kommen. Es regnet. Morgen auch.", "")
    assert said[0] == "totally different words here now"
    assert said[1:] == ["now"] or said[1:] == []


def test_translation_error_is_logged_not_raised(capsys):
    def boom(src, pre):
        raise RuntimeError("mt down")
    sp = ClauseSpeaker(boom, lambda en, end: None)
    sp.draft(5, "Wir kommen. Es")
    assert "clause translation failed" in capsys.readouterr().out


# ------------------------------------------------------------------ threaded runner
class FakeReader:
    def __init__(self):
        self.said, self.event = [], threading.Event()

    def offer(self, text, end=None):
        self.said.append(text)
        self.event.set()


def test_clause_voice_thread_speaks_and_pause_flushes():
    r = FakeReader()
    cv = clauses.ClauseVoice(fake_translate, r, pause=0.1)
    try:
        cv.draft(9, "Wir kommen morgen")
        assert r.event.wait(3)
        assert r.said == ["we come tomorrow"]
        cv.final(9, "Wir kommen morgen.", "We come tomorrow.")
        time.sleep(0.3)
        assert r.said == ["we come tomorrow", "."] or r.said == ["we come tomorrow"]
    finally:
        cv.stop()


# ------------------------------------------------------------------ timing
def test_run_clauses_records_per_stage_timing():
    class TTS:
        def first_audio(self, text):
            return 0.2, 0.5
    ticks = iter(i * 0.1 for i in range(10000))
    recs = timing.run_clauses(["Ich habe den Bericht geschickt, aber die Zahlen fehlen noch."],
                              fake_translate, TTS(), perf=lambda: next(ticks))
    assert [r["kind"] for r in recs] == ["draft", "final"]
    d = recs[0]["durations"]
    assert set(d) == {"commit", "mt", "tts_first", "playback"}
    assert abs(recs[0]["total"] - (timing.COMMIT_LAG + d["mt"] + 0.2 + timing.PLAYBACK_BUFFER)) < 1e-3
    s = timing.summary(recs, timing.CLAUSE_STAGES)
    assert s["commit"]["simulated"] and not s["mt"]["simulated"]


# ------------------------------------------------------------------ settings
def test_voice_clauses_setting_is_off_by_default_and_a_switch():
    assert config.DEFAULTS["voice_clauses"] is False
    assert "voice_clauses" in app_mod.BOOL_SETTINGS
