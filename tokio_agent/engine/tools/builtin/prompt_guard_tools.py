"""
Prompt Guard Tool — Lightweight WAF for prompts.

Detect and block high-risk injection patterns in user inputs.
Audit and log suspicious prompts.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Risk patterns ─────────────────────────────────────────────────────────

_HIGH_RISK = [
    (r"ignore\s+(all\s+)?previous\s+(instructions|prompts|rules)", "prompt_override"),
    (r"you\s+are\s+now\s+", "identity_hijack"),
    (r"pretend\s+(you\s+are|to\s+be)\s+", "identity_hijack"),
    (r"act\s+as\s+(if|a|an)\s+", "identity_hijack"),
    (r"forget\s+(everything|all|your)\s+", "memory_wipe"),
    (r"disregard\s+(your|all|the)\s+", "prompt_override"),
    (r"system\s*:\s*you\s+are", "system_injection"),
    (r"<\|im_start\|>", "format_injection"),
    (r"\[INST\]", "format_injection"),
    (r"<<SYS>>", "format_injection"),
    (r"\\n\\nsystem:", "format_injection"),
    (r"reveal\s+(your|the)\s+(system|initial|original)\s+(prompt|instructions)", "prompt_leak"),
    (r"what\s+(are|were)\s+your\s+(original|system|initial)\s+(instructions|prompt)", "prompt_leak"),
    (r"output\s+(your|the)\s+(system|initial)\s+prompt", "prompt_leak"),
    (r"repeat\s+(your|the)\s+instructions\s+verbatim", "prompt_leak"),
]

_MEDIUM_RISK = [
    (r"do\s+not\s+follow\s+(your|any)\s+rules", "rule_bypass"),
    (r"override\s+(your|the|all)\s+", "override_attempt"),
    (r"jailbreak", "jailbreak"),
    (r"DAN\s+mode", "jailbreak"),
    (r"developer\s+mode", "jailbreak"),
    (r"sudo\s+mode", "privilege_escalation"),
    (r"admin\s+mode", "privilege_escalation"),
    (r"base64\s*decode", "encoding_evasion"),
    (r"rot13", "encoding_evasion"),
    (r"hex\s*decode", "encoding_evasion"),
]

_LOW_RISK = [
    (r"bypass", "bypass_mention"),
    (r"hack", "hack_mention"),
    (r"exploit", "exploit_mention"),
    (r"inject", "inject_mention"),
]

_PG_CONN = None


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
        cur = _PG_CONN.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tokio_prompt_guard_log (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                risk_level TEXT NOT NULL,
                categories TEXT[],
                input_hash TEXT,
                input_preview TEXT,
                blocked BOOLEAN DEFAULT FALSE
            )
        """)
        cur.close()
        return _PG_CONN
    except Exception:
        _PG_CONN = None
        return None


def _log_audit(risk: str, categories: List[str], text: str, blocked: bool) -> None:
    conn = _pg_connect()
    if not conn:
        return
    try:
        import hashlib
        h = hashlib.sha256(text.encode()).hexdigest()[:16]
        preview = text[:200].replace("'", "''")
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO tokio_prompt_guard_log(risk_level, categories, input_hash, input_preview, blocked) "
            "VALUES (%s, %s, %s, %s, %s)",
            (risk, categories, h, preview, blocked),
        )
        cur.close()
    except Exception as e:
        logger.debug("Prompt guard audit log failed: %s", e)


def analyze_prompt(text: str) -> Dict[str, Any]:
    """Analyze a prompt for injection risks. Returns risk assessment."""
    text_lower = (text or "").lower()
    findings: List[Dict] = []

    for pattern, category in _HIGH_RISK:
        if re.search(pattern, text_lower):
            findings.append({"level": "high", "category": category, "pattern": pattern})

    for pattern, category in _MEDIUM_RISK:
        if re.search(pattern, text_lower):
            findings.append({"level": "medium", "category": category, "pattern": pattern})

    for pattern, category in _LOW_RISK:
        if re.search(pattern, text_lower):
            findings.append({"level": "low", "category": category, "pattern": pattern})

    if not findings:
        return {"risk": "none", "safe": True, "findings": []}

    max_level = "low"
    if any(f["level"] == "high" for f in findings):
        max_level = "high"
    elif any(f["level"] == "medium" for f in findings):
        max_level = "medium"

    blocked = max_level == "high"
    categories = list(set(f["category"] for f in findings))
    _log_audit(max_level, categories, text, blocked)

    return {
        "risk": max_level,
        "safe": max_level != "high",
        "blocked": blocked,
        "categories": categories,
        "findings": findings,
    }


def prompt_guard_tool(action: str, params: Optional[Dict[str, Any]] = None) -> str:
    """
    Prompt Guard tool.

    Actions:
      - analyze: Analyze prompt for injection risks (params: text)
      - audit_log: Get recent audit log entries (params: limit)
      - stats: Get prompt guard statistics
    """
    params = params or {}
    action = (action or "").strip().lower()

    try:
        if action == "analyze":
            text = str(params.get("text", "")).strip()
            if not text:
                return json.dumps({"ok": False, "error": "text es requerido"})
            result = analyze_prompt(text)
            return json.dumps({"ok": True, **result}, ensure_ascii=False)

        elif action == "audit_log":
            limit = int(params.get("limit", 50))
            conn = _pg_connect()
            if not conn:
                return json.dumps({"ok": False, "error": "PostgreSQL no disponible"})
            cur = conn.cursor()
            cur.execute(
                "SELECT timestamp, risk_level, categories, input_preview, blocked "
                "FROM tokio_prompt_guard_log ORDER BY timestamp DESC LIMIT %s",
                (limit,),
            )
            rows = []
            for row in cur.fetchall():
                rows.append({
                    "timestamp": str(row[0]),
                    "risk_level": row[1],
                    "categories": row[2],
                    "preview": row[3],
                    "blocked": row[4],
                })
            cur.close()
            return json.dumps({"ok": True, "entries": rows}, ensure_ascii=False)

        elif action == "stats":
            conn = _pg_connect()
            if not conn:
                return json.dumps({"ok": False, "error": "PostgreSQL no disponible"})
            cur = conn.cursor()
            cur.execute("""
                SELECT risk_level, COUNT(*), SUM(CASE WHEN blocked THEN 1 ELSE 0 END)
                FROM tokio_prompt_guard_log
                GROUP BY risk_level
            """)
            stats = {}
            for row in cur.fetchall():
                stats[row[0]] = {"total": row[1], "blocked": row[2]}
            cur.close()
            return json.dumps({"ok": True, "stats": stats}, ensure_ascii=False)

        return json.dumps({"ok": False, "error": f"Acción no soportada: {action}",
                          "supported": ["analyze", "audit_log", "stats"]}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "action": action, "error": str(e)}, ensure_ascii=False)
