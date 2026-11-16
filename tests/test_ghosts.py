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
