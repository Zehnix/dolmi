"""QA-7: optional cloud translator. Every provider call is mocked: no real HTTP, no stored keys."""
import json
import queue
import re
import threading
import time

import pytest

import cloud_translate
import live_subs

VOCAB = "[keep]\nNimbus Gateway\n\n[german]\nKranich = Crane\n\n[english]\nin the end effect = ultimately\n"


class FakePost:
    """Stands in for cloud_speech._post_json: records each request, answers with `reply` after `delay` s."""

    def __init__(self, reply="We ship the Nimbus Gateway on Friday.", delay=0.0, fail=None, provider="gemini"):
        self.reply, self.delay, self.fail, self.provider = reply, delay, fail, provider
        self.calls = []

    def __call__(self, url, payload, headers, timeout=30):
        self.calls.append({"url": url, "payload": payload, "headers": headers, "timeout": timeout})
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise self.fail
        if "deepl" in url:
            return {"translations": [{"text": self.reply}]}
        return {"candidates": [{"content": {"parts": [{"text": self.reply}]}}]}

    def prompt(self, i=0):
        return self.calls[i]["payload"]["contents"][0]["parts"][0]["text"]


def _translator(post, provider="gemini", timeout=1.5, vocab=VOCAB, glossary="Velorix, Halvora"):
    v = live_subs.Vocabulary(text=vocab)
    return cloud_translate.CloudTranslator(provider, "test-key", terms=lambda: (glossary, v), timeout=timeout,
                                           post=post, workers=1)


def _get(q, timeout=3.0):
    return q.get(timeout=timeout)


def test_replaces_a_final_in_place_with_glossary_and_context_in_the_prompt():
    post, q = FakePost(), queue.Queue()
    tr = _translator(post)
    ctx = [("Guten Morgen zusammen.", "Good morning everyone."), ("Wir reden über Kranich.", "We talk about Crane.")]
    assert tr.submit(7, "Wir liefern das Nimbus Gateway am Freitag.", "We deliver the nimbus gateway on Friday.",
                     "de", "de", ctx, q, session=42)
    kind, line_id, en, meta = _get(q)
    assert (kind, line_id, en) == ("final_update", 7, "We ship the Nimbus Gateway on Friday.")
    assert meta["source"] == "gemini" and meta["session"] == 42 and meta["latency"] >= 0
    prompt = post.prompt()
    assert "Velorix, Halvora" in prompt                                  # glossary spellings
    assert "Nimbus Gateway" in prompt and "Never translate" in prompt     # [keep]
    assert '"Kranich" as "Crane"' in prompt                               # [german]
    assert "Guten Morgen zusammen." in prompt and "Good morning everyone." in prompt   # context, both sides
    assert prompt.rstrip().endswith("New line (German): Wir liefern das Nimbus Gateway am Freitag.")
    assert post.calls[0]["headers"] == {"x-goog-api-key": "test-key"}
    assert post.calls[0]["timeout"] <= 1.5
    assert tr.summary()["replaced"] == 1


def test_vocabulary_rules_apply_to_the_cloud_answer():
    post, q = FakePost(reply="In the end effect Kranich works."), queue.Queue()
    tr = _translator(post)
    tr.submit(1, "Im Endeffekt läuft Kranich.", "local", "de", "de", [], q)
    _, _, en, _ = _get(q)
    assert en == "ultimately Crane works."                    # [english] fix + [german] term the model kept


def test_only_the_last_three_lines_go_as_context():
    post, q = FakePost(), queue.Queue()
    tr = _translator(post)
    ctx = [(f"Satz {i}.", f"Line {i}.") for i in range(6)]
    tr.submit(9, "Neu.", "New.", "de", "de", ctx, q)
    _get(q)
    prompt = post.prompt()
    assert "Satz 2." not in prompt and all(f"Satz {i}." in prompt for i in (3, 4, 5))


def test_slow_answer_keeps_the_local_line():
    post, q = FakePost(delay=0.4), queue.Queue()
    tr = _translator(post, timeout=0.2)
    tr.submit(3, "Wir liefern.", "We deliver.", "de", "de", [], q)
    time.sleep(0.8)
    assert q.empty()                                   # no final_update: the local subtitle stays
    assert tr.summary()["late"] == 1 and tr.summary()["replaced"] == 0


def test_a_line_queued_past_its_deadline_is_not_sent():
    post, q = FakePost(delay=0.3), queue.Queue()
    tr = _translator(post, timeout=0.25)
    tr.submit(1, "Eins.", "One.", "de", "de", [], q)
    tr.submit(2, "Zwei.", "Two.", "de", "de", [], q)    # waits behind the slow first request
    time.sleep(0.9)
    assert len(post.calls) == 1 and q.empty()
    assert tr.summary()["late"] == 2


def test_errors_keep_the_local_line_and_the_thread_alive():
    post, q = FakePost(fail=RuntimeError("HTTP 400: bad request")), queue.Queue()   # no cooldown (STR-6)
    tr = _translator(post)
    tr.submit(1, "Eins.", "One.", "de", "de", [], q)
    time.sleep(0.3)
    post.fail = None
    tr.submit(2, "Zwei.", "Two.", "de", "de", [], q)
    assert _get(q)[1] == 2 and tr.summary()["errors"] == 1


def test_same_answer_or_nothing_to_translate_sends_no_update():
    post, q = FakePost(reply="We deliver."), queue.Queue()
    tr = _translator(post)
    tr.submit(1, "Wir liefern.", "We deliver.", "de", "de", [], q)
    time.sleep(0.3)
    assert q.empty() and tr.summary()["same"] == 1
    assert not tr.submit(2, "", "", "de", "de", [], q)
    assert not tr.submit(3, "Let's ship it.", "Let's ship it.", "en", "de", [], q)   # English in a German meeting
    assert len(post.calls) == 1


def test_thinking_level_refused_falls_back_without_it():
    class Picky(FakePost):
        def __call__(self, url, payload, headers, timeout=30):
            if "thinkingConfig" in payload["generationConfig"]:
                self.calls.append({"payload": payload})
                raise RuntimeError("HTTP 400: thinking level not supported")
            return super().__call__(url, payload, headers, timeout)
    post, q = Picky(), queue.Queue()
    tr = _translator(post)
    tr.submit(1, "Wir liefern.", "We deliver.", "de", "de", [], q)
    assert _get(q)[2] == "We ship the Nimbus Gateway on Friday." and tr.thinking is None


def test_deepl_request_shape():
    post, q = FakePost(reply="We deliver the Crane."), queue.Queue()
    tr = _translator(post, provider="deepl")
    tr.key = "abc:fx"
    tr.submit(1, "Wir liefern Kranich.", "We deliver crane.", "de", "de", [("Hallo.", "Hello.")], q)
    _get(q)
    c = post.calls[0]
    assert c["url"] == "https://api-free.deepl.com/v2/translate"
    assert c["headers"] == {"Authorization": "DeepL-Auth-Key abc:fx"}
    assert c["payload"]["text"] == ["Wir liefern Kranich."] and c["payload"]["target_lang"] == "EN-US"
    assert c["payload"]["context"] == "Hallo."


def test_reply_parsing():
    assert cloud_translate.parse_reply('"We ship."') == "We ship."
    assert cloud_translate.parse_reply('{"translation": "We ship."}') == "We ship."
    assert cloud_translate.parse_reply("  We ship.\n") == "We ship."


def test_needs_a_key_and_a_known_provider():
    with pytest.raises(ValueError):
        cloud_translate.CloudTranslator("gemini", "")
    with pytest.raises(ValueError):
        cloud_translate.CloudTranslator("bing", "k")


# ------------------------------------------------------------------ app: pump and settings
def _calls(api, fn, n=1, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = [json.loads("[" + m + "]") for m in re.findall(rf"window\.dolmi\.{fn}\((.*)\)$", api._window.js(), re.M)]
        if len(found) >= n:
            return found
        time.sleep(0.02)
    raise AssertionError(f"{fn} called fewer than {n} times: {api._window.js()[-500:]}")


class Engine:
    glossary, vocab = "Velorix", live_subs.Vocabulary(text=VOCAB)

    def reload_terms(self):
        pass


def _cloud_api(api, monkeypatch, post, provider="gemini"):
    real = cloud_translate.CloudTranslator

    def make(*a, **kw):
        kw["post"] = post
        return real(*a, **kw)
    monkeypatch.setattr(cloud_translate, "CloudTranslator", make)
    monkeypatch.setattr(api, "_translator_key", lambda p: "test-key")
    api._engine = Engine()
    api.translator_set(provider)


def test_default_is_local_and_nothing_is_sent(api, monkeypatch):
    post = FakePost()
    monkeypatch.setattr(cloud_translate, "_post", post)
    assert api.translator_state()["translator"] == "local"
    api._session = {"token": object(), "stop": None, "transcript": None}
    api._ui_q.put(("final", "Hallo.", "Hello.", {"id": 1, "start": time.time() - 1, "end": time.time(), "lang": "de"}))
    _calls(api, "onLine")
    time.sleep(0.2)
    assert post.calls == [] and api._cloud_tr is None


def test_pump_replaces_the_final_in_history_transcript_and_page(api, monkeypatch, tmp_path):
    post = FakePost(reply="We ship on Friday.")
    _cloud_api(api, monkeypatch, post)
    tr = live_subs.Transcript(tmp_path, save=False)
    api._session = {"token": object(), "stop": None, "transcript": tr}
    now = time.time()
    for i, (de, en) in enumerate([("Eins.", "One."), ("Zwei.", "Two."), ("Drei.", "Three.")], 1):
        meta = {"id": i, "start": now - 1, "end": now, "lang": "de"}
        tr.add(now - 1, now, de, en, meta=meta)
        api._ui_q.put(("final", de, en, meta))
    meta = {"id": 4, "start": now - 1, "end": now, "lang": "de"}
    tr.add(now - 1, now, "Wir liefern am Freitag.", "We deliver at Friday.", meta=meta)
    api._ui_q.put(("final", "Wir liefern am Freitag.", "We deliver at Friday.", meta))
    updates = _calls(api, "onLineUpdate")
    assert [4, "We ship on Friday.", "gemini"] in [[u[0], u[1], u[2]["source"]] for u in updates]
    assert all("session" not in u[2] for u in updates)
    ts, de, en, m = api._history[3]
    assert en == "We ship on Friday." and m["en_local"] == "We deliver at Friday." and m["en_source"] == "gemini"
    assert tr.lines[4]["en"] == "We ship on Friday." and tr.lines[4]["en_source"] == "gemini"
    last = [c for c in post.calls if "Wir liefern am Freitag." in json.dumps(c["payload"], ensure_ascii=False)][-1]
    prompt = last["payload"]["contents"][0]["parts"][0]["text"]
    assert "Eins." in prompt and "Drei." in prompt and "Never translate" in prompt
    assert prompt.count("Wir liefern am Freitag.") == 1                  # the line itself is not its own context


def test_drafts_are_never_sent(api, monkeypatch):
    post = FakePost()
    _cloud_api(api, monkeypatch, post)
    api._session = {"token": object(), "stop": None, "transcript": None}
    api._ui_q.put(("draft", "Wir liefern", "We deliver", {"start": time.time() - 1, "end": time.time()}))
    _calls(api, "onDraft")
    time.sleep(0.3)
    assert post.calls == []


def test_update_for_an_earlier_meeting_or_after_stop_is_ignored(api):
    api._session = {"token": object(), "stop": None, "transcript": None}
    now = time.time()
    api._ui_q.put(("final", "Eins.", "One.", {"id": 1, "start": now - 1, "end": now}))
    _calls(api, "onLine")
    api._ui_q.put(("final_update", 1, "Better one.", {"source": "gemini", "session": 12345}))
    api._ui_q.put(("final_update", 99, "No such line.", {"source": "gemini"}))
    time.sleep(0.3)
    assert "onLineUpdate" not in api._window.js() and api._history[0][2] == "One."
    api._session = None
    api._ui_q.put(("final_update", 1, "After stop.", {"source": "gemini"}))
    time.sleep(0.2)
    assert "onLineUpdate" not in api._window.js()


def test_translator_settings_and_keys(api):
    st = api.translator_state()
    assert st["translator"] == "local" and st["status"] == "experimental - not benchmarked yet"
    assert set(st["providers"]) == {"gemini", "deepl"} and not st["providers"]["deepl"]["hasKey"]
    assert "cents" in st["providers"]["gemini"]["cost"] and "free tier" in st["providers"]["gemini"]["privacy"]
    assert not api.translator_set("bing")["ok"]
    r = api.translator_set_key("deepl", " abc:fx ")
    assert r["ok"] and r["providers"]["deepl"]["hasKey"]
    stored = api._settings["api_keys"]["deepl"]
    assert stored != "abc:fx" and api._translator_key("deepl") == "abc:fx"   # encrypted at rest
    assert api.translator_set("deepl")["translator"] == "deepl"
    import config
    assert config.load_settings()["translator"] == "deepl"


# ------------------------------------------------------------------ the page (headless browser)
def test_page_replaces_the_line_in_place_and_shows_the_setting(tmp_path):
    from pathlib import Path
    from test_live import WEB, _browser, _run_page
    exe = _browser()
    if not exe:
        pytest.skip("no Edge/Chrome for the headless page test")
    root = Path(__file__).resolve().parent
    html = (WEB / "index.html").read_text(encoding="utf-8")
    html = html.replace("<head>", f'<head>\n<base href="{WEB.as_uri()}/">', 1)
    html = html.replace("</body>", (root / "ui" / "translator_check.html").read_text(encoding="utf-8") + "\n</body>", 1)
    page = tmp_path / "translator_page.html"
    page.write_text(html, encoding="utf-8")
    r = _run_page(exe, [exe, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                        f"--user-data-dir={tmp_path / 'profile'}", "--window-size=1280,900",
                        "--virtual-time-budget=10000", "--dump-dom", page.as_uri()])
    assert "error" not in r, r.get("error")
    assert r["updated"] is True and r["missing"] is False
    assert r["en"].endswith("We ship on Friday.") and r["en"].startswith(r["ts"])     # time stamp kept
    assert r["de"].endswith("Wir liefern am Freitag.") and r["first"].endswith("One.")
    assert r["mark"] and "gemini" in r["title"]
    assert (r["wordsBefore"], r["wordsAfter"]) == ("5", "5")
    assert r["slotShown"] and r["select"] == "gemini" and r["options"] == ["local", "gemini", "deepl"]
    assert "experimental" in r["slotText"].lower() and "cents" in r["slotText"] and "Free tier warning." in r["slotText"]
    assert "2 lines improved" in r["slotText"]
