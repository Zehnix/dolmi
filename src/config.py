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
}


def prepare():
    """Create the config folder; copy the user's files from the old location once; seed examples."""
    CONFIG.mkdir(parents=True, exist_ok=True)
    for name in USER_FILES:
        new, old = CONFIG / name, ROOT / name
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
