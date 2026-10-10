import pytest

from pironman5.config import Config, ConfigError, load_config


def test_defaults_validate():
    cfg = Config().validate()
    assert cfg.rgb.led_count == 4
    assert cfg.fan.gpio_pin == 6
    assert cfg.fan.mode == "auto"


def test_merged_returns_a_new_config():
    cfg = Config().validate()
    new, changed = cfg.merged({"rgb": {"color": "#ff0000", "brightness": 50}})
    assert changed == ["rgb"]
    assert (new.rgb.color, new.rgb.brightness) == ("#ff0000", 50)
    assert cfg.rgb.color == "#00ffff"


def test_merged_ignores_unknown_keys():
    cfg, changed = Config().merged({"rgb": {"nonsense": 1}, "ghost": {"x": 1}})
    assert changed == []
    assert cfg == Config().validate()


@pytest.mark.parametrize(
    "patch",
    [
        {"rgb": {"color": "not-a-color"}},
        {"rgb": {"colors": ["nope", "#00ff00", "#0000ff", "#ffffff"]}},
        {"rgb": {"brightness": "abc"}},
        {"rgb": {"brightness": True}},
        {"rgb": {"enable": "no"}},
        {"rgb": {"colors": "#ff0000"}},
        {"rgb": None},
        {"fan": {"mode": "turbo"}},
        {"oled": {"rotation": 90}},
    ],
)
def test_invalid_values_rejected(patch):
    with pytest.raises(ConfigError):
        Config().merged(patch)


def test_values_are_clamped():
    assert Config.from_dict({"rgb": {"brightness": 999}}).rgb.brightness == 100


def test_numbers_convert_between_int_and_float():
    cfg = Config.from_dict({"fan": {"on_temp": 60}, "rgb": {"speed": 30.0}})
    assert cfg.fan.on_temp == 60.0
    assert cfg.rgb.speed == 30


def test_save_and_reload_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    Config.from_dict({"rgb": {"style": "rainbow"}, "fan": {"mode": "on"}}).save(path)
    reloaded = load_config(path)
    assert reloaded.rgb.style == "rainbow"
    assert reloaded.fan.mode == "on"


def test_per_led_colors_normalized_to_led_count():
    cfg = Config.from_dict({"rgb": {"sync": False, "colors": ["#ff0000"]}})
    assert len(cfg.rgb.colors) == cfg.rgb.led_count
    assert cfg.rgb.colors[0] == "#ff0000"


def test_resolved_db_path_defaults_next_to_config(tmp_path, monkeypatch):
    monkeypatch.delenv("STATE_DIRECTORY", raising=False)
    cfg = Config()
    cfg.path = tmp_path / "config.json"
    assert cfg.resolved_db_path == tmp_path / "history.db"


def test_resolved_db_path_uses_the_systemd_state_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIRECTORY", "/var/lib/pironman5")
    cfg = Config()
    cfg.path = tmp_path / "config.json"
    assert cfg.resolved_db_path.as_posix() == "/var/lib/pironman5/history.db"
    assert (
        Config.from_dict({"history": {"db_path": "/data/h.db"}}).resolved_db_path.as_posix() == "/data/h.db"
    )
