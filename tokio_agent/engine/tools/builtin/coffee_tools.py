"""
Coffee Machine Tools — Raspberry Pi GPIO-controlled coffee machine.

Controls two pumps (water + milk) via relay modules connected to GPIO pins.
Supports recipes, queue management, and Tokio emotional reactions for display.

Hardware:
  - Pump 1 (water):  GPIO pin (configurable, default 17)
  - Pump 2 (milk):   GPIO pin (configurable, default 27)
  - Relays:          Active LOW (pump ON when GPIO LOW)

Simulation mode available when no GPIO hardware is present.

Requires: gpiozero (pre-installed on Raspberry Pi OS)
"""
from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

WATER_PUMP_PIN = int(os.environ.get("COFFEE_WATER_PIN", "17"))
MILK_PUMP_PIN = int(os.environ.get("COFFEE_MILK_PIN", "27"))

# Pump flow rates (ml per second) — calibrate with your actual pumps
WATER_FLOW_RATE = float(os.environ.get("COFFEE_WATER_FLOW", "8.0"))
MILK_FLOW_RATE = float(os.environ.get("COFFEE_MILK_FLOW", "6.0"))

# ── Recipes (ml) ─────────────────────────────────────────────────────────────

RECIPES: Dict[str, Dict[str, Any]] = {
    "espresso": {
        "name": "Espresso",
        "name_es": "Café Espresso",
        "water_ml": 30,
        "milk_ml": 0,
        "emoji": "☕",
        "description": "Café puro, intenso, sin concesiones.",
        "tokio_mood": "focused",
    },
    "cafe_solo": {
        "name": "Black Coffee",
        "name_es": "Café Solo",
        "water_ml": 120,
        "milk_ml": 0,
        "emoji": "☕",
        "description": "Café negro largo. Para los que no duermen.",
        "tokio_mood": "determined",
    },
    "cafe_con_leche": {
        "name": "Coffee with Milk",
        "name_es": "Café con Leche",
        "water_ml": 80,
        "milk_ml": 80,
        "emoji": "🥛☕",
        "description": "Mitad café, mitad leche. El clásico argentino.",
        "tokio_mood": "happy",
    },
    "cortado": {
        "name": "Cortado",
        "name_es": "Cortado",
        "water_ml": 60,
        "milk_ml": 30,
        "emoji": "☕✂️",
        "description": "Café cortado con un toque de leche.",
        "tokio_mood": "chill",
    },
    "lagrima": {
        "name": "Lágrima",
        "name_es": "Lágrima",
        "water_ml": 30,
        "milk_ml": 120,
        "emoji": "🥛💧",
        "description": "Mucha leche, una lágrima de café. Para los suaves.",
        "tokio_mood": "gentle",
    },
    "doble": {
        "name": "Double Shot",
        "name_es": "Café Doble",
        "water_ml": 200,
        "milk_ml": 0,
        "emoji": "☕☕",
        "description": "Doble ración. Modo hackathon activado.",
        "tokio_mood": "hyper",
    },
    "leche": {
        "name": "Hot Milk",
        "name_es": "Leche Caliente",
        "water_ml": 0,
        "milk_ml": 150,
        "emoji": "🥛",
        "description": "Solo leche caliente. Simple y reconfortante.",
        "tokio_mood": "cozy",
    },
    "submarino": {
        "name": "Submarino",
        "name_es": "Submarino",
        "water_ml": 0,
        "milk_ml": 180,
        "emoji": "🥛🍫",
        "description": "Leche caliente para sumergir chocolate. Patagónico puro.",
        "tokio_mood": "excited",
    },
}

# ── Tokio Emotional Reactions ────────────────────────────────────────────────

TOKIO_EMOTIONS: Dict[str, Dict[str, Any]] = {
    "focused": {
        "face": "(•_•)",
        "color": "#FF6B35",
        "message": "Preparando con precisión quirúrgica...",
        "animation": "pulse",
    },
    "determined": {
        "face": "(ง •̀_•́)ง",
        "color": "#D32F2F",
        "message": "Café negro. Sin miedo. Sin azúcar.",
        "animation": "shake",
    },
    "happy": {
        "face": "(◕‿◕)",
        "color": "#4CAF50",
        "message": "¡Café con leche! El equilibrio perfecto.",
        "animation": "bounce",
    },
    "chill": {
        "face": "(‾́ ◡ ‾́)",
        "color": "#2196F3",
        "message": "Un cortadito... la vida es buena.",
        "animation": "float",
    },
    "gentle": {
        "face": "(◠‿◠)",
        "color": "#E8D5B7",
        "message": "Una lágrima... suave como la brisa patagónica.",
        "animation": "fade",
    },
    "hyper": {
        "face": "(⊙_⊙)",
        "color": "#FF1744",
        "message": "¡¡DOBLE!! ¿¿QUIÉN NECESITA DORMIR??",
        "animation": "vibrate",
    },
    "cozy": {
        "face": "(◡‿◡)",
        "color": "#FFFDE7",
        "message": "Leche calentita... como un abrazo.",
        "animation": "glow",
    },
    "excited": {
        "face": "(★‿★)",
        "color": "#795548",
        "message": "¡SUBMARINO! La infancia en una taza.",
        "animation": "spin",
    },
    "idle": {
        "face": "(◉‿◉)",
        "color": "#4ECDC4",
        "message": "Tokio Coffee listo. ¿Qué te sirvo?",
        "animation": "breathe",
    },
    "brewing": {
        "face": "(◕ᴗ◕✿)",
        "color": "#FF9800",
        "message": "Sirviendo...",
        "animation": "pour",
    },
    "done": {
        "face": "\\(◕‿◕)/",
        "color": "#8BC34A",
        "message": "¡Listo! Disfrutá tu café. ☕",
        "animation": "celebrate",
    },
    "error": {
        "face": "(╥_╥)",
        "color": "#F44336",
        "message": "Algo salió mal... revisá la máquina.",
        "animation": "glitch",
    },
}

# Tokio quotes while brewing
BREWING_QUOTES = [
    "El mejor código se escribe con café...",
    "Preparando tu café con la misma precisión que un WAF filtra requests...",
    "Los pingüinos de Madryn aprueban esta bebida.",
    "Café: el combustible de toda buena idea.",
    "Cada taza es un acto de resistencia contra el sueño.",
    "En la Patagonia el viento sopla fuerte, pero el café sopla más fuerte.",
    "Modo barista: activado. Modo hacker: nunca desactivado.",
    "Un café no cambia el mundo, pero el mundo no se cambia sin café.",
    "Procesando request... status: brewing... ETA: pronto.",
    "Este café tiene más capas de seguridad que tu WAF.",
]

# ── Hardware / GPIO ──────────────────────────────────────────────────────────

_simulation_mode = False
_water_pump = None
_milk_pump = None
_machine_lock = threading.Lock()
_brew_history: list = []
_current_brew: Optional[Dict] = None


def _init_gpio() -> bool:
    """Initialize GPIO pins for pump relays. Falls back to simulation."""
    global _water_pump, _milk_pump, _simulation_mode

    if _water_pump is not None:
        return True

    try:
        from gpiozero import OutputDevice
        _water_pump = OutputDevice(WATER_PUMP_PIN, active_high=False, initial_value=False)
        _milk_pump = OutputDevice(MILK_PUMP_PIN, active_high=False, initial_value=False)
        _simulation_mode = False
        logger.info("Coffee GPIO initialized: water=GPIO%d, milk=GPIO%d", WATER_PUMP_PIN, MILK_PUMP_PIN)
        return True
    except Exception as e:
        logger.warning("GPIO not available, using simulation mode: %s", e)
        _simulation_mode = True
        return True


def _pump_on(pump: str) -> None:
    """Turn a pump ON."""
    if _simulation_mode:
        logger.info("[SIM] Pump %s ON", pump)
        return
    if pump == "water" and _water_pump:
        _water_pump.on()
    elif pump == "milk" and _milk_pump:
        _milk_pump.on()


def _pump_off(pump: str) -> None:
    """Turn a pump OFF."""
    if _simulation_mode:
        logger.info("[SIM] Pump %s OFF", pump)
        return
    if pump == "water" and _water_pump:
        _water_pump.off()
    elif pump == "milk" and _milk_pump:
        _milk_pump.off()


def _all_pumps_off() -> None:
    """Emergency: all pumps OFF."""
    _pump_off("water")
    _pump_off("milk")


def _dispense(pump: str, ml: float, flow_rate: float) -> float:
    """Dispense a specific amount from a pump. Returns seconds elapsed."""
    if ml <= 0:
        return 0.0
    seconds = ml / flow_rate
    _pump_on(pump)
    time.sleep(seconds)
    _pump_off(pump)
    return seconds


# ── Brew Engine ──────────────────────────────────────────────────────────────

def _brew_coffee(recipe_id: str, custom_water: Optional[int] = None,
                 custom_milk: Optional[int] = None) -> Dict[str, Any]:
    """Execute a coffee brew. Thread-safe."""
    global _current_brew

    recipe = RECIPES.get(recipe_id)
    if not recipe:
        return {
            "status": "error",
            "message": f"Receta '{recipe_id}' no encontrada. Disponibles: {', '.join(RECIPES.keys())}",
            "tokio": TOKIO_EMOTIONS["error"],
        }

    _init_gpio()

    water_ml = custom_water if custom_water is not None else recipe["water_ml"]
    milk_ml = custom_milk if custom_milk is not None else recipe["milk_ml"]

    with _machine_lock:
        _current_brew = {
            "recipe": recipe_id,
            "status": "brewing",
            "started_at": time.strftime("%H:%M:%S"),
            "water_ml": water_ml,
            "milk_ml": milk_ml,
        }

        mode = "[SIM] " if _simulation_mode else ""
        result = {
            "status": "success",
            "recipe": recipe_id,
            "name": recipe["name_es"],
            "emoji": recipe["emoji"],
            "simulation": _simulation_mode,
            "quote": random.choice(BREWING_QUOTES),
        }

        try:
            # Phase 1: Water
            water_time = 0.0
            if water_ml > 0:
                logger.info("%sBrewing %s: dispensing %dml water", mode, recipe_id, water_ml)
                water_time = _dispense("water", water_ml, WATER_FLOW_RATE)

            # Phase 2: Milk
            milk_time = 0.0
            if milk_ml > 0:
                logger.info("%sBrewing %s: dispensing %dml milk", mode, recipe_id, milk_ml)
                milk_time = _dispense("milk", milk_ml, MILK_FLOW_RATE)

            total_time = water_time + milk_time
            result["water_ml"] = water_ml
            result["milk_ml"] = milk_ml
            result["brew_time_seconds"] = round(total_time, 1)
            result["message"] = f"{recipe['emoji']} {recipe['name_es']} listo! ({round(total_time, 1)}s)"
            result["tokio_emotion"] = TOKIO_EMOTIONS.get(recipe["tokio_mood"], TOKIO_EMOTIONS["done"])
            result["tokio_done"] = TOKIO_EMOTIONS["done"]

        except Exception as e:
            _all_pumps_off()
            result["status"] = "error"
            result["message"] = f"Error durante la preparación: {e}"
            result["tokio_emotion"] = TOKIO_EMOTIONS["error"]
            logger.error("Brew error: %s", e)

        finally:
            _all_pumps_off()
            _current_brew = None

        # Log to history
        _brew_history.append({
            "recipe": recipe_id,
            "name": recipe["name_es"],
            "water_ml": water_ml,
            "milk_ml": milk_ml,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": result["status"],
            "simulation": _simulation_mode,
        })
        if len(_brew_history) > 100:
            _brew_history.pop(0)

        return result


# ── Main Tool Entry Point ────────────────────────────────────────────────────

def coffee_control(action: str, params: Optional[Dict[str, Any]] = None) -> str:
    """Main entry point for the coffee machine tool."""
    params = params or {}

    try:
        if action == "brew":
            recipe = params.get("recipe", "cafe_solo")
            custom_water = params.get("water_ml")
            custom_milk = params.get("milk_ml")
            result = _brew_coffee(recipe, custom_water, custom_milk)
            return json.dumps(result, ensure_ascii=False)

        elif action == "recipes":
            recipes_list = []
            for rid, r in RECIPES.items():
                recipes_list.append({
                    "id": rid,
                    "name": r["name_es"],
                    "emoji": r["emoji"],
                    "water_ml": r["water_ml"],
                    "milk_ml": r["milk_ml"],
                    "description": r["description"],
                })
            return json.dumps({"recipes": recipes_list}, ensure_ascii=False)

        elif action == "status":
            _init_gpio()
            return json.dumps({
                "machine": "online",
                "simulation_mode": _simulation_mode,
                "water_pin": WATER_PUMP_PIN,
                "milk_pin": MILK_PUMP_PIN,
                "water_flow_rate": WATER_FLOW_RATE,
                "milk_flow_rate": MILK_FLOW_RATE,
                "current_brew": _current_brew,
                "total_brews": len(_brew_history),
                "tokio": TOKIO_EMOTIONS["idle"],
            }, ensure_ascii=False)

        elif action == "history":
            limit = int(params.get("limit", 10))
            return json.dumps({
                "total": len(_brew_history),
                "recent": _brew_history[-limit:],
            }, ensure_ascii=False)

        elif action == "emotion":
            mood = params.get("mood", "idle")
            emotion = TOKIO_EMOTIONS.get(mood, TOKIO_EMOTIONS["idle"])
            return json.dumps({
                "mood": mood,
                "tokio": emotion,
            }, ensure_ascii=False)

        elif action == "emotions":
            return json.dumps({
                "available_moods": {k: v["face"] for k, v in TOKIO_EMOTIONS.items()},
            }, ensure_ascii=False)

        elif action == "emergency_stop":
            _all_pumps_off()
            return json.dumps({
                "status": "stopped",
                "message": "Todas las bombas detenidas.",
                "tokio": TOKIO_EMOTIONS["error"],
            }, ensure_ascii=False)

        elif action == "test_pumps":
            _init_gpio()
            pump = params.get("pump", "water")
            duration = min(float(params.get("duration", 1.0)), 5.0)
            mode = "[SIM] " if _simulation_mode else ""
            _pump_on(pump)
            time.sleep(duration)
            _pump_off(pump)
            return json.dumps({
                "status": "ok",
                "message": f"{mode}Pump '{pump}' tested for {duration}s",
                "simulation": _simulation_mode,
            }, ensure_ascii=False)

        elif action == "calibrate":
            pump = params.get("pump", "water")
            ml = float(params.get("ml", 100))
            _init_gpio()
            mode = "[SIM] " if _simulation_mode else ""
            rate = WATER_FLOW_RATE if pump == "water" else MILK_FLOW_RATE
            seconds = ml / rate
            return json.dumps({
                "pump": pump,
                "target_ml": ml,
                "estimated_seconds": round(seconds, 1),
                "current_flow_rate": rate,
                "message": (
                    f"{mode}Para calibrar: ejecutá test_pumps con duration={round(seconds,1)}, "
                    f"medí cuántos ml salen realmente, y ajustá COFFEE_{pump.upper()}_FLOW en el env."
                ),
            }, ensure_ascii=False)

        elif action == "custom":
            water_ml = int(params.get("water_ml", 0))
            milk_ml = int(params.get("milk_ml", 0))
            if water_ml == 0 and milk_ml == 0:
                return json.dumps({
                    "status": "error",
                    "message": "Especificá al menos water_ml o milk_ml.",
                }, ensure_ascii=False)
            result = _brew_coffee("cafe_solo", custom_water=water_ml, custom_milk=milk_ml)
            result["name"] = "Café Custom"
            return json.dumps(result, ensure_ascii=False)

        else:
            return json.dumps({
                "status": "error",
                "message": f"Acción '{action}' no reconocida.",
                "available_actions": [
                    "brew", "recipes", "status", "history",
                    "emotion", "emotions", "emergency_stop",
                    "test_pumps", "calibrate", "custom",
                ],
            }, ensure_ascii=False)

    except Exception as e:
        _all_pumps_off()
        logger.error("Coffee tool error: %s", e)
        return json.dumps({
            "status": "error",
            "message": str(e),
            "tokio": TOKIO_EMOTIONS["error"],
        }, ensure_ascii=False)
