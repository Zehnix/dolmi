"""VOICE-0a timing harness: how long from the end of a German sentence until its English voice plays.

Replays the bench's synthetic clips (eval/make_synthetic.py) utterance by utterance through
capture -> VAD close -> ASR -> MT -> TTS first sample -> playback start, and writes one JSON record per
utterance with ISO 8601 UTC timestamps for every stage, plus p50/p95 per stage.

    python -m voice.timing --limit 40 --model small --device cpu --tts windows --out eval/results/voice-timing.json

ASR, MT and TTS are measured on this machine. Capture (one 100 ms chunk) and VAD close (the app's
pause-to-finalize) are what the live pipeline waits by design, and playback start is a nominal output
buffer; those three are marked "simulated" in the output. --tts none leaves TTS and playback out.
eval/results/ is git-ignored.

VOICE-9 (--clauses): end of a clause -> first audio on the clause path (voice/clauses.py). Each sample
sentence is fed word by word as committed German; per spoken clause the record has commit (simulated:
the LocalAgreement policy lag, about one draft update), mt (measured, translate_prefix), tts_first
(measured) and playback (simulated).

    python -m voice.timing --clauses --device cpu --tts os --out eval/results/voice-clauses.json
"""
import argparse
import json
import sys
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
STAGES = ("capture", "vad_close", "asr", "mt", "tts_first", "playback")
SIMULATED = {"capture", "vad_close", "playback", "commit"}
CAPTURE_CHUNK = 0.1        # s, live_subs.STEP / SR
VAD_CLOSE = 0.6            # s, live_subs.PAUSE_TO_FINALIZE
PLAYBACK_BUFFER = 0.04     # s, a typical shared-mode output buffer
COMMIT_LAG = 1.0           # s, LocalAgreement-2: a word commits one draft update (DRAFT_EVERY) after it is heard
CLAUSE_STAGES = ("commit", "mt", "tts_first", "playback")
CLAUSE_SAMPLES = (        # generic meeting German (no company terms), with commas and conjunctions
    "Ich habe den Bericht gestern geschickt, aber die Zahlen fehlen noch.",
    "Wir sollten das Projekt verschieben, weil das Team im Moment zu klein ist.",
    "Wenn die Tests morgen grün sind, können wir am Freitag veröffentlichen.",
    "Die Kunden haben angerufen, und sie möchten ein Angebot bis Montag.",
    "Ich glaube, dass wir das Budget für das nächste Quartal erhöhen müssen.",
    "Das Meeting dauert eine Stunde, danach machen wir eine kurze Pause.",
)


def iso(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def record(uid, durations, speech_end, **extra):
    """One utterance: stage durations (s, None = not run) -> cumulative UTC timestamps after speech end."""
    t, stamps = speech_end, {"speech_end": iso(speech_end)}
    for stage in STAGES:
        d = durations.get(stage)
        if d is None:
            continue
        t += d
        stamps[stage] = iso(t)
    return {"id": uid, "timestamps": stamps,
            "durations": {s: round(durations[s], 4) for s in STAGES if durations.get(s) is not None},
            "total": round(t - speech_end, 4), **extra}


def percentile(values, p):
    return float(np.percentile(values, p)) if values else None


def summary(records, stages=STAGES):
    out = {}
    for stage in (*stages, "total"):
        vals = [r["total"] if stage == "total" else r["durations"][stage] for r in records
                if stage == "total" or stage in r["durations"]]
        if vals:
            out[stage] = {"p50": round(percentile(vals, 50), 4), "p95": round(percentile(vals, 95), 4),
                          "n": len(vals), "simulated": stage in SIMULATED}
    return out


def utterances(limit=None):
    """(id, German audio float32 16 kHz, reference) for the synthetic clips' German utterances."""
    sys.path.insert(0, str(ROOT / "eval"))
    import paths
    folder = paths.data_dir() / "synthetic"
    n = 0
    for side in sorted(folder.glob("syn-s*.json")):
        meta = json.loads(side.read_text(encoding="utf-8"))
        with wave.open(str(side.with_suffix(".wav"))) as w:
            audio = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
            sr = w.getframerate()
        for u in meta["utterances"]:
            if u["lang"] != "de":
                continue
            yield u["id"], audio[max(0, int((u["start"] - 0.3) * sr)):int(u["end"] * sr)], u
            n += 1
            if limit and n >= limit:
                return


def run(items, eng, backend=None, clock=time.time, perf=time.perf_counter):
    """Time each utterance; `eng` has transcribe(audio) -> (text, lang) and translate(text, lang)."""
    records = []
    for uid, audio, ref in items:
        speech_end = clock()
        d = {"capture": CAPTURE_CHUNK, "vad_close": VAD_CLOSE}
        t = perf()
        de, lang = eng.transcribe(audio)
        d["asr"] = perf() - t
        t = perf()
        en = eng.translate(de, lang) if de else ""
        d["mt"] = perf() - t
        if backend is not None and en:
            d["tts_first"] = backend.first_audio(en)[0]
            d["playback"] = PLAYBACK_BUFFER
        records.append(record(uid, d, speech_end, de=de, en=en, ref_en=ref.get("en", ""),
                              audio_s=round(len(audio) / 16000, 2)))
    return records


def run_clauses(sentences, translate, backend=None, clock=time.time, perf=time.perf_counter,
                commit_lag=COMMIT_LAG):
    """VOICE-9: time each spoken clause. translate(src_de, prefix_en) -> English (translate_prefix)."""
    from voice.clauses import ClauseSpeaker
    records = []
    for n, de in enumerate(sentences):
        pieces = []
        sp = ClauseSpeaker(translate, lambda en, end: pieces.append(en), clock=clock, perf=perf,
                           on_timing=lambda t: timings.append(t))
        timings, words = [], de.split()
        for i in range(1, len(words) + 1):
            sp.draft(n, " ".join(words[:i]))
        sp.final(n, de)
        for k, t in enumerate(timings):
            if not t["en"]:
                continue
            d = {"commit": commit_lag, "mt": t["mt"]}
            if backend is not None:
                d["tts_first"] = backend.first_audio(t["en"])[0]
                d["playback"] = PLAYBACK_BUFFER
            records.append({"id": f"s{n}c{k}", "kind": t["kind"], "words": t["words"], "en": t["en"], "de": de,
                            "durations": {s: round(v, 4) for s, v in d.items()},
                            "total": round(sum(d.values()), 4)})
    return records


def main_clauses(args):
    sys.path[:0] = [str(ROOT), str(ROOT / "src")]
    import live_subs
    from voice import tts
    eng = live_subs.Engine(args.model, args.device, language="de")
    backend = tts.select_backend() if args.tts == "os" else None
    tr = lambda src, prefix: eng.translate_prefix(src, "de", prefix)
    tr("Hallo zusammen.", "")                    # warm-up
    if backend:
        backend.first_audio("Hello.")
    recs = run_clauses(CLAUSE_SAMPLES, tr, backend)
    out = {"created": iso(time.time()), "device": eng.device, "tts": getattr(backend, "name", None),
           "summary": summary(recs, CLAUSE_STAGES), "clauses": recs}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for stage, s in out["summary"].items():
        print(f"{stage:10} p50 {s['p50']*1000:7.0f} ms  p95 {s['p95']*1000:7.0f} ms  n={s['n']}")
    for r in recs:
        print(f"  {r['kind']:5} {r['words']:2} words -> {r['en']}")
    if backend:
        backend.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--model", default="small")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--tts", default="os", help="os (Windows System.Speech / Mac say) or none")
    ap.add_argument("--out", default=str(ROOT / "eval" / "results" / "voice-timing.json"))
    ap.add_argument("--clauses", action="store_true", help="VOICE-9: time the clause-by-clause path")
    args = ap.parse_args(argv)
    if args.clauses:
        return main_clauses(args)
    sys.path[:0] = [str(ROOT), str(ROOT / "src")]
    import live_subs
    import models
    from voice import tts
    model = str(models.candidate_dir(args.model)) if args.model in getattr(models, "CANDIDATES", {}) else args.model
    eng = live_subs.Engine(model, args.device, language="de")
    backend = tts.select_backend() if args.tts == "os" else None
    items = list(utterances(args.limit))
    if items:                                   # warm-up: first calls load kernels and voices
        eng.translate(eng.transcribe(items[0][1])[0] or "Hallo.", "de")
        if backend:
            backend.first_audio("Hello.")
    recs = run(items, eng, backend)
    out = {"created": iso(time.time()), "model": eng.model_name, "device": eng.device,
           "tts": getattr(backend, "name", None), "voice": getattr(backend, "voice_name", None),
           "summary": summary(recs), "utterances": recs}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for stage, s in out["summary"].items():
        print(f"{stage:10} p50 {s['p50']*1000:7.0f} ms  p95 {s['p95']*1000:7.0f} ms  n={s['n']}"
              + ("  (simulated)" if s["simulated"] else ""))
    if backend:
        backend.close()


if __name__ == "__main__":
    main()
