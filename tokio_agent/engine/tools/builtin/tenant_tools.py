"""
Tenant Tools — Nginx tenant management for WAF proxy.

Add, remove, list, health-check tenants behind the WAF/Nginx proxy.
Supports SSL via Let's Encrypt and ModSecurity integration.
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Dict, List, Optional

from ._common import run_local as _run, ssh_run


def _ssh_gcp(cmd: str, timeout: int = 60) -> str:
    """Run command on GCP WAF instance via SSH or gcloud."""
    gcp_host = os.getenv("GCP_WAF_HOST", "").strip()
    gcp_user = os.getenv("GCP_WAF_USER", "").strip()
    gcp_key = os.getenv("GCP_WAF_SSH_KEY", "").strip()
    gcp_instance = os.getenv("GCP_WAF_INSTANCE", "").strip()
    gcp_zone = os.getenv("GCP_WAF_ZONE", "us-central1-a").strip()

    if gcp_host and gcp_user:
        return ssh_run(gcp_host, gcp_user, cmd, key=gcp_key, connect_timeout=10, timeout=timeout)
    elif gcp_instance:
        p = subprocess.run(
            ["gcloud", "compute", "ssh", gcp_instance, f"--zone={gcp_zone}",
             "--tunnel-through-iap", "--command", cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        out = (p.stdout or "").strip()
        err = (p.stderr or "").strip()
        if p.returncode != 0 and not out:
            return err or f"exit code {p.returncode}"
        return out
    else:
        return _run(cmd, timeout)


def _nginx_conf(domain: str, backend: str, ssl: bool = False) -> str:
    """Generate Nginx server block for a tenant."""
    listen = "listen 443 ssl;\n    listen [::]:443 ssl;" if ssl else "listen 80;\n    listen [::]:80;"
    ssl_block = f"""
    ssl_certificate /etc/letsencrypt/live/{domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{domain}/privkey.pem;""" if ssl else ""

    return f"""server {{
    {listen}
    server_name {domain} www.{domain};
    {ssl_block}

    # ModSecurity
    modsecurity on;
    modsecurity_rules_file /etc/nginx/modsec/main.conf;

    location / {{
        proxy_pass {backend};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 60s;
        proxy_read_timeout 120s;
    }}

    # Health check
    location /health {{
        return 200 'ok';
        add_header Content-Type text/plain;
    }}

    access_log /var/log/nginx/{domain}_access.log;
    error_log /var/log/nginx/{domain}_error.log;
}}
"""


def tenant_tool(action: str, params: Optional[Dict[str, Any]] = None) -> str:
    """
    Tenant management tool for WAF/Nginx proxy.

    Actions:
      - list: List configured tenants
      - add: Add new tenant (params: domain, backend, ssl)
      - remove: Remove tenant (params: domain)
      - health: Check tenant health (params: domain)
      - ssl_setup: Setup SSL for tenant (params: domain, email)
      - reload_nginx: Reload Nginx configuration
      - status: Get Nginx status
    """
    params = params or {}
    action = (action or "").strip().lower()
    use_gcp = str(params.get("target", "gcp")).strip().lower() == "gcp"
    runner = _ssh_gcp if use_gcp else _run

    try:
        if action == "list":
            configs = runner("ls -1 /etc/nginx/conf.d/ 2>/dev/null || ls -1 /etc/nginx/sites-enabled/ 2>/dev/null || echo 'no configs'")
            return json.dumps({"ok": True, "tenants": configs}, ensure_ascii=False)

        elif action == "add":
            domain = str(params.get("domain", "")).strip()
            backend = str(params.get("backend", "http://localhost:8080")).strip()
            ssl = bool(params.get("ssl", False))
            if not domain:
                return json.dumps({"ok": False, "error": "domain es requerido"})
            conf = _nginx_conf(domain, backend, ssl)
            import shlex
            escaped = shlex.quote(conf)
            conf_path = f"/etc/nginx/conf.d/{domain}.conf"
            result = runner(f"echo {escaped} > {conf_path}")
            test = runner("nginx -t 2>&1")
            if "successful" in test.lower() or "ok" in test.lower():
                runner("nginx -s reload || systemctl reload nginx")
                return json.dumps({"ok": True, "domain": domain, "config": conf_path, "test": test}, ensure_ascii=False)
            else:
                runner(f"rm -f {conf_path}")
                return json.dumps({"ok": False, "error": f"Nginx config test failed: {test}"}, ensure_ascii=False)

        elif action == "remove":
            domain = str(params.get("domain", "")).strip()
            if not domain:
                return json.dumps({"ok": False, "error": "domain es requerido"})
            runner(f"rm -f /etc/nginx/conf.d/{domain}.conf /etc/nginx/sites-enabled/{domain}")
            test = runner("nginx -t 2>&1")
            runner("nginx -s reload || systemctl reload nginx")
            return json.dumps({"ok": True, "removed": domain, "test": test}, ensure_ascii=False)

        elif action == "health":
            domain = str(params.get("domain", "")).strip()
            if not domain:
                return json.dumps({"ok": False, "error": "domain es requerido"})
            check = _run(f"curl -sk -o /dev/null -w '%{{http_code}}' --connect-timeout 10 https://{domain}/health 2>/dev/null || "
                        f"curl -sk -o /dev/null -w '%{{http_code}}' --connect-timeout 10 http://{domain}/health 2>/dev/null || "
                        f"echo 'UNREACHABLE'")
            return json.dumps({"ok": True, "domain": domain, "status_code": check}, ensure_ascii=False)

        elif action == "ssl_setup":
            domain = str(params.get("domain", "")).strip()
            email = str(params.get("email", os.getenv("LETSENCRYPT_EMAIL", "admin@example.com"))).strip()
            if not domain:
                return json.dumps({"ok": False, "error": "domain es requerido"})
            result = runner(
                f"certbot certonly --nginx -d {domain} -d www.{domain} "
                f"--non-interactive --agree-tos --email {email} 2>&1",
                timeout=120,
            )
            return json.dumps({"ok": "congratulations" in result.lower() or "successfully" in result.lower(),
                              "result": result}, ensure_ascii=False)

        elif action == "reload_nginx":
            test = runner("nginx -t 2>&1")
            if "successful" in test.lower() or "ok" in test.lower():
                reload = runner("nginx -s reload || systemctl reload nginx")
                return json.dumps({"ok": True, "test": test, "reload": reload}, ensure_ascii=False)
            return json.dumps({"ok": False, "error": f"Config test failed: {test}"}, ensure_ascii=False)

        elif action == "status":
            result = {
                "nginx_test": runner("nginx -t 2>&1"),
                "nginx_status": runner("systemctl status nginx --no-pager 2>/dev/null || "
                                      "ps aux | grep nginx | grep -v grep"),
                "configs": runner("ls -la /etc/nginx/conf.d/ 2>/dev/null || echo 'no conf.d'"),
            }
            return json.dumps({"ok": True, "status": result}, ensure_ascii=False)

        return json.dumps({"ok": False, "error": f"Acción no soportada: {action}",
                          "supported": ["list", "add", "remove", "health", "ssl_setup",
                                        "reload_nginx", "status"]}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "action": action, "error": str(e)}, ensure_ascii=False)
