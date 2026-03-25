"""
TokioAI Thought Log — Persistent memory of everything Tokio says/thinks.

Every thought, observation, and message displayed on screen gets logged
to a JSON file. Queryable from Telegram via the API.

Storage: ~/.tokio_health/thought_log.json (rotates at 2000 entries)
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Optional

LOG_DIR = os.path.expanduser("~/.tokio_health")
LOG_FILE = os.path.join(LOG_DIR, "thought_log.json")
MAX_ENTRIES = 2000
SAVE_INTERVAL = 30  # batch save every 30s


class ThoughtLog:
    """Persistent log of Tokio's thoughts and messages."""

    def __init__(self):
        self._lock = threading.Lock()
        self._entries: list[dict] = []
        self._dirty = False
        self._last_save = 0.0

        os.makedirs(LOG_DIR, exist_ok=True)
        self._load()

        # Background save thread
        threading.Thread(target=self._save_loop, daemon=True).start()

    def _load(self):
        try:
            if os.path.isfile(LOG_FILE):
                with open(LOG_FILE, "r") as f:
                    self._entries = json.load(f)
                print(f"[ThoughtLog] Loaded {len(self._entries)} entries")
        except Exception as e:
            print(f"[ThoughtLog] Load error: {e}")
            self._entries = []

    def _save(self):
        if not self._dirty:
            return
        try:
            with self._lock:
                data = list(self._entries[-MAX_ENTRIES:])
                self._dirty = False
            with open(LOG_FILE, "w") as f:
                json.dump(data, f, ensure_ascii=False)
            self._last_save = time.time()
        except Exception as e:
            print(f"[ThoughtLog] Save error: {e}")

    def _save_loop(self):
        while True:
            time.sleep(SAVE_INTERVAL)
            self._save()

    def add(self, text: str, source: str = "ai_brain", emotion: str = "neutral"):
        """Log a thought/message.

        Args:
            text: What Tokio said
            source: Where it came from (ai_brain, system, security, drone, health, etc.)
            emotion: Emotion at the time
        """
        entry = {
            "text": text,
            "time": time.time(),
            "source": source,
            "emotion": emotion,
        }
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > MAX_ENTRIES:
                self._entries = self._entries[-MAX_ENTRIES:]
            self._dirty = True

    def get_recent(self, limit: int = 20) -> list[dict]:
        """Get the N most recent entries."""
        with self._lock:
            entries = self._entries[-limit:]
        now = time.time()
        return [
            {**e, "age_s": int(now - e["time"]),
             "time_str": time.strftime("%H:%M:%S", time.localtime(e["time"]))}
            for e in entries
        ]

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Search thoughts by text content."""
        query_lower = query.lower()
        now = time.time()
        results = []
        with self._lock:
            for e in reversed(self._entries):
                if query_lower in e["text"].lower():
                    results.append({
                        **e, "age_s": int(now - e["time"]),
                        "time_str": time.strftime("%H:%M:%S", time.localtime(e["time"])),
                    })
                    if len(results) >= limit:
                        break
        return results

    def get_by_source(self, source: str, limit: int = 20) -> list[dict]:
        """Get entries from a specific source."""
        now = time.time()
        results = []
        with self._lock:
            for e in reversed(self._entries):
                if e.get("source") == source:
                    results.append({
                        **e, "age_s": int(now - e["time"]),
                        "time_str": time.strftime("%H:%M:%S", time.localtime(e["time"])),
                    })
                    if len(results) >= limit:
                        break
        return results

    def get_summary(self) -> dict:
        """Summary stats."""
        with self._lock:
            total = len(self._entries)
            if not self._entries:
                return {"total": 0}
            sources = {}
            emotions = {}
            for e in self._entries:
                src = e.get("source", "unknown")
                emo = e.get("emotion", "unknown")
                sources[src] = sources.get(src, 0) + 1
                emotions[emo] = emotions.get(emo, 0) + 1

            first_ts = self._entries[0].get("time", 0)
            last_ts = self._entries[-1].get("time", 0)

        return {
            "total": total,
            "sources": sources,
            "emotions": emotions,
            "first_entry": time.strftime("%Y-%m-%d %H:%M", time.localtime(first_ts)) if first_ts else "?",
            "last_entry": time.strftime("%Y-%m-%d %H:%M", time.localtime(last_ts)) if last_ts else "?",
            "hours_span": round((last_ts - first_ts) / 3600, 1) if first_ts and last_ts else 0,
        }

    def flush(self):
        """Force save to disk."""
        self._dirty = True
        self._save()
