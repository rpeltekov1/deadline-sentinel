"""Command line: `python -m sentinel demo | run | live`."""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from .pipeline import run
from .sources import Config

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="sentinel", description="Deadline Sentinel: catch deadline changes, re-plan the week.")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("demo", help="run the two-day demo scenario (Monday baseline, then Wednesday)")
    d.add_argument("--out", default="out", help="where to write brief.txt, brief.html, week.ics, plan.json")
    d.add_argument("--day", choices=["1", "2", "both"], default="both")
    d.add_argument("--pause", action="store_true", help="wait for Enter between day 1 and day 2 (for recording)")

    r = sub.add_parser("run", help="one run on a snapshot folder (canvas.json plus optional mail/)")
    r.add_argument("snapshot")
    r.add_argument("--config", default=str(ROOT / "demo" / "config.json"))
    r.add_argument("--state", default=".sentinel/state.json")
    r.add_argument("--out", default="out")

    lv = sub.add_parser("live", help="one run against the Canvas API (read-only) and a folder of .eml files")
    lv.add_argument("--config", required=True)
    lv.add_argument("--state", default=".sentinel/state.json")
    lv.add_argument("--out", default="out")
    lv.add_argument("--llm", action="store_true", help="use watsonx.ai for extraction (needs WATSONX_* variables)")

    a = p.parse_args(argv)
    if a.cmd == "demo":
        cfg = Config.load(ROOT / "demo" / "config.json")
        state = Path(".sentinel/demo-state.json")
        if a.day in ("1", "both") and state.parent.exists():
            shutil.rmtree(state.parent)
        if a.day in ("1", "both"):
            r1 = run(cfg, ROOT / "demo" / "day1", state, Path(a.out) / "day1", use_llm=False)
            print(r1.brief)
            if a.day == "both" and a.pause:
                input("Day 1 done: Canvas and inbox as of Monday. Press Enter to jump to Wednesday...")
        if a.day in ("2", "both"):
            r2 = run(cfg, ROOT / "demo" / "day2", state, Path(a.out) / "day2", use_llm=False)
            print(r2.brief)
            print(f"Wrote {Path(a.out) / 'day2'}/brief.html, week.ics and plan.json")
        return 0
    if a.cmd == "run":
        res = run(Config.load(Path(a.config)), Path(a.snapshot), Path(a.state), Path(a.out))
        print(res.brief)
        return 0
    if a.cmd == "live":  # pragma: no cover
        res = run(Config.load(Path(a.config)), None, Path(a.state), Path(a.out), use_llm=a.llm or None)
        print(res.brief)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
