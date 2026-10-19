"""Dolmi webview UI — the JS bridge (Api exposed as window.pywebview.api).

Reuses the existing core (live_subs, assistant, inbox, config) untouched. Python pushes to the page by
calling window.dolmi.<fn>(...) through evaluate_js; the page calls Python through
window.pywebview.api.<method>.

All state is held in underscore attributes on purpose: pywebview exposes every *public* attribute of
the js_api object to page JavaScript (recursively), which would hand the page the window object, the
Whisper model, the settings with the encrypted keys, and the database handle.
"""
import itertools
import json
import os
import queue
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import config
import assistant
import inbox
import live_subs
import cloud_speech
import dolmi_platform

VERSION = config.version()
DEFAULT_SPEAKER = dolmi_platform.DEFAULT_SPEAKER   # what "Speaker" listens to when no device is picked
ICON = dolmi_platform.app_icon(config.ROOT)
_MEETING_RE = re.compile(r"\*\*(.+?)\*\*\s+(.*?)\s*\n<sub>(.*?)</sub>", re.S)
# settings the page may change through set_setting (provider, keys and invisible have their own calls)
SPEECH_SOURCES = ("local", *cloud_speech.PROVIDERS)   # Settings → Captions by
SETTABLE = {"speech_source", "mic_device", "language", "show_german", "save_transcripts", "keep_days", "audio_device", "model",
            "overlay", "theme", "ai_length", "ai_context", "opacity", "font"}


def _js(v):
    return json.dumps(v)


def _spoken_language_ok(code):
    import models
    return code == "auto" or code in models.LANGUAGES


class Api:
    def __init__(self):
        config.prepare()
        config.load_env()
        self._settings = config.load_settings()
        if not _spoken_language_ok(self._settings["language"]):
            self._settings["language"] = "de"
        self._settings_lock = threading.Lock()
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
        self._session = None                 # {"token", "stop", "transcript"} while starting/listening
        self._lock = threading.Lock()        # guards session transitions
        self._history = []                   # [(hh:mm:ss, de, en)] this session
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
        self._ask_q = queue.Queue()
        self._ov_q = queue.Queue()
        for loop in (self._pump, self._answer_loop, self._level_loop, self._overlay_loop):
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

    def _pump(self):
        """Drain ui_q (fed by the worker and answer threads) and push to the page."""
        while True:
            kind, a, b = self._ui_q.get()
            try:
                if kind in ("final", "draft") and not self._session:
                    continue                          # late result after Stop
                if kind == "error":
                    self._emit("onError", a)
                elif kind == "final":
                    with self._hist_lock:
                        self._history.append((datetime.now().strftime("%H:%M:%S"), a, b))
                    self._emit("onLine", datetime.now().strftime("%H:%M"), a, b)
                    self._ov("line", a, b)
                elif kind == "draft":
                    self._emit("onDraft", a, b)
                    self._ov("draft", a, b)
                elif kind == "ask_start":
                    self._emit("onAsk", a)
                elif kind == "ask_chunk":
                    self._emit("onAnswerChunk", a, b)
                elif kind == "ask_done":
                    self._emit("onAnswerDone", a, b)
                elif kind == "sum_chunk":
                    self._emit("onSummaryChunk", a)
                elif kind == "sum_done":
                    self._emit("onSummaryDone", a)
            except Exception as e:
                print(f"pump {kind} failed: {e}")

    def _level_loop(self):
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
            "barVisible": bool(self._overlay_visible),
            "engine": self._engine_label(), "dataFolder": str(self._data), "version": VERSION,
            "platform": {"os": dolmi_platform.OS, "words": dolmi_platform.WORDS},
        }

    def set_setting(self, key, value):
        if key not in SETTABLE:
            return False
        if key == "language" and not _spoken_language_ok(value):
            return False
        if key == "speech_source" and value not in SPEECH_SOURCES:
            return False
        with self._settings_lock:
            self._settings[key] = value
            config.save_settings(self._settings)
        if key == "keep_days":
            threading.Thread(target=self._purge_old, daemon=True).start()
        if key == "mic_device" and self._session and self._source() == "mic" and getattr(self, "_cap_stop", None):
            self._cap_stop.set()
        if key in ("opacity", "font"):
            self._push_overlay_style()
        if key in ("model", "language", "speech_source") and not self._session:
            self._engine = None              # next Start loads the newly chosen model/translator
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
                self._emit("onEngine", "loading models…")
                watch = self._watch_downloads(self._needed_models())
                source = self._settings.get("speech_source", "local")
                try:
                    if source == "local":
                        self._engine = live_subs.Engine(self._settings["model"], self._settings["device"],
                                                        self._settings["language"])
                    else:
                        # captions use the provider's fast speech model, not the Assistant's model
                        self._engine = cloud_speech.CloudEngine(source, self._key_for(source),
                                                                self._settings["language"])
                except Exception as e:
                    watch.set()
                    with self._lock:
                        if self._alive(token):
                            self._session = None
                    self._emit("onError", f"Could not start captions: {e}")
                    self._emit("onStopped")
                    return
                watch.set()
                self._emit("onEngine", self._engine_label())
        with self._lock:
            if not self._alive(token):                # stopped while the models loaded
                return
            self._engine.language = self._settings["language"]
            with self._hist_lock:
                self._history = []
            self._chat_id, self._ai_history = None, []
            stop, audio_q = threading.Event(), queue.Queue()
            tr = live_subs.Transcript(self._data, save=self._settings["save_transcripts"])
            self._last_transcript = tr
            self._session.update(stop=stop, transcript=tr)

        def capture():
            try:
                while not stop.is_set():
                    cap = threading.Event()        # set by stop_listening or a source switch
                    self._cap_stop = cap
                    if stop.is_set():
                        break
                    self._push_source()
                    if self._source() == "mic":
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
                         daemon=True).start()
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
        self._emit("onListening", False, tr.md.name if tr and tr.md else "")
        return True

    def _on_level(self, rms):
        self._level = rms

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
                self._emit("onEngine", f"downloading {m.name} — {mb:,.0f} of {m.size_mb:,} MB (first start only)")
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
            self._engine = None
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

    def list_meetings(self, archived=False):
        out, arch = [], self._archived()["meetings"]
        for p in sorted(self._data.glob("meeting_*.md"), reverse=True):
            if (p.name in arch) != bool(archived):
                continue
            try:
                dt = datetime.strptime(p.stem.removeprefix("meeting_"), "%Y-%m-%d_%H-%M")
                day, tm = f"{dt:%a %d %b %Y}", f"{dt:%H:%M}"
            except ValueError:
                day, tm = p.stem, ""
            try:
                lines = p.read_text(encoding="utf-8").count("<sub>")
            except OSError:
                lines = 0
            out.append({"name": p.name, "day": day, "time": tm, "lines": lines})
        return out

    def read_meeting(self, name):
        p = self._meeting_path(name)
        if not p or not p.exists():
            return []
        # Transcript.add writes "**HH:MM:SS** {en}  \n<sub>{de}</sub>" -> groups are (ts, en, de)
        return [{"ts": ts.strip(), "de": de.strip(), "en": en.strip()}
                for ts, en, de in _MEETING_RE.findall(p.read_text(encoding="utf-8"))]

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
            out += [f"[{x['ts']}]", f"Caption:     {x['de']}", f"Translation: {x['en']}", ""]
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
            for f in (p, p.with_suffix(".srt")):
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
        """Write only the files the page actually sent (None = leave that file alone)."""
        if vocabulary is not None:
            (config.CONFIG / "vocabulary.txt").write_text(vocabulary.rstrip() + "\n", encoding="utf-8")
        if glossary is not None:
            (config.CONFIG / "glossary.txt").write_text(glossary.rstrip() + "\n", encoding="utf-8")
        if self._engine:
            self._engine.reload_terms()
        return True

    # ------------------------------------------------------------------ assistant + inbox
    def ask(self, question):
        question = (question or "").strip()
        if not question:
            return -1
        qid = next(self._ids)
        with self._hist_lock:
            lines = [self._english(heard, sub) for _, heard, sub in self._history[-9:]]
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
        else:
            with self._hist_lock:
                history = list(self._history)
            if not history:
                return {"ok": False, "message": "Nothing to summarize yet — run a live session first."}
            lines = [f"[{t}] {self._english(heard, sub)}" for t, heard, sub in history]
            tr = self._last_transcript
            stamp = tr.md.stem.removeprefix("meeting_") if tr and tr.md else f"{datetime.now():%Y-%m-%d_%H-%M}"
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
            if text.strip() and self._settings["save_transcripts"]:
                try:
                    out_file.write_text(text.strip() + "\n", encoding="utf-8")
                except OSError as e:
                    print(f"summary save failed: {e}")
            self._ui_q.put(("sum_done", err, ""))

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    # ------------------------------------------------------------------ overlay (lazy caption bar)
    def show_overlay(self):
        if not self._overlay:
            from ui.overlay import Overlay
            ov = Overlay(background="#16150F", on_hide=self._on_overlay_hidden, on_toggle=self.toggle_source)
            self._overlay = ov

            def loaded(*_):
                self._overlay_ready = True
                self._stealth_overlay()
                self._push_overlay_style()
                lab = self._source_label()
                self._ov("source", lab["source"], lab["device"])

            def shown(*_):
                self._stealth_overlay()

            try:
                ov.window.events.loaded += loaded
                ov.window.events.shown += shown
            except Exception as e:
                print(f"overlay events: {e}")
        else:
            self._overlay.show()
            self._stealth_overlay()
        self._overlay_visible = True
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
        """No taskbar button (Windows) / on every Space (Mac); follows invisible mode for capture."""
        dolmi_platform.overlay_stealth(self)

    # ------------------------------------------------------------------ invisible mode
    def toggle_invisible(self, on):
        on = bool(on)
        with self._settings_lock:
            self._settings["invisible"] = on
            config.save_settings(self._settings)
        dolmi_platform.apply_invisible(self, on)
        return on

    def recover(self):
        """Global-hotkey escape hatch: invisible off, window back, page switches in sync."""
        self.toggle_invisible(False)
        self._emit("onInvisible", False)
        try:
            self._window.show()
            self._window.restore()
        except Exception:
            pass
