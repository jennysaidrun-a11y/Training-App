"""Training plans: a set schedule for new people, laid out day by day, with each day's lessons
and hands-on tasks in the order they're done ("Day 1: Lockout lesson, then shadow the mixer").

Plans live in content/plans.yaml (no personal data, saved to git like lessons). Who is on which
plan, from which date, and which tasks a trainer has signed off are records in the database
(db.py, kept forever). Lessons count as done when the person passes them; tasks when a manager
ticks them off."""
import datetime as dt
import re
import secrets

import yaml

from . import content

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DEFAULT_WORKDAYS = "01234"      # Monday to Friday
MAX_DAYS = 120
MAX_ITEMS = 40
KEY = re.compile(r"^[a-z0-9]{6,16}$")


def path():
    return content.CONTENT / "plans.yaml"


def new_key():
    return secrets.token_hex(5)


def load_plans():
    try:
        data = yaml.safe_load(path().read_text(encoding="utf-8")) or [] if path().exists() else []
    except yaml.YAMLError:
        data = []     # a broken file never takes the app down
    return [clean(p) for p in data if isinstance(p, dict)]


def get(plan_id):
    return next((p for p in load_plans() if p["id"] == plan_id), None)


def clean(p, lesson_ids=None):
    """A plan as the app keeps it: id, name, roles, days [{title, items [{key, lesson | task}]}].
    With lesson_ids, lessons that don't exist are dropped."""
    days = []
    for d in (p.get("days") or [])[:MAX_DAYS]:
        if not isinstance(d, dict):
            continue
        items = []
        for it in (d.get("items") or [])[:MAX_ITEMS]:
            if not isinstance(it, dict):
                continue
            key = str(it.get("key") or "")
            key = key if KEY.match(key) else new_key()
            if it.get("lesson") and (lesson_ids is None or str(it["lesson"]) in lesson_ids):
                items.append({"key": key, "lesson": str(it["lesson"])})
            elif str(it.get("task") or "").strip():
                items.append({"key": key, "task": str(it["task"]).strip()[:500]})
        days.append({"title": str(d.get("title") or "").strip()[:120], "items": items})
    pid = str(p.get("id") or "")
    return {"id": pid if KEY.match(pid) else new_key(), "name": str(p.get("name") or "").strip()[:120] or "Untitled plan",
            "roles": [str(r) for r in p.get("roles") or []], "days": days}


def save_plans(plans):
    header = "# Training plans: what a new person does each day, in order. Managers edit these on\n" \
             "# Manager > Plans. Each item is a lesson (by id) or a hands-on task a trainer signs off.\n"
    path().write_text(header + yaml.safe_dump([clean(p) for p in plans], sort_keys=False, allow_unicode=True, width=100),
                      encoding="utf-8")


def save(plan):
    plans = load_plans()
    plan = clean(plan, set(content.load_lessons()))
    for i, p in enumerate(plans):
        if p["id"] == plan["id"]:
            plans[i] = plan
            break
    else:
        plans.append(plan)
    save_plans(plans)
    return plan


def delete(plan_id):
    save_plans([p for p in load_plans() if p["id"] != plan_id])


def good_workdays(text):
    days = "".join(sorted(set(c for c in str(text or "") if c in "0123456")))
    return days or DEFAULT_WORKDAYS


def dates(start, n, workdays=DEFAULT_WORKDAYS):
    """The dates of plan days 1..n, counting only the workdays (0 = Monday) from the start date."""
    out, day = [], start
    work = {int(c) for c in good_workdays(workdays)}
    while len(out) < n:
        if day.weekday() in work:
            out.append(day)
        day += dt.timedelta(days=1)
    return out


def progress(plan, assignment, lesson_done, checked, today=None):
    """Each day of one person's plan with its date, items and how far along it is.
    lesson_done(lesson_id) -> bool; checked: {item key: check row}."""
    today = today or dt.date.today()
    start = dt.date.fromisoformat(assignment["start_date"])
    lessons = content.load_lessons()
    when = dates(start, len(plan["days"]), assignment.get("workdays"))
    days, today_n = [], None
    for n, (d, date) in enumerate(zip(plan["days"], when), 1):
        items = []
        for it in d["items"]:
            if "lesson" in it:
                lesson = lessons.get(it["lesson"])
                items.append({**it, "kind": "lesson", "title": lesson["title"] if lesson else "(lesson removed)",
                              "exists": bool(lesson), "done": bool(lesson) and lesson_done(it["lesson"])})
            else:
                c = checked.get(it["key"])
                items.append({**it, "kind": "task", "title": it["task"], "done": bool(c), "check": c})
        done = all(i["done"] for i in items)
        state = "done" if done else "today" if date == today else "late" if date < today else "upcoming"
        if date == today:
            today_n = n
        days.append({"n": n, "date": date, "title": d["title"], "items": items, "done": done, "state": state,
                     "count": sum(1 for i in items if i["done"])})
    total = sum(len(d["items"]) for d in days)
    finished = sum(d["count"] for d in days)
    if today_n is None and days:
        today_n = 0 if today < days[0]["date"] else len(days) + 1 if today > days[-1]["date"] else None
    return {"days": days, "total": total, "finished": finished, "today": today_n,
            "end": days[-1]["date"] if days else start, "complete": total > 0 and finished == total}


def month_grid(year, month):
    """Weeks (Monday first) of a month, as lists of dates (None outside the month)."""
    first = dt.date(year, month, 1)
    nxt = dt.date(year + (month == 12), month % 12 + 1, 1)
    weeks, week = [], [None] * first.weekday()
    day = first
    while day < nxt:
        week.append(day)
        if len(week) == 7:
            weeks.append(week)
            week = []
        day += dt.timedelta(days=1)
    if week:
        weeks.append(week + [None] * (7 - len(week)))
    return weeks
