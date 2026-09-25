"""Workers and completions. Stored in data/training.db, which never goes to git:
it holds employee records and the repo is public."""
import datetime as dt
import os
import sqlite3
from pathlib import Path

from .content import ROOT

PASS_MARK = 0.8
REFRESH_DAYS = 365

SCHEMA = """
CREATE TABLE IF NOT EXISTS workers (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  role TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS completions (
  id INTEGER PRIMARY KEY,
  worker_id INTEGER NOT NULL REFERENCES workers(id),
  lesson_id TEXT NOT NULL,
  score REAL NOT NULL,
  passed INTEGER NOT NULL,
  lesson_version TEXT,
  completed_at TEXT NOT NULL
);
"""


def db_path():
    return Path(os.environ.get("TRAINING_DB", ROOT / "data" / "training.db"))


def connect():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)  # creates anything missing, so a fresh or damaged setup heals itself
    return con


def workers(con, active_only=True):
    q = "SELECT * FROM workers" + (" WHERE active = 1" if active_only else "") + " ORDER BY name"
    return [dict(r) for r in con.execute(q)]


def worker(con, wid):
    r = con.execute("SELECT * FROM workers WHERE id = ?", (wid,)).fetchone()
    return dict(r) if r else None


def add_worker(con, name, role):
    con.execute("INSERT INTO workers (name, role) VALUES (?, ?)", (name.strip(), role))
    con.commit()


def set_worker_active(con, wid, active):
    con.execute("UPDATE workers SET active = ? WHERE id = ?", (1 if active else 0, wid))
    con.commit()


def record(con, wid, lesson, score):
    passed = score >= PASS_MARK
    con.execute(
        "INSERT INTO completions (worker_id, lesson_id, score, passed, lesson_version, completed_at) VALUES (?,?,?,?,?,?)",
        (wid, lesson["id"], score, int(passed), lesson.get("version"), dt.datetime.now().isoformat(timespec="seconds")),
    )
    con.commit()
    return passed


def last_pass(con, wid, lesson_id):
    r = con.execute(
        "SELECT * FROM completions WHERE worker_id = ? AND lesson_id = ? AND passed = 1 ORDER BY completed_at DESC LIMIT 1",
        (wid, lesson_id),
    ).fetchone()
    return dict(r) if r else None


def lesson_state(con, wid, lesson, today=None):
    """'done', 'due' (never passed), 'updated' (lesson changed since they passed) or 'refresh' (over a year old)."""
    today = today or dt.date.today()
    p = last_pass(con, wid, lesson["id"])
    if not p:
        return "due", None
    if lesson.get("version") and (p["lesson_version"] or "") < lesson["version"]:
        return "updated", p
    done = dt.date.fromisoformat(p["completed_at"][:10])
    if (today - done).days > REFRESH_DAYS:
        return "refresh", p
    return "done", p
