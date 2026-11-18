import json
import shutil
import tempfile
import unittest
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sentinel import extract, planner, reconcile
from sentinel.models import Deadline, Message
from sentinel.pipeline import run
from sentinel.sources import Config

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("America/Los_Angeles")
CFG = Config.load(ROOT / "demo" / "config.json")


def msg(body, posted, course="CSC 401", title=""):
    return Message(id="m", source="canvas-announcement", course=course, title=title, body=body, posted=posted)


class ExtractTests(unittest.TestCase):
    wed = datetime(2026, 11, 18, 9, 0, tzinfo=TZ)
    mon = datetime(2026, 11, 16, 9, 0, tzinfo=TZ)

    def one(self, body, posted=None, course="CSC 401"):
        claims = extract.extract_rules(msg(body, posted or self.wed, course), CFG.class_start)
        self.assertEqual(len(claims), 1, claims)
        return claims[0]

    def test_explicit_date_and_time(self):
        c = self.one("Project 4 is due Tuesday, November 24 at 11:59 PM.")
        self.assertEqual(c.due, datetime(2026, 11, 24, 23, 59, tzinfo=TZ))
        self.assertEqual((c.kind, c.number), ("project", "4"))

    def test_slash_date_defaults_to_2359(self):
        c = self.one("Homework 5 is due 11/30.")
        self.assertEqual(c.due, datetime(2026, 11, 30, 23, 59, tzinfo=TZ))
        self.assertIn("assumed 23:59", c.note)

    def test_next_weekday_said_on_wednesday(self):
        c = self.one("The Chapter 11 quiz moves to next Tuesday.")
        self.assertEqual(c.due.date(), datetime(2026, 11, 24).date())
        self.assertIn('read "next tuesday"', c.note.lower())

    def test_next_weekday_said_earlier_same_week_means_week_after(self):
        c = self.one("Quiz 6 is due next Tuesday.", posted=self.mon)
        self.assertEqual(c.due.date(), datetime(2026, 11, 24).date())

    def test_before_class_uses_lecture_start(self):
        c = self.one("Quiz 6 must be completed before class on Thursday.")
        self.assertEqual(c.due, datetime(2026, 11, 19, 14, 30, tzinfo=TZ))

    def test_tomorrow_midnight(self):
        c = self.one("Lab 3 is due tomorrow at midnight.")
        self.assertEqual(c.due, datetime(2026, 11, 19, 23, 59, tzinfo=TZ))

    def test_course_inferred_from_mail(self):
        m = Message(id="x", source="mail", course="", title="CSC 581 reminder",
                    body="Assignment 2 is due Friday at 5 PM.", posted=self.wed)
        c = extract.extract_rules(m, CFG.class_start)[0]
        self.assertEqual((c.course, c.due.hour), ("CSC 581", 17))

    def test_end_of_day_phrase_gives_2359_no_assumed_note(self):
        # "by end of day" and "EOD" on a Friday, posted Wednesday 18 Nov 2026
        for body in (
            "Homework 9 is due Friday by end of day.",
            "Homework 9 is due Friday EOD.",
        ):
            with self.subTest(body=body):
                c = self.one(body)
                self.assertEqual(c.due, datetime(2026, 11, 20, 23, 59, tzinfo=TZ),
                                 f"expected 23:59 on Friday 20 Nov, got {c.due}")
                self.assertFalse(c.note and "assumed" in c.note,
                                 f"note should not contain 'assumed', got: {c.note!r}")

    def test_sentences_without_items_are_ignored(self):
        self.assertEqual(extract.extract_rules(msg("The library closes at 10 PM on Friday.", self.wed)), [])

    def test_llm_path_parses_json_and_falls_back(self):
        fake = lambda p: 'Sure: [{"item": "Quiz 7", "kind": "quiz", "number": 7, "date": "2026-11-20", ' \
                         '"time": "17:00", "evidence": "Quiz 7 Friday 5pm", "note": "Friday = 20 Nov"}]'
        c = extract.extract_llm(msg("Quiz 7 is Friday at 5pm.", self.wed), ask=fake)[0]
        self.assertEqual((c.due, c.number), (datetime(2026, 11, 20, 17, 0, tzinfo=TZ), "7"))
        broken = lambda p: "no json here"
        c = extract.extract_llm(msg("Quiz 7 is due Friday at 5 PM.", self.wed), ask=broken)[0]
        self.assertEqual(c.due.hour, 17)          # rules took over


class ReconcileTests(unittest.TestCase):
    now = datetime(2026, 11, 18, 7, 0, tzinfo=TZ)

    def d(self, key, title, due, course="CSC 401"):
        return Deadline(key=key, course=course, title=title, due=due, source="canvas-assignment")

    def test_silent_move_new_and_removed(self):
        a = self.d("a", "Project 4", datetime(2026, 11, 24, 23, 59, tzinfo=TZ))
        b = self.d("b", "Quiz 6", datetime(2026, 11, 25, 23, 59, tzinfo=TZ))
        prev = {"assignments": {"a": a.to_json(), "b": b.to_json()}, "last_run": "2026-11-16T07:00:00-08:00"}
        a2 = self.d("a", "Project 4", datetime(2026, 11, 23, 23, 59, tzinfo=TZ))
        c = self.d("c", "Homework 9", datetime(2026, 11, 30, 23, 59, tzinfo=TZ))
        _, changes, _ = reconcile.reconcile(prev, self.now, [a2, c], [])
        self.assertEqual(sorted(ch.type for ch in changes), ["moved", "new", "removed"])
        moved = next(ch for ch in changes if ch.type == "moved")
        self.assertIn("no new announcement", moved.detail)

    def test_conflict_plans_on_earlier_date(self):
        q = self.d("q", "Chapter 11 Quiz", datetime(2026, 12, 1, 23, 59, tzinfo=TZ))
        claim = extract.extract_rules(msg("The Chapter 11 quiz moves to next Tuesday before class.",
                                          datetime(2026, 11, 17, 21, 0, tzinfo=TZ)), CFG.class_start)
        eff, changes, _ = reconcile.reconcile({}, self.now, [q], claim)
        self.assertEqual([ch.type for ch in changes], ["conflict"])
        self.assertEqual(eff[0].due, datetime(2026, 11, 24, 14, 30, tzinfo=TZ))

    def test_agreeing_message_is_not_a_conflict(self):
        p = self.d("p", "Project 4", datetime(2026, 11, 24, 23, 59, tzinfo=TZ))
        claim = extract.extract_rules(msg("Project 4 is due November 24 at 11:59 PM.", self.now), CFG.class_start)
        _, changes, _ = reconcile.reconcile({}, self.now, [p], claim)
        self.assertEqual(changes, [])

    def test_unmatched_message_date_becomes_deadline(self):
        claim = extract.extract_rules(msg("Homework 9 is due Friday.", self.now), CFG.class_start)
        eff, changes, _ = reconcile.reconcile({}, self.now, [], claim)
        self.assertEqual([ch.type for ch in changes], ["from_message"])
        self.assertEqual(eff[0].source, "canvas-announcement")


class PlannerTests(unittest.TestCase):
    now = datetime(2026, 11, 17, 7, 0, tzinfo=TZ)   # a Tuesday: CSC 401 14:30 and CSC 541 17:30

    def test_blocks_avoid_lectures_and_buffers(self):
        d = Deadline("k", "CSC 541", "Project 4", datetime(2026, 11, 23, 23, 59, tzinfo=TZ), "canvas-assignment",
                     kind="project", est_hours=12)
        p = planner.plan([d], self.now, CFG)
        busy = []
        for day in range(7):
            date = (self.now + timedelta(days=day)).date()
            for l in CFG.lectures:
                if date.weekday() in l["days"]:
                    s = datetime.combine(date, l["start"], tzinfo=TZ) - timedelta(minutes=CFG.buffer_min)
                    e = datetime.combine(date, l["end"], tzinfo=TZ) + timedelta(minutes=CFG.buffer_min)
                    busy.append((s, e))
        for b in p.blocks:
            self.assertGreaterEqual(b.start.time(), CFG.day_start)
            self.assertLessEqual(b.end.time(), CFG.day_end)
            self.assertLessEqual(b.end - b.start, timedelta(hours=CFG.block_max_h))
            for s, e in busy:
                self.assertTrue(b.end <= s or b.start >= e, (b, s, e))
        total = sum((b.end - b.start for b in p.blocks), timedelta())
        self.assertEqual(total, timedelta(hours=12))
        self.assertTrue(all(b.end <= d.due - timedelta(hours=24) for b in p.blocks))

    def test_daily_cap(self):
        d = Deadline("k", "CSC 500", "Final Paper", datetime(2026, 11, 30, 23, 59, tzinfo=TZ), "canvas-assignment",
                     kind="paper", est_hours=20)
        p = planner.plan([d], self.now, CFG)
        per_day = {}
        for b in p.blocks:
            per_day[b.start.date()] = per_day.get(b.start.date(), timedelta()) + (b.end - b.start)
        self.assertTrue(all(v <= timedelta(hours=CFG.daily_cap_h) for v in per_day.values()))

    def test_impossible_deadline_is_at_risk(self):
        d = Deadline("k", "CSC 401", "Project 5", datetime(2026, 11, 17, 13, 0, tzinfo=TZ), "canvas-assignment",
                     kind="project", est_hours=10)
        p = planner.plan([d], self.now, CFG)
        self.assertEqual(len(p.at_risk), 1)
        self.assertGreater(p.at_risk[0][1], 0)


class DemoTest(unittest.TestCase):
    def test_two_day_demo(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            state = tmp / "state.json"
            r1 = run(CFG, ROOT / "demo" / "day1", state, tmp / "d1", use_llm=False)
            self.assertIn("Nothing changed", r1.brief)
            r2 = run(CFG, ROOT / "demo" / "day2", state, tmp / "d2", use_llm=False)
            types = sorted(c.type for c in r2.changes)
            self.assertEqual(types, ["conflict", "conflict", "from_message", "moved", "new"])
            self.assertIn("BEGIN:VCALENDAR", (tmp / "d2" / "week.ics").read_text())
            data = json.loads((tmp / "d2" / "plan.json").read_text())
            self.assertTrue(data["blocks"])
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
