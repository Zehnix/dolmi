"""Reads English finals aloud, one at a time, without falling behind the meeting (VOICE-1 prototype).

Backlog control: at most MAX_QUEUE lines wait (a new line pushes out the oldest waiting one), and a line
whose speech ended more than MAX_AGE seconds ago is skipped instead of spoken. The subtitles still show
every line, so a skipped line is never lost. Nothing here mutes or ducks the meeting audio (VOICE-0b).
"""
import threading
import time
from collections import deque

MAX_QUEUE = 2       # lines waiting to be spoken
MAX_AGE = 6.0       # s after the end of the speech: older lines are skipped


class Reader:
    def __init__(self, backend, clock=time.time, max_queue=MAX_QUEUE, max_age=MAX_AGE):
        self.backend, self._clock = backend, clock
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
        with self._cv:
            while True:
                while self._q:
                    text, end = self._q.popleft()
                    if self._clock() - end <= self.max_age:
                        return text
                    self.skipped += 1
                if self._stopped:
                    return None
                woke = self._cv.wait(wait)
                if not woke:
                    return None              # timed out with nothing to say

    def _loop(self):
        try:
            while True:
                text = self.next_line()
                if text is None:
                    return
                try:
                    self.backend.speak(text)
                    self.spoken += 1
                except Exception as e:
                    if not self._stopped:    # a line cut by stop() is not a failure
                        print(f"voice: speaking a line failed ({getattr(self.backend, 'name', '?')}): {e}")
        finally:
            self._close_backend()

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
        cancel = getattr(self.backend, "cancel", None)
        if cancel:
            try:
                cancel()
            except Exception as e:
                print(f"voice: cutting the line failed ({getattr(self.backend, 'name', '?')}): {e}")
        if self._thread is None:         # never started: nobody else will close it
            self._close_backend()
