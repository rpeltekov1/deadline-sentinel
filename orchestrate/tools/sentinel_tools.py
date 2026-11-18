"""watsonx Orchestrate tools for Deadline Sentinel.

Import with the repository root as the package root, so `sentinel/` and `demo/` travel with the tools:

    orchestrate tools import -k python -f orchestrate/tools/sentinel_tools.py \
        -r orchestrate/requirements.txt -p .

All three tools are read-only. In this version they run on the bundled demo semester
(Monday baseline, Wednesday snapshot); the local CLI (`python -m sentinel live`) is the path that
reads a real Canvas account.
"""
import json
import tempfile
from datetime import date
from pathlib import Path

from ibm_watsonx_orchestrate.agent_builder.tools import tool, ToolPermission

from sentinel.pipeline import run
from sentinel.sources import Config

ROOT = Path(__file__).resolve().parent.parent.parent


def _wednesday():
    cfg = Config.load(ROOT / "demo" / "config.json")
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state.json"
        run(cfg, ROOT / "demo" / "day1", state, None, use_llm=False)
        return run(cfg, ROOT / "demo" / "day2", state, None, use_llm=False)


@tool(permission=ToolPermission.READ_ONLY)
def get_morning_brief() -> str:
    """Return today's morning brief for the student: what changed in Canvas and e-mail since the last
    check, conflicts between sources that need the student's confirmation, work at risk, today's work
    blocks and the rest of the week.

    Returns:
        The brief as plain text.
    """
    return _wednesday().brief


@tool(permission=ToolPermission.READ_ONLY)
def get_changes() -> str:
    """List every detected change since the last check as JSON. Each entry has a type (moved, new,
    removed, conflict, from_message), the course, the item title, the old and new due dates, how a
    relative date was interpreted, and the exact sentence it came from.

    Returns:
        A JSON array of change objects.
    """
    r = _wednesday()
    return json.dumps([{"type": c.type, "course": c.course, "title": c.title,
                        "old_due": c.old_due.isoformat() if c.old_due else None,
                        "new_due": c.new_due.isoformat() if c.new_due else None,
                        "interpretation": c.detail, "evidence": c.evidence} for c in r.changes], indent=1)


@tool(permission=ToolPermission.READ_ONLY)
def get_plan_for_day(day: str) -> str:
    """Return the planned work blocks for one day of the re-planned week.

    Args:
        day: the date as YYYY-MM-DD, for example 2026-11-19.

    Returns:
        A JSON array of blocks with course, item, start and end, plus the deadlines due that day.
    """
    r = _wednesday()
    want = date.fromisoformat(day)
    blocks = [{"course": b.course, "item": b.title, "start": b.start.strftime("%H:%M"), "end": b.end.strftime("%H:%M")}
              for b in r.plan.blocks if b.start.date() == want]
    due = [{"course": d.course, "item": d.title, "due": d.due.strftime("%H:%M")} for d in r.deadlines
           if d.due.date() == want]
    return json.dumps({"day": day, "blocks": blocks, "due": due}, indent=1)
