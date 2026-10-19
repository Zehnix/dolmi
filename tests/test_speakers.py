"""Speaker labels (beta, SPK-1..4): online clustering on synthetic voice vectors, the Diarizer hook, the
saved transcript (md / srt / speakers.json) with retroactive renames, the bridge calls, and that nothing
is loaded while the setting is off."""
import json
import queue
import threading
import time

import numpy as np
import pytest

import config
import live_subs
import speakers

TH = speakers.Thresholds(match=0.6, new=0.45, merge=0.75)
DIM = 64


def voices(n, seed=0):
    """n unrelated 'voices' (random unit vectors are nearly orthogonal in 64 dimensions)."""
    rng = np.random.default_rng(seed)
    return [speakers.unit(rng.standard_normal(DIM)) for _ in range(n)]


def said(voice, rng, spread=0.25):
    """One line of `voice`: the voice plus a little noise (cosine to the voice ~0.95)."""
    return speakers.unit(voice + spread * rng.standard_normal(DIM) / np.sqrt(DIM) * 3)


def blend(a, b, cos):
    """A unit vector with the given cosine to `a`, leaning towards `b`."""
    b = speakers.unit(b - np.dot(a, b) * a)
    return cos * a + np.sqrt(1 - cos ** 2) * b


# ------------------------------------------------------------------ clustering
def test_three_voices_get_three_stable_ids():
    rng, (a, b, c) = np.random.default_rng(1), voices(3)
    cl = speakers.Clusterer()
    order = [a, b, a, c, b, c, a, a, b, c, c, a]
    ids = [cl.assign(said(v, rng), 3.0, TH) for v in order]
    first = {}
    for v, (sid, conf) in zip(order, ids):
        first.setdefault(id(v), sid)
        assert sid == first[id(v)] and conf >= speakers.LOW_CONF
    assert sorted(first.values()) == [1, 2, 3]


def test_grey_zone_takes_the_nearest_unsure_and_does_not_learn():
    a, b = voices(2)
    cl = speakers.Clusterer()
    assert cl.assign(a, 3.0, TH) == (1, 1.0)
    before = cl.sums[1].copy()
    sid, conf = cl.assign(blend(a, b, 0.52), 3.0, TH)      # between new (0.45) and match (0.6)
    assert sid == 1 and conf < speakers.LOW_CONF
    assert np.allclose(cl.sums[1], before) and cl.counts[1] == 1


def test_far_voice_is_a_new_speaker_and_close_voice_learns():
    a, b = voices(2)
    cl = speakers.Clusterer()
    cl.assign(a, 3.0, TH)
    assert cl.assign(blend(a, b, 0.2), 3.0, TH)[0] == 2
    sid, conf = cl.assign(blend(a, b, 0.9), 3.0, TH)
    assert sid == 1 and conf >= 0.75 and cl.counts[1] == 2


def test_short_lines_inherit_the_previous_speaker():
    a, b = voices(2)
    cl = speakers.Clusterer()
    cl.assign(a, 3.0, TH)
    cl.assign(b, 3.0, TH)
    sid, conf = cl.assign(a, 0.6, TH)                       # "Ja." from voice a, but too short to tell
    assert sid == 2 and conf < speakers.LOW_CONF and cl.counts[1] == 1


def test_merge_pass_joins_one_voice_split_in_two():
    a, b = voices(2)
    merged = []
    cl = speakers.Clusterer(merge_every=3, on_merge=lambda old, new: merged.append((old, new)))
    cl.assign(a, 3.0, TH)
    split = blend(a, b, 0.4)                                # starts as a new speaker ...
    assert cl.assign(split, 3.0, TH)[0] == 2
    # ... then the lines in between pull both centroids together until the merge pass joins them
    middle = speakers.unit(a + split)                       # cosine 0.84 to both
    for _ in range(6):
        sid, _ = cl.assign(middle, 3.0, TH)
    assert merged == [(2, 1)] and cl.resolve(2) == 1 and sid == 1 and set(cl.sums) == {1}


def test_at_most_eight_speakers():
    vs = voices(12, seed=3)
    cl = speakers.Clusterer()
    ids = [cl.assign(v, 3.0, TH) for v in vs]
    assert max(s for s, _ in ids) == speakers.MAX_SPEAKERS == 8
    assert all(c <= 0.3 for _, c in ids[8:])                # the 9th voice on: nearest, unsure


def test_user_merge_and_new_speaker():
    a, b = voices(2)
    cl = speakers.Clusterer()
    cl.assign(a, 3.0, TH), cl.assign(b, 3.0, TH)
    assert cl.new_speaker() == 3 and 3 not in cl.sums      # "someone new": an id, no voice yet
    assert cl.merge(2, 1) == 1 and cl.resolve(2) == 1 and cl.counts[1] == 2
    assert cl.assign(b, 3.0, TH)[0] == 1                    # b's lines now count as speaker 1


def test_thresholds_per_mode_stay_in_the_researched_ranges():
    for mode in ("speaker", "mic"):
        th = speakers.THRESHOLDS[mode]
        assert 0.55 <= th.match <= 0.65 and 0.40 <= th.new <= 0.55 and th.new < th.match < th.merge


# ------------------------------------------------------------------ diarizer hook
def test_diarizer_assigns_and_never_raises():
    a, b = voices(2)
    table = {1: a, 2: b}

    def embed(audio):
        if audio[0] == 9:
            raise RuntimeError("bad buffer")
        return table[int(audio[0])]

    d = speakers.Diarizer(embed, mode=lambda: "mic")
    sec = speakers.SR * 2
    assert d.assign(np.full(sec, 1.0, np.float32), 0, 2)[0] == 1
    assert d.assign(np.full(sec, 2.0, np.float32), 2, 4)[0] == 2
    assert d.assign(np.full(sec, 9.0, np.float32), 4, 6) == (2, 0.3)       # failed: keeps the last speaker
    assert d.assign(np.full(sec // 4, 9.0, np.float32), 6, 6.5)[0] == 2    # short: not even embedded
    assert d.thresholds() is speakers.THRESHOLDS["mic"] and len(d.ms) == 2
    assert speakers.Diarizer(lambda x: 1 / 0).assign(np.ones(sec, np.float32)) == (None, None)


def test_worker_hook_puts_the_speaker_on_the_line(tmp_path, fake_engine):
    """The existing hook: diarizer.assign(...) -> meta['speaker'] / ['speaker_conf'] of the final."""
    class Fixed:
        def assign(self, audio, start, end):
            return 2, 0.9

    eng, audio_q, ui_q, stop = fake_engine(), queue.Queue(), queue.Queue(), threading.Event()
    tr = live_subs.Transcript(tmp_path)
    always_speech = lambda audio: 0.0                       # noqa: E731  (a sentence still going on)
    t = threading.Thread(target=live_subs.worker, args=(eng, audio_q, ui_q, tr, stop, always_speech),
                         kwargs={"diarizer": Fixed()}, daemon=True)
    t.start()
    for _ in range(30):                                     # 3 s of "speech"
        audio_q.put(np.full(1600, 0.1, np.float32))
    deadline = time.time() + 10
    while time.time() < deadline and not audio_q.empty():
        time.sleep(0.05)
    time.sleep(0.5)
    stop.set()                                              # mid-sentence Stop keeps the line
    while time.time() < deadline and not tr.lines:
        time.sleep(0.05)
    assert tr.lines, "no final line came out"
    line = next(iter(tr.lines.values()))
    assert line["speaker"] == 2 and line["speaker_conf"] == 0.9
    assert " · Speaker 2**" in tr.md.read_text(encoding="utf-8")


# ------------------------------------------------------------------ transcript files
def _meeting(folder, save=True):
    t = live_subs.Transcript(folder, save=save)
    t.add(t.t0 + 1, t.t0 + 2, "Hallo.", "Hello.", {"id": 1, "speaker": 1, "speaker_conf": 0.9})
    t.add(t.t0 + 3, t.t0 + 4, "Ja.", "Yes.", {"id": 2, "speaker": 2, "speaker_conf": 0.4})
    t.add(t.t0 + 5, t.t0 + 6, "Gut.", "Good.", {"id": 3, "speaker": 1, "speaker_conf": 0.95})
    return t


def test_live_lines_carry_raw_labels_and_finish_writes_final_names(tmp_path):
    t = _meeting(tmp_path)
    md = t.md.read_text(encoding="utf-8")
    assert md.count(" · Speaker 1**") == 2 and " · Speaker 2**" in md          # crash-safe raw labels
    assert "Speaker 1: Hello." in t.srt.read_text(encoding="utf-8")
    t.rename_speaker(1, "Lena")
    assert json.loads(t.speakers_json.read_text(encoding="utf-8"))["1"] == {"name": "Lena", "colour": speakers.colour(1)}
    t.finish()
    md, srt = t.md.read_text(encoding="utf-8"), t.srt.read_text(encoding="utf-8")
    assert md.count(" · Lena** ") == 2 and "Speaker 1" not in md and " · Speaker 2** Yes." in md
    assert "\nLena: Hello.\n" in srt and "\nLena: Good.\n" in srt and "\nSpeaker 2: Yes.\n" in srt
    assert set(json.loads(t.speakers_json.read_text(encoding="utf-8"))) == {"1", "2"}


def test_relabel_merges_every_past_line(tmp_path):
    t = _meeting(tmp_path)
    assert t.relabel(2, 1) == [2]
    t.finish()
    assert "Speaker 2" not in t.md.read_text(encoding="utf-8")
    assert json.loads(t.speakers_json.read_text(encoding="utf-8")).keys() == {"1"}
    assert [s["lines"] for s in t.speaker_list()] == [3]


def test_a_line_cut_before_a_merge_lands_on_the_merged_speaker(tmp_path):
    t = _meeting(tmp_path)
    t.relabel(2, 1)
    t.add(t.t0 + 7, t.t0 + 8, "Nein.", "No.", {"id": 4, "speaker": 2, "speaker_conf": 0.8})
    assert t.lines[4]["speaker"] == 1 and [s["id"] for s in t.speaker_list()] == [1]


def test_lines_without_speakers_look_as_before(tmp_path):
    t = live_subs.Transcript(tmp_path)
    t.add(t.t0 + 1, t.t0 + 2, "Hallo.", "Hello.", {"id": 1, "speaker": None, "speaker_conf": None})
    t.finish()
    assert " · " not in t.md.read_text(encoding="utf-8") and "\nHello.\n" in t.srt.read_text(encoding="utf-8")
    assert not t.speakers_json.exists() and t.speaker_list() == []


def test_no_files_when_transcripts_are_off(tmp_path):
    t = _meeting(tmp_path, save=False)
    t.rename_speaker(1, "Lena")
    t.finish()
    assert not list(tmp_path.iterdir()) and t.speaker_list()[0]["name"] == "Lena"


# ------------------------------------------------------------------ bridge (app.py)
def test_off_by_default_loads_nothing(api, monkeypatch):
    assert config.DEFAULTS["speaker_labels"] is False and api.state()["speakerLabels"] is False
    monkeypatch.setattr(speakers, "load_diarizer", lambda **k: pytest.fail("model loaded while off"))
    monkeypatch.setattr(speakers, "Embedder", lambda *a, **k: pytest.fail("model loaded while off"))
    assert api._make_diarizer() is None
    assert speakers.make_diarizer(config.DEFAULTS) is None


def test_switch_on_builds_the_diarizer_and_a_failure_keeps_captions(api, monkeypatch):
    assert api.set_setting("speaker_labels", True) and config.load_settings()["speaker_labels"] is True
    assert api.set_setting("speaker_labels", "yes") is False
    made = {}
    monkeypatch.setattr(speakers, "is_installed", lambda: True)
    monkeypatch.setattr(speakers, "load_diarizer", lambda **k: made.update(k) or "D")
    assert api._make_diarizer() == "D" and made["mode"]() in ("speaker", "mic")
    monkeypatch.setattr(speakers, "load_diarizer", lambda **k: 1 / 0)
    assert api._make_diarizer() is None and "Speaker labels are off for this meeting" in api._window.js()


def test_rename_after_stop_rewrites_the_saved_meeting(api):
    t = _meeting(api._data)
    api._last_transcript = t
    r = api.speaker_rename(1, "  Lena *Weber*\n ")
    assert r["ok"] and r["speakers"][0] == {"id": 1, "name": "Lena Weber", "colour": speakers.colour(1), "lines": 2}
    assert "window.dolmi.onSpeakers(" in api._window.js()
    rows = api.read_meeting(t.md.name)
    assert [x.get("speaker") for x in rows] == ["Lena Weber", "Speaker 2", "Lena Weber"]
    assert rows[0]["colour"] == speakers.colour(1) and rows[0]["ts"] and rows[0]["end"]
    assert "Lena Weber" in api.meeting_txt(t.md.name)
    assert api.speaker_rename(1, " ")["ok"] is False and api.speaker_rename("1", "X")["ok"] is False


def test_reassign_and_merge_through_the_bridge(api):
    t = _meeting(api._data)
    api._last_transcript = t
    r = api.speaker_reassign(3, None)                       # "this line is someone else": a new speaker
    assert r["ok"] and r["speaker"] == 3 and t.lines[3]["speaker"] == 3
    assert api.speaker_reassign(3, 2)["ok"] and t.lines[3]["speaker"] == 2
    assert api.speaker_reassign(99, 2)["ok"] is False
    r = api.speaker_merge(2, 1)
    assert r["ok"] and [s["id"] for s in r["speakers"]] == [1]
    assert "window.dolmi.onSpeakerMerge(2,1)" in api._window.js().replace(" ", "")
    assert all(x["speaker"] == 1 for x in t.lines.values())
    assert {x.get("speaker") for x in api.read_meeting(t.md.name)} == {"Speaker 1"}
    assert api.speaker_merge(1, 1)["ok"] is False


def test_live_merge_goes_through_the_diarizer(api):
    t = _meeting(api._data)
    a, b = voices(2)
    d = speakers.Diarizer(lambda x: a, on_merge=api._on_speaker_merge)
    api._session = {"token": object(), "stop": threading.Event(), "transcript": t, "diarizer": d}
    d.clusters.assign(a, 3, TH), d.clusters.assign(b, 3, TH)
    assert api.speaker_merge(2, 1)["ok"] and d.clusters.resolve(2) == 1
    assert all(x["speaker"] == 1 for x in t.lines.values())
    assert api.speaker_list()[0]["lines"] == 3
    assert api.speaker_reassign(1, None)["speaker"] == 3     # the diarizer hands out the new id


def test_a_late_merge_of_the_last_meeting_stays_in_that_meeting(api, monkeypatch, tmp_path):
    """After Stop the old diarizer can still merge while the next meeting is live: it must relabel
    the old meeting's lines, not the new meeting's (SPK-3 review)."""
    made = {}
    monkeypatch.setattr(speakers, "is_installed", lambda: True)
    monkeypatch.setattr(speakers, "load_diarizer", lambda **k: made.update(k) or "D")
    monkeypatch.setitem(api._settings, "speaker_labels", True)
    (tmp_path / "old").mkdir(), (tmp_path / "new").mkdir()
    old, new = _meeting(tmp_path / "old"), _meeting(tmp_path / "new")
    old.add(old.t0 + 7, old.t0 + 8, "Und?", "And?", {"id": 4, "speaker": 3, "speaker_conf": 0.9})
    new.add(new.t0 + 7, new.t0 + 8, "So.", "So.", {"id": 4, "speaker": 3, "speaker_conf": 0.9})
    assert api._make_diarizer(lambda: old) == "D"
    api._session = {"token": object(), "stop": threading.Event(), "transcript": new, "diarizer": None}
    before = api._window.js()
    made["on_merge"](3, 1)
    assert old.lines[4]["speaker"] == 1 and new.lines[4]["speaker"] == 3
    assert "onSpeakerMerge" not in api._window.js()[len(before):]
    assert "· Speaker 3**" not in old.md.read_text(encoding="utf-8")          # its files were rewritten
    new.add(new.t0 + 9, new.t0 + 10, "Ok.", "Ok.", {"id": 5, "speaker": 3, "speaker_conf": 0.9})
    assert new.lines[5]["speaker"] == 3                                      # nothing leaked into the new one


def test_new_speaker_after_stop_never_reuses_a_renamed_id(api):
    t = _meeting(api._data)
    api._last_transcript = t
    assert api.speaker_rename(2, "Anna")["ok"]
    assert api.speaker_reassign(2, 1)["ok"]                    # Anna has no lines left
    r = api.speaker_reassign(3, None)
    assert r["ok"] and r["speaker"] == 3 and t.speaker_name(3) == "Speaker 3"
    t.relabel(3, 1)                                            # a merged-away id is not reused either
    assert t.next_speaker_id() == 4


def test_rename_to_another_speakers_name_is_refused(api):
    t = _meeting(api._data)
    api._last_transcript = t
    r = api.speaker_rename(1, "speaker 2")
    assert r["ok"] is False and "Same person as" in r["message"] and t.speaker_name(1) == "Speaker 1"
    assert api.speaker_rename(1, "Lena")["ok"] and api.speaker_rename(1, "LENA")["ok"]  # its own name is fine
    assert api.speaker_rename(2, "lena")["ok"] is False


def test_stale_merge_or_reassign_to_a_merged_id_still_lands(api):
    t = _meeting(api._data)
    a, b = voices(2)
    d = speakers.Diarizer(lambda x: a, on_merge=api._on_speaker_merge)
    api._session = {"token": object(), "stop": threading.Event(), "transcript": t, "diarizer": d}
    d.clusters.assign(a, 3, TH), d.clusters.assign(b, 3, TH)
    d.clusters.alias[2] = 1                                    # merged, but line 2 still says 2
    assert api.speaker_merge(2, 1)["ok"] and t.lines[2]["speaker"] == 1
    t.lines[2]["speaker"] = 2                                  # a stale page reassigns to the merged id
    assert api.speaker_reassign(2, 2)["ok"] and t.lines[2]["speaker"] == 1


def test_embedding_times_are_bounded():
    d = speakers.Diarizer(lambda x: voices(1)[0])
    for _ in range(600):
        d.assign(np.zeros(speakers.SR * 2, np.float32))
    assert len(d.ms) == 500


def test_delete_meeting_removes_the_speaker_names(api):
    t = _meeting(api._data)
    t.finish()
    assert t.speakers_json.exists()
    api.delete_meeting(t.md.name)
    assert not t.md.exists() and not t.speakers_json.exists()


# ------------------------------------------------------------------ the real model (when downloaded)
@pytest.mark.skipif(not speakers.is_installed(), reason="speaker model not downloaded")
def test_real_model_embeds_a_line():
    emb = speakers.Embedder()
    rng = np.random.default_rng(0)
    x = (0.1 * rng.standard_normal(speakers.SR * 3)).astype(np.float32)
    v = emb(x)
    assert v.shape == (512,) and abs(np.linalg.norm(v) - 1) < 1e-6
    assert float(np.dot(v, emb(x))) > 0.999
    with pytest.raises(ValueError):
        emb(np.zeros(100, np.float32))
