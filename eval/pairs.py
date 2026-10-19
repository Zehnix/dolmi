"""STR-11: per-language-pair translation smoke for every spoken language Dolmi offers.

    python eval/pairs.py                 # 50 FLEURS sentences per language, own Opus-MT vs opus-mul, chrF
    python eval/pairs.py --limit 20 --langs fr,pt --no-download

Text only: the FLEURS (google/fleurs, CC BY 4.0) test transcriptions of each language and the en_us
transcriptions of the same FLoRes sentence ids (de_de for English, which gets German subtitles). Only the
small .tsv files are fetched, no audio. Models come from the app's catalogue (official CTranslate2 copies);
--no-download skips a language whose translator is not installed. Prints a Markdown table and writes it
to eval/results/pairs.md (git-ignored); the committed summary is eval/pairs_summary.md.
"""
import argparse
import csv
import sys
import time
from pathlib import Path

EVAL = Path(__file__).resolve().parent
ROOT = EVAL.parent
sys.path[:0] = [str(EVAL), str(ROOT)]
import metrics  # noqa: E402
import models  # noqa: E402
import translation_router  # noqa: E402

REPO = "google/fleurs"
FLEURS = {"de": "de_de", "fr": "fr_fr", "es": "es_419", "it": "it_it", "nl": "nl_nl", "pl": "pl_pl",
          "pt": "pt_br", "ru": "ru_ru", "uk": "uk_ua", "tr": "tr_tr", "ar": "ar_eg", "fa": "fa_ir",
          "ur": "ur_pk", "hi": "hi_in", "zh": "cmn_hans_cn", "ja": "ja_jp", "ko": "ko_kr", "sv": "sv_se",
          "da": "da_dk", "fi": "fi_fi", "cs": "cs_cz", "hu": "hu_hu", "ro": "ro_ro", "el": "el_gr",
          "bg": "bg_bg", "id": "id_id", "vi": "vi_vn", "en": "en_us"}
GOOD, FAIR = 50.0, 40.0          # chrF: >= GOOD 'good', >= FAIR 'fair', else 'experimental'


def sentences(code):
    """{FLoRes id: raw transcription} of one FLEURS test split (first row per id)."""
    from huggingface_hub import hf_hub_download
    path = hf_hub_download(REPO, f"data/{code}/test.tsv", repo_type="dataset")
    out = {}
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
            if len(row) >= 3 and row[0] not in out:
                out[row[0]] = row[2].strip()
    return out


def load(key, download):
    m = models.BY_KEY[key]
    if not models.is_installed(m):
        if not download:
            return None
        models.download(m)
    import ctranslate2
    import sentencepiece as spm
    from huggingface_hub import snapshot_download
    d = Path(snapshot_download(m.repo, local_files_only=True))
    return (ctranslate2.Translator(str(d), device="cpu", compute_type="int8"),
            spm.SentencePieceProcessor(model_file=str(d / "source.spm")),
            spm.SentencePieceProcessor(model_file=str(d / "target.spm")))


def translate(t, lines):
    mt, sp_src, sp_tgt = t
    toks = [sp_src.encode(x, out_type=str)[:400] + ["</s>"] for x in lines]
    res = mt.translate_batch(toks, beam_size=2, max_decoding_length=256, max_batch_size=16)
    return [sp_tgt.decode([w for w in r.hypotheses[0] if w not in ("</s>", "<pad>")]) for r in res]


def tier(score, key):
    label = "good" if score >= GOOD else "fair" if score >= FAIR else "experimental"
    return f"{label} (opus-mul)" if key == "opus-mul" else label


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--langs", default=",".join(FLEURS))
    ap.add_argument("--no-download", action="store_true")
    args = ap.parse_args(argv)
    cache, rows, models_cache = {}, [], {}

    def refs_for(code):
        if code not in cache:
            cache[code] = sentences(code)
        return cache[code]

    def model(key):
        if key not in models_cache:
            models_cache[key] = load(key, not args.no_download)
        return models_cache[key]

    for lang in [x.strip() for x in args.langs.split(",") if x.strip()]:
        src = refs_for(FLEURS[lang])
        ref = refs_for("de_de" if lang == "en" else "en_us")
        ids = [i for i in src if i in ref][:args.limit]
        own = translation_router.pair_default(lang)
        cells = {"lang": lang, "name": models.LANGUAGES[lang], "n": len(ids), "own": own}
        for col, key in (("own_chrf", own), ("mul_chrf", None if lang == "en" else "opus-mul")):
            t = model(key) if key else None
            if t is None or not ids:
                cells[col] = None
                continue
            started = time.perf_counter()
            hyp = translate(t, [src[i] for i in ids])
            cells[col] = round(metrics.chrf([ref[i] for i in ids], hyp), 1)
            cells[col + "_s"] = round(time.perf_counter() - started, 1)
        best = cells["own_chrf"] if cells["own_chrf"] is not None else cells.get("mul_chrf")
        cells["tier"] = tier(best, own) if best is not None else "not measured"
        rows.append(cells)
        print(f"{lang}: own {own} {cells['own_chrf']}  opus-mul {cells.get('mul_chrf')}", flush=True)

    fmt = lambda v: "-" if v is None else f"{v:.1f}"
    lines = ["| Lang | Name | n | Translator | chrF own | chrF opus-mul | Tier |", "|---|---|---|---|---|---|---|"]
    lines += [f"| {r['lang']} | {r['name']} | {r['n']} | {r['own']} | {fmt(r['own_chrf'])} | "
              f"{fmt(r.get('mul_chrf'))} | {r['tier']} |" for r in rows]
    table = "\n".join(lines)
    print(table)
    out = EVAL / "results"
    out.mkdir(exist_ok=True)
    (out / "pairs.md").write_text(table + "\n", encoding="utf-8")
    return rows


if __name__ == "__main__":
    main()
