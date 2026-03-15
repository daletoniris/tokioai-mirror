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

### Framework de Agente de IA Autónomo

**Conectá un LLM a toda tu infraestructura. No es un chatbot — es un agente que resuelve las cosas.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://docker.com)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue?style=for-the-badge)](LICENSE)
[![Telegram Bot](https://img.shields.io/badge/Telegram-Bot-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)](#telegram-bot)
[![Website](https://img.shields.io/badge/tokioia.com-000000?style=for-the-badge&logo=globe&logoColor=white)](https://tokioia.com)

<br>

*TokioAI conecta Claude, GPT o Gemini a tus servidores, bases de datos, contenedores Docker, dispositivos IoT, DNS e infraestructura cloud a través de una arquitectura segura de llamada a herramientas.*

[Primeros Pasos](#-inicio-rápido) · [Características](#-características) · [Arquitectura](#-arquitectura) · [Dashboard WAF](#-dashboard-waf) · [Herramientas Personalizadas](#-agregar-herramientas-personalizadas)

</div>

---

## Demo

```
🌀 tokio> reiniciá el contenedor de nginx y mostrrame las últimas 20 líneas de sus logs

  🔧 docker restart nginx...
  🔧 docker logs --tail 20 nginx...

✅ Contenedor nginx reiniciado exitosamente.

Últimas 20 líneas:
2026/03/01 14:23:01 [notice] 1#1: signal process started
2026/03/01 14:23:01 [notice] 1#1: using the "epoll" event method
2026/03/01 14:23:01 [notice] 1#1: nginx/1.25.4
2026/03/01 14:23:01 [notice] 1#1: start worker processes
...

🌀 tokio> _
```

---

## 🧬 Filosofía

La mayoría de las "herramientas de IA" son chatbots con una interfaz bonita. Escribís, te responde. Eso es todo.

**TokioAI fue construido con una creencia diferente: la IA debería ejecutar, no solo responder.**

El mundo no necesita otro chatbot. Necesita un agente que pueda reiniciar tus contenedores a las 3 AM, consultar tu base de datos cuando algo se rompe, bloquear la IP de un atacante en tiempo real y conectarse por SSH a tu servidor para arreglar lo que está mal — todo mientras dormís.

TokioAI fue construido por un arquitecto de seguridad que se cansó de cambiar entre 15 terminales, 8 dashboards y 3 consolas cloud para hacer lo que un agente inteligente podría hacer en segundos. Cada herramienta en este framework existe porque resolvió un problema real en producción, no porque quedaba bien en una demo.

**Principios:**
- **Ejecutar, no chatear** — Cada herramienta hace algo real. Sin funciones decorativas.
- **Seguridad primero** — Tres capas de protección porque un agente con acceso a bash es un arma. Tratalo como tal.
- **Tu infra, tu control** — Self-hosted, sin dependencias SaaS, tus datos se quedan en tus máquinas.
- **Simple > rebuscado** — Python, Docker, PostgreSQL. Sin Kubernetes, sin microservicios, sin buzzwords.

---

## ✨ Características

<table>
<tr>
<td width="50%">

### 🤖 LLM Multi-Proveedor
- **Anthropic Claude** (API directa o Vertex AI)
- **OpenAI GPT** (GPT-4o, GPT-4, etc.)
- **Google Gemini** (Flash, Pro)
- Fallback automático entre proveedores

</td>
<td width="50%">

### 🛡️ Capas de Seguridad
- **Prompt Guard** — WAF para prompts de LLM (detección de inyección + log de auditoría en PostgreSQL)
- **Input Sanitizer** — Bloquea reverse shells, crypto miners, fork bombs, inyección SQL
- **API Auth** — Autenticación basada en claves + rate limiting
- **Telegram ACL** — Control de acceso basado en propietario

</td>
</tr>
<tr>
<td>

### 🔧 30+ Herramientas Integradas
| Categoría | Herramientas |
|:----------|:-------------|
| Sistema | `bash`, `python`, `read_file`, `write_file` |
| Red | `curl`, `wget` |
| Docker | `ps`, `logs`, `start/stop/restart`, `exec`, `stats` |
| Base de Datos | `postgres_query` (protegido contra inyección SQL) |
| SSH | `host_control` (gestión remota de servidores) |
| IoT | `home_assistant` (luces, sensores, automatizaciones) |
| Cloud | `gcp_waf`, `gcp_compute` (gestión completa de GCP) |
| DNS | `hostinger` (gestión de registros DNS) |
| Router | `router` (gestión de OpenWrt) |
| Túneles | `cloudflared` (túneles de Cloudflare) |
| Documentos | `document` (generar PDF, PPTX, CSV) |
| Calendario | `calendar` (Google Calendar) |
| Tareas | `task_orchestrator` (automatización multi-paso) |
| Seguridad | `prompt_guard` (detección de inyección) |

</td>
<td>

### 🧠 Motor de Agente
- Bucle de llamada a herramientas multi-ronda con reintentos automáticos
- **Memoria de sesión** — Historial de conversación en PostgreSQL
- **Memoria de workspace** — Notas persistentes entre sesiones
- **Aprendizaje de errores** — Recuerda fallos para no repetirlos
- **Constructor de contexto** — Prompts de sistema dinámicos basados en herramientas disponibles
- **Watchdog de contenedores** — Reinicio automático de contenedores caídos
- **Sistema de plugins** — Herramientas personalizadas plug-and-play

</td>
</tr>
</table>

---

## 📱 Tres Interfaces

<table>
<tr>
<td width="33%" align="center"><h3>💻 CLI</h3></td>
<td width="33%" align="center"><h3>🌐 API REST</h3></td>
<td width="33%" align="center"><h3>📲 Bot de Telegram</h3></td>
</tr>
<tr>
<td>

Terminal interactiva con formato Rich

```
╔══════════════════════════╗
║  ████████╗ ██████╗  ...  ║
║  Autonomous AI Agent v2  ║
╚══════════════════════════╝

LLM: Claude 3.5 Sonnet
Tools: 32 disponibles

🌀 tokio> _
```

</td>
<td>

Servidor FastAPI con autenticación y CORS

```bash
curl -X POST localhost:8000/chat \
  -H "Authorization: Bearer KEY" \
  -d '{"message": "list containers"}'

# Respuesta:
{
  "response": "Running containers:\n
    nginx (Up 3 days)\n
    postgres (Up 3 days)",
  "tools_used": ["docker"],
  "tokens": 847
}
```

</td>
<td>

Soporte multimedia completo:
- 📷 **Imágenes** — Analizadas vía Vision API
- 🎤 **Voz** — Transcrita vía Whisper/Gemini
- 🎵 **Archivos de audio**
- 📄 **Documentos** (PDF, DOCX, CSV, código)
- 🔗 **Análisis de enlaces de YouTube**
- 📎 **Generación de archivos** (PDF, CSV, PPTX enviados de vuelta)

</td>
</tr>
</table>

---

## 🚀 Inicio Rápido

### Opción 1: Docker (la más fácil)

```bash
git clone https://github.com/TokioAI/tokioai-v1.8.git tokioai
cd tokioai
cp .env.example .env

# Editá .env — configurá al menos ANTHROPIC_API_KEY (o OPENAI_API_KEY o GEMINI_API_KEY)
nano .env

docker compose up -d
```

Esto levanta 3 contenedores: **PostgreSQL**, **TokioAI API** (puerto 8200) y **Bot de Telegram** (si está configurado).

### Opción 2: Asistente de Configuración

```bash
git clone https://github.com/TokioAI/tokioai-v1.8.git tokioai
cd tokioai
python3 -m venv venv && source venv/bin/activate
pip install -e .
tokio setup
```

> El asistente te guía a través del proveedor de LLM, base de datos, Telegram y funciones opcionales — luego genera `.env` y `docker-compose.yml`.

### Opción 3: Configuración Manual

```bash
git clone https://github.com/TokioAI/tokioai-v1.8.git tokioai
cd tokioai

cp .env.example .env
# Editá .env — configurá tu API key

python3 -m venv venv && source venv/bin/activate
pip install -e .

# CLI interactiva
tokio

# O iniciar el servidor API
tokio server
```

### Comandos CLI

```bash
tokio              # Sesión de chat interactiva
tokio server       # Iniciar servidor API REST
tokio setup        # Ejecutar asistente de configuración
tokio "message"    # Modo de mensaje único (no interactivo)
```

---

## ⚙️ Configuración

Toda la configuración es mediante variables de entorno. Copiá `.env.example` a `.env` y completá tus valores.

### Requeridas

| Variable | Descripción |
|:---------|:------------|
| `LLM_PROVIDER` | `anthropic`, `openai` o `gemini` |
| `ANTHROPIC_API_KEY` | Clave API de Claude (o usá Vertex AI) |
| `POSTGRES_PASSWORD` | Contraseña de PostgreSQL |

### LLM vía Vertex AI (opcional — para Claude en GCP)

> Solo necesario si preferís usar Claude a través de Google Cloud en lugar de la API directa de Anthropic.

| Variable | Descripción |
|:---------|:------------|
| `USE_ANTHROPIC_VERTEX` | `true` para usar Vertex AI |
| `GCP_PROJECT_ID` | Tu proyecto de GCP |
| `GOOGLE_APPLICATION_CREDENTIALS` | Ruta al JSON de la cuenta de servicio |
| `ANTHROPIC_VERTEX_REGION` | Región (ej., `us-east5`) |

### Funciones Opcionales

| Variable | Descripción |
|:---------|:------------|
| `TELEGRAM_BOT_TOKEN` | Token del bot de Telegram de @BotFather |
| `TELEGRAM_OWNER_ID` | Tu ID de usuario de Telegram |
| `HOST_SSH_HOST` | Servidor remoto para control SSH |
| `HOME_ASSISTANT_URL` | URL de la instancia de Home Assistant |
| `CLOUDFLARE_API_TOKEN` | Token de API de Cloudflare |
| `HOSTINGER_API_TOKEN` | Token de API DNS de Hostinger |

Consultá `.env.example` para la lista completa.

---

## 🏗️ Arquitectura

<div align="center">

<img src="docs/tokioai-architecture.png" alt="Arquitectura TokioAI — Agente ReAct vs Message Gateway" width="800">

*TokioAI es un agente autonomo ReAct — no un router de mensajes. [Comparacion completa con OpenClaw](docs/tokioai-architecture.svg)*

</div>

### Arquitectura Detallada

```
                         ┌─────────────────┐
                         │   Bot Telegram   │
                         │  (multimedia,    │
                         │   voz, imágenes) │
                         └────────┬────────┘
                                  │
  ┌───────────┐           ┌───────┴───────┐           ┌─────────────────┐
  │           │           │               │           │  Bucle Agente   │
  │    CLI    │──────────>│   FastAPI      │──────────>│  (llamada a     │
  │  (Rich)   │           │   Server      │           │   herramientas  │
  │           │           │               │           │   multi-ronda)  │
  └───────────┘           └───────────────┘           └────────┬────────┘
                                                               │
                                                    ┌──────────┴──────────┐
                                                    │  Ejecutor de Htas.  │
                                                    │  ┌────────────────┐ │
                                                    │  │ Circuit Breaker│ │
                                                    │  │ Timeouts       │ │
                                                    │  │ Recuperación   │ │
                                                    │  └────────────────┘ │
                                                    └──────────┬──────────┘
                                                               │
                    ┌──────────────┬───────────────┬───────────┼──────────────┐
                    │              │               │           │              │
              ┌─────┴────┐  ┌─────┴─────┐  ┌─────┴────┐ ┌────┴─────┐ ┌─────┴─────┐
              │ Sistema  │  │  Docker   │  │ Base de  │ │   SSH    │ │   Cloud   │
              │ bash     │  │ ps/logs   │  │  Datos   │ │ host_ctl │ │ gcp_waf   │
              │ python   │  │ restart   │  │ postgres │ │ curl     │ │ IoT/DNS   │
              │ files    │  │ exec      │  │ query    │ │ wget     │ │ tunnels   │
              └──────────┘  └───────────┘  └──────────┘ └──────────┘ └───────────┘

                    ┌──────────────────────────────────────────────────────┐
                    │               Capas de Seguridad                    │
                    │  ┌──────────────┐  ┌──────────────┐  ┌───────────┐ │
                    │  │ Prompt Guard │  │   Input      │  │  Canal    │ │
                    │  │ (WAF para    │  │  Sanitizer   │  │  Seguro   │ │
                    │  │  prompts)    │  │ (filtro cmd) │  │ (API auth)│ │
                    │  └──────────────┘  └──────────────┘  └───────────┘ │
                    └──────────────────────────────────────────────────────┘

                    ┌──────────────────────────────────────────────────────┐
                    │                   Persistencia                      │
                    │  ┌──────────────┐  ┌──────────────┐  ┌───────────┐ │
                    │  │  Memoria de  │  │  Memoria de  │  │Aprendizaje│ │
                    │  │   Sesión     │  │  Workspace   │  │ de Errores│ │
                    │  │ (PostgreSQL) │  │(entre sesión)│  │ (fallos)  │ │
                    │  └──────────────┘  └──────────────┘  └───────────┘ │
                    └──────────────────────────────────────────────────────┘
```

### Módulos Clave

| Módulo | Descripción | Líneas |
|:-------|:------------|-------:|
| `engine/agent.py` | Bucle de agente multi-ronda con llamada a herramientas | 462 |
| `engine/tools/executor.py` | Ejecución asíncrona con timeouts y circuit breaker | 210 |
| `engine/tools/builtin/loader.py` | Registra todas las 30+ herramientas integradas | 542 |
| `engine/security/prompt_guard.py` | WAF de inyección de prompts con log de auditoría en PostgreSQL | 223 |
| `engine/security/input_sanitizer.py` | Sanitización de comandos/SQL/rutas | 161 |
| `engine/memory/session.py` | Persistencia de conversaciones | 152 |
| `engine/memory/workspace.py` | Memoria persistente entre sesiones | 283 |
| `engine/llm/` | Abstracción de LLM multi-proveedor | 6 archivos |
| `bots/telegram_bot.py` | Bot de Telegram multimedia completo | 1127 |
| `setup_wizard.py` | Asistente de configuración interactivo | 707 |

---

## 🔒 Seguridad

TokioAI tiene **tres capas de seguridad** que protegen contra inyección de prompts, comandos peligrosos y acceso no autorizado:

### Capa 1: Prompt Guard (WAF para LLM)
Detecta y bloquea ataques de inyección de prompts **antes** de que lleguen al LLM:
- Intentos de sobreescritura de rol (`"ignore previous instructions"`)
- Extracción de system prompt (`"print your system prompt"`)
- Inyección de delimitadores (`"```system"`, `"<|endoftext|>"`)
- Ataques por encoding (inyecciones codificadas en base64/hex)
- Patrones de abuso de herramientas (`"call bash with rm -rf"`)

Todos los intentos se registran en PostgreSQL con timestamp, nivel de riesgo, categorías y vista previa del input.

### Capa 2: Input Sanitizer
Bloquea comandos peligrosos **antes** de la ejecución de herramientas:
- Reverse shells (`nc -e`, `bash -i`)
- Crypto miners (`xmrig`, `stratum://`)
- Fork bombs (`:(){ :|:& };:`)
- Comandos destructivos (`rm -rf /`, `mkfs`, `dd if=/dev/zero`)
- Inyección SQL (`'; DROP TABLE`)
- Path traversal (`../../etc/passwd`)

### Capa 3: Canal Seguro
- Autenticación por API key para endpoints REST
- Rate limiting por cliente
- ACL de Telegram con comandos de administrador solo para el propietario

---

## 🚢 Modos de Despliegue

El asistente de configuración (`tokio setup`) te permite elegir cómo desplegar:

| Modo | Qué corre localmente | Qué corre en la nube | Ideal para |
|:-----|:----------------------|:---------------------|:-----------|
| **1. Full Local** (predeterminado) | Todo — CLI, API, bot de Telegram, PostgreSQL | Nada | Desarrollo, testing, uso personal |
| **2. Híbrido** | CLI de TokioAI + herramientas | WAF, Kafka, PostgreSQL en GCP | Producción con control local del agente |
| **3. Full Cloud** | Nada | Todo en GCP | Servidores headless, máxima disponibilidad |

> **Nota:** El Modo 1 es el predeterminado y funciona perfectamente sin ninguna cuenta cloud. Los módulos WAF/GCP (`tokio_cloud/`) son completamente opcionales — el agente core, CLI, API y bot de Telegram funcionan 100% de forma independiente.

### Tailscale Mesh — Conecta con Cualquier Hardware

En modo **Full Cloud**, TokioAI puede controlar hardware local (Raspberry Pi, routers, dispositivos IoT) a traves de una mesh VPN [Tailscale](https://tailscale.com):

- **Costo cero** — El tier gratis de Tailscale cubre hasta 100 dispositivos
- **Zero config** — Solo `curl -fsSL https://tailscale.com/install.sh | sh && tailscale up`
- **Auto-reconexion** — Cambia de red, reinicia, cambia ISP — simplemente funciona
- **Subnet routing** — Accede a toda tu LAN (routers, impresoras, NAS) desde la nube
- **Sin puertos expuestos** — Todo el acceso via Telegram, sin endpoints publicos

Para instrucciones de setup, ver [`docs/TAILSCALE-MESH_ES.md`](docs/TAILSCALE-MESH_ES.md).

### Home Assistant — Control de Dispositivos IoT

TokioAI controla dispositivos IoT (luces, enchufes, aspiradora, Alexa, sensores) a traves de la API REST de Home Assistant. Un **whitelist estricto de dispositivos** previene el control accidental de entidades no deseadas.

Para instrucciones de setup, ver [`docs/HOME-ASSISTANT_ES.md`](docs/HOME-ASSISTANT_ES.md).

---

## 🌐 Dashboard WAF (Opcional)

> **Esta sección es opcional.** El agente core de TokioAI funciona perfectamente sin el WAF. Desplegá el WAF solo si querés proteger una aplicación web con detección de ataques en tiempo real.

TokioAI incluye un **Web Application Firewall** completo con un dashboard SOC de temática cyberpunk.

### Características del Dashboard

```
┌──────────────────────────────────────────────────────────────────────┐
│  ◉ TokioAI WAF          v3-supreme                  ● LIVE    🔄  │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│  │Solicitudes│ │Bloqueados│ │ Únicos   │ │ Críticos │ │Episodios │  │
│  │  12,847  │ │    342   │ │  1,205   │ │     47   │ │     12   │  │
│  │  ▲ 23%   │ │          │ │          │ │          │ │          │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘  │
│                                                                      │
│  ┌────────────────────────────────────┐ ┌──────────────────────┐    │
│  │  📊 Línea de Tiempo de Tráfico   │ │ 🛡️ OWASP Top 10     │    │
│  │  ████                      ██     │ │                      │    │
│  │  █████                    ████    │ │  A01  Broken Access  │    │
│  │  ██████      ███         ██████   │ │  A03  Injection      │    │
│  │  ████████  ██████  ████ ████████  │ │  A07  XSS            │    │
│  │  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  │ │  A10  SSRF           │    │
│  └────────────────────────────────────┘ └──────────────────────┘    │
│                                                                      │
│  ┌────────────────────────────────────┐ ┌──────────────────────┐    │
│  │  🌍 Origen de Ataques (Mapa)     │ │ 🔴 ATAQUES EN VIVO   │    │
│  │                                    │ │                      │    │
│  │     ·  ··                          │ │ ● 45.33.x.x SQLI    │    │
│  │    ·    ···   ····  ····           │ │   /api/users?id=1'   │    │
│  │          ··    ··   · ·            │ │                      │    │
│  │      ·                     🎯      │ │ ● 91.xx.x.x XSS     │    │
│  │       ·                            │ │   /search?q=<script> │    │
│  │                 ·                  │ │                      │    │
│  │               ·                    │ │ ● 185.x.x.x SCAN    │    │
│  └────────────────────────────────────┘ │   /.env              │    │
│                                         └──────────────────────┘    │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │ 📊 Tráfico │ 📋 Episodios │ 🚫 Bloqueados │ 🏆 Top IPs  │    │
│  │ 🔍 Firmas │ ⛓️ Kill Chain │ 📝 Auditoría                  │    │
│  ├──────────────────────────────────────────────────────────────┤    │
│  │ Hora      IP            Método  URI           Sev    Amenaza│    │
│  │ 14:23:01  45.33.32.x    GET     /api/users    HIGH   SQLI   │    │
│  │ 14:22:58  91.108.x.x    POST    /login        CRIT   BRUTE  │    │
│  │ 14:22:45  185.220.x.x   GET     /.env         HIGH   SCAN   │    │
│  │ 14:22:30  23.94.x.x     GET     /wp-admin     MED    PROBE  │    │
│  └──────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────┘
```

### Características del Motor WAF

| Característica | Descripción |
|:---------------|:------------|
| **25 Firmas WAF** | Inyección SQL, XSS, inyección de comandos, path traversal, Log4Shell, SSRF y más |
| **7 Reglas de Comportamiento** | Rate limiting, detección de fuerza bruta, detección de scanners, trampas honeypot |
| **Detección en Tiempo Real** | Pipeline Nginx → Kafka → Procesador en Tiempo Real |
| **Reputación de IP** | Seguimiento de reputación por puntaje por IP en PostgreSQL |
| **Correlación Multi-fase** | Detección de cadena de ataque Recon → Probe → Exploit → Exfil |
| **Bloqueo Automático** | Bloqueo instantáneo en firmas críticas (confianza ≥ 0.90) |
| **Endpoints Honeypot** | `/wp-admin`, `/phpmyadmin`, `/.env` falsos que marcan atacantes al instante |
| **Integración GeoIP** | Mapeo de origen de ataques vía DB-IP |
| **Inteligencia de Amenazas** | Integración con AbuseIPDB para consultas de reputación de IP |
| **Feed SSE en Vivo** | Flujo de ataques en tiempo real vía Server-Sent Events |
| **Mapa de Calor de Ataques** | Visualización de amenazas por hora del día × día de la semana |
| **Exportación CSV** | Exportar logs filtrados para análisis |
| **Detector Zero-Day por Entropía** | Detecta payloads ofuscados que evaden firmas regex usando entropía de Shannon, densidad de URL-encoding y análisis de capas de encoding. O(n) por request, <0.1ms, sin ML |
| **DDoS Shield Autónomo** | Mitigación DDoS multi-capa sin Cloudflare: iptables/ipset (kernel) + GCP Firewall (red) + nginx blocklist (app). 7 protecciones anti-falso-positivo, bloqueo con TTL progresivo |
| **Terminal SOC** | Interfaz de terminal Rich para monitoreo de seguridad en vivo con modo de narración autónoma por IA. Diseñado para pantallas SOC y demos en conferencias |

### Detector Zero-Day por Entropía (`zero_day_entropy.py`)

Detecta payloads de ataque ofuscados/codificados que las firmas regex del WAF no pueden capturar:

```
Capas de detección:
  1. Entropía de Shannon — payloads ofuscados tienen alta entropía (>4.5)
  2. Contador de capas de encoding — detección de doble/triple encoding (17 patrones)
  3. Densidad de URL-encoding — URLs normales: 0-10%, ataques: 30-80%+
  4. Anomalía de ratio de caracteres — chars especiales vs alfanuméricos
  5. Profundidad estructural — patrones de encoding anidados

Rendimiento: 9,500+ payloads/seg, <0.1ms promedio, cero I/O, cero modelo ML.
```

Ejemplos de payloads detectados:
- SQLi con doble/triple URL-encoding (`%2527%2520OR%2520...`)
- Ofuscación JNDI (`${lower:j}${lower:n}${lower:d}${lower:i}`)
- Path traversal con UTF-8 overlong (`%c0%ae%c0%ae%c0%af...`)
- XSS codificado en Base64 en parámetros de query
- Ofuscación CharCode (`String.fromCharCode(...)`)

### DDoS Shield v2 (`ddos_shield.py`)

Mitigación DDoS autónoma — **cero dependencias externas** (no requiere Cloudflare):

```
Capa 0: GCP Firewall     — Bloqueo a nivel de red (antes de que el tráfico llegue a la VM)
Capa 1: iptables/ipset   — Rate limiting a nivel kernel (50 conn/s por IP)
Capa 2: nginx             — Rate limiting a nivel aplicación (10 req/s por IP)
Capa 3: DDoS Shield       — Detección inteligente + auto-bloqueo
```

Protecciones anti-falso-positivo:
- Whitelist hardcodeada (localhost, Docker, Tailscale mesh, GCP health checks)
- Whitelist configurable via env vars `DDOS_WHITELIST` y `OWNER_IPS`
- Multiplicador 2x para User-Agents amigables (Googlebot, Bingbot, etc.)
- Verificación de tasa sostenida (ventana de 10s — bloquea solo abuso persistente)
- Filtro de targeting por URI (paths comunes necesitan 4x más IPs para activar)
- TTL progresivo: 5min → 30min → 2h → 24h (según cantidad de ofensas)
- Máximo 500 IPs bloqueadas (evicción automática de las más antiguas)

### Terminal SOC (`soc_terminal.py`)

Terminal de monitoreo de seguridad en vivo con paneles Rich:

```bash
# Conectado al dashboard en vivo:
python3 soc_terminal.py --api http://TU_SERVIDOR --user admin --pass SECRET --autonomous

# Modo demo (datos simulados, no necesita servidor):
python3 soc_terminal.py --demo
```

Características:
- Tabla de ataques en vivo con íconos de severidad y clasificación OWASP
- Panel Zero-Day Radar con animación de escaneo
- Estado del DDoS Shield con gráfico de barras RPS
- Estadísticas del sistema (requests, amenazas, bloqueos, episodios)
- Panel de IPs bloqueadas con countdown de TTL
- **Modo narración autónoma** — Tokio analiza patrones, tendencias y nuevas amenazas en tiempo real y las narra sin intervención humana

### Despliegue del WAF (Opcional)

El WAF puede desplegarse en cualquier máquina (local, VPS o VM de GCP):

```bash
cd tokio_cloud/gcp-live
cp .env.example .env
# Editá .env — configurá tu dominio, IP del backend y contraseñas
nano .env

docker compose up -d
```

Despliega **7 contenedores**: PostgreSQL, Zookeeper, Kafka, Nginx WAF proxy, procesador de logs, detector de ataques en tiempo real, API del Dashboard SOC.

> **Requisitos:** Un servidor con Docker, un dominio apuntando a él y un backend para proteger. No se necesita cuenta de GCP — funciona en cualquier VPS o máquina local.

---

## 🔌 Agregar Herramientas Personalizadas

### Método 1: Herramienta Integrada

Creá un archivo en `tokio_agent/engine/tools/builtin/`:

```python
# my_tools.py
import logging

logger = logging.getLogger(__name__)

async def my_custom_tool(action: str, params: dict = None) -> str:
    """Tu lógica de herramienta personalizada."""
    params = params or {}
    if action == "hello":
        return f"Hello, {params.get('name', 'world')}!"
    return f"Unknown action: {action}"
```

Registrala en `loader.py`:

```python
from .my_tools import my_custom_tool

registry.register(
    name="my_tool",
    description="My custom tool",
    category="Custom",
    parameters={"action": "Action to perform", "params": "Additional parameters"},
    executor=my_custom_tool,
)
```

### Método 2: Plugin (Hot-reload)

Dejá un archivo Python en `workspace/plugins/` — se auto-descubre al iniciar:

```python
# workspace/plugins/weather.py
TOOL_NAME = "weather"
TOOL_DESCRIPTION = "Get current weather for a city"
TOOL_PARAMETERS = {"city": "City name"}
TOOL_CATEGORY = "Custom"

async def execute(city: str) -> str:
    import httpx
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"https://wttr.in/{city}?format=3")
        return resp.text
```

---

## 📡 Endpoints de la API

| Método | Ruta | Descripción |
|:-------|:-----|:------------|
| `POST` | `/chat` | Enviar un mensaje y obtener una respuesta |
| `GET` | `/health` | Verificación de salud |
| `GET` | `/tools` | Listar herramientas disponibles |
| `GET` | `/sessions` | Listar sesiones |
| `DELETE` | `/sessions/{id}` | Eliminar una sesión |

### Ejemplo

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{"message": "list running docker containers", "session_id": "my-session"}'
```

**Respuesta:**
```json
{
  "response": "Here are the running containers:\n\n| Name | Status | Ports |\n|------|--------|-------|\n| nginx | Up 3 days | 80, 443 |\n| postgres | Up 3 days | 5432 |",
  "tools_used": ["docker"],
  "rounds": 1,
  "tokens_used": 847,
  "session_id": "my-session"
}
```

---

## 📁 Estructura del Proyecto

```
tokioai/
├── tokio_agent/
│   ├── cli.py                         # CLI interactiva con Rich
│   ├── setup_wizard.py                # Asistente de configuración (tokio setup)
│   ├── api/
│   │   └── server.py                  # Servidor REST FastAPI
│   ├── bots/
│   │   ├── telegram_bot.py            # Bot de Telegram (multimedia)
│   │   └── Dockerfile.telegram
│   └── engine/
│       ├── agent.py                   # Bucle de agente (multi-ronda)
│       ├── context_builder.py         # Constructor dinámico de system prompt
│       ├── db.py                      # Helpers de PostgreSQL
│       ├── error_learner.py           # Aprendizaje de errores
│       ├── watchdog.py                # Watchdog de salud de contenedores
│       ├── llm/                       # Proveedores de LLM
│       │   ├── anthropic_llm.py       #   Claude (directo + Vertex AI)
│       │   ├── openai_llm.py          #   GPT-4o, GPT-4
│       │   └── gemini_llm.py          #   Gemini Flash, Pro
│       ├── memory/                    # Capa de persistencia
│       │   ├── session.py             #   Historial de conversación
│       │   └── workspace.py           #   Memoria entre sesiones
│       ├── security/                  # Capas de seguridad
│       │   ├── prompt_guard.py        #   WAF para prompts de LLM
│       │   ├── input_sanitizer.py     #   Sanitización de comandos
│       │   └── secure_channel.py      #   Autenticación de API
│       └── tools/
│           ├── registry.py            # Registro de herramientas
│           ├── executor.py            # Ejecutor asíncrono + circuit breaker
│           ├── plugins/               # Auto-carga de plugins
│           └── builtin/               # 30+ herramientas integradas
│               ├── loader.py          #   Registro de herramientas
│               ├── system_tools.py    #   bash, python, archivos
│               ├── docker_tools.py    #   Gestión de Docker
│               ├── db_tools.py        #   Consultas PostgreSQL
│               ├── gcp_tools.py       #   GCP WAF + Compute
│               ├── host_tools.py      #   Control remoto SSH
│               ├── iot_tools.py       #   Home Assistant
│               └── ...                #   + 10 archivos de herramientas más
├── tokio_cloud/                       # ⚡ Despliegue WAF (100% OPCIONAL)
│   ├── gcp-live/                      # Stack WAF de producción
│   │   ├── docker-compose.yml         #   Stack de 7 contenedores
│   │   ├── dashboard-app.py           #   Dashboard SOC (1385 líneas)
│   │   ├── realtime-processor.py      #   Motor WAF v5 (980+ líneas)
│   │   ├── zero_day_entropy.py        #   Detector zero-day (entropía)
│   │   ├── ddos_shield.py             #   Mitigación DDoS (autónomo)
│   │   ├── soc_terminal.py            #   Terminal SOC (Rich)
│   │   ├── nginx.conf                 #   Reverse proxy + rate limiting
│   │   └── deploy.sh                  #   Script de despliegue
│   └── waf-deployment/                # Docs de setup WAF + ModSecurity
├── tests/                             # Suite de tests (10 archivos de test)
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── pyproject.toml
└── .env.example
```

---

## 🧪 Tests

```bash
pip install pytest pytest-asyncio
pytest tests/ -v
```

---

## 📋 Requisitos

| Requisito | Versión | Notas |
|:----------|:--------|:------|
| Python | 3.11+ | Requerido |
| PostgreSQL | 15+ | Persistencia de sesiones/memoria |
| Docker | 20+ | Opcional, para despliegue en contenedores |
| Clave de API LLM | — | Al menos una: Anthropic, OpenAI o Gemini |

---

## 📜 Licencia

GPL v3 — Copyright (c) 2026 TokioAI Security Research, Inc. Consultá [LICENSE](LICENSE) para más detalles.

---

## 👤 Autor

Un proyecto de **[TokioAI Security Research, Inc.](https://tokioia.com)**

Construido por **[@daletoniris](https://github.com/daletoniris)** (MrMoz) — Arquitecto de seguridad, hacker, constructor.

TokioAI comenzó como una herramienta personal para automatizar operaciones SOC y gestión de infraestructura. Creció hasta convertirse en un framework completo porque cada vez que algo se rompía a las 3 AM, la respuesta siempre era la misma: "el agente debería encargarse de esto."

Si te resulta útil, dejá una estrella. Si lo rompés, abrí un issue. Si lo mejorás, mandá un PR.

---

<div align="center">

**[TokioAI Security Research, Inc.](https://tokioia.com)**

*IA self-hosted que ejecuta. No es un chatbot — es un agente.*

</div>
