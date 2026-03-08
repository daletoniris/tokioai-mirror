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

## Notas de Seguridad

- El token de HA se guarda solo en `.env` (gitignored, nunca se commitea)
- La comunicacion entre TokioAI y HA va por Tailscale (cifrado WireGuard) o red local
- El whitelist de dispositivos previene que el agente interactue con entidades no autorizadas
- Ningun puerto de HA se expone a internet publico
