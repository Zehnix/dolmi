"""STR-6: one way in for every translation. route(text, src, dst, ...) -> Result(text, provider, reason).

Local Opus-MT is the default and always the fallback. Cloud (QA-7) and local-LLM (QA-8) engines plug in
as providers; they are only asked for finals, only when enabled, and only while not cooling down.
A provider that fails is skipped for COOLDOWN_S (429 / 5xx / timeouts), and the local result is used.
The per-language-pair table says which local model serves a pair; overrides come from bench results only."""
import threading
import time
from dataclasses import dataclass

import models
import netgate

COOLDOWN_S = 60.0          # a provider that answered 429/5xx (or timed out) is skipped this long

# Spoken language -> local translator key. Defaults come from models.translator_key; an entry here
# replaces one only when a bench (QA-4 / STR-11) showed it is better. Empty until then, on purpose.
PAIR_OVERRIDES = {}


def pair_default(lang):
    """Local model key for spoken `lang` -> subtitle language."""
    return PAIR_OVERRIDES.get(lang) or models.translator_key(lang)


def pair_tier(lang):
    """'good' (own Opus-MT pair), 'fair' (the many-languages model) for the language picker badge.
    STR-11 numbers can move a language to 'experimental'."""
    return "fair" if pair_default(lang) == "opus-mul" else "good"


def cooling_reason(error):
    """Why a provider error should cool it down ('' = it should not, e.g. a bad request)."""
    text = f"{type(error).__name__} {error}".lower()
    if isinstance(error, netgate.LocalOnly):
        return "local-only"
    if "http 429" in text or ("rate" in text and "limit" in text):
        return "429"
    if any(f"http {c}" in text for c in range(500, 600)):
        return "5xx"
    if isinstance(error, (TimeoutError, OSError)) or "timed out" in text or "timeout" in text:
        return "timeout"
    return ""


@dataclass
class Result:
    text: str
    provider: str          # "local" or the provider's name
    reason: str = ""       # why the local result was used ('' = the provider answered)


class Cooldown:
    """Per-provider 'do not ask before' times."""

    def __init__(self, seconds=COOLDOWN_S, clock=time.monotonic):
        self.seconds, self.clock = seconds, clock
        self._until = {}
        self._lock = threading.Lock()

    def hit(self, name, reason=""):
        with self._lock:
            self._until[name] = self.clock() + self.seconds
        print(f"translation router: {name} cooled down {self.seconds:.0f} s ({reason or 'error'})")

    def ready(self, name):
        with self._lock:
            return self.clock() >= self._until.get(name, 0.0)


class Router:
    """`local(text, lang)` is the Opus-MT path (Engine.translate); `providers` is an ordered list of
    (name, fn(text, src, dst, context, timeout) -> str). Cloud providers pass the STR-7 gate."""

    def __init__(self, local, providers=(), cooldown=None, timeout=1.5, clock=time.monotonic):
        self.local, self.providers = local, list(providers)
        self.cooldown = cooldown or Cooldown(clock=clock)
        self.timeout, self.clock = timeout, clock

    def route(self, text, src, dst="en", context=(), final=True, local_text=None):
        """Best translation available now. Drafts always stay local (cloud is for finals only)."""
        local = local_text if local_text is not None else None
        if final:
            for name, fn in self.providers:
                if not self.cooldown.ready(name):
                    continue
                started = self.clock()
                try:
                    netgate.check("translate")
                    out = fn(text, src, dst, list(context or []), self.timeout)
                except Exception as e:
                    reason = cooling_reason(e)
                    if reason and reason != "local-only":
                        self.cooldown.hit(name, reason)
                    print(f"translation router: {name} failed ({reason or type(e).__name__}), using local")
                    continue
                if self.clock() - started > self.timeout:
                    self.cooldown.hit(name, "late")
                    continue
                if out:
                    return Result(out, name)
        if local is None:
            local = self.local(text, src)
        return Result(local, "local", "" if not self.providers or not final else "fallback")
