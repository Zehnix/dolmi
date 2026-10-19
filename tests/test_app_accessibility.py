"""APP-14: the 'Larger UI text' option (Settings > Appearance) is saved and comes back in state()."""
import config


def test_text_size_defaults_to_normal(api):
    assert config.DEFAULTS["ui_text_size"] == "normal"
    assert api.state()["uiTextSize"] == "normal"


def test_larger_text_is_saved_and_reported(api):
    assert api.set_setting("ui_text_size", "large")
    assert api.state()["uiTextSize"] == "large"
    assert config.load_settings()["ui_text_size"] == "large"
    assert api.set_setting("ui_text_size", "normal")
    assert api.state()["uiTextSize"] == "normal"


def test_only_known_text_sizes_are_taken(api):
    for bad in ("huge", True, 2, None, ""):
        assert api.set_setting("ui_text_size", bad) is False
    assert api.state()["uiTextSize"] == "normal"
