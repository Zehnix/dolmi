import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import live_subs as m

def test_timestamp():
    assert m.Transcript._ts(3725.5) == "01:02:05,500"

def test_junk_filter():
    assert m.is_junk("Untertitel im Auftrag des ZDF")
    assert m.is_junk("..")
    assert not m.is_junk("Wir starten das Projekt am Montag.")

def test_transcript_files(tmp_path):
    t = m.Transcript(tmp_path)
    t.add(t.t0, t.t0 + 2.5, "Hallo zusammen", "Hello everyone")
    assert "Hello everyone" in t.md.read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:02,500" in t.srt.read_text(encoding="utf-8")

def test_worker_segments_on_pause(tmp_path):
    import queue, threading, time, numpy as np
    class Fake:
        context = ""
        def transcribe(self, a): return f"Satz mit {len(a)} Samples.", "de"
        def translate(self, text, lang): return "Sentence"
    aq, uq, stop = queue.Queue(), queue.Queue(), threading.Event()
    tr = m.Transcript(tmp_path)
    loud = lambda a: 0.0 if np.sqrt(np.mean(a[-1600:] ** 2)) > m.SILENCE_RMS else None   # sine = "speech"
    th = threading.Thread(target=m.worker, args=(Fake(), aq, uq, tr, stop, loud), daemon=True)
    th.start()
    speech = (0.1 * np.sin(np.arange(1600) / 5)).astype(np.float32)   # 0.1 s voiced
    for _ in range(10): aq.put(speech); time.sleep(0.1)                # 1 s speech
    for _ in range(10): aq.put(np.zeros(1600, np.float32)); time.sleep(0.1)  # 1 s silence
    time.sleep(0.3); stop.set()
    finals = []
    while not uq.empty():
        k, de, en, *_ = uq.get()
        if k == "final": finals.append(en)
    assert finals == ["Sentence"] and tr.n == 1

def test_transcript_off_writes_nothing(tmp_path):
    t = m.Transcript(tmp_path, save=False)
    t.add(0, 1, "Hallo", "Hello")
    assert t.md is None and not list(tmp_path.iterdir())

def test_delete_old_files_only_removes_old_dolmi_files(tmp_path):
    import os, time
    old, new, other = tmp_path / "meeting_old.md", tmp_path / "summary_new.md", tmp_path / "notes.md"
    for f in (old, new, other):
        f.write_text("x", encoding="utf-8")
    month_ago = time.time() - 31 * 86400
    os.utime(old, (month_ago, month_ago)); os.utime(other, (month_ago, month_ago))
    assert m.delete_old_files(tmp_path, 0) == 0          # "Never" keeps everything
    assert m.delete_old_files(tmp_path, 30) == 1
    assert not old.exists() and new.exists() and other.exists()   # user's own files are never touched

def test_summary_sends_transcript_and_cuts_very_long_ones():
    import assistant
    sent = {}
    def fake(model, key, system, messages, max_tokens):
        sent.update(system=system, text=messages[0]["content"], max_tokens=max_tokens)
        yield "## Summary"
    assistant._claude = fake
    out = "".join(assistant.stream_summary("claude", "m", "k", "x" * (assistant.MAX_TRANSCRIPT_CHARS + 50), "We use FastAPI"))
    assert out == "## Summary" and sent["max_tokens"] == 2048
    assert "cut for length" in sent["text"] and "We use FastAPI" in sent["system"]

def test_glossary_echo_is_dropped_but_real_sentences_stay():
    terms = {"crm", "alice", "bob", "carol", "acme", "falcon"}
    echo = "CRM, Perketer, Luzer, Erdmann, Kolor, Alice, Alice, Bob, Carol, Michel, Zellow, und Marseille."
    assert m.echoes_terms(echo, terms) == ""
    for real in ("Alice und Bob prüfen morgen das CRM-Release.", "Wir brauchen Tests, Logs, Metriken, Alerts und ein Dashboard."):
        assert m.echoes_terms(real, terms) == real

def test_whisper_loops_are_said_once():
    assert m.collapse_repeats("Er ist ein sehr guter Mann. Er ist ein sehr guter Mann. Hallo.") == \
        "Er ist ein sehr guter Mann. Hallo."
    assert m.collapse_repeats("We can hear it somewhere We can hear it somewhere We can hear it somewhere") == \
        "We can hear it somewhere"
    assert m.collapse_repeats("Das ist sehr sehr gut.") == "Das ist sehr sehr gut."

def test_assistant_answers_as_the_user_with_meeting_lines():
    import assistant
    sent = {}
    def fake(model, key, system, messages, max_tokens=1024):
        sent.update(system=system, last=messages[-1]["content"])
        yield "I'm Alex."
    assistant._openai = fake
    out = "".join(assistant.stream_answer("openai", "m", "k", "What is your name?", context="I'm Alex, a developer.",
                                          meeting=["Welcome to the interview."]))
    assert out == "I'm Alex."
    assert "as the user" in sent["system"] and "Never say you are an AI" in sent["system"]
    assert "I'm Alex, a developer." in sent["system"]
    assert "Welcome to the interview." in sent["last"] and sent["last"].endswith("Question: What is your name?")

def test_interview_style_questions_are_heard():
    import assistant
    asked = ["Tell me about yourself.", "Introduce yourself", "So, walk us through your last project.",
             "Have you worked with Kubernetes", "What's your biggest weakness", "Okay and how did you scale it",
             "I'd like to know why you left your last company.", "Give me an example of a hard bug.",
             "Reverse a linked list", "Implement an LRU cache.", "Given an array, find the longest substring"]
    said = ["We start at nine tomorrow.", "Thanks, that was great.", "Okay.", "I agree with Alice."]
    assert all(assistant.is_question(q) for q in asked), [q for q in asked if not assistant.is_question(q)]
    assert not any(assistant.is_question(s) for s in said), [s for s in said if assistant.is_question(s)]

def test_inbox_keeps_each_session_as_a_chat(tmp_path):
    import inbox
    db = inbox.connect(tmp_path / "dolmi.db")
    first = inbox.start_chat(db, "openai", "gpt-5.4-mini", started_at="2026-10-01T09:00:00+00:00")
    inbox.add_message(db, first, "What is RAG?", "Retrieval plus generation.")
    second = inbox.start_chat(db, "openai", "gpt-5.4-mini")
    inbox.add_message(db, second, "Reverse a linked list", "```python\nprev = None\n```", source="typed")
    inbox.add_message(db, second, "And the complexity?", "", error="No credit left")
    inbox.start_chat(db)   # a session where nobody asked anything is not listed
    chats = inbox.list_chats(db)
    assert [c["title"] for c in chats] == ["Reverse a linked list", "What is RAG?"]   # newest first
    assert chats[0]["questions"] == 2
    assert [c["title"] for c in inbox.list_chats(db, "retrieval")] == ["What is RAG?"]   # searches answers too
    assert "No credit left" in inbox.as_markdown(db, second)
    assert inbox.delete_older_than(db, 3) == 1 and len(inbox.list_chats(db)) == 1   # old chat gone
    inbox.delete_chat(db, second)
    assert inbox.list_chats(db) == [] and inbox.messages(db, second) == []   # messages deleted with the chat

def test_inbox_imports_answers_saved_before_it_existed(tmp_path):
    import inbox
    (tmp_path / "assistant_2026-10-06.md").write_text(
        "### 13:14:48 — Tell me about yourself.\n\nI'm Alex.\n\n### 13:30:31 — What is RAG?\n\nRetrieval.\n\n",
        encoding="utf-8")
    db = inbox.connect(tmp_path / "dolmi.db")
    [chat] = inbox.list_chats(db)
    assert chat["title"] == "Tell me about yourself." and chat["questions"] == 2
    assert [m["answer"] for m in inbox.messages(db, chat["id"])] == ["I'm Alex.", "Retrieval."]

def test_gemini_and_nvidia_use_their_openai_compatible_endpoints():
    import assistant
    calls = []
    def fake(label, model, key, system, messages, base_url=None, **options):
        calls.append((label, model, base_url, options))
        yield "<think>plan the answer</think>  It"
        yield "'s fine."
    real, assistant._openai_stream = assistant._openai_stream, fake
    try:
        g = "".join(assistant.stream_answer("gemini", "gemini-3-flash-preview", "k", "Is it fine?"))
        n = "".join(assistant.stream_summary("nvidia", "nvidia/nemotron-3-super-120b-a12b", "k", "[10:00] hi"))
    finally:
        assistant._openai_stream = real
    assert g == n == "It's fine."
    assert calls[0][0] == "Gemini (Google)" and "generativelanguage.googleapis.com" in calls[0][2]
    assert calls[0][3] == {"reasoning_effort": "low"}
    assert calls[1][0] == "NVIDIA" and calls[1][2] == "https://integrate.api.nvidia.com/v1" and calls[1][3] == {}

def test_inline_thinking_is_dropped_even_when_tags_are_split():
    import assistant
    pieces = ["<thi", "nk>secret", " plan</th", "ink>\n\n", " Answer <", "b>bold</b>"]
    assert "".join(assistant.strip_thinking(pieces)) == "Answer <b>bold</b>"
    assert "".join(assistant.strip_thinking(["no thinking here"])) == "no thinking here"
    assert "".join(assistant.strip_thinking(["a < b", " and c"])) == "a < b and c"

def test_languages_get_english_subtitles_and_english_gets_german():
    import models
    assert len(models.LANGUAGES) == 28 and models.SUBTITLES["fr"] == "en" and models.SUBTITLES["en"] == "de"
    de, en = models.BY_KEY[models.translator_key("de")], models.BY_KEY[models.translator_key("en")]
    assert de.repo == "gaudi/opus-mt-de-en-ctranslate2" and en.repo == "gaudi/opus-mt-en-de-ctranslate2"
    assert models.translator_key("fr") == "opus-fr" and models.BY_KEY["opus-fr"].repo == "gaudi/opus-mt-fr-en-ctranslate2"
    # languages without their own translator, and Auto-detect, use the multilingual -> English model
    assert models.translator_key("pt") == models.translator_key("auto") == "opus-mul"
    assert models.BY_KEY["opus-mul"].repo == "gaudi/opus-mt-mul-en-ctranslate2"

def test_cloudflare_puts_the_account_id_in_the_endpoint():
    import assistant
    calls = []
    def fake(label, model, key, system, messages, base_url=None, **options):
        calls.append((key, base_url)); yield "ok"
    real, assistant._openai_stream = assistant._openai_stream, fake
    try:
        out = "".join(assistant.stream_answer("cloudflare", "@cf/meta/llama-3.3-70b-instruct-fp8-fast", "acc123:tok", "Hi?"))
        try:
            "".join(assistant.stream_answer("cloudflare", "m", "tok-without-account", "Hi?"))
            missing = False
        except assistant.AssistantError as e:
            missing = "Account ID" in str(e)
    finally:
        assistant._openai_stream = real
    assert out == "ok" and calls[0] == ("tok", "https://api.cloudflare.com/client/v4/accounts/acc123/ai/v1")
    assert missing

def test_cloud_captions_send_whole_sentences_and_parse_gemini():
    import json, queue, threading, time, numpy as np
    import cloud_speech
    sent, drafts = [], []
    class Fake:
        cloud, DRAFT_EVERY = True, 0.5
        def submit(self, audio, start, end, ui_q, tr, meta=None): sent.append(len(audio) / m.SR)
        def draft(self, audio, ui_q, meta=None): drafts.append(len(audio) / m.SR)
    audio_q, ui_q, stop = queue.Queue(), queue.Queue(), threading.Event()
    speaking = threading.Event(); speaking.set()
    def detect(_): return 0.0 if speaking.is_set() else None
    t = threading.Thread(target=m.worker, args=(Fake(), audio_q, ui_q, None, stop, detect)); t.start()
    for i in range(30):                    # like a capture: 100 ms chunks, 1.5 s speech then silence
        if i == 15:
            speaking.clear()
        audio_q.put(np.full(1600, 0.1 if speaking.is_set() else 0.0, np.float32))
        time.sleep(0.1)
    stop.set(); t.join()
    assert len(sent) == 1 and sent[0] >= 1.4 and ui_q.empty()   # one whole sentence ...
    assert drafts and drafts[0] >= 0.8                         # ... with live drafts while it was spoken

    eng = cloud_speech.CloudEngine.__new__(cloud_speech.CloudEngine)
    eng.provider, eng.key, eng.language, eng.model_name, eng.thinking, eng.context = "gemini", "k", "de", "gemini-3.1-flash-lite", "minimal", ""
    eng.glossary, eng.vocab, eng._requests = "Acme", m.Vocabulary(m.Path("/nonexistent")), 0
    calls = []
    def fake_post(url, body, headers, timeout=30):
        calls.append(body)
        if "thinkingConfig" in body["generationConfig"]:
            raise RuntimeError("HTTP 400: thinking level is not supported for this model")
        text = json.dumps({"heard": "Wir starten am Montag.", "translation": "We start on Monday."})
        return {"candidates": [{"content": {"parts": [{"text": text}]}}]}
    real, cloud_speech._post_json = cloud_speech._post_json, fake_post
    try:
        assert eng.recognize(np.zeros(16000, np.float32)) == ("Wir starten am Montag.", "We start on Monday.")
    finally:
        cloud_speech._post_json = real
    assert eng.thinking is None and len(calls) == 3 and "Acme" in calls[2]["contents"][0]["parts"][0]["text"]

def test_cloud_finals_show_in_spoken_order_even_when_replies_arrive_out_of_order():
    import queue, time, numpy as np
    import cloud_speech
    eng = cloud_speech.CloudEngine.__new__(cloud_speech.CloudEngine)
    import threading
    eng._jobs, eng._lock, eng._requests = queue.Queue(), threading.Lock(), 0
    def serve():
        while True:
            fn, args = eng._jobs.get(); fn(*args)
    for _ in range(4):
        threading.Thread(target=serve, daemon=True).start()
    eng._utterance = eng._shown = 0; eng._pending = {}; eng._drafting = False; eng._last_error = 0.0
    eng.context, eng.provider, eng.language = "", "gemini", "de"
    def slow_first(audio):
        if audio[0] == 1:
            time.sleep(0.4)
        return (f"Satz {int(audio[0])}", f"Sentence {int(audio[0])}")
    eng.recognize = slow_first
    class Tr:
        rows = []
        def add(self, s, e, heard, sub, meta=None): self.rows.append(heard)
    ui_q, tr = queue.Queue(), Tr()
    for n in (1, 2, 3):
        eng.submit(np.full(16000, n, np.float32), 0, 1, ui_q, tr)
    time.sleep(0.8)
    finals = [a for kind, a, b, *_ in list(ui_q.queue) if kind == "final"]
    assert finals == ["Satz 1", "Satz 2", "Satz 3"] and tr.rows == finals

def test_english_speech_detected_when_another_language_is_set():
    """German selected, someone speaks English: kept as English, not forced into German."""
    from types import SimpleNamespace as NS
    calls = []
    def fake_asr(audio, language):
        calls.append(language)
        detected = language or audio   # audio stands in for what Whisper would detect
        return [NS(text="Hello everyone")], NS(language=detected)
    e = object.__new__(m.Engine)
    e.language, e.terms, e._asr = "de", set(), fake_asr
    e.vocab = NS(finish=lambda text, slots=None: text)
    assert e.transcribe("en") == ("Hello everyone", "en") and calls == [None]
    assert e.translate("Hello everyone", "en") == "Hello everyone"
    calls.clear()
    assert e.transcribe("nl")[1] == "de" and calls == [None, "de"]   # misdetection -> forced German
