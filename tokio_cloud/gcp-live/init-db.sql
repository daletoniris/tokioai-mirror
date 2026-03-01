CREATE TABLE IF NOT EXISTS waf_logs (
    id BIGSERIAL PRIMARY KEY, timestamp TIMESTAMPTZ DEFAULT NOW(),
    ip TEXT, method TEXT, uri TEXT, status INTEGER,
    body_bytes_sent INTEGER, request_time REAL, user_agent TEXT,
    referer TEXT, host TEXT, upstream_status TEXT, modsec_messages TEXT,
    raw_log JSONB, tenant_id TEXT DEFAULT 'default',
    severity TEXT DEFAULT 'info', blocked BOOLEAN DEFAULT FALSE,
    classification_source TEXT DEFAULT 'NONE',
    owasp_code TEXT, owasp_name TEXT, sig_id TEXT, threat_type TEXT,
    action TEXT DEFAULT 'log_only', confidence REAL,
    kafka_offset BIGINT, kafka_partition INTEGER
);
CREATE TABLE IF NOT EXISTS tenants (
    id SERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL, domain TEXT NOT NULL,
    backend_url TEXT, created_at TIMESTAMPTZ DEFAULT NOW(), active BOOLEAN DEFAULT TRUE
);
CREATE TABLE IF NOT EXISTS episodes (
    id BIGSERIAL PRIMARY KEY, episode_id TEXT UNIQUE,
    tenant_id TEXT DEFAULT 'default', start_time TIMESTAMPTZ, end_time TIMESTAMPTZ,
    src_ip TEXT, attack_type TEXT, severity TEXT DEFAULT 'medium',
    total_requests INTEGER DEFAULT 0, blocked_requests INTEGER DEFAULT 0,
    sample_uris TEXT, intelligence_analysis TEXT,
    status TEXT DEFAULT 'active', ml_label TEXT, ml_confidence DOUBLE PRECISION,
    description TEXT, owasp_code TEXT, owasp_name TEXT, risk_score REAL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS blocked_ips (
    id SERIAL PRIMARY KEY, ip TEXT NOT NULL, reason TEXT,
    blocked_at TIMESTAMPTZ DEFAULT NOW(), expires_at TIMESTAMPTZ,
    active BOOLEAN DEFAULT TRUE, tenant_id TEXT DEFAULT 'default',
    blocked_by TEXT DEFAULT 'system', threat_type TEXT,
    severity TEXT DEFAULT 'medium',
    episode_id TEXT, auto_blocked BOOLEAN DEFAULT FALSE,
    block_type TEXT DEFAULT 'manual', risk_score REAL DEFAULT 0,
    UNIQUE(ip, tenant_id)
);
CREATE TABLE IF NOT EXISTS block_audit_log (
    id BIGSERIAL PRIMARY KEY, ip TEXT NOT NULL, action TEXT NOT NULL,
    reason TEXT, performed_by TEXT DEFAULT 'system', tenant_id TEXT DEFAULT 'default',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS ip_reputation (
    ip TEXT PRIMARY KEY,
    reputation_score REAL DEFAULT 0.5,
    total_requests INT DEFAULT 0,
    total_threats INT DEFAULT 0,
    first_seen TIMESTAMPTZ DEFAULT NOW(),
    last_seen TIMESTAMPTZ DEFAULT NOW(),
    country TEXT,
    isp TEXT,
    tags TEXT[] DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_waf_logs_ts ON waf_logs(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_waf_logs_ip ON waf_logs(ip);
CREATE INDEX IF NOT EXISTS idx_waf_logs_sev ON waf_logs(severity);
CREATE INDEX IF NOT EXISTS idx_waf_logs_threat ON waf_logs(threat_type);
CREATE INDEX IF NOT EXISTS idx_waf_logs_owasp ON waf_logs(owasp_code);
CREATE INDEX IF NOT EXISTS idx_episodes_start ON episodes(start_time DESC);
CREATE INDEX IF NOT EXISTS idx_blocked_ip ON blocked_ips(ip);
CREATE INDEX IF NOT EXISTS idx_blocked_active ON blocked_ips(active);
CREATE INDEX IF NOT EXISTS idx_ip_rep_score ON ip_reputation(reputation_score);
