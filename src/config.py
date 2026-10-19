"""Where Dolmi keeps things, and its settings.

Nothing the app writes lives next to the program, so it also works from Program Files (an all-users
install) or a read-only Dolmi.app in /Applications, and survives upgrades, which replace the program.
The folders come from dolmi_platform:

    Windows                                             macOS
    settings.json, .env, vocabulary.txt, glossary.txt   (CONFIG)
      %APPDATA%\\Dolmi                                   ~/Library/Application Support/Dolmi
    transcripts, summaries, inbox (dolmi.db)            (data_folder)
      Documents\\Dolmi                                   ~/Documents/Dolmi
    dolmi.log                                           (LOGS)
      %LOCALAPPDATA%\\Dolmi                              ~/Library/Logs/Dolmi
    speech/translation models
      %USERPROFILE%\\.cache\\huggingface                  ~/.cache/huggingface

On first run, files from the old location (the repo/program folder) are copied over, so nothing is lost.
"""
import copy
import json
import os
import shutil
from pathlib import Path

import dolmi_platform

ROOT = Path(__file__).resolve().parent.parent      # program folder: repo root, {app}\app when installed, or the app folder in Dolmi.app
CONFIG = dolmi_platform.config_dir()
LOGS = dolmi_platform.log_dir()
SETTINGS = CONFIG / "settings.json"
USER_FILES = ("settings.json", ".env", "vocabulary.txt", "glossary.txt")

# live_subs reads vocabulary.txt/glossary.txt from here too
os.environ["DOLMI_CONFIG"] = str(CONFIG)

DEFAULTS = {
    "model": "auto", "device": "auto", "font": 22, "opacity": 0.88,
    "show_german": True, "overlay": True, "overlay_geometry": None, "audio_device": "", "audio_source": "speaker", "mic_device": "", "language": "de",
    "mode": "translate", "ai_provider": "claude", "ai_models": {}, "api_keys": {}, "ai_context": "",
    "ai_length": "short", "pro": "",
    "save_transcripts": True, "keep_days": 0, "privacy_seen": False, "data_folder": "",
    "invisible": False, "theme": "paper", "cf_account": "", "speech_source": "local",
    # New keys: each domain adds its own on ONE commented line (ticket id first), and adds the same keys
    # to SETTABLE in src/ui/app.py if the page may change them, e.g.
    #   "my_key": 1,                              # APP-99: what it does
    "bar_time": False,                            # LIVE-5: speech-start time on subtitle bar lines
    "warn_silence": True,                         # APP-3: 'Dolmi hears nothing' warning while live
    "vocab_profile": "",                          # VOC-11: spelling-hint profile for meetings ('' = every term)
    "translator": "local", "translator_timeout": 1.5,   # QA-7: cloud subtitles for finals (local|gemini|deepl), s to wait
    "preload_models": True,                       # APP-4: load downloaded models when Dolmi opens, keep them
    "check_updates": True,                        # APP-6: ask GitHub for a newer release when Dolmi opens
    "shortcuts_on": True, "shortcuts": {},       # APP-7: global shortcuts on/off; {action: combo} (empty = defaults)
    "bar_locked": False, "bar_positions": {}, "bar_screen": "",  # APP-8: click-through lock; bar place per screen
    "speaker_labels": False,                      # SPK-4: who said each line (beta, on-device voice model)
    "clarify_queue": False,                       # VOC-9: collect unsure words, ask "Did they say ...?" after Stop
    "capture_both": False,                        # SPK-5: caption the microphone too (mic + speaker mixed, beta)
    "voice_readout": False,                       # VOICE-1: read English finals aloud (experimental, OS voice)
}


def _old_location():
    """Where the user's files lived before they moved to CONFIG. A Mac source checkout used to run from
    Mac/ (its own copy of the core), so that is where its files are; everywhere else it is ROOT."""
    if dolmi_platform.OS == "mac" and (ROOT / "Mac").is_dir():
        return ROOT / "Mac"
    return ROOT


def prepare():
    """Create the config folder; copy the user's files from the old location once; seed examples."""
    CONFIG.mkdir(parents=True, exist_ok=True)
    legacy = _old_location()
    for name in USER_FILES:
        new, old = CONFIG / name, legacy / name
        if not new.exists() and old.exists():
            shutil.copy2(old, new)                 # the original stays where it was
    for name in ("vocabulary", "glossary"):
        real, example = CONFIG / f"{name}.txt", ROOT / f"{name}.example.txt"
        if not real.exists() and example.exists():
            shutil.copyfile(example, real)


def load_env():
    """Read KEY=VALUE lines from .env into os.environ without overriding what's already set."""
    env = CONFIG / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_settings():
    s = copy.deepcopy(DEFAULTS)   # nested dicts (api_keys, ai_models) must never alias DEFAULTS
    try:
        s.update(json.loads(SETTINGS.read_text(encoding="utf-8")))
        s["ai_context"] = s["ai_context"] or s.pop("ai_notes", "")
    except (OSError, ValueError) as e:
        if SETTINGS.exists():
            print(f"settings.json unreadable, using defaults: {e}")
    return s


def save_settings(s):
    """Atomic write: a crash or concurrent save can never leave a truncated settings.json
    (which would silently drop the saved API keys on the next load)."""
    CONFIG.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(s, indent=2), encoding="utf-8")
    os.replace(tmp, SETTINGS)


def documents_folder() -> Path:
    return dolmi_platform.documents_folder()


def data_folder(settings) -> Path:
    return Path(settings["data_folder"]) if settings.get("data_folder") else documents_folder() / "Dolmi"


def version() -> str:
    """The version shown in Settings: the VERSION file shipped with the program."""
    try:
        return (ROOT / "VERSION").read_text(encoding="ascii").strip()
    except OSError:
        return "dev"
