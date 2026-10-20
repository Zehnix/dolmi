"""Reads English lines aloud, one after another, and keeps up with the meeting by pace, never by skipping.

VOICE-10 (user requirement 2026-10-20): every line shown is spoken. Nothing is dropped for age or queue
length (the old MAX_QUEUE = 1 / MAX_AGE = 4 s rules skipped about a third of the clause pieces). Instead the
speaking speed follows the backlog: the estimated seconds of speech still waiting (queued words at
WORDS_PER_S plus voice audio already handed to the headphone mix). Speed is BASE_SPEED when the backlog is
under RELAXED_S and rises linearly to SPEED_MAX (1.4x, still intelligible); it is smoothed (SMOOTH, MAX_STEP)
so it never jumps from line to line, and falls back to BASE_SPEED when the voice has caught up.

No gaps: with the headphone mix the next line is synthesized while the current one is still playing
(LOOKAHEAD_S of audio left), as streaming TTS chains do, so lines follow each other without a pause.

Backends are a fallback chain: when the first (Kokoro) fails on a line, the line is retried with the next
(the OS voice) and the reader stays there. VOICE-7: with a `mixer`, PCM backends play through Dolmi's
headphone mix instead of the default output. Nothing here mutes or ducks the meeting audio (VOICE-0b).
"""
import threading
import time
from collections import deque

WORDS_PER_S = 2.3     # English speech at 1.0x (Kokoro af_heart measured 2.2-2.4 words/s)
BASE_SPEED = 1.05     # a touch quicker than the voices' default, which sounded slow in the listening test
SPEED_MAX = 1.4       # cap: faster than this stops being easy to follow
RELAXED_S = 2.0       # s of backlog spoken at BASE_SPEED
FULL_S = 6.0          # s of backlog at which SPEED_MAX is reached
SMOOTH = 0.5          # each line moves this share of the way to the target speed
MAX_STEP = 0.1        # ... and at most this much
LOOKAHEAD_S = 1.5     # s of audio still playing when the next line is synthesized (no gap between lines)


def target_speed(backlog):
    """Speed the backlog (s of speech waiting) asks for: BASE_SPEED up to RELAXED_S, SPEED_MAX at FULL_S."""
    if not backlog or backlog <= RELAXED_S:
        return BASE_SPEED
    k = min(1.0, (backlog - RELAXED_S) / (FULL_S - RELAXED_S))
    return round(BASE_SPEED + k * (SPEED_MAX - BASE_SPEED), 3)


def smooth_speed(previous, target):
    step = max(-MAX_STEP, min(MAX_STEP, (target - previous) * SMOOTH))
    return round(min(SPEED_MAX, max(BASE_SPEED, previous + step)), 3)


def speech_seconds(text):
    return len((text or "").split()) / WORDS_PER_S


class Reader:
    def __init__(self, backend, clock=time.time, fallbacks=(), mixer=None):
        self.backend, self._clock = backend, clock
        self._fallbacks = list(fallbacks)
        self.mixer = mixer
        self.last_speed = BASE_SPEED
        self._q, self._cv = deque(), threading.Condition()
        self._stopped = False
        self.spoken = self.skipped = 0      # skipped stays 0: every line is spoken (VOICE-10)
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True, name="voice-reader")
        self._thread.start()
        return self

    def offer(self, text, end=None):
        """Queue an English line; `end` (when its speech ended) is kept for callers, pace uses the backlog."""
        text = (text or "").strip()
        if not text:
            return
        with self._cv:
            if self._stopped:
                return
            self._q.append((text, self._clock() if end is None else end))
            self._cv.notify()

    def next_line(self, wait=None):
        """The next line still worth speaking, or None (stopped, or nothing within `wait` seconds)."""
        item = self.next_item(wait)
        return item[0] if item else None

    def next_item(self, wait=None):
        """(text, backlog s) of the next line, or None. The backlog counts this line, the lines after it
        and the voice audio still playing."""
        with self._cv:
            while True:
                if self._q:
                    text, _end = self._q.popleft()
                    backlog = speech_seconds(text) + sum(speech_seconds(t) for t, _ in self._q)
                    return text, backlog + self._playing()
                if self._stopped:
                    return None
                woke = self._cv.wait(wait)
                if not woke:
                    return None              # timed out with nothing to say

    def _loop(self):
        try:
            while True:
                item = self.next_item()
                if item is None:
                    return
                self._say(*item)
        finally:
            self._close_backend()

    def _playing(self):
        left = getattr(self.mixer, "tts_left", None)
        return left() if left else 0.0

    def _say(self, text, backlog):
        speed = smooth_speed(self.last_speed, target_speed(backlog))
        while True:
            try:
                self._speak_with(self.backend, text, speed)
                self.spoken += 1
                self.last_speed = speed
                return
            except Exception as e:
                if self._stopped:            # a line cut by stop() is not a failure
                    return
                print(f"voice: speaking a line failed ({getattr(self.backend, 'name', '?')}): {e}")
                if not self._next_backend():
                    return

    def _speak_with(self, backend, text, speed):
        fast = speed if getattr(backend, "speeds", False) else None
        if self.mixer is not None and hasattr(backend, "synth_chunks"):
            self._wait_lookahead()
            for pcm, sr in backend.synth_chunks(text, fast or 1.0):
                if self._stopped:
                    return
                self.mixer.play(pcm, sr)
            if not hasattr(self.mixer, "tts_left"):
                self.mixer.wait_tts()
        elif fast is not None:
            backend.speak(text, fast)
        else:
            backend.speak(text)

    def _wait_lookahead(self):
        """Start the next line while the current one still plays: synthesis overlaps playback, no gap."""
        left = getattr(self.mixer, "tts_left", None)
        while left and not self._stopped and left() > LOOKAHEAD_S:
            time.sleep(0.05)

    def _next_backend(self):
        """Fall back to the next working backend (Kokoro -> OS voice); False when none is left."""
        while self._fallbacks:
            nxt = self._fallbacks.pop(0)
            try:
                if not nxt.available():
                    continue
            except Exception as e:
                print(f"voice backend {getattr(nxt, 'name', nxt)} check failed: {e}")
                continue
            old, self.backend = self.backend, nxt
            print(f"voice: switching from {getattr(old, 'name', '?')} to {getattr(nxt, 'name', '?')}")
            try:
                close = getattr(old, "close", None)
                if close:
                    close()
            except Exception as e:
                print(f"voice: closing {getattr(old, 'name', '?')} failed: {e}")
            return True
        return False

    def _close_backend(self):
        try:
            close = getattr(self.backend, "close", None)
            if close:
                close()
        except Exception as e:
            print(f"voice: closing {getattr(self.backend, 'name', '?')} failed: {e}")

    def stop(self):
        """Stop reading: drop waiting lines, cut the line being spoken, and close the voice."""
        with self._cv:
            self._stopped = True
            self._q.clear()
            self._cv.notify_all()
        if self.mixer is not None:
            self.mixer.stop_tts()
        cancel = getattr(self.backend, "cancel", None)
        if cancel:
            try:
                cancel()
            except Exception as e:
                print(f"voice: cutting the line failed ({getattr(self.backend, 'name', '?')}): {e}")
        if self._thread is None:         # never started: nobody else will close it
            self._close_backend()
