"""Scoring for the Dolmi test bench, in pure Python (no jiwer/sacrebleu so the app venv stays as is).

    normalize(text, lang)        lowercase, punctuation stripped, numbers as spoken words, ß -> ss
    wer(refs, hyps, lang)        word error rate in % over a corpus (sum of edits / sum of ref words)
    cer(refs, hyps, lang)        character error rate in % (spaces count as characters)
    chrf(refs, hyps)             chrF (char order 6, beta 2) as sacrebleu computes it by default
    bleu(refs, hyps)             corpus BLEU-4 with the 13a tokenizer and exp smoothing, like sacrebleu
    term_accuracy(items)         share of expected terms found in the output

The normalizer is the one stated in eval/README.md; change it only together with baseline.json.
"""
import math
import re
import unicodedata
from collections import Counter

# ----------------------------------------------------------------- numbers as spoken
_DE_UNITS = ["null", "eins", "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht", "neun",
             "zehn", "elf", "zwölf", "dreizehn", "vierzehn", "fünfzehn", "sechzehn", "siebzehn",
             "achtzehn", "neunzehn"]
_DE_TENS = ["", "", "zwanzig", "dreißig", "vierzig", "fünfzig", "sechzig", "siebzig", "achtzig", "neunzig"]
_EN_UNITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
             "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
             "nineteen"]
_EN_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _de_below_1000(n, compound):
    if n < 20:
        word = _DE_UNITS[n]
        return "ein" if compound and n == 1 else word
    if n < 100:
        tens, unit = divmod(n, 10)
        return (("ein" if unit == 1 else _DE_UNITS[unit]) + "und" if unit else "") + _DE_TENS[tens]
    hundreds, rest = divmod(n, 100)
    head = "hundert" if hundreds == 1 else _DE_UNITS[hundreds] + "hundert"
    return head + (_de_below_1000(rest, compound) if rest else "")


def german_number(n):
    """12 -> 'zwölf', 2026 -> 'zweitausendsechsundzwanzig', 1500000 -> 'eine million fünfhunderttausend'."""
    if n < 1000:
        return _de_below_1000(n, compound=False)
    if n < 1_000_000:
        thousands, rest = divmod(n, 1000)
        head = "tausend" if thousands == 1 else _de_below_1000(thousands, compound=True) + "tausend"
        return head + (_de_below_1000(rest, compound=False) if rest else "")
    if n < 1_000_000_000:
        millions, rest = divmod(n, 1_000_000)
        head = "eine million" if millions == 1 else german_number(millions) + " millionen"
        return head + (" " + german_number(rest) if rest else "")
    return " ".join(_DE_UNITS[int(d)] for d in str(n))


def english_number(n):
    """21 -> 'twenty one', 2026 -> 'two thousand twenty six' (hyphens become spaces)."""
    if n < 20:
        return _EN_UNITS[n]
    if n < 100:
        tens, unit = divmod(n, 10)
        return _EN_TENS[tens] + (" " + _EN_UNITS[unit] if unit else "")
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        return _EN_UNITS[hundreds] + " hundred" + (" " + english_number(rest) if rest else "")
    for size, word in ((1_000_000_000, "billion"), (1_000_000, "million"), (1000, "thousand")):
        if n >= size:
            head, rest = divmod(n, size)
            if head >= 1000 and size == 1_000_000_000:
                break
            return english_number(head) + " " + word + (" " + english_number(rest) if rest else "")
    return " ".join(_EN_UNITS[int(d)] for d in str(n))


def german_ordinal(n):
    """3 -> 'dritte', 20 -> 'zwanzigste' (base form; inflected forms are folded onto it)."""
    special = {1: "erste", 3: "dritte", 7: "siebte", 8: "achte"}
    if n in special:
        return special[n]
    word = german_number(n)
    return word + ("te" if n < 20 else "ste")


_ORDINALS_DE = sorted({german_ordinal(n) for n in range(1, 101)}, key=len, reverse=True)
_ORDINAL_INFLECTED = re.compile(r"\b(" + "|".join(map(re.escape, _ORDINALS_DE)) + r")[nrsm]\b")
_MONTHS_DE = ("januar", "februar", "märz", "april", "mai", "juni", "juli", "august", "september",
              "oktober", "november", "dezember")


def _spell_numbers(text, lang):
    number = german_number if lang == "de" else english_number
    point, comma = ("punkt", "komma") if lang == "de" else ("point", "point")

    def thousands(m):   # German "1.500" / English "1,500" = one thousand five hundred
        return m.group(0).replace(".", "").replace(",", "")
    sep = r"\." if lang == "de" else ","
    text = re.sub(rf"\b\d{{1,3}}(?:{sep}\d{{3}})+\b(?![.,]\d)", thousands, text)
    if lang == "de":   # "am 3. März", "der 2. Punkt" -> ordinal
        def ordinal(m):
            return german_ordinal(int(m.group(1))) + " "
        months = "(?i:" + "|".join(_MONTHS_DE) + ")"   # runs before lowercasing: "3. Dann" ends a sentence
        text = re.sub(r"\b(\d{1,2})\.\s+(?=(?:" + months + r"|[a-zäöü]))", ordinal, text)
    text = re.sub(r"(\d)\.(\d)", rf"\1 {point} \2", text)
    text = re.sub(r"(\d),(\d)", rf"\1 {comma} \2", text)
    text = re.sub(r"\b(\d+)(st|nd|rd|th)\b", r"\1", text) if lang == "en" else text
    return re.sub(r"\d+", lambda m: " " + number(int(m.group(0))) + " ", text)


def normalize(text, lang="de"):
    """Text as compared for WER/CER: lowercase, numbers as spoken words, symbols spelled out,
    punctuation and hyphens removed, ß -> ss, single spaces. lang: 'de' or 'en' (number words)."""
    lang = "en" if lang == "en" else "de"
    text = unicodedata.normalize("NFKC", text or "")
    words = {"%": " prozent ", "€": " euro ", "&": " und ", "+": " plus "} if lang == "de" else \
            {"%": " percent ", "€": " euros ", "&": " and ", "+": " plus ", "$": " dollars "}
    for sym, word in words.items():
        text = text.replace(sym, word)
    text = _spell_numbers(text, lang).lower()
    if lang == "de":
        text = _ORDINAL_INFLECTED.sub(r"\1", text)
    text = text.replace("ß", "ss")
    text = re.sub(r"[^\w\s]|_", " ", text)
    return " ".join(text.split())


# ----------------------------------------------------------------- edit distance
def edit_ops(ref, hyp):
    """(substitutions, deletions, insertions) turning the ref sequence into the hyp sequence."""
    prev = [(j, 0, 0, j) for j in range(len(hyp) + 1)]   # (cost, subs, dels, ins)
    for i, r in enumerate(ref, 1):
        cur = [(i, 0, i, 0)]
        for j, h in enumerate(hyp, 1):
            if r == h:
                cur.append(prev[j - 1])
                continue
            s, d, n = prev[j - 1], prev[j], cur[j - 1]
            best = min((s[0], 0), (d[0], 1), (n[0], 2))[1]
            c = (s, d, n)[best]
            cur.append((c[0] + 1, c[1] + (best == 0), c[2] + (best == 1), c[3] + (best == 2)))
        prev = cur
    _, subs, dels, ins = prev[-1]
    return subs, dels, ins


def align(ref, hyp):
    """Minimum-edit alignment as [(i, j)] pairs: (i, j) match or substitution, (i, None) deletion,
    (None, j) insertion. O(len(ref) * len(hyp)) time; backpointers are kept in bytearrays."""
    n, m = len(ref), len(hyp)
    back = [bytearray(m + 1) for _ in range(n + 1)]   # 0 diag, 1 up (deletion), 2 left (insertion)
    prev = list(range(m + 1))
    for j in range(1, m + 1):
        back[0][j] = 2
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        row, r = back[i], ref[i - 1]
        row[0] = 1
        for j in range(1, m + 1):
            diag = prev[j - 1] + (r != hyp[j - 1])
            up, left = prev[j] + 1, cur[j - 1] + 1
            if diag <= up and diag <= left:
                cur[j] = diag
            elif up <= left:
                cur[j], row[j] = up, 1
            else:
                cur[j], row[j] = left, 2
        prev = cur
    pairs, i, j = [], n, m
    while i or j:
        step = back[i][j]
        if step == 0:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif step == 1:
            pairs.append((i - 1, None))
            i -= 1
        else:
            pairs.append((None, j - 1))
            j -= 1
    return pairs[::-1]


def error_counts(refs, hyps, lang="de", unit="word"):
    """{'edits', 'ref_len', 'subs', 'dels', 'ins'} summed over pairs, after normalize()."""
    total = Counter()
    for ref, hyp in zip(refs, hyps):
        r, h = normalize(ref, lang), normalize(hyp, lang)
        r, h = (r.split(), h.split()) if unit == "word" else (list(r), list(h))
        s, d, i = edit_ops(r, h)
        total.update(subs=s, dels=d, ins=i, edits=s + d + i, ref_len=len(r))
    return dict(total)


def _rate(refs, hyps, lang, unit):
    c = error_counts(refs, hyps, lang, unit)
    if not c.get("ref_len"):
        return 0.0 if not c.get("edits") else 100.0
    return 100.0 * c["edits"] / c["ref_len"]


def wer(refs, hyps, lang="de"):
    """Corpus word error rate in percent."""
    return _rate(_as_list(refs), _as_list(hyps), lang, "word")


def cer(refs, hyps, lang="de"):
    """Corpus character error rate in percent (on the normalized text, spaces included)."""
    return _rate(_as_list(refs), _as_list(hyps), lang, "char")


def _as_list(x):
    return [x] if isinstance(x, str) else list(x)


# ----------------------------------------------------------------- chrF (sacrebleu defaults)
def _char_ngrams(text, n):
    text = "".join(text.split())
    return Counter(text[i:i + n] for i in range(len(text) - n + 1))


def chrf_stats(ref, hyp, order=6):
    """[hyp_count, ref_count, matches] for each n in 1..order."""
    stats = []
    for n in range(1, order + 1):
        h, r = _char_ngrams(hyp, n), _char_ngrams(ref, n)
        n_ref = sum(r.values())   # like sacrebleu: hyp n-grams don't count where the ref has none
        stats += [sum(h.values()) if n_ref else 0, n_ref, sum((h & r).values())]
    return stats


def chrf_from_stats(stats, order=6, beta=2.0):
    factor = beta ** 2
    avg_p = avg_r = 0.0
    effective = 0
    for i in range(order):
        n_hyp, n_ref, n_match = stats[3 * i:3 * i + 3]
        if n_hyp > 0 and n_ref > 0:
            avg_p += n_match / n_hyp
            avg_r += n_match / n_ref
            effective += 1
    if not effective:
        return 0.0
    avg_p, avg_r = avg_p / effective, avg_r / effective
    if avg_p + avg_r == 0:
        return 0.0
    return 100.0 * (1 + factor) * avg_p * avg_r / (factor * avg_p + avg_r)


def chrf(refs, hyps, order=6, beta=2.0):
    """Corpus chrF: n-gram statistics summed over all sentences, then one score (sacrebleu's CHRF())."""
    total = [0] * (3 * order)
    for ref, hyp in zip(_as_list(refs), _as_list(hyps)):
        total = [a + b for a, b in zip(total, chrf_stats(ref, hyp, order))]
    return chrf_from_stats(total, order, beta)


# ----------------------------------------------------------------- BLEU (sacrebleu 13a + exp)
_13A = [
    (re.compile(r"([\{-\~\[-\` -\&\(-\+\:-\@\/])"), r" \1 "),
    (re.compile(r"([^0-9])([\.,])"), r"\1 \2 "),
    (re.compile(r"([\.,])([^0-9])"), r" \1 \2"),
    (re.compile(r"([0-9])(-)"), r"\1 \2 "),
]


def tokenize_13a(line):
    """mteval-v13a tokenization, as sacrebleu's default tokenizer does it."""
    line = line.replace("<skipped>", "").replace("-\n", "").replace("\n", " ")
    if "&" in line:
        line = line.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    line = f" {line} "
    for pattern, repl in _13A:
        line = pattern.sub(repl, line)
    return line.split()


def bleu_stats(ref, hyp, max_order=4):
    """[hyp_len, ref_len, matches_1..4, totals_1..4] for one sentence."""
    r, h = tokenize_13a(ref), tokenize_13a(hyp)
    matches, totals = [], []
    for n in range(1, max_order + 1):
        hc = Counter(tuple(h[i:i + n]) for i in range(len(h) - n + 1))
        rc = Counter(tuple(r[i:i + n]) for i in range(len(r) - n + 1))
        matches.append(sum((hc & rc).values()))
        totals.append(max(0, len(h) - n + 1))
    return [len(h), len(r)] + matches + totals


def bleu_from_stats(stats, max_order=4, effective_order=False):
    sys_len, ref_len = stats[0], stats[1]
    matches, totals = stats[2:2 + max_order], stats[2 + max_order:2 + 2 * max_order]
    precisions = [0.0] * max_order
    smooth, order = 1.0, max_order
    for n in range(1, max_order + 1):
        if totals[n - 1] == 0:
            break
        if effective_order:
            order = n
        if matches[n - 1] == 0:
            smooth *= 2
            precisions[n - 1] = 100.0 / (smooth * totals[n - 1])
        else:
            precisions[n - 1] = 100.0 * matches[n - 1] / totals[n - 1]
    if sys_len == 0 or not any(matches):   # sacrebleu stops early when nothing matches
        return 0.0
    bp = 1.0 if sys_len >= ref_len else math.exp(1 - ref_len / sys_len)
    logs = [math.log(p) if p > 0 else -9999999999 for p in precisions[:order]]
    return bp * math.exp(sum(logs) / order)


def bleu(refs, hyps, max_order=4):
    """Corpus BLEU (single reference), sacrebleu's BLEU() defaults: 13a, exp smoothing, no lowercasing."""
    total = [0] * (2 + 2 * max_order)
    for ref, hyp in zip(_as_list(refs), _as_list(hyps)):
        total = [a + b for a, b in zip(total, bleu_stats(ref, hyp, max_order))]
    return bleu_from_stats(total, max_order)


def sentence_bleu(ref, hyp, max_order=4):
    """Sentence BLEU with effective order (sacrebleu's sentence_bleu)."""
    return bleu_from_stats(bleu_stats(ref, hyp, max_order), max_order, effective_order=True)


# ----------------------------------------------------------------- terms
def term_found(term, text, lang="de"):
    """True when the (normalized) term occurs as whole words in the (normalized) text."""
    t, x = normalize(term, lang), normalize(text, lang)
    return bool(t) and re.search(rf"(?<!\w){re.escape(t)}(?!\w)", x) is not None


def term_accuracy(items, lang="de"):
    """items: iterable of (expected_terms, output_text). Returns (found, expected, percent)."""
    found = expected = 0
    for terms, text in items:
        for term in terms:
            expected += 1
            found += term_found(term, text, lang)
    return found, expected, (100.0 * found / expected if expected else None)


# ----------------------------------------------------------------- latency
def percentile(values, p):
    """Nearest-rank-with-interpolation percentile of a list (None for an empty list)."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    k = (len(vals) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)
