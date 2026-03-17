# Drone Security Setup — Ekoparty Demo

## Architecture

```
Telegram/CLI → TokioAI (GCP) → Tailscale (encrypted) → Raspi → WiFi → Tello
                                                          ↑
                                                    SAFETY PROXY
                                               (geofence, auth, kill switch)
```

**Nobody at the conference can control the drone** because:
1. The Tello WiFi is password-protected (station mode) or isolated
2. The Safety Proxy only accepts commands from Tailscale IPs
3. All commands are validated against geofence before execution
4. Kill switch can instantly stop all motors

## Network Options

### Option A: Raspi as WiFi Bridge (simplest)
The Raspi connects to the Tello's WiFi for drone control, and uses
Ethernet or a second WiFi adapter for Tailscale/internet.

```
Internet ← [Ethernet] ← Raspi → [WiFi] → Tello AP
                           ↑
                      Tailscale (via ethernet)
```

**Setup:**
```bash
# On Raspi: connect to Tello WiFi
nmcli device wifi connect "TELLO-XXXXXX" password ""

# Tailscale stays connected via ethernet
# The Raspi routes drone commands from Tailscale to Tello WiFi
```

### Option B: Tello Station Mode (more secure)
Put the Tello in station mode — it connects to YOUR WiFi network
instead of creating its own AP.

```
Your WiFi Router
  ├── Raspi (ethernet or WiFi)
  └── Tello (station mode)
```

**Setup:**
```python
from djitellopy import Tello
tello = Tello()
tello.connect()
tello.set_wifi_credentials("YOUR_WIFI_SSID", "YOUR_WIFI_PASSWORD")
# Tello reboots and connects to your WiFi
# Find its new IP with: nmap -sn 192.168.8.0/24
```

### Option C: Raspi as AP (most secure for demo)
Raspi creates its own hidden WiFi network. Only the Tello connects to it.

```bash
# On Raspi: create hidden AP
sudo nmcli connection add type wifi ifname wlan0 con-name TokioNet \
    autoconnect yes ssid "TokioNet" \
    wifi.hidden yes \
    wifi.mode ap \
    ipv4.method shared \
    wifi-sec.key-mgmt wpa-psk \
    wifi-sec.psk "STRONG_PASSWORD_HERE"
```

## Running the Safety Proxy

```bash
# On the Raspi:
cd /home/mrmoz
python3 -m tokio_raspi.drone_safety_proxy

# Or with custom safety level:
DRONE_SAFETY_LEVEL=demo python3 -m tokio_raspi.drone_safety_proxy
```

API runs on port 5001. Test:
```bash
curl http://localhost:5001/drone/status
curl -X POST http://localhost:5001/drone/command \
     -H "Content-Type: application/json" \
     -d '{"command": "status", "params": {}}'
```

## Safety Levels

| Setting | DEMO | NORMAL | EXPERT |
|:--------|:-----|:-------|:-------|
| Max height | 1.5m | 3m | 5m |
| Max distance | 3m | 10m | 20m |
| Max speed | 40 cm/s | 80 cm/s | 100 cm/s |
| Min battery | 25% | 20% | 15% |
| Command timeout | 20s | 30s | 60s |
| Flips | blocked | allowed | allowed |
| Raw RC | blocked | blocked | allowed |

## Kill Switch

From anywhere (Telegram, CLI, API):
```
"Tokio, kill switch del drone"
→ TOOL:drone({"action": "kill"})
→ Motors stop instantly
```

Reset after emergency:
```
"Tokio, reset kill switch"
→ TOOL:drone({"action": "kill_reset"})
```

## Security Checklist for Demo

- [ ] Raspi has 5V/5A power supply
- [ ] Tello battery fully charged
- [ ] Safety level set to DEMO
- [ ] Test geofence with simulation first
- [ ] Kill switch tested and working
- [ ] Tailscale connected on Raspi
- [ ] No open ports on Raspi (only Tailscale)
- [ ] Drone area clear of obstacles
- [ ] Emergency landing zone identified
