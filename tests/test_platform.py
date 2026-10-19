"""dolmi_platform: Windows and Mac expose the same API, both import on any OS, and every
platform word the UI uses exists on both sides."""
import importlib
import re
import sys
from pathlib import Path

import pytest

import dolmi_platform
from dolmi_platform import mac, windows

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "src" / "ui" / "web"


@pytest.mark.parametrize("module", ["dolmi_platform.windows", "dolmi_platform.mac", "dolmi_platform.mac_stealth"])
def test_platform_modules_import_on_any_os(module):
    importlib.import_module(module)


@pytest.mark.parametrize("impl", [windows, mac], ids=["windows", "mac"])
def test_both_platforms_export_the_whole_api(impl):
    missing = [name for name in dolmi_platform.API if not hasattr(impl, name)]
    assert missing == []


def test_package_re_exports_this_os_side():
    expected = mac if sys.platform == "darwin" else windows
    assert dolmi_platform.OS == expected.OS
    for name in dolmi_platform.API:
        assert getattr(dolmi_platform, name) is getattr(expected, name), name


def test_words_and_model_notes_have_the_same_keys():
    assert set(windows.WORDS) == set(mac.WORDS)
    assert set(windows.MODEL_NOTES) == set(mac.MODEL_NOTES)
    assert windows.WORDS["computer"] == "PC" and mac.WORDS["computer"] == "Mac"
    assert windows.DEFAULT_SPEAKER == "Default speaker" and mac.DEFAULT_SPEAKER == "All Mac audio"


def _words_used_by_the_page():
    """Every W('key') in js/*.js, and every {key} in the page's data-wt templates and the onboarding text."""
    used = set()
    for js in (WEB / "js").glob("*.js"):
        used |= set(re.findall(r"""\bW\(\s*['"]([A-Za-z_]+)['"]\s*\)""", js.read_text(encoding="utf-8")))
    for template in re.findall(r'data-wt="([^"]*)"', (WEB / "index.html").read_text(encoding="utf-8")):
        used |= set(re.findall(r"\{([A-Za-z_]+)\}", template))
    onboarding = (WEB / "js" / "onboarding.js").read_text(encoding="utf-8")
    used |= set(re.findall(r"(?<!\$)\{([a-z_]+)\}", onboarding))      # {computer} in STEPS (not ${...})
    return used


def test_every_platform_word_the_page_uses_exists_on_both_platforms():
    used = _words_used_by_the_page()
    assert {"computer", "COMPUTER", "speaker_default", "os_user"} <= used     # the scan finds them
    assert sorted(used - set(windows.WORDS)) == []
    assert sorted(used - set(mac.WORDS)) == []


def test_state_tells_the_page_its_platform(api):
    p = api.state()["platform"]
    assert p == {"os": dolmi_platform.OS, "words": dolmi_platform.WORDS}


def test_speaker_label_uses_the_platform_word(api):
    assert api.state()["sourceLabel"] == {"source": "speaker", "device": dolmi_platform.DEFAULT_SPEAKER}


def test_model_catalog_uses_this_platforms_notes_and_words():
    import models
    for key, note in dolmi_platform.MODEL_NOTES.items():
        assert models.BY_KEY[key].note == note
    pc = {"ram_gb": 64, "cores": 2, "gpu": False}
    assert models.fit(models.BY_KEY["large-v3"], pc) == (dolmi_platform.WORDS["fit_needs_gpu"], "live")
    assert models.fit(models.BY_KEY["large-v3-turbo"], pc) == (dolmi_platform.WORDS["fit_slow"], "draft")


def test_compute_choices():
    assert mac.compute_device("auto") == ("cpu", "int8") and mac.auto_speech(True) == "small"
    assert windows.auto_speech(True) == "large-v3-turbo" and windows.auto_speech(False) == "small"
    pytest.importorskip("ctranslate2")
    assert windows.compute_device("cpu") == ("cpu", "int8")
    assert windows.compute_device("cuda") == ("cuda", "float16")


def test_folders(monkeypatch, tmp_path):
    assert mac.config_dir() == Path.home() / "Library" / "Application Support" / "Dolmi"
    assert mac.log_dir() == Path.home() / "Library" / "Logs" / "Dolmi"
    assert mac.documents_folder() == Path.home() / "Documents"
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert windows.config_dir() == tmp_path / "Roaming" / "Dolmi"
    assert windows.log_dir() == tmp_path / "Local" / "Dolmi"
    assert windows.app_icon(ROOT).name == "dolmi-2.ico" and mac.app_icon(ROOT).name == "dolmi.png"
    assert windows.app_icon(ROOT).exists() and mac.app_icon(ROOT).exists()


def test_mac_key_storage_round_trip_and_tamper_check(monkeypatch):
    """The Keychain scheme is plain stdlib crypto: testable anywhere with a fixed secret."""
    monkeypatch.setattr(mac, "_master", lambda: b"k" * 32)
    sealed = mac.protect(b"sk-test-123")
    assert sealed != b"sk-test-123" and mac.unprotect(sealed) == b"sk-test-123"
    with pytest.raises(ValueError):
        mac.unprotect(sealed[:-1] + bytes([sealed[-1] ^ 1]))
    with pytest.raises(ValueError):
        mac.unprotect(b"short")


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
def test_windows_key_storage_round_trip():
    import assistant
    assert windows.unprotect(windows.protect(b"sk-test-123")) == b"sk-test-123"
    assert assistant.decrypt_key(assistant.encrypt_key("sk-test-123")) == "sk-test-123"
    assert assistant.decrypt_key("bm90LWVuY3J5cHRlZA==") == ""     # not ours: empty, never an exception


def test_live_subs_still_exports_capture():
    import live_subs
    for name in ("output_devices", "input_devices", "loopback_stream", "mic_stream"):
        assert getattr(live_subs, name) is getattr(dolmi_platform, name)


def test_mac_audio_helper_search_covers_source_and_app_bundle():
    """From source the helper is Mac/audio/dolmi-audio; in Dolmi.app it is app/audio/dolmi-audio."""
    import inspect
    src = inspect.getsource(mac._audio_helper)
    assert '"Mac" / "audio"' in src and 'root / "audio"' in src and "_MEIPASS" in src
    assert (ROOT / "Mac" / "audio" / "dolmi-audio.swift").exists()
