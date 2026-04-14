"""
TokioAI Health Alerts — automatic Telegram notifications for abnormal vitals.

Monitors:
- Heart Rate > 120 or < 45 → alert
- SpO2 < 92% → alert
- Blood Pressure systolic > 140 or < 90 → alert
- No data for > 30 min → connectivity alert

Cooldown: 10 minutes between same-type alerts.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .main import TokioRaspiApp

logger = logging.getLogger("health_alerts")

# Thresholds
HR_HIGH = 120
HR_LOW = 45
SPO2_LOW = 92
BP_SYS_HIGH = 140
BP_SYS_LOW = 90
NO_DATA_TIMEOUT = 1800  # 30 minutes

ALERT_COOLDOWN = 600  # 10 minutes between same alerts


class HealthAlerts:
    """Background health monitoring with Telegram alerts."""

    def __init__(self, app: TokioRaspiApp):
        self._app = app
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_alerts: dict = {}  # type -> timestamp
        self._last_data_time = time.time()
        self._check_interval = 30  # seconds

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="health-alerts")
        self._thread.start()
        logger.info("Health alerts monitoring started")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _should_alert(self, alert_type: str) -> bool:
        """Check if we should fire this alert (cooldown)."""
        last = self._last_alerts.get(alert_type, 0)
        return (time.time() - last) > ALERT_COOLDOWN

    def _fire_alert(self, alert_type: str, message: str, severity: str = "warning"):
        """Send alert to Telegram and log."""
        self._last_alerts[alert_type] = time.time()
        logger.warning(f"Health alert: {alert_type} — {message}")

        # Show on Tokio's face
        from .tokio_face import Emotion
        self._app.face.set_emotion(Emotion.ALERT, message[:60])

        # Log to event store if available
        if hasattr(self._app, 'event_store') and self._app.event_store:
            self._app.event_store.log_health_alert(
                alert_type=alert_type,
                value=0,
                threshold=0,
                message=message,
            )

        # Send directly to Telegram
        self._send_telegram(f"🚨 HEALTH ALERT\n\n{message}")

    def _send_telegram(self, text: str):
        """Send alert directly to Telegram bot."""
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_OWNER_ID", "")
        if not token or not chat_id:
            logger.debug("No Telegram credentials for health alert")
            return
        try:
            import urllib.request
            import json as _json
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = _json.dumps({"chat_id": chat_id, "text": text}).encode()
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
            logger.info(f"Telegram alert sent: {text[:60]}")
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")

    def _loop(self):
        while self._running:
            try:
                self._check_vitals()
                time.sleep(self._check_interval)
            except Exception as e:
                logger.error(f"Health check error: {e}")
                time.sleep(60)

    def _check_vitals(self):
        """Check current vitals against thresholds."""
        if not hasattr(self._app, 'health') or not self._app.health.available:
            # Check if health monitor has been unavailable too long
            if self._should_alert("no_data") and (time.time() - self._last_data_time) > NO_DATA_TIMEOUT:
                self._fire_alert("no_data",
                                 "❤️ Health monitor disconnected for 30+ minutes. Check BLE connection.",
                                 "info")
            return

        data = self._app.health.data
        self._last_data_time = time.time()

        # Heart Rate
        hr = data.heart_rate
        if hr and hr > 0:
            if hr > HR_HIGH and self._should_alert("hr_high"):
                self._fire_alert("hr_high",
                                 f"⚠️ Heart Rate ALTA: {hr} bpm (threshold: {HR_HIGH}). "
                                 "Descansá un poco.",
                                 "warning")
            elif hr < HR_LOW and self._should_alert("hr_low"):
                self._fire_alert("hr_low",
                                 f"⚠️ Heart Rate BAJA: {hr} bpm (threshold: {HR_LOW}). "
                                 "¿Estás bien?",
                                 "warning")

        # SpO2
        spo2 = data.spo2
        if spo2 and spo2 > 0:
            if spo2 < SPO2_LOW and self._should_alert("spo2_low"):
                self._fire_alert("spo2_low",
                                 f"🔴 SpO2 BAJO: {spo2}% (threshold: {SPO2_LOW}%). "
                                 "Nivel de oxígeno preocupante.",
                                 "critical")

        # Blood Pressure
        bp_sys = data.blood_pressure_sys
        if bp_sys and bp_sys > 0:
            if bp_sys > BP_SYS_HIGH and self._should_alert("bp_high"):
                self._fire_alert("bp_high",
                                 f"⚠️ Presión arterial ALTA: {bp_sys}/{data.blood_pressure_dia} mmHg. "
                                 f"(threshold: {BP_SYS_HIGH})",
                                 "warning")
            elif bp_sys < BP_SYS_LOW and self._should_alert("bp_low"):
                self._fire_alert("bp_low",
                                 f"⚠️ Presión arterial BAJA: {bp_sys}/{data.blood_pressure_dia} mmHg. "
                                 f"(threshold: {BP_SYS_LOW})",
                                 "warning")
