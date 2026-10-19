"""Text-to-speech backends for reading translations aloud.

Each backend has `name`, `available()`, `speak(text)` (blocks until the line has been spoken) and
`first_audio(text)` ((first audio, whole line) synthesis seconds, without playing; for the VOICE-0c
benchmark). Only the operating system's voices ship today, because they need nothing installed:

- Windows: System.Speech in ONE persistent PowerShell process (starting PowerShell per line costs ~1 s).
- macOS: the 'say' command.

Kokoro (Apache-2.0) was measured in VOICE-0c but needs PyTorch or kokoro-onnx plus a phonemizer, which
the app does not bundle yet; see design notes. `select_backend` takes the first available backend from a
preference list, so a faster one can be put in front later without touching the reader.
"""
import base64
import shutil
import subprocess
import sys
import threading
import time

import dolmi_platform

# System.Speech driver: one command per stdin line, one reply line per command.
#   SAY <base64 utf-8>    speak on the default output device, reply DONE
#   BENCH <base64>        synthesize the whole line to nothing, reply 'BENCH <ms>' (an upper bound for first audio)
#   VOICE <name>          select an installed voice, reply OK or NO
_PS_DRIVER = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
try { $s.SelectVoiceByHints('NotSet', 'NotSet', 0, [Globalization.CultureInfo]'en-US') } catch {}
$sw = New-Object System.Diagnostics.Stopwatch
$names = ($s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name + '|' + $_.VoiceInfo.Culture }) -join ';'
[Console]::Out.WriteLine('READY ' + $s.Voice.Name + ';' + $names); [Console]::Out.Flush()
while (($line = [Console]::In.ReadLine()) -ne $null) {
  $cmd, $arg = $line.Split(' ', 2)
  try {
    if ($cmd -eq 'SAY') {
      $s.SetOutputToDefaultAudioDevice(); $s.Speak([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($arg))); $r = 'DONE'
    } elseif ($cmd -eq 'BENCH') {
      $s.SetOutputToNull(); $sw.Restart()
      $s.Speak([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($arg)))
      $r = 'BENCH ' + [int]$sw.Elapsed.TotalMilliseconds
    } elseif ($cmd -eq 'VOICE') {
      $s.SelectVoice($arg); $r = 'OK'
    } else { $r = 'NO' }
  } catch { $r = 'NO' }
  [Console]::Out.WriteLine($r); [Console]::Out.Flush()
}
"""


class WindowsSpeech:
    name = "windows"

    def __init__(self, voice=None):
        self._voice, self._proc, self._lock = voice, None, threading.Lock()
        self.voice_name, self.voices = "", []

    def available(self):
        return dolmi_platform.OS == "win" and shutil.which("powershell") is not None

    def _ask(self, line):
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._start()
            self._proc.stdin.write(line + "\n")
            self._proc.stdin.flush()
            reply = self._proc.stdout.readline().strip()
        if not reply:
            raise RuntimeError("the Windows voice stopped answering")
        return reply

    def _start(self):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand",
             base64.b64encode(_PS_DRIVER.encode("utf-16-le")).decode("ascii")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", creationflags=flags)
        ready = self._proc.stdout.readline().strip()
        while ready and not ready.startswith("READY"):
            ready = self._proc.stdout.readline().strip()
        if not ready:
            raise RuntimeError("could not start the Windows voice")
        current, *names = ready[len("READY "):].split(";")
        self.voice_name, self.voices = current, [n.split("|")[0] for n in names if n]
        if self._voice and self._voice in self.voices:
            self._proc.stdin.write(f"VOICE {self._voice}\n")
            self._proc.stdin.flush()
            if self._proc.stdout.readline().strip() == "OK":
                self.voice_name = self._voice

    @staticmethod
    def _b64(text):
        return base64.b64encode(text.encode("utf-8")).decode("ascii")

    def speak(self, text):
        self._ask("SAY " + self._b64(text))

    def first_audio(self, text):
        """(first audio, whole line) in seconds; System.Speech gives no usable first-chunk event, so both
        are the time to synthesize the whole line (an upper bound for first audio)."""
        ms = int(self._ask("BENCH " + self._b64(text)).split()[1]) / 1000
        return ms, ms

    def cancel(self):
        """Cut the line being spoken now: end the voice process (the next line starts a fresh one).
        Deliberately without the lock, which a speaking line holds until it is done."""
        p = self._proc
        if p is not None and p.poll() is None:
            p.kill()

    def close(self):
        self.cancel()
        with self._lock:
            if self._proc is not None:
                self._proc.wait(timeout=5)
            self._proc = None


class MacSay:
    name = "mac-say"

    def __init__(self, voice=None):
        self.voice_name = voice or ""
        self._proc = None

    def available(self):
        return sys.platform == "darwin" and shutil.which("say") is not None

    def _cmd(self, *extra):
        return ["say", *(["-v", self.voice_name] if self.voice_name else []), *extra]

    def speak(self, text):
        self._proc = subprocess.Popen(self._cmd(text))
        try:
            code = self._proc.wait(timeout=60)
        finally:
            self._proc = None
        if code > 0:                     # a negative code means cancel() ended it on purpose
            raise RuntimeError(f"say failed ({code})")

    def first_audio(self, text):
        """'say' gives no first-audio event: time a render to a file (an upper bound for first audio)."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            t = time.perf_counter()
            subprocess.run(self._cmd("-o", f"{d}/x.aiff", text), check=True, timeout=60)
            total = time.perf_counter() - t
        return total, total

    def cancel(self):
        p = self._proc
        if p is not None and p.poll() is None:
            p.terminate()

    def close(self):
        self.cancel()


def os_backends():
    return [WindowsSpeech(), MacSay()]


def select_backend(backends=None):
    """The first available backend in preference order, or None (then reading aloud stays off)."""
    for b in os_backends() if backends is None else backends:
        try:
            if b.available():
                return b
        except Exception as e:
            print(f"voice backend {getattr(b, 'name', b)} check failed: {e}")
    return None
