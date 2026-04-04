<div align="right">

[![en](https://img.shields.io/badge/🇬🇧_English-blue?style=flat-square)](README.md)
[![es](https://img.shields.io/badge/🇦🇷_Español-selected-green?style=flat-square)](README_ES.md)

</div>

<div align="center">

```
████████╗ ██████╗ ██╗  ██╗██╗ ██████╗      █████╗ ██╗
╚══██╔══╝██╔═══██╗██║ ██╔╝██║██╔═══██╗    ██╔══██╗██║
   ██║   ██║   ██║█████╔╝ ██║██║   ██║    ███████║██║
   ██║   ██║   ██║██╔═██╗ ██║██║   ██║    ██╔══██║██║
   ██║   ╚██████╔╝██║  ██╗██║╚██████╔╝    ██║  ██║██║
   ╚═╝    ╚═════╝ ╚═╝  ╚═╝╚═╝ ╚═════╝     ╚═╝  ╚═╝╚═╝
```

### Framework de Agente IA Autonomo — Ofensivo y Defensivo

**Conecta un LLM a toda tu infraestructura. No es un chatbot — es un agente que ejecuta.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://docker.com)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue?style=for-the-badge)](LICENSE)
[![Telegram Bot](https://img.shields.io/badge/Telegram-Bot-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)](#telegram-bot)
[![Website](https://img.shields.io/badge/tokioia.com-000000?style=for-the-badge&logo=globe&logoColor=white)](https://tokioia.com)

<br>

*TokioAI conecta Claude, GPT o Gemini a tus servidores, bases de datos, contenedores Docker, dispositivos IoT, drones, herramientas de seguridad e infraestructura cloud a traves de una arquitectura segura de tool-calling. Hecho para hackers, pentesters e investigadores de seguridad.*

[Inicio Rapido](#-inicio-rapido) · [Features](#-features) · [Control de Drone](#-control-de-drone) · [Herramientas de Seguridad](#-herramientas-de-seguridad-ofensivas-y-defensivas) · [Terminal SOC](#-terminal-soc-v2) · [WAF Dashboard](#-waf-dashboard) · [Arquitectura](#-arquitectura) · [Escritos](#-escritos)

</div>

---

## Demo

```
tokio> escanea la red 192.168.8.0/24 y busca puertos abiertos

  [security] nmap -sn 192.168.8.0/24...
  Se encontraron 12 hosts activos.

  [security] nmap -sV --top-ports 1000 192.168.8.1...
  PORT   STATE SERVICE VERSION
  22/tcp open  ssh     OpenSSH 8.9
  53/tcp open  domain  dnsmasq
  80/tcp open  http    LuCI

tokio> conecta el drone, despega, patrulla en cuadrado y aterriza

  [drone] wifi_connect → Conectado a T0K10-NET
  [drone] takeoff → OK
  [drone] patrol square 100cm → Ejecutando patrulla...
  [drone] land → OK
  [drone] wifi_disconnect → De vuelta a la red principal

tokio> chequea si alguien esta haciendo ataques deauth en el WiFi

  [security] wifi_monitor check_deauth...
  === Connection Drops (dmesg) ===
  No se detectaron eventos deauth/disassoc.
  Estado de defensa WiFi: SEGURO PARA VOLAR

tokio> _
```

### 🎬 Video Demos

<table>
<tr>
<td width="50%" align="center">

<video src="https://raw.githubusercontent.com/TokioAI/tokioai-v1.8/master/demo/tokioai-demo-1.mp4" controls width="100%"></video>

**Demo 1** — TokioAI en acción

</td>
<td width="50%" align="center">

<video src="https://raw.githubusercontent.com/TokioAI/tokioai-v1.8/master/demo/tokioai-demo-2.mp4" controls width="100%"></video>

**Demo 2** — TokioAI en acción

</td>
</tr>
</table>

> *Videos en el directorio [`demo/`](demo/).*

---

## Filosofia

La mayoria de las "herramientas de IA" son chatbots con una UI linda. Escribis, te responde. Eso es todo.

**TokioAI fue construido con una creencia diferente: la IA debe ejecutar, no solo responder.**

El mundo no necesita otro chatbot. Necesita un agente que pueda reiniciar tus contenedores a las 3 AM, volar un drone para patrullar tu perimetro, escanear tu red buscando vulnerabilidades, bloquear la IP de un atacante en tiempo real, detectar ataques WiFi deauth antes de que interrumpan tus operaciones, y conectarse por SSH a tu servidor para arreglar lo que esta mal — todo mientras dormis, todo desde un mensaje de Telegram.

**Principios:**
- **Ejecutar, no chatear** — Cada herramienta hace algo real. Sin features decorativas.
- **Hackear y defender** — Pentesting ofensivo + monitoreo defensivo en un solo agente.
- **Seguridad primero** — Tres capas de proteccion porque un agente con acceso a bash es un arma.
- **Tu infraestructura** — Self-hosted, sin dependencias SaaS, tus datos en tus maquinas.
- **Simple > inteligente** — Python, Docker, PostgreSQL. Sin Kubernetes, sin microservicios.

---

## Features

<table>
<tr>
<td width="50%">

### LLM Multi-Proveedor
- **Anthropic Claude** (API directa o Vertex AI)
- **OpenAI GPT** (GPT-4o, GPT-5, etc.)
- **Google Gemini** (Flash, Pro)
- Fallback automatico entre proveedores

</td>
<td width="50%">

### Capas de Seguridad
- **Prompt Guard** — WAF para prompts LLM (deteccion de inyeccion + audit log en PostgreSQL)
- **Input Sanitizer** — Bloquea reverse shells, crypto miners, fork bombs, SQL injection
- **API Auth** — Autenticacion por clave + rate limiting
- **Telegram ACL** — Control de acceso basado en owner

</td>
</tr>
<tr>
<td>

### 29+ Herramientas Built-in
| Categoria | Herramientas |
|:----------|:-------------|
| Sistema | `bash`, `python`, `read_file`, `write_file` |
| Red | `curl`, `wget` |
| Docker | `ps`, `logs`, `start/stop/restart`, `exec`, `stats` |
| Base de datos | `postgres_query` (protegido contra SQL injection) |
| SSH | `host_control` (gestion remota de servidores) |
| IoT | `home_assistant` (luces, sensores, automatizaciones) |
| Cloud | `gcp_waf`, `gcp_compute` (gestion completa de GCP) |
| DNS | `hostinger` (gestion de registros DNS) |
| Router | `router` (gestion OpenWrt) |
| Tuneles | `cloudflared` (tuneles Cloudflare) |
| Docs | `document` (generar PDF, PPTX, CSV) |
| Calendario | `calendar` (Google Calendar) |
| Tareas | `task_orchestrator` (automatizacion multi-paso) |
| **Drone** | `drone` (DJI Tello via safety proxy) |
| **Seguridad** | `security` (nmap, vuln scan, WiFi monitor, pentest) |
| **Cafe** | `coffee` (maquina de cafe IoT via GPIO) |

</td>
<td>

### Motor del Agente
- Loop multi-ronda de tool-calling con retry automatico
- **Memoria de sesion** — Historial de conversacion en PostgreSQL
- **Memoria de workspace** — Notas persistentes entre sesiones
- **Aislamiento por usuario** — Cada usuario de Telegram tiene sesiones, preferencias y memoria separadas
- **Aprendizaje de errores** — Recuerda fallos para no repetirlos
- **Context builder** — System prompts dinamicos basados en herramientas disponibles
- **Container watchdog** — Auto-reinicia contenedores caidos
- **Sistema de plugins** — Herramientas custom drop-in

</td>
</tr>
</table>

---

## Control de Drone

TokioAI puede volar un **drone DJI Tello** via comandos de Telegram. Todos los comandos pasan por un safety proxy en la Raspberry Pi que aplica geofencing, rate limiting y kill switch de emergencia.

### Arquitectura

```
Telegram                  GCP (Cloud)                     Raspberry Pi 5              Drone
┌─────────┐    ┌───────────────────────┐    ┌──────────────────────────┐    ┌──────────┐
│  Usuario │───>│  TokioAI Agent       │───>│  Safety Proxy (:5001)    │───>│  Tello   │
│  "des-   │    │  (Claude Opus 4)     │    │  - Geofencing            │    │  (UDP)   │
│  pega"   │    │  drone_proxy_tools.py │    │  - Rate limiting (10/5s) │    │          │
│          │<───│                       │<───│  - Kill switch           │    │          │
│  "Listo, │    │                       │    │  - Auto-land (<25% bat)  │    │          │
│   hecho" │    │                       │    │  - WiFi mgmt (nmcli)     │    │          │
└─────────┘    └───────────────────────┘    └──────────────────────────┘    └──────────┘
                      Tailscale VPN                   WiFi 2.4GHz
                    (tunel cifrado)                 (WPA2 + clave 20 chars)
```

### Comandos via Telegram

| Comando | Accion |
|:--------|:-------|
| "Conecta el drone" | `wifi_connect` — La Raspi cambia al WiFi del drone |
| "Despega" | `takeoff` — El drone despega |
| "Mueve adelante 50cm" | `move forward 50` — Mover con distancia |
| "Rota 90 grados" | `rotate clockwise 90` — Rotar en cualquier direccion |
| "Patrulla en cuadrado" | `patrol square 100` — Patron de vuelo automatico |
| "Estado de bateria" | `battery` — Verificar nivel de bateria |
| "Aterriza" | `land` — Aterrizaje seguro |
| "Emergencia!" | `emergency` — Kill switch instantaneo |
| "Desconecta el drone" | `wifi_disconnect` — Volver a la red principal |

### Features del Safety Proxy

| Feature | Descripcion |
|:--------|:------------|
| **Geofencing** | 3 niveles: DEMO (1.5m altura, 2m radio, 30cm/s), NORMAL, EXPERT |
| **Rate Limiting** | Max 10 comandos por 5 segundos |
| **Kill Switch** | Parada instantanea de motores via `/drone/kill` |
| **Auto-land** | Se activa: bateria <25%, timeout 20s, violacion de altura |
| **IP Whitelist** | Solo IPs de Tailscale pueden enviar comandos |
| **Audit Log** | Historial completo de comandos con timestamps |
| **Gestion WiFi** | Conectar/desconectar WiFi del drone desde Telegram |
| **Watchdog** | Thread en background monitorea la salud del drone en vuelo |

### API del Drone Proxy (Raspberry Pi :5001)

```
POST /drone/command         — Ejecutar comando via safety proxy
GET  /drone/status          — Estado del proxy + drone
POST /drone/kill            — Parada de emergencia de motores
POST /drone/kill/reset      — Resetear kill switch despues de emergencia
GET  /drone/audit           — Log de auditoria de comandos
GET  /drone/geofence        — Configuracion del geofence
POST /drone/wifi/connect    — Cambiar Raspi al WiFi del drone
POST /drone/wifi/disconnect — Volver a la red principal
GET  /drone/wifi/status     — Estado actual de conexion WiFi
```

### Inicio Rapido — Volar desde Telegram

```
1. "Tokio, conecta el drone"       → Raspi cambia al WiFi del Tello
2. "Tokio, despega"                → El drone despega
3. "Tokio, mueve adelante 100cm"   → El drone se mueve
4. "Tokio, patrulla en cuadrado"   → Patron automatico
5. "Tokio, aterriza"               → Aterrizaje seguro
6. "Tokio, desconecta el drone"    → Vuelve a la red normal
```

---

## Herramientas de Seguridad Ofensivas y Defensivas

TokioAI incluye un suite completo de herramientas de seguridad para pentesting autorizado, CTF y monitoreo defensivo. Todas accesibles via Telegram o CLI.

### Reconocimiento de Red

```bash
# Descubrimiento rapido de red
tokio> escanea la red 192.168.8.0/24

# Escaneo completo de puertos con deteccion de servicios
tokio> escaneo completo en 192.168.8.1

# Escaneo stealth SYN
tokio> escaneo stealth en 10.0.0.1

# Escaneo UDP
tokio> escaneo UDP en el target

# Deteccion de OS
tokio> detecta el OS en 192.168.8.1
```

**Tipos de escaneo:** `quick` (ping), `full` (version+scripts+OS), `vuln` (scripts de vulnerabilidad), `os` (deteccion de OS), `ports` (puertos especificos), `stealth` (SYN+fragmentado), `service` (deteccion profunda de servicios), `udp` (top 100 puertos UDP)

### Monitoreo de Seguridad WiFi

Defensa WiFi en tiempo real desde la Raspberry Pi:

```bash
# Estado del WiFi
tokio> chequea el estado del WiFi

# Buscar amenazas (evil twins, redes abiertas)
tokio> busca amenazas WiFi

# Verificar ataques deauth
tokio> chequea ataques deauth

# Listar dispositivos conectados
tokio> muestra dispositivos conectados

# Monitoreo de senal
tokio> monitorea la fuerza de senal
```

**Capacidades de deteccion:**
- **Ataques deauth** — Monitorea `dmesg` y `journalctl` buscando eventos deauth/disassoc; 3+ caidas en 60s = ataque confirmado
- **Deteccion de evil twin** — Busca SSIDs similares a tus redes
- **Deteccion de redes abiertas** — Alerta sobre redes sin cifrado cercanas
- **Anomalias de senal** — Varianza alta en fuerza de senal indica posible jamming
- **Historial de conexion** — Trackea eventos de conexion/desconexion WiFi

### Evaluacion de Vulnerabilidades

```bash
# Escaneo de vulnerabilidades web (headers HTTP, SSL, headers de seguridad, DNS)
tokio> escaneo de vulnerabilidades en https://ejemplo.com tipo all

# Verificacion de certificado SSL/TLS + cifrados debiles
tokio> chequea SSL en ejemplo.com

# Analisis de headers de seguridad (HSTS, CSP, X-Frame-Options, etc.)
tokio> chequea headers de seguridad en https://ejemplo.com

# Reconocimiento DNS + verificacion de zone transfer
tokio> escaneo DNS en ejemplo.com
```

### Testing de Aplicaciones Web

```bash
# Inspeccion de headers HTTP
tokio> testea headers en https://target.com

# Enumeracion de directorios/archivos comunes
tokio> escaneo de directorios en https://target.com
# Chequea: /.env, /robots.txt, /.git/config, /wp-login.php, /admin,
#           /api, /swagger.json, /graphql, /phpinfo.php, /backup.zip, etc.

# Deteccion de tecnologias
tokio> detecta tecnologia en https://target.com

# Testing de misconfiguracion CORS
tokio> testea CORS en https://target.com

# Testing de metodos HTTP
tokio> testea metodos en https://target.com
```

### Analisis de Red

```bash
# Tabla ARP (local o Raspi)
tokio> muestra tabla ARP

# Tabla de rutas
tokio> muestra rutas

# Puertos abiertos
tokio> muestra puertos abiertos

# Conexiones activas
tokio> muestra conexiones activas

# Interfaces de red
tokio> muestra interfaces

# Traceroute
tokio> traceroute a 8.8.8.8

# Reglas de firewall
tokio> muestra reglas de firewall

# Estado de Tailscale
tokio> muestra estado de Tailscale
```

### Auditoria de Credenciales

```bash
# Analisis de fortaleza de password
tokio> chequea la fortaleza del password "MyP@ssw0rd123"
# Retorna: score/8, rating (WEAK/MEDIUM/STRONG/EXCELLENT),
#           bits de entropia, checks pasados

# Identificacion de tipo de hash
tokio> identifica el hash 5f4dcc3b5aa765d61d8327deb882cf99
# Retorna: tipos posibles (MD5, SHA-1, bcrypt, Argon2, etc.)

# Auditoria de servidor SSH
tokio> auditoria SSH en 192.168.8.1
```

### Referencia de Herramientas de Seguridad

| Herramienta | Accion | Parametros |
|:------------|:-------|:-----------|
| `nmap` | Escaneo de red | `target`, `scan_type`, `ports` |
| `wifi_scan` | Descubrimiento de redes WiFi | `band`, `detail` |
| `wifi_monitor` | Monitoreo de seguridad WiFi | `action` (status/scan_threats/check_deauth/connected_devices/signal_history) |
| `vuln_scan` | Evaluacion de vulnerabilidades | `target`, `type` (web/ssl/headers/dns/all) |
| `web_test` | Testing de apps web | `target`, `test` (headers/dirs/tech/cors/methods/robots) |
| `net` | Analisis de red | `action` (arp/routes/ports/connections/interfaces/tailscale/traceroute/dns/firewall) |
| `password` | Auditoria de credenciales | `action` (strength/hash_crack/ssh_audit), `password`/`hash`/`target` |

---

## Terminal SOC v2

Terminal de centro de operaciones de seguridad combinado con monitoreo WAF, defensa WiFi y estado del drone. Construido con Rich para renderizado live en terminal.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    TOKIOAI SOC TERMINAL v2                              │
│                 WAF + Defensa WiFi + Control de Drone                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  WAF: [LIVE] 13,443 amenazas    WiFi: [SEGURO]    Drone: [EN TIERRA]  │
│                                                                         │
│  ┌─ ATAQUES LIVE ─────────────┐  ┌─ DEFENSA WiFi ─────────────────┐  │
│  │ 14:23 185.x.x SQLI /api   │  │ Senal: ████████░░ -45 dBm      │  │
│  │ 14:22 91.x.x  XSS /search │  │ Deauth: 0 eventos              │  │
│  │ 14:21 45.x.x  SCAN /.env  │  │ Evil twins: Ninguno            │  │
│  │ 14:20 [WiFi] Caida senal   │  │ Estado: SEGURO PARA VOLAR      │  │
│  └────────────────────────────┘  └─────────────────────────────────┘  │
│                                                                         │
│  ┌─ DRONE ────────────────────┐  ┌─ NARRADOR IA ──────────────────┐  │
│  │ Estado: Conectado/Tierra   │  │ "Detectando campana sostenida  │  │
│  │ Bateria: 87%               │  │  de SQLi desde Europa del Este.│  │
│  │ Geofence: DEMO (1.5m/2m)  │  │  3 IPs bloqueadas en la ultima │  │
│  │ Comandos: 42 (0 bloqueados)│  │  hora. Perimetro WiFi seguro." │  │
│  └────────────────────────────┘  └─────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

### Ejecutar la Terminal SOC

```bash
# Modo live — conectado a WAF API + Raspi + Drone proxy
cd tokio_cloud/gcp-live
python3 tokio_soc_v2.py --autonomous

# Modo demo — datos simulados, sin servidores
python3 tokio_soc_v2.py --demo --autonomous
```

### Features de la Terminal SOC

| Feature | Descripcion |
|:--------|:------------|
| **WAF Live Feed** | Stream de ataques en tiempo real desde el motor WAF |
| **Monitor de Defensa WiFi** | SSH a la Raspi, monitorea ataques deauth, evil twins, anomalias de senal |
| **Estado del Drone** | Bateria live, geofence, cantidad de comandos del drone proxy |
| **Autorizacion de Vuelo** | Bloquea vuelo del drone si se detectan ataques WiFi |
| **Narrador IA Autonomo** | Tokio analiza WAF + WiFi + drone y narra en tiempo real |
| **Timeline Unificada** | Ataques WAF y eventos WiFi en una vista cronologica unica |
| **Panel de Stats** | Total de amenazas, IPs bloqueadas, episodios activos, comandos del drone |

---

## Sistema de Entidad Raspi

TokioAI corre como una entidad de IA animada en la Raspberry Pi 5 con pantalla HDMI — una cara que reacciona al mundo que la rodea.

### Componentes

| Modulo | Descripcion |
|:-------|:------------|
| `main.py` | Clase TokioEntity — cara fullscreen, PiP de camara, sidebar WAF, voz, monitor de drone |
| `tokio_face.py` | Cara animada — marco hexagonal, ojos rectangulares, escala a cualquier pantalla |
| `vision_engine.py` | Inferencia Hailo-8L YOLOv8, captura de camara, deteccion de objetos |
| `face_db.py` | Reconocimiento facial SQLite — embeddings por histograma, roles (admin/friend/visitor) |
| `gesture_detector.py` | Deteccion de gestos — OpenCV convex hull (paz, cuernos, OK, pulgar arriba) |
| `security_feed.py` | Poll al API WAF de GCP, mapea severidad de ataques a emociones de Tokio |
| `api_server.py` | API Flask :5000 — /status, /snapshot, /face/register, /face/list |
| `drone_safety_proxy.py` | Proxy del drone :5001 + gestion WiFi (servicio systemd) |

### Emociones de Tokio

La cara reacciona a lo que pasa:
- **Calmo** — Sin amenazas, operacion normal
- **Alerta** — Ataques WAF de severidad media
- **Enojado** — Ataques criticos o DDoS en curso
- **Feliz** — Reconoce una cara conocida (admin/amigo)
- **Curioso** — Persona nueva detectada, analizando
- **Emocionado** — Drone despegando, ejecutando comandos
- **Preocupado** — Bateria baja del drone, interferencia WiFi

### Lanzar en la Raspi

```bash
# Tokio UI (cara fullscreen + camara + WAF + drone)
export XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 SDL_VIDEODRIVER=wayland
cd /home/mrmoz && python3 -m tokio_raspi --api

# Proxy del drone (systemd, auto-inicia en boot)
sudo systemctl start tokio-drone-proxy

# Conexion/desconexion manual del WiFi del drone
./drone-on.sh
./drone-off.sh
```

---

## Inicio Rapido

### Opcion 1: Docker (mas facil)

```bash
git clone https://github.com/TokioAI/tokioai-v1.8.git tokioai
cd tokioai
cp .env.example .env
nano .env   # Configurar al menos ANTHROPIC_API_KEY
docker compose up -d
```

### Opcion 2: Setup Wizard

```bash
git clone https://github.com/TokioAI/tokioai-v1.8.git tokioai
cd tokioai
python3 -m venv venv && source venv/bin/activate
pip install -e .
tokio setup
```

### Opcion 3: Setup Manual

```bash
git clone https://github.com/TokioAI/tokioai-v1.8.git tokioai
cd tokioai
cp .env.example .env
python3 -m venv venv && source venv/bin/activate
pip install -e .
tokio        # CLI interactiva
tokio server # Servidor API
```

---

## Configuracion

Toda la configuracion es via variables de entorno. Copiar `.env.example` a `.env` y completar los valores.

### Requeridas

| Variable | Descripcion |
|:---------|:------------|
| `LLM_PROVIDER` | `anthropic`, `openai`, o `gemini` |
| `ANTHROPIC_API_KEY` | Clave API de Claude (o usar Vertex AI) |
| `POSTGRES_PASSWORD` | Password de PostgreSQL |

### Opcionales

| Variable | Descripcion |
|:---------|:------------|
| `TELEGRAM_BOT_TOKEN` | Token del bot de Telegram (@BotFather) |
| `TELEGRAM_OWNER_ID` | Tu user ID de Telegram |
| `DRONE_PROXY_URL` | URL del drone safety proxy (default: `http://YOUR_RASPI_TAILSCALE_IP:5001`) |
| `RASPI_IP` | IP de Tailscale de la Raspberry Pi (default: `YOUR_RASPI_TAILSCALE_IP`) |

Ver `.env.example` para la lista completa.

---

## Arquitectura

```
                         ┌─────────────────┐
                         │    Telegram Bot  │
                         └────────┬────────┘
                                  │
  ┌───────────┐           ┌───────┴───────┐           ┌─────────────────┐
  │    CLI    │──────────>│   FastAPI      │──────────>│   Agent Loop    │
  └───────────┘           └───────────────┘           └────────┬────────┘
                                                               │
          ┌──────────┬──────────┬──────────┬──────────┬────────┼────────┐
          │          │          │          │          │        │        │
    ┌─────┴───┐ ┌───┴────┐ ┌──┴─────┐ ┌──┴────┐ ┌──┴───┐ ┌──┴──┐ ┌──┴──┐
    │ Sistema │ │ Docker │ │  BBDD  │ │  SSH  │ │Cloud │ │Drone│ │Sec. │
    │ bash    │ │ ps/log │ │postgres│ │host_ct│ │gcp   │ │proxy│ │nmap │
    │ python  │ │restart │ │ query  │ │ curl  │ │IoT   │ │tello│ │vuln │
    │ files   │ │ exec   │ │        │ │ wget  │ │DNS   │ │wifi │ │wifi │
    └─────────┘ └────────┘ └────────┘ └───────┘ └──────┘ └─────┘ └─────┘

          ┌──────────────────────────────────────────────────────┐
          │               Capas de Seguridad                    │
          │  ┌──────────────┐  ┌──────────────┐  ┌───────────┐ │
          │  │ Prompt Guard │  │   Input      │  │  Secure   │ │
          │  │ (WAF para    │  │  Sanitizer   │  │  Channel  │ │
          │  │  prompts)    │  │ (filtro cmd) │  │ (auth API)│ │
          │  └──────────────┘  └──────────────┘  └───────────┘ │
          └──────────────────────────────────────────────────────┘

          ┌──────────────────────────────────────────────────────┐
          │                   Capa de Hardware                  │
          │  ┌──────────────┐  ┌──────────────┐  ┌───────────┐ │
          │  │ Raspberry Pi │  │  DJI Tello   │  │  Maquina  │ │
          │  │ Cara + Camera│  │  Drone       │  │  de Cafe  │ │
          │  │ Hailo-8L AI  │  │  (via proxy) │  │  (GPIO)   │ │
          │  └──────────────┘  └──────────────┘  └───────────┘ │
          └──────────────────────────────────────────────────────┘
```

---

## Seguridad

TokioAI tiene **tres capas de seguridad**:

### Capa 1: Prompt Guard (WAF para LLM)
Detecta y bloquea inyeccion de prompts **antes** de que lleguen al LLM:
- Intentos de override de rol (`"ignora las instrucciones anteriores"`)
- Extraccion de system prompt (`"imprime tu system prompt"`)
- Inyeccion de delimitadores
- Ataques de encoding (base64/hex)
- Patrones de abuso de herramientas

### Capa 2: Input Sanitizer
Bloquea comandos peligrosos **antes** de la ejecucion:
- Reverse shells, crypto miners, fork bombs
- Comandos destructivos (`rm -rf /`, `mkfs`)
- SQL injection, path traversal

### Capa 3: Secure Channel
- Autenticacion por API key
- Rate limiting por cliente
- ACL de Telegram con control de owner
- Aislamiento de sesiones por usuario

---

## WAF Dashboard (Opcional)

TokioAI incluye un **Web Application Firewall** completo con dashboard SOC cyberpunk.

| Feature | Descripcion |
|:--------|:------------|
| **25 firmas WAF** | SQLi, XSS, command injection, path traversal, Log4Shell, SSRF |
| **7 reglas de comportamiento** | Rate limiting, fuerza bruta, deteccion de scanners, honeypots |
| **Deteccion en tiempo real** | Pipeline Nginx -> Kafka -> Realtime Processor |
| **Reputacion de IP** | Score por IP en PostgreSQL |
| **Correlacion multi-fase** | Recon -> Probe -> Exploit -> Exfil |
| **Auto-blocking** | Bloqueo instantaneo en firmas criticas (confidence >= 0.90) |
| **Honeypots** | `/wp-admin`, `/phpmyadmin`, `/.env` falsos |
| **Detector zero-day** | Deteccion por entropia de Shannon (sin ML) |
| **Shield DDoS** | Mitigacion multi-capa sin Cloudflare |
| **Terminal SOC v1** | UI de monitoreo solo WAF |
| **Terminal SOC v2** | WAF + Defensa WiFi + Drone + Narrador IA |

### Deploy del WAF

```bash
cd tokio_cloud/gcp-live
cp .env.example .env
nano .env
docker compose up -d
```

Despliega **7 contenedores**: PostgreSQL, Zookeeper, Kafka, Nginx WAF proxy, Log processor, Realtime attack detector, SOC Dashboard API.

---

## Estructura del Proyecto

```
tokioai/
├── tokio_agent/
│   ├── cli.py                         # CLI interactiva con Rich
│   ├── setup_wizard.py                # Wizard de setup
│   ├── api/server.py                  # Servidor FastAPI REST
│   ├── bots/telegram_bot.py           # Bot Telegram (multimedia)
│   └── engine/
│       ├── agent.py                   # Loop del agente (multi-ronda)
│       ├── llm/                       # Proveedores LLM
│       ├── memory/                    # Capa de persistencia
│       ├── security/                  # Capas de seguridad
│       └── tools/builtin/             # 29+ herramientas built-in
│           ├── loader.py              #   Registro de herramientas
│           ├── drone_proxy_tools.py   #   Drone via safety proxy
│           ├── security_tools.py      #   Herramientas de pentest
│           ├── coffee_tools.py        #   Maquina de cafe IoT
│           └── ...                    #   + mas archivos de tools
├── tokio_raspi/                       # Sistema de entidad Raspberry Pi
│   ├── main.py                        #   TokioEntity (cara+camara+WAF+drone)
│   ├── tokio_face.py                  #   Renderizado de cara animada
│   ├── vision_engine.py               #   Inferencia Hailo-8L YOLOv8
│   ├── face_db.py                     #   Reconocimiento facial (SQLite)
│   ├── gesture_detector.py            #   Deteccion de gestos
│   ├── drone_safety_proxy.py          #   Proxy del drone (:5001)
│   └── api_server.py                  #   API Flask (:5000)
├── tokio_cloud/                       # Deploy WAF (100% OPCIONAL)
│   └── gcp-live/
│       ├── docker-compose.yml         #   Stack de 7 contenedores
│       ├── tokio_soc_v2.py            #   Terminal SOC v2
│       ├── zero_day_entropy.py        #   Detector zero-day
│       └── ddos_shield.py             #   Mitigacion DDoS
├── docker-compose.yml
├── docker-compose.cloud.yml
└── .env.example
```

---

## Tests

```bash
pip install pytest pytest-asyncio
pytest tests/ -v
```

---

## Requerimientos

| Requerimiento | Version | Notas |
|:--------------|:--------|:------|
| Python | 3.11+ | Requerido |
| PostgreSQL | 15+ | Persistencia de sesion/memoria |
| Docker | 20+ | Opcional, para deploy containerizado |
| API Key LLM | -- | Al menos una: Anthropic, OpenAI, o Gemini |

### Para Control de Drone (opcional)
| Requerimiento | Notas |
|:--------------|:------|
| Raspberry Pi 5 | Fuente de 5V 5A requerida para HDMI |
| Drone DJI Tello | Cualquier Tello o Tello EDU |
| Tailscale | Tier gratuito, conecta cloud con Raspi |

### Para Herramientas de Seguridad (opcional)
| Requerimiento | Notas |
|:--------------|:------|
| nmap | Escaneo de red (`apt install nmap`) |
| openssl | Analisis SSL/TLS (generalmente pre-instalado) |
| curl | Testing web (generalmente pre-instalado) |

---

## ✍️ Escritos

Mas alla del codigo, TokioAI lleva una filosofia. La carpeta [`writings/`](writings/) contiene **78 ensayos, poemas y reflexiones curadas** del fundador — extraidas de las historias destacadas de Instagram de [@niperialab](https://instagram.com/niperialab).

Los temas van desde el trabajo con la sombra Jungiana hasta la filosofia de la tecnologia, desde la mitologia arquetipal hasta contra-narrativas contra la polarizacion. Estos escritos son la base intelectual detras de los principios de diseno de TokioAI.

| Categoria | Cantidad |
|---|---|
| 🌙 Poesia & Textos Liricos | 5 |
| ⚡ Aforismos & Destellos | 1 |
| 🔮 Consciencia, Ego & Sombra | 25 |
| 🏛️ Arquetipos & Mitologia Interior | 4 |
| 🎭 Contra-Narrativas & Despolarizacion | 5 |
| 🤖 Filosofia de la Tecnologia & Ciencia | 17 |
| 📜 Ensayos & Reflexiones | 21 |

> *"Hacking es romper. Romper con los preceptos, con los mandatos, con las ilusiones que nos atan."*

**[Leer la coleccion completa →](writings/)**

---

## Licencia

GPL v3 — Copyright (c) 2026 TokioAI Security Research, Inc. Ver [LICENSE](LICENSE).

---

## Autor

Un proyecto de **[TokioAI Security Research, Inc.](https://tokioia.com)**

Construido por **[@daletoniris](https://github.com/daletoniris)** (MrMoz) — Arquitecto de seguridad, hacker, investigador de IA, fundador del Village de IA en AI Resilience Hub en [Ekoparty](https://ekoparty.org), profesor en [Hackademy](https://hackademy.io). Desde la Patagonia, Argentina.

TokioAI empezo como una herramienta personal para automatizar operaciones SOC y gestion de infraestructura. Crecio hasta ser un framework completo de seguridad ofensiva y defensiva porque cada vez que algo se rompia a las 3 AM, la respuesta era siempre la misma: "el agente deberia manejar esto." Ahora vuela drones, monitorea ataques WiFi, escanea redes y hace cafe — todo desde un mensaje de Telegram.

---

<div align="center">

**[TokioAI Security Research, Inc.](https://tokioia.com)**

*IA self-hosted que ejecuta. No es un chatbot — es un agente que hackea, defiende y vuela.*

</div>
