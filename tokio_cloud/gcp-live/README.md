# TokioAI WAF — Deployment Guide

## What is this?

A complete **Web Application Firewall (WAF)** with a real-time cyberpunk SOC dashboard. It sits in front of your web application, detects and blocks attacks, and gives you a live dashboard to monitor everything.

> **This is 100% optional.** The TokioAI agent works perfectly without the WAF. Deploy this only if you want to protect a web application.

## Architecture

```
Internet → [Nginx WAF Proxy] → Your Backend Server
                │
            [Kafka] ← access logs (JSON)
                │
        [Realtime Processor] → detects attacks, blocks IPs
                │
          [PostgreSQL] ← stores events, reputation
                │
         [Dashboard API] → SOC dashboard on port 8000
```

**7 containers:** PostgreSQL, Zookeeper, Kafka, Nginx WAF proxy, Log processor, Realtime processor, Dashboard API.

---

## Prerequisites

- A server with Docker and Docker Compose (GCP VM, AWS EC2, DigitalOcean, Hetzner, or any VPS)
- A domain pointing to your server (for SSL)
- A backend to protect (any web server)

**No GCP account required** — this runs on any Linux server with Docker.

---

## Quick Start

### 1. Set up your server

```bash
# SSH into your server
ssh user@your-server-ip

# Install Docker (if not installed)
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
```

### 2. Deploy the WAF

```bash
# Copy the gcp-live folder to your server
scp -r tokio_cloud/gcp-live/ user@your-server-ip:/opt/tokio-waf/

# SSH in and configure
ssh user@your-server-ip
cd /opt/tokio-waf

# Create your .env
cp .env.example .env
nano .env
# Set: POSTGRES_PASSWORD, DASHBOARD_PASSWORD, JWT_SECRET
```

### 3. Configure nginx.conf

Edit `nginx.conf` and replace these two placeholders:

| Placeholder | Replace with | Example |
|:------------|:-------------|:--------|
| `your-domain.com` | Your actual domain | `myapp.com` |
| `YOUR_BACKEND_IP` | Your backend server's IP | `203.0.113.50` |

```bash
# Quick replace (change the values!)
sed -i 's/your-domain.com/myapp.com/g' nginx.conf
sed -i 's/YOUR_BACKEND_IP/203.0.113.50/g' nginx.conf
```

### 4. SSL Certificate (optional but recommended)

```bash
# Option A: Let's Encrypt (free)
mkdir -p ssl
apt install -y certbot
certbot certonly --standalone -d myapp.com
cp /etc/letsencrypt/live/myapp.com/fullchain.pem ssl/
cp /etc/letsencrypt/live/myapp.com/privkey.pem ssl/

# Option B: Self-signed (for testing)
mkdir -p ssl
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout ssl/privkey.pem -out ssl/fullchain.pem \
  -subj "/CN=myapp.com"
```

### 5. Start everything

```bash
docker compose up -d
```

### 6. Access the dashboard

Open `http://your-server-ip:8000/dashboard` (or `https://your-domain.com/dashboard`).

Login with the credentials from your `.env` file (`DASHBOARD_USER` / `DASHBOARD_PASSWORD`).

---

## Updating

Use the included deploy script from your local machine:

```bash
export GCP_HOST=your-server-ip
export GCP_USER=your-ssh-user
./deploy.sh
```

This script:
- Creates a backup of current files
- Uploads updated files via SCP
- Restarts containers one by one (zero-downtime)
- Runs health checks
- Sends Telegram notification (if configured)

---

## Dashboard Features

- **Real-time SSE attack feed** — See attacks as they happen
- **World attack map** — Geographic origin of attacks
- **OWASP Top 10 breakdown** — Classification of threats
- **Kill chain visualization** — Multi-phase attack correlation
- **IP reputation tracking** — Score-based reputation per IP
- **Threat intelligence** — AbuseIPDB integration
- **Attack heatmap** — Hour x Day-of-week patterns
- **CSV export** — Download filtered logs for analysis

## WAF Engine

- **26 detection signatures** — SQLi, XSS, RCE, LFI, SSRF, Log4Shell, etc.
- **7 behavioral rules** — Rate limiting, brute force, scanner detection
- **Honeypot endpoints** — Fake `/wp-admin`, `/.env`, `/phpmyadmin`
- **Auto-blocking** — Instant block on critical signatures (confidence >= 0.90)
- **IP reputation scoring** — Persistent scores in PostgreSQL
- **Multi-phase correlation** — Detects Recon -> Probe -> Exploit -> Exfil chains

---

## Connecting to TokioAI Agent (Hybrid Mode)

If you want your local TokioAI agent to manage this WAF:

1. Run `tokio setup` and choose **Mode 2 (Hybrid)**
2. Enter your WAF server IP when prompted
3. The agent will be able to SSH into the WAF server and manage it

This is optional — the WAF works independently without the agent.
