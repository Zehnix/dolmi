"""Dolmi's own headphone mix (VOICE-7): English voice + the captured German as a quiet safety net.

One stereo stream goes to the user's headphones:
- English TTS at full level (PCM pushed with play()).
- The captured meeting audio (feed_german()) at the safety-net level: default 10 %, 0-30 %, 0 = English
  only. It is ducked further (x DUCK) while the voice speaks and raised (x IDLE_BOOST) when the voice has
  been quiet for IDLE_HOLD seconds. Gains ramp, so nothing clicks.
- Holding the 'hear the original' key plays the German at 100 % and the voice quietly underneath.
- Optional stereo split: English in the left ear, German in the right.
- A short chime when one of the user's names appears in a German line (check_name()).

All ducking happens here, in Dolmi's mix; the meeting app's own volume is never touched. Whether the
meeting itself is also audible depends on the routing (VOICE-5/6); until then the user hears the meeting
app directly as well, so the safety net is only fed when the headphones are not the captured speaker
(otherwise Dolmi would capture its own mix again).

render() is pure (numpy in, numpy out) so tests drive it with fake devices; start(sink) runs it on a
thread and writes 20 ms blocks to a sink with write(block) (PyAudioSink for real headphones).
"""
import re
import threading
import time
import unicodedata
from collections import deque

import numpy as np

SR = 24000                 # Kokoro's rate; other sources are resampled to it
BLOCK = 480                # 20 ms
SAFETY_DEFAULT = 0.10
SAFETY_MAX = 0.30
DUCK = 0.4                 # German x this while the voice speaks
IDLE_BOOST = 2.0           # German x this once the voice has been quiet for IDLE_HOLD s
IDLE_HOLD = 0.6
HOLD_VOICE = 0.25          # the voice's level while 'hear the original' is held
ATTACK_S, RELEASE_S = 0.08, 0.4   # time for a full 0->1 gain change: duck fast, come back slowly
GERMAN_MAX_LAG = 0.5       # s of captured audio kept; older is dropped so the safety net stays in sync
CHIME_COOLDOWN = 8.0       # s between two name chimes
CHIME_GAIN = 0.25


def resample(x, sr_in, sr_out=SR):
    """Linear resampling, good enough for speech in a safety net and for TTS at 22-24 kHz."""
    x = np.asarray(x, np.float32).reshape(-1)
    if sr_in == sr_out or x.size == 0:
        return x
    n = max(1, int(round(x.size * sr_out / sr_in)))
    return np.interp(np.linspace(0, x.size - 1, n), np.arange(x.size), x).astype(np.float32)


def chime(sr=SR):
    """Two soft rising tones, 0.3 s."""
    def tone(f, d):
        t = np.arange(int(sr * d)) / sr
        env = np.minimum(1, np.minimum(t, d - t) / 0.02)
        return np.sin(2 * np.pi * f * t) * env
    return (np.concatenate([tone(880, 0.14), tone(1320, 0.16)]) * CHIME_GAIN).astype(np.float32)


def test_tone(seconds=1.0, sr=SR):
    """The wizard's test tone: 440 Hz with soft edges."""
    t = np.arange(int(sr * seconds)) / sr
    env = np.minimum(1, np.minimum(t, seconds - t) / 0.05)
    return (0.2 * np.sin(2 * np.pi * 440 * t) * env).astype(np.float32)


def _fold(text):
    text = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in text if not unicodedata.combining(c)).replace("ß", "ss")


def name_list(setting):
    """'Anna, Anni' -> ['anna', 'anni'] (case- and accent-folded, empty parts dropped)."""
    return [_fold(n.strip()) for n in re.split(r"[,;\n]", setting or "") if n.strip()]


def mentions(text, names):
    """Does a German line contain one of the names as a whole word?"""
    folded = _fold(text or "")
    return any(re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", folded) for n in names)


class _Buffer:
    """FIFO of float32 samples."""

    def __init__(self):
        self._parts, self.size = deque(), 0

    def push(self, x):
        if x.size:
            self._parts.append(x)
            self.size += x.size

    def pull(self, n):
        out = np.zeros(n, np.float32)
        got = 0
        while got < n and self._parts:
            p = self._parts[0]
            take = min(n - got, p.size)
            out[got:got + take] = p[:take]
            got += take
            if take == p.size:
                self._parts.popleft()
            else:
                self._parts[0] = p[take:]
        self.size -= got
        return out, got

    def drop_to(self, keep):
        """Keep only the newest `keep` samples."""
        extra = self.size - keep
        if extra > 0:
            self.pull(extra)

    def clear(self):
        self._parts.clear()
        self.size = 0


class Mixer:
    def __init__(self, safety=SAFETY_DEFAULT, split=False, names="", german=True, clock=time.monotonic):
        self._lock = threading.Lock()
        self._clock = clock
        self._tts, self._german, self._fx = _Buffer(), _Buffer(), _Buffer()
        self.safety = self._clamp(safety)
        self.split, self.german_on = bool(split), bool(german)
        self.names = name_list(names)
        self.held = False
        self._g_gain = self._target_german(speaking=False, idle=True)
        self._v_gain = 1.0
        self._last_voice = -1e9
        self._last_chime = -1e9
        self._tts_done = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._thread = None
        self.sink = None

    # ---- settings
    @staticmethod
    def _clamp(v):
        try:
            return float(min(SAFETY_MAX, max(0.0, float(v))))
        except (TypeError, ValueError):
            return SAFETY_DEFAULT

    def configure(self, safety=None, split=None, names=None, german=None):
        with self._lock:
            if safety is not None:
                self.safety = self._clamp(safety)
            if split is not None:
                self.split = bool(split)
            if names is not None:
                self.names = name_list(names)
            if german is not None:
                self.german_on = bool(german)
                if not german:
                    self._german.clear()

    def hold(self, on):
        """'Hear the original' key down (True) / up (False)."""
        self.held = bool(on)

    # ---- inputs
    def play(self, pcm, sr):
        """Queue English voice PCM (any rate)."""
        x = resample(pcm, sr)
        with self._lock:
            self._tts.push(x)

    def feed_german(self, pcm, sr=16000):
        """Captured meeting audio (16 kHz mono from the capture thread)."""
        if not self.german_on:
            return
        x = resample(pcm, sr)
        with self._lock:
            self._german.push(x)
            self._german.drop_to(int(GERMAN_MAX_LAG * SR))

    def check_name(self, german_text):
        """Chime when a German line mentions the user; True when it chimed (the page can flash)."""
        if not self.names or not mentions(german_text, self.names):
            return False
        now = self._clock()
        with self._lock:
            if now - self._last_chime < CHIME_COOLDOWN:
                return False
            self._last_chime = now
            self._fx.push(chime())
        return True

    def play_effect(self, pcm, sr=SR):
        """A sound in both ears that is not ducked (test tone, chime)."""
        x = resample(pcm, sr)
        with self._lock:
            self._fx.push(x)

    def tts_busy(self):
        with self._lock:
            return self._tts.size > 0

    def tts_left(self):
        """Seconds of English voice queued and not played yet (the reader starts the next line early)."""
        with self._lock:
            return self._tts.size / SR

    def stop_tts(self):
        """Cut the voice now (reader stop)."""
        with self._lock:
            self._tts.clear()
            self._tts_done.notify_all()

    def wait_tts(self, timeout=60.0):
        """Block until the queued voice has been played (or cut)."""
        end = time.monotonic() + timeout
        with self._lock:
            while self._tts.size > 0 and not self._stop.is_set():
                left = end - time.monotonic()
                if left <= 0:
                    return False
                self._tts_done.wait(min(left, 0.1))
        return True

    # ---- the mix
    def _target_german(self, speaking, idle):
        if self.held:
            return 1.0
        if not self.german_on:
            return 0.0
        if speaking:
            return self.safety * DUCK
        return min(1.0, self.safety * (IDLE_BOOST if idle else 1.0))

    @staticmethod
    def _ramp(cur, target, n):
        rate = (1.0 / ATTACK_S if target < cur else 1.0 / RELEASE_S) * n / SR
        new = cur + max(-rate, min(rate, target - cur))
        return np.linspace(cur, new, n, dtype=np.float32), new

    def render(self, n=BLOCK):
        """The next n samples of the mix, shape (n, 2), float32 in [-1, 1]."""
        with self._lock:
            voice, got = self._tts.pull(n)
            german, _ = self._german.pull(n)
            fx, _ = self._fx.pull(n)
            now = self._clock()
            speaking = got > 0
            if speaking:
                self._last_voice = now
            idle = now - self._last_voice >= IDLE_HOLD
            g_env, self._g_gain = self._ramp(self._g_gain, self._target_german(speaking, idle), n)
            v_env, self._v_gain = self._ramp(self._v_gain, HOLD_VOICE if self.held else 1.0, n)
            if self._tts.size == 0:
                self._tts_done.notify_all()
            split = self.split
        v, g = voice * v_env, german * g_env
        if split:
            out = np.stack([v + fx, g + fx], axis=1)
        else:
            mono = v + g + fx
            out = np.stack([mono, mono], axis=1)
        return np.clip(out, -1.0, 1.0)

    # ---- output thread
    def start(self, sink):
        self.sink = sink
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="voice-mixer")
        self._thread.start()
        return self

    def _run(self):
        try:
            while not self._stop.is_set():
                self.sink.write(self.render())     # a real sink blocks for one block: that is the clock
        except Exception as e:
            if not self._stop.is_set():
                print(f"voice mixer: output failed ({getattr(self.sink, 'name', '?')}): {e}")
        finally:
            with self._lock:
                self._tts.clear()
                self._tts_done.notify_all()
            close = getattr(self.sink, "close", None)
            if close:
                try:
                    close()
                except Exception as e:
                    print(f"voice mixer: closing the output failed: {e}")

    def stop(self):
        self._stop.set()
        with self._lock:
            self._tts_done.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        elif self.sink is not None:
            close = getattr(self.sink, "close", None)
            if close:
                close()


# ---------------------------------------------------------------- devices
BLUETOOTH_HANDSFREE = re.compile(r"hands-?free|headset\b.*\b(ag|audio gateway)|\bhfp\b|\bsco\b", re.I)
SPEAKER_WORDS = re.compile(r"\bspeakers?\b|lautsprecher|\bdisplay audio\b|\bmonitor\b|realtek.*speaker", re.I)
HEADPHONE_WORDS = re.compile(r"head(phone|set)s?|kopfh|earbuds?|airpods|buds|ear", re.I)


def check_device(name, capture_device=""):
    """Setup-wizard verdict for an output device name: (level, message) with level ok|warn."""
    label = name or "the default output"
    if name and BLUETOOTH_HANDSFREE.search(name):
        return "warn", (f"{label} is a Bluetooth hands-free (call) output: the sound is mono and low quality, "
                        "and it can switch while the meeting uses the headset microphone. Pick the headset's "
                        "stereo/'Headphones' entry, or use a cable.")
    if name and capture_device and (name in capture_device or capture_device in name):
        return "warn", (f"Dolmi listens to the meeting on {label}. Playing the mix there too would make Dolmi "
                        "hear its own voice, so the German safety net stays off. Pick other headphones.")
    if name and SPEAKER_WORDS.search(name) and not HEADPHONE_WORDS.search(name):
        return "warn", (f"{label} looks like speakers. The translated voice needs headphones, or the meeting "
                        "microphone picks it up.")
    if not name:
        return "warn", "Using the default output. Pick your headphones so the voice never plays on speakers."
    return "ok", f"{label} looks like headphones."


class PyAudioSink:
    """Stereo float32 output to one device (Windows: WASAPI via pyaudiowpatch). Needs manual check."""
    name = "pyaudio"

    def __init__(self, device="", rate=SR):
        import pyaudiowpatch as pyaudio
        from dolmi_platform.windows import pa_open
        self._pa = pa_open()
        index = None
        if device:
            for i in range(self._pa.get_device_count()):
                d = self._pa.get_device_info_by_index(i)
                if d["maxOutputChannels"] >= 2 and not d.get("isLoopbackDevice") and device in d["name"]:
                    index = i
                    break
        self.device = device
        self._stream = self._pa.open(format=pyaudio.paFloat32, channels=2, rate=rate, output=True,
                                     output_device_index=index, frames_per_buffer=BLOCK)

    def write(self, block):
        self._stream.write(np.ascontiguousarray(block, np.float32).tobytes())

    def close(self):
        try:
            self._stream.stop_stream()
            self._stream.close()
        finally:
            from dolmi_platform.windows import pa_close
            pa_close(self._pa)


class SoundDeviceSink:
    """Stereo float32 output on macOS (sounddevice / PortAudio). Needs manual check on a Mac."""
    name = "sounddevice"

    def __init__(self, device="", rate=SR):
        import sounddevice as sd
        self.device = device
        self._stream = sd.OutputStream(samplerate=rate, channels=2, dtype="float32", blocksize=BLOCK,
                                       device=device or None)
        self._stream.start()

    def write(self, block):
        self._stream.write(np.ascontiguousarray(block, np.float32))

    def close(self):
        self._stream.stop()
        self._stream.close()


def open_sink(device=""):
    """The platform's headphone output."""
    import dolmi_platform
    return SoundDeviceSink(device) if dolmi_platform.OS == "mac" else PyAudioSink(device)


def headphone_devices():
    """Output device names for the setup's picker (Windows WASAPI outputs; Mac Core Audio outputs)."""
    import dolmi_platform
    if dolmi_platform.OS == "mac":
        try:
            import sounddevice as sd
            return [d["name"] for d in sd.query_devices() if d["max_output_channels"] > 0]
        except Exception as e:
            print(f"voice: listing outputs failed: {e}")
            return []
    try:
        import pyaudiowpatch as pyaudio
    except ImportError:
        return []
    from dolmi_platform.windows import pa_open, pa_close
    pa = pa_open()
    try:
        try:
            wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]
        except OSError:
            wasapi = None
        out = []
        for i in range(pa.get_device_count()):
            d = pa.get_device_info_by_index(i)
            if d["maxOutputChannels"] > 0 and not d.get("isLoopbackDevice") and (wasapi is None or d["hostApi"] == wasapi):
                out.append(d["name"])
        return out
    finally:
        pa_close(pa)
