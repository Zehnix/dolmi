"""LAT-1: per-line latency log (no text) and its p50/p95 report."""
import importlib.util
import json
import threading
import time

import streaming
from voice import reader

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent


def rows_in(folder):
    return [json.loads(x) for f in folder.glob("latency-*.jsonl") for x in f.read_text("utf-8").splitlines()]


def final_meta(utt, end):
    return {"id": utt, "session": 7, "utt": utt, "end": end, "trace": {"speech_end": end}}


def test_a_line_without_voice_is_written_at_once_with_iso_times_and_ms(tmp_path):
    log = streaming.LatencyLog(tmp_path, clock=lambda: 1_790_000_010.0)
    log.draft_shown(7, 3, 1_790_000_000.5)
    log.final_shown(final_meta(3, 1_790_000_000.0), "Wir fangen gleich an.", "We start soon.", 1_790_000_001.2)
    [row] = rows_in(tmp_path)
    assert row["utt"] == 3 and row["line"] == 3
    assert row["speech_end"].endswith("Z") and row["speech_end"].startswith("2026-")
    assert row["speech_end_to_german_shown_ms"] == 500 and row["speech_end_to_english_shown_ms"] == 1200
    assert row["de_words"] == 4 and row["en_words"] == 3
    assert "Wir" not in json.dumps(row)   # no text, only counts


def test_a_row_carries_the_pipeline_stamps_and_the_hold(tmp_path):
    t = 1_790_000_000.0
    log = streaming.LatencyLog(tmp_path, clock=lambda: t + 10)
    trace = {"speech_end": t, "vad_close": t + 0.6, "queued": t + 0.6, "asr_start": t + 0.7, "asr_done": t + 1.1,
             "held_start": t + 1.1, "held_end": t + 2.1, "release": "silence", "mt_start": t + 2.1,
             "mt_done": t + 2.3, "parts": 2, "joined_held": 0.8}
    log.final_shown({"id": 1, "session": 7, "utt": 1, "end": t, "trace": trace}, "Wir haben das Projekt",
                    "We have the project", t + 2.4)
    [row] = rows_in(tmp_path)
    assert row["asr_done"].endswith("Z") and row["mt_done"].endswith("Z")
    assert row["speech_end_to_vad_close_ms"] == 600 and row["vad_close_to_asr_start_ms"] == 100
    assert row["asr_start_to_asr_done_ms"] == 400 and row["asr_done_to_mt_start_ms"] == 1000
    assert row["mt_start_to_mt_done_ms"] == 200 and row["mt_done_to_english_shown_ms"] == 100
    assert row["held_ms"] == 1000 and row["release"] == "silence"
    assert row["parts"] == 2 and row["joined_held_ms"] == 800


def test_a_line_that_went_out_at_once_has_no_hold_and_old_traces_no_hold_fields(tmp_path):
    t = 1_790_000_000.0
    log = streaming.LatencyLog(tmp_path, clock=lambda: t + 10)
    stamped = {"speech_end": t, "asr_start": t + 0.6, "asr_done": t + 1.0, "mt_start": t + 1.0, "mt_done": t + 1.2}
    log.final_shown({"id": 1, "session": 7, "utt": 1, "end": t, "trace": stamped}, "Gut.", "Good.", t + 1.3)
    log.final_shown(final_meta(2, t), "Ja.", "Yes.", t + 1.0)          # a producer without stamps
    first, old = sorted(rows_in(tmp_path), key=lambda r: r["utt"])
    assert first["held_ms"] == 0 and first["parts"] == 1 and "release" not in first
    assert not any(k in old for k in ("held_ms", "parts", "asr_done", "asr_start_to_asr_done_ms"))


def test_a_voiced_line_waits_for_the_voice_and_records_its_times(tmp_path):
    log = streaming.LatencyLog(tmp_path, clock=lambda: 1_790_000_002.0)
    log.final_shown(final_meta(4, 1_790_000_000.0), "Gut.", "Good.", 1_790_000_001.0, voice=True)
    assert rows_in(tmp_path) == []
    log.voice_spoken(7, 4, 1_790_000_001.5, 1_790_000_002.0, last=False)
    assert rows_in(tmp_path) == []                     # more of that line is still queued
    log.voice_spoken(7, 4, 1_790_000_002.0, 1_790_000_003.0, last=True)
    [row] = rows_in(tmp_path)
    assert row["english_shown_to_voice_start_ms"] == 500 and row["voice_start_to_voice_end_ms"] == 1500
    assert row["speech_end_to_voice_end_ms"] == 3000


def test_flush_writes_lines_the_voice_never_read(tmp_path):
    log = streaming.LatencyLog(tmp_path, clock=lambda: 1_790_000_002.0)
    log.final_shown(final_meta(5, 1_790_000_000.0), "Ja.", "Yes.", 1_790_000_001.0, voice=True)
    log.flush()
    [row] = rows_in(tmp_path)
    assert "voice_start" not in row and row["utt"] == 5


def test_reader_reports_tagged_lines():
    class TTS:
        name = "fake"

        def speak(self, text):
            pass

    seen, done = [], threading.Event()
    r = reader.Reader(TTS())
    r.on_spoken = lambda tag, start, end, last: (seen.append((tag, last, end >= start)), done.set())
    r.start()
    r.offer("Hello there.", tag=(7, 1))
    assert done.wait(2)
    r.stop()
    assert seen == [((7, 1), True, True)]


def test_pump_writes_a_row_for_each_final(api, tmp_path):
    import config
    api._session = {"token": object(), "stop": None, "transcript": None}
    sid = api._session_id()
    api._ui_q.put(("final", "Wir fangen an.", "We start.", {"id": 1, "session": sid, "utt": 1, "rev": 1,
                                                             "start": time.time() - 2, "end": time.time() - 1,
                                                             "trace": {"speech_end": time.time() - 1}}))
    threading.Thread(target=api._pump, daemon=True).start()
    deadline = time.time() + 3
    while time.time() < deadline and not rows_in(config.LOGS):
        time.sleep(0.05)
    [row] = rows_in(config.LOGS)
    assert row["utt"] == 1 and row["de_words"] == 3 and row["speech_end_to_english_shown_ms"] >= 900


def test_report_prints_p50_and_p95_per_stage(tmp_path, capsys):
    spec = importlib.util.spec_from_file_location("latency_report", ROOT / "eval" / "latency_report.py")
    rep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rep)
    f = tmp_path / "latency-2026-10-26.jsonl"
    f.write_text("\n".join(json.dumps({"speech_end_to_english_shown_ms": v}) for v in range(1, 101)) + "\n{bad",
                 encoding="utf-8")
    assert rep.main([str(f)]) == 0
    out = capsys.readouterr().out
    assert "100 lines" in out and "speech_end_to_english_shown" in out
    assert out.split("speech_end_to_english_shown")[1].split()[1:] == ["50", "95"]
    assert "Where the English time goes" not in out          # old rows have no stage stamps


def load_report():
    spec = importlib.util.spec_from_file_location("latency_report", ROOT / "eval" / "latency_report.py")
    rep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rep)
    return rep


def test_report_splits_stamped_rows_into_stages_and_counts_holds():
    rep = load_report()
    old = {"speech_end_to_english_shown_ms": 9000}
    new = [{"speech_end_to_english_shown_ms": 2000, "speech_end_to_vad_close_ms": 600,
            "vad_close_to_asr_start_ms": 100, "asr_start_to_asr_done_ms": 400, "asr_done_to_mt_start_ms": h,
            "mt_start_to_mt_done_ms": 200, "mt_done_to_english_shown_ms": 100, "held_ms": h, "parts": p,
            **({"release": r} if r else {}), **({"joined_held_ms": 900} if p > 1 else {})}
           for h, p, r in ((0, 1, None), (0, 2, None), (1000, 1, "silence"), (2500, 1, "cap"))]
    out = rep.report([old] + new)
    assert "5 lines" in out
    split = out.split("Where the English time goes")[1]
    assert "(4 lines with stage stamps)" in split                  # the old row is left out of the split
    assert split.split("Whisper final decode")[1].split()[:2] == ["400", "400"]
    assert "held lines: 2 of 4 (50%)" in split and "released by: cap 1, silence 1" in split
    assert "joined lines: 1, earlier parts waited p50 900 ms" in split
