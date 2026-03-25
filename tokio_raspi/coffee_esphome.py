"""
TokioAI Coffee Machine — ESPHome/Home Assistant integration.

Controls a Philips Series 2200 coffee machine via ESPHome ESP32
that intercepts the UART between display and mainboard.

Based on: https://github.com/TillFleworr/ESPHome-Philips-Smart-Coffee
Protocol: 9600 baud, message header {0xD5, 0x55}

Architecture:
    Philips <-> ESP32 (ESPHome) <-> WiFi <-> Home Assistant <-> TokioAI

The ESP32 registers as a device in Home Assistant with buttons
for each drink type. TokioAI sends commands via HA REST API.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger("tokio.coffee")

# Home Assistant connection (reuses ha_feed config)
HA_URL = os.getenv("TOKIO_HA_URL", "http://localhost:8123")
HA_TOKEN = os.getenv("TOKIO_HA_TOKEN", "")

# ESPHome entity IDs in Home Assistant (configured when ESP32 is set up)
COFFEE_ENTITY_PREFIX = os.getenv("TOKIO_COFFEE_PREFIX", "philips_coffee")

# Drink commands mapped to HA button entities
DRINKS = {
    "espresso":       {"entity": "button.{prefix}_espresso",
                       "name": "Espresso", "water_ml": 40, "milk_ml": 0,
                       "time_s": 25, "emoji": ""},
    "cafe_solo":      {"entity": "button.{prefix}_coffee",
                       "name": "Cafe Solo", "water_ml": 120, "milk_ml": 0,
                       "time_s": 35, "emoji": ""},
    "cafe_con_leche": {"entity": "button.{prefix}_coffee_milk",
                       "name": "Cafe con Leche", "water_ml": 80, "milk_ml": 80,
                       "time_s": 45, "emoji": ""},
    "cappuccino":     {"entity": "button.{prefix}_cappuccino",
                       "name": "Cappuccino", "water_ml": 60, "milk_ml": 100,
                       "time_s": 50, "emoji": ""},
    "latte":          {"entity": "button.{prefix}_latte",
                       "name": "Latte", "water_ml": 60, "milk_ml": 150,
                       "time_s": 55, "emoji": ""},
    "hot_water":      {"entity": "button.{prefix}_hot_water",
                       "name": "Agua Caliente", "water_ml": 200, "milk_ml": 0,
                       "time_s": 30, "emoji": ""},
}

# Status sensor entity
STATUS_ENTITY = "sensor.{prefix}_status"

# Tokio's reactions to coffee events
COFFEE_REACTIONS = {
    "brewing": [
        "Preparando cafe... puedo oler los bits tostados.",
        "Cafe en camino. La mejor rutina del hacker.",
        "Moliendo granos. Esto va a estar bueno.",
    ],
    "ready": [
        "Cafe listo! Servido como un buen exploit: rapido y limpio.",
        "Tu cafe esta listo. Combustible premium para hackear.",
        "Listo el cafe. Ahora si, a romper cosas.",
    ],
    "error": [
        "Error en la cafetera. Hasta el hardware necesita un break.",
        "La cafetera tiro error. Reiniciando protocolo cafe...",
    ],
    "empty_water": [
        "Sin agua en la cafetera. Alguien que la recargue.",
        "Tanque de agua vacio. No puedo hacer cafe sin H2O.",
    ],
    "empty_beans": [
        "Sin granos de cafe. Necesito mas combustible.",
        "Se acabaron los granos. Emergencia cafetera.",
    ],
}


class CoffeeMachine:
    """Interface to Philips coffee machine via ESPHome + Home Assistant."""

    def __init__(self, ha_url: str = "", ha_token: str = ""):
        self._ha_url = ha_url or HA_URL
        self._ha_token = ha_token or HA_TOKEN
        self._prefix = COFFEE_ENTITY_PREFIX
        self._available = False
        self._status = "unknown"
        self._last_drink = ""
        self._brewing = False
        self._brew_start = 0.0
        self._brew_drink = ""
        self._callback = None
        self._lock = threading.Lock()
        self._drinks_served = 0
        self._last_error = ""
        self._esp32_connected = False
        self._esp32_checked = False
        self._esp32_check_time = 0.0

        if self._ha_token:
            self._available = True
            print(f"[Coffee] ESPHome integration ready (HA: {self._ha_url})")
        else:
            print("[Coffee] No HA token — coffee machine disabled")

    @property
    def available(self) -> bool:
        return self._available

    @property
    def status(self) -> str:
        return self._status

    @property
    def brewing(self) -> bool:
        return self._brewing

    @property
    def drinks_served(self) -> int:
        return self._drinks_served

    def set_callback(self, callback):
        """callback(event_type: str, message: str)"""
        self._callback = callback

    def _ha_call(self, method: str, endpoint: str, json_data: dict = None) -> Optional[dict]:
        """Call Home Assistant REST API."""
        try:
            import requests
            headers = {
                "Authorization": f"Bearer {self._ha_token}",
                "Content-Type": "application/json",
            }
            url = f"{self._ha_url}/api/{endpoint}"
            if method == "POST":
                r = requests.post(url, headers=headers, json=json_data or {}, timeout=5)
            else:
                r = requests.get(url, headers=headers, timeout=5)
            if r.status_code == 404:
                return None  # entity not found in HA — normal if ESP32 not connected
            r.raise_for_status()
            return r.json() if r.content else {}
        except Exception as e:
            self._last_error = str(e)
            return None

    def get_status(self) -> dict:
        """Get coffee machine status from HA."""
        if self._available and not self._esp32_checked:
            # Check once if ESP32 is registered in HA
            entity_id = STATUS_ENTITY.format(prefix=self._prefix)
            result = self._ha_call("GET", f"states/{entity_id}")
            if result:
                self._status = result.get("state", "unknown")
                self._esp32_connected = True
            else:
                self._status = "esperando ESP32"
                self._esp32_connected = False
            self._esp32_checked = True
            self._esp32_check_time = time.time()
        # Re-check every 60s
        if self._available and time.time() - self._esp32_check_time > 60:
            self._esp32_checked = False
        return {
            "available": self._available,
            "status": self._status,
            "brewing": self._brewing,
            "current_drink": self._brew_drink if self._brewing else "",
            "drinks_served": self._drinks_served,
            "last_drink": self._last_drink,
            "esp32_connected": self._esp32_connected,
        }

    def brew(self, drink_type: str) -> dict:
        """Start brewing a drink."""
        if not self._available:
            return {"ok": False, "error": "Coffee machine not available"}

        drink = DRINKS.get(drink_type)
        if not drink:
            available = list(DRINKS.keys())
            return {"ok": False, "error": f"Unknown drink: {drink_type}. Options: {available}"}

        if self._brewing:
            return {"ok": False, "error": f"Already brewing: {self._brew_drink}"}

        # Press the button via HA
        entity_id = drink["entity"].format(prefix=self._prefix)
        result = self._ha_call("POST", "services/button/press", {"entity_id": entity_id})

        if result is not None:
            with self._lock:
                self._brewing = True
                self._brew_start = time.time()
                self._brew_drink = drink["name"]

            # Start brew monitor thread
            threading.Thread(
                target=self._brew_monitor,
                args=(drink_type, drink["time_s"]),
                daemon=True,
            ).start()

            if self._callback:
                import random
                msg = random.choice(COFFEE_REACTIONS["brewing"])
                self._callback("brewing", msg)

            return {"ok": True, "drink": drink["name"], "time_s": drink["time_s"]}
        else:
            return {"ok": False, "error": f"HA API failed: {self._last_error}"}

    def _brew_monitor(self, drink_type: str, estimated_time: float):
        """Monitor brewing progress and notify when done."""
        time.sleep(estimated_time)
        with self._lock:
            self._brewing = False
            self._last_drink = drink_type
            self._drinks_served += 1

        if self._callback:
            import random
            msg = random.choice(COFFEE_REACTIONS["ready"])
            self._callback("ready", msg)

    def get_drinks_menu(self) -> list[dict]:
        """Return available drinks."""
        return [
            {"id": k, "name": v["name"], "water_ml": v["water_ml"],
             "milk_ml": v["milk_ml"], "time_s": v["time_s"]}
            for k, v in DRINKS.items()
        ]

    def get_status_text(self) -> str:
        """Short status text for AI brain context."""
        if self._brewing:
            elapsed = int(time.time() - self._brew_start)
            return f"preparando {self._brew_drink} ({elapsed}s)"
        if self._drinks_served > 0:
            return f"lista ({self._drinks_served} cafes servidos)"
        return self._status
