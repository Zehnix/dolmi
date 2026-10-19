"""APP-9 meetings: a title per meeting (typed, from the summary, or the first meaningful line), search across
every transcript (case- and accent-insensitive, with the matching lines) and a date filter."""
import json
import os

import pytest

from ui import app as app_mod


def line(t, en, de):
    return f"**{t}–{t}** {en}  \n<sub>{de}</sub>\n\n"


@pytest.fixture
def meetings(api):
    d = api._data
    (d / "meeting_2026-10-19_10-00.md").write_text(
        "<!-- started 10:00:00 -->\n" + line("10:00:02", "Hello everyone.", "Hallo zusammen.")
        + line("10:00:05", "Okay.", "Okay.")
        + line("10:00:09", "Let us talk about the budget for the café in Zürich.", "Lass uns über das Budget für das Café in Zürich reden.")
        + line("10:00:20", "The size of the order is fine.", "Die Größe der Bestellung passt."), encoding="utf-8")
    (d / "meeting_2026-10-12_09-30.md").write_text(
        line("09:30:01", "We plan the release for the next sprint.", "Wir planen das Release für den nächsten Sprint."),
        encoding="utf-8")
    (d / "meeting_2026-09-01_14-15.md").write_text(
        line("14:15:00", "Thanks.", "Danke."), encoding="utf-8")
    (d / "summary_2026-10-12_09-30.md").write_text(
        "## Summary\n\n**Sprint planning for release 2.3.** The team agreed on dates.\n\n## Decisions\n- ship", encoding="utf-8")
    return api


def by_name(items):
    return {m["name"]: m for m in items}


def test_titles_from_lines_summary_and_fallback(meetings):
    items = by_name(meetings.list_meetings())
    a = items["meeting_2026-10-19_10-00.md"]
    assert a["title"] == "Let us talk about the budget for the café in Zürich."    # greeting and 'Okay.' skipped
    assert a["titleSource"] == "lines" and a["date"] == "2026-10-19" and a["lines"] == 4
    b = items["meeting_2026-10-12_09-30.md"]
    assert (b["title"], b["titleSource"]) == ("Sprint planning for release 2.3.", "summary")
    c = items["meeting_2026-09-01_14-15.md"]
    assert (c["title"], c["titleSource"]) == ("Thanks.", "lines")                   # nothing better there


def test_english_speech_titles_use_what_was_said(meetings):
    meetings._settings["language"] = "en"
    a = by_name(meetings.list_meetings())["meeting_2026-10-19_10-00.md"]
    assert a["title"].startswith("Lass uns über das Budget")                       # 'de' holds the speech


def test_long_titles_are_shortened_on_a_word():
    t = app_mod._title_from_lines(["word " * 40])
    assert len(t) <= app_mod.TITLE_MAX + 1 and t.endswith("…") and not t.endswith(" …")
    assert app_mod._title_from_lines([]) == ""


def test_summary_title_skips_generic_headings():
    assert app_mod._title_from_summary("# Meeting notes\n\nTitle: Q4 hiring plan\n") == "Q4 hiring plan"
    assert app_mod._title_from_summary("## Zusammenfassung\n## Decisions\n") == ""


def test_rename_writes_a_sidecar_and_empty_restores_auto(meetings):
    name = "meeting_2026-10-19_10-00.md"
    r = meetings.rename_meeting(name, "  Budget\x00 review  ")
    assert r == {"ok": True, "title": "Budget review", "titleSource": "custom"}
    side = meetings._data / "meeting_2026-10-19_10-00.json"
    assert json.loads(side.read_text(encoding="utf-8")) == {"title": "Budget review"}
    assert by_name(meetings.list_meetings())[name]["title"] == "Budget review"
    assert len(meetings.rename_meeting(name, "x" * 500)["title"]) == app_mod.TITLE_EDIT_MAX
    r = meetings.rename_meeting(name, "")
    assert r["titleSource"] == "lines" and not side.exists()


def test_rename_keeps_other_sidecar_keys(meetings):
    side = meetings._data / "meeting_2026-10-19_10-00.json"
    side.write_text('{"speakers": {"S1": "Lena"}}', encoding="utf-8")
    meetings.rename_meeting("meeting_2026-10-19_10-00.md", "Budget")
    assert json.loads(side.read_text(encoding="utf-8")) == {"speakers": {"S1": "Lena"}, "title": "Budget"}
    meetings.rename_meeting("meeting_2026-10-19_10-00.md", "")
    assert json.loads(side.read_text(encoding="utf-8")) == {"speakers": {"S1": "Lena"}}


@pytest.mark.parametrize("bad", ["../meeting_x.md", "..\\meeting_x.md", "C:\\Windows\\meeting_x.md", "notes.md",
                                 "meeting_missing.md", None])
def test_rename_refuses_paths_outside_the_data_folder(meetings, bad, tmp_path):
    outside = meetings._data.parent / "meeting_x.md"
    outside.write_text(line("1", "a", "b"), encoding="utf-8")
    assert meetings.rename_meeting(bad, "Hacked")["ok"] is False
    assert not (meetings._data.parent / "meeting_x.json").exists()
    assert meetings.rename_meeting("meeting_2026-10-19_10-00.md", 42)["ok"] is False


def test_delete_removes_the_sidecar(meetings):
    meetings.rename_meeting("meeting_2026-10-19_10-00.md", "Budget")
    meetings.delete_meeting("meeting_2026-10-19_10-00.md")
    assert not (meetings._data / "meeting_2026-10-19_10-00.json").exists()


@pytest.mark.parametrize("query", ["BUDGET", "cafe zurich", "Café", "grosse", "GRÖSSE", "größe bestellung"])
def test_search_ignores_case_and_accents(meetings, query):
    r = meetings.search_meetings(query)
    assert [m["name"] for m in r] == ["meeting_2026-10-19_10-00.md"], query
    assert r[0]["matches"] == 1 and r[0]["hits"][0]["ts"] in ("10:00:09", "10:00:20")
    assert set(r[0]["hits"][0]) == {"ts", "offset", "de", "en"}


def test_search_finds_a_meeting_by_a_word_said_in_it_in_either_language(meetings):
    assert [m["name"] for m in meetings.search_meetings("sprint")] == ["meeting_2026-10-12_09-30.md"]
    assert [m["name"] for m in meetings.search_meetings("nächsten")] == ["meeting_2026-10-12_09-30.md"]
    assert meetings.search_meetings("nothing like this") == []


def test_search_matches_titles_too(meetings):
    meetings.rename_meeting("meeting_2026-09-01_14-15.md", "Kick-off Nordwind")
    r = meetings.search_meetings("nordwind")
    assert [m["name"] for m in r] == ["meeting_2026-09-01_14-15.md"] and r[0]["matches"] == 0


def test_search_hits_are_capped(meetings):
    d = meetings._data
    (d / "meeting_2026-10-20_08-00.md").write_text("".join(line(f"08:00:{i:02d}", f"budget {i}", f"Budget {i}")
                                                          for i in range(10)), encoding="utf-8")
    r = by_name(meetings.search_meetings("budget"))["meeting_2026-10-20_08-00.md"]
    assert r["matches"] == 10 and len(r["hits"]) == app_mod.SEARCH_HITS


def test_date_filter(meetings):
    names = lambda r: [m["name"] for m in r]
    assert names(meetings.search_meetings("", False, "2026-10-01", "2026-10-31")) == [
        "meeting_2026-10-19_10-00.md", "meeting_2026-10-12_09-30.md"]
    assert names(meetings.list_meetings(False, "2026-10-12", "2026-10-12")) == ["meeting_2026-10-12_09-30.md"]
    assert names(meetings.search_meetings("", False, "2026-10-13", "")) == ["meeting_2026-10-19_10-00.md"]
    assert len(meetings.search_meetings("", False, "junk", "../x")) == 3           # bad dates = no limit
    assert names(meetings.search_meetings("budget", False, "2026-09-01", "2026-09-30")) == []


def test_search_respects_the_archive(meetings):
    meetings.archive_meeting("meeting_2026-10-12_09-30.md", True)
    assert meetings.search_meetings("sprint") == []
    assert [m["name"] for m in meetings.search_meetings("sprint", True)] == ["meeting_2026-10-12_09-30.md"]


def test_search_survives_a_long_or_odd_query(meetings):
    assert meetings.search_meetings("a" * 5000) == []
    assert len(meetings.search_meetings(None)) == 3
    assert len(meetings.search_meetings("   ")) == 3
