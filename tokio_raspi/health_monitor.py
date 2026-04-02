"""
TokioAI Health Monitor — BLE smartwatch (MoYoung/Nexus) via gatttool.

Security-hardened for Ekoparty conference use:
- MAC whitelist (only accepts data from known watch)
- Data validation (physiologically possible ranges)
- Anti-spoofing (detects rapid impossible changes)
- Rate limiting (flood detection)
- BLE attack detection (replay, hijack)

Connects to a MoYoung V2 BLE watch using gatttool (address_type=public).

Captures via BLE notifications:
- Heart rate: Standard BLE HR Service (handle 0x005b, CCCD 0x005c)
- Blood pressure: Proprietary FEE3 channel (handle 0x0051), cmd 0x69
- Blood oxygen (SpO2): Proprietary FEE3 channel (handle 0x0051), cmd 0x6B
- Battery: Battery Service (handle 0x001a)

Protocol (reverse-engineered from MoYoung/CRPBle SDK):
  FEE3 notifications: FE EA 20 [len] [cmd] [sub] [data...]
  - cmd 0x69, len=8: Blood Pressure -> bytes[6]=systolic, bytes[7]=diastolic
  - cmd 0x6B, len=6: SpO2 -> bytes[4]=spo2_value (0xFF=measuring)
  - cmd 0x6D, len=6: HR echo -> bytes[4]=hr_value (0xFF=measuring)

Stores timestamped readings to ~/.tokio_health/health_log.json.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Callable

WATCH_ADDR = os.getenv("TOKIO_WATCH_ADDR", "")
WATCH_NAME = os.getenv("TOKIO_WATCH_NAME", "Nexus")

# BLE handles (MoYoung V2 / MOY-D695)
BATTERY_HANDLE = "0x001a"
HR_CCCD_HANDLE = "0x005c"
HR_VALUE_HANDLE = "0x005b"
FEE3_HANDLE = "0051"
FEE3_CCCD_HANDLE = "0x0052"  # CCCD for FEE3 notifications (BP/SpO2/steps)
FEE5_HANDLE = "0x0054"       # Write handle for sending commands to watch
STEPS_HANDLE = "004c"        # Steps/activity notification handle (FEE1)
STEPS_CCCD_HANDLE = "0x004d" # CCCD for FEE1 steps notifications
STEPS_META_HANDLE = "0046"   # Steps metadata notification handle (FEA1)
STEPS_META_CCCD_HANDLE = "0x0047"  # CCCD for FEA1

BATTERY_POLL_INTERVAL = 120  # re-subscribe + battery every 2 min
RECONNECT_INTERVAL = 20
DATA_TIMEOUT = 600  # 10 min without data -> force reconnect

HEALTH_DATA_DIR = os.getenv("TOKIO_HEALTH_DIR", os.path.expanduser("~/.tokio_health"))
HEALTH_LOG_FILE = os.path.join(HEALTH_DATA_DIR, "health_log.json")
MAX_LOG_ENTRIES = 2000


@dataclass
class HealthData:
    heart_rate: int = 0
    blood_pressure_sys: int = 0
    blood_pressure_dia: int = 0
    spo2: int = 0
    steps: int = 0
    calories: int = 0
    distance: int = 0
    battery: int = -1
    connected: bool = False
    last_hr_time: float = 0.0
    last_bp_time: float = 0.0
    last_spo2_time: float = 0.0
    last_steps_time: float = 0.0
    last_update: float = 0.0
    watch_name: str = ""


class BLESecurity:
    """BLE security layer — anti-spoofing, rate limiting, attack detection."""

    def __init__(self, allowed_mac: str):
        self._allowed_mac = allowed_mac.upper()
        self._lock = threading.Lock()

        # Rate limiting
        self._notification_times: list[float] = []
        self._max_notifications_per_sec = 20  # normal BLE is ~1-5/sec
        self._flood_detected = False
        self._flood_start: float = 0

        # Anti-spoofing: track value changes
        self._last_hr: int = 0
        self._last_hr_time: float = 0
        self._hr_change_threshold = 50  # bpm change in < 2 seconds = suspicious
        self._last_bp_sys: int = 0
        self._last_bp_time: float = 0

        # Attack detection
        self._replay_hashes: list[int] = []  # hash of last N payloads
        self._replay_window = 50  # track last 50 payloads
        self._replay_threshold = 10  # same payload 10+ times = replay
        self._spoofing_attempts = 0
        self._flooding_events = 0
        self._replay_attacks = 0
        self._last_attack_time: float = 0

        # Connection tracking
        self._connect_times: list[float] = []
        self._rapid_reconnect_threshold = 5  # 5 connects in 60s = hijack attempt

        self._callback: Optional[Callable] = None

    def set_callback(self, callback: Callable):
        self._callback = callback

    def check_mac(self, mac: str) -> bool:
        """Verify MAC address matches whitelist."""
        if mac.upper() != self._allowed_mac:
            with self._lock:
                self._spoofing_attempts += 1
                self._last_attack_time = time.time()
            self._alert("mac_spoof", f"BLE MAC spoof attempt: {mac} (expected {self._allowed_mac})")
            return False
        return True

    def check_rate(self) -> bool:
        """Rate limiting — detect notification flooding."""
        now = time.time()
        with self._lock:
            self._notification_times.append(now)
            # Keep only last 2 seconds
            self._notification_times = [t for t in self._notification_times if now - t < 2.0]

            rate = len(self._notification_times) / 2.0
            if rate > self._max_notifications_per_sec:
                if not self._flood_detected:
                    self._flood_detected = True
                    self._flood_start = now
                    self._flooding_events += 1
                    self._last_attack_time = now
                    self._alert("ble_flood", f"BLE notification flood: {rate:.0f}/sec")
                return False
            else:
                self._flood_detected = False
        return True

    def check_replay(self, raw_bytes: list[int]) -> bool:
        """Detect replay attacks — same payload repeated many times."""
        payload_hash = hash(tuple(raw_bytes))
        with self._lock:
            self._replay_hashes.append(payload_hash)
            if len(self._replay_hashes) > self._replay_window:
                self._replay_hashes = self._replay_hashes[-self._replay_window:]

            # Count occurrences of this hash
            count = self._replay_hashes.count(payload_hash)
            if count >= self._replay_threshold:
                self._replay_attacks += 1
                self._last_attack_time = time.time()
                self._alert("ble_replay", f"BLE replay attack: same payload {count} times")
                return False
        return True

    def validate_hr(self, hr: int) -> bool:
        """Validate heart rate is physiologically possible."""
        if not (30 <= hr <= 220):
            return False
        now = time.time()
        with self._lock:
            # Check for impossible rapid change
            if self._last_hr > 0 and self._last_hr_time > 0:
                dt = now - self._last_hr_time
                if dt < 2.0 and abs(hr - self._last_hr) > self._hr_change_threshold:
                    self._spoofing_attempts += 1
                    self._last_attack_time = now
                    self._alert("hr_spoof",
                                f"Suspicious HR change: {self._last_hr}->{hr} in {dt:.1f}s")
                    return False
            self._last_hr = hr
            self._last_hr_time = now
        return True

    def validate_bp(self, sys_bp: int, dia_bp: int) -> bool:
        """Validate blood pressure is physiologically possible."""
        if not (50 <= sys_bp <= 250 and 30 <= dia_bp <= 150):
            return False
        if dia_bp >= sys_bp:
            return False  # diastolic must be < systolic
        return True

    def validate_spo2(self, spo2: int) -> bool:
        """Validate SpO2 is physiologically possible."""
        return 70 <= spo2 <= 100  # below 70 = dead, above 100 = impossible

    def track_connection(self):
        """Track connection events for hijack detection."""
        now = time.time()
        with self._lock:
            self._connect_times.append(now)
            self._connect_times = [t for t in self._connect_times if now - t < 60]
            if len(self._connect_times) >= self._rapid_reconnect_threshold:
                self._last_attack_time = now
                self._alert("ble_hijack",
                            f"Rapid BLE reconnects: {len(self._connect_times)} in 60s — possible hijack")

    def get_status(self) -> dict:
        """Get security status summary."""
        with self._lock:
            return {
                "spoofing_attempts": self._spoofing_attempts,
                "flooding_events": self._flooding_events,
                "replay_attacks": self._replay_attacks,
                "flood_active": self._flood_detected,
                "last_attack_time": self._last_attack_time,
                "allowed_mac": self._allowed_mac,
            }

    def _alert(self, attack_type: str, message: str):
        """Send security alert."""
        print(f"[BLESec] ALERT: {message}")
        if self._callback:
            try:
                self._callback("ble_attack", {"type": attack_type, "details": message})
            except Exception:
                pass


class HealthMonitor:
    """BLE health monitor using gatttool — captures HR, BP, SpO2."""

    def __init__(self, watch_addr: str = WATCH_ADDR):
        self._addr = watch_addr
        self._data = HealthData(watch_name=WATCH_NAME)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._callback: Optional[Callable] = None
        self._available = False
        self._log: list[dict] = []
        self._log_lock = threading.Lock()
        self._last_data_time = 0

        # Security
        self._security = BLESecurity(watch_addr)

        try:
            result = subprocess.run(["which", "gatttool"], capture_output=True, timeout=5)
            self._available = result.returncode == 0
        except Exception:
            pass

        if not self._available:
            print("[Health] gatttool not found — health monitor disabled")
        else:
            self._load_log()

    @property
    def available(self) -> bool:
        return self._available

    @property
    def data(self) -> HealthData:
        return self._data

    @property
    def security(self) -> BLESecurity:
        return self._security

    def start(self, callback: Optional[Callable] = None):
        if not self._available or self._running:
            return
        self._callback = callback
        self._security.set_callback(callback)
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        print(f"[Health] Started — watching for {self._addr} (MAC whitelisted)")

    def stop(self):
        self._running = False

    def get_summary(self) -> dict:
        d = self._data
        summary = {
            "connected": d.connected,
            "heart_rate": d.heart_rate,
            "bp_sys": d.blood_pressure_sys,
            "bp_dia": d.blood_pressure_dia,
            "spo2": d.spo2,
            "steps": d.steps,
            "calories": d.calories,
            "battery": d.battery,
            "watch": d.watch_name,
            "last_hr_age": round(time.time() - d.last_hr_time, 1) if d.last_hr_time else None,
            "security": self._security.get_status(),
        }
        return summary

    def get_health_context(self) -> str:
        """Build health context string for the AI brain."""
        d = self._data
        if not d.connected and d.last_update == 0:
            return ""

        parts = []
        now = time.time()

        if d.heart_rate > 0:
            age = now - d.last_hr_time
            if age < 120:
                parts.append(f"HR: {d.heart_rate} bpm")
            else:
                parts.append(f"Ultimo HR: {d.heart_rate} bpm (hace {int(age // 60)} min)")

        if d.blood_pressure_sys > 0:
            age = now - d.last_bp_time
            if age < 300:
                parts.append(f"Presion: {d.blood_pressure_sys}/{d.blood_pressure_dia} mmHg")
            else:
                parts.append(f"Ultima presion: {d.blood_pressure_sys}/{d.blood_pressure_dia} (hace {int(age // 60)} min)")
            if d.blood_pressure_sys > 140 or d.blood_pressure_dia > 90:
                parts.append("Presion elevada!")
            elif d.blood_pressure_sys < 90:
                parts.append("Presion baja")

        if d.spo2 > 0:
            age = now - d.last_spo2_time
            if age < 300:
                parts.append(f"SpO2: {d.spo2}%")
            else:
                parts.append(f"Ultimo SpO2: {d.spo2}% (hace {int(age // 60)} min)")
            if d.spo2 < 95:
                parts.append("SpO2 bajo — verificar oxigenacion")

        if d.steps > 0:
            parts.append(f"Pasos hoy: {d.steps}")
            if d.calories > 0:
                parts.append(f"Calorias: {d.calories} kcal")
            if d.distance > 0:
                parts.append(f"Distancia: {d.distance}m")

        if d.battery >= 0:
            parts.append(f"Bateria reloj: {d.battery}%")

        stats = self._compute_stats("hr", 3600)
        if stats:
            parts.append(f"HR 1h: min={stats['min']} max={stats['max']} avg={stats['avg']} ({stats['count']} lecturas)")
            if stats["avg"] > 100:
                parts.append("HR elevado — posible estres o actividad fisica")
            elif stats["avg"] < 55:
                parts.append("HR bajo — en reposo")

        daily = self._compute_stats("hr", 86400)
        if daily and daily["count"] > 5:
            parts.append(f"HR hoy: min={daily['min']} max={daily['max']} avg={daily['avg']}")

        # Security status
        sec = self._security.get_status()
        if sec["spoofing_attempts"] > 0 or sec["replay_attacks"] > 0:
            parts.append(f"BLE Security: {sec['spoofing_attempts']} spoof attempts, {sec['replay_attacks']} replay attacks")

        if not parts:
            return "Salud: reloj " + ("conectado, esperando datos" if d.connected else "desconectado") + "."

        return "Salud (smartwatch): " + ". ".join(parts) + "."

    def _compute_stats(self, key: str, window_seconds: float) -> Optional[dict]:
        cutoff = time.time() - window_seconds
        with self._log_lock:
            readings = [e[key] for e in self._log if e.get(key, 0) > 0 and e["ts"] > cutoff]
        if not readings:
            return None
        return {
            "min": min(readings),
            "max": max(readings),
            "avg": round(sum(readings) / len(readings)),
            "count": len(readings),
        }

    def _log_reading(self, **kwargs):
        entry = {"ts": time.time()}
        entry.update({k: v for k, v in kwargs.items() if v and v > 0})
        if len(entry) <= 1:
            return
        with self._log_lock:
            self._log.append(entry)
            if len(self._log) > MAX_LOG_ENTRIES:
                self._log = self._log[-MAX_LOG_ENTRIES:]
        self._save_log()

    def _load_log(self):
        try:
            if os.path.exists(HEALTH_LOG_FILE):
                with open(HEALTH_LOG_FILE, "r") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self._log = data[-MAX_LOG_ENTRIES:]
                    print(f"[Health] Loaded {len(self._log)} historical readings")
        except Exception as e:
            print(f"[Health] Log load error: {e}")

    def _save_log(self):
        try:
            os.makedirs(HEALTH_DATA_DIR, exist_ok=True)
            with self._log_lock:
                data = list(self._log)
            with open(HEALTH_LOG_FILE, "w") as f:
                json.dump(data, f)
        except Exception as e:
            print(f"[Health] Log save error: {e}")

    # -- Main loop --

    def _reset_ble_adapter(self):
        """Reset BLE adapter when it gets stuck (I/O errors)."""
        try:
            print("[Health] Resetting BLE adapter (hci0)...")
            subprocess.run(["sudo", "hciconfig", "hci0", "reset"],
                           capture_output=True, timeout=5)
            time.sleep(1)
            subprocess.run(["sudo", "hciconfig", "hci0", "up"],
                           capture_output=True, timeout=5)
            time.sleep(2)
            print("[Health] BLE adapter reset complete")
        except Exception as e:
            print(f"[Health] BLE reset failed: {e}")

    def _run_loop(self):
        """Interactive gatttool with stdbuf line buffering — subscribes HR + FEE3 + keep-alive."""
        import select as _select
        consecutive_failures = 0
        self._last_data_time = time.time()

        while self._running:
            proc = None
            try:
                if consecutive_failures > 0 and consecutive_failures % 5 == 0:
                    self._reset_ble_adapter()

                self._security.track_connection()

                # stdbuf -oL forces line buffering (gatttool uses block buffer in pipe mode)
                proc = subprocess.Popen(
                    ["stdbuf", "-oL", "gatttool", "-b", self._addr, "-t", "public", "-I"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True
                )

                print("[Health] Connecting to watch...")
                proc.stdin.write("connect\n")
                proc.stdin.flush()

                connected = False
                subscribed = False
                last_keepalive = 0

                while self._running:
                    try:
                        ready, _, _ = _select.select([proc.stdout], [], [], 3.0)
                        if ready:
                            line = proc.stdout.readline()
                            if not line:
                                print("[Health] Connection lost (EOF)")
                                break
                            line = line.strip()

                            # Strip ANSI escape codes
                            line = re.sub(r'\x1b\[[0-9;]*m', '', line)
                            # Skip empty lines and prompt echoes
                            if not line or line.endswith(">") or line.startswith("Attempting"):
                                continue

                            if "Connection successful" in line and not connected:
                                connected = True
                                consecutive_failures = 0
                                self._data.connected = True
                                self._last_data_time = time.time()
                                print("[Health] Connected — subscribing to HR + FEE3...")

                            elif "written successfully" in line.lower():
                                pass  # normal — subscribe confirmation

                            elif "Notification" in line and "value" in line.lower():
                                self._last_data_time = time.time()
                                self._parse_notification(line)

                            elif "Characteristic value/descriptor:" in line:
                                # Battery read response
                                match = re.search(r'descriptor:\s*([0-9a-fA-F ]+)', line)
                                if match:
                                    raw = [int(b, 16) for b in match.group(1).strip().split()]
                                    if raw and 0 <= raw[0] <= 100:
                                        self._data.battery = raw[0]
                                        self._data.last_update = time.time()
                                        self._log_reading(bat=raw[0])
                                        print(f"[Health] Battery: {raw[0]}%")

                            elif "disconnect" in line.lower():
                                print(f"[Health] Disconnected: {line}")
                                break

                        # After connection, send subscribe commands (once)
                        if connected and not subscribed:
                            time.sleep(0.5)
                            # Subscribe to ALL notification handles
                            for cccd in (HR_CCCD_HANDLE, FEE3_CCCD_HANDLE,
                                         STEPS_CCCD_HANDLE, STEPS_META_CCCD_HANDLE):
                                proc.stdin.write(f"char-write-req {cccd} 0100\n")
                                proc.stdin.flush()
                                time.sleep(0.3)
                            # Read battery
                            proc.stdin.write(f"char-read-hnd {BATTERY_HANDLE}\n")
                            proc.stdin.flush()
                            subscribed = True  # set immediately to prevent re-sending
                            print("[Health] Subscribed to HR + FEE3 + Steps — listening...")

                        # Keep-alive every 5 min: re-subscribe + read battery
                        now = time.time()
                        if subscribed and now - last_keepalive > BATTERY_POLL_INTERVAL:
                            last_keepalive = now
                            try:
                                # Re-subscribe all CCCDs (watch may drop notification state)
                                for cccd in (HR_CCCD_HANDLE, FEE3_CCCD_HANDLE,
                                             STEPS_CCCD_HANDLE, STEPS_META_CCCD_HANDLE):
                                    proc.stdin.write(f"char-write-req {cccd} 0100\n")
                                    proc.stdin.flush()
                                    time.sleep(0.2)
                                proc.stdin.write(f"char-read-hnd {BATTERY_HANDLE}\n")
                                proc.stdin.flush()
                            except Exception:
                                break

                        # Data watchdog: no notifications for 10 min -> force reconnect
                        if subscribed and now - self._last_data_time > DATA_TIMEOUT:
                            print(f"[Health] No data for {DATA_TIMEOUT}s — forcing reconnect")
                            break

                    except Exception as e:
                        print(f"[Health] Read error: {e}")
                        break

                    if proc.poll() is not None:
                        print("[Health] gatttool process exited")
                        break

                self._data.connected = False
                self._kill_proc(proc)
                if not connected:
                    consecutive_failures += 1
                print(f"[Health] Reconnecting in {RECONNECT_INTERVAL}s... (failures: {consecutive_failures})")
                time.sleep(RECONNECT_INTERVAL)

            except Exception as e:
                print(f"[Health] Error: {e}")
                self._data.connected = False
                consecutive_failures += 1
                if proc:
                    self._kill_proc(proc)
                time.sleep(RECONNECT_INTERVAL)

    def _kill_proc(self, proc):
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    # -- Notification parsing --

    def _parse_notification(self, line: str):
        """Parse any BLE notification — HR, BP, SpO2, Battery."""
        match = re.search(r'handle\s*=\s*0x([0-9a-fA-F]+)\s+value:\s*([0-9a-fA-F ]+)', line)
        if not match:
            return

        handle = match.group(1).lower()
        hex_bytes = match.group(2).strip().split()
        raw = [int(b, 16) for b in hex_bytes]

        # Rate limiting check
        if not self._security.check_rate():
            return

        # Replay attack check
        if not self._security.check_replay(raw):
            return

        # Standard HR (handle 0x005b)
        if handle == HR_VALUE_HANDLE.replace("0x", ""):
            self._handle_hr(raw)

        # Battery (handle 0x001a)
        elif handle == BATTERY_HANDLE.replace("0x", ""):
            if raw:
                bat = raw[0]
                if 0 <= bat <= 100:
                    self._data.battery = bat
                    self._data.last_update = time.time()
                    self._log_reading(bat=bat)
                    print(f"[Health] Battery: {bat}%")

        # FEE3 proprietary sensor data (handle 0x0051)
        elif handle == FEE3_HANDLE:
            self._handle_fee3(raw)

        # Steps/activity (handle 0x004c) — direct step count
        elif handle == STEPS_HANDLE:
            self._handle_steps_direct(raw)

        # Steps metadata (handle 0x0046) — prefixed with 0x07
        elif handle == STEPS_META_HANDLE:
            if raw and raw[0] == 0x07 and len(raw) >= 8:
                self._handle_steps_direct(raw[1:])

        # Silently ignore other handles (prompts, echoes, etc.)
        else:
            pass

    def _handle_hr(self, raw: list[int]):
        """Parse standard BLE HR Measurement."""
        if len(raw) < 2:
            return
        flags = raw[0]
        if flags & 0x01:
            hr = raw[1] | (raw[2] << 8) if len(raw) >= 3 else 0
        else:
            hr = raw[1]

        # Security validation
        if not self._security.validate_hr(hr):
            return

        self._data.heart_rate = hr
        self._data.last_hr_time = time.time()
        self._data.last_update = time.time()
        self._data.connected = True
        self._log_reading(hr=hr)
        print(f"[Health] HR: {hr} bpm")
        if self._callback:
            try:
                self._callback("heart_rate", hr)
            except Exception:
                pass

    def _handle_fee3(self, raw: list[int]):
        """Parse MoYoung FEE3 proprietary health data."""
        if len(raw) < 5:
            return
        if raw[0] != 0xFE or raw[1] != 0xEA or raw[2] != 0x20:
            return

        pkt_len = raw[3]
        cmd = raw[4]

        # Steps/Activity
        if cmd == 0x51 and len(raw) >= 8:
            self._handle_steps_fee3(raw)

        # Blood Pressure
        elif cmd == 0x69 and pkt_len >= 8 and len(raw) >= 8:
            sys_bp = raw[6]
            dia_bp = raw[7]
            if sys_bp != 0xFF and dia_bp != 0xFF:
                if not self._security.validate_bp(sys_bp, dia_bp):
                    return
                self._data.blood_pressure_sys = sys_bp
                self._data.blood_pressure_dia = dia_bp
                self._data.last_bp_time = time.time()
                self._data.last_update = time.time()
                self._log_reading(bp_sys=sys_bp, bp_dia=dia_bp)
                print(f"[Health] BP: {sys_bp}/{dia_bp} mmHg")
                if self._callback:
                    try:
                        self._callback("blood_pressure", (sys_bp, dia_bp))
                    except Exception:
                        pass

        # SpO2
        elif cmd == 0x6B and len(raw) >= 6:
            spo2 = raw[5]
            if spo2 != 0xFF:
                if not self._security.validate_spo2(spo2):
                    return
                self._data.spo2 = spo2
                self._data.last_spo2_time = time.time()
                self._data.last_update = time.time()
                self._log_reading(spo2=spo2)
                print(f"[Health] SpO2: {spo2}%")
                if self._callback:
                    try:
                        self._callback("spo2", spo2)
                    except Exception:
                        pass

        # HR echo via FEE3
        elif cmd == 0x6D and len(raw) >= 6:
            hr = raw[5]
            if hr != 0xFF and self._security.validate_hr(hr):
                self._data.heart_rate = hr
                self._data.last_hr_time = time.time()
                self._data.last_update = time.time()
                print(f"[Health] HR (FEE3): {hr} bpm")

        else:
            print(f"[Health] FEE3 cmd=0x{cmd:02x} len={pkt_len}: {[hex(b) for b in raw]}")

    def _handle_steps_fee3(self, raw: list[int]):
        """Parse step/activity data from FEE3 notification."""
        sub = raw[5] if len(raw) > 5 else 0
        if sub == 0x00 and len(raw) >= 11:
            steps = (raw[6] << 8) | raw[7]
            distance = (raw[8] << 8) | raw[9]
            calories = (raw[10] << 8) | raw[11] if len(raw) >= 12 else 0
            if 0 < steps < 100000:
                self._data.steps = steps
                self._data.distance = distance
                self._data.calories = calories
                self._data.last_steps_time = time.time()
                self._data.last_update = time.time()
                self._log_reading(steps=steps, distance=distance, calories=calories)
                print(f"[Health] Steps: {steps}, Distance: {distance}m, Cal: {calories}")
                if self._callback:
                    try:
                        self._callback("steps", steps)
                    except Exception:
                        pass
        else:
            print(f"[Health] Steps sub=0x{sub:02x}: {[hex(b) for b in raw]}")

    def _handle_steps_direct(self, raw: list[int]):
        """Parse steps from direct notification (handle 0x004c).

        Format: [steps_lo, steps_hi, 0, distance_lo, distance_hi, 0, calories_lo, calories_hi, 0]
        """
        if len(raw) < 7:
            return
        steps = raw[0] | (raw[1] << 8)
        distance = raw[3] | (raw[4] << 8)
        calories = raw[6] | (raw[7] << 8) if len(raw) >= 8 else 0
        if 0 < steps < 100000:
            self._data.steps = steps
            self._data.distance = distance
            self._data.calories = calories
            self._data.last_steps_time = time.time()
            self._data.last_update = time.time()
            self._data.connected = True
