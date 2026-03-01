"""
Centralized PostgreSQL connection pool — Singleton for the entire agent.

Usage:
    from tokio_agent.engine.db import get_connection

    conn = get_connection()
    if conn:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_pool: Optional[object] = None  # psycopg2 connection pool
_pool_failed = False


def _pg_config() -> dict:
    return {
        "host": os.getenv("POSTGRES_HOST", "postgres"),
        "port": int(os.getenv("POSTGRES_PORT", "5432")),
        "database": os.getenv("POSTGRES_DB", "tokio"),
        "user": os.getenv("POSTGRES_USER", "tokio"),
        "password": os.getenv("POSTGRES_PASSWORD", ""),
    }


def _init_pool() -> Optional[object]:
    """Initialize a thread-safe connection pool (lazy, once)."""
    global _pool, _pool_failed
    if _pool is not None:
        return _pool
    if _pool_failed:
        return None
    with _lock:
        if _pool is not None:
            return _pool
        try:
            from psycopg2 import pool as pg_pool
            cfg = _pg_config()
            if not cfg["password"]:
                logger.debug("POSTGRES_PASSWORD not set — skipping PG pool")
                _pool_failed = True
                return None
            _pool = pg_pool.ThreadedConnectionPool(
                minconn=1,
                maxconn=5,
                host=cfg["host"],
                port=cfg["port"],
                database=cfg["database"],
                user=cfg["user"],
                password=cfg["password"],
                connect_timeout=5,
            )
            logger.info("PostgreSQL connection pool initialized")
            return _pool
        except Exception as exc:
            logger.debug("PostgreSQL pool init failed: %s", exc)
            _pool_failed = True
            return None


def get_connection():
    """Get a connection from the pool. Returns None if PG is unavailable.

    IMPORTANT: Caller must call return_connection(conn) when done.
    """
    pool = _init_pool()
    if pool is None:
        return None
    try:
        conn = pool.getconn()
        conn.autocommit = True
        return conn
    except Exception as exc:
        logger.debug("Could not get PG connection: %s", exc)
        return None


def return_connection(conn) -> None:
    """Return a connection back to the pool."""
    if conn is None:
        return
    pool = _init_pool()
    if pool is None:
        try:
            conn.close()
        except Exception:
            pass
        return
    try:
        pool.putconn(conn)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass


def execute_query(query: str, params: tuple = (), fetch: bool = True):
    """Convenience: execute a query and return results or None."""
    conn = get_connection()
    if conn is None:
        return None
    try:
        cur = conn.cursor()
        cur.execute(query, params)
        if fetch:
            result = cur.fetchall()
        else:
            result = True
        cur.close()
        return result
    except Exception as exc:
        logger.debug("PG query failed: %s", exc)
        return None
    finally:
        return_connection(conn)


def ensure_table(ddl: str) -> bool:
    """Run a CREATE TABLE IF NOT EXISTS statement. Returns True on success."""
    conn = get_connection()
    if conn is None:
        return False
    try:
        cur = conn.cursor()
        cur.execute(ddl)
        cur.close()
        return True
    except Exception as exc:
        logger.debug("ensure_table failed: %s", exc)
        return False
    finally:
        return_connection(conn)


def close_pool() -> None:
    """Close the pool (call on shutdown)."""
    global _pool, _pool_failed
    with _lock:
        if _pool is not None:
            try:
                _pool.closeall()
            except Exception:
                pass
            _pool = None
        _pool_failed = False
