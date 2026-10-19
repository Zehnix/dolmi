"""VOC-9 clarify queue: Kölner Phonetik, de-duplication by sound, matching the user's own rules, and the
bridge calls that add an answer to the right file. All terms are fictional."""
from types import SimpleNamespace

import pytest

import clarify
import live_subs
from clarify import Queue, add_rule, koelner


@pytest.mark.parametrize("word,code", [
    ("Müller-Lüdenscheidt", "65752682"), ("Wikipedia", "3412"), ("Breschnew", "17863"),
    ("Meier", "67"), ("Mayer", "67"), ("Philipp", "351"), ("Xaver", "4837"), ("", ""), ("123", "")])
def test_koelner_phonetik(word, code):
    assert koelner(word) == code


def test_koelner_c_rules():
    assert koelner("Cäsar") == koelner("Kaesar") == "487"       # initial C before A sounds like K
    assert koelner("Celle") == "85"                             # initial C before E sounds like Z
    assert koelner("Christ") == koelner("Krist")


def test_queue_collects_unsure_words_and_dedups_by_sound():
    q = Queue()
    q.add([("Das", 0.9), (" Kalibra", 0.3), ("kommt", 0.95)], "Das Kalibra kommt.")
    q.add([("Die", 0.9), ("Kalibrah", 0.2)], "Die Kalibrah ist da.")
    q.add([("Gut", 0.99)], "Gut.")
    items = q.items()
    assert len(items) == 1
    assert items[0]["heard"] == "Kalibra" and items[0]["count"] == 2
    assert items[0]["context"] == "Das Kalibra kommt." and items[0]["group"] == "gloss"


def test_queue_flags_odd_capitals_but_not_known_or_short_words():
    q = Queue([("keep", "Nordlicht GmbH", "")])
    q.add([("Wir", 0.9), ("ZRX", 0.9), ("Nordlicht", 0.1), ("äh", 0.1), ("ProdQ", 0.9)], "Wir ZRX Nordlicht äh ProdQ")
    assert sorted(e["heard"] for e in q.items()) == ["ProdQ", "ZRX"]


def test_queue_suggests_the_users_own_term_that_sounds_alike():
    q = Queue([("german", "Kalibra", "Calibra")])
    q.add([("Die", 0.9), ("Kahlibra", 0.2)], "Die Kahlibra.")
    e = q.items()[0]
    assert e["suggest"] == "Kalibra" and e["group"] == "german" and e["known"] is True


def test_ignored_words_and_drop():
    q = Queue(ignored=["Zorbel"])
    q.add([("x", 0.9), ("Zorbel", 0.1), ("Quandor", 0.1)], "x Zorbel Quandor")
    assert [e["heard"] for e in q.items()] == ["Quandor"]
    q.drop("Quandor")
    assert q.items() == []


def test_queue_is_capped():
    q = Queue()
    q.add([(w, 0.1) for w in ("Alpha", "Bravo", "Delta", "Gimpel", "Holm", "Lummer", "Mops", "Nuss", "Pulk",
                              "Rabe", "Sorge", "Tupf", "Wolke", "Zander")], "many")
    assert len(q.items()) == clarify.MAX_ITEMS


def test_add_rule_puts_the_line_in_its_section():
    v = "# notes\n[keep]\nNordlicht GmbH\n\n[german]\nKalibra = Calibra\n"
    assert add_rule(v, "", "keep", "Zorbel") == ("# notes\n[keep]\nNordlicht GmbH\nZorbel\n\n[german]\nKalibra = Calibra", None)
    assert add_rule(v, "", "german", "Quandor", "Quandor AG")[0].endswith("Kalibra = Calibra\nQuandor = Quandor AG")
    assert add_rule(v, "", "english", "colour", "color")[0].endswith("\n\n[english]\ncolour = color")
    assert add_rule("", "Nordlicht\n", "gloss", "Zorbel") == (None, "Nordlicht\nZorbel")
    with pytest.raises(ValueError):
        add_rule(v, "", "german", "Quandor", "")


def test_clarify_add_writes_the_right_file(api, fake_engine):
    import config
    (config.CONFIG / "vocabulary.txt").write_text("[keep]\nNordlicht GmbH\n", encoding="utf-8")
    (config.CONFIG / "glossary.txt").write_text("Nordlicht\n", encoding="utf-8")
    api._clarify = Queue()
    api._clarify.add([("Die", 0.9), ("Zorbel", 0.1), ("Quandor", 0.1)], "Die Zorbel Quandor")
    assert api.clarify_add("Zorbel", "german", "Zorbel", "Zorbel Ltd")["ok"] is True
    assert (config.CONFIG / "vocabulary.txt").read_text(encoding="utf-8").endswith("[german]\nZorbel = Zorbel Ltd\n")
    assert api.clarify_add("Quandor", "gloss", "Quandor")["ok"] is True
    assert (config.CONFIG / "glossary.txt").read_text(encoding="utf-8") == "Nordlicht\nQuandor\n"
    assert api.clarify_list()["items"] == []
    assert api.clarify_add("x", "german", "Only", "")["ok"] is False


def test_add_rule_no_duplicates_and_no_target_for_keep():
    v = "[keep]\nNordlicht GmbH\n\n[german]\nKalibra = Calibra\n"
    assert add_rule(v, "", "keep", "nordlicht gmbh") == (None, None)
    assert add_rule("", "Zorbel\n", "gloss", "zorbel") == (None, None)
    assert add_rule(v, "", "keep", "Zorbel", "stray")[0].startswith("[keep]\nNordlicht GmbH\nZorbel\n")
    with pytest.raises(clarify.Exists):
        add_rule(v, "", "german", "Kalibra", "Other")


def test_clarify_add_existing_keep_writes_nothing(api, fake_engine):
    import config
    (config.CONFIG / "vocabulary.txt").write_text("[keep]\nNordlicht\n", encoding="utf-8")
    api._clarify = Queue()
    api._clarify.add([("Die", 0.9), ("Nordlich", 0.1)], "Die Nordlich")
    assert api.clarify_add("Nordlich", "keep", "Nordlicht") == {"ok": True, "already": True}
    assert (config.CONFIG / "vocabulary.txt").read_text(encoding="utf-8") == "[keep]\nNordlicht\n"
    assert api.clarify_list()["items"] == []
    assert api.clarify_add("x", "german", "Nordlicht", "y")["ok"] is True   # other section is fine


def test_never_ask_hides_the_word_not_its_sound_alikes():
    q = Queue(ignored=["Mueller"])
    q.add([("x", 0.9), ("Mueller", 0.1)], "x Mueller")
    q.add([("x", 0.9), ("Miller", 0.1)], "x Miller")
    assert [e["heard"] for e in q.items()] == ["Miller"]


def test_clarify_skip_forever_is_remembered(api):
    import config
    api._clarify = Queue()
    api._clarify.add([("a", 0.9), ("Zorbel", 0.1)], "a Zorbel")
    api.clarify_skip("Zorbel", forever=True)
    assert api.clarify_list()["items"] == []
    assert api._clarify_ignored() == ["Zorbel"]
    assert (config.CONFIG / "clarify_ignore.txt").exists()


def test_setting_is_off_by_default_and_settable(api):
    import config
    assert config.DEFAULTS["clarify_queue"] is False and api.clarify_list()["on"] is False
    assert api.set_setting("clarify_queue", True) and api.clarify_list()["on"] is True
    assert api.set_setting("clarify_queue", "yes") is False


class _FakeWhisper:
    def __init__(self):
        self.kwargs = []

    def transcribe(self, audio, **kw):
        self.kwargs.append(kw)
        words = [SimpleNamespace(word=" Das", probability=0.9), SimpleNamespace(word=" Zorbel", probability=0.2)]
        seg = SimpleNamespace(text=" Das Zorbel", words=words if kw.get("word_timestamps") else None,
                              no_speech_prob=0.0, avg_logprob=-0.1)
        return iter([seg]), SimpleNamespace(language="de")


def _engine(clarify_queue):
    eng = live_subs.Engine.__new__(live_subs.Engine)
    eng.asr, eng.language, eng.glossary, eng.context, eng.terms = _FakeWhisper(), "de", "", "", set()
    eng.clarify = clarify_queue
    return eng


def test_engine_asks_for_word_probabilities_only_when_on_and_only_for_finals():
    off = _engine(None)
    off.transcribe([0.0] * 10)
    assert "word_timestamps" not in off.asr.kwargs[0]
    q = Queue()
    on = _engine(q)
    on.transcribe_draft([0.0] * 10)
    assert "word_timestamps" not in on.asr.kwargs[-1] and q.items() == []
    on.transcribe([0.0] * 10)
    assert on.asr.kwargs[-1]["word_timestamps"] is True
    assert [e["heard"] for e in q.items()] == ["Zorbel"]
