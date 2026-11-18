"""Render the morning brief (text and HTML), the week calendar (.ics) and plan.json."""
from __future__ import annotations

import html
import json
from datetime import datetime, timedelta, timezone

from .models import Change, Deadline, Plan


def fmt(d: datetime) -> str:
    return d.strftime("%a %d %b %H:%M")


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _change_line(c: Change) -> str:
    if c.type == "moved":
        return f"[MOVED] {c.course} {c.title}: {fmt(c.old_due)} -> {fmt(c.new_due)}. {_cap(c.detail)}."
    if c.type == "new":
        return f"[NEW] {c.course} {c.title}: due {fmt(c.new_due)}. {_cap(c.detail)}."
    if c.type == "removed":
        return f"[REMOVED] {c.course} {c.title} (was due {fmt(c.old_due)}). {_cap(c.detail)}."
    if c.type == "from_message":
        return f'[FOUND] {c.course} {c.title}: due {fmt(c.new_due)}, {c.detail}. Source: "{c.evidence}"'
    return ""


def _conflict_line(c: Change) -> str:
    return (f"{c.course} {c.title}: Canvas says {fmt(c.old_due)}, a message says {fmt(c.new_due)}"
            + (f" ({c.detail})" if c.detail else "") + f'. Source: "{c.evidence}" '
            f"Planning on {fmt(min(c.old_due, c.new_due))} until you confirm with the instructor.")


def brief_sections(now: datetime, deadlines: list[Deadline], changes: list[Change], plan: Plan):
    changed = [_change_line(c) for c in changes if c.type != "conflict"]
    conflicts = [_conflict_line(c) for c in changes if c.type == "conflict"]
    risk = [f"{d.course} {d.title} (due {fmt(d.due)}): {h} h of work does not fit before the deadline."
            for d, h in plan.at_risk]
    today = [f"{b.start:%H:%M}-{b.end:%H:%M}  {b.course} {b.title}" for b in plan.blocks
             if b.start.date() == now.date()]
    week = []
    for i in range(1, 7):
        day = (now + timedelta(days=i)).date()
        items = [f"{b.start:%H:%M}-{b.end:%H:%M} {b.course} {b.title}" for b in plan.blocks if b.start.date() == day]
        if items:
            week.append(f"{day:%a %d %b}: " + "; ".join(items))
    upcoming = [f"{fmt(d.due)}  {d.course} {d.title}" + (" (from a message)" if d.source != "canvas-assignment" else "")
                for d in deadlines if now < d.due <= now + timedelta(days=14)]
    return [("What changed", changed or ["Nothing changed since the last run."]),
            ("Needs your confirmation", conflicts or ["No conflicts between Canvas and your messages."]),
            ("At risk", risk or ["Everything fits before its deadline."]),
            ("Today", today or ["No work blocks today."]),
            ("Rest of the week", week or ["No blocks."]),
            ("Due in the next 14 days", upcoming or ["Nothing."])]


FOOTER = "Deadline Sentinel only reads. It never submits work, changes Canvas or sends mail."


def brief_text(now, deadlines, changes, plan) -> str:
    lines = [f"DEADLINE SENTINEL  morning brief for {now:%A %d %B %Y, %H:%M}", ""]
    for title, items in brief_sections(now, deadlines, changes, plan):
        lines.append(title.upper())
        lines += [f"  - {x}" for x in items]
        lines.append("")
    lines.append(FOOTER)
    return "\n".join(lines) + "\n"


def brief_html(now, deadlines, changes, plan) -> str:
    parts = []
    for title, items in brief_sections(now, deadlines, changes, plan):
        flag = " alert" if title in ("What changed", "Needs your confirmation", "At risk") and not items[0].startswith(
            ("Nothing", "No conflicts", "Everything")) else ""
        lis = "".join(f"<li>{html.escape(x)}</li>" for x in items)
        parts.append(f'<section class="{flag.strip()}"><h2>{html.escape(title)}</h2><ul>{lis}</ul></section>')
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Deadline Sentinel - {now:%d %b %Y}</title>
<style>
body{{font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;color:#000;background:#fff;max-width:880px;margin:2rem auto;padding:0 1rem}}
h1{{font-size:1.6rem;letter-spacing:.02em;border-bottom:3px solid #000;padding-bottom:.4rem}}
h2{{font-size:1rem;text-transform:uppercase;letter-spacing:.08em;margin:1.6rem 0 .4rem}}
section.alert{{border-left:6px solid #000;padding-left:1rem}}
ul{{margin:0;padding-left:1.2rem}} li{{margin:.3rem 0}}
footer{{margin-top:2rem;border-top:1px solid #000;padding-top:.6rem;font-size:.85rem}}
</style></head><body>
<h1>Deadline Sentinel &middot; {now:%A %d %B %Y, %H:%M}</h1>
{''.join(parts)}
<footer>{FOOTER}</footer></body></html>
"""


def _ics_time(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", r"\;").replace(",", r"\,").replace("\n", r"\n")


def ics(now: datetime, deadlines: list[Deadline], plan: Plan) -> str:
    stamp = _ics_time(now)
    ev = []
    for i, b in enumerate(plan.blocks):
        ev.append(["BEGIN:VEVENT", f"UID:block-{i}-{_ics_time(b.start)}@deadline-sentinel", f"DTSTAMP:{stamp}",
                   f"DTSTART:{_ics_time(b.start)}", f"DTEND:{_ics_time(b.end)}",
                   f"SUMMARY:{_esc(b.course + ' - ' + b.title)}", "END:VEVENT"])
    for d in deadlines:
        if d.due > now:
            ev.append(["BEGIN:VEVENT", f"UID:due-{_esc(d.key)}@deadline-sentinel", f"DTSTAMP:{stamp}",
                       f"DTSTART:{_ics_time(d.due - timedelta(minutes=15))}", f"DTEND:{_ics_time(d.due)}",
                       f"SUMMARY:{_esc('DUE ' + d.course + ' ' + d.title)}", "TRANSP:TRANSPARENT", "END:VEVENT"])
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Deadline Sentinel//EN", "CALSCALE:GREGORIAN"]
    for e in ev:
        lines += e
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def plan_json(now, deadlines, changes, plan) -> str:
    return json.dumps({
        "generated": now.isoformat(),
        "changes": [{**c.__dict__, "old_due": c.old_due.isoformat() if c.old_due else None,
                     "new_due": c.new_due.isoformat() if c.new_due else None} for c in changes],
        "deadlines": [d.to_json() for d in deadlines],
        "blocks": [{"course": b.course, "title": b.title, "start": b.start.isoformat(), "end": b.end.isoformat()}
                   for b in plan.blocks],
        "at_risk": [{"course": d.course, "title": d.title, "due": d.due.isoformat(), "short_hours": h}
                    for d, h in plan.at_risk],
    }, indent=2)
