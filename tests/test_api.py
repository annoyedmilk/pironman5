import base64
import json
import os
import socket
import urllib.request

from pironman5.web.server import ws_frame


def test_status_returns_hardware_state(client):
    status, body = client.get("/api/v1/status")
    assert status == 200
    assert body["mock"] is True
    assert "rgb_leds" in body
    assert len(base64.b64decode(body["oled_preview"]["fb"])) == 1024


def test_get_config(client):
    _, cfg = client.get("/api/v1/config")
    assert cfg["rgb"]["led_count"] == 4
    assert cfg["fan"]["gpio_pin"] == 6


def test_patch_config_applies_and_persists(client, core):
    status, body = client.patch("/api/v1/config", {"rgb": {"color": "#ff8800"}})
    assert status == 200
    assert "rgb" in body["changed"]
    assert core.config.rgb.color == "#ff8800"
    assert core.rgb.cfg.color == "#ff8800"
    assert json.loads(core.config.path.read_text())["rgb"]["color"] == "#ff8800"


def test_patch_config_rejects_invalid(client, core):
    status, body = client.patch("/api/v1/config", {"rgb": {"style": "disco"}})
    assert status == 422
    assert "rgb.style" in body["detail"]
    assert core.config.rgb.style != "disco"


def test_rejected_patch_does_not_block_later_changes(client, core):
    for bad in ({"rgb": {"brightness": "abc"}}, {"rgb": "x"}, {"rgb": {"enable": "no"}}, ["rgb"]):
        assert client.patch("/api/v1/config", bad)[0] == 422
    assert client.patch("/api/v1/config", {"fan": {"mode": "on"}})[0] == 200
    assert core.config.fan.mode == "on"
    assert core.config.rgb.enable is True


def test_patch_cannot_set_hardware_pins_or_paths(client, core):
    for locked in ({"fan": {"gpio_pin": 17}}, {"history": {"db_path": "/tmp/x.db"}}, {"web": {"port": 80}}):
        assert client.patch("/api/v1/config", locked)[0] == 422
    assert core.config.fan.gpio_pin == 6


def test_cross_origin_patch_is_refused(client, core):
    status, _ = client.patch("/api/v1/config", {"fan": {"mode": "off"}}, {"Origin": "http://evil.example"})
    assert status == 403
    assert core.config.fan.mode == "auto"


def test_history_endpoint(client):
    status, body = client.get("/api/v1/history?range=1h")
    assert status == 200
    assert body["range"] == "1h"
    assert isinstance(body["samples"], list)
    assert client.get("/api/v1/history?range=2y")[0] == 400


def test_static_files(client):
    with urllib.request.urlopen(client.base + "/") as res:
        assert res.headers["Content-Type"].startswith("text/html")
        etag = res.headers["ETag"]
    assert client.request("GET", "/", headers={"If-None-Match": etag}) == (304, None)
    with urllib.request.urlopen(client.base + "/static/icon-192.png") as res:
        assert res.headers["Content-Type"] == "image/png"
    assert client.get("/static/manifest.webmanifest")[1]["name"] == "Pironman 5"
    for path in ("/static/../server.py", "/static/nope.js", "/api/v1/nope"):
        assert client.get(path)[0] == 404


def _ws_connect(port):
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall(
        f"GET /api/v1/stream HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\n"
        f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode()
    )
    reader = sock.makefile("rb")
    assert reader.readline().startswith(b"HTTP/1.1 101")
    while reader.readline() != b"\r\n":
        pass
    return sock, reader


def _ws_text(reader):
    head, size = reader.read(2)
    assert head == 0x81
    if size == 126:
        size = int.from_bytes(reader.read(2), "big")
    return json.loads(reader.read(size))


def test_stream_sends_frames(server, core):
    sock, reader = _ws_connect(server.server_address[1])
    assert "rgb_leds" in _ws_text(reader)
    core.tick()
    assert _ws_text(reader)["hostname"]
    # A masked close frame ends the stream and frees the client slot.
    sock.sendall(bytes([0x88, 0x80, 1, 2, 3, 4]))
    core.tick()
    assert reader.read(2) == ws_frame(0x8, b"")
    sock.close()
