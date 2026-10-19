r"""Run: venv\Scripts\python.exe tests\test_webview_bridge.py      (Windows)
     Mac/venv/bin/python tests/test_webview_bridge.py         (macOS, from the repo root)

Headless tests for the Dolmi webview Api (no GUI). Uses a temp data folder + temp repo files so
nothing real is touched. Prints PASS/FAIL per check and a summary. New checks belong in pytest
files (tests/test_<area>.py with the `api` fixture from conftest.py), not here."""
import os, sys, time, tempfile, shutil, inspect
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # repo root
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "src"))
import config
from ui import app as app_mod

SEP = "\\" if os.name == "nt" else "/"           # this OS's path separator, for the traversal checks
FS_ROOT = "C:\\" if os.name == "nt" else "/"

results = []
def check(name, ok, extra=""):
    results.append(ok); print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))

calls = []
save_as = []   # what the fake Save dialog returns next
class FakeWindow:
    def evaluate_js(self, js): calls.append(js)
    def create_file_dialog(self, *a, **k): calls.append(('dialog', k)); return save_as.pop() if save_as else None

# sandbox: point data folder + repo files (settings/vocab) at temp dirs
tmp_root = Path(tempfile.mkdtemp()); tmp_data = Path(tempfile.mkdtemp()); tmp_cfg = Path(tempfile.mkdtemp()) / "Dolmi"
for f in ("vocabulary.example.txt", "glossary.example.txt"):
    shutil.copy(ROOT / f, tmp_root / f)
(tmp_root / "glossary.txt").write_text("OldRootTerm" + chr(10), encoding="utf-8")   # a user's file in the old location
config.ROOT = tmp_root; config.CONFIG = tmp_cfg; config.SETTINGS = tmp_cfg / "settings.json"
config.save_settings({**config.DEFAULTS, "data_folder": str(tmp_data)})

api = app_mod.Api(); api._window = FakeWindow()

# 0. config lives in %APPDATA%\Dolmi (Mac: ~/Library/Application Support/Dolmi): old files copied over
#    once, missing ones seeded from examples
check("old glossary copied to the config folder", (tmp_cfg / "glossary.txt").read_text(encoding="utf-8") == "OldRootTerm" + chr(10))
check("vocabulary seeded from the example", (tmp_cfg / "vocabulary.txt").exists())
check("settings saved in the config folder, not the program folder", (tmp_cfg / "settings.json").exists() and not (tmp_root / "settings.json").exists())
check("version reported", bool(api.state()["version"]))

# 1. nothing private is exposed to page JS (pywebview exposes public attributes recursively)
public_attrs = [k for k in vars(api) if not k.startswith("_")]
check("no public (JS-exposed) attributes", not public_attrs, public_attrs)
exposed = sorted(n for n, _ in inspect.getmembers(api, callable) if not n.startswith("_"))
check("exposed API = methods only", "window" not in exposed and "toggle_invisible" in exposed, ", ".join(exposed))

# 2. state
s = api.state()
need = {"language","showGerman","invisible","provider","providers","model","hasKey","speechModel",
        "listening","engine","audioDevice","saveTranscripts","keepDays","overlay","theme"}
check("state() has all keys", need <= set(s), sorted(need - set(s)))

# 3. caption pump: no session -> late results dropped; with session -> emitted
api._ui_q.put(("final", "spÃ¤t", "late")); time.sleep(0.3)
check("late result after Stop is dropped", not any("onLine" in c for c in calls))
api._session = {"token": object(), "stop": None, "transcript": None}
api._ui_q.put(("final", "Ich habe den MCP getestet.", "I tested the MCP."))
api._ui_q.put(("draft", "Wir brauchen", "We need")); time.sleep(0.4)
j = "\n".join(calls)
check("onLine emitted", "window.dolmi.onLine(" in j and "I tested the MCP." in j)
check("onDraft emitted", "window.dolmi.onDraft(" in j)
check("history captured", len(api._history) == 1 and api._history[0][2] == "I tested the MCP.")
api._session = None

# 4. start/stop token: a stale _begin must not resurrect a stopped session
api._engine = type("E", (), {"device": "cpu", "model_name": "fake", "language": "de"})()
tok = object(); api._session = {"token": tok, "stop": None, "transcript": None}
api.stop_listening()
api._begin(tok)                        # would start threads if the token check were missing
check("stopped session not resurrected by stale _begin", api._session is None)
api._engine = None

# 5. meetings: parse + path-traversal guard
(tmp_data / "meeting_2026-10-09_11-14.md").write_text(
    "# Meeting\n\n**11:35:02** Awesome, no Cloud browser.  \n<sub>Geil, kein Cloud Browser.</sub>\n\n"
    "**11:35:06** We need no code.  \n<sub>Wir brauchen keinen Code.</sub>\n\n", encoding="utf-8")
items = api.list_meetings(); pairs = api.read_meeting("meeting_2026-10-09_11-14.md")
check("list_meetings", len(items) == 1 and items[0]["lines"] == 2 and items[0]["time"] == "11:14")
check("read_meeting maps de/en correctly", len(pairs) == 2 and pairs[0]["en"].startswith("Awesome") and pairs[0]["de"].startswith("Geil"))
outside = tmp_root / "meeting_secret.md"; outside.write_text("**1** x  \n<sub>y</sub>\n", encoding="utf-8")
check("path traversal blocked (read)", api.read_meeting(".." + SEP + tmp_root.name + SEP + "meeting_secret.md") == [] and api.read_meeting(str(outside)) == [])
api.delete_meeting(str(outside))
check("path traversal blocked (delete)", outside.exists())
out_txt = tmp_root / "export"
save_as.append((str(out_txt),))            # user picks a name without .txt, as a tuple like Cocoa returns
r = api.export_meeting("meeting_2026-10-09_11-14.md")
txt = (tmp_root / "export.txt").read_text(encoding="utf-8") if r.get("ok") else ""
check("export writes captions + translation to a .txt", r.get("ok") and r["path"].endswith("export.txt")
      and "Caption:     Geil, kein Cloud Browser." in txt and "Translation: Awesome, no Cloud browser." in txt
      and "[11:35:06]" in txt and txt.startswith("Dolmi meeting — "))
check("export suggests the meeting's name as .txt", any(isinstance(c, tuple) and c[1].get("save_filename") == "meeting_2026-10-09_11-14.txt" for c in calls))
check("cancelled export writes nothing", api.export_meeting("meeting_2026-10-09_11-14.md") == {"ok": False, "cancelled": True})
check("export refuses paths outside the data folder", api.export_meeting(str(outside)).get("ok") is False)

# 6. vocab: saving the glossary must never touch vocabulary.txt
vocab_before = (tmp_cfg / "vocabulary.txt").read_text(encoding="utf-8")
api.save_vocab(None, "Northwind\nMCP\nKnowledge Base")
check("glossary saved", (tmp_cfg / "glossary.txt").read_text(encoding="utf-8").startswith("Northwind\nMCP"))
check("vocabulary.txt untouched", (tmp_cfg / "vocabulary.txt").read_text(encoding="utf-8") == vocab_before)

# 7. settings whitelist + atomic persistence
check("whitelisted key accepted", api.set_setting("theme", "ink") and config.load_settings()["theme"] == "ink")
check("non-whitelisted key rejected", api.set_setting("api_keys", {}) is False and api.set_setting("data_folder", FS_ROOT) is False)
check("unknown provider ignored", api.set_provider("evil")["provider"] in ("claude", "openai"))
for prov, hint in (("gemini", "AIza"), ("nvidia", "nvapi-")):
    st = api.set_provider(prov)
    check(f"{prov} provider selectable with its key hint", st["provider"] == prov and st["keyHint"].startswith(hint))
st = api.set_ai_model("nvidia/llama-3.1-nemotron-70b-instruct")
check("custom assistant model saved per provider", st["model"] == "nvidia/llama-3.1-nemotron-70b-instruct" and bool(config.load_settings()["ai_models"].get("nvidia")))
st = api.set_ai_model("")
check("empty model restores the default", st["model"] == st["modelDefault"] and "nvidia" not in config.load_settings()["ai_models"])
api.set_provider("gemini")
r = api.list_ai_models()
check("Gemini defaults to gemini-3.8-flash; no key -> built-in list", r["default"] == "gemini-3.8-flash"
      and r["current"] == "gemini-3.8-flash" and r["live"] is False and r["models"][0] == "gemini-3.8-flash")
st = api.set_provider("cloudflare")
check("Cloudflare selectable; no key until account ID and token are both saved", st["provider"] == "cloudflare" and not st["hasKey"])
check("bad Cloudflare account ID rejected", api.set_cf_account("not-an-id")["ok"] is False)
api.set_key("cf-token-123")
check("token alone isn't enough", api.state()["hasKey"] is False)
r = api.set_cf_account("0123456789abcdef0123456789ABCDEF")
check("account ID saved (lower-cased); key = account:token", r["ok"] and r["hasKey"]
      and api._ai_key() == "0123456789abcdef0123456789abcdef:cf-token-123")
api.set_key(""); api.set_cf_account("")
api.set_provider("claude")
check("27 languages and Auto-detect can be spoken; unknown codes can't",
      api.set_setting("language", "fr") is True and api.set_setting("language", "xx") is False)
api.set_setting("language", "auto")
check("Auto-detect needs the multilingual translator", "opus-mul" in api._needed_models())
api.set_setting("language", "en")
check("English speech needs the English -> German translator", "opus-en" in api._needed_models() and "opus-de" not in api._needed_models())
api._history = [("10:00:00", "We ship on Friday.", "Wir liefern am Freitag.")]
check("the Assistant reads the English side (what was heard)", api._english("We ship on Friday.", "Wir liefern am Freitag.") == "We ship on Friday.")
api.set_setting("language", "de")
check("captions source must be local or a speech provider", api.set_setting("speech_source", "claude") is False
      and api.set_setting("speech_source", "gemini") and api.state()["speechSource"] == "gemini")
check("Gemini cloud captions need no local models", api._needed_models() == [] and api.missing_models() == [])
api.set_setting("speech_source", "openai")
check("OpenAI cloud captions still translate locally", api._needed_models() == ["opus-de"])
api.set_setting("speech_source", "local")
check("German speech: the Assistant reads the English subtitle", api._english("Wir liefern am Freitag.", "We ship on Friday.") == "We ship on Friday.")
api._history = []

# 8. summary guards (no API key in the sandbox)
r = api.summarize("meeting_2026-10-09_11-14.md")
check("summarize(meeting) needs a key", r["ok"] is False and "API key" in r["message"])
check("summarize() with no live session", api.summarize()["ok"] is False)

# 9. assistant chats: list / open (continues the chat) / new / delete
import inbox
db = inbox.connect(api._db_path)
cid = inbox.start_chat(db, "claude", "m"); inbox.add_message(db, cid, "What was decided?", "Upload comes later.", "", "typed"); db.close()
chats = api.list_chats()
check("list_chats", len(chats) == 1 and chats[0]["title"] == "What was decided?" and chats[0]["questions"] == 1)
msgs = api.open_chat(cid)
check("open_chat returns messages + continues chat", len(msgs) == 1 and api._chat_id == cid and len(api._ai_history) == 2)
api.new_chat(); check("new_chat resets", api._chat_id is None and api._ai_history == [])
check("search filters", api.list_chats("nothing-matches-xyz") == [])
api.delete_chat(cid); check("delete_chat", api.list_chats() == [])

# 10. answer loop survives without a key and still reports done
calls.clear(); api.ask("Hello?"); time.sleep(1.5)
check("ask -> onAsk + onAnswerDone (error reported, loop alive)", any("onAsk(" in c for c in calls) and any("onAnswerDone(" in c for c in calls))
calls.clear(); api.ask("Second?"); time.sleep(1.5)
check("answer loop still alive for a 2nd question", any("onAnswerDone(" in c for c in calls))

# 11. archive: meetings + chats move between the normal and archived lists
api.archive_meeting("meeting_2026-10-09_11-14.md", True)
check("archived meeting leaves the main list", api.list_meetings() == [] and len(api.list_meetings(True)) == 1)
api.archive_meeting("meeting_2026-10-09_11-14.md", False)
check("unarchive restores it", len(api.list_meetings()) == 1)
check("archive blocks path traversal", api.archive_meeting(".." + SEP + "x" + SEP + "meeting_a.md") is False)
db = inbox.connect(api._db_path)
cid2 = inbox.start_chat(db, "claude", "m"); inbox.add_message(db, cid2, "Keep this?", "Yes.", "", "typed"); db.close()
api.archive_chat(cid2, True)
ids = lambda chats: {c["id"] for c in chats}
check("archived chat hidden / listed as archived", cid2 not in ids(api.list_chats()) and cid2 in ids(api.list_chats("", True)))
api.delete_chat(cid2)
check("deleting an archived chat clears its archive flag", cid2 not in api._archived()["chats"])

# 12. clipboard, bar settings, PC detection, models
check("copy_text round-trip", api.copy_text("Dolmi ✓ Copy") is True)
check("opacity/font settable", api.set_setting("opacity", 0.6) and api.set_setting("font", 28)
      and config.load_settings()["opacity"] == 0.6)
pc = api.pc_info()
check("pc_info detects this PC", pc["ram_gb"] > 0 and pc["cores"] > 0 and isinstance(pc["gpu"], bool) and bool(pc["model_dir"]),
      f"{pc['gpu_name']} · {pc['ram_gb']} GB · gpu={pc['gpu']}")
ms = api.list_models()
check("list_models: speech models + the needed translator", any(m["kind"] == "speech" for m in ms)
      and any(m["needed"] and m["kind"] == "translate" for m in ms))
check("remove_model rejects unknown keys", api.remove_model("nope")["ok"] is False)
check("list_models marks Auto's pick and the selected model",
      sum(m["auto"] for m in ms) == 1 and not any(m["selected"] for m in ms))

# 13. the user picks models; Start never downloads without asking
import models
check("use_model rejects unknown keys and translators",
      api.use_model("nope") is False and api.use_model(models.translator_key("de")) is False)
check("use_model picks a speech model", api.use_model("tiny") and config.load_settings()["model"] == "tiny"
      and next(m for m in api.list_models() if m["key"] == "tiny")["selected"])
tiny_missing = not models.is_installed(models.BY_KEY["tiny"])
missing = api.missing_models()
check("missing_models lists exactly what isn't downloaded", ("tiny" in {m["key"] for m in missing}) == tiny_missing
      and all(m["sizeMb"] > 0 for m in missing))
fake = [{"key": "large-v3", "name": "Whisper Large v3", "sizeMb": 3091}]
real_missing, engine = api.missing_models, api._engine
api.missing_models, api._engine = (lambda: fake), None
r = api.start_listening()
check("Start asks before downloading (no session started)", r["ok"] is False and r["missing"] == fake
      and api._session is None)
api.missing_models, api._engine = real_missing, engine
api.use_model("auto")
check("use_model('auto') restores Auto", config.load_settings()["model"] == "auto")

# 14. audio source: speaker (online meeting) or microphone (in person)
import threading as _th
check("set_source rejects unknown sources", api.set_source("radio") is False)
lab = api.set_source("mic")
check("set_source('mic') switches, is saved and labelled", lab["source"] == "mic" and bool(lab["device"])
      and config.load_settings()["audio_source"] == "mic" and api.state()["audioSource"] == "mic")
check("toggle_source flips back to the speaker", api.toggle_source()["source"] == "speaker"
      and api.state()["sourceLabel"]["source"] == "speaker")
check("mic_devices returns a list", isinstance(api.mic_devices(), list), f"{len(api.mic_devices())} mics")
check("mic_device is settable", api.set_setting("mic_device", "") is True)
fake_cap, saved = _th.Event(), api._session
api._session, api._cap_stop = {"token": object(), "stop": _th.Event()}, fake_cap
api.set_source("mic")
check("switching mid-meeting reopens capture on the new source", fake_cap.is_set())
api._session, api._cap_stop = saved, None
api.set_source("speaker")

print(f"\n{sum(results)}/{len(results)} passed")

