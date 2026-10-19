"""
Dolmi engine: live German -> English subtitles for any meeting app on Windows and macOS, 100% free & local.

v2 improvements over v1:
  * System audio capture     -> no virtual cable, you still HEAR the meeting
                                (WASAPI loopback / Core Audio process tap: dolmi_platform)
  * Pause-based segmentation -> no more sentences cut in half every 2 s
  * Live "draft" line        -> text appears ~1 s after speech, finalized on pause
  * faster-whisper (int8)    -> ~4x faster than openai-whisper on CPU
  * Hallucination filter     -> drops Whisper's classic German silence ghosts
  * Real timestamps          -> correct SRT + readable Markdown transcript

The window and subtitle bar live in src/ (webview UI). Run: start.bat (Windows), Mac/start.command (macOS)
or the installed app.
"""
import itertools, os, queue, re, threading, time, unicodedata
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

import numpy as np

import dolmi_platform
import models

# ---------------------------------------------------------------- settings
SR = 16000                 # Whisper sample rate
STEP = SR // 10            # segmentation looks at the audio 100 ms at a time (the capture chunk size)
DRAFT_EVERY = 1.0          # s between live draft updates
PAUSE_TO_FINALIZE = 0.6    # s of silence that ends a sentence
PUNCT_PAUSE = 0.25         # s of silence that ends a sentence a draft already closed with . ? !
MAX_UTTERANCE = 12.0       # s, long monologues are cut here (at the last pause inside, see Segmenter)
MIN_CUT = 2.0              # s, a cut at the cap keeps at least this much in the first part
OVERLAP = 1.0              # s carried over (and de-duplicated) when a cut at the cap finds no pause
MIN_SENTENCE = 3.0         # s, a draft ending in . ? ! this long may finalize after PUNCT_PAUSE
MIN_FLUSH = 0.5            # s of speech a sentence cut off by Stop needs to still be transcribed
PREROLL = 0.3              # s of audio from before the speech onset kept at the start of a sentence
SILENCE_RMS = 0.006        # energy threshold (raise if noisy)
LOW_ENERGY_RMS = 0.01      # loudest 100 ms below this: a lone "Thank you." there is Whisper's ghost
                           # (quiet remote speech peaks near 0.018, so this stays well under it)
ASR_VAD_THRESHOLD = 0.35   # faster-whisper's own VAD: lower than its 0.5 default so soft speech survives
CLOUD_SPLIT, CLOUD_BREATH, CLOUD_MAX = 3.0, 0.2, 6.0   # cloud captions: cut long speech sooner

# Typical Whisper output on silence / noise. A line is junk only when these phrases (whole words),
# broadcaster names and years make up most of it: "Tschüss, bis morgen alle!" is a real line.
HALLUCINATIONS = [
    "untertitel im auftrag des zdf", "untertitel im auftrag des", "untertitel der amara.org-community",
    "untertitel von", "untertitel", "untertitelung", "amara.org",
    "vielen dank fürs zuschauen und bis zum nächsten mal", "danke fürs zuschauen und bis zum nächsten mal",
    "vielen dank fürs zuschauen", "danke fürs zuschauen", "copyright",
    "abonniert den kanal", "abonniert unseren kanal", "abonniert", "abonnieren",
    "thank you for watching and see you next time", "thanks for watching and see you next time",
    "thanks for watching", "thank you for watching", "subscribe to",
    "sous-titrage", "sous-titres",
]
BROADCASTERS = ["zdf", "swr", "wdr", "ndr", "mdr", "ard", "rbb", "funk", "orf", "srf", "radio-canada"]
SILENCE_GHOSTS = {         # kept when someone says them; dropped alone on near-silent audio
    "thank you", "thank you very much", "thanks", "vielen dank", "tschüss",
}
# Languages written in Latin script: only for these is a line in another script a misheard ghost.
# (Serbian is left out: it is written in both Latin and Cyrillic.)
LATIN_LANGUAGES = {
    "en", "de", "fr", "es", "it", "nl", "pl", "pt", "tr", "sv", "da", "fi", "cs", "hu", "ro", "id", "vi",
    "no", "nb", "nn", "is", "et", "lv", "lt", "sk", "sl", "hr", "bs", "sq", "ca", "eu", "gl", "ga", "cy",
    "mt", "lb", "af", "sw", "ms", "tl", "az", "uz", "tk", "ha", "yo", "so", "mi", "haw", "la", "br", "oc",
    "fo", "ht", "ln", "jw", "su",
}

# --------------------------------------------------------------- capture
# Capture is platform code; re-exported here so callers keep using live_subs.loopback_stream etc.
from dolmi_platform import output_devices, input_devices, loopback_stream, mic_stream  # noqa: E402,F401

# ---------------------------------------------------------------- engines
VOCAB_SECTIONS = ("keep", "german", "english")
MIN_TERM = 3   # shorter terms ("KI") match ordinary words too, so they need a trailing "!" to be used

class VocabRule(NamedTuple):
    section: str   # keep | german | english
    src: str       # term as written; "*" stands for any letters ("geil*" = geil, geile, geilen…)
    dst: str       # "" for [keep] without "=" (the term stays as written)
    line: int      # 1-based line in vocabulary.txt

def strip_comment(line: str) -> str:
    """Drop everything from the first unescaped '#'; '\\#' is a literal '#' (for terms like C#)."""
    m = re.search(r"(?<!\\)#", line)
    return (line[:m.start()] if m else line).replace("\\#", "#").strip()

def _problem(n, raw, message):
    return {"line": n, "text": raw.strip(), "message": message}

def parse_vocabulary(text: str):
    """vocabulary.txt -> (rules, problems). A problem ({line, text, message}) is never applied."""
    rules, problems, section = [], [], None
    for n, raw in enumerate(text.splitlines(), 1):
        line = strip_comment(raw)
        if not line:
            continue
        if line.startswith("["):
            name = line[1:-1].strip().lower() if line.endswith("]") else ""
            section = name if name in VOCAB_SECTIONS else ""
            if not section:
                problems.append(_problem(n, raw, f"Unknown section {line}: use [keep], [german] or [english]. "
                                                 "Lines below it are skipped until the next section."))
            continue
        if section is None:
            problems.append(_problem(n, raw, "This rule is not in a section: put [keep], [german] or [english] above it."))
            continue
        if not section:
            continue   # under an unknown section (reported once, at its header)
        src, eq, dst = (s.strip() for s in line.partition("="))
        marked = src.endswith("!")
        src = src.rstrip("!").rstrip()
        message = None
        if not re.sub(r"[\s*]", "", src):
            message = "There is no term before '='." if eq else "A rule needs a word, not just '*'."
        elif section == "german" and not eq:
            message = "Write it as 'German = English'. To keep a word unchanged, put it under [keep]."
        elif section == "english" and not eq:
            message = "Write it as 'wrong English = correct English'."
        elif eq and not dst:
            message = ("Nothing after '='. Remove the '=' to keep the term as written." if section == "keep"
                       else "Nothing after '=': write the English it must become (a rule never deletes words).")
        elif any(w.startswith("*") for w in src.split()):
            message = "A '*' only goes at the end of a word (toll* = great); a rule never matches inside a word."
        elif len(re.sub(r"[\s*]", "", src)) < MIN_TERM and not marked:
            message = (f"'{src}' is shorter than {MIN_TERM} letters and may match ordinary words. "
                       f"Write '{src}!' to use it anyway.")
        if message:
            problems.append(_problem(n, raw, message))
            continue
        rules.append(VocabRule(section, src, dst, n))
    return rules, problems

def parse_glossary(text: str):
    """glossary.txt -> (terms, problems): one spelling hint per line, '#' starts a comment."""
    terms, problems = [], []
    for n, raw in enumerate(text.splitlines(), 1):
        line = strip_comment(raw)
        if not line:
            continue
        if line.startswith("["):
            problems.append(_problem(n, raw, "Sections belong in the translation rules, not the spelling hints."))
        elif "=" in line:
            problems.append(_problem(n, raw, "Spelling hints are single terms. Put 'X = Y' rules under "
                                             "German → English or Fix English."))
        else:
            terms.append(line)
    return terms, problems

def term_pattern(src: str):
    """Whole-word, case-insensitive pattern; '*' matches any letters, spaces match any whitespace."""
    body = r"\s+".join(re.escape(w).replace(r"\*", r"\w*") for w in src.split())
    return re.compile(rf"(?<!\w){body}(?!\w)", re.I)

class Vocabulary:
    """Applies vocabulary.txt. Locked terms travel through the translator as X0X-style
    placeholders (Opus-MT copies those unchanged), then become the wanted English.
    Rules apply longest first, so "New York" wins over "New"; lines with problems are skipped."""
    def __init__(self, path=None, text=None):
        if text is None:
            text = path.read_text(encoding="utf-8") if path and path.exists() else ""
        self.rules, self.problems = parse_vocabulary(text)   # rules in file order (cloud prompts list them)
        self.lock, self.fix = [], []   # (pattern, english); english None = keep the words as spoken
        for r in sorted(self.rules, key=lambda r: -len(r.src)):
            if r.section == "english":
                self.fix.append((term_pattern(r.src), r.dst))
            else:
                self.lock.append((term_pattern(r.src), r.dst or (None if "*" in r.src else r.src)))
        self._fix_re = re.compile("|".join(f"(?P<f{i}>{pat.pattern})" for i, (pat, _) in enumerate(self.fix)), re.I)

    def protect(self, de):
        slots, spoken = Slots(), []
        for pat, en in self.lock:
            def slot(m, en=en):
                slots.append(m.group(0) if en is None else en)
                spoken.append(m.group(0))
                return f"X{len(slots) - 1}X"
            de = pat.sub(slot, de)
        slots.spoken = spoken
        slots.where = [_slot_place(de, i) for i in range(len(slots))]
        return de, slots

    def finish(self, en, slots=(), reinsert=True):
        """The translator's output with each placeholder turned into its term. A placeholder the translator
        dropped is put back (reinsert): next to a word it stood beside in the source if that word survived,
        else at the end of the matching clause, so a kept term is never silently lost."""
        where = getattr(slots, "where", None) or [None] * len(slots)
        done = lambda w: slots[int(w[1:-1])] if w and re.fullmatch(r"X\d+X", w) and int(w[1:-1]) < i else w
        for i, term in enumerate(slots):
            en, found = _put_term(en, i, term)
            if not found and reinsert:
                place = where[i] and (done(where[i][0]), done(where[i][1]), *where[i][2:])
                en = _reinsert(en, term, place)   # a neighbour that was a placeholder is its term by now
        if self.fix:   # one pass: at each spot the longest rule wins and its output is not matched again
            en = self._fix_re.sub(lambda m: self.fix[int(m.lastgroup[1:])][1], en)
        return en

    def enforce(self, heard, en):
        """A translation made elsewhere (Gemini hears and translates in one step) through the same rules:
        where a locked term heard in `heard` still shows up in the source spelling, it becomes the wanted
        English; [english] fixes apply. Nothing is re-inserted: the model may have put the term its own way."""
        _, slots = self.protect(heard)
        for i, (term, said) in enumerate(zip(slots, slots.spoken)):
            if not re.search(rf"(?<!\w){re.escape(term)}(?!\w)", en, re.I):
                en = re.sub(rf"(?<!\w){re.escape(said)}(?!\w)", f"X{i}X", en, count=1, flags=re.I)
        return self.finish(en, slots, reinsert=False)

class Slots(list):
    """protect()'s terms by placeholder number, plus .spoken (the words matched in the source) and .where
    (each placeholder's surroundings in the source, for finish() to put back a dropped one)."""
    spoken: list = []
    where: list = []

_CLAUSE_END = re.compile(r"[,;:.!?](?=\s|$)")

def _clause_ends(text):
    """Where each clause ends: its closing , ; : . ! ? (or the end of the text for a last open clause)."""
    ends = [m.start() for m in _CLAUSE_END.finditer(text)]
    stripped = text.rstrip()
    if stripped and (not ends or ends[-1] < len(stripped) - 1):
        ends.append(len(stripped))
    return ends or [0]

def _bare(word):
    return re.sub(r"^\W+|\W+$", "", word) or None

def _slot_place(de, i):
    """(word before, word after, clause number, clause count) of placeholder i in the protected source;
    a neighbour across a comma or full stop is not counted as one."""
    m = re.search(rf"X{i}X", de)
    if not m:
        return None
    prev = re.search(r"(\S+)\s+$", de[:m.start()])
    nxt = re.match(r"\s+(\S+)", de[m.end():])
    prev = _bare(prev.group(1)) if prev and not re.search(r"[,;:.!?]$", prev.group(1)) else None
    nxt = _bare(nxt.group(1)) if nxt else None
    ends = _clause_ends(de)
    return prev, nxt, sum(e < m.start() for e in ends), len(ends)

def _article(term):
    """'a' or 'an' before a term: acronyms by their spoken first letter (an MCP, a CRM), words by vowel."""
    first = (term.split() or [""])[0]
    if first.isupper() and first.isalpha() and len(first) <= 5:
        return "an" if first[0] in "AEFHILMNORSX" else "a"
    w = first.lower()
    if w.startswith(("uni", "use", "usu", "eu", "one", "once")):
        return "a"
    if w.startswith(("hour", "honest", "honou", "heir")):
        return "an"
    return "an" if w[:1] in "aeiou" else "a"

def _put_term(en, i, term):
    """Placeholder i -> term (tolerating 'X 0 X', 'x0x' and glued German endings like 'X0Xn'); a/an before it
    is matched to the term. Returns (text, found)."""
    pat = re.compile(rf"[Xx]\s*{i}\s*[Xx](?P<suf>[a-zäöü']*)(?!\w)")
    out, last, found = [], 0, False
    for m in pat.finditer(en):
        found = True
        head = en[last:m.start()]
        art = re.search(r"(?<!\w)(a|an|A|An)(\s+)$", head)
        if art:
            want = _article(term)
            want = want.capitalize() if art.group(1)[0] == "A" else want
            head = head[:art.start()] + want + art.group(2)
        suf = m.group("suf")
        out += [head, term + (suf if suf in ("s", "es", "'s") else "")]
        last = m.end()
    return "".join(out) + en[last:], found

def _reinsert(en, term, place):
    """Put a dropped term back: after/before a source neighbour that survived translation (a name, a
    number, another term), else at the end of the clause at the same place in the sentence."""
    prev, nxt, clause, clauses = place or (None, None, None, 1)
    for anchor, after in ((prev, True), (nxt, False)):
        if not anchor or not (len(anchor) >= 4 or re.search(r"\d", anchor)):
            continue
        hits = list(re.finditer(rf"(?<!\w){re.escape(anchor)}(?!\w)", en))
        if len(hits) == 1:
            m = hits[0]
            return en[:m.end()] + " " + term + en[m.end():] if after else en[:m.start()] + term + " " + en[m.start():]
    ends = _clause_ends(en)
    k = len(ends) - 1 if clause is None else min(len(ends) - 1, round(clause * (len(ends) - 1) / max(1, clauses - 1)))
    at = ends[k]
    return (en[:at].rstrip() + " " + term).lstrip() + en[at:]

class Engine:
    def __init__(self, model, device, language="de"):
        dolmi_platform.prepare_gpu()   # Windows: wire up pip-installed CUDA libs before probing the GPU
        device, ctype = dolmi_platform.compute_device(device)   # "auto": GPU if usable, else CPU
        from faster_whisper import WhisperModel
        if model == "auto":
            model = dolmi_platform.auto_speech(device == "cuda")
        print(f"📥 Whisper '{model}' on {device} ({ctype})")
        self.model_name, self.device = model, device
        self.asr = WhisperModel(model, device=device, compute_type=ctype)
        self.language = language   # spoken language code, or "auto"
        self.translators = {}      # model key -> (translator, source spm, target spm)
        # Load (and on first use download) the translator up front
        self._translator(models.translator_key(language), download=True)
        self.context = ""   # previous sentence -> better names/terms
        self.reload_terms()

    def reload_terms(self):
        """Re-read glossary.txt (Whisper spelling hints) and vocabulary.txt (translation rules).

        Safe while the ASR thread runs: everything is built first, then each attribute is swapped in one
        assignment, so a line being translated finishes with the objects it already holds. If a file
        can't be read (e.g. half-written by an editor), the previous rules stay."""
        cfg = Path(os.environ.get("DOLMI_CONFIG") or Path(__file__).parent)
        g = cfg / "glossary.txt"
        try:
            terms, _ = parse_glossary(g.read_text(encoding="utf-8") if g.exists() else "")
            vocab = Vocabulary(cfg / "vocabulary.txt")
        except (OSError, UnicodeDecodeError) as e:
            print(f"Vocabulary not reloaded from {cfg} ({e}); keeping the previous rules")
            if hasattr(self, "vocab"):
                return
            terms, vocab = [], Vocabulary()
        glossary = ", ".join(terms)
        self.vocab = vocab
        self.terms = {w.lower() for w in re.findall(r"\w+", glossary) if len(w) > 2}
        self.glossary = glossary

    def _translator(self, key, download=False):
        """Loaded translator for a model key; never downloads mid-meeting unless asked."""
        if key in self.translators:
            return self.translators[key]
        m = models.BY_KEY[key]
        if not models.is_installed(m):
            if not download:
                return None
            models.download(m)
        import ctranslate2, sentencepiece as spm
        from huggingface_hub import snapshot_download
        d = Path(snapshot_download(m.repo, local_files_only=True))
        self.translators[key] = (
            ctranslate2.Translator(str(d), device=self.device, compute_type="int8"),
            spm.SentencePieceProcessor(model_file=str(d / "source.spm")),
            spm.SentencePieceProcessor(model_file=str(d / "target.spm")))
        print(f"📥 Translator {m.name}")
        return self.translators[key]

    def transcribe(self, audio, retry_empty=True):
        """Speech -> (text in the spoken language, language code).

        People switch to English mid-meeting, so a non-English setting still lets Whisper
        detect: the chosen language or English is kept; anything else is a misdetection
        (usually a short clip) and is transcribed again in the chosen language.
        Nothing heard: tried once more without Whisper's own VAD before the buffer is given up
        (finals only: transcribe_draft skips the retry, a draft is redone a moment later anyway).
        `last_info` says what happened ({'retried', 'echo'}) so the worker can log why a line is empty."""
        mixed = self.language not in ("auto", "en")
        language = None if self.language == "auto" or mixed else self.language
        segs, info = self._asr(audio, language)
        if mixed and info.language not in (self.language, "en"):
            language = self.language
            segs, info = self._asr(audio, language)
        retried = retry_empty and not any(s.text.strip() for s in segs)
        if retried:
            segs, info = self._asr(audio, language, retry=True)
        raw = " ".join(t for t in (s.text.strip() for s in segs) if t)
        text = drop_echoes(segs, self.terms)
        self.last_info = {"retried": retried, "echo": text != raw}
        return collapse_repeats(text), info.language

    def transcribe_draft(self, audio):
        return self.transcribe(audio, retry_empty=False)

    def _asr(self, audio, language, retry=False):
        """Whisper segments (each with no_speech_prob and avg_logprob) and the detection info.
        Our own VAD already found speech, so Whisper's VAD only trims (at a lower threshold than its
        default); the retry skips it entirely."""
        vad = {"vad_filter": False} if retry else {"vad_filter": True, "vad_parameters": {"threshold": ASR_VAD_THRESHOLD}}
        segs, info = self.asr.transcribe(
            audio, language=language, beam_size=5, condition_on_previous_text=False, **vad,
            # spelling hints go in as hotwords; only the previous sentence is the prompt
            hotwords=self.glossary or None, initial_prompt=self.context[-200:] or None)
        return list(segs), info

    def translate(self, text, lang):
        if lang == "en" and self.language != "en":
            return self.vocab.finish(text)   # English speech, English subtitles: nothing to translate
        if lang not in models.LANGUAGES:
            lang = self.language
        key = models.translator_key(lang)
        t = self._translator(key)
        if t is None and key != "opus-en":
            t = self._translator("opus-mul")   # any language -> English
        if t is None:
            return f"[{lang}] {text}"   # no translator for this language installed yet
        mt, sp_src, sp_tgt = t
        vocab = self.vocab   # one set of rules per line, even if reload_terms swaps them meanwhile
        text, slots = vocab.protect(text)
        toks = sp_src.encode(text, out_type=str)[:400] + ["</s>"]
        res = mt.translate_batch([toks], beam_size=2, max_decoding_length=256)
        out = [tok for tok in res[0].hypotheses[0] if tok not in ("</s>", "<pad>")]
        return vocab.finish(sp_tgt.decode(out), slots)

def collapse_repeats(text: str) -> str:
    """Whisper sometimes loops: 'He's a good man. He's a good man.' -> said once."""
    return re.sub(r"(\b.{8,}?)(?:[\s,.!?]+\1\b)+", r"\1", text, flags=re.I)

ECHO_TRUSTED = (0.2, -0.5)   # no_speech_prob below and avg_logprob above: Whisper is sure it heard speech

def _is_term_list(sentence: str, terms: set) -> bool:
    """A comma list of 5+ one- or two-word items (none longer than three) with 2+ of our own terms."""
    parts = [x for x in re.split(r"[,;]", sentence) if x.strip()]
    if len(parts) < 5 or any(len(x.split()) > 3 for x in parts):
        return False
    hits = sum(w.lower() in terms for w in re.findall(r"\w+", sentence))
    return hits >= 2 and sum(len(x.split()) <= 2 for x in parts) >= 0.7 * len(parts)

def echoes_terms(text: str, terms: set, no_speech_prob=None, avg_logprob=None) -> str:
    """On silence or music Whisper can read its hint list back: 'Acme, Alice, Bob, Carol, …'.
    Returns `text` without the sentences that are that echo ('' when the whole line was one).
    A Whisper segment that is confident it heard speech keeps its list: people do read out names."""
    if no_speech_prob is not None and avg_logprob is not None and \
            no_speech_prob < ECHO_TRUSTED[0] and avg_logprob > ECHO_TRUSTED[1]:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(x for x in sentences if x and not _is_term_list(x, terms))

def _trusted(seg) -> bool:
    p, lp = getattr(seg, "no_speech_prob", None), getattr(seg, "avg_logprob", None)
    return p is not None and lp is not None and p < ECHO_TRUSTED[0] and lp > ECHO_TRUSTED[1]

def drop_echoes(segs, terms: set) -> str:
    """The joined text of Whisper segments without an echoed hint list. Whisper may split one echo over
    several segments ('Acme, Alice, Bob,' + 'Carol, Dave, ...'), so each run of segments it is not sure
    about is checked as one text; the segments it is sure about are kept as they are."""
    out, run = [], []
    for seg in list(segs) + [None]:
        if seg is not None and not _trusted(seg):
            run.append(seg.text.strip())
            continue
        if run:
            out.append(echoes_terms(" ".join(t for t in run if t), terms))
            run = []
        if seg is not None:
            out.append(seg.text.strip())
    return " ".join(t for t in out if t).strip()

_URL = re.compile(r"(?:https?://|www\.)\S*", re.I)
_W = "A-Za-z0-9À-ɏ"   # Latin letters (with accents): only these mark word edges, as Whisper glues CJK onto "Amara.org"
_DOMAIN = re.compile(rf"(?<![{_W}.-])[{_W}-]+(?:\.[{_W}-]+)*\.(?:com|org|net|de|io|tv|info|eu|at|ch)(?![{_W}])", re.I)
# words of a phrase may be separated by any punctuation ("Zuschauen, und bis zum nächsten Mal")
_HALLUCINATION = re.compile("|".join(
    rf"(?<![{_W}])" + r"[\s,.!?;:-]+".join(map(re.escape, h.split())) + rf"(?![{_W}])"
    for h in sorted(HALLUCINATIONS + BROADCASTERS, key=len, reverse=True)) + r"|(?<!\d)(?:19|20)\d\d(?!\d)", re.I)
# A subtitle credit ("Untertitel von ...", "Sous-titres réalisés par ...") is a ghost at any length, but
# people in a subtitle app also say "Untertitel bitte auf Englisch": only the credit shape counts.
# That is a credit word right after it, Amara or a broadcaster anywhere in the line ("Untertitelung
# aufgrund der Amara.org-Community", "Untertitel des Bayerischen Rundfunks"), or just a name
# ("Untertitel Stephanie Geiges").
_CREDIT_HEAD = r"^(?:untertitel\w*|sous-titr\w*)"
_CREDIT = re.compile(
    r"^amara\.org|" + _CREDIT_HEAD + r"[\s,]*(?::|(?:von|by|par)\b|(?:faits?|r[ée]alis\w*)\s+par\b"
    r"|im\s+auftrag|soci[ée]t[ée])"
    r"|" + _CREDIT_HEAD + rf"(?![{_W}]).*?(?<![{_W}])(?:amara|\w*rundfunk\w*|"
    + "|".join(map(re.escape, BROADCASTERS)) + rf")(?![{_W}])", re.I)
_CREDIT_NAME = re.compile(r"^(?:Untertitel\w*|Sous-titr\w*)\s+(?:[A-ZÄÖÜÉ][a-zäöüßéèàç'-]+\s*){2,3}[.!]?$")

def _letters(t: str) -> int:
    return sum(c.isalnum() for c in t)

def junk_reason(t: str, energy=None, lang=None):
    """Why a line is Whisper junk rather than speech (None = keep it).

    energy: loudest 100 ms RMS of the audio (None = unknown: lone ghost phrases are kept);
    lang: the spoken language; a line in another script is junk only for a Latin-script language
    (None, "auto" or e.g. Hebrew detected on Auto-detect: other scripts are kept)."""
    letters = _letters(t)
    if letters < 2:                                   # '..', '-' (but 'Ja', 'OK' are lines)
        return "no_words"
    links = [m.span() for m in _URL.finditer(t)]   # a URL in a real sentence is part of the sentence
    domains = [m.group() for m in _DOMAIN.finditer(t) if not any(a <= m.start() < b for a, b in links)]
    if sum(_letters(t[a:b]) for a, b in links) + sum(map(_letters, domains)) >= 0.5 * letters:
        return "url"
    head = t.strip().lstrip("\"'([-–—.,: ")
    if _CREDIT.match(head) or _CREDIT_NAME.match(head):
        return "hallucination"
    covered = sum(_letters(m.group()) for m in _HALLUCINATION.finditer(t))
    if covered >= 0.6 * letters:
        return "hallucination"
    if lang in LATIN_LANGUAGES:
        alpha = [c for c in t if c.isalpha()]
        latin = sum("LATIN" in unicodedata.name(c, "") for c in alpha)
        if alpha and latin < 0.5 * len(alpha):        # Cyrillic, Hangul, CJK, Arabic ... on German audio
            return "non_latin"
    if energy is not None and energy < LOW_ENERGY_RMS and \
            re.sub(r"[^\w\s]", "", t).strip().lower() in SILENCE_GHOSTS:
        return "silence_ghost"
    return None

def is_junk(t: str, energy=None, lang=None) -> bool:
    return junk_reason(t, energy, lang) is not None

def peak_rms(audio, window=STEP) -> float:
    """Loudness of the loudest 100 ms of `audio` (0.0 for no audio)."""
    n = len(audio) // window * window
    if n == 0:
        return float(np.sqrt(np.mean(audio ** 2))) if len(audio) else 0.0
    return float(np.sqrt((audio[:n].reshape(-1, window) ** 2).mean(axis=1)).max())

def dedupe_overlap(previous: str, text: str, max_words=8) -> str:
    """`text` without its first words when they repeat the last words of `previous` (audio overlap).
    A single repeated word counts only when it is long: 'das ist so' + 'so ist das' is real speech."""
    norm = lambda w: re.sub(r"\W", "", w.lower())
    prev, words = previous.split(), text.split()
    for k in range(min(max_words, len(prev), len(words)), 0, -1):
        head = [norm(w) for w in words[:k]]
        if [norm(w) for w in prev[-k:]] == head and any(head) and (k > 1 or len(head[0]) >= 5):
            return " ".join(words[k:])
    return text

# ------------------------------------------------------------ transcript
class Transcript:
    """One meeting's lines. Each final is appended to the .md and .srt the moment it arrives (nothing is
    lost on a crash) and also kept in `lines`, keyed by its id, so a later pass can change a line
    (update_line) and finish() rewrites both files from `lines`. Times are the speech's own start/end
    (epoch seconds), not the time the line was written."""

    def __init__(self, folder="transcripts", save=True):
        self.save = save
        self.t0 = time.time()      # meeting start: SRT times and the "+mm:ss" offsets count from here
        self.n = 0
        self.lines = {}            # id -> {"id", "start", "end", "de", "en", **meta}
        self._next_id = 0          # lines without a producer id get ("auto", n): never equal to a producer id
        self._lock = threading.Lock()
        self.md = self.srt = None
        if not save:   # privacy setting: live subtitles only, nothing written to disk
            return
        Path(folder).mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        self.md = Path(folder) / f"meeting_{stamp}.md"
        self.srt = Path(folder) / f"meeting_{stamp}.srt"
        self._header = f"# Meeting {stamp}\n\n<!-- started {datetime.fromtimestamp(self.t0):%H:%M:%S} -->\n\n"
        self.md.write_text(self._header, encoding="utf-8")

    @staticmethod
    def _ts(s):
        h, r = divmod(s, 3600); m, s = divmod(r, 60)
        return f"{int(h):02}:{int(m):02}:{int(s):02},{int((s % 1) * 1000):03}"

    @staticmethod
    def _md_line(x):
        span = f"{datetime.fromtimestamp(x['start']):%H:%M:%S}–{datetime.fromtimestamp(x['end']):%H:%M:%S}"
        return f"**{span}** {x['en']}  \n<sub>{x['de']}</sub>\n\n"

    def _srt_block(self, n, x):
        return f"{n}\n{self._ts(max(0.0, x['start'] - self.t0))} --> {self._ts(max(0.0, x['end'] - self.t0))}\n{x['en']}\n\n"

    def add(self, start, end, de, en, meta=None):
        """Record one final line; returns its id (meta['id'] when given, else ("auto", n))."""
        meta = dict(meta or {})
        with self._lock:
            key = meta.get("id")
            if key is None:
                key = ("auto", self._next_id)
                self._next_id += 1
            self.lines[key] = x = {**meta, "id": key, "start": start, "end": end, "de": de, "en": en}
            self.n += 1
            if not self.save:
                return key
            with self.md.open("a", encoding="utf-8") as f:
                f.write(self._md_line(x))
            with self.srt.open("a", encoding="utf-8") as f:
                f.write(self._srt_block(self.n, x))
        return key

    def update_line(self, line_id, **fields):
        """Change a kept line (e.g. a better translation or a speaker name); the files follow on finish().
        Returns False when there is no such line."""
        with self._lock:
            x = self.lines.get(line_id)
            if x is None:
                return False
            x.update({k: v for k, v in fields.items() if k != "id"})
            return True

    def finish(self):
        """Rewrite the .md and .srt from `lines` (call once the meeting has stopped)."""
        with self._lock:
            if not self.save:
                return
            rows = sorted(self.lines.values(), key=lambda x: x["start"])
            self.md.write_text(self._header + "".join(self._md_line(x) for x in rows), encoding="utf-8")
            self.srt.write_text("".join(self._srt_block(i, x) for i, x in enumerate(rows, 1)), encoding="utf-8")

# --------------------------------------------------------------- worker
UI_Q_CONTRACT = """What worker() puts on ui_q (producer: this module; consumer: src/ui/app.py _pump).

worker(eng, audio_q, ui_q, tr, stop, detect=speech_gap, clock=time.time, on_drop=None, diarizer=None)
  audio_q   16 kHz mono float32 chunks (~100 ms each; any size works).
  detect    speech_gap-like: seconds since the last speech at the end of a ~1 s window, or None.
  clock     epoch seconds (time.time). Segmentation runs in AUDIO time (sample counts); the clock only
            anchors the stream: epoch of sample n = stream start + n / SR. The quality bench injects one.
  on_drop   on_drop(reason, start, end, text) for every utterance that does not become a final.
            Reasons: empty, echo, no_words, url, hallucination, non_latin, silence_ghost, too_short,
            no_speech (an utterance with no voiced audio: never sent to Whisper), stopped, error.
            Every drop is also logged with its reason.
  stop      when set: nothing more goes on ui_q, but finals already cut (and the sentence still being
            spoken, if it holds ≥ MIN_FLUSH s of speech) are still transcribed into tr.
  diarizer  optional: diarizer.assign(audio, start, end) -> (speaker_id | None, confidence), called for
            every final before it is emitted (local path) and before eng.submit (cloud path).

Items are (kind, a, b) or (kind, a, b, meta). Times are time.time() epoch seconds (UTC).
  ("final", heard, subtitle, meta)  meta = {"id": int, 1-based per session in emit order,
                                    "start": speech onset, "end": end of speech, "emitted": when it was
                                    put on ui_q, "lang": spoken language code, "speaker": None,
                                    "speaker_conf": None}
  ("draft", heard, subtitle, meta)  the sentence still being spoken; meta = {"start", "end", "emitted"}
  ("draft", "", "", {})             clears the live line
  ("draft", "", "", {"dropped": reason, "start": s, "end": e})
                                    the sentence that showed a draft ended empty/junk: no final follows
  ("final_update", id, subtitle, {"source", "emitted"})
                                    reserved for QA-7 (wave 2): a better subtitle for final `id`
  ("error", message, "")            from the cloud engine
Each final is also written with tr.add(start, end, heard, subtitle, meta=meta).
Cloud path: eng.submit(audio, start, end, ui_q, tr, meta={"speaker", "speaker_conf"}) and
eng.draft(audio, ui_q, meta={"start", "end"}); the engine emits the same shapes and numbers its
finals from eng.line_ids, which worker() resets for each session.
"""


def speech_gap(audio):
    """Seconds since the last speech at the end of `audio`, or None if it holds no speech.
    Uses Silero VAD rather than loudness, so music or background noise doesn't count as talking."""
    tail = audio[-int(PREROLL * SR):]
    if not len(tail) or np.sqrt(np.mean(tail ** 2)) < SILENCE_RMS:   # cheap early-out: last 300 ms silent
        return None
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    ts = get_speech_timestamps(audio, VadOptions(min_speech_duration_ms=100,
                                                 min_silence_duration_ms=150, speech_pad_ms=30))
    return (len(audio) - ts[-1]["end"]) / SR if ts else None


class Utterance:
    """One piece of speech cut from the stream. Sample positions count from the stream start."""

    def __init__(self, uid, audio, first, onset, end, overlap, reason):
        self.uid, self.audio = uid, audio
        self.first = first       # sample of audio[0] (pre-roll and carried audio included)
        self.onset = onset       # sample where its speech starts
        self.end = end           # sample where its speech ends
        self.overlap = overlap   # s at the start that repeat the end of the previous utterance
        self.reason = reason     # pause | sentence | breath | cap_pause | cap_hard | stop


class Segmenter:
    """Cuts the audio stream into utterances in audio time, one ~100 ms piece at a time, so no audio
    is skipped however late it runs. Only the worker thread feeds it; the ASR thread may call
    mark_sentence_end() and read uid / active."""

    def __init__(self, detect=None, cloud=False):
        self.detect = detect or speech_gap
        self.cloud = cloud
        self.samples = 0                         # samples fed so far: the audio clock
        self.recent = np.zeros(0, np.float32)    # last 1 s: VAD context and pre-roll source
        self.used = 0                            # samples before this belong to an earlier utterance
        self.uid = 0                             # number of the newest utterance
        self._lock = threading.Lock()            # mark_sentence_end (ASR thread) vs. a cut (worker)
        self._clear()

    def _clear(self):
        self.parts, self.silent = [], []         # audio pieces and their "no speech in it" flags
        self.first = self.onset = self.last_voice = None
        self.overlap, self.sentence_end = 0.0, None

    @property
    def active(self):
        return self.first is not None

    def duration(self):
        return (self.samples - self.first) / SR if self.active else 0.0

    def audio(self):
        return np.concatenate(self.parts) if self.parts else np.zeros(0, np.float32)

    def mark_sentence_end(self, uid, upto):
        """A draft of utterance `uid` that heard the audio up to sample `upto` ended with . ? !"""
        with self._lock:
            if uid == self.uid and self.active:
                self.sentence_end = upto

    def feed(self, chunk):
        """Feed captured audio; returns the utterances it completed (usually none)."""
        out = []
        for i in range(0, len(chunk), STEP):
            piece = np.asarray(chunk[i:i + STEP], np.float32)
            with self._lock:
                out += self._step(piece)
        return out

    def _step(self, piece):
        start, n = self.samples, len(piece)
        if not n:
            return []
        unused = self.recent[max(0, len(self.recent) - (start - self.used)):]
        self.samples += n
        self.recent = np.concatenate([self.recent, piece])[-SR:]
        gap = self.detect(self.recent)
        if gap is not None and gap < n / SR + 0.1:          # speech in (or just before) this piece
            voice = max(start, self.samples - int(round(gap * SR)))
            if not self.active:                              # it starts: keep ~300 ms from before it
                self._clear()
                self.uid += 1
                pre = unused[len(unused) - min(len(unused), int(PREROLL * SR)):]
                if len(pre):
                    self.parts, self.silent = [pre.copy()], [True]
                self.first, self.onset = start - len(pre), start
            self.last_voice = voice
        if not self.active:
            return []
        self.parts.append(piece)
        self.silent.append(gap is None or gap >= n / SR)
        return self._decide()

    def _decide(self):
        pause, dur = (self.samples - self.last_voice) / SR, self.duration()
        if pause >= PAUSE_TO_FINALIZE:
            return [self._finish("pause")]
        if self.cloud:   # cloud captions: requests are ~1 s each, so long speech is cut at a breath
            if dur >= CLOUD_SPLIT and pause >= CLOUD_BREATH:
                return [self._finish("breath")]
            return [self._cut()] if dur >= CLOUD_MAX else []
        # a draft already closed the sentence: a short pause is enough (a comma pause is not)
        if self.sentence_end is not None and self.sentence_end >= self.last_voice and \
                pause >= PUNCT_PAUSE and dur >= MIN_SENTENCE:
            return [self._finish("sentence")]
        return [self._cut()] if dur >= MAX_UTTERANCE else []

    def _finish(self, reason):
        u = Utterance(self.uid, self.audio(), self.first, self.onset, self.last_voice, self.overlap, reason)
        self.used = self.samples
        self._clear()
        return u

    def _cut(self):
        """At the length cap: cut in the middle of the last pause (at least MIN_CUT in) and carry
        the rest into the next utterance. With no pause, cut here and also carry the last OVERLAP s
        (local only; the ASR side drops the repeated words) so no word is split without a second look."""
        offs = [0]
        for p in self.parts:
            offs.append(offs[-1] + len(p))
        total, low = offs[-1], int(MIN_CUT * SR)
        quiet = [i for i, s in enumerate(self.silent) if s and offs[i] >= low]
        if quiet:
            last = begin = quiet[-1]
            while begin - 1 >= 0 and self.silent[begin - 1] and offs[begin - 1] >= low:
                begin -= 1
            cut, keep_from, reason = (offs[begin] + offs[last + 1]) // 2, None, "cap_pause"
            speech_end = self.first + offs[begin]
        else:
            cut, speech_end, reason = total, self.first + total, "cap_hard"
            keep_from = None if self.cloud else max(0, total - int(OVERLAP * SR))
        carry = cut if keep_from is None else keep_from
        u = Utterance(self.uid, self.audio()[:cut], self.first, self.onset,
                      min(speech_end, self.last_voice), self.overlap, reason)
        voiced = [offs[i] for i, s in enumerate(self.silent) if not s and offs[i] >= cut]
        if keep_from is None and not voiced:     # the speech ended in this pause: nothing to carry
            self.used = self.first + cut         # (the pause after the cut can still be pre-roll)
            self._clear()
            return u
        parts, silent = [], []                   # the carried audio keeps its pieces for a later cut
        for i, p in enumerate(self.parts):
            if offs[i + 1] > carry:
                parts.append(p[max(0, carry - offs[i]):])
                silent.append(self.silent[i])
        onset = self.first + (max(cut, voiced[0]) if voiced and keep_from is None else cut)
        first, last_voice = self.first + carry, self.last_voice
        self._clear()
        self.uid += 1
        self.parts, self.silent = parts, silent
        self.first, self.onset, self.last_voice = first, onset, last_voice
        self.overlap = (cut - carry) / SR
        self.used = first
        return u


def _spoken(eng, detected):
    """The language a line should be in: the chosen one, or what Whisper detected on Auto-detect."""
    chosen = getattr(eng, "language", None)
    return detected if chosen in (None, "auto") else chosen


def worker(eng, audio_q, ui_q, tr, stop, detect=speech_gap, clock=time.time, on_drop=None, diarizer=None):
    """Audio chunks -> subtitles on ui_q (see UI_Q_CONTRACT). This thread only segments; on the local
    path a second thread runs Whisper on finished utterances, so no speech is skipped while it works."""
    cloud = getattr(eng, "cloud", False)
    seg = Segmenter(detect, cloud=cloud)
    t0 = None                                    # epoch of sample 0

    def epoch(n):
        return t0 + n / SR

    def drop(reason, start, end, text=""):
        print(f"Dropped a line ({reason}, {end - start:.1f} s): {ascii(text[:80])}")
        if on_drop:
            on_drop(reason, start, end, text)

    def speaker(audio, start, end):
        return (None, None) if diarizer is None else diarizer.assign(audio, start, end)

    jobs, busy = queue.Queue(), threading.Event()
    if cloud:
        eng.line_ids = itertools.count(1)
        every = eng.DRAFT_EVERY
    else:
        every = DRAFT_EVERY
        threading.Thread(target=_asr_loop, name="dolmi-asr", daemon=True,
                         args=(eng, jobs, busy, seg, ui_q, tr, stop, clock, drop, speaker)).start()
    draft_uid, last_draft = None, None
    try:
        while not stop.is_set():
            try:
                chunk = audio_q.get(timeout=0.2)
            except queue.Empty:
                continue
            if t0 is None:
                t0 = clock() - len(chunk) / SR
            for u in seg.feed(chunk):
                start, end = epoch(u.onset), epoch(u.end)
                if u.end <= u.onset:                 # silence only: no decode, no ghost from the no-VAD retry
                    drop("no_speech", start, end)
                elif not cloud:
                    jobs.put(("final", u, start, end))
                elif len(u.audio) / SR > 0.8:
                    spk, conf = speaker(u.audio, start, end)
                    eng.submit(u.audio, start, end, ui_q, tr, meta={"speaker": spk, "speaker_conf": conf})
                else:
                    drop("too_short", start, end)
            if not seg.active:
                continue
            if draft_uid != seg.uid:
                draft_uid, last_draft = seg.uid, None
            dur = seg.duration()
            if (last_draft is not None and (seg.samples - last_draft) / SR < every) or \
                    dur < 0.8 or (not cloud and dur <= 0.8):
                continue                             # not time for a draft yet
            meta = {"start": epoch(seg.onset), "end": epoch(seg.samples)}
            if cloud:                                # live line ~1 s after the words, not after the sentence
                eng.draft(seg.audio(), ui_q, meta=meta)
            elif jobs.empty() and not busy.is_set():   # drafts only when Whisper looks idle (a draft may
                # slip in just as it picks up a job; _asr_loop then skips it as stale)
                jobs.put(("draft", seg.uid, seg.audio(), seg.samples, meta))
            else:
                continue
            last_draft = seg.samples
        if seg.active and t0 is not None:
            start, end = epoch(seg.onset), epoch(seg.last_voice)
            if not cloud and (seg.last_voice - seg.onset) / SR >= MIN_FLUSH:   # mid-sentence Stop: keep it
                jobs.put(("final", Utterance(seg.uid, seg.audio(), seg.first, seg.onset, seg.last_voice,
                                             seg.overlap, "stop"), start, end))
            else:
                drop("stopped", start, epoch(seg.samples))
    finally:
        jobs.put(None)                           # the ASR thread ends after the jobs already queued


class _Discard:
    """ui_q stand-in after Stop."""
    @staticmethod
    def put(item):
        pass


def _asr_loop(eng, jobs, busy, seg, ui_q, tr, stop, clock, drop, speaker):
    """Whisper side of worker(): finals in the order spoken; a draft only while nothing else waits.
    After Stop it still transcribes the finals already queued, into the transcript file only: that
    speech was captured, so it must not be lost because the CPU was behind."""
    ids, draft_shown = itertools.count(1), None
    previous = (None, "")                        # (uid, text) of the last utterance; "" when it was dropped
    while True:
        job = jobs.get()
        if job is None:                          # worker() has stopped and queued everything it had
            return
        if stop.is_set():
            ui_q = _Discard                      # nobody shows captions any more
            if job[0] == "draft":
                continue
        busy.set()
        try:
            if job[0] == "draft":
                _, uid, audio, upto, meta = job
                if uid != seg.uid or not seg.active or not jobs.empty():
                    continue                     # stale: that sentence is over, or a final waits
                de, lang = getattr(eng, "transcribe_draft", eng.transcribe)(audio)
                if uid != seg.uid or not seg.active or not de or is_junk(de, lang=_spoken(eng, lang)):
                    continue
                if len(audio) / SR >= MIN_SENTENCE and de.rstrip().endswith((".", "?", "!")):
                    seg.mark_sentence_end(uid, upto)   # final on the next short pause
                ui_q.put(("draft", de, eng.translate(de, lang), {**meta, "emitted": clock()}))
                draft_shown = uid
                continue
            _, u, start, end = job
            de, lang = eng.transcribe(u.audio)
            if u.overlap and de and previous[0] == u.uid - 1:   # only against the part just before the cut
                de = dedupe_overlap(previous[1], de)
            if de:
                reason = junk_reason(de, peak_rms(u.audio), _spoken(eng, lang))
            else:
                reason = "echo" if (getattr(eng, "last_info", None) or {}).get("echo") else "empty"
            if reason:
                previous = (u.uid, "")               # its words were not shown: none to de-duplicate
                drop(reason, start, end, de)
                gone = {"dropped": reason, "start": start, "end": end} if draft_shown == u.uid else {}
                ui_q.put(("draft", "", "", gone))
            else:
                spk, conf = speaker(u.audio, start, end)
                en = eng.translate(de, lang)
                meta = {"id": next(ids), "start": start, "end": end, "emitted": clock(), "lang": lang,
                        "speaker": spk, "speaker_conf": conf}
                ui_q.put(("final", de, en, meta))
                tr.add(start, end, de, en, meta=meta)
                previous = (u.uid, de)
                if not stop.is_set():                # a new session may already use eng.context
                    eng.context = de
                ui_q.put(("draft", "", "", {}))
            draft_shown = None
        except Exception as e:                   # one bad buffer must not end the captions
            print(f"Speech recognition failed on one buffer: {e!r}")
            if job[0] == "final":
                drop("error", job[2], job[3])
        finally:
            busy.clear()


# ---------------------------------------------------------------- saving
def save_snippet(folder, text):
    """Append a sentence or word the user clicked to transcripts/saved_<date>.md."""
    path = Path(folder) / f"saved_{datetime.now():%Y-%m-%d}.md"
    with path.open("a", encoding="utf-8") as f:
        f.write(f"- **{datetime.now():%H:%M}** {text}\n")
    return path

DOLMI_FILES = ("meeting_*", "saved_*", "assistant_*", "summary_*")

def delete_old_files(folder, days, now=None):
    """Delete Dolmi's own transcript files older than `days` (0 = keep forever). Returns how many."""
    folder = Path(folder)
    if not days or not folder.exists():
        return 0
    cutoff = (now or time.time()) - days * 86400
    deleted = 0
    for f in {f for pattern in DOLMI_FILES for f in folder.glob(pattern)}:
        if f.is_file() and f.stat().st_mtime < cutoff:
            try:
                f.unlink()
                deleted += 1
            except OSError as e:
                print(f"Could not delete old transcript {f.name}: {e}")
    if deleted:
        print(f"Deleted {deleted} transcript files older than {days} days")
    return deleted

if __name__ == "__main__":   # the UI lives in src/main.py
    import runpy
    runpy.run_path(str(Path(__file__).with_name("src") / "main.py"), run_name="__main__")
