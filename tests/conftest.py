import json
import urllib.error
import urllib.request

import pytest

from pironman5.config import Config
from pironman5.core import Core
from pironman5.web import Server


@pytest.fixture
def config(tmp_path):
    cfg = Config().validate()
    cfg.path = tmp_path / "config.json"
    cfg.history.db_path = str(tmp_path / "history.db")
    return cfg


@pytest.fixture
def core(config):
    c = Core(config, mock=True)
    yield c
    c.stop()


@pytest.fixture
def server(core):
    s = Server(core, "127.0.0.1", 0)
    s.start()
    yield s
    s.stop()


class Client:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"

    def request(self, method, path, body=None, headers=None):
        """Return (status, decoded JSON body or None)."""
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        try:
            res = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as exc:
            res = exc
        with res:
            text = res.read()
            return res.status, json.loads(text) if text else None

    def get(self, path):
        return self.request("GET", path)

    def patch(self, path, body, headers=None):
        return self.request("PATCH", path, body, headers)


@pytest.fixture
def client(server):
    return Client(server.server_address[1])
