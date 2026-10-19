# Dolmi 1.0 — App specification

**Dolmi** shows live English subtitles for German speech in any meeting app — Teams, Zoom, Slack huddles, Google Meet, Webex — or any audio playing on the PC. Everything runs locally: no cloud, no account, nothing uploaded.

## At a glance

| | |
|---|---|
| Version | 1.0 |
| Platform | Windows 10 / 11, 64-bit; macOS 14.2+ (see [macOS](#macos) below) |
| Languages | 27 spoken languages + Auto-detect → English subtitles (default: German), and English speech → German subtitles. Set in Settings → Spoken language |
| Delay | Draft text ~1 s after speech; final sentence ~1–2.5 s after the speaker pauses (CPU, `small` model) |
| Translation speed | ~0.06 s per sentence on CPU |
| Privacy | 100% offline after the first model download |
| Install | Per-user, no admin rights: unzip `Dolmi-Setup-1.0.zip` → double-click `Install Dolmi.bat` |
| Install location | `%LOCALAPPDATA%\Dolmi` · shortcuts on Desktop + Start Menu · listed in Settings → Apps |

## System requirements

| | Minimum | Recommended |
|---|---|---|
| OS | Windows 10 64-bit | Windows 11 |
| CPU | 4 cores, from ~2018 | 8+ cores (tested: Intel Core Ultra 7 155H) |
| RAM | 8 GB | 16 GB |
| Disk | 2 GB free | 4 GB free (if using the large GPU model) |
| GPU | Not needed | NVIDIA with 4 GB+ VRAM + CUDA 12 / cuDNN 9 → `large-v3-turbo` model |
| Python | 3.10–3.12 (installed automatically via winget if missing) | 3.12 |
| Internet | Only for installation and first model download | — |
| Audio | Any speakers/headphones; Dolmi records what Windows plays (WASAPI loopback), selectable in Settings → Listen to | — |

Disk use: app + components ~305 MB · speech model `small` 464 MB · translator 146 MB · optional `large-v3-turbo` 1.6 GB.

## How it works

```
Teams audio ─► WASAPI loopback capture (16 kHz mono)
           ─► voice/pause detection (energy threshold + Silero VAD)
           ─► Whisper speech recognition (German text)
           ─► vocabulary: lock names/terms
           ─► Opus-MT translation (English text)
           ─► vocabulary: restore terms, fix known mistakes
           ─► Dolmi window + subtitle bar + transcript files (.md + .srt)
```

## Models

| Model | Job | Source | Size | Licence |
|---|---|---|---|---|
| Whisper `small` (CTranslate2, int8) | German speech → German text, default on CPU | OpenAI Whisper via `Systran/faster-whisper-small` | 464 MB | MIT |
| Whisper `medium` (optional) | More accurate, slower | `Systran/faster-whisper-medium` | ~1.5 GB | MIT |
| Whisper `large-v3-turbo` (optional) | Best accuracy, needs NVIDIA GPU | `mobiuslabsgmbh/faster-whisper-large-v3-turbo` | 1.6 GB | MIT |
| Opus-MT X→en (CTranslate2), one per language | Text → English for 23 languages (de, fr, es, it, nl, pl, ru, uk, tr, ar, ur, hi, zh, ja, ko, sv, da, fi, cs, hu, bg, id, vi); downloaded on demand | Helsinki-NLP Opus-MT, via `gaudi/opus-mt-<lang>-en-ctranslate2` | 148–177 MB each | CC-BY 4.0 |
| Opus-MT mul→en | Fallback for Portuguese, Persian, Romanian, Greek and Auto-detect; lower quality | `gaudi/opus-mt-mul-en-ctranslate2` | 159 MB | CC-BY 4.0 |
| Opus-MT en→de (CTranslate2) | English text → German; downloaded when English is picked | `gaudi/opus-mt-en-de-ctranslate2` | 152 MB | CC-BY 4.0 |
| Silero VAD | Skips silence before recognition | Bundled with faster-whisper | < 2 MB | MIT |

Models download from Hugging Face and are cached in `%USERPROFILE%\.cache\huggingface`. The **Models** tab in Dolmi lists every model with its size, accuracy and speed ratings and how well it runs on this PC (based on RAM, CPU threads and a usable NVIDIA GPU), marks the recommended one, and lets the user download, switch (**Use**) and uninstall models. The model in use and the translator can't be uninstalled.

## Technologies

| Area | Technology |
|---|---|
| Language | Python 3.12 |
| Speech recognition | faster-whisper 1.2 (CTranslate2 inference engine, int8 on CPU / float16 on GPU) |
| Translation | CTranslate2 4.8 + SentencePiece tokenizer |
| Audio capture | PyAudioWPatch (PortAudio with WASAPI loopback) |
| Signal processing | NumPy (resampling, energy detection) |
| UI | HTML/CSS in a native window — pywebview 6 on Microsoft WebView2 — bridged to Python (`src/ui/app.py`); "Bridge" design with Paper (light) and Ink (dark) themes; the subtitle bar is a frameless, transparent, always-on-top webview |
| Fonts & icons | Fraunces, Hanken Grotesk, JetBrains Mono (SIL OFL) and Lucide icons (ISC), bundled in `src/ui/web/vendor` — nothing is loaded from the internet |
| Packaging | Inno Setup `Dolmi-Setup-x.y.z.exe` with the embeddable CPython 3.12 (PSF-signed `pythonw.exe` as launcher); built and tested by GitHub Actions (`installer/`) |

**Deliberately not used:** PyTorch and Hugging Face Transformers. Windows Smart App Control blocks PyTorch's DLLs, and they aren't needed for running these models. For the same reason Dolmi isn't a packaged `.exe`: unsigned executables are blocked, so it runs on the signed python.org Python.

## Methods

- **Pause-based segmentation:** a sentence ends after 0.6 s of silence, or is cut after 12 s of continuous speech. This avoids sentences being split mid-word.
- **Draft + final text:** a quick draft is shown about every second (yellow) and replaced by the final sentence on the pause (white).
- **Real-time catch-up:** queued audio is merged, so delay never builds up when the PC is busy.
- **Hallucination filter:** drops Whisper's typical German "silence" phrases (e.g. *"Untertitel im Auftrag des ZDF"*).
- **Context prompting:** Whisper gets the spelling-hints list and the previous sentence, so names and terms come out consistently.
- **Vocabulary locking:** terms in `[keep]` / `[german]` are swapped for placeholders (`X0X`) before translation and restored afterwards, so names like *Acme* or *Project Falcon* are never translated. `[english]` rules fix known mistranslations.
- **Crash-safe transcripts:** every sentence is appended to disk immediately (Markdown + SRT with real timestamps).
- **Automatic device choice:** GPU only when an NVIDIA card *and* the CUDA 12 libraries are present; otherwise CPU.

## Features

- Main window: Live transcript, Saved items, Vocabulary editor, Settings
- Click a sentence / double-click a word to save it (`transcripts\saved_<date>.md`)
- Copy all, Export transcript
- Vocabulary and spelling hints editable in the app, applied without restart
- Always-on-top subtitle bar: move, resize from any edge, text size, German line on/off, opacity
- Settings and subtitle-bar position remembered (`settings.json`)
- Audio meter in the top bar while live; warns after 8 s with no sound (wrong speaker selected)
- **Dolmi folder**: transcripts and saved items live in `Documents\Dolmi` per Windows user; Settings → Privacy shows it and can move it (Change…). Models live in the per-user Hugging Face cache, also shown there
- Errors logged to `dolmi.log`

## Files

| File | Purpose |
|---|---|
| `src/main.py` | Entry point: the native window, logging, invisible mode at launch, recovery hotkey |
| `src/ui/app.py` | The JS bridge (`window.pywebview.api`): live captions, meetings, assistant, settings, models |
| `src/ui/overlay.py` | Subtitle bar window |
| `src/ui/web/` | The UI: `index.html` (main window), `css/app.css`, `js/*.js` (one file per screen), `overlay.html` (subtitle bar), bundled fonts/icons |
| `src/config.py` | Settings and where files live (`%APPDATA%\Dolmi`, `Documents\Dolmi`; Mac: `~/Library/Application Support/Dolmi`, `~/Documents/Dolmi`) |
| `cloud_speech.py` | Optional cloud captions: Gemini (hear + translate), OpenAI or Cloudflare Whisper (hear; Opus-MT translates) |
| `inbox.py` | Local SQLite store (`dolmi.db`) |
| `dolmi_platform/` | Everything Windows and macOS do differently, behind one API: `windows.py` (WASAPI capture, CUDA, DPAPI, `SetWindowDisplayAffinity`, Ctrl+Alt+Shift+D) and `mac.py` + `mac_stealth.py` (process-tap capture, Keychain, `NSWindow.sharingType`, ⌃⌥⇧D); `WORDS` holds every PC/Mac word the UI shows |
| `models.py` | Model catalog, PC/Mac check, download / uninstall |
| `assistant.py` | Optional cloud helper (Claude / OpenAI / Gemini / NVIDIA / Cloudflare), keys encrypted per user (DPAPI / Keychain) |
| `live_subs.py` | Engine: capture, recognition, translation, transcripts |
| `vocabulary.txt` | Translation rules (in `%APPDATA%\Dolmi`) |
| `glossary.txt` | Spelling hints for speech recognition (in `%APPDATA%\Dolmi`) |
| `installer/` | `build.ps1`, `dolmi.iss` (Inno Setup), `test-install.ps1` |
| `Mac/` | Mac-only files: `audio/dolmi-audio.swift` (system-audio helper), `packaging/` (`build.sh`, `dolmi.spec`, `launcher.py`), `start.command`, Mac requirements |
| `transcripts\` | Meeting transcripts and saved items (kept on uninstall) |

## Known limits

- Languages without their own translator (Portuguese, Persian, Romanian, Greek) use the multilingual model, which makes more mistakes.
- Auto-detect only translates languages whose translator (or the multilingual one) is installed; others show the original text marked like `[ja]`. In Auto-detect, English speech is shown as is.
- Doesn't tell speakers apart.
- Accuracy drops with overlapping speakers, bad audio or fast speech; the CPU `small` model is the main limit.
- A bare "Chris" can't be resolved to Chris1 / Chris2; only full names are labelled.
- Records from the default output device at start; restart the session after switching headphones/speakers.

## Privacy

Audio and text never leave the PC. Transcribing colleagues is processing personal data, so tell meeting participants you're using it; Dolmi shows a one-time reminder. (Not legal advice.)

Settings → Privacy: turn saving transcripts off (live subtitles only), or delete Dolmi's files (meeting, saved) automatically after 7, 30 or 90 days. Other files in the folder are never touched.

## macOS

The Mac app runs the same code (repo root) with `dolmi_platform/mac.py`; only these points differ.

| | |
|---|---|
| Platform | macOS 14.2 Sonoma or later, Apple Silicon (Intel builds from source) |
| Install | Open `Dolmi-x.y.z.dmg` → drag Dolmi to Applications; uninstall by moving it to the Bin |
| OS | Minimum macOS 14.2, recommended macOS 15 or later |
| CPU | Minimum Apple M1; recommended Apple M3/M4 or later (tested: Apple M4, 10 cores) |
| GPU | Not used — CTranslate2 has no Metal backend, so Whisper runs on the CPU cores (int8 with Apple Accelerate); Auto always picks `small` |
| Python | Bundled in Dolmi.app; 3.12 to run from source (`brew install python@3.12`) |
| Audio | Dolmi records what the Mac plays with a Core Audio process tap (`Mac/audio/dolmi-audio`, asks for *System Audio Recording* permission once), or a microphone chosen in Settings → Listen to (sounddevice) |
| UI | pywebview 6 on WKWebView (Cocoa); the subtitle bar floats on every Space and above full-screen meetings |
| Invisible mode | `NSWindow.sharingType = none` + Dock hiding; ⌃⌥⇧D via Carbon `RegisterEventHotKey` (no Accessibility permission). On macOS 27 turning it off rebuilds the windows |
| API keys | Encrypted with a random secret kept in the login Keychain |
| Packaging | PyInstaller `Dolmi.app` (the shared source ships unchanged in `Contents/Resources/app`) in a `Dolmi-x.y.z.dmg`, signed with a local self-signed certificate so permissions survive rebuilds (`Mac/packaging/build.sh`) |
| Files | `~/Library/Application Support/Dolmi` (settings, vocabulary), `~/Documents/Dolmi` (transcripts), `~/Library/Logs/Dolmi` (log), `~/.cache/huggingface` (models) |
