# TokioAI Live Attack Demo

Scripts for demonstrating TokioAI's autonomous defense capabilities at live events.

## Setup

### On the big screen (SOC Terminal)
```bash
python3 tokio_soc_v2.py --autonomous
```

### On the attacker laptop
```bash
# Install tools
apt install -y sqlmap nikto nmap ffuf wrk curl
pip install nuclei

# Make executable
chmod +x attack_demo.sh

# Run
./attack_demo.sh https://your-target-domain.com
```

## Attack Phases

| Phase | Attack Type | Tools |
|-------|------------|-------|
| 1 | Reconnaissance | curl, path discovery |
| 2 | SQL Injection | curl payloads, sqlmap |
| 3 | Cross-Site Scripting | curl XSS vectors |
| 4 | Command Injection / RCE | OS injection, Log4Shell, SSTI |
| 5 | Path Traversal | directory traversal, LFI/RFI |
| 6 | SSRF & XXE | cloud metadata, XML entities |
| 7 | Auth Attacks | brute force, JWT, cookies |
| 8 | DDoS / Rate Limiting | HTTP flood, slowloris, wrk |
| 9 | Vuln Scanners | nikto, nuclei, CVE probes |

## Modes

- **Interactive** (default): press Enter between phases
- **`--all`**: run all phases with 3s pauses
- **`--fast`**: no pauses at all
- **`--phase N`**: start from phase N

## Legal

**Only use against infrastructure you own.** These are real attack payloads designed for authorized security testing and demonstrations.
