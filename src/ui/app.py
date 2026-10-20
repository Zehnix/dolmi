"""Dolmi webview UI — the JS bridge (Api exposed as window.pywebview.api).

Reuses the existing core (live_subs, assistant, inbox, config) untouched. Python pushes to the page by
calling window.dolmi.<fn>(...) through evaluate_js; the page calls Python through
window.pywebview.api.<method>.

All state is held in underscore attributes on purpose: pywebview exposes every *public* attribute of
the js_api object to page JavaScript (recursively), which would hand the page the window object, the
Whisper model, the settings with the encrypted keys, and the database handle.
"""
import gc
import itertools
import json
import numbers
import os
import queue
import re
import threading
import time
import unicodedata
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import config
import assistant
import inbox
import live_subs
import streaming
import clarify
import cloud_speech
import cloud_translate
import netgate
import dolmi_platform
import updates

VERSION = config.version()
DEFAULT_SPEAKER = dolmi_platform.DEFAULT_SPEAKER   # what "Speaker" listens to when no device is picked
ICON = dolmi_platform.app_icon(config.ROOT)
# a saved line: "**HH:MM:SS–HH:MM:SS** {en}  \n<sub>{de}</sub>" (older files: "**HH:MM:SS** ...")
_MEETING_RE = re.compile(r"\*\*(.+?)\*\*\s+(.*?)\s*\n<sub>(.*?)</sub>", re.S)
_STARTED_RE = re.compile(r"<!-- started (\d\d:\d\d:\d\d) -->")
# APP-9 meeting titles and search
TITLE_MAX = 70                    # characters of an automatic title
TITLE_EDIT_MAX = 120              # characters of a title the user types
SEARCH_HITS = 3                   # matching lines shown per meeting
_DATE_RE = re.compile(r"\d{4}-\d\d-\d\d")
_FILLER = {"ok", "okay", "yes", "no", "yeah", "thanks", "thank you", "hello", "hi", "hello everyone", "good morning",
           "hallo", "ja", "nein", "danke", "genau", "gut", "hallo zusammen", "guten morgen", "can you hear me",
           "können sie mich hören", "kannst du mich hören", "one moment", "einen moment"}
_SUMMARY_HEADINGS = {"summary", "meeting summary", "meeting notes", "notes", "zusammenfassung", "decisions",
                     "action items", "open questions", "overview", "tl;dr", "tldr"}
LAG_WINDOW = 5                                     # the Lag figure is the median of this many finals
# settings the page may change through set_setting (provider, keys and invisible have their own calls)
SPEECH_SOURCES = ("local", *cloud_speech.PROVIDERS)   # Settings → Captions by
SETTABLE = {"speech_source", "mic_device", "language", "show_german", "save_transcripts", "keep_days", "audio_device", "model",
            "overlay", "theme", "ai_length", "ai_context", "opacity", "font",
            "warn_silence",                                    # APP-3
            "preload_models",                                  # APP-4
            "check_updates",                                   # APP-6
            "shortcuts_on",                                    # APP-7
            "speaker_labels",                                  # SPK-4
            "clarify_queue",                                   # VOC-9
            "bar_time",                                        # LIVE-5
            "capture_both",                                    # SPK-5
            "voice_readout",                                   # VOICE-1
            "voice_natural",                                   # VOICE-8
            "voice_mix", "voice_safety", "voice_split", "voice_out_device", "voice_names",   # VOICE-7
            "ui_text_size",                                    # APP-14
            "local_only",                                      # STR-7
            "unsure_marks",                                    # STR-8
            "cloud_failover"}                                  # STR-9
UI_TEXT_SIZES = ("normal", "large")                      # APP-14: Appearance > Larger UI text
BOOL_SETTINGS = {"warn_silence", "preload_models", "check_updates", "shortcuts_on",
                 "speaker_labels", "clarify_queue", "bar_time",
                 "capture_both", "voice_readout", "local_only", "voice_natural", "voice_mix", "voice_split",
                 "unsure_marks", "cloud_failover"}        # on/off switches that must be true or false
SPEAKER_NAME_MAX = 40                                    # SPK-4: longest speaker name
VOICE_NAMES_MAX = 120                                    # VOICE-7: 'chime when I am named' text
VOICE_REBUILD = {"voice_natural", "voice_mix", "voice_out_device"}   # VOICE-7/8: these restart the voice
HOLD_POLL_S, HOLD_MAX_S = 0.05, 30.0                     # VOICE-7: hold-to-hear-original key polling
# APP-3 'nothing heard' warning: how quiet counts as silence, and how long before Dolmi says so
HEARD_SILENCE_RMS = live_subs.SILENCE_RMS
HEARD_AFTER_S = 20.0
HEARD_TICK_S = 1.0


class _VoiceTapQueue(streaming.SampleQueue):
    """VOICE-7: the capture queue, also handing each chunk to the headphone mix's safety net."""

    def __init__(self, tap, **kw):
        super().__init__(**kw)
        self._tap = tap

    def _put(self, item):
        super()._put(item)
        try:
            self._tap(item)
        except Exception as e:                  # the mix must never stop the captions
            print(f"voice: safety-net feed failed: {e}")


_VK_F1 = 0x70


def _key_down(key):
    """Windows: is this shortcut key (letter, digit or F-key) held right now?"""
    import ctypes
    k = key.lower()
    if len(k) == 1 and k.isalnum():
        vk = ord(k.upper())
    elif k.startswith("f") and k[1:].isdigit():
        vk = _VK_F1 + int(k[1:]) - 1
    else:
        return False
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
HEARD_SOUND_NOW_S = 3.0          # sound in the last 3 s counts as "sound present" (speech has short gaps)
# APP-4 Instant Start: settings that change which models the next Start needs
ENGINE_SETTINGS = ("model", "language", "speech_source")
WARM_DEBOUNCE_S = 0.5            # several setting changes in a row load the models once


# APP-7 global shortcuts: what each does, and its default (Ctrl+Alt+Shift+D stays "show Dolmi")
DEFAULT_SHORTCUTS = {"toggle_live": "ctrl+alt+shift+s", "toggle_bar": "ctrl+alt+shift+b",
                     "toggle_source": "ctrl+alt+shift+m", "toggle_lock": "ctrl+alt+shift+l",
                     "hold_original": "ctrl+alt+shift+o"}          # VOICE-7: hear the original while held
RESERVED_SHORTCUTS = {"ctrl+alt+shift+d": "showing Dolmi"}
_SHORTCUT_MOD_ORDER = ("ctrl", "alt", "shift", "meta")
_SHORTCUT_MOD_NAMES = {"ctrl": "ctrl", "control": "ctrl", "alt": "alt", "option": "alt", "opt": "alt",
                       "shift": "shift", "meta": "meta", "cmd": "meta", "command": "meta", "win": "meta", "super": "meta"}


def normalize_shortcut(text):
    """'Ctrl+Shift+Alt+S' -> 'ctrl+alt+shift+s'; None unless it is an F1-F24 key with two or more modifiers
    (not only Shift), or a letter or digit with three modifiers or Cmd/Win+Alt. A global hotkey takes the
    key from every app: Ctrl+Alt is AltGr on German keyboards (Ctrl+Alt+Q = @, +7 = {, +E = EUR) and
    Ctrl+Shift+S/T/N are everyday keys ('Save as', reopen tab)."""
    if not isinstance(text, str) or len(text) > 40:
        return None
    parts = [x.strip().lower() for x in text.split("+")]
    if len(parts) < 3 or not all(parts):
        return None
    *mods, key = parts
    mods = {_SHORTCUT_MOD_NAMES.get(m) for m in mods}
    if None in mods or len(mods) != len(parts) - 1 or mods == {"shift"} or len(mods) < 2:
        return None
    ok_key = (len(key) == 1 and key.isascii() and key.isalnum()) or (
        key[:1] == "f" and key[1:].isdigit() and 1 <= int(key[1:]) <= 24 and key[1:2] != "0")
    if not ok_key:
        return None
    if len(key) == 1 and len(mods) < 3 and mods != {"alt", "meta"}:
        return None
    return "+".join([m for m in _SHORTCUT_MOD_ORDER if m in mods] + [key])


def _shortcut_parts(combo):
    *mods, key = combo.split("+")
    return tuple(mods), key


BAR_SAVE_DELAY_S = 0.6           # APP-8: save the bar's place once it stops moving


def _screen_key(s):
    """A screen's identity for the bar position: its geometry, e.g. '0,0,1920x1080' (APP-8)."""
    return f"{int(s.x)},{int(s.y)},{int(s.width)}x{int(s.height)}"


def _screen_at(x, y, screens):
    """The screen containing the point, else the nearest one, else None."""
    best, dist = None, None
    for s in screens or []:
        dx = max(s.x - x, 0, x - (s.x + s.width))
        dy = max(s.y - y, 0, y - (s.y + s.height))
        d = dx * dx + dy * dy
        if dist is None or d < dist:
            best, dist = s, d
    return best


def _valid_place(p):
    return (isinstance(p, dict) and all(isinstance(p.get(k), numbers.Real) and not isinstance(p.get(k), bool)
                                        for k in ("x", "y", "w", "h")) and p["w"] > 0 and p["h"] > 0)


def _bar_place(positions, last, screens, near=None):
    """Where to open the subtitle bar: its saved place on the screen it was last on, else on the screen
    `near` (the main window's centre) is on, else on any connected screen it was saved for; None = default.
    Only screens connected now count (keys are geometries), and the place is pulled inside that screen."""
    positions = positions if isinstance(positions, dict) else {}
    by_key = {_screen_key(s): s for s in screens or []}
    order = [last]
    if near:
        s = _screen_at(near[0], near[1], screens)
        if s:
            order.append(_screen_key(s))
    order += list(positions)
    for key in order:
        p, s = positions.get(key), by_key.get(key)
        if s is None or not _valid_place(p):
            continue
        w, h = min(int(p["w"]), s.width), min(int(p["h"]), s.height)
        x = min(max(int(p["x"]), s.x), s.x + s.width - w)
        y = min(max(int(p["y"]), s.y), s.y + s.height - h)
        return {"x": x, "y": y, "w": w, "h": h, "screen": key}
    return None


def _js(v):
    return json.dumps(v)


def _clock(epoch):
    return datetime.fromtimestamp(epoch).strftime("%H:%M:%S")


def _offset(seconds):
    """Seconds into the meeting as '+mm:ss' (or '+h:mm:ss' after an hour)."""
    s = max(0, int(seconds))
    h, r = divmod(s, 3600)
    return f"+{h}:{r // 60:02}:{r % 60:02}" if h else f"+{r // 60:02}:{r % 60:02}"


def _seconds(hhmmss):
    try:
        h, m, s = (int(x) for x in hhmmss.split(":"))
    except ValueError:
        return None
    return h * 3600 + m * 60 + s


def _plain(meta):
    """A JSON-safe copy of a caption's meta (producers may hand over numpy values)."""
    out = {}
    for k, v in meta.items():
        if type(v).__module__ == "numpy" and hasattr(v, "tolist"):   # np.bool_, np.float32, arrays
            v = v.tolist()
        elif isinstance(v, numbers.Integral) and not isinstance(v, bool):
            v = int(v)
        elif isinstance(v, numbers.Real) and not isinstance(v, bool):
            v = float(v)
        out[str(k)] = v
    return out


def _fold(text):
    """Case- and accent-insensitive form for search: 'Größe' and 'GROSSE' both -> 'grosse', 'é' -> 'e'."""
    s = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).casefold()


def _shorten(text, limit=TITLE_MAX):
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",.;:-–")
    return (cut or text[:limit]) + "…"


def _title_from_lines(texts):
    """The first meaningful line (four words or more, not a greeting or 'okay'), shortened."""
    first = ""
    for text in texts:
        text = " ".join(str(text or "").split())
        if not text:
            continue
        first = first or text
        bare = _fold(text).strip(" .!?,…")
        if len(text.split()) >= 4 and bare not in _FILLER:
            return _shorten(text)
    return _shorten(first)


def _title_from_summary(text):
    """A summary's title: its first line that is not a generic heading, without markdown, first sentence."""
    for line in str(text or "").splitlines():
        s = re.sub(r"^[#>*\-\s]+|[*_`]+", "", line).strip()
        s = re.sub(r"^(title|titel|topic|thema)\s*:\s*", "", s, flags=re.I).strip()
        if not s or _fold(s).rstrip(":") in _SUMMARY_HEADINGS:
            continue
        return _shorten(re.split(r"(?<=[.!?])\s", s, 1)[0])
    return ""


# APP-12 "Report a problem": what never leaves the computer in a report
_SECRET_NAME = re.compile(r"key|token|secret|password|passwd|pro$|cf_account|account|auth|credential|salt|hash", re.I)
# a value that looks like a key: provider prefixes, encrypted keys, or a long run of letters AND digits
_SECRET_VALUE = re.compile(r"\b(?:sk-|sk_|nvapi-|AIza|xox[abp]-|ghp_|gho_|github_pat_|gAAAA|dpapi:)[\w\-:.=]*"
                           r"|(?<![\w+-])(?=[\w\-+]*\d)(?=[\w\-+]*[A-Za-z])[\w\-+]{32,}=*")
REDACTED = "[removed]"
REPORT_LOG_MAX = 2_000_000      # the last ~2 MB of the log are enough to see what went wrong


def redact_text(text):
    """Text with every key-like value replaced by [removed]."""
    return _SECRET_VALUE.sub(REDACTED, str(text))


def scrub_settings(value, name=""):
    """A copy of the settings with every key/token removed: values under a secret-sounding name are
    replaced whole (dicts too, e.g. api_keys), and any other string that looks like a key is redacted."""
    if name and _SECRET_NAME.search(name):
        return REDACTED if value not in ("", None, {}, []) else value
    if isinstance(value, dict):
        return {k: scrub_settings(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub_settings(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def _spoken_language_ok(code):
    import models
    return code == "auto" or code in models.LANGUAGES


def _replace_text(path, text, tries=10, wait=0.05):
    """Write text to path through a temp file, so a reader never sees half a file. On Windows os.replace
    fails while anyone else has the file open (the engine reloading it, an indexer, antivirus), so it is
    retried briefly; on a final failure the temp file is removed and the error is raised."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(tries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == tries - 1:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(wait)


VOCAB_RECENT = 6   # latest edited terms shown in the Vocabulary summary


def _vocab_items(vocabulary, glossary):
    """Every spelling hint and rule as (group, term, target) in file order; group: gloss/keep/german/english."""
    return ([("gloss", t, "") for t in live_subs.parse_glossary(glossary)[0]] +
            [(r.section, r.src, r.dst) for r in live_subs.parse_vocabulary(vocabulary)[0]])


class Api:
    # APP-3 watcher state (class defaults; underscore names are never exposed to the page).
    # _clock is the one clock for the watcher and for _last_caption, which the caption pump sets on
    # every non-empty final/draft (time.monotonic seconds); tests replace it with a fake clock.
    _clock = staticmethod(time.monotonic)
    _sleep = staticmethod(time.sleep)
    _level_peak = 0.0                    # loudest input level since the watcher's last tick
    _last_caption = 0.0                  # _clock() time of the last non-empty final/draft
    _heard_state = None                  # None | "silent" | "no_captions": what the page shows
    _heard_key = None                    # (session token, source label) the counts belong to
    _heard_start = 0.0
    _last_sound = 0.0
    _heard_tick_at = None                # clock time of the last tick (one tick per clock step)
    # APP-4 Instant Start: the models are loaded in the background and kept; the page shows 'Ready' only
    # when _engine_state is "ready" (idle | loading | ready | missing)
    _engine_state = "idle"
    _settings_gen = 0                    # bumped by every change to ENGINE_SETTINGS (a load in flight is stale)
    _shortcuts = None                    # APP-7: the platform's registered shortcuts (None = not started)
    _shortcut_status = {}                # action -> registered (False: another app has that key)
    _shortcuts_paused = False            # APP-7: let go while the page records a new key
    _update = None                       # APP-6: the last update check's answer (updates.check), None before

    def __init__(self):
        config.prepare()
        config.load_env()
        self._settings = config.load_settings()
        if not _spoken_language_ok(self._settings["language"]):
            self._settings["language"] = "de"
        self._settings_lock = threading.Lock()
        netgate.set_local_only(self._settings.get("local_only", False))   # STR-7
        self._data = config.data_folder(self._settings)
        self._data.mkdir(parents=True, exist_ok=True)
        self._db_path = self._data / "dolmi.db"
        self._purge_old()

        self._window = None                  # set by main
        self._make_window = None             # set by main: builds a fresh main window (Mac invisible-off)
        self._overlay = None
        self._overlay_ready = False          # page loaded -> safe to evaluate_js without blocking
        self._overlay_visible = False
        self._engine = None
        self._engine_lock = threading.Lock() # one engine load at a time
        self._gen_lock = threading.Lock()    # _settings_gen check + _engine store vs. _drop_engine (short)
        self._session = None                 # {"token", "stop", "transcript"} while starting/listening
        self._lock = threading.Lock()        # guards session transitions
        self._history = []                   # [(hh:mm:ss speech start, de, en, meta)] this session
        self._lags = deque(maxlen=LAG_WINDOW)  # end of speech -> line shown, last few finals (seconds)
        self._lag_session = None             # the session self._lags belongs to
        self._hist_lock = threading.Lock()
        self._last_transcript = None
        self._ai_history = []
        self._chat_id = None                 # inbox chat the next answer is saved into
        self._asks = {}
        self._ids = itertools.count()
        self._level = 0.0
        self._pc = None                      # cached PC specs (pc_info)
        self._model_lists = {}               # (provider, key) -> model IDs
        self._dl = set()                     # model keys downloading right now
        self._ui_q = queue.Queue()
        self._guard = streaming.CaptionGuard()   # STR-3: stale drafts and old sessions never reach the page
        self._tracer = None                  # STR-4: (session token, TraceWriter) while DOLMI_TRACE=1
        self._ask_q = queue.Queue()
        self._ov_q = queue.Queue()
        self._vocab_lock = threading.Lock()  # a save and the watcher's look at the files never interleave
        self._cloud_tr = None                # QA-7: CloudTranslator for the chosen provider and key (lazy)
        os.environ["DOLMI_VOCAB_PROFILE"] = self._settings.get("vocab_profile", "")   # VOC-11: read by reload_terms
        self._warm_req = threading.Event()   # set: load the next Start's models in the background (APP-4)
        for loop in (self._pump, self._answer_loop, self._level_loop, self._overlay_loop, self._vocab_watch_loop):
            threading.Thread(target=loop, daemon=True).start()

    # ------------------------------------------------------------------ plumbing
    def _emit(self, fn, *args):
        w = self._window
        if not w:
            return
        try:
            w.evaluate_js(f"window.dolmi && window.dolmi.{fn}(" + ",".join(_js(a) for a in args) + ")")
        except Exception as e:
            print(f"emit {fn} failed: {e}")

    def _ov(self, fn, *args):
        """Queue a caption for the overlay, only once it's loaded and shown (evaluate_js on an
        unloaded or hidden window blocks for up to 15 s and would stall the caption pump)."""
        if self._overlay and self._overlay_ready and self._overlay_visible:
            self._ov_q.put((fn, args))

    def _overlay_loop(self):
        while True:
            fn, args = self._ov_q.get()
            ov = self._overlay
            if ov and self._overlay_visible:
                ov.emit(fn, *args)

    def _mark_caption(self):
        """Note when a caption last arrived; the 'nothing heard' watcher (APP-3) reads _last_caption.
        Uses the watcher's monotonic clock (self._clock, which tests can fake) when it exists."""
        self._last_caption = getattr(self, "_clock", time.monotonic)()

    def _pump(self):
        """Drain ui_q (fed by the worker and answer threads) and push to the page.
        Items are (kind, a, b) or (kind, a, b, meta); captions get their timing meta completed here."""
        while True:
            kind, a, b, *rest = self._ui_q.get()
            meta = rest[0] if rest and isinstance(rest[0], dict) else {}
            try:
                if kind in ("final", "draft", "overload") and not self._session:
                    continue                          # late result after Stop
                if kind in ("final", "draft", "overload") and not self._guard.accept(kind, a, b, meta, self._session_id()):
                    continue                          # STR-3: older session, or a draft overtaken by newer work
                if kind == "overload":
                    self._emit("onError", f"Captions are {a} s behind the speaker. Live drafts pause until "
                                          "Dolmi catches up; no speech is lost.")
                elif kind == "error":
                    self._emit("onError", a)
                elif kind == "final":
                    meta = self._caption_meta(meta, final=True)
                    self._write_trace(meta, a, b)
                    ts = _clock(meta["start"])
                    with self._hist_lock:
                        self._history.append((ts, a, b, meta))
                    if a or b:
                        self._mark_caption()
                    self._emit("onLine", ts[:5], a, b, meta)
                    self._ov("line", a, b, ts[:5] if self._settings.get("bar_time") else "")
                    self._cloud_submit(a, b, meta)
                    self._voice_offer(b, meta)
                    self._voice_name_check(a)
                elif kind == "cloud_status":                    # STR-9: "local" while the cloud is down
                    self._emit("onCloudStatus", a)
                elif kind == "final_update":
                    self._apply_line_update(a, b, meta)
                elif kind == "draft":
                    meta = self._caption_meta(meta, final=False)
                    if a or b:
                        self._mark_caption()
                    self._emit("onDraft", a, b, meta)
                    self._ov("draft", a, b, {k: meta.get(k) for k in
                                             ("committed", "tentative", "en_committed", "en_tentative")})
                elif kind == "ask_start":
                    self._emit("onAsk", a)
                elif kind == "ask_chunk":
                    self._emit("onAnswerChunk", a, b)
                elif kind == "ask_done":
                    self._emit("onAnswerDone", a, b)
                elif kind == "sum_chunk":
                    self._emit("onSummaryChunk", a)
                elif kind == "sum_done":
                    self._emit("onSummaryDone", a, b or None)
            except Exception as e:
                print(f"pump {kind} failed: {e}")

    def _session_id(self):
        """The id every caption of the running session carries in meta["session"] (None when stopped)."""
        s = self._session
        return id(s["token"]) if s else None

    def _write_trace(self, meta, de, en):
        """STR-4: DOLMI_TRACE=1 appends the final's latency trace to this session's jsonl."""
        if not streaming.TraceWriter.enabled() or not isinstance(meta.get("trace"), dict):
            return
        s = self._session
        if not self._tracer or self._tracer[0] is not (s and s["token"]):
            t0 = meta["trace"].get("speech_start", meta.get("start"))
            self._tracer = (s and s["token"], streaming.TraceWriter(t0))
        self._tracer[1].write(meta, de, en)

    def _caption_meta(self, meta, final):
        """Complete a caption's timing meta: 'shown' (now), 'lag' (shown - end of speech, only when the
        producer measured the end), 'offset' (seconds into the meeting) and, for finals, 'lag_median'
        over the last LAG_WINDOW finals of this session (None until one was measured). A final
        without timing (old producers) is placed at now."""
        meta = _plain(meta)
        session = self._session
        if session is not self._lag_session:          # new meeting: forget the old lags
            self._lag_session, self._lags = session, deque(maxlen=LAG_WINDOW)
        now = time.time()
        measured = isinstance(meta.get("end"), (int, float))
        if final:                                     # a missing or non-numeric time counts as unknown
            if not measured:
                meta["end"] = now
            if not isinstance(meta.get("start"), (int, float)):
                meta["start"] = meta["end"]
        meta["shown"] = now
        meta["lag"] = max(0.0, now - meta["end"]) if measured else None
        tr = session.get("transcript") if session else None
        t0 = getattr(tr, "t0", None)
        if t0 and isinstance(meta.get("start"), (int, float)):
            meta["offset"] = max(0.0, meta["start"] - t0)
        if isinstance(meta.get("trace"), dict):       # STR-4: one trace; the lag display reads its split
            meta["trace"] = {**_plain(meta["trace"]), "shown": now}
            meta["lag_policy"], meta["lag_compute"] = streaming.split_lag(meta["trace"])
        if final:
            if meta["lag"] is not None:
                self._lags.append(meta["lag"])
            meta["lag_median"] = self._lag_median()
        return meta

    def _lag_median(self):
        """Median lag of the last few finals in seconds, or None before one was measured."""
        lags = sorted(self._lags)
        if not lags:
            return None
        mid = len(lags) // 2
        return lags[mid] if len(lags) % 2 else (lags[mid - 1] + lags[mid]) / 2

    def _level_loop(self):
        threading.Thread(target=self._heard_watch_loop, daemon=True).start()
        while True:
            time.sleep(0.1)
            s = self._session
            if s and s.get("stop"):
                self._emit("onLevel", round(float(self._level), 4))

    def _purge_old(self):
        """'Delete after N days': transcript/summary/saved files AND inbox chats (0 = keep all)."""
        days = self._settings["keep_days"]
        try:
            live_subs.delete_old_files(self._data, days)
        except Exception as e:
            print(f"file retention failed: {e}")
        try:   # short-lived connection: sqlite handles are per-thread
            db = inbox.connect(self._db_path)
            inbox.delete_older_than(db, days)
            db.close()
        except Exception as e:
            print(f"inbox retention failed: {e}")

    # ------------------------------------------------------------------ state / settings
    def _english(self, heard, subtitle):
        """The English side of a caption: the subtitle for German speech, what was heard for English."""
        return heard if self._settings["language"] == "en" else subtitle

    def _provider(self):
        p = self._settings["ai_provider"]
        return p if p in assistant.PROVIDERS else next(iter(assistant.PROVIDERS))

    def _ai_model(self):
        return self._settings["ai_models"].get(self._provider()) or assistant.PROVIDERS[self._provider()]["model"]

    def _ai_key(self):
        return self._key_for(self._provider())

    def _key_for(self, provider):
        """A provider's saved key; for Cloudflare "<account id>:<token>" (empty until both are saved)."""
        key = assistant.decrypt_key(self._settings["api_keys"].get(provider, ""))
        if key and provider == "cloudflare":
            account = self._settings.get("cf_account", "")
            return f"{account}:{key}" if account else ""
        return key

    def _engine_label(self):
        e = self._engine
        if e and getattr(e, "cloud", False):
            return f"CLOUD · {e.model_name}"
        return f"{e.device.upper()} · {e.model_name}" if e else "not loaded"

    def state(self):
        s = self._settings
        return {
            "speechSource": s.get("speech_source", "local"), "language": s["language"], "showGerman": s["show_german"], "invisible": s["invisible"],
            "saveTranscripts": s["save_transcripts"], "keepDays": s["keep_days"],
            "provider": self._provider(), "providers": {k: v["label"] for k, v in assistant.PROVIDERS.items()},
            "model": self._ai_model(), "hasKey": bool(self._ai_key()),
            "modelDefault": assistant.PROVIDERS[self._provider()]["model"],
            "keyHint": assistant.PROVIDERS[self._provider()]["key_hint"],
            "cfAccount": s.get("cf_account", ""),
            "audioDevice": s["audio_device"], "audioSource": self._source(), "micDevice": s.get("mic_device", ""),
            "sourceLabel": self._source_label(), "speechModel": s["model"], "overlay": bool(s.get("overlay")),
            "theme": s.get("theme", "paper"), "listening": bool(self._session),
            "opacity": s.get("opacity", 0.88), "font": s.get("font", 22),
            "warnSilence": bool(s.get("warn_silence", True)),
            "speakerLabels": bool(s.get("speaker_labels", False)),
            "captureBoth": bool(s.get("capture_both", False)),
            "voiceReadout": bool(s.get("voice_readout", False)),
            "voiceNatural": bool(s.get("voice_natural", False)), "voiceMix": bool(s.get("voice_mix", False)),
            "voiceSafety": s.get("voice_safety", 0.10), "voiceSplit": bool(s.get("voice_split", False)),
            "voiceOutDevice": s.get("voice_out_device", ""), "voiceNames": s.get("voice_names", ""),
            "barVisible": bool(self._overlay_visible), "barLocked": bool(s.get("bar_locked")),
            "barTime": bool(s.get("bar_time", False)),
            "uiTextSize": s.get("ui_text_size") if s.get("ui_text_size") in UI_TEXT_SIZES else "normal",
            "shortcutsOn": bool(s.get("shortcuts_on", True)), "shortcuts": self._shortcut_map(),
            "shortcutStatus": dict(self._shortcut_status),
            "engine": self._engine_label(), "engineState": self._engine_state,
            "preloadModels": bool(s.get("preload_models", True)),
            "localOnly": netgate.is_local_only(), "unsureMarks": bool(s.get("unsure_marks")),
            "cloudFailover": bool(s.get("cloud_failover")),
            "checkUpdates": bool(s.get("check_updates", True)), "update": self._update, "dataFolder": str(self._data), "version": VERSION,
            "platform": {"os": dolmi_platform.OS, "words": dolmi_platform.WORDS},
        }

    def set_setting(self, key, value):
        if key not in SETTABLE:
            return False
        if key == "language" and not _spoken_language_ok(value):
            return False
        if key == "speech_source" and value not in SPEECH_SOURCES:
            return False
        if key in BOOL_SETTINGS and not isinstance(value, bool):
            return False
        if key == "ui_text_size" and value not in UI_TEXT_SIZES:
            return False
        if key == "voice_safety":                            # VOICE-7: 0-30 %
            if isinstance(value, bool) or not isinstance(value, numbers.Real):
                return False
            value = round(min(0.30, max(0.0, float(value))), 3)
        if key in ("voice_out_device", "voice_names"):
            if not isinstance(value, str):
                return False
            value = value.strip()[:VOICE_NAMES_MAX if key == "voice_names" else 200]
        with self._settings_lock:
            self._settings[key] = value
            config.save_settings(self._settings)
        if key == "unsure_marks" and self._engine is not None:
            self._engine.unsure_marks = bool(value)
        if key == "local_only":
            netgate.set_local_only(value)
        if key == "keep_days":
            threading.Thread(target=self._purge_old, daemon=True).start()
        if key == "mic_device" and self._session and self._source() == "mic" and getattr(self, "_cap_stop", None):
            self._cap_stop.set()
        if key in ("opacity", "font"):
            self._push_overlay_style()
        if key in ENGINE_SETTINGS and not self._session:
            self._drop_engine()              # next Start loads the newly chosen model/translator
            self._request_warm()
        if (key == "voice_readout" and not value) or key in VOICE_REBUILD:
            self._voice_stop()                   # the next line builds the voice with the new choice
        if key in ("voice_safety", "voice_split", "voice_names"):
            m = getattr(self, "_voice_mix", None)
            if m is not None:
                m.configure(safety=self._settings.get("voice_safety"), split=self._settings.get("voice_split"),
                            names=self._settings.get("voice_names"))
        if key == "shortcuts_on" and self._shortcuts is not None:
            self._start_shortcuts()
        if key == "preload_models":
            if value:
                self._request_warm()
            elif not self._session:
                self._drop_engine()          # off: give the memory back now
        return True

    def pc_info(self):
        """What this computer has — detected, never assumed — and where Dolmi keeps things."""
        if self._pc is None:
            info = {"ram_gb": 0, "cores": os.cpu_count() or 1, "gpu": False, "gpu_name": "",
                    "model_dir": "", "data_dir": str(self._data)}
            try:
                dolmi_platform.prepare_gpu()           # pip-installed CUDA libs count as installed
                import models
                s = models.system_info()
                info.update(ram_gb=s["ram_gb"], cores=s["cores"], gpu=bool(s["gpu"]))
            except Exception as e:
                print(f"pc_info: {e}")
            info["gpu_name"] = dolmi_platform.gpu_name()   # graphics card (Windows) or Apple chip (Mac)
            try:
                from huggingface_hub.constants import HF_HUB_CACHE
                info["model_dir"] = str(HF_HUB_CACHE)
            except Exception:
                pass
            self._pc = info
        return self._pc

    def copy_text(self, text):
        """Put text on the system clipboard (works regardless of the webview's clipboard permissions)."""
        return dolmi_platform.copy_text(text)

    # ---- archive (a small JSON next to the data, so the shared inbox schema stays unchanged)
    def _archived(self):
        try:
            d = json.loads((self._data / "archived.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        return {"meetings": set(d.get("meetings", [])), "chats": set(d.get("chats", []))}

    def _save_archived(self, a):
        tmp = self._data / "archived.json.tmp"
        tmp.write_text(json.dumps({k: sorted(v) for k, v in a.items()}, indent=1), encoding="utf-8")
        os.replace(tmp, self._data / "archived.json")

    def archive_meeting(self, name, on=True):
        p = self._meeting_path(name)
        if not p:
            return False
        a = self._archived()
        (a["meetings"].add if on else a["meetings"].discard)(p.name)
        self._save_archived(a)
        return True

    def archive_chat(self, chat_id, on=True):
        a = self._archived()
        (a["chats"].add if on else a["chats"].discard)(int(chat_id))
        self._save_archived(a)
        return True

    def set_key(self, key):
        with self._settings_lock:
            self._settings["api_keys"][self._provider()] = assistant.encrypt_key((key or "").strip())
            config.save_settings(self._settings)
        return True

    def list_ai_models(self):
        """Models for Settings → Assistant → Model: the provider's live list (with a key; NVIDIA's is
        public), else a short built-in one. Cached per provider and key until the app restarts."""
        provider, key = self._provider(), self._ai_key()
        cache_key = (provider, key)
        live = bool(key) or provider == "nvidia"
        if live and cache_key not in self._model_lists:
            try:
                self._model_lists[cache_key] = assistant.list_models(provider, key)
            except assistant.AssistantError as e:
                print(e)
                live = False
        models = self._model_lists.get(cache_key) if live else None
        models = list(models or assistant.KNOWN_MODELS[provider])
        if self._ai_model() not in models:
            models.insert(1, self._ai_model())
        return {"models": models, "live": bool(live and cache_key in self._model_lists),
                "current": self._ai_model(), "default": assistant.PROVIDERS[provider]["model"]}

    def set_cf_account(self, account):
        """The Cloudflare account ID (32 hex characters, from the Workers AI "Use REST API" page)."""
        account = (account or "").strip().lower()
        if account and not re.fullmatch(r"[0-9a-f]{32}", account):
            return {"ok": False, "message": "An account ID is 32 letters and digits (0-9, a-f)."}
        with self._settings_lock:
            self._settings["cf_account"] = account
            config.save_settings(self._settings)
        return {"ok": True, **self.state()}

    def set_ai_model(self, model):
        """The Assistant model for the current provider; empty goes back to the provider's default."""
        model = (model or "").strip()[:120]
        with self._settings_lock:
            models = self._settings.setdefault("ai_models", {})
            if model and model != assistant.PROVIDERS[self._provider()]["model"]:
                models[self._provider()] = model
            else:
                models.pop(self._provider(), None)
            config.save_settings(self._settings)
        return self.state()

    def set_provider(self, provider):
        if provider in assistant.PROVIDERS:
            with self._settings_lock:
                self._settings["ai_provider"] = provider
                config.save_settings(self._settings)
        return self.state()

    def test_key(self):
        try:
            reply = "".join(assistant.stream_answer(self._provider(), self._ai_model(), self._ai_key(),
                                                     "Reply with just: OK"))
            return {"ok": True, "message": f"Connected — {self._ai_model()} replied: {reply.strip()[:40]}"}
        except assistant.AssistantError as e:
            return {"ok": False, "message": str(e)}
        except Exception as e:
            return {"ok": False, "message": f"Unexpected error: {e}"}

    # ------------------------------------------------------------------ cloud translator (QA-7, experimental)
    def _translator_key(self, provider):
        """The saved key for a cloud translator: Gemini shares the Assistant's Gemini key; DeepL has its own."""
        if provider == "gemini":
            return self._key_for("gemini")
        return assistant.decrypt_key(self._settings["api_keys"].get(provider, ""))

    def _translator(self):
        """The CloudTranslator for the chosen provider and its key, or None (local only / no key)."""
        provider = self._settings.get("translator", "local")
        key = self._translator_key(provider) if provider in cloud_translate.PROVIDERS else ""
        tr = self._cloud_tr
        if not key:
            return None
        timeout = self._settings.get("translator_timeout", cloud_translate.TIMEOUT)
        if tr is None or (tr.provider, tr.key, tr.timeout) != (provider, key, timeout):
            if tr is not None:
                tr.close()
            tr = self._cloud_tr = cloud_translate.CloudTranslator(provider, key, terms=self._terms,
                                                                  timeout=timeout)
        return tr

    def _terms(self):
        """The running engine's glossary and Vocabulary rules (the same the local translator uses)."""
        e = self._engine
        return getattr(e, "glossary", ""), getattr(e, "vocab", None)

    # ---- VOICE-1 prototype: read English finals aloud (off by default; does not mute the meeting audio)
    # VOICE-8: natural voice (Kokoro) first when on, then the OS voice. VOICE-7: Dolmi's headphone mix.
    def _voice_backends(self):
        from voice import tts
        return tts.natural_backends() if self._settings.get("voice_natural") else tts.os_backends()

    @staticmethod
    def _voice_ok(b):
        try:
            return b.available()
        except Exception as e:
            print(f"voice backend {getattr(b, 'name', b)} check failed: {e}")
            return False

    def _voice_reader(self):
        """The running reader, started on first use; None when no voice backend works here."""
        r = getattr(self, "_voice", None)
        if r is None:
            try:
                from voice import reader, tts
                chain = self._voice_backends()
                backend = tts.select_backend(chain)
            except ImportError as e:            # a build without the voice/ folder
                print(f"voice: not in this build ({e})")
                return None
            if backend is None:
                return None
            rest = chain[chain.index(backend) + 1:] if backend in chain else []
            pcm_only = getattr(backend, "pcm", False)
            mix = self._voice_mixer() if (self._settings.get("voice_mix") or pcm_only) else None
            if mix is None and pcm_only:        # Kokoro has no output of its own: use the OS voice
                backend = next((b for b in rest if self._voice_ok(b)), None)
                rest = []
                if backend is None:
                    return None
            r = self._voice = reader.Reader(backend, fallbacks=rest, mixer=mix).start()
        return r

    def _voice_german_allowed(self):
        """The safety net only when Dolmi would not capture its own mix again (mic, or other headphones)."""
        if self._source() == "mic":
            return True
        out, cap = self._settings.get("voice_out_device", ""), self._settings.get("audio_device", "")
        return bool(out) and bool(cap) and out not in cap and cap not in out

    def _voice_mixer(self):
        """Dolmi's headphone mix on the chosen headphones; None when that output cannot be opened."""
        m = getattr(self, "_voice_mix", None)
        if m is not None:
            return m
        try:
            from voice import mixer
            s = self._settings
            m = mixer.Mixer(safety=s.get("voice_safety", mixer.SAFETY_DEFAULT), split=s.get("voice_split"),
                            names=s.get("voice_names", ""),
                            german=bool(s.get("voice_mix")) and self._voice_german_allowed())
            m.start(self._voice_sink(s.get("voice_out_device", "")))
        except Exception as e:
            print(f"voice: could not open the headphone mix ({e})")
            return None
        self._voice_mix = m
        return m

    @staticmethod
    def _voice_sink(device):
        from voice import mixer
        return mixer.open_sink(device)

    def _voice_feed(self, chunk):
        """Captured audio (16 kHz) -> the German safety net, only while the headphone mix runs."""
        m = getattr(self, "_voice_mix", None)
        if m is not None and m.german_on and chunk is not None:
            m.feed_german(chunk, 16000)

    def _voice_name_check(self, german):
        m = getattr(self, "_voice_mix", None)
        if m is not None and german and m.check_name(german):
            self._emit("onNameHeard", german)

    def _voice_hold_original(self):
        """Hold-to-hear-original key: German at full level until the key is let go (Windows polls the
        key; elsewhere the shortcut toggles)."""
        m = getattr(self, "_voice_mix", None)
        if m is None:
            return False
        if dolmi_platform.OS != "win":
            m.hold(not m.held)
            return m.held
        if m.held:
            return True
        m.hold(True)
        key = _shortcut_parts(self._shortcut_map()["hold_original"])[1]

        def watch():
            t = time.monotonic()
            try:
                while time.monotonic() - t < HOLD_MAX_S and _key_down(key):
                    time.sleep(HOLD_POLL_S)
            except Exception as e:
                print(f"voice: hold key polling failed: {e}")
            finally:
                m.hold(False)
        threading.Thread(target=watch, daemon=True, name="voice-hold").start()
        return True

    def _voice_offer(self, en, meta):
        if not self._settings.get("voice_readout") or not en:
            return
        r = self._voice_reader()
        if r:
            r.offer(en, meta.get("end"))

    def _voice_stop(self):
        r, self._voice = getattr(self, "_voice", None), None
        m, self._voice_mix = getattr(self, "_voice_mix", None), None
        if r:
            r.stop()
        if m:
            m.stop()

    def voice_state(self):
        """For Settings > Audio: is reading aloud on, and which voice would speak."""
        r = self._voice_reader() if self._settings.get("voice_readout") else None
        backend = r.backend if r else None
        if backend is None:
            try:
                from voice import tts
                backend = tts.select_backend(self._voice_backends())
            except ImportError:
                backend = None
        return {"on": bool(self._settings.get("voice_readout")), "available": backend is not None,
                "backend": getattr(backend, "name", ""), "spoken": r.spoken if r else 0,
                "skipped": r.skipped if r else 0, "mix": getattr(self, "_voice_mix", None) is not None}

    def voice_setup(self):
        """Settings > Audio voice setup: headphones list + check, natural-voice download state."""
        try:
            from voice import kokoro, mixer
        except ImportError:
            return {"ok": False, "devices": [], "germanAllowed": False,
                    "check": {"level": "warn", "message": "The translated voice is not in this build."},
                    "natural": {"installed": False, "deps": False, "downloading": False, "sizeMb": 0, "mb": 0}}
        out = self._settings.get("voice_out_device", "")
        cap = "" if self._source() == "mic" else (self._settings.get("audio_device") or "")
        if not out and self._source() != "mic" and not cap:
            level, message = "warn", ("The default output is also the speaker Dolmi listens to. Pick your "
                                      "headphones, so the voice is not captioned again and the safety net can play.")
        else:
            level, message = mixer.check_device(out, cap)
        try:
            devices = mixer.headphone_devices()
        except Exception as e:
            print(f"voice setup: listing outputs failed: {e}")
            devices = []
        return {"ok": True, "devices": devices, "check": {"level": level, "message": message},
                "germanAllowed": self._voice_german_allowed(),
                "natural": {"installed": kokoro.is_installed(), "deps": kokoro.deps_ok(),
                            "downloading": "kokoro" in self._dl, "sizeMb": kokoro.SIZE_MB,
                            "mb": round(kokoro.downloaded_mb())}}

    def voice_test_tone(self):
        """Play the setup's test tone on the chosen headphones (opens the output briefly if needed)."""
        try:
            from voice import mixer
            m = getattr(self, "_voice_mix", None)
            if m is not None:
                m.play_effect(mixer.test_tone())
                return {"ok": True}
            m = mixer.Mixer(german=False).start(self._voice_sink(self._settings.get("voice_out_device", "")))
        except Exception as e:
            return {"ok": False, "message": f"Could not play on that output: {e}"}
        m.play(mixer.test_tone(), mixer.SR)

        def done():
            m.wait_tts(5)
            m.stop()
        threading.Thread(target=done, daemon=True).start()
        return {"ok": True}

    def download_voice_model(self):
        """VOICE-8: fetch the natural voice (~94 MB) into the models folder, with progress events."""
        try:
            from voice import kokoro
        except ImportError:
            return False
        if "kokoro" in self._dl:
            return False
        self._dl.add("kokoro")

        def work():
            err = ""
            stop = threading.Event()

            def poll():
                while not stop.wait(1.0):
                    self._emit("onModelProgress", "kokoro", round(kokoro.downloaded_mb()), kokoro.SIZE_MB)
            threading.Thread(target=poll, daemon=True).start()
            try:
                kokoro.download()
            except Exception as e:
                err = f"Download failed: {e}"
            stop.set()
            self._dl.discard("kokoro")
            self._voice_stop()                  # the next line picks the natural voice up
            self._emit("onModelDone", "kokoro", err)
        threading.Thread(target=work, daemon=True).start()
        return True

    def _cloud_submit(self, de, en, meta):
        """Send a final (never a draft) to the cloud translator, with the previous lines as context."""
        if meta.get("id") is None or not de:
            return
        try:
            tr = self._translator()
            if tr is None:
                return
            with self._hist_lock:
                context = [(d, e) for _, d, e, m in self._history[-1 - cloud_translate.CONTEXT_LINES:]
                           if m.get("id") != meta["id"]][-cloud_translate.CONTEXT_LINES:]
            tr.submit(meta["id"], de, en, meta.get("lang") or self._settings["language"],
                      self._settings["language"], context, self._ui_q, session=id(self._session))
        except Exception as e:
            print(f"cloud translator: line {meta.get('id')} not sent ({e})")

    def _apply_line_update(self, line_id, en, meta):
        """('final_update', id, subtitle, meta): a better subtitle for a line already shown. Replaces it in
        this session's history (Assistant, summary), the transcript and the page; nothing if it is gone."""
        session = self._session
        if not session or not en or meta.get("session", id(session)) != id(session):
            return                                    # stopped, or an answer for an earlier meeting
        meta = _plain({k: v for k, v in meta.items() if k != "session"})
        with self._hist_lock:
            for i in range(len(self._history) - 1, -1, -1):
                ts, de, old, m = self._history[i]
                if m.get("id") == line_id:
                    self._history[i] = (ts, de, en, {**m, "en_source": meta.get("source"), "en_local": old})
                    break
            else:
                return
        tr = session.get("transcript")
        if tr is not None:
            tr.update_line(line_id, en=en, en_source=meta.get("source"))
        self._emit("onLineUpdate", line_id, en, meta)

    def translator_state(self):
        """Settings → Captions → Subtitles by: choice, key state, cost, privacy note and this session's counts."""
        provider = self._settings.get("translator", "local")
        tr = self._cloud_tr
        return {"translator": provider, "status": cloud_translate.STATUS,
                "providers": {k: {"label": v["label"], "cost": v["cost"], "privacy": v["privacy"],
                                  "hasKey": bool(self._translator_key(k))}
                              for k, v in cloud_translate.PROVIDERS.items()},
                "timeout": self._settings.get("translator_timeout", cloud_translate.TIMEOUT),
                "stats": tr.summary() if tr is not None and tr.provider == provider else None}

    def translator_set(self, provider):
        """'local' (Opus-MT only, the default), 'gemini' or 'deepl'. Takes effect from the next line."""
        if provider != "local" and provider not in cloud_translate.PROVIDERS:
            return {"ok": False, "message": "Unknown translator"}
        with self._settings_lock:
            self._settings["translator"] = provider
            config.save_settings(self._settings)
        return {"ok": True, **self.translator_state()}

    def translator_set_key(self, provider, key):
        """Save a translator key, encrypted like the Assistant keys. Gemini's is the same key as the Assistant's."""
        if provider not in cloud_translate.PROVIDERS:
            return {"ok": False, "message": "Unknown translator"}
        key = (key or "").strip()
        with self._settings_lock:
            self._settings["api_keys"][provider] = assistant.encrypt_key(key)
            config.save_settings(self._settings)
        return {"ok": True, **self.translator_state()}

    # ------------------------------------------------------------------ audio source
    def _source(self):
        return "mic" if self._settings.get("audio_source") == "mic" else "speaker"

    def _source_label(self):
        """What Dolmi is listening to, for the front page and the subtitle bar's corner."""
        if self._source() == "mic":
            return {"source": "mic", "device": self._settings.get("mic_device") or "Default microphone"}
        return {"source": "speaker", "device": self._settings["audio_device"] or DEFAULT_SPEAKER}

    def _push_source(self):
        lab = self._source_label()
        self._emit("onSource", lab)
        self._ov("source", lab["source"], lab["device"])

    def set_source(self, source):
        """Speaker (meeting audio) or mic (people in the room). Takes effect at once mid-meeting."""
        if source not in ("speaker", "mic"):
            return False
        with self._settings_lock:
            self._settings["audio_source"] = source
            config.save_settings(self._settings)
        cap = getattr(self, "_cap_stop", None)
        if self._session and cap:
            cap.set()                      # capture loop reopens on the new source
        else:
            self._push_source()
        return self._source_label()

    def toggle_source(self):
        return self.set_source("speaker" if self._source() == "mic" else "mic")

    def mic_devices(self):
        try:
            return live_subs.input_devices()
        except Exception as e:
            print(f"mic_devices failed: {e}")
            return []

    def devices(self):
        try:
            return live_subs.output_devices()
        except Exception as e:
            print(f"devices failed: {e}")
            return []

    # ------------------------------------------------------------------ live captions
    def start_listening(self, download=False):
        """Start captions. Models that aren't downloaded yet are listed instead of fetched
        silently, so the user decides (download=True) — a speech model can be 3 GB."""
        if not download and not self._engine:
            missing = self.missing_models()
            if missing:
                return {"ok": False, "missing": missing}
        with self._lock:
            if self._session:
                return {"ok": True}
            token = object()
            self._session = {"token": token, "stop": None, "transcript": None}
        threading.Thread(target=self._begin, args=(token,), daemon=True).start()
        return {"ok": True}

    def _alive(self, token):
        s = self._session
        return s is not None and s.get("token") is token

    def _begin(self, token):
        with self._engine_lock:                       # a second Start waits here, then reuses it
            if not self._alive(token):
                return
            if not self._engine:
                self._set_engine_state("loading", "speech and translation models")
                watch = self._watch_downloads(self._needed_models())
                try:
                    self._engine = self._new_engine()
                except Exception as e:
                    watch.set()
                    with self._lock:
                        if self._alive(token):
                            self._session = None
                    self._set_engine_state("idle", "models load on Start")
                    self._emit("onError", f"Could not start captions: {e}")
                    self._emit("onStopped")
                    return
                watch.set()
                self._set_engine_state("ready")
        meeting = {}                                  # the diarizer's merges relabel THIS meeting only
        diarizer = self._make_diarizer(lambda: meeting.get("transcript"))  # SPK-3: None unless the beta is on
        with self._lock:
            if not self._alive(token):                # stopped while the models loaded
                return
            self._engine.language = self._settings["language"]
            self._engine.clarify = self._clarify = (clarify.Queue(self._vocab_rows(), self._clarify_ignored())
                                                    if self._settings.get("clarify_queue") else None)
            with self._hist_lock:
                self._history = []
            self._chat_id, self._ai_history = None, []
            sid = id(token)
            # STR-3: captured audio is counted; a backlog over ~30 s is shown and logged, never dropped
            stop, audio_q = threading.Event(), _VoiceTapQueue(
                self._voice_feed, on_overload=lambda sec: self._ui_q.put(("overload", round(sec), "", {"session": sid})))
            both = bool(self._settings.get("capture_both"))   # SPK-5: mic + speaker at once (beta)
            source_log = None
            if both:
                import speakers
                source_log = speakers.SourceLog()
                if diarizer is not None:
                    diarizer.source_log = source_log
            tr = live_subs.Transcript(self._data, save=self._settings["save_transcripts"])
            meeting["transcript"] = tr
            self._last_transcript = tr
            self._session.update(stop=stop, transcript=tr, diarizer=diarizer)

        def capture():
            try:
                while not stop.is_set():
                    cap = threading.Event()        # set by stop_listening or a source switch
                    self._cap_stop = cap
                    if stop.is_set():
                        break
                    self._push_source()
                    if both:
                        dolmi_platform.dual_stream(audio_q, cap, self._settings.get("mic_device") or None,
                                                   self._settings["audio_device"] or None,
                                                   on_level=self._on_level, log=source_log)
                    elif self._source() == "mic":
                        live_subs.mic_stream(audio_q, cap, self._settings.get("mic_device") or None,
                                             on_level=self._on_level)
                    else:
                        live_subs.loopback_stream(audio_q, cap, self._settings["audio_device"] or None,
                                                  on_level=self._on_level)
            except Exception as e:
                # no audio means no captions: end the session instead of sitting on "Translating"
                self._emit("onError", f"Can't record audio: {e}")
                if self._alive(token):
                    self.stop_listening()
                else:
                    stop.set()

        threading.Thread(target=capture, daemon=True).start()
        threading.Thread(target=live_subs.worker, args=(self._engine, audio_q, self._ui_q, tr, stop),
                         kwargs={"diarizer": diarizer, "session": sid}, daemon=True).start()
        self._emit("onListening", True, self._engine_label())
        if self._settings.get("overlay"):             # subtitles are the point of pressing Start
            self.show_overlay()
            self._emit("onBar", True)

    def stop_listening(self):
        with self._lock:
            s, self._session = self._session, None
        if s and s.get("stop"):
            s["stop"].set()
        cap = getattr(self, "_cap_stop", None)
        if cap:
            cap.set()
        self._level = 0.0
        tr = s.get("transcript") if s else None
        if tr and s.get("diarizer"):                  # SPK-3: rewrite the files with the final speaker names
            threading.Thread(target=self._finish_transcript, args=(tr,), daemon=True).start()
        self._emit("onListening", False, tr.md.name if tr and tr.md else "")
        return True

    @staticmethod
    def _finish_transcript(tr):
        try:
            tr.finish()
        except OSError as e:
            print(f"Could not rewrite the transcript {tr.md}: {e}")

    def _on_level(self, rms):
        self._level = rms
        if rms > self._level_peak:
            self._level_peak = rms

    # ------------------------------------------------------------------ speaker labels (SPK-3/4, beta)
    def _make_diarizer(self, transcript=lambda: None):
        """The session's diarizer, or None when Speaker labels is off or its model cannot load
        (captions still run, without labels). Its merges relabel transcript() - the meeting it was
        built for - even when they run after Stop while the next meeting is already live."""
        if not self._settings.get("speaker_labels"):
            return None
        try:
            import speakers
            if not speakers.is_installed():
                self._emit("onEngine", f"downloading the speaker model ({speakers.MODEL_MB} MB)…")
            return speakers.load_diarizer(
                mode=self._source, on_merge=lambda old, new: self._on_speaker_merge(old, new, transcript()))
        except Exception as e:
            print(f"Speaker labels unavailable: {e!r}")
            self._emit("onError", "Speaker labels are off for this meeting: the voice model could not load.")
            return None

    def _speaker_target(self):
        """(transcript, diarizer, live) the speaker calls act on: this meeting, or the last one after Stop."""
        s = self._session
        tr = s.get("transcript") if s else None
        if tr is None:
            return self._last_transcript, None, False
        return tr, s.get("diarizer"), True

    def _speakers_changed(self, tr, live):
        """After a rename/reassign/merge: tell the page, and after Stop rewrite the saved files at once."""
        if not live:
            self._finish_transcript(tr)
        out = tr.speaker_list()
        self._emit("onSpeakers", out)
        return out

    def _on_speaker_merge(self, old, new, tr=None):
        """The diarizer found two ids are one voice (or the user merged them): relabel the lines of
        `tr` (default: the meeting the speaker calls act on). The page only hears about it when `tr`
        is the meeting it shows; a merge of an older meeting just rewrites that meeting's files."""
        target, _, live = self._speaker_target()
        if tr is None:
            tr = target
        if tr is None:
            return
        tr.relabel(old, new)
        if tr is not target:                          # an older, stopped meeting
            self._finish_transcript(tr)
            return
        self._emit("onSpeakerMerge", old, new)
        self._speakers_changed(tr, live)

    @staticmethod
    def _speaker_id(v):
        return isinstance(v, int) and not isinstance(v, bool) and v >= 0   # 0 = "You" (SPK-5)

    def speaker_list(self):
        """[{id, name, colour, lines}] for this meeting (or the last one)."""
        tr = self._speaker_target()[0]
        return tr.speaker_list() if tr else []

    def speaker_rename(self, speaker_id, name):
        """Name a speaker; every past line of this meeting and its saved files follow."""
        tr, _, live = self._speaker_target()
        name = re.sub(r"[\s·*<>\[\]|]+", " ", str(name or "")).strip()[:SPEAKER_NAME_MAX].strip()
        if tr is None or not self._speaker_id(speaker_id) or not name:
            return {"ok": False, "message": "Type a name for this speaker."}
        if any(s["id"] != speaker_id and s["name"].casefold() == name.casefold() for s in tr.speaker_list()):
            return {"ok": False, "message": f"Another speaker is already called {name}. "
                                            "If they are the same person, use 'Same person as'."}
        tr.rename_speaker(speaker_id, name)
        return {"ok": True, "speakers": self._speakers_changed(tr, live)}

    def speaker_reassign(self, line_id, speaker_id=None):
        """'This line is someone else': give one line another speaker (None = a new speaker)."""
        tr, d, live = self._speaker_target()
        if tr is None or (speaker_id is not None and not self._speaker_id(speaker_id)):
            return {"ok": False, "message": "That speaker is not in this meeting."}
        if speaker_id is None:
            if d is not None:
                speaker_id = d.new_speaker()
            else:
                speaker_id = tr.next_speaker_id()
        if not tr.update_line(line_id, speaker=speaker_id, speaker_conf=1.0):
            return {"ok": False, "message": "That line is no longer in this meeting."}
        return {"ok": True, "speaker": speaker_id, "speakers": self._speakers_changed(tr, live)}

    def speaker_merge(self, from_id, into_id):
        """Two labels are the same person: every line of `from_id` becomes `into_id`."""
        tr, d, live = self._speaker_target()
        if tr is None or not (self._speaker_id(from_id) and self._speaker_id(into_id)) or from_id == into_id:
            return {"ok": False, "message": "Pick two different speakers."}
        if d is not None:
            d.merge(from_id, into_id)                 # calls _on_speaker_merge
        else:
            self._on_speaker_merge(from_id, into_id)
        return {"ok": True, "speakers": tr.speaker_list()}

    # ------------------------------------------------------------------ 'nothing heard' warning (APP-3)
    def _heard_watch_loop(self):
        while True:
            self._sleep(HEARD_TICK_S)
            try:
                self._heard_tick()
            except Exception as e:
                print(f"heard watch failed: {e}")

    def _heard_tick(self):
        """Once a second while live: 'silent' after 20 s below the silence level, 'no_captions' when
        there is sound but no caption for 20 s; a caption, Stop or the setting turned off clears it.
        Capture errors are not handled here (they already raise onError)."""
        now = self._clock()
        if self._heard_tick_at is not None and now <= self._heard_tick_at:
            return
        self._heard_tick_at = now
        s = self._session
        if not (s and s.get("stop") and self._settings.get("warn_silence", True)):
            self._heard_key, self._level_peak = None, 0.0
            self._set_heard(None)
            return
        peak, self._level_peak = self._level_peak, 0.0
        key = (s.get("token"), json.dumps(self._source_label(), sort_keys=True))
        if key != self._heard_key:
            # new session or another device: count afresh from the previous tick (it went live or
            # switched at most one tick ago), so the warning is never late
            self._heard_key, self._heard_start, self._last_sound = key, now - HEARD_TICK_S, 0.0
            self._set_heard(None)
        if peak >= HEARD_SILENCE_RMS:
            self._last_sound = now
        since_caption = now - max(self._heard_start, self._last_caption)
        quiet_for = now - max(self._heard_start, self._last_sound)
        if since_caption < HEARD_AFTER_S:
            state = None                            # never while captions arrive
        elif quiet_for >= HEARD_AFTER_S:
            state = "silent"
        elif quiet_for < HEARD_SOUND_NOW_S:
            state = "no_captions"
        else:
            state = self._heard_state               # a pause in uncaptioned sound: keep what is shown
        self._set_heard(state)

    def _set_heard(self, state):
        if state != self._heard_state:
            self._heard_state = state
            self._emit("onHeardWarn", state)

    def open_mic_privacy(self):
        """Open the OS microphone privacy page (Windows Settings / macOS System Settings). It only opens
        the page; it changes nothing."""
        try:
            dolmi_platform.open_path(dolmi_platform.mic_privacy_url)
            return True
        except Exception as e:
            print(f"open microphone privacy page failed: {e}")
            return False

    # ------------------------------------------------------------------ instant start (APP-4)
    def _new_engine(self):
        """The engine the next Start uses: Whisper + translator here, or a cloud speech provider."""
        source = self._settings.get("speech_source", "local")
        s = self._settings
        if source == "local":
            eng = live_subs.Engine(s["model"], s["device"], s["language"])
        else:   # captions use the provider's fast speech model, not the Assistant's model
            eng = cloud_speech.CloudEngine(source, self._key_for(source), s["language"])
            if s.get("cloud_failover"):   # STR-9: a failed line is captioned on this PC (model loads on first need)
                eng.fallback_factory = lambda: live_subs.Engine(s["model"], s["device"], s["language"])
        eng.unsure_marks = bool(s.get("unsure_marks"))   # STR-8
        return eng

    def _set_engine_state(self, state, label=None):
        """idle | loading | ready | missing; the page says 'Ready' only for "ready"."""
        self._engine_state = state
        self._emit("onEngine", label or self._engine_label(), state)

    def _drop_engine(self):
        """Forget the loaded models (a setting changed, or preloading was turned off) and free their memory."""
        with self._gen_lock:
            self._settings_gen += 1
            dropped, self._engine = self._engine, None
        if dropped is not None:
            del dropped
            gc.collect()
        if self._engine_state != "idle":
            self._set_engine_state("idle", "models load on Start")

    def _start_background(self):
        """Called once by main when the window is up: keep the models warm from now on."""
        threading.Thread(target=self._warm_loop, name="dolmi-warm", daemon=True).start()
        self._request_warm()

    def _request_warm(self):
        self._warm_req.set()

    def _warm_loop(self):
        while True:
            self._warm_req.wait()
            time.sleep(WARM_DEBOUNCE_S)
            self._warm_req.clear()
            try:
                self._warm_once()
            except Exception as e:           # never let a failed load stop later ones
                print(f"Background model load failed: {e}")

    def _warm_once(self):
        """Load the models the next Start needs, in the background, and keep them: Start then only opens
        the audio. Only models already on disk are loaded (a download is always the user's choice), and
        nothing happens while captions run or when "Load models when Dolmi opens" is off.
        Returns True when the models are loaded."""
        if not self._settings.get("preload_models", True) or self._session:
            return False
        if self._engine:
            if self._engine_state != "ready":
                self._set_engine_state("ready")
            return True
        if self._needs_download():
            self._set_engine_state("missing", "models download on Start")
            return False
        with self._engine_lock:              # a Start that comes now waits here, then reuses the engine
            if self._engine or self._session:
                return bool(self._engine)
            gen = self._settings_gen
            self._set_engine_state("loading", "speech and translation models")
            try:
                engine = self._new_engine()
            except Exception as e:           # e.g. a cloud provider without a key: Start says why
                print(f"Background model load skipped: {e}")
                self._set_engine_state("idle", "models load on Start")
                return False
            if gen == self._settings_gen:    # priming takes seconds: skip it for an engine already stale
                self._prime(engine)
            with self._gen_lock:             # a setting may also change while priming: re-check, then store
                fresh = gen == self._settings_gen
                if fresh:
                    self._engine = engine
            if not fresh:                    # load the new choice instead
                del engine
                gc.collect()
                self._request_warm()
                return False
        if self._engine is engine:           # not dropped again in the meantime
            self._set_engine_state("ready")
        return True

    def _needs_download(self):
        try:
            return bool(self.missing_models())
        except Exception as e:
            print(f"Could not check the models: {e}")
            return True

    @staticmethod
    def _prime(engine):
        """Run each model once on a little silence, so the first real caption does not pay for the GPU
        (CUDA) and decoder set-up. Failures only cost that first-caption speed."""
        try:
            import numpy as np
            asr = getattr(engine, "asr", None)
            if asr is not None:
                silence = np.zeros(live_subs.SR, dtype=np.float32)
                language = None if engine.language == "auto" else engine.language
                list(asr.transcribe(silence, beam_size=1, vad_filter=False, condition_on_previous_text=False,
                                    language=language)[0])
                # the first real caption still paid ~4 s (bench, RTX 4060) for the Silero VAD (the
                # segmenter's speech_gap and Whisper's vad_filter) and the beam-5 decoder: load them too
                quiet = (np.random.default_rng(0).standard_normal(live_subs.SR)
                         * live_subs.SILENCE_RMS * 4).astype(np.float32)
                live_subs.speech_gap(quiet)
                list(asr.transcribe(silence, beam_size=5, vad_filter=True, condition_on_previous_text=False,
                                    language=language)[0])
            lang = engine.language if engine.language != "auto" else "de"
            engine.translate("Hello everyone." if lang == "en" else "Hallo zusammen.", lang)
        except Exception as e:
            print(f"Model warm-up run failed (first caption may be slower): {e}")
        engine.context = ""

    # ------------------------------------------------------------------ models
    def _auto_speech(self):
        return dolmi_platform.auto_speech(self.pc_info()["gpu"])

    def _needed_models(self):
        """The speech model and translator the next Start will use."""
        import models
        source, lang = self._settings.get("speech_source", "local"), self._settings["language"]
        if source == "gemini":
            return []                                 # Gemini hears and translates: nothing local
        speech = self._settings["model"] if self._settings["model"] != "auto" else self._auto_speech()
        return [k for k in ((speech if source == "local" else None), models.translator_key(lang)) if k]

    def missing_models(self):
        """The models the next Start needs that aren't on disk yet."""
        import models
        out = []
        for k in self._needed_models():
            m = models.BY_KEY.get(k)
            try:
                if m and not models.is_installed(m):
                    out.append({"key": m.key, "name": m.name, "sizeMb": m.size_mb})
            except OSError as e:
                print(f"missing_models: could not check {k}: {e}")
        return out

    def use_model(self, key):
        """Pick the speech model ('auto' or a catalog key) from the models list."""
        import models
        m = models.BY_KEY.get(key)
        if key != "auto" and not (m and m.job == models.SPEECH):
            return False
        return self.set_setting("model", key)

    def _watch_downloads(self, keys):
        """While models load, report download progress for any that aren't installed yet."""
        import models
        done = threading.Event()
        todo = [models.BY_KEY[k] for k in keys if k in models.BY_KEY and not models.is_installed(models.BY_KEY[k])]

        def poll():
            while todo and not done.wait(1.0):
                m = next((m for m in todo if not models.is_installed(m)), None)
                if not m:
                    return
                mb = models.downloaded_mb(m)
                self._emit("onEngine", f"downloading {m.name} — {mb:,.0f} of {m.size_mb:,} MB (first start only)", "loading")
        threading.Thread(target=poll, daemon=True).start()
        return done

    def list_models(self):
        """Speech models + the translators this language needs, with install state and fit."""
        import models
        pc = self.pc_info()
        pcm = {"ram_gb": pc["ram_gb"] or 8, "cores": pc["cores"], "gpu": pc["gpu"]}
        needed = set(self._needed_models())
        lang = self._settings["language"]
        translators = {models.translator_key(lang)} | ({"opus-mul"} if lang != "en" else set())
        loaded = set()
        if self._engine:
            loaded = {self._engine.model_name, *self._engine.translators}
        choice = self._settings["model"]
        out = []
        for m in models.CATALOG:
            if m.job == models.TRANSLATE and m.key not in translators:
                continue
            try:
                installed = models.is_installed(m)
            except Exception:
                installed = False
            speech = m.job == models.SPEECH
            verdict, colour = models.fit(m, pcm) if speech else ("Runs well", "ok")
            if speech and verdict.startswith("Runs well"):
                verdict = "Runs well on your GPU" if pc["gpu"] else "Runs well on your CPU"
            out.append({"key": m.key, "name": m.name, "kind": "speech" if speech else "translate",
                        "sizeMb": m.size_mb, "installed": installed, "downloading": m.key in self._dl,
                        "fit": verdict, "fitOk": colour == "ok", "fitLevel": colour, "note": m.note,
                        "needed": m.key in needed, "loaded": m.key in loaded,
                        "selected": speech and m.key == choice,
                        "auto": speech and choice == "auto" and m.key in needed})
        return out

    def download_model(self, key):
        import models
        m = models.BY_KEY.get(key)
        if not m or key in self._dl:
            return False
        self._dl.add(key)

        def work():
            err = ""
            stop = threading.Event()

            def poll():
                while not stop.wait(1.0):
                    self._emit("onModelProgress", key, round(models.downloaded_mb(m)), m.size_mb)
            threading.Thread(target=poll, daemon=True).start()
            try:
                models.download(m)
            except Exception as e:
                err = f"Download failed: {e}"
            stop.set()
            self._dl.discard(key)
            self._emit("onModelDone", key, err)
            if not err:
                self._request_warm()         # the next Start's models may all be here now

        threading.Thread(target=work, daemon=True).start()
        return True

    def remove_model(self, key):
        import models
        m = models.BY_KEY.get(key)
        if not m:
            return {"ok": False, "message": "Unknown model."}
        e = self._engine
        if e and (e.model_name == key or key in e.translators):
            if self._session:
                return {"ok": False, "message": "Stop translating before removing a model in use."}
            self._drop_engine()
        try:
            models.uninstall(m)
        except Exception as ex:
            return {"ok": False, "message": f"Could not remove it: {ex}"}
        return {"ok": True}

    def open_model_folder(self):
        d = self.pc_info().get("model_dir")
        if d:
            try:
                Path(d).mkdir(parents=True, exist_ok=True)
                dolmi_platform.open_path(d)
            except OSError as e:
                print(f"open model folder failed: {e}")
        return True

    # ------------------------------------------------------------------ meetings
    def _meeting_path(self, name):
        """A meeting file inside the data folder, or None (blocks ..\\, ../ and absolute paths)."""
        try:
            base = self._data.resolve()
            p = (base / str(name)).resolve()
        except (OSError, ValueError):
            return None
        if p.parent != base or p.suffix != ".md" or not p.name.startswith("meeting_"):
            return None
        return p

    def _meeting_files(self, archived, date_from="", date_to=""):
        """(path, ISO date) of the saved meetings, newest first, in the archived or main list and between
        the two dates (YYYY-MM-DD, both included; '' = no limit)."""
        arch = self._archived()["meetings"]
        lo = date_from if isinstance(date_from, str) and _DATE_RE.fullmatch(date_from) else ""
        hi = date_to if isinstance(date_to, str) and _DATE_RE.fullmatch(date_to) else ""
        for p in sorted(self._data.glob("meeting_*.md"), reverse=True):
            if (p.name in arch) != bool(archived):
                continue
            date = p.stem.removeprefix("meeting_")[:10]
            date = date if _DATE_RE.fullmatch(date) else ""
            if (lo or hi) and (not date or (lo and date < lo) or (hi and date > hi)):
                continue
            yield p, date

    def _meeting_item(self, p, date, rows=None):
        try:
            dt = datetime.strptime(p.stem.removeprefix("meeting_"), "%Y-%m-%d_%H-%M")
            day, tm = f"{dt:%a %d %b %Y}", f"{dt:%H:%M}"
        except ValueError:
            day, tm = p.stem, ""
        rows = self.read_meeting(p.name) if rows is None else rows
        title, source = self._meeting_title(p, rows)
        return {"name": p.name, "day": day, "time": tm, "date": date, "lines": len(rows),
                "title": title, "titleSource": source}

    def _meeting_meta(self, p):
        """The meeting's sidecar (meeting_<stamp>.json next to its .md): {"title": ...}; {} when none."""
        try:
            meta = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
            return meta if isinstance(meta, dict) else {}
        except (OSError, ValueError):
            return {}

    def _meeting_title(self, p, rows):
        """(title, source): the user's title ("custom"), else the summary's ("summary"), else the first
        meaningful line ("lines"); ("", "") for an empty meeting."""
        meta = self._meeting_meta(p)
        custom = meta.get("title")
        if isinstance(custom, str) and custom.strip():
            return custom.strip()[:TITLE_EDIT_MAX], "custom"
        from_summary = meta.get("summary_title")           # APP-10: the structured summary's own title
        if isinstance(from_summary, str) and from_summary.strip():
            return _shorten(from_summary.strip()), "summary"
        summary = self._data / f"summary_{p.stem.removeprefix('meeting_')}.md"
        try:
            title = _title_from_summary(summary.read_text(encoding="utf-8")) if summary.is_file() else ""
        except OSError:
            title = ""
        if title:
            return title, "summary"
        title = _title_from_lines(self._english(r["de"], r["en"]) for r in rows)
        return (title, "lines") if title else ("", "")

    def list_meetings(self, archived=False, date_from="", date_to=""):
        """The saved meetings, newest first: {name, day, time, date (YYYY-MM-DD), lines, title, titleSource}."""
        return [self._meeting_item(p, date) for p, date in self._meeting_files(archived, date_from, date_to)]

    def search_meetings(self, query="", archived=False, date_from="", date_to=""):
        """Meetings whose lines (either language) or title contain every word of the query, ignoring case
        and accents, each with its first matching lines ("hits") and how many lines match ("matches")."""
        words = _fold(str(query or "")[:200]).split()
        out = []
        for p, date in self._meeting_files(archived, date_from, date_to):
            rows = self.read_meeting(p.name)
            item = self._meeting_item(p, date, rows)
            if not words:
                out.append(item)
                continue
            hits = [r for r in rows if all(w in _fold(r["de"] + " \n " + r["en"]) for w in words)]
            if not hits and not all(w in _fold(item["title"]) for w in words):
                continue
            item["matches"] = len(hits)
            item["hits"] = [{k: r.get(k, "") for k in ("ts", "offset", "de", "en")} for r in hits[:SEARCH_HITS]]
            out.append(item)
        return out

    def rename_meeting(self, name, title):
        """Give a meeting its own title (kept in meeting_<stamp>.json next to it); '' = automatic again."""
        p = self._meeting_path(name)
        if not p or not p.exists() or not isinstance(title, str):
            return {"ok": False, "message": "That meeting is not there any more."}
        title = " ".join("".join(c for c in title if c.isprintable()).split())[:TITLE_EDIT_MAX]
        side = p.with_suffix(".json")
        meta = self._meeting_meta(p)
        if title:
            meta["title"] = title
        else:
            meta.pop("title", None)
        try:
            if meta:
                tmp = side.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
                os.replace(tmp, side)
            elif side.exists():
                side.unlink()
        except OSError as e:
            print(f"rename meeting {p.name} failed: {e}")
            return {"ok": False, "message": f"Could not save the title: {e}"}
        new, source = self._meeting_title(p, self.read_meeting(p.name))
        return {"ok": True, "title": new, "titleSource": source}

    def read_meeting(self, name):
        """A saved meeting's lines: {ts (speech start), end ('' in old files), offset ('+mm:ss'), de, en}."""
        p = self._meeting_path(name)
        if not p or not p.exists():
            return []
        text = p.read_text(encoding="utf-8")
        colours = self._speaker_colours(p)
        rows = []
        for span, en, de in _MEETING_RE.findall(text):   # groups are (time span[ · speaker], en, de)
            span, _, who = span.partition(" · ")
            start, _, end = span.strip().replace("-", "–").partition("–")
            row = {"ts": start.strip(), "end": end.strip(), "de": de.strip(), "en": en.strip()}
            if who.strip():                                # SPK-3: a speaker label (beta)
                row["speaker"] = who.strip()
                row["colour"] = colours.get(who.strip(), "")
            rows.append(row)
        # offsets count from the meeting's start: the "started" note, else the file name's minute
        m = _STARTED_RE.search(text)
        t0 = _seconds(m.group(1)) if m else None
        if t0 is None:
            try:
                t0 = _seconds(f"{datetime.strptime(p.stem.removeprefix('meeting_'), '%Y-%m-%d_%H-%M'):%H:%M:%S}")
            except ValueError:
                t0 = _seconds(rows[0]["ts"]) if rows else None
        for r in rows:
            s = _seconds(r["ts"])
            r["offset"] = _offset((s - t0) % 86400) if s is not None and t0 is not None else ""
        return rows

    @staticmethod
    def _speaker_colours(md_path):
        """name -> colour from meeting_<stamp>.speakers.json ({} when there is none or it is unreadable)."""
        try:
            data = json.loads(md_path.with_suffix(".speakers.json").read_text(encoding="utf-8"))
            return {str(v["name"]): str(v.get("colour", "")) for v in data.values() if isinstance(v, dict) and v.get("name")}
        except (OSError, ValueError, KeyError, AttributeError):
            return {}

    def meeting_txt(self, name):
        """A saved meeting as plain text: each line's time, the caption (what was said) and its translation."""
        p = self._meeting_path(name)
        pairs = self.read_meeting(name)
        if not p or not pairs:
            return ""
        try:
            when = f"{datetime.strptime(p.stem.removeprefix('meeting_'), '%Y-%m-%d_%H-%M'):%A %d %B %Y, %H:%M}"
        except ValueError:
            when = p.stem
        out = [f"Dolmi meeting — {when}", f"{len(pairs)} lines · caption (what was said) and translation", ""]
        for x in pairs:
            span = x["ts"] + (f"–{x['end']}" if x.get("end") else "")
            out += [f"[{span}]" + (f" {x['offset']}" if x.get("offset") else "")
                    + (f" {x['speaker']}" if x.get("speaker") else ""),
                    f"Caption:     {x['de']}", f"Translation: {x['en']}", ""]
        return "\n".join(out)

    def export_meeting(self, name):
        """Save a meeting as a .txt file wherever the user picks (system Save dialog)."""
        text = self.meeting_txt(name)
        if not text:
            return {"ok": False, "message": "That meeting has no lines to export."}
        try:
            import webview
            downloads = Path.home() / "Downloads"
            chosen = self._window.create_file_dialog(
                webview.FileDialog.SAVE, directory=str(downloads if downloads.is_dir() else self._data),
                save_filename=Path(str(name)).with_suffix(".txt").name)
        except Exception as e:
            return {"ok": False, "message": f"Could not open the save dialog: {e}"}
        if isinstance(chosen, (list, tuple)):
            chosen = chosen[0] if chosen else None
        if not chosen:
            return {"ok": False, "cancelled": True}
        target = Path(chosen)
        if target.suffix.lower() != ".txt":
            target = target.with_name(target.name + ".txt")
        try:
            target.write_text(text, encoding="utf-8")
        except OSError as e:
            return {"ok": False, "message": f"Could not save: {e}"}
        return {"ok": True, "path": str(target)}

    def delete_meeting(self, name):
        p = self._meeting_path(name)
        if p:
            for f in (p, p.with_suffix(".srt"), p.with_suffix(".json"), p.with_suffix(".speakers.json")):
                try:
                    f.unlink()
                except OSError:
                    pass
            a = self._archived()
            if p.name in a["meetings"]:
                a["meetings"].discard(p.name)
                self._save_archived(a)
        return True

    def open_data_folder(self):
        try:
            self._data.mkdir(exist_ok=True)
            dolmi_platform.open_path(self._data)
        except OSError as e:
            print(f"open folder failed: {e}")
        return True

    # ------------------------------------------------------------------ vocabulary
    def read_vocab(self):
        return {"vocabulary": self._read_file("vocabulary.txt"), "glossary": self._read_file("glossary.txt")}

    def _read_file(self, name):
        p = config.CONFIG / name
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def save_vocab(self, vocabulary=None, glossary=None):
        """Write only the files the page actually sent (None = leave that file alone) and apply them from
        the next line. Lines with problems are saved as typed but never applied; they come back in
        "problems" (same shape as validate_vocab)."""
        before = set(_vocab_items(self._read_file("vocabulary.txt"), self._read_file("glossary.txt")))
        with self._vocab_lock:
            for name, text in (("vocabulary.txt", vocabulary), ("glossary.txt", glossary)):
                if text is not None:
                    _replace_text(config.CONFIG / name, str(text).rstrip() + "\n")
            self._vocab_seen = self._vocab_mtimes()   # this save is not a hand edit: one reload, not two
        changed = [x for x in _vocab_items(self._read_file("vocabulary.txt"), self._read_file("glossary.txt"))
                   if x not in before]
        if changed:   # what this save added or changed: the summary's "latest edited terms"
            self._vocab_recent = changed[-VOCAB_RECENT:]
        if self._engine:
            self._engine.reload_terms()
        return {"ok": True, "problems": self.validate_vocab()}

    def validate_vocab(self, vocabulary=None, glossary=None):
        """Problems ({line, text, message}) in the given texts, or in the saved files for None."""
        v = self._read_file("vocabulary.txt") if vocabulary is None else str(vocabulary)
        g = self._read_file("glossary.txt") if glossary is None else str(glossary)
        res = {"vocabulary": live_subs.parse_vocabulary(v)[1], "glossary": live_subs.parse_glossary(g)[1]}
        return {"ok": not (res["vocabulary"] or res["glossary"]), **res}

    def vocab_summary(self, vocabulary=None, glossary=None, profile=None):
        """What the vocabulary does right now, for the Vocabulary tab (texts None = the saved files):
        counts per group, the latest edited terms, how many lines have problems, the Whisper hotword budget
        of the chosen profile (tokens, which terms get cut), the glossary size warning, and hints likely to
        over-bias Whisper. profile None = the saved setting."""
        v = self._read_file("vocabulary.txt") if vocabulary is None else str(vocabulary)
        g = self._read_file("glossary.txt") if glossary is None else str(glossary)
        rules, vproblems = live_subs.parse_vocabulary(v)
        _, profiles, gproblems = live_subs.parse_glossary_profiles(g)
        profile = self._settings.get("vocab_profile", "") if profile is None else str(profile)
        profile = next((p for p in profiles if p.lower() == profile.strip().lower()), "")
        engine_tok = getattr(self._engine, "tokenizer", None)
        tok = (engine_tok() if engine_tok else None) or live_subs.whisper_tokenizer()
        terms = live_subs.glossary_terms(g, profile)
        budget = live_subs.hotword_budget(terms, tok)
        soft_terms, soft_chars = live_subs.GLOSSARY_SOFT
        chars = len(", ".join(terms))
        recent = getattr(self, "_vocab_recent", None)
        return {
            "counts": {"gloss": len(live_subs.glossary_terms(g)),
                       **{s: sum(r.section == s for r in rules) for s in live_subs.VOCAB_SECTIONS}},
            "problems": len(vproblems) + len(gproblems),
            "recent": [{"group": k, "term": t} for k, t, _ in (recent or _vocab_items(v, g)[-VOCAB_RECENT:])],
            "recentFrom": "save" if recent else "files",
            "profile": profile,
            "profiles": [{"name": n, "terms": len(live_subs.glossary_terms(g, n))} for n in profiles],
            "budget": {"tokens": budget["tokens"], "limit": budget["limit"], "exact": budget["exact"],
                       "terms": len(terms), "chars": chars, "cut": budget["cut"],
                       "tooLong": len(terms) > soft_terms or chars > soft_chars,
                       "softTerms": soft_terms, "softChars": soft_chars,
                       "cloud": self._settings.get("speech_source", "local") != "local"},   # no budget in the cloud
            "bias": live_subs.bias_warnings(terms),
        }

    def set_vocab_profile(self, name=""):
        """Spelling-hint profile for the meeting ('' = every term). Applied from the next line, also mid-meeting."""
        name = str(name or "").strip()
        profiles = live_subs.parse_glossary_profiles(self._read_file("glossary.txt"))[1]
        match = next((p for p in profiles if p.lower() == name.lower()), "")
        if name and not match:
            return {"ok": False, "profile": self._settings.get("vocab_profile", "")}
        with self._settings_lock:
            self._settings["vocab_profile"] = match
            config.save_settings(self._settings)
        os.environ["DOLMI_VOCAB_PROFILE"] = match
        if self._engine:
            self._engine.reload_terms()
        return {"ok": True, "profile": match}

    # ------------------------------------------------------------------ VOC-9 clarify queue
    CLARIFY_IGNORE = "clarify_ignore.txt"   # words the user said never to ask about again (config folder)

    def _vocab_rows(self):
        return _vocab_items(self._read_file("vocabulary.txt"), self._read_file("glossary.txt"))

    def _clarify_ignored(self):
        return [w.strip() for w in self._read_file(self.CLARIFY_IGNORE).splitlines() if w.strip()]

    def clarify_list(self):
        """The last meeting's unsure words: {on, items: [{heard, suggest, group, known, context, count}]}."""
        q = getattr(self, "_clarify", None)
        return {"on": bool(self._settings.get("clarify_queue")),
                "items": [{k: e[k] for k in ("heard", "suggest", "group", "known", "context", "count")}
                          for e in (q.items() if q else [])]}

    def clarify_add(self, heard, group, term, target=""):
        """Answer one question: add `term` (and `target` for German -> English / Fix English) to `group`
        through save_vocab, then drop the question."""
        try:
            vocabulary, glossary = clarify.add_rule(self._read_file("vocabulary.txt"), self._read_file("glossary.txt"),
                                                    str(group), str(term), str(target or ""))
        except clarify.Exists:
            return {"ok": False, "error": f"'{str(term).strip()}' already has a rule there. Change it in the editor below."}
        except ValueError:
            return {"ok": False, "error": "Pick a group and type the term (and its English for a rule)."}
        if vocabulary is None and glossary is None:      # already in that group: nothing to write
            self.clarify_skip(heard)
            return {"ok": True, "already": True}
        res = self.save_vocab(vocabulary, glossary)
        self.clarify_skip(heard)
        return res

    def clarify_skip(self, heard, forever=False):
        """Drop one question; forever=True also remembers the word so it is never asked again."""
        q = getattr(self, "_clarify", None)
        if q:
            q.drop(str(heard))
        if forever and str(heard).strip():
            words = self._clarify_ignored()
            if str(heard).strip() not in words:
                _replace_text(config.CONFIG / self.CLARIFY_IGNORE, "\n".join(words + [str(heard).strip()]) + "\n")
        return {"ok": True}

    def _vocab_mtimes(self):
        out = {}
        for name in ("vocabulary.txt", "glossary.txt"):
            try:
                out[name] = (config.CONFIG / name).stat().st_mtime_ns
            except OSError:
                out[name] = None
        return out

    def _vocab_check(self):
        """Reload the engine's rules when either file changed on disk since the last look (hand edits)."""
        with self._vocab_lock:
            now = self._vocab_mtimes()
            changed, self._vocab_seen = now != getattr(self, "_vocab_seen", now), now
        if changed and self._engine:
            print("Vocabulary files changed on disk; reloading")
            self._engine.reload_terms()
        return changed

    def _vocab_watch_loop(self):
        with self._vocab_lock:
            self._vocab_seen = self._vocab_mtimes()
        while True:
            time.sleep(2)
            try:
                self._vocab_check()
            except Exception as e:   # keep watching; a bad edit must not stop later reloads
                print(f"Vocabulary reload failed: {e}")

    # ------------------------------------------------------------------ assistant + inbox
    def ask(self, question):
        question = (question or "").strip()
        if not question:
            return -1
        qid = next(self._ids)
        with self._hist_lock:
            lines = [self._english(heard, sub) for _, heard, sub, *_ in self._history[-9:]]
            meeting = [line for line in lines if line != question][-8:]
        self._asks[qid] = {"q": question, "meeting": meeting}
        self._ask_q.put(qid)
        return qid

    def _answer_loop(self):
        db = None                                  # this thread's own sqlite connection
        while True:
            qid = self._ask_q.get()
            item = self._asks.pop(qid, None)
            if not item:
                continue
            try:
                self._ui_q.put(("ask_start", qid, ""))
                answer, err = "", ""
                try:
                    history = list(self._ai_history)
                    for piece in assistant.stream_answer(
                            self._provider(), self._ai_model(), self._ai_key(), item["q"],
                            context=self._settings["ai_context"], history=history,
                            length=self._settings["ai_length"], meeting=item["meeting"]):
                        answer += piece
                        self._ui_q.put(("ask_chunk", qid, piece))
                    self._ai_history = (self._ai_history + [
                        {"role": "user", "content": item["q"]},
                        {"role": "assistant", "content": answer.strip()}])[-assistant.HISTORY_MESSAGES:]
                except assistant.AssistantError as e:
                    err = str(e)
                except Exception as e:
                    err = f"Unexpected error: {e}"
                self._ui_q.put(("ask_done", qid, err))
                if self._settings["save_transcripts"] and (answer or err):
                    if db is None:
                        db = inbox.connect(self._db_path)
                    if self._chat_id is None:
                        self._chat_id = inbox.start_chat(db, self._provider(), self._ai_model())
                    inbox.add_message(db, self._chat_id, item["q"], answer, err, "typed")
                    self._emit("onChatsChanged", self._chat_id)
            except Exception as e:                 # never let one bad answer kill the loop
                print(f"answer loop: {e}")

    def _with_db(self, fn):
        """Run fn(db) on a short-lived connection (js_api calls arrive on varying threads)."""
        db = inbox.connect(self._db_path)
        try:
            return fn(db)
        finally:
            db.close()

    def list_chats(self, search="", archived=False):
        arch = self._archived()["chats"]

        def q(db):
            out = []
            for c in inbox.list_chats(db, search or ""):
                if (c["id"] in arch) != bool(archived):
                    continue
                try:
                    when = f"{inbox.local(c['started_at']):%a %d %b, %H:%M}"
                except ValueError:
                    when = c["started_at"]
                out.append({"id": c["id"], "title": c["title"] or "Conversation", "when": when,
                            "questions": c["questions"]})
            return out
        try:
            return self._with_db(q)
        except Exception as e:
            print(f"list_chats failed: {e}")
            return []

    def open_chat(self, chat_id):
        """Show a saved chat and continue it: later questions are added to it, with its Q&A as memory."""
        def q(db):
            return [{"q": m["question"], "a": m["answer"], "err": m["error"],
                     "when": f"{inbox.local(m['asked_at']):%H:%M}"} for m in inbox.messages(db, int(chat_id))]
        try:
            msgs = self._with_db(q)
        except Exception as e:
            print(f"open_chat failed: {e}")
            return []
        self._chat_id = int(chat_id)
        memory = []
        for m in msgs:
            memory += [{"role": "user", "content": m["q"]}, {"role": "assistant", "content": m["a"]}]
        self._ai_history = memory[-assistant.HISTORY_MESSAGES:]
        return msgs

    def new_chat(self):
        self._chat_id, self._ai_history = None, []
        return True

    def delete_chat(self, chat_id):
        try:
            self._with_db(lambda db: inbox.delete_chat(db, int(chat_id)))
        except Exception as e:
            print(f"delete_chat failed: {e}")
        if self._chat_id == int(chat_id):
            self.new_chat()
        self.archive_chat(chat_id, False)
        return True

    # ------------------------------------------------------------------ summary
    def summarize(self, meeting=None):
        """Summarize a saved meeting (Meetings tab passes its file name) or, with no name, the live
        session. The summary file is named after its meeting so the two pair up."""
        if meeting:
            pairs = self.read_meeting(meeting)
            if not pairs:
                return {"ok": False, "message": "That meeting has no lines to summarize."}
            lines = [f"[{p['ts']}] {self._english(p['de'], p['en'])}" for p in pairs]
            stamp = str(meeting).removeprefix("meeting_").removesuffix(".md")
            meeting_md = self._meeting_path(meeting)
        else:
            with self._hist_lock:
                history = list(self._history)
            if not history:
                return {"ok": False, "message": "Nothing to summarize yet — run a live session first."}
            lines = [f"[{t}] {self._english(heard, sub)}" for t, heard, sub, *_ in history]
            tr = self._last_transcript
            stamp = tr.md.stem.removeprefix("meeting_") if tr and tr.md else f"{datetime.now():%Y-%m-%d_%H-%M}"
            meeting_md = tr.md if tr and tr.md else None
        if not self._ai_key():
            return {"ok": False, "message": "Add an API key in Settings → Assistant first."}
        transcript = "\n".join(lines)
        provider, model, key, context = self._provider(), self._ai_model(), self._ai_key(), self._settings["ai_context"]
        out_file = self._data / f"summary_{stamp}.md"

        def work():
            text, err = "", ""
            try:
                for piece in assistant.stream_summary(provider, model, key, transcript, context):
                    text += piece
                    self._ui_q.put(("sum_chunk", piece, ""))
            except assistant.AssistantError as e:
                err = str(e)
            except Exception as e:
                err = f"Unexpected error: {e}"
            if not text.strip() and not err:
                err = "The AI returned an empty summary."
            notes = None if err else assistant.parse_summary(text)
            if notes:          # APP-10: structured cards + ready-to-paste texts; else the text as it came
                notes = {**notes, "email": assistant.summary_email(notes), "teams": assistant.summary_teams(notes)}
            if text.strip() and not err and self._settings["save_transcripts"]:
                try:
                    out_file.write_text(assistant.summary_markdown(notes) if notes else text.strip() + "\n",
                                        encoding="utf-8")
                    if notes and notes["title"] and meeting_md:
                        self._save_summary_title(meeting_md, notes["title"])
                except OSError as e:
                    print(f"summary save failed: {e}")
            self._ui_q.put(("sum_done", err, notes))

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    def _save_summary_title(self, meeting_md, title):
        """Keep the summary's title in the meeting's sidecar (APP-9), beside a title the user typed."""
        meeting_md = Path(meeting_md)
        meta = self._meeting_meta(meeting_md)
        meta["summary_title"] = title[:TITLE_EDIT_MAX]
        side = meeting_md.with_suffix(".json")
        tmp = side.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, side)

    # ------------------------------------------------------------------ report a problem (APP-12)
    def _report_files(self, include_transcripts=False, include_vocabulary=False):
        """{name in the zip: text}: info, scrubbed settings, the log; transcripts/vocabulary only if asked."""
        import platform
        s = self._settings
        info = {"app_version": VERSION, "os": platform.platform(), "python": platform.python_version(),
                "machine": platform.machine(), "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "speech_model": s.get("model"), "device": s.get("device"), "language": s.get("language"),
                "speech_source": s.get("speech_source"), "translator": s.get("translator"),
                "ai_provider": s.get("ai_provider"), "ai_model": self._ai_model(),
                "included": {"transcripts": bool(include_transcripts), "vocabulary": bool(include_vocabulary)}}
        files = {"info.json": json.dumps(info, indent=2),
                 "settings.json": json.dumps(scrub_settings(s), indent=2, ensure_ascii=False)}
        log = Path(config.LOGS) / "dolmi.log"
        try:
            with open(log, "rb") as f:
                f.seek(max(0, log.stat().st_size - REPORT_LOG_MAX))
                files["dolmi.log"] = redact_text(f.read().decode("utf-8", "replace"))
        except OSError:
            files["dolmi.log"] = "(no log file: Dolmi writes one only when it runs without a console)"
        if include_transcripts:
            for p in sorted(self._data.glob("meeting_*.md")) + sorted(self._data.glob("summary_*.md")):
                try:
                    files[f"transcripts/{p.name}"] = redact_text(p.read_text(encoding="utf-8"))
                except OSError as e:
                    print(f"report: could not read {p.name}: {e}")
        if include_vocabulary:
            for name in ("vocabulary.txt", "glossary.txt"):
                p = Path(config.CONFIG) / name
                if p.is_file():
                    files[f"vocabulary/{name}"] = p.read_text(encoding="utf-8")
        return files

    def report_problem(self, include_transcripts=False, include_vocabulary=False):
        """Save a zip for a bug report wherever the user picks. Nothing is sent anywhere."""
        import zipfile
        try:
            import webview
            downloads = Path.home() / "Downloads"
            chosen = self._window.create_file_dialog(
                webview.FileDialog.SAVE, directory=str(downloads if downloads.is_dir() else self._data),
                save_filename=f"dolmi-report-{datetime.now():%Y-%m-%d_%H-%M}.zip")
        except Exception as e:
            return {"ok": False, "message": f"Could not open the save dialog: {e}"}
        if isinstance(chosen, (list, tuple)):
            chosen = chosen[0] if chosen else None
        if not chosen:
            return {"ok": False, "cancelled": True}
        target = Path(chosen)
        if target.suffix.lower() != ".zip":
            target = target.with_name(target.name + ".zip")
        try:
            files = self._report_files(bool(include_transcripts), bool(include_vocabulary))
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
                for name, text in files.items():
                    z.writestr(name, text)
        except OSError as e:
            print(f"report_problem: could not write {target}: {e}")
            return {"ok": False, "message": f"Could not save the report: {e}"}
        return {"ok": True, "path": str(target), "files": sorted(files)}

    # ------------------------------------------------------------------ global shortcuts (APP-7)
    def _shortcut_map(self):
        """{action: combo}: the saved ones that are still valid, defaults for the rest."""
        saved = self._settings.get("shortcuts")
        saved = saved if isinstance(saved, dict) else {}
        out = {}
        for name, default in DEFAULT_SHORTCUTS.items():
            combo = normalize_shortcut(saved.get(name)) if name in saved else default
            out[name] = combo or default
        return out

    def _start_shortcuts(self):
        """(Re-)register the global shortcuts with the system (main, after the window is up; and after
        every change). Off: none are registered."""
        old, self._shortcuts = self._shortcuts, False      # False = started but none registered
        if old:
            try:
                old.stop()
            except Exception as e:
                print(f"shortcuts stop: {e}")
        self._shortcut_status = {}
        if not self._settings.get("shortcuts_on", True) or self._shortcuts_paused:
            return {}
        bindings = {name: _shortcut_parts(combo) for name, combo in self._shortcut_map().items()}
        try:
            self._shortcuts = dolmi_platform.start_shortcuts(bindings, self._on_shortcut)
            self._shortcut_status = dict(self._shortcuts.status)
        except Exception as e:
            print(f"Global shortcuts unavailable: {e}")
        return dict(self._shortcut_status)

    def _on_shortcut(self, name):
        """A global shortcut was pressed (on the platform's thread). The lock is switched here; the others
        go to the page, which presses its own button, so the screen and the missing-model prompt stay right."""
        if name == "toggle_lock":
            self.toggle_bar_lock()
        elif name == "hold_original":
            self._voice_hold_original()
        elif name in DEFAULT_SHORTCUTS:
            self._emit("onShortcut", name)

    def pause_shortcuts(self, paused=True):
        """While the page records a new key, Dolmi's own shortcuts are let go: a registered combo is taken
        by the system before the page gets the key press, so the recorder would never see it."""
        paused = bool(paused)
        if paused != self._shortcuts_paused:
            self._shortcuts_paused = paused
            if self._shortcuts is not None:
                self._start_shortcuts()
        return {"ok": True, "status": dict(self._shortcut_status)}

    def set_shortcut(self, name, combo):
        """Change one shortcut ('' = back to its default). Refuses everyday keys, a key another action
        has, and Dolmi's own show-Dolmi key."""
        if name not in DEFAULT_SHORTCUTS:
            return {"ok": False, "message": "Unknown shortcut."}
        combo = DEFAULT_SHORTCUTS[name] if combo in ("", None) else normalize_shortcut(combo)
        if not combo:
            return {"ok": False, "message": "Use Ctrl+Alt+Shift (or Win/Cmd+Alt) with a letter or digit, or two of Ctrl, Alt, Shift, Win/Cmd with an F-key."}
        if combo in RESERVED_SHORTCUTS:
            return {"ok": False, "message": f"That one is for {RESERVED_SHORTCUTS[combo]}."}
        current = self._shortcut_map()
        clash = next((n for n, c in current.items() if c == combo and n != name), None)
        if clash:
            return {"ok": False, "message": "Another Dolmi shortcut uses that already.", "clash": clash}
        current[name] = combo
        with self._settings_lock:
            self._settings["shortcuts"] = {n: c for n, c in current.items() if c != DEFAULT_SHORTCUTS[n]}
            config.save_settings(self._settings)
        if self._shortcuts is not None:
            self._start_shortcuts()
        return {"ok": True, "shortcuts": self._shortcut_map(), "status": dict(self._shortcut_status)}

    # ------------------------------------------------------------------ update check (APP-6)
    def _start_update_check(self):
        """Called once by main when the window is up: ask GitHub in the background (when allowed)."""
        if self._settings.get("check_updates", True):
            threading.Thread(target=self.check_update, name="dolmi-update", daemon=True).start()

    def check_update(self, force=False):
        """Is a newer Dolmi out? {"available", "version", "url", "current"}. Skipped (available False,
        "off" True) when the setting is off, unless the user pressed "Check now" (force)."""
        if not force and not self._settings.get("check_updates", True):
            return {"available": False, "version": "", "url": updates.RELEASES_PAGE, "current": VERSION, "off": True}
        result = updates.check(VERSION)
        self._update = result
        if result["available"]:
            self._emit("onUpdate", result)
        return result

    def open_release(self, url=None):
        """'Download x.y.z': open the release page in the browser (only this repository's release pages)."""
        url = url or updates.RELEASES_PAGE
        if not updates.is_release_page(url):
            return False
        import webbrowser
        return bool(webbrowser.open(url))

    # ------------------------------------------------------------------ overlay (lazy caption bar)
    def show_overlay(self):
        if not self._overlay:
            from ui.overlay import Overlay
            ov = Overlay(background="#16150F", on_hide=self._on_overlay_hidden, on_toggle=self.toggle_source,
                         place=self._bar_start_place())
            self._overlay = ov

            def loaded(*_):
                self._overlay_ready = True
                self._stealth_overlay()
                self._push_overlay_style()
                lab = self._source_label()
                self._ov("source", lab["source"], lab["device"])
                self._ov("locked", bool(self._settings.get("bar_locked")))

            def shown(*_):
                self._stealth_overlay()

            try:
                ov.window.events.loaded += loaded
                ov.window.events.shown += shown
                ov.window.events.moved += lambda *a: self._bar_moved()
                ov.window.events.resized += lambda *a: self._bar_moved()
            except Exception as e:
                print(f"overlay events: {e}")
        else:
            self._overlay.show()
            self._stealth_overlay()
        self._overlay_visible = True
        self._ov("locked", bool(self._settings.get("bar_locked")))   # it may have changed while hidden
        return True

    def hide_overlay(self):
        if self._overlay:
            self._overlay.hide()
        self._overlay_visible = False
        return True

    def _push_overlay_style(self):
        s = self._settings
        self._ov("style", float(s.get("opacity", 0.88)), int(s.get("font", 22)))

    def _on_overlay_hidden(self):
        """The bar's own ✕ was pressed: keep the main window's switch in sync."""
        self._overlay_visible = False
        self._emit("onBar", False)

    def _stealth_overlay(self):
        """No taskbar button (Windows) / on every Space (Mac); follows invisible mode for capture; and the
        click-through lock (a style change can reset it, so it is applied after the others)."""
        dolmi_platform.overlay_stealth(self)
        if self._overlay:
            dolmi_platform.set_click_through(self._overlay.window, bool(self._settings.get("bar_locked")))

    # ------------------------------------------------------------------ bar lock and place (APP-8)
    def set_bar_locked(self, on):
        """Lock the subtitle bar: it ignores the mouse, so clicks go to the meeting window below. Unlocked
        from Dolmi (Settings > Subtitle bar) or the shortcut, since the bar itself can't be clicked then."""
        on = bool(on)
        with self._settings_lock:
            self._settings["bar_locked"] = on
            config.save_settings(self._settings)
        ok = True
        if self._overlay:
            ok = dolmi_platform.set_click_through(self._overlay.window, on)
        self._ov("locked", on)
        self._emit("onBarLock", on)
        return {"locked": on, "applied": bool(ok)}

    def toggle_bar_lock(self):
        return self.set_bar_locked(not self._settings.get("bar_locked"))

    @staticmethod
    def _screens():
        try:
            import webview
            return list(webview.screens)
        except Exception as e:
            print(f"screens: {e}")
            return []

    def _bar_start_place(self):
        """The bar's saved place for a connected screen (see _bar_place), or None for the default."""
        near = None
        try:
            w = self._window
            if w and w.width:
                near = (w.x + w.width / 2, w.y + w.height / 2)
        except Exception:
            pass
        s = self._settings
        return _bar_place(s.get("bar_positions"), s.get("bar_screen"), self._screens(), near)

    def _bar_moved(self):
        """The bar moved or was resized: save its place for that screen once it stops (debounced)."""
        old = getattr(self, "_bar_timer", None)
        if old:
            old.cancel()
        self._bar_timer = threading.Timer(BAR_SAVE_DELAY_S, self._save_bar_place)
        self._bar_timer.daemon = True
        self._bar_timer.start()

    def _save_bar_place(self):
        """Remember where the bar is, keyed by the screen it is on. Returns that screen's key."""
        ov = self._overlay
        if not ov:
            return None
        try:
            w = ov.window
            p = {"x": int(w.x), "y": int(w.y), "w": int(w.width), "h": int(w.height)}
        except Exception as e:
            print(f"bar place: {e}")
            return None
        screen = _screen_at(p["x"] + p["w"] / 2, p["y"] + p["h"] / 2, self._screens())
        if not screen or not _valid_place(p):
            return None
        key = _screen_key(screen)
        with self._settings_lock:
            positions = dict(self._settings.get("bar_positions") or {})
            positions[key] = p
            self._settings["bar_positions"], self._settings["bar_screen"] = positions, key
            config.save_settings(self._settings)
        return key

    # ------------------------------------------------------------------ invisible mode
    def toggle_invisible(self, on):
        on = bool(on)
        with self._settings_lock:
            self._settings["invisible"] = on
            config.save_settings(self._settings)
        dolmi_platform.apply_invisible(self, on)
        return on

    def _bring_to_front(self):
        """A second Dolmi was launched (APP-5): show this window instead (invisible mode stays as it is)."""
        w = self._window
        if not w:
            return
        try:
            w.show()
            w.restore()
        except Exception as e:
            print(f"show window: {e}")
        dolmi_platform.bring_to_front(w)

    def recover(self):
        """Global-hotkey escape hatch: invisible off, window back, page switches in sync."""
        self.toggle_invisible(False)
        self._emit("onInvisible", False)
        try:
            self._window.show()
            self._window.restore()
        except Exception:
            pass
