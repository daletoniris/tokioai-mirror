"""
Cloudflare API Tools — Configure tunnel routes and manage DNS via Cloudflare.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

import requests


def _cf_headers(api_token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}


def _get_ingress(url: str, api_token: str) -> Tuple[List[Dict], str]:
    try:
        r = requests.get(url, headers=_cf_headers(api_token), timeout=30)
        if r.status_code != 200:
            return [], f"HTTP {r.status_code}: {r.text[:240]}"
        ingress = r.json().get("result", {}).get("config", {}).get("ingress", [])
        return ingress if isinstance(ingress, list) else [], ""
    except Exception as e:
        return [], str(e)


def _put_ingress(url: str, api_token: str, ingress: List[Dict]) -> Tuple[bool, str]:
    try:
        r = requests.put(url, headers=_cf_headers(api_token), json={"config": {"ingress": ingress}}, timeout=30)
        return (True, "ok") if r.status_code == 200 else (False, f"HTTP {r.status_code}: {r.text[:400]}")
    except Exception as e:
        return False, str(e)


def configure_tunnel_route(
    tunnel_id: str, account_id: str, hostname: str,
    service: str, api_token: str,
) -> Tuple[bool, str]:
    """Configure a public hostname route for a Cloudflare tunnel."""
    if not api_token:
        return False, "CLOUDFLARE_API_TOKEN no configurado"
    if not account_id:
        return False, "CLOUDFLARE_ACCOUNT_ID no configurado"
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations"
    ingress, err = _get_ingress(url, api_token)
    if err:
        return False, f"No pude leer config del túnel: {err}"
    ingress = [r for r in ingress if str(r.get("hostname", "")).lower() != hostname.lower()]
    ingress.insert(0, {"hostname": hostname, "service": service})
    if not hostname.startswith("www."):
        www = f"www.{hostname}"
        ingress = [r for r in ingress if str(r.get("hostname", "")).lower() != www.lower()]
        ingress.insert(1, {"hostname": www, "service": service})
    if not any(r.get("service", "").startswith("http_status:") for r in ingress):
        ingress.append({"service": "http_status:404"})
    ok, put_err = _put_ingress(url, api_token, ingress)
    return (True, f"Ruta configurada: {hostname} -> {service}") if ok else (False, put_err)


def remove_tunnel_route(
    tunnel_id: str, account_id: str, hostname: str, api_token: str,
) -> Tuple[bool, str]:
    """Remove hostname route from tunnel config."""
    if not api_token or not account_id or not tunnel_id or not hostname:
        return False, "Parámetros incompletos"
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations"
    ingress, err = _get_ingress(url, api_token)
    if err:
        return False, err
    wanted = {hostname.lower()}
    if not hostname.startswith("www."):
        wanted.add(f"www.{hostname}".lower())
    before = len(ingress)
    ingress = [r for r in ingress if str(r.get("hostname", "")).lower() not in wanted]
    removed = before - len(ingress)
    if not any(r.get("service", "").startswith("http_status:") for r in ingress):
        ingress.append({"service": "http_status:404"})
    ok, put_err = _put_ingress(url, api_token, ingress)
    return (True, f"Rutas removidas: {removed}") if ok else (False, put_err)


def cloudflare_tool(action: str, params: Optional[Dict[str, Any]] = None) -> str:
    """
    Cloudflare API tool.

    Actions:
      - configure_tunnel_route: params(tunnel_id, account_id, hostname, service, api_token)
      - remove_tunnel_route: params(tunnel_id, account_id, hostname, api_token)
    """
    params = params or {}
    action = (action or "").strip().lower()
    try:
        api_token = str(params.get("api_token", os.getenv("CLOUDFLARE_API_TOKEN", ""))).strip()
        account_id = str(params.get("account_id", os.getenv("CLOUDFLARE_ACCOUNT_ID", ""))).strip()
        tunnel_id = str(params.get("tunnel_id", os.getenv("CLOUDFLARED_TUNNEL_ID", ""))).strip()

        if action == "configure_tunnel_route":
            hostname = str(params.get("hostname", "")).strip()
            service = str(params.get("service", "http://localhost:8080")).strip()
            ok, msg = configure_tunnel_route(tunnel_id, account_id, hostname, service, api_token)
            return json.dumps({"ok": ok, "action": action, "result" if ok else "error": msg}, ensure_ascii=False)

        elif action == "remove_tunnel_route":
            hostname = str(params.get("hostname", "")).strip()
            ok, msg = remove_tunnel_route(tunnel_id, account_id, hostname, api_token)
            return json.dumps({"ok": ok, "action": action, "result" if ok else "error": msg}, ensure_ascii=False)

        return json.dumps({"ok": False, "error": f"Acción no soportada: {action}",
                          "supported": ["configure_tunnel_route", "remove_tunnel_route"]}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "action": action, "error": str(e)}, ensure_ascii=False)
