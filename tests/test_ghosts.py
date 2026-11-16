"""Ghost filter 2.0: thank-you ghosts on near-silent audio, third-language one-liners, translator markup and
invented text, garbled multi-script decodes. Examples are shaped after the 2026-11 meeting reviews, with
neutral text."""
import queue
import threading
from types import SimpleNamespace as NS

import numpy as np

import live_subs as m

SR = m.SR


def loud(sec):
    return (0.1 * np.sin(np.arange(int(sec * SR)) / 5)).astype(np.float32)


def quiet(sec, level=0.002):
    return (level * np.sin(np.arange(int(sec * SR)) / 5)).astype(np.float32)


def W(word, start, end):
    return NS(word=word, start=start, end=end)


# ---------------------------------------------------------------- "Vielen Dank" / "Thank you" on silence
def test_a_thank_you_segment_on_near_silent_audio_goes_and_the_real_line_stays():
    audio = np.concatenate([loud(2), quiet(1.5)])
    segs = [NS(text=" Das Update kommt nächste Woche.", start=0.0, end=2.0, words=None),
            NS(text=" Vielen Dank.", start=2.2, end=3.2, words=None)]
    kept, removed = m.strip_quiet_ghosts(segs, audio)
    assert [s.text for s in kept] == [" Das Update kommt nächste Woche."] and removed == 1


def test_a_thank_you_that_was_said_aloud_stays():
    audio = loud(3.5)
    segs = [NS(text=" Das Update kommt nächste Woche.", start=0.0, end=2.0, words=None),
            NS(text=" Vielen Dank.", start=2.2, end=3.2, words=None)]
    kept, removed = m.strip_quiet_ghosts(segs, audio)
    assert len(kept) == 2 and removed == 0


def test_without_word_times_a_thank_you_inside_a_loud_segment_is_left_alone():
    audio = np.concatenate([loud(2), quiet(1), loud(2)])
    segs = [NS(text=" Das Update kommt. Vielen Dank. Dann testen wir.", start=0.0, end=5.0, words=None)]
    kept, removed = m.strip_quiet_ghosts(segs, audio)
    assert kept == segs and removed == 0      # the segment as a whole is loud: no evidence about the phrase


def test_with_word_times_a_thank_you_inserted_mid_line_on_silence_goes():
    # 11-13: "Vielen Dank" appended in the middle of real lines (6x in one meeting)
    audio = np.concatenate([loud(2), quiet(1), loud(2)])
    words = [W(" Das", 0.0, 0.4), W(" Update", 0.4, 1.0), W(" kommt.", 1.0, 1.9),
             W(" Vielen", 2.1, 2.5), W(" Dank.", 2.5, 2.9),
             W(" Dann", 3.0, 3.4), W(" testen", 3.4, 4.0), W(" wir.", 4.0, 4.8)]
    segs = [NS(text=" Das Update kommt. Vielen Dank. Dann testen wir.", start=0.0, end=4.8, words=words)]
    [seg], removed = m.strip_quiet_ghosts(segs, audio)
    assert seg.text == "Das Update kommt. Dann testen wir." and removed == 1
    assert [w.word for w in seg.words] == [" Das", " Update", " kommt.", " Dann", " testen", " wir."]


def test_with_word_times_an_english_thank_you_on_silence_goes_and_a_spoken_danke_stays():
    audio = np.concatenate([loud(2), quiet(1)])
    words = [W(" Okay,", 0.0, 0.5), W(" danke.", 0.6, 1.2), W(" Thank", 2.2, 2.5), W(" you.", 2.5, 2.9)]
    [seg], _ = m.strip_quiet_ghosts([NS(text=" Okay, danke. Thank you.", start=0, end=2.9, words=words)], audio)
    assert seg.text == "Okay, danke."


def test_a_lone_ghost_phrase_with_no_words_left_drops_the_segment():
    audio = np.concatenate([loud(1), quiet(2)])
    words = [W(" Danke.", 1.5, 2.0)]
    kept, removed = m.strip_quiet_ghosts([NS(text=" Danke.", start=1.5, end=2.0, words=words)], audio)
    assert kept == [] and removed == 1


def test_the_engine_drops_a_quiet_thank_you_segment_from_the_final():
    e = object.__new__(m.Engine)
    audio = np.concatenate([loud(2), quiet(1.5)])
    segs = [NS(text=" Wir starten am Montag.", start=0.0, end=2.0), NS(text=" Thank you.", start=2.3, end=3.2)]
    e.language, e.terms, e._asr = "de", set(), lambda audio, language, retry=False, **kw: (segs, NS(language="de"))
    assert e.transcribe(audio) == ("Wir starten am Montag.", "de") and e.last_info["ghosts"] == 1


# ---------------------------------------------------------------- third-language one-liners
def test_short_third_language_lines_whisper_called_english_are_ghosts():
    # 11-16: "Goed.", "Vamos los ojos." shown as English-source lines in a German meeting
    assert m.third_language_reason("Goed.", "en", 0.41, "de") == "third_language"
    assert m.third_language_reason("Vamos los ojos.", "en", 0.38, "de") == "third_language"


def test_real_short_english_or_german_replies_stay():
    for text in ("Okay.", "Sounds good.", "Thank you.", "Yes, exactly.", "Perfekt.", "Genau so.", "I'm fine."):
        assert m.third_language_reason(text, "en", 0.4, "de") is None, text


def test_a_third_language_line_stays_when_whisper_is_sure_or_the_line_is_long_or_a_known_term():
    assert m.third_language_reason("Goed.", "en", 0.93, "de") is None
    assert m.third_language_reason("Vamos a revisar el informe de ventas hoy", "en", 0.4, "de") is None
    assert m.third_language_reason("Kranich.", "en", 0.4, "de", terms={"kranich"}) is None


def test_only_lines_called_english_in_a_meeting_of_another_language_are_checked():
    assert m.third_language_reason("Goed.", "de", 0.4, "de") is None   # forced German: no detection to trust
    assert m.third_language_reason("Goed.", "en", 0.4, "auto") is None  # Auto-detect: every language is fair
    assert m.third_language_reason("Goed.", "en", 0.4, "en") is None
    assert m.third_language_reason("Goed.", "en", None, "de") is None    # no language probability: no evidence
    assert m.third_language_reason("um", "en", 0.3, "de") is None        # a hesitation joins the next line


class OneLine:
    """Whisper hears one short English-called line with a low language probability."""
    cloud, language, context = False, "de", ""

    def __init__(self, text, prob):
        self.text, self.prob = text, prob

    def transcribe(self, audio):
        self.last_info = {"language_probability": self.prob}
        return self.text, "en"

    def transcribe_draft(self, audio):
        return "", "de"

    def translate(self, text, lang):
        return text


def run(eng, audio):
    aq, uq, stop, drops = queue.Queue(), queue.Queue(), threading.Event(), []
    gap = lambda a: None if np.abs(a[-160:]).max() < 0.01 else 0.0
    th = threading.Thread(target=m.worker, args=(eng, aq, uq, NS(add=lambda *a, **k: None), stop, gap),
                          kwargs={"on_drop": lambda reason, *a: drops.append(reason)}, daemon=True)
    th.start()
    for i in range(0, len(audio), m.STEP):
        aq.put(audio[i:i + m.STEP])
    items = []
    for _ in range(40):
        try:
            items.append(uq.get(timeout=0.05))
        except queue.Empty:
            pass
    stop.set()
    th.join(2)
    return [i for i in items if i[0] == "final"], drops


def test_the_pipeline_drops_a_third_language_ghost_and_keeps_a_sure_one():
    audio = np.concatenate([loud(1), np.zeros(int(1.5 * SR), np.float32)])
    finals, drops = run(OneLine("Goed.", 0.4), audio)
    assert finals == [] and drops == ["third_language"]
    finals, drops = run(OneLine("Okay.", 0.4), audio)
    assert [f[1] for f in finals] == ["Okay."] and drops == []


# ---------------------------------------------------------------- translator markup and invented text
def test_markup_and_invented_text_are_suspect():
    assert m.mt_suspect("Jahrhundert.", "==References==") == "markup"         # 11-16 10:15:14
    assert m.mt_suspect("Siehe Liste", "[[List of things]]") == "markup"
    assert m.mt_suspect("und wo", "This is the case in the United Kingdom, where the") == "too_long"   # 11-06
    assert m.mt_suspect("Das ist ihm egal.", "He doesn't care.") is None
    assert m.mt_suspect("Die Variable {{name}} ist leer.", "The variable {{name}} is empty.") is None


class FakeMarian:
    """CTranslate2 stand-in: answers from a list, one per call, and records the call options."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def translate_batch(self, batch, **kw):
        self.calls.append(kw)
        return [NS(hypotheses=[self.answers.pop(0).split()])]


class Spm:
    def encode(self, text, out_type=str):
        return text.split()

    def decode(self, tokens):
        return " ".join(tokens)


def mt_engine(*answers):
    e = object.__new__(m.Engine)
    e.language, e.vocab, mt = "de", m.Vocabulary(text=""), FakeMarian(*answers)
    e.translators = {"opus-de": (mt, Spm(), Spm())}
    return e, mt


def test_a_markup_translation_is_retried_shorter_and_the_retry_is_shown():
    e, mt = mt_engine("==References==", "Century.")
    assert e.translate("Jahrhundert.", "de") == "Century."
    assert len(mt.calls) == 2 and mt.calls[1]["max_decoding_length"] < mt.calls[0]["max_decoding_length"]


def test_when_the_retry_is_still_bad_the_line_shows_german_only():
    e, _ = mt_engine("==References==", "{{cite web}}")
    assert e.translate("Jahrhundert.", "de") == ""


def test_a_good_translation_needs_no_retry():
    e, mt = mt_engine("He doesn't care.")
    assert e.translate("Das ist ihm egal.", "de") == "He doesn't care." and len(mt.calls) == 1
