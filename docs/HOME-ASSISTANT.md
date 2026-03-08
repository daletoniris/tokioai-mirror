# Home Assistant Integration

TokioAI integrates with [Home Assistant](https://www.home-assistant.io/) to control IoT devices through natural language via Telegram or CLI.

## Architecture

```
User (Telegram/CLI)
    |
TokioAI Agent (GCP / any host)
    |  REST API calls
    v
Home Assistant (local network / Tailscale mesh)
    |
Smart Devices (lights, switches, vacuum, Alexa, sensors)
```

TokioAI communicates with Home Assistant via its REST API. When deployed on a remote server (e.g., GCP), connectivity is provided through a [Tailscale mesh VPN](./TAILSCALE-MESH.md) — no ports exposed to the internet.

## Prerequisites

1. **Home Assistant** installed and running (Docker recommended)
2. **Long-Lived Access Token** from HA (Profile > Security > Long-Lived Access Tokens)
3. **Network connectivity** between TokioAI and HA (local network or Tailscale)

## Setup

### 1. Deploy Home Assistant (Docker)

```bash
docker run -d \
  --name homeassistant \
  --restart=unless-stopped \
  --network=host \
  --privileged \
  --stop-timeout 120 \
  -e TZ=America/Buenos_Aires \
  -v /path/to/ha-config:/config \
  -v /run/dbus:/run/dbus:ro \
  ghcr.io/home-assistant/home-assistant:stable
```

**Important flags:**
- `--stop-timeout 120` — Gives HA enough time to flush its SQLite database on shutdown. Without this, configuration changes made through the UI may be lost on restart (default is only 10 seconds).
- `--network=host` — Required for device discovery (mDNS, SSDP).
- `-v /path/to/ha-config:/config` — Persistent config directory. All settings, automations, and database are stored here.

### 2. Initial Configuration

After first start, open `http://<your-ip>:8123` and complete the onboarding wizard:
- Set your location, timezone, and **unit system** (metric recommended)
- Create your admin account
- Add device integrations (Tuya, Alexa Media Player, etc.)

### 3. Generate Access Token

1. Go to your HA profile (bottom-left corner)
2. Scroll to **Long-Lived Access Tokens**
3. Click **Create Token**, name it `tokioai`
4. Copy the token — you won't see it again

### 4. Configure TokioAI

Add to your `.env` file:

```env
HOME_ASSISTANT_URL=http://<ha-ip>:8123
HOME_ASSISTANT_TOKEN=<your-long-lived-token>
```

If TokioAI runs on a different network (e.g., GCP), use the Tailscale IP:

```env
HOME_ASSISTANT_URL=http://<tailscale-ip>:8123
```

### 5. Restart TokioAI

```bash
docker compose down && docker compose up -d
```

## Device Whitelist

TokioAI uses a **strict device whitelist** to prevent accidental control of unintended devices. Only explicitly listed entities can be queried or controlled.

### Why a whitelist?

Without a whitelist, the agent could attempt to interact with any HA entity — including internal system entities, configuration switches, or devices that shouldn't be automated. This caused instability in earlier versions.

### Configuring allowed devices

Edit `tokio_agent/engine/tools/builtin/iot_tools.py`:

```python
# PRIMARY_DEVICES: the real devices (what gets listed/reported)
PRIMARY_DEVICES = {
    "light.smart_bulb":                          "Kitchen Lamp",
    "light.smart_bulb_2":                        "Living Room",
    "switch.my_smart_plug":                      "Smart Plug",
    "media_player.alexa":                        "Alexa",
    "vacuum.my_robot":                           "Robot Vacuum",
}

# ALLOWED_ENTITY_IDS: full set including useful sub-entities
ALLOWED_ENTITY_IDS = {
    *PRIMARY_DEVICES.keys(),
    "sensor.temperature",              # read-only sensor
    "sensor.my_robot_battery",         # vacuum battery
    "select.my_robot_mode",            # vacuum mode
}
```

**To find your entity IDs:**
1. Go to HA > Settings > Devices & Services > Entities
2. Or use the API: `curl -s http://<ha-ip>:8123/api/states -H "Authorization: Bearer <token>" | python3 -m json.tool`

### Adding a new device

1. Add the `entity_id` to `PRIMARY_DEVICES` (with a friendly name) or `ALLOWED_ENTITY_IDS`
2. Rebuild and restart TokioAI
3. Test: ask TokioAI to check the device status

## Supported Device Types

| Type | Actions | Example |
|------|---------|---------|
| **Lights** | on, off, toggle, brightness, color | "Turn on the kitchen light in blue" |
| **Switches** | on, off, toggle | "Turn off the kitchen plug" |
| **Vacuum** | start, stop, pause, return_to_base, locate | "Start the vacuum" |
| **Media Player** | speak (TTS), play music, volume, status | "Tell Alexa to play jazz" |
| **Sensors** | read state | "What's the temperature?" |

## Troubleshooting

### Changes in HA UI don't persist after restart

**Cause:** Docker's default stop timeout (10s) is too short for HA to flush its SQLite WAL journal.

**Fix:** Always use `--stop-timeout 120` when creating the container:

```bash
docker run -d --stop-timeout 120 ...
```

If the container already exists, recreate it:

```bash
docker stop -t 60 homeassistant
docker rm homeassistant
docker run -d --stop-timeout 120 ... # full command above
```

### Connection refused from TokioAI container

**Cause:** TokioAI container can't reach HA on the network.

**Fixes:**
- If same host: use `http://host.docker.internal:8123` or `--network=host`
- If remote (via Tailscale): use the Tailscale IP (`100.x.x.x`)
- Verify HA is listening on all interfaces: `ss -tlnp | grep 8123` should show `0.0.0.0:8123`

### Unit system shows Fahrenheit instead of Celsius

Change in `.storage/core.config`:

```bash
# Edit the file
sudo python3 -c "
import json
path = '/path/to/ha-config/.storage/core.config'
with open(path) as f:
    data = json.load(f)
data['data']['unit_system_v2'] = 'metric'
with open(path, 'w') as f:
    json.dump(data, f, indent=2)
"
# Restart HA
docker restart homeassistant
```

### Device shows "unknown" state

- The device may be offline or not paired with HA
- Check HA UI > Settings > Devices for the device status
- Try power-cycling the device

## Security Notes

- The HA token is stored only in `.env` (gitignored, never committed)
- Communication between TokioAI and HA goes through Tailscale (WireGuard-encrypted) or local network
- The device whitelist prevents the agent from interacting with unauthorized entities
- No HA ports are exposed to the public internet
