"""Vocabulary: parser problems (VOC-4), wildcard and longest-first rules (VOC-10), reload without a
restart (VOC-3) and the editor's bridge calls (VOC-1). All terms here are fictional."""
import os
import time
from pathlib import Path

import pytest

import live_subs
from live_subs import Vocabulary, parse_glossary, parse_vocabulary


def problems(text):
    return parse_vocabulary(text)[1]


def only_problem(text):
    found = problems(text)
    assert len(found) == 1, found
    return found[0]


# ------------------------------------------------------------------ VOC-4: parser problems
def test_clean_file_has_no_problems_and_keeps_every_rule():
    rules, found = parse_vocabulary("# header\n[keep]\nNordlicht GmbH\n[german]\nKalibra = Calibra\n"
                                    "[english]\nunrolled = rolled out\n")
    assert found == []
    assert [(r.section, r.src, r.dst) for r in rules] == [
        ("keep", "Nordlicht GmbH", ""), ("german", "Kalibra", "Calibra"), ("english", "unrolled", "rolled out")]


def test_inline_comment_is_stripped_from_the_target():
    rules, found = parse_vocabulary("[german]\nKalibra = Calibra   # the product, not the town\n")
    assert found == [] and rules[0].dst == "Calibra"
    v = Vocabulary(text="[german]\nKalibra = Calibra  # note\n")
    assert v.finish(*v.protect("Kalibra")) == "Calibra"


def test_escaped_hash_is_part_of_the_term():
    rules, found = parse_vocabulary("[keep]\nC\\#! = C#\n[english]\nsea sharp = C\\#  # language\n")
    assert found == []
    assert (rules[0].src, rules[1].dst) == ("C#", "C#")


def test_indented_comment_lines_are_not_rules_or_hotwords():
    assert parse_vocabulary("[keep]\n   # Nordlicht GmbH\n\t# Kalibra\n") == ([], [])
    terms, found = parse_glossary("Nordlicht GmbH\n   # not a term\n  Kalibra  # spelled like this\n")
    assert terms == ["Nordlicht GmbH", "Kalibra"] and found == []


def test_german_without_equals_is_reported_not_applied():
    p = only_problem("[german]\n\nKalibra\n")
    assert p["line"] == 3 and p["text"] == "Kalibra" and "=" in p["message"]
    assert Vocabulary(text="[german]\nKalibra\n").lock == []


def test_german_with_empty_target_is_reported_not_applied():
    assert only_problem("[german]\nKalibra =\n")["line"] == 2
    assert Vocabulary(text="[german]\nKalibra =\n").lock == []


def test_english_without_equals_is_reported_not_applied():
    assert only_problem("[english]\nunrolled\n")["line"] == 2
    assert Vocabulary(text="[english]\nunrolled\n").finish("it was unrolled") == "it was unrolled"


def test_english_with_empty_target_is_reported_never_deletes_words():
    assert only_problem("[english]\nunrolled =   # oops\n")["line"] == 2
    assert Vocabulary(text="[english]\nunrolled =\n").finish("it was unrolled") == "it was unrolled"


def test_unknown_section_is_reported_and_its_lines_ignored():
    text = "[keep]\nNordlicht\n[germna]\nKalibra = Calibra\n[english]\nunrolled = rolled out\n"
    p = only_problem(text)
    assert p["line"] == 3 and "[germna]" in p["text"]
    v = Vocabulary(text=text)
    assert len(v.lock) == 1 and len(v.fix) == 1


def test_rule_before_any_section_is_reported():
    assert only_problem("Kalibra = Calibra\n[keep]\nNordlicht\n")["line"] == 1


def test_short_term_is_reported_unless_marked_with_bang():
    p = only_problem("[german]\nKX = Kalibra X\n")
    assert p["line"] == 2 and "!" in p["message"]
    assert Vocabulary(text="[german]\nKX = Kalibra X\n").lock == []
    rules, found = parse_vocabulary("[german]\nKX! = Kalibra X\n")
    assert found == [] and rules[0].src == "KX"
    v = Vocabulary(text="[german]\nKX! = Kalibra X\n")
    assert v.finish(*v.protect("Das KX läuft")) == "Das Kalibra X läuft"


def test_glossary_reports_rules_and_sections():
    terms, found = parse_glossary("Nordlicht\nKalibra = Calibra\n[keep]\n")
    assert terms == ["Nordlicht"] and [p["line"] for p in found] == [2, 3]


def test_the_example_files_are_clean():
    root = Path(live_subs.__file__).parent
    assert problems((root / "vocabulary.example.txt").read_text(encoding="utf-8")) == []
    assert parse_glossary((root / "glossary.example.txt").read_text(encoding="utf-8"))[1] == []
    assert "then restart" not in (root / "vocabulary.example.txt").read_text(encoding="utf-8")


# ------------------------------------------------------------------ VOC-10: wildcards + order
def test_wildcard_matches_every_ending_on_word_boundaries():
    v = Vocabulary(text="[german]\nkalib* = great\n")
    for word in ("kalib", "kalibe", "Kaliben", "KALIBES"):
        de, slots = v.protect(f"Das ist {word}.")
        assert v.finish(de, slots) == "Das ist great.", word
    de, slots = v.protect("Das ist unkaliben.")          # starts mid-word: not this rule
    assert slots == []


def test_wildcard_keep_keeps_the_spoken_form():
    v = Vocabulary(text="[keep]\nNordlicht*\n")
    de, slots = v.protect("Die Nordlichts kommen")
    assert slots == ["Nordlichts"] and "Nordlichts" not in de


def test_wildcard_needs_three_letters_before_the_star():
    assert "!" in only_problem("[german]\nka* = great\n")["message"]
    assert only_problem("[keep]\n*\n")["line"] == 2


def test_english_rules_apply_longest_first():
    v = Vocabulary(text="[english]\ngame = match\nan game changer = a game changer\n")
    assert v.finish("This is an game changer") == "This is a game changer"
    assert v.finish("A good game") == "A good match"
    assert v.finish("an game changer, the game") == "a game changer, the match"   # one pass, no re-matching


def test_locks_apply_longest_first():
    v = Vocabulary(text="[german]\nNord = north\nNordlicht GmbH = Northern Light Ltd\n")
    assert v.finish(*v.protect("Die Nordlicht GmbH und Nord")) == "Die Northern Light Ltd und north"


def test_case_is_ignored_as_before():
    v = Vocabulary(text="[english]\nunrolled = rolled out\n")
    assert v.finish("UNROLLED today") == "rolled out today"


# ------------------------------------------------------------------ VOC-3: reload without restart
def _bare_engine(cfg, monkeypatch):
    """A real live_subs.Engine without models: only reload_terms/vocab are exercised."""
    monkeypatch.setenv("DOLMI_CONFIG", str(cfg))
    eng = live_subs.Engine.__new__(live_subs.Engine)
    eng.reload_terms()
    return eng


def test_reload_terms_swaps_in_new_rules(tmp_path, monkeypatch):
    (tmp_path / "vocabulary.txt").write_text("[german]\nKalibra = Calibra\n", encoding="utf-8")
    (tmp_path / "glossary.txt").write_text("Nordlicht\n  # hidden\n", encoding="utf-8")
    eng = _bare_engine(tmp_path, monkeypatch)
    assert eng.glossary == "Nordlicht" and "nordlicht" in eng.terms
    old = eng.vocab
    (tmp_path / "vocabulary.txt").write_text("[german]\nKalibra = Kalibra Pro\n", encoding="utf-8")
    eng.reload_terms()
    assert eng.vocab is not old
    assert eng.vocab.finish(*eng.vocab.protect("Kalibra")) == "Kalibra Pro"
    assert old.finish(*old.protect("Kalibra")) == "Calibra"     # a line mid-translation keeps its rules


def test_reload_terms_keeps_old_rules_when_a_file_cannot_be_read(tmp_path, monkeypatch):
    (tmp_path / "vocabulary.txt").write_text("[german]\nKalibra = Calibra\n", encoding="utf-8")
    eng = _bare_engine(tmp_path, monkeypatch)
    (tmp_path / "vocabulary.txt").write_bytes(b"[german]\n\xff\xfe broken")
    eng.reload_terms()
    assert eng.vocab.finish(*eng.vocab.protect("Kalibra")) == "Calibra"
