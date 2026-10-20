# PyInstaller spec for Dolmi.app. Build with packaging/build.sh, not directly.
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

MAC = Path(SPECPATH).parent          # Mac/: the audio helper and the icon
ROOT = MAC.parent                     # repo root: the shared program (Windows and Mac)
VERSION = (ROOT / "VERSION").read_text().strip()

# the program itself, as source, in Contents/Resources/app (see launcher.py): the shared files from
# the repo root, plus the Mac-only audio helper from Mac/audio (into app/audio)
APP_FILES = ["assistant.py", "cloud_speech.py", "cloud_translate.py", "inbox.py", "live_subs.py", "models.py", "speakers.py", "streaming.py", "clarify.py", "netgate.py", "translation_router.py", "uncertainty.py", "VERSION",
             "vocabulary.example.txt", "glossary.example.txt", "LICENSE", "THIRD-PARTY-NOTICES.md",
             "PRIVACY.md", "assets/dolmi.png"]
APP_DIRS = ["src", "dolmi_platform", "voice"] # copied whole (web UI: src/ui/web incl. css/ and js/)
MAC_FILES = ["audio/dolmi-audio"]
datas = [(str(ROOT / f), str(Path("app") / Path(f).parent)) for f in APP_FILES]
datas += [(str(MAC / f), str(Path("app") / Path(f).parent)) for f in MAC_FILES]
for d in APP_DIRS:
    for p in (ROOT / d).rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts:
            datas.append((str(p), str(Path("app") / p.parent.relative_to(ROOT))))

binaries, hiddenimports = [], []
for pkg in ("faster_whisper", "ctranslate2", "webview", "sounddevice", "onnxruntime", "av",
            "sentencepiece", "tokenizers", "keyring", "kaldi_native_fbank"):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h

a = Analysis([str(MAC / "packaging" / "launcher.py")], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, excludes=["tkinter", "pytest", "PyInstaller"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Dolmi", console=False,
          argv_emulation=False, target_arch="arm64" if os.uname().machine == "arm64" else None)
coll = COLLECT(exe, a.binaries, a.datas, name="Dolmi")
app = BUNDLE(
    coll, name="Dolmi.app", icon=str(MAC / "assets" / "dolmi.icns"), bundle_identifier="app.dolmi.Dolmi",
    version=VERSION,
    info_plist={
        "CFBundleDisplayName": "Dolmi",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "14.2",          # Core Audio process taps
        "LSApplicationCategoryType": "public.app-category.productivity",
        "NSHighResolutionCapable": True,
        "NSAudioCaptureUsageDescription":
            "Dolmi listens to what your Mac plays (your meeting) to show live subtitles. Audio never leaves your Mac.",
        "NSMicrophoneUsageDescription":
            "Dolmi can caption a microphone you choose in Settings. Audio never leaves your Mac.",
        "NSHumanReadableCopyright": "© 2026 Zehnix (Annas Mustafa). AGPL-3.0 or commercial licence",
    })
