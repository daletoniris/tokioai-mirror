"""
User Preferences Tool — Save and retrieve user preferences persistently.

Uses PostgreSQL when available, falls back to local JSON file.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_LOCAL_PATH = Path(os.getenv("TOKIO_PREFS_PATH", "/workspace/cli/user_preferences.json"))
_PG_CONN = None
_PG_READY = False


def _pg_connect():
    global _PG_CONN
    if _PG_CONN is not None:
        return _PG_CONN
    try:
        import psycopg2
        _PG_CONN = psycopg2.connect(
            host=os.getenv("POSTGRES_HOST", "postgres"),
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            database=os.getenv("POSTGRES_DB", "tokio"),
            user=os.getenv("POSTGRES_USER", "tokio"),
            password=os.getenv("POSTGRES_PASSWORD", ""),
            connect_timeout=5,
        )
        _PG_CONN.autocommit = True
        return _PG_CONN
    except Exception:
        _PG_CONN = None
        return None


def _pg_ensure():
    global _PG_READY
    if _PG_READY:
        return
    conn = _pg_connect()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tokio_user_preferences (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.close()
        _PG_READY = True
    except Exception:
        pass


def _load_local() -> Dict[str, str]:
    try:
        if _LOCAL_PATH.exists():
            return json.loads(_LOCAL_PATH.read_text())
    except Exception:
        pass
    return {}


def _save_local(prefs: Dict[str, str]) -> None:
    try:
        _LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LOCAL_PATH.write_text(json.dumps(prefs, ensure_ascii=False, indent=2))
    except Exception as e:
        logger.debug("Could not save local prefs: %s", e)


def _get(key: str) -> Optional[str]:
    _pg_ensure()
    conn = _pg_connect()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("SELECT value FROM tokio_user_preferences WHERE key=%s", (key,))
            row = cur.fetchone()
            cur.close()
            if row:
                return row[0]
        except Exception:
            pass
    prefs = _load_local()
    return prefs.get(key)


def _set(key: str, value: str) -> bool:
    _pg_ensure()
    conn = _pg_connect()
    pg_ok = False
    if conn:
        try:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO tokio_user_preferences(key, value, updated_at)
                   VALUES (%s, %s, NOW())
                   ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()""",
                (key, value),
            )
            cur.close()
            pg_ok = True
        except Exception:
            pass
    prefs = _load_local()
    prefs[key] = value
    _save_local(prefs)
    return pg_ok or True


def _delete(key: str) -> bool:
    _pg_ensure()
    conn = _pg_connect()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM tokio_user_preferences WHERE key=%s", (key,))
            cur.close()
        except Exception:
            pass
    prefs = _load_local()
    if key in prefs:
        del prefs[key]
        _save_local(prefs)
    return True


def _get_all() -> Dict[str, str]:
    _pg_ensure()
    conn = _pg_connect()
    result: Dict[str, str] = {}
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("SELECT key, value FROM tokio_user_preferences")
            for row in cur.fetchall():
                result[row[0]] = row[1]
            cur.close()
            return result
        except Exception:
            pass
    return _load_local()


def user_preferences_tool(action: str, params: Optional[Dict[str, Any]] = None) -> str:
    """
    User preferences tool.

    Actions:
      - get: Get preference (params: key)
      - set: Set preference (params: key, value)
      - delete: Delete preference (params: key)
      - list: List all preferences
    """
    params = params or {}
    action = (action or "").strip().lower()

    try:
        if action == "get":
            key = str(params.get("key", "")).strip()
            if not key:
                return json.dumps({"ok": False, "error": "key es requerido"})
            value = _get(key)
            if value is None:
                return json.dumps({"ok": True, "found": False, "key": key})
            return json.dumps({"ok": True, "found": True, "key": key, "value": value}, ensure_ascii=False)

        elif action == "set":
            key = str(params.get("key", "")).strip()
            value = str(params.get("value", "")).strip()
            if not key:
                return json.dumps({"ok": False, "error": "key es requerido"})
            _set(key, value)
            return json.dumps({"ok": True, "key": key, "value": value}, ensure_ascii=False)

        elif action == "delete":
            key = str(params.get("key", "")).strip()
            if not key:
                return json.dumps({"ok": False, "error": "key es requerido"})
            _delete(key)
            return json.dumps({"ok": True, "deleted": key}, ensure_ascii=False)

        elif action == "list":
            prefs = _get_all()
            return json.dumps({"ok": True, "preferences": prefs}, ensure_ascii=False)

        return json.dumps({"ok": False, "error": f"Acción no soportada: {action}",
                          "supported": ["get", "set", "delete", "list"]}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "action": action, "error": str(e)}, ensure_ascii=False)
