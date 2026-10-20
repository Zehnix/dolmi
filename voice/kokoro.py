"""Natural English voice (VOICE-8): Kokoro-82M on onnxruntime, phonemes from misaki without a fallback.

Licences (THIRD-PARTY-NOTICES.md): Kokoro-82M weights Apache-2.0 (hexgrad); the ONNX export and voice
files are onnx-community/Kokoro-82M-v1.0-ONNX (Apache-2.0); misaki Apache-2.0; spaCy + en_core_web_sm MIT;
num2words LGPL-2.1 (an unmodified, separately installed Python package).

Why not the kokoro-onnx package: it (and misaki[en]) requires espeakng-loader / phonemizer, which ship
the GPL-3.0 espeak-ng library. Dolmi runs the same ONNX model directly (a few lines below) and asks misaki
for phonemes with fallback=None, so a word misaki does not know is left out instead of going to espeak.

The model files come from Hugging Face into the same cache as the speech and translation models, through
the same netgate check, only when the user presses Download in Settings > Audio.

Streaming first audio: Kokoro is not autoregressive, so a line is split into a short first piece (up to
the first comma, or FIRST_WORDS words) and clauses after it; the first piece plays while the rest is made.
"""
import re
import threading
import time

import numpy as np

REPO = "onnx-community/Kokoro-82M-v1.0-ONNX"
MODEL_FILE = "onnx/model.onnx"                     # fp32, 325 MB. VOICE-10 measurement on the dev laptop
                                                   # (22 threads, Whisper busy on the GPU): fp32 synthesizes at
                                                   # RTF 0.30-0.45, the int8 file at 0.9-1.0, too slow to keep up
INT8_FILE = "onnx/model_quantized.onnx"            # 92 MB, what Dolmi 2.3.0 downloaded: still used when it is
                                                   # the only one there
THREADS = 4                                        # fp32: 4 threads RTF 0.30, 2 threads 0.45 ...
CPU_THREADS = 2                                    # ... but 2 while Whisper also runs on the CPU (4 slowed it)
VOICES = ("af_heart", "am_michael", "bf_emma")     # US woman (default), US man, UK woman
DEFAULT_VOICE = "af_heart"
COMMON = ["tokenizer.json", "config.json", *(f"voices/{v}.bin" for v in VOICES)]
FILES = [MODEL_FILE, *COMMON]
SIZE_MB = 327
SR = 24000
MAX_TOKENS = 510                                   # the style table has one row per input length
FIRST_WORDS = 6


def _hf():
    from huggingface_hub import try_to_load_from_cache
    return try_to_load_from_cache


def model_dir():
    """The downloaded snapshot folder, or None when a file is missing."""
    try:
        find = _hf()
    except ImportError:
        return None
    paths = [find(REPO, f) for f in COMMON]
    if not all(isinstance(p, str) for p in paths) or model_file(find) is None:
        return None
    from pathlib import Path
    return Path(paths[0]).parent                  # .../snapshots/<rev>/tokenizer.json -> snapshots/<rev>


def model_file(find=None):
    """The ONNX file to run: fp32 when downloaded, else the int8 file an older Dolmi fetched; None if neither."""
    find = find or _hf()
    for f in (MODEL_FILE, INT8_FILE):
        if isinstance(find(REPO, f), str):
            return f
    return None


def is_installed():
    return model_dir() is not None


def downloaded_mb():
    from pathlib import Path
    try:
        from huggingface_hub.constants import HF_HUB_CACHE
    except ImportError:
        return 0
    root = Path(HF_HUB_CACHE) / ("models--" + REPO.replace("/", "--"))
    if not root.exists():
        return 0
    return sum(f.stat().st_size for f in root.rglob("*") if f.is_file() and not f.is_symlink()) / 1e6


def download():
    import netgate
    netgate.check("download")                     # STR-7: refuses in local-only mode
    from huggingface_hub import snapshot_download
    snapshot_download(REPO, allow_patterns=FILES)


def deps_ok():
    """True when onnxruntime, misaki's English G2P and spaCy's small English model are installed. The
    model must already be there: misaki would otherwise download it itself, past netgate."""
    try:
        import onnxruntime  # noqa: F401
        import misaki.en  # noqa: F401
        import spacy
        return bool(spacy.util.is_package("en_core_web_sm"))
    except Exception:
        return False


def split_chunks(text, first_words=FIRST_WORDS):
    """A short first piece (fast first audio), then the rest split at clause punctuation."""
    text = " ".join((text or "").split())
    if not text:
        return []
    parts = [p.strip() for p in re.split(r"(?<=[,;:.!?])\s+", text) if p.strip()]
    first = parts[0].split()
    if len(first) > first_words + 2:              # no early comma: cut the first clause after a few words
        parts[0:1] = [" ".join(first[:first_words]), " ".join(first[first_words:])]
    return parts


def tokens(phonemes, vocab):
    """Phoneme string -> model ids; unknown symbols are dropped, long lines cut to MAX_TOKENS."""
    return [vocab[c] for c in phonemes if c in vocab][:MAX_TOKENS]


def _speed_ok(speed):
    return float(min(2.0, max(0.5, speed or 1.0)))


class KokoroVoice:
    """Backend with the tts.py interface plus `synth_chunks` (PCM for the headphone mix)."""
    name = "kokoro"
    speeds = True
    pcm = True

    def __init__(self, voice=DEFAULT_VOICE, folder=None, g2p=None, session=None):
        self.voice_name = voice if voice in VOICES else DEFAULT_VOICE
        self._folder, self._g2p, self._sess = folder, g2p, session
        self._styles = self._vocab = None
        self._lock = threading.Lock()
        self.player = None                        # set by the app: a mixer.Mixer that plays PCM
        self.threads = THREADS                    # the app sets CPU_THREADS when Whisper runs on the CPU

    def available(self):
        if self._sess is not None:
            return True
        return (self._folder or model_dir()) is not None and deps_ok()

    def _load(self):
        if self._vocab is not None:
            return
        import json
        folder = self._folder or model_dir()
        if folder is None:
            raise RuntimeError("the natural voice is not downloaded")
        tok = json.loads((folder / "tokenizer.json").read_text(encoding="utf-8"))
        self._vocab = tok["model"]["vocab"]
        self._styles = np.fromfile(folder / "voices" / f"{self.voice_name}.bin", dtype=np.float32).reshape(-1, 256)
        if self._sess is None:
            import onnxruntime as ort
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = self.threads
            name = model_file() if self._folder is None else next(
                (f for f in (MODEL_FILE, INT8_FILE) if (folder / f).is_file()), MODEL_FILE)
            self._sess = ort.InferenceSession(str(folder / name), opts, providers=["CPUExecutionProvider"])
        if self._g2p is None:
            from misaki import en
            g2p = en.G2P(trf=False, british=self.voice_name.startswith("b"), fallback=None, unk="")
            self._g2p = lambda t: g2p(t)[0]

    def synth(self, text, speed=1.0):
        """One piece of text -> float32 mono PCM at SR."""
        with self._lock:
            self._load()
            ids = tokens(self._g2p(text), self._vocab)
            if not ids:
                return np.zeros(0, np.float32)
            style = self._styles[min(len(ids), len(self._styles) - 1)][None, :]
            out = self._sess.run(None, {"input_ids": np.array([[0, *ids, 0]], np.int64), "style": style,
                                        "speed": np.array([_speed_ok(speed)], np.float32)})[0]
        return np.asarray(out, np.float32).reshape(-1)

    def synth_chunks(self, text, speed=1.0):
        """Yield (pcm, SR) piece by piece: the first piece comes back after one short synthesis."""
        for part in split_chunks(text):
            pcm = self.synth(part, speed)
            if pcm.size:
                yield pcm, SR

    def speak(self, text, speed=1.0):
        if self.player is None:
            raise RuntimeError("the natural voice needs Dolmi's audio player")
        for pcm, sr in self.synth_chunks(text, speed):
            self.player.play(pcm, sr)
        self.player.wait_tts()

    def first_audio(self, text):
        """(first piece, whole line) synthesis seconds, without playing."""
        t = time.perf_counter()
        first = None
        for _ in self.synth_chunks(text):
            if first is None:
                first = time.perf_counter() - t
        total = time.perf_counter() - t
        return (first if first is not None else total), total

    def cancel(self):
        if self.player is not None:
            self.player.stop_tts()

    def close(self):
        self.cancel()
