"""'English only' routing on Windows (VOICE-5, experimental, off by default).

Goal: the user hears only Dolmi's English voice while Dolmi still captures the German meeting audio.
Route picked by the VOICE-4 spike (design notes: research/voice-brainstorm.md, 'Spike results'):
WASAPI process loopback keeps capturing an app at full level when the speaker it plays on is muted or
at volume 0, but a muted audio *session* (volume mixer mute) is silent in the capture too. So:

  1. the meeting apps (Teams incl. its WebView2, Zoom incl. CptHost, Chrome, Edge) are routed with
     IAudioPolicyConfig (as EarTrumpet / SoundSwitch do) to an "unheard" speaker the user picks,
  2. that speaker is muted (its old mute/volume are saved),
  3. Dolmi captures the apps with process loopback (include tree) and speaks English on the headphones.

Everything changed is first written to a journal in %APPDATA%\\Dolmi (audio-routing.json) and undone on
Stop/exit, at the next launch after a crash, and by Settings > Audio > Restore audio. When the unheard
speaker is missing or is the one the user listens on, nothing is rerouted:
the meeting stays audible and Settings says why (Router.notice). Turning the German
down instead (ducking) is VOICE-7.

The Windows calls live behind a small policy object (WinPolicy, using voice/win_audio.py), so the logic
is unit-tested with a fake one (tests/test_voice_routing.py) and never touches real audio there.
"""
import json
import os
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path

JOURNAL = "audio-routing.json"
TICK_S = 2.0                  # re-scan for new meeting processes / device changes

# exe name -> family; a "child" exe only counts when an ancestor belongs to a family (WebView2 is used by
# many apps, but only Teams' copy carries the meeting audio).
FAMILIES = {
    "teams": {"ms-teams.exe", "teams.exe"},
    "zoom": {"zoom.exe", "cpthost.exe", "zoomwebviewhost.exe"},
    "chrome": {"chrome.exe"},
    "edge": {"msedge.exe"},
}
CHILD_ONLY = {"msedgewebview2.exe"}

FALLBACK_NO_DEVICE = "English only: pick a second speaker to send the meeting to. You still hear the meeting."
FALLBACK_SAME_DEVICE = ("English only: the meeting can't go to the speaker you listen on. "
                        "You still hear the meeting.")
FALLBACK_LOST = "English only: the speaker for the meeting is gone. Audio restored; you hear the meeting again."
FALLBACK_ERROR = "English only could not change the audio routing. You still hear the meeting."


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def meeting_processes(procs, families=None):
    """[(pid, exe, family)] of meeting-app processes (and their children) in a process list
    [(pid, parent pid, exe)]."""
    families = FAMILIES if families is None else families
    by_pid = {pid: (ppid, exe) for pid, ppid, exe in procs}
    fam_of = {exe: f for f, exes in families.items() for exe in exes}

    def family(pid, depth=0):
        if pid not in by_pid or depth > 32:
            return None
        ppid, exe = by_pid[pid]
        if exe in fam_of:
            return fam_of[exe]
        return family(ppid, depth + 1) if ppid != pid else None

    out = []
    for pid, ppid, exe in procs:
        f = fam_of.get(exe) or (family(ppid) if exe in CHILD_ONLY else None)
        if f:
            out.append((pid, exe, f))
    return out


class WinPolicy:
    """The real Windows side (voice/win_audio.py). All methods may raise OSError."""

    def __init__(self):
        from voice import win_audio
        self._wa = win_audio
        self._cfg = win_audio.PolicyConfig()

    def devices(self):
        return self._wa.render_devices()

    def processes(self):
        return self._wa.processes()

    def get_route(self, pid):
        return self._cfg.get(pid)

    def set_route(self, pid, device_id):
        self._cfg.set(pid, device_id)

    def endpoint(self, device_id):
        v = self._wa.EndpointVolume(device_id)
        return {"muted": v.muted, "level": v.level}

    def set_endpoint(self, device_id, muted=None, level=None):
        v = self._wa.EndpointVolume(device_id)
        if level is not None:
            v.level = level
        if muted is not None:
            v.muted = muted


class Router:
    """Applies and undoes the English-only route. `policy` is a WinPolicy (or a fake in tests)."""

    def __init__(self, policy, journal_dir, log=print):
        self.policy, self.log = policy, log
        self.path = Path(journal_dir) / JOURNAL
        self.target = ""
        self.active = False
        self.notice = ""             # shown in Settings when English only fell back (meeting stays audible)
        self._lock = threading.RLock()

    # ---------------------------------------------------------------- journal
    def _load(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as e:
            self.log(f"routing: journal {self.path} unreadable ({e}); keeping it for a manual restore")
            return None

    def _save(self, j):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(j, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def pending(self):
        return self.path.exists()

    # ---------------------------------------------------------------- apply / restore
    def apply(self, target, listen_device=""):
        """Route the meeting apps to `target` and mute it. Returns True when routed; False = not
        routed (self.notice says why). `listen_device` is where the user hears Dolmi ('' = default)."""
        with self._lock:
            try:
                devices = self.policy.devices()
            except OSError as e:
                return self._fallback(FALLBACK_ERROR, e)
            ids = {d["id"] for d in devices}
            listen = listen_device or next((d["id"] for d in devices if d.get("default")), "")
            if not target or target not in ids:
                return self._fallback(FALLBACK_NO_DEVICE)
            if target == listen:
                return self._fallback(FALLBACK_SAME_DEVICE)
            j = self._load() or {"version": 1, "saved_utc": utc_now(), "apps": {}, "endpoint": None}
            try:
                if j.get("endpoint") is None:
                    j["endpoint"] = {"id": target, **self.policy.endpoint(target)}
                self._save(j)                # before anything changes
                self.target, self.active, self.notice = target, True, ""
                self._route_new(j)
                self.policy.set_endpoint(target, muted=True)
            except OSError as e:
                self.restore()
                return self._fallback(FALLBACK_ERROR, e)
            self.log(f"routing: meeting apps -> {target} (muted)")
            return True

    def _fallback(self, notice, err=None):
        self.active, self.notice = False, notice
        self.log(f"routing: not routed ({notice}{f'; {err}' if err else ''})")
        return False

    def _route_new(self, j):
        """Route every meeting process not routed yet; the original route of each exe is journaled first."""
        changed = False
        for pid, exe, _ in meeting_processes(self.policy.processes()):
            entry = j["apps"].get(exe)
            if entry is None:
                j["apps"][exe] = entry = {"original": self.policy.get_route(pid), "pids": []}
                self._save(j)
            if pid in entry["pids"]:
                continue
            self.policy.set_route(pid, self.target)
            entry["pids"].append(pid)
            changed = True
        if changed:
            self._save(j)

    def restore(self):
        """Undo everything in the journal. Apps that are not running now keep their entry (Windows needs
        a running process to change its route) and are retried by tick()/the next restore. Returns True
        when nothing is left to restore."""
        with self._lock:
            self.active = False
            j = self._load()
            if j is None:
                return not self.path.exists()
            running = {}
            try:
                for pid, _ppid, exe in self.policy.processes():
                    running.setdefault(exe, pid)
            except OSError as e:
                self.log(f"routing: cannot list processes for restore ({e})")
            for exe in list(j.get("apps", {})):
                pid = running.get(exe)
                if pid is None:
                    continue
                try:
                    original = j["apps"][exe]["original"]
                    if self.policy.get_route(pid) != original:
                        self.policy.set_route(pid, original)
                    del j["apps"][exe]
                except OSError as e:
                    self.log(f"routing: restore of {exe} failed ({e})")
            ep = j.get("endpoint")
            if ep:
                try:
                    self.policy.set_endpoint(ep["id"], muted=ep["muted"], level=ep["level"])
                    j["endpoint"] = None
                except OSError as e:
                    self.log(f"routing: restore of speaker {ep['id']} failed ({e})")
            if j.get("apps") or j.get("endpoint"):
                self._save(j)
                self.log(f"routing: still to restore: {sorted(j.get('apps', {}))}")
                return False
            self.path.unlink(missing_ok=True)
            self.log("routing: audio restored")
            return True

    def tick(self, listen_device=""):
        """Every TICK_S: route new meeting processes (Teams/Chrome start helper processes during a call),
        re-apply after a device change, or restore + fall back when the unheard speaker is gone; when
        inactive, finish a restore that was waiting for an app to run."""
        with self._lock:
            if not self.active:
                if self.pending():
                    self.restore()
                return
            try:
                ids = {d["id"] for d in self.policy.devices()}
            except OSError:
                return
            if self.target not in ids:
                self.restore()
                self._fallback(FALLBACK_LOST)
                return
            j = self._load() or {"version": 1, "saved_utc": utc_now(), "apps": {}, "endpoint": None}
            try:
                self._route_new(j)
                if not self.policy.endpoint(self.target)["muted"]:
                    self.policy.set_endpoint(self.target, muted=True)   # e.g. Windows reset it on replug
            except OSError as e:
                self.log(f"routing: re-apply failed ({e})")

    def capture_pids(self):
        """Roots of the running meeting-app trees, for process loopback."""
        procs = self.policy.processes()
        found = meeting_processes(procs)
        fam = {pid for pid, _, _ in found}
        parent = {pid: ppid for pid, ppid, _ in procs}
        return sorted(pid for pid in fam if parent.get(pid) not in fam)


def process_loopback_stream(out_q: queue.Queue, stop: threading.Event, pids, on_level=None,
                            loopback_cls=None):
    """Capture the given process trees (WASAPI process loopback), mix them and put 16 kHz mono float32
    chunks (numpy) into out_q until stop is set, like dolmi_platform.loopback_stream."""
    import numpy as np
    if loopback_cls is None:
        from voice.win_audio import ProcessLoopback as loopback_cls
    if not pids:
        raise OSError("no meeting app is running")
    caps = [loopback_cls(pid, include_tree=True, rate=16000, channels=1) for pid in pids]
    try:
        while not stop.is_set():
            parts = [np.frombuffer(c.read(0.1 / len(caps)), dtype=np.int16) for c in caps]
            n = max((len(p) for p in parts), default=0)
            if not n:
                continue
            mix = np.zeros(n, dtype=np.float32)
            for p in parts:
                mix[: len(p)] += p.astype(np.float32) / 32768.0
            np.clip(mix, -1.0, 1.0, out=mix)
            out_q.put(mix)
            if on_level:
                on_level(float(np.sqrt(np.mean(mix * mix))))
    finally:
        for c in caps:
            c.close()


class Watcher:
    """Calls router.tick() every TICK_S on a daemon thread until stop()."""

    def __init__(self, router, listen_device="", interval=TICK_S):
        self.router, self.listen, self.interval = router, listen_device, interval
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True, name="routing-watch")

    def start(self):
        self._t.start()
        return self

    def _run(self):
        from voice import win_audio
        win_audio.com_init()
        while not self._stop.wait(self.interval):
            try:
                self.router.tick(self.listen)
            except Exception as e:      # keep watching; a failed tick is retried
                self.router.log(f"routing: tick failed ({e})")

    def stop(self):
        self._stop.set()


def available():
    try:
        from voice import win_audio
        return win_audio.available()
    except ImportError:
        return False

