"""
IoT Integration Tools — Home Assistant, Alexa, Lights, Vacuum, Sensors.

All interactions go through the Home Assistant REST API.
Requires: HOME_ASSISTANT_URL + HOME_ASSISTANT_TOKEN in env.
"""
from __future__ import annotations

import colorsys
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# ── Allowed devices whitelist ─────────────────────────────────────────────
# Only these devices can be controlled/queried by TokioAI.
# Prevents accidental writes to unknown entities that caused instability.
#
# PRIMARY_DEVICES: the 6 real devices (what gets listed/reported)
# ALLOWED_ENTITY_IDS: full set including useful sub-entities (what can be accessed)

PRIMARY_DEVICES = {
    "light.lampara_cocina_lampara_cocina":       "Lampara Cocina",
    "light.living_living":                       "Living",
    "light.laboratorio_laboratorio":             "Laboratorio",
    "switch.enchufe_cocina_enchufe_cocina":      "Enchufe Cocina",
    "media_player.jarvis":                       "Jarvis (Alexa)",
    "vacuum.ava_pro_ii_ava_pro_ii":              "AVA PRO II",
}

ALLOWED_ENTITY_IDS = {
    # ── Primary devices (6) ──
    *PRIMARY_DEVICES.keys(),
    # ── Sensors: environment ──
    "sensor.temperatura_casa",                   # temp sensor (read-only)
    # ── Sensors: vacuum ──
    "sensor.ava_pro_ii_ava_pro_ii_bateria",      # vacuum battery (LocalTuya)
    # ── Sensors: Alexa ──
    "sensor.jarvis_proxima_alarma",              # Alexa next alarm
    "sensor.jarvis_proximo_recordatorio",         # Alexa next reminder
    "sensor.jarvis_proximo_temporizador",         # Alexa next timer
    # ── Switches: Alexa ──
    "switch.jarvis_no_molestar",                 # Alexa do-not-disturb
}


def _is_allowed(entity_id: str) -> bool:
    """Check if an entity_id is in the whitelist."""
    return (entity_id or "").strip().lower() in ALLOWED_ENTITY_IDS


# ── Device Memory (persistent cache) ──────────────────────────────────────

_DEVICE_MEMORY_PATH = Path(
    os.getenv("TOKIO_DEVICE_MEMORY_PATH", "/workspace/cli/ha_entities_cache.json")
)
_DEVICE_MEMORY_CACHE: Dict = {"updated_at": "", "entities": {}, "aliases": {}}
_PG_CONN = None
_PG_READY = False


def _pg_enabled() -> bool:
    return os.getenv("TOKIO_IOT_PG_ENABLED", "true").strip().lower() not in {
        "0", "false", "no",
    }


def _pg_connect():
    global _PG_CONN
    if not _pg_enabled():
        return None
    if _PG_CONN is not None:
        return _PG_CONN
    try:
        import psycopg2
        _PG_CONN = psycopg2.connect(
            host=os.getenv("POSTGRES_HOST", "postgres"),
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            database=os.getenv("POSTGRES_DB", "tokio"),
            user=os.getenv("POSTGRES_USER", "tokio"),
            password=os.getenv("POSTGRES_PASSWORD", ""),
            connect_timeout=5,
        )
        _PG_CONN.autocommit = True
        return _PG_CONN
    except Exception as exc:
        logger.debug("PostgreSQL no disponible para memoria IoT: %s", exc)
        _PG_CONN = None
        return None


def _pg_ensure_schema() -> None:
    global _PG_READY
    if _PG_READY:
        return
    conn = _pg_connect()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tokio_device_memory (
                key TEXT PRIMARY KEY,
                value JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.close()
        _PG_READY = True
    except Exception as exc:
        logger.debug("No pude crear schema tokio_device_memory: %s", exc)


def _pg_load() -> Optional[Dict[str, Any]]:
    _pg_ensure_schema()
    conn = _pg_connect()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute("SELECT value FROM tokio_device_memory WHERE key=%s", ("ha_entities_cache",))
        row = cur.fetchone()
        cur.close()
        if row and row[0]:
            data = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            return data
    except Exception as exc:
        logger.debug("No pude leer memoria IoT desde PostgreSQL: %s", exc)
    return None


def _pg_save(data: Dict[str, Any]) -> bool:
    _pg_ensure_schema()
    conn = _pg_connect()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO tokio_device_memory(key, value, updated_at)
               VALUES (%s, %s::jsonb, NOW())
               ON CONFLICT (key) DO UPDATE
               SET value = EXCLUDED.value, updated_at = NOW()""",
            ("ha_entities_cache", json.dumps(data, ensure_ascii=False)),
        )
        cur.close()
        return True
    except Exception as exc:
        logger.debug("No pude guardar memoria IoT en PostgreSQL: %s", exc)
        return False


def _load_device_memory() -> Dict:
    global _DEVICE_MEMORY_CACHE
    pg = _pg_load()
    if isinstance(pg, dict) and pg:
        _DEVICE_MEMORY_CACHE = pg
        return _DEVICE_MEMORY_CACHE
    try:
        if _DEVICE_MEMORY_PATH.exists():
            data = json.loads(_DEVICE_MEMORY_PATH.read_text())
            if isinstance(data, dict):
                _DEVICE_MEMORY_CACHE = data
    except Exception:
        pass
    return _DEVICE_MEMORY_CACHE


def _save_device_memory() -> None:
    pg_ok = _pg_save(_DEVICE_MEMORY_CACHE)
    try:
        _DEVICE_MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        _DEVICE_MEMORY_PATH.write_text(
            json.dumps(_DEVICE_MEMORY_CACHE, ensure_ascii=False, indent=2)
        )
    except Exception as exc:
        if not pg_ok:
            logger.debug("Could not persist HA entity cache: %s", exc)


def _remember_entity(
    entity_id: str,
    friendly_name: str = "",
    domain: str = "",
    state: str = "",
) -> None:
    mem = _load_device_memory()
    entity_id = (entity_id or "").strip().lower()
    if not entity_id or "." not in entity_id:
        return
    if not domain:
        domain = entity_id.split(".", 1)[0]
    aliases = mem.setdefault("aliases", {})
    entities = mem.setdefault("entities", {})
    entities[entity_id] = {
        "entity_id": entity_id,
        "friendly_name": friendly_name or "",
        "domain": domain,
        "state": state or "",
        "last_seen": datetime.now().isoformat(),
    }
    slug = entity_id.split(".", 1)[1]
    aliases[f"{domain}:{slug}"] = entity_id
    aliases[slug] = entity_id
    if friendly_name:
        lowered = friendly_name.strip().lower()
        aliases[f"{domain}:{lowered}"] = entity_id
        aliases[lowered] = entity_id
    mem["updated_at"] = datetime.now().isoformat()
    _save_device_memory()


# ── HA REST helpers ───────────────────────────────────────────────────────

def _ha_config() -> Tuple[str, str]:
    base = os.getenv("HOME_ASSISTANT_URL", "http://host.docker.internal:8123").rstrip("/")
    token = os.getenv("HOME_ASSISTANT_TOKEN", "").strip()
    return base, token


def _ha_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _ha_request(
    method: str,
    path: str,
    json_payload: Optional[Dict] = None,
    timeout: int = 15,
    retries: int = 2,
):
    base, token = _ha_config()
    if not token:
        return None, "HOME_ASSISTANT_TOKEN no configurado"
    url = f"{base}{path}"
    attempt = 0
    while attempt <= retries:
        try:
            resp = requests.request(
                method.upper(), url,
                headers=_ha_headers(token),
                json=json_payload,
                timeout=timeout,
            )
            return resp, ""
        except Exception as exc:
            if attempt >= retries:
                return None, str(exc)
            time.sleep(0.5 * (attempt + 1))
        finally:
            attempt += 1
    return None, "error de conexión no especificado"


def _ha_post(service: str, payload: Dict, timeout: int = 15) -> Tuple[bool, str]:
    resp, err = _ha_request("POST", f"/api/services/{service}", json_payload=payload, timeout=timeout)
    if resp is None:
        if "HOME_ASSISTANT_TOKEN no configurado" in err:
            return False, "❌ HOME_ASSISTANT_TOKEN no configurado."
        return False, err
    if resp.status_code == 200:
        return True, resp.text
    return False, f"HTTP {resp.status_code}: {resp.text[:400]}"


def _ha_get_state(entity_id: str, timeout: int = 10) -> Tuple[bool, str, Dict]:
    resp, err = _ha_request("GET", f"/api/states/{entity_id}", timeout=timeout)
    if resp is None:
        return False, err, {}
    if resp.status_code == 200:
        return True, "", resp.json()
    return False, f"HTTP {resp.status_code}: {resp.text[:300]}", {}


def _ha_list_states(timeout: int = 15) -> Tuple[bool, str, List[Dict]]:
    resp, err = _ha_request("GET", "/api/states", timeout=timeout)
    if resp is None:
        return False, err, []
    if resp.status_code == 200:
        data = resp.json()
        if isinstance(data, list):
            for st in data:
                eid = str(st.get("entity_id", "")).strip().lower()
                if not eid or "." not in eid:
                    continue
                domain = eid.split(".", 1)[0]
                friendly = str(st.get("attributes", {}).get("friendly_name", "")).strip()
                state = str(st.get("state", "")).strip()
                _remember_entity(eid, friendly_name=friendly, domain=domain, state=state)
            return True, "", data
        return False, "Formato inesperado desde /api/states", []
    return False, f"HTTP {resp.status_code}: {resp.text[:300]}", []


# ── Entity resolution ─────────────────────────────────────────────────────

def _resolve_from_memory(domain: str, name: str) -> Optional[str]:
    mem = _load_device_memory()
    aliases = mem.get("aliases", {})
    entities = mem.get("entities", {})
    raw = (name or "").strip().lower()
    if not raw:
        return None
    if raw in entities:
        return raw
    if raw.startswith(f"{domain}."):
        return raw
    for key in (f"{domain}:{raw}", raw):
        if key in aliases:
            resolved = str(aliases[key]).lower()
            if resolved.startswith(f"{domain}."):
                return resolved
    return None


def _resolve_ha_entity(domain: str, name_or_id: str) -> str:
    domain = (domain or "").strip().lower()
    raw = (name_or_id or "").strip()
    if not domain:
        return raw
    if not raw:
        return f"{domain}.unknown"
    lowered = raw.lower()
    if lowered.startswith(f"{domain}."):
        _remember_entity(lowered, domain=domain)
        return lowered
    slug = lowered.replace(" ", "_")
    direct = f"{domain}.{slug}"
    memory_match = _resolve_from_memory(domain, lowered)
    if memory_match:
        return memory_match
    ok, _, states = _ha_list_states()
    if not ok:
        return direct
    for st in states:
        eid = str(st.get("entity_id", "")).lower()
        if eid == direct:
            fn = str(st.get("attributes", {}).get("friendly_name", "")).strip()
            _remember_entity(eid, friendly_name=fn, domain=domain, state=str(st.get("state", "")))
            return eid
    for st in states:
        eid = str(st.get("entity_id", "")).lower()
        if not eid.startswith(f"{domain}."):
            continue
        fn = str(st.get("attributes", {}).get("friendly_name", "")).strip().lower()
        if fn and fn == lowered:
            _remember_entity(eid, friendly_name=fn, domain=domain, state=str(st.get("state", "")))
            return eid
    for st in states:
        eid = str(st.get("entity_id", "")).lower()
        if not eid.startswith(f"{domain}."):
            continue
        fn = str(st.get("attributes", {}).get("friendly_name", "")).strip().lower()
        if fn and lowered in fn:
            _remember_entity(eid, friendly_name=fn, domain=domain, state=str(st.get("state", "")))
            return eid
    _remember_entity(direct, domain=domain)
    return direct


# ── Alexa ─────────────────────────────────────────────────────────────────

_ALEXA_DEFAULT = "Jarvis"
_ALEXA_GENERIC = {"default", "alexa", "echo", "eco", "", "none", "dispositivo", "device"}


def _norm_device(name: Optional[str]) -> str:
    if not name or name.strip().lower() in _ALEXA_GENERIC:
        return _ALEXA_DEFAULT
    return name.strip()


def _resolve_alexa(device: str) -> str:
    name = _norm_device(device)
    slug = name.lower().replace(" ", "_")
    direct = f"media_player.{slug}"
    _, token = _ha_config()
    if not token:
        return direct
    ok, _, states = _ha_list_states(timeout=10)
    if not ok:
        return direct
    candidates = [s for s in states if str(s.get("entity_id", "")).startswith("media_player.")]
    for c in candidates:
        if c.get("entity_id") == direct:
            return direct
    lowered = name.lower()
    for c in candidates:
        fn = str(c.get("attributes", {}).get("friendly_name", "")).lower()
        if fn == lowered:
            return c.get("entity_id", direct)
    for c in candidates:
        fn = str(c.get("attributes", {}).get("friendly_name", "")).lower()
        if lowered in fn:
            return c.get("entity_id", direct)
    return direct


def alexa_speak(text: str, device_name: str = "default") -> str:
    """Make Alexa speak text via Home Assistant."""
    eid = _resolve_alexa(device_name)
    if not _is_allowed(eid):
        return f"🚫 Dispositivo no autorizado: {eid}. Solo se permiten dispositivos del whitelist."
    ok, detail = _ha_post("notify/alexa_media", {
        "message": text, "target": [eid], "data": {"type": "tts"},
    })
    if ok:
        return f"✅ Mensaje enviado a {eid}: '{text}'"
    ok2, detail2 = _ha_post("media_player/play_media", {
        "entity_id": eid, "media_content_id": text, "media_content_type": "tts",
    })
    if ok2:
        return f"✅ TTS enviado a {eid}: '{text}'"
    return f"❌ No pude enviar TTS.\n- notify: {detail}\n- play_media: {detail2}"


def alexa_play_music(query: str, device_name: str = "default") -> str:
    """Play music on Alexa via Home Assistant.

    Uses alexa_media notify service to send a voice command like
    'play [query] on Amazon Music' which gives much more accurate results
    than the generic media_player/play_media approach.
    """
    eid = _resolve_alexa(device_name)
    if not _is_allowed(eid):
        return f"🚫 Dispositivo no autorizado: {eid}. Solo se permiten dispositivos del whitelist."

    # Ensure device is on and at reasonable volume
    _ha_post("media_player/turn_on", {"entity_id": eid}, timeout=10)
    _ha_post("media_player/volume_set", {"entity_id": eid, "volume_level": 0.35}, timeout=10)
    time.sleep(1.0)

    # Method 1: alexa_media notify with voice command (most accurate)
    # This sends a voice command as if you said it to Alexa
    ok, detail = _ha_post("notify/alexa_media", {
        "message": f"play {query}",
        "target": [eid],
        "data": {"type": "tts"},
    })
    if ok:
        time.sleep(4)
        st_ok, _, data = _ha_get_state(eid)
        if st_ok and data.get("state") == "playing":
            title = data.get("attributes", {}).get("media_title", "")
            artist = data.get("attributes", {}).get("media_artist", "")
            now_playing = f" ({title}" + (f" - {artist})" if artist else ")") if title else ""
            return f"✅ Reproduciendo en {eid}: '{query}'{now_playing}"

    # Method 2: alexa_media notify with ANNOUNCE type (sends as command)
    ok2, detail2 = _ha_post("notify/alexa_media", {
        "message": f"play {query} on Amazon Music",
        "target": [eid],
        "data": {"type": "announce"},
    })
    if ok2:
        time.sleep(4)
        st_ok, _, data = _ha_get_state(eid)
        if st_ok and data.get("state") == "playing":
            return f"✅ Reproduciendo en {eid}: '{query}'"

    # Method 3: Fallback to play_media with MUSIC type
    ok3, _ = _ha_post("media_player/play_media", {
        "entity_id": eid,
        "media_content_type": "AMAZON_MUSIC",
        "media_content_id": query,
    })
    if ok3:
        time.sleep(3)
        st_ok, _, data = _ha_get_state(eid)
        if st_ok and data.get("state") == "playing":
            return f"✅ Reproduciendo en {eid}: '{query}' (Amazon Music)"
        return f"⚠️ Comando enviado a {eid}, verificar manualmente."

    return f"❌ No pude reproducir '{query}' en {eid}"


def alexa_status(device_name: str = "default") -> str:
    """Get Alexa device status (SILENT, no TTS)."""
    eid = _resolve_alexa(device_name)
    if not _is_allowed(eid):
        return f"🚫 Dispositivo no autorizado: {eid}. Solo se permiten dispositivos del whitelist."
    ok, err, data = _ha_get_state(eid)
    if not ok:
        return f"❌ No pude obtener estado de {eid}: {err}"
    state = data.get("state", "unknown")
    attrs = data.get("attributes", {})
    vol = int((attrs.get("volume_level", 0) or 0) * 100)
    muted = " (silenciado)" if attrs.get("is_volume_muted") else ""
    result = f"📊 {eid}: {state}, vol={vol}%{muted}"
    title = attrs.get("media_title", "")
    if title:
        result += f"\nReproduciendo: {title}"
        artist = attrs.get("media_artist", "")
        if artist:
            result += f" — {artist}"
    return result


def alexa_set_volume(device_name: str = "default", level: int = 50) -> str:
    """Set Alexa volume 0-100 (SILENT)."""
    eid = _resolve_alexa(device_name)
    if not _is_allowed(eid):
        return f"🚫 Dispositivo no autorizado: {eid}. Solo se permiten dispositivos del whitelist."
    level = max(0, min(100, int(level)))
    ok, detail = _ha_post("media_player/volume_set", {
        "entity_id": eid, "volume_level": level / 100.0,
    })
    return f"✅ Volumen de {eid} → {level}%" if ok else f"❌ Error: {detail}"


# ── Lights ────────────────────────────────────────────────────────────────

_COLOR_MAP = {
    "rojo": [255, 0, 0], "verde": [0, 255, 0], "azul": [0, 0, 255],
    "amarillo": [255, 255, 0], "naranja": [255, 165, 0], "violeta": [128, 0, 128],
    "morado": [128, 0, 128], "rosa": [255, 105, 180], "magenta": [255, 0, 255],
    "cian": [0, 255, 255], "celeste": [0, 255, 255], "blanco": [255, 255, 255],
    "red": [255, 0, 0], "green": [0, 255, 0], "blue": [0, 0, 255],
    "yellow": [255, 255, 0], "orange": [255, 165, 0], "purple": [128, 0, 128],
    "pink": [255, 105, 180], "cyan": [0, 255, 255], "white": [255, 255, 255],
}


def _rgb_to_hs(rgb: List[int]) -> List[float]:
    r, g, b = [max(0, min(255, int(c))) / 255.0 for c in rgb]
    h, s, _ = colorsys.rgb_to_hsv(r, g, b)
    return [round(h * 360.0, 2), round(s * 100.0, 2)]


def ha_control_light(
    entity_id: str,
    state: str = "on",
    brightness: int = 255,
    rgb_color: Optional[List[int]] = None,
    color: str = "",
) -> str:
    """Control Home Assistant light (on/off, brightness, color)."""
    entity_id = _resolve_ha_entity("light", entity_id)
    if not _is_allowed(entity_id):
        return f"🚫 Dispositivo no autorizado: {entity_id}. Solo se permiten dispositivos del whitelist."
    state = state.strip().lower()
    if state not in ("on", "off", "toggle"):
        return "❌ state debe ser 'on', 'off' o 'toggle'"
    payload: Dict[str, Any] = {"entity_id": entity_id}
    if color and not rgb_color:
        rgb_color = _COLOR_MAP.get(color.strip().lower())
    if state == "off":
        ok, detail = _ha_post("light/turn_off", payload)
    elif state == "toggle":
        ok, detail = _ha_post("light/toggle", payload)
    else:
        payload["brightness"] = max(0, min(255, int(brightness)))
        if rgb_color and len(rgb_color) == 3:
            st_ok, _, st_data = _ha_get_state(entity_id)
            modes = list((st_data.get("attributes", {}).get("supported_color_modes", []) or [])) if st_ok else []
            rgb = [max(0, min(255, int(c))) for c in rgb_color]
            if modes and "hs" in modes and not any(m.startswith("rgb") for m in modes):
                payload["hs_color"] = _rgb_to_hs(rgb)
            else:
                payload["rgb_color"] = rgb
        ok, detail = _ha_post("light/turn_on", payload)
    if ok:
        time.sleep(1.0)
        st_ok, _, st_data = _ha_get_state(entity_id)
        final = st_data.get("state", "unknown") if st_ok else "?"
        return f"✅ Light {entity_id} → {state} (estado final: {final})"
    return f"❌ Error controlando light: {detail}"


def ha_control_switch(entity_id: str, state: str) -> str:
    """Control Home Assistant switch (on/off/toggle)."""
    entity_id = _resolve_ha_entity("switch", entity_id)
    if not _is_allowed(entity_id):
        return f"🚫 Dispositivo no autorizado: {entity_id}. Solo se permiten dispositivos del whitelist."
    state = state.strip().lower()
    if state not in ("on", "off", "toggle"):
        return "❌ state debe ser 'on', 'off' o 'toggle'"
    svc = {"on": "switch/turn_on", "off": "switch/turn_off", "toggle": "switch/toggle"}[state]
    ok, detail = _ha_post(svc, {"entity_id": entity_id})
    if ok:
        time.sleep(0.8)
        st_ok, _, st_data = _ha_get_state(entity_id)
        final = st_data.get("state", "?") if st_ok else "?"
        return f"✅ Switch {entity_id} → {state} (estado: {final})"
    return f"❌ Error controlando switch: {detail}"


def ha_control_vacuum(entity_id: str, action: str) -> str:
    """Control Home Assistant vacuum (start/stop/pause/return_to_base/locate)."""
    entity_id = _resolve_ha_entity("vacuum", entity_id)
    if not _is_allowed(entity_id):
        return f"🚫 Dispositivo no autorizado: {entity_id}. Solo se permiten dispositivos del whitelist."
    action = action.strip().lower()
    valid = {"start", "stop", "pause", "return_to_base", "locate", "clean_spot"}
    if action not in valid:
        return f"❌ action debe ser: {', '.join(valid)}"
    ok, detail = _ha_post(f"vacuum/{action}", {"entity_id": entity_id})
    if ok:
        time.sleep(1.2)
        st_ok, _, st_data = _ha_get_state(entity_id)
        return f"✅ Vacuum {entity_id} → {action} (estado: {st_data.get('state', '?') if st_ok else '?'})"
    return f"❌ Error controlando vacuum: {detail}"


def ha_get_state(entity_id: str) -> str:
    """Get state of any HA entity."""
    raw = (entity_id or "").strip()
    if raw and "." not in raw:
        for d in ("light", "switch", "vacuum", "sensor", "binary_sensor", "media_player"):
            candidate = _resolve_ha_entity(d, raw)
            if _is_allowed(candidate):
                ok, _, _ = _ha_get_state(candidate)
                if ok:
                    entity_id = candidate
                    break
    if not _is_allowed(entity_id):
        return f"🚫 Dispositivo no autorizado: {entity_id}. Solo se permiten dispositivos del whitelist."
    ok, err, data = _ha_get_state(entity_id)
    if not ok:
        return f"❌ No pude obtener estado de {entity_id}: {err}"
    state = data.get("state", "unknown")
    attrs = data.get("attributes", {})
    result = f"📊 {entity_id}: {state}"
    if "brightness" in attrs:
        result += f" | brillo={int((attrs['brightness'] / 255) * 100)}%"
    if "rgb_color" in attrs:
        rgb = attrs["rgb_color"]
        result += f" | color=({rgb[0]},{rgb[1]},{rgb[2]})"
    if "battery_level" in attrs:
        result += f" | batería={attrs['battery_level']}%"
    return result


def ha_sync_entities() -> str:
    """Force sync HA entities into persistent cache (only whitelisted)."""
    ok, err, states = _ha_list_states()
    if not ok:
        return f"❌ No pude sincronizar entidades HA: {err}"
    synced = []
    for st in states:
        eid = str(st.get("entity_id", "")).lower()
        if eid in PRIMARY_DEVICES:
            fn = PRIMARY_DEVICES[eid]
            state = str(st.get("state", "")).strip()
            synced.append(f"  - {fn} ({eid}): {state}")
    _save_device_memory()
    lines = [f"✅ {len(PRIMARY_DEVICES)} dispositivos sincronizados:"]
    lines.extend(synced)
    return "\n".join(lines)


def ha_list_entities(domain: str = "light", filter_unavailable: bool = True) -> str:
    """List HA entities for a domain (only whitelisted devices)."""
    domain = (domain or "light").strip().lower()
    mem = _load_device_memory()
    entities = mem.get("entities", {}) if isinstance(mem, dict) else {}
    rows = []
    for eid, info in entities.items():
        if not str(eid).startswith(f"{domain}."):
            continue
        if not _is_allowed(str(eid)):
            continue
        fn = str(info.get("friendly_name", "")).strip()
        st = str(info.get("state", "")).strip()
        if filter_unavailable and st.lower() in ("unavailable", "unknown", "none"):
            continue
        rows.append((str(eid), fn, st))
    if not rows:
        _ha_list_states()
        mem = _load_device_memory()
        entities = mem.get("entities", {}) if isinstance(mem, dict) else {}
        for eid, info in entities.items():
            if str(eid).startswith(f"{domain}."):
                if not _is_allowed(str(eid)):
                    continue
                fn = str(info.get("friendly_name", "")).strip()
                st = str(info.get("state", "")).strip()
                if filter_unavailable and st.lower() in ("unavailable", "unknown", "none"):
                    continue
                rows.append((str(eid), fn, st))
    if not rows:
        return f"⚠️ No encontré entidades autorizadas del dominio '{domain}'."
    lines = [f"📋 Entidades {domain} autorizadas ({len(rows)}):"]
    for eid, fn, st in sorted(rows):
        lines.append(f"  - {eid} ({fn}) [{st}]" if fn else f"  - {eid} [{st}]")
    return "\n".join(lines)


def ha_set_alias(alias: str, entity_id: str) -> str:
    """Set a manual alias for long-term memory."""
    alias = (alias or "").strip().lower()
    entity_id = (entity_id or "").strip().lower()
    if not alias or not entity_id or "." not in entity_id:
        return "❌ alias y entity_id válido son requeridos"
    if not _is_allowed(entity_id):
        return f"🚫 Dispositivo no autorizado: {entity_id}. Solo se permiten dispositivos del whitelist."
    domain = entity_id.split(".", 1)[0]
    mem = _load_device_memory()
    mem.setdefault("aliases", {})[alias] = entity_id
    mem["aliases"][f"{domain}:{alias}"] = entity_id
    _remember_entity(entity_id, domain=domain)
    _save_device_memory()
    return f"✅ Alias guardado: '{alias}' → {entity_id}"


# ── Unified IoT entry point ──────────────────────────────────────────────

def iot_control(action: str, params: Optional[Dict[str, Any]] = None) -> str:
    """
    Unified IoT control tool.

    Actions:
      - alexa_speak: Send TTS to Alexa (params: text, device_name)
      - alexa_play_music: Play music (params: query, device_name)
      - alexa_status: Get status (params: device_name)
      - alexa_set_volume: Set volume (params: device_name, level)
      - light_control: Control light (params: entity_id, state, brightness, color)
      - switch_control: Control switch (params: entity_id, state)
      - vacuum_control: Control vacuum (params: entity_id, action)
      - get_state: Get entity state (params: entity_id)
      - sync_entities: Force sync HA entities
      - list_entities: List entities (params: domain)
      - set_alias: Set alias (params: alias, entity_id)
    """
    params = params or {}
    action = (action or "").strip().lower()

    try:
        if action == "alexa_speak":
            return alexa_speak(
                text=str(params.get("text", "")),
                device_name=str(params.get("device_name", "default")),
            )
        elif action == "alexa_play_music":
            return alexa_play_music(
                query=str(params.get("query", "")),
                device_name=str(params.get("device_name", "default")),
            )
        elif action == "alexa_status":
            return alexa_status(device_name=str(params.get("device_name", "default")))
        elif action == "alexa_set_volume":
            return alexa_set_volume(
                device_name=str(params.get("device_name", "default")),
                level=int(params.get("level", 50)),
            )
        elif action == "light_control":
            return ha_control_light(
                entity_id=str(params.get("entity_id", "")),
                state=str(params.get("state", "on")),
                brightness=int(params.get("brightness", 255)),
                color=str(params.get("color", "")),
            )
        elif action == "switch_control":
            return ha_control_switch(
                entity_id=str(params.get("entity_id", "")),
                state=str(params.get("state", "on")),
            )
        elif action == "vacuum_control":
            return ha_control_vacuum(
                entity_id=str(params.get("entity_id", "")),
                action=str(params.get("vacuum_action", "start")),
            )
        elif action == "get_state":
            return ha_get_state(entity_id=str(params.get("entity_id", "")))
        elif action == "sync_entities":
            return ha_sync_entities()
        elif action == "list_entities":
            return ha_list_entities(
                domain=str(params.get("domain", "light")),
                filter_unavailable=bool(params.get("filter_unavailable", True)),
            )
        elif action == "set_alias":
            return ha_set_alias(
                alias=str(params.get("alias", "")),
                entity_id=str(params.get("entity_id", "")),
            )
        else:
            return json.dumps({
                "ok": False,
                "error": f"Acción no soportada: {action}",
                "supported": [
                    "alexa_speak", "alexa_play_music", "alexa_status", "alexa_set_volume",
                    "light_control", "switch_control", "vacuum_control", "get_state",
                    "sync_entities", "list_entities", "set_alias",
                ],
            }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "action": action, "error": str(exc)}, ensure_ascii=False)
