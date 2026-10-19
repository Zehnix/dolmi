"""First-run copy of the user's files into the config folder, from wherever they used to live."""
import config
import dolmi_platform
import pytest


@pytest.fixture
def program(tmp_path, monkeypatch):
    root, cfg = tmp_path / "program", tmp_path / "config" / "Dolmi"
    (root / "Mac").mkdir(parents=True)
    (root / ".env").write_text("FROM=root\n", encoding="utf-8")
    (root / "Mac" / ".env").write_text("FROM=mac\n", encoding="utf-8")
    monkeypatch.setattr(config, "ROOT", root)
    monkeypatch.setattr(config, "CONFIG", cfg)
    return cfg


def test_windows_copies_from_the_program_folder(program, monkeypatch):
    monkeypatch.setattr(dolmi_platform, "OS", "win")
    config.prepare()
    assert (program / ".env").read_text(encoding="utf-8") == "FROM=root\n"


def test_a_mac_source_checkout_copies_from_the_old_mac_folder(program, monkeypatch):
    monkeypatch.setattr(dolmi_platform, "OS", "mac")
    config.prepare()
    assert (program / ".env").read_text(encoding="utf-8") == "FROM=mac\n"


def test_an_existing_config_file_is_never_overwritten(program, monkeypatch):
    monkeypatch.setattr(dolmi_platform, "OS", "win")
    program.mkdir(parents=True)
    (program / ".env").write_text("KEEP=1\n", encoding="utf-8")
    config.prepare()
    assert (program / ".env").read_text(encoding="utf-8") == "KEEP=1\n"
