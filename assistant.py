"""Dolmi Assistant mode: answers spoken English technical questions with a fast cloud model.

Providers: Claude (Anthropic), OpenAI, Gemini (Google), NVIDIA or Cloudflare Workers AI. The last
three are reached through their OpenAI-compatible endpoints, so the openai package talks to all four. API keys are encrypted for the
signed-in user (Windows DPAPI, or a secret in the macOS login Keychain; see dolmi_platform), so only
that user on that computer can read them; settings.json never holds a key in plain text.
"""
import base64, getpass, hashlib, hmac, json, os, re

import dolmi_platform

PROVIDERS = {
    "claude": {"label": "Claude (Anthropic)", "model": "claude-haiku-4-5",
               "key_hint": "sk-ant-…", "console": "console.anthropic.com"},
    "openai": {"label": "OpenAI", "model": "gpt-5.4-mini",
               "key_hint": "sk-…", "console": "platform.openai.com"},
    "gemini": {"label": "Gemini (Google)", "model": "gemini-3.8-flash",
               "key_hint": "AIza…", "console": "aistudio.google.com/apikey",
               "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/"},
    "nvidia": {"label": "NVIDIA", "model": "nvidia/nemotron-3-super-120b-a12b",
               "key_hint": "nvapi-…", "console": "build.nvidia.com",
               "base_url": "https://integrate.api.nvidia.com/v1"},
    # Cloudflare needs the account ID too: the app passes "<account id>:<API token>" as the key
    "cloudflare": {"label": "Cloudflare Workers AI", "model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
                   "key_hint": "API token", "console": "dash.cloudflare.com → AI → Workers AI → Use REST API",
                   "base_url": "https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1"},
}

SYSTEM = """You are the user's live copilot in meetings and interviews. Other people are talking to \
the user; their speech was transcribed, so expect small transcription errors and infer what was meant.

For each question, write what the user can say back: first person, as the user, in natural spoken \
English they can read out loud. Never say you are an AI, a language model or ChatGPT.

- Questions about the user (background, experience, projects, education, strengths and weaknesses, \
motivation, goals, opinions, how they work, "tell me about a time when…"): answer confidently as \
the user, built from the profile below. Never refuse, never say something wasn't shared, never say \
you'd rather not answer. If the profile doesn't cover it, give a natural answer that fits the profile, \
keeping concrete facts (numbers, dates, names) general instead of making them up.
- Technical questions: answer correctly and confidently, the way the user would explain it in an \
interview, and tie it to the user's real experience from the profile wherever it fits ("In my last role I…", \
"When I built … I used …"). Topics include AI and LLMs, agents (LangGraph, MCP, tool use), RAG and \
retrieval, embeddings, evaluation, ML fundamentals, Python and FastAPI, full-stack and web \
development, APIs, SQL and NoSQL databases, Docker and Kubernetes, cloud (Azure, AWS, GCP), DevOps \
and CI/CD, security, system design, and data structures and algorithms.
- Data structures and algorithms or coding questions: say the approach in one or two sentences, \
give time and space complexity (Big-O), mention the key edge case, then a short Python solution. \
If a brute force exists, name it first, then the better approach.

Format, for reading at a glance:
1. First line: the direct answer in one sentence.
2. Then 2-4 short bullet points with key details.
3. Only if code clearly helps (always for coding questions): one short Python code block.
Every message gets an answer. If it isn't clearly a question, treat it as said to the user and \
reply the way the user would.
Earlier questions and answers, and the last lines of the meeting, are included: use them for \
follow-ups like "and how did that scale?"."""

LENGTHS = {
    "short": "Keep the words under 150 (a code block doesn't count).",
    "detailed": "Go deeper when it helps: up to 400 words, still answer-first, with one example.",
}
HISTORY_MESSAGES = 10   # last 5 question/answer pairs travel with each new question

SUMMARY_SYSTEM = """You turn a meeting transcript into notes for someone who attended. The transcript \
comes from speech recognition and machine translation, so expect small errors: infer what was meant, \
but never invent facts, names, numbers or dates.

Answer with one JSON object only (no Markdown, no code fence), in English, with exactly these keys:
{"title": "a short title for the meeting, at most 8 words",
 "summary": "3-6 sentences: what the meeting was about and where it ended",
 "decisions": ["one decision per item"],
 "action_items": [{"who": "owner or empty", "what": "the task", "when": "deadline or empty"}],
 "open_questions": ["one question per item"]}
Use empty lists when there are none and empty strings for an owner or deadline nobody said.
Be brief and skip small talk."""
MAX_TRANSCRIPT_CHARS = 120_000   # ~2-3 hours of speech; older lines are cut beyond this

# ------------------------------------------------------------------ Pro unlock
# Only a salted PBKDF2 hash of the password is used, and it lives in .env (DOLMI_PRO_SALT /
# DOLMI_PRO_HASH), never in the source. This is a local check: it keeps the feature out of reach
# of normal users, but anyone editing the source can bypass it. Real paid licensing needs
# server-side license keys. See .env.example for how to generate these for your own password.
def _pro_secret(name):
    try:
        return bytes.fromhex(os.environ.get(name, ""))
    except ValueError:
        return b""

def check_password(password):
    salt, expected = _pro_secret("DOLMI_PRO_SALT"), _pro_secret("DOLMI_PRO_HASH")
    if not salt or not expected:
        return False   # Assistant unlock not configured (set DOLMI_PRO_SALT / DOLMI_PRO_HASH in .env)
    attempt = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return hmac.compare_digest(attempt, expected)

def _pro_token():
    return f"dolmi-pro:{getpass.getuser()}"

def pro_unlock_value():
    """Stored in settings; encrypted for this user (DPAPI / Keychain), so it can't be copied to another computer."""
    return encrypt_key(_pro_token())

def is_pro(stored):
    return decrypt_key(stored) == _pro_token()

# Speech recognition often drops the "?", and interviewers ask with commands ("Tell me about…",
# "Walk us through…") or after a filler ("So, …"), so all of those count as questions.
QUESTION_START = re.compile(
    r"^(?:(?:so|okay|ok|and|now|alright|well|then|right|um|uh|yeah)[,.\s]+)*"
    r"(what|why|how|when|where|which|who|whose|can|could|should|would|is|are|do|does|did|will|have|has|"
    r"had|were|was|any|explain|describe|tell|walk|compare|introduce|share|give|talk|list|name|define|"
    r"summari[sz]e|elaborate|imagine|suppose|let's|difference between|"
    # coding-interview tasks are said as commands: "Reverse a linked list", "Implement an LRU cache"
    r"reverse|implement|write|find|design|build|sort|merge|detect|check if|given|count|calculate|solve|"
    r"optimi[sz]e|convert|validate|traverse|search)\b", re.I)
QUESTION_ANYWHERE = re.compile(
    r"\?|\b(tell (me|us)|can you|could you|would you|do you|did you|have you|are you|were you|"
    r"what's your|what is your|how do you|how would you|i'd like to know|i want to know|i'm curious)\b", re.I)

def is_question(text):
    t = text.strip()
    return len(t.split()) >= 2 and bool(QUESTION_START.match(t) or QUESTION_ANYWHERE.search(t))

# ------------------------------------------------------------------ key storage (dolmi_platform.protect)
def encrypt_key(key):
    return base64.b64encode(dolmi_platform.protect(key.encode())).decode() if key else ""

def decrypt_key(stored):
    try:
        return dolmi_platform.unprotect(base64.b64decode(stored)).decode() if stored else ""
    except (OSError, ValueError):
        return ""

# ------------------------------------------------------------------ answering
class AssistantError(Exception):
    """A problem worth showing the user as-is (bad key, no credit, network)."""

def build_system(context="", length="short"):
    system = f"{SYSTEM}\n{LENGTHS.get(length, LENGTHS['short'])}"
    if context.strip():
        system += f"\n\nThe user's profile and notes (who they are, their work, this meeting):\n{context.strip()}"
    else:
        system += "\n\nThe user has not saved a profile yet."
    return system

def stream_answer(provider, model, api_key, question, context="", history=(), length="short", meeting=()):
    """Yield the answer text piece by piece. `history` is earlier {role, content} messages;
    `meeting` is the last transcript lines before the question, so it knows what's being discussed."""
    if not api_key:
        raise AssistantError("Add an API key in Settings → Assistant first.")
    content = question
    if meeting:
        content = "Said in the meeting just before:\n" + "\n".join(meeting) + f"\n\nQuestion: {question}"
    messages = list(history)[-HISTORY_MESSAGES:] + [{"role": "user", "content": content}]
    system = build_system(context, length)
    yield from _sender(provider)(model, api_key, system, messages)

def stream_summary(provider, model, api_key, transcript, context=""):
    """Yield meeting notes (summary, decisions, action items, open questions) piece by piece."""
    if not api_key:
        raise AssistantError("Add an API key in Settings → Assistant first.")
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        transcript = "[…earlier part of the meeting cut for length…]\n" + transcript[-MAX_TRANSCRIPT_CHARS:]
    system = SUMMARY_SYSTEM
    if context.strip():
        system += f"\n\nBackground the user saved about themselves and the project:\n{context.strip()}"
    messages = [{"role": "user", "content": f"Meeting transcript:\n\n{transcript}"}]
    yield from _sender(provider)(model, api_key, system, messages, max_tokens=2048)

# Shown in Settings → Assistant → Model before a key is saved (with a key, the provider's own list is used)
KNOWN_MODELS = {
    "claude": ["claude-haiku-4-5", "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"],
    "openai": ["gpt-5.4-mini"],
    "gemini": ["gemini-3.8-flash", "gemini-3-flash-preview"],
    "nvidia": ["nvidia/nemotron-3-super-120b-a12b"],
    "cloudflare": ["@cf/meta/llama-3.3-70b-instruct-fp8-fast", "@cf/openai/gpt-oss-120b", "@cf/openai/gpt-oss-20b",
                   "@cf/google/gemma-4-26b-a4b-it", "@cf/zai-org/glm-5.3-flash", "@cf/deepseek-ai/deepseek-v4-flash-0731",
                   "@cf/moonshotai/kimi-k2.6", "@cf/qwen/qwen3.8-27b", "@cf/mistralai/mistral-small-3.1-24b-instruct",
                   "@cf/meta/llama-4-scout-17b-16e-instruct", "@cf/nvidia/nemotron-3-120b-a12b"],
}
# Not chat models: embeddings, speech, images, video, safety classifiers, rerankers, ...
_NOT_CHAT = re.compile(r"embed|tts|whisper|transcribe|audio|realtime|speech|dall-e|image|imagen|veo|sora|lyria|"
                       r"vision|[-_]vl\b|clip|ocr|parse|moderation|guard|safety|reward|rerank|retriev|aqa|"
                       r"davinci|babbage|search|computer-use|deplot|kosmos|fuyu|neva|vila|cosmos", re.I)

SUMMARY_LIST_MAX = 30   # items kept per list; a runaway answer never floods the cards

def _clean(value, limit=600):
    return re.sub(r"\s+", " ", value).strip()[:limit] if isinstance(value, str) else ""


def parse_summary(text):
    """The model's JSON summary as {title, summary, decisions, action_items [{who, what, when}],
    open_questions}, every field checked and cleaned; None when the answer is not such an object
    (the caller then shows the text as it came)."""
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("summary"), str):
        return None

    def strings(key):
        items = data.get(key)
        items = items if isinstance(items, list) else []
        return [c for c in (_clean(x) for x in items) if c][:SUMMARY_LIST_MAX]

    actions = []
    for item in data.get("action_items") if isinstance(data.get("action_items"), list) else []:
        if isinstance(item, str):
            item = {"what": item}
        if not isinstance(item, dict) or not _clean(item.get("what")):
            continue
        actions.append({k: _clean(item.get(k), 200 if k != "what" else 600) for k in ("who", "what", "when")})
    summary = {"title": _clean(data.get("title"), 120), "summary": _clean(data.get("summary"), 3000),
               "decisions": strings("decisions"), "action_items": actions[:SUMMARY_LIST_MAX],
               "open_questions": strings("open_questions")}
    return summary if summary["summary"] or summary["decisions"] or actions else None


def _action_line(a):
    extra = ", ".join(x for x in (a["who"], a["when"] and f"by {a['when']}") if x)
    return a["what"] + (f" ({extra})" if extra else "")


def summary_markdown(s):
    """The saved summary file: the title first, so the Meetings list can show it."""
    out = [f"# {s['title'] or 'Meeting summary'}", "", "## Summary", s["summary"] or "-", "", "## Decisions"]
    out += [f"- {d}" for d in s["decisions"]] or ["None recorded."]
    out += ["", "## Action items"]
    out += [f"- [ ] {_action_line(a)}" for a in s["action_items"]] or ["None recorded."]
    out += ["", "## Open questions"]
    out += [f"- {q}" for q in s["open_questions"]] or ["None."]
    return "\n".join(out) + "\n"


def _plain_sections(s, bullet):
    out = []
    for head, items in (("Decisions", s["decisions"]), ("Action items", [_action_line(a) for a in s["action_items"]]),
                        ("Open questions", s["open_questions"])):
        if items:
            out += ["", head + ":"] + [f"{bullet} {x}" for x in items]
    return out


def summary_email(s):
    """Plain text to paste into an email: a Subject line, then the notes."""
    title = s["title"] or "Meeting summary"
    out = [f"Subject: Meeting notes: {title}", "", "Hi all,", "", "here are the notes from our meeting.", "",
           s["summary"]] + _plain_sections(s, "-") + ["", "Best regards"]
    return "\n".join(out)


def summary_teams(s):
    """Plain text for a Teams chat: short, bold title (Teams renders **...**), bullets."""
    out = [f"**{s['title'] or 'Meeting summary'}**", s["summary"]] + _plain_sections(s, "•")
    return "\n".join(out)


def list_models(provider, api_key=""):
    """Every chat model the provider offers this key, sorted, default first. NVIDIA's list is public;
    the others need the key. Raises AssistantError when the list can't be fetched."""
    p = PROVIDERS[provider]
    try:
        if provider == "cloudflare":
            import json, urllib.error, urllib.request
            account, token = _cloudflare(api_key)
            req = urllib.request.Request(
                f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/models/search"
                "?task=Text%20Generation&per_page=500", headers={"Authorization": f"Bearer {token}"})
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    data = json.load(r)
            except urllib.error.HTTPError as e:
                raise AssistantError(f"Cloudflare said {e.code}: check the account ID and API token.")
            ids = [m["name"] for m in data["result"] if "lora" not in m["name"]]
        elif provider == "claude":
            import anthropic
            ids = [m.id for m in anthropic.Anthropic(api_key=api_key, timeout=15.0).models.list(limit=1000)]
        else:
            import openai
            client = openai.OpenAI(api_key=api_key or "none", base_url=p.get("base_url"), timeout=15.0)
            ids = [m.id.removeprefix("models/") for m in client.models.list()]
    except Exception as e:
        raise AssistantError(f"Couldn't list {p['label']} models: {e}")
    ids = sorted({i for i in ids if not _NOT_CHAT.search(i)})
    return [p["model"]] + [i for i in ids if i != p["model"]]

def _sender(provider):
    """The streaming call for a provider (looked up at call time, so tests can swap them)."""
    if provider == "claude":
        return _claude
    if provider == "openai":
        return _openai
    return lambda *args, **kwargs: _compatible(provider, *args, **kwargs)

def _claude(model, api_key, system, messages, max_tokens=1024):
    import anthropic
    client = anthropic.Anthropic(api_key=api_key, timeout=60.0)
    try:
        with client.messages.stream(model=model, max_tokens=max_tokens, system=system,
                                    messages=messages) as stream:
            yield from stream.text_stream
            if stream.get_final_message().stop_reason == "refusal":
                yield "\n\n(The model declined to answer this one.)"
    except anthropic.AuthenticationError:
        raise AssistantError("Claude rejected the API key — check it in Settings → Assistant.")
    except anthropic.PermissionDeniedError:
        raise AssistantError("This Claude API key isn't allowed to use that model.")
    except anthropic.NotFoundError:
        raise AssistantError(f"Claude doesn't know the model '{model}'.")
    except anthropic.RateLimitError:
        raise AssistantError("Claude rate limit or credit limit reached — try again shortly.")
    except anthropic.APIStatusError as e:
        raise AssistantError(f"Claude error {e.status_code}: {e.message}")
    except anthropic.APIConnectionError:
        raise AssistantError("Can't reach Claude — check the internet connection.")

def _openai(model, api_key, system, messages, max_tokens=1024):
    yield from _openai_stream("OpenAI", model, api_key, system, messages, max_completion_tokens=max_tokens)

def _compatible(provider, model, api_key, system, messages, max_tokens=1024):
    """Gemini and NVIDIA through their OpenAI-compatible endpoints. No token cap: their thinking
    models count reasoning against it and would cut answers short; the prompts already set the length."""
    p = PROVIDERS[provider]
    base_url, extra = p["base_url"], {}
    if provider == "cloudflare":
        account, api_key = _cloudflare(api_key)
        base_url = base_url.format(account=account)
    if provider == "gemini" and model.startswith(("gemini-2.5", "gemini-3")):
        extra["reasoning_effort"] = "low"   # live answers: a little thinking, not seconds of it
    yield from strip_thinking(_openai_stream(p["label"], model, api_key, system, messages,
                                             base_url=base_url, **extra))

def _cloudflare(credential):
    """'<account id>:<API token>' -> (account id, token)."""
    account, _, token = (credential or "").partition(":")
    if not account or not token:
        raise AssistantError("Add your Cloudflare Account ID and API token in Settings → Assistant.")
    return account, token

def strip_thinking(pieces):
    """Some open models (NVIDIA's catalog) stream their reasoning inline as <think>…</think> before
    the answer. Drop it, even when a tag is split across streamed pieces."""
    buf, thinking, after = "", False, False   # after: just left a think block, skip its blank lines
    for piece in pieces:
        buf += piece
        while buf:
            if after:
                buf = buf.lstrip()
                after = not buf
                if not buf:
                    break
            tag = "</think>" if thinking else "<think>"
            i = buf.find(tag)
            if i >= 0:
                if not thinking and buf[:i]:
                    yield buf[:i]
                buf, thinking, after = buf[i + len(tag):], not thinking, thinking
                continue
            keep = next((n for n in range(len(tag) - 1, 0, -1) if buf.endswith(tag[:n])), 0)
            if not thinking and len(buf) > keep:
                yield buf[:len(buf) - keep]
            buf = buf[len(buf) - keep:] if keep else ""
            break
    if buf and not thinking:
        yield buf

def _openai_stream(label, model, api_key, system, messages, base_url=None, **options):
    import openai
    client = openai.OpenAI(api_key=api_key, base_url=base_url, timeout=60.0)
    try:
        stream = client.chat.completions.create(
            model=model, stream=True, messages=[{"role": "system", "content": system}] + messages, **options)
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
    except openai.AuthenticationError:
        raise AssistantError(f"{label} rejected the API key — check it in Settings → Assistant.")
    except openai.PermissionDeniedError:
        raise AssistantError(f"This {label} API key isn't allowed to use that model.")
    except openai.NotFoundError:
        raise AssistantError(f"{label} doesn't know the model '{model}'.")
    except openai.RateLimitError:
        raise AssistantError(f"{label} rate limit or quota reached — check billing or try again shortly.")
    except openai.APIStatusError as e:
        if e.status_code == 400 and "api key" in str(e).lower():   # Gemini answers a bad key with 400
            raise AssistantError(f"{label} rejected the API key — check it in Settings → Assistant.")
        raise AssistantError(f"{label} error {e.status_code}: {e.message}")
    except openai.APIConnectionError:
        raise AssistantError(f"Can't reach {label} — check the internet connection.")
