"""The web UI in headless Edge (APP-11): tests/ui/run_ui_tests.py opens the real page with a mock bridge at
900x600 and 1360x860, 100 % and 150 % scaling. Skipped when no Edge/Chrome is installed."""
import inspect
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "src" / "ui" / "web"
sys.path.insert(0, str(ROOT / "tests" / "ui"))
import run_ui_tests as ui  # noqa: E402

needs_browser = pytest.mark.skipif(not ui.find_browser(), reason="no Edge/Chrome for the headless UI tests")


@pytest.fixture(scope="module")
def results():
    return ui.run_all()


def _report(r):
    return "\n".join(r.get("failures", [])) or "ok"


@needs_browser
@pytest.mark.parametrize("label", [f"{w}x{h}@{s}" for w, h, s in ui.SIZES])
def test_every_screen_section_and_control(results, label):
    r = results[label]
    assert r["ok"], _report(r)
    assert r["checks"] > 150                        # the checks really ran
    assert r["unknown"] == [], f"bridge calls the mock does not know: {r['unknown']}"


@needs_browser
def test_subtitle_bar(results):
    r = results["subtitle bar"]
    assert r["ok"], _report(r)


@needs_browser
def test_catches_the_stacked_meetings_regression():
    r = ui.run_all(sizes=[(1360, 860, 1)], extra_css=".grid{grid-template-columns:1fr!important}", overlay=False)
    r = r["1360x860@1"]
    assert not r["ok"] and any("not side by side" in f for f in r["failures"]), _report(r)


@needs_browser
def test_catches_wrapped_toggles_and_sideways_scrolling():
    css = ".set{flex-wrap:wrap}.set>div:first-child{flex:1 0 100%}.set-page{min-width:1400px}"
    r = ui.run_all(sizes=[(900, 600, 1)], extra_css=css, overlay=False)["900x600@1"]
    assert not r["ok"], _report(r)
    assert any("wrapped below its label" in f for f in r["failures"]), _report(r)
    assert any("sticks out of the" in f or "scrolls sideways" in f for f in r["failures"]), _report(r)



@needs_browser
def test_catches_status_chips_that_ignore_hidden():
    """Final QA: .chip{display:inline-flex} beat the hidden attribute, so "Local only" and the cloud failover
    chip always showed above 1100 px and pushed the Invisible button out of the window."""
    css = ".chip[hidden]{display:inline-flex!important}"
    r = ui.run_all(sizes=[(1360, 860, 1)], extra_css=css, overlay=False)["1360x860@1"]
    assert not r["ok"], _report(r)
    assert any("#chipLocal shows although it is hidden" in f for f in r["failures"]), _report(r)
    assert any("#chipCloud shows although it is hidden" in f for f in r["failures"]), _report(r)

def _bridge_calls(text):
    """Python methods a script calls on the bridge: b.name(, a.name(, api().name(, bridge().name(."""
    names = set(re.findall(r"\b(?:b|a|api\(\)|bridge\(\))\.([a-z][a-z_]*)\(", text))
    return {n for n in names if n not in {"replace", "then", "catch", "finally", "click", "focus"}}


def test_page_calls_only_methods_the_api_has():
    from ui import app as app_mod
    from ui import overlay
    api = {n for n, _ in inspect.getmembers(app_mod.Api, callable) if not n.startswith("_")}
    bar = {n for n, _ in inspect.getmembers(overlay.OverlayApi, callable) if not n.startswith("_")}
    page = set().union(*(_bridge_calls(p.read_text(encoding="utf-8")) for p in (WEB / "js").glob("*.js")))
    assert page and sorted(page - api) == []
    assert sorted(_bridge_calls((WEB / "overlay.html").read_text(encoding="utf-8")) - bar) == []


def test_mock_bridge_only_fakes_real_methods():
    from ui import app as app_mod
    from ui import overlay
    real = {n for cls in (app_mod.Api, overlay.OverlayApi) for n, _ in inspect.getmembers(cls, callable)
            if not n.startswith("_")}
    mock = (ROOT / "tests" / "ui" / "mock_bridge.js").read_text(encoding="utf-8")
    body = mock[mock.index("const canned={"):mock.index("window.__canned")]
    faked = set(re.findall(r"(?:^|[\s,{])([a-z][a-z_]*):(?:\(|[a-z]+=>)", body, re.M))
    assert faked and sorted(faked - real) == []
