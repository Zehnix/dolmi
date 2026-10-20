"""STR-10: the streaming bench's flicker counters and table (no models run)."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "str_bench", Path(__file__).resolve().parents[1] / "eval" / "streaming" / "bench.py")
bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bench)


def draft(committed, tentative, utt=1):
    return {"kind": "stream_event", "item": ["draft", f"{committed} {tentative}", "",
                                             {"utt": utt, "committed": committed, "tentative": tentative}]}


def test_append_only_drafts_have_no_rewrites():
    events = [draft("", "guten"), draft("guten", "morgen alle"), draft("guten morgen", "alle zusammen")]
    rewrites, churn, n = bench.draft_flicker(events)
    assert rewrites == 0
    assert churn > 0
    assert n == 2


def test_changed_committed_prefix_counts_as_rewrite():
    events = [draft("guten morgen", "x"), draft("gute morgen", "x")]
    assert bench.draft_flicker(events)[0] == 1


def test_new_utterance_and_final_reset_the_comparison():
    events = [draft("eins zwei", ""), {"kind": "stream_event", "item": ["final", "eins zwei", "", {}]},
              draft("drei", ""), draft("vier", "", utt=2), draft("vier fuenf", "", utt=2)]
    assert bench.draft_flicker(events)[::2] == (0, 1)


def test_table_shows_failed_configs():
    text = bench.table([{"config": "cuda-small-old", "error": "no CUDA"}],
                       type("A", (), {"set": "synthetic", "limit": 1})())
    assert "cuda-small-old" in text and "failed: no CUDA" in text


def test_trimmed_window_front_is_not_a_rewrite():
    events = [draft("a b c d", "e"), draft("c d e f", ""), draft("", "neu")]
    assert bench.draft_flicker(events)[0] == 0


def test_no_measured_drafts_is_not_zero_rewrites():
    events = [{"kind": "stream_event", "item": ["draft", "hallo", "", {"utt": 1}]}]
    assert bench.draft_flicker(events) == (None, None, 0)


def test_table_rewrites_show_the_draft_count_only_with_str1():
    row = {"config": "c", "str1": True, "stream_wer": 1.0, "missed": 0.0, "first_p50": 1.0, "first_p95": 2.0,
           "final_p50": 1.0, "final_p95": 2.0, "rtf": 0.3, "rewrites": 26, "drafts": 1344, "churn": 3.3,
           "vram_peak_mb": 3813}
    args = type("A", (), {"set": "synthetic", "limit": 2})()
    assert "26 / 1344 drafts" in bench.table([row], args)
    assert "26 / 1344" not in bench.table([{**row, "str1": False}], args)
    assert "| 0 |" not in bench.table([{**row, "rewrites": None, "drafts": 0}], args)
