"""
TokioAI Setup Wizard v2.1 — Interactive installation and configuration.

Three clearly defined deployment modes:
  1. Full Local  — Everything via Docker Compose on this machine
  2. Hybrid      — CLI/tools local + WAF/Kafka/Postgres on GCP
  3. Full Cloud  — Everything in GCP

Auto-detects existing infrastructure and validates each step.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BANNER = r"""
 ______     __  __     __     ______     __  __     __
/\__  _\   /\ \/\ \   /\ \   /\  ___\   /\ \/\ \   /\ \
\/_/\ \/   \ \ \_\ \  \ \ \  \ \ \____  \ \ \_\ \  \ \ \
   \ \_\    \ \_____\  \ \_\  \ \_____\  \ \_____\  \ \_\
    \/_/     \/_____/   \/_/   \/_____/   \/_____/   \/_/

   ╔═══════════════════════════════════════════════════╗
   ║          TokioAI Setup Wizard v2.1                ║
   ║     Autonomous AI Agent — Configuration           ║
   ╚═══════════════════════════════════════════════════╝
"""

DEPLOY_MODES = {
    "1": {
        "name": "Full Local",
        "desc": "Todo en esta maquina via Docker Compose.\n"
                "  Incluye: TokioAI CLI, Telegram Bot, PostgreSQL, todo local.\n"
                "  Ideal para: desarrollo, pruebas, uso personal.",
    },
    "2": {
        "name": "Hybrid",
        "desc": "CLI y tools corren localmente, servicios criticos en GCP.\n"
                "  Local: TokioAI Core, CLI, herramientas\n"
                "  GCP: WAF/Nginx/ModSecurity, Kafka, PostgreSQL\n"
                "  Ideal para: produccion con control local del agente.",
    },
    "3": {
        "name": "Full Cloud",
        "desc": "Todo desplegado en GCP.\n"
                "  GCP: WAF, Kafka, Postgres, TokioAI CLI, Telegram Bot\n"
                "  Ideal para: servidores headless, maxima disponibilidad.",
    },
}


def run_wizard():
    """Run the interactive setup wizard."""
    print(BANNER)
    config: Dict[str, str] = {}
    features: Dict[str, bool] = {}

    # ── Step 1: User info ─────────────────────────────────────────────
    _section("Paso 1: Tu Informacion")
    config["user_name"] = _ask("Como te llamas?", "")
    config["language"] = _ask("Idioma preferido?", "es", ["es", "en", "pt"])
    print()

    # ── Step 2: LLM Provider ─────────────────────────────────────────
    _section("Paso 2: Proveedor LLM")
    print("  1. Claude (Anthropic) — Recomendado: Opus 4.6, Sonnet 4")
    print("  2. OpenAI — GPT-4o, o1, o3")
    print("  3. Gemini — Gemini 2.0 Flash, Pro")
    provider_choice = _ask("Proveedor principal (1/2/3)", "1", ["1", "2", "3"])

    provider_map = {"1": "anthropic", "2": "openai", "3": "gemini"}
    config["LLM_PROVIDER"] = provider_map[provider_choice]

    if provider_choice == "1":
        print("\n  Como conectar con Claude?")
        print("  a. API directa (ANTHROPIC_API_KEY)")
        print("  b. Vertex AI (credenciales GCP)")
        claude_method = _ask("Elige (a/b)", "a", ["a", "b"])

        if claude_method == "a":
            config["ANTHROPIC_API_KEY"] = _ask_secret("ANTHROPIC_API_KEY")
            config["CLAUDE_MODEL"] = _ask("Modelo Claude", "claude-sonnet-4-20250514")
        else:
            config["USE_ANTHROPIC_VERTEX"] = "true"
            config["GCP_PROJECT_ID"] = _ask("GCP Project ID")
            config["ANTHROPIC_VERTEX_REGION"] = _ask("Region Vertex", "us-east5")
            config["GOOGLE_APPLICATION_CREDENTIALS"] = _ask(
                "Ruta a credenciales GCP", "/workspace/credentials.json")
            config["CLAUDE_MODEL"] = _ask("Modelo Claude", "claude-sonnet-4@20250514")
    elif provider_choice == "2":
        config["OPENAI_API_KEY"] = _ask_secret("OPENAI_API_KEY")
        config["OPENAI_MODEL"] = _ask("Modelo OpenAI", "gpt-4o")
    elif provider_choice == "3":
        config["GEMINI_API_KEY"] = _ask_secret("GEMINI_API_KEY")
        config["GEMINI_MODEL"] = _ask("Modelo Gemini", "gemini-2.0-flash")

    # Backup providers
    print("\n  Proveedores de respaldo (fallback)?")
    has_backup = _ask("Configurar fallback? (s/n)", "n", ["s", "n"])
    if has_backup == "s":
        if provider_choice != "1":
            if _ask("  Tienes API key de Anthropic? (s/n)", "n", ["s", "n"]) == "s":
                config["ANTHROPIC_API_KEY"] = _ask_secret("  ANTHROPIC_API_KEY")
        if provider_choice != "2":
            if _ask("  Tienes API key de OpenAI? (s/n)", "n", ["s", "n"]) == "s":
                config["OPENAI_API_KEY"] = _ask_secret("  OPENAI_API_KEY")
        if provider_choice != "3":
            if _ask("  Tienes API key de Gemini? (s/n)", "n", ["s", "n"]) == "s":
                config["GEMINI_API_KEY"] = _ask_secret("  GEMINI_API_KEY")

    # ── Step 3: Deployment mode ──────────────────────────────────────
    _section("Paso 3: Modo de Despliegue")
    for key, mode in DEPLOY_MODES.items():
        print(f"  {key}. {mode['name']}")
        for line in mode["desc"].split("\n"):
            print(f"     {line}")
        print()

    # Auto-detect existing infrastructure
    print("  Detectando infraestructura existente...")
    detected = _auto_detect()
    if detected:
        print(f"  Detectado: {', '.join(detected)}")
        if "docker" in detected and "gcp" not in detected:
            print("  Sugerencia: Modo 1 (Full Local)")
        elif "gcp" in detected:
            print("  Sugerencia: Modo 2 (Hybrid)")
    print()

    deploy = _ask("Modo de despliegue (1/2/3)", "1", ["1", "2", "3"])

    # ── Step 4: GCP Infrastructure ───────────────────────────────────
    if deploy in ("2", "3"):
        _section("Paso 4: Infraestructura GCP")
        has_gcp = _ask("Ya tienes infraestructura WAF en GCP? (s/n)", "n", ["s", "n"])

        if has_gcp == "s":
            print("\n  Perfecto. Integramos con tu infra existente.")
            print("  NO modificaremos nada en GCP, solo nos conectamos.\n")

            if not config.get("GCP_PROJECT_ID"):
                config["GCP_PROJECT_ID"] = _ask("GCP Project ID")
            config["GCP_INSTANCE_NAME"] = _ask("Nombre instancia WAF")
            config["GCP_ZONE"] = _ask("Zona GCP", "us-central1-a")

            # Auto-detect GCP instance
            if _has_gcloud():
                print("\n  Detectando infraestructura...")
                gcp_info = _detect_gcp_infra(
                    config["GCP_PROJECT_ID"], config["GCP_ZONE"], config["GCP_INSTANCE_NAME"])
                if gcp_info:
                    print(f"  Instancia: {gcp_info.get('status', '?')}")
                    if gcp_info.get("external_ip"):
                        print(f"  IP externa: {gcp_info['external_ip']}")
                        config["GCP_WAF_HOST"] = gcp_info["external_ip"]
                    _validate_step("GCP Instance", True)
                else:
                    _validate_step("GCP Instance", False, "No detectada")
                    print("  Configuracion manual:")

            config["GCP_WAF_USER"] = _ask("Usuario SSH para GCP", "tokio")
            config["GCP_WAF_SSH_KEY"] = _ask("Ruta clave SSH para GCP", "")
            if not config.get("GCP_WAF_HOST"):
                config["GCP_WAF_HOST"] = _ask("IP del host GCP")

            # PostgreSQL location
            pg_on_gcp = _ask("PostgreSQL esta en la instancia GCP? (s/n)", "s", ["s", "n"])
            if pg_on_gcp == "s":
                config["POSTGRES_HOST"] = config.get("GCP_WAF_HOST", _ask("IP PostgreSQL"))
            else:
                config["POSTGRES_HOST"] = _ask("Host PostgreSQL")
        else:
            print("\n  Crearemos toda la infraestructura en GCP.")
            print("  Se creara: VM, Nginx+ModSecurity, Kafka, PostgreSQL\n")

            if not config.get("GCP_PROJECT_ID"):
                config["GCP_PROJECT_ID"] = _ask("GCP Project ID")
            config["GCP_ZONE"] = _ask("Zona GCP", "us-central1-a")
            config["GCP_MACHINE_TYPE"] = _ask("Tipo de maquina", "e2-medium")
            config["GCP_DISK_SIZE"] = _ask("Disco (GB)", "30")
            config["WAF_DOMAIN"] = _ask("Dominio a proteger (ej: miapp.com)")
            config["WAF_BACKEND"] = _ask("Backend origin URL", "http://localhost:8080")
            config["DEPLOY_GCP_WAF"] = "true"
            config["POSTGRES_HOST"] = "postgres"
    else:
        config["POSTGRES_HOST"] = _ask("Host PostgreSQL", "postgres")

    # ── Step 5: PostgreSQL ───────────────────────────────────────────
    _section("Paso 5: PostgreSQL")
    if not config.get("POSTGRES_HOST"):
        config["POSTGRES_HOST"] = _ask("Host PostgreSQL", "postgres")
    config["POSTGRES_PORT"] = _ask("Puerto", "5432")
    config["POSTGRES_DB"] = _ask("Base de datos", "tokio")
    config["POSTGRES_USER"] = _ask("Usuario", "tokio")
    config["POSTGRES_PASSWORD"] = _ask_secret("Contrasena PostgreSQL")

    # Validate PG connection
    pg_ok = _test_pg(config)
    _validate_step("PostgreSQL", pg_ok, "" if pg_ok else "No se pudo conectar (se validara al iniciar)")

    # ── Step 6: Telegram Bot (optional) ──────────────────────────────
    _section("Paso 6: Telegram Bot (opcional)")
    has_telegram = _ask("Configurar bot de Telegram? (s/n)", "n", ["s", "n"])
    features["telegram"] = has_telegram == "s"
    if has_telegram == "s":
        config["TELEGRAM_BOT_TOKEN"] = _ask_secret("Token del bot (@BotFather)")
        config["TELEGRAM_OWNER_ID"] = _ask("Tu Telegram user_id (owner)")

    # ── Step 7: Host control (optional) ──────────────────────────────
    _section("Paso 7: Control de Host Remoto (opcional)")
    has_host = _ask("Configurar host remoto? (s/n)", "n", ["s", "n"])
    features["host"] = has_host == "s"
    if has_host == "s":
        config["HOST_SSH_HOST"] = _ask("IP o hostname")
        config["HOST_SSH_USER"] = _ask("Usuario SSH", "pi")
        config["HOST_SSH_PORT"] = _ask("Puerto SSH", "22")
        config["HOST_SSH_KEY_PATH"] = _ask("Ruta clave SSH", "~/.ssh/id_rsa")

        # Validate SSH
        ssh_ok = _test_ssh(config["HOST_SSH_HOST"], config["HOST_SSH_USER"],
                           config["HOST_SSH_KEY_PATH"], int(config["HOST_SSH_PORT"]))
        _validate_step("SSH Host", ssh_ok)

    # ── Step 8: IoT / Home Assistant (optional) ──────────────────────
    _section("Paso 8: IoT / Home Assistant (opcional)")
    has_iot = _ask("Configurar Home Assistant? (s/n)", "n", ["s", "n"])
    features["iot"] = has_iot == "s"
    if has_iot == "s":
        config["HOME_ASSISTANT_URL"] = _ask("URL de Home Assistant", "http://host.docker.internal:8123")
        config["HOME_ASSISTANT_TOKEN"] = _ask_secret("Token HA (Long-Lived)")

    # ── Step 9: Router (optional) ────────────────────────────────────
    _section("Paso 9: Control de Router (opcional)")
    has_router = _ask("Configurar router OpenWrt? (s/n)", "n", ["s", "n"])
    features["router"] = has_router == "s"
    if has_router == "s":
        config["ROUTER_HOST"] = _ask("IP del router")
        config["ROUTER_USER"] = _ask("Usuario SSH", "root")
        config["ROUTER_SSH_KEY_PATH"] = _ask("Ruta clave SSH", "")

    # ── Step 10: Cloudflare / DNS (optional) ─────────────────────────
    _section("Paso 10: DNS y Cloudflare (opcional)")
    has_cf = _ask("Configurar Cloudflare? (s/n)", "n", ["s", "n"])
    features["cloudflare"] = has_cf == "s"
    if has_cf == "s":
        config["CLOUDFLARE_API_TOKEN"] = _ask_secret("Cloudflare API Token")
        config["CLOUDFLARE_ACCOUNT_ID"] = _ask("Account ID")
        config["CLOUDFLARED_TUNNEL_ID"] = _ask("Tunnel ID (si tienes)", "")
        config["CLOUDFLARED_TUNNEL_TOKEN"] = _ask("Tunnel Token (si tienes)", "")

    has_hostinger = _ask("Configurar Hostinger DNS? (s/n)", "n", ["s", "n"])
    features["hostinger"] = has_hostinger == "s"
    if has_hostinger == "s":
        config["HOSTINGER_API_TOKEN"] = _ask_secret("Hostinger API Token")

    # ── Step 11: Generate files ──────────────────────────────────────
    _section("Paso 11: Generando Configuracion")

    install_dir = _ask("Directorio de instalacion", os.path.expanduser("~/.tokio"))
    install_path = Path(install_dir)
    install_path.mkdir(parents=True, exist_ok=True)

    # Generate .env
    env_path = install_path / ".env"
    _generate_env(config, env_path)
    _validate_step(f".env -> {env_path}", True)

    # Generate docker-compose.yml
    compose_path = install_path / "docker-compose.yml"
    _generate_compose(config, compose_path, deploy, features)
    _validate_step(f"docker-compose.yml -> {compose_path}", True)

    # Save user preferences
    prefs_path = install_path / "preferences.json"
    prefs = {
        "user_name": config.get("user_name", ""),
        "language": config.get("language", "es"),
        "llm_provider": config.get("LLM_PROVIDER", "anthropic"),
        "deploy_mode": DEPLOY_MODES[deploy]["name"],
    }
    with open(prefs_path, "w") as f:
        json.dump(prefs, f, indent=2, ensure_ascii=False)
    _validate_step(f"preferences.json -> {prefs_path}", True)

    # ── Architecture Summary ─────────────────────────────────────────
    print()
    _print_architecture(deploy, config, features)

    # ── Final Summary ────────────────────────────────────────────────
    print("\n" + "=" * 55)
    print("  Configuracion completada!")
    print("=" * 55)
    print(f"\n  Archivos en: {install_path}")

    if config.get("DEPLOY_GCP_WAF") == "true":
        print(f"\n  Para desplegar WAF en GCP:")
        print(f"    cd {install_path}")
        print(f"    tokio deploy-gcp")

    print(f"\n  Para iniciar TokioAI:")
    if deploy == "1":
        print(f"    cd {install_path}")
        print(f"    docker compose up -d")
        print(f"    docker exec -it tokio-cli tokio")
    elif deploy == "2":
        print(f"    cd {install_path}")
        print(f"    tokio")
    else:
        print(f"    # Sube archivos a GCP")
        print(f"    gcloud compute scp {install_path}/* <instance>:~/tokio/")

    # Tools summary
    tools = ["bash", "python", "read_file", "write_file", "curl", "wget",
             "docker", "postgres_query", "gcp_waf", "gcp_compute",
             "infra", "task_orchestrator", "tenant", "user_preferences",
             "prompt_guard", "calendar", "self_heal", "document"]
    if features.get("host"):
        tools.append("host_control")
    if features.get("iot"):
        tools.append("iot_control")
    if features.get("router"):
        tools.append("router_control")
    if features.get("cloudflare"):
        tools.extend(["cloudflare", "tunnel"])
    if features.get("hostinger"):
        tools.append("hostinger")

    print(f"\n  Tools configuradas ({len(tools)}):")
    for t in sorted(tools):
        print(f"    + {t}")
    print()


# ── Helpers ───────────────────────────────────────────────────────────────

def _section(title: str):
    print(f"\n{'─' * 3} {title} {'─' * max(0, 48 - len(title))}")


def _validate_step(name: str, success: bool, detail: str = ""):
    mark = "[OK]" if success else "[!]"
    msg = f"  {mark} {name}"
    if detail:
        msg += f" — {detail}"
    print(msg)


def _ask(prompt: str, default: str = "", choices: list = None) -> str:
    suffix = f" [{default}]" if default else ""
    if choices:
        suffix += f" ({'/'.join(choices)})"
    while True:
        answer = input(f"  {prompt}{suffix}: ").strip()
        if not answer:
            if default is not None and default != "":
                return default
            if default == "":
                return ""
            print("    Respuesta requerida")
            continue
        if choices and answer not in choices:
            print(f"    Opciones validas: {', '.join(choices)}")
            continue
        return answer


def _ask_secret(prompt: str) -> str:
    import getpass
    while True:
        value = getpass.getpass(f"  {prompt}: ")
        if value.strip():
            return value.strip()
        print("    Valor requerido")


def _has_gcloud() -> bool:
    try:
        r = subprocess.run(["gcloud", "--version"], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def _auto_detect() -> List[str]:
    """Auto-detect available infrastructure."""
    detected = []

    # Docker
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
        if r.returncode == 0:
            detected.append("docker")
    except Exception:
        pass

    # PostgreSQL (local)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        result = s.connect_ex(("localhost", 5432))
        s.close()
        if result == 0:
            detected.append("postgresql-local")
    except Exception:
        pass

    # gcloud
    if _has_gcloud():
        detected.append("gcp")

    # SSH keys
    ssh_dir = Path.home() / ".ssh"
    if ssh_dir.exists() and any(ssh_dir.glob("id_*")):
        detected.append("ssh-keys")

    return detected


def _detect_gcp_infra(project: str, zone: str, instance: str) -> Optional[Dict]:
    try:
        r = subprocess.run(
            ["gcloud", "compute", "instances", "describe", instance,
             f"--zone={zone}", f"--project={project}", "--format=json"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            data = json.loads(r.stdout)
            result = {"status": data.get("status", "unknown")}
            nifs = data.get("networkInterfaces", [])
            if nifs:
                result["internal_ip"] = nifs[0].get("networkIP", "")
                access = nifs[0].get("accessConfigs", [])
                if access:
                    result["external_ip"] = access[0].get("natIP", "")
            return result
    except Exception:
        pass
    return None


def _test_pg(config: Dict) -> bool:
    """Test PostgreSQL connection."""
    host = config.get("POSTGRES_HOST", "postgres")
    if host in ("postgres",):
        return False  # Docker internal — can't test from host
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        result = s.connect_ex((host, int(config.get("POSTGRES_PORT", "5432"))))
        s.close()
        return result == 0
    except Exception:
        return False


def _test_ssh(host: str, user: str, key: str, port: int = 22) -> bool:
    """Test SSH connectivity."""
    try:
        cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5",
               "-p", str(port)]
        if key:
            key_expanded = os.path.expanduser(key)
            if os.path.exists(key_expanded):
                cmd += ["-i", key_expanded]
        cmd += [f"{user}@{host}", "echo ok"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return r.returncode == 0 and "ok" in r.stdout
    except Exception:
        return False


def _print_architecture(deploy: str, config: Dict, features: Dict):
    """Print ASCII architecture diagram."""
    mode_name = DEPLOY_MODES[deploy]["name"]
    print(f"  Arquitectura: {mode_name}")
    print("  " + "-" * 50)

    if deploy == "1":
        print("""
    +--[ Docker Host (Local) ]------------------+
    |                                            |
    |  +--------+   +----------+   +---------+  |
    |  | Tokio  |-->| Telegram |   | Postgres|  |
    |  | CLI    |   | Bot      |   |         |  |
    |  +--------+   +----------+   +---------+  |
    |       |                                    |
    |  +--------+   +----------+                 |
    |  |Watchdog|   | Document |                 |
    |  |        |   | Gen      |                 |
    |  +--------+   +----------+                 |
    +--------------------------------------------+""")
    elif deploy == "2":
        gcp_ip = config.get("GCP_WAF_HOST", "?.?.?.?")
        print(f"""
    +--[ Local ]---------------+     +--[ GCP ({gcp_ip}) ]-----+
    |                          |     |                          |
    |  +--------+ +--------+  | SSH |  +-------+ +----------+ |
    |  | Tokio  | |Telegram|  |---->|  | Nginx | | Postgres | |
    |  | CLI    | | Bot    |  |     |  | +Mod  | |          | |
    |  +--------+ +--------+  |     |  +-------+ +----------+ |
    |  +--------+ +--------+  |     |  +-------+              |
    |  |Watchdog| |Document|  |     |  | Kafka |              |
    |  +--------+ +--------+  |     |  +-------+              |
    +---------|----------------+     +--------------------------+
              v
    +--[ Integraciones ]--------+
    | IoT / Router / Cloudflare |
    +----------------------------+""")
    else:
        print("""
    +--[ GCP Cloud ]------------------------------------+
    |                                                    |
    |  +--------+ +----------+ +-------+ +----------+  |
    |  | Tokio  | | Telegram | | Nginx | | Postgres |  |
    |  | CLI    | | Bot      | | +Mod  | |          |  |
    |  +--------+ +----------+ +-------+ +----------+  |
    |  +--------+ +----------+ +-------+               |
    |  |Watchdog| | Document | | Kafka |               |
    |  +--------+ +----------+ +-------+               |
    +----------------------------------------------------+""")


def _generate_env(config: Dict[str, str], path: Path) -> None:
    sections = {
        "# -- LLM Configuration": [
            "LLM_PROVIDER", "ANTHROPIC_API_KEY", "CLAUDE_MODEL",
            "USE_ANTHROPIC_VERTEX", "ANTHROPIC_VERTEX_REGION",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "OPENAI_API_KEY", "OPENAI_MODEL",
            "GEMINI_API_KEY", "GEMINI_MODEL",
        ],
        "# -- PostgreSQL": [
            "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB",
            "POSTGRES_USER", "POSTGRES_PASSWORD",
        ],
        "# -- GCP": [
            "GCP_PROJECT_ID", "GCP_INSTANCE_NAME", "GCP_ZONE",
            "GCP_WAF_HOST", "GCP_WAF_USER", "GCP_WAF_SSH_KEY",
            "GCP_MACHINE_TYPE",
        ],
        "# -- Telegram": [
            "TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_ID", "TELEGRAM_ALLOWED_IDS",
        ],
        "# -- Host Control": [
            "HOST_SSH_HOST", "HOST_SSH_USER", "HOST_SSH_PORT", "HOST_SSH_KEY_PATH",
        ],
        "# -- IoT / Home Assistant": [
            "HOME_ASSISTANT_URL", "HOME_ASSISTANT_TOKEN",
        ],
        "# -- Router": [
            "ROUTER_HOST", "ROUTER_USER", "ROUTER_SSH_KEY_PATH",
        ],
        "# -- Cloudflare": [
            "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID",
            "CLOUDFLARED_TUNNEL_ID", "CLOUDFLARED_TUNNEL_TOKEN",
        ],
        "# -- Hostinger": [
            "HOSTINGER_API_TOKEN",
        ],
        "# -- System": [
            "CORS_ORIGINS", "TOKIO_PORT", "WATCHDOG_ENABLED",
        ],
    }

    lines = [
        "# =============================================",
        "# TokioAI Configuration - Generated by wizard",
        "# =============================================",
        "",
    ]

    for section_header, keys in sections.items():
        section_lines = []
        for key in keys:
            if key in config and config[key]:
                section_lines.append(f"{key}={config[key]}")
        if section_lines:
            lines.append(section_header)
            lines.extend(section_lines)
            lines.append("")

    # Defaults
    if "CORS_ORIGINS" not in config:
        lines.append("CORS_ORIGINS=http://localhost:3000,http://localhost:8080")
    if "TOKIO_PORT" not in config:
        lines.append("TOKIO_PORT=8000")
    lines.append("WATCHDOG_ENABLED=true")

    path.write_text("\n".join(lines) + "\n")


def _generate_compose(config: Dict[str, str], path: Path, deploy: str,
                       features: Dict[str, bool]) -> None:
    pg_host = config.get("POSTGRES_HOST", "postgres")
    is_local_pg = pg_host in ("postgres", "localhost", "127.0.0.1")

    compose = {"services": {}}

    # Tokio CLI service
    cli_service = {
        "build": {"context": ".", "dockerfile": "Dockerfile"},
        "container_name": "tokio-cli",
        "env_file": ".env",
        "ports": [f"{config.get('TOKIO_PORT', '8000')}:8000"],
        "volumes": [
            "tokio-workspace:/workspace",
            "/var/run/docker.sock:/var/run/docker.sock",
        ],
        "restart": "unless-stopped",
        "healthcheck": {
            "test": ["CMD", "curl", "-f", "http://localhost:8000/health"],
            "interval": "30s",
            "timeout": "10s",
            "retries": 5,
            "start_period": "15s",
        },
    }

    if is_local_pg:
        cli_service["depends_on"] = {"postgres": {"condition": "service_healthy"}}

    compose["services"]["tokio-cli"] = cli_service

    # Telegram bot (if configured)
    if features.get("telegram"):
        compose["services"]["tokio-telegram"] = {
            "build": {
                "context": "./tokio_agent/bots",
                "dockerfile": "Dockerfile.telegram",
            },
            "container_name": "tokio-telegram",
            "env_file": ".env",
            "environment": ["CLI_SERVICE_URL=http://tokio-cli:8000"],
            "depends_on": {
                "tokio-cli": {"condition": "service_healthy"},
            },
            "volumes": ["tokio-workspace:/workspace"],
            "restart": "unless-stopped",
        }

    # PostgreSQL (only for local deployment)
    if is_local_pg:
        compose["services"]["postgres"] = {
            "image": "postgres:15-alpine",
            "container_name": "tokio-postgres",
            "environment": {
                "POSTGRES_DB": config.get("POSTGRES_DB", "tokio"),
                "POSTGRES_USER": config.get("POSTGRES_USER", "tokio"),
                "POSTGRES_PASSWORD": config.get("POSTGRES_PASSWORD", ""),
            },
            "ports": ["5432:5432"],
            "volumes": ["pg-data:/var/lib/postgresql/data"],
            "restart": "unless-stopped",
            "healthcheck": {
                "test": ["CMD-SHELL", f"pg_isready -U {config.get('POSTGRES_USER', 'tokio')} -d {config.get('POSTGRES_DB', 'tokio')}"],
                "interval": "10s",
                "timeout": "5s",
                "retries": 5,
            },
        }

    compose["volumes"] = {"tokio-workspace": {}}
    if is_local_pg:
        compose["volumes"]["pg-data"] = {}

    try:
        import yaml
        path.write_text(yaml.dump(compose, default_flow_style=False, sort_keys=False))
    except ImportError:
        path.write_text(_dict_to_yaml(compose))


def _dict_to_yaml(d: dict, indent: int = 0) -> str:
    lines = []
    prefix = "  " * indent
    for key, value in d.items():
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            lines.append(_dict_to_yaml(value, indent + 1))
        elif isinstance(value, list):
            lines.append(f"{prefix}{key}:")
            for item in value:
                if isinstance(item, dict):
                    first = True
                    for k, v in item.items():
                        if first:
                            lines.append(f"{prefix}  - {k}: {v}")
                            first = False
                        else:
                            lines.append(f"{prefix}    {k}: {v}")
                else:
                    lines.append(f'{prefix}  - "{item}"')
        elif value is None:
            lines.append(f"{prefix}{key}:")
        else:
            lines.append(f"{prefix}{key}: {value}")
    return "\n".join(lines)


if __name__ == "__main__":
    run_wizard()
