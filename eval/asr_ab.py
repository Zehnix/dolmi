"""QA-6: CPU speech A/B, the app's Whisper against NVIDIA Parakeet-TDT-0.6B-v3 (via onnx-asr).

Each side runs in its own Python: Whisper in the app venv (the real live_subs.Engine with the fictional
hotwords), Parakeet in a throwaway venv that has onnx-asr (never the app venv):

    python -m venv %LOCALAPPDATA%\\Dolmi-eval\\asr-venv
    %LOCALAPPDATA%\\Dolmi-eval\\asr-venv\\Scripts\\pip install "onnx-asr[cpu,hub]" numpy
    venv\\Scripts\\python eval/asr_ab.py run --backend whisper --set synthetic --out <folder>
    %LOCALAPPDATA%\\Dolmi-eval\\asr-venv\\Scripts\\python eval/asr_ab.py run --backend parakeet --set synthetic --out <folder>
    python eval/asr_ab.py compare <folder>

Component mode only (one clip per reference line): WER/CER, German term accuracy, real-time factor,
and "missed" = clips that came back empty. Parakeet takes no hotwords, so the term numbers show what
the Vocabulary's hotwords are worth. Per-clip text goes to <out> (default outside the checkout).
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

EVAL = Path(__file__).resolve().parent
ROOT = EVAL.parent
for _p in (str(EVAL), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import metrics  # noqa: E402

SR = 16000
PARAKEET = "nemo-parakeet-tdt-0.6b-v3"


def default_out():
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache")
    return base / "dolmi-eval" / "asr-ab"


def whisper_backend(model, device):
    from types import SimpleNamespace

    import run_eval
    args = SimpleNamespace(no_terms=False, translator="opus-de", model=model, device=device)
    eng = run_eval.make_engine(args, run_eval.load_manifest().get("terms") or [])

    def transcribe(audio):
        eng.context = ""
        return eng.transcribe(audio)[0]
    return transcribe


def parakeet_backend(quantization=None):
    import onnx_asr
    model = onnx_asr.load_model(PARAKEET, quantization=quantization)

    def transcribe(audio):
        return model.recognize(audio, sample_rate=SR) or ""
    return transcribe


def run_clips(transcribe, clips, log=print):
    """One row per clip: {'id', 'lang', 'ref', 'hyp', 'dur', 'secs', 'terms'}."""
    rows = []
    for k, c in enumerate(clips):
        audio = c["load"]()
        t = time.perf_counter()
        hyp = (transcribe(audio) or "").strip()
        rows.append({"id": c["id"], "lang": c["lang"], "ref": c["text"], "hyp": hyp,
                     "dur": round(len(audio) / SR, 3), "secs": round(time.perf_counter() - t, 3),
                     "terms": c.get("terms", [])})
        if (k + 1) % 50 == 0:
            log(f"  {k + 1}/{len(clips)}")
    return rows


def summarize(rows):
    de = [r for r in rows if r["lang"] == "de" and r["ref"]]
    audio = sum(r["dur"] for r in rows)
    out = {"clips": len(rows),
           "wer_de": metrics.wer([r["ref"] for r in de], [r["hyp"] for r in de], "de") if de else None,
           "cer_de": metrics.cer([r["ref"] for r in de], [r["hyp"] for r in de], "de") if de else None,
           "missed_pct": 100.0 * sum(1 for r in rows if r["ref"] and not r["hyp"]) / len(rows) if rows else None,
           "rtf": sum(r["secs"] for r in rows) / audio if audio else None,
           "secs_p50": metrics.percentile([r["secs"] for r in rows], 50),
           "secs_p95": metrics.percentile([r["secs"] for r in rows], 95)}
    found, expected, pct = metrics.term_accuracy((r["terms"], r["hyp"]) for r in de if r["terms"])
    out.update(terms_found=found, terms_expected=expected, term_acc_de=pct)
    return out


def compare(folder):
    lines = ["| set | backend | clips | WER de | CER de | missed % | terms de | RTF | p50 s | p95 s |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for f in sorted(Path(folder).glob("*.summary.json")):
        s = json.loads(f.read_text(encoding="utf-8"))

        def v(key, d=2):
            return "n/a" if s.get(key) is None else f"{s[key]:.{d}f}"
        lines.append(f"| {s['set']} | {s['backend']} | {s['clips']} | {v('wer_de')} | {v('cer_de')} | "
                     f"{v('missed_pct')} | {v('term_acc_de', 1)} | {v('rtf', 3)} | {v('secs_p50')} | {v('secs_p95')} |")
    return "\n".join(lines)


def main(argv=None):
    # live_subs.Engine prints emoji; a cp1252 Windows console would crash the whole run on them.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--backend", choices=("whisper", "parakeet"), required=True)
    r.add_argument("--set", default="synthetic")
    r.add_argument("--limit", type=int)
    r.add_argument("--model", default="small")
    r.add_argument("--device", default="cpu")
    r.add_argument("--quantization", default=None, help="parakeet: e.g. int8")
    r.add_argument("--out", default=str(default_out()))
    c = sub.add_parser("compare")
    c.add_argument("folder", nargs="?", default=str(default_out()))
    a = ap.parse_args(argv)
    if a.cmd == "compare":
        print(compare(a.folder))
        return 0
    import run_eval
    clips = run_eval.load_clips(a.set, [], limit=a.limit)
    t = time.perf_counter()
    fn = whisper_backend(a.model, a.device) if a.backend == "whisper" else parakeet_backend(a.quantization)
    load_s = time.perf_counter() - t
    rows = run_clips(fn, clips)
    name = a.backend + (f"-{a.model}" if a.backend == "whisper" else f"-{a.quantization or 'fp32'}")
    summary = {"set": a.set, "backend": name, "load_s": round(load_s, 1), **summarize(rows)}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{a.set}.{name}"
    (out / f"{stem}.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows), encoding="utf-8")
    (out / f"{stem}.summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
