# Dolmi test bench

Scores Dolmi's live subtitles (transcription, translation, missed lines, latency) on fixed test audio, so a model or pipeline change is adopted only when the numbers say it is better.

## Quick start

From the repo root, with the app's venv (no extra packages needed):

```
python eval/make_synthetic.py        # ~38 min of meeting-style audio + 3 speaker clips (needs OS voices)
python eval/fetch_public.py          # optional: FLEURS German test split (570 MB download)
python eval/run_eval.py              # synthetic set, component + stream mode, Whisper small on CPU
```

`run_eval.py` prints a summary table and writes `eval/results/<run>.md` and `eval/results/<run>.jsonl`. Both folders, `eval/data/` and `eval/results/`, are git-ignored: audio and per-line output never go into git.

The audio folder can live outside the checkout, so a worktree or a fresh clone does not have to rebuild it. The bench looks in this order: `DOLMI_EVAL_DATA` (any folder), `eval/data/` if it exists, `%LOCALAPPDATA%\dolmi-eval\data` if it exists, else it builds into `eval/data/`. The manifest names files `data/...` either way, so the hashes and the baseline stay comparable.

Common options:

| Option | Meaning |
|---|---|
| `--mode component\|stream\|both` | which half to run (default both) |
| `--set synthetic\|fleurs\|private:<folder>` | test set (default synthetic) |
| `--model small` / `--device cpu\|cuda\|auto` | speech model and device (an RTX 4060 runs `large-v3-turbo`) |
| `--translator opus-de` | the app's Opus-MT, or another CTranslate2 Marian model (models.py key or Hugging Face repo) |
| `--limit N` | component: first N clips; stream: first N sessions |
| `--no-terms` | run without the fictional glossary/vocabulary |
| `--comet --comet-python <python>` | add COMET (see below) |
| `--write-baseline` | store the numbers in `eval/baseline.json` |

## Comparing with the baseline

`eval/baseline.json` holds numbers only (no text, no audio), one entry per `set|model|device|translator|terms`. The committed entries were measured on the Foundation code (`exon/integration` at `d50e5ad`, before the wave-1 pipeline fixes). To compare after a change, run the same command and read the two tables side by side:

```
python eval/run_eval.py --model small --device cpu
python eval/run_eval.py --model large-v3-turbo --device cuda
```

How the Foundation entries were made: a scratch worktree at `d50e5ad` with the `eval/` folder copied on top (the bench is not in that commit), then `--write-baseline`. `git_rev()` records the HEAD of the checkout it runs in, so the `git` field of those entries was set to `d50e5ad` by hand afterwards. Do the same for any later entry that should stand for an older commit. The CPU `small` Foundation entry is still to be measured this way (about 1.5 to 2 hours): `python eval/run_eval.py --model small --device cpu --write-baseline`.

Add `--write-baseline` only when the new numbers should become the reference. Timing numbers (latency, real-time factor) depend on what else the PC is doing; compare them on an idle machine, or look at the quality numbers only.

## What is measured

**Component mode** runs each reference line as its own clip (with up to 0.25 s of the surrounding audio, no previous-sentence prompt):

- **WER / CER** (German, and English lines separately), pooled over the set. The normalizer (`metrics.normalize`) lowercases, spells numbers as spoken words (`23` and `dreiundzwanzig` match, `4.2` = `vier punkt zwei`, `1.500` = `tausendfünfhundert`, `3. März` = `dritte märz`, inflected ordinals fold onto `dritte`), spells `%`/`€`/`&`/`+`, maps `ß` to `ss`, and strips punctuation and hyphens.
- **chrF and BLEU** of the app's translator on the gold German (`chrf_gold`, `bleu_gold`) and on the ASR output (`chrf_cascade`, `bleu_cascade`). Both are pure-Python ports that give the same numbers as sacrebleu 2.4 defaults (chrF: character order 6, beta 2; BLEU: 13a tokenizer, exp smoothing).
- **Term accuracy**: share of the fictional terms (`manifest.json` → `terms`) that come out right, in the German transcript and in the English subtitle.
- **Real-time factor**: ASR seconds per audio second.
- WER by line type (tags below).

**Stream mode** plays each whole session through the app's real `live_subs.worker()` with the real `Engine`, in 100 ms chunks, on a **simulated clock**: while every thread of the worker waits (the segmenter for audio, the `dolmi-asr` thread for a job) the clock jumps to the next chunk, while anything computes it runs at wall speed. So latencies include ASR/MT time, idle waiting costs nothing, and the audio never runs ahead of a busy ASR thread. At the end of the audio, Stop comes only once the ASR thread has no job left (finals after Stop never reach the screen), and the bench waits for that thread before scoring. If `worker()` takes a `clock=` parameter it is injected; otherwise the bench stands in for `live_subs.time` during the run. `--realtime --speed 1` feeds a real queue in real time instead (it, too, waits for the ASR thread to finish before Stop).

- **Missed-segment rate**: reference lines with no overlapping final (a final spans its meta `start`..`end`, the speech it holds; older workers without a line `id`: from its first voiced chunk to the moment it was emitted).
- **Lines with no word recovered** and **word recall / stream WER**: the finals' text aligned against the whole reference transcript.
- **Clipped first word**: lines that came out, but without their first word.
- **Mid-word / mid-utterance cuts**: finals whose audio ends inside a spoken word / inside a reference line (word timings come from the TTS events).
- **Latency per stage**: end of speech → final (p50/p95), speech start → first text on screen (draft or final), ASR call and MT call durations.
- **Drops**: if `worker()` takes `on_drop=`, every reported drop is counted by reason.

The ui_q items may be the old `("final", de, en)` tuples or carry a meta dict (`{'id', 'start', 'end', 'emitted', ...}`); a later final with the same `id` replaces the text, and `start`/`end` from the meta are used when present. With an `id` they are contract times (stream start + sample / SR) and are used as audio times directly.

Line tags: `S` status, `Q` question, `C` German/English code-switching, `E` English line, `V` verb-final long sentence, `B` backchannel (`Ja.`, `Genau.`), `M`/`ME` monologue over 12 s, `N` numbers and dates; audio conditions `soft_start` (300 ms fade-in), `quiet` (-22 dB), `long`.

## Test sets

### synthetic (`make_synthetic.py`)

Scripted meeting talk with fictional companies (Velorix, Brandtwerk, Halvora), products (Nimbus Gateway, Lumora, Quellsync, Projekt Kranich), people and figures; tech terms, numbers, dates, code-switching, long verb-final sentences, backchannels, monologues over 12 s and pauses of 0.2 to 3 s. Voiced with the OS text-to-speech:

- Windows: System.Speech through a PowerShell child process, voices *Microsoft Hedda Desktop* (de-DE), *Zira* (en-US), *Hazel* (en-GB). Word timings come from the voice's progress events.
- macOS: `say` with *Anna*, *Samantha*, *Daniel*; word timings are estimated from text length (needs a manual check on a Mac).

Extra German speakers (`de-b`, `de-c`) are the same voice at another rate, resampled so the pitch moves too. Four sessions (`syn-s1` … `syn-s4`, ~9.5 min each): clean; light noise + level variation; noise at 15 dB SNR + Opus 24 kbps round trip; Opus 16 kbps with four speakers. The Opus round trip uses PyAV; if PyAV cannot load, it is skipped and the session notes say so.

Each `eval/data/synthetic/<id>.wav` has a sidecar `<id>.json` with the references and per-word timings; `eval/manifest.json` has the same references (without word timings) and the sha256 of each WAV. Another TTS version gives other audio: the bench then warns that the scores are not comparable with the baseline.

### speakers (for speaker labelling)

Three clips of about 100 s, three voices each, made by the same script:

- `eval/data/speakers/spk-1.wav` — voices de-a, de-b, de-c
- `eval/data/speakers/spk-2.wav` — voices de-b, de-c, de-a
- `eval/data/speakers/spk-3.wav` — voices de-a, de-c, en-a

Each has a `.json` sidecar; every utterance carries `speaker`, `start`, `end`, `text` and word timings.

### fleurs (`fetch_public.py`)

The FLEURS German test split (google/fleurs on Hugging Face, CC BY 4.0, 862 clips, 3.15 h) with English references from the en_us transcriptions of the same sentence ids. Download stays in the Hugging Face cache; WAVs are unpacked to `eval/data/fleurs/` as 16 kHz PCM. The script checks free disk first and refuses more than 2 GB. Stream mode on FLEURS stitches the first 60 clips (~13 min) into one session with 1 s pauses.

**CoVoST 2** is not downloaded: its audio is Common Voice 4, which is gated behind an account and accepted terms. Add it by hand if needed.

### private:<folder>

Your own recordings, scored without anything entering the repo: put `<name>.wav` (16 kHz mono 16-bit) and `<name>.json` in a folder outside git:

```json
{"id": "m1", "utterances": [{"id": "m1-1", "start": 3.2, "end": 7.9, "lang": "de",
  "text": "corrected German", "en": "reference English or null", "terms": [], "en_terms": [], "tags": []}]}
```

Then run `python eval/run_eval.py --set private:D:\path\to\folder`. Results still go to `eval/results/` (git-ignored). Only use meetings whose participants agreed.

## COMET (optional)

COMET needs PyTorch, which must never go into the app venv. Make a separate venv and point the bench at it:

```
python -m venv %USERPROFILE%\comet-venv
%USERPROFILE%\comet-venv\Scripts\pip install unbabel-comet
python eval/run_eval.py --mode component --comet --comet-python %USERPROFILE%\comet-venv\Scripts\python.exe
```

It downloads `Unbabel/wmt22-comet-da` (~2.3 GB) on first use and scores the gold-German translations. Without it the table shows "n/a".

## Rules

- Audio, results and real meeting clips stay out of git (`eval/data/`, `eval/results/` are ignored); the manifest lists files, hashes and references only.
- The term list is fictional. Never copy real vocabulary, glossary or company terms into `eval/`.
- Change `metrics.normalize` only together with a new baseline: it changes every WER.
