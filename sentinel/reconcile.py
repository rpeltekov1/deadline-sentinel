"""Compare today's sources with what was known yesterday, and with each other.

Rules, in order:
1. A Canvas due date that differs from the last run is a silent change ("moved").
2. A date in an announcement or e-mail that matches a Canvas item but disagrees with it is a
   conflict. The plan uses the EARLIER of the two dates, and the brief asks the student to confirm
   with the instructor. Sentinel never decides on its own that a later date is safe.
3. A date in a message that matches no Canvas item becomes a new deadline, marked with its source.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

from .models import Change, Claim, Deadline, kind_of


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z]+|\d+", s.lower()))


def match(claim: Claim, deadlines: list[Deadline]) -> Optional[Deadline]:
    best, best_score = None, 0.0
    for d in deadlines:
        if claim.course and d.course != claim.course:
            continue
        toks = _tokens(d.title)
        if claim.kind != kind_of(d.title) and claim.kind not in toks:
            continue
        if claim.number and claim.number not in toks:
            continue
        score = 1.0 + (1.0 if claim.number else 0.0) + (0.5 if "final" in claim.hint and "final" in toks else 0.0)
        score -= abs((d.due - claim.due).total_seconds()) / (86400 * 365)   # tie-break: nearer date
        if score > best_score:
            best, best_score = d, score
    return best


def reconcile(prev: dict, now: datetime, deadlines: list[Deadline], claims: list[Claim]
              ) -> tuple[list[Deadline], list[Change], dict]:
    prev_items = {k: Deadline.from_json(v) for k, v in prev.get("assignments", {}).items()}
    last_run = datetime.fromisoformat(prev["last_run"]) if prev.get("last_run") else None
    baseline = not prev_items
    changes: list[Change] = []

    current = {d.key: d for d in deadlines}
    if not baseline:
        for k, d in current.items():
            old = prev_items.get(k)
            if old is None:
                changes.append(Change("new", d.course, d.title, new_due=d.due, detail="new item in Canvas"))
            elif old.due != d.due:
                announced = any(match(c, [d]) and abs((c.due - d.due).total_seconds()) < 60
                                and (last_run is None or (c.posted and c.posted > last_run)) for c in claims)
                changes.append(Change("moved", d.course, d.title, old_due=old.due, new_due=d.due,
                                      detail="changed in Canvas and announced" if announced
                                      else "changed in Canvas with no new announcement"))
        for k, old in prev_items.items():
            if k not in current and old.due > now:
                changes.append(Change("removed", old.course, old.title, old_due=old.due,
                                      detail="no longer in Canvas"))

    effective = {k: Deadline(**{**d.__dict__}) for k, d in current.items()}
    seen = set()
    for c in claims:
        if c.due < now - timedelta(hours=12):
            continue
        sig = (c.course, c.hint, c.due)
        if sig in seen:
            continue
        seen.add(sig)
        d = match(c, deadlines)
        if d is not None:
            if abs((d.due - c.due).total_seconds()) < 60:
                continue                                    # message agrees with Canvas
            changes.append(Change("conflict", d.course, d.title, old_due=d.due, new_due=c.due,
                                  detail=c.note or "", evidence=c.evidence))
            e = effective[d.key]
            if c.due < e.due:
                e.due, e.evidence, e.note = c.due, c.evidence, "planned on the earlier date until confirmed"
        else:
            title = c.hint.title() if c.hint else "Unnamed deadline"
            key = f"msg:{c.course}:{c.hint}"
            effective[key] = Deadline(key=key, course=c.course or "?", title=title, due=c.due, source=c.source,
                                      kind=c.kind, evidence=c.evidence, note=c.note)
            changes.append(Change("from_message", c.course or "?", title, new_due=c.due,
                                  detail=f"found in {'an e-mail' if c.source == 'mail' else 'an announcement'}, "
                                         f"not in Canvas" + (f"; {c.note}" if c.note else ""),
                                  evidence=c.evidence))

    state = {"assignments": {k: d.to_json() for k, d in current.items()}, "last_run": now.isoformat()}
    order = {"moved": 0, "conflict": 1, "from_message": 2, "new": 3, "removed": 4}
    changes.sort(key=lambda ch: (order[ch.type], ch.new_due or ch.old_due))
    return sorted(effective.values(), key=lambda d: d.due), changes, state
