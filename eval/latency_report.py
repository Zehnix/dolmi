"""p50/p95 per stage of one day's latency log (LAT-1), and where the English time goes.

    venv\\Scripts\\python.exe eval\\latency_report.py              # today (UTC)
    venv\\Scripts\\python.exe eval\\latency_report.py 2026-10-26   # that day
    venv\\Scripts\\python.exe eval\\latency_report.py path\\to\\latency-2026-10-26.jsonl

The file is %LOCALAPPDATA%\\Dolmi\\latency-YYYY-MM-DD.jsonl (~/Library/Logs/Dolmi on a Mac): one row per
final line with its stage deltas in ms (keys ending in _ms). No text is in it.

Rows written since 2026-11-16 also carry the pipeline stamps (vad_close, asr_start, asr_done, mt_start, mt_done)
and the hold fields (held_ms, parts, joined_held_ms, release). For those rows a second table splits speech end ->
English into its stages, so a slow day shows whether the time went to the pause, the queue, Whisper, the hold or
the translator. Older rows have no stamps and are left out of that table.
"""
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# speech end -> English shown, in pipeline order: (label, row key)
BREAKDOWN = (("pause that ends the line", "speech_end_to_vad_close_ms"),
             ("queue wait", "vad_close_to_asr_start_ms"),
             ("Whisper final decode", "asr_start_to_asr_done_ms"),
             ("hold / join", "asr_done_to_mt_start_ms"),
             ("translation", "mt_start_to_mt_done_ms"),
             ("to the screen", "mt_done_to_english_shown_ms"))


def percentile(values, q):
    """Nearest-rank percentile of `values` (q in 0..100)."""
    s = sorted(values)
    k = max(0, min(len(s) - 1, math.ceil(q / 100 * len(s)) - 1))
    return s[k]


def stages(rows):
    """{stage key: [ms, ...]} over every *_ms field in `rows`."""
    out = {}
    for row in rows:
        for k, v in row.items():
            if k.endswith("_ms") and isinstance(v, (int, float)):
                out.setdefault(k, []).append(v)
    return out


def read_rows(path):
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue                 # a line cut off by a crash: skip it
    return rows


def breakdown(rows):
    """Lines for the stage split of rows that have the pipeline stamps ([] when none has them)."""
    stamped = [r for r in rows if isinstance(r.get("asr_start_to_asr_done_ms"), (int, float))]
    if not stamped:
        return []
    total = [r["speech_end_to_english_shown_ms"] for r in stamped
             if isinstance(r.get("speech_end_to_english_shown_ms"), (int, float))]
    mean_total = sum(total) / len(total) if total else 0
    lines = ["", f"Where the English time goes ({len(stamped)} lines with stage stamps)",
             f"{'stage':28} {'p50 ms':>8} {'p95 ms':>8} {'share':>6}"]
    for label, key in BREAKDOWN:
        v = [r[key] for r in stamped if isinstance(r.get(key), (int, float))]
        if v:
            share = f"{100 * sum(v) / len(v) / mean_total:5.0f}%" if mean_total else "     -"
            lines.append(f"{label:28} {percentile(v, 50):8} {percentile(v, 95):8} {share:>6}")
    if total:
        lines.append(f"{'speech end -> English':28} {percentile(total, 50):8} {percentile(total, 95):8}")
    held = [r["held_ms"] for r in stamped if r.get("held_ms")]
    lines.append(f"held lines: {len(held)} of {len(stamped)} ({100 * len(held) / len(stamped):.0f}%)"
                 + (f", held p50 {percentile(held, 50)} ms, p95 {percentile(held, 95)} ms" if held else ""))
    reasons = {}
    for r in stamped:
        if r.get("release"):
            reasons[r["release"]] = reasons.get(r["release"], 0) + 1
    if reasons:
        lines.append("released by: " + ", ".join(f"{k} {v}" for k, v in sorted(reasons.items())))
    joined = [r for r in stamped if (r.get("parts") or 1) > 1]
    if joined:
        waits = [r.get("joined_held_ms", 0) for r in joined]
        lines.append(f"joined lines: {len(joined)}, earlier parts waited p50 {percentile(waits, 50)} ms, "
                     f"p95 {percentile(waits, 95)} ms")
    return lines


def report(rows):
    lines = [f"{len(rows)} lines", f"{'stage':40} {'n':>5} {'p50 ms':>8} {'p95 ms':>8}"]
    for k, v in sorted(stages(rows).items()):
        lines.append(f"{k[:-3]:40} {len(v):5} {percentile(v, 50):8} {percentile(v, 95):8}")
    return "\n".join(lines + breakdown(rows))


def default_path(day):
    import dolmi_platform
    return Path(dolmi_platform.log_dir()) / f"latency-{day}.jsonl"


def main(argv):
    arg = argv[0] if argv else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = Path(arg) if arg.endswith(".jsonl") else default_path(arg)
    if not path.exists():
        print(f"No latency log at {path}")
        return 1
    print(report(read_rows(path)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
