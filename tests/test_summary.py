"""APP-10 structured summary: the model answers JSON (title, summary, decisions, action items, open questions),
checked and cleaned; anything else falls back to the text. The provider is always faked: no real API calls."""
import json
import time

import pytest

import assistant
from ui import app as app_mod

GOOD = {"title": "Budget review Q4", "summary": "We went over the Q4 budget.",
        "decisions": ["Keep the launch date", ""],
        "action_items": [{"who": "Lena", "what": "Send the numbers", "when": "Friday"}, {"what": "Book a room"},
                         {"who": "Tom"}],
        "open_questions": ["Who pays for travel?"]}


def test_parse_valid_json_and_code_fence():
    s = assistant.parse_summary("```json\n" + json.dumps(GOOD) + "\n```")
    assert s["title"] == "Budget review Q4" and s["decisions"] == ["Keep the launch date"]
    assert s["action_items"] == [{"who": "Lena", "what": "Send the numbers", "when": "Friday"},
                                 {"who": "", "what": "Book a room", "when": ""}]     # one without a task dropped
    assert s["open_questions"] == ["Who pays for travel?"]


@pytest.mark.parametrize("text", ["## Summary\nPlain markdown notes", "{not json", "[1, 2]", "",
                                  json.dumps({"title": "x"}), json.dumps({"summary": 5})])
def test_parse_falls_back_on_anything_else(text):
    assert assistant.parse_summary(text) is None


def test_wrong_types_are_cleaned():
    s = assistant.parse_summary(json.dumps({"summary": "ok", "decisions": "one", "action_items": ["Call Lena"],
                                            "open_questions": [3, "Why?"], "title": None}))
    assert s == {"title": "", "summary": "ok", "decisions": [], "open_questions": ["Why?"],
                 "action_items": [{"who": "", "what": "Call Lena", "when": ""}]}


def test_email_teams_and_markdown_texts():
    s = assistant.parse_summary(json.dumps(GOOD))
    email = assistant.summary_email(s)
    assert email.startswith("Subject: Meeting notes: Budget review Q4")
    assert "- Send the numbers (Lena, by Friday)" in email and "Open questions:" in email
    teams = assistant.summary_teams(s)
    assert teams.startswith("**Budget review Q4**") and "• Keep the launch date" in teams
    md = assistant.summary_markdown(s)
    assert md.startswith("# Budget review Q4\n") and "- [ ] Book a room" in md
    assert app_mod._title_from_summary(md) == "Budget review Q4"


def run_summary(api, monkeypatch, answer, meeting=None):
    def fake_stream(provider, model, key, transcript, context=""):
        assert "Hello everyone" in transcript
        yield from (answer[i:i + 7] for i in range(0, len(answer), 7))
    monkeypatch.setattr(assistant, "stream_summary", fake_stream)
    monkeypatch.setattr(api, "_ai_key", lambda *a: "fake-key")
    assert api.summarize(meeting)["ok"]
    for _ in range(200):
        if "onSummaryDone" in api._window.js():
            return
        time.sleep(0.02)
    raise AssertionError("no onSummaryDone")


@pytest.fixture
def saved(api):
    (api._data / "meeting_2026-10-19_10-00.md").write_text(
        "**10:00:02–10:00:04** Hello everyone.  \n<sub>Hallo zusammen.</sub>\n\n", encoding="utf-8")
    return api


def test_summary_cards_reach_the_page_and_title_feeds_the_meeting(saved, monkeypatch):
    run_summary(saved, monkeypatch, json.dumps(GOOD), "meeting_2026-10-19_10-00.md")
    done = [c for c in saved._window.calls if isinstance(c, str) and "onSummaryDone" in c][-1]
    notes = json.loads(done[done.index('"",') + 3:-1])
    assert notes["title"] == "Budget review Q4" and notes["email"].startswith("Subject:")
    assert "**Budget review Q4**" in notes["teams"]
    side = json.loads((saved._data / "meeting_2026-10-19_10-00.json").read_text(encoding="utf-8"))
    assert side == {"summary_title": "Budget review Q4"}
    item = saved.list_meetings()[0]
    assert (item["title"], item["titleSource"]) == ("Budget review Q4", "summary")
    assert saved.rename_meeting("meeting_2026-10-19_10-00.md", "My own")["title"] == "My own"   # typed wins


def test_plain_text_answer_falls_back(saved, monkeypatch):
    run_summary(saved, monkeypatch, "## Summary\nJust text.", "meeting_2026-10-19_10-00.md")
    assert 'window.dolmi.onSummaryDone("",null)' in saved._window.js()
    assert (saved._data / "summary_2026-10-19_10-00.md").read_text(encoding="utf-8") == "## Summary\nJust text.\n"
    assert not (saved._data / "meeting_2026-10-19_10-00.json").exists()


def test_nothing_is_sent_without_a_key(saved):
    saved._settings["api_keys"] = {}
    r = saved.summarize("meeting_2026-10-19_10-00.md")
    assert not r["ok"] and "API key" in r["message"]
