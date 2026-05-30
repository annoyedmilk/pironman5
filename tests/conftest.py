import pytest
from fastapi.testclient import TestClient

from pironman5.config import Config
from pironman5.core import Core
from pironman5.web import create_app


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
    if c.history is not None:
        c.history.close()


@pytest.fixture
def client(core):
    return TestClient(create_app(core))
