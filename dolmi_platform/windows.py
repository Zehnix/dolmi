"""Windows side of Dolmi: audio capture (WASAPI loopback), GPU detection, clipboard, invisible mode,
key storage (DPAPI), folders and the global hotkey.

Every Windows library (pyaudiowpatch, ctypes.windll, ctranslate2) is imported inside the functions
that need it, so this module imports on any OS and can be unit-tested anywhere. The same names exist
in mac.py; dolmi_platform/__init__.py picks one of the two.
"""
import ctypes
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

OS = "win"
WEBVIEW_GUI = "edgechromium"
mic_privacy_url = "ms-settings:privacy-microphone"

# Every user-visible word that differs between Windows and Mac. The page reads the same table from
# state()["platform"]["words"] through W('key') in js/core.js.
WORDS = {
    "computer": "PC",
    "COMPUTER": "PC",
    "os_user": "Windows user",
    "mod": "Ctrl",
    "hotkey": "Ctrl+Alt+Shift+D",
    "taskbar": "taskbar",
    "speaker_default": "Default speaker",
    "mic_privacy": "Windows privacy settings",
    "speaker_hint": "The speaker your meeting plays through",
    "key_storage": "Encrypted with Windows DPAPI",
    "fit_needs_gpu": "Needs NVIDIA GPU",
    "fit_slow": "Slow on this PC",
}
DEFAULT_SPEAKER = WORDS["speaker_default"]   # what "Speaker" listens to when no device is picked

# Speech model descriptions in Settings (models.py) that depend on the hardware Windows PCs have.
MODEL_NOTES = {
    "tiny": "Fastest, for very weak PCs. Makes many mistakes in German.",
    "small": "Recommended for laptops without NVIDIA GPU. Good balance of speed and accuracy.",
    "medium": "More accurate. On CPU it needs a strong processor and adds 2-4 s delay.",
    "large-v3-turbo": "Near best accuracy, much faster than Large v3. Best with an NVIDIA GPU.",
    "large-v3": "Highest accuracy. Too slow for live subtitles without an NVIDIA GPU.",
}


# ------------------------------------------------------------------ capture (WASAPI loopback)
def output_devices():
    """Names of speakers/headphones Dolmi can listen to (any meeting app playing there works)."""
    import pyaudiowpatch as pyaudio
    pa = pyaudio.PyAudio()
    try:
        return [d["name"].removesuffix(" [Loopback]") for d in pa.get_loopback_device_info_generator()]
    finally:
        pa.terminate()

def input_devices():
    """Names of microphones Dolmi can listen to (for in-person meetings)."""
    import pyaudiowpatch as pyaudio
    pa = pyaudio.PyAudio()
    try:
        wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]
        return [d["name"] for d in map(pa.get_device_info_by_index, range(pa.get_device_count()))
                if d["hostApi"] == wasapi and d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice")]
    finally:
        pa.terminate()

def _capture(pa, dev, out_q, stop, on_level):
    """Stream one PyAudio input device as 16 kHz mono into out_q until stop is set."""
    import numpy as np
    import pyaudiowpatch as pyaudio
    from live_subs import SR
    rate, ch = int(dev["defaultSampleRate"]), max(1, min(int(dev["maxInputChannels"]), 2))
    print(f"🎧 Capturing: {dev['name']}  ({rate} Hz, {ch} ch)")

    def cb(data, frames, t, status):
        a = np.frombuffer(data, dtype=np.float32).reshape(-1, ch).mean(axis=1)
        # cheap resample to 16 kHz (fine for speech)
        idx = np.linspace(0, len(a) - 1, int(len(a) * SR / rate)).astype(np.int64)
        out_q.put(a[idx])
        if on_level:
            on_level(float(np.sqrt(np.mean(a * a))) if len(a) else 0.0)
        return (None, pyaudio.paContinue)

    s = pa.open(format=pyaudio.paFloat32, channels=ch, rate=rate, input=True,
                input_device_index=dev["index"], frames_per_buffer=int(rate * 0.1),
                stream_callback=cb)
    s.start_stream()
    stop.wait()
    s.stop_stream(); s.close()

def loopback_stream(out_q: queue.Queue, stop: threading.Event, device=None, on_level=None):
    """Capture whatever Windows plays on a speaker (WASAPI loopback); default speaker if device is None.
    on_level(rms) is called about 10x a second so the UI can show that audio is arriving."""
    import pyaudiowpatch as pyaudio
    pa = pyaudio.PyAudio()
    try:
        wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        spk = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
        if device:
            spk = next((lb for lb in pa.get_loopback_device_info_generator() if device in lb["name"]), spk)
        if not spk["isLoopbackDevice"]:
            for lb in pa.get_loopback_device_info_generator():
                if spk["name"] in lb["name"]:
                    spk = lb
                    break
        _capture(pa, spk, out_q, stop, on_level)
    finally:
        pa.terminate()

def mic_stream(out_q: queue.Queue, stop: threading.Event, device=None, on_level=None):
    """Capture a microphone (people in the room); the Windows default microphone if device is None."""
    import pyaudiowpatch as pyaudio
    pa = pyaudio.PyAudio()
    try:
        wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        mics = [d for d in map(pa.get_device_info_by_index, range(pa.get_device_count()))
                if d["hostApi"] == wasapi["index"] and d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice")]
        mic = next((d for d in mics if device and device in d["name"]), None)
        if mic is None and wasapi["defaultInputDevice"] >= 0:
            mic = pa.get_device_info_by_index(wasapi["defaultInputDevice"])
        if mic is None:
            raise RuntimeError("no microphone found - plug one in, or check Windows Settings > Privacy > Microphone")
        _capture(pa, mic, out_q, stop, on_level)
    finally:
        pa.terminate()


# ------------------------------------------------------------------ compute (NVIDIA GPU or CPU)
def prepare_gpu():
    """Make pip-installed CUDA libs (nvidia-cublas-cu12 / nvidia-cudnn-cu12) discoverable, so the GPU
    path works without a full CUDA toolkit. On PATH so find_library() sees them, and on the DLL
    search path so CTranslate2 can load them. A no-op when the wheels aren't installed (stays CPU)."""
    roots = [Path(sys.prefix) / "Lib" / "site-packages", *map(Path, sys.path)]   # venv, or {app}\gpu
    seen = set()
    for d in (r / "nvidia" / pkg / "bin" for r in roots for pkg in ("cublas", "cudnn")):
        if d.is_dir() and d not in seen:
            seen.add(d)
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
            try:
                os.add_dll_directory(str(d))
            except OSError:
                pass

def compute_device(device):
    """(device, compute type) for faster-whisper; "auto" means the GPU when CTranslate2 can use it."""
    import ctranslate2
    if device == "auto":   # no torch: ask CTranslate2 directly
        import ctypes.util   # GPU alone isn't enough: CUDA 12 libs must be installed too
        libs_ok = all(ctypes.util.find_library(l) for l in ("cublas64_12", "cudnn64_9"))
        device = "cuda" if ctranslate2.get_cuda_device_count() > 0 and libs_ok else "cpu"
    return device, ("float16" if device == "cuda" else "int8")

def auto_speech(gpu=None):
    """Auto's speech model: Large v3 Turbo on a usable NVIDIA GPU, else Small."""
    if gpu is None:
        gpu = gpu_ready()
    return "large-v3-turbo" if gpu else "small"

class _MemStatus(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

def system_info():
    m = _MemStatus(); m.dwLength = ctypes.sizeof(m)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return {"ram_gb": round(m.ullTotalPhys / 2**30), "cores": os.cpu_count() or 1,
            "gpu": gpu_ready()}

def gpu_ready():
    """NVIDIA GPU that CTranslate2 can actually use (card + CUDA 12 libraries)."""
    import ctypes.util, ctranslate2
    return ctranslate2.get_cuda_device_count() > 0 and all(
        ctypes.util.find_library(l) for l in ("cublas64_12", "cudnn64_9"))

def gpu_name():
    """The graphics card's marketing name (NVIDIA first), for display only."""
    flags = 0x08000000  # CREATE_NO_WINDOW
    for cmd in (["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name) -join '|'"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=8, creationflags=flags).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        names = [n.strip() for n in out.replace("|", "\n").splitlines() if n.strip()]
        if names:
            return next((n for n in names if "NVIDIA" in n.upper()), names[0])
    return ""


# ------------------------------------------------------------------ desktop
_user32_ready = False

def _user32():
    """user32 with the signatures Dolmi relies on (set once; ctypes shares the loaded DLL)."""
    global _user32_ready
    from ctypes import wintypes
    u = ctypes.windll.user32
    if not _user32_ready:
        u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        u.GetAncestor.restype = wintypes.HWND
        u.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        u.SetWindowDisplayAffinity.restype = wintypes.BOOL
        _user32_ready = True
    return u

def copy_text(text):
    """Put text on the Windows clipboard (works regardless of WebView2 clipboard permissions)."""
    _u = _user32()
    k32 = ctypes.windll.kernel32
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    _u.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    data = (text or "").encode("utf-16-le") + b"\x00\x00"
    for _ in range(10):
        if _u.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return False
    try:
        _u.EmptyClipboard()
        h = k32.GlobalAlloc(0x0002, len(data))          # GMEM_MOVEABLE
        ctypes.memmove(k32.GlobalLock(h), data, len(data))
        k32.GlobalUnlock(h)
        _u.SetClipboardData(13, h)                       # CF_UNICODETEXT; the clipboard owns h now
    finally:
        _u.CloseClipboard()
    return True

def open_path(path):
    """Show a folder in Explorer (raises OSError like os.startfile)."""
    os.startfile(path)

def register_app():
    """Own taskbar entry + icon instead of inheriting pythonw's."""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Dolmi.App")
    except (AttributeError, OSError):
        pass

def app_icon(root):
    """The window/taskbar icon ("-2" makes Windows drop its cached old icon)."""
    return Path(root) / "assets" / "dolmi-2.ico"


# ------------------------------------------------------------------ invisible mode (raw HWND)
WDA_NONE = 0x00
WDA_EXCLUDEFROMCAPTURE = 0x11  # window is simply absent from captures (Windows 10 2004+)
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080  # off the taskbar and Alt+Tab
WS_EX_APPWINDOW = 0x00040000   # forces a taskbar button
_SWP = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020  # NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED

def _native_hwnd(win, title):
    """Top-level HWND for a pywebview window: .native first, window title as the fallback."""
    try:
        native = getattr(win, "native", None)
        if native is not None:
            return int(native.Handle.ToInt32())
    except Exception:
        pass
    try:
        return _user32().FindWindowW(None, title) if win else 0
    except Exception:
        return 0

def _set_toolwindow(hwnd, on):
    """Off the taskbar and Alt+Tab (on) or back on them (off)."""
    if not hwnd:
        return
    try:
        _u = _user32()
        ex = _u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        want = (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW if on else (ex | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW
        if want != ex:
            _u.SetWindowLongW(hwnd, GWL_EXSTYLE, want)
            _u.SetWindowPos(hwnd, 0, 0, 0, 0, 0, _SWP)
    except Exception as e:
        print(f"invisible: taskbar style failed ({e})")

def _set_capture(hwnd, hidden):
    """Leave the window out of screen shares/recordings (hidden) or include it again."""
    if not hwnd:
        return False
    try:
        return bool(_user32().SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE if hidden else WDA_NONE))
    except Exception as e:
        print(f"invisible: capture affinity failed ({e})")
        return False

def overlay_stealth(api):
    """The bar never has a taskbar button, and follows invisible mode for capture."""
    hwnd = _native_hwnd(api._overlay.window if api._overlay else None, "Dolmi overlay")
    _set_toolwindow(hwnd, True)
    _set_capture(hwnd, api._settings["invisible"])

def apply_invisible(api, on):
    """Invisible mode on/off for the main window and the subtitle bar."""
    main = _native_hwnd(api._window, "Dolmi")
    _set_toolwindow(main, on)
    _set_capture(main, on)                 # last: a style change can reset the affinity
    if api._overlay:
        # Changing capture affinity on a visible WebView2 window can leave it painted blank
        # (white) until it's re-shown — so re-show it ourselves around the change.
        ov_hwnd = _native_hwnd(api._overlay.window, "Dolmi overlay")
        if api._overlay_visible:
            api._overlay.hide()
            _set_capture(ov_hwnd, on)
            api._overlay.show()
        else:
            _set_capture(ov_hwnd, on)



# ------------------------------------------------------------------ subtitle bar click-through lock (APP-8)
WS_EX_LAYERED = 0x00080000      # required for WS_EX_TRANSPARENT to let clicks through
WS_EX_TRANSPARENT = 0x00000020  # hit-testing skips the window: clicks land on the window below
LWA_ALPHA = 0x00000002
_layered_by_us = set()          # HWNDs Dolmi made layered (only those lose WS_EX_LAYERED on unlock)

def _set_click_through(hwnd, on):
    """Clicks go through the window (on) or reach it again (off). True when the style bits are as asked."""
    if not hwnd:
        return False
    try:
        u = _user32()
        ex = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if on:
            want = ex | WS_EX_LAYERED | WS_EX_TRANSPARENT
            if not ex & WS_EX_LAYERED:
                _layered_by_us.add(hwnd)
        else:
            want = ex & ~WS_EX_TRANSPARENT
            if hwnd in _layered_by_us:
                want &= ~WS_EX_LAYERED
                _layered_by_us.discard(hwnd)
        if want != ex:
            u.SetWindowLongW(hwnd, GWL_EXSTYLE, want)
            if on and not ex & WS_EX_LAYERED:
                u.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)   # a fresh layered window paints nothing until set
            u.SetWindowPos(hwnd, 0, 0, 0, 0, 0, _SWP)
        return _click_through_bits(hwnd) == bool(on)
    except Exception as e:
        print(f"click-through: style change failed ({e})")
        return False

def _click_through_bits(hwnd):
    """True when the window has both WS_EX_LAYERED and WS_EX_TRANSPARENT (clicks pass through it)."""
    ex = _user32().GetWindowLongW(hwnd, GWL_EXSTYLE)
    return bool(ex & WS_EX_LAYERED and ex & WS_EX_TRANSPARENT)

def set_click_through(win, on):
    """Subtitle bar lock: the bar ignores the mouse, so it never takes a click from the meeting window."""
    return _set_click_through(_native_hwnd(win, "Dolmi overlay"), on)

def click_through(win):
    hwnd = _native_hwnd(win, "Dolmi overlay")
    return bool(hwnd) and _click_through_bits(hwnd)

# ------------------------------------------------------------------ API key storage (DPAPI)
def _dpapi(protect, data):
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in, blob_out = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), _Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("Windows could not encrypt/decrypt the API key")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)

def protect(data: bytes) -> bytes:
    """Encrypt for this Windows user only (DPAPI); raises OSError on failure."""
    return _dpapi(True, data)

def unprotect(data: bytes) -> bytes:
    """Decrypt what protect() wrote for this Windows user; raises OSError if it can't."""
    return _dpapi(False, data)


# ------------------------------------------------------------------ folders
def config_dir() -> Path:
    """settings.json, .env, vocabulary.txt, glossary.txt: %APPDATA%\\Dolmi."""
    return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Dolmi"

def log_dir() -> Path:
    """dolmi.log: %LOCALAPPDATA%\\Dolmi."""
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Dolmi"

def documents_folder() -> Path:
    buf = ctypes.create_unicode_buffer(260)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:  # 5 = CSIDL_PERSONAL
        return Path(buf.value)
    return Path.home() / "Documents"

def needs_log_file():
    """Under pythonw there's no console: print() must go to the log file instead."""
    return sys.stdout is None or sys.stderr is None


# ------------------------------------------------------------------ one Dolmi at a time (APP-5)
ERROR_ALREADY_EXISTS = 183
ASFW_ANY = -1                  # AllowSetForegroundWindow: whichever process comes next may take the focus
SW_RESTORE = 9
INFINITE = 0xFFFFFFFF


def _kernel32():
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    k.CreateMutexW.restype = wintypes.HANDLE
    k.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    k.CreateEventW.restype = wintypes.HANDLE
    k.SetEvent.argtypes = [wintypes.HANDLE]
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.WaitForSingleObject.restype = wintypes.DWORD
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    return k


class _Instance:
    """Held by the running Dolmi: the named mutex (so a second launch can tell) and the event a second
    launch sets to ask for the window."""

    def __init__(self, k32, mutex, event):
        self._k32, self._mutex, self._event = k32, mutex, event
        self._closed = False

    def listen(self, on_activate):
        """Call on_activate() (on a background thread) each time another launch asks for the window."""
        def run():
            while not self._closed and self._k32.WaitForSingleObject(self._event, INFINITE) == 0:
                if self._closed:
                    return
                try:
                    on_activate()
                except Exception as e:
                    print(f"Dolmi: could not bring the window to the front ({e})")
        threading.Thread(target=run, name="dolmi-single-instance", daemon=True).start()
        return self

    def close(self):
        self._closed = True
        self._k32.SetEvent(self._event)          # wake the listener so it ends
        self._k32.CloseHandle(self._event)
        self._k32.CloseHandle(self._mutex)


def single_instance(name="Dolmi"):
    """The first Dolmi gets a guard to keep (and .listen(on_activate) on); a second launch gets None after
    asking the first one to show its window, and should exit. Per Windows user session ("Local\\")."""
    k32 = _kernel32()
    mutex = k32.CreateMutexW(None, False, "Local\\" + name + ".SingleInstance")
    already = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
    event = k32.CreateEventW(None, False, False, "Local\\" + name + ".Activate")   # auto-reset
    if not mutex or not event:                   # cannot tell: never block Dolmi from starting
        print(f"Dolmi: single-instance check unavailable (error {ctypes.get_last_error()})")
        return _Instance(k32, mutex or event, event or mutex)
    if already:
        try:
            ctypes.windll.user32.AllowSetForegroundWindow(ASFW_ANY)   # the running one may take the focus
        except Exception:
            pass
        k32.SetEvent(event)
        k32.CloseHandle(event)
        k32.CloseHandle(mutex)
        return None
    return _Instance(k32, mutex, event)


def bring_to_front(win):
    """Restore the main window if minimised and give it the keyboard focus."""
    hwnd = _native_hwnd(win, "Dolmi")
    if not hwnd:
        return False
    u = _user32()
    if u.IsIconic(hwnd):
        u.ShowWindow(hwnd, SW_RESTORE)
    return bool(u.SetForegroundWindow(hwnd))


# ------------------------------------------------------------------ global hotkey (always-works escape hatch)
WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT = 0x0001, 0x0002, 0x0004
MOD_NOREPEAT = 0x4000
VK_D = 0x44


class GlobalHotkey(threading.Thread):
    """A system-wide hotkey, so Dolmi can always be turned visible again even when it is hidden from
    the taskbar and Alt+Tab and buried behind other windows. `on_press` runs on this thread, so it
    should only hand work back to the UI thread."""

    def __init__(self, mods, vk, on_press, name="hotkey"):
        super().__init__(daemon=True)
        self.mods, self.vk, self.on_press, self.hotkey_name = mods, vk, on_press, name
        self.registered = threading.Event()
        self.ok = False

    def run(self):
        from ctypes import wintypes
        _u = _user32()
        if not _u.RegisterHotKey(None, 1, self.mods | MOD_NOREPEAT, self.vk):
            print(f"Dolmi: could not register the {self.hotkey_name} global hotkey (another app may use it)")
            self.registered.set()
            return
        self.ok = True
        self.registered.set()
        msg = wintypes.MSG()
        while _u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                try:
                    self.on_press()
                except Exception as e:
                    print(f"Dolmi hotkey handler error: {e}")


def start_recover_hotkey(on_press):
    """Ctrl+Alt+Shift+D: a system-wide hotkey that always brings Dolmi back, even hidden from the taskbar."""
    return GlobalHotkey(MOD_CONTROL | MOD_ALT | MOD_SHIFT, VK_D, on_press,
                        name=f"show Dolmi ({WORDS['hotkey']})").start()


# ------------------------------------------------------------------ global shortcuts (APP-7)
MOD_WIN = 0x0008
WM_QUIT = 0x0012
_SHORTCUT_MODS = {"ctrl": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "meta": MOD_WIN}


def _shortcut_vk(key):
    """Virtual-key code for a shortcut key: a-z, 0-9, f1-f24; None for anything else."""
    if len(key) == 1 and (key.isascii() and (key.isalpha() or key.isdigit())):
        return ord(key.upper())
    if key[:1] == "f" and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        return 0x6F + int(key[1:])                          # VK_F1 = 0x70
    return None


class Shortcuts(threading.Thread):
    """Dolmi's global shortcuts (start/stop, bar, source, lock): RegisterHotKey on one thread, so they work
    while Teams or Zoom has the focus. `status` says which ones Windows accepted (another app may own
    one). stop() unregisters them all; `on_press(name)` runs on its own short thread."""

    def __init__(self, bindings, on_press):
        super().__init__(daemon=True, name="dolmi-shortcuts")
        self.bindings, self.on_press = dict(bindings), on_press
        self.status = {}
        self.ready = threading.Event()
        self._tid = None

    def run(self):
        from ctypes import wintypes
        u = _user32()
        self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
        ids = {}
        for i, (name, (mods, key)) in enumerate(self.bindings.items(), start=100):
            vk = _shortcut_vk(key)
            bits = 0
            for m in mods:
                bits |= _SHORTCUT_MODS.get(m, 0)
            ok = bool(vk and bits and u.RegisterHotKey(None, i, bits | MOD_NOREPEAT, vk))
            self.status[name] = ok
            if ok:
                ids[i] = name
            else:
                print(f"Dolmi: could not register the shortcut for {name} (another app may use it)")
        self.ready.set()
        msg = wintypes.MSG()
        try:
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                name = ids.get(msg.wParam) if msg.message == WM_HOTKEY else None
                if name:
                    threading.Thread(target=self._fire, args=(name,), daemon=True).start()
        finally:
            for i in ids:
                u.UnregisterHotKey(None, i)

    def _fire(self, name):
        try:
            self.on_press(name)
        except Exception as e:
            print(f"Dolmi shortcut {name} failed: {e}")

    def stop(self):
        if self._tid:
            ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        self.join(2)


def start_shortcuts(bindings, on_press):
    """Register {name: (modifiers, key)} system-wide; returns the handle (.status {name: bool}, .stop())."""
    s = Shortcuts(bindings, on_press)
    s.start()
    s.ready.wait(2)
    return s
