import pytest

from app import db


@pytest.fixture(autouse=True)
def isolate_default_database(monkeypatch, tmp_path):
    test_database = tmp_path / "agentgate-test.db"
    monkeypatch.setattr(db, "DB_PATH", test_database)
    db.init_db()
    assert db.DB_PATH == test_database
