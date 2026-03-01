"""
Hostinger DNS & Site Tools — Manage DNS records and publish/unpublish sites.

Requires: HOSTINGER_API_TOKEN in .env.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import requests

_BASE = "https://api.hostinger.com/api/dns/v1"


def _headers() -> Dict[str, str]:
    token = os.getenv("HOSTINGER_API_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _get(path: str) -> Dict:
    r = requests.get(f"{_BASE}{path}", headers=_headers(), timeout=30)
    return {"status": r.status_code, "data": r.json() if r.status_code == 200 else r.text}


def _post(path: str, payload: Dict) -> Dict:
    r = requests.post(f"{_BASE}{path}", headers=_headers(), json=payload, timeout=30)
    return {"status": r.status_code, "data": r.json() if r.status_code in (200, 201) else r.text}


def _put(path: str, payload: Dict) -> Dict:
    r = requests.put(f"{_BASE}{path}", headers=_headers(), json=payload, timeout=30)
    return {"status": r.status_code, "data": r.json() if r.status_code == 200 else r.text}


def _delete(path: str) -> Dict:
    r = requests.delete(f"{_BASE}{path}", headers=_headers(), timeout=30)
    return {"status": r.status_code, "data": r.json() if r.status_code in (200, 204) else r.text}


def hostinger_tool(action: str, params: Optional[Dict[str, Any]] = None) -> str:
    """
    Hostinger DNS & site management.

    Actions:
      - list_dns: List DNS records (params: domain)
      - add_dns: Add DNS record (params: domain, type, name, value, ttl)
      - update_dns: Update DNS record (params: domain, record_id, type, name, value, ttl)
      - delete_dns: Delete DNS record (params: domain, record_id)
      - publish_site: Point domain to IP (params: domain, ip)
      - unpublish_site: Remove A records (params: domain)
      - setup_waf_dns: Setup DNS for WAF proxy (params: domain, waf_ip)
    """
    params = params or {}
    action = (action or "").strip().lower()
    token = os.getenv("HOSTINGER_API_TOKEN", "").strip()
    if not token:
        return json.dumps({"ok": False, "error": "HOSTINGER_API_TOKEN no configurado"}, ensure_ascii=False)

    try:
        domain = str(params.get("domain", "")).strip()

        if action == "list_dns":
            if not domain:
                return json.dumps({"ok": False, "error": "domain requerido"})
            r = _get(f"/zones/{domain}")
            return json.dumps({"ok": r["status"] == 200, "data": r["data"]}, ensure_ascii=False, default=str)

        elif action == "add_dns":
            if not domain:
                return json.dumps({"ok": False, "error": "domain requerido"})
            payload = {
                "type": str(params.get("type", "A")).upper(),
                "name": str(params.get("name", "@")),
                "content": str(params.get("value", "")),
                "ttl": int(params.get("ttl", 14400)),
            }
            r = _post(f"/zones/{domain}/records", payload)
            return json.dumps({"ok": r["status"] in (200, 201), "data": r["data"]}, ensure_ascii=False, default=str)

        elif action == "update_dns":
            record_id = str(params.get("record_id", ""))
            if not domain or not record_id:
                return json.dumps({"ok": False, "error": "domain y record_id requeridos"})
            payload = {
                "type": str(params.get("type", "A")).upper(),
                "name": str(params.get("name", "@")),
                "content": str(params.get("value", "")),
                "ttl": int(params.get("ttl", 14400)),
            }
            r = _put(f"/zones/{domain}/records/{record_id}", payload)
            return json.dumps({"ok": r["status"] == 200, "data": r["data"]}, ensure_ascii=False, default=str)

        elif action == "delete_dns":
            record_id = str(params.get("record_id", ""))
            if not domain or not record_id:
                return json.dumps({"ok": False, "error": "domain y record_id requeridos"})
            r = _delete(f"/zones/{domain}/records/{record_id}")
            return json.dumps({"ok": r["status"] in (200, 204)}, ensure_ascii=False, default=str)

        elif action == "publish_site":
            ip = str(params.get("ip", "")).strip()
            if not domain or not ip:
                return json.dumps({"ok": False, "error": "domain e ip requeridos"})
            results = []
            for name in ("@", "www"):
                r = _post(f"/zones/{domain}/records", {
                    "type": "A", "name": name, "content": ip, "ttl": 14400,
                })
                results.append({"name": name, "status": r["status"]})
            return json.dumps({"ok": True, "results": results}, ensure_ascii=False, default=str)

        elif action == "unpublish_site":
            if not domain:
                return json.dumps({"ok": False, "error": "domain requerido"})
            r = _get(f"/zones/{domain}")
            if r["status"] != 200:
                return json.dumps({"ok": False, "error": f"No pude leer DNS: {r['data']}"}, ensure_ascii=False, default=str)
            records = r["data"] if isinstance(r["data"], list) else r["data"].get("records", [])
            deleted = 0
            for rec in records:
                if rec.get("type") == "A":
                    _delete(f"/zones/{domain}/records/{rec.get('id', rec.get('record_id', ''))}")
                    deleted += 1
            return json.dumps({"ok": True, "deleted_records": deleted}, ensure_ascii=False, default=str)

        elif action == "setup_waf_dns":
            waf_ip = str(params.get("waf_ip", "")).strip()
            if not domain or not waf_ip:
                return json.dumps({"ok": False, "error": "domain y waf_ip requeridos"})
            r = _get(f"/zones/{domain}")
            if r["status"] == 200:
                records = r["data"] if isinstance(r["data"], list) else r["data"].get("records", [])
                for rec in records:
                    if rec.get("type") == "A":
                        _delete(f"/zones/{domain}/records/{rec.get('id', rec.get('record_id', ''))}")
            results = []
            for name in ("@", "www"):
                r = _post(f"/zones/{domain}/records", {
                    "type": "A", "name": name, "content": waf_ip, "ttl": 14400,
                })
                results.append({"name": name, "status": r["status"]})
            return json.dumps({"ok": True, "action": "setup_waf_dns", "results": results}, ensure_ascii=False, default=str)

        return json.dumps({"ok": False, "error": f"Acción no soportada: {action}",
                          "supported": ["list_dns", "add_dns", "update_dns", "delete_dns",
                                        "publish_site", "unpublish_site", "setup_waf_dns"]}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "action": action, "error": str(e)}, ensure_ascii=False)
