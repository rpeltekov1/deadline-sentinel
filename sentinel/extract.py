"""Find dates in announcements and e-mail, and say which item each one is about.

Two extractors share one output type (Claim):

* `extract_rules` is deterministic and always available. It handles explicit dates
  ("Nov 24", "11/24"), relative ones ("next Tuesday", "this Friday", "tomorrow"),
  times ("11:59 PM", "midnight", "before class") and item names ("Project 4",
  "Chapter 11 quiz", "final report").
* `extract_llm` asks a watsonx.ai Granite model for the same JSON when the
  WATSONX_* environment variables are set, and falls back to the rules on any error.

Every claim keeps the sentence it came from and a note on how a relative date was
read, so the student can check the interpretation instead of trusting it blindly.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from datetime import datetime, timedelta, time
from typing import Callable, Iterable, Optional

from .models import Claim, Message, KIND_WORDS

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
WEEKDAYS = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
            "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4,
            "saturday": 5, "sat": 5, "sunday": 6, "sun": 6}
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

RE_MONTH_DAY = re.compile(
    r"\b(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+"
    r"(?P<day>\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s*(?P<year>\d{4}))?", re.I)
RE_SLASH = re.compile(r"\b(?P<m>\d{1,2})/(?P<d>\d{1,2})(?:/(?P<y>\d{2,4}))?\b")
RE_WEEKDAY = re.compile(
    r"\b(?:(?P<rel>next|this|coming|on|by|until)\s+)?"
    r"(?P<wd>monday|tuesday|wednesday|thursday|friday|saturday|sunday|tues|thurs|thur)\b", re.I)
RE_DAYWORD = re.compile(r"\b(?P<w>tomorrow|tonight|today)\b", re.I)
RE_TIME = re.compile(r"\b(?P<h>\d{1,2})(?::(?P<mi>\d{2}))?\s*(?P<ap>a\.?m\.?|p\.?m\.?)", re.I)
RE_ITEM = re.compile(
    r"\b(?:(?P<chap>chapter|ch\.?)\s*(?P<chn>\d+)\s+)?"
    r"(?P<adj>final\s+)?"
    r"(?P<kind>project|quiz|homework|hw|assignment|lab|exam|midterm|paper|report|presentation|slides)"
    r"(?:\s*#?\s*(?P<num>\d+))?\b", re.I)
RE_COURSE = re.compile(r"\bCSC[\s-]?(\d{3})\b", re.I)
DUE_WORDS = re.compile(
    r"\b(due|deadline|submit|submission|mov(?:e|ed|es|ing)|postponed|pushed|extended|rescheduled|open|opens|close|closes|"
    r"by|before|until|now|take|complete)\b", re.I)


def course_from_text(text: str) -> str:
    m = RE_COURSE.search(text)
    return f"CSC {m.group(1)}" if m else ""


def _sentences(text: str) -> list[str]:
    text = re.sub(r"</p>|<br\s*/?>", "\n\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)          # strip HTML from Canvas announcements
    out = []
    for para in re.split(r"\n\s*\n", text):     # greetings and sign-offs stay separate
        para = re.sub(r"&nbsp;|\s+", " ", para).strip()
        out += [s.strip() for s in re.split(r"(?<=[.!?])\s+", para) if s.strip()]
    return out


def _resolve_date(sentence: str, posted: datetime) -> Optional[tuple[datetime, str]]:
    """Return (date at 00:00 local, interpretation note) or None."""
    tz = posted.tzinfo
    today = posted.date()
    m = RE_MONTH_DAY.search(sentence)
    if m:
        mon = MONTHS[m.group("mon").lower()[:3]]
        year = int(m.group("year")) if m.group("year") else today.year
        d = datetime(year, mon, int(m.group("day")), tzinfo=tz)
        if not m.group("year") and d.date() < today - timedelta(days=60):
            d = d.replace(year=year + 1)
        return d, ""
    m = RE_SLASH.search(sentence)
    if m:
        y = m.group("y")
        year = (int(y) + 2000 if y and len(y) == 2 else int(y)) if y else today.year
        try:
            return datetime(year, int(m.group("m")), int(m.group("d")), tzinfo=tz), ""
        except ValueError:
            return None
    m = RE_WEEKDAY.search(sentence)
    if m:
        target = WEEKDAYS[m.group("wd").lower()]
        rel = (m.group("rel") or "").lower()
        ahead = (target - today.weekday()) % 7
        if rel == "next":
            if ahead == 0:
                ahead = 7
            # "next Tuesday" said earlier in the same Mon-Sun week means the week after.
            if today.weekday() < target and ahead < 7:
                ahead += 7
        d = datetime.combine(today + timedelta(days=ahead), time(0), tzinfo=tz)
        phrase = (rel + " " if rel else "") + m.group("wd")
        note = f'read "{phrase}" (posted {DAY_NAMES[today.weekday()]} {today:%d %b}) as {DAY_NAMES[d.weekday()]} {d:%d %b}'
        return d, note
    m = RE_DAYWORD.search(sentence)
    if m:
        w = m.group("w").lower()
        d = datetime.combine(today + timedelta(days=1 if w == "tomorrow" else 0), time(0), tzinfo=tz)
        return d, f'read "{w}" (posted {today:%d %b}) as {DAY_NAMES[d.weekday()]} {d:%d %b}'
    return None


def _resolve_time(sentence: str, day: datetime, class_start: Optional[time]) -> tuple[datetime, str]:
    s = sentence.lower()
    m = RE_TIME.search(sentence)
    if m:
        h = int(m.group("h")) % 12
        if m.group("ap").lower().startswith("p"):
            h += 12
        return day.replace(hour=h, minute=int(m.group("mi") or 0)), ""
    if "midnight" in s:
        return day.replace(hour=23, minute=59), ""
    if "noon" in s:
        return day.replace(hour=12, minute=0), ""
    if "before class" in s or "start of class" in s or "in class" in s:
        if class_start:
            return day.replace(hour=class_start.hour, minute=class_start.minute), "before class = lecture start"
        return day.replace(hour=12, minute=0), "before class, lecture time unknown: assumed noon"
    if re.search(r"\beod\b|end.of.day", s):
        return day.replace(hour=23, minute=59), ""
    return day.replace(hour=23, minute=59), "no time given: assumed 23:59"


def extract_rules(msg: Message, class_start_for: Callable[[str, int], Optional[time]] = lambda c, wd: None
                  ) -> list[Claim]:
    course = msg.course or course_from_text(msg.title + " " + msg.body)
    claims: list[Claim] = []
    for sent in _sentences(msg.title + ". " + msg.body):
        item = RE_ITEM.search(sent)
        if not item or not DUE_WORDS.search(sent):
            continue
        res = _resolve_date(sent, msg.posted)
        if not res:
            continue
        day, note = res
        due, tnote = _resolve_time(sent, day, class_start_for(course, day.weekday()))
        kind = KIND_WORDS.get(item.group("kind").lower(), "other")
        num = item.group("chn") or item.group("num")
        lead = re.search(r"((?:[A-Z][a-z]+\s+){1,2})$", sent[:item.start()])
        hint = " ".join(x for x in [
            lead.group(1).strip().lower() if lead and not item.group("chn") else "",
            f"chapter {item.group('chn')}" if item.group("chn") else "",
            "final" if item.group("adj") else "",
            kind, item.group("num") or ""] if x)
        claims.append(Claim(course=course, hint=hint, kind=kind, number=num, due=due,
                            evidence=sent, note="; ".join(x for x in (note, tnote) if x),
                            message_id=msg.id, source=msg.source, posted=msg.posted))
    return claims


# ---------------------------------------------------------------- watsonx.ai (optional)

LLM_PROMPT = """You extract deadlines from a university course message.
Message posted {posted} ({weekday}). Course: {course}.
Return only a JSON list. Each element: {{"item": "<assignment name as written>", "kind": "<project|quiz|homework|exam|paper|report|presentation|lab|other>",
"number": "<number or null>", "date": "YYYY-MM-DD", "time": "HH:MM or null", "evidence": "<the exact sentence>",
"note": "<how you resolved any relative date>"}}. Return [] if the message states no deadline.

Message:
{text}
"""


def _watsonx_chat(prompt: str) -> str:  # pragma: no cover - needs a live watsonx.ai project
    key, project = os.environ["WATSONX_APIKEY"], os.environ["WATSONX_PROJECT_ID"]
    base = os.environ.get("WATSONX_URL", "https://us-south.ml.cloud.ibm.com")
    model = os.environ.get("WATSONX_MODEL", "ibm/granite-3-8b-instruct")
    req = urllib.request.Request(
        "https://iam.cloud.ibm.com/identity/token",
        data=f"grant_type=urn:ibm:params:oauth:grant-type:apikey&apikey={key}".encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    token = json.load(urllib.request.urlopen(req, timeout=30))["access_token"]
    body = {"model_id": model, "project_id": project, "max_tokens": 800, "temperature": 0,
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(f"{base}/ml/v1/text/chat?version=2024-10-08", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    out = json.load(urllib.request.urlopen(req, timeout=60))
    return out["choices"][0]["message"]["content"]


def extract_llm(msg: Message, ask: Callable[[str], str] = _watsonx_chat,
                class_start_for=lambda c, wd: None) -> list[Claim]:
    course = msg.course or course_from_text(msg.title + " " + msg.body)
    prompt = LLM_PROMPT.format(posted=msg.posted.date().isoformat(), weekday=DAY_NAMES[msg.posted.weekday()],
                               course=course or "unknown", text=(msg.title + "\n" + msg.body)[:6000])
    try:
        raw = ask(prompt)
        raw = raw[raw.index("["): raw.rindex("]") + 1]
        items = json.loads(raw)
        claims = []
        for it in items:
            day = datetime.fromisoformat(it["date"]).replace(tzinfo=msg.posted.tzinfo)
            if it.get("time"):
                hh, mm = map(int, it["time"].split(":"))
                due = day.replace(hour=hh, minute=mm)
            else:
                due = day.replace(hour=23, minute=59)
            kind = it.get("kind") or "other"
            num = str(it["number"]) if it.get("number") not in (None, "null", "") else None
            claims.append(Claim(course=course, hint=it.get("item", kind).lower(), kind=kind, number=num, due=due,
                                evidence=it.get("evidence", ""), note=("model: " + it.get("note", "")).strip(),
                                message_id=msg.id, source=msg.source, posted=msg.posted))
        return claims
    except Exception:
        return extract_rules(msg, class_start_for)


def extract(messages: Iterable[Message], use_llm: Optional[bool] = None, class_start_for=lambda c, wd: None
            ) -> list[Claim]:
    if use_llm is None:
        use_llm = bool(os.environ.get("WATSONX_APIKEY") and os.environ.get("WATSONX_PROJECT_ID"))
    out: list[Claim] = []
    for m in messages:
        out.extend(extract_llm(m, class_start_for=class_start_for) if use_llm
                   else extract_rules(m, class_start_for))
    return out
