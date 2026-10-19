"""macOS side of Dolmi: audio capture (Core Audio process tap helper + sounddevice), chip detection,
clipboard, invisible mode (window rebuilds), key storage (Keychain secret), folders and the hotkey.

Every macOS library (sounddevice, AppKit/objc, keyring, Carbon) is imported inside the functions that
need it, so this module imports on any OS and can be unit-tested on Windows. The same names exist in
windows.py; dolmi_platform/__init__.py picks one of the two.
"""
import hmac
import json
import os
import platform
import queue
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import mac_stealth as stealth

OS = "mac"
WEBVIEW_GUI = "cocoa"
mic_privacy_url = "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"

# Every user-visible word that differs between Windows and Mac (same keys as windows.WORDS).
WORDS = {
    "computer": "Mac",
    "COMPUTER": "MAC",
    "os_user": "Mac user",
    "mod": "⌃",
    "hotkey": "⌃⌥⇧D",
    "taskbar": "Dock",
    "speaker_default": "All Mac audio",
    "speaker_hint": "Everything your Mac plays, or a microphone",
    "key_storage": "Encrypted with your Mac Keychain",
    "fit_needs_gpu": "Too slow for live use",
    "fit_slow": "Slow on this Mac",
}
DEFAULT_SPEAKER = WORDS["speaker_default"]   # what "Speaker" listens to when no device is picked

# Speech model descriptions in Settings (models.py) for Apple chips.
MODEL_NOTES = {
    "tiny": "Fastest, for very old Macs. Makes many mistakes in German.",
    "small": "Recommended for most Macs. Good balance of speed and accuracy.",
    "medium": "More accurate. Needs a recent Apple chip; adds 2-4 s delay.",
    "large-v3-turbo": "Near best accuracy, much faster than Large v3. Only Pro/Max/Ultra chips keep up live.",
    "large-v3": "Highest accuracy. Too slow for live subtitles on a Mac; fine for re-checking names.",
}


# ------------------------------------------------------------------ capture (process tap + sounddevice)
SYSTEM_AUDIO = ""          # audio_device value for "everything the Mac plays" (the default)

def _audio_helper():
    """The dolmi-audio binary (Core Audio process tap); built from Mac/audio/dolmi-audio.swift.
    From source it sits in Mac/audio; inside Dolmi.app in Contents/Resources/app/audio."""
    root = Path(__file__).resolve().parent.parent      # repo root, or the app folder in Dolmi.app
    for d in (root / "Mac" / "audio", root / "audio", Path(getattr(sys, "_MEIPASS", "")) / "audio"):
        if (d / "dolmi-audio").is_file():
            return d / "dolmi-audio"
    raise RuntimeError("the system audio helper is missing - run ./start.command once to build it")

def output_devices():
    """Inputs Dolmi can listen to besides all Mac audio: microphones and virtual devices (BlackHole...)."""
    import sounddevice as sd
    return [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]

def input_devices():
    """Names of microphones (and other inputs) Dolmi can listen to for in-person meetings."""
    import sounddevice as sd
    return [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]

def _sd_capture(device, out_q, stop, feed):
    """Stream one input device by name (None = the Mac's default input) until stop is set."""
    import sounddevice as sd
    inputs = [d for d in sd.query_devices() if d["max_input_channels"] > 0]
    info = next((d for d in inputs if device and device in d["name"]), None)
    if info is None and device is None:
        default = sd.default.device[0]
        info = sd.query_devices(default) if default is not None and default >= 0 else (inputs[0] if inputs else None)
    if info is None:
        raise RuntimeError(f"input '{device}' not found - pick another in Settings" if device else "no microphone found")
    rate, ch = int(info["default_samplerate"]), max(1, min(int(info["max_input_channels"]), 2))
    print(f"🎧 Capturing: {info['name']}  ({rate} Hz, {ch} ch)")
    with sd.InputStream(device=info["index"], channels=ch, samplerate=rate, dtype="float32",
                        blocksize=int(rate * 0.1),
                        callback=lambda data, *_: feed(data.mean(axis=1).copy(), rate)):
        stop.wait()

def mic_stream(out_q: queue.Queue, stop: threading.Event, device=None, on_level=None):
    """Capture a microphone (people in the room); the Mac's default input if device is None."""
    import numpy as np
    from live_subs import SR

    def feed(a, rate):
        idx = np.linspace(0, len(a) - 1, int(len(a) * SR / rate)).astype(np.int64)
        out_q.put(a[idx])
        if on_level:
            on_level(float(np.sqrt(np.mean(a * a))) if len(a) else 0.0)
    _sd_capture(device or None, out_q, stop, feed)

def loopback_stream(out_q: queue.Queue, stop: threading.Event, device=None, on_level=None):
    """Capture everything the Mac plays (device None) or one input device by name.
    on_level(rms) is called about 10x a second so the UI can show that audio is arriving."""
    import numpy as np
    from live_subs import SR

    def feed(a, rate):
        # cheap resample to 16 kHz (fine for speech)
        idx = np.linspace(0, len(a) - 1, int(len(a) * SR / rate)).astype(np.int64)
        out_q.put(a[idx])
        if on_level:
            on_level(float(np.sqrt(np.mean(a * a))) if len(a) else 0.0)

    if device:
        return _sd_capture(device, out_q, stop, feed)

    proc = subprocess.Popen([str(_audio_helper())], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        header = proc.stdout.readline().decode(errors="replace").split()
        if len(header) != 3 or header[0] != "DOLMI-AUDIO":
            raise RuntimeError(proc.stderr.read().decode(errors="replace").strip() or "audio helper failed")
        rate = int(header[1])
        print(f"🎧 Capturing: all Mac audio  ({rate} Hz, mono)")
        block = int(rate * 0.1) * 4            # 100 ms of float32
        while not stop.is_set():
            data = proc.stdout.read(block)
            if not data:
                raise RuntimeError(proc.stderr.read().decode(errors="replace").strip() or "audio helper stopped")
            feed(np.frombuffer(data[: len(data) // 4 * 4], dtype=np.float32), rate)
    finally:
        proc.terminate()
        try:
            proc.wait(2)
        except subprocess.TimeoutExpired:
            proc.kill()


# ------------------------------------------------------------------ compute (always the CPU cores)
def prepare_gpu():
    """Nothing to prepare: CTranslate2 has no Metal backend, so there is no GPU path on a Mac."""

def compute_device(device):
    """(device, compute type) for faster-whisper."""
    if device == "auto":   # CTranslate2 has no Metal backend: Whisper runs on the CPU cores
        device = "cpu"
    return device, "int8"

def auto_speech(gpu=None):
    """Auto's pick: Small on every Mac. Medium is a manual choice for Pro/Max/Ultra chips."""
    return "small"

def _sysctl(name):
    try:
        return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""

def _chip_name():
    """'Apple M4', 'Apple M2 Pro', or the Intel CPU name."""
    return _sysctl("machdep.cpu.brand_string") or platform.processor() or "Mac"

def system_info():
    try:
        ram = int(_sysctl("hw.memsize")) / 2**30
    except ValueError:
        ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
    # performance cores do the real-time work; efficiency cores lag behind on Whisper
    perf = _sysctl("hw.perflevel0.physicalcpu")
    return {"ram_gb": round(ram), "cores": os.cpu_count() or 1, "gpu": gpu_ready(),
            "perf_cores": int(perf) if perf.isdigit() else os.cpu_count() or 1, "chip": _chip_name()}

def gpu_ready():
    """Whisper here runs through CTranslate2, which has no Metal backend: always the CPU on a Mac."""
    return False

def gpu_name():
    """Shown where Windows shows the graphics card: the Apple chip (or Intel CPU) name."""
    return _chip_name()


# ------------------------------------------------------------------ desktop
def copy_text(text):
    """Put text on the Mac clipboard (works regardless of WebKit clipboard permissions)."""
    try:
        subprocess.run(["pbcopy"], input=(text or "").encode("utf-8"), check=True, timeout=5,
                       env={**os.environ, "LANG": "en_US.UTF-8"})
        return True
    except (OSError, subprocess.SubprocessError):
        return False

def open_path(path):
    """Show a folder in Finder."""
    subprocess.run(["open", str(path)], check=False)

def register_app():
    """Nothing to register: the Dock entry comes from Dolmi.app (or the Python framework from source)."""

def app_icon(root):
    """The Dock icon when running from source (Dolmi.app has its own .icns)."""
    return Path(root) / "assets" / "dolmi.png"


# ------------------------------------------------------------------ invisible mode (NSWindow)
def _native(win):
    """The NSWindow behind a pywebview window (None until it has been created)."""
    return getattr(win, "native", None) if win else None

def _icon():
    import config
    return app_icon(config.ROOT)

def overlay_stealth(api):
    """The bar floats on every Space and over full-screen meetings, and follows invisible mode for capture."""
    win = _native(api._overlay.window if api._overlay else None)
    stealth.float_everywhere(win)
    stealth.exclude_from_capture(win, api._settings["invisible"])

def apply_invisible(api, on):
    """Invisible mode on/off. macOS 27 can't make a hidden window capturable again, so turning it off
    may swap in fresh windows (see mac_stealth.py)."""
    stealth.apply(_native(api._window), on, dock=True, icon=_icon())
    if api._overlay and not stealth.exclude_from_capture(_native(api._overlay.window), on):
        _rebuild_overlay(api)
    if not on and api._window and not stealth.capture_allowed(_native(api._window)):
        _rebuild_main(api)

def _rebuild_overlay(api):
    """A fresh subtitle bar (macOS 27 can't make a hidden-from-capture window capturable again)."""
    old, visible = api._overlay, api._overlay_visible
    api._overlay, api._overlay_ready, api._overlay_visible = None, False, False
    if visible:
        api.show_overlay()
    try:
        old.window.destroy()
    except Exception as e:
        print(f"overlay rebuild: {e}")

def _rebuild_main(api):
    """A fresh main window at the same place, on the same tab, with this session's captions."""
    old = api._window
    if not api._make_window:
        return
    try:
        tab = old.evaluate_js("(document.querySelector('.nav .a')||{dataset:{}}).dataset.v") or "live"
        geo = dict(x=old.x, y=old.y, width=old.width, height=old.height)
    except Exception:
        tab, geo = "live", {}
    new = api._make_window(**geo)

    def loaded(*_):
        time.sleep(1.0)                    # let the page's boot() finish first
        api._emit("onInvisible", False)
        api._window and api._window.evaluate_js(
            f"(document.querySelector('.nav [data-v={json.dumps(tab)}]')||{{click(){{}}}}).click()")
        if api._session:
            api._emit("onListening", True, api._engine_label())
            with api._hist_lock:
                lines = list(api._history)
            for ts, de, en, *meta in lines:
                api._emit("onLine", ts[:5], de, en, meta[0] if meta else {})
        api._emit("onBar", bool(api._overlay_visible))
    new.events.loaded += lambda *a: threading.Thread(target=loaded, daemon=True).start()
    api._window = new
    try:
        old.destroy()
    except Exception as e:
        print(f"window rebuild: {e}")


# ------------------------------------------------------------------ API key storage (Keychain)
# A random 32-byte secret lives in the login Keychain (service "Dolmi"). Keys are encrypted with an
# HMAC-SHA256 keystream (CTR) and authenticated with HMAC-SHA256, all from the standard library.
_KEYCHAIN = ("Dolmi", "settings-encryption")
_secret = None

def _master():
    global _secret
    if _secret is None:
        import keyring
        stored = keyring.get_password(*_KEYCHAIN)
        if not stored:
            stored = secrets.token_hex(32)
            keyring.set_password(*_KEYCHAIN, stored)
        _secret = bytes.fromhex(stored)
    return _secret

def _stream(key, nonce, n):
    out = b"".join(hmac.digest(key, nonce + i.to_bytes(4, "big"), "sha256") for i in range((n + 31) // 32))
    return out[:n]

def _seal(protect, data):
    try:
        master = _master()
    except Exception as e:
        raise OSError(f"the Keychain could not encrypt/decrypt the API key ({e})")
    enc, mac = hmac.digest(master, b"enc", "sha256"), hmac.digest(master, b"mac", "sha256")
    if protect:
        nonce = secrets.token_bytes(16)
        body = bytes(a ^ b for a, b in zip(data, _stream(enc, nonce, len(data))))
        return nonce + body + hmac.digest(mac, nonce + body, "sha256")
    nonce, body, tag = data[:16], data[16:-32], data[-32:]
    if len(data) < 48 or not hmac.compare_digest(tag, hmac.digest(mac, nonce + body, "sha256")):
        raise ValueError("not encrypted by this Mac user")
    return bytes(a ^ b for a, b in zip(body, _stream(enc, nonce, len(body))))

def protect(data: bytes) -> bytes:
    """Encrypt with this Mac user's Keychain secret; raises OSError if the Keychain fails."""
    return _seal(True, data)

def unprotect(data: bytes) -> bytes:
    """Decrypt what protect() wrote; raises ValueError if another user/Mac wrote it."""
    return _seal(False, data)


# ------------------------------------------------------------------ folders
def config_dir() -> Path:
    """settings.json, .env, vocabulary.txt, glossary.txt: ~/Library/Application Support/Dolmi."""
    return Path.home() / "Library" / "Application Support" / "Dolmi"

def log_dir() -> Path:
    """dolmi.log: ~/Library/Logs/Dolmi."""
    return Path.home() / "Library" / "Logs" / "Dolmi"

def documents_folder() -> Path:
    return Path.home() / "Documents"

def needs_log_file():
    """Launched from Finder (Dolmi.app) there's no terminal: print() must go to the log file."""
    return sys.stdout is None or sys.stderr is None or not sys.stdout.isatty()


# ------------------------------------------------------------------ global hotkey
def start_recover_hotkey(on_press):
    """⌃⌥⇧D: a system-wide hotkey that always brings Dolmi back, even hidden from the Dock."""
    return stealth.GlobalHotkey(stealth.MOD_CONTROL | stealth.MOD_ALT | stealth.MOD_SHIFT, stealth.VK_D,
                                on_press, name=f"show Dolmi ({WORDS['hotkey']})").start()
