"""Test bench (eval/): metrics on hand-computed examples, the manifest schema, and stream mode with a
fake engine on a simulated clock."""
import json
import queue
import re
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "eval"
for _p in (str(EVAL), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import metrics  # noqa: E402
import run_eval  # noqa: E402
import make_synthetic  # noqa: E402

SR = 16000


# ------------------------------------------------------------------ normalizer
@pytest.mark.parametrize("text, expected", [
    ("Am 3. März um 14 Uhr.", "am dritte märz um vierzehn uhr"),
    ("Das kostet 1.500 €!", "das kostet tausendfünfhundert euro"),
    ("Version 4.2 ist auf Staging.", "version vier punkt zwei ist auf staging"),
    ("Die Fehlerquote liegt bei 2,5 Prozent.", "die fehlerquote liegt bei zwei komma fünf prozent"),
    ("Wir haben 3. Dann geht es los.", "wir haben drei dann geht es los"),   # sentence end, not an ordinal
    ("bis zum dritten Mai", "bis zum dritte mai"),                           # inflected ordinal folded
    ("Das API-Schlüssel-Thema ist größer.", "das api schlüssel thema ist grösser"),
    ("Bis Ende 2026", "bis ende zweitausendsechsundzwanzig"),
])
def test_normalize_german(text, expected):
    assert metrics.normalize(text, "de") == expected


def test_normalize_english():
    assert metrics.normalize("We made 48,000 euros, up 21%.", "en") == \
        "we made forty eight thousand euros up twenty one percent"


@pytest.mark.parametrize("n, words", [
    (0, "null"), (1, "eins"), (16, "sechzehn"), (21, "einundzwanzig"), (30, "dreißig"), (101, "hunderteins"),
    (1000, "tausend"), (2026, "zweitausendsechsundzwanzig"), (31000, "einunddreißigtausend"),
    (1_500_000, "eine million fünfhunderttausend"),
])
def test_german_number(n, words):
    assert metrics.german_number(n) == words


# ------------------------------------------------------------------ WER / CER
def test_wer_one_substitution():
    assert metrics.wer("das ist ein Test", "Das ist Test Test.") == pytest.approx(25.0)


def test_wer_deletion_and_insertions():
    # a b c -> a c d e takes 3 edits (e.g. delete b, insert d and e) over 3 words
    assert metrics.wer("a b c", "a c d e", "en") == pytest.approx(100.0)
    c = metrics.error_counts(["a b c"], ["a c d e"], "en")
    assert (c["edits"], c["ref_len"]) == (3, 3) and c["subs"] + c["dels"] + c["ins"] == 3
    assert metrics.error_counts(["a b c d"], ["a d"], "en")["dels"] == 2


def test_wer_is_pooled_over_the_corpus():
    # 1 error in 1 word + 0 errors in 3 words = 1/4, not the mean of 100 % and 0 %
    assert metrics.wer(["Ja.", "das ist gut"], ["Nein.", "Das ist gut."]) == pytest.approx(25.0)


def test_cer():
    assert metrics.cer("abc", "abd") == pytest.approx(100 / 3)
    assert metrics.cer("", "") == 0.0


def test_numbers_as_digits_or_words_score_the_same():
    assert metrics.wer("Wir haben 23 offene Tickets.", "wir haben dreiundzwanzig offene Tickets") == 0.0


# ------------------------------------------------------------------ chrF / BLEU
def test_chrf_hand_computed():
    # abc vs abd: 1-grams 2/3, 2-grams 1/2, 3-grams 0/1 (higher orders absent) -> P = R = 7/18
    assert metrics.chrf("abc", "abd") == pytest.approx(100 * 7 / 18)
    assert metrics.chrf("Das Haus ist klein.", "Das Haus ist klein.") == pytest.approx(100.0)


def test_chrf_matches_sacrebleu_value():
    assert metrics.chrf(["Das Haus ist groß."], ["Das Haus ist klein."]) == pytest.approx(59.38820572402751)


def test_bleu_hand_computed():
    # the cat sat on a mat vs the cat sat on the mat: p1..p4 = 5/6, 3/5, 2/4, 1/3, no brevity penalty
    expected = 100 * (5 / 6 * 3 / 5 * 2 / 4 * 1 / 3) ** 0.25
    assert metrics.bleu("the cat sat on the mat", "the cat sat on a mat") == pytest.approx(expected)


def test_bleu_corpus_with_brevity_penalty_matches_sacrebleu():
    refs = ["Wir sehen uns Montag.", "Das ist sehr gut."]
    hyps = ["Wir sehen uns am Montag.", "Das ist gut."]
    assert metrics.bleu(refs, hyps) == pytest.approx(32.9018503238123)


def test_bleu_no_match_is_zero():
    assert metrics.bleu("a b c d", "w x y z") == 0.0
    assert metrics.sentence_bleu("a b c d", "") == 0.0


# ------------------------------------------------------------------ terms, alignment, percentiles
def test_term_accuracy():
    items = [(["Nimbus Gateway", "Velorix"], "Das Nimbus-Gateway von Velorix."), (["Lumora"], "Lumen")]
    assert metrics.term_accuracy(items) == (2, 3, pytest.approx(200 / 3))


def test_align():
    assert metrics.align(list("abc"), list("axc")) == [(0, 0), (1, 1), (2, 2)]
    assert metrics.align(list("abc"), list("ac")) == [(0, 0), (1, None), (2, 1)]
    assert metrics.align([], list("x")) == [(None, 0)]


def test_percentile():
    assert metrics.percentile([1, 2, 3, 4], 50) == pytest.approx(2.5)
    assert metrics.percentile([], 50) is None


# ------------------------------------------------------------------ test data
def test_every_template_fills_completely():
    import random
    rng = random.Random(1)
    for cat, text, en in make_synthetic.TEMPLATES:
        lang = "en" if cat in ("E", "ME") else "de"
        de, out = make_synthetic.fill(rng, text, en or text, lang)
        assert "{" not in de and "{" not in out, text


def test_monologues_are_long():
    # > 12 s at the voices' ~14 characters per second
    assert all(len(t) / 14 > 12 for c, t, _ in make_synthetic.TEMPLATES if c in ("M", "ME"))


def test_manifest_schema():
    path = EVAL / "manifest.json"
    if not path.exists():
        pytest.skip("eval/manifest.json not built yet")
    man = json.loads(path.read_text(encoding="utf-8"))
    assert man["version"] == 1
    assert all({"de", "en", "kind"} <= set(t) for t in man["terms"])
    syn = man["sets"]["synthetic"]
    total = 0.0
    for s in syn["sessions"]:
        assert s["file"].startswith("data/") and s["file"].endswith(".wav")   # git-ignored folder
        assert re.fullmatch(r"[0-9a-f]{64}", s["sha256"])
        assert s["sr"] == SR
        total += s["duration"]
        prev_end = 0.0
        for u in s["utterances"]:
            assert {"id", "start", "end", "text", "en", "lang", "speaker", "terms", "tags"} <= set(u)
            assert "words" not in u                     # word timings stay next to the audio
            assert prev_end <= u["start"] < u["end"] <= s["duration"]
            assert u["lang"] in ("de", "en") and u["text"].strip()
            prev_end = u["end"]
        gaps = [b["start"] - a["end"] for a, b in zip(s["utterances"], s["utterances"][1:])]
        assert min(gaps) >= 0.19 and max(gaps) <= 3.01
    assert 30 * 60 <= total <= 60 * 60
    if "fleurs" in man["sets"]:
        assert all(re.fullmatch(r"[0-9a-f]{64}", f["sha256"]) for f in man["sets"]["fleurs"]["files"])


def test_eval_outputs_are_git_ignored():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "eval/data/" in ignored and "eval/results/" in ignored


# ------------------------------------------------------------------ stream mode
TONES = {300: "Erster Satz hier.", 500: "Zweiter Satz da.", 700: "Dritter Satz dort."}


class ToneEngine:
    """Fake engine: 'hears' the strongest tone in the buffer and returns its sentence."""
    cloud = False

    def __init__(self):
        self.context, self.device, self.calls = "", "cpu", 0

    def transcribe(self, audio):
        self.calls += 1
        spec = np.abs(np.fft.rfft(audio))
        freq = np.argmax(spec) * SR / len(audio)
        return TONES[min(TONES, key=lambda f: abs(f - freq))], "de"

    def translate(self, text, lang):
        return "EN " + text


def tone_session():
    spans = [(1.0, 2.5, 300), (3.5, 5.0, 500), (6.0, 8.0, 700)]
    audio = np.zeros(10 * SR, np.float32)
    utts = []
    for k, (a, b, f) in enumerate(spans):
        t = np.arange(int((b - a) * SR)) / SR
        audio[int(a * SR):int(a * SR) + len(t)] = 0.1 * np.sin(2 * np.pi * f * t)
        words = TONES[f].rstrip(".").split()
        step = (b - a) / len(words)
        utts.append({"id": f"u{k}", "start": a, "end": b, "text": TONES[f], "lang": "de", "tags": ["S"],
                     "words": [[w, a + i * step, a + (i + 1) * step] for i, w in enumerate(words)]})
    return {"id": "tones", "audio": audio, "utterances": utts, "duration": 10.0}


def loud(audio):
    return 0.0 if np.sqrt(np.mean(audio[-1600:] ** 2)) > 0.006 else None


def test_stream_mode_with_fake_engine_is_fast_and_finds_every_line():
    t = time.perf_counter()
    summary, rows = run_eval.run_stream(ToneEngine(), tone_session(), detect=loud)
    assert time.perf_counter() - t < 30
    assert summary["finals"] == 3 and summary["missed_rate"] == 0.0
    assert summary["word_recall"] == pytest.approx(100.0)
    assert summary["clipped_first_word_rate"] == 0.0 and summary["mid_word_cut_rate"] == 0.0
    # finals come after the 0.6 s pause that ends a sentence, on the simulated clock
    assert 0.5 <= summary["final_latency_p50"] <= 1.0
    assert summary["clock"] in ("injected", "patched")
    assert [r["missed"] for r in rows] == [False, False, False]


def test_stream_mode_counts_a_dropped_line_as_missed():
    class DropsTheSecond(ToneEngine):
        def transcribe(self, audio):
            text, lang = super().transcribe(audio)
            return ("" if text.startswith("Zweiter") else text), lang
    summary, rows = run_eval.run_stream(DropsTheSecond(), tone_session(), detect=loud)
    assert [r["missed"] for r in rows] == [False, True, False]
    assert summary["missed_rate"] == pytest.approx(100 / 3)


def test_stream_mode_uses_an_injected_clock_and_on_drop_when_the_worker_has_them():
    seen = {}

    def worker(eng, audio_q, ui_q, tr, stop, detect=None, clock=None, on_drop=None):
        seen["clock"] = clock
        dropped = False
        while not stop.is_set():
            try:
                audio_q.get(timeout=0.2)
            except Exception:
                continue
            now = clock()
            if 2.9 < now < 3.05:   # pretend the first line was finalized here
                ui_q.put(("final", TONES[300], "EN", {"id": 1, "start": 1.0, "end": 2.5, "emitted": now}))
            if now > 5.5 and not dropped:
                on_drop("junk", text="Zweiter Satz da.")
                dropped = True
    summary, rows = run_eval.run_stream(ToneEngine(), tone_session(), worker=worker)
    assert seen["clock"] is not None and summary["clock"] == "injected"
    assert summary["on_drop"] and summary["drops"] == 1
    assert [r["missed"] for r in rows] == [False, True, True]


def threaded_worker(asr_seconds):
    """A worker with the wave-1 shape (live_subs on exon/integration): this thread only segments, in
    audio time; a 'dolmi-asr' thread transcribes finished sentences from a job queue and sends what
    it finishes after Stop to nobody. Finals carry contract times (stream start + sample / SR)."""
    class Discard:
        @staticmethod
        def put(item):
            pass

    def worker(eng, audio_q, ui_q, tr, stop, detect=None, clock=time.time, on_drop=None, diarizer=None):
        jobs = queue.Queue()

        def asr_loop(ui_q):
            n = 0
            while True:
                job = jobs.get()
                if job is None:
                    return
                if stop.is_set():
                    ui_q = Discard
                audio, start, end = job
                time.sleep(asr_seconds)                     # Whisper at work
                de, lang = eng.transcribe(audio)
                n += 1
                meta = {"id": n, "start": start, "end": end, "emitted": clock()}
                ui_q.put(("final", de, eng.translate(de, lang), meta))
                tr.add(start, end, de, "", meta=meta)
        threading.Thread(target=asr_loop, name="dolmi-asr", daemon=True, args=(ui_q,)).start()
        t0, fed, parts, onset, last_voice = None, 0, [], None, None
        try:
            while not stop.is_set():
                try:
                    chunk = audio_q.get(timeout=0.2)
                except queue.Empty:
                    continue
                if t0 is None:
                    t0 = clock() - len(chunk) / SR
                fed += len(chunk)
                if detect(chunk) is not None:
                    if onset is None:
                        onset, parts = fed - len(chunk), []
                    last_voice = fed
                if onset is not None:
                    parts.append(chunk)
                    if (fed - last_voice) / SR >= 0.6:
                        jobs.put((np.concatenate(parts), t0 + onset / SR, t0 + last_voice / SR))
                        onset = None
        finally:
            jobs.put(None)
    return worker


def test_stream_mode_waits_for_a_threaded_asr_and_counts_its_compute():
    summary, rows = run_eval.run_stream(ToneEngine(), tone_session(), worker=threaded_worker(0.4), detect=loud)
    assert summary["finals"] == 3 and summary["missed_rate"] == 0.0
    assert [r["missed"] for r in rows] == [False, False, False]
    assert summary["word_recall"] == pytest.approx(100.0)
    # 0.6 s pause + 0.4 s of ASR: the audio clock did not run ahead while the ASR thread worked
    assert 0.95 <= summary["final_latency_p50"] <= 1.4


def test_stream_mode_keeps_the_backlog_of_a_slow_asr_to_the_end():
    # 3.5 s per sentence, sentences ~2.5 s apart: a backlog builds and the last final is ready only
    # ~1.6 s after the audio ends. It must still reach ui_q (stop only once the job queue is empty).
    summary, rows = run_eval.run_stream(ToneEngine(), tone_session(), worker=threaded_worker(3.5), detect=loud)
    assert summary["finals"] == 3 and summary["missed_rate"] == 0.0
    assert summary["final_latency_p95"] >= 4.5


def test_asr_watch_sees_a_thread_waiting_for_jobs_as_idle():
    watch = run_eval.AsrWatch()
    jobs, done = queue.Queue(), threading.Event()

    def asr():
        while True:
            job = jobs.get()
            if job is None:
                return
            done.wait(5)
    th = threading.Thread(target=asr, name="dolmi-asr", daemon=True)
    th.start()
    time.sleep(0.1)
    assert watch.idle()
    jobs.put(1)
    time.sleep(0.1)
    assert not watch.idle()                 # working on the job
    done.set()
    time.sleep(0.1)
    assert watch.idle()
    jobs.put(None)
    watch.join(5)
    assert not th.is_alive() and watch.idle()


def test_sim_audio_clock_skips_waiting_but_counts_compute():
    stop = threading.Event()
    sim = run_eval.SimAudio(np.zeros(SR, np.float32), stop)
    assert len(sim.get()) == 1600 and sim.clock() == pytest.approx(0.1, abs=0.01)
    assert sim.empty()                       # the next chunk has not "arrived" yet
    time.sleep(0.25)                         # compute time: the clock moves on, audio piles up
    assert not sim.empty() and len(sim.get_nowait()) == 1600
    for _ in range(20):
        try:
            sim.get()
        except Exception:
            break
    assert stop.is_set()


def test_parse_item_handles_old_and_new_formats():
    assert run_eval.parse_item(("final", "de", "en")) == ("final", "de", "en", {})
    assert run_eval.parse_item(("final", "de", "en", {"id": 3}))[3] == {"id": 3}
    assert run_eval.parse_item({"kind": "draft", "de": "x", "en": "y"})[:3] == ("draft", "x", "y")


def test_baseline_numbers_hold_no_text():
    out = run_eval.numbers_only({"wer_de": 1.23456, "clips": 3, "note": "Satz", "by": {"B": 1.0}, "_rows": [1]})
    assert out == {"wer_de": 1.235, "clips": 3, "by": {"B": 1.0}}

