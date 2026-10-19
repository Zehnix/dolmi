"""Dolmi model catalog: what each model does, what it needs, and whether this computer has/can run it."""
import sys, types
from dataclasses import dataclass
from pathlib import Path

from dolmi_platform import MODEL_NOTES, WORDS, auto_speech, gpu_ready, system_info  # noqa: F401  (models.system_info etc.)

# faster-whisper imports PyAV (FFmpeg) at startup, but only uses it to decode audio *files*;
# Dolmi passes raw samples. If PyAV's FFmpeg libraries can't load (Windows Smart App Control can block
# its unsigned DLLs at any time; it blocked libvpx here on 2026-10-05), fall back to an empty stand-in
# instead of crashing.
try:
    import av  # noqa: F401
except ImportError as e:
    print(f"PyAV unavailable ({e}); not needed for live audio, continuing without it")
    sys.modules["av"] = types.ModuleType("av")

@dataclass(frozen=True)
class Model:
    key: str            # name passed to faster-whisper, or "opus-<lang>" for translators
    name: str
    repo: str
    job: str
    size_mb: int        # download size from Hugging Face
    ram_gb: int         # free RAM needed while running
    min_cores: int      # for live use on CPU
    gpu: str            # "no" | "optional" | "recommended" | "required" (NVIDIA; Macs run on the CPU)
    vram_gb: int
    accuracy: int       # 1-5, relative, German speech
    speed: int          # 1-5, relative, on CPU
    note: str

SPEECH, TRANSLATE = "Speech recognition (speech → text, 99 languages)", "Translation (→ English, English → German)"

CATALOG = [
    Model("tiny", "Whisper Tiny", "Systran/faster-whisper-tiny", SPEECH, 78, 1, 2, "no", 0, 1, 5,
          MODEL_NOTES["tiny"]),
    Model("base", "Whisper Base", "Systran/faster-whisper-base", SPEECH, 148, 1, 2, "no", 0, 2, 4,
          "Fast and light. Noticeably less accurate than Small."),
    Model("small", "Whisper Small", "Systran/faster-whisper-small", SPEECH, 486, 2, 4, "no", 0, 3, 3,
          MODEL_NOTES["small"]),
    Model("medium", "Whisper Medium", "Systran/faster-whisper-medium", SPEECH, 1531, 4, 8, "optional", 4, 4, 2,
          MODEL_NOTES["medium"]),
    Model("large-v3-turbo", "Whisper Large v3 Turbo", "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
          SPEECH, 1622, 6, 12, "recommended", 4, 5, 2,
          MODEL_NOTES["large-v3-turbo"]),
    Model("large-v3", "Whisper Large v3", "Systran/faster-whisper-large-v3", SPEECH, 3091, 8, 16, "required", 6, 5, 1,
          MODEL_NOTES["large-v3"]),
]

# Spoken languages Dolmi offers. Every language gets English subtitles, except English, which gets
# German subtitles. Each has a ready-made Opus-MT translator (CTranslate2, ~150-180 MB) except those
# in NO_OWN_TRANSLATOR, which use the multilingual -> English model (also used for Auto-detect).
LANGUAGES = {
    "de": "German", "fr": "French", "es": "Spanish", "it": "Italian", "nl": "Dutch",
    "pl": "Polish", "pt": "Portuguese", "ru": "Russian", "uk": "Ukrainian", "tr": "Turkish",
    "ar": "Arabic", "fa": "Persian", "ur": "Urdu", "hi": "Hindi", "zh": "Chinese",
    "ja": "Japanese", "ko": "Korean", "sv": "Swedish", "da": "Danish", "fi": "Finnish",
    "cs": "Czech", "hu": "Hungarian", "ro": "Romanian", "el": "Greek", "bg": "Bulgarian",
    "id": "Indonesian", "vi": "Vietnamese", "en": "English",
}
SUBTITLES = {code: "de" if code == "en" else "en" for code in LANGUAGES}   # spoken -> subtitle language
NO_OWN_TRANSLATOR = {"pt", "fa", "ro", "el"}
TRANSLATOR_SIZES = {"de": 152, "fr": 154, "es": 160, "it": 177, "nl": 162, "pl": 158, "ru": 160,
                    "uk": 158, "tr": 157, "ar": 159, "zh": 160, "ja": 155, "ko": 160, "hi": 157,
                    "ur": 158, "sv": 151, "da": 153, "fi": 154, "cs": 157, "hu": 157, "bg": 158,
                    "id": 149, "vi": 148, "en": 152, "mul": 159}

def translator_key(lang):
    """Model key of the translator for a spoken language: "opus-de" = German -> English,
    "opus-en" = English -> German, "opus-mul" = many languages -> English (also for Auto-detect)."""
    if lang == "en":
        return "opus-en"
    if lang == "auto" or lang in NO_OWN_TRANSLATOR or lang not in TRANSLATOR_SIZES:
        return "opus-mul"
    return f"opus-{lang}"

for _code, _mb in TRANSLATOR_SIZES.items():
    if _code == "mul":
        CATALOG.append(Model(
            "opus-mul", "Opus-MT Many languages → English", "gaudi/opus-mt-mul-en-ctranslate2",
            TRANSLATE, _mb, 1, 2, "no", 0, 3, 5,
            "Fallback for languages without their own translator and for Auto-detect. Lower quality."))
        continue
    _src, _dst = LANGUAGES[_code], LANGUAGES[SUBTITLES[_code]]
    CATALOG.append(Model(
        f"opus-{_code}", f"Opus-MT {_src} → {_dst}", f"gaudi/opus-mt-{_code}-{SUBTITLES[_code]}-ctranslate2",
        TRANSLATE, _mb, 1, 2, "no", 0, 4, 5, f"Translates {_src} sentences into {_dst} in ~0.06 s, even on CPU."))
BY_KEY = {m.key: m for m in CATALOG}

# ------------------------------------------------------------------ this computer
# system_info(), gpu_ready() and auto_speech() are platform code (dolmi_platform), re-exported above.

def fit(model, pc):
    """(verdict, colour key) for how well this computer runs the model live."""
    if pc["ram_gb"] < model.ram_gb + 2:
        return "Not enough RAM", "live"
    if pc["gpu"]:
        return "Runs well (GPU)", "ok"
    if model.gpu == "required":
        return WORDS["fit_needs_gpu"], "live"
    if pc["cores"] < model.min_cores or model.gpu == "recommended":
        return WORDS["fit_slow"], "draft"
    if model.gpu == "optional":
        return "Works, 2-4 s delay", "draft"
    return "Runs well", "ok"

# ------------------------------------------------------------------ install state
def _cache_dir(model):
    from huggingface_hub.constants import HF_HUB_CACHE
    return Path(HF_HUB_CACHE) / ("models--" + model.repo.replace("/", "--"))

def is_installed(model):
    # faster-whisper fetches only the files it needs, so check for the weights, not a full snapshot
    snapshots = _cache_dir(model) / "snapshots"
    return snapshots.exists() and any((s / "model.bin").exists() for s in snapshots.iterdir())

def downloaded_mb(model):
    # Without symlink support (most Windows PCs) files end up in snapshots/, not blobs/; on a Mac they
    # land in blobs/ and snapshots/ holds symlinks to them, so symlinks are skipped
    root = _cache_dir(model)
    if not root.exists():
        return 0
    return sum(f.stat().st_size for f in root.rglob("*") if f.is_file() and not f.is_symlink()) / 1e6

def download(model):
    if model.job == SPEECH:   # same files faster-whisper loads
        from faster_whisper.utils import download_model
        download_model(model.key)
    else:
        from huggingface_hub import snapshot_download
        snapshot_download(model.repo)

def uninstall(model):
    from huggingface_hub import scan_cache_dir
    for repo in scan_cache_dir().repos:
        if repo.repo_id == model.repo:
            scan_cache_dir().delete_revisions(*(r.commit_hash for r in repo.revisions)).execute()
