"""
Session Manager — Manages conversation sessions with persistence.

Sessions are stored in PostgreSQL and cached in memory.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages conversation sessions."""

    def __init__(self, workspace):
        self.workspace = workspace
        self._sessions: Dict[str, Dict[str, Any]] = {}

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

        # Try loading from PostgreSQL
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

        # Soft cap at 200 messages as safety net (compaction handles the real limit)
        if len(session["messages"]) > 200:
            session["messages"] = session["messages"][-200:]

        self._save_session(session_id)

    def get_conversation(
        self,
        session_id: str,
        max_messages: int = 100,
    ) -> List[Dict[str, str]]:
        """Get conversation history formatted for the LLM.

        Returns up to max_messages. Token-based compaction in the agent
        handles the real context limit — this just caps message count.
        """
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
        """Replace all messages in a session (used after compaction).

        Args:
            session_id: The session to update.
            new_messages: New message list [{"role": ..., "content": ...}].
        """
        session = self.get_session(session_id)
        if not session:
            self.create_session(session_id)
            session = self._sessions[session_id]

        # Convert to internal format with timestamps
        from datetime import datetime
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

    def _save_session(self, session_id: str) -> None:
        """Save session to PostgreSQL."""
        session = self._sessions.get(session_id)
        if not session:
            return

        conn = self.workspace._get_pg()
        if not conn:
            return

        try:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO tokio_sessions (session_id, messages, metadata, updated_at)
                   VALUES (%s, %s, %s, NOW())
                   ON CONFLICT (session_id) DO UPDATE
                   SET messages = %s, metadata = %s, updated_at = NOW()""",
                (
                    session_id,
                    json.dumps(session["messages"]),
                    json.dumps(session["metadata"]),
                    json.dumps(session["messages"]),
                    json.dumps(session["metadata"]),
                ),
            )
        except Exception as e:
            logger.debug(f"Error saving session: {e}")

    def _load_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Load session from PostgreSQL."""
        conn = self.workspace._get_pg()
        if not conn:
            return None

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
            logger.debug(f"Error loading session: {e}")
        return None
