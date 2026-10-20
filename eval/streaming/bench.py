"""STR-10 streaming benchmark: the same audio through several Dolmi configurations, one table.

    python eval/streaming/bench.py                       # default matrix, synthetic set, 2 sessions
    python eval/streaming/bench.py --set fleurs --limit 1 --configs cpu-small-old,cuda-turbo-str1

Each config is one `eval/run_eval.py --mode stream` run in its own process (so the environment switch
DOLMI_STABLE_DRAFTS and the device take effect cleanly). From its results file this script adds the
streaming numbers the harness does not summarise itself:
  committed rewrites  a draft whose committed words do not extend the previous ones (a front trimmed
                      out of the STR-1 window and an empty reset do not count)
  tail churn          tentative words changed between consecutive drafts, per draft
and the GPU memory peak (nvidia-smi polled every 0.5 s; whole-GPU, so other apps count too).
Reference projects (WhisperLiveKit, SimulStreaming) are not run here: they need torch in a separate venv.
The table goes to eval/streaming/results/<time>.md (git-ignored with eval/results); numbers only.
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVAL = HERE.parent
RESULTS = EVAL / "results"

# name -> (model, device, DOLMI_STABLE_DRAFTS)
CONFIGS = {
    "cpu-small-old": ("small", "cpu", "0"),
    "cpu-small-str1": ("small", "cpu", "1"),
    "cuda-small-old": ("small", "cuda", "0"),
    "cuda-small-str1": ("small", "cuda", "1"),
    "cuda-turbo-old": ("large-v3-turbo", "cuda", "0"),
    "cuda-turbo-str1": ("large-v3-turbo", "cuda", "1"),
}
DEFAULT = "cpu-small-old,cpu-small-str1,cuda-small-old,cuda-small-str1,cuda-turbo-old,cuda-turbo-str1"


def extends(prev, cur):
    """cur keeps prev's committed words: prev (minus a front that STR-1 trimmed out of the window) is
    a prefix of cur. An empty cur is a reset (new window), not a rewrite."""
    if not prev:
        return True
    return any(cur[:len(prev) - d] == prev[d:] for d in range(len(prev)) if len(prev) - d <= len(cur))


def draft_flicker(events):
    """(committed rewrites, mean tail churn) from the draft stream_events of one run."""
    rewrites = churn = n = 0
    prev_c, prev_t, prev_u = None, None, None
    for e in events:
        item = e.get("item")
        if not isinstance(item, list) or not item or item[0] not in ("draft", "final"):
            continue
        if item[0] == "final":
            prev_c = prev_t = None
            continue
        meta = next((x for x in item[1:] if isinstance(x, dict)), {})
        if "committed" not in meta:
            continue
        c, t = str(meta.get("committed") or "").split(), str(meta.get("tentative") or "").split()
        if meta.get("utt") != prev_u:
            prev_c = prev_t = None
            prev_u = meta.get("utt")
        if prev_c is not None:
            if c and not extends(prev_c, c):
                rewrites += 1
            churn += sum(1 for a, b in zip(prev_t, t) if a != b) + abs(len(prev_t) - len(t))
            n += 1
        prev_c, prev_t = c, t
    return rewrites, (churn / n if n else None)


class GpuPeak:
    def __init__(self):
        self.peak, self._stop = None, threading.Event()

    def _poll(self):
        while not self._stop.is_set():
            try:
                out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                                     capture_output=True, text=True, timeout=5).stdout.split()
                if out:
                    self.peak = max(self.peak or 0, int(out[0]))
            except (OSError, ValueError, subprocess.SubprocessError):
                return
            self._stop.wait(0.5)

    def __enter__(self):
        self._t = threading.Thread(target=self._poll, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join(timeout=10)


def run_one(name, args):
    model, device, stable = CONFIGS[name]
    env = {**os.environ, "DOLMI_STABLE_DRAFTS": stable}
    before = set(RESULTS.glob("*.jsonl")) if RESULTS.exists() else set()
    cmd = [sys.executable, str(EVAL / "run_eval.py"), "--mode", "stream", "--set", args.set,
           "--model", model, "--device", device, "--no-terms", "--note", f"STR-10 {name}"]
    if args.limit:
        cmd += ["--limit", str(args.limit)]
    t0 = time.time()
    with GpuPeak() as gpu:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    new = sorted(set(RESULTS.glob("*.jsonl")) - before)
    if proc.returncode != 0 or not new:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        return {"config": name, "error": " | ".join(tail) or f"exit {proc.returncode}"}
    rows = [json.loads(line) for line in new[-1].read_text(encoding="utf-8").splitlines() if line.strip()]
    stream = next((r["stream"] for r in rows if r.get("kind") == "summary"), {}) or {}
    rewrites, churn = draft_flicker([r for r in rows if r.get("kind") == "stream_event"])
    return {"config": name, "model": model, "device": device, "str1": stable == "1",
            "stream_wer": stream.get("stream_wer"), "missed": stream.get("missed_rate"),
            "first_p50": stream.get("first_text_latency_p50"), "first_p95": stream.get("first_text_latency_p95"),
            "final_p50": stream.get("final_latency_p50"), "final_p95": stream.get("final_latency_p95"),
            "rtf": stream.get("rtf"), "rewrites": rewrites, "churn": churn,
            "vram_peak_mb": gpu.peak if device == "cuda" else None, "wall_s": round(time.time() - t0)}


def fmt(v, nd=2):
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    return f"{v:.{nd}f}" if isinstance(v, float) else str(v)


def table(results, args):
    head = ["config", "STR-1", "stream WER %", "missed %", "first text p50/p95 s", "final lag p50/p95 s",
            "RTF", "committed rewrites", "tail churn", "GPU MB peak"]
    lines = [f"# STR-10 streaming bench ({args.set}, limit {args.limit or 'all'}, {datetime.now():%Y-%m-%d %H:%M})",
             "", "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in results:
        if "error" in r:
            lines.append(f"| {r['config']} | failed: {r['error'][:120]} |" + " |" * (len(head) - 2))
            continue
        lines.append("| " + " | ".join([
            r["config"], fmt(r["str1"]), fmt(r["stream_wer"]), fmt(r["missed"], 1),
            f"{fmt(r['first_p50'])}/{fmt(r['first_p95'])}", f"{fmt(r['final_p50'])}/{fmt(r['final_p95'])}",
            fmt(r["rtf"]), fmt(r["rewrites"]), fmt(r["churn"]), fmt(r["vram_peak_mb"])]) + " |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", default="synthetic")
    ap.add_argument("--limit", type=int, default=2, help="first N sessions (0 = all)")
    ap.add_argument("--configs", default=DEFAULT, help="comma list of: " + ", ".join(CONFIGS))
    args = ap.parse_args(argv)
    names = [c.strip() for c in args.configs.split(",") if c.strip()]
    unknown = [c for c in names if c not in CONFIGS]
    if unknown:
        ap.error(f"unknown config(s): {', '.join(unknown)}")
    results = []
    for name in names:
        print(f"[STR-10] {name} ...", flush=True)
        results.append(run_one(name, args))
        print(f"[STR-10] {name}: {results[-1]}", flush=True)
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    text = table(results, args)
    path = out / f"{datetime.now():%Y%m%d-%H%M%S}.md"
    path.write_text(text, encoding="utf-8")
    (out / f"{path.stem}.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(text)
    print(f"Table: {path}")


if __name__ == "__main__":
    main()
