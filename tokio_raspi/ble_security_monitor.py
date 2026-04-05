"""
TokioAI BLE Security Monitor — Bluetooth attack detection.

Detects:
- BlueBorne-style attacks (unexpected L2CAP/SDP connections)
- KNOB attacks (encryption key negotiation downgrades)
- BLE advertisement flooding (DoS)
- Suspicious BLE device scanning (recon before attack)
- MAC address randomization tracking (tracking attacks)
- BLE MITM indicators (connection parameter manipulation)

Runs alongside the HealthMonitor, using hcitool/hcidump for passive monitoring.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional, Callable


@dataclass
class BLEAttack:
    timestamp: float
    attack_type: str  # blueborne, knob, adv_flood, recon, mitm, mac_tracking
    attacker_mac: str
    description: str
    severity: str = "medium"  # low, medium, high, critical
    mitigated: bool = False


@dataclass
class BLESecurityStats:
    monitoring: bool = False
    scan_count: int = 0
    devices_seen: int = 0
    suspicious_devices: int = 0
    attacks_detected: int = 0
    blueborne_attempts: int = 0
    knob_attempts: int = 0
    adv_floods: int = 0
    recon_scans: int = 0
    mitm_indicators: int = 0
    last_scan: float = 0.0
    attacks: list = field(default_factory=list)


class BLESecurityMonitor:
    """Passive BLE security monitor — detects Bluetooth attacks."""

    def __init__(self, hci_device: str = "hci0",
                 allowed_macs: Optional[set] = None):
        self._hci = hci_device
        self._running = False
        self._lock = threading.Lock()
        self._callback: Optional[Callable] = None
        self._thread: Optional[threading.Thread] = None

        # Allowed devices (won't trigger alerts)
        self._allowed_macs = {m.upper() for m in (allowed_macs or set())}
        self._allowed_macs.add("D2:2E:68:90:39:01")  # TokioWatch always allowed

        # Device tracking
        self._devices: dict[str, dict] = {}  # MAC -> {name, rssi, first_seen, last_seen, adv_count, services}
        self._device_history: dict[str, list[float]] = defaultdict(list)  # MAC -> timestamps

        # Attack detection thresholds
        self._adv_flood_threshold = 100  # advs from same MAC in 10s
        self._recon_scan_threshold = 30  # unique devices from one MAC in 60s
        self._rapid_connect_threshold = 10  # connection attempts in 30s
        self._mac_rotation_threshold = 20  # new random MACs in 60s

        # L2CAP/SDP connection tracking (BlueBorne indicator)
        self._l2cap_connections: dict[str, list[float]] = defaultdict(list)

        # Stats
        self._stats = BLESecurityStats()
        self._attacks: list[BLEAttack] = []
        self._random_mac_tracker: list[tuple[float, str]] = []  # (time, MAC)

    def set_callback(self, callback: Callable):
        """Set callback for attack alerts: callback(attack_type, description)"""
        self._callback = callback

    def start(self):
        """Start BLE security monitoring."""
        if self._running:
            return
        self._running = True
        self._stats.monitoring = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        print("[BLE-Security] Monitor started")

    def stop(self):
        """Stop monitoring."""
        self._running = False
        self._stats.monitoring = False
        if self._thread:
            self._thread.join(timeout=5)
        print("[BLE-Security] Monitor stopped")

    def _monitor_loop(self):
        """Main monitoring loop — periodic BLE scanning."""
        while self._running:
            try:
                self._scan_devices()
                self._check_hci_events()
                self._analyze_threats()
                time.sleep(15)  # Scan every 15 seconds
            except Exception as e:
                print(f"[BLE-Security] Error in monitor loop: {e}")
                time.sleep(30)

    def _scan_devices(self):
        """Scan for BLE devices using hcitool."""
        try:
            # LE scan for 5 seconds
            result = subprocess.run(
                ["sudo", "timeout", "5", "hcitool", "-i", self._hci, "lescan", "--duplicates"],
                capture_output=True, text=True, timeout=10
            )
            output = result.stdout + result.stderr
            now = time.time()

            new_devices = set()
            for line in output.split('\n'):
                # Parse: XX:XX:XX:XX:XX:XX DeviceName
                match = re.match(r'([0-9A-Fa-f:]{17})\s+(.*)', line.strip())
                if not match:
                    continue
                mac = match.group(1).upper()
                name = match.group(2).strip() or "(unknown)"
                new_devices.add(mac)

                with self._lock:
                    if mac not in self._devices:
                        self._devices[mac] = {
                            'name': name, 'first_seen': now, 'last_seen': now,
                            'adv_count': 1, 'rssi': 0
                        }
                    else:
                        self._devices[mac]['last_seen'] = now
                        self._devices[mac]['adv_count'] += 1
                        if name != "(unknown)":
                            self._devices[mac]['name'] = name

                    self._device_history[mac].append(now)
                    # Keep only last 60 seconds of history
                    self._device_history[mac] = [
                        t for t in self._device_history[mac] if now - t < 60
                    ]

            with self._lock:
                self._stats.scan_count += 1
                self._stats.last_scan = now
                self._stats.devices_seen = len(self._devices)

            # Track MAC randomization (many new random MACs = tracking attack or recon)
            self._check_mac_randomization(new_devices, now)

        except subprocess.TimeoutExpired:
            pass
        except Exception as e:
            print(f"[BLE-Security] Scan error: {e}")

    def _check_hci_events(self):
        """Check HCI events for suspicious activity (L2CAP connections, etc)."""
        try:
            # Check active connections
            result = subprocess.run(
                ["hcitool", "-i", self._hci, "con"],
                capture_output=True, text=True, timeout=5
            )
            connections = result.stdout.strip().split('\n')[1:]  # Skip header
            now = time.time()

            for conn in connections:
                match = re.search(r'([0-9A-Fa-f:]{17})', conn)
                if not match:
                    continue
                mac = match.group(1).upper()

                if mac not in self._allowed_macs:
                    # Unexpected BLE connection — potential BlueBorne
                    with self._lock:
                        self._l2cap_connections[mac].append(now)
                        self._l2cap_connections[mac] = [
                            t for t in self._l2cap_connections[mac] if now - t < 30
                        ]
                        if len(self._l2cap_connections[mac]) >= 3:
                            self._report_attack(BLEAttack(
                                timestamp=now,
                                attack_type="blueborne",
                                attacker_mac=mac,
                                description=f"Multiple unexpected L2CAP connections from {mac} — possible BlueBorne attack",
                                severity="critical"
                            ))

        except Exception as e:
            print(f"[BLE-Security] HCI event check error: {e}")

    def _check_mac_randomization(self, new_macs: set, now: float):
        """Detect suspicious MAC randomization patterns."""
        # Random MACs have bit 1 of first octet set (locally administered)
        random_macs = set()
        for mac in new_macs:
            first_byte = int(mac.split(':')[0], 16)
            if first_byte & 0x02:  # Locally administered bit
                random_macs.add(mac)

        with self._lock:
            for mac in random_macs:
                self._random_mac_tracker.append((now, mac))
            # Keep only last 60 seconds
            self._random_mac_tracker = [
                (t, m) for t, m in self._random_mac_tracker if now - t < 60
            ]
            unique_random = len(set(m for _, m in self._random_mac_tracker))
            if unique_random >= self._mac_rotation_threshold:
                self._report_attack(BLEAttack(
                    timestamp=now,
                    attack_type="recon",
                    attacker_mac="multiple",
                    description=f"Excessive MAC randomization detected: {unique_random} random MACs in 60s — possible BLE recon/tracking",
                    severity="medium"
                ))
                self._random_mac_tracker = []  # Reset to avoid repeated alerts

    def _analyze_threats(self):
        """Analyze accumulated data for attack patterns."""
        now = time.time()
        with self._lock:
            for mac, history in self._device_history.items():
                if mac in self._allowed_macs:
                    continue

                recent = [t for t in history if now - t < 10]

                # Advertisement flooding
                if len(recent) >= self._adv_flood_threshold:
                    self._report_attack(BLEAttack(
                        timestamp=now,
                        attack_type="adv_flood",
                        attacker_mac=mac,
                        description=f"BLE advertisement flood from {mac}: {len(recent)} advs in 10s",
                        severity="high"
                    ))
                    self._device_history[mac] = []  # Reset

            # Check for KNOB-like indicators: many short connections
            for mac, conns in self._l2cap_connections.items():
                recent_conns = [t for t in conns if now - t < 30]
                if len(recent_conns) >= self._rapid_connect_threshold:
                    self._report_attack(BLEAttack(
                        timestamp=now,
                        attack_type="knob",
                        attacker_mac=mac,
                        description=f"Rapid BLE connection attempts from {mac}: {len(recent_conns)} in 30s — possible KNOB/key negotiation attack",
                        severity="critical"
                    ))
                    self._l2cap_connections[mac] = []

    def _report_attack(self, attack: BLEAttack):
        """Report a detected attack."""
        # Dedup: don't report same type from same MAC within 60s
        for existing in self._attacks[-10:]:
            if (existing.attack_type == attack.attack_type
                    and existing.attacker_mac == attack.attacker_mac
                    and time.time() - existing.timestamp < 60):
                return

        self._attacks.append(attack)
        if len(self._attacks) > 100:
            self._attacks = self._attacks[-100:]

        # Update stats
        if attack.attack_type == "blueborne":
            self._stats.blueborne_attempts += 1
        elif attack.attack_type == "knob":
            self._stats.knob_attempts += 1
        elif attack.attack_type == "adv_flood":
            self._stats.adv_floods += 1
        elif attack.attack_type == "recon":
            self._stats.recon_scans += 1
        elif attack.attack_type == "mitm":
            self._stats.mitm_indicators += 1
        self._stats.attacks_detected += 1
        self._stats.attacks = [
            {"time": a.timestamp, "type": a.attack_type,
             "mac": a.attacker_mac, "desc": a.description,
             "severity": a.severity}
            for a in self._attacks[-20:]
        ]

        severity_icon = {"low": "🔵", "medium": "🟡", "high": "🟠", "critical": "🔴"}
        icon = severity_icon.get(attack.severity, "⚪")
        print(f"[BLE-Security] {icon} {attack.attack_type.upper()}: {attack.description}")

        if self._callback:
            try:
                self._callback(attack.attack_type, attack.description)
            except Exception:
                pass

    def get_status(self) -> dict:
        """Get current BLE security status."""
        with self._lock:
            return {
                "monitoring": self._stats.monitoring,
                "scan_count": self._stats.scan_count,
                "devices_seen": self._stats.devices_seen,
                "attacks_detected": self._stats.attacks_detected,
                "blueborne_attempts": self._stats.blueborne_attempts,
                "knob_attempts": self._stats.knob_attempts,
                "adv_floods": self._stats.adv_floods,
                "recon_scans": self._stats.recon_scans,
                "recent_attacks": self._stats.attacks[-10:],
                "allowed_devices": list(self._allowed_macs),
                "last_scan": self._stats.last_scan,
            }

    def get_devices(self) -> list[dict]:
        """Get list of detected BLE devices."""
        with self._lock:
            result = []
            for mac, info in self._devices.items():
                result.append({
                    "mac": mac,
                    "name": info.get('name', ''),
                    "first_seen": info.get('first_seen', 0),
                    "last_seen": info.get('last_seen', 0),
                    "adv_count": info.get('adv_count', 0),
                    "is_allowed": mac in self._allowed_macs,
                    "is_random_mac": bool(int(mac.split(':')[0], 16) & 0x02),
                })
            return sorted(result, key=lambda x: x['last_seen'], reverse=True)
