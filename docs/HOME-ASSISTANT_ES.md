# Integracion con Home Assistant

TokioAI se integra con [Home Assistant](https://www.home-assistant.io/) para controlar dispositivos IoT mediante lenguaje natural via Telegram o CLI.

## Arquitectura

```
Usuario (Telegram/CLI)
    |
TokioAI Agent (GCP / cualquier host)
    |  Llamadas REST API
    v
Home Assistant (red local / Tailscale mesh)
    |
Dispositivos (luces, enchufes, aspiradora, Alexa, sensores)
```

TokioAI se comunica con Home Assistant via su REST API. Cuando se despliega en un servidor remoto (ej. GCP), la conectividad se logra a traves de una [mesh VPN Tailscale](./TAILSCALE-MESH_ES.md) — sin exponer puertos a internet.

### LocalTuya (Control Local)

Los dispositivos Tuya se controlan localmente via [LocalTuya](https://github.com/xZetsubou/hass-localtuya) — una integracion custom de HA que se comunica directamente con los dispositivos en la red local, sin pasar por la nube de Tuya.

**Ventajas sobre Tuya Cloud:**
- Sin dependencia de la nube — funciona aunque los servidores de Tuya esten caidos
- Sin errores de "sign invalid" por tokens expirados
- Tiempos de respuesta mas rapidos (comunicacion directa por LAN)
- No requiere internet para controlar dispositivos

**Requisitos:**
- Local keys de los dispositivos Tuya (extraidos con `tinytuya` o `tuya_sharing`)
- Los dispositivos deben estar en la misma red que Home Assistant
- IPs de los dispositivos (descubiertos via `tinytuya.deviceScan()`)

Ver [detalles de configuracion de LocalTuya](#configuracion-de-localtuya) mas abajo.

## Prerequisitos

1. **Home Assistant** instalado y corriendo (Docker recomendado)
2. **Token de acceso de larga duracion** de HA (Perfil > Seguridad > Tokens de larga duracion)
3. **Conectividad de red** entre TokioAI y HA (red local o Tailscale)

## Configuracion

### 1. Desplegar Home Assistant (Docker)

```bash
docker run -d \
  --name homeassistant \
  --restart=unless-stopped \
  --network=host \
  --privileged \
  --stop-timeout 120 \
  -e TZ=America/Buenos_Aires \
  -v /ruta/a/ha-config:/config \
  -v /run/dbus:/run/dbus:ro \
  ghcr.io/home-assistant/home-assistant:stable
```

**Flags importantes:**
- `--stop-timeout 120` — Le da a HA tiempo suficiente para hacer flush de su base de datos SQLite al apagarse. Sin esto, los cambios de configuracion hechos desde la UI pueden perderse al reiniciar (el default son solo 10 segundos).
- `--network=host` — Necesario para descubrimiento de dispositivos (mDNS, SSDP).
- `-v /ruta/a/ha-config:/config` — Directorio de configuracion persistente.

### 2. Configuracion Inicial

Despues del primer inicio, abrir `http://<tu-ip>:8123` y completar el wizard:
- Configurar ubicacion, zona horaria y **sistema de unidades** (metrico recomendado)
- Crear cuenta de administrador
- Agregar integraciones (Tuya, Alexa Media Player, etc.)

### 3. Generar Token de Acceso

1. Ir al perfil de HA (esquina inferior izquierda)
2. Bajar a **Tokens de acceso de larga duracion**
3. Click en **Crear token**, nombrarlo `tokioai`
4. Copiar el token — no se muestra de nuevo

### 4. Configurar TokioAI

Agregar al archivo `.env`:

```env
HOME_ASSISTANT_URL=http://<ip-de-ha>:8123
HOME_ASSISTANT_TOKEN=<tu-token>
```

Si TokioAI corre en otra red (ej. GCP), usar la IP de Tailscale:

```env
HOME_ASSISTANT_URL=http://<ip-tailscale>:8123
```

### 5. Reiniciar TokioAI

```bash
docker compose down && docker compose up -d
```

## Whitelist de Dispositivos

TokioAI usa un **whitelist estricto** para prevenir el control accidental de dispositivos no deseados. Solo las entidades explicitamente listadas pueden ser consultadas o controladas.

### Por que un whitelist?

Sin whitelist, el agente podria intentar interactuar con cualquier entidad de HA — incluyendo entidades internas del sistema, switches de configuracion, o dispositivos que no deberian automatizarse. Esto causaba inestabilidad en versiones anteriores.

### Configurar dispositivos permitidos

Editar `tokio_agent/engine/tools/builtin/iot_tools.py`:

```python
# PRIMARY_DEVICES: dispositivos reales (lo que se lista/reporta)
PRIMARY_DEVICES = {
    "light.smart_bulb":                          "Lampara Cocina",
    "light.smart_bulb_2":                        "Living",
    "switch.mi_enchufe":                         "Enchufe Inteligente",
    "media_player.alexa":                        "Alexa",
    "vacuum.mi_robot":                           "Robot Aspiradora",
}

# ALLOWED_ENTITY_IDS: set completo incluyendo sub-entidades utiles
ALLOWED_ENTITY_IDS = {
    *PRIMARY_DEVICES.keys(),
    "sensor.temperatura",              # sensor de solo lectura
    "sensor.mi_robot_bateria",         # bateria del robot
    "select.mi_robot_modo",            # modo del robot
}
```

**Para encontrar tus entity IDs:**
1. Ir a HA > Ajustes > Dispositivos y Servicios > Entidades
2. O usar la API: `curl -s http://<ip-ha>:8123/api/states -H "Authorization: Bearer <token>" | python3 -m json.tool`

### Agregar un nuevo dispositivo

1. Agregar el `entity_id` a `PRIMARY_DEVICES` (con nombre amigable) o `ALLOWED_ENTITY_IDS`
2. Rebuild y reiniciar TokioAI
3. Probar: pedirle a TokioAI que consulte el estado del dispositivo

## Tipos de Dispositivos Soportados

| Tipo | Acciones | Ejemplo |
|------|----------|---------|
| **Luces** | on, off, toggle, brillo, color | "Prende la luz de la cocina en azul" |
| **Enchufes** | on, off, toggle | "Apaga el enchufe de la cocina" |
| **Aspiradora** | start, stop, pause, volver a base, localizar | "Pone a limpiar la aspiradora" |
| **Media Player** | hablar (TTS), reproducir musica, volumen, estado | "Decile a Alexa que ponga jazz" |
| **Sensores** | leer estado | "Cual es la temperatura?" |

## Solucion de Problemas

### Los cambios en la UI de HA no persisten al reiniciar

**Causa:** El timeout de Docker por defecto (10s) es muy corto para que HA haga flush de su journal WAL de SQLite.

**Solucion:** Siempre usar `--stop-timeout 120` al crear el container:

```bash
docker run -d --stop-timeout 120 ...
```

Si el container ya existe, recrearlo:

```bash
docker stop -t 60 homeassistant
docker rm homeassistant
docker run -d --stop-timeout 120 ... # comando completo arriba
```

### Connection refused desde el container de TokioAI

**Causa:** El container de TokioAI no puede alcanzar HA en la red.

**Soluciones:**
- Si estan en el mismo host: usar `http://host.docker.internal:8123` o `--network=host`
- Si es remoto (via Tailscale): usar la IP de Tailscale (`100.x.x.x`)
- Verificar que HA escucha en todas las interfaces: `ss -tlnp | grep 8123` debe mostrar `0.0.0.0:8123`

### El sistema de unidades muestra Fahrenheit en vez de Celsius

Cambiar en `.storage/core.config`:

```bash
sudo python3 -c "
import json
path = '/ruta/a/ha-config/.storage/core.config'
with open(path) as f:
    data = json.load(f)
data['data']['unit_system_v2'] = 'metric'
with open(path, 'w') as f:
    json.dump(data, f, indent=2)
"
docker restart homeassistant
```

### Dispositivo muestra estado "unknown"

- El dispositivo puede estar offline o no emparejado con HA
- Verificar en la UI de HA > Ajustes > Dispositivos
- Probar apagar y encender el dispositivo

## Configuracion de LocalTuya

LocalTuya reemplaza la integracion de Tuya Cloud para control puramente local de dispositivos.

### 1. Instalar LocalTuya

Descargar [hass-localtuya](https://github.com/xZetsubou/hass-localtuya) y extraer en `<ha-config>/custom_components/localtuya/`.

### 2. Extraer Local Keys de los Dispositivos

```python
# Usando tuya_sharing (requiere cuenta Tuya Cloud)
from tuya_sharing import Manager
m = Manager(...)
m.update_device_cache()
for dev in m.device_map.values():
    print(f"{dev.name}: id={dev.id} key={dev.local_key}")
```

O usar `tinytuya wizard` para un enfoque interactivo.

### 3. Descubrir IPs de Dispositivos

```python
import tinytuya
devices = tinytuya.deviceScan(verbose=False, maxretry=3)
for ip, dev in devices.items():
    print(f"{ip}: id={dev['gwId']}, ver={dev['version']}")
```

### 4. Agregar Integracion en HA

Ir a Ajustes > Integraciones > Agregar > LocalTuya. Elegir modo "Sin Cloud" para operacion 100% local.

Agregar cada dispositivo con:
- **Device ID** y **Local Key** (del paso 2)
- **Direccion IP** (del paso 3)
- **Version de protocolo** (3.3 para la mayoria, 3.4 para enchufes nuevos)

### 5. Configurar Tipos de Entidad

Para cada dispositivo, seleccionar la plataforma apropiada:
- **Luces**: DPS 20 (switch), 22 (brillo), 23 (temp color), 24 (color HSV)
- **Enchufes**: DPS 1 (on/off)
- **Aspiradora**: DPS 5 (estado), 1 (iniciar), 2 (pausa), 4 (modo), 9 (velocidad)

### 6. Remover Tuya Cloud

Una vez confirmado que LocalTuya funciona correctamente, remover la integracion Tuya Cloud para evitar errores de "sign invalid" y entidades duplicadas.

## Integracion con Alexa

Alexa se controla via [alexa_media_player](https://github.com/alandtse/alexa_media_player), una integracion custom de HA.

### Reproduccion de Musica

TokioAI usa un sistema de 3 metodos con fallback para reproduccion confiable:

1. **`notify/alexa_media` con TTS** — Envia un comando de voz como "play jazz en Amazon Music" (mas preciso)
2. **`notify/alexa_media` con ANNOUNCE** — Envia como comando de anuncio
3. **`media_player/play_media` con AMAZON_MUSIC** — Fallback directo por tipo de media

Este enfoque da mejores resultados que el metodo generico `play_media` solo, ya que interpreta la consulta de la misma forma que Alexa interpretaria un comando de voz.

## Notas de Seguridad

- El token de HA se guarda solo en `.env` (gitignored, nunca se commitea)
- La comunicacion entre TokioAI y HA va por Tailscale (cifrado WireGuard) o red local
- El whitelist de dispositivos previene que el agente interactue con entidades no autorizadas
- Ningun puerto de HA se expone a internet publico
- LocalTuya solo se comunica en la red local — ningun dato sale a la nube
