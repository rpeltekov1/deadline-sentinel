"""Read-only inputs: a Canvas snapshot (demo), the live Canvas API, and a folder of e-mail.

Nothing here writes to Canvas or sends mail. The live client only issues GET requests.
"""
from __future__ import annotations

import email
import email.policy
import json
import os
import re
import urllib.request
from datetime import datetime, time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from .models import Deadline, Message, kind_of

DAY_IDX = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}


class Config:
    def __init__(self, data: dict):
        self.data = data
        self.tz = ZoneInfo(data.get("timezone", "America/Los_Angeles"))
        self.day_start = _hm(data.get("workday_start", "06:00"))
        self.day_end = _hm(data.get("workday_end", "20:00"))
        self.buffer_min = int(data.get("lecture_buffer_minutes", 60))
        self.block_max_h = float(data.get("block_max_hours", 2))
        self.finish_early_h = float(data.get("finish_early_hours", 24))
        self.daily_cap_h = float(data.get("daily_cap_hours", 6))
        self.hours = data.get("hours", {})
        self.lectures = [
            {"course": l["course"], "days": [DAY_IDX[d] for d in l["days"]],
             "start": _hm(l["start"]), "end": _hm(l["end"])} for l in data.get("lectures", [])]

    @staticmethod
    def load(path: Path) -> "Config":
        return Config(json.loads(Path(path).read_text()))

    def class_start(self, course: str, weekday: int) -> Optional[time]:
        for l in self.lectures:
            if l["course"] == course and weekday in l["days"]:
                return l["start"]
        return None


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _dt(s: str, tz) -> datetime:
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return (d if d.tzinfo else d.replace(tzinfo=tz)).astimezone(tz)


# ---------------------------------------------------------------- snapshot (demo and tests)

def load_snapshot(folder: Path, cfg: Config) -> tuple[datetime, list[Deadline], list[Message]]:
    folder = Path(folder)
    snap = json.loads((folder / "canvas.json").read_text())
    now = _dt(snap["captured"], cfg.tz)
    deadlines = [_assignment(a, cfg) for a in snap.get("assignments", []) if a.get("due_at")]
    msgs = [Message(id=f"ann-{a['id']}", source="canvas-announcement", course=a["course"], title=a["title"],
                    body=a["message"], posted=_dt(a["posted_at"], cfg.tz)) for a in snap.get("announcements", [])]
    if (folder / "mail").is_dir():
        msgs += read_mail_dir(folder / "mail", cfg)
    return now, deadlines, msgs


def _assignment(a: dict, cfg: Config) -> Deadline:
    return Deadline(key=f"canvas:{a['id']}", course=a["course"], title=a["name"], due=_dt(a["due_at"], cfg.tz),
                    source="canvas-assignment", kind=kind_of(a["name"]), url=a.get("html_url", ""))


def read_mail_dir(folder: Path, cfg: Config) -> list[Message]:
    out = []
    for p in sorted(Path(folder).glob("*.eml")):
        m = email.message_from_bytes(p.read_bytes(), policy=email.policy.default)
        part = m.get_body(preferencelist=("plain", "html"))
        body = part.get_content() if part else ""
        posted = parsedate_to_datetime(m["Date"]).astimezone(cfg.tz) if m["Date"] else datetime.now(cfg.tz)
        out.append(Message(id=f"mail-{m.get('Message-ID', p.name).strip('<>')}", source="mail", course="",
                           title=str(m["Subject"] or ""), body=body, posted=posted))
    return out


# ---------------------------------------------------------------- live Canvas (GET only)

class CanvasClient:  # pragma: no cover - needs a Canvas token
    def __init__(self, base_url: str, token: str, tz):
        self.base, self.token, self.tz = base_url.rstrip("/"), token, tz

    def _get(self, path: str) -> list:
        url, out = f"{self.base}/api/v1/{path}", []
        while url:
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token}"})
            with urllib.request.urlopen(req, timeout=30) as r:
                out.extend(json.load(r))
                nxt = re.search(r'<([^>]+)>;\s*rel="next"', r.headers.get("Link", ""))
                url = nxt.group(1) if nxt else None
        return out

    def snapshot(self, cfg: Config) -> tuple[datetime, list[Deadline], list[Message]]:
        now = datetime.now(self.tz)
        courses = self._get("users/self/courses?enrollment_state=active&per_page=50")
        deadlines, msgs = [], []
        for c in courses:
            code = _course_code(c)
            for a in self._get(f"courses/{c['id']}/assignments?per_page=100"):
                if a.get("due_at"):
                    deadlines.append(_assignment({"id": a["id"], "course": code, "name": a["name"],
                                                  "due_at": a["due_at"], "html_url": a.get("html_url", "")}, cfg))
            for a in self._get(f"announcements?context_codes[]=course_{c['id']}&per_page=50"):
                msgs.append(Message(id=f"ann-{a['id']}", source="canvas-announcement", course=code,
                                    title=a.get("title", ""), body=a.get("message", ""),
                                    posted=_dt(a["posted_at"], self.tz)))
        return now, deadlines, msgs


def _course_code(c: dict) -> str:
    m = re.search(r"CSC[\s-]?(\d{3})", c.get("course_code", "") + " " + c.get("name", ""), re.I)
    return f"CSC {m.group(1)}" if m else c.get("course_code", str(c["id"]))


def live_snapshot(cfg: Config):  # pragma: no cover
    canvas = cfg.data.get("canvas", {})
    token = os.environ.get(canvas.get("token_env", "CANVAS_TOKEN"), "")
    if not token:
        raise SystemExit("Set CANVAS_TOKEN (Canvas > Account > Settings > New access token).")
    now, deadlines, msgs = CanvasClient(canvas.get("base_url", "https://canvas.instructure.com"), token,
                                        cfg.tz).snapshot(cfg)
    if cfg.data.get("mail_dir"):
        msgs += read_mail_dir(Path(cfg.data["mail_dir"]).expanduser(), cfg)
    return now, deadlines, msgs
