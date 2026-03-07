# TokioAI — Tailscale Mesh Deployment

Connect TokioAI to any hardware, anywhere, from any network. Zero additional cost.

## Overview

TokioAI uses [Tailscale](https://tailscale.com) to create a secure mesh VPN between the cloud (where TokioAI runs) and your local hardware (Raspberry Pi, routers, IoT devices, servers). This means:

- TokioAI in the cloud can control hardware at home
- You can switch networks and everything reconnects automatically
- No ports exposed to the internet, no port forwarding, no dynamic DNS
- Free tier covers up to 100 devices

## Architecture

```
                        Tailscale Mesh (WireGuard)
                     ================================

  Cloud VM (GCP/AWS/VPS)              Local Hardware
  ┌──────────────────────┐           ┌──────────────────┐
  │  TokioAI Agent       │◄─────────►│  Raspberry Pi    │
  │  (28+ tools,         │  Tailscale│  - GPIO/relays   │
  │   Claude/GPT/Gemini) │  100.x.x  │  - sensors       │
  │                      │           │  - cameras       │
  │  Telegram Bot        │           └──────────────────┘
  │  (multi-user ACL)    │
  │                      │           ┌──────────────────┐
  │  WAF/SOC             │◄─────────►│  Home Server     │
  │  (optional, shared   │  Tailscale│  - backup node   │
  │   postgres + kafka)  │  100.x.x  │  - subnet router │
  └──────────────────────┘           │    → LAN access  │
           ▲                         └──────────────────┘
           │                                  │
           │ Tailscale                        │ Subnet Route
           │ 100.x.x.x                       │ 192.168.x.0/24
           ▼                                  ▼
  ┌──────────────────┐              ┌──────────────────┐
  │  Your Phone/     │              │  Router           │
  │  Laptop          │              │  (SSH control)    │
  │  (Tailscale app) │              │  Any LAN device   │
  └──────────────────┘              └──────────────────┘
```

## Setup Guide

### 1. Install Tailscale on all machines

```bash
# Works on Linux, macOS, Raspberry Pi, etc.
curl -fsSL https://tailscale.com/install.sh | sh
```

### 2. Authenticate the cloud VM

```bash
sudo tailscale up
# Opens a browser link to authenticate — log in with your account
```

### 3. Authenticate local machines

On each local machine (Raspberry Pi, home server):

```bash
sudo tailscale up
```

### 4. Enable subnet routing (optional but recommended)

If you want TokioAI to reach devices on your local network (routers, printers, NAS, etc.), pick one machine on your LAN as a **subnet router**:

```bash
# On the subnet router machine (e.g., your home server):
sudo sysctl -w net.ipv4.ip_forward=1
echo 'net.ipv4.ip_forward=1' | sudo tee -a /etc/sysctl.conf
sudo tailscale up --advertise-routes=192.168.8.0/24
```

Then approve the route in the [Tailscale admin console](https://login.tailscale.com/admin/machines) and accept routes on the cloud VM:

```bash
# On the cloud VM:
sudo tailscale set --accept-routes=true
```

### 5. Deploy TokioAI in the cloud

```bash
# Copy the project to your cloud VM
scp -r tokioai-v2/ user@your-vm:/opt/tokioai-v2/

# Edit .env with your API keys and Telegram token
cp .env.example .env
nano .env

# If sharing an existing PostgreSQL (recommended):
docker compose -f docker-compose.cloud.yml up -d

# If running standalone:
docker compose up -d
```

### 6. Mount SSH keys for remote control

If TokioAI needs to SSH into routers or hosts via the mesh:

```bash
# Create a directory for SSH keys on the cloud VM
mkdir -p /opt/tokioai-v2/ssh-keys/

# Copy your SSH keys
cp id_ed25519_router /opt/tokioai-v2/ssh-keys/
chmod 600 /opt/tokioai-v2/ssh-keys/*

# Uncomment the volume mounts in docker-compose.cloud.yml
# Then restart:
docker compose -f docker-compose.cloud.yml up -d
```

## How it works

### Container networking

Docker containers on a bridge network can reach Tailscale destinations through the host's routing table. No special configuration needed — if the host can reach `192.168.8.1` via Tailscale, so can the containers.

### Session isolation

Each Telegram user gets their own session ID (`telegram-{user_id}`) stored in PostgreSQL. Conversations never mix between users. Add users via:

```bash
# In .env:
TELEGRAM_ALLOWED_IDS=user1_id,user2_id

# Or via Telegram (owner only):
/allow 123456789
```

### Reconnection

Tailscale handles all reconnection automatically:
- Network changes (WiFi → mobile → different WiFi)
- ISP outages (reconnects when internet returns)
- Machine reboots (systemd service starts automatically)

## Verify connectivity

From your cloud VM, test that you can reach everything:

```bash
# Direct Tailscale peers
sudo tailscale status

# Subnet-routed devices (e.g., your router)
ping 192.168.8.1

# From inside the TokioAI container
docker exec tokio-agent python3 -c "
import socket
for ip, port, name in [('192.168.8.1', 22, 'Router'), ('100.x.x.x', 22, 'Raspi')]:
    try:
        s=socket.socket(); s.settimeout(3); s.connect((ip, port))
        print(f'{name}: OK'); s.close()
    except: print(f'{name}: FAIL')
"
```

## Cost

- **Tailscale**: Free (up to 100 devices)
- **Cloud VM overhead**: ~15 MB RAM for tailscaled daemon
- **Container overhead**: ~88 MB RAM (tokio-agent + telegram bot)
- **Bandwidth**: Negligible (text commands, not video streams)
- **No additional GCP/AWS charges** for Tailscale traffic

## Security

- All traffic encrypted with WireGuard (Tailscale's underlying protocol)
- No ports exposed publicly for TokioAI — only Telegram webhook
- SSH keys mounted read-only in containers
- API credentials in `.env` (gitignored, never committed)
- Subnet routes require explicit approval in Tailscale admin console
- Each machine authenticated individually via Tailscale SSO

## Adding new hardware

To add a new device (another Raspberry Pi, Arduino gateway, sensor hub):

```bash
# On the new device:
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# Authenticate via the browser link
# The device immediately appears in your mesh — TokioAI can reach it
```

No firewall rules, no VPN configs, no DNS changes. Just `tailscale up`.
