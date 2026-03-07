"""
Workspace — Agent identity, long-term memory, and user preferences.

Manages:
- SOUL: Agent identity and personality
- MEMORY: Learned facts and user preferences
- PostgreSQL persistence for cross-session data
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class Workspace:
    """Manages the agent's persistent workspace."""

    def __init__(
        self,
        workspace_dir: Optional[str] = None,
        pg_dsn: Optional[str] = None,
    ):
        default_dir = "/workspace/cli" if os.path.isdir("/workspace") else os.path.expanduser("~/.tokio/workspace")
        self.workspace_dir = Path(
            workspace_dir or os.getenv("TOKIO_WORKSPACE", default_dir)
        )
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

        # File-based storage
        self._soul_path = self.workspace_dir / "SOUL.md"
        self._memory_path = self.workspace_dir / "MEMORY.md"
        self._config_path = self.workspace_dir / "config.json"

        # PostgreSQL
        self._pg_dsn = pg_dsn or self._build_pg_dsn()
        self._pg_conn = None

        # In-memory cache (per-user: keyed by user_id)
        self._preferences: Dict[str, str] = {}
        self._user_preferences: Dict[str, Dict[str, str]] = {}
        self._memory_entries: List[str] = []

        # Initialize
        self._ensure_files()
        self._load_from_files()
        self._ensure_pg()

    # ── Identity (SOUL) ──

    def get_soul(self) -> str:
        """Get the agent's identity/personality."""
        if self._soul_path.exists():
            return self._soul_path.read_text()
        return self._default_soul()

    def update_soul(self, content: str) -> None:
        self._soul_path.write_text(content)

    # ── Memory ──

    def get_memory(self) -> str:
        """Get all memory entries as text."""
        if self._memory_path.exists():
            return self._memory_path.read_text()
        return ""

    def add_memory(self, entry: str, user_id: str = "") -> None:
        """Add a new memory entry. If user_id provided, store per-user."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        line = f"- [{timestamp}] {entry}"
        self._memory_entries.append(line)

        # Append to file (global memory)
        with open(self._memory_path, "a") as f:
            f.write(line + "\n")

        # Also persist to PostgreSQL (per-user if user_id provided)
        self._pg_save_memory(entry, user_id)

    def get_user_memory(self, user_id: str) -> str:
        """Get memory entries for a specific user."""
        conn = self._get_pg()
        if not conn:
            return ""
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT entry, created_at FROM tokio_user_memory WHERE user_id = %s ORDER BY id DESC LIMIT 30",
                (user_id,),
            )
            rows = cur.fetchall()
            if rows:
                lines = [f"- [{r[1].strftime('%Y-%m-%d %H:%M')}] {r[0]}" for r in reversed(rows)]
                return "\n".join(lines)
        except Exception as e:
            logger.debug(f"Error loading user memory: {e}")
        return ""

    def search_memory(self, query: str, user_id: str = "") -> List[str]:
        """Search memory for matching entries."""
        query_lower = query.lower()
        results = [e for e in self._memory_entries if query_lower in e.lower()]
        if user_id:
            conn = self._get_pg()
            if conn:
                try:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT entry FROM tokio_user_memory WHERE user_id = %s AND entry ILIKE %s LIMIT 20",
                        (user_id, f"%{query}%"),
                    )
                    results.extend([r[0] for r in cur.fetchall()])
                except Exception:
                    pass
        return results

    # ── User Preferences (per-user isolation) ──

    def get_preference(self, key: str, default: str = "", user_id: str = "") -> str:
        """Get a user preference. If user_id is provided, get per-user pref."""
        if user_id:
            cache = self._user_preferences.get(user_id, {})
            val = cache.get(key)
            if val is not None:
                return val
            val = self._pg_get_preference(key, user_id)
            if val is not None:
                self._user_preferences.setdefault(user_id, {})[key] = val
                return val
            return default
        # Fallback to global (CLI usage)
        val = self._preferences.get(key)
        if val is not None:
            return val
        val = self._pg_get_preference(key)
        if val is not None:
            self._preferences[key] = val
            return val
        return default

    def set_preference(self, key: str, value: str, user_id: str = "") -> None:
        """Set a user preference. If user_id is provided, store per-user."""
        if user_id:
            self._user_preferences.setdefault(user_id, {})[key] = value
            self._pg_save_preference(key, value, user_id)
        else:
            self._preferences[key] = value
            self._pg_save_preference(key, value)

    def get_all_preferences(self, user_id: str = "") -> Dict[str, str]:
        """Get all user preferences for a specific user."""
        if user_id:
            pg_prefs = self._pg_get_all_preferences(user_id)
            cache = self._user_preferences.get(user_id, {})
            return {**pg_prefs, **cache}
        pg_prefs = self._pg_get_all_preferences()
        return {**pg_prefs, **self._preferences}

    # ── Config ──

    def get_config(self) -> Dict[str, Any]:
        """Get the agent configuration."""
        if self._config_path.exists():
            try:
                return json.loads(self._config_path.read_text())
            except json.JSONDecodeError:
                return {}
        return {}

    def save_config(self, config: Dict[str, Any]) -> None:
        self._config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2)
        )

    # ── PostgreSQL ──

    def _build_pg_dsn(self) -> str:
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = os.getenv("POSTGRES_PORT", "5432")
        db = os.getenv("POSTGRES_DB", "tokio")
        user = os.getenv("POSTGRES_USER", "tokio")
        pw = os.getenv("POSTGRES_PASSWORD", "")
        return f"postgresql://{user}:{pw}@{host}:{port}/{db}"

    def _get_pg(self):
        """Get PostgreSQL connection (lazy init)."""
        if self._pg_conn is not None:
            try:
                self._pg_conn.cursor().execute("SELECT 1")
                return self._pg_conn
            except Exception:
                self._pg_conn = None

        try:
            import psycopg2  # type: ignore
            self._pg_conn = psycopg2.connect(self._pg_dsn, connect_timeout=5)
            self._pg_conn.autocommit = True
            return self._pg_conn
        except Exception as e:
            logger.debug(f"PostgreSQL no disponible: {e}")
            return None

    def _ensure_pg(self) -> None:
        """Create tables if they don't exist."""
        conn = self._get_pg()
        if not conn:
            return
        try:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tokio_memory (
                    id SERIAL PRIMARY KEY,
                    entry TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tokio_preferences (
                    key VARCHAR(255) PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tokio_sessions (
                    session_id VARCHAR(255) PRIMARY KEY,
                    messages JSONB DEFAULT '[]'::jsonb,
                    metadata JSONB DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            # Per-user preferences (isolated per Telegram user)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tokio_user_preferences (
                    user_id VARCHAR(255) NOT NULL,
                    key VARCHAR(255) NOT NULL,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    PRIMARY KEY (user_id, key)
                )
            """)
            # Per-user memory (isolated per Telegram user)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tokio_user_memory (
                    id SERIAL PRIMARY KEY,
                    user_id VARCHAR(255) NOT NULL,
                    entry TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_memory_user_id
                ON tokio_user_memory (user_id)
            """)
            # Load global preferences from PG into cache
            cur.execute("SELECT key, value FROM tokio_preferences")
            for row in cur.fetchall():
                if row[0] not in self._preferences:
                    self._preferences[row[0]] = row[1]
        except Exception as e:
            logger.debug(f"Error creating PG tables: {e}")

    def _pg_save_memory(self, entry: str, user_id: str = "") -> None:
        conn = self._get_pg()
        if not conn:
            return
        try:
            if user_id:
                conn.cursor().execute(
                    "INSERT INTO tokio_user_memory (user_id, entry) VALUES (%s, %s)",
                    (user_id, entry),
                )
            else:
                conn.cursor().execute(
                    "INSERT INTO tokio_memory (entry) VALUES (%s)", (entry,)
                )
        except Exception as e:
            logger.debug(f"Error saving memory to PG: {e}")

    def _pg_save_preference(self, key: str, value: str, user_id: str = "") -> None:
        conn = self._get_pg()
        if not conn:
            return
        try:
            if user_id:
                conn.cursor().execute(
                    """INSERT INTO tokio_user_preferences (user_id, key, value, updated_at)
                       VALUES (%s, %s, %s, NOW())
                       ON CONFLICT (user_id, key) DO UPDATE SET value = %s, updated_at = NOW()""",
                    (user_id, key, value, value),
                )
            else:
                conn.cursor().execute(
                    """INSERT INTO tokio_preferences (key, value, updated_at)
                       VALUES (%s, %s, NOW())
                       ON CONFLICT (key) DO UPDATE SET value = %s, updated_at = NOW()""",
                    (key, value, value),
                )
        except Exception as e:
            logger.debug(f"Error saving preference to PG: {e}")

    def _pg_get_preference(self, key: str, user_id: str = "") -> Optional[str]:
        conn = self._get_pg()
        if not conn:
            return None
        try:
            cur = conn.cursor()
            if user_id:
                cur.execute(
                    "SELECT value FROM tokio_user_preferences WHERE user_id = %s AND key = %s",
                    (user_id, key),
                )
            else:
                cur.execute("SELECT value FROM tokio_preferences WHERE key = %s", (key,))
            row = cur.fetchone()
            return row[0] if row else None
        except Exception:
            return None

    def _pg_get_all_preferences(self, user_id: str = "") -> Dict[str, str]:
        conn = self._get_pg()
        if not conn:
            return {}
        try:
            cur = conn.cursor()
            if user_id:
                cur.execute(
                    "SELECT key, value FROM tokio_user_preferences WHERE user_id = %s",
                    (user_id,),
                )
            else:
                cur.execute("SELECT key, value FROM tokio_preferences")
            return {row[0]: row[1] for row in cur.fetchall()}
        except Exception:
            return {}

    # ── File management ──

    def _ensure_files(self) -> None:
        if not self._soul_path.exists():
            self._soul_path.write_text(self._default_soul())
        if not self._memory_path.exists():
            self._memory_path.write_text("# TokioAI Memory\n\n")

    def _load_from_files(self) -> None:
        if self._memory_path.exists():
            for line in self._memory_path.read_text().splitlines():
                line = line.strip()
                if line.startswith("- "):
                    self._memory_entries.append(line)

    @staticmethod
    def _default_soul() -> str:
        return """# TokioAI — Autonomous Security Agent

I am **TokioAI**, an autonomous AI agent specialized in cybersecurity,
system administration, and infrastructure management.

## Core Principles
1. **Never give up** — Always find an alternative approach
2. **Think → Act → Observe → Learn** — Systematic problem solving
3. **Tool mastery** — Use tools effectively, learn from failures
4. **Complete context** — Understand the full picture before acting
5. **Error learning** — Every failure teaches me something

## Personality
- Proactive and autonomous
- Direct and clear communication
- Adapts language to the user's preference
- Security-first mindset
"""
