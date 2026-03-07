"""
Built-in tool loader — Registers ALL core tools into the registry.

This includes system, network, docker, database, GCP, host, IoT,
calendar, router, cloudflare, hostinger, tunnel, infra,
task orchestrator, tenant, user preferences, and prompt guard tools.
"""
from __future__ import annotations

import logging
from ..registry import ToolRegistry

logger = logging.getLogger(__name__)


def load_builtin_tools(registry: ToolRegistry) -> int:
    """Register all built-in tools into the given registry.

    Returns:
        Number of tools registered.
    """
    count = 0

    # ── System tools ──────────────────────────────────────────────────────
    from .system_tools import bash, python_exec, read_file, write_file

    registry.register(
        name="bash",
        description="Ejecuta un comando bash (soporta curl, wget, git, ssh, etc.)",
        category="Sistema",
        parameters={"command": "Comando shell a ejecutar"},
        executor=bash,
        examples=[
            'TOOL:bash({"command": "ls -la /etc/"})',
            'TOOL:bash({"command": "ps aux | grep nginx"})',
        ],
    )
    count += 1

    registry.register(
        name="python",
        description="Ejecuta código Python en un archivo temporal",
        category="Sistema",
        parameters={"code": "Código Python a ejecutar"},
        executor=python_exec,
        examples=['TOOL:python({"code": "import os; print(os.getcwd())"})'],
    )
    count += 1

    registry.register(
        name="read_file",
        description="Lee un archivo del sistema de archivos",
        category="Sistema",
        parameters={
            "path": "Ruta al archivo",
            "lines": "(Opcional) Máximo de líneas a leer",
        },
        executor=read_file,
        examples=['TOOL:read_file({"path": "/etc/hostname"})'],
    )
    count += 1

    registry.register(
        name="write_file",
        description="Escribe contenido a un archivo",
        category="Sistema",
        parameters={
            "path": "Ruta al archivo",
            "content": "Contenido a escribir",
        },
        executor=write_file,
        examples=['TOOL:write_file({"path": "/tmp/test.txt", "content": "hola"})'],
    )
    count += 1

    # ── Network tools ─────────────────────────────────────────────────────
    from .network_tools import curl, wget

    registry.register(
        name="curl",
        description="Ejecuta petición HTTP (GET, POST, PUT, DELETE)",
        category="Red",
        parameters={
            "url": "URL destino",
            "method": "(Opcional) Método HTTP, default GET",
            "headers": "(Opcional) Dict de headers",
            "data": "(Opcional) Cuerpo de la petición",
        },
        executor=curl,
        examples=['TOOL:curl({"url": "https://api.example.com/data"})'],
    )
    count += 1

    registry.register(
        name="wget",
        description="Descarga un archivo desde una URL",
        category="Red",
        parameters={
            "url": "URL a descargar",
            "output_path": "(Opcional) Ruta local para guardar",
        },
        executor=wget,
    )
    count += 1

    # ── Docker tools ──────────────────────────────────────────────────────
    try:
        from .docker_tools import docker_cmd

        registry.register(
            name="docker",
            description="Gestiona contenedores Docker (ps, logs, start, stop, restart, inspect, exec, stats, images)",
            category="Contenedores",
            parameters={"command": "Sub-comando docker (ej: 'ps', 'logs mi-container')"},
            executor=docker_cmd,
            examples=[
                'TOOL:docker({"command": "ps"})',
                'TOOL:docker({"command": "logs tokio-ai-modsecurity"})',
            ],
        )
        count += 1
    except Exception as e:
        logger.warning(f"⚠️ Docker tools no disponibles: {e}")

    # ── Database tools ────────────────────────────────────────────────────
    try:
        from .db_tools import postgres_query

        registry.register(
            name="postgres_query",
            description="Ejecuta una consulta SQL en PostgreSQL",
            category="Base de Datos",
            parameters={
                "query": "Consulta SQL",
                "database": "(Opcional) Nombre de la base de datos",
            },
            executor=postgres_query,
            examples=[
                'TOOL:postgres_query({"query": "SELECT COUNT(*) FROM blocked_ips"})',
            ],
        )
        count += 1
    except Exception as e:
        logger.warning(f"⚠️ PostgreSQL tools no disponibles: {e}")

    # ── GCP tools ─────────────────────────────────────────────────────────
    try:
        from .gcp_tools import gcp_waf, gcp_compute, gcp_waf_deploy, gcp_waf_destroy

        registry.register(
            name="gcp_waf",
            description="Gestiona el WAF en GCP (ModSecurity + Nginx): status, blocked_ips, block_ip, unblock_ip, rules, logs, reload, health",
            category="GCP",
            parameters={
                "action": "Acción: status, blocked_ips, block_ip, unblock_ip, rules, logs, reload, audit_log, health",
                "params": "(Opcional) Parámetros adicionales (ip, lines, etc.)",
            },
            executor=gcp_waf,
            examples=[
                'TOOL:gcp_waf({"action": "status"})',
                'TOOL:gcp_waf({"action": "block_ip", "params": {"ip": "1.2.3.4"}})',
            ],
        )
        count += 1

        registry.register(
            name="gcp_compute",
            description="Gestiona instancias GCP Compute: list, status, start, stop, ssh, describe, firewall_list, firewall_add",
            category="GCP",
            parameters={
                "action": "Acción: list, status, start, stop, ssh, describe, firewall_list, firewall_add",
                "params": "(Opcional) Parámetros (instance, command, name, allow, etc.)",
            },
            executor=gcp_compute,
            examples=[
                'TOOL:gcp_compute({"action": "list"})',
                'TOOL:gcp_compute({"action": "ssh", "params": {"command": "docker ps"}})',
            ],
        )
        count += 1

        registry.register(
            name="gcp_waf_deploy",
            description="Despliega infraestructura WAF completa en GCP (VM, firewall, Docker, Nginx, Kafka, Postgres)",
            category="GCP",
            parameters={
                "params": "domain, backend, instance_name, machine_type, zone, disk_size, postgres_password",
            },
            executor=gcp_waf_deploy,
            examples=[
                'TOOL:gcp_waf_deploy({"params": {"domain": "example.com", "backend": "http://origin:8080"}})',
            ],
        )
        count += 1

        registry.register(
            name="gcp_waf_destroy",
            description="Destruye infraestructura WAF en GCP (IRREVERSIBLE, requiere confirm=true)",
            category="GCP",
            parameters={
                "params": "instance_name, zone, confirm=true",
            },
            executor=gcp_waf_destroy,
            examples=[
                'TOOL:gcp_waf_destroy({"params": {"instance_name": "tokio-waf-example", "confirm": true}})',
            ],
        )
        count += 1
    except Exception as e:
        logger.warning(f"⚠️ GCP tools no disponibles: {e}")

    # ── Host tools ────────────────────────────────────────────────────────
    try:
        from .host_tools import host_control

        registry.register(
            name="host_control",
            description=(
                "Control remoto de host vía SSH: status, run, reboot, services, update, "
                "cron_list, cron_add, cron_remove, write_file, read_file, journalctl, "
                "systemctl, install_packages, list_web_backends, get_public_ip, "
                "setup_log_retention, network_info, disk_info"
            ),
            category="Host",
            parameters={
                "action": "Acción a ejecutar",
                "params": "(Opcional) Parámetros adicionales",
            },
            executor=host_control,
            examples=[
                'TOOL:host_control({"action": "status"})',
                'TOOL:host_control({"action": "run", "params": {"command": "uname -a"}})',
                'TOOL:host_control({"action": "cron_add", "params": {"schedule": "*/5 * * * *", "command": "/usr/local/bin/check.sh", "comment": "health_check"}})',
            ],
        )
        count += 1
    except Exception as e:
        logger.warning(f"⚠️ Host tools no disponibles: {e}")

    # ── IoT tools ─────────────────────────────────────────────────────────
    try:
        from .iot_tools import iot_control

        registry.register(
            name="iot_control",
            description=(
                "Control IoT vía Home Assistant: alexa_speak, alexa_play_music, alexa_status, "
                "alexa_set_volume, light_control, switch_control, vacuum_control, get_state, "
                "sync_entities, list_entities, set_alias"
            ),
            category="IoT",
            parameters={
                "action": "Acción IoT",
                "params": "Parámetros específicos de la acción",
            },
            executor=iot_control,
            examples=[
                'TOOL:iot_control({"action": "alexa_speak", "params": {"text": "Hola mundo"}})',
                'TOOL:iot_control({"action": "light_control", "params": {"entity_id": "salon", "state": "on", "color": "azul"}})',
                'TOOL:iot_control({"action": "vacuum_control", "params": {"entity_id": "robot_vacuum", "vacuum_action": "start"}})',
            ],
        )
        count += 1
    except Exception as e:
        logger.warning(f"⚠️ IoT tools no disponibles: {e}")

    # ── Calendar tools ────────────────────────────────────────────────────
    try:
        from .calendar_tools import calendar_tool

        registry.register(
            name="calendar",
            description="Gestión de calendario ICS: query (hoy/semana/mes), summary, free_slots",
            category="Productividad",
            parameters={
                "action": "query, summary, free_slots",
                "params": "period (today/week/month/YYYY-MM-DD), file (ruta .ics)",
            },
            executor=calendar_tool,
            examples=[
                'TOOL:calendar({"action": "query", "params": {"period": "week"}})',
                'TOOL:calendar({"action": "free_slots", "params": {"period": "tomorrow"}})',
            ],
        )
        count += 1
    except Exception as e:
        logger.warning(f"⚠️ Calendar tools no disponibles: {e}")

    # ── Router tools ──────────────────────────────────────────────────────
    try:
        from .router_tools import router_control

        registry.register(
            name="router_control",
            description=(
                "Control de router OpenWrt/GL.iNet vía SSH: health, firewall_status, "
                "wifi_status, detect_attack_signals, wifi_defense_status, "
                "wifi_defense_harden, recover_wifi, add_block_ip, remove_block_ip, run"
            ),
            category="Red",
            parameters={
                "action": "Acción del router",
                "params": "Parámetros (ip, command, confirm, etc.)",
            },
            executor=router_control,
            examples=[
                'TOOL:router_control({"action": "health"})',
                'TOOL:router_control({"action": "wifi_defense_status"})',
            ],
        )
        count += 1
    except Exception as e:
        logger.warning(f"⚠️ Router tools no disponibles: {e}")

    # ── Cloudflare tools ──────────────────────────────────────────────────
    try:
        from .cloudflare_tools import cloudflare_tool

        registry.register(
            name="cloudflare",
            description="Cloudflare API: configure_tunnel_route, remove_tunnel_route",
            category="Red",
            parameters={
                "action": "configure_tunnel_route o remove_tunnel_route",
                "params": "tunnel_id, account_id, hostname, service, api_token",
            },
            executor=cloudflare_tool,
            examples=[
                'TOOL:cloudflare({"action": "configure_tunnel_route", "params": {"hostname": "app.example.com", "service": "http://localhost:8080"}})',
            ],
        )
        count += 1
    except Exception as e:
        logger.warning(f"⚠️ Cloudflare tools no disponibles: {e}")

    # ── Hostinger tools ───────────────────────────────────────────────────
    try:
        from .hostinger_tools import hostinger_tool

        registry.register(
            name="hostinger",
            description="Hostinger DNS: list_dns, add_dns, update_dns, delete_dns, publish_site, unpublish_site, setup_waf_dns",
            category="DNS",
            parameters={
                "action": "Acción DNS",
                "params": "domain, type, name, value, record_id, ip, waf_ip",
            },
            executor=hostinger_tool,
            examples=[
                'TOOL:hostinger({"action": "list_dns", "params": {"domain": "example.com"}})',
                'TOOL:hostinger({"action": "setup_waf_dns", "params": {"domain": "example.com", "waf_ip": "1.2.3.4"}})',
            ],
        )
        count += 1
    except Exception as e:
        logger.warning(f"⚠️ Hostinger tools no disponibles: {e}")

    # ── Tunnel tools ──────────────────────────────────────────────────────
    try:
        from .tunnel_tools import tunnel_tool

        registry.register(
            name="tunnel",
            description="Cloudflared tunnel lifecycle: status, start, stop, restart, logs, deploy, info",
            category="Red",
            parameters={
                "action": "Acción del tunnel",
                "params": "tunnel_token, lines",
            },
            executor=tunnel_tool,
            examples=[
                'TOOL:tunnel({"action": "status"})',
                'TOOL:tunnel({"action": "deploy"})',
            ],
        )
        count += 1
    except Exception as e:
        logger.warning(f"⚠️ Tunnel tools no disponibles: {e}")

    # ── Infra tools ───────────────────────────────────────────────────────
    try:
        from .infra_tools import infra_tool

        registry.register(
            name="infra",
            description=(
                "Infraestructura local: system_info, processes, services, logs, network, "
                "disk_usage, backup_db, restore_db, check_ports, monitor"
            ),
            category="Infraestructura",
            parameters={
                "action": "Acción de infraestructura",
                "params": "service, lines, path, count, sort, host, database, etc.",
            },
            executor=infra_tool,
            examples=[
                'TOOL:infra({"action": "monitor"})',
                'TOOL:infra({"action": "backup_db", "params": {"output": "/workspace/backup.sql"}})',
            ],
        )
        count += 1
    except Exception as e:
        logger.warning(f"⚠️ Infra tools no disponibles: {e}")

    # ── Task Orchestrator ─────────────────────────────────────────────────
    try:
        from .task_orchestrator import task_orchestrator

        registry.register(
            name="task_orchestrator",
            description=(
                "Orquestador de tareas autónomas: cron_list, cron_add, cron_remove, "
                "run_once, install_package, create_script, run_playbook, schedule_task"
            ),
            category="Automatización",
            parameters={
                "action": "Acción del orquestador",
                "params": "schedule, command, comment, package, path, content, steps[], name, method",
            },
            executor=task_orchestrator,
            examples=[
                'TOOL:task_orchestrator({"action": "cron_add", "params": {"schedule": "0 */6 * * *", "command": "/opt/check.sh", "comment": "health_6h"}})',
                'TOOL:task_orchestrator({"action": "run_playbook", "params": {"steps": [{"command": "echo hello", "description": "test"}]}})',
            ],
        )
        count += 1
    except Exception as e:
        logger.warning(f"⚠️ Task orchestrator no disponible: {e}")

    # ── Tenant tools ──────────────────────────────────────────────────────
    try:
        from .tenant_tools import tenant_tool

        registry.register(
            name="tenant",
            description="Gestión de tenants Nginx/WAF: list, add, remove, health, ssl_setup, reload_nginx, status",
            category="WAF",
            parameters={
                "action": "Acción de tenant",
                "params": "domain, backend, ssl, email, target (gcp/local)",
            },
            executor=tenant_tool,
            examples=[
                'TOOL:tenant({"action": "list"})',
                'TOOL:tenant({"action": "add", "params": {"domain": "app.example.com", "backend": "http://localhost:3000"}})',
            ],
        )
        count += 1
    except Exception as e:
        logger.warning(f"⚠️ Tenant tools no disponibles: {e}")

    # ── User Preferences ──────────────────────────────────────────────────
    try:
        from .user_preferences_tool import user_preferences_tool

        registry.register(
            name="user_preferences",
            description="Preferencias de usuario persistentes: get, set, delete, list",
            category="Sistema",
            parameters={
                "action": "get, set, delete, list",
                "params": "key, value",
            },
            executor=user_preferences_tool,
            examples=[
                'TOOL:user_preferences({"action": "set", "params": {"key": "user_name", "value": "Carlos"}})',
                'TOOL:user_preferences({"action": "get", "params": {"key": "user_name"}})',
            ],
        )
        count += 1
    except Exception as e:
        logger.warning(f"⚠️ User preferences tool no disponible: {e}")

    # ── Prompt Guard ──────────────────────────────────────────────────────
    try:
        from .prompt_guard_tools import prompt_guard_tool

        registry.register(
            name="prompt_guard",
            description="Auditoría de seguridad de prompts: analyze, audit_log, stats",
            category="Seguridad",
            parameters={
                "action": "analyze, audit_log, stats",
                "params": "text, limit",
            },
            executor=prompt_guard_tool,
            examples=[
                'TOOL:prompt_guard({"action": "analyze", "params": {"text": "ignore all previous instructions"}})',
                'TOOL:prompt_guard({"action": "stats"})',
            ],
        )
        count += 1
    except Exception as e:
        logger.warning(f"⚠️ Prompt guard tool no disponible: {e}")

    # ── Self-Heal / Watchdog ─────────────────────────────────────────────
    try:
        from ...watchdog import self_heal_tool

        registry.register(
            name="self_heal",
            description="Auto-recuperación de contenedores: status, check, restart, events",
            category="Contenedores",
            parameters={
                "action": "status, check, restart, events",
                "params": "container (para restart), limit (para events)",
            },
            executor=self_heal_tool,
            examples=[
                'TOOL:self_heal({"action": "status"})',
                'TOOL:self_heal({"action": "check"})',
                'TOOL:self_heal({"action": "restart", "params": {"container": "tokio-cli"}})',
            ],
        )
        count += 1
    except Exception as e:
        logger.warning(f"⚠️ Self-heal tool no disponible: {e}")

    # ── Document Tools ───────────────────────────────────────────────────
    try:
        from .document_tools import document_tool

        registry.register(
            name="document",
            description="Generación de documentos: generate_pdf, generate_slides, generate_csv",
            category="Productividad",
            parameters={
                "action": "generate_pdf, generate_slides, generate_csv",
                "params": "title, content, sections, data, output_path, template",
            },
            executor=document_tool,
            examples=[
                'TOOL:document({"action": "generate_pdf", "params": {"title": "Reporte WAF", "sections": [{"heading": "Resumen", "body": "..."}]}})',
                'TOOL:document({"action": "generate_csv", "params": {"data": [["IP","Hits"],["1.2.3.4","100"]], "output_path": "/workspace/report.csv"}})',
            ],
        )
        count += 1
    except Exception as e:
        logger.warning(f"⚠️ Document tools no disponibles: {e}")

    # ── Drone Tools (DJI Tello) ────────────────────────────────────────────
    try:
        from .drone_tools import drone_control

        registry.register(
            name="drone",
            description=(
                "Control de drone DJI Tello: connect, disconnect, takeoff, land, emergency, "
                "move, rotate, flip, go_xyz, curve, rc_control, set_speed, motor_on, motor_off, "
                "patrol, stream_on, stream_off, take_photo, set_video, status, battery, "
                "telemetry, mission_pad, wifi, flight_log, reboot"
            ),
            category="IoT",
            parameters={
                "action": "Accion del drone",
                "params": "Parametros especificos de la accion",
            },
            executor=drone_control,
            examples=[
                'TOOL:drone({"action": "connect"})',
                'TOOL:drone({"action": "takeoff"})',
                'TOOL:drone({"action": "move", "params": {"direction": "forward", "distance": 100}})',
                'TOOL:drone({"action": "rotate", "params": {"direction": "clockwise", "degrees": 90}})',
                'TOOL:drone({"action": "patrol", "params": {"pattern": "square", "size": 150}})',
                'TOOL:drone({"action": "take_photo", "params": {"output": "/tmp/drone.jpg"}})',
                'TOOL:drone({"action": "status"})',
                'TOOL:drone({"action": "land"})',
            ],
        )
        count += 1
    except Exception as e:
        logger.warning(f"⚠️ Drone tools no disponibles: {e}")

    # ── Coffee Machine Tools (Raspberry Pi) ─────────────────────────────
    try:
        from .coffee_tools import coffee_control

        registry.register(
            name="coffee",
            description=(
                "Control de máquina de café con Raspberry Pi: brew, recipes, status, "
                "history, emotion, emotions, emergency_stop, test_pumps, calibrate, custom"
            ),
            category="IoT",
            parameters={
                "action": "Acción de la máquina de café",
                "params": "recipe, water_ml, milk_ml, pump, duration, mood, limit",
            },
            executor=coffee_control,
            examples=[
                'TOOL:coffee({"action": "brew", "params": {"recipe": "cafe_con_leche"}})',
                'TOOL:coffee({"action": "brew", "params": {"recipe": "espresso"}})',
                'TOOL:coffee({"action": "brew", "params": {"recipe": "cortado"}})',
                'TOOL:coffee({"action": "recipes"})',
                'TOOL:coffee({"action": "status"})',
                'TOOL:coffee({"action": "history"})',
                'TOOL:coffee({"action": "emotion", "params": {"mood": "happy"}})',
                'TOOL:coffee({"action": "emergency_stop"})',
                'TOOL:coffee({"action": "custom", "params": {"water_ml": 100, "milk_ml": 50}})',
            ],
        )
        count += 1
    except Exception as e:
        logger.warning(f"⚠️ Coffee tools no disponibles: {e}")

    logger.info(f"✅ {count} tools builtin registradas")
    return count
