"""Workers, managers, completions and the anonymous survey. Stored in
data/training.db, which never goes to git: it holds employee records and the repo
is public. Completions are never deleted (they are the training record)."""
import datetime as dt
import hashlib
import os
import secrets
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
CREATE TABLE IF NOT EXISTS managers (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  pin_hash TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1
);
-- Anonymous on purpose: no worker, no time of day, and a random id so the order
-- of answers can't be lined up with the order of completions.
CREATE TABLE IF NOT EXISTS survey_answers (
  id INTEGER PRIMARY KEY,
  lesson_id TEXT NOT NULL,
  month TEXT NOT NULL,
  answers TEXT NOT NULL,
  next_topic TEXT NOT NULL DEFAULT ''
);
-- One unused ticket per passed lesson, so each completion can answer once.
CREATE TABLE IF NOT EXISTS survey_tickets (
  ticket_hash TEXT PRIMARY KEY,
  lesson_id TEXT NOT NULL
);
"""
ADDED_COLUMNS = [("workers", "pin_hash", "TEXT"), ("workers", "lang", "TEXT"),
                 ("survey_answers", "comments", "TEXT NOT NULL DEFAULT ''"), ("survey_answers", "form", "TEXT NOT NULL DEFAULT ''"),
                 ("managers", "email", "TEXT"),
                 # Self sign-ups wait for a manager (approved = 0); everyone added before this is approved.
                 ("workers", "approved", "INTEGER NOT NULL DEFAULT 1"), ("workers", "requested_on", "TEXT"),
                 # What the lesson was called and cited when it was taken, so the record stands
                 # even after the lesson is renamed or deleted. Records are kept forever.
                 ("completions", "lesson_title", "TEXT"), ("completions", "lesson_citations", "TEXT"),
                 ("completions", "lesson_topic", "TEXT")]


def db_path():
    return Path(os.environ.get("TRAINING_DB", ROOT / "data" / "training.db"))


def connect():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)  # creates anything missing, so a fresh or damaged setup heals itself
    for table, column, kind in ADDED_COLUMNS:  # columns added after the first release
        if column not in {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    return con


def workers(con, active_only=True):
    q = "SELECT * FROM workers WHERE approved = 1" + (" AND active = 1" if active_only else "") + " ORDER BY name"
    return [dict(r) for r in con.execute(q)]


def worker(con, wid):
    r = con.execute("SELECT * FROM workers WHERE id = ?", (wid,)).fetchone()
    return dict(r) if r else None


def add_worker(con, name, role, pin_hash=None):
    cur = con.execute("INSERT INTO workers (name, role, pin_hash) VALUES (?, ?, ?)", (name.strip(), role, pin_hash))
    con.commit()
    return cur.lastrowid


MAX_PENDING = 50   # a cap on waiting sign-ups, so a flood of them can't fill the list


def request_account(con, name, role, pin_hash):
    """A worker signs themselves up; they can't sign in until a manager approves."""
    if len(pending_workers(con)) >= MAX_PENDING:
        return None
    cur = con.execute("INSERT INTO workers (name, role, pin_hash, approved, requested_on) VALUES (?, ?, ?, 0, ?)",
                      (name.strip(), role, pin_hash, dt.datetime.now().isoformat(timespec="minutes")))
    con.commit()
    return cur.lastrowid


def pending_workers(con):
    return [dict(r) for r in con.execute("SELECT * FROM workers WHERE approved = 0 ORDER BY requested_on, id")]


def approve_worker(con, wid, role):
    con.execute("UPDATE workers SET approved = 1, active = 1, role = ? WHERE id = ? AND approved = 0", (role, wid))
    con.commit()


def decline_worker(con, wid):
    """Only a sign-up still waiting can be declined, so no training record is ever lost."""
    con.execute("DELETE FROM workers WHERE id = ? AND approved = 0", (wid,))
    con.commit()


def set_worker_pin(con, wid, pin_hash):
    con.execute("UPDATE workers SET pin_hash = ? WHERE id = ?", (pin_hash, wid))
    con.commit()


def set_worker_lang(con, wid, lang):
    con.execute("UPDATE workers SET lang = ? WHERE id = ?", (lang, wid))
    con.commit()


def set_worker_role(con, wid, role):
    con.execute("UPDATE workers SET role = ? WHERE id = ?", (role, wid))
    con.commit()


def managers(con):
    return [dict(r) for r in con.execute("SELECT * FROM managers WHERE active = 1 ORDER BY name")]


def manager(con, mid):
    r = con.execute("SELECT * FROM managers WHERE id = ?", (mid,)).fetchone()
    return dict(r) if r else None


def add_manager(con, name, pin_hash, email=None):
    cur = con.execute("INSERT INTO managers (name, pin_hash, email) VALUES (?, ?, ?)",
                      (name.strip(), pin_hash, email.strip().lower() if email else None))
    con.commit()
    return cur.lastrowid


def manager_by_email(con, email):
    r = con.execute("SELECT * FROM managers WHERE active = 1 AND lower(email) = ?", (email.strip().lower(),)).fetchone()
    return dict(r) if r else None


def set_manager_email(con, mid, email):
    con.execute("UPDATE managers SET email = ? WHERE id = ?", (email.strip().lower(), mid))
    con.commit()


def set_manager_pin(con, mid, pin_hash):
    con.execute("UPDATE managers SET pin_hash = ? WHERE id = ?", (pin_hash, mid))
    con.commit()


def set_worker_active(con, wid, active):
    con.execute("UPDATE workers SET active = ? WHERE id = ?", (1 if active else 0, wid))
    con.commit()


def record(con, wid, lesson, score):
    """Saves an attempt; returns (passed, completion id)."""
    passed = score >= PASS_MARK
    cur = con.execute(
        "INSERT INTO completions (worker_id, lesson_id, score, passed, lesson_version, completed_at, lesson_title, lesson_citations,"
        " lesson_topic) VALUES (?,?,?,?,?,?,?,?,?)",
        (wid, lesson["id"], score, int(passed), lesson.get("version"), dt.datetime.now().isoformat(timespec="seconds"),
         lesson.get("title"), ", ".join(lesson.get("citations") or []), lesson.get("topic_name")),
    )
    con.commit()
    return passed, cur.lastrowid


def completion(con, cid):
    r = con.execute("SELECT c.*, w.name, w.role FROM completions c JOIN workers w ON w.id = c.worker_id WHERE c.id = ?",
                    (cid,)).fetchone()
    return dict(r) if r else None


def attempts(con, wid=None):
    """Every attempt, newest first (passed and not), with the worker's name."""
    q = "SELECT c.*, w.name, w.role FROM completions c JOIN workers w ON w.id = c.worker_id"
    rows = con.execute(q + (" WHERE c.worker_id = ?" if wid else "") + " ORDER BY c.completed_at DESC, c.id DESC",
                       (wid,) if wid else ())
    return [dict(r) for r in rows]


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


# ---- Anonymous survey ---------------------------------------------------------

def _ticket_hash(ticket):
    return hashlib.sha256(ticket.encode()).hexdigest()


def survey_ticket(con, lesson_id):
    ticket = secrets.token_urlsafe(18)
    con.execute("INSERT INTO survey_tickets (ticket_hash, lesson_id) VALUES (?, ?)", (_ticket_hash(ticket), lesson_id))
    con.commit()
    return ticket


def save_survey(con, ticket, answers, next_topic, comments="", form=""):
    """Uses up the ticket and stores the answers with no link to who gave them.
    Returns False if the ticket is unknown or already used."""
    row = con.execute("SELECT lesson_id FROM survey_tickets WHERE ticket_hash = ?", (_ticket_hash(ticket or ""),)).fetchone()
    if not row:
        return False
    con.execute("DELETE FROM survey_tickets WHERE ticket_hash = ?", (_ticket_hash(ticket),))
    con.execute("INSERT INTO survey_answers (id, lesson_id, month, answers, next_topic, comments, form) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (secrets.randbits(62), row["lesson_id"], dt.date.today().strftime("%Y-%m"),
                 ",".join(str(a) for a in answers), next_topic.strip()[:300], comments.strip()[:1000], form))
    con.commit()
    return True


def survey_rows(con, lesson_id=None, form=None):
    """Answers for one version of the form (and one lesson, if given)."""
    q, args = "SELECT lesson_id, month, answers, next_topic, comments FROM survey_answers WHERE 1=1", []
    if form is not None:
        q, args = q + " AND form = ?", args + [form]
    if lesson_id:
        q, args = q + " AND lesson_id = ?", args + [lesson_id]
    rows = con.execute(q + " ORDER BY month DESC, id", args)
    return [{**dict(r), "answers": [int(a) for a in r["answers"].split(",") if a]} for r in rows]
