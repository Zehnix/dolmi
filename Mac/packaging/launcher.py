"""Dolmi.app entry point. The program ships as plain source in Contents/Resources/app (the same layout
as the repo root, plus the audio helper in app/audio), so config.ROOT, the web UI and dolmi_platform
resolve exactly as when running from source; this launcher only puts that folder on sys.path and runs
src/main.py."""
import multiprocessing
import os
import runpy
import sys

# Helper processes Python starts (multiprocessing's resource tracker, at Start) re-run this binary;
# without this they would boot a second, invisible Dolmi instead of doing their job and exiting.
multiprocessing.freeze_support()

APP = os.path.join(sys._MEIPASS, "app")
sys.path[:0] = [APP, os.path.join(APP, "src")]

if os.environ.get("DOLMI_NEVER_SET"):   # never runs: lists the imports PyInstaller must bundle
    import anthropic, AppKit, ctranslate2, faster_whisper, Foundation, huggingface_hub  # noqa: E401,F401
    import keyring, keyring.backends.macOS, numpy, objc, openai, PyObjCTools.AppHelper  # noqa: E401,F401
    import sentencepiece, sounddevice, sqlite3, webview, webview.platforms.cocoa, WebKit  # noqa: E401,F401
    import base64, urllib.request, wave  # noqa: E401,F401  (cloud_speech)

runpy.run_path(os.path.join(APP, "src", "main.py"), run_name="__main__")
