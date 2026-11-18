"""Turn deadlines into work blocks that fit around lectures.

Earliest-deadline-first over free time: the working day (default 06:00-20:00) minus every
lecture and a buffer either side of it. Each item gets its estimated hours in blocks of at most
`block_max_hours`, aiming to finish `finish_early_hours` before the due time and falling back
to the due time itself. Whatever cannot fit before the due time is reported as at risk.
"""
from __future__ import annotations

from datetime import datetime, timedelta, time

from .models import Block, Deadline, Plan, DEFAULT_HOURS
from .sources import Config

STEP = timedelta(minutes=30)


def _round_up(t: datetime) -> datetime:
    t = t.replace(second=0, microsecond=0)
    extra = (30 - t.minute % 30) % 30
    return t + timedelta(minutes=extra)


def free_intervals(cfg: Config, start: datetime, end: datetime) -> list[list[datetime]]:
    out = []
    day = start.date()
    buf = timedelta(minutes=cfg.buffer_min)
    while day <= end.date():
        lo = datetime.combine(day, cfg.day_start, tzinfo=cfg.tz)
        hi = datetime.combine(day, cfg.day_end, tzinfo=cfg.tz)
        busy = sorted(
            (datetime.combine(day, l["start"], tzinfo=cfg.tz) - buf, datetime.combine(day, l["end"], tzinfo=cfg.tz) + buf)
            for l in cfg.lectures if day.weekday() in l["days"])
        cur = lo
        for b0, b1 in busy:
            if b0 > cur:
                out.append([cur, min(b0, hi)])
            cur = max(cur, b1)
        if cur < hi:
            out.append([cur, hi])
        day += timedelta(days=1)
    s = _round_up(start)
    return [[max(a, s), min(b, end)] for a, b in out if min(b, end) - max(a, s) >= STEP]


def hours_for(d: Deadline, cfg: Config) -> float:
    if d.est_hours:
        return d.est_hours
    return float(cfg.hours.get(d.kind, DEFAULT_HOURS.get(d.kind, 2.0)))


def plan(deadlines: list[Deadline], now: datetime, cfg: Config, horizon_days: int = 7) -> Plan:
    future = [d for d in deadlines if d.due > now]
    if not future:
        return Plan()
    end = max(min(max(d.due for d in future), now + timedelta(days=60)), now + timedelta(days=horizon_days))
    free = free_intervals(cfg, now, end)
    result = Plan()
    block_max = timedelta(hours=cfg.block_max_h)
    used: dict = {}
    cap = timedelta(hours=cfg.daily_cap_h)
    for d in sorted(future, key=lambda x: x.due):
        need = timedelta(hours=hours_for(d, cfg))
        target = d.due - timedelta(hours=cfg.finish_early_h)
        for limit in ((target, d.due) if target > now else (d.due,)):
            trial, left, taken, trial_used = _allocate(free, need, limit, block_max, used, cap)
            if left <= timedelta(0) or limit == d.due:
                break
        free, used = trial, trial_used
        for s, e in taken:
            result.blocks.append(Block(d.key, d.course, d.title, s, e))
        if left > timedelta(0):
            result.at_risk.append((d, round(left.total_seconds() / 3600, 1)))
    result.blocks.sort(key=lambda b: b.start)
    return result


def _allocate(free, need, limit, block_max, used, cap):
    """Take time from the earliest free intervals before `limit`, at most `cap` per day.
    Return (new_free, still_needed, taken, new_used)."""
    taken = []
    used = dict(used)
    new = [list(iv) for iv in free]
    left = need
    for iv in new:
        if left <= timedelta(0):
            break
        while left > timedelta(0) and iv[0] < min(iv[1], limit):
            avail = min(iv[1], limit) - iv[0]
            room = cap - used.get(iv[0].date(), timedelta(0))
            if avail < STEP or room < STEP:
                break
            chunk = min(avail, block_max, _ceil30(left), room)
            taken.append((iv[0], iv[0] + chunk))
            used[iv[0].date()] = used.get(iv[0].date(), timedelta(0)) + chunk
            left -= chunk
            iv[0] = iv[0] + chunk + STEP          # a 30-minute break between blocks
    return [iv for iv in new if iv[1] - iv[0] >= STEP], left, taken, used


def _ceil30(td: timedelta) -> timedelta:
    m = -(-int(td.total_seconds() // 60) // 30) * 30
    return timedelta(minutes=m)
