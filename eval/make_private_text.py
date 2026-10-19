"""Build a PRIVATE text test set from your own meeting transcripts (QA-9). Nothing it reads or writes may
enter the repo: the output must be outside this checkout, and only aggregate numbers are printed.

    python eval/make_private_text.py --out D:\\private\\qa-private\\meetings.json \\
        --transcripts "%USERPROFILE%\\Documents\\Dolmi" --review <folder with findings.md + vocabulary.txt>

There is no saved meeting audio, only transcripts, so this is a text set for the translation half
(eval/run_eval.py --set private:<file>): German lines as heard, with
  * en_terms  the English each reviewed Vocabulary rule should produce for a term found in the line
              ([keep] term -> itself, [german] X = Y and [keep] X = Y -> Y);
  * avoid     for the lines the review marked "mistranslated": the wrong English the app produced, so a
              translator scores "known mistakes avoided";
  * hint      the review's "most likely" meaning, for the blind A/B (eval/ab_blind.py) only.
The reviewed vocabulary.txt and glossary.txt go into the set, so the engine uses those rules.
Use only meetings whose participants agreed.
"""
import argparse
import json
import re
import sys
from pathlib import Path

EVAL = Path(__file__).resolve().parent
ROOT = EVAL.parent
sys.path.insert(0, str(ROOT))

LINE = re.compile(r"\*\*(\d\d:\d\d:\d\d)(?:–(\d\d:\d\d:\d\d))?\*\*\s+(.*?)\s*\n<sub>(.*?)</sub>", re.S)
SECTION = re.compile(r"^###\s+(meeting_[\w-]+\.md)")
TIME = re.compile(r"\b(\d\d:\d\d:\d\d)\b")
QUOTED_EN = re.compile(r'EN\s+((?:"[^"]+"(?:\s*,\s*)?)+)')
QUOTE = re.compile(r'"([^"]+)"')


def read_meeting(path):
    """[(start, end, en, de)] from a saved meeting .md (the app's own format)."""
    text = Path(path).read_text(encoding="utf-8")
    return [(m[1], m[2] or m[1], m[3].strip(), m[4].strip()) for m in LINE.finditer(text)]


def mistranslations(findings):
    """[(meeting file, time, [wrong English], most likely)] for the review's 'mistranslated' rows."""
    out, current = [], None
    for line in Path(findings).read_text(encoding="utf-8").splitlines():
        m = SECTION.match(line)
        if m:
            current = m[1]
            continue
        if not line.startswith("|") or "mistranslated" not in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or not current:
            continue
        t = TIME.search(cells[0])
        q = QUOTED_EN.search(cells[1])
        if not t or not q:
            continue
        out.append((current, t[1], QUOTE.findall(q[1]), cells[2]))
    return out


def term_rules(vocabulary_text):
    """[(pattern, expected English)] from a vocabulary.txt: [keep] terms and [german]/[keep] X = Y."""
    import live_subs
    rules, _ = live_subs.parse_vocabulary(vocabulary_text)
    out = []
    for r in rules:
        if r.section == "english" or "*" in r.src:
            continue
        out.append((live_subs.term_pattern(r.src), r.dst or r.src))
    return out


def build(transcripts, findings=None, vocabulary="", glossary="", meetings=None):
    """The text set dict (see eval/translators.py) and counts for the log."""
    files = sorted(Path(transcripts).glob("meeting_*.md"))
    if meetings:
        files = [f for f in files if f.name in meetings]
    rules = term_rules(vocabulary) if vocabulary else []
    lines, by_time = [], {}
    for f in files:
        for k, (start, end, en, de) in enumerate(read_meeting(f)):
            if not de or len(de.split()) < 3:
                continue
            terms = sorted({want for pat, want in rules if pat.search(de)})
            row = {"id": f"{f.stem}-{k}", "de": de, "en": None, "en_terms": terms, "avoid": [],
                   "tags": ["meeting"] + (["terms"] if terms else []), "app_en": en}
            by_time[(f.name, start)] = row
            lines.append(row)
    mapped = 0
    for meeting, t, wrong, hint in (mistranslations(findings) if findings else []):
        row = by_time.get((meeting, t))
        if row is None:            # the review's time can be inside a line: take the line that started before
            cands = [(s, r) for (m, s), r in by_time.items() if m == meeting and s <= t]
            row = max(cands, key=lambda x: x[0])[1] if cands else None
        if row is None:
            continue
        found = [w for w in wrong if w.lower() in row["app_en"].lower()]
        if not found:
            continue
        row["avoid"] = sorted(set(row["avoid"]) | set(found))
        row["hint"] = hint
        if "mistranslated" not in row["tags"]:
            row["tags"].append("mistranslated")
            mapped += 1
    keep = [r for r in lines if r["en_terms"] or r["avoid"]]
    for r in keep:
        r.pop("app_en", None)
    data = {"kind": "text", "about": "PRIVATE meeting text set (QA-9). Never commit.", "lines": keep,
            "vocabulary": vocabulary, "glossary": glossary}
    counts = {"meetings": len(files), "lines_read": len(lines), "lines_kept": len(keep),
              "with_terms": sum(bool(r["en_terms"]) for r in keep), "mistranslated": mapped,
              "review_rows": len(mistranslations(findings)) if findings else 0}
    return data, counts


def inside_repo(path):
    try:
        Path(path).resolve().relative_to(ROOT.resolve())
    except ValueError:
        return False
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, help="the .json to write (outside this checkout)")
    ap.add_argument("--transcripts", required=True, help="folder with meeting_*.md (read-only)")
    ap.add_argument("--review", help="folder with findings.md, vocabulary.txt, glossary.txt from the vocabulary review")
    ap.add_argument("--meetings", nargs="*", help="only these meeting_*.md file names")
    args = ap.parse_args(argv)
    if inside_repo(args.out):
        raise SystemExit("Refusing to write a private test set inside the repo: pick a folder outside it")
    review = Path(args.review) if args.review else None
    read = lambda name: (review / name).read_text(encoding="utf-8") if review and (review / name).exists() else ""
    data, counts = build(args.transcripts, review / "findings.md" if review and (review / "findings.md").exists() else None,
                         read("vocabulary.txt"), read("glossary.txt"), args.meetings)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print("Private text set written (counts only): " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return counts


if __name__ == "__main__":
    main()
