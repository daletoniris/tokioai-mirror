"""
TokioAI Event Store — SQLite persistence for security events.

Stores: WAF attacks, WiFi attacks, BLE events, threat level changes,
visitor counts (stand mode), health alerts.

Never lose data on Entity restart.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Optional

logger = logging.getLogger("event_store")

DB_PATH = os.getenv("TOKIO_EVENT_DB", os.path.expanduser("~/tokio_events.db"))


class EventStore:
    """Thread-safe SQLite event store."""

    def __init__(self, db_path: str = DB_PATH):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self):
        """Create tables if they don't exist."""
        try:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")

            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    event_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    severity TEXT DEFAULT 'info',
                    data TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
                CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp);
                CREATE INDEX IF NOT EXISTS idx_events_source ON events(source);

                CREATE TABLE IF NOT EXISTS threat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    defcon INTEGER NOT NULL,
                    level_name TEXT,
                    score REAL,
                    vectors TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS visitor_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    count INTEGER,
                    recognized TEXT,
                    action TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS health_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    alert_type TEXT NOT NULL,
                    value REAL,
                    threshold REAL,
                    message TEXT,
                    notified INTEGER DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
            """)
            self._conn.commit()
            logger.info(f"Event store initialized: {self._db_path}")
        except Exception as e:
            logger.error(f"Failed to init event store: {e}")

    def log_event(self, event_type: str, source: str, severity: str = "info",
                  data: dict = None):
        """Log a security event."""
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO events (timestamp, event_type, source, severity, data) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (time.time(), event_type, source, severity,
                     json.dumps(data) if data else None)
                )
                self._conn.commit()
            except Exception as e:
                logger.error(f"Event log error: {e}")

    def log_threat(self, defcon: int, level_name: str, score: float,
                   vectors: dict = None):
        """Log a threat level change."""
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO threat_history (timestamp, defcon, level_name, score, vectors) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (time.time(), defcon, level_name, score,
                     json.dumps(vectors) if vectors else None)
                )
                self._conn.commit()
            except Exception as e:
                logger.error(f"Threat log error: {e}")

    def log_visitor(self, count: int, recognized: list = None, action: str = "detected"):
        """Log visitor activity."""
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO visitor_log (timestamp, count, recognized, action) "
                    "VALUES (?, ?, ?, ?)",
                    (time.time(), count, json.dumps(recognized) if recognized else None,
                     action)
                )
                self._conn.commit()
            except Exception as e:
                logger.error(f"Visitor log error: {e}")

    def log_health_alert(self, alert_type: str, value: float, threshold: float,
                         message: str):
        """Log a health alert."""
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO health_alerts (timestamp, alert_type, value, threshold, message) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (time.time(), alert_type, value, threshold, message)
                )
                self._conn.commit()
            except Exception as e:
                logger.error(f"Health alert log error: {e}")

    def get_events(self, event_type: str = None, source: str = None,
                   hours: float = 24, limit: int = 100) -> list:
        """Query recent events."""
        with self._lock:
            try:
                query = "SELECT timestamp, event_type, source, severity, data FROM events WHERE timestamp > ?"
                params = [time.time() - hours * 3600]
                if event_type:
                    query += " AND event_type = ?"
                    params.append(event_type)
                if source:
                    query += " AND source = ?"
                    params.append(source)
                query += " ORDER BY timestamp DESC LIMIT ?"
                params.append(limit)

                cursor = self._conn.execute(query, params)
                rows = cursor.fetchall()
                return [
                    {
                        "timestamp": r[0],
                        "event_type": r[1],
                        "source": r[2],
                        "severity": r[3],
                        "data": json.loads(r[4]) if r[4] else None,
                    }
                    for r in rows
                ]
            except Exception as e:
                logger.error(f"Event query error: {e}")
                return []

    def get_threat_history(self, hours: float = 24) -> list:
        """Get threat level history."""
        with self._lock:
            try:
                cursor = self._conn.execute(
                    "SELECT timestamp, defcon, level_name, score FROM threat_history "
                    "WHERE timestamp > ? ORDER BY timestamp DESC LIMIT 100",
                    (time.time() - hours * 3600,)
                )
                return [
                    {"timestamp": r[0], "defcon": r[1], "level_name": r[2], "score": r[3]}
                    for r in cursor.fetchall()
                ]
            except Exception as e:
                logger.error(f"Threat history error: {e}")
                return []

    def get_stats(self, hours: float = 24) -> dict:
        """Get event statistics."""
        with self._lock:
            try:
                since = time.time() - hours * 3600
                cursor = self._conn.execute(
                    "SELECT event_type, severity, COUNT(*) FROM events "
                    "WHERE timestamp > ? GROUP BY event_type, severity",
                    (since,)
                )
                by_type = {}
                by_severity = {}
                total = 0
                for row in cursor.fetchall():
                    t, s, c = row
                    by_type[t] = by_type.get(t, 0) + c
                    by_severity[s] = by_severity.get(s, 0) + c
                    total += c

                return {
                    "total_events": total,
                    "by_type": by_type,
                    "by_severity": by_severity,
                    "period_hours": hours,
                }
            except Exception as e:
                logger.error(f"Stats error: {e}")
                return {}

    def cleanup(self, days: int = 30):
        """Remove events older than N days."""
        with self._lock:
            try:
                cutoff = time.time() - days * 86400
                for table in ["events", "threat_history", "visitor_log", "health_alerts"]:
                    self._conn.execute(f"DELETE FROM {table} WHERE timestamp < ?", (cutoff,))
                self._conn.execute("VACUUM")
                self._conn.commit()
                logger.info(f"Cleaned up events older than {days} days")
            except Exception as e:
                logger.error(f"Cleanup error: {e}")

    def close(self):
        if self._conn:
            self._conn.close()
