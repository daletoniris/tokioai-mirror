#!/usr/bin/env python3
"""
TokioAI WAF Dashboard v3 — Supreme SOC Dashboard
==================================================
Complete rewrite with:
- SSE live attack feed
- World attack map with GeoIP
- Attack heatmap (hour x day-of-week)
- Threat intelligence panel (AbuseIPDB)
- Signature monitor with hit counts
- Attack chain visualization
- IP reputation integration
- CSV export
- Cyberpunk SOC dark theme
"""
import os, json, secrets, time, csv, io, asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import FastAPI, Query, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
import jwt as pyjwt

DASH_USER = os.getenv("DASHBOARD_USER", "admin")
DASH_PASS = os.getenv("DASHBOARD_PASSWORD", "changeme")
JWT_SECRET = os.getenv("JWT_SECRET", secrets.token_hex(32))
JWT_ALGO = "HS256"
JWT_EXP_HOURS = 24
ABUSEIPDB_KEY = os.getenv("ABUSEIPDB_API_KEY", "")

PG = dict(
    host=os.getenv("POSTGRES_HOST", "postgres"),
    port=int(os.getenv("POSTGRES_PORT", "5432")),
    dbname=os.getenv("POSTGRES_DB", "tokio"),
    user=os.getenv("POSTGRES_USER", "tokio"),
    password=os.getenv("POSTGRES_PASSWORD", "changeme"),
)

app = FastAPI(title="TokioAI WAF Dashboard", docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
security = HTTPBearer(auto_error=False)

# Try to import GeoIP helper
try:
    import geoip_helper
    geoip_helper.init()
    HAS_GEOIP = True
except Exception:
    HAS_GEOIP = False


def get_db():
    conn = psycopg2.connect(**PG)
    conn.autocommit = True
    return conn


def ensure_schema():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS waf_logs (
        id BIGSERIAL PRIMARY KEY, timestamp TIMESTAMPTZ DEFAULT NOW(),
        ip TEXT, method TEXT, uri TEXT, status INT, body_bytes_sent INT DEFAULT 0,
        user_agent TEXT, host TEXT, referer TEXT, request_time FLOAT DEFAULT 0,
        severity TEXT DEFAULT 'info', blocked BOOLEAN DEFAULT FALSE,
        tenant_id TEXT, raw_log JSONB, classification_source TEXT,
        owasp_code TEXT, owasp_name TEXT, sig_id TEXT, threat_type TEXT,
        action TEXT DEFAULT 'log_only', confidence REAL,
        kafka_offset BIGINT, kafka_partition INT
    );
    CREATE INDEX IF NOT EXISTS idx_wl_ts ON waf_logs(timestamp DESC);
    CREATE INDEX IF NOT EXISTS idx_wl_ip ON waf_logs(ip);
    CREATE INDEX IF NOT EXISTS idx_wl_sev ON waf_logs(severity);
    CREATE INDEX IF NOT EXISTS idx_wl_tt ON waf_logs(threat_type);
    CREATE INDEX IF NOT EXISTS idx_wl_ow ON waf_logs(owasp_code);

    CREATE TABLE IF NOT EXISTS episodes (
        id BIGSERIAL PRIMARY KEY, episode_id TEXT UNIQUE,
        attack_type TEXT, severity TEXT DEFAULT 'medium', src_ip TEXT,
        start_time TIMESTAMPTZ, end_time TIMESTAMPTZ,
        total_requests INT DEFAULT 0, blocked_requests INT DEFAULT 0,
        sample_uris TEXT, intelligence_analysis TEXT,
        status TEXT DEFAULT 'active', ml_label TEXT, ml_confidence FLOAT,
        description TEXT, owasp_code TEXT, owasp_name TEXT, risk_score REAL DEFAULT 0,
        created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW(),
        tenant_id TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_ep_st ON episodes(start_time DESC);

    CREATE TABLE IF NOT EXISTS blocked_ips (
        id BIGSERIAL PRIMARY KEY, ip TEXT NOT NULL, reason TEXT,
        blocked_at TIMESTAMPTZ DEFAULT NOW(), expires_at TIMESTAMPTZ,
        active BOOLEAN DEFAULT TRUE, tenant_id TEXT, blocked_by TEXT,
        threat_type TEXT, severity TEXT DEFAULT 'high',
        episode_id TEXT, auto_blocked BOOLEAN DEFAULT FALSE,
        block_type TEXT DEFAULT 'manual', risk_score REAL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_bi_ip ON blocked_ips(ip);
    CREATE INDEX IF NOT EXISTS idx_bi_act ON blocked_ips(active);

    CREATE TABLE IF NOT EXISTS block_audit_log (
        id BIGSERIAL PRIMARY KEY, ip TEXT, action TEXT, reason TEXT,
        performed_by TEXT DEFAULT 'system', created_at TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS ip_reputation (
        ip TEXT PRIMARY KEY,
        reputation_score REAL DEFAULT 0.5,
        total_requests INT DEFAULT 0,
        total_threats INT DEFAULT 0,
        first_seen TIMESTAMPTZ DEFAULT NOW(),
        last_seen TIMESTAMPTZ DEFAULT NOW(),
        country TEXT, isp TEXT, tags TEXT[] DEFAULT '{}'
    );
    """)
    cur.close()
    conn.close()


BLOCKLIST_PATH = os.getenv("BLOCKLIST_PATH", "/blocklist/blocked.conf")


def sync_blocklist():
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT ip FROM blocked_ips WHERE active=true")
        ips = [row[0] for row in cur.fetchall()]
        cur.close(); conn.close()
        lines = ["# TokioAI WAF Blocklist — auto-generated",
                 f"# Updated: {datetime.now(timezone.utc).isoformat()}",
                 f"# Total blocked: {len(ips)}"]
        for ip in ips:
            lines.append(f"deny {ip};")
        lines.append("# End blocklist")
        bdir = os.path.dirname(BLOCKLIST_PATH)
        if bdir and not os.path.exists(bdir):
            os.makedirs(bdir, exist_ok=True)
        with open(BLOCKLIST_PATH, "w") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        print(f"[blocklist] Sync error: {e}", flush=True)


@app.on_event("startup")
async def startup():
    ensure_schema()
    sync_blocklist()


# ─── Auth ─────────────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str


def create_token(username: str) -> str:
    return pyjwt.encode({
        "sub": username,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXP_HOURS),
    }, JWT_SECRET, algorithm=JWT_ALGO)


def verify_token(creds: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    if not creds:
        raise HTTPException(401, "Token requerido")
    try:
        return pyjwt.decode(creds.credentials, JWT_SECRET, algorithms=[JWT_ALGO])["sub"]
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expirado")
    except Exception:
        raise HTTPException(401, "Token invalido")


# Anti brute-force: track failed login attempts per IP
_login_fails = {}  # {ip: (count, first_fail_time)}
LOGIN_MAX_FAILS = 5
LOGIN_LOCKOUT_SECS = 300  # 5 min lockout after 5 fails


@app.post("/api/auth/login")
def login(req: LoginRequest, request: Request):
    ip = request.headers.get("X-Real-IP", request.client.host)
    # Check lockout
    if ip in _login_fails:
        count, first_t = _login_fails[ip]
        if count >= LOGIN_MAX_FAILS and time.time() - first_t < LOGIN_LOCKOUT_SECS:
            raise HTTPException(429, f"Demasiados intentos. Reintente en {LOGIN_LOCKOUT_SECS - int(time.time() - first_t)}s")
        if time.time() - first_t >= LOGIN_LOCKOUT_SECS:
            del _login_fails[ip]
    if req.username == DASH_USER and req.password == DASH_PASS:
        _login_fails.pop(ip, None)
        return {"token": create_token(req.username), "expires_in": JWT_EXP_HOURS * 3600}
    # Track failure
    if ip in _login_fails:
        count, first_t = _login_fails[ip]
        _login_fails[ip] = (count + 1, first_t)
    else:
        _login_fails[ip] = (1, time.time())
    raise HTTPException(401, "Credenciales invalidas")


@app.get("/health")
def health():
    try:
        c = get_db(); c.close()
        return {"status": "healthy", "db": "ok", "version": "v3-supreme"}
    except Exception as e:
        return {"status": "degraded", "db": str(e)}


# ─── Core API ─────────────────────────────────────────────────────────────────
@app.get("/api/summary")
def summary(date_from: Optional[str] = None, date_to: Optional[str] = None,
            user: str = Depends(verify_token)):
    try:
        conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
        df, dt = _date_filter(date_from, date_to)
        cur.execute("""
            SELECT COUNT(*) total,
                   COUNT(CASE WHEN blocked THEN 1 END) blocked,
                   COUNT(DISTINCT ip) unique_ips,
                   COUNT(CASE WHEN severity='critical' THEN 1 END) critical,
                   COUNT(CASE WHEN severity='high' THEN 1 END) high,
                   COUNT(CASE WHEN severity='medium' THEN 1 END) medium,
                   COUNT(CASE WHEN severity='low' THEN 1 END) low
            FROM waf_logs WHERE timestamp >= %s AND timestamp <= %s
        """, [df, dt])
        row = cur.fetchone()
        cur.execute("SELECT COUNT(*) c FROM episodes WHERE status='active'")
        ep = cur.fetchone()
        cur.execute("SELECT COUNT(*) c FROM blocked_ips WHERE active=true")
        bl = cur.fetchone()
        # Previous period comparison
        period = dt - df
        prev_df = df - period
        prev_dt = df
        cur.execute("""SELECT COUNT(*) total,
                       COUNT(CASE WHEN severity IN ('high','critical') THEN 1 END) threats
                       FROM waf_logs WHERE timestamp >= %s AND timestamp <= %s""",
                    [prev_df, prev_dt])
        prev = cur.fetchone()
        cur.close(); conn.close()
        return {**row, "active_episodes": ep["c"], "active_blocks": bl["c"],
                "prev_total": prev["total"], "prev_threats": prev["threats"]}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/attacks/recent")
def recent_attacks(limit: int = Query(1000, ge=1, le=50000), offset: int = 0,
                   severity: Optional[str] = None, ip: Optional[str] = None,
                   threat_type: Optional[str] = None, owasp_code: Optional[str] = None,
                   search: Optional[str] = None,
                   date_from: Optional[str] = None, date_to: Optional[str] = None,
                   user: str = Depends(verify_token)):
    try:
        conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
        df, dt = _date_filter(date_from, date_to)
        clauses = ["timestamp >= %s", "timestamp <= %s"]
        params = [df, dt]
        if severity:
            clauses.append("severity = %s"); params.append(severity)
        if ip:
            clauses.append("ip = %s"); params.append(ip)
        if threat_type:
            clauses.append("threat_type = %s"); params.append(threat_type)
        if owasp_code:
            clauses.append("owasp_code = %s"); params.append(owasp_code)
        if search:
            clauses.append("(uri ILIKE %s OR ip ILIKE %s OR user_agent ILIKE %s OR host ILIKE %s)")
            s = f"%{search}%"
            params.extend([s, s, s, s])
        where = " WHERE " + " AND ".join(clauses)
        cur.execute(f"""SELECT timestamp,ip,method,uri,status,severity,blocked,host,user_agent,
                               request_time,threat_type,owasp_code,owasp_name,sig_id,action,confidence
                        FROM waf_logs{where} ORDER BY timestamp DESC LIMIT %s OFFSET %s""",
                    params + [limit, offset])
        rows = cur.fetchall(); cur.close(); conn.close()
        return [_serialize(r) for r in rows]
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/episodes")
def episodes(limit: int = 30, status: Optional[str] = None,
             date_from: Optional[str] = None, date_to: Optional[str] = None,
             user: str = Depends(verify_token)):
    try:
        conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
        df, dt = _date_filter(date_from, date_to)
        clauses = ["start_time >= %s", "start_time <= %s"]
        params = [df, dt]
        if status:
            clauses.append("status = %s"); params.append(status)
        where = " WHERE " + " AND ".join(clauses)
        cur.execute(f"""SELECT episode_id, attack_type, severity, src_ip,
                        start_time, end_time, total_requests, sample_uris,
                        status, ml_label, ml_confidence, description,
                        owasp_code, owasp_name, risk_score
                        FROM episodes{where} ORDER BY start_time DESC LIMIT %s""", params + [limit])
        rows = cur.fetchall(); cur.close(); conn.close()
        return [_serialize(r) for r in rows]
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/blocked")
def blocked_list(user: str = Depends(verify_token)):
    try:
        conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""SELECT ip, reason, threat_type, severity, blocked_at, expires_at,
                              auto_blocked, block_type, risk_score, episode_id
                       FROM blocked_ips WHERE active=true ORDER BY blocked_at DESC LIMIT 200""")
        rows = cur.fetchall(); cur.close(); conn.close()
        return [_serialize(r) for r in rows]
    except Exception as e:
        return {"error": str(e)}


class BlockIPRequest(BaseModel):
    ip: str
    reason: str = "Manual block"
    duration_hours: int = 24


@app.post("/api/blocked")
def block_ip(req: BlockIPRequest, user: str = Depends(verify_token)):
    try:
        conn = get_db(); cur = conn.cursor()
        expires = datetime.now(timezone.utc) + timedelta(hours=req.duration_hours)
        cur.execute("DELETE FROM blocked_ips WHERE ip=%s", (req.ip,))
        cur.execute("""INSERT INTO blocked_ips (ip, reason, expires_at, active, auto_blocked, block_type)
                       VALUES (%s, %s, %s, true, false, 'manual')""", (req.ip, req.reason, expires))
        cur.execute("INSERT INTO block_audit_log (ip, action, reason, performed_by) VALUES (%s, 'block', %s, %s)",
                    (req.ip, req.reason, user))
        cur.close(); conn.close()
        sync_blocklist()
        return {"ok": True, "ip": req.ip, "expires_at": str(expires)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.delete("/api/blocked/{ip}")
def unblock_ip(ip: str, user: str = Depends(verify_token)):
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("UPDATE blocked_ips SET active=false WHERE ip=%s AND active=true", (ip,))
        cur.execute("INSERT INTO block_audit_log (ip,action,reason,performed_by) VALUES (%s,'unblock','manual',%s)", (ip, user))
        cur.close(); conn.close()
        sync_blocklist()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/top_ips")
def top_ips(hours: int = 24, date_from: Optional[str] = None, date_to: Optional[str] = None,
            user: str = Depends(verify_token)):
    try:
        conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
        df, dt = _date_filter(date_from, date_to)
        cur.execute("""
            SELECT ip, COUNT(*) hits,
                   COUNT(CASE WHEN severity IN ('high','critical') THEN 1 END) threats,
                   COUNT(DISTINCT uri) unique_uris,
                   COUNT(DISTINCT threat_type) attack_types,
                   MAX(severity) max_severity
            FROM waf_logs WHERE ip NOT IN ('unknown','-','','172.18.0.1')
              AND timestamp >= %s AND timestamp <= %s
            GROUP BY ip ORDER BY threats DESC, hits DESC LIMIT 25
        """, (df, dt))
        rows = cur.fetchall(); cur.close(); conn.close()
        return [_serialize(r) for r in rows]
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/timeline")
def timeline(date_from: Optional[str] = None, date_to: Optional[str] = None,
             user: str = Depends(verify_token)):
    try:
        conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
        df, dt = _date_filter(date_from, date_to)
        # Auto-select granularity based on time range
        span = (dt - df).total_seconds()
        if span > 86400 * 3:  # >3 days → group by day
            trunc = 'day'
        elif span > 86400:  # >1 day → group by 4 hours
            trunc = 'hour'
        else:  # <=1 day → group by hour
            trunc = 'hour'
        cur.execute(f"""
            SELECT date_trunc('{trunc}', timestamp) as hour,
                   COUNT(*) total,
                   COUNT(CASE WHEN blocked THEN 1 END) blocked,
                   COUNT(CASE WHEN severity IN ('high','critical') THEN 1 END) threats
            FROM waf_logs WHERE timestamp >= %s AND timestamp <= %s
            GROUP BY 1 ORDER BY 1
        """, (df, dt))
        rows = cur.fetchall(); cur.close(); conn.close()
        result = [_serialize(r) for r in rows]
        for r in result:
            r["granularity"] = trunc
        return result
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/owasp_breakdown")
def owasp_breakdown(date_from: Optional[str] = None, date_to: Optional[str] = None,
                    user: str = Depends(verify_token)):
    try:
        conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
        df, dt = _date_filter(date_from, date_to)
        cur.execute("""
            SELECT owasp_code, owasp_name, COUNT(*) cnt,
                   COUNT(DISTINCT ip) unique_ips
            FROM waf_logs WHERE owasp_code IS NOT NULL
              AND timestamp >= %s AND timestamp <= %s
            GROUP BY owasp_code, owasp_name ORDER BY cnt DESC
        """, (df, dt))
        rows = cur.fetchall(); cur.close(); conn.close()
        return [_serialize(r) for r in rows]
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/audit")
def audit_log(limit: int = 50, user: str = Depends(verify_token)):
    try:
        conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM block_audit_log ORDER BY created_at DESC LIMIT %s", (limit,))
        rows = cur.fetchall(); cur.close(); conn.close()
        return [_serialize(r) for r in rows]
    except Exception as e:
        return {"error": str(e)}


# ─── NEW API ENDPOINTS ────────────────────────────────────────────────────────

@app.get("/api/geo_attacks")
def geo_attacks(date_from: Optional[str] = None, date_to: Optional[str] = None,
                user: str = Depends(verify_token)):
    """GeoIP breakdown of attacking IPs."""
    try:
        conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
        df, dt = _date_filter(date_from, date_to)
        cur.execute("""
            SELECT ip, COUNT(*) hits,
                   COUNT(CASE WHEN severity IN ('high','critical') THEN 1 END) threats
            FROM waf_logs WHERE severity != 'info'
              AND ip NOT IN ('unknown','-','','172.18.0.1')
              AND timestamp >= %s AND timestamp <= %s
            GROUP BY ip ORDER BY threats DESC LIMIT 200
        """, (df, dt))
        rows = cur.fetchall()
        cur.close(); conn.close()
        result = []
        for r in rows:
            geo = geoip_helper.lookup(r["ip"]) if HAS_GEOIP else {
                "country_code": "XX", "country": "Unknown", "lat": 0, "lng": 0}
            result.append({
                "ip": r["ip"], "hits": int(r["hits"]), "threats": int(r["threats"]),
                **geo
            })
        return result
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/heatmap")
def heatmap(date_from: Optional[str] = None, date_to: Optional[str] = None,
            user: str = Depends(verify_token)):
    """Attack heatmap: hour-of-day x day-of-week."""
    try:
        conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
        df, dt = _date_filter(date_from, date_to)
        cur.execute("""
            SELECT EXTRACT(dow FROM timestamp)::int as dow,
                   EXTRACT(hour FROM timestamp)::int as hour,
                   COUNT(*) cnt,
                   COUNT(CASE WHEN severity IN ('high','critical') THEN 1 END) threats
            FROM waf_logs WHERE timestamp >= %s AND timestamp <= %s
            GROUP BY 1, 2 ORDER BY 1, 2
        """, (df, dt))
        rows = cur.fetchall(); cur.close(); conn.close()
        return [_serialize(r) for r in rows]
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/attack_chains")
def attack_chains(ip: Optional[str] = None,
                  date_from: Optional[str] = None, date_to: Optional[str] = None,
                  user: str = Depends(verify_token)):
    """Correlated attack chains from same IP."""
    try:
        conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
        df, dt = _date_filter(date_from, date_to)
        if ip:
            cur.execute("""
                SELECT timestamp, ip, method, uri, status, severity, threat_type, sig_id, action
                FROM waf_logs WHERE ip = %s AND severity != 'info'
                  AND timestamp >= %s AND timestamp <= %s
                ORDER BY timestamp LIMIT 100
            """, (ip, df, dt))
        else:
            cur.execute("""
                WITH top_attackers AS (
                    SELECT ip FROM waf_logs
                    WHERE severity IN ('high','critical')
                      AND timestamp >= %s AND timestamp <= %s
                    GROUP BY ip HAVING COUNT(*) >= 3
                    ORDER BY COUNT(*) DESC LIMIT 10
                )
                SELECT w.timestamp, w.ip, w.method, w.uri, w.status, w.severity,
                       w.threat_type, w.sig_id, w.action
                FROM waf_logs w JOIN top_attackers t ON w.ip = t.ip
                WHERE w.severity != 'info' AND w.timestamp >= %s AND w.timestamp <= %s
                ORDER BY w.ip, w.timestamp LIMIT 500
            """, (df, dt, df, dt))
        rows = cur.fetchall(); cur.close(); conn.close()
        return [_serialize(r) for r in rows]
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/live_feed")
async def live_feed(request: Request, token: Optional[str] = None,
                    creds: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    """Server-Sent Events stream for real-time attack updates.
    Accepts token via query param (for EventSource) or Authorization header."""
    # EventSource can't send headers, so accept token as query param
    tok = None
    if creds:
        tok = creds.credentials
    elif token:
        tok = token
    if not tok:
        raise HTTPException(401, "Token requerido")
    try:
        pyjwt.decode(tok, JWT_SECRET, algorithms=[JWT_ALGO])
    except Exception:
        raise HTTPException(401, "Token invalido")
    async def event_generator():
        last_id = 0
        conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT MAX(id) mid FROM waf_logs")
        row = cur.fetchone()
        last_id = (row["mid"] or 0)
        cur.close(); conn.close()
        while True:
            if await request.is_disconnected():
                break
            try:
                conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
                cur.execute("""SELECT id, timestamp, ip, method, uri, status, severity,
                                      threat_type, sig_id, action, confidence
                               FROM waf_logs WHERE id > %s AND severity != 'info'
                               ORDER BY id LIMIT 20""", (last_id,))
                rows = cur.fetchall()
                cur.close(); conn.close()
                for r in rows:
                    last_id = r["id"]
                    data = _serialize(r)
                    if HAS_GEOIP:
                        geo = geoip_helper.lookup(r["ip"] or "")
                        data["country"] = geo.get("country_code", "XX")
                    yield f"data: {json.dumps(data)}\n\n"
            except Exception:
                pass
            await asyncio.sleep(3)
    return StreamingResponse(event_generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/threat_intel/{ip}")
def threat_intel(ip: str, user: str = Depends(verify_token)):
    """Lookup IP in AbuseIPDB + local reputation."""
    result = {"ip": ip, "local": {}, "abuseipdb": None, "geo": None}
    try:
        conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM ip_reputation WHERE ip=%s", (ip,))
        rep = cur.fetchone()
        if rep:
            result["local"] = _serialize(rep)
        cur.execute("""SELECT COUNT(*) total,
                       COUNT(CASE WHEN severity IN ('high','critical') THEN 1 END) threats,
                       COUNT(DISTINCT threat_type) attack_types,
                       MIN(timestamp) first_seen, MAX(timestamp) last_seen
                       FROM waf_logs WHERE ip=%s""", (ip,))
        stats = cur.fetchone()
        result["local_stats"] = _serialize(stats) if stats else {}
        cur.close(); conn.close()
    except Exception:
        pass
    if HAS_GEOIP:
        result["geo"] = geoip_helper.lookup(ip)
    if ABUSEIPDB_KEY:
        try:
            import urllib.request
            req = urllib.request.Request(
                f"https://api.abuseipdb.com/api/v2/check?ipAddress={ip}&maxAgeInDays=90",
                headers={"Key": ABUSEIPDB_KEY, "Accept": "application/json"})
            resp = urllib.request.urlopen(req, timeout=10)
            result["abuseipdb"] = json.loads(resp.read().decode())
        except Exception:
            pass
    return result


@app.get("/api/stats/daily")
def daily_stats(days: int = 30, user: str = Depends(verify_token)):
    """Daily statistics for trend charts."""
    try:
        conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT date_trunc('day', timestamp)::date as day,
                   COUNT(*) total,
                   COUNT(CASE WHEN blocked THEN 1 END) blocked,
                   COUNT(CASE WHEN severity IN ('high','critical') THEN 1 END) threats,
                   COUNT(DISTINCT ip) unique_ips
            FROM waf_logs WHERE timestamp >= NOW() - INTERVAL '%s days'
            GROUP BY 1 ORDER BY 1
        """, (days,))
        rows = cur.fetchall(); cur.close(); conn.close()
        return [_serialize(r) for r in rows]
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/export/csv")
def export_csv(date_from: Optional[str] = None, date_to: Optional[str] = None,
               severity: Optional[str] = None, user: str = Depends(verify_token)):
    """Export filtered logs as CSV."""
    try:
        conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
        df, dt = _date_filter(date_from, date_to)
        clauses = ["timestamp >= %s", "timestamp <= %s"]
        params = [df, dt]
        if severity:
            clauses.append("severity = %s"); params.append(severity)
        where = " WHERE " + " AND ".join(clauses)
        cur.execute(f"""SELECT timestamp,ip,method,uri,status,severity,blocked,
                               threat_type,owasp_code,sig_id,action,confidence,user_agent
                        FROM waf_logs{where} ORDER BY timestamp DESC LIMIT 50000""", params)
        rows = cur.fetchall(); cur.close(); conn.close()
        output = io.StringIO()
        if rows:
            writer = csv.DictWriter(output, fieldnames=rows[0].keys())
            writer.writeheader()
            for r in rows:
                writer.writerow({k: str(v) if v is not None else "" for k, v in r.items()})
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode()),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=waf_logs_{datetime.now().strftime('%Y%m%d')}.csv"})
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/signatures")
def signatures(date_from: Optional[str] = None, date_to: Optional[str] = None,
               user: str = Depends(verify_token)):
    """List all WAF signatures with hit counts."""
    try:
        conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
        df, dt = _date_filter(date_from, date_to)
        cur.execute("""
            SELECT sig_id, threat_type, COUNT(*) hits,
                   MAX(timestamp) last_hit, MAX(severity) max_severity
            FROM waf_logs WHERE sig_id IS NOT NULL
              AND timestamp >= %s AND timestamp <= %s
            GROUP BY sig_id, threat_type ORDER BY hits DESC
        """, (df, dt))
        rows = cur.fetchall(); cur.close(); conn.close()
        return [_serialize(r) for r in rows]
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/reputation")
def reputation_list(user: str = Depends(verify_token)):
    """List IPs with lowest reputation scores."""
    try:
        conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""SELECT ip, reputation_score, total_requests, total_threats,
                              first_seen, last_seen, country, tags
                       FROM ip_reputation ORDER BY reputation_score ASC LIMIT 50""")
        rows = cur.fetchall(); cur.close(); conn.close()
        return [_serialize(r) for r in rows]
    except Exception as e:
        return {"error": str(e)}


# ─── Helpers ──────────────────────────────────────────────────────────────────
def _date_filter(date_from, date_to):
    now = datetime.now(timezone.utc)
    if date_from:
        try:
            df = datetime.fromisoformat(date_from.replace("Z", "+00:00"))
        except Exception:
            df = now - timedelta(days=7)
    else:
        df = now - timedelta(days=7)
    if date_to:
        try:
            dt = datetime.fromisoformat(date_to.replace("Z", "+00:00"))
        except Exception:
            dt = now
    else:
        dt = now
    return df, dt


def _serialize(row):
    return {k: str(v) if v is not None else None for k, v in row.items()}


# ─── Dashboard HTML — Supreme Cyberpunk SOC ──────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index():
    return DASHBOARD_HTML


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TokioAI WAF — Supreme SOC</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;900&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root{--bg:#060a14;--bg2:#0a0f1e;--card:#0d1424;--card2:#131b30;--border:#1a2540;--border2:#243050;
--primary:#00e5ff;--primary2:#00b8d4;--danger:#ff1744;--warning:#ff9100;--success:#00e676;
--text:#e8eaf6;--text2:#7986cb;--accent:#7c4dff;--accent2:#b388ff;
--gradient:linear-gradient(135deg,#00e5ff 0%,#7c4dff 50%,#ff1744 100%);
--glow:0 0 20px rgba(0,229,255,.15);--glow-strong:0 0 30px rgba(0,229,255,.3)}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;overflow-x:hidden}

/* ─── Animated Background Grid ─── */
body::before{content:'';position:fixed;inset:0;background:
  linear-gradient(rgba(0,229,255,.03) 1px,transparent 1px),
  linear-gradient(90deg,rgba(0,229,255,.03) 1px,transparent 1px);
  background-size:60px 60px;pointer-events:none;z-index:0;animation:gridShift 20s linear infinite}
@keyframes gridShift{0%{transform:translate(0,0)}100%{transform:translate(60px,60px)}}

/* ─── Scanner Line ─── */
.scanner-line{position:fixed;top:0;left:0;right:0;height:2px;background:var(--gradient);z-index:9998;
  animation:scanLine 4s ease-in-out infinite;opacity:.6}
@keyframes scanLine{0%{transform:translateY(0);opacity:.6}50%{transform:translateY(50vh);opacity:.2}100%{transform:translateY(0);opacity:.6}}

/* ─── Login ─── */
.login-overlay{position:fixed;inset:0;background:var(--bg);display:flex;align-items:center;justify-content:center;z-index:9999}
.login-box{background:var(--card);border:1px solid var(--border);border-radius:20px;padding:48px;width:420px;text-align:center;box-shadow:var(--glow-strong);position:relative;overflow:hidden}
.login-box::before{content:'';position:absolute;top:-50%;left:-50%;width:200%;height:200%;background:conic-gradient(from 0deg,transparent,rgba(0,229,255,.05),transparent,rgba(124,77,255,.05),transparent);animation:loginGlow 6s linear infinite}
@keyframes loginGlow{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}
.login-box>*{position:relative;z-index:1}
.login-box h1{color:var(--primary);font-size:28px;margin-bottom:4px;font-weight:900;letter-spacing:-0.5px;text-shadow:0 0 30px rgba(0,229,255,.3)}
.login-box .sub{color:var(--text2);margin-bottom:28px;font-size:12px;letter-spacing:2px;text-transform:uppercase}
.login-box input{width:100%;padding:14px 18px;background:var(--bg);border:1px solid var(--border);border-radius:10px;color:var(--text);font-size:14px;margin-bottom:14px;outline:none;transition:all .3s}
.login-box input:focus{border-color:var(--primary);box-shadow:0 0 15px rgba(0,229,255,.15)}
.login-box button{width:100%;padding:15px;background:var(--gradient);border:none;border-radius:10px;color:#fff;font-size:15px;font-weight:700;cursor:pointer;transition:all .3s;text-shadow:0 1px 2px rgba(0,0,0,.3)}
.login-box button:hover{transform:translateY(-2px);box-shadow:0 8px 25px rgba(0,229,255,.25)}
.login-error{color:var(--danger);font-size:13px;margin-top:10px;display:none}
.app{display:none;position:relative;z-index:1}

/* ─── Topbar ─── */
.topbar{background:var(--card);border-bottom:1px solid var(--border);padding:8px 20px;display:flex;align-items:center;justify-content:space-between;gap:12px;position:sticky;top:0;z-index:100;backdrop-filter:blur(10px)}
.topbar .left{display:flex;align-items:center;gap:10px}
.topbar h1{font-size:17px;color:var(--primary);white-space:nowrap;font-weight:900;letter-spacing:-0.3px;text-shadow:0 0 20px rgba(0,229,255,.2)}
.topbar .right{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.topbar .status{font-size:11px;color:var(--success);animation:pulse 2s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
.topbar .ver{font-size:10px;color:var(--text2);font-family:'JetBrains Mono',monospace}

/* ─── Buttons ─── */
.btn-sm{padding:6px 14px;border-radius:8px;border:1px solid var(--border);background:var(--card2);color:var(--text);cursor:pointer;font-size:12px;font-weight:500;transition:all .25s;white-space:nowrap}
.btn-sm:hover{border-color:var(--primary);color:var(--primary);box-shadow:0 0 10px rgba(0,229,255,.1)}
.btn-danger{border-color:var(--danger);color:var(--danger)}.btn-danger:hover{box-shadow:0 0 10px rgba(255,23,68,.2)}
.btn-primary{background:var(--gradient);border:none;color:#fff;font-weight:700}

/* ─── Filters ─── */
.filters{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:10px 20px;background:var(--card);border-bottom:1px solid var(--border)}
.filters input,.filters select{padding:7px 10px;background:var(--bg);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:12px;outline:none;transition:border .2s}
.filters input:focus,.filters select:focus{border-color:var(--primary)}
.filters input[type=date]{width:140px}.filters input[type=text]{width:160px}
.filters label{font-size:10px;color:var(--text2);text-transform:uppercase;letter-spacing:.8px;font-weight:600}
.filter-group{display:flex;flex-direction:column;gap:3px}

/* ─── Layout ─── */
.container{max-width:1600px;margin:0 auto;padding:14px 20px}

/* ─── Stats Cards ─── */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:10px;margin-bottom:14px}
.stat-card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px;position:relative;overflow:hidden;transition:all .3s}
.stat-card:hover{border-color:var(--primary);box-shadow:var(--glow)}
.stat-card::after{content:'';position:absolute;bottom:0;left:0;right:0;height:2px}
.stat-card.c-primary::after{background:var(--primary)}.stat-card.c-danger::after{background:var(--danger)}
.stat-card.c-warning::after{background:var(--warning)}.stat-card.c-success::after{background:var(--success)}
.stat-card.c-accent::after{background:var(--accent)}
.stat-card .label{font-size:10px;color:var(--text2);text-transform:uppercase;letter-spacing:.8px;font-weight:600}
.stat-card .value{font-size:26px;font-weight:900;margin-top:2px;font-family:'JetBrains Mono',monospace}
.stat-card .trend{font-size:10px;margin-top:2px;font-weight:600}
.stat-card .trend.up{color:var(--danger)}.stat-card .trend.down{color:var(--success)}.stat-card .trend.flat{color:var(--text2)}
.c-primary .value{color:var(--primary)}.c-danger .value{color:var(--danger)}
.c-warning .value{color:var(--warning)}.c-success .value{color:var(--success)}.c-accent .value{color:var(--accent)}

/* ─── Grid layouts ─── */
.row-2{display:grid;grid-template-columns:2fr 1fr;gap:12px;margin-bottom:14px}
.row-3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:14px}
.row-live{display:grid;grid-template-columns:3fr 1fr;gap:12px;margin-bottom:14px}

/* ─── Panels ─── */
.panel{background:var(--card);border:1px solid var(--border);border-radius:12px;overflow:hidden;transition:border .3s}
.panel:hover{border-color:var(--border2)}
.panel-header{padding:12px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;background:var(--card2)}
.panel-header h3{font-size:13px;font-weight:700;display:flex;align-items:center;gap:8px}
.panel-header h3 i{color:var(--primary);font-size:14px}

/* ─── Tabs ─── */
.tabs{display:flex;gap:4px;margin-bottom:12px;background:var(--card);border-radius:12px;padding:4px;border:1px solid var(--border)}
.tab{padding:8px 16px;border-radius:10px;cursor:pointer;font-size:12px;font-weight:600;color:var(--text2);transition:all .25s}
.tab.active{background:var(--gradient);color:#fff}
.tab:hover:not(.active){color:var(--text);background:var(--card2)}

/* ─── Tables ─── */
table{width:100%;border-collapse:collapse}
th{background:var(--card2);padding:8px 12px;text-align:left;font-size:10px;color:var(--text2);text-transform:uppercase;letter-spacing:.8px;font-weight:700;position:sticky;top:0;z-index:1}
td{padding:7px 12px;border-top:1px solid var(--border);font-size:11px}
tr:hover td{background:rgba(0,229,255,.02)}
.table-wrap{max-height:500px;overflow-y:auto}
.table-wrap::-webkit-scrollbar{width:4px}.table-wrap::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}

/* ─── Badges ─── */
.badge{padding:2px 8px;border-radius:5px;font-size:10px;font-weight:700;text-transform:uppercase;white-space:nowrap;letter-spacing:.3px}
.badge.critical{background:rgba(255,23,68,.15);color:#ff1744;border:1px solid rgba(255,23,68,.3)}
.badge.high{background:rgba(255,145,0,.12);color:#ff9100;border:1px solid rgba(255,145,0,.25)}
.badge.medium{background:rgba(124,77,255,.12);color:#b388ff;border:1px solid rgba(124,77,255,.25)}
.badge.low{background:rgba(0,230,118,.12);color:#00e676;border:1px solid rgba(0,230,118,.25)}
.badge.info,.badge.normal{background:rgba(121,134,203,.1);color:#7986cb;border:1px solid rgba(121,134,203,.2)}
.badge.active{background:rgba(0,229,255,.12);color:#00e5ff;border:1px solid rgba(0,229,255,.25)}
.badge.resolved{background:rgba(121,134,203,.1);color:#546e7a}
.badge.blocked{background:rgba(255,23,68,.12);color:#ff1744;border:1px solid rgba(255,23,68,.25)}
.badge.owasp{background:rgba(0,229,255,.08);color:#00e5ff;border:1px solid rgba(0,229,255,.25)}

/* ─── Charts ─── */
.chart-box{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px;height:240px}

/* ─── OWASP Panel ─── */
.owasp-box{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px}
.owasp-item{display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid var(--border)}
.owasp-item:last-child{border:none}
.owasp-item .code{font-weight:900;font-size:13px;min-width:36px;font-family:'JetBrains Mono',monospace}
.owasp-item .name{flex:1;font-size:11px;color:var(--text2);margin-left:8px}
.owasp-item .cnt{font-weight:900;font-size:15px;font-family:'JetBrains Mono',monospace}

/* ─── Live Feed ─── */
.live-feed{background:var(--card);border:1px solid var(--border);border-radius:12px;overflow:hidden;max-height:600px}
.live-feed .feed-header{padding:10px 14px;border-bottom:1px solid var(--border);background:var(--card2);display:flex;align-items:center;gap:8px}
.live-feed .feed-header .dot{width:8px;height:8px;border-radius:50%;background:var(--danger);animation:blink 1s ease-in-out infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}
.live-feed .feed-header h4{font-size:12px;font-weight:700;color:var(--danger)}
.feed-list{max-height:550px;overflow-y:auto;padding:4px}
.feed-item{padding:8px 10px;border-bottom:1px solid var(--border);font-size:11px;animation:feedIn .3s ease-out;display:flex;gap:8px;align-items:flex-start}
@keyframes feedIn{from{opacity:0;transform:translateX(20px)}to{opacity:1;transform:translateX(0)}}
.feed-item .sev-dot{width:6px;height:6px;border-radius:50%;margin-top:4px;flex-shrink:0}
.feed-item .sev-dot.critical{background:var(--danger)}.feed-item .sev-dot.high{background:var(--warning)}
.feed-item .sev-dot.medium{background:var(--accent)}.feed-item .sev-dot.low{background:var(--success)}
.feed-item .feed-ip{color:var(--primary);font-weight:700;font-family:'JetBrains Mono',monospace;font-size:10px}
.feed-item .feed-uri{color:var(--text2);font-family:'JetBrains Mono',monospace;font-size:10px;word-break:break-all}
.feed-item .feed-type{font-weight:600;font-size:10px}
.feed-item .feed-time{color:var(--text2);font-size:9px;font-family:'JetBrains Mono',monospace}

/* ─── Heatmap ─── */
.heatmap-grid{display:grid;grid-template-columns:30px repeat(24,1fr);gap:2px;padding:10px}
.hm-label{font-size:9px;color:var(--text2);display:flex;align-items:center;justify-content:center;font-family:'JetBrains Mono',monospace}
.hm-cell{width:100%;aspect-ratio:1;border-radius:3px;cursor:pointer;transition:all .2s;position:relative}
.hm-cell:hover{transform:scale(1.3);z-index:2;box-shadow:var(--glow)}
.hm-cell[title]:hover::after{content:attr(title);position:absolute;bottom:120%;left:50%;transform:translateX(-50%);background:var(--card2);color:var(--text);padding:4px 8px;border-radius:4px;font-size:9px;white-space:nowrap;border:1px solid var(--border);z-index:10}

/* ─── World Map ─── */
.world-map{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px;position:relative;overflow:hidden;min-height:280px}
.map-container{position:relative;width:100%;height:260px}
.map-dot{position:absolute;width:8px;height:8px;border-radius:50%;transform:translate(-50%,-50%);animation:mapPulse 2s ease-out infinite;cursor:pointer}
.map-dot.critical{background:var(--danger);box-shadow:0 0 12px var(--danger)}
.map-dot.high{background:var(--warning);box-shadow:0 0 10px var(--warning)}
.map-dot.medium{background:var(--accent);box-shadow:0 0 8px var(--accent)}
@keyframes mapPulse{0%{transform:translate(-50%,-50%) scale(1);opacity:1}100%{transform:translate(-50%,-50%) scale(2.5);opacity:0}}
.map-bg{position:absolute;inset:0;opacity:.15;background:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1000 500'%3E%3Cpath d='M150 120 L200 100 L250 110 L280 95 L300 100 L320 90 L350 105 L330 120 L340 140 L320 160 L290 150 L270 160 L250 155 L230 165 L210 150 L180 155 L160 140 Z' fill='%2300e5ff' opacity='0.3'/%3E%3Cpath d='M480 80 L550 70 L620 80 L680 75 L700 90 L720 85 L750 100 L730 120 L740 150 L720 170 L680 160 L650 170 L620 165 L580 175 L550 160 L520 165 L500 145 L490 120 L495 100 Z' fill='%2300e5ff' opacity='0.3'/%3E%3Cpath d='M700 90 L780 80 L850 100 L900 95 L920 110 L900 140 L880 160 L850 170 L800 165 L760 180 L730 170 L720 150 L740 130 L730 110 Z' fill='%2300e5ff' opacity='0.3'/%3E%3Cpath d='M200 200 L230 190 L260 200 L280 220 L260 260 L240 280 L220 300 L200 290 L210 260 L195 240 L200 220 Z' fill='%2300e5ff' opacity='0.3'/%3E%3Cpath d='M500 180 L530 170 L560 175 L580 190 L570 220 L555 240 L535 250 L510 245 L500 220 L505 200 Z' fill='%2300e5ff' opacity='0.3'/%3E%3Cpath d='M800 280 L850 270 L880 290 L870 320 L840 330 L810 320 L800 300 Z' fill='%2300e5ff' opacity='0.3'/%3E%3C/svg%3E") center/contain no-repeat}
.map-dest{position:absolute;width:12px;height:12px;border-radius:50%;border:2px solid var(--primary);background:rgba(0,229,255,.3);transform:translate(-50%,-50%);animation:destPulse 2s ease-in-out infinite}
@keyframes destPulse{0%,100%{box-shadow:0 0 5px var(--primary)}50%{box-shadow:0 0 20px var(--primary)}}

/* ─── Risk bar ─── */
.risk-bar{width:60px;height:6px;background:var(--border);border-radius:3px;overflow:hidden;display:inline-block;vertical-align:middle}
.risk-bar .fill{height:100%;border-radius:3px;transition:width .5s}

/* ─── Signature Monitor ─── */
.sig-item{display:flex;align-items:center;padding:8px 12px;border-bottom:1px solid var(--border);gap:10px;font-size:11px}
.sig-item:hover{background:var(--card2)}
.sig-id{font-family:'JetBrains Mono',monospace;font-weight:700;color:var(--primary);min-width:70px}
.sig-type{min-width:100px;font-weight:600}.sig-hits{font-family:'JetBrains Mono',monospace;font-weight:900;font-size:14px;min-width:50px;text-align:right}
.sig-bar{flex:1;height:4px;background:var(--border);border-radius:2px;overflow:hidden}
.sig-bar .fill{height:100%;border-radius:2px;transition:width .5s}

/* ─── Misc ─── */
.mono{font-family:'JetBrains Mono',monospace;font-size:11px}
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:200;align-items:center;justify-content:center;backdrop-filter:blur(4px)}
.modal{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:28px;width:440px;box-shadow:var(--glow-strong)}
.modal h3{margin-bottom:18px;color:var(--primary);font-weight:900}
.modal input,.modal select{width:100%;padding:12px;background:var(--bg);border:1px solid var(--border);border-radius:8px;color:var(--text);margin-bottom:12px;font-size:13px}
.modal .actions{display:flex;gap:8px;justify-content:flex-end;margin-top:18px}
@media(max-width:1100px){.stats{grid-template-columns:repeat(3,1fr)}.row-2,.row-3,.row-live{grid-template-columns:1fr}.container{padding:10px}}
@media(max-width:700px){.stats{grid-template-columns:repeat(2,1fr)}.filters{flex-direction:column}}

/* ─── Animated Logo ─── */
.spiral-logo{position:relative;display:inline-flex;align-items:center;justify-content:center}
.spiral-logo .glow-bg{position:absolute;border-radius:50%;background:radial-gradient(circle,rgba(0,229,255,.1) 0%,transparent 70%);animation:sp-pulse 3s ease-in-out infinite}
.spiral-md{width:80px;height:80px}.spiral-md .glow-bg{width:80px;height:80px}.spiral-md svg{width:80px;height:80px}
.spiral-sm{width:32px;height:32px}.spiral-sm .glow-bg{width:32px;height:32px}.spiral-sm svg{width:32px;height:32px}
@keyframes sp-pulse{0%,100%{transform:scale(1);opacity:.6}50%{transform:scale(1.15);opacity:1}}
.ring-1{animation:sp-cw 8s linear infinite;transform-origin:100px 100px}
.ring-2{animation:sp-ccw 12s linear infinite;transform-origin:100px 100px}
.ring-3{animation:sp-cw 6s linear infinite;transform-origin:100px 100px}
.ring-4{animation:sp-ccw 16s linear infinite;transform-origin:100px 100px}
.ring-5{animation:sp-cw 10s linear infinite;transform-origin:100px 100px}
@keyframes sp-cw{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}
@keyframes sp-ccw{from{transform:rotate(0deg)}to{transform:rotate(-360deg)}}
.spiral-path{animation:sp-cw 5s linear infinite;transform-origin:100px 100px}
.core-anim{animation:sp-core 2s ease-in-out infinite;transform-origin:100px 100px}
@keyframes sp-core{0%,100%{transform:scale(1);opacity:1}50%{transform:scale(1.3);opacity:.7}}
.orbit-dot{animation:sp-cw 4s linear infinite;transform-origin:100px 100px}
.orbit-dot-2{animation:sp-ccw 6s linear infinite;transform-origin:100px 100px}
.orbit-dot-3{animation:sp-cw 9s linear infinite;transform-origin:100px 100px}
</style>
</head>
<body>
<div class="scanner-line"></div>

<!-- Login -->
<div class="login-overlay" id="loginOverlay">
<div class="login-box">
  <div class="logo-wrap" style="margin-bottom:20px"><div class="spiral-logo spiral-md" id="loginLogo"></div></div>
  <h1>TokioAI WAF</h1>
  <div class="sub">Supreme Security Operations Center</div>
  <input type="text" id="loginUser" placeholder="Usuario" autocomplete="username">
  <input type="password" id="loginPass" placeholder="Contrasena" autocomplete="current-password">
  <button onclick="doLogin()"><i class="fas fa-shield-halved"></i> Iniciar sesion</button>
  <div class="login-error" id="loginError">Credenciales invalidas</div>
</div>
</div>

<!-- App -->
<div class="app" id="app">
<div class="topbar">
  <div class="left">
    <div class="spiral-logo spiral-sm" id="topLogo"></div>
    <h1>TokioAI WAF</h1>
    <span class="ver">v3-supreme</span>
  </div>
  <div class="right">
    <span class="status" id="liveStatus"><i class="fas fa-circle" style="font-size:8px"></i> LIVE</span>
    <button class="btn-sm" onclick="refreshAll()" title="Refresh"><i class="fas fa-sync-alt"></i></button>
    <button class="btn-sm" onclick="exportCSV()" title="Export CSV"><i class="fas fa-download"></i> CSV</button>
    <button class="btn-sm btn-danger" onclick="logout()"><i class="fas fa-sign-out-alt"></i></button>
  </div>
</div>

<!-- Filters -->
<div class="filters">
  <div class="filter-group"><label>Desde</label><input type="datetime-local" id="fDateFrom"></div>
  <div class="filter-group"><label>Hasta</label><input type="datetime-local" id="fDateTo"></div>
  <div class="filter-group"><label>IP</label><input type="text" id="fIp" placeholder="ej: 87.120.x.x"></div>
  <div class="filter-group"><label>Buscar</label><input type="text" id="fSearch" placeholder="URI/pattern"></div>
  <div class="filter-group"><label>Severidad</label>
    <select id="fSeverity"><option value="">Todas</option><option value="critical">Critical</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></select></div>
  <div class="filter-group"><label>OWASP</label>
    <select id="fOwasp"><option value="">Todos</option><option value="A01">A01</option><option value="A03">A03</option><option value="A05">A05</option><option value="A07">A07</option><option value="A10">A10</option></select></div>
  <div class="filter-group"><label>Amenaza</label>
    <select id="fThreat"><option value="">Todos</option><option value="SQLI">SQLI</option><option value="XSS">XSS</option><option value="CMD_INJECTION">CMD Inj</option><option value="PATH_TRAVERSAL">Path Trav</option><option value="SCAN_PROBE">Scanner</option><option value="BRUTE_FORCE">Brute Force</option><option value="HONEYPOT">Honeypot</option><option value="LOG4SHELL">Log4Shell</option><option value="SSRF">SSRF</option></select></div>
  <div class="filter-group"><label>Limite</label>
    <select id="fLimit"><option value="100">100</option><option value="500">500</option><option value="1000" selected>1K</option><option value="5000">5K</option></select></div>
  <button class="btn-sm btn-primary" onclick="applyFilters()" style="margin-top:auto"><i class="fas fa-search"></i> Filtrar</button>
  <button class="btn-sm" onclick="resetFilters()" style="margin-top:auto"><i class="fas fa-times"></i></button>
  <div style="display:flex;gap:3px;margin-top:auto">
    <button class="btn-sm" onclick="setHourFilter(1)">1h</button>
    <button class="btn-sm" onclick="setHourFilter(6)">6h</button>
    <button class="btn-sm" onclick="setHourFilter(24)">24h</button>
    <button class="btn-sm" onclick="setHourFilter(168)">7d</button>
  </div>
  <div style="display:flex;gap:3px;margin-top:auto">
    <button class="btn-sm" onclick="prevDay()"><i class="fas fa-chevron-left"></i></button>
    <button class="btn-sm" onclick="todayFilter()"><i class="fas fa-calendar-day"></i></button>
    <button class="btn-sm" onclick="nextDay()"><i class="fas fa-chevron-right"></i></button>
  </div>
</div>

<div class="container">
  <!-- Stats -->
  <div class="stats" id="statsRow"></div>

  <!-- Row: Timeline + OWASP + Live Feed -->
  <div class="row-live">
    <div>
      <div class="row-2" style="margin-bottom:12px">
        <div class="chart-box"><canvas id="timelineChart"></canvas></div>
        <div class="owasp-box"><h4 style="color:var(--primary);margin-bottom:10px;font-size:12px;font-weight:900"><i class="fas fa-shield-halved"></i> OWASP Top 10</h4><div id="owaspList"></div></div>
      </div>
      <!-- World Map + Heatmap -->
      <div class="row-2">
        <div class="world-map">
          <h4 style="color:var(--primary);margin-bottom:8px;font-size:12px;font-weight:900"><i class="fas fa-globe"></i> Attack Origins</h4>
          <div class="map-container" id="worldMap"><div class="map-bg"></div><div class="map-dest" style="left:74%;top:35%" title="GCP (US)"></div></div>
        </div>
        <div class="panel">
          <div class="panel-header"><h3><i class="fas fa-fire"></i> Attack Heatmap</h3><span style="font-size:10px;color:var(--text2);margin-left:8px">Amenazas por hora del dia vs dia de la semana</span></div>
          <div id="heatmapContainer" style="padding:8px"></div>
        </div>
      </div>
    </div>
    <!-- Live Feed Sidebar -->
    <div class="live-feed">
      <div class="feed-header"><div class="dot"></div><h4>LIVE ATTACKS</h4><span style="margin-left:auto;font-size:10px;color:var(--text2)" id="feedCount">0</span></div>
      <div class="feed-list" id="liveFeed"></div>
    </div>
  </div>

  <!-- Tabs -->
  <div class="tabs">
    <div class="tab active" data-tab="attacks" onclick="switchTab('attacks')"><i class="fas fa-signal"></i> Trafico</div>
    <div class="tab" data-tab="episodes" onclick="switchTab('episodes')"><i class="fas fa-layer-group"></i> Episodios</div>
    <div class="tab" data-tab="blocked" onclick="switchTab('blocked')"><i class="fas fa-ban"></i> Bloqueados</div>
    <div class="tab" data-tab="topips" onclick="switchTab('topips')"><i class="fas fa-ranking-star"></i> Top IPs</div>
    <div class="tab" data-tab="signatures" onclick="switchTab('signatures')"><i class="fas fa-fingerprint"></i> Signatures</div>
    <div class="tab" data-tab="chains" onclick="switchTab('chains')"><i class="fas fa-link"></i> Kill Chain</div>
    <div class="tab" data-tab="audit" onclick="switchTab('audit')"><i class="fas fa-clipboard-list"></i> Auditoria</div>
  </div>

  <!-- Tab Panels -->
  <div id="panel-attacks" class="panel"><div class="panel-header"><h3><i class="fas fa-signal"></i> Trafico Reciente</h3><button class="btn-sm btn-danger" onclick="showBlockModal()"><i class="fas fa-ban"></i> Bloquear IP</button></div><div class="table-wrap"><table><thead><tr><th>Hora</th><th>IP</th><th>Metodo</th><th>URI</th><th>Status</th><th>Sev</th><th>Amenaza</th><th>OWASP</th><th>Firma</th><th>Host</th></tr></thead><tbody id="attacksBody"></tbody></table></div></div>

  <div id="panel-episodes" class="panel" style="display:none"><div class="panel-header"><h3><i class="fas fa-layer-group"></i> Episodios</h3></div><div class="table-wrap"><table><thead><tr><th>ID</th><th>Tipo</th><th>OWASP</th><th>IP</th><th>Reqs</th><th>Risk</th><th>Inicio</th><th>Sev</th><th>Estado</th><th>Descripcion</th></tr></thead><tbody id="episodesBody"></tbody></table></div></div>

  <div id="panel-blocked" class="panel" style="display:none"><div class="panel-header"><h3><i class="fas fa-ban"></i> IPs Bloqueadas</h3><button class="btn-sm" onclick="showBlockModal()"><i class="fas fa-plus"></i></button></div><div class="table-wrap"><table><thead><tr><th>IP</th><th>Razon</th><th>Tipo</th><th>Amenaza</th><th>Risk</th><th>Desde</th><th>Expira</th><th>Auto</th><th>Accion</th></tr></thead><tbody id="blockedBody"></tbody></table></div></div>

  <div id="panel-topips" class="panel" style="display:none"><div class="panel-header"><h3><i class="fas fa-ranking-star"></i> Top IPs</h3></div><div class="table-wrap"><table><thead><tr><th>IP</th><th>Hits</th><th>Amenazas</th><th>URIs</th><th>Tipos</th><th>Max Sev</th><th>Intel</th><th>Accion</th></tr></thead><tbody id="topipsBody"></tbody></table></div></div>

  <div id="panel-signatures" class="panel" style="display:none"><div class="panel-header"><h3><i class="fas fa-fingerprint"></i> WAF Signatures</h3></div><div id="sigList" style="max-height:500px;overflow-y:auto"></div></div>

  <div id="panel-chains" class="panel" style="display:none"><div class="panel-header"><h3><i class="fas fa-link"></i> Attack Kill Chains</h3></div><div class="table-wrap"><table><thead><tr><th>Hora</th><th>IP</th><th>Metodo</th><th>URI</th><th>Status</th><th>Sev</th><th>Tipo</th><th>Firma</th></tr></thead><tbody id="chainsBody"></tbody></table></div></div>

  <div id="panel-audit" class="panel" style="display:none"><div class="panel-header"><h3><i class="fas fa-clipboard-list"></i> Auditoria</h3></div><div class="table-wrap"><table><thead><tr><th>Fecha</th><th>IP</th><th>Accion</th><th>Razon</th><th>Por</th></tr></thead><tbody id="auditBody"></tbody></table></div></div>
</div>
</div>

<!-- Block Modal -->
<div class="modal-overlay" id="blockModal" onclick="if(event.target===this)this.style.display='none'">
<div class="modal">
  <h3><i class="fas fa-ban"></i> Bloquear IP</h3>
  <input type="text" id="blockIp" placeholder="IP (ej: 1.2.3.4)">
  <input type="text" id="blockReason" placeholder="Razon" value="Comportamiento sospechoso">
  <select id="blockDuration"><option value="1">1 hora</option><option value="6">6 horas</option><option value="24" selected>24 horas</option><option value="168">7 dias</option><option value="720">30 dias</option></select>
  <div class="actions">
    <button class="btn-sm" onclick="document.getElementById('blockModal').style.display='none'">Cancelar</button>
    <button class="btn-sm btn-danger" onclick="doBlock()"><i class="fas fa-ban"></i> Bloquear</button>
  </div>
</div>
</div>

<!-- Threat Intel Modal -->
<div class="modal-overlay" id="intelModal" onclick="if(event.target===this)this.style.display='none'">
<div class="modal" style="width:520px">
  <h3><i class="fas fa-crosshairs"></i> Threat Intelligence</h3>
  <div id="intelContent" style="font-size:12px;max-height:400px;overflow-y:auto"></div>
  <div class="actions"><button class="btn-sm" onclick="document.getElementById('intelModal').style.display='none'">Cerrar</button></div>
</div>
</div>

<script>
// ─── Animated Spiral Logo ───────────────────────────────────────────────────
function drawSpiralLogo(el){
  el.innerHTML=`<div class="glow-bg"></div>
  <svg viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg" style="overflow:visible">
    <defs>
      <filter id="glow"><feGaussianBlur stdDeviation="2.5" result="c"/><feMerge><feMergeNode in="c"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
      <filter id="glow2"><feGaussianBlur stdDeviation="4" result="c"/><feMerge><feMergeNode in="c"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
      <linearGradient id="sg" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#00ffc8" stop-opacity="0"/><stop offset="50%" stop-color="#00e5ff" stop-opacity="1"/><stop offset="100%" stop-color="#7c4dff" stop-opacity="0.6"/></linearGradient>
    </defs>
    <g class="ring-1"><polygon points="100,18 174,59 174,141 100,182 26,141 26,59" fill="none" stroke="#00ffc8" stroke-width="1" stroke-dasharray="8 4" opacity="0.4" filter="url(#glow)"/></g>
    <g class="ring-2"><circle cx="100" cy="100" r="68" fill="none" stroke="#7c4dff" stroke-width="1" stroke-dasharray="3 9" opacity="0.6" filter="url(#glow)"/></g>
    <g class="ring-3"><rect x="44" y="44" width="112" height="112" rx="4" fill="none" stroke="#00e5ff" stroke-width="0.8" stroke-dasharray="5 6" opacity="0.35" filter="url(#glow)"/></g>
    <g class="ring-4"><circle cx="100" cy="100" r="52" fill="none" stroke="#ff1744" stroke-width="0.8" stroke-dasharray="2 6" opacity="0.4" filter="url(#glow)"/></g>
    <g class="ring-5"><polygon points="100,62 131,78 138,112 117,138 83,138 62,112 69,78" fill="none" stroke="#00ffc8" stroke-width="0.8" stroke-dasharray="4 5" opacity="0.3" filter="url(#glow)"/></g>
    <g class="spiral-path"><path d="M 100 100 Q 100 88, 110 84 Q 126 80, 130 93 Q 136 112, 120 124 Q 100 138, 82 126 Q 62 112, 68 88 Q 76 64, 100 60 Q 130 56, 146 78 Q 158 100, 148 124 Q 136 150, 110 158 Q 80 164, 60 146" fill="none" stroke="url(#sg)" stroke-width="2" stroke-linecap="round" opacity="0.9" filter="url(#glow)"/></g>
    <g class="orbit-dot"><circle cx="100" cy="18" r="3.5" fill="#00ffc8" filter="url(#glow2)"/></g>
    <g class="orbit-dot-2"><circle cx="168" cy="100" r="2.5" fill="#7c4dff" filter="url(#glow2)"/></g>
    <g class="orbit-dot-3"><circle cx="100" cy="48" r="2" fill="#ff1744" filter="url(#glow2)"/></g>
    <g class="core-anim"><polygon points="100,86 112,107 88,107" fill="none" stroke="#00ffc8" stroke-width="1.5" opacity="0.8" filter="url(#glow)"/><circle cx="100" cy="100" r="5" fill="#00ffc8" opacity="0.9" filter="url(#glow2)"/><circle cx="100" cy="100" r="2" fill="#ffffff"/></g>
  </svg>`;
}
document.querySelectorAll('.spiral-logo').forEach(drawSpiralLogo);

// ─── State ──────────────────────────────────────────────────────────────────
let TOKEN=localStorage.getItem('tokio_waf_token');
let chart=null,feedCount=0,sseSource=null;
const API='';

// ─── Auth ───────────────────────────────────────────────────────────────────
function doLogin(){
  const u=document.getElementById('loginUser').value,p=document.getElementById('loginPass').value;
  fetch(API+'/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u,password:p})})
  .then(r=>{if(!r.ok)throw r;return r.json()}).then(d=>{
    TOKEN=d.token;localStorage.setItem('tokio_waf_token',TOKEN);
    document.getElementById('loginOverlay').style.display='none';
    document.getElementById('app').style.display='block';
    initDateFilters();refreshAll();startSSE();
  }).catch(()=>{document.getElementById('loginError').style.display='block'});
}
function logout(){TOKEN=null;localStorage.removeItem('tokio_waf_token');if(sseSource)sseSource.close();location.reload()}
function authFetch(url){return fetch(url,{headers:{'Authorization':'Bearer '+TOKEN}}).then(r=>{if(r.status===401){logout();throw'auth'}return r.json()})}

if(TOKEN){
  document.getElementById('loginOverlay').style.display='none';
  document.getElementById('app').style.display='block';
  initDateFilters();refreshAll();startSSE();
}
document.getElementById('loginPass').addEventListener('keypress',e=>{if(e.key==='Enter')doLogin()});

// ─── Date Filters ───────────────────────────────────────────────────────────
function initDateFilters(){const n=new Date(),f=new Date(n.getTime()-7*24*3600000);document.getElementById('fDateFrom').value=toLocalISO(f);document.getElementById('fDateTo').value=toLocalISO(n)}
function toLocalISO(d){return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16)}
function getDateParams(){let p='';const f=document.getElementById('fDateFrom').value,t=document.getElementById('fDateTo').value;if(f)p+=`&date_from=${new Date(f).toISOString()}`;if(t)p+=`&date_to=${new Date(t).toISOString()}`;return p}
function getFilterParams(){let p=getDateParams();const ip=document.getElementById('fIp').value;if(ip)p+=`&ip=${encodeURIComponent(ip)}`;const s=document.getElementById('fSearch').value;if(s)p+=`&search=${encodeURIComponent(s)}`;const sev=document.getElementById('fSeverity').value;if(sev)p+=`&severity=${sev}`;const ow=document.getElementById('fOwasp').value;if(ow)p+=`&owasp_code=${ow}`;const tt=document.getElementById('fThreat').value;if(tt)p+=`&threat_type=${tt}`;return p}
function applyFilters(){refreshAll()}
function resetFilters(){['fIp','fSearch'].forEach(id=>document.getElementById(id).value='');['fSeverity','fOwasp','fThreat'].forEach(id=>document.getElementById(id).value='');initDateFilters();refreshAll()}
function prevDay(){const el=document.getElementById('fDateFrom');const d=new Date(el.value);d.setDate(d.getDate()-1);const d2=new Date(d);d2.setHours(23,59,59);document.getElementById('fDateFrom').value=toLocalISO(d);document.getElementById('fDateTo').value=toLocalISO(d2);refreshAll()}
function nextDay(){const el=document.getElementById('fDateFrom');const d=new Date(el.value);d.setDate(d.getDate()+1);const d2=new Date(d);d2.setHours(23,59,59);document.getElementById('fDateFrom').value=toLocalISO(d);document.getElementById('fDateTo').value=toLocalISO(d2);refreshAll()}
function todayFilter(){const n=new Date(),f=new Date(n.getTime()-24*3600000);document.getElementById('fDateFrom').value=toLocalISO(f);document.getElementById('fDateTo').value=toLocalISO(n);refreshAll()}
function setHourFilter(h){const n=new Date(),f=new Date(n.getTime()-h*3600000);document.getElementById('fDateFrom').value=toLocalISO(f);document.getElementById('fDateTo').value=toLocalISO(n);refreshAll()}

// ─── SSE Live Feed ──────────────────────────────────────────────────────────
function startSSE(){
  if(sseSource)sseSource.close();
  try{
    sseSource=new EventSource(API+'/api/live_feed?token='+TOKEN);
    sseSource.onmessage=function(e){
      try{
        const d=JSON.parse(e.data);
        const feed=document.getElementById('liveFeed');
        const sevColors={critical:'var(--danger)',high:'var(--warning)',medium:'var(--accent)',low:'var(--success)'};
        const item=document.createElement('div');
        item.className='feed-item';
        item.innerHTML=`
          <div class="sev-dot ${d.severity||'info'}"></div>
          <div>
            <div><span class="feed-ip">${d.ip||'?'}</span> <span class="feed-type" style="color:${sevColors[d.severity]||'var(--text2)'}">${d.threat_type||''}</span></div>
            <div class="feed-uri">${(d.uri||'').substring(0,60)}</div>
            <div class="feed-time">${d.sig_id||''} ${d.country||''} ${fmtTime(d.timestamp)}</div>
          </div>`;
        feed.insertBefore(item,feed.firstChild);
        if(feed.children.length>100)feed.removeChild(feed.lastChild);
        feedCount++;document.getElementById('feedCount').textContent=feedCount;
      }catch(ex){}
    };
    sseSource.onerror=function(){setTimeout(startSSE,5000)};
  }catch(ex){console.log('SSE not available');}
}

// ─── Refresh ────────────────────────────────────────────────────────────────
function refreshAll(){loadStats();loadTimeline();loadOwasp();loadAttacks();loadEpisodes();loadBlocked();loadTopIps();loadSignatures();loadChains();loadAudit();loadGeoMap();loadHeatmap()}

function loadStats(){
  authFetch(API+'/api/summary?'+getDateParams().slice(1)).then(d=>{
    const t=int(d.total),pt=int(d.prev_total),threats=int(d.critical)+int(d.high),pthreats=int(d.prev_threats);
    const trendT=pt>0?((t-pt)/pt*100).toFixed(0):0,trendTh=pthreats>0?((threats-pthreats)/pthreats*100).toFixed(0):0;
    document.getElementById('statsRow').innerHTML=`
      <div class="stat-card c-primary"><div class="label">Requests</div><div class="value">${fmt(t)}</div><div class="trend ${trendT>0?'up':trendT<0?'down':'flat'}">${trendT>0?'&#9650;':'&#9660;'} ${Math.abs(trendT)}% vs prev</div></div>
      <div class="stat-card c-danger"><div class="label">Bloqueados</div><div class="value">${fmt(d.blocked)}</div></div>
      <div class="stat-card c-success"><div class="label">IPs Unicas</div><div class="value">${fmt(d.unique_ips)}</div></div>
      <div class="stat-card c-danger"><div class="label">Critical</div><div class="value">${fmt(d.critical)}</div></div>
      <div class="stat-card c-warning"><div class="label">High</div><div class="value">${fmt(d.high)}</div></div>
      <div class="stat-card c-accent"><div class="label">Medium</div><div class="value">${fmt(d.medium)}</div></div>
      <div class="stat-card c-warning"><div class="label">Episodios</div><div class="value">${fmt(d.active_episodes)}</div></div>
      <div class="stat-card c-danger"><div class="label">IPs Block</div><div class="value">${fmt(d.active_blocks)}</div></div>`;
  }).catch(()=>{});
}

function loadTimeline(){
  authFetch(API+'/api/timeline?'+getDateParams().slice(1)).then(data=>{
    if(!Array.isArray(data)||!data.length)return;
    const gran=data[0].granularity||'hour';
    const labels=data.map(d=>{
      const dt=new Date(d.hour);
      if(gran==='day')return dt.toLocaleDateString('es',{day:'2-digit',month:'short'});
      return dt.toLocaleDateString('es',{day:'2-digit',month:'short'})+' '+dt.toLocaleTimeString('es',{hour:'2-digit',minute:'2-digit'});
    });
    const totals=data.map(d=>parseInt(d.total));const threats=data.map(d=>parseInt(d.threats));const blocked=data.map(d=>parseInt(d.blocked));
    if(chart)chart.destroy();
    chart=new Chart(document.getElementById('timelineChart'),{type:'line',data:{labels,datasets:[
      {label:'Total',data:totals,borderColor:'#00e5ff',backgroundColor:'rgba(0,229,255,.06)',fill:true,tension:.4,borderWidth:2,pointRadius:1},
      {label:'Amenazas',data:threats,borderColor:'#ff9100',backgroundColor:'rgba(255,145,0,.06)',fill:true,tension:.4,borderWidth:2,pointRadius:1},
      {label:'Bloqueados',data:blocked,borderColor:'#ff1744',backgroundColor:'rgba(255,23,68,.06)',fill:true,tension:.4,borderWidth:2,pointRadius:1}
    ]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#7986cb',font:{size:10}}}},
      scales:{x:{ticks:{color:'#3949ab',font:{size:9},maxRotation:45,maxTicksLimit:12},grid:{color:'#1a2540'}},y:{ticks:{color:'#3949ab',font:{size:9}},grid:{color:'#1a2540'}}}}});
  }).catch(()=>{});
}

function loadOwasp(){
  authFetch(API+'/api/owasp_breakdown?'+getDateParams().slice(1)).then(data=>{
    if(!Array.isArray(data)){document.getElementById('owaspList').innerHTML='<div style="color:var(--text2);font-size:11px">Sin datos</div>';return}
    const colors={'A01':'#ff1744','A03':'#ff9100','A05':'#7c4dff','A06':'#00bfa5','A07':'#448aff','A10':'#00e676'};
    document.getElementById('owaspList').innerHTML=data.map(r=>`<div class="owasp-item"><span class="code" style="color:${colors[r.owasp_code]||'var(--primary)'}">${r.owasp_code||'?'}</span><span class="name">${r.owasp_name||'Unknown'}</span><span class="cnt" style="color:${colors[r.owasp_code]||'var(--text)'}">${r.cnt}</span></div>`).join('')||'<div style="color:var(--text2);font-size:11px">Sin datos OWASP</div>';
  }).catch(()=>{});
}

function loadGeoMap(){
  authFetch(API+'/api/geo_attacks?'+getDateParams().slice(1)).then(data=>{
    if(!Array.isArray(data))return;
    const map=document.getElementById('worldMap');
    map.querySelectorAll('.map-dot').forEach(d=>d.remove());
    // Aggregate by country for better visualization
    const byCountry={};
    data.forEach(d=>{
      const cc=d.country_code||'XX';
      if(!byCountry[cc])byCountry[cc]={...d,total_hits:0,total_threats:0,ips:[]};
      byCountry[cc].total_hits+=parseInt(d.hits)||0;
      byCountry[cc].total_threats+=parseInt(d.threats)||0;
      byCountry[cc].ips.push(d.ip);
    });
    const countries=Object.values(byCountry);
    const maxVal=Math.max(...countries.map(d=>d.total_hits||1),1);
    countries.forEach(d=>{
      if(!d.lat&&!d.lng)return;
      const x=((d.lng+180)/360*100).toFixed(1);
      const y=((90-d.lat)/180*100).toFixed(1);
      const t=d.total_threats,h=d.total_hits;
      const sev=t>50?'critical':t>10?'high':t>0?'medium':'low';
      const size=Math.max(6,Math.min(18,(h/maxVal)*18));
      const dot=document.createElement('div');
      dot.className=`map-dot ${sev}`;
      dot.style.cssText=`left:${x}%;top:${y}%;width:${size}px;height:${size}px`;
      dot.title=`${d.country||d.country_code} | ${d.ips.length} IPs | ${fmt(h)} hits | ${fmt(t)} threats`;
      dot.onclick=()=>showIntel(d.ips[0]);
      map.appendChild(dot);
    });
  }).catch(()=>{});
}

function loadHeatmap(){
  authFetch(API+'/api/heatmap?'+getDateParams().slice(1)).then(data=>{
    if(!Array.isArray(data))return;
    const grid={};let maxVal=1;
    data.forEach(r=>{const k=`${r.dow}-${r.hour}`;const v=parseInt(r.threats)||parseInt(r.cnt)||0;grid[k]=v;if(v>maxVal)maxVal=v});
    const days=['Dom','Lun','Mar','Mie','Jue','Vie','Sab'];
    let html='<div class="heatmap-grid"><div class="hm-label"></div>';
    for(let h=0;h<24;h++)html+=`<div class="hm-label">${h}</div>`;
    for(let d=0;d<7;d++){
      html+=`<div class="hm-label">${days[d]}</div>`;
      for(let h=0;h<24;h++){
        const v=grid[`${d}-${h}`]||0;
        const intensity=v/maxVal;
        const color=intensity>0.7?'rgba(255,23,68,'+(.3+intensity*.7)+')':intensity>0.3?'rgba(255,145,0,'+(.2+intensity*.6)+')':intensity>0?'rgba(0,229,255,'+(.1+intensity*.5)+')':'rgba(26,37,64,.5)';
        html+=`<div class="hm-cell" style="background:${color}" title="${days[d]} ${h}:00 — ${v} threats"></div>`;
      }
    }
    html+='</div>';
    document.getElementById('heatmapContainer').innerHTML=html;
  }).catch(()=>{});
}

function loadAttacks(){
  const limit=parseInt(document.getElementById('fLimit').value)||1000;
  document.getElementById('attacksBody').innerHTML='<tr><td colspan="10" style="text-align:center;color:var(--text2);padding:20px"><i class="fas fa-spinner fa-spin"></i></td></tr>';
  authFetch(API+`/api/attacks/recent?limit=${limit}`+getFilterParams()).then(data=>{
    if(!Array.isArray(data)){document.getElementById('attacksBody').innerHTML='<tr><td colspan="10" style="text-align:center;color:var(--text2)">Sin datos</td></tr>';return}
    document.getElementById('attacksBody').innerHTML=data.map(r=>`
      <tr><td class="mono">${fmtTime(r.timestamp)}</td><td><strong style="cursor:pointer;color:var(--primary)" onclick="showIntel('${r.ip}')">${r.ip||'-'}</strong></td><td>${r.method||'-'}</td>
      <td title="${esc(r.uri||'')}" class="mono">${(r.uri||'-').substring(0,50)}</td><td>${r.status||'-'}</td>
      <td><span class="badge ${r.severity==='info'||!r.severity?'normal':r.severity}">${r.severity==='info'||!r.severity?'normal':r.severity}</span></td>
      <td>${r.threat_type&&r.threat_type!='None'?'<span class="badge medium">'+r.threat_type+'</span>':'-'}</td>
      <td>${r.owasp_code&&r.owasp_code!='None'?'<span class="badge owasp">'+r.owasp_code+'</span>':'-'}</td>
      <td class="mono">${r.sig_id&&r.sig_id!='None'?r.sig_id:'-'}</td>
      <td>${r.host||'-'}</td></tr>`).join('');
  }).catch(()=>{});
}

function loadEpisodes(){
  authFetch(API+'/api/episodes?limit=50'+getDateParams()).then(data=>{
    if(!Array.isArray(data))return;
    document.getElementById('episodesBody').innerHTML=data.map(r=>{
      const risk=parseFloat(r.risk_score)||0;const rc=risk>=.75?'#ff1744':risk>=.5?'#ff9100':'#00e676';
      return `<tr><td class="mono">${(r.episode_id||'').substring(0,12)}</td><td>${r.attack_type||'-'}</td>
      <td>${r.owasp_code&&r.owasp_code!='None'?'<span class="badge owasp">'+r.owasp_code+'</span>':'-'}</td>
      <td><strong style="cursor:pointer;color:var(--primary)" onclick="showIntel('${r.src_ip}')">${r.src_ip||'-'}</strong></td><td>${r.total_requests||0}</td>
      <td><div class="risk-bar"><div class="fill" style="width:${risk*100}%;background:${rc}"></div></div> ${(risk*100).toFixed(0)}%</td>
      <td class="mono">${fmtTime(r.start_time)}</td><td><span class="badge ${r.severity}">${r.severity}</span></td>
      <td><span class="badge ${r.status}">${r.status}</span></td>
      <td title="${esc(r.description||'')}">${(r.description||'-').substring(0,60)}</td></tr>`;
    }).join('');
  }).catch(()=>{});
}

function loadBlocked(){
  authFetch(API+'/api/blocked').then(data=>{
    if(!Array.isArray(data))return;
    document.getElementById('blockedBody').innerHTML=data.map(r=>{
      const risk=parseFloat(r.risk_score)||0;const rc=risk>=.75?'#ff1744':risk>=.5?'#ff9100':'#00e676';
      return `<tr><td><strong style="cursor:pointer;color:var(--primary)" onclick="showIntel('${r.ip}')">${r.ip}</strong></td>
      <td title="${esc(r.reason||'')}">${(r.reason||'-').substring(0,40)}</td>
      <td><span class="badge ${r.block_type==='instant'?'critical':r.block_type==='signature'?'critical':r.block_type==='correlation'?'high':r.block_type==='episode'?'medium':'info'}">${r.block_type||'manual'}</span></td>
      <td>${r.threat_type||'-'}</td>
      <td><div class="risk-bar"><div class="fill" style="width:${risk*100}%;background:${rc}"></div></div></td>
      <td class="mono">${fmtTime(r.blocked_at)}</td><td class="mono">${fmtTime(r.expires_at)}</td>
      <td>${r.auto_blocked==='True'?'<i class="fas fa-robot" style="color:var(--primary)"></i>':'<i class="fas fa-user" style="color:var(--text2)"></i>'}</td>
      <td><button class="btn-sm btn-danger" onclick="doUnblock('${r.ip}')"><i class="fas fa-unlock"></i></button></td></tr>`;
    }).join('');
  }).catch(()=>{});
}

function loadTopIps(){
  authFetch(API+'/api/top_ips?'+getDateParams().slice(1)).then(data=>{
    if(!Array.isArray(data))return;
    document.getElementById('topipsBody').innerHTML=data.map(r=>`
      <tr><td><strong style="cursor:pointer;color:var(--primary)" onclick="showIntel('${r.ip}')">${r.ip}</strong></td>
      <td>${r.hits}</td><td style="color:${parseInt(r.threats)>0?'var(--danger)':'var(--text2)'}">${r.threats}</td>
      <td>${r.unique_uris}</td><td>${r.attack_types||'-'}</td>
      <td><span class="badge ${r.max_severity||'info'}">${r.max_severity||'-'}</span></td>
      <td><button class="btn-sm" onclick="showIntel('${r.ip}')"><i class="fas fa-crosshairs"></i></button></td>
      <td><button class="btn-sm btn-danger" onclick="blockIpQuick('${r.ip}')"><i class="fas fa-ban"></i></button></td></tr>`).join('');
  }).catch(()=>{});
}

function loadSignatures(){
  authFetch(API+'/api/signatures?'+getDateParams().slice(1)).then(data=>{
    if(!Array.isArray(data))return;
    const maxHits=Math.max(...data.map(d=>parseInt(d.hits)||1),1);
    const sevColors={critical:'var(--danger)',high:'var(--warning)',medium:'var(--accent)',low:'var(--success)'};
    document.getElementById('sigList').innerHTML=data.map(r=>{
      const hits=parseInt(r.hits)||0;const pct=(hits/maxHits*100).toFixed(0);
      const color=sevColors[r.max_severity]||'var(--text2)';
      return `<div class="sig-item">
        <span class="sig-id">${r.sig_id||'?'}</span>
        <span class="sig-type"><span class="badge ${r.max_severity||'info'}">${r.threat_type||'?'}</span></span>
        <div class="sig-bar"><div class="fill" style="width:${pct}%;background:${color}"></div></div>
        <span class="sig-hits" style="color:${color}">${hits}</span>
        <span class="mono" style="color:var(--text2);font-size:9px">${fmtTime(r.last_hit)}</span>
      </div>`;
    }).join('')||'<div style="padding:20px;text-align:center;color:var(--text2)">Sin datos</div>';
  }).catch(()=>{});
}

function loadChains(){
  authFetch(API+'/api/attack_chains?'+getDateParams().slice(1)).then(data=>{
    if(!Array.isArray(data))return;
    let lastIp='';
    document.getElementById('chainsBody').innerHTML=data.map(r=>{
      const newIp=r.ip!==lastIp;lastIp=r.ip;
      return `<tr style="${newIp?'border-top:2px solid var(--primary)':''}">
        <td class="mono">${fmtTime(r.timestamp)}</td>
        <td><strong style="color:var(--primary)">${r.ip||'-'}</strong></td>
        <td>${r.method||'-'}</td><td class="mono" title="${esc(r.uri||'')}">${(r.uri||'-').substring(0,50)}</td>
        <td>${r.status||'-'}</td><td><span class="badge ${r.severity}">${r.severity}</span></td>
        <td><span class="badge medium">${r.threat_type||'-'}</span></td>
        <td class="mono">${r.sig_id||'-'}</td></tr>`;
    }).join('');
  }).catch(()=>{});
}

function loadAudit(){
  authFetch(API+'/api/audit?limit=50').then(data=>{
    if(!Array.isArray(data))return;
    document.getElementById('auditBody').innerHTML=data.map(r=>`
      <tr><td class="mono">${fmtTime(r.created_at)}</td><td><strong>${r.ip||'-'}</strong></td>
      <td><span class="badge ${r.action==='block'?'blocked':'active'}">${r.action}</span></td>
      <td>${r.reason||'-'}</td><td>${r.performed_by||'-'}</td></tr>`).join('');
  }).catch(()=>{});
}

// ─── Threat Intel ───────────────────────────────────────────────────────────
function showIntel(ip){
  document.getElementById('intelContent').innerHTML='<i class="fas fa-spinner fa-spin"></i> Loading...';
  document.getElementById('intelModal').style.display='flex';
  authFetch(API+'/api/threat_intel/'+ip).then(d=>{
    let html=`<div style="margin-bottom:12px"><h4 style="color:var(--primary);margin-bottom:8px">${ip}</h4>`;
    if(d.geo){html+=`<div style="margin-bottom:4px"><i class="fas fa-globe" style="color:var(--primary)"></i> <strong>${d.geo.country||'Unknown'}</strong> (${d.geo.country_code}) — ${d.geo.continent||''}</div>`}
    if(d.local_stats){const s=d.local_stats;html+=`<div style="color:var(--text2);font-size:11px">Total: ${s.total||0} reqs | Threats: ${s.threats||0} | Types: ${s.attack_types||0} | First: ${fmtTime(s.first_seen)} | Last: ${fmtTime(s.last_seen)}</div>`}
    html+='</div>';
    if(d.local&&d.local.reputation_score){
      const score=parseFloat(d.local.reputation_score);const sc=score<.3?'var(--danger)':score<.6?'var(--warning)':'var(--success)';
      html+=`<div style="margin-bottom:12px;padding:10px;background:var(--card2);border-radius:8px"><strong>Reputation Score:</strong> <span style="color:${sc};font-size:18px;font-weight:900">${(score*100).toFixed(0)}%</span><div style="color:var(--text2);font-size:10px">Threats: ${d.local.total_threats||0} / Total: ${d.local.total_requests||0}</div></div>`;
    }
    if(d.abuseipdb&&d.abuseipdb.data){const a=d.abuseipdb.data;const sc=a.abuseConfidenceScore||0;const ac=sc>50?'var(--danger)':sc>20?'var(--warning)':'var(--success)';
      html+=`<div style="padding:10px;background:var(--card2);border-radius:8px;margin-bottom:8px"><strong style="color:var(--warning)"><i class="fas fa-shield-halved"></i> AbuseIPDB</strong><div style="margin-top:6px">Abuse Score: <span style="color:${ac};font-weight:900;font-size:16px">${sc}%</span></div><div style="color:var(--text2);font-size:11px">ISP: ${a.isp||'?'} | Country: ${a.countryCode||'?'} | Reports: ${a.totalReports||0} | Domain: ${a.domain||'?'} | Usage: ${a.usageType||'?'}</div></div>`;
    }
    html+=`<div style="margin-top:12px"><button class="btn-sm btn-danger" onclick="blockIpQuick('${ip}');document.getElementById('intelModal').style.display='none'"><i class="fas fa-ban"></i> Bloquear ${ip}</button></div>`;
    document.getElementById('intelContent').innerHTML=html;
  }).catch(()=>{document.getElementById('intelContent').innerHTML='Error loading intel'});
}

// ─── Export CSV ──────────────────────────────────────────────────────────────
function exportCSV(){
  const dp=getDateParams().slice(1);const sev=document.getElementById('fSeverity').value;
  let url=API+'/api/export/csv?'+dp;if(sev)url+=`&severity=${sev}`;
  fetch(url,{method:'POST',headers:{'Authorization':'Bearer '+TOKEN}}).then(r=>r.blob()).then(b=>{
    const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='waf_logs.csv';a.click()
  }).catch(()=>alert('Export error'));
}

// ─── Helpers ────────────────────────────────────────────────────────────────
function fmtTime(t){if(!t||t==='None')return'-';try{return new Date(t).toLocaleString('es-AR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'})}catch{return t}}
function esc(s){return s?s.replace(/"/g,'&quot;').replace(/</g,'&lt;'):''}
function int(v){return parseInt(v)||0}
function fmt(v){const n=int(v);return n>=1000?n.toLocaleString():n}
function switchTab(name){
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t.dataset.tab===name));
  ['attacks','episodes','blocked','topips','signatures','chains','audit'].forEach(p=>{
    const el=document.getElementById('panel-'+p);if(el)el.style.display=p===name?'':'none';
  });
}
function showBlockModal(){document.getElementById('blockModal').style.display='flex'}
function doBlock(){
  const ip=document.getElementById('blockIp').value.trim();
  const reason=document.getElementById('blockReason').value.trim()||'Manual block';
  const dur=parseInt(document.getElementById('blockDuration').value)||24;
  if(!ip||!/^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(ip)){alert('IP invalida');return}
  fetch(API+'/api/blocked',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+TOKEN},body:JSON.stringify({ip,reason,duration_hours:dur})})
  .then(r=>r.json()).then(d=>{if(d.ok){document.getElementById('blockModal').style.display='none';document.getElementById('blockIp').value='';refreshAll()}else alert('Error: '+(d.error||''))}).catch(e=>alert('Error: '+e));
}
function doUnblock(ip){if(confirm('Desbloquear '+ip+'?'))fetch(API+'/api/blocked/'+ip,{method:'DELETE',headers:{'Authorization':'Bearer '+TOKEN}}).then(r=>r.json()).then(()=>refreshAll()).catch(()=>{})}
function blockIpQuick(ip){document.getElementById('blockIp').value=ip;showBlockModal()}

// Auto-refresh every 30s
setInterval(refreshAll,30000);
</script>
</body>
</html>"""
