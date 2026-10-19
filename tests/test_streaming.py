"""STR-1..5: LocalAgreement drafts, stable draft English, the ui_q message contract with its stale-result
guard, bounded draft work, and the per-utterance latency trace."""
import json
import threading
import time

import numpy as np

import live_subs as m
import streaming
from test_pipeline import run_worker, silence, speech
from test_live import _calls, _session

SR = m.SR


# ------------------------------------------------------------------ STR-1: LocalAgreement-2
def words(text, start=0.0, step=0.4):
    return [(start + i * step, start + (i + 1) * step, w) for i, w in enumerate(text.split())]


def test_local_agreement_commits_only_what_two_hypotheses_agree_on():
    hyps = iter([words("Wir haben"), words("Wir haben das Proj"), words("Wir haben das Projekt verschoben"),
                 words("Wir haben das Projekt verschoben, weil")])
    d = streaming.StableDraft(lambda audio, prompt: next(hyps))
    audio = np.zeros(2 * SR, np.float32)
    assert d.update(audio) == ("", "Wir haben")                         # one hypothesis: nothing agreed yet
    assert d.update(audio) == ("Wir haben", "das Proj")
    assert d.update(audio) == ("Wir haben das", "Projekt verschoben")
    assert d.update(audio) == ("Wir haben das Projekt verschoben,", "weil")   # "verschoben" agrees with "verschoben,"


def test_committed_text_never_changes_once_shown():
    """A noisy replay: hypotheses flip their tail words; the committed part only ever grows."""
    rng = np.random.default_rng(7)
    truth = "heute sprechen wir über das neue Projekt und den Zeitplan für das nächste Quartal".split()
    shown = []

    def hyp(audio, prompt):
        n = min(len(truth), len(shown_words()) + 3)
        out = list(truth[:n])
        for i in range(len(shown_words()), n):                     # the unsure tail sometimes differs
            if rng.random() < 0.4:
                out[i] = f"{out[i]}{rng.integers(10**6)}"
        return words(" ".join(out))

    def shown_words():
        return shown[-1].split() if shown else []

    d = streaming.StableDraft(hyp)
    for _ in range(300):
        committed, _tail = d.update(np.zeros(SR, np.float32))
        if shown:
            assert committed.startswith(shown[-1])                   # append-only
        shown.append(committed)
    assert shown[-1].split() == truth


def test_buffer_is_trimmed_at_a_committed_sentence_end_and_scrolled_text_becomes_the_prompt():
    seen = []
    first = words("Das ist gut. Wir machen weiter", step=1.5)        # "gut." ends at 4.5 s

    def hyp(audio, prompt):
        seen.append((len(audio) / SR, prompt))
        if len(seen) <= 2:
            return first
        return [(w[0] - 4.5, w[1] - 4.5, w[2]) for w in first[3:]] + [(6.0, 6.5, "morgen")]

    d = streaming.StableDraft(hyp, trim_after=8.0)
    audio = np.zeros(int(11 * SR), np.float32)
    d.update(audio)
    d.update(audio)                                                  # all agreed, buffer 11 s > 8 s: trim
    assert d.offset == 4.5                                            # at the end of "gut.", not mid-word
    assert d.prompt() == "Das ist gut."
    committed, tail = d.update(audio)
    assert seen[-1] == (11 - 4.5, "Das ist gut.")                     # only the audio after the cut
    assert committed == "Das ist gut. Wir machen weiter" and tail == "morgen"


def test_repeated_words_at_the_trim_boundary_are_not_committed_twice():
    hb = streaming.HypothesisBuffer()
    hb.insert(words("wir machen weiter"), 0)
    hb.flush()
    hb.insert(words("wir machen weiter"), 0)
    assert [w[2] for w in hb.flush()] == ["wir", "machen", "weiter"]
    # the next hypothesis starts with the last committed bigram again (audio re-heard after a trim)
    hb.insert([(1.1, 1.2, "machen"), (1.2, 1.25, "weiter"), (1.3, 1.6, "morgen")], 0.0)
    assert [w[2] for w in hb.new] == ["morgen"]


# ------------------------------------------------------------------ STR-5: stable draft English
def test_draft_english_is_append_only_and_uses_the_shown_english_as_prefix():
    calls = []
    outs = iter(["We have", "We have the project", "We have the project postponed", "We have the project postponed because"])

    def translate(src, prefix):
        calls.append(prefix)
        return next(outs)

    st = streaming.StableTranslation(translate)
    assert st.update("Wir haben") == ("", "We have")
    assert st.update("Wir haben das Projekt") == ("We have", "the project")
    assert st.update("Wir haben das Projekt verschoben") == ("We have the project", "postponed")
    assert st.update("Wir haben das Projekt verschoben") == ("We have the project", "postponed")   # no new call
    assert calls == ["", "", "We have"]                  # the committed English is the prefix


def test_a_translator_ignoring_the_prefix_cannot_rewrite_committed_english():
    outs = iter(["We have the", "We have the plan", "They had a plan entirely"])
    st = streaming.StableTranslation(lambda src, prefix: next(outs))
    st.update("a")
    st.update("a b")
    committed, _ = st.update("a b c")
    assert committed.startswith("We have the")


# ------------------------------------------------------------------ STR-3: bounded work
def test_mailbox_keeps_only_the_newest_item():
    box = streaming.Mailbox()
    box.put(1)
    box.put(2)
    box.put(3)
    assert box.get(timeout=0) == 3 and box.get(timeout=0) is None and box.replaced == 2


def test_sample_queue_counts_audio_reports_overload_once_and_drops_nothing():
    warned = []
    q = streaming.SampleQueue(limit=1.0, on_overload=warned.append)
    for _ in range(25):
        q.put(np.zeros(SR // 10, np.float32))                      # 2.5 s
    deadline = time.time() + 2
    while not warned and time.time() < deadline:
        time.sleep(0.01)
    assert len(warned) == 1 and warned[0] > 1.0 and abs(q.backlog() - 2.5) < 1e-6
    got = [q.get() for _ in range(25)]
    assert len(got) == 25 and q.backlog() == 0


def test_guard_drops_stale_drafts_and_old_sessions_but_never_finals():
    g = streaming.CaptionGuard()
    ok = lambda kind, utt, rev, text="x", session=1: g.accept(kind, text, text, {"session": session, "utt": utt, "rev": rev}, 1)
    assert ok("draft", 5, 1)
    assert not ok("draft", 5, 1)                                    # same rev again
    assert ok("draft", 6, 3)
    assert ok("final", 6, 4)
    assert ok("final", 5, 2)                                        # a late final is still shown, in order
    assert not ok("draft", 5, 5)                                    # a draft after its final: dropped
    assert not ok("draft", 6, 6)
    assert ok("draft", 7, 7)
    assert not ok("draft", 6, 8, text="")                           # a clear of an older line keeps utt 7's draft
    assert ok("draft", 7, 9, text="")
    assert not ok("final", 8, 10, session=2)                        # anything from an old session
    assert g.accept("draft", "x", "y", {}, 1)                       # older producers without the contract


def test_pump_drops_late_drafts_and_other_sessions(api):
    _session(api)
    sid = api._session_id()
    api._ui_q.put(("draft", "Wir", "We", {"session": sid, "utt": 1, "rev": 2}))
    api._ui_q.put(("draft", "W", "", {"session": sid, "utt": 1, "rev": 1}))          # older: dropped
    api._ui_q.put(("draft", "Alt", "Old", {"session": sid + 1, "utt": 9, "rev": 9}))  # other session
    api._ui_q.put(("final", "Wir sind da.", "We are here.", {"session": sid, "utt": 1, "rev": 3,
                                                               "start": time.time() - 2, "end": time.time() - 1,
                                                               "trace": {"speech_end": time.time() - 1,
                                                                         "vad_close": time.time() - 0.4}}))
    api._ui_q.put(("draft", "Wir sind", "We are", {"session": sid, "utt": 1, "rev": 4}))  # after its final
    api._ui_q.put(("draft", "Next", "Next", {"session": sid, "utt": 2, "rev": 5}))
    (line,) = _calls(api, "onLine", 1)
    drafts = _calls(api, "onDraft", 2)
    time.sleep(0.3)
    assert [d[0] for d in _calls(api, "onDraft", 2)] == ["Wir", "Next"]
    meta = line[3]
    assert meta["trace"]["shown"] == meta["shown"]                  # STR-4: the pump closes the trace
    assert 0.5 < meta["lag_policy"] < 0.7 and meta["lag_compute"] >= 0.4
    assert drafts


def test_pump_shows_overload_as_a_toast(api):
    _session(api)
    api._ui_q.put(("overload", 31, "", {"session": api._session_id()}))
    (msg,) = _calls(api, "onError", 1)[0]
    assert "31 s behind" in msg and "no speech is lost" in msg


# ------------------------------------------------------------------ worker
class Stable:
    """An engine with word timestamps: each decode hears one more word of the sentence."""
    cloud, language, context, stable_drafts = False, "de", "", True
    SENTENCE = "Wir haben das Projekt gestern verschoben weil".split()

    def __init__(self):
        self.prefixes = []

    def transcribe_words(self, audio, prompt=""):
        n = min(len(self.SENTENCE), max(1, int(len(audio) / SR / 0.4)))
        return words(" ".join(self.SENTENCE[:n])), "de"

    def transcribe(self, audio):
        return "Wir haben das Projekt gestern verschoben.", "de"

    def translate(self, text, lang):
        return f"EN[{text}]"

    def translate_prefix(self, text, lang, prefix=""):
        self.prefixes.append(prefix)
        return " ".join(f"e{w}" for w in text.split())


def test_stable_drafts_grow_append_only_in_both_languages():
    eng = Stable()
    items, _ = run_worker(eng, np.concatenate([speech(4.5), silence(1)]), realtime=True, wait=4, finals=1)
    drafts = [i for i in items if i[0] == "draft" and i[1]]
    assert len(drafts) >= 3
    committed = [d[3]["committed"] for d in drafts]
    for a, b in zip(committed, committed[1:]):
        assert b.startswith(a)
    assert committed[-1] and all(d[1] == " ".join(x for x in (d[3]["committed"], d[3]["tentative"]) if x) for d in drafts)
    english = [d[3]["en_committed"] for d in drafts if "en_committed" in d[3]]
    for a, b in zip(english, english[1:]):
        assert b.startswith(a)
    assert any(eng.prefixes)                                         # the shown English went in as prefix
    assert [i[1] for i in items if i[0] == "final"] == ["Wir haben das Projekt gestern verschoben."]


class Slow:
    """Whisper slower than the speaker: every decode takes 2.5 s."""
    cloud, language, context = False, "de", ""

    def __init__(self):
        self.drafts = 0

    def transcribe_draft(self, audio):
        self.drafts += 1
        time.sleep(2.5)
        return "Wir", "de"

    def transcribe(self, audio):
        time.sleep(2.5)
        return "Ein Satz.", "de"

    def translate(self, text, lang):
        return "A sentence."


def test_a_backlog_loses_no_utterance_sheds_drafts_and_says_so(monkeypatch):
    monkeypatch.setattr(streaming, "OVERLOAD_AFTER", 1.0)
    drops = []
    talk = np.concatenate([np.concatenate([speech(1.2), silence(0.8)]) for _ in range(5)])
    eng = Slow()
    items, tr = run_worker(eng, talk, realtime=True, wait=20, finals=5, on_drop=lambda *a: drops.append(a))
    assert len([i for i in items if i[0] == "final"]) == 5 and len(tr.rows) == 5 and not drops
    assert eng.drafts <= 2                                           # draft work went first
    assert any(i[0] == "overload" for i in items)


# ------------------------------------------------------------------ STR-4: trace
def test_every_final_has_an_ordered_trace(fake_engine):
    audio = np.concatenate([silence(0.5), speech(1), silence(1), speech(1.2), silence(1)])
    items, _ = run_worker(fake_engine(), audio, realtime=True, wait=4, finals=2)
    finals = [i for i in items if i[0] == "final"]
    assert len(finals) == 2
    for f in finals:
        t = {**f[3]["trace"], "shown": time.time()}
        assert streaming.trace_ok(t), t
        assert t["speech_end"] <= t["vad_close"] <= t["queued"] + 0.2


def test_trace_file_has_utc_start_offsets_and_no_text(tmp_path, monkeypatch):
    monkeypatch.setenv("DOLMI_TRACE_DIR", str(tmp_path))
    monkeypatch.delenv("DOLMI_TRACE_TEXT", raising=False)
    w = streaming.TraceWriter(t0=1000.0)
    w.write({"id": 1, "utt": 3, "trace": {"speech_start": 1001.0, "speech_end": 1002.0, "vad_close": 1002.6,
                                          "asr_done": 1003.0, "mt_done": 1003.2, "shown": 1003.3}},
            "Geheim", "Secret")
    (path,) = tmp_path.glob("trace_*.jsonl")
    head, row = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    assert head["session_start"].endswith("+00:00")
    assert row["speech_start"] == 1.0 and row["shown"] == 3.3 and row["utt"] == 3
    assert abs(row["lag_policy"] - 0.6) < 1e-9 and abs(row["lag_compute"] - 0.7) < 1e-9
    assert "Geheim" not in path.read_text(encoding="utf-8")
