"""Shared pytest fixtures for Dolmi's tests (Windows and macOS).

New tests go in their own file, tests/test_<area>.py, and use these fixtures instead of adding checks
to the test_webview_bridge.py script:

    def test_theme_is_saved(api):
        assert api.set_setting("theme", "ink")
        assert "onInvisible" not in api._window.js()      # what Python pushed to the page

    def test_worker_emits_a_final(tmp_path, fake_engine):
        eng = fake_engine(text="Hallo zusammen.", subtitle="Hello everyone.")
        ...  # live_subs.worker(eng, audio_q, ui_q, Transcript(tmp_path), stop, detect)

Nothing real is touched: `api` uses a temp program folder, config folder and data folder.
"""
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent   # repo root
for _p in (str(ROOT / "src"), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# test_webview_bridge.py is a script (python tests/test_webview_bridge.py): importing it would run all
# of its checks at collection time against module-level state, so pytest leaves it alone.
collect_ignore = ["test_webview_bridge.py"]


class FakeWindow:
    """Stands in for the pywebview window: records every evaluate_js call (what Python pushes to the
    page) and answers the Save dialog with whatever is queued in save_as."""

    def __init__(self):
        self.calls = []
        self.save_as = []

    def evaluate_js(self, js):
        self.calls.append(js)

    def create_file_dialog(self, *args, **kwargs):
        self.calls.append(("dialog", kwargs))
        return self.save_as.pop() if self.save_as else None

    def js(self):
        """Every script sent to the page so far, as one string (e.g. to look for 'window.dolmi.onLine(')."""
        return "\n".join(c for c in self.calls if isinstance(c, str))


class FakeEngine:
    """Stands in for live_subs.Engine (and the Api's _engine): transcribe returns `text`, translate
    returns `subtitle`. Counts reload_terms() calls; transcribed audio lengths land in `heard`."""

    cloud = False

    def __init__(self, text="Hallo zusammen.", subtitle="Hello everyone.", lang="de"):
        self.text, self.subtitle, self.lang = text, subtitle, lang
        self.model_name, self.device, self.language = "fake", "cpu", lang
        self.translators = {}
        self.context = ""
        self.heard = []
        self.reloads = 0

    def transcribe(self, audio):
        self.heard.append(len(audio))
        return self.text, self.lang

    def translate(self, text, lang):
        return self.subtitle

    def reload_terms(self):
        self.reloads += 1


@pytest.fixture
def fake_engine():
    """The FakeEngine class: build one with fake_engine(text=..., subtitle=...)."""
    return FakeEngine


@pytest.fixture
def api(tmp_path, monkeypatch):
    """A sandboxed webview Api, built like tests/test_webview_bridge.py does: temp program folder (with
    the example vocabulary/glossary), temp config folder and temp data folder; `api._window` is a
    FakeWindow. The Api's background threads are daemons and stop with the test run."""
    import config
    from ui import app as app_mod

    program, data, cfg = tmp_path / "program", tmp_path / "data", tmp_path / "config" / "Dolmi"
    program.mkdir()
    for name in ("vocabulary.example.txt", "glossary.example.txt"):
        shutil.copy(ROOT / name, program / name)
    monkeypatch.setattr(config, "ROOT", program)
    monkeypatch.setattr(config, "CONFIG", cfg)
    monkeypatch.setattr(config, "SETTINGS", cfg / "settings.json")
    config.save_settings({**config.DEFAULTS, "data_folder": str(data)})

    a = app_mod.Api()
    a._window = FakeWindow()
    yield a
    a._session = None          # late queue items are dropped once there is no session
