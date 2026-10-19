"""VOICE-0c: first-audio latency of the text-to-speech candidates on the same English lines.

    python -m voice.bench_tts                       # OS voices (Windows Zira/Hazel, Mac 'say')
    <python with kokoro installed> voice/bench_tts.py --kokoro   # also Kokoro-82M (Apache-2.0)

Kokoro is timed to its first generated chunk (what a streaming player could start on). Prints a table and
JSON lines; numbers only, no audio is kept.
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINES = ["Yes.", "The use case of Velorix is pretty similar.",
         "We should move the release to next week because the tests are not finished yet.",
         "Can you send me the numbers for the third quarter before Friday?",
         "I think that works, but let's check with the team in Munich first."]


def _row(name, firsts, totals, note=""):
    return {"backend": name, "first_p50": round(statistics.median(firsts), 3), "first_max": round(max(firsts), 3),
            "total_p50": round(statistics.median(totals), 3), "lines": len(firsts), "note": note}


def bench_os():
    sys.path[:0] = [str(ROOT), str(ROOT / "src")]
    from voice import tts
    rows = []
    if tts.WindowsSpeech().available():
        for voice in ("Microsoft Zira Desktop", "Microsoft Hazel Desktop"):
            b = tts.WindowsSpeech(voice)
            b.first_audio("Hello.")                      # warm-up (starts PowerShell)
            res = [b.first_audio(t) for t in LINES]
            rows.append(_row(f"windows:{b.voice_name}", [r[0] for r in res], [r[1] for r in res],
                             "whole line synthesized (upper bound for first audio)"))
            b.close()
    if tts.MacSay().available():
        b = tts.MacSay()
        res = [b.first_audio(t) for t in LINES]
        rows.append(_row("mac-say", [r[0] for r in res], [r[1] for r in res], "render to file"))
    return rows


def bench_kokoro(voice="am_michael"):
    from kokoro import KPipeline
    pipe = KPipeline(lang_code="a")
    next(iter(pipe("Hello.", voice=voice)))              # warm-up: loads the model and voice
    firsts, totals = [], []
    for t in LINES:
        start = time.perf_counter()
        gen = iter(pipe(t, voice=voice))
        next(gen)
        firsts.append(time.perf_counter() - start)
        for _ in gen:
            pass
        totals.append(time.perf_counter() - start)
    import torch
    return [_row(f"kokoro-82M:{voice}", firsts, totals, "PyTorch " + ("cuda" if torch.cuda.is_available() else "cpu"))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kokoro", action="store_true")
    ap.add_argument("--no-os", action="store_true")
    args = ap.parse_args()
    rows = ([] if args.no_os else bench_os()) + (bench_kokoro() if args.kokoro else [])
    for r in rows:
        print(json.dumps(r))


if __name__ == "__main__":
    main()
