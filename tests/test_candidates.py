"""QA-6 speech A/B helper (eval/asr_ab.py) with a fake backend: no model, no onnx-asr needed."""
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "eval"
for _p in (str(EVAL), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import asr_ab  # noqa: E402

SR = 16000


def _clip(cid, text, terms=()):
    return {"id": cid, "lang": "de", "text": text, "terms": list(terms),
            "load": lambda: np.zeros(SR, np.float32)}


def test_run_and_summarize_counts_missed_terms_and_wer():
    clips = [_clip("a", "Der Nimbus Gateway läuft", ["Nimbus Gateway"]),
             _clip("b", "Wir starten morgen"),
             _clip("c", "Das passt so")]
    answers = {0: "der nimbus gateway läuft", 1: "", 2: "das passt"}
    calls = []

    def fake(audio):
        calls.append(len(audio))
        return answers[len(calls) - 1]
    rows = asr_ab.run_clips(fake, clips, log=lambda *_: None)
    assert [r["hyp"] for r in rows] == ["der nimbus gateway läuft", "", "das passt"]
    s = asr_ab.summarize(rows)
    assert s["clips"] == 3
    assert round(s["missed_pct"], 1) == 33.3          # the empty answer
    assert round(s["wer_de"], 1) == round(100 * 4 / 10, 1)   # 3 deletions + 1 deletion of 10 words
    assert (s["terms_found"], s["terms_expected"]) == (1, 1)
    assert s["rtf"] is not None and s["rtf"] >= 0


def test_compare_reads_summaries(tmp_path):
    for name, wer in (("whisper-small", 12.0), ("parakeet-fp32", 8.5)):
        (tmp_path / f"synthetic.{name}.summary.json").write_text(json.dumps(
            {"set": "synthetic", "backend": name, "clips": 3, "wer_de": wer, "cer_de": 3.0, "missed_pct": 0.0,
             "term_acc_de": 50.0, "rtf": 0.2, "secs_p50": 0.5, "secs_p95": 1.0}), encoding="utf-8")
    table = asr_ab.compare(tmp_path)
    assert "| synthetic | parakeet-fp32 | 3 | 8.50 |" in table
    assert "| synthetic | whisper-small | 3 | 12.00 |" in table


def test_default_out_is_outside_the_checkout():
    assert ROOT.resolve() not in asr_ab.default_out().resolve().parents
