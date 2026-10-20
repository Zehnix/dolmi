"""VOICE-9: speak clause by clause instead of waiting for the final line (setting voice_clauses, off).

The STR-1 draft path gives each utterance's committed (stable) German. ClauseSpeaker cuts it at clause
boundaries, translates each new clause with the English already spoken forced as the target prefix
(STR-5 / Engine.translate_prefix), and hands only the new English to the reader. When the final comes,
only the English after what was already spoken is read, so nothing is said twice.

German puts the verb at the end of subordinate clauses, so cuts are cautious:
- always after . ! ? ; :
- after a comma when the next word starts a clause (dass, weil, und, der ...) or the left part is long;
  a short comma part ("Äpfel, Birnen") is a list, not a clause
- before aber / denn / sondern (and und / oder after a long part) without a comma
- on a pause, but not inside an open subordinate clause (its verb is still to come)
A cut needs the next committed word, so a trailing comma that may still change is never spoken.

Committed words that are later withdrawn (streaming.js finding 2026-10-20) are never re-spoken: the
speaker counts spoken German words and only reads words beyond that count.
"""
import queue
import re
import threading
import time

MIN_WORDS = 3          # shortest clause worth a voice line
LONG_PART = 6          # a comma part this long is taken as a clause on its own
PAUSE = 0.3            # s without new committed words: flush the open clause

SENTENCE_END = (".", "!", "?", ";", ":")
SUBORDINATE = {"dass", "daß", "weil", "wenn", "ob", "obwohl", "obgleich", "damit", "als", "bevor",
               "nachdem", "während", "sodass", "falls", "seit", "seitdem", "bis", "indem", "sobald",
               "solange", "wie", "wo", "warum", "was", "wer", "wohin", "woher"}
RELATIVE = {"der", "die", "das", "dem", "den", "dessen", "deren", "welcher", "welche", "welches"}
COORDINATING = {"und", "oder", "aber", "denn", "sondern"}
STARTERS = SUBORDINATE | RELATIVE | COORDINATING
STRONG_COORD = {"aber", "denn", "sondern"}


def _bare(word):
    return re.sub(r"\W", "", word.lower())


def clause_cuts(words, final=False, pause=False):
    """Word indices (exclusive ends) where a finished clause ends in `words`.
    `final`: the utterance is over, the rest is a clause. `pause`: the speaker stopped for PAUSE s."""
    cuts, start = [], 0
    for i, w in enumerate(words):
        has_next = i + 1 < len(words)
        n = i + 1 - start                                  # words in the open clause including w
        nxt = _bare(words[i + 1]) if has_next else ""
        if w.endswith(SENTENCE_END) and (has_next or final):
            cut = i + 1
        elif w.endswith(",") and has_next and n >= MIN_WORDS and (nxt in STARTERS or n >= LONG_PART):
            cut = i + 1
        elif has_next and n >= MIN_WORDS + 1 and (nxt in STRONG_COORD or (nxt in ("und", "oder") and n >= LONG_PART)):
            cut = i + 1                                    # before a conjunction without a comma
        else:
            continue
        cuts.append(cut)
        start = cut
    rest = len(words) - start
    if rest and final:
        cuts.append(len(words))
    elif rest >= MIN_WORDS - 1 and pause and not _open_subclause(words, start):
        cuts.append(len(words))
    return cuts


def _open_subclause(words, start):
    """Verb-final caution: the open part has a subordinate or relative clause whose verb may still come."""
    return any(_bare(words[j]) in SUBORDINATE | RELATIVE and (j == start or words[j - 1].endswith(","))
               for j in range(start, len(words)))


class ClauseSpeaker:
    """One utterance at a time. translate(src_de, prefix_en) -> English starting with prefix_en.
    say(english, end) receives each new piece to speak. Not thread-safe; ClauseVoice runs it on one thread."""

    def __init__(self, translate, say, clock=time.time, perf=time.perf_counter, on_timing=None):
        self.translate, self.say = translate, say
        self._clock, self._perf, self.on_timing = clock, perf, on_timing
        self.utt = None
        self._reset(None)

    def _reset(self, utt):
        self.utt = utt
        self.spoken_de = []        # German words already spoken (as committed when spoken)
        self.spoken_en = ""        # English already spoken (the target prefix for the next clause)

    def draft(self, utt, committed, pause=False):
        """New committed German for utterance `utt` (the whole committed text, not a delta)."""
        if utt != self.utt:
            self._reset(utt)
        words = (committed or "").split()
        cuts = [c for c in clause_cuts(words, pause=pause) if c > len(self.spoken_de)]
        if cuts:
            self._speak(words[:cuts[-1]], "draft")

    def final(self, utt, de, en=""):
        """The final line: speak what was not said yet (all of `en` when nothing was spoken)."""
        if utt != self.utt or not self.spoken_de:
            self._reset(None)
            if en.strip():
                self.say(en.strip(), self._clock())
            return
        words = (de or "").split()
        if len(words) > len(self.spoken_de):
            self._speak(words, "final")
        self._reset(None)

    def _speak(self, words, kind):
        t = self._perf()
        try:
            full = self.translate(" ".join(words), self.spoken_en)
        except Exception as e:
            print(f"voice: clause translation failed ({kind}, {len(words)} words): {e}")
            return
        mt = self._perf() - t
        new = _after_prefix(full, self.spoken_en)
        self.spoken_de, self.spoken_en = list(words), f"{self.spoken_en} {new}".strip()
        if new:
            self.say(new, self._clock())
        if self.on_timing:
            self.on_timing({"kind": kind, "words": len(words), "mt": mt, "en": new})


def _after_prefix(full, prefix):
    """The part of `full` after the forced English prefix (word-wise, case and punctuation ignored)."""
    out, head = full.split(), prefix.split()
    k = 0
    while k < len(head) and k < len(out) and _bare(out[k]) == _bare(head[k]):
        k += 1
    if k < len(head):              # the engine ignored the prefix (protected terms): skip as many words
        k = min(len(head), len(out))
    return " ".join(out[k:])


class ClauseVoice:
    """Runs a ClauseSpeaker on its own thread so translation never blocks the UI pump.
    Drafts of the same utterance are coalesced (only the newest committed text matters); finals are kept."""

    def __init__(self, translate, reader, clock=time.time, pause=PAUSE):
        self.reader, self._clock, self.pause = reader, clock, pause
        self.speaker = ClauseSpeaker(translate, reader.offer, clock=clock)
        self._q = queue.Queue()
        self._last = {}            # utt -> (committed, time it last grew)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="voice-clauses")
        self._thread.start()

    def draft(self, utt, committed):
        if committed:
            self._q.put(("draft", utt, committed))

    def final(self, utt, de, en):
        self._q.put(("final", utt, de, en))

    def stop(self):
        self._stop.set()
        self._q.put(("stop",))

    def _loop(self):
        while not self._stop.is_set():
            try:
                items = [self._q.get(timeout=self.pause / 2)]
            except queue.Empty:
                self._check_pause()
                continue
            while True:                             # take what waits; keep only the newest of a draft run
                try:
                    items.append(self._q.get_nowait())
                except queue.Empty:
                    break
            for i, item in enumerate(items):
                if item[0] == "stop":
                    return
                nxt = items[i + 1] if i + 1 < len(items) else None
                if item[0] == "draft" and nxt and nxt[0] == "draft" and nxt[1] == item[1]:
                    continue
                self._handle(item)

    def _handle(self, item):
        try:
            if item[0] == "draft":
                _, utt, committed = item
                if self._last.get(utt, ("",))[0] != committed:
                    self._last = {utt: (committed, self._clock(), False)}
                self.speaker.draft(utt, committed)
            elif item[0] == "final":
                self._last = {}
                self.speaker.final(*item[1:])
        except Exception as e:
            print(f"voice: clause speaking failed ({item[0]}): {e}")

    def _check_pause(self):
        for utt, (committed, since, done) in list(self._last.items()):
            if not done and self._clock() - since >= self.pause:
                self._last[utt] = (committed, since, True)
                try:
                    self.speaker.draft(utt, committed, pause=True)
                except Exception as e:
                    print(f"voice: clause speaking failed (pause): {e}")
