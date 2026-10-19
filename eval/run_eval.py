"""Dolmi test bench: one command scores transcription, translation, lost lines and latency.

    python eval/run_eval.py                                   # synthetic set, both modes, CPU
    python eval/run_eval.py --mode component --set fleurs --limit 200
    python eval/run_eval.py --model large-v3-turbo --device cuda --write-baseline

Modes
  component  ASR on each reference clip -> WER/CER; MT on the gold German -> chrF/BLEU; MT on the ASR
             output (cascade); term accuracy for the fictional term list; real-time factor.
  stream     plays whole sessions through live_subs.worker() in 100 ms chunks with the real Engine on a
             simulated clock (idle waiting is skipped, compute time counts, audio never runs ahead of a busy
             ASR thread), then measures missed-segment rate, clipped first words, mid-word cuts and
             latency per stage.

Sets: synthetic (eval/make_synthetic.py), fleurs (eval/fetch_public.py), private:<folder> (your own
sessions in the same sidecar format; nothing from it is ever written to the repo).
Results: eval/results/<run>.jsonl (one row per clip/utterance, text only, never audio) and
eval/results/<run>.md (the summary table, also printed). See eval/README.md.
"""
import argparse
import inspect
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time as _time
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

EVAL = Path(__file__).resolve().parent
ROOT = EVAL.parent
for _p in (str(EVAL), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import metrics  # noqa: E402

SR = 16000
CHUNK_S = 0.1
MANIFEST = EVAL / "manifest.json"
BASELINE = EVAL / "baseline.json"
RESULTS = EVAL / "results"


# ================================================================== data
def read_wav(path):
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"{path}: expected 16-bit PCM WAV")
        a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        if w.getnchannels() > 1:
            a = a.reshape(-1, w.getnchannels()).mean(axis=1)
        if w.getframerate() != SR:
            n = int(len(a) * SR / w.getframerate())
            a = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.float32)
        return a


def load_manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {"sets": {}, "terms": []}


def _sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_sessions(set_name, warnings):
    """[{'id', 'path', 'utterances': [...], 'duration'}] for a stream-capable set."""
    if set_name.startswith("private:"):
        folder = Path(set_name.split(":", 1)[1]).expanduser()
        out = []
        for side in sorted(folder.glob("*.json")):
            entry = json.loads(side.read_text(encoding="utf-8"))
            wav = folder / Path(entry.get("file", side.with_suffix(".wav").name)).name
            if "utterances" in entry and wav.exists():
                out.append({**entry, "path": wav})
        if not out:
            raise SystemExit(f"No sessions in {folder}: expected <name>.wav + <name>.json (see eval/README.md)")
        return out
    if set_name == "fleurs":
        return [stitch_clips("fleurs-stitched", load_clips("fleurs", warnings, limit=60), warnings)]   # ~13 min
    man = load_manifest()
    listed = man.get("sets", {}).get(set_name)
    if not listed or "sessions" not in listed:
        reason = (listed or {}).get("reason", "build it first (eval/README.md)")
        raise SystemExit(f"Set '{set_name}' has no sessions in eval/manifest.json: {reason}")
    out = []
    for s in listed["sessions"]:
        wav = EVAL / s["file"]
        side = wav.with_suffix(".json")
        if not wav.exists():
            raise SystemExit(f"{wav} is missing: run  python eval/make_synthetic.py")
        entry = json.loads(side.read_text(encoding="utf-8")) if side.exists() else s
        if _sha256(wav) != s["sha256"]:
            warnings.append(f"{wav.name} differs from the manifest (other TTS voices or seed?): "
                            "scores are not comparable with baseline.json")
        out.append({**entry, "path": wav})
    return out


def load_clips(set_name, warnings, limit=None):
    """[{'id', 'audio' (loader), 'text', 'lang', 'en', 'terms', 'en_terms', 'tags'}] for component mode."""
    if set_name == "fleurs":
        index = EVAL / "data" / "fleurs" / "de_de" / "test" / "index.jsonl"
        if not index.exists():
            raise SystemExit(f"{index} is missing: run  python eval/fetch_public.py")
        clips = []
        for line in index.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            path = EVAL / row["file"]
            clips.append({"id": row["id"], "load": (lambda p=path: read_wav(p)), "text": row["text"],
                          "lang": "de", "en": row.get("en"), "terms": [], "en_terms": [], "tags": ["fleurs"]})
            if limit and len(clips) >= limit:
                break
        return clips
    clips = []
    for sess in load_sessions(set_name, warnings):
        cache = {}

        def session_audio(p=sess["path"]):
            if p not in cache:
                cache[p] = read_wav(p)
            return cache[p]
        utts = sess["utterances"]
        for k, u in enumerate(utts):
            before = u["start"] - (utts[k - 1]["end"] if k else 0.0)
            after = (utts[k + 1]["start"] if k + 1 < len(utts) else u["end"] + 1.0) - u["end"]
            a = max(0.0, u["start"] - min(0.25, before / 2))
            b = u["end"] + min(0.25, after / 2)
            clips.append({**u, "load": (lambda a=a, b=b, f=session_audio: f()[int(a * SR):int(b * SR)]),
                          "en": u.get("en")})
            if limit and len(clips) >= limit:
                return clips
    return clips


def stitch_clips(sid, clips, warnings, pause=1.0):
    """Turn single clips (FLEURS) into one session with known timings, so stream mode can run on them."""
    pieces, utts, t = [np.zeros(SR, np.float32)], [], 1.0
    for c in clips:
        a = c["load"]()
        utts.append({k: v for k, v in c.items() if k != "load"} | {"start": round(t, 3), "end": round(t + len(a) / SR, 3)})
        pieces += [a, np.zeros(int(pause * SR), np.float32)]
        t += len(a) / SR + pause
    pieces.append(np.zeros(2 * SR, np.float32))
    return {"id": sid, "audio": np.concatenate(pieces), "utterances": utts, "duration": t + 2}


# ================================================================== engine
def term_files(folder, terms):
    """Fictional glossary.txt + vocabulary.txt for the Engine (never the user's real ones)."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "glossary.txt").write_text("\n".join(t["de"] for t in terms) + "\n", encoding="utf-8")
    keep = [t["de"] for t in terms if t["kind"] != "german"]
    german = [f"{t['de']} = {t['en']}" for t in terms if t["kind"] == "german"]
    (folder / "vocabulary.txt").write_text(
        "[keep]\n" + "\n".join(keep) + "\n\n[german]\n" + "\n".join(german) + "\n", encoding="utf-8")


def make_engine(args, terms):
    """The app's real live_subs.Engine with the fictional term files and the chosen translator."""
    cfg = EVAL / "data" / "config"
    if args.no_terms:
        cfg.mkdir(parents=True, exist_ok=True)
        for name in ("glossary.txt", "vocabulary.txt"):
            (cfg / name).unlink(missing_ok=True)
    else:
        term_files(cfg, terms)
    os.environ["DOLMI_CONFIG"] = str(cfg)
    import live_subs
    eng = live_subs.Engine(args.model, args.device, language="de")
    if args.translator not in ("", "default", "opus-de"):
        eng.translators["opus-de"] = load_translator(args.translator, eng.device)
    return eng


def load_translator(name, device):
    """A CTranslate2 Marian model for German -> English: a models.py key or a Hugging Face repo id."""
    import ctranslate2
    import sentencepiece as spm
    from huggingface_hub import snapshot_download
    import models
    repo = models.BY_KEY[name].repo if name in models.BY_KEY else name
    d = Path(snapshot_download(repo))
    return (ctranslate2.Translator(str(d), device=device, compute_type="int8"),
            spm.SentencePieceProcessor(model_file=str(d / "source.spm")),
            spm.SentencePieceProcessor(model_file=str(d / "target.spm")))


class Timed:
    """Wraps an engine and records how long each transcribe()/translate() call takes."""

    def __init__(self, eng):
        object.__setattr__(self, "_eng", eng)
        object.__setattr__(self, "calls", {"asr": [], "mt": []})

    def transcribe(self, audio):
        t = _time.perf_counter()
        out = self._eng.transcribe(audio)
        self.calls["asr"].append(_time.perf_counter() - t)
        return out

    def translate(self, text, lang):
        t = _time.perf_counter()
        out = self._eng.translate(text, lang)
        self.calls["mt"].append(_time.perf_counter() - t)
        return out

    def __getattr__(self, name):
        return getattr(self._eng, name)

    def __setattr__(self, name, value):
        setattr(self._eng, name, value)


# ================================================================== component mode
def run_component(eng, clips, rows, log=print):
    """ASR + MT on single clips. Appends one row per clip to `rows`; returns the summary dict."""
    asr_time = audio_time = 0.0
    for k, c in enumerate(clips):
        audio = c["load"]()
        eng.context = ""   # clips are independent: no previous-sentence prompt
        t = _time.perf_counter()
        hyp, lang = eng.transcribe(audio)
        asr_time += _time.perf_counter() - t
        audio_time += len(audio) / SR
        row = {"kind": "asr", "id": c["id"], "lang": c["lang"], "tags": c.get("tags", []), "ref": c["text"],
               "hyp": hyp, "detected": lang, "dur": round(len(audio) / SR, 2),
               "wer": round(metrics.wer(c["text"], hyp, c["lang"]), 2) if c["text"] else None}
        if c.get("en") and c["lang"] != "en":
            row["mt_gold"] = eng.translate(c["text"], c["lang"])
            row["mt_asr"] = eng.translate(hyp, lang) if hyp else ""
            row["en_ref"] = c["en"]
        row["terms"], row["en_terms"] = c.get("terms", []), c.get("en_terms", [])
        rows.append(row)
        if (k + 1) % 25 == 0:
            log(f"  component {k + 1}/{len(clips)}")
    return summarize_component(rows, asr_time, audio_time)


def summarize_component(rows, asr_time=None, audio_time=None):
    asr = [r for r in rows if r["kind"] == "asr"]
    out = {"clips": len(asr)}
    for lang in ("de", "en"):
        rs = [r for r in asr if r["lang"] == lang]
        if rs:
            out[f"wer_{lang}"] = metrics.wer([r["ref"] for r in rs], [r["hyp"] for r in rs], lang)
            out[f"cer_{lang}"] = metrics.cer([r["ref"] for r in rs], [r["hyp"] for r in rs], lang)
            out[f"clips_{lang}"] = len(rs)
    tags = sorted({t for r in asr for t in r.get("tags", [])})
    out["wer_by_tag"] = {t: round(metrics.wer([r["ref"] for r in asr if t in r["tags"]],
                                              [r["hyp"] for r in asr if t in r["tags"]], "de"), 2)
                         for t in tags}
    found, expected, acc = metrics.term_accuracy(((r["terms"], r["hyp"]) for r in asr), "de")
    out["term_acc_asr"] = acc
    mt = [r for r in asr if "mt_gold" in r]
    if mt:
        refs = [r["en_ref"] for r in mt]
        out["chrf_gold"] = metrics.chrf(refs, [r["mt_gold"] for r in mt])
        out["bleu_gold"] = metrics.bleu(refs, [r["mt_gold"] for r in mt])
        out["chrf_cascade"] = metrics.chrf(refs, [r["mt_asr"] for r in mt])
        out["bleu_cascade"] = metrics.bleu(refs, [r["mt_asr"] for r in mt])
        out["term_acc_mt"] = metrics.term_accuracy(((r["en_terms"], r["mt_gold"]) for r in mt), "en")[2]
        out["mt_pairs"] = len(mt)
    if asr_time is not None and audio_time:
        out["asr_rtf"] = asr_time / audio_time
    return out


def run_comet(python, rows):
    """COMET (Unbabel/wmt22-comet-da) in a separate venv that has unbabel-comet; None + reason if not."""
    mt = [r for r in rows if r.get("mt_gold")]
    if not python or not mt:
        return None, "COMET skipped: pass --comet-python <venv python with unbabel-comet>"
    data = [{"src": r["ref"], "mt": r["mt_gold"], "ref": r["en_ref"]} for r in mt]
    script = ("import json,sys\nfrom comet import download_model, load_from_checkpoint\n"
              "m=load_from_checkpoint(download_model('Unbabel/wmt22-comet-da'))\n"
              "d=json.load(open(sys.argv[1],encoding='utf-8'))\n"
              "print(json.dumps({'score': m.predict(d, batch_size=16, gpus=0).system_score}))\n")
    with tempfile.TemporaryDirectory() as tmp:
        inp = Path(tmp) / "comet.json"
        inp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        try:
            res = subprocess.run([python, "-c", script, str(inp)], capture_output=True, text=True, timeout=3600)
            return 100 * json.loads(res.stdout.strip().splitlines()[-1])["score"], None
        except Exception as e:
            return None, f"COMET skipped: {type(e).__name__}: {e}"


# ================================================================== stream mode
class AsrWatch:
    """Finds the worker's ASR thread ('dolmi-asr', started by worker() since wave 1) and tells whether it
    is idle: blocked in jobs.get() with no job queued. Without such a thread (the single-threaded
    worker before wave 1, or a fake worker) the worker itself is the only compute, so it is always idle."""

    NAME = "dolmi-asr"

    def __init__(self):
        self.before = {t.ident for t in threading.enumerate()}
        self.thread = None

    def find(self):
        if self.thread is None:
            self.thread = next((t for t in threading.enumerate()
                                if t.name == self.NAME and t.ident not in self.before), None)
        return self.thread

    def idle(self):
        th = self.find()
        if th is None or not th.is_alive():
            return True
        frame = sys._current_frames().get(th.ident)
        if frame is None:
            return True
        # the innermost Python frame must be threading's Condition.wait under queue.get, and the
        # queue empty: right after a pop the thread is out of wait(), and before it the queue is full
        if frame.f_code.co_name != "wait" or not frame.f_code.co_filename.endswith("threading.py"):
            return False
        f, in_get = frame, False
        while f is not None:
            if f.f_code.co_name == "get" and f.f_code.co_filename.endswith("queue.py"):
                in_get = True
                q = f.f_locals.get("self")
                if q is not None and hasattr(q, "empty") and not q.empty():
                    return False
            f = f.f_back
        return in_get

    def join(self, timeout=60.0):
        th = self.find()
        if th is not None:
            th.join(timeout)


class SimAudio:
    """The worker's audio queue on a simulated clock. Hands out 100 ms chunks; while every thread of the
    worker waits (for audio, or the ASR thread for a job) the clock jumps to when the next chunk would
    arrive, and while anything computes it runs at wall speed (times `compute_scale`). So latency
    includes ASR/MT time, idle waiting costs nothing, and the audio never runs ahead of a busy ASR
    thread. At the end of the audio, stop is set only once the ASR thread has no job left, because
    the worker discards finals that come out after Stop."""

    def __init__(self, audio, stop, chunk_s=CHUNK_S, compute_scale=1.0, idle=None, poll=0.002):
        self.audio, self.stop = audio, stop
        self.chunk = int(SR * chunk_s)
        self.pos, self.t = 0, 0.0
        self.scale = compute_scale
        self.idle = idle or (lambda: True)
        self.poll = poll
        self.mark = _time.perf_counter()
        self.lock = threading.Lock()   # the worker and its ASR thread both read the clock
        self.batch_start = 0   # audio position where the chunks of the latest get() began
        self.log = []          # (clock value handed out, batch_start): maps worker timestamps to audio

    def _tick(self):
        with self.lock:
            now = _time.perf_counter()
            self.t += (now - self.mark) * self.scale
            self.mark = now
            return self.t

    def clock(self):
        t = self._tick()
        self.log.append((t, self.batch_start))
        return t

    def now(self):
        """The current simulated time, for our own records (not logged as a worker timestamp)."""
        return self._tick()

    def audio_at(self, stamp):
        """Audio time (s) where the buffer began that the worker stamped `stamp` (a value clock() returned)."""
        import bisect
        k = bisect.bisect_right(self.log, (stamp, float("inf"))) - 1
        return self.log[k][1] / SR if k >= 0 else 0.0

    def contract_time(self, stamp):
        """Audio time (s) of a UI_Q_CONTRACT time (stream start + sample / SR). The worker anchors the
        stream at clock() - first chunk = 0 on this clock, so it already is audio time."""
        return stamp

    def audio_time(self):
        return self.pos / SR

    def _take(self):
        end = min(self.pos + self.chunk, len(self.audio))
        chunk, self.pos = self.audio[self.pos:end], end
        return chunk

    def _arrival(self):
        return min(self.pos + self.chunk, len(self.audio)) / SR

    def _ready(self):
        return self.pos < len(self.audio) and self._arrival() <= self.t

    def get(self, block=True, timeout=None):
        waited = _time.perf_counter()
        while True:
            self._tick()
            if self.pos >= len(self.audio):
                if self.idle():                 # every job done: now the session may stop
                    self.stop.set()
                    raise queue.Empty
            elif self._ready():
                break
            elif self.idle():                   # nobody computes: skip the wait for the next chunk
                with self.lock:
                    self.t = max(self.t, self._arrival())
                break
            if timeout is not None and _time.perf_counter() - waited >= timeout:
                raise queue.Empty
            _time.sleep(self.poll)              # the ASR thread computes: real time passes
        self.batch_start = self.pos
        return self._take()

    def get_nowait(self):
        self._tick()
        if not self._ready():
            raise queue.Empty
        return self._take()

    def empty(self):
        self._tick()
        return not self._ready()

    def qsize(self):
        self._tick()
        n = 0
        while self.pos + (n + 1) * self.chunk <= min(len(self.audio), int(self.t * SR)):
            n += 1
        return n


class _SimTime:
    """Stands in for the `time` module inside live_subs when worker() has no clock parameter."""

    def __init__(self, clock):
        self._clock = clock

    def time(self):
        return self._clock()

    monotonic = perf_counter = time

    def sleep(self, seconds):
        pass

    def __getattr__(self, name):
        return getattr(_time, name)


class Recorder:
    """ui_q / Transcript stand-in: remembers every item with the simulated time and audio position."""

    def __init__(self, sim):
        self.sim, self.items, self.adds, self.n, self.save = sim, [], [], 0, True

    def put(self, item, *a, **kw):
        self.items.append((self.sim.now(), self.sim.audio_time(), item))

    put_nowait = put

    def add(self, *args, **kw):
        self.n += 1
        self.adds.append((self.sim.now(), self.sim.audio_time(), args, kw))

    def __getattr__(self, name):   # any other Transcript method: accept and ignore
        return lambda *a, **kw: None


def parse_item(item):
    """(kind, de, en, meta) from a ui_q item: ('final', de, en[, meta]) or a dict with 'kind'/'type'."""
    if isinstance(item, dict):
        return item.get("kind") or item.get("type"), item.get("de", ""), item.get("en", ""), item
    if not isinstance(item, (tuple, list)) or not item:
        return None, "", "", {}
    meta = next((x for x in item if isinstance(x, dict)), {})
    texts = [x for x in item[1:] if isinstance(x, str)]
    return item[0], (texts + ["", ""])[0], (texts + ["", ""])[1], meta


def run_stream(eng, session, worker=None, detect=None, compute_scale=1.0, realtime=False, speed=1.0):
    """Play one session through worker(); returns (summary dict, per-utterance rows)."""
    import live_subs
    worker = worker or live_subs.worker
    audio = session["audio"] if "audio" in session else read_wav(session["path"])
    audio = np.concatenate([audio, np.zeros(2 * SR, np.float32)])
    stop = threading.Event()
    watch = AsrWatch()
    sim = SimAudio(audio, stop, compute_scale=compute_scale, idle=watch.idle)
    rec = Recorder(sim)
    drops = []
    params = inspect.signature(worker).parameters
    kwargs = {}
    if detect is not None:
        kwargs["detect"] = detect
    if "on_drop" in params:
        kwargs["on_drop"] = lambda *a, **kw: drops.append({"args": [str(x)[:200] for x in a],
                                                            **{k: str(v)[:200] for k, v in kw.items()}})
    clock_mode = "injected" if "clock" in params else "patched"
    wall = _time.perf_counter()
    if realtime:
        clock_mode = f"realtime x{speed}"
        run_realtime(worker, eng, audio, rec, sim, stop, kwargs, speed, watch)
    elif "clock" in params:
        worker(eng, sim, rec, rec, stop, clock=sim.clock, **kwargs)
        watch.join()                       # the ASR thread ends after the jobs already queued
    else:
        saved = live_subs.time
        live_subs.time = _SimTime(sim.clock)
        try:
            worker(eng, sim, rec, rec, stop, **kwargs)
            watch.join()
        finally:
            live_subs.time = saved
    wall = _time.perf_counter() - wall
    summary, rows = score_stream(session, rec, drops)
    summary["_events"] = [{"kind": "stream_event", "t": round(t, 3), "audio": round(apos, 3),
                           "item": [x if isinstance(x, (str, int, float, dict, type(None))) else str(x) for x in item]
                           if isinstance(item, (tuple, list)) else item} for t, apos, item in rec.items]
    summary["_events"] += [{"kind": "stream_add", "t": round(t, 3), "audio": round(apos, 3),
                            "args": [x if isinstance(x, (str, int, float)) else str(x) for x in args]}
                           for t, apos, args, _ in rec.adds]
    summary.update({"clock": clock_mode, "on_drop": "on_drop" in params, "wall_s": round(wall, 1),
                    "audio_s": len(audio) / SR,
                    "rtf": wall / (len(audio) / SR)})
    return summary, rows


def run_realtime(worker, eng, audio, rec, sim, stop, kwargs, speed, watch=None, drain_timeout=120.0):
    """Fallback: feed audio in (scaled) real time through a real queue on a thread. After the last
    chunk it waits until the worker has taken every chunk and its ASR thread has no job left, and only
    then sets stop (finals that come out after Stop never reach ui_q)."""
    watch = watch or AsrWatch()
    q = queue.Queue()
    t0 = _time.time()
    sim.clock = sim.now = lambda: (_time.time() - t0) * speed
    sim.audio_time = lambda: fed[0] / SR
    sim.audio_at = lambda stamp: (stamp - t0) * speed   # the worker stamps with the real time.time()
    sim.contract_time = lambda stamp: stamp - t0        # contract times: stream start + sample / SR
    fed = [0]
    th = threading.Thread(target=worker, args=(eng, q, rec, rec, stop), kwargs=kwargs, daemon=True)
    th.start()
    step = int(SR * CHUNK_S)
    for i in range(0, len(audio), step):
        q.put(audio[i:i + step])
        fed[0] = min(i + step, len(audio))
        _time.sleep(CHUNK_S / speed)
    deadline = _time.time() + drain_timeout
    while _time.time() < deadline and not q.empty():
        _time.sleep(0.05)
    _time.sleep(0.5)                       # the worker cuts the last sentence on the trailing silence
    while _time.time() < deadline and not watch.idle():
        _time.sleep(0.05)
    stop.set()
    th.join(timeout=30)
    watch.join(timeout=30)


def score_stream(session, rec, drops):
    utts = session["utterances"]
    finals, drafts = [], []
    add_iter = iter(rec.adds)
    for t_emit, apos, item in rec.items:
        kind, de, en, meta = parse_item(item)
        if kind == "final":
            add = next(add_iter, None)
            start, end = meta.get("start"), meta.get("end")
            if "id" in meta:
                # UI_Q_CONTRACT: start/end are stream start + sample / SR, i.e. audio time already.
                # The span ends at the speech end, not where the audio was at emission: under a
                # backlog a final comes out long after its sentence and must not cover the next one.
                start = apos if start is None else rec.sim.contract_time(start)
                cut = apos if end is None else rec.sim.contract_time(end)
                span_end = min(cut, apos)
            else:
                # older workers: tr.add(start, end, ...) or meta times stamped with the clock when
                # the buffer was taken; the line spans up to where the audio was at emission
                if start is None and add and len(add[2]) >= 2 and isinstance(add[2][0], (int, float)):
                    start, end = add[2][0], add[2][1]
                start = apos if start is None else rec.sim.audio_at(start)
                cut = apos if end is None or not meta else end
                span_end = apos
            start = min(start, span_end)
            finals.append({"t": t_emit, "start": start, "end": span_end, "cut": cut, "de": de, "en": en,
                           "id": meta.get("id")})
        elif kind == "draft" and de:
            drafts.append({"t": t_emit, "apos": apos})
    # a revised final (same id) replaces the text; the first emission time stays
    merged, by_id = [], {}
    for f in finals:
        if f["id"] is not None and f["id"] in by_id:
            by_id[f["id"]].update(de=f["de"], en=f["en"], end=max(by_id[f["id"]]["end"], f["end"]))
            continue
        merged.append(f)
        if f["id"] is not None:
            by_id[f["id"]] = f
    finals = merged

    # ---- time overlap: a reference utterance with no overlapping final is missed
    rows = []
    lat_final, lat_first = [], []
    for u in utts:
        over = [f for f in finals if f["start"] < u["end"] and f["end"] > u["start"]]
        row = {"kind": "stream_utt", "id": u["id"], "tags": u.get("tags", []), "ref": u["text"],
               "missed": not over}
        if over:
            cover = next((f for f in over if f["end"] >= u["end"]), over[-1])
            lat_final.append(cover["t"] - u["end"])
            row["final_latency"] = round(cover["t"] - u["end"], 3)
        first = [d["t"] for d in drafts if d["apos"] >= u["start"] and d["t"] >= u["start"]]
        first += [f["t"] for f in over]
        if first:
            lat_first.append(min(first) - u["start"])
            row["first_text_latency"] = round(min(first) - u["start"], 3)
        rows.append(row)

    # ---- text alignment: which reference words came out at all, and were first words clipped?
    ref_words, ref_utt = [], []
    for k, u in enumerate(utts):
        ws = metrics.normalize(u["text"], "de").split()
        ref_words += ws
        ref_utt += [k] * len(ws)
    hyp_words = metrics.normalize(" ".join(f["de"] for f in finals), "de").split()
    pairs = metrics.align(ref_words, hyp_words)
    correct = [False] * len(ref_words)
    for i, j in pairs:
        if i is not None and j is not None and ref_words[i] == hyp_words[j]:
            correct[i] = True
    per_utt = {}
    for i, k in enumerate(ref_utt):
        per_utt.setdefault(k, []).append(correct[i])
    clipped = eligible = lost_text = 0
    for k, flags in per_utt.items():
        rows[k]["words_recovered"] = round(sum(flags) / len(flags), 3)
        if not any(flags):
            lost_text += 1
        elif len(flags) >= 2 and not rows[k]["missed"]:
            eligible += 1
            if not flags[0] and any(flags[1:]):
                clipped += 1
                rows[k]["first_word_clipped"] = True

    # ---- cuts: where did each final end, relative to the words being spoken?
    words = [w for u in utts for w in u.get("words", [])]
    mid_word = mid_utt = 0
    for f in finals[:-1]:
        c = f["cut"]
        if any(s + 0.03 < c < e - 0.03 for _, s, e in words):
            mid_word += 1
        if any(u["start"] + 0.1 < c < u["end"] - 0.1 for u in utts):
            mid_utt += 1
    n_cuts = max(1, len(finals) - 1)
    missed = sum(r["missed"] for r in rows)
    reasons = {}
    for d in drops:
        key = (d.get("reason") or (d["args"][0] if d["args"] else "?"))[:40]
        reasons[key] = reasons.get(key, 0) + 1
    summary = {
        "utterances": len(utts), "finals": len(finals), "drafts": len(drafts),
        "missed_rate": 100 * missed / max(1, len(utts)),
        "missed_by_tag": _rate_by_tag(rows, "missed"),
        "lost_text_rate": 100 * lost_text / max(1, len(per_utt)),
        "word_recall": 100 * sum(correct) / max(1, len(correct)),
        "stream_wer": metrics.wer(" ".join(u["text"] for u in utts), " ".join(f["de"] for f in finals), "de"),
        "clipped_first_word_rate": 100 * clipped / max(1, eligible),
        "mid_word_cut_rate": 100 * mid_word / n_cuts if words else None,
        "mid_utterance_cut_rate": 100 * mid_utt / n_cuts,
        "final_latency_p50": metrics.percentile(lat_final, 50), "final_latency_p95": metrics.percentile(lat_final, 95),
        "first_text_latency_p50": metrics.percentile(lat_first, 50),
        "first_text_latency_p95": metrics.percentile(lat_first, 95),
        "drops": len(drops), "drop_reasons": reasons,
    }
    return summary, rows


def _rate_by_tag(rows, key):
    tags = sorted({t for r in rows for t in r["tags"]})
    return {t: round(100 * sum(r[key] for r in rows if t in r["tags"]) / sum(t in r["tags"] for r in rows), 1)
            for t in tags}


def combine_stream(parts):
    """Weighted totals over sessions (rates by utterance/final counts, latencies re-pooled)."""
    if not parts:
        return {}
    out = {"sessions": len(parts)}
    n_u = sum(p["utterances"] for p in parts)
    out["utterances"], out["finals"] = n_u, sum(p["finals"] for p in parts)
    for key in ("missed_rate", "lost_text_rate", "word_recall", "stream_wer", "clipped_first_word_rate"):
        out[key] = sum(p[key] * p["utterances"] for p in parts) / max(1, n_u)
    for key in ("mid_word_cut_rate", "mid_utterance_cut_rate"):
        vals = [(p[key], p["finals"]) for p in parts if p[key] is not None]
        out[key] = sum(v * n for v, n in vals) / max(1, sum(n for _, n in vals)) if vals else None
    lat = {k: [r[k] for p in parts for r in p["_rows"] if k in r] for k in ("final_latency", "first_text_latency")}
    for k, vals in lat.items():
        out[f"{k}_p50"], out[f"{k}_p95"] = metrics.percentile(vals, 50), metrics.percentile(vals, 95)
    tags = {}
    for p in parts:
        for r in p["_rows"]:
            for t in r["tags"]:
                tags.setdefault(t, []).append(r["missed"])
    out["missed_by_tag"] = {t: round(100 * sum(v) / len(v), 1) for t, v in sorted(tags.items())}
    out["drops"] = sum(p["drops"] for p in parts)
    reasons = {}
    for p in parts:
        for k, v in p["drop_reasons"].items():
            reasons[k] = reasons.get(k, 0) + v
    out["drop_reasons"] = reasons
    out["rtf"] = sum(p["wall_s"] for p in parts) / max(1e-9, sum(p["audio_s"] for p in parts))
    out["clock"], out["on_drop"] = parts[0]["clock"], parts[0]["on_drop"]
    return out


# ================================================================== report
def fmt(v, digits=1, unit=""):
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.{digits}f}{unit}"
    return f"{v}{unit}"


def summary_table(run):
    c, s = run.get("component") or {}, run.get("stream") or {}
    lines = [f"### Dolmi test bench: {run['set']} · {run['model']} on {run['device']} · {run['translator']}",
             "", f"{run['date']} · git {run['git']} · {run.get('note', '')}".rstrip(" ·"), "",
             "| Area | Metric | Value |", "|---|---|---|"]
    if c:
        lines += [
            f"| ASR | WER German (normalized) | {fmt(c.get('wer_de'), 2, ' %')} |",
            f"| ASR | CER German | {fmt(c.get('cer_de'), 2, ' %')} |",
            f"| ASR | WER English lines | {fmt(c.get('wer_en'), 2, ' %')} |",
            f"| ASR | Term accuracy (fictional terms) | {fmt(c.get('term_acc_asr'), 1, ' %')} |",
            f"| ASR | Real-time factor (clips) | {fmt(c.get('asr_rtf'), 3)} |",
            f"| MT | chrF on gold German | {fmt(c.get('chrf_gold'), 2)} |",
            f"| MT | BLEU on gold German | {fmt(c.get('bleu_gold'), 2)} |",
            f"| MT | chrF on ASR output (cascade) | {fmt(c.get('chrf_cascade'), 2)} |",
            f"| MT | BLEU on ASR output (cascade) | {fmt(c.get('bleu_cascade'), 2)} |",
            f"| MT | Term accuracy in English | {fmt(c.get('term_acc_mt'), 1, ' %')} |",
            f"| MT | COMET wmt22-da | {fmt(c.get('comet'), 2) if c.get('comet') is not None else c.get('comet_note', 'n/a')} |",
        ]
    if s:
        lines += [
            f"| Stream | Missed-segment rate (no overlapping final) | {fmt(s.get('missed_rate'), 1, ' %')} |",
            f"| Stream | Lines with no word recovered | {fmt(s.get('lost_text_rate'), 1, ' %')} |",
            f"| Stream | Word recall / stream WER | {fmt(s.get('word_recall'), 1, ' %')} / {fmt(s.get('stream_wer'), 1, ' %')} |",
            f"| Stream | Clipped first word | {fmt(s.get('clipped_first_word_rate'), 1, ' %')} |",
            f"| Stream | Mid-word cuts (per final) | {fmt(s.get('mid_word_cut_rate'), 1, ' %')} |",
            f"| Stream | Mid-utterance cuts (per final) | {fmt(s.get('mid_utterance_cut_rate'), 1, ' %')} |",
            f"| Stream | End of speech → final, p50 / p95 | {fmt(s.get('final_latency_p50'), 2, ' s')} / {fmt(s.get('final_latency_p95'), 2, ' s')} |",
            f"| Stream | Speech start → first text, p50 / p95 | {fmt(s.get('first_text_latency_p50'), 2, ' s')} / {fmt(s.get('first_text_latency_p95'), 2, ' s')} |",
            f"| Stream | ASR call p50 / p95 | {fmt(s.get('asr_call_p50'), 2, ' s')} / {fmt(s.get('asr_call_p95'), 2, ' s')} |",
            f"| Stream | MT call p50 / p95 | {fmt(s.get('mt_call_p50'), 3, ' s')} / {fmt(s.get('mt_call_p95'), 3, ' s')} |",
            f"| Stream | Real-time factor (wall / audio) | {fmt(s.get('rtf'), 3)} |",
            f"| Stream | Drops logged (on_drop) | {s.get('drops') if s.get('on_drop') else 'n/a (worker has no on_drop)'} |",
            f"| Stream | Clock | {s.get('clock')} · {s.get('sessions')} sessions · {s.get('utterances')} lines |",
        ]
        if s.get("missed_by_tag"):
            lines += ["", "Missed by line type: " + ", ".join(f"{k} {v} %" for k, v in s["missed_by_tag"].items())]
        if s.get("drop_reasons"):
            lines += ["", "Drop reasons: " + ", ".join(f"{k} {v}" for k, v in s["drop_reasons"].items())]
    if c.get("wer_by_tag"):
        lines += ["", "WER by line type: " + ", ".join(f"{k} {v} %" for k, v in c["wer_by_tag"].items())]
    lines += ["", "Tags: S status, Q question, C code-switching, E English, V verb-final, B backchannel, "
              "M/ME monologue > 12 s, N numbers; soft_start, quiet, long = audio conditions."]
    for w in run.get("warnings", []):
        lines.append(f"\n> {w}")
    return "\n".join(lines) + "\n"


def git_rev():
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, timeout=10).stdout.strip() or "?"
    except Exception:
        return "?"


def numbers_only(d):
    """Baseline copy of a summary: numbers (and small dicts of numbers) only, no text."""
    out = {}
    for k, v in d.items():
        if k.startswith("_"):
            continue
        if isinstance(v, float):
            out[k] = round(v, 3)
        elif isinstance(v, (int, bool)) or v is None:
            out[k] = v
        elif isinstance(v, dict) and all(isinstance(x, (int, float)) for x in v.values()):
            out[k] = {a: round(b, 3) if isinstance(b, float) else b for a, b in v.items()}
        elif k == "clock":
            out[k] = v
    return out


def write_baseline(run):
    base = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {
        "about": "Test bench numbers (no text, no audio). Key: set|model|device|translator|terms. "
                 "Re-run the same command to compare; see eval/README.md.", "runs": {}}
    key = "|".join([run["set"], run["model"], run["device"], run["translator"], "terms" if run["terms"] else "no-terms"])
    base["runs"][key] = {"date": run["date"], "git": run["git"], "command": run["command"],
                         "component": numbers_only(run.get("component") or {}),
                         "stream": numbers_only(run.get("stream") or {})}
    BASELINE.write_text(json.dumps(base, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return key


def warm_up(eng, set_name, warnings):
    """One transcription + translation before timing anything: the first CUDA call loads kernels for
    seconds, which would otherwise land on the first session's latency."""
    clip = load_clips(set_name, warnings, limit=1)
    audio = clip[0]["load"]() if clip else (0.05 * np.sin(np.arange(2 * SR) / 7)).astype(np.float32)
    eng.transcribe(audio)
    eng.translate("Guten Morgen zusammen.", "de")
    eng.context = ""
    eng.calls["asr"].clear()
    eng.calls["mt"].clear()


# ================================================================== main
def main(argv=None):
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="Dolmi test bench", formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n\n", 1)[1])
    ap.add_argument("--mode", choices=("component", "stream", "both"), default="both")
    ap.add_argument("--set", default="synthetic", help="synthetic | fleurs | private:<folder>")
    ap.add_argument("--model", default="small", help="speech model (models.py key, e.g. small, large-v3-turbo)")
    ap.add_argument("--device", default="cpu", choices=("cpu", "cuda", "auto"))
    ap.add_argument("--translator", default="opus-de",
                    help="opus-de (the app's) or another CTranslate2 Marian model: models.py key or HF repo")
    ap.add_argument("--limit", type=int, help="component: first N clips; stream: first N sessions")
    ap.add_argument("--no-terms", action="store_true", help="run without the fictional glossary/vocabulary")
    ap.add_argument("--comet", action="store_true", help="also score COMET (needs --comet-python)")
    ap.add_argument("--comet-python", default=os.environ.get("DOLMI_COMET_PYTHON"),
                    help="python.exe of a separate venv with unbabel-comet (never the app venv)")
    ap.add_argument("--realtime", action="store_true", help="stream: feed in real time instead of simulated time")
    ap.add_argument("--speed", type=float, default=1.0, help="with --realtime: playback speed")
    ap.add_argument("--compute-scale", type=float, default=1.0,
                    help="stream: count compute time x this (e.g. 2 = pretend the PC is twice as slow)")
    ap.add_argument("--write-baseline", action="store_true", help="store the numbers in eval/baseline.json")
    ap.add_argument("--note", default="", help="free text shown under the table title")
    args = ap.parse_args(argv)

    warnings = []
    man = load_manifest()
    terms = man.get("terms") or []
    started = datetime.now(timezone.utc)
    run = {"set": args.set if not args.set.startswith("private:") else "private", "model": args.model,
           "device": args.device, "translator": args.translator, "terms": not args.no_terms,
           "date": started.strftime("%Y-%m-%dT%H:%M:%SZ"), "git": git_rev(), "note": args.note,
           "command": "python eval/run_eval.py " + " ".join(a if not a.startswith("private:") else "private:<folder>"
                                                           for a in (argv if argv is not None else sys.argv[1:]))}
    print(f"Loading {args.model} on {args.device} ...")
    eng = Timed(make_engine(args, terms))
    run["device"] = getattr(eng, "device", args.device) if args.device == "auto" else args.device
    warm_up(eng, args.set, warnings)
    rows = []
    if args.mode in ("component", "both"):
        clips = load_clips(args.set, warnings, args.limit)
        print(f"Component mode: {len(clips)} clips")
        run["component"] = run_component(eng, clips, rows)
        if args.comet:
            score, note = run_comet(args.comet_python, rows)
            run["component"]["comet"] = score
            if note:
                run["component"]["comet_note"] = note
                warnings.append(note)
    if args.mode in ("stream", "both"):
        sessions = load_sessions(args.set, warnings)[:args.limit] if args.limit else load_sessions(args.set, warnings)
        parts = []
        eng.calls["asr"].clear()
        eng.calls["mt"].clear()
        for k, sess in enumerate(sessions):
            print(f"Stream mode: session {k + 1}/{len(sessions)} {sess['id']} ({sess.get('duration', 0) / 60:.1f} min)")
            eng.context = ""
            summary, srows = run_stream(eng, sess, compute_scale=args.compute_scale,
                                        realtime=args.realtime, speed=args.speed)
            summary["_rows"] = srows
            parts.append(summary)
            for r in srows + summary["_events"]:
                rows.append({**r, "session": sess["id"]})
            print(f"  missed {summary['missed_rate']:.1f} %, final latency p50 {fmt(summary['final_latency_p50'], 2)} s, "
                  f"rtf {summary['rtf']:.2f}")
        run["stream"] = combine_stream(parts)
        for stage in ("asr", "mt"):
            run["stream"][f"{stage}_call_p50"] = metrics.percentile(eng.calls[stage], 50)
            run["stream"][f"{stage}_call_p95"] = metrics.percentile(eng.calls[stage], 95)
    run["warnings"] = warnings

    RESULTS.mkdir(parents=True, exist_ok=True)
    name = f"{started:%Y%m%d-%H%M%S}_{run['set']}_{args.model}_{run['device']}"
    with (RESULTS / f"{name}.jsonl").open("w", encoding="utf-8") as f:
        f.write(json.dumps({"kind": "run", **{k: v for k, v in run.items() if k not in ("component", "stream")}},
                           ensure_ascii=False) + "\n")
        for r in rows:
            f.write(json.dumps({"run": name, **r}, ensure_ascii=False) + "\n")
        f.write(json.dumps({"kind": "summary", "component": numbers_only(run.get("component") or {}),
                            "stream": numbers_only(run.get("stream") or {})}, ensure_ascii=False) + "\n")
    table = summary_table(run)
    (RESULTS / f"{name}.md").write_text(table, encoding="utf-8")
    print("\n" + table)
    print(f"Results: {RESULTS / name}.jsonl / .md")
    if args.write_baseline:
        print(f"Baseline updated: {write_baseline(run)}")
    return run


if __name__ == "__main__":
    main()
