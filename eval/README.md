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
| `--mode component\|stream\|both\|mt` | which half to run (default both); `mt` = translation only on the gold German, no Whisper |
| `--set synthetic\|fleurs\|private:<folder or .json>` | test set (default synthetic); several: `--set synthetic,fleurs` |
| `--model small` / `--device cpu\|cuda\|auto` | speech model (models.py key or a CTranslate2 folder) and device (an RTX 4060 runs `large-v3-turbo`) |
| `--translator opus-de` | the app's Opus-MT, another CTranslate2 Marian model (models.py key, Hugging Face repo or folder), or `gemini` / `deepl` (the QA-7 cloud translator with your saved key) |
| `--limit N` | component/mt: first N clips/lines; stream: first N sessions |
| `--smoke` | the CI smoke suite (below); exits 1 on a regression |
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

## Smoke suite (CI, QA-3)

`python eval/run_eval.py --smoke` = 25 FLEURS clips, Whisper small + Opus-MT on the CPU, component mode, compared with the `smoke|small|cpu|opus-de|terms` entry in `baseline.json`. It fails (exit 1) when German WER rises more than 1 point or chrF (on the gold German or on the ASR output) falls more than 1 point. About 3 minutes on a laptop CPU including the model load (run `fetch_public.py` once first). `.github/workflows/eval-smoke.yml` runs the same command on pull requests that touch the speech/translation code, with the models and FLEURS clips cached. `--check-baseline` applies the same limits to any run against its own entry. After an intended quality change, refresh the entry with `--smoke --write-baseline`.

## Translation A/B and cloud translators (QA-4, QA-7)

`--mode mt` scores only the translator, on the gold German of a set (no Whisper, under a minute per set), so candidates are cheap to compare:

```
python eval/run_eval.py --mode mt --set fleurs --translator %LOCALAPPDATA%\Dolmi-eval\models\<ct2 folder>
```

`--translator gemini` (or `deepl`) sends each line with the previous three lines (German + accepted English) and the Vocabulary's rules, exactly as the app does, using the key saved in Dolmi (or `DOLMI_GEMINI_KEY` / `DOLMI_DEEPL_KEY`). The local Opus-MT runs on the same lines, and the table adds a "vs local" block: chrF and COMET deltas, term accuracy, known mistakes avoided, the share of lines whose chrF beats local, answer time p50/p95, and how often the app would have kept the local line (failed or slower than 1.5 s). The adoption run (a few cents; it sends the lines to the provider):

```
python eval/run_eval.py --mode mt --translator gemini --set synthetic,fleurs,private:<private text set .json>
python eval/ab_blind.py make <that run's .jsonl> --out <folder>     # 100 lines, sides hidden
python eval/ab_blind.py score <folder>                               # after filling in sheet.csv
```

### Candidate models

`models.CANDIDATES` lists models the bench has measured but the app does not offer (no reputable publisher ships them as CTranslate2). `--model` / `--translator` take their keys and load the local conversion from `%LOCALAPPDATA%\Dolmi-eval\models\<folder>`. Convert in a throwaway venv there, never in the app venv:

```
python -m venv %LOCALAPPDATA%\Dolmi-eval\convert\venv
%LOCALAPPDATA%\Dolmi-eval\convert\venv\Scripts\pip install ctranslate2==4.8.2 pyyaml sentencepiece transformers<5 torch --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple
REM Opus-MT (Marian zip from the "source" URL, unpacked):  then copy source.spm/target.spm into the output
...\python -m ctranslate2.converters.opus_mt --model_dir <unzipped> --output_dir <folder> --quantization int8
REM Whisper (Hugging Face transformers model):  then copy tokenizer.json from mobiuslabsgmbh/faster-whisper-large-v3-turbo
...\python -m ctranslate2.converters.transformers --model primeline/whisper-large-v3-turbo-german --output_dir <folder> --quantization float16 --copy_files preprocessor_config.json
```

Adoption rule (tracker, quality domain): at least +3 COMET and +2 chrF over local, and at least 60 % wins in the blind A/B of 100 sentences. COMET needs the separate venv below; where it cannot run, decide on chrF (+2) and term accuracy and say so.

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

Then run `python eval/run_eval.py --set private:D:\path\to\folder`. Results go to a `results` folder next to your private data, never into the checkout, and `--write-baseline` refuses private sets. Only use meetings whose participants agreed.

### private text sets (no audio)

When only transcripts exist, a text set scores the translation half: `{"kind": "text", "lines": [{"id", "de", "en" (reference or null), "en_terms": [...], "avoid": [known wrong English], "tags": []}], "vocabulary": "...", "glossary": "..."}` (see `translators.py`). `make_private_text.py` builds one from your saved meetings and a vocabulary review (the English each reviewed rule should produce, and the lines the review marked mistranslated):

```
python eval/make_private_text.py --out <private folder>\meetings.json --transcripts "%USERPROFILE%\Documents\Dolmi" --review <review folder>
python eval/run_eval.py --set private:<private folder>\meetings.json
```

It refuses to write inside the checkout and prints counts only. The table shows term accuracy and "known mistakes avoided" (lines where none of the reviewed wrong English comes back).

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
