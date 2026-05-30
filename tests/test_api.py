def test_status_returns_hardware_state(client):
    body = client.get("/api/v1/status").json()
    assert body["mock"] is True
    assert "rgb_leds" in body
    assert "oled_preview" in body


def test_get_config(client):
    cfg = client.get("/api/v1/config").json()
    assert cfg["rgb"]["led_count"] == 4
    assert cfg["fan"]["gpio_pin"] == 6


def test_patch_config_applies_and_persists(client, core):
    res = client.patch("/api/v1/config", json={"rgb": {"color": "#ff8800"}})
    assert res.status_code == 200
    assert "rgb" in res.json()["changed"]
    assert core.config.rgb.color == "#ff8800"


def test_patch_config_rejects_invalid(client, core):
    res = client.patch("/api/v1/config", json={"rgb": {"style": "disco"}})
    assert res.status_code == 422
    # Bad value must not stick.
    assert core.config.rgb.style != "disco"


def test_patch_fan_mode(client, core):
    res = client.patch("/api/v1/config", json={"fan": {"mode": "on"}})
    assert res.status_code == 200
    assert core.config.fan.mode == "on"


def test_history_endpoint(client):
    body = client.get("/api/v1/history?range=1h").json()
    assert body["range"] == "1h"
    assert isinstance(body["samples"], list)


def test_stream_sends_initial_frame(client):
    with client.websocket_connect("/api/v1/stream") as ws:
        frame = ws.receive_json()
        assert "rgb_leds" in frame
