from dataclasses import replace

import pytest

from pironman5.config import Config, FanConfig, RGBConfig
from pironman5.hardware.fan import decide
from pironman5.hardware.oled import HEIGHT, WIDTH, OLEDDriver, draw
from pironman5.hardware.rgb import BLACK, EFFECTS, frame
from pironman5.metrics import Interface, Sample

WIRED = Interface("eth0", up=True, ip="10.0.0.2")
WIFI = Interface("wlan0", wireless=True, up=True, ip="10.0.0.3", signal=-60)
SAMPLE = Sample(cpu_percent=11, cpu_temperature=47.3, memory_percent=38, disk_percent=2, interfaces=[WIRED])


def test_fan_modes():
    assert decide(FanConfig(mode="on"), False, 20.0) is True
    assert decide(FanConfig(mode="off"), True, 90.0) is False


def test_fan_auto_has_hysteresis():
    cfg = FanConfig(mode="auto", on_temp=55.0)
    assert decide(cfg, False, 54.9) is False
    assert decide(cfg, False, 55.0) is True
    assert decide(cfg, True, 52.0) is True
    assert decide(cfg, True, 51.0) is False
    assert decide(cfg, True, None) is True


def _rgb(**values):
    cfg = RGBConfig(**values)
    cfg.validate()
    return cfg


@pytest.mark.parametrize("style", EFFECTS)
def test_each_style_fills_the_strip(style):
    colors, _ = frame(_rgb(style=style, led_count=6), 1.23)
    assert len(colors) == 6
    assert all(0 <= channel <= 255 for color in colors for channel in color)


def test_static_frames_do_not_wake_the_driver():
    assert frame(_rgb(enable=False), 0.0) == ([BLACK] * 4, None)
    assert frame(_rgb(style="solid", color="#ff0000", brightness=50), 0.0) == ([(127, 0, 0)] * 4, None)
    colors, delay = frame(_rgb(sync=False, colors=["#ff0000", "#00ff00"], color="#0000ff"), 0.0)
    assert (colors, delay) == ([(255, 0, 0), (0, 255, 0), (0, 0, 255), (0, 0, 255)], None)


def test_breathing_peaks_at_half_period():
    cfg = _rgb(style="breathing", color="#ffffff", speed=100)
    assert frame(cfg, 0.0)[0][0] == BLACK
    assert frame(cfg, 0.5)[0][0] == (255, 255, 255)


def test_flow_moves_one_led_and_reverses():
    cfg = _rgb(style="flow", color="#ffffff", speed=100)
    assert [frame(cfg, 0.1 * i + 0.05)[0].index((255, 255, 255)) for i in range(5)] == [0, 1, 2, 3, 0]
    assert frame(replace(cfg, style="flow_reverse"), 0.05)[0].index((255, 255, 255)) == 3


def test_oled_picture_fits_the_panel():
    cfg = Config().validate()
    hot = replace(
        SAMPLE,
        cpu_percent=100,
        cpu_temperature=99.9,
        memory_percent=100,
        disk_percent=100,
        power_watts=25.0,
        interfaces=[replace(WIFI, ip="255.255.255.255")],
    )
    alert = replace(hot, throttle={"undervoltage": "now"})
    for sample in (SAMPLE, hot, alert, Sample()):
        canvas = draw(sample, cfg.merged({"system": {"temperature_unit": "F"}})[0], True)
        assert canvas.cols[WIDTH - 1] == 0
        assert all(column >> (HEIGHT - 1) == 0 for column in canvas.cols)
        assert len(canvas.pages(1, 1)) == WIDTH * HEIGHT // 8


def test_oled_status_line():
    cfg = Config().validate()

    def footer(**values):
        return [column >> 53 for column in draw(replace(SAMPLE, **values), cfg, False).cols]

    wired = footer()
    # The icon shows the interface type. The first interface with an address wins.
    assert footer(interfaces=[WIFI])[:8] != wired[:8]
    assert footer(interfaces=[Interface("wlan0", wireless=True), WIRED]) == wired
    # The time gives way to a long address, and an alert takes the full line.
    assert any(wired[100:]) and not any(footer(interfaces=[replace(WIRED, ip="255.255.255.255")])[112:])
    assert all(column == 0x3FF for column in footer(throttle={"undervoltage": "now"})[:20])


class _Panel:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *args: self.calls.append((name, *args))


def test_oled_sends_only_changed_columns():
    cfg = Config().validate()
    driver = OLEDDriver(mock=True)
    driver._panel = panel = _Panel()
    driver.flush(cfg, driver.draw(cfg, SAMPLE, False))
    assert [(page, column, len(data)) for _, page, column, data in panel.calls[-8:]] == [
        (page, 0, WIDTH) for page in range(8)
    ]
    panel.calls.clear()
    driver.flush(cfg, driver.draw(cfg, replace(SAMPLE, cpu_percent=12), False))
    # Only the bar end and the last digit of the CPU row change.
    assert [call[1] for call in panel.calls] in ([2, 3], [3])
    assert all(column > 30 and column + len(data) < WIDTH for _, _, column, data in panel.calls)


def test_oled_brightness_sets_the_contrast_once():
    driver = OLEDDriver(mock=True)
    driver._panel = panel = _Panel()
    for brightness in (100, 100, 50, 0):
        cfg = Config().merged({"oled": {"brightness": brightness}})[0]
        driver.flush(cfg, driver.draw(cfg, SAMPLE, False))
    assert [c for c in panel.calls if c[0] == "contrast"] == [
        ("contrast", 0xCF),
        ("contrast", 104),
        ("contrast", 0),
    ]


def test_oled_sleeps_and_wakes():
    cfg = Config().merged({"oled": {"sleep_timeout": 5}})[0]
    driver = OLEDDriver(mock=True)
    driver._panel = panel = _Panel()
    driver._wake_at -= 10
    driver.flush(cfg, driver.draw(cfg, SAMPLE, False))
    assert panel.calls == [("power", False)]
    assert driver.preview["awake"] is False
    driver.wake()
    driver.flush(cfg, driver.draw(cfg, SAMPLE, False))
    assert panel.calls[1] == ("power", True)
