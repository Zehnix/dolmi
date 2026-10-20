"""VOICE-6: Mac 'English only' picks the muting process-tap helper mode, and falls back to the normal
capture (subtitles only) on older macOS or when the helper cannot mute. A fake helper (a Python script)
stands in for Mac/audio/dolmi-audio, so this runs on any OS."""
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from dolmi_platform import mac

ROOT = Path(__file__).resolve().parent.parent
SWIFT = ROOT / "Mac" / "audio" / "dolmi-audio.swift"

FAKE = r'''
import struct, sys
args = sys.argv[1:]
open(sys.argv[0] + ".calls", "a").write(" ".join(args) + "\n")
if "--mute-apps" in args and "FAIL_MUTE" in open(sys.argv[0] + ".mode").read():
    sys.stderr.write("dolmi-audio: could not create the system audio tap (OSStatus 560947818)\n")
    sys.exit(1)
level = 0.5 if "--mute-apps" in args else 0.25
sys.stdout.buffer.write(b"DOLMI-AUDIO 16000 1\n")
for _ in range(5):
    sys.stdout.buffer.write(struct.pack("<1600f", *([level] * 1600)))
sys.stdout.flush()
'''


@pytest.fixture
def helper(tmp_path, monkeypatch):
    fake = tmp_path / "dolmi-audio.py"
    fake.write_text(FAKE)
    (tmp_path / "dolmi-audio.py.mode").write_text("")
    real_popen = subprocess.Popen
    monkeypatch.setattr(mac, "_audio_helper", lambda: fake)
    monkeypatch.setattr(mac.subprocess, "Popen",
                        lambda cmd, **kw: real_popen([sys.executable, *map(str, cmd)], **kw))

    class H:
        def calls(self):
            p = tmp_path / "dolmi-audio.py.calls"
            return p.read_text().splitlines() if p.exists() else []

        def fail_mute(self):
            (tmp_path / "dolmi-audio.py.mode").write_text("FAIL_MUTE")
    return H()


def _capture(english_only):
    q, stop, levels = queue.Queue(), threading.Event(), []

    def on_level(rms):
        levels.append(round(rms, 3))
        stop.set()
    mac.loopback_stream(q, stop, None, on_level=on_level, english_only=english_only)
    return levels, q


def test_english_only_runs_the_muting_tap_for_meeting_apps(helper, monkeypatch):
    monkeypatch.setattr(mac, "_macos_version", lambda: (14, 2))
    levels, q = _capture(True)
    assert levels == [0.5] and not q.empty()
    (call,) = helper.calls()
    assert call.startswith("--mute-apps ")
    apps = call.split(" ", 1)[1].split(",")
    assert {"com.microsoft.teams", "us.zoom", "com.google.chrome", "com.apple.safari"} <= set(apps)


def test_off_by_default_keeps_the_all_audio_tap(helper, monkeypatch):
    monkeypatch.setattr(mac, "_macos_version", lambda: (15, 0))
    levels, _ = _capture(False)
    assert levels == [0.25] and helper.calls() == [""]
    import config
    assert config.DEFAULTS["english_only"] is False


def test_older_macos_falls_back_to_subtitles_only(helper, monkeypatch, capsys):
    monkeypatch.setattr(mac, "_macos_version", lambda: (14, 1))
    levels, _ = _capture(True)
    assert levels == [0.25] and helper.calls() == [""]
    assert "subtitles only" in capsys.readouterr().out


def test_helper_that_cannot_mute_falls_back(helper, monkeypatch, capsys):
    monkeypatch.setattr(mac, "_macos_version", lambda: (14, 4))
    helper.fail_mute()
    levels, _ = _capture(True)
    assert levels == [0.25]
    assert [c.split(" ")[0] for c in helper.calls()] == ["--mute-apps", ""]
    assert "could not create the system audio tap" in capsys.readouterr().out


@pytest.mark.parametrize("version,ok", [((14, 2), True), ((15,), True), ((26, 0), True),
                                        ((14, 1), False), ((13, 6), False), ((), False)])
def test_english_only_supported(monkeypatch, version, ok):
    monkeypatch.setattr(mac, "_macos_version", lambda: version)
    assert mac.english_only_supported() is ok


def test_swift_helper_has_the_muted_process_tap():
    src = SWIFT.read_text(encoding="utf-8")
    for needle in ("--mute-apps", ".mutedWhenTapped", "stereoMixdownOfProcesses", "kAudioProcessPropertyBundleID",
                   "kAudioHardwarePropertyProcessObjectList", "kAudioTapPropertyDescription",
                   "ticks % 20", ".unmuted"):
        assert needle in src, needle
    assert src.count('write("DOLMI-AUDIO') == 2     # header once per mode, before any samples
