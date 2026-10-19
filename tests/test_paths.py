"""Meeting files can't be reached outside the data folder, whatever separator the page sends.

These were the Mac-only variants of the bridge checks (../ instead of ..\\); both styles now run on
both platforms."""
import pytest

SEPARATORS = pytest.mark.parametrize("sep", ["\\", "/"], ids=["backslash", "slash"])
MEETING = "meeting_2026-10-09_11-14.md"


@pytest.fixture
def meeting(api):
    (api._data / MEETING).write_text(
        "# Meeting\n\n**11:35:02** Awesome, no Cloud browser.  \n<sub>Geil, kein Cloud Browser.</sub>\n\n",
        encoding="utf-8")
    return MEETING


@pytest.fixture
def outside(api, tmp_path):
    p = tmp_path / "meeting_secret.md"
    p.write_text("**1** x  \n<sub>y</sub>\n", encoding="utf-8")
    return p


def test_a_meeting_in_the_data_folder_is_readable(api, meeting):
    assert api.read_meeting(meeting)[0]["de"].startswith("Geil")


@SEPARATORS
def test_read_blocks_traversal(api, outside, sep):
    up = ".." + sep + outside.parent.name + sep + outside.name
    assert api.read_meeting(up) == [] and api.read_meeting(str(outside)) == []


@SEPARATORS
def test_delete_blocks_traversal(api, outside, sep):
    api.delete_meeting(".." + sep + outside.parent.name + sep + outside.name)
    api.delete_meeting(str(outside))
    assert outside.exists()


@SEPARATORS
def test_archive_blocks_traversal(api, sep):
    assert api.archive_meeting(".." + sep + "x" + sep + "meeting_a.md") is False


@SEPARATORS
def test_export_blocks_traversal(api, outside, sep):
    assert api.export_meeting(".." + sep + outside.parent.name + sep + outside.name).get("ok") is False
    assert api.export_meeting(str(outside)).get("ok") is False


@pytest.mark.parametrize("folder", ["C:\\", "/"])
def test_data_folder_is_not_settable_from_the_page(api, folder):
    assert api.set_setting("data_folder", folder) is False


def test_export_writes_a_txt_next_to_the_chosen_name(api, meeting, tmp_path):
    api._window.save_as.append((str(tmp_path / "export"),))   # Cocoa returns a tuple, no extension
    r = api.export_meeting(meeting)
    assert r["ok"] and r["path"].endswith("export.txt")
    assert "Caption:     Geil, kein Cloud Browser." in (tmp_path / "export.txt").read_text(encoding="utf-8")
