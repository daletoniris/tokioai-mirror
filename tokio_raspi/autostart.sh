#!/bin/bash
# TokioAI Entity Autostart — screen rotate + entity launch
# Called from labwc autostart (needs Wayland compositor ready)
#
# Setup (run once on Raspi):
#   mkdir -p ~/.config/labwc
#   echo 'sleep 8 && /home/mrmoz/tokio_autostart.sh &' > ~/.config/labwc/autostart
#   chmod +x ~/.config/labwc/autostart
#   cp autostart.sh ~/tokio_autostart.sh && chmod +x ~/tokio_autostart.sh
#
# The drone safety proxy runs as systemd service (tokio-drone-proxy.service)

LOG="/tmp/tokio_entity.log"
LOCKFILE="/tmp/tokio_entity.lock"

# ── Prevent double-launch ──────────────────────────────────────────
if [ -f "$LOCKFILE" ]; then
    OLD_PID=$(cat "$LOCKFILE" 2>/dev/null)
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "[$(date)] Entity already running (PID $OLD_PID), aborting" >> "$LOG"
        exit 0
    fi
    rm -f "$LOCKFILE"
fi

echo "[$(date)] TokioAI autostart begin" > "$LOG"

# ── Wait for HDMI and rotate screen ─────────────────────────────────
for i in 1 2 3; do
    STATUS=$(cat /sys/class/drm/card1-HDMI-A-2/status 2>/dev/null)
    [ "$STATUS" = "connected" ] && break
    echo "[Tokio] HDMI not detected, forcing hotplug ($i)..." >> "$LOG"
    sudo sh -c 'echo on > /sys/class/drm/card1-HDMI-A-2/status' 2>/dev/null
    sleep 3
done

for i in 1 2 3 4 5; do
    OUTPUT=$(wlr-randr 2>&1)
    echo "$OUTPUT" | grep -q "HDMI-A-2" && break
    echo "[Tokio] Waiting for HDMI-A-2 in compositor ($i)..." >> "$LOG"
    sleep 3
done

wlr-randr --output HDMI-A-2 --transform 270 2>>"$LOG" && \
    echo "[Tokio] Screen rotated 270" >> "$LOG"

# ── Kill any existing entity (NOT drone proxy!) ─────────────────────
# SAFE pattern: only kills the entity main module, NOT drone_safety_proxy
pkill -f 'tokio_raspi\.__main__' 2>/dev/null || true
pkill -f 'tokio_raspi --api' 2>/dev/null || true
sleep 2
sudo fuser -k 5000/tcp 2>/dev/null || true
# Kill leftover gatttool (health monitor)
pkill -f gatttool 2>/dev/null || true

# ── Reset wlan1 for WiFi defense monitor mode ───────────────────────
if ip link show wlan1 &>/dev/null; then
    echo "[Tokio] Resetting wlan1 for monitor mode..." >> "$LOG"
    sudo rmmod rtl8xxxu 2>/dev/null || true
    sleep 1
    sudo modprobe rtl8xxxu 2>/dev/null || true
    sleep 2
    echo "[Tokio] wlan1 driver reloaded" >> "$LOG"
fi

# ── Generate Home Assistant token ────────────────────────────────────
HA_TOKEN=$(sudo python3 -c "
import jwt, time, json
try:
    with open('/home/mrmoz/homeassistant/.storage/auth') as f:
        data = json.load(f)
    for t in data.get('data', {}).get('refresh_tokens', []):
        if t.get('client_name') == 'TokioAI':
            now = int(time.time())
            payload = {'iss': t['id'], 'iat': now, 'exp': now + 315360000}
            print(jwt.encode(payload, t['jwt_key'], algorithm='HS256'))
            break
except Exception as e:
    print('', end='')
" 2>/dev/null)

# ── Launch entity ────────────────────────────────────────────────────
export SDL_VIDEODRIVER=wayland
export PYTHONUNBUFFERED=1
export TOKIO_HA_TOKEN="$HA_TOKEN"

# Load secrets from env file (not committed to git)
[ -f /home/mrmoz/.tokio_env ] && source /home/mrmoz/.tokio_env

cd /home/mrmoz
echo "[$(date)] Launching entity..." >> "$LOG"

# Write PID lockfile
echo $$ > "$LOCKFILE"
exec python3 -u -m tokio_raspi --api >> "$LOG" 2>&1
