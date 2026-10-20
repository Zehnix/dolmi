# STR-10 streaming bench: Dolmi configurations on identical audio

Command: `python eval/streaming/bench.py --limit 2` (synthetic set, first 2 sessions, simulated clock,
no glossary). Laptop: RTX 4060 Laptop 8 GB, CPU int8 for the CPU rows. 2026-10-20. Numbers only.

| config | STR-1 | stream WER % | missed % | first text p50/p95 s | final lag p50/p95 s | RTF | committed rewrites | tail churn | GPU MB peak |
|---|---|---|---|---|---|---|---|---|---|
| cpu-small-old | no | 10.55 | 0.0 | 14.14/19.74 | 10.99/17.50 | 1.00 | - | - | - |
| cpu-small-str1 | yes | 10.55 | 0.0 | 15.94/32.27 | 12.94/28.48 | 1.02 | - | - | - |
| cuda-small-old | no | 10.35 | 0.0 | 0.87/1.93 | 1.31/7.39 | 0.34 | - | 4.38 | 3781 |
| cuda-small-str1 | yes | 10.32 | 0.0 | 0.87/1.91 | 1.41/7.86 | 0.36 | 26 / 1344 drafts | 3.32 | 3813 |
| cuda-turbo-old | no | 8.22 | 0.0 | 1.20/2.24 | 1.93/8.51 | 0.64 | - | 3.88 | 5157 |
| cuda-turbo-str1 | yes | 8.22 | 0.0 | 1.20/2.12 | 1.94/8.47 | 0.65 | 23 / 1569 drafts | 2.71 | 5157 |

Reading it
- CPU small int8 cannot keep up on this laptop (RTF ~1.0, first text ~14 s); STR-1 makes it worse
  (p95 first text 32 s). Keep STR-1 off on the CPU (current default).
- GPU: STR-1 costs ~nothing (RTF +0.01-0.02, same latency) and cuts tail churn 24-30 %, but the
  committed text is withdrawn ~2 % of drafts (e.g. "liegt bei 45" -> "liegt bei"): STR-1's
  "committed never changes" promise does not hold yet.
- Tier defaults: GPU large-v3-turbo (WER 8.2 vs 10.3, 5.2 GB peak fits 8 GB); CPU small int8 only
  with the CPU free (Teams run not done).
- "old" rows have no committed text, so rewrites do not apply ("-"). CPU drafts carry no
  tentative/committed split, so neither rewrites nor churn are measured there ("-").
- Committed rewrites are a lower bound: the trimmed-window check accepts any suffix of the old words
  as a trimmed front, so a rewrite like "a b c" -> "c x" is not counted.

Not covered: WhisperLiveKit / SimulStreaming (AlignAtt) reference runs (torch bench venv), a run with
Teams in the background, Mac.
