"""Tests for centralized PostgreSQL pool (db.py)."""
import pytest
from unittest.mock import patch, MagicMock

from tokio_agent.engine.db import (
    _pg_config,
    get_connection,
    return_connection,
    execute_query,
    close_pool,
)


class TestPgConfig:
    @patch.dict("os.environ", {
        "POSTGRES_HOST": "myhost",
        "POSTGRES_PORT": "5433",
        "POSTGRES_DB": "mydb",
        "POSTGRES_USER": "myuser",
        "POSTGRES_PASSWORD": "mypass",
    })
    def test_reads_env(self):
        cfg = _pg_config()
        assert cfg["host"] == "myhost"
        assert cfg["port"] == 5433
        assert cfg["database"] == "mydb"
        assert cfg["user"] == "myuser"
        assert cfg["password"] == "mypass"

    @patch.dict("os.environ", {}, clear=True)
    def test_defaults(self):
        cfg = _pg_config()
        assert cfg["host"] == "postgres"
        assert cfg["port"] == 5432
        assert cfg["password"] == ""


class TestGetConnection:
    @patch.dict("os.environ", {"POSTGRES_PASSWORD": ""})
    def test_no_password_returns_none(self):
        # Reset pool state
        import tokio_agent.engine.db as db_mod
        db_mod._pool = None
        db_mod._pool_failed = False

        conn = get_connection()
        assert conn is None

    def test_return_connection_none(self):
        # Should not raise
        return_connection(None)


class TestClosePool:
    def test_close_resets_state(self):
        import tokio_agent.engine.db as db_mod
        db_mod._pool_failed = True
        close_pool()
        assert db_mod._pool_failed is False
        assert db_mod._pool is None
