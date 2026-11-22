from pathlib import Path

from chalkboard.config import Settings


def test_env_prefix(monkeypatch):
    url = "postgresql+psycopg://a:b@h/d"
    monkeypatch.setenv("CHALKBOARD_DATABASE_URL", url)
    assert Settings().database_url == url


def test_defaults(monkeypatch):
    monkeypatch.delenv("CHALKBOARD_DATABASE_URL", raising=False)
    monkeypatch.delenv("CHALKBOARD_DATA_DIR", raising=False)
    assert Settings().data_dir == Path("data")
