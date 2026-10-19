"""VOC-9: the clarify queue. During a meeting it collects the words Dolmi was unsure about (low Whisper word
probability, or an unknown capitalised term) and after Stop asks "Did they say ...?" once per sound.

Words that sound alike are one entry: they are grouped by their Kölner Phonetik code. A code that matches one
of the user's own spelling hints or rules suggests that term (and its group); the terms come from the user's
local vocabulary.txt / glossary.txt at runtime, nothing is built in. Pure Python, no models."""
import re
import threading

LOW_PROB = 0.5          # a word Whisper gave less than this is "unsure"
MAX_ITEMS = 12          # longest list shown after a meeting
MIN_LEN = 3             # shorter words are too often fillers to ask about
GROUPS = ("gloss", "keep", "german", "english")

_FOLD = str.maketrans({"Ä": "A", "Ö": "O", "Ü": "U", "ß": "S"})


def koelner(word):
    """Kölner Phonetik code of a word ('' for no letters): German sound-alikes share a code."""
    s = re.sub(r"[^A-Z]", "", str(word).upper().translate(_FOLD))
    raw = []
    for i, c in enumerate(s):
        prev = s[i - 1] if i else ""
        nxt = s[i + 1] if i + 1 < len(s) else ""
        if c in "AEIJOUY":
            d = "0"
        elif c == "H":
            continue
        elif c == "B":
            d = "1"
        elif c == "P":
            d = "3" if nxt == "H" else "1"
        elif c in "DT":
            d = "8" if nxt in ("C", "S", "Z") and nxt else "2"
        elif c in "FVW":
            d = "3"
        elif c in "GKQ":
            d = "4"
        elif c == "C":
            if i == 0:
                d = "4" if nxt and nxt in "AHKLOQRUX" else "8"
            else:
                d = "4" if nxt and nxt in "AHKOQUX" and prev not in ("S", "Z") else "8"
        elif c == "X":
            d = "8" if prev and prev in "CKQ" else "48"
        elif c == "L":
            d = "5"
        elif c in "MN":
            d = "6"
        elif c == "R":
            d = "7"
        else:            # S, Z
            d = "8"
        raw.append(d)
    out = []
    for ch in "".join(raw):
        if not out or out[-1] != ch:
            out.append(ch)
    code = "".join(out)
    return code[:1] + code[1:].replace("0", "") if code else ""


def known_terms(items):
    """(group, term, target) rows (app._vocab_items) -> {code: (term, group)}, first row of a code wins.
    A multi-word term is indexed by its whole sound and by each of its words of MIN_LEN letters."""
    index = {}
    for group, term, _ in items:
        for part in [term] + (term.split() if " " in term else []):
            code = koelner(part)
            if code and len(part) >= MIN_LEN:
                index.setdefault(code, (term, group))
    return index


class Queue:
    """Thread-safe: the ASR worker adds, the UI reads after Stop."""

    def __init__(self, items=(), ignored=()):
        self._lock = threading.Lock()
        self._known = known_terms(items)
        self._known_words = {w.lower() for _, t, _ in items for w in re.findall(r"\w+", t)}
        self._ignored = {w.strip().lower() for w in ignored if w.strip()}   # exact words, not sounds
        self._entries = {}      # code -> entry dict, in first-heard order

    def add(self, words, line):
        """words: [(word, probability)] of one final line; line: the line's text (shown as context)."""
        with self._lock:
            for i, (word, prob) in enumerate(words):
                w = word.strip().strip(".,;:!?\"'()[]«»„“”")
                if len(w) < MIN_LEN or not re.fullmatch(r"[^\W\d_][\w\-]*", w) or w.lower() in self._known_words:
                    continue
                unsure = prob is not None and prob < LOW_PROB
                odd_caps = i > 0 and (w.isupper() or re.search(r"[a-z][A-Z]", w))   # e.g. an acronym or "ProdX"
                if not (unsure or odd_caps):
                    continue
                code = koelner(w)
                if not code or w.lower() in self._ignored:
                    continue
                e = self._entries.get(code)
                if e:
                    e["count"] += 1
                    continue
                term, group = self._known.get(code, (w, "gloss"))
                self._entries[code] = {"code": code, "heard": w, "suggest": term, "group": group,
                                       "known": code in self._known, "context": line.strip(), "count": 1}

    def items(self):
        """The list for 'Did they say ...?': most often heard first, at most MAX_ITEMS."""
        with self._lock:
            es = list(self._entries.values())
        return sorted(es, key=lambda e: -e["count"])[:MAX_ITEMS]

    def drop(self, word):
        """Forget the entry for this word's sound (answered or skipped)."""
        with self._lock:
            self._entries.pop(koelner(word), None)


class Exists(ValueError):
    """The group already has a rule for this word."""


def add_rule(vocabulary, glossary, group, term, target=""):
    """(vocabulary, glossary) texts with one new term or rule in `group`; the other text is None
    (= unchanged, for save_vocab). A [section] that exists gets the line at its end, else it is added."""
    rule = group in ("german", "english")
    term = str(term).strip().replace("#", "\\#")
    target = str(target).strip().replace("#", "\\#") if rule else ""   # a target only means something for a rule
    if group not in GROUPS or not term or (rule and not target):
        raise ValueError("group/term/target")

    def source(l):
        return l.split("#")[0].split("=")[0].strip().lower()

    if group == "gloss":
        if term.lower() in {source(l) for l in glossary.splitlines()}:
            return None, None                                  # already a spelling hint: nothing to write
        return None, (glossary.rstrip() + "\n" if glossary.strip() else "") + term
    line = f"{term} = {target}" if target else term
    lines = vocabulary.rstrip().splitlines()
    head = next((n for n, l in enumerate(lines) if l.split("#")[0].strip().lower() == f"[{group}]"), None)
    if head is None:
        return "\n".join(lines + ([""] if lines else []) + [f"[{group}]", line]), None
    end = head + 1
    while end < len(lines) and not lines[end].strip().startswith("["):
        end += 1
    if term.lower() in {source(l) for l in lines[head + 1:end]}:
        if rule:
            raise Exists(term)                                 # a second rule for one word would conflict
        return None, None                                      # already kept: no duplicate line
    while end > head + 1 and not lines[end - 1].strip():
        end -= 1
    return "\n".join(lines[:end] + [line] + lines[end:]), None
