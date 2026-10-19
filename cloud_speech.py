"""Cloud captions: speech goes to a speech API instead of the local Whisper model.

Settings → Captions by: "local" (Whisper on this computer, private, the default) or a provider whose
API key is saved in Settings → Assistant:

  gemini      one request hears AND translates (Gemini understands audio); gemini-3.1-flash-lite,
              minimal thinking: about 1 s per request
  openai      gpt-4o-mini-transcribe hears it; Opus-MT translates it locally
  cloudflare  Workers AI Whisper Large v3 Turbo hears it; Opus-MT translates it locally

Kept fast on purpose:
  * a draft request every DRAFT_EVERY s while someone talks (two may be in flight), shown as the live line, so text
    appears about a second after the words instead of after the sentence;
  * long speech is cut at short breaths (live_subs.worker), so captions keep flowing;
  * requests run in parallel (finals are still shown in order) on kept-alive HTTPS connections.
"""
import base64
import http.client
import io
import itertools
import json
import ssl
import threading
import time
import queue
import wave

import numpy as np

import live_subs
import models

PROVIDERS = {"gemini": "Gemini", "openai": "OpenAI", "cloudflare": "Cloudflare Workers AI"}
GEMINI_MODEL = "gemini-3.1-flash-lite"           # ~1 s with minimal thinking; 3.8 Flash takes ~2 s
OPENAI_MODEL = "gpt-4o-mini-transcribe"
CLOUDFLARE_MODEL = "@cf/openai/whisper-large-v3-turbo"
DRAFT_EVERY = 0.8     # s between draft requests while someone is talking
DRAFTS_IN_FLIGHT = 2  # so drafts keep their pace even when a request takes ~1 s
WORKERS = 4           # parallel requests (finals are still shown in the order spoken)
_ERROR_EVERY = 30.0   # s between repeated error toasts
FINAL_RETRIES = 1     # a failed request for a finished sentence is sent once more before the line is dropped
RETRY_DELAY = 0.3     # s before that retry


def wav_bytes(audio, rate=live_subs.SR):
    """16 kHz float32 samples -> a 16-bit mono WAV file in memory."""
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out.getvalue()


_conns = threading.local()   # one kept-alive HTTPS connection per host per thread: no TLS handshake per request


def _post_json(url, payload, headers, timeout=30):
    host, _, path = url.removeprefix("https://").partition("/")
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", **headers}
    pool = _conns.__dict__.setdefault("pool", {})
    for attempt in (1, 2):   # a kept-alive connection the server closed: reconnect once
        conn = pool.get(host)
        if conn is None:
            conn = pool[host] = http.client.HTTPSConnection(host, timeout=timeout,
                                                            context=ssl.create_default_context())
        try:
            conn.request("POST", "/" + path, body, headers)
            r = conn.getresponse()
            data = r.read()
        except (OSError, http.client.HTTPException):
            conn.close()
            pool.pop(host, None)
            if attempt == 2:
                raise
            continue
        if r.status >= 400:
            raise RuntimeError(f"HTTP {r.status}: {data.decode(errors='replace')[:300]}")
        return json.loads(data)


PREVIOUS_CHARS = 200     # previous sentence(s) sent as context (SEG-11)
RULES_CHARS = 4000       # Gemini: at most this much of each rule list per request (the rest: Vocabulary.enforce)
WHISPER_PROMPT_CHARS = 600   # Whisper-style prompts keep ~223 tokens; terms first, the previous text last


def _cap(items, limit):
    """Leading items whose '; '-joined text stays within limit characters."""
    out, size = [], 0
    for x in items:
        size += len(x) + 2
        if size > limit:
            break
        out.append(x)
    return out


def _spelled(term):
    """A rule term as a spelling hint: 'toll*' -> 'toll' (the '*' is Dolmi's, not a word)."""
    return term.replace("*", "").strip()


def build_prompt(glossary, vocab, previous, lang, style="gemini"):
    """The text part of a cloud caption request, from the same files the local path uses.

    style "gemini": hear AND translate. Spelling hints, [keep] as 'never translate', [german] as 'translate
      X as Y' ([keep] with a target as 'write X as Y'), and the previous sentence as context.
    style "openai": gpt-4o-mini-transcribe only transcribes but follows a short instruction prompt: the same
      terms as spellings ([keep] and the German side of [german]) and the previous sentence.
    style "whisper": Cloudflare's Whisper reads the prompt as text said just before: a plain term list, then
      the previous sentence, within ~223 tokens.
    The translation rules of openai/whisper captions apply locally (Engine.translate protects them)."""
    rules = getattr(vocab, "rules", [])
    keep = [r for r in rules if r.section == "keep"]
    german = [r for r in rules if r.section == "german"]
    previous = (previous or "")[-PREVIOUS_CHARS:].strip()
    spellings = [t for t in ([x.strip() for x in (glossary or "").split(",")] +
                             [_spelled(r.src) for r in keep + german]) if t]
    spellings = list(dict.fromkeys(spellings))   # first spelling wins, no repeats
    if style == "whisper":
        text = ", ".join(_cap(spellings, WHISPER_PROMPT_CHARS - len(previous)))
        return " ".join(x for x in (text + "." if text else "", previous) if x)
    if style == "openai":
        if not (spellings or previous):
            return ""
        src = models.LANGUAGES.get(lang)
        out = f"Live meeting{' in ' + src if src else ''}."
        if spellings:
            out += " Spell these names and terms exactly like this: " + ", ".join(_cap(spellings, RULES_CHARS)) + "."
        if previous:
            out += f' The previous sentence was: "{previous}"'
        return out
    src = models.LANGUAGES.get(lang)   # None for Auto-detect
    dst = models.LANGUAGES[models.SUBTITLES.get(lang, "en")]
    spoken = f"spoken in {src}" if src else "in any language"
    out = (f"This is speech from a live meeting, {spoken}; it may end mid-sentence. Transcribe "
           f"exactly what is said, in the language it is spoken in, then translate it into natural {dst}. Return JSON with the "
           f'keys "heard" and "translation". If there is no clear speech (silence, music, noise), '
           f"return empty strings; never invent words.")
    if glossary:
        out += f" Names and terms that may come up (spell them like this): {glossary}."
    kept = [_spelled(r.src) + (" (any ending)" if "*" in r.src else "") for r in keep if not r.dst]
    if kept:
        out += (" Never translate these; copy them into the translation exactly as written: "
                + "; ".join(_cap(kept, RULES_CHARS)) + ".")
    pairs = [f'"{_spelled(r.src)}"{" (any ending)" if "*" in r.src else ""} as "{r.dst}"'
             for r in german + [r for r in keep if r.dst]]
    if pairs:
        out += " Always translate these terms this way: " + "; ".join(_cap(pairs, RULES_CHARS)) + "."
    if previous:
        out += f' The previous sentence was: "{previous}".'
    return out


class CloudEngine(live_subs.Engine):
    """Same interface as live_subs.Engine (translate, reload_terms, translators, ...), but the
    speech recognition (and for Gemini the translation) happens in the cloud."""
    cloud = True
    DRAFT_EVERY = DRAFT_EVERY
    line_ids = itertools.count(1)   # final caption ids; live_subs.worker starts a new count per session
    on_drop = None                  # live_subs.worker sets its drop(reason, start, end, text) here

    def __init__(self, provider, api_key, language="de", model=""):
        if provider not in PROVIDERS:
            raise RuntimeError(f"{provider} can't caption speech; pick Gemini, OpenAI or Cloudflare")
        if not api_key:
            raise RuntimeError(f"add your {PROVIDERS[provider]} API key in Settings → Assistant first")
        self.provider, self.key, self.language = provider, api_key, language
        self.model_name = model or {"gemini": GEMINI_MODEL, "openai": OPENAI_MODEL,
                                    "cloudflare": CLOUDFLARE_MODEL}[provider]
        self.device = "cpu"   # for the local translator (OpenAI/Cloudflare); captions come from the cloud
        self.translators, self.context = {}, ""
        self.thinking = "minimal"   # Gemini: the lowest thinking level the model accepts (None = don't ask)
        self.reload_terms()
        if provider != "gemini":   # these only transcribe: translate locally, like local captions
            self._translator(models.translator_key(language), download=True)
        self._jobs = queue.Queue()
        self._lock = threading.Lock()
        self._utterance = 0          # id of the sentence being spoken now
        self._shown = 0              # last sentence whose final caption was shown
        self._pending = {}           # sentence id -> result, waiting for earlier sentences
        self._drafting = 0           # draft requests in flight
        self._draft_seq = self._draft_shown = 0   # newest draft sent / shown (an older reply never wins)
        self._last_error = 0.0
        self._requests = 0
        for _ in range(WORKERS):     # daemon threads: never keep the app from quitting
            threading.Thread(target=self._serve, daemon=True, name="cloud-captions").start()
        print(f"☁️ Cloud captions: {PROVIDERS[provider]} · {self.model_name}")

    def _serve(self):
        """One request thread. It opens its HTTPS connection right away, so the first captions don't
        pay ~0.5 s for DNS + TLS (best effort: otherwise it connects on first use)."""
        host = {"gemini": "generativelanguage.googleapis.com", "cloudflare": "api.cloudflare.com"}.get(self.provider)
        if host:
            try:
                conn = http.client.HTTPSConnection(host, timeout=30, context=ssl.create_default_context())
                conn.connect()
                _conns.__dict__.setdefault("pool", {})[host] = conn
            except OSError:
                pass
        while True:
            fn, args = self._jobs.get()
            try:
                fn(*args)
            except Exception as e:
                print(f"cloud captions: {e}")

    # ---- the worker hands audio here (from live_subs.worker; never blocks it)
    def draft(self, audio, ui_q, meta=None):
        """Live line for the sentence still being spoken; skipped while the previous draft is in flight."""
        with self._lock:
            if self._drafting >= DRAFTS_IN_FLIGHT:
                return
            self._drafting += 1
            self._draft_seq += 1
            uid, seq = self._utterance + 1, self._draft_seq
        self._jobs.put((self._run_draft, (audio.copy(), uid, seq, ui_q, meta)))

    def submit(self, audio, start, end, ui_q, transcript, meta=None):
        """A finished sentence: recognized in parallel with others, shown in the order spoken.
        meta carries the speaker fields (see live_subs.UI_Q_CONTRACT)."""
        with self._lock:
            self._utterance += 1
            uid = self._utterance
        self._jobs.put((self._run_final, (audio.copy(), uid, start, end, ui_q, transcript, meta)))

    def _run_draft(self, audio, uid, seq, ui_q, meta=None):
        try:
            heard, subtitle = self.recognize(audio)
        except Exception as e:
            heard = subtitle = ""
            print(f"cloud draft: {e}")
        with self._lock:
            self._drafting -= 1
            # only for the sentence still being spoken, and never older than a draft already shown
            show = uid == self._utterance + 1 and uid > self._shown and seq > self._draft_shown
            if show and heard and not live_subs.is_junk(heard, lang=self.language):
                self._draft_shown = seq
                ui_q.put(("draft", heard, subtitle, {**(meta or {}), "emitted": time.time()}))

    def _drop(self, reason, start, end, text=""):
        if self.on_drop:
            self.on_drop(reason, start, end, text)
        else:
            print(f"Dropped a cloud line ({reason}): {ascii(text[:80])}")

    def _run_final(self, audio, uid, start, end, ui_q, tr, meta=None):
        result, failed = ("", ""), None
        for attempt in range(1 + FINAL_RETRIES):   # a dropped connection or a busy server: try again once
            try:
                result, failed = self.recognize(audio), None
                break
            except Exception as e:
                failed = e
                print(f"cloud captions (attempt {attempt + 1}): {e}")
                if attempt < FINAL_RETRIES:
                    time.sleep(RETRY_DELAY)
        if failed is not None and time.time() - self._last_error > _ERROR_EVERY:
            self._last_error = time.time()
            ui_q.put(("error", f"{PROVIDERS[self.provider]} captions failed: {failed}", ""))
        energy = live_subs.peak_rms(audio)
        with self._lock:
            self._pending[uid] = (result, start, end, dict(meta or {}), failed, energy)
            while self._shown + 1 in self._pending:          # show finished sentences in order
                self._shown += 1
                (heard, subtitle), s, e, m, err, rms = self._pending.pop(self._shown)
                heard, subtitle = self._dedupe(self._shown, m.pop("overlap", 0), heard, subtitle)
                if err is not None:
                    reason = "error"
                elif not heard:
                    reason = "empty"
                else:
                    reason = live_subs.junk_reason(heard, rms, self.language)
                if reason:
                    self._previous = (self._shown, "", "")
                    self._drop(reason, s, e, heard)
                else:
                    line = {**m, "id": next(self.line_ids), "start": s, "end": e, "emitted": time.time(),
                            "lang": self.language, "speaker": m.get("speaker"),
                            "speaker_conf": m.get("speaker_conf")}
                    ui_q.put(("final", heard, subtitle, line))
                    tr.add(s, e, heard, subtitle, meta=line)
                    self._previous = (self._shown, heard, subtitle)
                    self.context = heard
                if self._shown == self._utterance:           # nothing newer is being spoken
                    ui_q.put(("draft", "", "", {k: m[k] for k in ("session", "utt") if k in m}))

    def _dedupe(self, uid, overlap, heard, subtitle):
        """A cut without a pause sends the last CLOUD_OVERLAP s twice: drop the repeated words from
        the line after it (and from its translation: Gemini's own, or a new local one)."""
        prev_uid, prev_heard, prev_sub = getattr(self, "_previous", (None, "", ""))
        if not (overlap and heard and prev_heard and prev_uid == uid - 1):
            return heard, subtitle
        trimmed = live_subs.dedupe_overlap(prev_heard, heard)
        if trimmed == heard:
            return heard, subtitle
        if not trimmed:
            return "", ""
        if self.provider == "gemini":
            return trimmed, live_subs.dedupe_overlap(prev_sub, subtitle)
        return trimmed, self.translate(trimmed, self.language)

    # ---- one piece of speech: audio -> (what was said, its translation)
    def recognize(self, audio):
        self._requests += 1
        if self._requests == 1:
            print(f"☁️ First request: {len(audio) / live_subs.SR:.1f} s of speech")
        if self.provider == "gemini":
            return self._gemini(audio)
        heard = self._openai(audio) if self.provider == "openai" else self._cloudflare(audio)
        heard = live_subs.collapse_repeats(heard.strip())
        return heard, (self.translate(heard, self.language) if heard else "")

    def _gemini(self, audio):
        prompt = build_prompt(self.glossary, self.vocab, self.context, self.language)
        body = {"contents": [{"parts": [{"text": prompt}, {"inline_data": {
                    "mime_type": "audio/wav", "data": base64.b64encode(wav_bytes(audio)).decode()}}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0}}
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent"
        while True:
            level = self.thinking
            if level:
                body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": level}
            else:
                body["generationConfig"].pop("thinkingConfig", None)
            try:
                data = _post_json(url, body, {"x-goog-api-key": self.key})
                break
            except RuntimeError as e:
                if not (level and "thinking" in str(e).lower()):
                    raise
                # this model refuses that level: try the next one up, then no thinking config at all
                self.thinking = {"minimal": "low"}.get(level)
        parts = data["candidates"][0]["content"].get("parts", [])
        out = json.loads("".join(p.get("text", "") for p in parts) or "{}")
        heard = live_subs.collapse_repeats((out.get("heard") or "").strip())
        return heard, live_subs.collapse_repeats(self.vocab.enforce(heard, (out.get("translation") or "").strip()))

    def _openai(self, audio):
        import openai
        client = openai.OpenAI(api_key=self.key, timeout=30.0)
        r = client.audio.transcriptions.create(
            model=self.model_name, file=("sentence.wav", wav_bytes(audio), "audio/wav"),
            language=openai.NOT_GIVEN if self.language == "auto" else self.language,
            prompt=build_prompt(self.glossary, self.vocab, self.context, self.language, "openai") or openai.NOT_GIVEN)
        return r.text

    def _cloudflare(self, audio):
        account, _, token = self.key.partition(":")
        payload = {"audio": base64.b64encode(wav_bytes(audio)).decode()}
        if self.language != "auto":
            payload["language"] = self.language
        prompt = build_prompt(self.glossary, self.vocab, self.context, self.language, "whisper")
        if prompt:
            payload["initial_prompt"] = prompt
        data = _post_json(f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{self.model_name}",
                          payload, {"Authorization": f"Bearer {token}"})
        return (data.get("result") or {}).get("text", "")
