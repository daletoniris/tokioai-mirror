#!/usr/bin/env python3
"""
TokioAI Realtime Processor v5 — Supreme WAF Engine
====================================================
Based on v4 with major enhancements:
- Instant first-hit blocking for critical signatures (no more waiting for 5 hits)
- 8 new WAF signatures (CRLF, Log4Shell, Smuggling, Deserialization, LDAP, WebSocket, API Abuse, Cryptominer)
- Honeypot detection with instant block
- IP reputation scoring (persistent in PostgreSQL)
- Attack correlation engine (multi-phase detection)
- Lowered thresholds: SIG=2, RATE=40, EPISODE_MIN=2
- All scanner/probe/brute-force signatures promoted to block_ip
"""
import json, os, sys, time, re, hashlib, math
from datetime import datetime, timedelta, timezone
from collections import defaultdict, deque
from kafka import KafkaConsumer
import psycopg2
from psycopg2.extras import RealDictCursor

# ─── TokioAI Advanced Modules ────────────────────────────────────────────────
try:
    from zero_day_entropy import analyze_payload as zd_analyze
    HAS_ZERO_DAY = True
    print("[rt] Zero-Day Entropy Detector: LOADED")
except ImportError:
    HAS_ZERO_DAY = False
    print("[rt] Zero-Day Entropy Detector: not available")

try:
    from ddos_shield import detector as ddos_detector
    HAS_DDOS = True
    print("[rt] DDoS Shield: LOADED")
except ImportError:
    HAS_DDOS = False
    print("[rt] DDoS Shield: not available")

# ─── Config ───────────────────────────────────────────────────────────────────
PG = dict(
    host=os.getenv("POSTGRES_HOST", "postgres"),
    port=int(os.getenv("POSTGRES_PORT", "5432")),
    dbname=os.getenv("POSTGRES_DB", "tokio"),
    user=os.getenv("POSTGRES_USER", "tokio"),
    password=os.getenv("POSTGRES_PASSWORD", "changeme"),
)
KAFKA_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
TOPIC = os.getenv("KAFKA_TOPIC_WAF_LOGS", "waf-logs")
GROUP = os.getenv("KAFKA_CONSUMER_GROUP", "gcp-realtime-group")

RATE_BLOCK_THRESHOLD = int(os.getenv("RATE_BLOCK_THRESHOLD", "40"))
RATE_BLOCK_WINDOW = int(os.getenv("RATE_BLOCK_WINDOW_SEC", "300"))
SIG_BLOCK_THRESHOLD = int(os.getenv("SIG_BLOCK_THRESHOLD", "2"))
SIG_BLOCK_WINDOW = int(os.getenv("SIG_BLOCK_WINDOW_SEC", "600"))
EPISODE_BLOCK_THRESHOLD = int(os.getenv("EPISODE_BLOCK_THRESHOLD", "3"))
BLOCK_DURATION_HR = int(os.getenv("BLOCK_DURATION_HR", "24"))
EPISODE_WINDOW = int(os.getenv("EPISODE_WINDOW_SEC", "600"))
EPISODE_MIN_REQUESTS = int(os.getenv("EPISODE_MIN_REQUESTS", "2"))

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
OWNER_CHAT_ID = os.getenv("TELEGRAM_OWNER_CHAT_ID", "")

INTERNAL_IPS = {
    "127.0.0.1", "::1", "172.18.0.1", "172.17.0.1", "10.10.0.1",
    # Add your Tailscale/internal IPs via OWNER_IPS env var
}
# Owner IPs — never auto-block (loaded from env, comma-separated)
_owner_ips = os.getenv("OWNER_IPS", "").split(",")
INTERNAL_IPS |= {ip.strip() for ip in _owner_ips if ip.strip()}

# ─── OWASP Top 10 2021 ───────────────────────────────────────────────────────
OWASP_MAP = {
    "A01": "Broken Access Control",
    "A02": "Cryptographic Failures",
    "A03": "Injection",
    "A05": "Security Misconfiguration",
    "A06": "Vulnerable Components",
    "A07": "Auth Failures",
    "A08": "Data Integrity Failures",
    "A09": "Security Logging Failures",
    "A10": "SSRF",
}

THREAT_TO_OWASP = {
    "PATH_TRAVERSAL": "A01", "LFI_RCE": "A01", "SCAN_PROBE": "A01",
    "FORCED_BROWSING": "A01", "DIR_LISTING": "A01", "HONEYPOT": "A01",
    "SQLI": "A03", "XSS": "A03", "CMD_INJECTION": "A03",
    "RFI_LFI": "A03", "XXE": "A03", "LDAP_INJECTION": "A03",
    "NOSQL_INJECTION": "A03", "SSTI": "A03", "CRLF_INJECTION": "A03",
    "LOG4SHELL": "A03",
    "BRUTE_FORCE": "A07", "CREDENTIAL_STUFFING": "A07",
    "SSRF": "A10",
    "EXPOSED_CONFIG": "A05", "DEBUG_ENDPOINT": "A05",
    "DESERIALIZATION": "A08",
    "HTTP_SMUGGLING": "A06", "API_ABUSE": "A06",
    "WEBSOCKET_HIJACK": "A01",
    "CRYPTOMINER": "A05",
    "ZERO_DAY_OBFUSCATED": "A03",
    "VOLUMETRIC_FLOOD": "A05",
    "DISTRIBUTED_FLOOD": "A05",
    "IP_FLOOD": "A05",
    "IP_BURST": "A05",
    "SLOWLORIS": "A05",
    "APP_LAYER_FLOOD": "A05",
    "RATE_SPIKE": "A05",
}

# ─── WAF Signature Rules (SOC/OWASP Grade) ───────────────────────────────────
WAF_SIGNATURES = [
    # === A03: Injection — SQL Injection ===
    ("WAF-1001", "SQLI", "critical", "block_ip", 0.95, "uri", [
        r"(?i)(union\s+(all\s+)?select|select\s+.*\s+from\s|insert\s+into|drop\s+table|delete\s+from|update\s+.*\s+set)",
        r"(?i)('\s*or\s+'|'\s*=\s*'|1\s*=\s*1|\".*--|;\s*drop\s|;\s*delete\s)",
        r"(?i)(\bexec\b\s*\(|\bxp_cmdshell|\bsp_executesql|\bwaitfor\s+delay)",
        r"(?i)(benchmark\s*\(|sleep\s*\(\d|pg_sleep|information_schema|sys\.objects|load_file)",
        r"(?i)(%27|%22|%3B).*(?:union|select|insert|drop|delete|update)",
    ]),
    # === A03: Injection — XSS ===
    ("WAF-1002", "XSS", "critical", "block_ip", 0.93, "uri", [
        r"(?i)(<script[\s>]|javascript\s*:|vbscript\s*:|expression\s*\()",
        r"(?i)(onerror\s*=|onload\s*=|onmouseover\s*=|onfocus\s*=|onclick\s*=)",
        r"(?i)(eval\s*\(|document\.cookie|document\.write|window\.location|\.innerHTML)",
        r"(?i)(alert\s*\(|confirm\s*\(|prompt\s*\(|String\.fromCharCode|atob\s*\()",
        r"(?i)(%3Cscript|%3Csvg|%3Cimg|%3Ciframe)",
    ]),
    # === A03: Injection — Command Injection ===
    ("WAF-1003", "CMD_INJECTION", "critical", "block_ip", 0.94, "uri", [
        r"(?i)(\|\s*cat\s+/|\|\s*ls\s+-|\|\s*whoami|\|\s*id\b|\|\s*uname)",
        r"(?i)(;\s*cat\s+/|;\s*ls\s+-|;\s*id\b|;\s*whoami|&&\s*cat\s+/)",
        r"(?i)(\bwget\s+https?://|\bcurl\s+https?://|\bnc\s+-[elp]|\bpython\s+-c)",
        r"(?i)(/bin/sh|/bin/bash|cmd\.exe|powershell\s|command\.com)",
    ]),
    # === A01: Broken Access — Path Traversal / LFI ===
    ("WAF-1004", "PATH_TRAVERSAL", "critical", "block_ip", 0.95, "uri", [
        r"(?i)(/etc/passwd|/etc/shadow|/etc/hosts|/proc/self|/proc/version)",
        r"(?i)(\.\.\/|\.\.\\|%2e%2e%2f|%252e%252e|%c0%ae)",
        r"(?i)(\.\./\.\./\.\./|\.\.\\\.\.\\\.\.\%5c)",
        r"(?i)(file:///|php://filter|php://input|data://text|expect://)",
    ]),
    # === A01: Scanner/Probe — PROMOTED to block_ip ===
    ("WAF-1005", "SCAN_PROBE", "high", "block_ip", 0.88, "uri", [
        r"(?i)(wp-login\.php|wp-admin|xmlrpc\.php|wp-config\.php|wp-includes/)",
        r"(?i)(phpmyadmin|adminer|phpinfo\.php|server-status|server-info)",
        r"(?i)(/\.env|/\.git/|/\.htaccess|/\.DS_Store|/\.svn/|/\.hg/)",
        r"(?i)(/config\.php|/configuration\.php|/settings\.php|/database\.yml|/config\.yml)",
        r"(?i)(/web\.config|/applicationhost\.config|/Thumbs\.db)",
        r"(?i)(cgi-bin/|/shell\.php|/c99\.php|/r57\.php|/webshell|/backdoor)",
        r"(?i)(/actuator|/swagger|/api-docs|/graphql|/debug|/trace|/metrics)",
        r"(?i)(/solr/|/jenkins|/manager/html|/jmx-console|/admin-console)",
        r"(?i)(/telescope|/horizon|/nova-api|/_debugbar|/elfinder|/_profiler)",
        r"(?i)(/vendor/|/node_modules/|/composer\.(json|lock)|/package\.json)",
    ]),
    # === A07: Auth Failures — Brute Force — PROMOTED to block_ip ===
    ("WAF-1006", "BRUTE_FORCE", "high", "block_ip", 0.82, "uri", [
        r"(?i)^/+login\b",
        r"(?i)^/+signin\b",
        r"(?i)^/+auth\b",
        r"(?i)^/+admin/?$",
        r"(?i)^/+administrator\b",
        r"(?i)^/+user/login",
        r"(?i)(api/+token|api/+auth|oauth/+token|api/+login|api/+session)",
    ]),
    # === A10: SSRF ===
    ("WAF-1007", "SSRF", "critical", "block_ip", 0.90, "uri", [
        r"(?i)(http://localhost|http://127\.0\.0\.1|http://0\.0\.0\.0)",
        r"(?i)(http://169\.254\.169\.254|http://metadata\.google)",
        r"(?i)(http://\[::1\]|http://0x7f)",
        r"(?i)(gopher://|dict://|ftp://localhost)",
    ]),
    # === A03: Injection — XXE ===
    ("WAF-1008", "XXE", "critical", "block_ip", 0.92, "uri", [
        r"(?i)(<!DOCTYPE|<!ENTITY|SYSTEM\s+[\"']file://)",
        r"(?i)(%26%23|&#x|&#\d)",
    ]),
    # === A05: Security Misconfiguration — PROMOTED to block_ip ===
    ("WAF-1009", "EXPOSED_CONFIG", "high", "block_ip", 0.80, "uri", [
        r"(?i)(/\.env\.production|/\.env\.local|/\.env\.development)",
        r"(?i)(/wp-config\.php\.bak|/config\.php\.bak|/\.htpasswd)",
        r"(?i)(/crossdomain\.xml|/clientaccesspolicy\.xml)",
        r"(?i)(\.sql$|\.bak$|\.backup$|\.old$|\.orig$|\.save$|\.swp$|\.tmp$|\.log$|\.conf$)",
        r"(?i)(\.tar$|\.tar\.gz$|\.zip$|\.rar$|\.7z$|\.dump$|\.gz$)",
    ]),
    # === A03: NoSQL Injection ===
    ("WAF-1010", "NOSQL_INJECTION", "critical", "block_ip", 0.88, "uri", [
        r"(?i)(\$gt|\$lt|\$ne|\$regex|\$where|\$exists)",
        r"(?i)({\s*\"\$|\[\s*\"\$)",
    ]),
    # === A03: SSTI ===
    ("WAF-1011", "SSTI", "critical", "block_ip", 0.90, "uri", [
        r"\{\{.*\}\}",
        r"\$\{.*\}",
        r"(?i)(__class__|__subclasses__|__import__|__builtins__)",
    ]),
    # ──────────────────────────────────────────────────────────────────────────
    # NEW SIGNATURES v5
    # ──────────────────────────────────────────────────────────────────────────
    # === A03: CRLF Injection ===
    ("WAF-1012", "CRLF_INJECTION", "critical", "block_ip", 0.92, "uri", [
        r"(?i)(%0d%0a|%0D%0A|\r\n)",
        r"(?i)(\\r\\n|%5cr%5cn)",
        r"(?i)(Set-Cookie:|Location:|Content-Type:).*(%0d|%0a)",
    ]),
    # === A03: Log4Shell / JNDI ===
    ("WAF-1013", "LOG4SHELL", "critical", "block_ip", 0.97, "uri", [
        r"(?i)(\$\{jndi:|\$\{lower:|\$\{env:|\$\{sys:)",
        r"(?i)(\$\{j\$\{|\$\{jn\$\{|\$\{\$\{lower:j)",
        r"(?i)(%24%7Bjndi|%24%7Blower|%24%7Benv)",
    ]),
    # === A06: HTTP Request Smuggling ===
    ("WAF-1014", "HTTP_SMUGGLING", "critical", "block_ip", 0.91, "uri", [
        r"(?i)(transfer-encoding\s*:\s*chunked.*content-length)",
        r"(?i)(content-length\s*:\s*\d+.*transfer-encoding)",
        r"(?i)(0\r\n\r\n|0\n\n)",
    ]),
    # === A08: Deserialization ===
    ("WAF-1015", "DESERIALIZATION", "critical", "block_ip", 0.90, "uri", [
        r"(?i)(rO0AB|aced0005|ACER0005)",
        r"(?i)(O:\d+:\"[A-Za-z]|a:\d+:\{)",
        r"(?i)(pickle\.loads|yaml\.load|__reduce__|subprocess\.call)",
    ]),
    # === A03: LDAP Injection ===
    ("WAF-1016", "LDAP_INJECTION", "critical", "block_ip", 0.89, "uri", [
        r"(?i)(\)\(cn=|\)\(uid=|\)\(mail=)",
        r"(?i)(\*\)\(|\)\(\||\)\(\&)",
        r"(?i)(objectClass=\*|objectCategory=\*)",
    ]),
    # === A01: WebSocket Hijack ===
    ("WAF-1017", "WEBSOCKET_HIJACK", "high", "block_ip", 0.85, "uri", [
        r"(?i)(upgrade:\s*websocket.*<script|upgrade:\s*websocket.*javascript:)",
        r"(?i)(ws://.*[\'\"]|wss://.*[\'\"])",
    ]),
    # === A06: API Abuse ===
    ("WAF-1018", "API_ABUSE", "high", "block_ip", 0.83, "uri", [
        r"(?i)(__schema|__type|introspectionQuery|IntrospectionQuery)",
        r"(?i)(/api/v\d+/.*\?.*page=\d{4,}|/api/.*\?.*limit=\d{4,})",
        r"(?i)(/graphql.*query\s*\{.*\{.*\{)",
    ]),
    # === A05: Cryptominer / Malware ===
    ("WAF-1019", "CRYPTOMINER", "critical", "block_ip", 0.95, "uri", [
        r"(?i)(coinhive|cryptonight|coin-hive|jsecoin|cryptoloot)",
        r"(?i)(/wp-content/uploads/.*\.php|/wp-content/plugins/.*\.php\?)",
        r"(?i)(c99|r57|b374k|weevely|wso\s*shell|FilesMan)",
    ]),
    # ──────────────────────────────────────────────────────────────────────────
    # HONEYPOT SIGNATURE — instant block, highest confidence
    # ──────────────────────────────────────────────────────────────────────────
    ("WAF-5001", "HONEYPOT", "critical", "block_ip", 0.99, "uri", [
        r"(?i)^/(wp-admin|wp-login\.php|xmlrpc\.php|phpmyadmin)",
        r"(?i)^/(\.env|\.git/config|admin\.php|shell\.php|config\.php\.bak)",
        r"(?i)^/(debug|telescope|_debugbar|elfinder|filemanager)",
        r"(?i)^/(solr|jenkins|manager/html|jmx-console|admin-console)",
        r"(?i)^/(cgi-bin/|webshell|backdoor|c99\.php|r57\.php)",
    ]),
    # ──────────────────────────────────────────────────────────────────────────
    # User-Agent based signatures
    # ──────────────────────────────────────────────────────────────────────────
    # === Scanner UAs — PROMOTED to block_ip ===
    ("WAF-2001", "SCAN_PROBE", "high", "block_ip", 0.85, "ua", [
        r"(?i)(nikto|sqlmap|nmap|dirbuster|gobuster|wpscan|masscan|nuclei|zgrab)",
        r"(?i)(hydra|metasploit|burpsuite|owasp\s*zap|acunetix|netsparker|qualys|openvas)",
        r"(?i)(whatweb|wapiti|skipfish|arachni|w3af|vega|zaproxy)",
    ]),
    # === Generic programmatic UAs ===
    ("WAF-2002", "SCAN_PROBE", "medium", "log_only", 0.65, "ua", [
        r"(?i)(python-requests|python-urllib|Go-http-client|okhttp|java/|libwww-perl|curl/|wget/)",
    ]),
    # === Suspicious file extensions ===
    ("WAF-3001", "SCAN_PROBE", "medium", "log_only", 0.75, "uri", [
        r"(?i)\.(php|asp|aspx|jsp|cgi|pl|py|rb|sh|bat|cmd|exe)(\?|$)",
    ]),
    # === Known bot patterns ===
    ("WAF-4001", "BOT", "low", "log_only", 0.60, "ua", [
        r"(?i)(bot|crawl|spider|slurp|baiduspider|yandex|duckduck|bingbot)",
        r"(?i)(semrush|ahrefs|mj12bot|dotbot|petalbot|bytespider|gptbot|claudebot)",
        r"(?i)(googlebot|facebookexternalhit|twitterbot|linkedinbot|whatsapp)",
    ]),
    # === Log4Shell in UA ===
    ("WAF-2003", "LOG4SHELL", "critical", "block_ip", 0.97, "ua", [
        r"(?i)(\$\{jndi:|\$\{lower:|\$\{env:|\$\{sys:)",
    ]),
]

SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def _sev_rank(s):
    return SEVERITY_ORDER.get(s, 0)


# ─── WAF Classification Engine ───────────────────────────────────────────────
def classify_request(method, uri, status, user_agent, ip, host, request_time=0, size=0):
    matches = []
    text_uri = uri or ""
    text_ua = user_agent or ""
    status = int(status or 0)

    SAFE_PATHS = {"/", "/robots.txt", "/sitemap.xml", "/favicon.ico", "/favicon.png",
                  "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png",
                  "/.well-known/acme-challenge"}
    SAFE_EXTENSIONS = {".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
                       ".woff", ".woff2", ".ttf", ".eot", ".map", ".webp", ".avif"}
    uri_path = text_uri.split("?")[0]
    is_safe_path = (uri_path in SAFE_PATHS
                    or text_uri.startswith("/.well-known/")
                    or any(uri_path.endswith(ext) for ext in SAFE_EXTENSIONS))

    # --- Layer 1: WAF Signature Matching ---
    for sig_id, threat_type, severity, action, confidence, target_field, patterns in WAF_SIGNATURES:
        target = text_ua if target_field == "ua" else text_uri
        if is_safe_path and target_field != "ua":
            continue
        for pat in patterns:
            if re.search(pat, target):
                matches.append({
                    "sig_id": sig_id,
                    "threat_type": threat_type,
                    "severity": severity,
                    "action": action,
                    "confidence": confidence,
                })
                break

    # --- Layer 2: Behavioral Analysis (skip for safe paths) ---
    if not is_safe_path:
        if host and re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", host):
            matches.append({"sig_id": "BEH-001", "threat_type": "RECON_IP", "severity": "low", "action": "log_only", "confidence": 0.50})

        if method == "POST" and text_uri in ("/", "") and status in (301, 302, 400, 403, 404, 405):
            matches.append({"sig_id": "BEH-002", "threat_type": "EXPLOIT_ATTEMPT", "severity": "high", "action": "monitor", "confidence": 0.78})

        if not method or not text_uri:
            matches.append({"sig_id": "BEH-003", "threat_type": "MALFORMED", "severity": "medium", "action": "monitor", "confidence": 0.72})

        # 403 = defense working, only flag if a signature also matched (handled by priority sort)
        if status == 403 and matches:
            matches.append({"sig_id": "BEH-004", "threat_type": "WAF_BLOCKED", "severity": "low", "action": "log_only", "confidence": 0.50})

        if status == 401 and re.search(r"(?i)(login|auth|admin|api/token)", text_uri):
            matches.append({"sig_id": "BEH-005", "threat_type": "BRUTE_FORCE", "severity": "high", "action": "monitor", "confidence": 0.80})

        if request_time and float(request_time) > 10:
            matches.append({"sig_id": "BEH-006", "threat_type": "SLOW_ATTACK", "severity": "medium", "action": "monitor", "confidence": 0.65})

        if size and int(size) > 1_000_000:
            matches.append({"sig_id": "BEH-007", "threat_type": "DATA_EXFIL", "severity": "high", "action": "monitor", "confidence": 0.70})

    # --- Layer 2.5: Zero-Day Entropy Analysis ---
    if HAS_ZERO_DAY and not is_safe_path:
        zd_result = zd_analyze(text_uri, text_ua)
        if zd_result:
            # Only add if no existing signature already caught it with higher confidence
            existing_max_conf = max((m["confidence"] for m in matches), default=0)
            if zd_result["confidence"] > existing_max_conf * 0.8 or not matches:
                matches.append({
                    "sig_id": zd_result["sig_id"],
                    "threat_type": zd_result["threat_type"],
                    "severity": zd_result["severity"],
                    "action": zd_result["action"],
                    "confidence": zd_result["confidence"],
                    "entropy": zd_result.get("entropy"),
                    "encoding_layers": zd_result.get("encoding_layers"),
                    "analysis_summary": zd_result.get("analysis_summary"),
                })

    # --- Layer 3: Determine result ---
    if not matches:
        return "info", "NONE", None, None, "log_only", None, None, []

    matches.sort(key=lambda m: (_sev_rank(m["severity"]), m["confidence"]), reverse=True)
    primary = matches[0]

    owasp_code = THREAT_TO_OWASP.get(primary["threat_type"])
    owasp_name = OWASP_MAP.get(owasp_code) if owasp_code else None

    conf = primary["confidence"]
    if len(matches) > 1:
        conf = min(0.99, conf + 0.03 * (len(matches) - 1))

    actions_rank = {"log_only": 0, "monitor": 1, "block_ip": 2}
    best_action = max(matches, key=lambda m: actions_rank.get(m["action"], 0))["action"]

    return (
        primary["severity"],
        primary["threat_type"],
        owasp_code,
        owasp_name,
        best_action,
        conf,
        primary["sig_id"],
        matches,
    )


# ─── IP Tracker (Multi-factor) ───────────────────────────────────────────────
class IPTracker:
    def __init__(self):
        self.windows = defaultdict(list)
        self.sig_hits = defaultdict(list)
        self.threats = defaultdict(list)
        self.request_count = defaultdict(int)
        self.unique_uris = defaultdict(set)
        self.methods = defaultdict(lambda: defaultdict(int))
        self.status_codes = defaultdict(lambda: defaultdict(int))
        # Attack correlation: phases detected per IP
        self.phases = defaultdict(set)  # ip -> set of phases

    def add(self, ip, ts, threat_type=None, uri=None, severity="info", sig_id=None, action=None, status=0):
        now = time.time()
        self.windows[ip].append(now)
        self.request_count[ip] += 1
        if uri:
            self.unique_uris[ip].add(uri)
        self.status_codes[ip][status] += 1
        cutoff = now - max(RATE_BLOCK_WINDOW, EPISODE_WINDOW, SIG_BLOCK_WINDOW)
        self.windows[ip] = [t for t in self.windows[ip] if t > cutoff]

        if sig_id and action == "block_ip":
            self.sig_hits[ip].append((now, sig_id, threat_type, severity, action))
            self.sig_hits[ip] = [x for x in self.sig_hits[ip] if x[0] > now - SIG_BLOCK_WINDOW]

        if threat_type and threat_type != "NONE" and severity != "info":
            self.threats[ip].append((now, threat_type, uri, severity))
            self.threats[ip] = [x for x in self.threats[ip] if x[0] > cutoff]

        # Track attack phases for correlation
        self._track_phase(ip, threat_type, severity, status)

    def _track_phase(self, ip, threat_type, severity, status):
        """Detect multi-phase attack progression."""
        if not threat_type or threat_type == "NONE":
            return
        # Phase 1: Recon
        if threat_type in ("SCAN_PROBE", "RECON_IP", "BOT") or (self.status_codes[ip].get(404, 0) >= 5):
            self.phases[ip].add("recon")
        # Phase 2: Probe
        if threat_type in ("PATH_TRAVERSAL", "EXPOSED_CONFIG", "HONEYPOT", "BRUTE_FORCE"):
            self.phases[ip].add("probe")
        # Phase 3: Exploit
        if threat_type in ("SQLI", "XSS", "CMD_INJECTION", "XXE", "SSTI", "LOG4SHELL",
                           "DESERIALIZATION", "LDAP_INJECTION", "NOSQL_INJECTION", "SSRF",
                           "CRLF_INJECTION", "HTTP_SMUGGLING"):
            self.phases[ip].add("exploit")
        # Phase 4: Exfil
        if threat_type == "DATA_EXFIL":
            self.phases[ip].add("exfil")

    def get_attack_phases(self, ip):
        """Return detected phases for correlation."""
        return self.phases.get(ip, set())

    def is_multi_phase_attack(self, ip):
        """Detect if IP is conducting multi-phase attack (2+ phases)."""
        phases = self.phases.get(ip, set())
        return len(phases) >= 2

    def should_rate_block(self, ip):
        recent = [t for t in self.windows.get(ip, []) if t > time.time() - RATE_BLOCK_WINDOW]
        return len(recent) >= RATE_BLOCK_THRESHOLD

    def should_sig_block(self, ip):
        recent = [x for x in self.sig_hits.get(ip, []) if x[0] > time.time() - SIG_BLOCK_WINDOW]
        return len(recent) >= SIG_BLOCK_THRESHOLD

    def get_episode_data(self, ip):
        threats = self.threats.get(ip, [])
        if len(threats) < EPISODE_MIN_REQUESTS:
            return None
        types = [t[1] for t in threats]
        uris = list(set(t[2] for t in threats if t[2]))[:10]
        severities = [t[3] for t in threats]
        primary_type = max(set(types), key=types.count)
        worst_severity = max(set(severities), key=_sev_rank)
        owasp_code = THREAT_TO_OWASP.get(primary_type)
        owasp_name = OWASP_MAP.get(owasp_code) if owasp_code else None
        risk_score = self._calc_risk_score(ip, threats, worst_severity)
        return {
            "type": primary_type,
            "count": len(threats),
            "uris": uris,
            "severity": worst_severity,
            "start": threats[0][0],
            "end": threats[-1][0],
            "types_breakdown": {t: types.count(t) for t in set(types)},
            "owasp_code": owasp_code,
            "owasp_name": owasp_name,
            "risk_score": risk_score,
            "unique_uris": len(self.unique_uris.get(ip, set())),
            "phases": list(self.get_attack_phases(ip)),
        }

    def _calc_risk_score(self, ip, threats, worst_sev):
        score = 0.0
        sev_scores = {"info": 0, "low": 0.2, "medium": 0.5, "high": 0.8, "critical": 1.0}
        score += sev_scores.get(worst_sev, 0) * 0.25
        count = len(threats)
        score += min(1.0, count / 20) * 0.20
        unique_types = len(set(t[1] for t in threats))
        score += min(1.0, unique_types / 4) * 0.15
        recent = [t for t in self.windows.get(ip, []) if t > time.time() - 60]
        rps = len(recent) / 60.0
        score += min(1.0, rps / 5) * 0.10
        uri_count = len(self.unique_uris.get(ip, set()))
        score += min(1.0, uri_count / 10) * 0.10
        # Multi-phase bonus (20%)
        phases = len(self.get_attack_phases(ip))
        score += min(1.0, phases / 3) * 0.20
        return round(min(1.0, score), 3)


tracker = IPTracker()


# ─── Telegram ─────────────────────────────────────────────────────────────────
def _notify(msg):
    if not BOT_TOKEN or not OWNER_CHAT_ID:
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": OWNER_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception:
        pass


# ─── Database ─────────────────────────────────────────────────────────────────
def get_pg():
    for i in range(30):
        try:
            c = psycopg2.connect(**PG)
            c.autocommit = True
            return c
        except Exception as e:
            print(f"[rt] DB not ready ({e}), retry...")
            time.sleep(3)
    sys.exit(1)


def ensure_schema(conn):
    cur = conn.cursor()
    for sql in [
        "ALTER TABLE waf_logs ADD COLUMN IF NOT EXISTS owasp_code TEXT",
        "ALTER TABLE waf_logs ADD COLUMN IF NOT EXISTS owasp_name TEXT",
        "ALTER TABLE waf_logs ADD COLUMN IF NOT EXISTS sig_id TEXT",
        "ALTER TABLE waf_logs ADD COLUMN IF NOT EXISTS threat_type TEXT",
        "ALTER TABLE waf_logs ADD COLUMN IF NOT EXISTS action TEXT DEFAULT 'log_only'",
        "ALTER TABLE waf_logs ADD COLUMN IF NOT EXISTS confidence REAL",
        "ALTER TABLE blocked_ips ADD COLUMN IF NOT EXISTS episode_id TEXT",
        "ALTER TABLE blocked_ips ADD COLUMN IF NOT EXISTS auto_blocked BOOLEAN DEFAULT false",
        "ALTER TABLE blocked_ips ADD COLUMN IF NOT EXISTS block_type TEXT DEFAULT 'manual'",
        "ALTER TABLE blocked_ips ADD COLUMN IF NOT EXISTS risk_score REAL",
        "ALTER TABLE episodes ADD COLUMN IF NOT EXISTS ml_label TEXT",
        "ALTER TABLE episodes ADD COLUMN IF NOT EXISTS ml_confidence DOUBLE PRECISION",
        "ALTER TABLE episodes ADD COLUMN IF NOT EXISTS description TEXT",
        "ALTER TABLE episodes ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW()",
        "ALTER TABLE episodes ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'active'",
        "ALTER TABLE episodes ADD COLUMN IF NOT EXISTS owasp_code TEXT",
        "ALTER TABLE episodes ADD COLUMN IF NOT EXISTS owasp_name TEXT",
        "ALTER TABLE episodes ADD COLUMN IF NOT EXISTS risk_score REAL DEFAULT 0",
        """CREATE TABLE IF NOT EXISTS block_audit_log (
            id BIGSERIAL PRIMARY KEY, ip TEXT NOT NULL, action TEXT NOT NULL,
            reason TEXT, performed_by TEXT DEFAULT 'system', created_at TIMESTAMPTZ DEFAULT NOW())""",
        """CREATE TABLE IF NOT EXISTS ip_reputation (
            ip TEXT PRIMARY KEY,
            reputation_score REAL DEFAULT 0.5,
            total_requests INT DEFAULT 0,
            total_threats INT DEFAULT 0,
            first_seen TIMESTAMPTZ DEFAULT NOW(),
            last_seen TIMESTAMPTZ DEFAULT NOW(),
            country TEXT,
            isp TEXT,
            tags TEXT[] DEFAULT '{}')""",
        "CREATE INDEX IF NOT EXISTS idx_waf_logs_ts ON waf_logs(timestamp DESC)",
        "CREATE INDEX IF NOT EXISTS idx_waf_logs_ip ON waf_logs(ip)",
        "CREATE INDEX IF NOT EXISTS idx_waf_logs_sev ON waf_logs(severity)",
        "CREATE INDEX IF NOT EXISTS idx_waf_logs_threat ON waf_logs(threat_type)",
        "CREATE INDEX IF NOT EXISTS idx_waf_logs_owasp ON waf_logs(owasp_code)",
        "CREATE INDEX IF NOT EXISTS idx_ip_rep_score ON ip_reputation(reputation_score)",
    ]:
        try:
            cur.execute(sql)
        except Exception:
            pass
    cur.close()
    print("[rt] Schema verified/updated")


def insert_waf_log(cur, event, severity, threat_type, owasp_code, owasp_name, action, confidence, sig_id):
    try:
        cur.execute("""
            INSERT INTO waf_logs (timestamp, ip, method, uri, status, body_bytes_sent, user_agent, host,
                                  referer, request_time, severity, blocked, raw_log, classification_source,
                                  owasp_code, owasp_name, sig_id, threat_type, action, confidence)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            event.get("timestamp"),
            event.get("ip", "unknown"),
            event.get("method", ""),
            event.get("uri", ""),
            int(event.get("status", 0)),
            int(event.get("size", event.get("body_bytes_sent", 0))),
            event.get("user_agent", ""),
            event.get("host", ""),
            event.get("referer", ""),
            float(event.get("request_time", 0)),
            severity,
            action == "block_ip",
            json.dumps(event, default=str),
            threat_type if threat_type != "NONE" else "NONE",
            owasp_code,
            owasp_name,
            sig_id,
            threat_type if threat_type != "NONE" else None,
            action,
            confidence,
        ))
    except Exception as e:
        print(f"[rt] Insert err: {e}")


def upsert_episode(cur, ip, ep_data, threat_type, confidence):
    key = f"{ip}-{ep_data['type']}-{int(ep_data['start'] // 300)}"
    ep_id = "ep-" + hashlib.md5(key.encode()).hexdigest()[:12]
    uris_str = json.dumps(ep_data["uris"][:10]) if ep_data.get("uris") else "[]"
    owasp_str = f" [{ep_data.get('owasp_code', '')}]" if ep_data.get("owasp_code") else ""
    phases_str = " -> ".join(ep_data.get("phases", []))
    desc = f"{ep_data['type']}{owasp_str} from {ip} — {ep_data['count']} threats, risk: {ep_data.get('risk_score', 0):.0%}"
    if phases_str:
        desc += f" | Kill chain: {phases_str}"
    if ep_data.get("types_breakdown"):
        desc += " [" + ", ".join(f"{k}:{v}" for k, v in ep_data["types_breakdown"].items()) + "]"

    try:
        cur.execute("""
            INSERT INTO episodes (episode_id, attack_type, severity, src_ip, start_time, end_time,
                                  total_requests, sample_uris, status, ml_label, ml_confidence,
                                  description, owasp_code, owasp_name, risk_score)
            VALUES (%s,%s,%s,%s,to_timestamp(%s),to_timestamp(%s),%s,%s,'active',%s,%s,%s,%s,%s,%s)
            ON CONFLICT (episode_id) DO UPDATE SET
                end_time=to_timestamp(%s), total_requests=%s, sample_uris=%s,
                severity=CASE WHEN episodes.severity='critical' THEN 'critical' ELSE %s END,
                ml_label=COALESCE(%s, episodes.ml_label),
                ml_confidence=GREATEST(%s, episodes.ml_confidence),
                description=%s, risk_score=GREATEST(%s, episodes.risk_score),
                owasp_code=COALESCE(%s, episodes.owasp_code),
                updated_at=NOW()
        """, (
            ep_id, ep_data["type"], ep_data["severity"], ip,
            ep_data["start"], ep_data["end"],
            ep_data["count"], uris_str,
            threat_type, confidence, desc,
            ep_data.get("owasp_code"), ep_data.get("owasp_name"), ep_data.get("risk_score", 0),
            ep_data["end"], ep_data["count"], uris_str,
            ep_data["severity"], threat_type, confidence, desc,
            ep_data.get("risk_score", 0), ep_data.get("owasp_code"),
        ))
        return ep_id
    except Exception as e:
        print(f"[rt] Episode err: {e}")
        return None


BLOCKLIST_PATH = os.environ.get("BLOCKLIST_PATH", "/blocklist/blocked.conf")


def sync_blocklist_file(cur):
    """Write the nginx blocklist file with all active blocked IPs."""
    try:
        cur.execute("SELECT ip FROM blocked_ips WHERE active=true")
        ips = [row[0] for row in cur.fetchall()]
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
        print(f"[rt] Blocklist synced: {len(ips)} IPs -> {BLOCKLIST_PATH}", flush=True)
    except Exception as e:
        print(f"[rt] Blocklist sync error: {e}", flush=True)


def auto_block_ip(cur, ip, reason, block_type, episode_id=None, threat_type=None, severity="high", risk_score=0):
    if ip in INTERNAL_IPS:
        return False
    try:
        cur.execute("SELECT id FROM blocked_ips WHERE ip=%s AND active=true", (ip,))
        if cur.fetchone():
            return False
        # Remove old inactive entries to avoid UNIQUE constraint violation
        cur.execute("DELETE FROM blocked_ips WHERE ip=%s AND active=false", (ip,))
        expires = datetime.now(timezone.utc) + timedelta(hours=BLOCK_DURATION_HR)
        cur.execute("""
            INSERT INTO blocked_ips (ip, reason, expires_at, active, threat_type, severity,
                                     episode_id, auto_blocked, block_type, risk_score)
            VALUES (%s,%s,%s,true,%s,%s,%s,true,%s,%s)
        """, (ip, reason, expires, threat_type, severity, episode_id, block_type, risk_score))
        cur.execute("""
            INSERT INTO block_audit_log (ip, action, reason, performed_by)
            VALUES (%s, 'block', %s, %s)
        """, (ip, reason, f"tokioai-auto-{block_type}"))
        owasp = THREAT_TO_OWASP.get(threat_type, "")
        print(f"[rt] BLOCKED {ip} [{block_type}]: {reason}")
        _notify(
            f"*Auto-Block* [{block_type}]\n"
            f"IP: `{ip}`\n"
            f"Tipo: {threat_type} {f'({owasp})' if owasp else ''}\n"
            f"Severidad: {severity}\n"
            f"Risk Score: {risk_score:.0%}\n"
            f"Expira: {expires.strftime('%Y-%m-%d %H:%M UTC')}"
        )
        sync_blocklist_file(cur)
        return True
    except Exception as e:
        print(f"[rt] Block err: {e}")
        return False


def update_ip_reputation(cur, ip, is_threat=False, severity="info"):
    """Update IP reputation score in PostgreSQL."""
    try:
        if is_threat:
            penalty = -0.15 if severity == "critical" else -0.05
            cur.execute("""
                INSERT INTO ip_reputation (ip, reputation_score, total_requests, total_threats, last_seen)
                VALUES (%s, GREATEST(0, 0.5 + %s), 1, 1, NOW())
                ON CONFLICT (ip) DO UPDATE SET
                    reputation_score = GREATEST(0, ip_reputation.reputation_score + %s),
                    total_requests = ip_reputation.total_requests + 1,
                    total_threats = ip_reputation.total_threats + 1,
                    last_seen = NOW()
            """, (ip, penalty, penalty))
        else:
            cur.execute("""
                INSERT INTO ip_reputation (ip, reputation_score, total_requests, last_seen)
                VALUES (%s, 0.501, 1, NOW())
                ON CONFLICT (ip) DO UPDATE SET
                    reputation_score = LEAST(1.0, ip_reputation.reputation_score + 0.001),
                    total_requests = ip_reputation.total_requests + 1,
                    last_seen = NOW()
            """, (ip,))
    except Exception:
        pass


def check_reputation_block(cur, ip):
    """Check if IP's reputation warrants blocking."""
    try:
        cur.execute("SELECT reputation_score, total_threats FROM ip_reputation WHERE ip=%s", (ip,))
        row = cur.fetchone()
        if row and row[0] < 0.2 and row[1] >= 3:
            return True
    except Exception:
        pass
    return False


def maintenance(cur):
    try:
        cur.execute("""
            UPDATE blocked_ips SET active=false
            WHERE active=true AND expires_at IS NOT NULL AND expires_at < NOW()
            RETURNING ip""")
        for r in cur.fetchall():
            cur.execute("INSERT INTO block_audit_log(ip,action,reason,performed_by) VALUES(%s,'unblock','Expired','tokioai-auto')", (r[0],))
    except Exception:
        pass
    try:
        cur.execute("UPDATE episodes SET status='resolved',updated_at=NOW() WHERE status='active' AND end_time < NOW() - INTERVAL '30 minutes'")
    except Exception:
        pass


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    print("=" * 70)
    print("[rt] TokioAI Realtime Processor v5 — Supreme WAF Engine")
    print("=" * 70)
    print(f"[rt] Kafka: {KAFKA_SERVERS} | Topic: {TOPIC} | Group: {GROUP}")
    print(f"[rt] WAF Signatures: {len(WAF_SIGNATURES)} rules loaded")
    print(f"[rt] Blocking: rate={RATE_BLOCK_THRESHOLD}reqs/{RATE_BLOCK_WINDOW}s, "
          f"sig={SIG_BLOCK_THRESHOLD}hits/{SIG_BLOCK_WINDOW}s, "
          f"episode={EPISODE_BLOCK_THRESHOLD}episodes")
    print(f"[rt] Episodes: min {EPISODE_MIN_REQUESTS} threats in {EPISODE_WINDOW}s")
    print(f"[rt] Features: instant-block, honeypots, ip-reputation, attack-correlation")

    conn = get_pg()
    ensure_schema(conn)
    print("[rt] PostgreSQL ready")

    consumer = None
    for i in range(30):
        try:
            consumer = KafkaConsumer(
                TOPIC,
                bootstrap_servers=KAFKA_SERVERS.split(","),
                group_id=GROUP + "-v5",
                auto_offset_reset="earliest",
                value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                consumer_timeout_ms=5000,
            )
            break
        except Exception as e:
            print(f"[rt] Kafka not ready ({e})...")
            time.sleep(5)
    if not consumer:
        print("[rt] FATAL: Kafka failed")
        sys.exit(1)

    print(f"[rt] Consuming '{TOPIC}'")
    stats = {"processed": 0, "threats": 0, "episodes": 0, "blocks": 0, "instant_blocks": 0}
    last_maint = time.time()
    last_report = time.time()

    while True:
        try:
            for tp, msgs in consumer.poll(timeout_ms=2000).items():
                for msg in msgs:
                    e = msg.value
                    ip = e.get("ip", "unknown")
                    uri = e.get("uri", "")

                    # Skip dashboard/API traffic — not attack surface
                    is_dashboard = uri.startswith("/api/") or uri.startswith("/dashboard") or uri == "/health"
                    if is_dashboard:
                        stats["processed"] += 1
                        continue

                    severity, threat_type, owasp_code, owasp_name, action, conf, sig_id, all_matches = \
                        classify_request(
                            e.get("method"), uri, e.get("status"),
                            e.get("user_agent"), ip, e.get("host"),
                            e.get("request_time", 0),
                            e.get("size", e.get("body_bytes_sent", 0)),
                        )

                    is_real_ip = ip not in INTERNAL_IPS and ip not in ("unknown", "-", "")
                    if is_real_ip:
                        tracker.add(ip, time.time(), threat_type, e.get("uri"), severity,
                                    sig_id, action, int(e.get("status", 0)))

                    cur = conn.cursor()
                    insert_waf_log(cur, e, severity, threat_type, owasp_code, owasp_name, action, conf, sig_id)

                    if severity != "info":
                        stats["threats"] += 1

                    # ─── DDoS Shield: record request and check ────────────
                    if HAS_DDOS and is_real_ip:
                        ddos_event = ddos_detector.record_request(
                            ip, e.get("uri", ""), e.get("method", "GET"),
                            float(e.get("request_time", 0)),
                            int(e.get("status", 200)),
                            e.get("user_agent", ""),
                        )
                        if ddos_event:
                            ddos_type = ddos_event["type"]
                            ddos_sev = ddos_event["severity"]
                            print(f"[rt] DDoS: {ddos_type} — {ddos_event['description']}")
                            stats["threats"] += 1
                            # Block the trigger IP for severe floods
                            if ddos_sev == "critical":
                                auto_block_ip(
                                    cur, ip,
                                    f"DDoS: {ddos_type} — {ddos_event['description']}",
                                    "ddos", threat_type=ddos_type,
                                    severity="critical", risk_score=0.95,
                                )
                                stats["blocks"] += 1

                    if is_real_ip:
                        # Update IP reputation
                        is_threat = severity in ("high", "critical") and threat_type != "NONE"
                        update_ip_reputation(cur, ip, is_threat=is_threat, severity=severity)

                        # ─── INSTANT BLOCK: first-hit for critical block_ip signatures ─
                        if (action == "block_ip" and severity == "critical"
                                and conf is not None and conf >= 0.90):
                            if auto_block_ip(
                                cur, ip,
                                f"Instant block: {threat_type} [{sig_id}] conf={conf:.0%}",
                                "instant", threat_type=threat_type, severity="critical",
                                risk_score=0.95,
                            ):
                                stats["blocks"] += 1
                                stats["instant_blocks"] += 1

                        # ─── Multi-phase attack correlation block ───────────────
                        elif tracker.is_multi_phase_attack(ip):
                            phases = tracker.get_attack_phases(ip)
                            if auto_block_ip(
                                cur, ip,
                                f"Multi-phase attack: {' -> '.join(sorted(phases))}",
                                "correlation", threat_type=threat_type, severity="critical",
                                risk_score=0.99,
                            ):
                                stats["blocks"] += 1

                        # ─── Signature-based blocking (threshold) ───────────────
                        elif tracker.should_sig_block(ip):
                            sig_count = len([x for x in tracker.sig_hits[ip]
                                             if x[0] > time.time() - SIG_BLOCK_WINDOW])
                            if auto_block_ip(
                                cur, ip,
                                f"WAF Signature: {sig_count} critical hits in {SIG_BLOCK_WINDOW}s",
                                "signature", threat_type=threat_type, severity="critical",
                                risk_score=0.95,
                            ):
                                stats["blocks"] += 1

                        # ─── Rate-based blocking ────────────────────────────────
                        elif tracker.should_rate_block(ip):
                            rate_count = len([t for t in tracker.windows[ip]
                                              if t > time.time() - RATE_BLOCK_WINDOW])
                            if auto_block_ip(
                                cur, ip,
                                f"Rate limit: {rate_count} reqs in {RATE_BLOCK_WINDOW}s",
                                "rate_limit", threat_type="RATE_LIMIT", severity="high",
                                risk_score=0.80,
                            ):
                                stats["blocks"] += 1

                        # ─── Reputation-based blocking ──────────────────────────
                        elif check_reputation_block(cur, ip):
                            if auto_block_ip(
                                cur, ip,
                                "Low reputation score (<0.2)",
                                "reputation", threat_type=threat_type or "LOW_REPUTATION",
                                severity="high", risk_score=0.85,
                            ):
                                stats["blocks"] += 1

                        # ─── Episode-based blocking ─────────────────────────────
                        ep_data = tracker.get_episode_data(ip)
                        if ep_data:
                            ep_id = upsert_episode(cur, ip, ep_data, threat_type, conf)
                            if ep_id:
                                stats["episodes"] += 1
                            if ep_data.get("risk_score", 0) >= 0.75:
                                if auto_block_ip(
                                    cur, ip,
                                    f"Episode risk: {ep_data['risk_score']:.0%} — "
                                    f"{ep_data['count']} threats ({ep_data['type']})",
                                    "episode", episode_id=ep_id,
                                    threat_type=ep_data["type"],
                                    severity=ep_data["severity"],
                                    risk_score=ep_data["risk_score"],
                                ):
                                    stats["blocks"] += 1

                    cur.close()
                    stats["processed"] += 1

            now = time.time()
            if now - last_maint > 60:
                cur = conn.cursor()
                maintenance(cur)
                cur.close()
                last_maint = now

            if now - last_report > 300:
                ddos_info = ""
                if HAS_DDOS:
                    ds = ddos_detector.get_status()
                    ddos_info = (f" | DDoS: {ds['current_rps']:.0f}rps "
                                 f"peak={ds['peak_rps']:.0f} "
                                 f"blocked={ds['blocked_ips_count']} "
                                 f"attack={'YES' if ds['under_attack'] else 'no'}")
                print(f"[rt] Stats: {stats['processed']} processed | {stats['threats']} threats | "
                      f"{stats['episodes']} episodes | {stats['blocks']} blocks "
                      f"({stats['instant_blocks']} instant){ddos_info}")
                last_report = now

        except psycopg2.OperationalError:
            print("[rt] DB reconnecting...")
            try:
                conn = get_pg()
            except Exception:
                time.sleep(5)
        except Exception as ex:
            print(f"[rt] Error: {ex}")
            time.sleep(1)


if __name__ == "__main__":
    main()
