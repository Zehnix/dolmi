"""APP-5: only one Dolmi at a time; a second launch asks the running one for its window and exits."""
import subprocess
import sys
import threading
import uuid
from pathlib import Path

import pytest

import dolmi_platform
from dolmi_platform import mac, windows

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("impl", [windows, mac], ids=["windows", "mac"])
def test_both_platforms_have_it(impl):
    assert callable(impl.single_instance) and callable(impl.bring_to_front)
    assert impl.bring_to_front(None) is False                 # no window yet: nothing to do, no crash


def _name():
    return f"DolmiTest-{uuid.uuid4().hex[:8]}"


def _check_second_launch(impl):
    name = _name()
    first = impl.single_instance(name)
    assert first is not None
    asked = threading.Event()
    first.listen(asked.set)
    try:
        assert impl.single_instance(name) is None             # a second launch: told to exit
        assert asked.wait(5)                                  # ...after asking the first for its window
        asked.clear()
        assert impl.single_instance(name) is None and asked.wait(5)   # every time
    finally:
        first.close()
    again = impl.single_instance(name)                        # the first one quit: a new launch may run
    assert again is not None
    again.close()


@pytest.mark.skipif(sys.platform != "win32", reason="named mutex: Windows")
def test_windows_second_launch_asks_the_first_and_exits():
    _check_second_launch(windows)


@pytest.mark.skipif(sys.platform == "win32", reason="flock + Unix socket: macOS (needs manual check on a Mac)")
def test_mac_second_launch_asks_the_first_and_exits(tmp_path, monkeypatch):
    monkeypatch.setattr(mac, "config_dir", lambda: tmp_path)
    _check_second_launch(mac)


@pytest.mark.skipif(sys.platform != "win32", reason="named mutex: Windows")
def test_windows_second_process_exits_and_signals():
    """Across processes, as two launches really are."""
    name = _name()
    first = windows.single_instance(name)
    asked = threading.Event()
    first.listen(asked.set)
    try:
        code = ("import sys; sys.path.insert(0, %r); from dolmi_platform import windows; "
                "sys.exit(0 if windows.single_instance(%r) is None else 3)") % (str(ROOT), name)
        r = subprocess.run([sys.executable, "-c", code], timeout=60)
        assert r.returncode == 0 and asked.wait(5)
    finally:
        first.close()


def test_main_exits_when_dolmi_already_runs(monkeypatch):
    import main
    from ui import app as app_mod
    monkeypatch.setattr(main, "_setup_output", lambda: None)
    monkeypatch.setattr(dolmi_platform, "single_instance", lambda name="Dolmi": None)
    monkeypatch.setattr(app_mod, "Api", lambda: pytest.fail("a second Dolmi must not start"))
    monkeypatch.setattr(main.webview, "start", lambda *a, **k: pytest.fail("a second Dolmi must not open a window"))
    assert main.main() is None


def test_bring_to_front_shows_the_window(api, monkeypatch):
    seen = []

    class Win:
        def show(self): seen.append("show")
        def restore(self): seen.append("restore")
    api._window = Win()
    monkeypatch.setattr(dolmi_platform, "bring_to_front", lambda w: seen.append("front"))
    api._bring_to_front()
    assert seen == ["show", "restore", "front"]
    assert not api._settings["invisible"]                     # invisible mode is left as it was


@pytest.mark.parametrize("value,want", [(None, "Dolmi"), ("", "Dolmi"), ("dev", "Dolmi-dev"), ("my_src-2", "Dolmi-my_src-2"),
                                        ("a\b", "Dolmi"), ("x" * 33, "Dolmi"), ("dév", "Dolmi")])
def test_developers_can_run_a_second_copy_with_dolmi_instance(monkeypatch, value, want):
    import main
    if value is None:
        monkeypatch.delenv("DOLMI_INSTANCE", raising=False)
    else:
        monkeypatch.setenv("DOLMI_INSTANCE", value)
    assert main._instance_name() == want
