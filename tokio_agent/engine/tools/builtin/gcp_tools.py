"""
GCP Tools — Full cloud infrastructure management.

IMPORTANT: Uses TWO separate service accounts:
  - Vertex AI (LLM): GOOGLE_APPLICATION_CREDENTIALS → /app/vertex-credentials.json
  - GCP Infra (compute, WAF): GCP_SA_KEY_JSON → /app/gcp-sa-key.json

The gcloud commands in this module ALWAYS activate the infra service account
so they never conflict with the Vertex AI credentials used by the LLM.

Provides:
- WAF management (ModSecurity, blocked IPs, rules, logs)
- GCP Compute instance control (start, stop, status, list, ssh)
- Full WAF deployment/destroy on GCP (VM, firewall, Docker, Nginx, Kafka, Postgres)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import textwrap
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_INFRA_ACTIVATED = False


def _get_gcp_config() -> Dict[str, str]:
    return {
        "sa_key_json": os.getenv("GCP_SA_KEY_JSON", "/app/gcp-sa-key.json").strip(),
        "project_id": os.getenv("GCP_PROJECT_ID", "").strip(),
        "zone": os.getenv("GCP_ZONE", "us-central1-a").strip(),
        "instance_name": os.getenv("GCP_INSTANCE_NAME", "").strip(),
        "region": os.getenv("GCP_REGION", "us-central1").strip(),
        "machine_type": os.getenv("GCP_MACHINE_TYPE", "e2-medium").strip(),
    }


async def _activate_infra_sa() -> str:
    """Activate the GCP infra service account for gcloud commands.

    This is separate from GOOGLE_APPLICATION_CREDENTIALS which is used
    by the Anthropic Vertex AI SDK for LLM calls.
    """
    global _INFRA_ACTIVATED
    if _INFRA_ACTIVATED:
        return ""

    config = _get_gcp_config()
    sa_key = config["sa_key_json"]
    project = config["project_id"]

    if not sa_key or not os.path.exists(sa_key):
        return f"⚠️ GCP_SA_KEY_JSON no encontrado: {sa_key}"

    # Activate the infra service account
    cmd = f"gcloud auth activate-service-account --key-file={sa_key} --quiet 2>&1"
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()
        return f"Error activando SA de infra: {err}"

    # Set default project
    if project:
        await asyncio.create_subprocess_shell(
            f"gcloud config set project {project} --quiet 2>&1",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )

    _INFRA_ACTIVATED = True
    logger.info(f"✅ GCP infra SA activada: {sa_key} (project={project})")
    return ""


async def _exec(cmd: str, timeout: int = 120) -> str:
    """Execute a shell command, ensuring the infra SA is activated first."""
    # Always activate infra SA before running gcloud commands
    activation_error = await _activate_infra_sa()
    if activation_error:
        return activation_error

    try:
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            return f"Error (exit {proc.returncode}):\n{err}\n{out}".strip()
        return out or "✅ Ejecutado"
    except asyncio.TimeoutError:
        return f"⏱️ Timeout ({timeout}s)"
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


# ── WAF Management ────────────────────────────────────────────────────────

async def gcp_waf(action: str, params: Optional[Dict] = None) -> str:
    """
    GCP WAF management (ModSecurity + Nginx).

    Actions:
      - status: WAF status (nginx, modsecurity, blocked IPs)
      - blocked_ips: List blocked IPs
      - block_ip: Block an IP (params: ip)
      - unblock_ip: Unblock an IP (params: ip)
      - rules: List ModSecurity rules
      - logs: Recent WAF logs (params: lines)
      - reload: Reload Nginx
      - audit_log: Get modsec audit log
      - health: Full health check
    """
    params = params or {}
    config = _get_gcp_config()
    instance = config.get("instance_name", "")
    zone = config.get("zone", "us-central1-a")
    project = config.get("project_id", "")

    if not instance or not project:
        return json.dumps({
            "ok": False,
            "error": "GCP_INSTANCE_NAME y GCP_PROJECT_ID requeridos. Configúralos en .env.",
        }, ensure_ascii=False)

    # SSH directo (sin IAP tunnel — requiere puerto 22 abierto y OS Login configurado)
    base = f"gcloud compute ssh {instance} --zone={zone} --project={project} --command="

    cmd_map = {
        "status": (
            "echo '=== Nginx ===' && "
            "docker exec modsecurity-nginx nginx -t 2>&1 && "
            "echo '=== Containers ===' && "
            "docker ps --format '{{.Names}} {{.Status}}' && "
            "echo '=== Blocked IPs ===' && "
            "docker exec modsecurity-nginx cat /etc/nginx/blocked_ips.conf 2>/dev/null | wc -l"
        ),
        "blocked_ips": (
            "docker exec modsecurity-nginx cat /etc/nginx/blocked_ips.conf 2>/dev/null || "
            "echo 'No blocked IPs file'"
        ),
        "block_ip": (
            f"docker exec modsecurity-nginx sh -c "
            f"\"echo 'deny {params.get('ip', '')}; ' >> /etc/nginx/blocked_ips.conf && "
            f"nginx -s reload\" 2>&1"
        ),
        "unblock_ip": (
            f"docker exec modsecurity-nginx sh -c "
            f"\"sed -i '/{params.get('ip', '')}/d' /etc/nginx/blocked_ips.conf && "
            f"nginx -s reload\" 2>&1"
        ),
        "rules": (
            "docker exec modsecurity-nginx ls /etc/modsecurity.d/owasp-crs/rules/ 2>/dev/null || "
            "echo 'No rules directory'"
        ),
        "logs": (
            f"docker exec modsecurity-nginx tail -n {params.get('lines', 50)} "
            f"/var/log/modsecurity/modsec_audit.log 2>/dev/null || "
            f"echo 'No audit log'"
        ),
        "reload": "docker exec modsecurity-nginx nginx -s reload 2>&1",
        "audit_log": (
            f"docker exec modsecurity-nginx tail -n {params.get('lines', 200)} "
            f"/var/log/modsecurity/modsec_audit.log 2>/dev/null"
        ),
        "health": (
            "echo '=== Docker ===' && docker ps --format '{{.Names}}: {{.Status}}' && "
            "echo '=== Nginx Test ===' && docker exec modsecurity-nginx nginx -t 2>&1 && "
            "echo '=== Disk ===' && df -h / && "
            "echo '=== Memory ===' && free -h && "
            "echo '=== Logs (last 10) ===' && "
            "docker exec modsecurity-nginx tail -n 10 /var/log/modsecurity/modsec_audit.log 2>/dev/null || true"
        ),
    }

    remote = cmd_map.get(action)
    if not remote:
        return json.dumps({
            "ok": False,
            "error": f"Acción WAF no soportada: '{action}'",
            "supported": list(cmd_map.keys()),
        }, ensure_ascii=False)

    result = await _exec(f'{base}"{remote}"', timeout=60)
    return result


# ── GCP Compute ───────────────────────────────────────────────────────────

async def gcp_compute(action: str, params: Optional[Dict] = None) -> str:
    """
    GCP Compute instance management.

    Actions:
      - list: List all instances
      - status: Get instance status
      - start: Start instance
      - stop: Stop instance
      - ssh: Execute command via SSH
      - describe: Full instance description
      - firewall_list: List firewall rules
      - firewall_add: Add firewall rule
    """
    params = params or {}
    config = _get_gcp_config()
    project = config.get("project_id", "")
    zone = config.get("zone", "us-central1-a")
    instance = params.get("instance", config.get("instance_name", ""))

    if not project:
        return json.dumps({"ok": False, "error": "GCP_PROJECT_ID requerido"}, ensure_ascii=False)

    if action == "list":
        return await _exec(
            f"gcloud compute instances list --project={project} --format=json", 60
        )

    elif action == "status":
        return await _exec(
            f"gcloud compute instances describe {instance} --zone={zone} "
            f"--project={project} --format='value(status)'", 30
        )

    elif action == "start":
        return await _exec(
            f"gcloud compute instances start {instance} --zone={zone} --project={project}", 120
        )

    elif action == "stop":
        return await _exec(
            f"gcloud compute instances stop {instance} --zone={zone} --project={project}", 120
        )

    elif action == "ssh":
        command = params.get("command", "hostname")
        return await _exec(
            f'gcloud compute ssh {instance} --zone={zone} --project={project} '
            f'--command="{command}"', 60
        )

    elif action == "describe":
        return await _exec(
            f"gcloud compute instances describe {instance} --zone={zone} "
            f"--project={project} --format=json", 30
        )

    elif action == "firewall_list":
        return await _exec(
            f"gcloud compute firewall-rules list --project={project} --format=json", 30
        )

    elif action == "firewall_add":
        name = str(params.get("name", "")).strip()
        allow = str(params.get("allow", "tcp:80,tcp:443")).strip()
        source = str(params.get("source_ranges", "0.0.0.0/0")).strip()
        target_tags = str(params.get("target_tags", "")).strip()
        if not name:
            return json.dumps({"ok": False, "error": "params.name requerido"})
        cmd = (
            f"gcloud compute firewall-rules create {name} "
            f"--project={project} --allow={allow} "
            f"--source-ranges={source}"
        )
        if target_tags:
            cmd += f" --target-tags={target_tags}"
        return await _exec(cmd, 60)

    return json.dumps({
        "ok": False,
        "error": f"Acción no soportada: '{action}'",
        "supported": ["list", "status", "start", "stop", "ssh", "describe",
                      "firewall_list", "firewall_add"],
    }, ensure_ascii=False)


# ── Full WAF Deployment ──────────────────────────────────────────────────

_DOCKER_COMPOSE_TEMPLATE = textwrap.dedent("""\
version: '3.8'
services:
  modsecurity-nginx:
    image: owasp/modsecurity-crs:nginx-alpine
    container_name: modsecurity-nginx
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/conf.d:/etc/nginx/conf.d
      - ./nginx/modsec:/etc/nginx/modsec
      - ./nginx/ssl:/etc/nginx/ssl
      - ./nginx/blocked_ips.conf:/etc/nginx/blocked_ips.conf
      - nginx_logs:/var/log/nginx
      - modsec_logs:/var/log/modsecurity
    environment:
      - PARANOIA=1
      - ANOMALY_INBOUND=5
      - ANOMALY_OUTBOUND=4

  log-processor:
    image: python:3.11-slim
    container_name: log-processor
    restart: unless-stopped
    volumes:
      - ./log-processor:/app
      - modsec_logs:/var/log/modsecurity:ro
    working_dir: /app
    command: python main.py
    environment:
      - POSTGRES_HOST=postgres
      - POSTGRES_PORT=5432
      - POSTGRES_DB=${{POSTGRES_DB:-tokio}}
      - POSTGRES_USER=${{POSTGRES_USER:-tokio}}
      - POSTGRES_PASSWORD=${{POSTGRES_PASSWORD:-changeme}}
    depends_on:
      - postgres

  postgres:
    image: postgres:15-alpine
    container_name: postgres
    restart: unless-stopped
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    environment:
      - POSTGRES_DB=${{POSTGRES_DB:-tokio}}
      - POSTGRES_USER=${{POSTGRES_USER:-tokio}}
      - POSTGRES_PASSWORD=${{POSTGRES_PASSWORD:-changeme}}

  kafka:
    image: bitnami/kafka:3.6
    container_name: kafka
    restart: unless-stopped
    ports:
      - "9092:9092"
    environment:
      - KAFKA_CFG_NODE_ID=0
      - KAFKA_CFG_PROCESS_ROLES=controller,broker
      - KAFKA_CFG_LISTENERS=PLAINTEXT://:9092,CONTROLLER://:9093
      - KAFKA_CFG_LISTENER_SECURITY_PROTOCOL_MAP=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT
      - KAFKA_CFG_CONTROLLER_QUORUM_VOTERS=0@kafka:9093
      - KAFKA_CFG_CONTROLLER_LISTENER_NAMES=CONTROLLER
    volumes:
      - kafka_data:/bitnami/kafka

volumes:
  pgdata:
  nginx_logs:
  modsec_logs:
  kafka_data:
""")

_NGINX_DEFAULT_CONF = textwrap.dedent("""\
server {{
    listen 80 default_server;
    server_name {domain};

    modsecurity on;
    modsecurity_rules_file /etc/nginx/modsec/main.conf;

    include /etc/nginx/blocked_ips.conf;

    location / {{
        proxy_pass {backend};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}

    location /health {{
        return 200 'ok';
        add_header Content-Type text/plain;
    }}
}}
""")

_STARTUP_SCRIPT = textwrap.dedent("""\
#!/bin/bash
set -e
apt-get update -y
apt-get install -y docker.io docker-compose-plugin curl git
systemctl enable docker
systemctl start docker
mkdir -p /opt/tokio-waf
cd /opt/tokio-waf
docker compose up -d || docker-compose up -d
""")


async def gcp_waf_deploy(params: Optional[Dict[str, Any]] = None) -> str:
    """
    Deploy full WAF infrastructure on GCP.

    params:
      - instance_name: VM name (default: tokio-waf-{domain})
      - domain: Domain to protect
      - backend: Backend origin URL
      - machine_type: GCP machine type (default: e2-medium)
      - zone: GCP zone
      - disk_size: Boot disk size in GB (default: 30)
      - postgres_password: PostgreSQL password
    """
    params = params or {}
    config = _get_gcp_config()
    project = config["project_id"]
    if not project:
        return json.dumps({"ok": False, "error": "GCP_PROJECT_ID requerido"})

    domain = str(params.get("domain", "")).strip()
    if not domain:
        return json.dumps({"ok": False, "error": "params.domain requerido"})

    instance = str(params.get("instance_name", f"tokio-waf-{domain.replace('.', '-')}")).strip()
    zone = str(params.get("zone", config["zone"])).strip()
    machine = str(params.get("machine_type", config["machine_type"])).strip()
    disk_size = int(params.get("disk_size", 30))
    backend = str(params.get("backend", "http://localhost:8080")).strip()
    pg_pass = str(params.get("postgres_password", os.getenv("POSTGRES_PASSWORD", ""))).strip()

    steps = []

    # 1. Create VM
    create_cmd = (
        f"gcloud compute instances create {instance} "
        f"--project={project} --zone={zone} "
        f"--machine-type={machine} "
        f"--boot-disk-size={disk_size}GB "
        f"--image-family=ubuntu-2204-lts --image-project=ubuntu-os-cloud "
        f"--tags=http-server,https-server,tokio-waf "
        f"--metadata=startup-script='{_STARTUP_SCRIPT}' "
        f"--scopes=default"
    )
    result = await _exec(create_cmd, timeout=180)
    steps.append({"step": "create_vm", "result": result[:300]})

    # 2. Create firewall rules
    for rule_name, ports in [
        (f"tokio-waf-http-{instance}", "tcp:80,tcp:443"),
        (f"tokio-waf-pg-{instance}", "tcp:5432"),
        (f"tokio-waf-kafka-{instance}", "tcp:9092"),
    ]:
        fw = await _exec(
            f"gcloud compute firewall-rules create {rule_name} "
            f"--project={project} --allow={ports} "
            f"--source-ranges=0.0.0.0/0 --target-tags=tokio-waf 2>&1 || true",
            30,
        )
        steps.append({"step": f"firewall_{rule_name}", "result": fw[:200]})

    # 3. Wait for VM startup
    logger.info("Waiting 60s for VM startup script...")
    await asyncio.sleep(60)

    # 4. Copy docker-compose and nginx config
    compose = _DOCKER_COMPOSE_TEMPLATE
    nginx_conf = _NGINX_DEFAULT_CONF.format(domain=domain, backend=backend)

    ssh_base = (
        f"gcloud compute ssh {instance} --zone={zone} --project={project} "
        f"--command="
    )

    setup_cmds = [
        f"mkdir -p /opt/tokio-waf/nginx/conf.d /opt/tokio-waf/nginx/modsec /opt/tokio-waf/nginx/ssl /opt/tokio-waf/log-processor",
        f"cat > /opt/tokio-waf/docker-compose.yml << 'COMPOSE_EOF'\n{compose}\nCOMPOSE_EOF",
        f"cat > /opt/tokio-waf/nginx/conf.d/default.conf << 'NGINX_EOF'\n{nginx_conf}\nNGINX_EOF",
        f"touch /opt/tokio-waf/nginx/blocked_ips.conf",
        f"cat > /opt/tokio-waf/nginx/modsec/main.conf << 'MODSEC_EOF'\nSecRuleEngine On\nMODSEC_EOF",
        f"cd /opt/tokio-waf && POSTGRES_PASSWORD={pg_pass} docker compose up -d 2>&1 || "
        f"cd /opt/tokio-waf && POSTGRES_PASSWORD={pg_pass} docker-compose up -d 2>&1",
    ]

    for i, cmd in enumerate(setup_cmds):
        r = await _exec(f'{ssh_base}"{cmd}"', timeout=120)
        steps.append({"step": f"setup_{i}", "result": r[:200]})

    # 5. Get external IP
    ip_result = await _exec(
        f"gcloud compute instances describe {instance} --zone={zone} --project={project} "
        f"--format='value(networkInterfaces[0].accessConfigs[0].natIP)'",
        30,
    )
    steps.append({"step": "get_ip", "ip": ip_result.strip()})

    return json.dumps({
        "ok": True,
        "instance": instance,
        "zone": zone,
        "domain": domain,
        "external_ip": ip_result.strip(),
        "steps": steps,
        "next_steps": [
            f"Point DNS for {domain} to {ip_result.strip()}",
            "Setup SSL with certbot",
            "Configure Cloudflare tunnel if needed",
        ],
    }, ensure_ascii=False, indent=2)


async def gcp_waf_destroy(params: Optional[Dict[str, Any]] = None) -> str:
    """
    Destroy WAF infrastructure on GCP.

    params:
      - instance_name: VM to destroy
      - zone: GCP zone
      - confirm: Must be true to proceed
    """
    params = params or {}
    config = _get_gcp_config()
    project = config["project_id"]
    if not project:
        return json.dumps({"ok": False, "error": "GCP_PROJECT_ID requerido"})

    if not params.get("confirm"):
        return json.dumps({
            "ok": False,
            "error": "⚠️ DESTRUCCIÓN IRREVERSIBLE. Agrega params.confirm=true para continuar.",
        })

    instance = str(params.get("instance_name", config.get("instance_name", ""))).strip()
    zone = str(params.get("zone", config["zone"])).strip()
    if not instance:
        return json.dumps({"ok": False, "error": "instance_name requerido"})

    steps = []

    result = await _exec(
        f"gcloud compute instances delete {instance} --zone={zone} --project={project} --quiet",
        180,
    )
    steps.append({"step": "delete_vm", "result": result[:300]})

    for suffix in ["http", "pg", "kafka"]:
        rule_name = f"tokio-waf-{suffix}-{instance}"
        r = await _exec(
            f"gcloud compute firewall-rules delete {rule_name} --project={project} --quiet 2>&1 || true",
            30,
        )
        steps.append({"step": f"delete_fw_{suffix}", "result": r[:200]})

    return json.dumps({"ok": True, "destroyed": instance, "steps": steps}, ensure_ascii=False, indent=2)
