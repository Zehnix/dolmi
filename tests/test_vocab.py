"""Vocabulary: parser problems (VOC-4), wildcard and longest-first rules (VOC-10), reload without a
restart (VOC-3), the editor's bridge calls (VOC-1), the live summary (VOC-2), hotword budget and profiles
(VOC-5, VOC-11), placeholders that never lose a term (VOC-6) and cloud prompts (VOC-7). All terms are fictional."""
import os
import time
from pathlib import Path

import numpy as np
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


def test_leading_wildcard_is_reported_not_applied():
    assert "end of a word" in only_problem("[german]\n*kalib = great\n")["message"]
    assert "end of a word" in only_problem("[english]\nbig *thing = big deal\n")["message"]
    assert problems("[german]\nkalib* zone* = great\n") == []


def test_example_escaped_hash_rule_works():
    root = Path(live_subs.__file__).parent
    line = next(l for l in (root / "vocabulary.example.txt").read_text(encoding="utf-8").splitlines() if "\\#" in l)
    rule = line[line.index("(") + 1:line.rindex(")")]
    v = Vocabulary(text=f"[german]\n{rule}\n")
    assert v.problems == [] and v.finish(*v.protect("Kanal #1 bitte")).startswith("channel #1")


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


def test_watcher_reloads_the_engine_after_a_hand_edit(api, tmp_path, monkeypatch):
    import config
    eng = _bare_engine(config.CONFIG, monkeypatch)
    api._engine = eng
    vocab = config.CONFIG / "vocabulary.txt"
    api._vocab_seen = api._vocab_mtimes()
    vocab.write_text("[german]\nKalibra = Calibra Hand\n", encoding="utf-8")
    later = time.time() + 5
    os.utime(vocab, (later, later))
    deadline = time.time() + 8           # the background watcher polls every ~2 s
    try:
        while time.time() < deadline and "Calibra Hand" not in eng.vocab.finish(*eng.vocab.protect("Kalibra")):
            api._vocab_check()
            time.sleep(0.2)
        assert eng.vocab.finish(*eng.vocab.protect("Kalibra")) == "Calibra Hand"
    finally:
        # the Api's watcher thread outlives the test, and once DOLMI_CONFIG is restored a later reload
        # would read the real config folder: leave it no engine to reload
        api._engine = None


def test_watcher_does_nothing_without_changes(api, fake_engine):
    api._engine = eng = fake_engine()
    api._vocab_seen = api._vocab_mtimes()
    assert api._vocab_check() is False and eng.reloads == 0


# ------------------------------------------------------------------ VOC-1: bridge
def test_save_vocab_writes_both_files_and_reloads(api, fake_engine):
    import config
    api._engine = eng = fake_engine()
    res = api.save_vocab("[keep]\nNordlicht GmbH\n", "Nordlicht GmbH\nKalibra\n")
    assert res["ok"] is True
    assert (config.CONFIG / "vocabulary.txt").read_text(encoding="utf-8") == "[keep]\nNordlicht GmbH\n"
    assert (config.CONFIG / "glossary.txt").read_text(encoding="utf-8") == "Nordlicht GmbH\nKalibra\n"
    assert eng.reloads == 1
    assert api._vocab_check() is False            # the save itself does not trigger a second reload


def test_save_vocab_reports_problems_it_will_not_apply(api):
    res = api.save_vocab("[german]\nKalibra\n", None)
    assert res["ok"] is True and res["problems"]["vocabulary"][0]["line"] == 2


def test_validate_vocab_returns_line_numbers_and_writes_nothing(api):
    import config
    before = (config.CONFIG / "vocabulary.txt").read_text(encoding="utf-8")
    res = api.validate_vocab("# top\n[german]\nKalibra\n[english]\nfoo =\n", "Nordlicht\nA = B\n")
    assert res["ok"] is False
    assert [p["line"] for p in res["vocabulary"]] == [3, 5]
    assert [p["line"] for p in res["glossary"]] == [2]
    assert (config.CONFIG / "vocabulary.txt").read_text(encoding="utf-8") == before
    assert api.validate_vocab("[keep]\nNordlicht\n", "Kalibra\n") == {"ok": True, "vocabulary": [], "glossary": []}


def test_validate_vocab_checks_saved_files_when_nothing_sent(api):
    res = api.validate_vocab(None, None)
    assert res["ok"] is True                     # the seeded example files are clean


def test_save_vocab_retries_while_the_file_is_held_open(api, monkeypatch):
    import config
    real, calls = os.replace, []
    def flaky(src, dst):
        calls.append(dst)
        if len(calls) < 3:
            raise PermissionError(5, "Access is denied")
        real(src, dst)
    monkeypatch.setattr(os, "replace", flaky)
    api.save_vocab("[keep]\nNordlicht\n", None)
    assert len(calls) == 3
    assert (config.CONFIG / "vocabulary.txt").read_text(encoding="utf-8") == "[keep]\nNordlicht\n"
    assert not (config.CONFIG / "vocabulary.tmp").exists()


def test_save_vocab_removes_the_temp_file_when_it_cannot_replace(api, monkeypatch):
    import config
    before = (config.CONFIG / "vocabulary.txt").read_text(encoding="utf-8")
    def locked(src, dst):
        raise PermissionError(5, "Access is denied")
    monkeypatch.setattr(os, "replace", locked)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        api.save_vocab("[keep]\nNordlicht\n", None)
    assert not (config.CONFIG / "vocabulary.tmp").exists()
    assert (config.CONFIG / "vocabulary.txt").read_text(encoding="utf-8") == before


def test_translate_uses_one_set_of_rules_per_line(monkeypatch):
    eng = live_subs.Engine.__new__(live_subs.Engine)
    eng.language = "de"
    eng.vocab = Vocabulary(text="[keep]\nNordlicht\n")
    new = Vocabulary(text="[english]\nNordlicht = Southlight\n")

    class Sp:
        def encode(self, text, out_type=str):
            eng.vocab = new                     # a reload lands while the line is being translated
            return text.split()

        def decode(self, toks):
            return " ".join(toks)

    class Hyp:
        def __init__(self, toks):
            self.hypotheses = [toks]

    class Mt:
        def translate_batch(self, batch, **kw):
            return [Hyp(batch[0])]

    monkeypatch.setattr(eng, "_translator", lambda key, download=False: (Mt(), Sp(), Sp()), raising=False)
    assert eng.translate("Nordlicht kommt", "de") == "Nordlicht kommt"



# ------------------------------------------------------------------ VOC-11: hotword budget + profiles
SAMPLE_TERMS = ["Nordlicht GmbH", "Kalibra", "Wissensgraph", "Velorix", "Brandtwerk", "Halvora Cloud Suite"]


def test_token_count_matches_the_whisper_tokenizer():
    tok = live_subs.whisper_tokenizer()
    if tok is None:
        pytest.skip("no Whisper model downloaded: no tokenizer to compare with")
    b = live_subs.hotword_budget(SAMPLE_TERMS, tok)
    # faster-whisper encodes ' ' + hotwords (transcribe.get_prompt)
    assert b["tokens"] == len(tok.encode(" " + ", ".join(SAMPLE_TERMS), add_special_tokens=False).ids)
    assert b["exact"] is True and b["kept"] == SAMPLE_TERMS and b["cut"] == []


def test_terms_past_the_budget_are_reported_as_cut():
    tok = live_subs.whisper_tokenizer()
    if tok is None:
        pytest.skip("no Whisper model downloaded: no tokenizer to compare with")
    terms = [f"Kalibra{i}" for i in range(120)]
    b = live_subs.hotword_budget(terms, tok)
    assert b["tokens"] > live_subs.HOTWORD_TOKENS and b["cut"]
    assert b["kept"] + b["cut"] == terms
    fits = lambda ts: len(tok.encode(" " + ", ".join(ts), add_special_tokens=False).ids)
    assert fits(b["kept"]) <= live_subs.HOTWORD_TOKENS < fits(b["kept"] + b["cut"][:1])


def test_budget_without_a_tokenizer_is_an_estimate():
    b = live_subs.hotword_budget(SAMPLE_TERMS * 20, None)
    assert b["exact"] is False and b["cut"] and b["tokens"] > live_subs.HOTWORD_TOKENS
    assert live_subs.hotword_budget([], None)["tokens"] == 0


def test_glossary_without_profiles_is_one_list_as_before():
    text = "# my terms\nNordlicht GmbH\nKalibra\n"
    common, profiles, found = live_subs.parse_glossary_profiles(text)
    assert common == ["Nordlicht GmbH", "Kalibra"] and profiles == {} and found == []
    assert live_subs.glossary_terms(text) == live_subs.glossary_terms(text, "Sales") == common
    assert parse_glossary(text) == (common, [])


def test_glossary_profiles_add_their_block_to_the_common_terms():
    text = ("Nordlicht GmbH\n\n## profile: Sales calls\nVelorix\nKalibra\n"
            "## Profile: Dev  # engineers\nBrandtwerk\nnordlicht gmbh\n")
    common, profiles, found = live_subs.parse_glossary_profiles(text)
    assert found == [] and common == ["Nordlicht GmbH"]
    assert profiles == {"Sales calls": ["Velorix", "Kalibra"], "Dev": ["Brandtwerk", "nordlicht gmbh"]}
    assert live_subs.glossary_terms(text, "sales calls") == ["Nordlicht GmbH", "Velorix", "Kalibra"]
    assert live_subs.glossary_terms(text, "Dev") == ["Nordlicht GmbH", "Brandtwerk"]        # no duplicates
    assert live_subs.glossary_terms(text, "") == ["Nordlicht GmbH", "Velorix", "Kalibra", "Brandtwerk"]
    assert live_subs.glossary_terms(text, "Gone") == live_subs.glossary_terms(text, "")
    assert parse_glossary(text)[0] == ["Nordlicht GmbH", "Velorix", "Kalibra", "Brandtwerk", "nordlicht gmbh"]


def test_profile_line_problems():
    found = live_subs.parse_glossary_profiles("## profile:\nKalibra\n## profile: A\nVelorix\n## profile: a\nHalvora\n")[2]
    assert [p["line"] for p in found] == [1, 5]
    assert "name" in found[0]["message"] and "twice" in found[1]["message"]


def test_reload_terms_uses_the_profile_and_only_terms_that_fit(tmp_path, monkeypatch):
    (tmp_path / "glossary.txt").write_text("Nordlicht\n## profile: Sales\nVelorix\n## profile: Dev\nBrandtwerk\nKalibra\n",
                                           encoding="utf-8")
    monkeypatch.setenv("DOLMI_VOCAB_PROFILE", "dev")
    monkeypatch.setattr(live_subs, "whisper_tokenizer", lambda: None)
    eng = _bare_engine(tmp_path, monkeypatch)
    assert eng.glossary == "Nordlicht, Brandtwerk, Kalibra"
    monkeypatch.setattr(live_subs, "HOTWORD_TOKENS", 7)          # ~22 characters by the estimate
    eng.reload_terms()
    assert eng.glossary == "Nordlicht, Brandtwerk"             # whole terms only, never half of one
    monkeypatch.setenv("DOLMI_VOCAB_PROFILE", "")
    monkeypatch.setattr(live_subs, "HOTWORD_TOKENS", 223)
    eng.reload_terms()
    assert eng.glossary == "Nordlicht, Velorix, Brandtwerk, Kalibra"


def test_cloud_engines_get_every_term(tmp_path, monkeypatch):
    (tmp_path / "glossary.txt").write_text("\n".join(f"Kalibra{i}" for i in range(200)), encoding="utf-8")
    monkeypatch.setattr(live_subs, "whisper_tokenizer", lambda: None)
    eng = _bare_engine(tmp_path, monkeypatch)
    assert len(eng.glossary.split(", ")) < 200
    eng.cloud = True
    eng.reload_terms()
    assert len(eng.glossary.split(", ")) == 200


def test_over_biasing_hints_are_flagged():
    found = {w["term"]: w["reason"] for w in live_subs.bias_warnings(["XQ", "VLX", "Teams", "cloud", "Velorix",
                                                                       "Nordlicht GmbH", "Microsoft Teams"])}
    assert set(found) == {"XQ", "VLX", "Teams", "cloud"}
    assert "acronym" in found["VLX"] and "Everyday" in found["Teams"]


# ------------------------------------------------------------------ VOC-6: placeholders never lose a term
KEEP = "[keep]\nNordlicht\nVLX!\n[german]\nKalibra = Calibra\nWissensgraph = knowledge graph\n"


def test_placeholders_become_their_terms_as_before():
    v = Vocabulary(text=KEEP)
    de, slots = v.protect("Wir testen Kalibra mit Nordlicht.")
    assert de == "Wir testen X1X mit X0X." and list(slots) == ["Nordlicht", "Calibra"]
    assert v.finish("We test X1X with X0X.", slots) == "We test Calibra with Nordlicht."
    assert v.finish("We test X 1 X with x0x.", slots) == "We test Calibra with Nordlicht."


def test_a_dropped_placeholder_is_put_back_next_to_a_surviving_neighbour():
    v = Vocabulary(text=KEEP)
    de, slots = v.protect("Dann kommt Nordlicht 2 morgen.")
    assert v.finish("Then comes 2 tomorrow.", slots) == "Then comes Nordlicht 2 tomorrow."


def test_a_dropped_placeholder_goes_to_the_end_of_its_clause():
    v = Vocabulary(text=KEEP)
    de, slots = v.protect("Wir testen Kalibra heute, und morgen kommt der Rest.")
    assert v.finish("We test today, and tomorrow comes the rest.", slots) == \
        "We test today Calibra, and tomorrow comes the rest."
    de, slots = v.protect("Morgen kommt der Rest, wir testen Kalibra.")
    assert v.finish("Tomorrow comes the rest, we are testing.", slots) == \
        "Tomorrow comes the rest, we are testing Calibra."


def test_a_kept_term_is_never_silently_lost():
    v = Vocabulary(text=KEEP)
    sources = ["Kalibra läuft.", "Wir testen Kalibra und Nordlicht, dann VLX.", "Nordlicht, Kalibra, Wissensgraph!",
               "Der Wissensgraph von Nordlicht ist fertig, sagt Kalibra"]
    outputs = ["", "It works.", "We test and, then.", "Done, says", "X0X", "x 1 x and X9X"]
    for de in sources:
        _, slots = v.protect(de)
        assert slots
        for en in outputs:
            out = v.finish(en, slots)
            for term in slots:
                assert term in out, (de, en, out)


def test_old_callers_with_a_plain_slot_list_still_get_the_term_back():
    v = Vocabulary(text=KEEP)
    assert v.finish("It works.", ["Calibra"]) == "It works Calibra."


def test_article_before_a_kept_term_follows_the_term():
    v = Vocabulary(text="[keep]\nVLX!\nAcme\n[german]\nKalibra = Unicorn Suite\n")
    _, slots = v.protect("Wir brauchen ein VLX und eine Acme und ein Kalibra")
    assert list(slots) == ["Unicorn Suite", "Acme", "VLX"]
    assert v.finish("We need an X2X and a X1X and an X0X", slots) == "We need a VLX and an Acme and a Unicorn Suite"
    _, slots = v.protect("Ein Kalibra")
    assert v.finish("An X0X", slots) == "A Unicorn Suite"


def test_glued_german_endings_on_a_placeholder_are_dropped_english_plural_kept():
    v = Vocabulary(text=KEEP)
    _, slots = v.protect("Kalibra")
    assert v.finish("the X0Xn", slots) == "the Calibra"
    assert v.finish("two X0Xs", slots) == "two Calibras"


def test_enforce_fixes_a_cloud_translation_that_kept_the_source_term():
    v = Vocabulary(text=KEEP + "[english]\nunrolled = rolled out\n")
    assert v.enforce("Wir testen Kalibra", "We test Kalibra") == "We test Calibra"
    assert v.enforce("Wir testen Kalibra", "We test Calibra") == "We test Calibra"
    assert v.enforce("Nordlicht ist ausgerollt", "Nordlicht was unrolled") == "Nordlicht was rolled out"
    assert v.enforce("Wir testen Kalibra", "We are testing it") == "We are testing it"   # never guessed in


# ------------------------------------------------------------------ VOC-7: cloud prompts carry the rules
RULES = "[keep]\nNordlicht\nVelorix*\n[german]\nKalibra = Calibra\n[english]\nunrolled = rolled out\n"


def _cloud(provider, monkeypatch, tmp_path):
    """A CloudEngine without threads or network: only the request building is exercised."""
    import cloud_speech
    (tmp_path / "vocabulary.txt").write_text(RULES, encoding="utf-8")
    (tmp_path / "glossary.txt").write_text("Brandtwerk\nHalvora\n", encoding="utf-8")
    monkeypatch.setenv("DOLMI_CONFIG", str(tmp_path))
    eng = cloud_speech.CloudEngine.__new__(cloud_speech.CloudEngine)
    eng.provider, eng.key, eng.language, eng.model_name = provider, "acct:fake-key", "de", "m"
    eng.thinking, eng.context, eng._requests = None, "Wir waren beim Thema Brandtwerk.", 0
    eng.reload_terms()
    return eng


def test_build_prompt_gemini_lists_glossary_keep_german_and_previous_text():
    import cloud_speech
    p = cloud_speech.build_prompt("Brandtwerk, Halvora", Vocabulary(text=RULES), "Vorher gesagt.", "de")
    assert "Brandtwerk, Halvora" in p
    assert "Never translate these" in p and "Nordlicht" in p and "Velorix (any ending)" in p
    assert '"Kalibra" as "Calibra"' in p
    assert 'The previous sentence was: "Vorher gesagt."' in p
    assert "unrolled" not in p                               # [english] fixes run on the output instead


def test_build_prompt_transcription_styles_spell_the_terms_and_carry_the_previous_text():
    import cloud_speech
    v = Vocabulary(text=RULES)
    for style in ("openai", "whisper"):
        p = cloud_speech.build_prompt("Brandtwerk", v, "Vorher gesagt.", "de", style)
        for term in ("Brandtwerk", "Nordlicht", "Velorix", "Kalibra", "Vorher gesagt."):
            assert term in p, (style, term)
        assert "*" not in p
    assert cloud_speech.build_prompt("", Vocabulary(text=""), "", "de", "openai") == ""
    long = cloud_speech.build_prompt(", ".join(f"Kalibra{i}" for i in range(300)), v, "Vorher.", "de", "whisper")
    assert len(long) <= cloud_speech.WHISPER_PROMPT_CHARS + 2 and long.endswith("Vorher.")


def test_gemini_request_has_the_rules_and_its_translation_goes_through_them(monkeypatch, tmp_path):
    import cloud_speech
    eng = _cloud("gemini", monkeypatch, tmp_path)
    sent = []

    def post(url, body, headers, timeout=30):
        sent.append(body)
        text = '{"heard": "Wir haben Kalibra ausgerollt", "translation": "We unrolled Kalibra"}'
        return {"candidates": [{"content": {"parts": [{"text": text}]}}]}
    monkeypatch.setattr(cloud_speech, "_post_json", post)
    heard, en = eng._gemini(np.zeros(1600, np.float32))
    prompt = sent[0]["contents"][0]["parts"][0]["text"]
    assert "Never translate" in prompt and '"Kalibra" as "Calibra"' in prompt and "Brandtwerk" in prompt
    assert "Wir waren beim Thema Brandtwerk." in prompt
    assert (heard, en) == ("Wir haben Kalibra ausgerollt", "We rolled out Calibra")


def test_cloudflare_request_prompt_has_terms_and_previous_text(monkeypatch, tmp_path):
    import cloud_speech
    eng = _cloud("cloudflare", monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(cloud_speech, "_post_json",
                        lambda url, body, headers, timeout=30: sent.append(body) or {"result": {"text": "Hallo"}})
    assert eng._cloudflare(np.zeros(1600, np.float32)) == "Hallo"
    p = sent[0]["initial_prompt"]
    assert "Nordlicht" in p and "Kalibra" in p and "Brandtwerk" in p and p.endswith("Wir waren beim Thema Brandtwerk.")


def test_openai_request_prompt_has_terms_and_previous_text(monkeypatch, tmp_path):
    import sys
    import types
    eng = _cloud("openai", monkeypatch, tmp_path)
    sent = {}

    class Transcriptions:
        def create(self, **kw):
            sent.update(kw)
            return types.SimpleNamespace(text="Hallo")

    fake = types.ModuleType("openai")
    fake.NOT_GIVEN = object()
    fake.OpenAI = lambda **kw: types.SimpleNamespace(audio=types.SimpleNamespace(transcriptions=Transcriptions()))
    monkeypatch.setitem(sys.modules, "openai", fake)
    assert eng._openai(np.zeros(1600, np.float32)) == "Hallo"
    for term in ("Nordlicht", "Kalibra", "Brandtwerk", "Wir waren beim Thema Brandtwerk."):
        assert term in sent["prompt"], term


# ------------------------------------------------------------------ VOC-2/5/11: summary + profiles via the bridge
def test_vocab_summary_counts_budget_and_bias(api):
    s = api.vocab_summary("[keep]\nNordlicht\n[german]\nKalibra = Calibra\nVelorix = Velo\n[english]\nfoo = bar\nbaz\n",
                          "Teams\nVLX\nBrandtwerk\n## profile: Sales\nHalvora\n")
    assert s["counts"] == {"gloss": 4, "keep": 1, "german": 2, "english": 1}
    assert s["problems"] == 1
    assert s["profile"] == "" and s["profiles"] == [{"name": "Sales", "terms": 4}]
    b = s["budget"]
    assert b["terms"] == 4 and b["cut"] == [] and b["limit"] == 223 and not b["tooLong"] and b["tokens"] > 0
    assert [w["term"] for w in s["bias"]] == ["Teams", "VLX"]
    assert s["recentFrom"] == "files" and s["recent"][-1] == {"group": "english", "term": "foo"}


def test_vocab_summary_warns_about_a_long_glossary(api):
    long = "\n".join(f"Kalibra Begriff {i}" for i in range(40))
    b = api.vocab_summary(None, long)["budget"]
    assert b["tooLong"] and b["terms"] == 40


def test_vocab_summary_shows_what_the_last_save_changed(api):
    api.save_vocab("[keep]\nNordlicht\n", "Brandtwerk\n")
    api.save_vocab("[keep]\nNordlicht\nVelorix\n", "Brandtwerk\nHalvora\n")
    s = api.vocab_summary()
    assert s["recentFrom"] == "save"
    assert s["recent"] == [{"group": "gloss", "term": "Halvora"}, {"group": "keep", "term": "Velorix"}]


def test_set_vocab_profile_saves_the_setting_and_reloads(api, fake_engine, monkeypatch):
    import config
    monkeypatch.setenv("DOLMI_VOCAB_PROFILE", "")   # restored after the test
    api.save_vocab(None, "Nordlicht\n## profile: Sales\nVelorix\n")
    api._engine = eng = fake_engine()
    assert api.set_vocab_profile("sales") == {"ok": True, "profile": "Sales"}
    assert config.load_settings()["vocab_profile"] == "Sales" and os.environ["DOLMI_VOCAB_PROFILE"] == "Sales"
    assert eng.reloads == 1 and api.vocab_summary()["profile"] == "Sales"
    assert api.set_vocab_profile("Gone")["ok"] is False
    assert api.set_vocab_profile("") == {"ok": True, "profile": ""}
    assert api.vocab_summary(None, None, "Gone")["profile"] == ""
