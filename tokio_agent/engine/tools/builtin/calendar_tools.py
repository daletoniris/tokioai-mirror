"""
Calendar Tool — Read, query, and share ICS calendar events.

Supports local .ics files and URLs, recurring events (WEEKLY/DAILY),
and formatted output for Telegram.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

WEEKDAY_ES = {0: "Lunes", 1: "Martes", 2: "Miércoles", 3: "Jueves",
              4: "Viernes", 5: "Sábado", 6: "Domingo"}
_DAY_MAP = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


# ── ICS parser ────────────────────────────────────────────────────────────

def _unfold(text: str) -> str:
    return re.sub(r"\r?\n[ \t]", "", text)


def _parse_dt(value: str) -> Optional[datetime]:
    if ":" in value and "=" in value.split(":")[0]:
        value = value.split(":", 1)[1]
    value = value.strip().replace("Z", "")
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _parse_rrule(line: str) -> Dict[str, str]:
    result = {}
    for part in line.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            result[k.strip()] = v.strip()
    return result


def _parse_ics(text: str) -> List[Dict[str, Any]]:
    text = _unfold(text)
    events: List[Dict] = []
    cur: Optional[Dict] = None
    for line in text.splitlines():
        line = line.strip()
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT":
            if cur:
                events.append(cur)
            cur = None
        elif cur is not None:
            if line.startswith("SUMMARY:"):
                cur["summary"] = line[8:].strip()
            elif line.startswith("DTSTART"):
                dt = _parse_dt(line.split(":", 1)[-1] if ":" in line else "")
                if dt:
                    cur["dtstart"] = dt
            elif line.startswith("DTEND"):
                dt = _parse_dt(line.split(":", 1)[-1] if ":" in line else "")
                if dt:
                    cur["dtend"] = dt
            elif line.startswith("LOCATION:"):
                cur["location"] = line[9:].strip()
            elif line.startswith("DESCRIPTION:"):
                cur["description"] = line[12:].strip()
            elif line.startswith("RRULE:"):
                cur["rrule"] = _parse_rrule(line[6:])
            elif line.startswith("X-MICROSOFT-CDO-BUSYSTATUS:"):
                cur["busystatus"] = line[27:].strip()
            elif line.startswith("UID:"):
                cur["uid"] = line[4:].strip()
    return events


def _expand_recurring(ev: Dict, start: date, end: date) -> List[Dict]:
    rrule = ev.get("rrule")
    dtstart = ev.get("dtstart")
    dtend = ev.get("dtend")
    if not dtstart:
        return []
    duration = (dtend - dtstart) if dtend else timedelta(hours=1)
    if not rrule:
        if start <= dtstart.date() <= end:
            return [ev]
        return []
    freq = rrule.get("FREQ", "")
    until_str = rrule.get("UNTIL", "")
    interval = int(rrule.get("INTERVAL", "1"))
    byday = rrule.get("BYDAY", "")
    until_date = end
    if until_str:
        ut = _parse_dt(until_str)
        if ut:
            until_date = min(ut.date(), end)
    occs: List[Dict] = []
    if freq == "WEEKLY" and byday:
        targets = [_DAY_MAP[d.strip()] for d in byday.split(",") if d.strip() in _DAY_MAP]
        current = dtstart.date()
        while current <= until_date:
            if current >= start and current.weekday() in targets:
                occ = dict(ev)
                occ["dtstart"] = datetime.combine(current, dtstart.time())
                occ["dtend"] = occ["dtstart"] + duration
                occs.append(occ)
            current += timedelta(days=1)
    elif freq == "DAILY":
        current = dtstart.date()
        step = timedelta(days=interval)
        while current <= until_date:
            if current >= start:
                occ = dict(ev)
                occ["dtstart"] = datetime.combine(current, dtstart.time())
                occ["dtend"] = occ["dtstart"] + duration
                occs.append(occ)
            current += step
    else:
        if start <= dtstart.date() <= end:
            occs.append(ev)
    return occs


def _load_calendar(file_path: str) -> str:
    if file_path.startswith(("http://", "https://")):
        import urllib.request
        with urllib.request.urlopen(file_path, timeout=15) as resp:
            return resp.read().decode("utf-8", errors="replace")
    path = Path(file_path).expanduser()
    if not path.exists():
        for candidate in [
            Path("/app/data/calendar.ics"),
            Path("/workspace/calendar.ics"),
            Path.home() / "calendar.ics",
        ]:
            if candidate.exists():
                path = candidate
                break
    return path.read_text(encoding="utf-8", errors="replace")


def _get_range(period: str):
    today = date.today()
    period = (period or "today").strip().lower()
    if period in ("today", "hoy"):
        return today, today
    if period in ("tomorrow", "mañana"):
        t = today + timedelta(days=1)
        return t, t
    if period in ("week", "semana", "this_week"):
        s = today - timedelta(days=today.weekday())
        return s, s + timedelta(days=6)
    if period in ("next_week", "proxima_semana"):
        s = today - timedelta(days=today.weekday()) + timedelta(weeks=1)
        return s, s + timedelta(days=6)
    if period in ("month", "mes"):
        s = today.replace(day=1)
        e = (today.replace(month=today.month % 12 + 1, day=1) - timedelta(days=1)
             if today.month < 12 else today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1))
        return s, e
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            d = datetime.strptime(period, fmt).date()
            return d, d
        except ValueError:
            continue
    return today, today


def _format_event(ev: Dict) -> str:
    dt = ev.get("dtstart")
    end = ev.get("dtend")
    summary = ev.get("summary", "Sin título")
    bs = ev.get("busystatus", "").upper()
    if bs == "FREE" or summary.lower() == "libre":
        emoji, label = "🟢", "Libre"
    elif bs == "TENTATIVE":
        emoji, label = "🟡", "Provisional"
    elif bs == "BUSY" or summary.lower() == "ocupado":
        emoji, label = "🔴", "Ocupado"
    else:
        emoji, label = "⚪", summary
    t1 = dt.strftime("%H:%M") if dt else "??:??"
    t2 = end.strftime("%H:%M") if end else "??:??"
    loc = f" 📍 {ev['location']}" if ev.get("location") else ""
    return f"  {emoji} {t1}–{t2}  {label}{loc}"


# ── Main entry point ─────────────────────────────────────────────────────

def calendar_tool(action: str = "query", params: Optional[Dict[str, Any]] = None) -> str:
    """
    Calendar tool.

    Actions:
      - query: Events for a period (today/tomorrow/week/month/YYYY-MM-DD)
      - summary: Calendar overview
      - share: Format for sharing
      - free_slots: Find available slots
    """
    params = params or {}
    action = (action or "query").lower().strip()

    file_path = params.get("file", "")
    if not file_path:
        for c in [Path("/app/data/calendar.ics"), Path("/workspace/calendar.ics"),
                   Path.home() / "calendar.ics"]:
            if c.exists():
                file_path = str(c)
                break
    if not file_path:
        return json.dumps({"ok": False, "error": "No se encontró calendar.ics"})

    try:
        ics_text = _load_calendar(file_path)
    except Exception as e:
        return json.dumps({"ok": False, "error": f"Error leyendo calendario: {e}"})

    events = _parse_ics(ics_text)

    if action == "query":
        period = params.get("period", params.get("date", "today"))
        start, end = _get_range(period)
        all_occ: List[Dict] = []
        for ev in events:
            all_occ.extend(_expand_recurring(ev, start, end))
        all_occ.sort(key=lambda e: e.get("dtstart", datetime.min))
        by_date: Dict[date, List] = {}
        for ev in all_occ:
            dt = ev.get("dtstart")
            if dt:
                by_date.setdefault(dt.date(), []).append(ev)
        lines = [f"📅 Calendario: {start.strftime('%d/%m')} — {end.strftime('%d/%m/%Y')}\n"]
        if not by_date:
            lines.append("  Sin eventos para este período.")
        else:
            cur = start
            while cur <= end:
                if cur in by_date:
                    dn = WEEKDAY_ES.get(cur.weekday(), "")
                    lines.append(f"\n── {dn} {cur.strftime('%d/%m')} ──")
                    for ev in by_date[cur]:
                        lines.append(_format_event(ev))
                cur += timedelta(days=1)
        total = len(all_occ)
        libre = sum(1 for e in all_occ if e.get("busystatus", "") == "FREE" or e.get("summary", "").lower() == "libre")
        ocupado = sum(1 for e in all_occ if e.get("busystatus", "") == "BUSY" or e.get("summary", "").lower() == "ocupado")
        lines.append(f"\n📊 Total: {total} | 🟢 {libre} libres | 🟡 {total - libre - ocupado} prov | 🔴 {ocupado} ocupados")
        return json.dumps({"ok": True, "formatted": "\n".join(lines)}, ensure_ascii=False, default=str)

    elif action == "summary":
        total = len(events)
        summaries: Dict[str, int] = {}
        for ev in events:
            s = ev.get("summary", "Sin título")
            summaries[s] = summaries.get(s, 0) + 1
        return json.dumps({"ok": True, "total_events": total, "event_types": summaries}, ensure_ascii=False, default=str)

    elif action == "free_slots":
        period = params.get("period", params.get("date", "today"))
        start, end = _get_range(period)
        all_occ = []
        for ev in events:
            all_occ.extend(_expand_recurring(ev, start, end))
        by_date: Dict[date, List] = {}
        for ev in all_occ:
            dt = ev.get("dtstart")
            if dt:
                by_date.setdefault(dt.date(), []).append(ev)
        lines = [f"🟢 Disponibilidad: {start.strftime('%d/%m')} — {end.strftime('%d/%m/%Y')}\n"]
        cur = start
        while cur <= end:
            free = [e for e in by_date.get(cur, [])
                    if e.get("busystatus", "") == "FREE" or e.get("summary", "").lower() == "libre"]
            if free:
                dn = WEEKDAY_ES.get(cur.weekday(), "")
                lines.append(f"── {dn} {cur.strftime('%d/%m')} ──")
                for ev in sorted(free, key=lambda e: e.get("dtstart", datetime.min)):
                    dt = ev.get("dtstart")
                    et = ev.get("dtend")
                    if dt and et:
                        lines.append(f"  🟢 {dt.strftime('%H:%M')}–{et.strftime('%H:%M')}")
            cur += timedelta(days=1)
        return json.dumps({"ok": True, "formatted": "\n".join(lines)}, ensure_ascii=False, default=str)

    return json.dumps({"ok": False, "error": f"Acción desconocida: {action}"})
