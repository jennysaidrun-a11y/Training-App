"""The training app: worker pages, the lesson player and the manager pages."""
import datetime as dt
import os
import re
import secrets
import shutil
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import auth, content, db, editor, extract, i18n, research, rules

HERE = content.ROOT / "trainer"
templates = Jinja2Templates(directory=HERE / "templates")


def _asset_version():
    """Changes whenever a static file changes, so browsers fetch the new copy
    instead of an old cached one (?v=... on every /static link)."""
    import hashlib
    h = hashlib.sha1()
    for path in sorted((HERE / "static").rglob("*")):
        if path.is_file():
            h.update(path.read_bytes())
    return h.hexdigest()[:10]


templates.env.globals["asset_v"] = _asset_version()

# Uploaded videos live next to the database (never in git: the repo is public
# and videos are big). Served with range requests so the player can seek.
VIDEO_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime"}
MAX_VIDEO_BYTES = 2 * 1024**3
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}
MAX_IMAGE_BYTES = 15 * 1024**2
MEDIA_TYPES = {**VIDEO_TYPES, **IMAGE_TYPES}
MEDIA_NAME = re.compile(r"^[a-z0-9-]+\.(mp4|m4v|webm|mov|jpg|jpeg|png|webp|gif)$")


def media_dir():
    path = db.db_path().parent / "media"
    path.mkdir(parents=True, exist_ok=True)
    return path


STATE_LABELS = {
    "due": "To do",
    "updated": "Updated: retake",
    "refresh": "Yearly refresher",
    "done": "Done",
}


def render(request, name, **ctx):
    ctx.setdefault("role_names", content.role_names())
    ctx.setdefault("who", auth.current(request))
    lang = ctx.setdefault("lang", i18n.pick(request, ctx["who"]))
    ctx.update(langs=i18n.LANGS, rtl=lang in i18n.RTL, t=lambda key, **kw: i18n.t(key, lang, **kw))
    return templates.TemplateResponse(request, name, ctx)


# ---- Keeping rules current in the background --------------------------------

CHECK_EVERY_HOURS = 24
_check_lock = threading.Lock()


def _check_is_stale():
    checked = rules.load_status().get("checked_at")
    if not checked:
        return True
    age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(checked)
    return age > dt.timedelta(hours=CHECK_EVERY_HOURS)


def check_rules_now():
    if not _check_lock.acquire(blocking=False):
        return False
    try:
        rules.run_check()
        return True
    except Exception as e:  # the app keeps running; the next loop tries again
        print(f"Rules check failed: {e}")
        return False
    finally:
        _check_lock.release()


def _rules_loop():
    while True:
        if _check_is_stale():
            check_rules_now()
        time.sleep(3600)


@asynccontextmanager
async def lifespan(_app):
    if os.environ.get("TRAINING_NO_RULES_LOOP") != "1":
        threading.Thread(target=_rules_loop, daemon=True).start()
    yield


app = FastAPI(title="United Bakery Training", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


OPEN_MANAGER_PATHS = ("/manage/signin", "/manage/setup")


@app.middleware("http")
async def managers_only(request: Request, call_next):
    """Everything under /manage and /api/manage needs a signed-in manager."""
    path = request.url.path
    if (path.startswith("/manage") or path.startswith("/api/manage")) and not path.startswith(OPEN_MANAGER_PATHS):
        who = auth.current(request)
        if not who or who["kind"] != "m":
            if path.startswith("/api/"):
                return JSONResponse({"error": "Sign in as a manager first."}, status_code=401)
            return RedirectResponse("/manage/signin?next=" + path, status_code=303)
    return await call_next(request)


@app.middleware("http")
async def fresh_static_files(request: Request, call_next):
    """Browsers check for a newer copy of styles and scripts on every load, so an
    update never shows new pages with old styles."""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# ---- Worker side ------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    with db.connect() as con:
        people = db.workers(con)
    return render(request, "home.html", workers=people)


def _my_lessons(con, person, lessons):
    """A person's lessons in folder order, each tagged with its folder."""
    rows = []
    for topic, members in content.group_by_topic(content.lessons_for_role(lessons, person["role"])):
        for lesson in members:
            state, last = db.lesson_state(con, person["id"], lesson)
            rows.append({"lesson": lesson, "state": state, "label": STATE_LABELS[state], "last": last, "topic": topic})
    return rows


def _safe_next(url, default):
    return url if url and url.startswith("/") and not url.startswith("//") and "\\" not in url else default


@app.get("/signin/{wid}", response_class=HTMLResponse)
def signin_page(request: Request, wid: int, wrong: int = 0):
    with db.connect() as con:
        person = db.worker(con, wid)
    if not person or not person["active"]:
        raise HTTPException(404, "No one with that id")
    return render(request, "signin.html", person=person, wrong=wrong, locked=auth.locked_for("w", wid),
                  has_pin=bool(person.get("pin_hash")))


@app.post("/signin/{wid}")
def signin(request: Request, wid: int, pin: str = Form("")):
    with db.connect() as con:
        person = db.worker(con, wid)
    if not person or not person["active"]:
        raise HTTPException(404, "No one with that id")
    if not auth.check_pin("w", wid, pin.strip(), person.get("pin_hash")):
        return RedirectResponse(f"/signin/{wid}?wrong=1", status_code=303)
    response = RedirectResponse(f"/me/{wid}", status_code=303)
    auth.set_session(response, request, "w", wid)
    return response


@app.post("/lang")
def set_language(request: Request, lang: str = Form("en"), back: str = Form("/")):
    lang = lang if lang in i18n.CODES else "en"
    who = auth.current(request)
    if who and who["kind"] == "w":
        with db.connect() as con:
            db.set_worker_lang(con, who["id"], lang)
    response = RedirectResponse(_safe_next(back, "/"), status_code=303)
    response.set_cookie(i18n.COOKIE, lang, max_age=365 * 86400, samesite="lax", path="/")
    return response


@app.post("/signout")
def signout():
    response = RedirectResponse("/", status_code=303)
    auth.clear_session(response)
    return response


def _can_see(who, wid):
    return bool(who) and (who["kind"] == "m" or who["id"] == wid)


@app.get("/me/{wid}", response_class=HTMLResponse)
def my_page(request: Request, wid: int):
    who = auth.current(request)
    if not _can_see(who, wid):
        return RedirectResponse(f"/signin/{wid}", status_code=303)
    with db.connect() as con:
        person = db.worker(con, wid)
        if not person:
            raise HTTPException(404, "No one with that id")
        lessons = content.load_lessons()
        rows = _my_lessons(con, person, lessons)
        history = db.attempts(con, wid)
    return render(request, "me.html", person=person, rows=rows, history=history, who=who,
                  titles={lid: l["title"] for lid, l in lessons.items()})


def _get_lesson(lesson_id):
    lesson = content.load_lessons().get(lesson_id)
    if not lesson:
        raise HTTPException(404, "No such lesson")
    return lesson


content_hash = content.content_hash


def localize(lesson, lang):
    """The lesson in one language: its translation laid over the English, piece by
    piece, so anything not translated yet still shows (in English)."""
    if lang == "en":
        return lesson, True
    if content.translation_state(lesson, lang) != "current":
        return lesson, False
    tr = lesson["translations"][lang]
    out = dict(lesson)
    for k in ("title", "summary"):
        out[k] = tr.get(k) or lesson[k]
    tsec = tr.get("sections") or []
    out["sections"] = [{**s, **{k: v for k, v in (tsec[i] if i < len(tsec) else {}).items() if v and k in ("heading", "text")}}
                       for i, s in enumerate(lesson["sections"])]
    out["questions"] = []
    for i, q in enumerate(lesson["questions"]):
        tq = (tr.get("questions") or [])[i] if i < len(tr.get("questions") or []) else {}
        choices = tq.get("choices") if tq.get("choices") and len(tq["choices"]) == len(q["choices"]) else q["choices"]
        out["questions"].append({**q, "q": tq.get("q") or q["q"], "choices": choices, "why": tq.get("why") or q.get("why", "")})
    return out, True


def _public_lesson(lesson):
    """What the player needs, without the answers."""
    return {
        "id": lesson["id"],
        "title": lesson["title"],
        "video": lesson.get("video") or "",
        "sections": lesson["sections"],
        "questions": [
            {k: q.get(k) for k in ("q", "choices", "at", "after_section")} for q in lesson["questions"]
        ],
    }


def _survey_for(lang):
    sv = content.load_survey()
    return {"form": sv.get("form", ""), "title": i18n.localized(sv["title"], lang), "intro": i18n.localized(sv["intro"], lang),
            "scale": [i18n.localized(x, lang) for x in sv["scale"]], "ratings": [i18n.localized(x, lang) for x in sv["ratings"]],
            "next_topic": i18n.localized(sv["next_topic"], lang), "comments": i18n.localized(sv["comments"], lang)}


@app.get("/lesson/{lesson_id}", response_class=HTMLResponse)
def lesson_page(request: Request, lesson_id: str):
    """Results are saved only for the signed-in worker; anyone else sees a preview."""
    who = auth.current(request)
    lang = request.query_params.get("lang")
    lang = lang if lang in i18n.CODES else i18n.pick(request, who)
    lesson, translated = localize(_get_lesson(lesson_id), lang)
    person = who if who and who["kind"] == "w" else None
    status = rules.load_status()
    return render(request, "lesson.html", lesson=lesson, person=person, data=_public_lesson(lesson),
                  rule_status=status.get("rules", {}), survey=_survey_for(lang), who=who, lang=lang,
                  translated=translated, strings=i18n.table(lang))


@app.post("/api/lesson/{lesson_id}/check")
async def check_answer(lesson_id: str, request: Request):
    """Feedback for one answer while the lesson plays."""
    body = await request.json()
    lesson, _ = localize(_get_lesson(lesson_id), i18n.pick(request, auth.current(request)))
    try:
        q = lesson["questions"][int(body["question"])]
    except (KeyError, IndexError, ValueError):
        raise HTTPException(400, "Unknown question")
    right = int(body.get("choice", -1)) == q["answer"]
    return {"correct": right, "why": q.get("why", "")}


@app.post("/api/lesson/{lesson_id}/finish")
async def finish(lesson_id: str, request: Request):
    """Grades first-try answers ({question index: choice}) and records the result."""
    body = await request.json()
    lesson = _get_lesson(lesson_id)
    answers = {int(k): int(v) for k, v in (body.get("answers") or {}).items()}
    total = len(lesson["questions"])
    right = sum(1 for i, q in enumerate(lesson["questions"]) if answers.get(i) == q["answer"])
    score = right / total if total else 1.0
    passed = score >= db.PASS_MARK
    out = {"score": round(score * 100), "passed": passed, "right": right, "total": total,
           "pass_mark": round(db.PASS_MARK * 100), "saved": False}
    who = auth.current(request)
    if who and who["kind"] == "w":   # only the signed-in worker's own result is saved
        with db.connect() as con:
            passed, cid = db.record(con, who["id"], lesson, score)
            out["saved"] = True
            if passed:
                out["certificate"] = f"/certificate/{cid}"
                out["survey_ticket"] = db.survey_ticket(con, lesson["id"])
    return out


@app.post("/api/survey")
async def survey(request: Request):
    """The anonymous survey after a passed lesson. The ticket proves a completion
    without saying whose; it works once."""
    body = await request.json()
    sv = content.load_survey()
    try:
        answers = [int(a) for a in body.get("answers") or []]
    except (TypeError, ValueError):
        answers = []
    if len(answers) != len(sv["ratings"]) or not all(0 <= a < len(sv["scale"]) for a in answers):
        raise HTTPException(400, "Pick an answer for each question.")
    with db.connect() as con:
        if not db.save_survey(con, str(body.get("ticket") or ""), answers, str(body.get("next_topic") or ""),
                              str(body.get("comments") or ""), sv.get("form", "")):
            raise HTTPException(400, "This survey was already sent.")
    return {"ok": True}


@app.get("/certificate/{cid}", response_class=HTMLResponse)
def certificate(request: Request, cid: int):
    who = auth.current(request)
    with db.connect() as con:
        c = db.completion(con, cid)
    if not c or not c["passed"]:
        raise HTTPException(404, "No such certificate")
    if not _can_see(who, c["worker_id"]):
        return RedirectResponse(f"/signin/{c['worker_id']}", status_code=303)
    lesson = content.load_lessons().get(c["lesson_id"]) or {"title": c["lesson_id"], "citations_parsed": []}
    return render(request, "certificate.html", c=c, lesson=lesson, number=_certificate_number(c), provider=PROVIDER)


PROVIDER = "United Bakery"


def _certificate_number(c):
    return f"UB-{c['id']:05d}-{auth._sign('cert:' + str(c['id']))[:6].upper()}"


# ---- Manager side -----------------------------------------------------------

@app.get("/manage", response_class=HTMLResponse)
def manage(request: Request):
    lessons = content.load_lessons()
    status = rules.load_status()
    flagged = {lid: rules.lesson_flags(l, status) for lid, l in lessons.items()}
    with db.connect() as con:
        people = db.workers(con)
        progress = []
        for p in people:
            rows = _my_lessons(con, p, lessons)
            todo = [r for r in rows if r["state"] != "done"]
            progress.append({"person": p, "done": len(rows) - len(todo), "total": len(rows), "todo": todo})
        inactive = [w for w in db.workers(con, active_only=False) if not w["active"]]
        managers = db.managers(con)
    return render(request, "manage.html", lessons=lessons, flagged=flagged, progress=progress, inactive=inactive,
                  roles=content.load_roles(), status=status, broken=content.broken_lesson_files(), managers=managers,
                  groups=content.group_by_topic(list(lessons.values())))


@app.post("/manage/workers")
def add_worker(name: str = Form(...), role: str = Form(...), pin: str = Form("")):
    pin = pin.strip()
    if pin and not auth.WORKER_PIN.match(pin):
        return RedirectResponse("/manage?pin_bad=1#people", status_code=303)
    if name.strip():
        with db.connect() as con:
            db.add_worker(con, name, role, auth.hash_pin(pin) if pin else None)
    return RedirectResponse("/manage#people", status_code=303)


@app.post("/manage/workers/{wid}/pin")
def worker_pin(wid: int, pin: str = Form("")):
    if not auth.WORKER_PIN.match(pin.strip()):
        return RedirectResponse("/manage?pin_bad=1#people", status_code=303)
    with db.connect() as con:
        db.set_worker_pin(con, wid, auth.hash_pin(pin.strip()))
    auth._fails.pop(("w", wid), None)
    return RedirectResponse("/manage?pin_set=1#people", status_code=303)


@app.post("/manage/workers/{wid}/active")
def worker_active(wid: int, active: int = Form(...)):
    with db.connect() as con:
        db.set_worker_active(con, wid, bool(active))
    return RedirectResponse("/manage#people", status_code=303)


# ---- Dashboard and requirements -------------------------------------------------

MIN_SURVEY_ANSWERS = 3   # fewer and a manager could guess who answered


def _required(lesson, role):
    return "all" in lesson["roles"] or role in lesson["roles"]


def _cell(con, person, lesson, last_try):
    """One person x one lesson: state, the date and the certificate if passed."""
    required = _required(lesson, person["role"])
    state, p = db.lesson_state(con, person["id"], lesson)
    if state == "due" and last_try:
        state = "failed"
    if not required and state in ("due", "failed"):
        state = "na"
    return {"state": state, "required": required, "pass": p, "last_try": last_try,
            "label": {"done": "Done", "updated": "Retake (updated)", "refresh": "Yearly refresher", "due": "Not started",
                      "failed": "Failed, retake", "na": "Not required"}[state]}


def _survey_summary(con, lesson_id, lessons):
    """Per statement: how many disagreed / were neutral / agreed, and the average
    score out of the form's total (0-1-2 per statement, like the paper form)."""
    survey = content.load_survey()
    rows = db.survey_rows(con, lesson_id or None, survey.get("form", ""))
    n = len(rows)
    top = len(survey["scale"]) - 1
    out = {"n": n, "enough": n >= MIN_SURVEY_ANSWERS, "min": MIN_SURVEY_ANSWERS, "bars": [], "topics": [], "comments": [],
           "scale": [i18n.localized(x, "en") for x in survey["scale"]], "next_topic": i18n.localized(survey["next_topic"], "en"),
           "max_score": top * len(survey["ratings"]), "form": survey.get("form", "")}
    if not out["enough"]:
        return out
    for i, text in enumerate(survey["ratings"]):
        vals = [r["answers"][i] for r in rows if len(r["answers"]) > i]
        counts = [sum(1 for v in vals if v == k) for k in range(top + 1)]
        out["bars"].append({"text": i18n.localized(text, "en"), "counts": counts,
                            "pcts": [round(100 * c / len(vals)) if vals else 0 for c in counts],
                            "avg": round(sum(vals) / len(vals), 1) if vals else 0})
    totals = [sum(r["answers"]) for r in rows]
    out["avg_score"] = round(sum(totals) / n, 1)
    title = lambda lid: lessons.get(lid, {}).get("title", lid)
    out["topics"] = [{"text": r["next_topic"], "lesson": title(r["lesson_id"]), "month": r["month"]} for r in rows if r["next_topic"]][:60]
    out["comments"] = [{"text": r["comments"], "lesson": title(r["lesson_id"]), "month": r["month"]} for r in rows if r["comments"]][:60]
    return out


@app.get("/manage/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, role: str = "", lesson: str = ""):
    lessons = content.load_lessons()
    roles = content.load_roles()
    role = role if role in content.role_names() else ""
    focus = lessons.get(lesson)
    columns = [l for _, members in content.group_by_topic([l for l in lessons.values() if not role or _required(l, role)])
               for l in members]
    with db.connect() as con:
        people = [w for w in db.workers(con) if not role or w["role"] == role]
        latest = {}
        for a in db.attempts(con):          # newest first, so the first seen is the latest try
            latest.setdefault((a["worker_id"], a["lesson_id"]), a)
        matrix, totals = [], {"required": 0, "done": 0, "behind": 0}
        for p in people:
            cells = [_cell(con, p, l, latest.get((p["id"], l["id"]))) for l in columns]
            for c in cells:
                if c["required"]:
                    totals["required"] += 1
                    totals["done" if c["state"] == "done" else "behind"] += 1
            matrix.append({"person": p, "cells": cells,
                           "done": sum(1 for c in cells if c["required"] and c["state"] == "done"),
                           "required": sum(1 for c in cells if c["required"])})
        who_did = None
        if focus:
            who_did = sorted(({"person": p, **_cell(con, p, focus, latest.get((p["id"], focus["id"])))} for p in people),
                             key=lambda r: ({"done": 0, "updated": 1, "refresh": 1, "failed": 2, "due": 3, "na": 4}[r["state"]], r["person"]["name"]))
        survey = _survey_summary(con, focus["id"] if focus else "", lessons)
    totals["pct"] = round(100 * totals["done"] / totals["required"]) if totals["required"] else 100
    return render(request, "dashboard.html", roles=roles, role=role, lessons=sorted(lessons.values(), key=lambda l: l["title"]),
                  focus=focus, columns=columns, matrix=matrix, totals=totals, who_did=who_did, survey=survey)


@app.get("/manage/requirements", response_class=HTMLResponse)
def requirements_page(request: Request, saved: int = 0):
    lessons = sorted(content.load_lessons().values(), key=lambda l: l["title"])
    return render(request, "requirements.html", lessons=lessons, roles=content.load_roles(), saved=saved,
                  required=_required)


@app.post("/manage/requirements")
async def save_requirements(request: Request):
    """Which lessons each position must take (saved as each lesson's roles)."""
    form = await request.form()
    picked = {}
    for value in form.getlist("req"):
        lid, _, rid = str(value).partition("|")
        picked.setdefault(lid, set()).add(rid)
    role_ids = [r["id"] for r in content.load_roles()]
    for lesson in content.load_lessons().values():
        chosen = [r for r in role_ids if r in picked.get(lesson["id"], set())]
        roles = ["all"] if len(chosen) == len(role_ids) else chosen
        if roles != lesson["roles"]:
            lesson["roles"] = roles
            content.save_lesson(lesson)
    return RedirectResponse("/manage/requirements?saved=1", status_code=303)


@app.get("/manage/topics", response_class=HTMLResponse)
def topics_page(request: Request):
    lessons = list(content.load_lessons().values())
    topics = content.load_topics()
    return render(request, "topics.html", groups=content.group_by_topic(lessons, topics), topics=topics, lang="en")


@app.post("/manage/topics")
async def edit_topics(request: Request):
    """Folder changes: add, rename, delete, move up/down, and put a lesson in a folder
    or move it up/down inside one."""
    form = await request.form()
    action, tid, lid = form.get("action"), str(form.get("topic") or ""), str(form.get("lesson") or "")
    topics = content.load_topics()
    idx = next((i for i, t in enumerate(topics) if t["id"] == tid), None)
    if action == "add":
        name = str(form.get("name") or "").strip()[:80]
        if name:
            base, new_id, n = content.slugify(name) or "topic", None, 2
            new_id = base
            while any(t["id"] == new_id for t in topics):
                new_id, n = f"{base}-{n}", n + 1
            topics.append({"id": new_id, "name": name, "lessons": []})
    elif action == "rename" and idx is not None:
        name = str(form.get("name") or "").strip()[:80]
        if name:
            topics[idx]["name"] = name
    elif action == "delete" and idx is not None:
        topics.pop(idx)                 # its lessons fall back to "Other lessons"
    elif action in ("up", "down") and idx is not None:
        j = idx - 1 if action == "up" else idx + 1
        if 0 <= j < len(topics):
            topics[idx], topics[j] = topics[j], topics[idx]
    elif action == "put" and lid in content.load_lessons():
        for t in topics:
            if lid in t["lessons"]:
                t["lessons"].remove(lid)
        if idx is not None:
            topics[idx]["lessons"].append(lid)
    elif action in ("lesson-up", "lesson-down") and idx is not None and lid in topics[idx]["lessons"]:
        ls = topics[idx]["lessons"]
        i = ls.index(lid)
        j = i - 1 if action == "lesson-up" else i + 1
        if 0 <= j < len(ls):
            ls[i], ls[j] = ls[j], ls[i]
    content.save_topics(topics)
    return RedirectResponse("/manage/topics" + (f"#t-{tid}" if tid else ""), status_code=303)


@app.get("/manage/translations", response_class=HTMLResponse)
def translations_page(request: Request):
    lessons = sorted(content.load_lessons().values(), key=lambda l: l["title"])
    grid = [{"lesson": l, "states": {code: content.translation_state(l, code) for code in i18n.CODES if code != "en"}} for l in lessons]
    return render(request, "translations.html", grid=grid, langs=[x for x in i18n.LANGS if x[0] != "en"],
                  research_ready=research.available(), lang="en")


@app.post("/api/manage/lesson/{lesson_id}/translate")
async def translate_lesson(request: Request, lesson_id: str):
    """Has Claude translate one lesson into one language and saves it with the
    fingerprint of the English it came from."""
    lang = (await request.json()).get("lang")
    if lang not in i18n.CODES or lang == "en":
        raise HTTPException(400, "Unknown language")
    if not research.available():
        return JSONResponse({"error": "setup"}, status_code=503)
    lesson = _get_lesson(lesson_id)
    try:
        words = await run_in_threadpool(research.translate, lesson, lang)
    except research.NotSignedIn:
        return JSONResponse({"error": "signin"}, status_code=503)
    except Exception as e:
        print(f"Translation failed: {e.__class__.__name__}: {e}")
        return JSONResponse({"error": f"Claude couldn't finish that ({e.__class__.__name__}). Try again."}, status_code=502)
    lesson = dict(_get_lesson(lesson_id))    # re-read: the lesson may have been saved meanwhile
    if content.content_hash(lesson) != content.content_hash(_get_lesson(lesson_id)):
        return JSONResponse({"error": "The lesson changed while translating. Try again."}, status_code=409)
    lesson["translations"] = {**(lesson.get("translations") or {}),
                              lang: {"source": content.content_hash(lesson), "made_on": dt.date.today().isoformat(), "by": "Claude", **words}}
    content.save_lesson(lesson)
    return {"ok": True, "state": "current"}


# ---- Manager sign-in ----------------------------------------------------------

@app.get("/manage/setup", response_class=HTMLResponse)
def setup_page(request: Request, bad: str = ""):
    """First run only: create the first manager."""
    with db.connect() as con:
        if db.managers(con):
            return RedirectResponse("/manage/signin", status_code=303)
    return render(request, "manager_signin.html", setup=True, bad=bad, managers=[])


@app.post("/manage/setup")
def setup(request: Request, name: str = Form(""), pin: str = Form(""), pin2: str = Form("")):
    with db.connect() as con:
        if db.managers(con):
            return RedirectResponse("/manage/signin", status_code=303)
        if not name.strip():
            return RedirectResponse("/manage/setup?bad=name", status_code=303)
        if not auth.MANAGER_PIN.match(pin.strip()) or pin.strip() != pin2.strip():
            return RedirectResponse("/manage/setup?bad=pin", status_code=303)
        mid = db.add_manager(con, name, auth.hash_pin(pin.strip()))
    response = RedirectResponse("/manage/dashboard", status_code=303)
    auth.set_session(response, request, "m", mid)
    return response


@app.get("/manage/signin", response_class=HTMLResponse)
def manager_signin_page(request: Request, next: str = "", wrong: int = 0):
    with db.connect() as con:
        people = db.managers(con)
    if not people:
        return RedirectResponse("/manage/setup", status_code=303)
    return render(request, "manager_signin.html", setup=False, managers=people, wrong=wrong, next=next)


@app.post("/manage/signin")
def manager_signin(request: Request, manager: int = Form(0), pin: str = Form(""), next: str = Form("")):
    with db.connect() as con:
        m = db.manager(con, manager)
    if not m or not m["active"] or not auth.check_pin("m", manager, pin.strip(), m["pin_hash"]):
        return RedirectResponse(f"/manage/signin?wrong=1&next={next if _safe_next(next, '') else ''}", status_code=303)
    response = RedirectResponse(_safe_next(next, "/manage/dashboard"), status_code=303)
    auth.set_session(response, request, "m", manager)
    return response


@app.post("/manage/managers")
def add_manager(name: str = Form(""), pin: str = Form("")):
    if not name.strip() or not auth.MANAGER_PIN.match(pin.strip()):
        return RedirectResponse("/manage?manager_bad=1#managers", status_code=303)
    with db.connect() as con:
        db.add_manager(con, name, auth.hash_pin(pin.strip()))
    return RedirectResponse("/manage?manager_added=1#managers", status_code=303)


@app.post("/manage/check")
def manage_check():
    threading.Thread(target=check_rules_now, daemon=True).start()
    return RedirectResponse("/manage?checking=1#rules", status_code=303)


@app.get("/rules", response_class=HTMLResponse)
def rules_page(request: Request):
    lessons = content.load_lessons()
    status = rules.load_status()
    used_by = {}
    for l in lessons.values():
        for c in l["citations_parsed"]:
            used_by.setdefault(c["ref"], []).append(l)
    return render(request, "rules.html", status=status, used_by=used_by)


@app.post("/manage/lesson/{lesson_id}/reviewed")
def mark_reviewed(lesson_id: str):
    lesson = _get_lesson(lesson_id)
    lesson["reviewed_on"] = content.now_stamp()
    content.save_lesson(lesson)
    return RedirectResponse("/manage#lessons", status_code=303)


def _editor(request, lesson, is_new=False):
    status = rules.load_status().get("rules", {})
    return render(request, "edit.html", lesson=lesson, is_new=is_new, roles=content.load_roles(),
                  data=editor.editor_payload(lesson), rule_status=status, research_ready=research.available(),
                  research_mode=research.mode())


@app.get("/manage/lesson/new", response_class=HTMLResponse)
def new_lesson(request: Request):
    return _editor(request, {"id": "", "roles": ["all"]}, is_new=True)


@app.get("/manage/lesson/{lesson_id}", response_class=HTMLResponse)
def edit_lesson(request: Request, lesson_id: str):
    return _editor(request, _get_lesson(lesson_id))


@app.post("/api/manage/lesson/{lesson_id}")
async def save_lesson(request: Request, lesson_id: str):
    """Saves the slide editor's lesson (JSON). 'new' makes a new lesson.
    Errors come back as {"errors": [{"slide": n | 0 | "rules" | None, "error": ...}]}."""
    data = await request.json()
    is_new = lesson_id == "new"
    lessons = content.load_lessons()
    lesson = {"id": ""} if is_new else dict(_get_lesson(lesson_id))

    sections, questions, errors = editor.from_slides(data.get("slides") or [])
    errors = editor.check_meta(data) + errors
    if errors:
        return JSONResponse({"errors": errors}, status_code=400)

    today = dt.date.today().isoformat()
    if is_new:
        base = content.slugify(data["title"])
        new_id, n = base, 2
        while new_id in lessons:
            new_id, n = f"{base}-{n}", n + 1
        lesson = {"id": new_id, "version": today}
    roles = [r for r in data.get("roles") or [] if r == "all" or r in content.role_names()] or ["all"]
    old_video = lesson.get("video") or ""
    video = (data.get("video") or "").strip()
    lesson.update({
        "title": data["title"].strip(),
        "summary": (data.get("summary") or "").strip(),
        "video": video,
        "roles": roles,
        "sections": sections,
        "questions": questions,
        "citations": [content.parse_citation(c)["ref"] for c in data.get("citations") or []],
        "sources": [{"title": (s.get("title") or "").strip() or s["url"].strip(), "url": s["url"].strip()}
                    for s in data.get("sources") or []],
        "reviewed_on": content.now_stamp(),
    })
    if data.get("retake"):
        lesson["version"] = today
    content.save_lesson(lesson)
    saved = content.load_lessons()
    if old_video and old_video != video:
        _remove_unused_upload(old_video, saved)
    _sweep_media(saved)
    return {"ok": True, "id": lesson["id"], "reviewed_on": lesson["reviewed_on"]}


@app.post("/api/manage/video")
async def upload_video(request: Request):
    """Stores a video for the editor and returns its /media/ link. It becomes
    part of a lesson when the lesson is saved."""
    form = await request.form()
    upload = form.get("video_file")
    if not getattr(upload, "filename", None):
        raise HTTPException(400, "Choose a video file.")
    ext = os.path.splitext(upload.filename)[1].lower()
    if ext not in VIDEO_TYPES:
        return JSONResponse({"error": f"'{upload.filename}' isn't a video the app can play. Use an .mp4, .mov, .m4v or .webm file."}, status_code=400)
    if (upload.size or 0) > MAX_VIDEO_BYTES:
        return JSONResponse({"error": "That video is over 2 GB. Please use a shorter or smaller copy."}, status_code=400)
    return {"video": _save_upload(upload, str(form.get("lesson") or "lesson"))}


@app.post("/api/manage/image")
async def upload_image(request: Request):
    """Stores a picture for a reading slide and returns its /media/ link."""
    form = await request.form()
    upload = form.get("image_file")
    if not getattr(upload, "filename", None):
        raise HTTPException(400, "Choose a picture.")
    ext = os.path.splitext(upload.filename)[1].lower()
    if ext not in IMAGE_TYPES:
        hint = " iPhone photos (.heic): share them as JPEG first, or take a screenshot." if ext == ".heic" else ""
        return JSONResponse({"error": f"'{upload.filename}' isn't a picture the app can show. Use a .jpg, .png, .webp or .gif.{hint}"}, status_code=400)
    if (upload.size or 0) > MAX_IMAGE_BYTES:
        return JSONResponse({"error": "That picture is over 15 MB. Please use a smaller copy."}, status_code=400)
    return {"image": _save_upload(upload, str(form.get("lesson") or "lesson"))}


def _verify(citations):
    """Looks each citation up at eCFR / DIR right now."""
    out = []
    for ref in citations[:12]:
        c = content.parse_citation(ref)
        if not c:
            out.append({"ref": ref, "found": False, "error": "Not a citation the app can read."})
            continue
        entry = rules.check_rule(c, {})
        out.append({"ref": c["ref"], "url": c["url"], "found": entry.get("found"),
                    "name": entry.get("name", ""), "error": entry.get("last_error", "")})
    return out


@app.post("/api/manage/verify-citations")
async def verify_citations(request: Request):
    body = await request.json()
    refs = [str(c) for c in body.get("citations") or []]
    return {"checks": await run_in_threadpool(_verify, refs)}


@app.post("/api/manage/attach")
async def attach_file(request: Request):
    """Reads the text out of a training file attached in the Claude panel. The
    file itself isn't kept; its text goes to Claude with the next message, and its
    pictures are saved as uploads Claude can put on slides (swept if unused)."""
    form = await request.form()
    upload = form.get("file")
    if not getattr(upload, "filename", None):
        raise HTTPException(400, "Choose a file.")
    if (upload.size or 0) > extract.MAX_FILE_BYTES:
        return JSONResponse({"error": "That file is over 25 MB. Try a PDF export, or split it."}, status_code=400)
    data = await upload.read()
    try:
        return await run_in_threadpool(extract.extract, upload.filename, data, _save_picture)
    except extract.Unreadable as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/manage/research")
async def research_chat(request: Request):
    """One turn of the editor's Claude panel."""
    if not research.available():
        return JSONResponse({"error": "setup"}, status_code=503)
    body = await request.json()
    history = [{"role": t.get("role"), "text": str(t.get("text") or "")} for t in body.get("history") or []
               if t.get("role") in ("user", "assistant")][-20:]
    if not history or history[-1]["role"] != "user" or not history[-1]["text"].strip():
        raise HTTPException(400, "Say what to research.")
    try:
        result = await run_in_threadpool(research.run, history, body.get("draft"))
    except research.NotSignedIn:
        return JSONResponse({"error": "signin"}, status_code=503)
    except Exception as e:  # shown in the panel; the manager can try again
        print(f"Research failed: {e.__class__.__name__}: {e}")
        return JSONResponse({"error": f"Claude couldn't finish that ({e.__class__.__name__}). Try again in a minute."}, status_code=502)
    if result.get("lesson"):
        result["checks"] = await run_in_threadpool(_verify, result["lesson"]["citations"])
        if await run_in_threadpool(_settle_pictures, result["lesson"]):
            result["reply"] = (result.get("reply") or "") + "\n\n(A picture Claude picked couldn't be checked as free to use, so it's left off.)"
    return result


COMMONS_API = "https://commons.wikimedia.org/w/api.php"
FREE_LICENSE = re.compile(r"^(public domain|pd\b|cc0|cc by(-sa)? \d)", re.I)
PICTURE_MIME = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}
WEB_HEADERS = {"User-Agent": "bakery-training-app (lesson pictures; https://github.com/jennysaidrun-a11y/Training-App)"}


def _commons_picture(page, get=None):
    """A Wikimedia Commons photo Claude picked: the app itself reads its license from
    Commons, keeps it only if it's public domain, CC0, CC BY or CC BY-SA, and saves a
    copy. Returns (/media/ link, source for the credits) or None."""
    import html
    from urllib.parse import unquote

    import requests
    get = get or requests.get
    title = unquote(page.split("/wiki/", 1)[1]).replace("_", " ")
    r = get(COMMONS_API, params={"action": "query", "titles": title, "prop": "imageinfo", "iiprop": "url|mime|extmetadata",
                                 "iiurlwidth": 1000, "format": "json", "formatversion": 2}, headers=WEB_HEADERS, timeout=20)
    info = (r.json()["query"]["pages"][0].get("imageinfo") or [None])[0]
    meta = (info or {}).get("extmetadata", {})
    license_name = meta.get("LicenseShortName", {}).get("value", "").strip()
    if not info or info.get("mime") not in PICTURE_MIME or not FREE_LICENSE.match(license_name) or re.search(r"\b(nc|nd)\b", license_name, re.I):
        return None
    img = get(info.get("thumburl") or info["url"], headers=WEB_HEADERS, timeout=30)
    if img.status_code != 200 or not 0 < len(img.content) <= MAX_IMAGE_BYTES:
        return None
    name = f"commons-{dt.datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}{PICTURE_MIME[info['mime']]}"
    (media_dir() / name).write_bytes(img.content)
    artist = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")))).strip()
    credit = f"Photo: {title.removeprefix('File:').rsplit('.', 1)[0]}" + (f", by {artist[:80]}" if artist else "") + f", {license_name}"
    return f"/media/{name}", {"title": credit, "url": page}


def _settle_pictures(lesson, get=None):
    """Claude's picture links must be real: attached-file pictures that exist, or
    Commons photos with a free license (saved here, credited in sources). Anything
    else is taken off the slide. Returns how many were taken off."""
    dropped = 0
    for s in lesson.get("slides", []):
        link = s.get("image")
        if not link:
            continue
        if link.startswith("/media/"):
            name = link.rsplit("/", 1)[1]
            ok = bool(MEDIA_NAME.match(name)) and (media_dir() / name).is_file()
        else:
            try:
                found = _commons_picture(link, get)
            except Exception as e:  # Commons unreachable or an odd answer
                print(f"Picture check failed for {link}: {e.__class__.__name__}")
                found = None
            ok = bool(found)
            if found:
                s["image"], source = found
                lesson["sources"] = [x for x in lesson.get("sources", []) if x.get("url") != link] + [source]
        if not ok:
            s["image"] = ""
            dropped += 1
    return dropped


@app.get("/media/{name}")
def media(name: str):
    if not MEDIA_NAME.match(name):
        raise HTTPException(404, "No such video")
    path = media_dir() / name
    if not path.is_file():
        raise HTTPException(404, "No such video")
    return FileResponse(path, media_type=MEDIA_TYPES["." + name.rsplit(".", 1)[1]])


def _save_upload(upload, lesson_id):
    ext = os.path.splitext(upload.filename)[1].lower()
    name = f"{content.slugify(lesson_id)}-{dt.datetime.now().strftime('%Y%m%d%H%M%S')}{ext}"
    with open(media_dir() / name, "wb") as out:
        shutil.copyfileobj(upload.file, out, 1024 * 1024)
    return f"/media/{name}"


def _save_picture(ext, data):
    name = f"attached-{dt.datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}{ext}"
    (media_dir() / name).write_bytes(data)
    return f"/media/{name}"


def _remove_unused_upload(video, lessons):
    """Deletes an old uploaded video once no lesson points to it."""
    if not video.startswith("/media/") or any(l.get("video") == video for l in lessons.values()):
        return
    name = video.rsplit("/", 1)[1]
    if MEDIA_NAME.match(name):
        (media_dir() / name).unlink(missing_ok=True)


def _sweep_media(lessons, older_than_hours=24):
    """Deletes uploads nobody saved into a lesson (an editor closed without saving)."""
    used = {l.get("video") for l in lessons.values()}
    used |= {s.get("image") for l in lessons.values() for s in l.get("sections", [])}
    cutoff = time.time() - older_than_hours * 3600
    for path in media_dir().iterdir():
        if MEDIA_NAME.match(path.name) and f"/media/{path.name}" not in used and path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)


@app.get("/health")
def health():
    return JSONResponse({"ok": True})
