"""VOICE-4 routing spike (Windows): does WASAPI process loopback still capture an app at full level
when the app's output is silenced for the user?

    python -m voice.routing_spike [--seconds 1.5] [--json out.json]

A child process plays a 440 Hz test tone (winsound, so it follows the Windows default / per-app device
like a meeting app does). Dolmi-style process loopback (include tree) records that child while we try
each way of silencing it:
  baseline           nothing changed
  endpoint_volume_0  the speaker the tone plays on is set to volume 0
  endpoint_muted     that speaker is muted
  session_muted      only the tone's audio session is muted (ISimpleAudioVolume, = volume mixer mute)
  routed_other_0     the tone app is routed (IAudioPolicyConfig) to another speaker held at volume 0
                     (skipped when the PC has only one speaker)
  other_process      control: loopback of an idle process that plays nothing (must be silent)
Every change is undone in `finally`, and the script prints the restored state.
"""
import argparse
import array
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import wave
from datetime import datetime, timezone

from voice import win_audio as wa

TONE_HZ, TONE_AMP, RATE = 440, 0.25, 48000
CHILD = ("import sys, winsound; winsound.PlaySound(sys.argv[1], winsound.SND_FILENAME | winsound.SND_ASYNC"
         " | winsound.SND_LOOP); sys.stdin.read()")


def write_tone(path, seconds=1.0):
    n = int(RATE * seconds)
    samples = array.array("h", (int(32767 * TONE_AMP * math.sin(2 * math.pi * TONE_HZ * i / RATE))
                                for i in range(n)))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(samples.tobytes())


def dbfs(pcm: bytes):
    """RMS level of 16-bit PCM in dBFS (-inf for no audio); a full-scale sine is -3 dBFS."""
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) // 2 * 2])
    if not a:
        return float("-inf")
    rms = math.sqrt(sum(x * x for x in a) / len(a)) / 32768
    return 20 * math.log10(rms) if rms > 0 else float("-inf")


def measure(cap, seconds):
    settle = time.time() + 0.3          # drop what was captured before the change settled
    while time.time() < settle:
        cap.read(0.05)
    t_end, pcm = time.time() + seconds, bytearray()
    while time.time() < t_end:
        pcm += cap.read(0.1)
    return {"dbfs": round(dbfs(bytes(pcm)), 1), "frames": len(pcm) // 2}


def tree(pid):
    """pid and all its descendants (a venv python.exe is a launcher that starts the real one)."""
    procs, out = wa.processes(), {pid}
    grew = True
    while grew:
        new = {p for p, pp, _ in procs if pp in out} - out
        out |= new
        grew = bool(new)
    return out


def find_session(pid, device_id=None, wait=3.0):
    t_end = time.time() + wait
    while time.time() < t_end:
        pids = tree(pid)
        for spid, simple, state in wa.sessions(device_id):
            if spid in pids and state == 1:
                return spid, simple
        time.sleep(0.1)
    return None


def run(seconds=1.5):
    wa.com_init()
    devices = wa.render_devices()
    default = next(d for d in devices if d["default"])
    other = next((d for d in devices if not d["default"]), None)
    tone = os.path.join(tempfile.mkdtemp(prefix="dolmi-spike-"), "tone.wav")
    write_tone(tone)
    child = subprocess.Popen([sys.executable, "-c", CHILD, tone], stdin=subprocess.PIPE)
    vol = wa.EndpointVolume(default["id"])
    saved = {"level": vol.level, "muted": vol.muted}
    other_vol = wa.EndpointVolume(other["id"]) if other else None
    other_saved = {"level": other_vol.level, "muted": other_vol.muted} if other else None
    policy = wa.PolicyConfig()
    route_saved = tone_pid = None
    results = {"utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "windows_build": sys.getwindowsversion().build, "devices": [d["name"] for d in devices],
               "tone_dbfs_nominal": round(20 * math.log10(TONE_AMP / math.sqrt(2)), 1), "routes": {}}
    r = results["routes"]
    try:
        found = find_session(child.pid, default["id"])
        if found is None:
            raise RuntimeError("the test tone's audio session did not appear")
        tone_pid, simple = found
        results["tone_pid_is_child"] = tone_pid != child.pid
        cap = wa.ProcessLoopback(child.pid, include_tree=True)
        try:
            r["baseline"] = measure(cap, seconds)
            vol.level = 0.0
            r["endpoint_volume_0"] = measure(cap, seconds)
            vol.level = saved["level"]
            vol.muted = True
            r["endpoint_muted"] = measure(cap, seconds)
            vol.muted = saved["muted"]
            before = wa.session_mute(simple, True)
            r["session_muted"] = measure(cap, seconds)
            wa.session_mute(simple, before)
            route_saved = policy.get(tone_pid)
            if other:
                other_vol.level = 0.0
                policy.set(tone_pid, other["id"])
                time.sleep(0.5)         # the audio service moves the stream
                r["routed_other_0"] = measure(cap, seconds)
                r["routed_other_0"]["session_on_other"] = find_session(tone_pid, other["id"], 1.0) is not None
            else:
                policy.set(tone_pid, default["id"])
                r["routed_other_0"] = {"skipped": "only one speaker on this PC",
                                       "policy_set_get_ok": policy.get(tone_pid) == default["id"]}
            policy.set(tone_pid, route_saved)
            route_saved = None
            r["after_restore"] = measure(cap, seconds)
        finally:
            cap.close()
        idle = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
        try:
            ctl = wa.ProcessLoopback(idle.pid, include_tree=True)
            try:
                r["other_process"] = measure(ctl, seconds)
            finally:
                ctl.close()
        finally:
            idle.kill()
            idle.wait()
    finally:
        vol.level, vol.muted = saved["level"], saved["muted"]
        if other:
            other_vol.level, other_vol.muted = other_saved["level"], other_saved["muted"]
        if route_saved is not None:
            policy.set(tone_pid, route_saved)
        child.kill()
        child.wait()
        results["restored"] = {"level": round(vol.level, 3), "muted": vol.muted,
                               "matches": abs(vol.level - saved["level"]) < 1e-3 and vol.muted == saved["muted"]}
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seconds", type=float, default=1.5)
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    res = run(a.seconds)
    text = json.dumps(res, indent=2)
    print(text)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            f.write(text)


if __name__ == "__main__":
    main()
