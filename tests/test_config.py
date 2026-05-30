import pytest

from pironman5.config import Config, ConfigError, load_config


def test_defaults_validate():
    cfg = Config().validate()
    assert cfg.rgb.led_count == 4
    assert cfg.fan.gpio_pin == 6
    assert cfg.fan.mode == "auto"


def test_legacy_fan_modes_are_mapped():
    cfg = Config()
    cfg.fan.mode = "always_on"
    cfg.validate()
    assert cfg.fan.mode == "on"
    cfg.fan.mode = "quiet"
    cfg.validate()
    assert cfg.fan.mode == "auto"


def test_merge_partial():
    cfg = Config()
    changed = cfg.merge({"rgb": {"color": "#ff0000", "brightness": 50}})
    assert changed == ["rgb"]
    assert cfg.rgb.color == "#ff0000"
    assert cfg.rgb.brightness == 50


def test_merge_ignores_unknown_keys():
    cfg = Config()
    changed = cfg.merge({"rgb": {"nonsense": 1}, "ghost": {"x": 1}})
    assert changed == []


def test_invalid_color_rejected():
    cfg = Config()
    cfg.merge({"rgb": {"color": "not-a-color"}})
    with pytest.raises(ConfigError):
        cfg.validate()


def test_values_are_clamped():
    cfg = Config()
    cfg.merge({"rgb": {"brightness": 999}})
    cfg.validate()
    assert cfg.rgb.brightness == 100


def test_save_and_reload_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    cfg = Config()
    cfg.merge({"rgb": {"style": "rainbow"}, "fan": {"mode": "on"}})
    cfg.validate()
    cfg.save(path)

    reloaded = load_config(path)
    assert reloaded.rgb.style == "rainbow"
    assert reloaded.fan.mode == "on"


def test_per_led_colors_normalized_to_led_count():
    cfg = Config()
    # Too few entries get padded with the sync color up to led_count.
    cfg.merge({"rgb": {"sync": False, "colors": ["#ff0000"]}})
    cfg.validate()
    assert len(cfg.rgb.colors) == cfg.rgb.led_count
    assert cfg.rgb.colors[0] == "#ff0000"


def test_per_led_invalid_color_rejected():
    cfg = Config()
    cfg.merge({"rgb": {"colors": ["nope", "#00ff00", "#0000ff", "#ffffff"]}})
    with pytest.raises(ConfigError):
        cfg.validate()


def test_resolved_db_path_defaults_next_to_config(tmp_path):
    cfg = Config()
    cfg.path = tmp_path / "config.json"
    assert cfg.resolved_db_path == tmp_path / "history.db"
