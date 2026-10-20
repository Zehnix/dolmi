"""Reads English finals aloud, one at a time, without falling behind the meeting (VOICE-1 prototype).

Backlog control: at most MAX_QUEUE lines wait (a new line pushes out the oldest waiting one), and a line
whose speech ended more than MAX_AGE seconds ago is skipped instead of spoken. The subtitles still show
every line, so a skipped line is never lost. Nothing here mutes or ducks the meeting audio (VOICE-0b).

VOICE-8: a line that starts more than BEHIND_S after its speech ended is spoken faster (1.15x, up to
1.3x at BEHIND_S + 2 s) by backends that take a speed. Backends are a fallback chain: when the first
(Kokoro) fails on a line, the line is retried with the next (the OS voice) and the reader stays there.
VOICE-7: with a `mixer`, PCM backends play through Dolmi's headphone mix instead of the default output.
"""
import threading
import time
from collections import deque

MAX_QUEUE = 2       # lines waiting to be spoken
MAX_AGE = 6.0       # s after the end of the speech: older lines are skipped
BEHIND_S = 2.0      # s behind before the voice speeds up
SPEED_MIN, SPEED_MAX = 1.15, 1.3


def catch_up_speed(lag):
    """1.0 when on time; 1.15x just past BEHIND_S, rising to 1.3x at BEHIND_S + 2 s."""
    if lag is None or lag <= BEHIND_S:
        return 1.0
    return round(min(SPEED_MAX, SPEED_MIN + (lag - BEHIND_S) / 2.0 * (SPEED_MAX - SPEED_MIN)), 3)


class Reader:
    def __init__(self, backend, clock=time.time, max_queue=MAX_QUEUE, max_age=MAX_AGE, fallbacks=(), mixer=None):
        self.backend, self._clock = backend, clock
        self._fallbacks = list(fallbacks)
        self.mixer = mixer
        self.last_speed = 1.0
        self.max_queue, self.max_age = max_queue, max_age
        self._q, self._cv = deque(), threading.Condition()
        self._stopped = False
        self.spoken = self.skipped = 0
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True, name="voice-reader")
        self._thread.start()
        return self

    def offer(self, text, end=None):
        """Queue an English line; `end` is when its speech ended (epoch seconds, default now)."""
        text = (text or "").strip()
        if not text:
            return
        with self._cv:
            if self._stopped:
                return
            while len(self._q) >= self.max_queue:
                self._q.popleft()
                self.skipped += 1
            self._q.append((text, self._clock() if end is None else end))
            self._cv.notify()

    def next_line(self, wait=None):
        """The next line still worth speaking, or None (stopped, or nothing within `wait` seconds)."""
        item = self.next_item(wait)
        return item[0] if item else None

    def next_item(self, wait=None):
        """(text, seconds behind) of the next line still worth speaking, or None."""
        with self._cv:
            while True:
                while self._q:
                    text, end = self._q.popleft()
                    lag = self._clock() - end
                    if lag <= self.max_age:
                        return text, lag
                    self.skipped += 1
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

    def _say(self, text, lag):
        speed = catch_up_speed(lag)
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
            for pcm, sr in backend.synth_chunks(text, fast or 1.0):
                if self._stopped:
                    return
                self.mixer.play(pcm, sr)
            self.mixer.wait_tts()
        elif fast is not None:
            backend.speak(text, fast)
        else:
            backend.speak(text)

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
