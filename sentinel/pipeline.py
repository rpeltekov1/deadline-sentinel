"""One Sentinel run: read sources, extract claims, reconcile, plan, write outputs."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import extract, outputs, planner, reconcile, sources
from .models import Change, Deadline, Plan


@dataclass
class RunResult:
    now: datetime
    deadlines: list
    changes: list
    plan: Plan
    brief: str


def run(cfg: sources.Config, snapshot: Optional[Path], state_file: Path, out_dir: Optional[Path],
        use_llm: Optional[bool] = None) -> RunResult:
    if snapshot is not None:
        now, deadlines, msgs = sources.load_snapshot(snapshot, cfg)
    else:  # pragma: no cover
        now, deadlines, msgs = sources.live_snapshot(cfg)
    claims = extract.extract(msgs, use_llm=use_llm, class_start_for=cfg.class_start)
    prev = json.loads(state_file.read_text()) if state_file.exists() else {}
    effective, changes, state = reconcile.reconcile(prev, now, deadlines, claims)
    plan = planner.plan(effective, now, cfg)
    brief = outputs.brief_text(now, effective, changes, plan)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2))
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "brief.txt").write_text(brief)
        (out_dir / "brief.html").write_text(outputs.brief_html(now, effective, changes, plan))
        (out_dir / "week.ics").write_text(outputs.ics(now, effective, plan))
        (out_dir / "plan.json").write_text(outputs.plan_json(now, effective, changes, plan))
    return RunResult(now, effective, changes, plan, brief)
