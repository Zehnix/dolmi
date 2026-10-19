"""Optional cloud translator (QA-7, experimental, off by default): a better subtitle for finished lines.

The local line (Opus-MT) is always shown first. When Settings → Captions → "Subtitles by" is Gemini or
DeepL, each FINAL line (never a draft) is also sent to that service with the previous 2-3 German lines
and their accepted English as context and the Vocabulary's terms as rules. If the answer comes back
within TIMEOUT seconds it replaces the local subtitle in place:

    ui_q.put(("final_update", line_id, subtitle, {"source": provider, "emitted": epoch}))

Otherwise (slow, offline, quota, bad key) the local subtitle simply stays. The user's own key is used
(Settings → Assistant for Gemini; its own field for DeepL). Not adopted as a default: it must pass the
adoption rule first (eval/run_eval.py --translator gemini, see eval/README.md).
"""
import json
import queue
import threading
import time
from collections import deque

import cloud_speech
import models
import translation_router

PROVIDERS = {
    "gemini": {"label": "Gemini Flash-Lite", "model": cloud_speech.GEMINI_MODEL,
               "cost": "about 3-8 cents per meeting hour (paid tier)",
               "privacy": "Gemini's free tier lets Google use what you send to improve its products. "
                          "Use a key with billing turned on for work meetings."},
    "deepl": {"label": "DeepL", "model": "deepl",
              "cost": "about 1-1.50 € per meeting hour (DeepL API Pro, ~20 € per million characters)",
              "privacy": "DeepL API Free may keep and use what you send; DeepL API Pro does not. "
                         "Use a Pro key for work meetings."},
}
TIMEOUT = 1.5         # s from the final to the better subtitle; later answers are dropped (local line stays)
CONTEXT_LINES = 3     # previous lines (German + accepted English) sent with each request
WORKERS = 2           # requests in flight at once; more lines than that queue and usually time out
RULES_CHARS = cloud_speech.RULES_CHARS
STATUS = "experimental - not benchmarked yet"


class Skip(Exception):
    """The line is not worth sending (nothing to translate)."""


def target_language(lang, spoken):
    """Subtitle language for a line heard in `lang` while the meeting language is `spoken`."""
    if lang not in models.LANGUAGES:
        lang = spoken if spoken in models.LANGUAGES else "de"
    return models.SUBTITLES.get(lang, "en"), lang


def build_prompt(de, lang, context, glossary="", vocab=None):
    """The Gemini request text: translate one meeting line with context and the Vocabulary's rules.

    Uses the same pieces as cloud captions (cloud_speech.build_prompt): glossary spellings, [keep] terms
    as 'never translate', [german] (and [keep] with a target) as 'always translate X as Y'."""
    dst, src = target_language(lang, lang)
    src_name, dst_name = models.LANGUAGES[src], models.LANGUAGES[dst]
    rules = getattr(vocab, "rules", [])
    keep = [r for r in rules if r.section == "keep"]
    german = [r for r in rules if r.section == "german"]
    out = [f"You translate live meeting subtitles from {src_name} into natural, concise {dst_name}. "
           f"Spoken language: lines may be unfinished or split mid-sentence; translate only the new line, "
           f"keep its meaning, do not add or explain anything. Reply with the {dst_name} subtitle only."]
    if glossary:
        out.append(f"Names and terms that may come up (spell them like this): {glossary}.")
    kept = [cloud_speech._spelled(r.src) + (" (any ending)" if "*" in r.src else "") for r in keep if not r.dst]
    if kept:
        out.append("Never translate these; copy them exactly as written: "
                   + "; ".join(cloud_speech._cap(kept, RULES_CHARS)) + ".")
    pairs = [f'"{cloud_speech._spelled(r.src)}"{" (any ending)" if "*" in r.src else ""} as "{r.dst}"'
             for r in german + [r for r in keep if r.dst]]
    if pairs:
        out.append("Always translate these terms this way: " + "; ".join(cloud_speech._cap(pairs, RULES_CHARS)) + ".")
    ctx = [(d, e) for d, e in (context or [])[-CONTEXT_LINES:] if d]
    if ctx:
        out.append("Previous lines (for context only, do not translate them again):")
        out += [f"{src_name}: {d}\n{dst_name}: {e}" for d, e in ctx]
    out.append(f"New line ({src_name}): {de}")
    return "\n".join(out)


def _post(url, payload, headers, timeout):
    """cloud_speech._post_json on this thread's kept-alive connection, with this request's time limit
    (a pooled connection keeps the timeout it was opened with otherwise)."""
    host = url.removeprefix("https://").partition("/")[0]
    conn = cloud_speech._conns.__dict__.get("pool", {}).get(host)
    if conn is not None:
        conn.timeout = timeout
        if conn.sock is not None:
            conn.sock.settimeout(timeout)
    return cloud_speech._post_json(url, payload, headers, timeout=timeout, purpose="translate")


def _gemini(key, model, prompt, timeout, post, thinking="minimal"):
    config = {"temperature": 0}
    if thinking:
        config["thinkingConfig"] = {"thinkingLevel": thinking}
    body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": config}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    data = post(url, body, {"x-goog-api-key": key}, timeout=timeout)
    parts = data["candidates"][0]["content"].get("parts", [])
    return parse_reply("".join(p.get("text", "") for p in parts))


def _deepl(key, de, src, dst, context, timeout, post):
    host = "api-free.deepl.com" if key.endswith(":fx") else "api.deepl.com"
    body = {"text": [de], "source_lang": src.upper(), "target_lang": "EN-US" if dst == "en" else dst.upper()}
    ctx = " ".join(d for d, _ in (context or [])[-CONTEXT_LINES:] if d)
    if ctx:
        body["context"] = ctx                 # DeepL's own context field: not translated, not billed
    data = post(f"https://{host}/v2/translate", body, {"Authorization": f"DeepL-Auth-Key {key}"}, timeout=timeout)
    return (data.get("translations") or [{}])[0].get("text", "").strip()


class CloudTranslator:
    """Sends finals to a cloud translator in the background and puts ('final_update', ...) on ui_q.

    submit() never blocks the caption pump. Each line has TIMEOUT seconds from submit(): a request that
    starts later is skipped, an answer that arrives later is dropped; either way the local line stays.
    `post` (cloud_speech._post_json) and `clock` can be replaced in tests."""

    def __init__(self, provider, key, terms=None, timeout=TIMEOUT, post=None, clock=time.monotonic,
                 wall=time.time, workers=WORKERS):
        if provider not in PROVIDERS:
            raise ValueError(f"unknown cloud translator {provider!r}")
        if not key:
            raise ValueError(f"no {PROVIDERS[provider]['label']} key saved")
        self.provider, self.key, self.timeout = provider, key, timeout
        self.model = PROVIDERS[provider]["model"]
        self.terms = terms or (lambda: ("", None))   # () -> (glossary text, live_subs.Vocabulary or None)
        self.post = post or _post
        self.thinking = "minimal"   # Gemini: lowest thinking level; dropped if the model refuses it
        self.clock, self.wall = clock, wall
        self.stats = {"sent": 0, "replaced": 0, "same": 0, "late": 0, "errors": 0, "skipped": 0}
        self.latency = deque(maxlen=200)
        self.cooldown = translation_router.Cooldown(clock=clock)   # STR-6: skip it a while after 429/5xx
        self._jobs = queue.Queue()
        self._lock = threading.Lock()
        self._closed = False
        self._workers = workers
        for _ in range(workers):
            threading.Thread(target=self._serve, daemon=True, name="cloud-translate").start()

    def close(self):
        self._closed = True
        for _ in range(self._workers):
            self._jobs.put(None)

    # ---- the caption pump hands finals here
    def submit(self, line_id, de, local_en, lang, spoken, context, ui_q, session=None):
        """Queue one final line. Returns False when it is not sent (nothing to translate).
        `session` comes back in the update's meta, so a late answer never lands on a later meeting's line."""
        if self._closed or not de or not de.strip():
            return False
        dst, src = target_language(lang, spoken)
        if src == dst or (src == "en" and spoken != "en"):
            return False      # English said in a German meeting: shown as heard, nothing to translate
        if not self.cooldown.ready(self.provider):
            self._count("skipped")   # cooling down: the local line stays
            return False
        self._jobs.put((self.clock(), line_id, de, local_en, src, dst, list(context or []), ui_q, session))
        return True

    def _serve(self):
        while True:
            job = self._jobs.get()
            if job is None:
                return
            try:
                self._run(*job)
            except Exception as e:                        # never let one line end the thread
                self._count("errors")
                print(f"cloud translator: line {job[1]} kept local ({type(e).__name__}: {str(e)[:200]})")

    def _count(self, key):
        with self._lock:
            self.stats[key] += 1

    def translate(self, de, src, dst, context, timeout):
        """One request; returns the subtitle with the Vocabulary's rules applied ('' = no answer)."""
        glossary, vocab = self.terms()
        if self.provider == "gemini":
            prompt = build_prompt(de, src, context, glossary, vocab)
            try:
                en = _gemini(self.key, self.model, prompt, timeout, self.post, self.thinking)
            except RuntimeError as e:
                if not (self.thinking and "thinking" in str(e).lower()):
                    raise
                self.thinking = None              # this model takes no thinking level: ask without one
                en = _gemini(self.key, self.model, prompt, timeout, self.post, None)
        else:
            en = parse_reply(_deepl(self.key, de, src, dst, context, timeout, self.post))
        if vocab is not None and en:
            en = vocab.enforce(de, en)    # locked terms the model wrote its own way -> the wanted spelling
        return en

    def _run(self, submitted, line_id, de, local_en, src, dst, context, ui_q, session=None):
        left = self.timeout - (self.clock() - submitted)
        if left <= 0.05:
            self._count("late")
            print(f"cloud translator: line {line_id} kept local (queued {self.clock() - submitted:.2f} s)")
            return
        self._count("sent")
        try:
            en = self.translate(de, src, dst, context, left)
        except Exception as e:
            reason = translation_router.cooling_reason(e)
            if reason and reason != "local-only":
                self.cooldown.hit(self.provider, reason)
            raise
        took = self.clock() - submitted
        self.latency.append(took)
        if took > self.timeout:
            self._count("late")
            print(f"cloud translator: line {line_id} kept local (answer after {took:.2f} s)")
            return
        if not en or en == local_en:
            self._count("same")
            return
        self._count("replaced")
        meta = {"source": self.provider, "emitted": self.wall(), "latency": round(took, 3)}
        if session is not None:
            meta["session"] = session
        ui_q.put(("final_update", line_id, en, meta))

    def summary(self):
        lat = sorted(self.latency)
        with self._lock:
            out = dict(self.stats)
        out["latency_p50"] = lat[len(lat) // 2] if lat else None
        return out


def parse_reply(text):
    """Gemini sometimes wraps the subtitle in JSON or quotes; return the plain line."""
    t = (text or "").strip()
    if t.startswith("{"):
        try:
            d = json.loads(t)
            return str(d.get("translation") or d.get("subtitle") or "").strip()
        except ValueError:
            pass
    return t.strip('"').strip()
