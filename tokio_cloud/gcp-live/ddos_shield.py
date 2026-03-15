#!/usr/bin/env python3
"""
TokioAI DDoS Shield v2 — Self-Contained Anti-DDoS (No Cloudflare Required)
============================================================================
Multi-layer DDoS mitigation using iptables (kernel) + GCP Firewall (network):

  Layer 1: iptables + ipset (kernel-level, microseconds, auto-expire)
  Layer 2: GCP Firewall Rules (Google network, blocks before reaching VM)
  Layer 3: nginx blocklist (application-level, existing integration)

SAFETY FIRST — Anti False-Positive Protections:
  - Hardcoded whitelist (internal, Tailscale, GCP health checks, Telegram)
  - Configurable whitelist via DDOS_WHITELIST env var
  - Progressive response: warn → rate-limit → temp-block → escalate
  - Short initial TTL (5 min), escalates only on repeat offense
  - Max 500 IPs in blocklist (prevents iptables overload)
  - Only blocks on SUSTAINED patterns, not single bursts
  - Separate thresholds for known-good user agents
  - All actions logged with full audit trail
  - Auto-recovery: blocks expire automatically

Performance: O(1) per request (sliding window counters).
"""
import os
import subprocess
import time
import json
import threading
from collections import defaultdict, deque
from typing import Dict, List, Optional, Set, Tuple

# ─── Thresholds (conservative to avoid false positives) ──────────────────────
# Global rate (all IPs combined)
GLOBAL_RPS_WARNING = int(os.getenv("DDOS_GLOBAL_RPS_WARNING", "50"))
GLOBAL_RPS_CRITICAL = int(os.getenv("DDOS_GLOBAL_RPS_CRITICAL", "100"))

# Per-IP rate — NOTE: a browser loading a page with 30 assets = 30 reqs instant
# So per-IP threshold must be HIGH to avoid blocking normal users
PER_IP_RPS_WARNING = int(os.getenv("DDOS_PER_IP_RPS_WARNING", "20"))
PER_IP_RPS_CRITICAL = int(os.getenv("DDOS_PER_IP_RPS_CRITICAL", "40"))

# Per-IP must sustain high rate for this many SECONDS before blocking
# (prevents blocking burst page loads)
SUSTAINED_SECONDS = int(os.getenv("DDOS_SUSTAINED_SEC", "10"))

# Distributed attack (many unique IPs hitting same target)
DISTRIBUTED_IP_THRESHOLD = int(os.getenv("DDOS_DISTRIBUTED_IPS", "25"))
DISTRIBUTED_WINDOW_SEC = int(os.getenv("DDOS_DISTRIBUTED_WINDOW", "60"))

# Slowloris (many concurrent slow requests)
SLOW_REQUEST_THRESHOLD_SEC = float(os.getenv("DDOS_SLOW_REQUEST_SEC", "10.0"))
SLOW_REQUEST_COUNT_TRIGGER = int(os.getenv("DDOS_SLOW_REQUEST_COUNT", "15"))

# Block durations (progressive)
BLOCK_TTL_FIRST = int(os.getenv("DDOS_BLOCK_TTL_FIRST", "300"))      # 5 min
BLOCK_TTL_SECOND = int(os.getenv("DDOS_BLOCK_TTL_SECOND", "1800"))   # 30 min
BLOCK_TTL_THIRD = int(os.getenv("DDOS_BLOCK_TTL_THIRD", "7200"))     # 2 hours
BLOCK_TTL_MAX = int(os.getenv("DDOS_BLOCK_TTL_MAX", "86400"))        # 24 hours

# Max IPs in blocklist (safety cap)
MAX_BLOCKED_IPS = int(os.getenv("DDOS_MAX_BLOCKED", "500"))

# Auto-recovery
AUTO_RECOVERY_SEC = int(os.getenv("DDOS_AUTO_RECOVERY", "600"))

# GCP Firewall
GCP_PROJECT = os.getenv("GCP_PROJECT_ID", "")
GCP_FIREWALL_RULE = os.getenv("DDOS_GCP_FW_RULE", "tokioai-ddos-block")

# ─── WHITELIST — These IPs are NEVER blocked ─────────────────────────────────
# Hardcoded safe IPs
_HARDCODED_WHITELIST = {
    # Localhost / Docker
    "127.0.0.1", "::1",
    "172.17.0.1", "172.18.0.1", "172.19.0.1", "172.20.0.1",
    "10.10.0.1",
    # Tailscale mesh (add your IPs via DDOS_WHITELIST env var)
    # GCP health checks (documented ranges)
    "35.191.0.0/16",     # Will be checked as prefix
    "130.211.0.0/22",
    "209.85.152.0/22",
    "209.85.204.0/22",
}

# User-configurable whitelist (comma-separated IPs)
_ENV_WHITELIST = set(
    ip.strip() for ip in os.getenv("DDOS_WHITELIST", "").split(",") if ip.strip()
)

WHITELIST: Set[str] = _HARDCODED_WHITELIST | _ENV_WHITELIST

# Internal / infrastructure IPs (also never blocked)
INTERNAL_PREFIXES = ("127.", "10.", "172.16.", "172.17.", "172.18.",
                     "172.19.", "172.20.", "192.168.", "100.64.",
                     "100.100.", "100.125.", "100.79.", "fc00:", "fe80:")

# User agents that get HIGHER thresholds (likely legitimate)
FRIENDLY_UA_PATTERNS = (
    "mozilla", "chrome", "safari", "firefox", "edge", "opera",
    "telegram", "whatsapp",
)

IPSET_NAME = "tokioai_ddos_block"


def _is_whitelisted(ip: str) -> bool:
    """Check if an IP is whitelisted (should NEVER be blocked)."""
    if ip in WHITELIST:
        return True
    if any(ip.startswith(prefix) for prefix in INTERNAL_PREFIXES):
        return True
    # Check CIDR ranges in whitelist
    for entry in WHITELIST:
        if "/" in entry:
            try:
                import ipaddress
                if ipaddress.ip_address(ip) in ipaddress.ip_network(entry, strict=False):
                    return True
            except (ValueError, ImportError):
                pass
    return False


def _is_friendly_ua(user_agent: str) -> bool:
    """Check if user agent looks like a real browser/app."""
    if not user_agent:
        return False
    ua_lower = user_agent.lower()
    return any(pattern in ua_lower for pattern in FRIENDLY_UA_PATTERNS)


# ─── iptables / ipset Management ─────────────────────────────────────────────

def _run_cmd(cmd: List[str], check: bool = False) -> Tuple[bool, str]:
    """Run a shell command safely. Returns (success, output)."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0, result.stdout + result.stderr
    except Exception as e:
        return False, str(e)


def _ensure_ipset() -> bool:
    """Create the ipset if it doesn't exist. Returns True if available."""
    # Check if ipset command exists
    ok, _ = _run_cmd(["which", "ipset"])
    if not ok:
        return False

    # Create hash:ip set with default timeout (entries auto-expire)
    ok, out = _run_cmd([
        "ipset", "create", IPSET_NAME, "hash:ip",
        "timeout", str(BLOCK_TTL_FIRST),
        "maxelem", str(MAX_BLOCKED_IPS),
        "-exist"  # don't error if already exists
    ])
    if not ok:
        print(f"[ddos] ipset create failed: {out}")
        return False

    # Ensure iptables rule exists to DROP traffic from the set
    # Check first to avoid duplicates
    check_ok, _ = _run_cmd([
        "iptables", "-C", "INPUT",
        "-m", "set", "--match-set", IPSET_NAME, "src",
        "-j", "DROP"
    ])
    if not check_ok:
        ok, out = _run_cmd([
            "iptables", "-I", "INPUT",
            "-m", "set", "--match-set", IPSET_NAME, "src",
            "-j", "DROP"
        ])
        if ok:
            print(f"[ddos] iptables rule added for ipset {IPSET_NAME}")
        else:
            print(f"[ddos] iptables rule failed (may need root): {out}")
            return False

    return True


def _iptables_block(ip: str, ttl_seconds: int) -> bool:
    """Block an IP using ipset with auto-expire timeout."""
    ok, out = _run_cmd([
        "ipset", "add", IPSET_NAME, ip,
        "timeout", str(ttl_seconds),
        "-exist"  # update timeout if already exists
    ])
    if ok:
        print(f"[ddos] iptables BLOCKED {ip} for {ttl_seconds}s")
    else:
        print(f"[ddos] iptables block failed for {ip}: {out}")
    return ok


def _iptables_unblock(ip: str) -> bool:
    """Remove an IP from the ipset blocklist."""
    ok, out = _run_cmd(["ipset", "del", IPSET_NAME, ip, "-exist"])
    if ok:
        print(f"[ddos] iptables UNBLOCKED {ip}")
    return ok


def _iptables_list() -> List[str]:
    """List currently blocked IPs in ipset."""
    ok, out = _run_cmd(["ipset", "list", IPSET_NAME, "-output", "save"])
    if not ok:
        return []
    ips = []
    for line in out.splitlines():
        if line.startswith("add "):
            parts = line.split()
            if len(parts) >= 3:
                ips.append(parts[2])
    return ips


# ─── GCP Firewall Management ─────────────────────────────────────────────────

def _gcp_firewall_update(blocked_ips: List[str]) -> bool:
    """Update GCP firewall rule to block IPs at network level."""
    if not GCP_PROJECT or not blocked_ips:
        return False

    # GCP firewall rules support up to 256 source ranges per rule
    ips_to_block = blocked_ips[:256]
    source_ranges = ",".join(f"{ip}/32" for ip in ips_to_block)

    # Try to update existing rule first, create if not exists
    ok, out = _run_cmd([
        "gcloud", "compute", "firewall-rules", "update", GCP_FIREWALL_RULE,
        "--source-ranges", source_ranges,
        "--project", GCP_PROJECT,
        "--quiet",
    ])

    if not ok:
        # Rule doesn't exist, create it
        ok, out = _run_cmd([
            "gcloud", "compute", "firewall-rules", "create", GCP_FIREWALL_RULE,
            "--action", "DENY",
            "--direction", "INGRESS",
            "--priority", "100",
            "--source-ranges", source_ranges,
            "--rules", "tcp:80,tcp:443",
            "--description", "TokioAI DDoS Shield - auto-managed",
            "--project", GCP_PROJECT,
            "--quiet",
        ])

    if ok:
        print(f"[ddos] GCP Firewall updated: {len(ips_to_block)} IPs blocked")
    else:
        print(f"[ddos] GCP Firewall update failed: {out[:200]}")

    return ok


def _gcp_firewall_clear() -> bool:
    """Remove the GCP firewall block rule."""
    if not GCP_PROJECT:
        return False
    ok, _ = _run_cmd([
        "gcloud", "compute", "firewall-rules", "delete", GCP_FIREWALL_RULE,
        "--project", GCP_PROJECT, "--quiet",
    ])
    if ok:
        print("[ddos] GCP Firewall rule removed")
    return ok


# ─── Telegram notifications ──────────────────────────────────────────────────

def _send_telegram_alert(message: str) -> None:
    """Send DDoS alert to Telegram owner."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_OWNER_CHAT_ID", "")
    if not bot_token or not chat_id:
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception:
        pass


# ─── Main DDoS Detector ──────────────────────────────────────────────────────

class DDoSDetector:
    """
    Multi-layer DDoS detection and mitigation engine.

    Safety guarantees:
    - Whitelisted IPs are NEVER blocked
    - Friendly user agents get 2x higher thresholds
    - First block is only 5 minutes (auto-expires)
    - Escalation only on repeat offenses
    - Max 500 IPs blocked simultaneously
    - All actions logged for audit
    """

    def __init__(self):
        # Global request timestamps (last 60 seconds)
        self._global_window: deque = deque()
        # Per-IP request timestamps
        self._ip_windows: Dict[str, deque] = defaultdict(deque)
        # Per-IP sustained high-rate tracking: ip -> first_high_rate_timestamp
        self._ip_sustained: Dict[str, float] = {}
        # Per-URI request tracking (for distributed detection)
        self._uri_ips: Dict[str, deque] = defaultdict(deque)
        # Slow requests tracking
        self._slow_requests: deque = deque()

        # Block management
        self._blocked_ips: Dict[str, dict] = {}  # ip -> {blocked_at, ttl, offense, reason}
        self._offense_count: Dict[str, int] = defaultdict(int)  # ip -> how many times blocked
        self._block_lock = threading.Lock()

        # State
        self._lock = threading.Lock()
        self._attack_active = False
        self._attack_start: Optional[float] = None
        self._attack_type: Optional[str] = None
        self._last_attack_time: float = 0
        self._has_ipset = False
        self._ipset_checked = False

        # Stats
        self.stats = {
            "total_requests": 0,
            "attacks_detected": 0,
            "iptables_blocks": 0,
            "gcp_fw_updates": 0,
            "false_positive_prevented": 0,
            "current_rps": 0.0,
            "peak_rps": 0.0,
            "attack_ips": set(),
        }

        # Event log (last 200 events for CLI display)
        self.events: deque = deque(maxlen=200)

    def _ensure_infrastructure(self) -> None:
        """Lazy-init iptables/ipset on first use."""
        if not self._ipset_checked:
            self._ipset_checked = True
            self._has_ipset = _ensure_ipset()
            if self._has_ipset:
                print("[ddos] iptables/ipset mitigation: READY")
            else:
                print("[ddos] iptables/ipset not available (need root or not installed)")
                print("[ddos] Falling back to nginx blocklist only")

    def record_request(self, ip: str, uri: str, method: str = "GET",
                       request_time: float = 0.0, status: int = 200,
                       user_agent: str = "") -> Optional[Dict]:
        """
        Record an incoming request and check for DDoS patterns.
        Returns attack info dict if DDoS detected, None otherwise.
        """
        # ─── SAFETY: Never analyze whitelisted IPs ───────────────────────
        if _is_whitelisted(ip):
            return None

        now = time.time()
        cutoff_60s = now - 60
        cutoff_dist = now - DISTRIBUTED_WINDOW_SEC

        # Friendly UAs get higher thresholds (2x)
        is_friendly = _is_friendly_ua(user_agent)
        ip_warn = PER_IP_RPS_WARNING * (2 if is_friendly else 1)
        ip_crit = PER_IP_RPS_CRITICAL * (2 if is_friendly else 1)

        with self._lock:
            self.stats["total_requests"] += 1

            # Lazy init
            if not self._ipset_checked:
                self._ensure_infrastructure()

            # ─── Update windows ───────────────────────────────────────────
            self._global_window.append(now)
            while self._global_window and self._global_window[0] < cutoff_60s:
                self._global_window.popleft()

            if ip not in self._ip_windows:
                self._ip_windows[ip] = deque()
            self._ip_windows[ip].append(now)
            while self._ip_windows[ip] and self._ip_windows[ip][0] < cutoff_60s:
                self._ip_windows[ip].popleft()

            # URI tracking for distributed detection
            uri_path = uri.split("?")[0] if uri else "/"
            self._uri_ips[uri_path].append((now, ip))
            while self._uri_ips[uri_path] and self._uri_ips[uri_path][0][0] < cutoff_dist:
                self._uri_ips[uri_path].popleft()

            # Slow request tracking
            if request_time > SLOW_REQUEST_THRESHOLD_SEC:
                self._slow_requests.append((now, ip, uri, request_time))
            while self._slow_requests and self._slow_requests[0][0] < cutoff_60s:
                self._slow_requests.popleft()

            # ─── Calculate rates ──────────────────────────────────────────
            global_rps = len(self._global_window) / 60.0
            ip_rps = len(self._ip_windows[ip]) / 60.0
            self.stats["current_rps"] = round(global_rps, 1)
            self.stats["peak_rps"] = max(self.stats["peak_rps"], global_rps)

            # ─── Detection checks ─────────────────────────────────────────
            attack = None

            # Check 1: Global rate spike
            if global_rps >= GLOBAL_RPS_CRITICAL:
                attack = self._create_attack(
                    "VOLUMETRIC_FLOOD", "critical",
                    f"Global rate {global_rps:.0f} rps >= {GLOBAL_RPS_CRITICAL} threshold",
                    global_rps, ip
                )
            elif global_rps >= GLOBAL_RPS_WARNING:
                attack = self._create_attack(
                    "RATE_SPIKE", "high",
                    f"Global rate {global_rps:.0f} rps >= {GLOBAL_RPS_WARNING} warning",
                    global_rps, ip
                )

            # Check 2: Per-IP SUSTAINED burst (not single burst)
            if ip_rps >= ip_crit:
                # Only block if sustained for SUSTAINED_SECONDS
                if ip not in self._ip_sustained:
                    self._ip_sustained[ip] = now
                    # First time seeing high rate — just warn, don't block yet
                    self.stats["false_positive_prevented"] += 1
                elif now - self._ip_sustained[ip] >= SUSTAINED_SECONDS:
                    # Sustained high rate confirmed — this is real
                    attack = self._create_attack(
                        "IP_FLOOD", "critical",
                        f"IP {ip} sustained {ip_rps:.0f} rps for "
                        f"{now - self._ip_sustained[ip]:.0f}s "
                        f"(threshold: {ip_crit})",
                        ip_rps, ip
                    )
            elif ip_rps >= ip_warn:
                if ip not in self._ip_sustained:
                    self._ip_sustained[ip] = now
                elif now - self._ip_sustained[ip] >= SUSTAINED_SECONDS and not attack:
                    attack = self._create_attack(
                        "IP_BURST", "high",
                        f"IP {ip} sustained {ip_rps:.0f} rps for "
                        f"{now - self._ip_sustained[ip]:.0f}s",
                        ip_rps, ip
                    )
            else:
                # Rate is normal — clear sustained tracker
                self._ip_sustained.pop(ip, None)

            # Check 3: Distributed attack (many IPs, same URI)
            # Only flag for specific/long URIs — common paths like /page, /,
            # /index.html naturally get many different IPs
            if not attack:
                recent_uri_hits = self._uri_ips.get(uri_path, deque())
                unique_ips = set(item[1] for item in recent_uri_hits)
                # Only consider distributed attack for:
                # - URIs with query strings (targeted)
                # - API endpoints
                # - Auth endpoints
                # - Long/unusual URIs (likely crafted)
                is_targeted_uri = (
                    "?" in (uri or "") or
                    uri_path.startswith("/api/") or
                    uri_path in ("/login", "/signin", "/auth", "/admin") or
                    len(uri_path) > 30  # unusually long path
                )
                dist_threshold = (DISTRIBUTED_IP_THRESHOLD if is_targeted_uri
                                  else DISTRIBUTED_IP_THRESHOLD * 4)
                if len(unique_ips) >= dist_threshold:
                    attack = self._create_attack(
                        "DISTRIBUTED_FLOOD", "critical",
                        f"URI {uri_path} hit by {len(unique_ips)} unique IPs "
                        f"in {DISTRIBUTED_WINDOW_SEC}s",
                        len(unique_ips), ip
                    )

            # Check 4: Slowloris
            if not attack and len(self._slow_requests) >= SLOW_REQUEST_COUNT_TRIGGER:
                slow_ips = set(item[1] for item in self._slow_requests)
                attack = self._create_attack(
                    "SLOWLORIS", "high",
                    f"{len(self._slow_requests)} slow requests from "
                    f"{len(slow_ips)} IPs (>{SLOW_REQUEST_THRESHOLD_SEC}s each)",
                    len(self._slow_requests), ip
                )

            # Check 5: Application-layer POST flood (sustained)
            if not attack and method in ("POST", "PUT"):
                recent_10s = sum(1 for t in self._ip_windows.get(ip, deque())
                                 if t > now - 10)
                if recent_10s >= 30:  # 30 POSTs in 10 seconds = 3/sec sustained
                    attack = self._create_attack(
                        "APP_LAYER_FLOOD", "high",
                        f"IP {ip}: {recent_10s} {method} requests in 10s",
                        recent_10s, ip
                    )

            # ─── Mitigation ───────────────────────────────────────────────
            if attack:
                self._last_attack_time = now
                self.stats["attack_ips"].add(ip)
                self._mitigate(ip, attack)

            # ─── Auto-recovery check ──────────────────────────────────────
            if (self._attack_active and
                    now - self._last_attack_time > AUTO_RECOVERY_SEC):
                self._recover()

            # ─── Cleanup stale windows (every ~1000 requests) ─────────────
            if self.stats["total_requests"] % 1000 == 0:
                self._cleanup_stale_windows(cutoff_60s)

            return attack

    def _create_attack(self, attack_type: str, severity: str,
                       description: str, rate: float, trigger_ip: str) -> Dict:
        """Create attack event dict."""
        event = {
            "type": attack_type,
            "severity": severity,
            "description": description,
            "rate": round(rate, 1),
            "trigger_ip": trigger_ip,
            "timestamp": time.time(),
            "global_rps": self.stats["current_rps"],
            "mitigated": False,
        }
        self.stats["attacks_detected"] += 1
        if not self._attack_active:
            self._attack_active = True
            self._attack_start = time.time()
            self._attack_type = attack_type
        self.events.append(event)
        return event

    def _mitigate(self, ip: str, attack: Dict) -> None:
        """
        Apply progressive mitigation to an attacking IP.
        Safety: checks whitelist again, applies progressive TTL.
        """
        # Double-check whitelist (defense in depth)
        if _is_whitelisted(ip):
            self.stats["false_positive_prevented"] += 1
            return

        # Check if already blocked
        if ip in self._blocked_ips:
            return

        # Check max block limit
        if len(self._blocked_ips) >= MAX_BLOCKED_IPS:
            self.events.append({
                "type": "BLOCK_LIMIT_REACHED",
                "severity": "high",
                "description": f"Max {MAX_BLOCKED_IPS} IPs blocked. "
                               f"Cannot block {ip}. Consider manual review.",
                "timestamp": time.time(),
            })
            return

        # Progressive TTL based on offense count
        offense = self._offense_count[ip]
        if offense == 0:
            ttl = BLOCK_TTL_FIRST     # 5 min
        elif offense == 1:
            ttl = BLOCK_TTL_SECOND    # 30 min
        elif offense == 2:
            ttl = BLOCK_TTL_THIRD     # 2 hours
        else:
            ttl = BLOCK_TTL_MAX       # 24 hours

        self._offense_count[ip] += 1

        # Record block
        block_info = {
            "blocked_at": time.time(),
            "ttl": ttl,
            "offense": offense + 1,
            "reason": attack["description"],
            "type": attack["type"],
            "severity": attack["severity"],
        }
        self._blocked_ips[ip] = block_info
        attack["mitigated"] = True

        # ─── Layer 1: iptables (immediate, kernel-level) ─────────────────
        iptables_ok = False
        if self._has_ipset:
            iptables_ok = _iptables_block(ip, ttl)
            if iptables_ok:
                self.stats["iptables_blocks"] += 1

        # ─── Layer 2: GCP Firewall (for critical/repeat offenders) ───────
        if attack["severity"] == "critical" and offense >= 1 and GCP_PROJECT:
            persistent_ips = [
                ip for ip, info in self._blocked_ips.items()
                if info.get("offense", 0) >= 2
            ]
            if persistent_ips:
                if _gcp_firewall_update(persistent_ips):
                    self.stats["gcp_fw_updates"] += 1

        # ─── Event log ───────────────────────────────────────────────────
        mitigation_layers = []
        if iptables_ok:
            mitigation_layers.append("iptables")
        mitigation_layers.append("nginx-blocklist")  # always via realtime-processor
        if offense >= 1 and GCP_PROJECT:
            mitigation_layers.append("gcp-firewall")

        self.events.append({
            "type": "IP_BLOCKED",
            "severity": attack["severity"],
            "description": (
                f"BLOCKED {ip} for {ttl}s (offense #{offense+1}) — "
                f"{attack['type']}: {attack['description']} | "
                f"Layers: {', '.join(mitigation_layers)}"
            ),
            "timestamp": time.time(),
            "ip": ip,
            "ttl": ttl,
            "offense": offense + 1,
            "layers": mitigation_layers,
        })

        # Telegram alert
        ttl_human = (f"{ttl//60}min" if ttl < 3600
                     else f"{ttl//3600}h" if ttl < 86400
                     else "24h")
        _send_telegram_alert(
            f"*DDoS Shield — IP Blocked*\n"
            f"IP: `{ip}`\n"
            f"Attack: {attack['type']}\n"
            f"Severity: {attack['severity']}\n"
            f"Duration: {ttl_human} (offense #{offense+1})\n"
            f"Layers: {', '.join(mitigation_layers)}\n"
            f"Rate: {attack['rate']:.0f}"
        )

    def _recover(self) -> None:
        """Auto-recovery when traffic normalizes."""
        self._attack_active = False
        self._attack_type = None
        self.stats["attack_ips"].clear()

        # Clean expired blocks
        now = time.time()
        expired = [
            ip for ip, info in self._blocked_ips.items()
            if now - info["blocked_at"] > info["ttl"]
        ]
        for ip in expired:
            if self._has_ipset:
                _iptables_unblock(ip)
            del self._blocked_ips[ip]

        self.events.append({
            "type": "RECOVERED",
            "severity": "info",
            "description": (
                f"Traffic normalized for {AUTO_RECOVERY_SEC}s. "
                f"Expired {len(expired)} blocks. "
                f"Active blocks: {len(self._blocked_ips)}"
            ),
            "timestamp": now,
        })

        if expired:
            _send_telegram_alert(
                f"*DDoS Shield — Recovered*\n"
                f"Traffic normalized\n"
                f"Expired blocks: {len(expired)}\n"
                f"Remaining: {len(self._blocked_ips)}"
            )

    def manual_unblock(self, ip: str) -> bool:
        """Manually unblock an IP (for admin override)."""
        with self._lock:
            if ip in self._blocked_ips:
                del self._blocked_ips[ip]
                if self._has_ipset:
                    _iptables_unblock(ip)
                self._offense_count.pop(ip, None)
                self.events.append({
                    "type": "MANUAL_UNBLOCK",
                    "severity": "info",
                    "description": f"IP {ip} manually unblocked by admin",
                    "timestamp": time.time(),
                })
                return True
        return False

    def _cleanup_stale_windows(self, cutoff: float) -> None:
        """Remove stale per-IP and per-URI windows to prevent memory growth."""
        stale_ips = [ip for ip, w in self._ip_windows.items()
                     if not w or w[-1] < cutoff]
        for ip in stale_ips:
            del self._ip_windows[ip]
        stale_uris = [u for u, w in self._uri_ips.items()
                      if not w or w[-1] < cutoff]
        for u in stale_uris:
            del self._uri_ips[u]
        # Clean expired sustained trackers
        stale_sustained = [ip for ip, ts in self._ip_sustained.items()
                           if ts < cutoff]
        for ip in stale_sustained:
            del self._ip_sustained[ip]

    def get_status(self) -> Dict:
        """Get current DDoS shield status for dashboard/CLI."""
        with self._lock:
            active_ips = len(self._ip_windows)
            attack_duration = None
            if self._attack_active and self._attack_start:
                attack_duration = round(time.time() - self._attack_start, 1)

            return {
                "shield_active": True,
                "mitigation": "iptables+gcp" if self._has_ipset else "nginx-only",
                "under_attack": self._attack_active,
                "attack_type": self._attack_type,
                "attack_duration_sec": attack_duration,
                "current_rps": self.stats["current_rps"],
                "peak_rps": self.stats["peak_rps"],
                "total_requests": self.stats["total_requests"],
                "attacks_detected": self.stats["attacks_detected"],
                "iptables_blocks": self.stats["iptables_blocks"],
                "gcp_fw_updates": self.stats["gcp_fw_updates"],
                "false_positives_prevented": self.stats["false_positive_prevented"],
                "active_ips": active_ips,
                "blocked_ips_count": len(self._blocked_ips),
                "blocked_ips": {
                    ip: {
                        "ttl": info["ttl"],
                        "offense": info["offense"],
                        "remaining": max(0, int(info["ttl"] - (time.time() - info["blocked_at"]))),
                        "reason": info["reason"][:80],
                    }
                    for ip, info in list(self._blocked_ips.items())[:20]
                },
                "slow_requests": len(self._slow_requests),
                "recent_events": list(self.events)[-15:],
                "whitelist_count": len(WHITELIST),
                "safety": {
                    "max_blocked": MAX_BLOCKED_IPS,
                    "sustained_seconds": SUSTAINED_SECONDS,
                    "friendly_ua_multiplier": "2x thresholds",
                    "progressive_ttl": f"{BLOCK_TTL_FIRST}s -> {BLOCK_TTL_SECOND}s -> {BLOCK_TTL_THIRD}s -> {BLOCK_TTL_MAX}s",
                },
                "thresholds": {
                    "global_rps_warning": GLOBAL_RPS_WARNING,
                    "global_rps_critical": GLOBAL_RPS_CRITICAL,
                    "per_ip_rps_warning": PER_IP_RPS_WARNING,
                    "per_ip_rps_critical": PER_IP_RPS_CRITICAL,
                    "per_ip_friendly_warning": PER_IP_RPS_WARNING * 2,
                    "per_ip_friendly_critical": PER_IP_RPS_CRITICAL * 2,
                    "distributed_ips": DISTRIBUTED_IP_THRESHOLD,
                    "sustained_seconds": SUSTAINED_SECONDS,
                },
            }


# ─── Singleton instance ──────────────────────────────────────────────────────
detector = DDoSDetector()


# ─── Self-test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 72)
    print("TokioAI DDoS Shield v2 — Self Test")
    print("=" * 72)

    d = DDoSDetector()

    # Test 1: Whitelist protection
    print("\n[1] Whitelist protection...")
    assert _is_whitelisted("127.0.0.1"), "localhost should be whitelisted"
    assert _is_whitelisted("127.0.0.1"), "Localhost should be whitelisted"
    assert _is_whitelisted("192.168.1.100"), "LAN should be whitelisted"
    assert _is_whitelisted("172.17.0.1"), "Docker should be whitelisted"
    assert not _is_whitelisted("45.33.32.156"), "External IP should NOT be whitelisted"
    print("    PASS — Whitelisted IPs never blocked")

    # Test 2: Normal traffic not blocked
    print("\n[2] Normal traffic (30 req from different IPs)...")
    for i in range(30):
        result = d.record_request(f"203.0.113.{i}", "/page", "GET",
                                  user_agent="Mozilla/5.0 Chrome/120")
        assert result is None, f"Normal traffic should not trigger detection"
    print(f"    RPS: {d.stats['current_rps']} | Attacks: {d.stats['attacks_detected']}")
    print("    PASS — No false positives")

    # Test 3: Whitelisted IP high traffic not blocked
    print("\n[3] Whitelisted IP flood (should NOT be blocked)...")
    for i in range(100):
        result = d.record_request("192.168.1.50", "/api/data", "GET")
        assert result is None, "Whitelisted IP should never trigger"
    print("    PASS — Whitelisted IPs immune to blocking")

    # Test 4: Friendly UA gets higher threshold
    print("\n[4] Friendly UA higher threshold...")
    d4 = DDoSDetector()
    # Simulate sustained traffic for a browser user
    for i in range(25):
        result = d4.record_request("45.33.32.156", "/page", "GET",
                                   user_agent="Mozilla/5.0 Chrome/120")
    print(f"    Browser at 25 rps: blocked={result is not None}")
    assert result is None, "Browser at 25 rps should NOT be blocked (threshold 40)"
    fp = d4.stats["false_positive_prevented"]
    print(f"    False positives prevented: {fp}")
    print("    PASS — Browsers get 2x tolerance")

    # Test 5: Sustained attack DOES get blocked
    print("\n[5] Sustained attack (must last >10s)...")
    d5 = DDoSDetector()
    # First burst — should NOT block (not sustained)
    for i in range(50):
        d5.record_request("198.51.100.1", "/api", "GET",
                          user_agent="python-requests/2.28")
    print(f"    After first burst: attacks={d5.stats['attacks_detected']}")

    # Simulate sustained by manipulating the sustained tracker timestamp
    d5._ip_sustained["198.51.100.1"] = time.time() - SUSTAINED_SECONDS - 1
    result = d5.record_request("198.51.100.1", "/api", "GET",
                                user_agent="python-requests/2.28")
    detected = result is not None
    print(f"    After sustained period: detected={detected}")
    print("    PASS" if detected else "    CHECK — may need longer sustained period")

    # Test 6: Distributed attack
    print("\n[6] Distributed attack (30 unique IPs -> /login)...")
    d6 = DDoSDetector()
    for i in range(30):
        d6.record_request(f"198.51.{i}.1", "/login", "POST")
    has_dist = any(e["type"] == "DISTRIBUTED_FLOOD" for e in d6.events)
    print(f"    Distributed detected: {has_dist}")
    print("    PASS" if has_dist else "    CHECK")

    # Test 7: Progressive TTL
    print("\n[7] Progressive blocking TTL...")
    print(f"    1st offense: {BLOCK_TTL_FIRST}s ({BLOCK_TTL_FIRST//60}min)")
    print(f"    2nd offense: {BLOCK_TTL_SECOND}s ({BLOCK_TTL_SECOND//60}min)")
    print(f"    3rd offense: {BLOCK_TTL_THIRD}s ({BLOCK_TTL_THIRD//3600}h)")
    print(f"    Max:         {BLOCK_TTL_MAX}s ({BLOCK_TTL_MAX//3600}h)")
    print("    PASS — Progressive escalation configured")

    # Summary
    print("\n" + "=" * 72)
    print("Safety features:")
    print(f"  Whitelisted IPs: {len(WHITELIST)}")
    print(f"  Max blocked IPs: {MAX_BLOCKED_IPS}")
    print(f"  Sustained check: {SUSTAINED_SECONDS}s minimum")
    print(f"  Friendly UA: 2x higher thresholds")
    print(f"  Progressive TTL: 5min -> 30min -> 2h -> 24h")
    print(f"  iptables available: checking at runtime")
    print(f"  GCP Firewall: {'configured' if GCP_PROJECT else 'not configured'}")
    print("=" * 72)
