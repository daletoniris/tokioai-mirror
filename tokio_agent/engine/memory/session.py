"""
Session Manager — Manages conversation sessions with persistence.

Sessions are stored in PostgreSQL (GCP) with SQLite fallback (CLI local).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages conversation sessions with dual persistence."""

    def __init__(self, workspace):
        self.workspace = workspace
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._sqlite_conn: Optional[sqlite3.Connection] = None
        self._ensure_sqlite()

    def create_session(self, session_id: Optional[str] = None) -> str:
        """Create a new session and return its ID."""
        sid = session_id or f"session-{uuid.uuid4().hex[:12]}"
        self._sessions[sid] = {
            "messages": [],
            "metadata": {
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            },
        }
        self._save_session(sid)
        return sid

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get a session by ID."""
        if session_id in self._sessions:
            return self._sessions[session_id]

        # Try loading from PostgreSQL first, then SQLite
        return self._load_session(session_id)

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
    ) -> None:
        """Add a message to a session."""
        session = self.get_session(session_id)
        if not session:
            self.create_session(session_id)
            session = self._sessions[session_id]

        session["messages"].append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        })
        session["metadata"]["updated_at"] = datetime.now().isoformat()

        # Soft cap at 200 messages as safety net
        if len(session["messages"]) > 200:
            session["messages"] = session["messages"][-200:]

        self._save_session(session_id)

    def get_conversation(
        self,
        session_id: str,
        max_messages: int = 100,
    ) -> List[Dict[str, str]]:
        """Get conversation history formatted for the LLM."""
        session = self.get_session(session_id)
        if not session:
            return []

        messages = session["messages"][-max_messages:]
        return [
            {"role": m["role"], "content": m["content"]}
            for m in messages
        ]

    def replace_messages(
        self,
        session_id: str,
        new_messages: List[Dict[str, str]],
    ) -> None:
        """Replace all messages in a session (used after compaction)."""
        session = self.get_session(session_id)
        if not session:
            self.create_session(session_id)
            session = self._sessions[session_id]

        session["messages"] = [
            {
                "role": m["role"],
                "content": m["content"],
                "timestamp": m.get("timestamp", datetime.now().isoformat()),
            }
            for m in new_messages
        ]
        session["metadata"]["updated_at"] = datetime.now().isoformat()
        self._save_session(session_id)

    def list_sessions(self, limit: int = 20) -> List[Dict[str, Any]]:
        """List recent sessions."""
        sessions = []
        for sid, data in sorted(
            self._sessions.items(),
            key=lambda x: x[1]["metadata"].get("updated_at", ""),
            reverse=True,
        )[:limit]:
            sessions.append({
                "session_id": sid,
                "message_count": len(data["messages"]),
                "created_at": data["metadata"].get("created_at"),
                "updated_at": data["metadata"].get("updated_at"),
            })
        return sessions

    # ── SQLite (local fallback) ──

    def _get_sqlite_path(self) -> Path:
        """Get SQLite database path."""
        return self.workspace.workspace_dir / "sessions.db"

    def _ensure_sqlite(self) -> None:
        """Initialize SQLite database for local session persistence."""
        try:
            db_path = self._get_sqlite_path()
            self._sqlite_conn = sqlite3.connect(str(db_path))
            self._sqlite_conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    messages TEXT NOT NULL DEFAULT '[]',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL DEFAULT ''
                )
            """)
            self._sqlite_conn.commit()
        except Exception as e:
            logger.debug(f"SQLite init failed: {e}")
            self._sqlite_conn = None

    def _get_sqlite(self) -> Optional[sqlite3.Connection]:
        """Get SQLite connection."""
        if self._sqlite_conn is not None:
            try:
                self._sqlite_conn.execute("SELECT 1")
                return self._sqlite_conn
            except Exception:
                self._sqlite_conn = None
        self._ensure_sqlite()
        return self._sqlite_conn

    # ── Persistence (PG primary, SQLite fallback) ──

    def _save_session(self, session_id: str) -> None:
        """Save session to PostgreSQL and/or SQLite."""
        session = self._sessions.get(session_id)
        if not session:
            return

        messages_json = json.dumps(session["messages"], ensure_ascii=False)
        metadata_json = json.dumps(session["metadata"], ensure_ascii=False)

        # Try PostgreSQL first
        saved_pg = False
        conn = self.workspace._get_pg()
        if conn:
            try:
                cur = conn.cursor()
                cur.execute(
                    """INSERT INTO tokio_sessions (session_id, messages, metadata, updated_at)
                       VALUES (%s, %s, %s, NOW())
                       ON CONFLICT (session_id) DO UPDATE
                       SET messages = %s, metadata = %s, updated_at = NOW()""",
                    (session_id, messages_json, metadata_json,
                     messages_json, metadata_json),
                )
                saved_pg = True
            except Exception as e:
                logger.debug(f"Error saving session to PG: {e}")

        # Always save to SQLite as local backup
        sq = self._get_sqlite()
        if sq:
            try:
                sq.execute(
                    """INSERT OR REPLACE INTO sessions (session_id, messages, metadata, updated_at)
                       VALUES (?, ?, ?, ?)""",
                    (session_id, messages_json, metadata_json,
                     datetime.now().isoformat()),
                )
                sq.commit()
            except Exception as e:
                logger.debug(f"Error saving session to SQLite: {e}")

    def _load_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Load session from PostgreSQL, falling back to SQLite."""
        # Try PostgreSQL first
        conn = self.workspace._get_pg()
        if conn:
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT messages, metadata FROM tokio_sessions WHERE session_id = %s",
                    (session_id,),
                )
                row = cur.fetchone()
                if row:
                    session = {
                        "messages": row[0] if isinstance(row[0], list) else json.loads(row[0]),
                        "metadata": row[1] if isinstance(row[1], dict) else json.loads(row[1]),
                    }
                    self._sessions[session_id] = session
                    return session
            except Exception as e:
                logger.debug(f"Error loading session from PG: {e}")

        # Fallback to SQLite
        sq = self._get_sqlite()
        if sq:
            try:
                cur = sq.execute(
                    "SELECT messages, metadata FROM sessions WHERE session_id = ?",
                    (session_id,),
                )
                row = cur.fetchone()
                if row:
                    session = {
                        "messages": json.loads(row[0]),
                        "metadata": json.loads(row[1]),
                    }
                    self._sessions[session_id] = session
                    return session
            except Exception as e:
                logger.debug(f"Error loading session from SQLite: {e}")

        return None

    def get_recent_sessions(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Get most recent sessions from SQLite (for CLI session resume)."""
        sq = self._get_sqlite()
        if not sq:
            return []

        try:
            cur = sq.execute(
                "SELECT session_id, metadata, updated_at FROM sessions ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            )
            results = []
            for row in cur.fetchall():
                meta = json.loads(row[1]) if row[1] else {}
                results.append({
                    "session_id": row[0],
                    "updated_at": row[2],
                    "metadata": meta,
                })
            return results
        except Exception:
            return []
