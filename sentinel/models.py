"""Core data types for Deadline Sentinel."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

# Rough effort estimates in hours, by kind of deliverable. Overridable in config.json.
DEFAULT_HOURS = {
    "project": 6.0,
    "paper": 8.0,
    "report": 5.0,
    "presentation": 4.0,
    "homework": 3.0,
    "assignment": 3.0,
    "lab": 2.0,
    "quiz": 1.5,
    "exam": 6.0,
    "other": 2.0,
}

KIND_WORDS = {
    "project": "project",
    "paper": "paper",
    "report": "report",
    "presentation": "presentation",
    "slides": "presentation",
    "homework": "homework",
    "hw": "homework",
    "assignment": "assignment",
    "lab": "lab",
    "quiz": "quiz",
    "exam": "exam",
    "midterm": "exam",
    "final exam": "exam",
}


def kind_of(title: str) -> str:
    t = title.lower()
    for word in ("final exam", "midterm", "exam", "quiz", "report", "paper", "presentation",
                 "slides", "project", "homework", "hw", "assignment", "lab"):
        if re.search(r"\b" + re.escape(word) + r"\b", t):
            return KIND_WORDS[word]
    return "other"


@dataclass
class Deadline:
    """One thing that is due. `key` is stable across runs for the same item."""
    key: str
    course: str
    title: str
    due: datetime            # timezone-aware
    source: str              # canvas-assignment | canvas-announcement | mail
    kind: str = "other"
    url: str = ""
    evidence: str = ""       # the sentence a message-derived date came from
    note: str = ""           # how a relative date was interpreted
    est_hours: Optional[float] = None

    def to_json(self) -> dict:
        d = asdict(self)
        d["due"] = self.due.isoformat()
        return d

    @staticmethod
    def from_json(d: dict) -> "Deadline":
        d = dict(d)
        d["due"] = datetime.fromisoformat(d["due"])
        return Deadline(**d)


@dataclass
class Message:
    """An announcement or an e-mail, as raw text."""
    id: str
    source: str              # canvas-announcement | mail
    course: str              # may be "" when unknown (mail); extract.py tries to infer it
    title: str
    body: str
    posted: datetime


@dataclass
class Claim:
    """A date stated in a message, with the item it seems to refer to."""
    course: str
    hint: str                # e.g. "chapter 11 quiz", "final report"
    kind: str
    number: Optional[str]
    due: datetime
    evidence: str
    note: str
    message_id: str
    source: str
    posted: Optional[datetime] = None


@dataclass
class Change:
    type: str                # new | moved | removed | conflict | from_message
    course: str
    title: str
    old_due: Optional[datetime] = None
    new_due: Optional[datetime] = None
    detail: str = ""
    evidence: str = ""


@dataclass
class Block:
    key: str
    course: str
    title: str
    start: datetime
    end: datetime


@dataclass
class Plan:
    blocks: list = field(default_factory=list)
    at_risk: list = field(default_factory=list)   # (Deadline, shortfall_hours)
