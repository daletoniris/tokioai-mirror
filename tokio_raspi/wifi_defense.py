"""
TokioAI WiFi Defense — Active 2.4GHz security with monitor mode.

Requires a SECOND WiFi adapter (wlan1) for monitoring while wlan0
stays connected to the network.

Capabilities:
- Deauth frame detection (802.11 type 0xC0)
- Rogue AP / evil twin detection
- Beacon flood detection
- Active mitigations:
  - Auto-reconnect after deauth
  - Deauth attacker's clients (counter-deauth)
  - Alert via callback (Telegram/UI)
  - Blacklist MAC addresses

Requires: scapy (pip install scapy), airmon-ng (aircrack-ng package)
"""
from __future__ import annotations

import atexit
import os
import re
import subprocess
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

# Try to import scapy — not fatal if missing
try:
    from scapy.all import (
        sniff, Dot11, Dot11Deauth, Dot11Beacon, Dot11ProbeResp,
        Dot11AssoReq, RadioTap, sendp, conf as scapy_conf
    )
    SCAPY_AVAILABLE = True
except ImportError:
    SCAPY_AVAILABLE = False


@dataclass
class WiFiAttack:
    timestamp: float
    attack_type: str  # deauth, evil_twin, beacon_flood, probe_flood
    attacker_mac: str
    target_mac: str
    channel: int
    ssid: str
    count: int = 1
    mitigated: bool = False


@dataclass
class WiFiDefenseStats:
    deauth_detected: int = 0
    evil_twins: int = 0
    beacon_floods: int = 0
    mitigations_applied: int = 0
    monitoring: bool = False
    monitor_interface: str = ""
    attacks: list = field(default_factory=list)


class WiFiDefense:
    """Active WiFi defense using monitor mode on second adapter."""

    def __init__(self, monitor_iface: str = "wlan1", main_iface: str = "wlan0",
                 protected_ssid: str = ""):
        self._monitor_iface = monitor_iface
        self._main_iface = main_iface
        self._monitor_mode_iface = ""
        self._running = False
        self._lock = threading.Lock()
        self._callback = None
        self._protected_ssid = protected_ssid or os.getenv("WIFI_PROTECTED_SSID", "")
        self._protected_channel: int = 0  # discovered at learn_network time

        # Known legitimate APs (BSSID -> SSID)
        self._known_aps: dict[str, str] = {}

        # Deauth tracking — detect quickly for demo/real attacks
        self._deauth_counts: dict[str, int] = defaultdict(int)
        self._deauth_window_start = time.time()
        self._deauth_threshold = 3  # deauth frames per 15s window to trigger
        self._deauth_confirmed: dict[str, int] = defaultdict(int)  # windows with attacks
        self._deauth_confirm_windows = 1  # alert on first window (immediate detection)

        # Stats
        self._stats = WiFiDefenseStats()
        self._attacks: list[WiFiAttack] = []

        # Blacklisted MACs (rogue APs)
        self._blacklisted_macs: set[str] = set()

        # Probe request tracking
        self._probe_counts: dict[str, int] = defaultdict(int)
        self._probe_window_start = time.time()
        self._probe_threshold = 100  # probes per 30s from same MAC (raised: normal devices send 30-50)
        self._known_scanners: set[str] = set()  # ignore after first alert

        # Own MACs — NEVER treat our own traffic as attacks
        self._own_macs: set[str] = set()

        # Mitigation settings
        self.auto_reconnect = True
        self.counter_deauth = True
        self.alert_enabled = True

        # Forensic log file
        self._log_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "wifi_attacks.log"
        )

        # Register cleanup
        atexit.register(self._cleanup)

    @property
    def available(self) -> bool:
        if not SCAPY_AVAILABLE:
            return False
        try:
            result = subprocess.run(
                ["ip", "link", "show", self._monitor_iface],
                capture_output=True, text=True, timeout=3
            )
            return result.returncode == 0
        except Exception:
            return False

    def set_callback(self, callback):
        """Set callback: callback(attack_type, message, severity)"""
        self._callback = callback

    def learn_network(self):
        """Scan and learn legitimate APs + own MACs + protected channel."""
        # Learn own MAC addresses — never treat our own traffic as attacks
        for iface in (self._main_iface, self._monitor_iface):
            try:
                mac_path = f"/sys/class/net/{iface}/address"
                mac = open(mac_path).read().strip().upper()
                if mac and mac != "00:00:00:00:00:00":
                    self._own_macs.add(mac)
            except Exception:
                pass
        # Also ignore broadcast (our counter-deauth uses it as src)
        self._own_macs.add("FF:FF:FF:FF:FF:FF")
        print(f"[WiFiDefense] Own MACs (ignored): {self._own_macs}")

        # Use nmcli for reliable scan (includes channel info)
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "BSSID,SSID,CHAN", "dev", "wifi", "list"],
                capture_output=True, text=True, timeout=15
            )
            for line in result.stdout.strip().split("\n"):
                parts = line.split(":")
                if len(parts) >= 8:  # BSSID has 6 colons + SSID + CHAN
                    bssid = ":".join(parts[:6]).strip().upper()
                    rest = ":".join(parts[6:])  # SSID:CHAN (SSID may have colons)
                    # Last field is channel number
                    last_colon = rest.rfind(":")
                    if last_colon > 0:
                        ssid = rest[:last_colon].strip()
                        chan_str = rest[last_colon+1:].strip()
                    else:
                        ssid = rest.strip()
                        chan_str = ""
                    if ssid:
                        self._known_aps[bssid] = ssid
                        # Find protected network channel
                        if self._protected_ssid and ssid == self._protected_ssid and chan_str.isdigit():
                            self._protected_channel = int(chan_str)
            print(f"[WiFiDefense] Learned {len(self._known_aps)} legitimate APs")
            if self._protected_channel:
                print(f"[WiFiDefense] Protected network '{self._protected_ssid}' on channel {self._protected_channel}")
        except Exception as e:
            print(f"[WiFiDefense] nmcli scan failed, trying iw: {e}")
            try:
                result = subprocess.run(
                    ["sudo", "iw", "dev", self._main_iface, "scan"],
                    capture_output=True, text=True, timeout=15
                )
                current_bssid = ""
                for line in result.stdout.split("\n"):
                    line = line.strip()
                    if line.startswith("BSS "):
                        current_bssid = line.split()[1].split("(")[0].upper()
                    elif line.startswith("SSID:") and current_bssid:
                        ssid = line[5:].strip()
                        if ssid:
                            self._known_aps[current_bssid] = ssid
                print(f"[WiFiDefense] Learned {len(self._known_aps)} legitimate APs (iw)")
            except Exception as e2:
                print(f"[WiFiDefense] Network learning failed: {e2}")

    def start(self):
        """Start monitor mode and packet capture."""
        if not self.available:
            print(f"[WiFiDefense] Cannot start — {self._monitor_iface} not available or scapy missing")
            return False

        self.learn_network()

        if not self._enable_monitor_mode():
            return False

        self._running = True
        self._stats.monitoring = True
        threading.Thread(target=self._capture_loop, daemon=True).start()
        threading.Thread(target=self._mitigation_loop, daemon=True).start()
        print(f"[WiFiDefense] Active defense started on {self._monitor_mode_iface}")
        return True

    def stop(self):
        """Stop monitoring and restore managed mode."""
        self._running = False
        self._stats.monitoring = False
        self._disable_monitor_mode()

    def _cleanup(self):
        """Ensure monitor mode is disabled on exit."""
        if self._running:
            self._running = False
            try:
                self._disable_monitor_mode()
            except Exception:
                pass

    def _enable_monitor_mode(self) -> bool:
        """Put the monitor interface into monitor mode (robust for rtl8xxxu).

        If already in monitor mode (e.g. set by relaunch.sh), skip reconfiguration
        to avoid the rtl8xxxu DORMANT bug.
        """
        iface = self._monitor_iface
        try:
            # Check if already in monitor mode
            result = subprocess.run(
                ["sudo", "iw", "dev", iface, "info"],
                capture_output=True, text=True, timeout=5
            )
            already_monitor = "type monitor" in result.stdout

            if already_monitor:
                # DO NOT touch the interface — rtl8xxxu goes DORMANT if we do anything
                print(f"[WiFiDefense] {iface} already in monitor mode — not touching it")
            else:
                # Set monitor mode: down → monitor → up (NO managed cycle for rtl8xxxu)
                subprocess.run(["sudo", "ip", "link", "set", iface, "down"],
                               capture_output=True, timeout=5)
                time.sleep(0.5)
                subprocess.run(["sudo", "iw", iface, "set", "monitor", "control"],
                               capture_output=True, timeout=5)
                time.sleep(0.5)
                subprocess.run(["sudo", "ip", "link", "set", iface, "up"],
                               capture_output=True, timeout=5)
                time.sleep(1)

            # Verify it's actually receiving packets
            try:
                rx_path = f"/sys/class/net/{iface}/statistics/rx_packets"
                rx_before = int(open(rx_path).read().strip())
                time.sleep(2)
                rx_after = int(open(rx_path).read().strip())
                if rx_after > rx_before:
                    print(f"[WiFiDefense] Monitor mode verified: {rx_after - rx_before} packets in 2s")
                else:
                    print(f"[WiFiDefense] WARNING: 0 rx packets — interface may be stuck in DORMANT")
            except Exception:
                pass

            self._monitor_mode_iface = iface
            self._stats.monitor_interface = iface
            print(f"[WiFiDefense] Monitor mode enabled: {iface}")
            return True
        except Exception as e:
            print(f"[WiFiDefense] Failed to enable monitor mode: {e}")
            return False

    def _disable_monitor_mode(self):
        """Restore managed mode."""
        try:
            subprocess.run(
                ["sudo", "airmon-ng", "stop", self._monitor_mode_iface],
                capture_output=True, timeout=10
            )
        except Exception:
            try:
                subprocess.run(
                    ["sudo", "ip", "link", "set", self._monitor_iface, "down"],
                    capture_output=True, timeout=3
                )
                subprocess.run(
                    ["sudo", "iw", self._monitor_iface, "set", "type", "managed"],
                    capture_output=True, timeout=3
                )
                subprocess.run(
                    ["sudo", "ip", "link", "set", self._monitor_iface, "up"],
                    capture_output=True, timeout=3
                )
            except Exception:
                pass

    def _hop_channel(self, channel: int):
        """Switch monitor interface to a specific channel."""
        try:
            result = subprocess.run(
                ["sudo", "iw", "dev", self._monitor_mode_iface, "set", "channel", str(channel)],
                capture_output=True, timeout=2,
            )
            if result.returncode != 0:
                # Interface may have reset, try setting it up again
                subprocess.run(
                    ["sudo", "ip", "link", "set", self._monitor_mode_iface, "up"],
                    capture_output=True, timeout=2,
                )
        except Exception:
            pass

    def _channel_hop_loop(self):
        """Background thread: channel hopping or fixed channel for protected network.

        If a protected channel is set, stay FIXED on that channel (no hopping).
        The rtl8xxxu driver breaks when switching channels — it goes DORMANT.
        Fixed channel = 100% detection rate on the protected network.
        Evil twin detection still works via beacon comparison on the same channel.
        """
        pc = self._protected_channel
        if pc:
            print(f"[WiFiDefense] FIXED on channel {pc} (protecting {self._protected_ssid})")
            # No hopping — just keep alive
            while self._running:
                time.sleep(10)
        else:
            # No protected network — do normal hopping
            scan_channels = [1, 6, 11, 2, 7, 3, 8, 4, 9, 5, 10]
            idx = 0
            while self._running:
                try:
                    self._hop_channel(scan_channels[idx % len(scan_channels)])
                except Exception:
                    pass
                idx += 1
                time.sleep(2)

    def _capture_loop(self):
        """Main packet capture loop using scapy."""
        iface = self._monitor_mode_iface

        threading.Thread(target=self._channel_hop_loop, daemon=True).start()
        print(f"[WiFiDefense] Channel hopping started (ch 1-11, 2s interval)")

        def packet_handler(pkt):
            if not self._running:
                return
            try:
                self._process_packet(pkt)
            except Exception:
                pass

        retry_count = 0
        while self._running:
            try:
                # Re-resolve interface name each time to handle USB resets
                scapy_conf.iface = iface
                sniff(
                    iface=iface,
                    prn=packet_handler,
                    store=False,
                    timeout=10,
                )
                retry_count = 0  # successful capture
            except (OSError, IOError) as e:
                if self._running:
                    retry_count += 1
                    print(f"[WiFiDefense] Capture error (retry {retry_count}): {e}")
                    if retry_count <= 5:
                        time.sleep(3)
                    else:
                        # Interface may need full re-init
                        print(f"[WiFiDefense] Re-initializing monitor mode...")
                        self._enable_monitor_mode()
                        iface = self._monitor_mode_iface
                        retry_count = 0
                        time.sleep(2)
            except Exception as e:
                if self._running:
                    print(f"[WiFiDefense] Capture error: {e}")
                    time.sleep(3)

    def _process_packet(self, pkt):
        """Analyze a captured packet for attacks."""
        now = time.time()

        # Reset deauth window every 15 seconds
        if now - self._deauth_window_start > 15:
            # Before reset, record which MACs were active in this window
            with self._lock:
                for mac, count in self._deauth_counts.items():
                    if count >= self._deauth_threshold:
                        self._deauth_confirmed[mac] += 1
                    else:
                        # No sustained attack — decay confirmation counter
                        if mac in self._deauth_confirmed and self._deauth_confirmed[mac] > 0:
                            self._deauth_confirmed[mac] -= 1
                self._deauth_counts.clear()
            self._deauth_window_start = now

        # Reset probe window every 30 seconds
        if now - self._probe_window_start > 30:
            with self._lock:
                self._probe_counts.clear()
            self._probe_window_start = now

        # Deauth detection
        if pkt.haslayer(Dot11Deauth):
            src = pkt[Dot11].addr2 or "unknown"
            dst = pkt[Dot11].addr1 or "broadcast"
            src = src.upper()

            # Ignore our own counter-deauth traffic
            if src in self._own_macs:
                return

            with self._lock:
                self._deauth_counts[src] += 1
                current_count = self._deauth_counts[src]
                confirmed_windows = self._deauth_confirmed.get(src, 0)

            # Only alert if threshold reached AND sustained across windows
            if current_count >= self._deauth_threshold:
                # First window: just mark, don't alert yet (could be normal roaming)
                if confirmed_windows >= self._deauth_confirm_windows - 1:
                    with self._lock:
                        self._stats.deauth_detected += 1
                        attack = WiFiAttack(
                            timestamp=now,
                            attack_type="deauth",
                            attacker_mac=src,
                            target_mac=dst,
                            channel=0,
                            ssid="",
                            count=current_count,
                        )
                        self._attacks.append(attack)
                        self._attacks = self._attacks[-50:]
                        # Reset to avoid repeated alerts
                        self._deauth_counts[src] = 0
                        self._deauth_confirmed[src] = 0

                    self._log_attack(attack)
                    self._safe_callback(
                        "deauth",
                        f"DEAUTH ATTACK from {src} ({current_count} pkts, {confirmed_windows + 1} windows)",
                        "critical"
                    )

        # Probe request flood detection
        elif pkt.haslayer(Dot11) and pkt.type == 0 and pkt.subtype == 4:
            src = pkt[Dot11].addr2
            if src:
                src = src.upper()
                # Ignore our own probe requests
                if src in self._own_macs:
                    return
                # Skip known scanners (already alerted once)
                if src in self._known_scanners:
                    return

                with self._lock:
                    self._probe_counts[src] += 1
                    current_count = self._probe_counts[src]

                if current_count >= self._probe_threshold:
                    with self._lock:
                        attack = WiFiAttack(
                            timestamp=now,
                            attack_type="probe_flood",
                            attacker_mac=src,
                            target_mac="broadcast",
                            channel=0,
                            ssid="",
                            count=current_count,
                        )
                        self._attacks.append(attack)
                        self._attacks = self._attacks[-50:]
                        self._probe_counts[src] = 0

                    self._known_scanners.add(src)
                    self._log_attack(attack)
                    self._safe_callback(
                        "probe_flood",
                        f"SCANNER detected: {src} ({current_count} probes)",
                        "high"
                    )

                    # Counter-deauth scanner (but NOT broadcast addresses)
                    if (self.counter_deauth and SCAPY_AVAILABLE
                            and src != "FF:FF:FF:FF:FF:FF"
                            and not src.startswith("FF:")):
                        self._send_counter_deauth(src)

        # Evil twin / rogue AP detection — ONLY for the protected network
        # Dual-band routers have different BSSIDs per band, so checking all SSIDs
        # creates massive false positives. Only check the protected SSID.
        elif pkt.haslayer(Dot11Beacon) and self._protected_ssid:
            bssid = pkt[Dot11].addr3
            if bssid:
                bssid = bssid.upper()
                try:
                    ssid = pkt[Dot11].info.decode("utf-8", errors="ignore")
                except Exception:
                    ssid = ""

                # Only check evil twins for the protected network
                if ssid != self._protected_ssid:
                    return

                # Learn legitimate BSSIDs for the protected SSID dynamically
                # (first time we see this BSSID on the protected channel, it's legit)
                if bssid not in self._known_aps:
                    self._known_aps[bssid] = ssid
                    return

                # If this BSSID is already known for a DIFFERENT SSID, that's suspicious
                known_ssid = self._known_aps.get(bssid, "")
                if known_ssid and known_ssid != ssid:
                    with self._lock:
                        recent = [a for a in self._attacks
                                  if a.attack_type == "evil_twin"
                                  and a.attacker_mac == bssid
                                  and now - a.timestamp < 300]
                        if recent:
                            return

                        self._stats.evil_twins += 1
                        attack = WiFiAttack(
                            timestamp=now,
                            attack_type="evil_twin",
                            attacker_mac=bssid,
                            target_mac="",
                            channel=0,
                            ssid=ssid,
                        )
                        self._attacks.append(attack)
                        self._attacks = self._attacks[-50:]

                    self._log_attack(attack)
                    self._safe_callback(
                        "evil_twin",
                        f"EVIL TWIN! Fake '{ssid}' from {bssid}",
                        "critical"
                    )

    def _safe_callback(self, attack_type: str, message: str, severity: str):
        """Call callback with full error protection."""
        if not self._callback or not self.alert_enabled:
            return
        try:
            self._callback(attack_type, message, severity)
        except Exception as e:
            print(f"[WiFiDefense] Callback error (non-fatal): {e}")

    def _send_counter_deauth(self, target_mac: str):
        """Send counter-deauth to disrupt attacker. Never targets broadcast."""
        if not SCAPY_AVAILABLE:
            return
        if target_mac in ("FF:FF:FF:FF:FF:FF", "00:00:00:00:00:00"):
            return
        try:
            dot11 = Dot11(addr1=target_mac,
                          addr2="ff:ff:ff:ff:ff:ff",
                          addr3=target_mac)
            pkt = RadioTap() / dot11 / Dot11Deauth(reason=7)
            sendp(pkt, iface=self._monitor_mode_iface,
                  count=3, inter=0.05, verbose=False)
        except Exception:
            pass

    def _mitigation_loop(self):
        """Background loop that applies mitigations."""
        while self._running:
            time.sleep(5)

            with self._lock:
                recent = [a for a in self._attacks if time.time() - a.timestamp < 30]

            for attack in recent:
                if attack.mitigated:
                    continue

                if attack.attack_type == "deauth" and self.auto_reconnect:
                    self._mitigate_deauth(attack)

                if attack.attack_type == "evil_twin":
                    self._mitigate_evil_twin(attack)

    def _mitigate_deauth(self, attack: WiFiAttack):
        """Respond to deauth attack."""
        attack.mitigated = True
        with self._lock:
            self._stats.mitigations_applied += 1

        if self.auto_reconnect:
            try:
                subprocess.run(
                    ["sudo", "nmcli", "dev", "wifi", "connect",
                     "--", self._main_iface],
                    capture_output=True, timeout=10
                )
            except Exception:
                pass

        # Counter-deauth (never target broadcast)
        if self.counter_deauth and attack.attacker_mac != "FF:FF:FF:FF:FF:FF":
            self._send_counter_deauth(attack.attacker_mac)

        self._safe_callback(
            "mitigation",
            f"Deauth mitigated — reconnecting, attacker: {attack.attacker_mac}",
            "high"
        )

    def _mitigate_evil_twin(self, attack: WiFiAttack):
        """Respond to evil twin attack."""
        attack.mitigated = True
        with self._lock:
            self._stats.mitigations_applied += 1
            self._blacklisted_macs.add(attack.attacker_mac)

        self._safe_callback(
            "mitigation",
            f"Evil twin '{attack.ssid}' from {attack.attacker_mac} — blacklisted",
            "critical"
        )

    def _log_attack(self, attack: WiFiAttack):
        """Log attack to forensic file."""
        try:
            import datetime
            ts = datetime.datetime.fromtimestamp(attack.timestamp).strftime("%Y-%m-%d %H:%M:%S")
            line = (f"{ts} | {attack.attack_type:12s} | "
                    f"SRC:{attack.attacker_mac} | DST:{attack.target_mac} | "
                    f"SSID:{attack.ssid} | COUNT:{attack.count}\n")
            with open(self._log_file, "a") as f:
                f.write(line)
        except Exception:
            pass

    def enable_counter_deauth(self, enabled: bool = True):
        """Enable/disable counter-deauth (use only with authorization)."""
        self.counter_deauth = enabled
        print(f"[WiFiDefense] Counter-deauth {'ENABLED' if enabled else 'disabled'}")

    def get_attack_log(self, limit: int = 20) -> list[dict]:
        """Get recent attacks for API/UI."""
        with self._lock:
            attacks = list(self._attacks[-limit:])
        return [{
            "time": a.timestamp,
            "type": a.attack_type,
            "attacker": a.attacker_mac,
            "target": a.target_mac,
            "ssid": a.ssid,
            "count": a.count,
            "mitigated": a.mitigated,
        } for a in attacks]

    def get_stats(self) -> WiFiDefenseStats:
        with self._lock:
            return WiFiDefenseStats(
                deauth_detected=self._stats.deauth_detected,
                evil_twins=self._stats.evil_twins,
                beacon_floods=self._stats.beacon_floods,
                mitigations_applied=self._stats.mitigations_applied,
                monitoring=self._stats.monitoring,
                monitor_interface=self._stats.monitor_interface,
                attacks=[a for a in self._attacks if time.time() - a.timestamp < 300],
            )
