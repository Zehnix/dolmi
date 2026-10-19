"""Speaker labels (beta, off by default): who said each line, as "Speaker 1", "Speaker 2" ... on this computer.

Each final line's audio becomes a voice embedding (3D-Speaker CAM++ English, an ONNX model run with the
onnxruntime Dolmi already ships, on Kaldi filterbank features from kaldi-native-fbank; no PyTorch), and an
online clusterer gives it the id of the closest known voice or a new one. Embeddings stay in memory and
are gone when the meeting ends; nothing about a voice is written to disk (see PRIVACY.md).

The worker calls Diarizer.assign(audio, start, end) -> (speaker id | None, confidence) for every final
(live_subs.UI_Q_CONTRACT); the id lands in the line's meta["speaker"].
"""
import hashlib
import os
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SR = 16000
# Official sherpa-onnx release of the 3D-Speaker model (Apache-2.0, trained on VoxCeleb), ~30 MB.
MODEL_FILE = "3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx"
MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
             + MODEL_FILE)
MODEL_SHA256 = "357a834f702b80161e5b981182c038e18553c1f2ca752ed6cec2052365d4129b"
MODEL_MB = 30

MAX_SPEAKERS = 8
MIN_SECONDS = 1.0          # shorter lines ("Ja.", "Genau.") carry too little voice: they keep the last speaker
MAX_EMBED_SECONDS = 10.0   # longer lines are embedded from their middle 10 s (bounds the CPU time)
MERGE_EVERY = 4            # a merge pass after every 4 assigned lines
LOW_CONF = 0.7             # below this the UI shows the label dimmed with a "?"
# Colours of Speaker 1..8 (readable on the Paper and the Ink theme).
COLOURS = ("#C0613A", "#3F7FA6", "#6E9A3A", "#9A5BA8", "#C4952E", "#3A9A8C", "#B5546E", "#6B7280")


@dataclass(frozen=True)
class Thresholds:
    match: float   # cosine >= match: the same voice (its centroid learns from the line)
    new: float     # cosine < new: a new voice; between new and match: the nearest voice, unsure, no learning
    merge: float   # two voices whose centroids are this close become one


# SPK-6: tuned on the test bench's synthetic voices (eval/data/speakers + synthetic sessions, 2-4 voices).
# speaker = Opus round-trip sessions (loopback audio is one codec-processed stream): 86.8 % of lines right;
# mic = clean, noisy and simulated-room sessions: 88.8 %. Those TTS voices are one voice at other rates,
# so harder to tell apart than real people; tuning on real meetings is still to do.
THRESHOLDS = {
    "speaker": Thresholds(match=0.60, new=0.50, merge=0.75),
    "mic": Thresholds(match=0.60, new=0.55, merge=0.75),
}


def colour(speaker_id):
    """The colour of a speaker id (1-based)."""
    try:
        return COLOURS[(int(speaker_id) - 1) % len(COLOURS)]
    except (TypeError, ValueError):
        return COLOURS[-1]


def default_name(speaker_id):
    """'Speaker 3' for an id; a string label (a name set by another producer) is its own name."""
    return speaker_id if isinstance(speaker_id, str) else f"Speaker {speaker_id}"


# ------------------------------------------------------------------ model file
def model_dir():
    """Next to the speech models: the Hugging Face cache folder (~/.cache/huggingface), never in git."""
    from huggingface_hub.constants import HF_HUB_CACHE
    return Path(HF_HUB_CACHE).parent / "dolmi-speakers"


def model_path():
    override = os.environ.get("DOLMI_SPEAKER_MODEL")
    return Path(override) if override else model_dir() / MODEL_FILE


def is_installed():
    return model_path().is_file()


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def download(timeout=60):
    """Fetch the model once (checksum-verified, written through a .part file). Returns its path."""
    path = model_path()
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".part")
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=timeout) as r, open(part, "wb") as f:
            while block := r.read(1 << 20):
                f.write(block)
        got = _sha256(part)
        if got != MODEL_SHA256:
            raise ValueError(f"speaker model checksum mismatch ({got})")
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)
    return path


# ------------------------------------------------------------------ embeddings
class Embedder:
    """Audio (16 kHz mono float32) -> unit-length voice embedding. Matches sherpa-onnx's extractor for
    this model: 80 Kaldi filterbank bins (no dither, no snip-edges, 20 Hz to Nyquist - 400 Hz), samples
    in [-1, 1], mean over time subtracted ("global-mean")."""

    def __init__(self, path=None, threads=2):
        import kaldi_native_fbank as knf
        import onnxruntime as ort
        self._knf = knf
        so = ort.SessionOptions()
        so.intra_op_num_threads, so.inter_op_num_threads = threads, 1
        so.log_severity_level = 3
        self._sess = ort.InferenceSession(str(path or model_path()), so, providers=["CPUExecutionProvider"])
        self._input = self._sess.get_inputs()[0].name
        opts = knf.FbankOptions()
        opts.frame_opts.dither = 0
        opts.frame_opts.snip_edges = False
        opts.frame_opts.samp_freq = SR
        opts.mel_opts.num_bins = 80
        opts.mel_opts.low_freq = 20
        opts.mel_opts.high_freq = -400
        self._opts = opts

    def features(self, audio):
        f = self._knf.OnlineFbank(self._opts)
        f.accept_waveform(SR, np.asarray(audio, np.float32))
        f.input_finished()
        n = f.num_frames_ready
        if n == 0:
            return np.zeros((0, 80), np.float32)
        feats = np.stack([f.get_frame(i) for i in range(n)]).astype(np.float32)
        return feats - feats.mean(axis=0, keepdims=True)

    def __call__(self, audio):
        audio = np.asarray(audio, np.float32)
        cap = int(MAX_EMBED_SECONDS * SR)
        if len(audio) > cap:
            mid = (len(audio) - cap) // 2
            audio = audio[mid:mid + cap]
        feats = self.features(audio)
        if len(feats) < 10:
            raise ValueError("too little audio for a voice embedding")
        e = self._sess.run(None, {self._input: feats[None]})[0][0]
        return unit(e)


def unit(v):
    v = np.asarray(v, np.float64)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


# ------------------------------------------------------------------ clustering
def _conf(sim, th, zone):
    """Confidence in 0..1 for a decision: match 0.75-1, grey zone 0.3-0.7, new voice 0.75-1."""
    if zone == "match":
        return round(0.75 + 0.25 * min(1.0, (sim - th.match) / max(1e-6, 1 - th.match)), 2)
    if zone == "grey":
        return round(0.3 + 0.4 * (sim - th.new) / max(1e-6, th.match - th.new), 2)
    return round(0.75 + 0.25 * min(1.0, max(0.0, (th.new - sim) / max(1e-6, th.new))), 2)


class Clusterer:
    """Online clustering of voice embeddings with running centroids (pure numpy, no model).

    assign(vec, seconds, th) -> (id, confidence):
      - lines shorter than MIN_SECONDS keep the previous speaker (unsure);
      - cosine to the nearest centroid >= th.match: that speaker, and the centroid learns the line;
      - < th.new: a new speaker (at most MAX_SPEAKERS; then the nearest, unsure);
      - in between (grey zone): the nearest speaker, unsure, the centroid does not learn;
      - every MERGE_EVERY lines, speakers whose centroids are >= th.merge alike merge (the higher id
        goes into the lower one); on_merge(old, new) is called for each.
    Ids are 1, 2, 3 ... and never reused."""

    def __init__(self, max_speakers=MAX_SPEAKERS, merge_every=MERGE_EVERY, on_merge=None):
        self.max_speakers, self.merge_every, self.on_merge = max_speakers, merge_every, on_merge
        self.sums, self.counts = {}, {}   # id -> sum of unit embeddings, number of lines learnt
        self.alias = {}                   # merged id -> the id it became
        self.last = None
        self._next = 1
        self._since_merge = 0

    def centroid(self, sid):
        return unit(self.sums[sid])

    def resolve(self, sid):
        while sid in self.alias:
            sid = self.alias[sid]
        return sid

    def new_speaker(self, vec=None):
        """A fresh id; without `vec` it has no voice yet (the user's "someone new")."""
        sid = self._next
        self._next += 1
        if vec is not None:
            self.sums[sid], self.counts[sid] = unit(vec), 1
        return sid

    def _nearest(self, vec):
        best, sim = None, -2.0
        for sid in self.sums:
            s = float(np.dot(self.centroid(sid), vec))
            if s > sim:
                best, sim = sid, s
        return best, sim

    def assign(self, vec, seconds, th):
        if seconds < MIN_SECONDS and self.last is not None:
            return self.last, 0.5
        vec = unit(vec)
        best, sim = self._nearest(vec)
        if best is None:
            sid, conf = self.new_speaker(vec), 1.0
        elif sim >= th.match:
            self.sums[best] = self.sums[best] + vec
            self.counts[best] += 1
            sid, conf = best, _conf(sim, th, "match")
        elif sim < th.new and len(self.sums) < self.max_speakers:
            sid, conf = self.new_speaker(vec), _conf(sim, th, "new")
        elif sim < th.new:
            sid, conf = best, 0.3                  # no room for another voice
        else:
            sid, conf = best, _conf(sim, th, "grey")
        self._since_merge += 1
        if self._since_merge >= self.merge_every:
            self._since_merge = 0
            self.merge_pass(th)
            sid = self.resolve(sid)
        self.last = sid
        return sid, conf

    def merge(self, old, new):
        """Fold speaker `old` into `new` (the user's "same person", or the merge pass)."""
        old, new = self.resolve(old), self.resolve(new)
        if old == new:
            return new
        if old in self.sums:
            if new in self.sums:
                self.sums[new] = self.sums[new] + self.sums.pop(old)
                self.counts[new] += self.counts.pop(old)
            else:
                self.sums[new], self.counts[new] = self.sums.pop(old), self.counts.pop(old)
        self.alias[old] = new
        if self.last == old:
            self.last = new
        if self.on_merge:
            self.on_merge(old, new)
        return new

    def merge_pass(self, th):
        while True:
            ids = sorted(self.sums)
            pair = next(((a, b) for i, a in enumerate(ids) for b in ids[i + 1:]
                         if float(np.dot(self.centroid(a), self.centroid(b))) >= th.merge), None)
            if pair is None:
                return
            self.merge(pair[1], pair[0])


class Diarizer:
    """What the worker calls: assign(audio, start, end) -> (speaker id | None, confidence).
    mode: "speaker" | "mic", or a callable returning it (the audio source can change mid-meeting).
    Never raises: a line it cannot embed keeps the previous speaker, or no label."""

    def __init__(self, embed, mode="speaker", on_merge=None, clusterer=None):
        self.embed = embed
        self.mode = mode
        self.clusters = clusterer or Clusterer(on_merge=on_merge)
        self.ms = []                       # embedding times (ms), for the bench and the log
        self._lock = threading.Lock()

    def thresholds(self):
        m = self.mode() if callable(self.mode) else self.mode
        return THRESHOLDS.get(m, THRESHOLDS["speaker"])

    def assign(self, audio, start=None, end=None):
        seconds = len(audio) / SR
        with self._lock:
            c = self.clusters
            if seconds < MIN_SECONDS and c.last is not None:
                return c.assign(None, seconds, self.thresholds())
            try:
                t = time.perf_counter()
                vec = self.embed(audio)
                self.ms.append(1000 * (time.perf_counter() - t))
            except Exception as e:             # one odd buffer must not cost the line its caption
                print(f"Speaker embedding failed ({seconds:.1f} s): {e!r}")
                return (c.last, 0.3) if c.last is not None else (None, None)
            return c.assign(vec, seconds, self.thresholds())

    def merge(self, old, new):
        with self._lock:
            return self.clusters.merge(old, new)

    def new_speaker(self):
        with self._lock:
            return self.clusters.new_speaker()


def load_diarizer(mode="speaker", on_merge=None, threads=2):
    """The ready Diarizer (downloads the ~30 MB model on first use)."""
    return Diarizer(Embedder(download(), threads=threads), mode=mode, on_merge=on_merge)


def make_diarizer(settings, mode="speaker", on_merge=None):
    """None unless the speaker_labels setting is on (then nothing is loaded or downloaded)."""
    if not settings.get("speaker_labels"):
        return None
    return load_diarizer(mode=mode, on_merge=on_merge)
