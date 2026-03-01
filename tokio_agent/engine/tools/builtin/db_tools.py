"""
Database tools — PostgreSQL query execution.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


async def postgres_query(query: str, database: Optional[str] = None) -> str:
    """Execute a PostgreSQL query.

    Args:
        query: SQL query to execute.
        database: Optional database name override.

    Returns:
        Formatted query results or status message.
    """
    try:
        import psycopg2  # type: ignore
    except ImportError:
        return "Error: psycopg2 no instalado. Ejecuta: pip install psycopg2-binary"

    db = database or os.getenv("POSTGRES_DB", "tokio")

    try:
        conn = psycopg2.connect(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            database=db,
            user=os.getenv("POSTGRES_USER", "tokio"),
            password=os.getenv("POSTGRES_PASSWORD", ""),
            connect_timeout=10,
        )

        cursor = conn.cursor()
        cursor.execute(query)

        q_upper = query.strip().upper()
        if q_upper.startswith("SELECT") or q_upper.startswith("WITH"):
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]

            if not rows:
                conn.close()
                return "Consulta ejecutada: 0 filas devueltas."

            # Format as table
            result = f"Columnas: {' | '.join(columns)}\n{'─' * 60}\n"
            for row in rows[:200]:  # Limit to 200 rows
                result += " | ".join(str(v) for v in row) + "\n"

            if len(rows) > 200:
                result += f"\n... ({len(rows)} filas total, mostrando 200)"

            conn.close()
            return result

        else:
            conn.commit()
            affected = cursor.rowcount
            conn.close()
            return f"✅ Query ejecutada. Filas afectadas: {affected}"

    except Exception as e:
        return f"Error PostgreSQL: {type(e).__name__}: {e}"
