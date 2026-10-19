"""Live view: caption pump timing meta (lag, speech times), the saved meeting format, and the Live /
Meetings pages in a headless browser (scrolling up stays put, times on both columns)."""
import html as html_lib
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pytest

import live_subs

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "src" / "ui" / "web"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


# ------------------------------------------------------------------ caption pump
def _calls(api, fn, n, timeout=3.0):
    """The arguments of the first n window.dolmi.<fn>(...) calls Python pushed to the page."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = [json.loads("[" + m + "]") for m in re.findall(rf"window\.dolmi\.{fn}\((.*)\)$", api._window.js(), re.M)]
        if len(found) >= n:
            return found
        time.sleep(0.02)
    raise AssertionError(f"{fn} called fewer than {n} times: {api._window.js()[-500:]}")


def _session(api, transcript=None):
    api._session = {"token": object(), "stop": None, "transcript": transcript}


def test_pump_accepts_old_three_tuples(api):
    _session(api)
    before = time.time()
    api._ui_q.put(("final", "Hallo zusammen.", "Hello everyone."))
    api._ui_q.put(("draft", "Wir", "We"))
    (hhmm, de, en, meta), = _calls(api, "onLine", 1)
    (dde, den, dmeta), = _calls(api, "onDraft", 1)
    assert (de, en) == ("Hallo zusammen.", "Hello everyone.") and (dde, den) == ("Wir", "We")
    assert before <= meta["start"] <= meta["end"] <= meta["shown"]       # no meta: the line is "now"
    assert meta["lag"] is None and meta["lag_median"] is None             # nothing measured: page shows '–'
    assert hhmm == datetime.fromtimestamp(meta["start"]).strftime("%H:%M")
    ts, hde, hen, hmeta = api._history[0]
    assert (hde, hen) == ("Hallo zusammen.", "Hello everyone.") and hmeta["shown"] == meta["shown"]
    assert ts == datetime.fromtimestamp(meta["start"]).strftime("%H:%M:%S")


def test_pump_marks_last_caption_on_the_watcher_clock(api):
    """APP-3's 'nothing heard' watcher compares _last_caption with self._clock(), so the pump must set it
    on that clock for every non-empty final and draft, and leave it alone for empty ones."""
    _session(api)
    ticks = iter([100.0, 105.0])
    api._clock = lambda: next(ticks)
    api._last_caption = 0.0
    api._ui_q.put(("draft", "", ""))
    api._ui_q.put(("final", "", ""))
    api._ui_q.put(("draft", "Wir", "We"))
    _calls(api, "onDraft", 2)
    assert api._last_caption == 100.0
    api._ui_q.put(("final", "Wir liefern.", "We ship."))
    _calls(api, "onLine", 2)
    assert api._last_caption == 105.0


def test_pump_last_caption_defaults_to_monotonic(api):
    _session(api)
    before = time.monotonic()
    api._ui_q.put(("final", "Hallo.", "Hello."))
    _calls(api, "onLine", 1)
    assert before <= api._last_caption <= time.monotonic()


def test_pump_uses_speech_times_and_measures_lag(api, tmp_path):
    tr = live_subs.Transcript(tmp_path, save=False)
    tr.t0 -= 10                                                     # the meeting began 10 s ago
    _session(api, tr)
    end = time.time() - 1.5
    meta = {"id": 7, "start": end - 2.0, "end": end, "emitted": end + 0.1, "lang": "de", "speaker": None, "speaker_conf": 0.0}
    api._ui_q.put(("final", "Wir liefern am Freitag.", "We ship on Friday.", meta))
    (hhmm, _, _, m), = _calls(api, "onLine", 1)
    assert m["id"] == 7 and m["lang"] == "de" and m["start"] == end - 2.0
    assert m["shown"] >= m["end"] and m["lag"] == pytest.approx(m["shown"] - end) and m["lag"] >= 1.5
    assert m["lag_median"] == m["lag"]
    assert m["offset"] == pytest.approx(end - 2.0 - tr.t0)
    assert hhmm == datetime.fromtimestamp(end - 2.0).strftime("%H:%M")      # speech start, not display time
    assert api._history[0][0] == datetime.fromtimestamp(end - 2.0).strftime("%H:%M:%S")


def test_pump_draft_meta_lag_and_dropped(api):
    _session(api)
    end = time.time() - 0.7
    api._ui_q.put(("draft", "Wir brauchen", "We need", {"start": end - 1, "end": end, "emitted": end}))
    api._ui_q.put(("draft", "", "", {"dropped": "stale"}))
    (_, _, m), (_, _, gone) = _calls(api, "onDraft", 2)
    assert m["lag"] >= 0.7 and m["shown"] >= m["end"] and "lag_median" not in m
    assert gone["dropped"] == "stale" and gone["lag"] is None
    assert not api._history                                                   # drafts are not history


def test_lag_is_median_of_last_five_finals(api):
    _session(api)
    now = time.time()
    for i, lag in enumerate([9.0, 1.0, 2.0, 3.0, 4.0, 5.0]):                 # the 9 s falls out of the window
        api._ui_q.put(("final", f"Satz {i}", f"Line {i}", {"start": now - lag - 1, "end": now - lag}))
    found = _calls(api, "onLine", 6)
    assert found[0][3]["lag_median"] == pytest.approx(9.0, abs=0.2)
    assert found[-1][3]["lag_median"] == pytest.approx(3.0, abs=0.2)       # median of 1..5
    _session(api)                                                             # a new meeting starts over
    api._ui_q.put(("final", "Neu", "New"))
    assert _calls(api, "onLine", 7)[-1][3]["lag_median"] is None


def test_lag_starts_as_dash_and_is_not_hard_coded():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert re.search(r'id="sLag">–<', html) and "0.3s" not in html


def test_numpy_meta_is_sent_as_plain_numbers(api):
    np = pytest.importorskip("numpy")
    _session(api)
    end = time.time()
    api._ui_q.put(("final", "Hallo", "Hello", {"id": np.int64(3), "start": np.float64(end - 1), "end": np.float32(end)}))
    (_, _, _, m), = _calls(api, "onLine", 1)
    assert m["id"] == 3 and isinstance(m["start"], float)


def test_numpy_flags_and_missing_times_still_reach_the_page(api):
    """Producers now send meta: a numpy flag or an explicit end=None must not drop the line."""
    np = pytest.importorskip("numpy")
    _session(api)
    api._ui_q.put(("final", "Eins.", "One.", {"id": 1, "speech": np.bool_(True), "end": None, "start": None}))
    (_, _, _, m), = _calls(api, "onLine", 1)
    assert m["speech"] is True and m["lag"] is None and isinstance(m["start"], float) and m["start"] == m["end"]


# ------------------------------------------------------------------ transcript files
def test_transcript_writes_speech_times(tmp_path):
    t = live_subs.Transcript(tmp_path)
    start = t.t0 + 5.25
    t.add(start, start + 2.5, "Hallo zusammen", "Hello everyone", {"id": "u1"})
    md = t.md.read_text(encoding="utf-8")
    span = f"{datetime.fromtimestamp(start):%H:%M:%S}–{datetime.fromtimestamp(start + 2.5):%H:%M:%S}"
    assert f"**{span}** Hello everyone  \n<sub>Hallo zusammen</sub>" in md
    assert f"<!-- started {datetime.fromtimestamp(t.t0):%H:%M:%S} -->" in md
    assert "00:00:05,250 --> 00:00:07,750" in t.srt.read_text(encoding="utf-8")
    assert t.lines["u1"]["en"] == "Hello everyone" and t.n == 1


def test_transcript_without_saving_still_keeps_times_and_lines(tmp_path):
    t = live_subs.Transcript(tmp_path, save=False)
    assert t.t0 <= time.time() and t.md is None
    a, b = t.add(t.t0, t.t0 + 1, "Hallo", "Hello"), t.add(t.t0 + 2, t.t0 + 3, "Tschüss", "Bye")
    assert a != b and t.lines[b]["de"] == "Tschüss"
    t.finish()
    assert not list(tmp_path.iterdir())


def test_update_line_and_finish_rewrite_both_files(tmp_path):
    t = live_subs.Transcript(tmp_path)
    t.add(t.t0 + 1, t.t0 + 2, "Eins", "One", {"id": 1})
    t.add(t.t0 + 3, t.t0 + 4, "Zwei", "Twoo", {"id": 2})
    assert t.update_line(2, en="Two", speaker="Alice") and not t.update_line(99, en="x")
    assert "Twoo" in t.md.read_text(encoding="utf-8")                      # files change only on finish()
    t.finish()
    md, srt = t.md.read_text(encoding="utf-8"), t.srt.read_text(encoding="utf-8")
    assert "Twoo" not in md and "Two  \n<sub>Zwei</sub>" in md and md.startswith("# Meeting ")
    assert md.count("<sub>") == 2 and t.lines[2]["speaker"] == "Alice"
    # a line with a speaker carries its name (SPK-3; a plain string is the name itself)
    assert srt.startswith("1\n00:00:01,000 --> 00:00:02,000\nOne") and "2\n00:00:03,000 --> 00:00:04,000\nAlice: Two\n" in srt


def test_auto_ids_never_clash_with_producer_ids(tmp_path):
    t = live_subs.Transcript(tmp_path)
    auto = t.add(t.t0 + 1, t.t0 + 2, "Eins", "One")
    t.add(t.t0 + 3, t.t0 + 4, "Zwei", "Two", {"id": 0})
    assert auto != 0 and len(t.lines) == 2
    t.finish()
    assert t.md.read_text(encoding="utf-8").count("<sub>") == 2


# ------------------------------------------------------------------ reading saved meetings
def test_new_meeting_file_round_trips(api, tmp_path):
    t = live_subs.Transcript(api._data)
    t.add(t.t0 + 65, t.t0 + 68, "Wir liefern am Freitag.", "We ship on Friday.")
    rows = api.read_meeting(t.md.name)
    assert rows == [{"ts": f"{datetime.fromtimestamp(t.t0 + 65):%H:%M:%S}",
                     "end": f"{datetime.fromtimestamp(t.t0 + 68):%H:%M:%S}",
                     "de": "Wir liefern am Freitag.", "en": "We ship on Friday.", "offset": "+01:05"}]
    txt = api.meeting_txt(t.md.name)
    assert f"[{rows[0]['ts']}–{rows[0]['end']}] +01:05" in txt and "Translation: We ship on Friday." in txt


def test_old_meeting_file_still_opens(api):
    shutil.copy(FIXTURES / "old_format_meeting.md", api._data / "meeting_2026-10-09_11-14.md")
    rows = api.read_meeting("meeting_2026-10-09_11-14.md")
    assert [r["ts"] for r in rows] == ["11:35:02", "11:35:06"] and rows[0]["end"] == ""
    assert rows[0]["de"].startswith("Guten Morgen") and rows[0]["en"].startswith("Good morning")
    assert rows[0]["offset"] == "+21:02"                                    # from the file name's 11:14
    assert "[11:35:06] +21:06" in api.meeting_txt("meeting_2026-10-09_11-14.md")
    assert api.list_meetings()[0]["lines"] == 2


# ------------------------------------------------------------------ the page itself (headless browser)
def _browser():
    names = ["msedge", "chrome", "google-chrome", "chromium", "chromium-browser"]
    paths = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
             r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
             r"C:\Program Files\Google\Chrome\Application\chrome.exe",
             "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
             "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    for n in names:
        if shutil.which(n):
            return shutil.which(n)
    return next((p for p in paths if os.path.exists(p)), None)


@pytest.fixture(scope="module")
def live_page(tmp_path_factory):
    """Run tests/ui/live_scroll.html inside the real index.html and return what it reported."""
    exe = _browser()
    if not exe:
        pytest.skip("no Edge/Chrome for the headless page test")
    tmp = tmp_path_factory.mktemp("page")
    html = (WEB / "index.html").read_text(encoding="utf-8")
    html = html.replace("<head>", f'<head>\n<base href="{WEB.as_uri()}/">', 1)
    html = html.replace("</body>", (ROOT / "tests" / "ui" / "live_scroll.html").read_text(encoding="utf-8") + "\n</body>", 1)
    page = tmp / "live_scroll_page.html"
    page.write_text(html, encoding="utf-8")
    cmd = [exe, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
           f"--user-data-dir={tmp / 'profile'}", "--window-size=1280,900", "--virtual-time-budget=10000",
           "--dump-dom", page.as_uri()]
    return _run_page(exe, cmd)


def _run_page(exe, cmd, tries=3):
    # A page run takes ~2 s, but headless Edge with --dump-dom sometimes never exits: retry instead of
    # waiting minutes for one hung browser.
    for attempt in range(tries):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                 timeout=40).stdout
            break
        except subprocess.TimeoutExpired:
            if attempt == tries - 1:
                raise
    found = re.findall(r'<pre id="result">(.*?)</pre>', out, re.S)
    assert found, f"harness did not report: {out[-800:]}"
    return json.loads(html_lib.unescape(found[-1]))


def test_subtitle_bar_scrolls_and_stays_put_while_up(tmp_path):
    exe = _browser()
    if not exe:
        pytest.skip("no Edge/Chrome for the headless page test")
    html = (WEB / "overlay.html").read_text(encoding="utf-8")
    html = html.replace("<head>", f'<head>\n<base href="{WEB.as_uri()}/">', 1)
    html = html.replace("</body>", (ROOT / "tests" / "ui" / "overlay_scroll.html").read_text(encoding="utf-8") + "\n</body>", 1)
    page = tmp_path / "overlay_scroll_page.html"
    page.write_text(html, encoding="utf-8")
    r = _run_page(exe, [exe, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                        f"--user-data-dir={tmp_path / 'profile'}", "--window-size=600,130",
                        "--virtual-time-budget=5000", "--dump-dom", page.as_uri()])
    assert "error" not in r, r.get("error")
    assert r["scrollable"] and r["followsAtBottom"]
    assert r["stayedPut"] and r["keptWhileUp"] == 8
    assert r["trimmed"] == 4 and r["followsAgain"]


def test_page_scroll_up_stays_put_and_pill_counts(live_page):
    r = live_page
    assert "error" not in r, r.get("error")
    assert r["scrollable"] and r["followsAtBottom"] and r["pillHiddenWhileLive"]
    assert r["scrollTopUnchanged"]
    assert "Jump to latest" in r["pill"] and "5 new" in r["pill"]
    assert r["backAtBottom"] and r["pillHiddenAfterJump"]


def test_page_keeps_following_after_another_tab(live_page):
    r = live_page
    assert r["backFromTabAtBottom"] and r["tabStillFollowing"] and r["tabPillHidden"]


def test_page_lag_figure(live_page):
    r = live_page
    assert r["lagBefore"] == "–" and r["lag"] == "1.2s" and r["lagAfter"] == "2.5s" and r["lagAfterReset"] == "–"
    assert "live draft 0.9s" in r["lagTitle"]


def test_page_times_match_on_both_columns(live_page):
    r = live_page
    assert r["tsDe"] == r["tsEn"] and re.fullmatch(r"\d\d:\d\d:\d\d · \+01:56", r["tsDe"])
    assert r["meetDe"] == "DE · 10:00:05 · +00:05" and r["meetEn"] == "EN · 10:00:05 · +00:05"
    assert "[10:00:05 · +00:05] EN  Hello everyone.\n[10:00:05 · +00:05] DE  Hallo zusammen." in r["copy"]
    assert "[10:01:10 · +01:10] EN  Old line." in r["copy"]
