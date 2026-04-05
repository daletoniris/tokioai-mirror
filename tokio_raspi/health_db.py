"""
TokioAI Health Database — SQLite persistent storage for all health metrics.

Stores: heart_rate, blood_pressure, spo2, steps, battery, hemoglobin,
        cholesterol, blood_sugar, uric_acid (future sensors).

Provides:
  - Historical queries (by date range, metric type)
  - Daily/weekly/monthly aggregates (avg, min, max)
  - Health trends and anomaly detection
  - Full report generation for Tokio to consult anytime
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

DB_PATH = os.path.expanduser("~/.tokio_health/health.db")

# All supported metrics — current and future
METRICS = [
    'heart_rate', 'bp_systolic', 'bp_diastolic', 'spo2',
    'steps', 'calories', 'distance', 'battery',
    # Future biometrics
    'hemoglobin', 'cholesterol_total', 'cholesterol_hdl', 'cholesterol_ldl',
    'blood_sugar', 'uric_acid',
]

# Normal ranges for health assessment
NORMAL_RANGES = {
    'heart_rate':       (60, 100, 'bpm'),
    'bp_systolic':      (90, 120, 'mmHg'),
    'bp_diastolic':     (60, 80, 'mmHg'),
    'spo2':             (95, 100, '%'),
    'hemoglobin':       (13.5, 17.5, 'g/dL'),       # male adult
    'cholesterol_total':(0, 200, 'mg/dL'),
    'cholesterol_hdl':  (40, 60, 'mg/dL'),
    'cholesterol_ldl':  (0, 100, 'mg/dL'),
    'blood_sugar':      (70, 100, 'mg/dL'),          # fasting
    'uric_acid':        (3.4, 7.0, 'mg/dL'),         # male adult
    'steps':            (0, 100000, 'steps'),
}


class HealthDB:
    """SQLite-based health data storage with full history and analytics."""

    def __init__(self, db_path: str = DB_PATH):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._lock:
            conn = self._get_conn()
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS readings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    datetime TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    value REAL NOT NULL,
                    unit TEXT,
                    source TEXT DEFAULT 'ble_watch',
                    notes TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_readings_metric 
                    ON readings(metric, timestamp);
                CREATE INDEX IF NOT EXISTS idx_readings_ts 
                    ON readings(timestamp);
                CREATE INDEX IF NOT EXISTS idx_readings_date 
                    ON readings(datetime);

                CREATE TABLE IF NOT EXISTS daily_summary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    avg_value REAL,
                    min_value REAL,
                    max_value REAL,
                    count INTEGER,
                    UNIQUE(date, metric)
                );
                CREATE INDEX IF NOT EXISTS idx_daily_date 
                    ON daily_summary(date, metric);
            """)
            conn.commit()
            conn.close()

    # ── Store readings ──

    def store(self, metric: str, value: float, unit: str = '',
              source: str = 'ble_watch', notes: str = None, ts: float = None):
        """Store a single health reading."""
        if ts is None:
            ts = time.time()
        dt = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                'INSERT INTO readings (timestamp, datetime, metric, value, unit, source, notes) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                (ts, dt, metric, value, unit, source, notes)
            )
            conn.commit()
            conn.close()

    def store_hr(self, hr: int, ts: float = None):
        self.store('heart_rate', hr, 'bpm', ts=ts)

    def store_bp(self, sys: int, dia: int, ts: float = None):
        self.store('bp_systolic', sys, 'mmHg', ts=ts)
        self.store('bp_diastolic', dia, 'mmHg', ts=ts)

    def store_spo2(self, spo2: int, ts: float = None):
        self.store('spo2', spo2, '%', ts=ts)

    def store_steps(self, steps: int, distance: int = 0, calories: int = 0, ts: float = None):
        self.store('steps', steps, 'steps', ts=ts)
        if distance > 0:
            self.store('distance', distance, 'm', ts=ts)
        if calories > 0:
            self.store('calories', calories, 'kcal', ts=ts)

    def store_battery(self, bat: int, ts: float = None):
        self.store('battery', bat, '%', source='watch_battery', ts=ts)

    def store_hemoglobin(self, value: float, ts: float = None):
        self.store('hemoglobin', value, 'g/dL', ts=ts)

    def store_cholesterol(self, total: float, hdl: float = None, ldl: float = None, ts: float = None):
        self.store('cholesterol_total', total, 'mg/dL', ts=ts)
        if hdl is not None:
            self.store('cholesterol_hdl', hdl, 'mg/dL', ts=ts)
        if ldl is not None:
            self.store('cholesterol_ldl', ldl, 'mg/dL', ts=ts)

    def store_blood_sugar(self, value: float, ts: float = None):
        self.store('blood_sugar', value, 'mg/dL', ts=ts)

    def store_uric_acid(self, value: float, ts: float = None):
        self.store('uric_acid', value, 'mg/dL', ts=ts)

    # ── Query readings ──

    def get_latest(self, metric: str) -> Optional[dict]:
        """Get most recent reading for a metric."""
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                'SELECT * FROM readings WHERE metric=? ORDER BY timestamp DESC LIMIT 1',
                (metric,)
            ).fetchone()
            conn.close()
            return dict(row) if row else None

    def get_range(self, metric: str, hours: int = 24, limit: int = 500) -> list[dict]:
        """Get readings for a metric within last N hours."""
        since = time.time() - (hours * 3600)
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                'SELECT * FROM readings WHERE metric=? AND timestamp>=? ORDER BY timestamp DESC LIMIT ?',
                (metric, since, limit)
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]

    def get_date_range(self, metric: str, start_date: str, end_date: str) -> list[dict]:
        """Get readings between dates (YYYY-MM-DD)."""
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                'SELECT * FROM readings WHERE metric=? AND datetime>=? AND datetime<? ORDER BY timestamp',
                (metric, start_date, end_date + ' 23:59:59')
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]

    def get_stats(self, metric: str, hours: int = 24) -> dict:
        """Get avg/min/max for a metric in last N hours."""
        since = time.time() - (hours * 3600)
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                'SELECT AVG(value) as avg, MIN(value) as min, MAX(value) as max, COUNT(*) as count '
                'FROM readings WHERE metric=? AND timestamp>=?',
                (metric, since)
            ).fetchone()
            conn.close()
            if row and row['count'] > 0:
                return {
                    'metric': metric,
                    'hours': hours,
                    'avg': round(row['avg'], 1),
                    'min': round(row['min'], 1),
                    'max': round(row['max'], 1),
                    'count': row['count']
                }
            return {'metric': metric, 'hours': hours, 'avg': 0, 'min': 0, 'max': 0, 'count': 0}

    # ── Daily summaries ──

    def update_daily_summary(self, date: str = None):
        """Compute daily summary for all metrics."""
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
        start = date + ' 00:00:00'
        end = date + ' 23:59:59'
        with self._lock:
            conn = self._get_conn()
            metrics = conn.execute(
                'SELECT DISTINCT metric FROM readings WHERE datetime>=? AND datetime<=?',
                (start, end)
            ).fetchall()
            for m in metrics:
                metric = m['metric']
                row = conn.execute(
                    'SELECT AVG(value) as avg, MIN(value) as min, MAX(value) as max, COUNT(*) as cnt '
                    'FROM readings WHERE metric=? AND datetime>=? AND datetime<=?',
                    (metric, start, end)
                ).fetchone()
                if row and row['cnt'] > 0:
                    conn.execute(
                        'INSERT OR REPLACE INTO daily_summary (date, metric, avg_value, min_value, max_value, count) '
                        'VALUES (?, ?, ?, ?, ?, ?)',
                        (date, metric, round(row['avg'], 1), round(row['min'], 1),
                         round(row['max'], 1), row['cnt'])
                    )
            conn.commit()
            conn.close()

    def get_daily_summaries(self, days: int = 7) -> list[dict]:
        """Get daily summaries for last N days."""
        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                'SELECT * FROM daily_summary WHERE date>=? ORDER BY date DESC, metric',
                (since,)
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]

    # ── Full health report ──

    def full_report(self) -> dict:
        """Generate comprehensive health report — everything Tokio needs."""
        report = {
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'current': {},
            'today': {},
            'week': {},
            'assessments': [],
            'total_readings': 0,
            'db_path': self._db_path,
        }

        # Total readings
        with self._lock:
            conn = self._get_conn()
            row = conn.execute('SELECT COUNT(*) as cnt FROM readings').fetchone()
            report['total_readings'] = row['cnt'] if row else 0
            conn.close()

        # Current values + today stats + week stats
        for metric in METRICS:
            latest = self.get_latest(metric)
            if latest:
                age_min = (time.time() - latest['timestamp']) / 60
                report['current'][metric] = {
                    'value': latest['value'],
                    'unit': latest.get('unit', ''),
                    'time': latest['datetime'],
                    'age_minutes': round(age_min, 1)
                }

            today_stats = self.get_stats(metric, hours=24)
            if today_stats['count'] > 0:
                report['today'][metric] = today_stats

            week_stats = self.get_stats(metric, hours=168)
            if week_stats['count'] > 0:
                report['week'][metric] = week_stats

        # Health assessments
        for metric, (low, high, unit) in NORMAL_RANGES.items():
            latest = self.get_latest(metric)
            if latest:
                val = latest['value']
                if val < low:
                    report['assessments'].append({
                        'metric': metric, 'value': val, 'unit': unit,
                        'status': 'LOW', 'range': f'{low}-{high}',
                        'message': f'{metric} is below normal ({val} {unit}, normal: {low}-{high})'
                    })
                elif val > high:
                    report['assessments'].append({
                        'metric': metric, 'value': val, 'unit': unit,
                        'status': 'HIGH', 'range': f'{low}-{high}',
                        'message': f'{metric} is above normal ({val} {unit}, normal: {low}-{high})'
                    })
                else:
                    report['assessments'].append({
                        'metric': metric, 'value': val, 'unit': unit,
                        'status': 'NORMAL', 'range': f'{low}-{high}',
                        'message': f'{metric} is normal ({val} {unit})'
                    })

        return report

    # ── Import legacy data ──

    def import_legacy_json(self, json_path: str) -> int:
        """Import data from the old health_log.json format."""
        if not os.path.exists(json_path):
            return 0
        with open(json_path, 'r') as f:
            data = json.load(f)

        count = 0
        for entry in data:
            ts = entry.get('ts', 0)
            if 'hr' in entry:
                self.store_hr(int(entry['hr']), ts=ts)
                count += 1
            if 'bp_sys' in entry and 'bp_dia' in entry:
                self.store_bp(int(entry['bp_sys']), int(entry['bp_dia']), ts=ts)
                count += 1
            if 'spo2' in entry:
                self.store_spo2(int(entry['spo2']), ts=ts)
                count += 1
            if 'steps' in entry:
                self.store_steps(int(entry['steps']), ts=ts)
                count += 1
            if 'bat' in entry:
                self.store_battery(int(entry['bat']), ts=ts)
                count += 1
        return count

    # ── DB stats ──

    def db_stats(self) -> dict:
        """Get database statistics."""
        with self._lock:
            conn = self._get_conn()
            total = conn.execute('SELECT COUNT(*) as c FROM readings').fetchone()['c']
            metrics = conn.execute(
                'SELECT metric, COUNT(*) as c, MIN(datetime) as first, MAX(datetime) as last '
                'FROM readings GROUP BY metric ORDER BY c DESC'
            ).fetchall()
            size = os.path.getsize(self._db_path) if os.path.exists(self._db_path) else 0
            conn.close()
            return {
                'total_readings': total,
                'db_size_mb': round(size / 1024 / 1024, 2),
                'metrics': [dict(m) for m in metrics]
            }
