"""APP-2: Settings in sections. Every existing control keeps its id and keeps saving through the same
bridge call; the section menu, deep links and mount points other domains build on are in place."""
import re
from html.parser import HTMLParser
from pathlib import Path

import config
from ui import app as app_mod

WEB = Path(__file__).resolve().parent.parent / "src" / "ui" / "web"
HTML = (WEB / "index.html").read_text(encoding="utf-8")
JS = {p.name: p.read_text(encoding="utf-8") for p in (WEB / "js").glob("*.js")}

# every id the Settings view had before APP-2 (other files look them up by id)
SETTINGS_IDS = [
    "openOnb", "setDevice", "setSrc", "setMic", "sourceHint", "setSource", "modelRow", "modelHint",
    "setModel", "setLang", "srcLab", "swGerman", "setProvider", "cfRow", "setCfAccount", "setKey",
    "setTest", "aiModelHint", "setAiModel", "setAiModelOther", "keyStatus", "swInv", "swBar", "swSave",
    "setKeep", "theme", "setOpacity", "setOpacityVal", "setFont", "setFontVal", "modelsCard", "specs",
    "modelList", "modelDir", "openModels",
]
SECTIONS = [
    ("audio", "Audio"), ("captions", "Captions & languages"), ("speech", "Speech models"),
    ("vocab", "Vocabulary"), ("assistant", "Assistant & AI keys"), ("bar", "Subtitle bar"),
    ("privacy", "Privacy & data"), ("appearance", "Appearance"), ("about", "About & updates"),
]
MOUNTS = ["captions.translator", "bar.clickthrough", "bar.time", "privacy.speakers", "about.updates",
          "about.shortcuts", "about.report", "appearance.textsize", "audio.voice", "audio.capture_both"]
# which section each control lives in (the plan's table)
HOME = {
    "audio": ["setSrc", "setDevice", "setMic", "swWarnSilence"],
    "captions": ["setSource", "setLang", "swGerman"],
    "speech": ["setModel", "modelsCard"],
    "assistant": ["setProvider", "setCfAccount", "setKey", "setAiModel", "keyStatus"],
    "bar": ["swBar", "setOpacity", "setFont"],
    "privacy": ["swInv", "swSave", "setKeep"],
    "appearance": ["theme"],
}


class Page(HTMLParser):
    """Collects every id with its tag, attributes and the ids/data-sec of its open ancestors."""

    VOID = {"input", "img", "br", "meta", "link", "hr"}

    def __init__(self):
        super().__init__()
        self.stack, self.ids, self.nodes = [], {}, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        node = {"tag": tag, "attrs": a, "ancestors": [dict(x["attrs"]) for x in self.stack]}
        self.nodes.append(node)
        if "id" in a:
            self.ids.setdefault(a["id"], []).append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        if not any(n["tag"] == tag for n in self.stack):
            return                                  # stray end tag: keep the open elements
        while self.stack.pop()["tag"] != tag:
            pass


def page():
    p = Page()
    p.feed(HTML)
    return p


def section_of(node):
    for a in reversed(node["ancestors"]):
        if "set-page" in (a.get("class") or "").split():
            return a.get("data-sec")
    return None


def test_every_existing_settings_id_is_still_there_once():
    ids = page().ids
    missing = [i for i in SETTINGS_IDS if i not in ids]
    doubled = [i for i, n in ids.items() if len(n) > 1]
    assert missing == [] and doubled == []


def test_exactly_the_nine_sections_in_order():
    p = page()
    menu = [n for n in p.nodes if n["tag"] == "button" and "data-sec" in n["attrs"]
            and any("set-menu" in (a.get("class") or "") for a in n["ancestors"])]
    assert [b["attrs"]["data-sec"] for b in menu] == [k for k, _ in SECTIONS]
    for key, name in SECTIONS:
        assert re.search(r'data-sec="%s"[^>]*>(?:<[^>]+>)*\s*%s\s*<' % (key, re.escape(name.replace("&", "&amp;"))), HTML), name
    pages = [n["attrs"]["data-sec"] for n in p.nodes if "set-page" in (n["attrs"].get("class") or "").split()]
    assert pages == [k for k, _ in SECTIONS]


def test_controls_live_in_their_sections():
    ids = page().ids
    for sec, controls in HOME.items():
        for cid in controls:
            assert section_of(ids[cid][0]) == sec, cid


def test_mount_points_are_empty_and_hidden():
    for name in MOUNTS:
        m = re.search(r'<div data-mount="%s" hidden></div>' % re.escape(name), HTML)
        assert m, name
    assert "function settingsMount(" in JS["settings.js"]
    assert "function openSettings(" in JS["settings.js"]


def test_heard_mount_is_the_first_thing_in_live():
    assert re.search(r'<section class="view a" id="v-live">\s*<div id="heardWarn" hidden></div>', HTML)
    assert '<script src="js/heard.js"></script>' in HTML


def test_onboarding_deep_links_to_speech_models():
    js = JS["onboarding.js"]
    assert "scrollIntoView" not in js
    assert "openSettings('speech'" in js


def test_section_remembered_in_try_catch():
    js = JS["settings.js"]
    for call in re.findall(r"localStorage\.\w+\([^)]*\)", js):
        line = next(l for l in js.splitlines() if call in l)
        assert "try" in line, line


def test_every_key_the_page_saves_is_whitelisted_and_saves(api):
    keys = set()
    for src in JS.values():
        keys |= set(re.findall(r"set_setting\(\s*'([a-z_]+)'", src))
        keys |= set(re.findall(r"wireSwitch\(\s*'[^']+'\s*,\s*'([a-z_]+)'", src))
        keys |= set(re.findall(r"wireRange\(\s*'[^']+'\s*,\s*'([a-z_]+)'", src))
    assert {"warn_silence", "show_german", "save_transcripts", "overlay", "theme", "opacity", "font"} <= keys
    assert sorted(keys - app_mod.SETTABLE) == []
    sample = {"keep_days": 7, "opacity": 0.6, "font": 28, "language": "en", "speech_source": "local",
              "theme": "ink", "model": "small"}
    for k in keys:
        v = sample.get(k, False if k in ("warn_silence", "show_german", "save_transcripts", "overlay",
                                         "speaker_labels") else "")
        assert api.set_setting(k, v) is True, k
        assert config.load_settings()[k] == v, k


def test_warn_silence_defaults_on_and_is_saved(api):
    assert config.DEFAULTS["warn_silence"] is True
    assert api.state()["warnSilence"] is True
    assert api.set_setting("warn_silence", False) is True
    assert config.load_settings()["warn_silence"] is False
    assert api.state()["warnSilence"] is False
    assert api.set_setting("warn_silence", "no") is False      # switches are true/false only


def test_new_keys_rule_is_written_in_config():
    src = (Path(config.__file__)).read_text(encoding="utf-8")
    assert "each domain adds its own" in src and "SETTABLE" in src
