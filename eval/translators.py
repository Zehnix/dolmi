"""Translators for the bench: the app's Opus-MT without Whisper (--mode mt), other CTranslate2 Marian
models, and the QA-7 cloud translators (--translator gemini|deepl) with the user's own key.

Text test sets (--set private:<file.json>) are lines without audio, for the translation half only:

    {"kind": "text", "lines": [{"id": "t1", "de": "German line", "en": "reference or null",
      "en_terms": ["terms the English must contain"], "avoid": ["known wrong English"], "tags": []}],
     "vocabulary": "optional vocabulary.txt text", "glossary": "optional glossary.txt text"}
"""
import json
import os
import time
from pathlib import Path

CLOUD = ("gemini", "deepl")
SHOW_WITHIN = 1.5      # s: the app shows a cloud subtitle only when it comes back this fast (QA-7)


def load_text_set(path):
    """A text test set (see the module doc), or None when `path` is not one."""
    p = Path(path).expanduser()
    if not p.is_file() or p.suffix.lower() not in (".json", ".jsonl"):
        return None
    if p.suffix.lower() == ".jsonl":
        lines = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
        return {"kind": "text", "lines": lines}
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("kind") != "text":
        raise SystemExit(f"{p}: expected a text set ({{'kind': 'text', 'lines': [...]}}), see eval/README.md")
    return data


def resolve_model(name):
    """Folder of a CTranslate2 Marian model: a local folder, a models.py key, or a Hugging Face repo."""
    if Path(name).expanduser().is_dir():
        return Path(name).expanduser()
    import models
    from huggingface_hub import snapshot_download
    repo = models.BY_KEY[name].repo if name in models.BY_KEY else name
    return Path(snapshot_download(repo))


def load_marian(name, device):
    import ctranslate2
    import sentencepiece as spm
    d = resolve_model(name)
    return (ctranslate2.Translator(str(d), device=device, compute_type="int8"),
            spm.SentencePieceProcessor(model_file=str(d / "source.spm")),
            spm.SentencePieceProcessor(model_file=str(d / "target.spm")))


def mt_engine(translator, device="cpu", language="de"):
    """live_subs.Engine's translate() (Vocabulary protect/finish included) without loading Whisper."""
    import live_subs
    import models

    class MTEngine(live_subs.Engine):
        cloud = True                                    # no Whisper hotword budget to apply

        def __init__(self):
            self.model_name, self.device, self.language = "none", "cpu" if device == "auto" else device, language
            self.translators, self.context = {}, ""
            self.reload_terms()

    eng = MTEngine()
    key = models.translator_key(language)
    if translator in ("", "default", "opus-de") or translator in CLOUD:
        eng._translator(key, download=True)
    else:
        eng.translators[key] = load_marian(translator, eng.device)
    return eng


def saved_key(provider):
    """The user's key from Dolmi's own settings (decrypted the way the app does; never printed)."""
    src = Path(__file__).resolve().parent.parent / "src"
    import sys
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    import config
    import assistant
    key = os.environ.get(f"DOLMI_{provider.upper()}_KEY") or \
        assistant.decrypt_key(config.load_settings().get("api_keys", {}).get(provider, ""))
    if not key:
        raise SystemExit(f"No {provider} key: save it in Dolmi (Settings) or set DOLMI_{provider.upper()}_KEY")
    return key


class CloudMT:
    """A QA-7 cloud translator for the bench: translate_ctx() with the previous lines as context, exactly
    the request the app sends. Waits up to `timeout` s (longer than the app, to score quality); answers
    slower than SHOW_WITHIN count as fallbacks (the app would have kept the local line). `local` is the
    app's Opus-MT engine, used for the side-by-side numbers and the blind A/B."""

    def __init__(self, provider, local, key=None, timeout=10.0, post=None):
        import cloud_translate
        self.local = local
        self.tr = cloud_translate.CloudTranslator(provider, key or saved_key(provider), workers=0,
                                                  terms=lambda: (getattr(local, "glossary", ""), getattr(local, "vocab", None)),
                                                  timeout=timeout, post=post)
        self.provider, self.timeout = provider, timeout
        self.latency, self.failed = [], 0

    def translate_ctx(self, text, lang, context):
        import cloud_translate
        if not text:
            return ""
        dst, src = cloud_translate.target_language(lang, self.local.language)
        if src == dst or src == "en":
            return self.local.translate(text, lang)
        t = time.perf_counter()
        try:
            out = self.tr.translate(text, src, dst, context, self.timeout)
        except Exception as e:
            self.failed += 1
            print(f"  {self.provider}: {type(e).__name__}: {str(e)[:120]}")
            out = ""
        self.latency.append(time.perf_counter() - t)
        return out

    def translate(self, text, lang):
        return self.translate_ctx(text, lang, [])

    def stats(self):
        import metrics
        n = max(1, len(self.latency))
        return {"cloud_calls": len(self.latency), "cloud_failed": self.failed,
                "cloud_latency_p50": metrics.percentile(self.latency, 50),
                "cloud_latency_p95": metrics.percentile(self.latency, 95),
                "cloud_fallback_rate": 100 * (self.failed + sum(x > SHOW_WITHIN for x in self.latency)) / n}

    def __getattr__(self, name):
        return getattr(self.local, name)
