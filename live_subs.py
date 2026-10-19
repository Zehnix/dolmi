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
import os, queue, re, threading, time, unicodedata
from datetime import datetime
from pathlib import Path

import numpy as np

import dolmi_platform
import models

# ---------------------------------------------------------------- settings
SR = 16000                 # Whisper sample rate
DRAFT_EVERY = 1.0          # s between live draft updates
PAUSE_TO_FINALIZE = 0.6    # s of silence that ends a sentence
MAX_UTTERANCE = 12.0       # s, force-finalize long monologues
MIN_SENTENCE = 3.0         # s, a draft ending in . ? ! this long is finalized without a pause
SILENCE_RMS = 0.006        # energy threshold (raise if noisy)
LOW_ENERGY_RMS = 0.015     # loudest 100 ms below this: a lone "Thank you." there is Whisper's ghost
CLOUD_SPLIT, CLOUD_BREATH, CLOUD_MAX = 3.0, 0.2, 6.0   # cloud captions: cut long speech sooner

# Typical Whisper output on silence / noise. A line is junk only when these phrases (whole words)
# make up most of it: "Tschüss, bis morgen alle!" is a real line.
HALLUCINATIONS = [
    "untertitel im auftrag des zdf", "untertitel im auftrag des", "untertitel der amara.org-community",
    "untertitel von", "untertitel", "untertitelung", "amara.org", "zdf", "swr", "wdr",
    "vielen dank fürs zuschauen", "danke fürs zuschauen", "bis zum nächsten mal", "copyright",
    "abonniert", "abonnieren", "thanks for watching", "thank you for watching", "subscribe to",
    "sous-titrage", "sous-titres",
]
SILENCE_GHOSTS = {         # kept when someone says them; dropped alone on near-silent audio
    "thank you", "thank you very much", "thanks", "vielen dank", "tschüss",
}
NON_LATIN_LANGUAGES = {"ru", "uk", "bg", "el", "ar", "fa", "ur", "hi", "zh", "ja", "ko"}

# --------------------------------------------------------------- capture
# Capture is platform code; re-exported here so callers keep using live_subs.loopback_stream etc.
from dolmi_platform import output_devices, input_devices, loopback_stream, mic_stream  # noqa: E402,F401

# ---------------------------------------------------------------- engines
class Vocabulary:
    """Applies vocabulary.txt. Locked terms travel through the translator as X0X-style
    placeholders (Opus-MT copies those unchanged), then become the wanted English."""
    def __init__(self, path):
        self.lock, self.fix = [], []   # (pattern, english)
        section = None
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        for line in (l.strip() for l in lines):
            if not line or line.startswith("#"):
                continue
            if line.startswith("["):
                section = line.strip("[]").lower()
                continue
            src, _, dst = (s.strip() for s in line.partition("="))
            pat = re.compile(rf"(?<!\w){re.escape(src)}(?!\w)", re.I)
            if section in ("keep", "german"):
                self.lock.append((pat, dst or src))
            elif section == "english":
                self.fix.append((pat, dst))
        self.lock.sort(key=lambda p: -len(p[0].pattern))   # "New York" before "New"

    def protect(self, de):
        slots = []
        for pat, en in self.lock:
            def slot(_, en=en):
                slots.append(en)
                return f"X{len(slots) - 1}X"
            de = pat.sub(slot, de)
        return de, slots

    def finish(self, en, slots=()):
        for i, term in enumerate(slots):
            en = re.sub(rf"X\s*{i}\s*X", lambda _: term, en)
        for pat, good in self.fix:
            en = pat.sub(lambda _: good, en)
        return en

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
        """Re-read glossary.txt (Whisper spelling hints) and vocabulary.txt (translation rules)."""
        cfg = Path(os.environ.get("DOLMI_CONFIG") or Path(__file__).parent)
        g = cfg / "glossary.txt"
        lines = g.read_text(encoding="utf-8").splitlines() if g.exists() else []
        self.glossary = ", ".join(l.strip() for l in lines if l.strip() and not l.startswith("#"))
        self.terms = {w.lower() for w in re.findall(r"\w+", self.glossary) if len(w) > 2}
        self.vocab = Vocabulary(cfg / "vocabulary.txt")

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

    def transcribe(self, audio):
        """Speech -> (text in the spoken language, language code).

        People switch to English mid-meeting, so a non-English setting still lets Whisper
        detect: the chosen language or English is kept; anything else is a misdetection
        (usually a short clip) and is transcribed again in the chosen language."""
        mixed = self.language not in ("auto", "en")
        segs, info = self._asr(audio, None if self.language == "auto" or mixed else self.language)
        if mixed and info.language not in (self.language, "en"):
            segs, info = self._asr(audio, self.language)
        text = collapse_repeats(" ".join(s.text.strip() for s in segs).strip())
        return ("" if echoes_terms(text, self.terms) else text), info.language

    def _asr(self, audio, language):
        segs, info = self.asr.transcribe(
            audio, language=language,
            beam_size=5, vad_filter=True, condition_on_previous_text=False,
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
        text, slots = self.vocab.protect(text)
        toks = sp_src.encode(text, out_type=str)[:400] + ["</s>"]
        res = mt.translate_batch([toks], beam_size=2, max_decoding_length=256)
        out = [tok for tok in res[0].hypotheses[0] if tok not in ("</s>", "<pad>")]
        return self.vocab.finish(sp_tgt.decode(out), slots)

def collapse_repeats(text: str) -> str:
    """Whisper sometimes loops: 'He's a good man. He's a good man.' -> said once."""
    return re.sub(r"(\b.{8,}?)(?:[\s,.!?]+\1\b)+", r"\1", text, flags=re.I)

def echoes_terms(text: str, terms: set) -> bool:
    """On silence or music Whisper can read its hint list back: 'Acme, Alice, Bob, Carol, …'.
    A comma list of mostly one- or two-word items that contains our own terms is that echo."""
    parts = [x for x in re.split(r"[,;]", text) if x.strip()]
    hits = sum(w.lower() in terms for w in re.findall(r"\w+", text))
    return len(parts) >= 5 and hits >= 2 and sum(len(x.split()) <= 2 for x in parts) >= 0.7 * len(parts)

_URL = re.compile(r"https?://|www\.\S", re.I)
_W = "A-Za-z0-9À-ɏ"   # Latin letters (with accents): only these mark word edges, as Whisper glues CJK onto "Amara.org"
_DOMAIN = re.compile(rf"(?<![{_W}.-])[{_W}-]+(?:\.[{_W}-]+)*\.(?:com|org|net|de|io|tv|info|eu|at|ch)(?![{_W}])", re.I)
_HALLUCINATION = re.compile("|".join(rf"(?<![{_W}]){re.escape(h)}(?![{_W}])" for h in
                                     sorted(HALLUCINATIONS, key=len, reverse=True)), re.I)

def _letters(t: str) -> int:
    return sum(c.isalnum() for c in t)

def junk_reason(t: str, energy=None, lang=None):
    """Why a line is Whisper junk rather than speech (None = keep it).

    energy: loudest 100 ms RMS of the audio (None = unknown: lone ghost phrases are kept);
    lang: the spoken language (None or "auto" = unknown: other scripts are kept)."""
    letters = _letters(t)
    if letters < 2:                                   # '..', '-' (but 'Ja', 'OK' are lines)
        return "no_words"
    if _URL.search(t) or sum(_letters(m.group()) for m in _DOMAIN.finditer(t)) >= 0.5 * letters:
        return "url"
    covered = sum(_letters(m.group()) for m in _HALLUCINATION.finditer(t))
    if covered >= 0.6 * letters:
        return "hallucination"
    if lang not in (None, "auto") and lang not in NON_LATIN_LANGUAGES:
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


# ------------------------------------------------------------ transcript
class Transcript:
    def __init__(self, folder="transcripts", save=True):
        self.save = save
        if not save:   # privacy setting: live subtitles only, nothing written to disk
            self.md = self.srt = None
            return
        Path(folder).mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        self.md = Path(folder) / f"meeting_{stamp}.md"
        self.srt = Path(folder) / f"meeting_{stamp}.srt"
        self.t0 = time.time()
        self.n = 0
        self.md.write_text(f"# Meeting {stamp}\n\n", encoding="utf-8")

    @staticmethod
    def _ts(s):
        h, r = divmod(s, 3600); m, s = divmod(r, 60)
        return f"{int(h):02}:{int(m):02}:{int(s):02},{int((s % 1) * 1000):03}"

    def add(self, start, end, de, en):   # appended live -> nothing lost on crash
        if not self.save:
            return
        self.n += 1
        with self.md.open("a", encoding="utf-8") as f:
            f.write(f"**{datetime.now():%H:%M:%S}** {en}  \n<sub>{de}</sub>\n\n")
        with self.srt.open("a", encoding="utf-8") as f:
            f.write(f"{self.n}\n{self._ts(start - self.t0)} --> "
                    f"{self._ts(end - self.t0)}\n{en}\n\n")

# --------------------------------------------------------------- worker
def speech_gap(audio):
    """Seconds since the last speech at the end of `audio`, or None if it holds no speech.
    Uses Silero VAD rather than loudness, so music or background noise doesn't count as talking."""
    if np.sqrt(np.mean(audio ** 2)) < SILENCE_RMS:   # cheap early-out for real silence
        return None
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    ts = get_speech_timestamps(audio, VadOptions(min_speech_duration_ms=100,
                                                 min_silence_duration_ms=150, speech_pad_ms=30))
    return (len(audio) - ts[-1]["end"]) / SR if ts else None

def worker(eng, audio_q, ui_q, tr, stop, detect=speech_gap):
    buf, recent = np.zeros(0, np.float32), np.zeros(0, np.float32)
    utt_start, last_voice, last_draft = None, 0.0, 0.0
    while not stop.is_set():
        try:
            chunk = audio_q.get(timeout=0.2)
        except queue.Empty:
            continue
        # drain backlog so we never fall behind real time
        while not audio_q.empty():
            chunk = np.concatenate([chunk, audio_q.get_nowait()])
        now = time.time()
        recent = np.concatenate([recent, chunk])[-SR:]   # VAD needs ~1 s of context
        gap = detect(recent)
        voiced = gap is not None and gap < len(chunk) / SR + 0.1
        if voiced:
            last_voice = now - gap
            if utt_start is None:
                utt_start = now
        if utt_start is None:
            continue
        buf = np.concatenate([buf, chunk])
        dur = len(buf) / SR
        pause = now - last_voice

        finalize = pause >= PAUSE_TO_FINALIZE or dur >= MAX_UTTERANCE
        if getattr(eng, "cloud", False):   # cloud captions: requests run off this thread, so nothing waits
            # long speech is cut at a short breath so captions keep flowing (requests are ~1 s each)
            if finalize or (dur >= CLOUD_SPLIT and pause >= CLOUD_BREATH) or dur >= CLOUD_MAX:
                if dur > 0.8:
                    eng.submit(buf, utt_start, now, ui_q, tr)
                buf, utt_start, last_draft = np.zeros(0, np.float32), None, 0.0
            elif dur >= 0.8 and now - last_draft >= eng.DRAFT_EVERY:   # shorter clips get guessed at
                eng.draft(buf, ui_q)              # live line ~1 s after the words, not after the sentence
                last_draft = now
            continue
        if not finalize and (now - last_draft < DRAFT_EVERY or dur <= 0.8):
            continue   # not time for a draft yet
        de, lang = eng.transcribe(buf)
        last_draft = now
        # Fast talkers barely pause: a draft that already ends a sentence is final too
        finalize = finalize or (dur >= MIN_SENTENCE and de.rstrip().endswith((".", "?", "!")))
        if not finalize:
            if de and not is_junk(de):
                ui_q.put(("draft", de, eng.translate(de, lang)))
            continue
        if de and not is_junk(de):
            en = eng.translate(de, lang)
            ui_q.put(("final", de, en))
            tr.add(utt_start, now, de, en)
            eng.context = de
        buf, utt_start = np.zeros(0, np.float32), None
        ui_q.put(("draft", "", ""))

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
