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

### LocalTuya (Local Control)

Tuya smart devices are controlled locally via [LocalTuya](https://github.com/xZetsubou/hass-localtuya) — a custom HA integration that communicates directly with devices on the LAN, bypassing the Tuya cloud entirely.

**Benefits over Tuya Cloud:**
- No cloud dependency — works even if Tuya servers are down
- No "sign invalid" token errors
- Faster response times (direct LAN communication)
- No internet required for device control

**Requirements:**
- Tuya device local keys (extracted via `tinytuya` or `tuya_sharing`)
- Devices must be on the same network as Home Assistant
- Device IPs (discovered via `tinytuya.deviceScan()`)

See [LocalTuya setup details](#localtuya-setup) below.

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

## LocalTuya Setup

LocalTuya replaces the Tuya Cloud integration for local-only device control.

### 1. Install LocalTuya

Download [hass-localtuya](https://github.com/xZetsubou/hass-localtuya) and extract to `<ha-config>/custom_components/localtuya/`.

### 2. Extract Device Local Keys

```python
# Using tuya_sharing (requires a valid Tuya Cloud account)
from tuya_sharing import Manager, LoginControl

m = Manager("", "", "", "", "")
m.terminal_id = "your_terminal_id"
m.token_info = {...}  # from existing HA Tuya config entry
m.update_device_cache()
for dev in m.device_map.values():
    print(f"{dev.name}: id={dev.id} key={dev.local_key}")
```

Or use `tinytuya wizard` for an interactive approach.

### 3. Discover Device IPs

```python
import tinytuya
devices = tinytuya.deviceScan(verbose=False, maxretry=3)
for ip, dev in devices.items():
    print(f"{ip}: id={dev['gwId']}, ver={dev['version']}")
```

### 4. Add Integration in HA

Go to Settings > Integrations > Add > LocalTuya. Choose "No Cloud" mode for fully local operation.

Add each device with:
- **Device ID** and **Local Key** (from step 2)
- **IP address** (from step 3)
- **Protocol version** (3.3 for most devices, 3.4 for newer plugs)

### 5. Configure Entity Types

For each device, select the appropriate platform:
- **Lights**: DPS 20 (switch), 22 (brightness), 23 (color temp), 24 (color HSV)
- **Switches**: DPS 1 (on/off)
- **Vacuum**: DPS 5 (status), 1 (start), 2 (pause), 4 (mode), 9 (fan speed)

### 6. Remove Tuya Cloud

Once LocalTuya is confirmed working, remove the Tuya Cloud integration to avoid "sign invalid" errors and duplicate entities.

## Alexa Integration

Alexa is controlled via [alexa_media_player](https://github.com/alandtse/alexa_media_player), a custom HA integration.

### Music Playback

TokioAI uses a 3-method fallback for reliable music playback:

1. **`notify/alexa_media` with TTS** — Sends a voice command like "play jazz on Amazon Music" (most accurate)
2. **`notify/alexa_media` with ANNOUNCE** — Sends as an announcement command
3. **`media_player/play_media` with AMAZON_MUSIC** — Direct media type fallback

This approach gives much better results than the generic `play_media` method alone, since it interprets the query the same way Alexa would interpret a voice command.

## Security Notes

- The HA token is stored only in `.env` (gitignored, never committed)
- Communication between TokioAI and HA goes through Tailscale (WireGuard-encrypted) or local network
- The device whitelist prevents the agent from interacting with unauthorized entities
- No HA ports are exposed to the public internet
- LocalTuya communicates only on the local network — no data leaves to cloud
