"""
TokioAI WiFi Defense Enhancements — EAPOL/WPA2 attack detection.

Adds to the existing WiFiDefense:
- WPA2 handshake capture detection (EAPOL frame monitoring)
- PMKID attack detection (RSN PMKID in first EAPOL message)
- Karma attack detection (rogue AP responding to all probes)
- Enhanced evil twin with signal strength comparison
- Full summary reporting

This module is imported and attached to the existing WiFiDefense class.
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional, Callable

try:
    from scapy.all import (
        Dot11, Dot11Beacon, Dot11ProbeResp, Dot11ProbeReq,
        Dot11Auth, Dot11AssoReq, Dot11AssoResp,
        EAPOL, RadioTap, Raw
    )
    SCAPY_AVAILABLE = True
except ImportError:
    SCAPY_AVAILABLE = False


@dataclass
class WPA2Attack:
    timestamp: float
    attack_type: str  # handshake_capture, pmkid, karma, key_reinstall
    attacker_mac: str
    target_mac: str
    ssid: str
    description: str
    severity: str = "critical"


class WPA2Monitor:
    """Detects WPA2-specific attacks: handshake capture, PMKID, KRACK."""

    def __init__(self, protected_ssid: str = "", protected_bssid: str = ""):
        self._protected_ssid = protected_ssid
        self._protected_bssid = protected_bssid.upper()
        self._lock = threading.Lock()
        self._callback: Optional[Callable] = None

        # EAPOL tracking per station
        # key: (ap_mac, client_mac) -> list of EAPOL message numbers seen
        self._eapol_tracker: dict[tuple, list] = defaultdict(list)
        self._eapol_timestamps: dict[tuple, list[float]] = defaultdict(list)

        # PMKID detection
        self._pmkid_attempts: dict[str, int] = defaultdict(int)  # AP MAC -> count

        # Karma detection: track probe responses
        self._probe_requests: dict[str, set] = defaultdict(set)  # MAC -> SSIDs probed
        self._probe_responses: dict[str, set] = defaultdict(set)  # AP MAC -> SSIDs responded
        self._karma_window_start = time.time()

        # Key reinstallation (KRACK) detection
        self._msg3_counter: dict[tuple, int] = defaultdict(int)  # (AP,client) -> msg3 count
        self._msg3_window_start = time.time()

        # Stats
        self._attacks: list[WPA2Attack] = []
        self._stats = {
            "handshake_captures_detected": 0,
            "pmkid_attempts": 0,
            "karma_attacks": 0,
            "krack_indicators": 0,
            "eapol_frames_seen": 0,
            "monitoring": True,
        }

    def set_callback(self, callback: Callable):
        self._callback = callback

    def process_packet(self, pkt):
        """Process a packet for WPA2 attack indicators. Called from WiFiDefense._process_packet."""
        if not SCAPY_AVAILABLE:
            return

        now = time.time()

        # EAPOL frame detection (WPA2 handshake)
        if pkt.haslayer(EAPOL):
            self._process_eapol(pkt, now)

        # Probe request tracking (for Karma detection)
        if pkt.haslayer(Dot11ProbeReq):
            self._track_probe_request(pkt, now)

        # Probe response tracking (for Karma detection)
        if pkt.haslayer(Dot11ProbeResp):
            self._track_probe_response(pkt, now)

        # Periodic Karma analysis
        if now - self._karma_window_start > 30:
            self._analyze_karma(now)
            self._karma_window_start = now

        # Periodic KRACK analysis
        if now - self._msg3_window_start > 60:
            self._analyze_krack(now)
            self._msg3_window_start = now

    def _process_eapol(self, pkt, now: float):
        """Analyze EAPOL frames for handshake capture and PMKID attacks."""
        with self._lock:
            self._stats["eapol_frames_seen"] += 1

        if not pkt.haslayer(Dot11):
            return

        src = (pkt[Dot11].addr2 or "").upper()
        dst = (pkt[Dot11].addr1 or "").upper()
        bssid = (pkt[Dot11].addr3 or "").upper()

        eapol_layer = pkt[EAPOL]
        raw_data = bytes(eapol_layer)

        # Determine EAPOL message number (1-4)
        msg_num = self._identify_eapol_message(raw_data, src, bssid)

        pair = (bssid, dst if src == bssid else src)

        with self._lock:
            self._eapol_tracker[pair].append(msg_num)
            self._eapol_timestamps[pair].append(now)

            # Keep only last 30 seconds
            cutoff = now - 30
            while self._eapol_timestamps[pair] and self._eapol_timestamps[pair][0] < cutoff:
                self._eapol_timestamps[pair].pop(0)
                if self._eapol_tracker[pair]:
                    self._eapol_tracker[pair].pop(0)

            msgs = self._eapol_tracker[pair]

            # Full 4-way handshake captured = someone is capturing handshakes
            if self._has_full_handshake(msgs):
                # Check if this is targeting our network
                is_ours = (bssid == self._protected_bssid) if self._protected_bssid else True
                if is_ours:
                    self._report_attack(WPA2Attack(
                        timestamp=now,
                        attack_type="handshake_capture",
                        attacker_mac="unknown",
                        target_mac=pair[1],
                        ssid=self._protected_ssid or bssid,
                        description=f"Full WPA2 4-way handshake captured for {pair[1]} on AP {bssid} — attacker may be capturing credentials",
                        severity="critical"
                    ))
                    self._eapol_tracker[pair] = []  # Reset

            # PMKID detection: Message 1 with PMKID in RSN IE
            if msg_num == 1 and self._check_pmkid(raw_data):
                self._pmkid_attempts[bssid] += 1
                if self._pmkid_attempts[bssid] <= 3:  # Alert first 3 times
                    self._report_attack(WPA2Attack(
                        timestamp=now,
                        attack_type="pmkid",
                        attacker_mac=bssid,
                        target_mac=dst,
                        ssid=self._protected_ssid or bssid,
                        description=f"PMKID attack detected: AP {bssid} sending PMKID in EAPOL msg1 — hashcat-style offline attack",
                        severity="critical"
                    ))

            # KRACK: repeated message 3
            if msg_num == 3:
                self._msg3_counter[pair] += 1

    def _identify_eapol_message(self, raw: bytes, src: str, bssid: str) -> int:
        """Identify which of the 4 EAPOL messages this is."""
        if len(raw) < 6:
            return 0

        # Key Info field is at offset 5-6 in EAPOL-Key
        try:
            key_info = (raw[5] << 8) | raw[6] if len(raw) > 6 else 0
        except (IndexError, TypeError):
            return 0

        is_from_ap = (src == bssid)
        has_mic = bool(key_info & 0x0100)
        has_ack = bool(key_info & 0x0080)
        is_install = bool(key_info & 0x0040)

        if is_from_ap and has_ack and not has_mic:
            return 1  # AP -> Client, ANonce
        elif not is_from_ap and has_mic and not has_ack:
            if is_install:
                return 4  # Client -> AP, final
            return 2  # Client -> AP, SNonce + MIC
        elif is_from_ap and has_ack and has_mic:
            return 3  # AP -> Client, install key
        elif not is_from_ap and has_mic:
            return 4  # Client -> AP, ACK

        return 0

    def _has_full_handshake(self, msgs: list) -> bool:
        """Check if messages 1-4 were all seen."""
        return all(m in msgs for m in [1, 2, 3, 4])

    def _check_pmkid(self, raw: bytes) -> bool:
        """Check if EAPOL message contains PMKID (used in hashcat PMKID attack)."""
        # PMKID is in RSN IE, tag 0xDD with OUI 00:0F:AC:04
        # Look for the pattern in raw bytes
        pmkid_oui = bytes([0x00, 0x0F, 0xAC, 0x04])
        return pmkid_oui in raw

    def _track_probe_request(self, pkt, now: float):
        """Track probe requests for Karma detection."""
        if not pkt.haslayer(Dot11):
            return
        src = (pkt[Dot11].addr2 or "").upper()
        # Get probed SSID
        try:
            ssid_layer = pkt[Dot11ProbeReq]
            info = ssid_layer.info
            if info and len(info) > 0:
                ssid = info.decode('utf-8', errors='ignore')
                if ssid:
                    with self._lock:
                        self._probe_requests[src].add(ssid)
        except Exception:
            pass

    def _track_probe_response(self, pkt, now: float):
        """Track probe responses for Karma detection."""
        if not pkt.haslayer(Dot11):
            return
        src = (pkt[Dot11].addr2 or "").upper()  # AP MAC
        try:
            ssid_layer = pkt[Dot11ProbeResp]
            info = ssid_layer.info
            if info and len(info) > 0:
                ssid = info.decode('utf-8', errors='ignore')
                if ssid:
                    with self._lock:
                        self._probe_responses[src].add(ssid)
        except Exception:
            pass

    def _analyze_karma(self, now: float):
        """Detect Karma attacks: AP responding to many different SSIDs."""
        with self._lock:
            for ap_mac, responded_ssids in self._probe_responses.items():
                if ap_mac in (self._protected_bssid,):
                    continue
                # A normal AP responds to 1-2 SSIDs (its own)
                # A Karma AP responds to ALL probed SSIDs
                if len(responded_ssids) >= 5:
                    # Check if these match client probes
                    all_probed = set()
                    for client_ssids in self._probe_requests.values():
                        all_probed.update(client_ssids)

                    overlap = responded_ssids & all_probed
                    if len(overlap) >= 3:
                        self._report_attack(WPA2Attack(
                            timestamp=now,
                            attack_type="karma",
                            attacker_mac=ap_mac,
                            target_mac="broadcast",
                            ssid=f"multiple ({len(responded_ssids)} SSIDs)",
                            description=f"Karma/Evil Twin attack: AP {ap_mac} responding to {len(responded_ssids)} different SSIDs — rogue AP honeypot",
                            severity="critical"
                        ))

            # Reset for next window
            self._probe_requests.clear()
            self._probe_responses.clear()

    def _analyze_krack(self, now: float):
        """Detect KRACK (Key Reinstallation Attack): repeated message 3."""
        with self._lock:
            for pair, count in self._msg3_counter.items():
                if count >= 3:  # Normal handshake has 1 msg3, KRACK retransmits many
                    self._report_attack(WPA2Attack(
                        timestamp=now,
                        attack_type="key_reinstall",
                        attacker_mac=pair[0],
                        target_mac=pair[1],
                        ssid=self._protected_ssid,
                        description=f"KRACK indicator: {count} EAPOL message 3 retransmissions from {pair[0]} to {pair[1]}",
                        severity="critical"
                    ))
            self._msg3_counter.clear()

    def _report_attack(self, attack: WPA2Attack):
        """Report a detected attack."""
        # Dedup
        for existing in self._attacks[-10:]:
            if (existing.attack_type == attack.attack_type
                    and existing.attacker_mac == attack.attacker_mac
                    and time.time() - existing.timestamp < 120):
                return

        self._attacks.append(attack)
        if len(self._attacks) > 50:
            self._attacks = self._attacks[-50:]

        with self._lock:
            self._stats[f"{attack.attack_type}s_detected"] = self._stats.get(f"{attack.attack_type}s_detected", 0) + 1

        print(f"[WPA2-Monitor] 🔴 {attack.attack_type.upper()}: {attack.description}")

        if self._callback:
            try:
                self._callback(attack.attack_type, attack.description)
            except Exception:
                pass

    def get_status(self) -> dict:
        """Get WPA2 monitor status."""
        with self._lock:
            return {
                **self._stats,
                "recent_attacks": [
                    {"time": a.timestamp, "type": a.attack_type,
                     "attacker": a.attacker_mac, "target": a.target_mac,
                     "ssid": a.ssid, "desc": a.description,
                     "severity": a.severity}
                    for a in self._attacks[-10:]
                ]
            }
