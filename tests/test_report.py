"""APP-12 'Report a problem': a zip saved where the user picks with the log, the settings without any key or
token, the version, OS and model choice. Transcripts and vocabulary only when asked. No network."""
import json
import socket
import zipfile

import pytest

import config
from ui import app as app_mod

FAKE_KEY = "sk-ant-api03-AbCdEf1234567890GhIjKlMn"
FAKE_BLOB = "AQAAANCMnd8BFdERjHoAwE/Cl+sBAAAA0123456789abcdefABCDEF=="
CF_ID = "0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the report reached the network")
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)


@pytest.fixture
def rep(api, tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "dolmi.log").write_text(f"started\nusing key {FAKE_KEY}\nerror in meeting_2026-10-19_10-00.md\n",
                                    encoding="utf-8")
    monkeypatch.setattr(config, "LOGS", logs)
    api._settings.update({"api_keys": {"claude": FAKE_BLOB, "gemini": "x"}, "cf_account": CF_ID, "pro": "unlocked",
                          "ai_context": f"I work at Acme, my token is {FAKE_KEY}", "model": "large-v3",
                          "translator_keys": {"deepl": "abc:fx"}, "theme": "ink"})
    (api._data / "meeting_2026-10-19_10-00.md").write_text("**10:00** Hello.\n", encoding="utf-8")
    (config.CONFIG / "vocabulary.txt").write_text("Nordlicht\n", encoding="utf-8")
    return api


def save(api, tmp_path, *flags):
    api._window.save_as.append(str(tmp_path / "out" / "report"))
    (tmp_path / "out").mkdir(exist_ok=True)
    r = api.report_problem(*flags)
    assert r["ok"], r
    z = zipfile.ZipFile(r["path"])
    return r, {n: z.read(n).decode("utf-8") for n in z.namelist()}


def test_report_has_log_info_and_scrubbed_settings(rep, tmp_path):
    r, files = save(rep, tmp_path)
    assert r["path"].endswith("report.zip")
    assert sorted(files) == ["dolmi.log", "info.json", "settings.json"]      # no transcripts, no vocabulary
    whole = "\n".join(files.values())
    for secret in (FAKE_KEY, FAKE_BLOB, CF_ID, "abc:fx", "unlocked"):
        assert secret not in whole
    s = json.loads(files["settings.json"])
    assert s["api_keys"] == "[removed]" and s["cf_account"] == "[removed]" and s["translator_keys"] == "[removed]"
    assert s["theme"] == "ink" and "Acme" in s["ai_context"] and "[removed]" in s["ai_context"]
    info = json.loads(files["info.json"])
    assert info["app_version"] == app_mod.VERSION and info["os"] and info["speech_model"] == "large-v3"
    assert "meeting_2026-10-19_10-00.md" in files["dolmi.log"]                # ordinary names survive


def test_transcripts_and_vocabulary_only_when_ticked(rep, tmp_path):
    _, files = save(rep, tmp_path, True, True)
    assert "transcripts/meeting_2026-10-19_10-00.md" in files and files["vocabulary/vocabulary.txt"] == "Nordlicht\n"


def test_cancelled_dialog_writes_nothing(rep):
    assert rep.report_problem() == {"ok": False, "cancelled": True}


@pytest.mark.parametrize("value", [FAKE_KEY, "AIzaSyD-1234567890abcdefghijklmnop", "nvapi-abcDEF123456",
                                   "ghp_" + "a1" * 18, CF_ID])
def test_key_like_values_are_redacted(value):
    assert value not in app_mod.redact_text(f"value={value} end")
    assert app_mod.scrub_settings({"note": value}) == {"note": "[removed]"} or value not in json.dumps(
        app_mod.scrub_settings({"note": value}))
