"""Blind A/B of two translations (the adoption rule's "≥ 60 % wins in a blind A/B of 100 sentences").

    python eval/ab_blind.py make  <results.jsonl> --out <folder>   # 100 random lines, sides shuffled
    python eval/ab_blind.py score <folder>                          # after you filled in the sheet

`make` takes a run made with a cloud translator (rows carry mt_gold = candidate and mt_gold_local = the
app's Opus-MT) and writes sheet.csv (id, German, A, B, choice) plus key.json (which side is which).
Open sheet.csv, write A, B or = in the choice column without looking at key.json, then `score` prints the
candidate's win rate (ties left out). For a private set, keep --out in the private folder.
"""
import argparse
import csv
import json
import random
import sys
from pathlib import Path


def load_pairs(results):
    rows = [json.loads(x) for x in Path(results).read_text(encoding="utf-8").splitlines() if x.strip()]
    return [r for r in rows if r.get("mt_gold") and r.get("mt_gold_local") and r["mt_gold"] != r["mt_gold_local"]]


def make(results, out, n=100, seed=7):
    pairs = load_pairs(results)
    rnd = random.Random(seed)
    pick = rnd.sample(pairs, min(n, len(pairs)))
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    key = {}
    with (out / "sheet.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["n", "German", "A", "B", "choice (A, B or =)"])
        for i, r in enumerate(pick, 1):
            flip = rnd.random() < 0.5
            a, b = (r["mt_gold"], r["mt_gold_local"]) if flip else (r["mt_gold_local"], r["mt_gold"])
            key[str(i)] = {"candidate": "A" if flip else "B", "id": r.get("id")}
            w.writerow([i, r.get("ref", ""), a, b, ""])
    (out / "key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
    return len(pick)


def score(folder):
    folder = Path(folder)
    key = json.loads((folder / "key.json").read_text(encoding="utf-8"))
    win = lose = tie = 0
    with (folder / "sheet.csv").open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            choice = (row.get("choice (A, B or =)") or "").strip().upper()
            if choice not in ("A", "B", "="):
                continue
            if choice == "=":
                tie += 1
            elif choice == key[row["n"]]["candidate"]:
                win += 1
            else:
                lose += 1
    rate = 100 * win / (win + lose) if win + lose else None
    return {"wins": win, "losses": lose, "ties": tie, "win_rate": rate, "passes_60": rate is not None and rate >= 60}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make")
    m.add_argument("results")
    m.add_argument("--out", required=True)
    m.add_argument("-n", type=int, default=100)
    s = sub.add_parser("score")
    s.add_argument("folder")
    args = ap.parse_args(argv)
    if args.cmd == "make":
        print(f"{make(args.results, args.out, args.n)} lines in {Path(args.out) / 'sheet.csv'}")
    else:
        r = score(args.folder)
        print(json.dumps(r))
        return r


if __name__ == "__main__":
    sys.exit(0 if main() is not False else 1)
