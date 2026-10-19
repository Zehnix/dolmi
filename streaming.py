"""Streaming building blocks shared by the worker (live_subs.py) and the caption pump (src/ui/app.py).

- HypothesisBuffer / StableDraft (STR-1): LocalAgreement-2 commit policy on word timestamps with
  buffer trimming, so a draft is an append-only committed part plus a tentative tail.
- StableTranslation (STR-5): the same idea on the English side (forced target prefix + LCP commit).
- Mailbox, SampleQueue (STR-3): latest-only hand-over for draft work; a queue that counts the audio
  it holds and reports overload instead of losing anything.
- CaptionGuard (STR-3): the stale-result guard _pump runs on every caption.
- trace helpers and TraceWriter (STR-4): one per-utterance latency record.

HypothesisBuffer and the update/trim logic of StableDraft are ported from ufal/whisper_streaming
(whisper_online.py: HypothesisBuffer, OnlineASRProcessor), MIT licence, Copyright (c) 2023 ÚFAL;
see THIRD-PARTY-NOTICES.md.
"""
import json
import os
import queue
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

SR = 16000
PROMPT_CHARS = 200        # committed text that scrolled out of the buffer, used as Whisper's prompt
STABLE_TRIM = 10.0        # s of buffer after which it is trimmed at a committed sentence/word end
DRAFT_MT_EVERY = 1.0      # s, at most one draft translation per second (live-translate uses 1000 ms)
OVERLOAD_AFTER = 30.0     # s of audio waiting for Whisper before the user is told it is behind


def _norm(word):
    return re.sub(r"\W", "", word.lower())


def _text(words):
    return " ".join(w for _, _, w in words).strip()


# ------------------------------------------------------------------ STR-1: LocalAgreement-2
class HypothesisBuffer:
    """Words are (start, end, text) in seconds from the utterance start. flush() commits the longest
    common prefix of the last two hypotheses; the rest (complete()) is the tentative tail.
    Unlike the original, words are compared without case and punctuation ("Projekt" == "Projekt.")."""

    def __init__(self):
        self.committed_in_buffer = []
        self.buffer = []          # tentative words of the previous hypothesis
        self.new = []
        self.last_committed_time = 0.0

    def insert(self, new, offset):
        new = [(a + offset, b + offset, t) for a, b, t in new]
        self.new = [w for w in new if w[0] > self.last_committed_time - 0.1]
        if self.new and abs(self.new[0][0] - self.last_committed_time) < 1 and self.committed_in_buffer:
            # the start of the new hypothesis may repeat the last committed words (1- to 5-grams)
            cn, nn = len(self.committed_in_buffer), len(self.new)
            for i in range(1, min(cn, nn, 5) + 1):
                tail = [_norm(w[2]) for w in self.committed_in_buffer[-i:]]
                head = [_norm(w[2]) for w in self.new[:i]]
                if tail == head:
                    del self.new[:i]
                    break

    def flush(self):
        commit = []
        while self.new and self.buffer and _norm(self.new[0][2]) == _norm(self.buffer[0][2]):
            w = self.new.pop(0)
            self.buffer.pop(0)
            commit.append(w)
            self.last_committed_time = w[1]
        self.buffer, self.new = self.new, []
        self.committed_in_buffer.extend(commit)
        return commit

    def pop_committed(self, t):
        while self.committed_in_buffer and self.committed_in_buffer[0][1] <= t:
            self.committed_in_buffer.pop(0)

    def complete(self):
        return self.buffer


class StableDraft:
    """The draft of one utterance (OnlineASRProcessor without its own audio buffer: the Segmenter
    already holds the utterance). update(audio) re-decodes only the audio after the trim point.

    transcribe_words(audio, prompt) -> [(start, end, word)] with times relative to `audio`."""

    def __init__(self, transcribe_words, trim_after=STABLE_TRIM):
        self.transcribe_words = transcribe_words
        self.trim_after = trim_after
        self.hyp = HypothesisBuffer()
        self.committed = []       # every committed word of this utterance, in order
        self.offset = 0.0         # s of the utterance audio already trimmed off

    def prompt(self):
        """Committed text that scrolled out of the buffer (the rest Whisper hears again anyway)."""
        gone = [w for w in self.committed if w[1] <= self.offset]
        return _text(gone)[-PROMPT_CHARS:]

    def update(self, audio):
        """-> (committed text, tentative text) after decoding the utterance audio heard so far."""
        buf = audio[int(self.offset * SR):]
        words = self.transcribe_words(buf, self.prompt())
        self.hyp.insert(words, self.offset)
        self.committed += self.hyp.flush()
        if len(buf) / SR > self.trim_after:
            self._trim()
        return self.text()

    def text(self):
        return _text(self.committed), _text(self.hyp.complete())

    def _trim(self):
        """Cut the buffer at the last committed sentence end, else at the last committed word end:
        never inside a word, and never before text that is not committed yet."""
        inside = [w for w in self.committed if w[1] > self.offset]
        if not inside:
            return
        ends = [w for w in inside if w[2].rstrip().endswith((".", "?", "!"))]
        cut = (ends or inside)[-1][1]
        self.hyp.pop_committed(cut)
        self.offset = cut


# ------------------------------------------------------------------ STR-5: stable draft English
def common_prefix(a, b):
    n = 0
    for x, y in zip(a, b):
        if _norm(x) != _norm(y):
            break
        n += 1
    return n


class StableTranslation:
    """English for one utterance's committed German. Each call translates the committed source with the
    committed English forced as target prefix; words two calls agree on are committed (LocalAgreement on
    the target). Committed English never changes until the final replaces the line.

    translate(src, prefix) -> English (prefix may be ignored by the engine, e.g. with protected terms)."""

    def __init__(self, translate):
        self.translate = translate
        self.src = None
        self.committed = []       # English words shown as fixed
        self.last = []            # words of the previous output after the committed part

    def update(self, src):
        if src and src != self.src:
            self.src = src
            out = self.translate(src, " ".join(self.committed)).split()
            k = len(self.committed)
            if [_norm(w) for w in out[:k]] != [_norm(w) for w in self.committed]:
                out = self.committed + out[k:] if len(out) > k else list(self.committed)
            rest = out[k:]
            n = common_prefix(self.last, rest)
            self.committed += rest[:n]
            self.last = rest[n:]
        return " ".join(self.committed), " ".join(self.last)


# ------------------------------------------------------------------ STR-3: bounded work
class Mailbox:
    """Holds at most one item: a newer put replaces the waiting one (draft work only)."""

    def __init__(self):
        self._item, self._cv = None, threading.Condition()
        self.replaced = 0

    def put(self, item):
        with self._cv:
            if self._item is not None:
                self.replaced += 1
            self._item = item
            self._cv.notify()

    def get(self, timeout=None):
        """The waiting item (None after `timeout`)."""
        with self._cv:
            if self._item is None:
                self._cv.wait(timeout)
            item, self._item = self._item, None
            return item

    def peek(self):
        return self._item


class SampleQueue(queue.Queue):
    """A FIFO that counts the seconds of audio it holds. It never drops anything (finals and captured
    audio must not be lost); above `limit` s it calls on_overload(seconds) once, and again only after
    the backlog fell below half the limit."""

    def __init__(self, limit=None, on_overload=None, size=None):
        super().__init__()
        self.limit, self.on_overload = OVERLOAD_AFTER if limit is None else limit, on_overload
        self.size = size or (lambda item: len(item) if item is not None else 0)
        self.samples, self._warned = 0, False

    def _put(self, item):
        super()._put(item)
        self.samples += self.size(item)
        if not self._warned and self.samples / SR > self.limit:
            self._warned = True
            if self.on_overload:
                threading.Thread(target=self.on_overload, args=(self.samples / SR,), daemon=True).start()

    def _get(self):
        item = super()._get()
        self.samples -= self.size(item)
        if self._warned and self.samples / SR < self.limit / 2:
            self._warned = False
        return item

    def backlog(self):
        return self.samples / SR


class CaptionGuard:
    """The stale-result guard of the caption pump. Captions carry meta {"session", "utt", "rev"}:
    - anything from another session (after Stop/Start) is dropped;
    - finals are never dropped and are shown in arrival order (FIFO);
    - a draft is dropped when its utterance already has a final or a newer draft was shown;
    - a draft clear is dropped when a draft of a newer utterance is on screen.
    Captions without "session" (older producers, tests) pass unchanged."""

    def __init__(self):
        self.reset(None)

    def reset(self, session):
        self.session, self.final_utt, self.draft_utt, self.rev = session, 0, 0, {}

    def accept(self, kind, a, b, meta, session):
        if "session" not in meta:
            return True
        if meta["session"] != session:
            return False
        if session != self.session:
            self.reset(session)
        utt, rev = meta.get("utt"), meta.get("rev")
        if kind == "final":
            if isinstance(utt, int):
                self.final_utt = max(self.final_utt, utt)
                self.rev = {u: r for u, r in self.rev.items() if u > utt}
            return True
        if not isinstance(utt, int):
            return True
        if not (a or b):                                  # clear (or "dropped") of the live line
            return utt >= self.draft_utt
        if utt <= self.final_utt or (rev is not None and rev <= self.rev.get(utt, -1)):
            return False
        if rev is not None:
            self.rev[utt] = rev
        self.draft_utt = max(self.draft_utt, utt)
        return True


# ------------------------------------------------------------------ STR-4: latency trace
TRACE_FIELDS = ("speech_start", "speech_end", "vad_close", "queued", "asr_start", "asr_done",
                "mt_start", "mt_done", "emitted", "shown")


def split_lag(trace):
    """(policy lag, compute lag) in s: policy = waiting for the pause that closes the utterance
    (speech_end -> vad_close), compute = everything after it (queue, ASR, MT, UI) up to shown.
    None where a time is missing."""
    t = trace or {}
    def gap(a, b):
        return max(0.0, t[b] - t[a]) if isinstance(t.get(a), (int, float)) and isinstance(t.get(b), (int, float)) else None
    return gap("speech_end", "vad_close"), gap("vad_close", "shown")


def trace_ok(trace):
    """speech_start <= speech_end <= asr_done <= mt_done <= shown (the order every final must keep)."""
    order = [trace.get(k) for k in ("speech_start", "speech_end", "asr_done", "mt_done", "shown")]
    vals = [v for v in order if isinstance(v, (int, float))]
    return len(vals) == len(order) and all(x <= y + 1e-6 for x, y in zip(vals, vals[1:]))


def trace_dir():
    if os.environ.get("DOLMI_TRACE_DIR"):
        return Path(os.environ["DOLMI_TRACE_DIR"])
    import dolmi_platform
    return Path(dolmi_platform.config_dir()) / "trace"


class TraceWriter:
    """DOLMI_TRACE=1: one jsonl per session in %APPDATA%\\Dolmi\\trace. The first row holds the session
    start as UTC ISO 8601; every row after it is one final with its times as second offsets from that
    start. No transcript text unless DOLMI_TRACE_TEXT=1 (privacy)."""

    def __init__(self, t0=None):
        self.t0 = time.time() if t0 is None else t0
        self.text = os.environ.get("DOLMI_TRACE_TEXT") == "1"
        self.path, self._lock = None, threading.Lock()

    @staticmethod
    def enabled():
        return os.environ.get("DOLMI_TRACE") == "1"

    def write(self, meta, de="", en=""):
        trace = meta.get("trace") or {}
        row = {"id": meta.get("id"), "utt": meta.get("utt")}
        row.update({k: round(trace[k] - self.t0, 3) for k in TRACE_FIELDS if isinstance(trace.get(k), (int, float))})
        row["lag_policy"], row["lag_compute"] = split_lag(trace)
        if self.text:
            row.update(de=de, en=en)
        try:
            with self._lock:
                if self.path is None:
                    folder = trace_dir()
                    folder.mkdir(parents=True, exist_ok=True)
                    start = datetime.fromtimestamp(self.t0, timezone.utc)
                    self.path = folder / f"trace_{start:%Y%m%dT%H%M%SZ}.jsonl"
                    with self.path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps({"session_start": start.isoformat()}) + "\n")
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row) + "\n")
        except OSError as e:
            print(f"Trace not written ({e})")
