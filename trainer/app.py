"""The training app: worker pages, the lesson player and the manager pages."""
import datetime as dt
import os
import re
import secrets
import shutil
import threading
import time
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
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
    ctx.setdefault("who", auth.manager(request) if request.url.path.startswith("/manage") else auth.current(request))
    ctx.setdefault("is_manager", bool(auth.manager(request)))   # only managers see the Manager tab
    if ctx["is_manager"] and request.url.path.startswith("/manage"):
        with db.connect() as con:
            ctx.setdefault("pending_count", len(db.pending_workers(con)))
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


app = FastAPI(title="United Bakery Training", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


# Where pages may load things from. Inline scripts stay allowed (the templates use
# them); everything else is this app, plus Google Fonts and YouTube for lesson videos.
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' https://www.youtube.com https://s.ytimg.com",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data: blob: https:",
    "media-src 'self' blob: https:",
    "frame-src https://www.youtube.com https://www.youtube-nocookie.com",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'self'",
])


def _same_site(request):
    """False when a form or script on another website is posting here (a forged request)."""
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source:
        return True     # some browsers and tools send neither; the cookies' SameSite rule still applies
    from urllib.parse import urlsplit
    return urlsplit(source).netloc == request.headers.get("host", request.url.netloc)


@app.middleware("http")
async def safety(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS") and not _same_site(request):
        return JSONResponse({"error": "That request came from another website."}, status_code=403)
    response = await call_next(request)
    h = response.headers
    h.setdefault("Content-Security-Policy", CSP)
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "SAMEORIGIN")
    h.setdefault("Referrer-Policy", "same-origin")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        h.setdefault("Strict-Transport-Security", "max-age=31536000")
    if request.url.path.startswith(("/manage", "/api/", "/me/", "/certificate/")):
        h.setdefault("Cache-Control", "no-store")    # records and PINs never sit in a shared browser's cache
    return response


OPEN_MANAGER_PATHS = ("/manage/signin", "/manage/setup", "/manage/recover")


@app.middleware("http")
async def managers_only(request: Request, call_next):
    """Everything under /manage and /api/manage needs a signed-in manager."""
    path = request.url.path
    if (path.startswith("/manage") or path.startswith("/api/manage")) and not path.startswith(OPEN_MANAGER_PATHS):
        who = auth.manager(request)
        if not who:
            if path.startswith("/api/"):
                return JSONResponse({"error": "Sign in as a manager first."}, status_code=401)
            return RedirectResponse("/manage/signin?next=" + path, status_code=303)
        response = await call_next(request)
        if request.method == "GET" and not path.startswith("/api/"):
            auth.set_session(response, request, "m", who["id"])   # 30 days from the last visit
        return response
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
    """A person's lessons in folder order, each tagged with its folder. In a folder
    set to "in order", a lesson stays locked until the ones before it are passed."""
    rows = []
    for topic, members in content.group_by_topic(content.lessons_for_role(lessons, person["role"])):
        blocked = False
        for lesson in members:
            state, last = db.lesson_state(con, person["id"], lesson)
            locked = bool(topic.get("in_order")) and blocked and state == "due"
            rows.append({"lesson": lesson, "state": state, "label": STATE_LABELS[state], "last": last, "topic": topic,
                         "locked": locked})
            blocked = blocked or state == "due"
    return rows


def _locked_for(con, person, lesson_id):
    return any(r["locked"] for r in _my_lessons(con, person, content.load_lessons()) if r["lesson"]["id"] == lesson_id)


def _safe_next(url, default):
    return url if url and url.startswith("/") and not url.startswith("//") and "\\" not in url else default


@app.get("/signin/{wid}", response_class=HTMLResponse)
def signin_page(request: Request, wid: int, wrong: int = 0):
    with db.connect() as con:
        person = db.worker(con, wid)
    if not person or not person["active"] or not person.get("approved", 1):
        raise HTTPException(404, "No one with that id")
    return render(request, "signin.html", person=person, wrong=wrong, locked=auth.locked_for("w", wid),
                  has_pin=bool(person.get("pin_hash")))


@app.post("/signin/{wid}")
def signin(request: Request, wid: int, pin: str = Form("")):
    with db.connect() as con:
        person = db.worker(con, wid)
    if not person or not person["active"] or not person.get("approved", 1):
        raise HTTPException(404, "No one with that id")
    if not auth.check_pin("w", wid, pin.strip(), person.get("pin_hash"), request):
        return RedirectResponse(f"/signin/{wid}?wrong=1", status_code=303)
    response = RedirectResponse(f"/me/{wid}", status_code=303)
    auth.set_session(response, request, "w", wid)
    return response


@app.get("/join", response_class=HTMLResponse)
def join_page(request: Request, bad: str = "", done: int = 0):
    """A new worker makes their own account; a manager approves it."""
    return render(request, "join.html", roles=content.load_roles(), bad=bad, done=done)


@app.post("/join")
def join(request: Request, name: str = Form(""), role: str = Form(""), pin: str = Form(""), pin2: str = Form("")):
    name = " ".join(name.split())[:80]
    if not name:
        return RedirectResponse("/join?bad=name", status_code=303)
    if role not in {r["id"] for r in content.load_roles()}:
        return RedirectResponse("/join?bad=role", status_code=303)
    if not auth.WORKER_PIN.match(pin.strip()) or pin.strip() != pin2.strip():
        return RedirectResponse("/join?bad=pin", status_code=303)
    with db.connect() as con:
        if not db.request_account(con, name, role, auth.hash_pin(pin.strip())):
            return RedirectResponse("/join?bad=full", status_code=303)
    return RedirectResponse("/join?done=1", status_code=303)


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
def signout(request: Request, kind: str = Form("")):
    response = RedirectResponse("/", status_code=303)
    auth.clear_session(response, request, kind if kind in ("w", "m") else None)
    return response


def _can_see(request, wid):
    """A worker sees their own page and certificates; a manager sees everyone's."""
    w = auth.worker(request)
    return bool(auth.manager(request)) or bool(w and w["id"] == wid)


@app.get("/me/{wid}", response_class=HTMLResponse)
def my_page(request: Request, wid: int):
    who = auth.current(request)
    if not _can_see(request, wid):
        return RedirectResponse(f"/signin/{wid}", status_code=303)
    with db.connect() as con:
        person = db.worker(con, wid)
        if not person:
            raise HTTPException(404, "No one with that id")
        lessons = content.load_lessons()
        rows = _my_lessons(con, person, lessons)
        history = db.attempts(con, wid)
    return render(request, "me.html", person=person, rows=rows, history=history, who=who,
                  manager_view=bool(auth.manager(request)),
                  titles={t["id"]: t["title"] for t in content.trashed_lessons()} | {lid: l["title"] for lid, l in lessons.items()})


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
    if person:
        with db.connect() as con:
            if _locked_for(con, person, lesson_id):
                return RedirectResponse(f"/me/{person['id']}", status_code=303)
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
            if _locked_for(con, who, lesson_id):
                raise HTTPException(403, "Finish the lessons before this one first.")
            passed, cid = db.record(con, who["id"], {**lesson, "topic_name": content.topic_name(lesson["id"])}, score)
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
    with db.connect() as con:
        c = db.completion(con, cid)
    if not c or not c["passed"]:
        raise HTTPException(404, "No such certificate")
    if not _can_see(request, c["worker_id"]):
        return RedirectResponse(f"/signin/{c['worker_id']}", status_code=303)
    lesson = content.load_lessons().get(c["lesson_id"]) or {"title": c["lesson_id"], "citations_parsed": []}
    trashed = next((t["data"] for t in content.trashed_lessons() if t["id"] == c["lesson_id"]), None)
    if trashed and not c.get("lesson_title"):
        c = {**c, "lesson_title": trashed.get("title"), "lesson_citations": ", ".join(trashed.get("citations") or [])}
    if c.get("lesson_title"):   # as it was when they passed it
        lesson = {"title": c["lesson_title"],
                  "citations_parsed": [{"ref": r.strip()} for r in (c.get("lesson_citations") or "").split(",") if r.strip()]}
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
                  current_manager=auth.manager(request), trash=content.trashed_lessons(),
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


@app.get("/manage/approvals", response_class=HTMLResponse)
def approvals_page(request: Request):
    with db.connect() as con:
        waiting = db.pending_workers(con)
    return render(request, "approvals.html", waiting=waiting, roles=content.load_roles())


@app.post("/manage/approvals/{wid}")
def approval(wid: int, action: str = Form(""), role: str = Form("")):
    with db.connect() as con:
        if action == "approve" and role in {r["id"] for r in content.load_roles()}:
            db.approve_worker(con, wid, role)
        elif action == "decline":
            db.decline_worker(con, wid)
    return RedirectResponse(f"/manage/approvals?done={action}", status_code=303)


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


def _survey_ring(scale, counts):
    """Agree first (its share goes in the middle), then neutral, then disagree."""
    order = list(range(len(scale)))[::-1]
    return _donut([(scale[k], f"seg{k}", counts[k]) for k in order])


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
                            "avg": round(sum(vals) / len(vals), 1) if vals else 0,
                            "ring": _survey_ring(out["scale"], counts)})
    out["all_ring"] = _survey_ring(out["scale"], [sum(b["counts"][k] for b in out["bars"]) for k in range(top + 1)])
    totals = [sum(r["answers"]) for r in rows]
    out["avg_score"] = round(sum(totals) / n, 1)
    title = lambda lid: lessons.get(lid, {}).get("title", lid)
    out["topics"] = [{"text": r["next_topic"], "lesson": title(r["lesson_id"]), "month": r["month"]} for r in rows if r["next_topic"]][:60]
    out["comments"] = [{"text": r["comments"], "lesson": title(r["lesson_id"]), "month": r["month"]} for r in rows if r["comments"]][:60]
    return out


DONUT_R = 48
DONUT_C = 2 * 3.14159265 * DONUT_R
COMPLETION_PARTS = [("Done", "done", ("done",)), ("Retake due", "retake", ("updated", "refresh")),
                    ("Failed, retake", "failed", ("failed",)), ("Not started", "due", ("due",))]


def _donut(parts, headline=0):
    """parts: [(name, css class, count)] -> ring segments for an SVG circle (dash
    length and offset along the ring, with a small gap between segments).
    headline: which part's share goes in the middle."""
    total = sum(n for _, _, n in parts)
    live = sum(1 for _, _, n in parts if n)
    gap = 2 if live > 1 else 0
    segs, at = [], 0.0
    for name, cls, n in parts:
        length = DONUT_C * n / total if total else 0
        segs.append({"name": name, "cls": cls, "n": n, "pct": round(100 * n / total) if total else 0,
                     "dash": f"{max(length - gap, 0):.2f} {DONUT_C:.2f}", "offset": f"{-at:.2f}", "show": n > 0})
        at += length
    return {"total": total, "segs": segs, "pct": segs[headline]["pct"] if total else 0}


def _completion(cells):
    """A completion ring over the required person-by-lesson cells."""
    req = [c for c in cells if c["required"]]
    return _donut([(name, cls, sum(1 for c in req if c["state"] in states)) for name, cls, states in COMPLETION_PARTS])


def _dashboard_charts(role, focus, columns, matrix, who_did, roles):
    """The main completion ring plus smaller rings that fit the question asked:
    one lesson picked -> that lesson by position; a position picked -> each of its
    lessons; neither -> each position."""
    names = {r["id"]: r["name"] for r in roles}
    by_role = lambda rows: [(r["id"], [c for row in rows if row[0] == r["id"] for c in row[1]]) for r in roles]
    if focus:
        main = _completion(who_did or [])
        title, by = f"“{focus['title']}”" + (f", {names[role]}" if role else ""), "By position"
        split = [{"label": names[rid], "ring": _completion(cells), "href": f"?role={rid}&lesson={focus['id']}"}
                 for rid, cells in by_role([(r["person"]["role"], [r]) for r in who_did or []])]
    else:
        main = _completion([c for row in matrix for c in row["cells"]])
        title = names[role] if role else "Everyone"
        if role:
            by = "By lesson"
            split = [{"label": l["title"], "ring": _completion([row["cells"][i] for row in matrix]), "href": f"?role={role}&lesson={l['id']}"}
                     for i, l in enumerate(columns)]
        else:
            by = "By position"
            split = [{"label": names[rid], "ring": _completion(cells), "href": f"?role={rid}"}
                     for rid, cells in by_role([(row["person"]["role"], row["cells"]) for row in matrix])]
    return {"main": main, "title": title, "by": by, "split": [x for x in split if x["ring"]["total"]]}


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
    charts = _dashboard_charts(role, focus, columns, matrix, who_did, roles)
    return render(request, "dashboard.html", roles=roles, role=role, lessons=sorted(lessons.values(), key=lambda l: l["title"]),
                  focus=focus, columns=columns, matrix=matrix, totals=totals, who_did=who_did, survey=survey, charts=charts)


def _records(role="", person=0, lesson="", result="passed", topic=""):
    """Training records (every attempt ever saved; nothing is ever deleted), oldest first."""
    lessons = {t["id"]: t["data"] for t in content.trashed_lessons()} | content.load_lessons()  # trashed ones still name old records
    role_names = content.role_names()
    topics = content.load_topics()
    order = {t["name"]: i for i, t in enumerate(topics)}
    with db.connect() as con:
        people = {w["id"]: w for w in db.workers(con, active_only=False)}
        rows = []
        for a in reversed(db.attempts(con)):
            w = people.get(a["worker_id"])
            if not w or (role and a["role"] != role) or (person and a["worker_id"] != person) \
                    or (lesson and a["lesson_id"] != lesson) or (result == "passed" and not a["passed"]):
                continue
            live = lessons.get(a["lesson_id"]) or {}
            # The folder it's in now; a lesson deleted for good keeps the folder it was in when taken.
            folder = content.topic_name(a["lesson_id"], topics)
            if folder == content.OTHER_TOPIC["name"] and a["lesson_id"] not in lessons:
                folder = a.get("lesson_topic") or folder
            if topic and folder != topic:
                continue
            rows.append({"topic": folder,
                "name": a["name"], "position": role_names.get(a["role"], a["role"]), "active": bool(w["active"]),
                "lesson": a.get("lesson_title") or live.get("title") or a["lesson_id"],
                "version": a.get("lesson_version") or "", "date": a["completed_at"][:16].replace("T", " "),
                "score": round(a["score"] * 100), "passed": bool(a["passed"]),
                "certificate": _certificate_number(a) if a["passed"] else "",
                "cert_link": f"/certificate/{a['id']}" if a["passed"] else "",
                "rules": a.get("lesson_citations") or ", ".join(live.get("citations") or []),
            })
    rows.sort(key=lambda r: order.get(r["topic"], len(order)))   # by folder, oldest first within each
    return rows


RECORD_COLUMNS = [("Topic", "topic"), ("Name", "name"), ("Position", "position"), ("Lesson", "lesson"), ("Date", "date"),
                  ("Score %", "score"), ("Result", "result"), ("Certificate", "certificate"),
                  ("Lesson version", "version"), ("Rules", "rules"), ("Still employed", "employed")]


@app.get("/manage/records", response_class=HTMLResponse)
def records_page(request: Request, role: str = "", person: int = 0, lesson: str = "", result: str = "passed",
                 topic: str = ""):
    with db.connect() as con:
        people = sorted(db.workers(con, active_only=False), key=lambda w: w["name"].lower())
    lesson_ids = {}
    for lid, title in _record_lessons():
        lesson_ids.setdefault(lid, title)
    rows = _records(role, person, lesson, result, topic)
    folders = [t["name"] for t in content.load_topics()] + [content.OTHER_TOPIC["name"]]
    folders += sorted({r["topic"] for r in _records(result="all")} - set(folders))
    return render(request, "records.html", rows=rows, roles=content.load_roles(), folders=folders, topic=topic,
                  people=people, lesson_choices=sorted(lesson_ids.items(), key=lambda kv: kv[1].lower()),
                  role=role, person=person, lesson=lesson, result=result, provider=PROVIDER,
                  printed=dt.datetime.now().strftime("%Y-%m-%d %H:%M"))


def _record_lessons():
    """(lesson id, title) for every lesson that is live or appears in a record."""
    out = [(lid, l["title"]) for lid, l in content.load_lessons().items()]
    out += [(t["id"], t["title"]) for t in content.trashed_lessons()]
    with db.connect() as con:
        out += [(r["lesson_id"], r["lesson_title"] or r["lesson_id"]) for r in
                con.execute("SELECT DISTINCT lesson_id, lesson_title FROM completions")]
    return out


@app.get("/manage/records.csv")
def records_csv(role: str = "", person: int = 0, lesson: str = "", result: str = "passed", topic: str = ""):
    import csv
    import io
    buf = io.StringIO()
    out = csv.writer(buf)
    out.writerow([c for c, _ in RECORD_COLUMNS])
    for r in _records(role, person, lesson, result, topic):
        r = {**r, "result": "Passed" if r["passed"] else "Not passed", "employed": "Yes" if r["active"] else "No"}
        # A leading = + - @ would make Excel run the cell as a formula.
        out.writerow([("'" + str(v)) if str(v)[:1] in "=+-@" else v for v in (r[k] for _, k in RECORD_COLUMNS)])
    name = f"training-records-{dt.date.today().isoformat()}.csv"
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",   # BOM: Excel reads it as UTF-8
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})


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
    lessons = content.load_lessons()
    topics = content.load_topics()
    status = rules.load_status()
    groups = content.group_by_topic(list(lessons.values()), topics)
    used_by = {}
    for l in lessons.values():
        for c in l["citations_parsed"]:
            used_by.setdefault(c["ref"], []).append(l)
    known = {t["id"] for t in topics}
    rule_groups = {}
    for r in content.sync_rules(lessons, topics):
        kind, words = rules.rule_state(r, status)
        s = status.get("rules", {}).get(r["ref"]) or {}
        c = content.parse_citation(r["ref"])
        rule_groups.setdefault(r["topic"] if r["topic"] in known else "other", []).append(
            {**r, "state": kind, "state_words": words, "url": c["url"], "official": _short_rule_name(s.get("name", "")),
             "used_by": used_by.get(r["ref"], [])})
    if not any(t["id"] == "other" for t, _ in groups):   # always there, so a rule can be added with no folders yet
        groups.append((content.OTHER_TOPIC, []))
    # Changed or missing rules no lesson cites yet (the ones lessons cite are flagged on the lesson).
    loose = [r for rs in rule_groups.values() for r in rs if r["state"] in ("bad", "warn") and not r["used_by"]]
    return render(request, "topics.html", groups=groups, topics=topics, rule_groups=rule_groups, loose=loose,
                  lang="en", lessons=lessons, status=status, trash=content.trashed_lessons(),
                  flagged={lid: rules.lesson_flags(l, status) for lid, l in lessons.items()},
                  broken=content.broken_lesson_files(), rule_msg=RULE_MESSAGES.get(request.query_params.get("rule", "")),
                  rule_bad=request.query_params.get("rule") in ("bad", "dup", "notfound", "unreachable", "inuse"),
                  rule_ref=request.query_params.get("ref", "")[:40])


def _short_rule_name(name):
    """'§ 1910.178 Powered industrial trucks.' -> 'Powered industrial trucks'"""
    return re.sub(r"^(§\s*[\w.]+|Section\s+[\w.]+\.)\s*", "", name or "").strip().rstrip(".")


RULE_MESSAGES = {
    "added": "Rule added. It was found at the official source.",
    "saved": "Rule saved.",
    "deleted": "Rule deleted.",
    "bad": "That isn't a rule citation the app can read. Use the form 29 CFR 1910.147 (federal) or 8 CCR 3314 (California).",
    "dup": "That rule is already on the list.",
    "notfound": "That rule wasn't found at the official source (eCFR or California's Title 8 site). Check the number.",
    "unreachable": "The official source couldn't be reached to check that rule. Try again in a minute.",
    "inuse": "That rule can't be deleted while lessons cite it. Remove it from those lessons first (last page of the lesson).",
}


@app.post("/manage/rules")
async def edit_rules(request: Request):
    """The rules list on the Lessons page: add (checked at the official source first),
    rename, file in a folder, note, mark still correct, delete."""
    form = await request.form()
    action = form.get("action")
    topics = content.load_topics()
    known = {t["id"] for t in topics}
    topic = str(form.get("topic") or "")
    topic = topic if topic in known else ""
    rules_list = content.load_rules()
    c = content.parse_citation(str(form.get("ref") or ""))
    where = "#t-" + (topic or "other")
    if not c:
        return RedirectResponse(f"/manage/topics?rule=bad{where}", status_code=303)
    ref = c["ref"]
    q = "&ref=" + quote(ref)
    rule = next((r for r in rules_list if r["ref"] == ref), None)
    if action == "add":
        if rule:
            return RedirectResponse(f"/manage/topics?rule=dup{q}#t-{rule['topic'] or 'other'}", status_code=303)
        status = rules.load_status()
        entry = await run_in_threadpool(rules.check_rule, c, status["rules"].get(ref))
        if entry.get("found") is None or entry.get("last_error"):
            return RedirectResponse(f"/manage/topics?rule=unreachable{q}{where}", status_code=303)
        if not entry["found"]:
            return RedirectResponse(f"/manage/topics?rule=notfound{q}{where}", status_code=303)
        status["rules"][ref] = entry
        rules.save_status(status)
        rules_list.append({"ref": ref, "name": str(form.get("name") or "").strip()[:120] or _short_rule_name(entry.get("name", "")),
                           "topic": topic, "note": "", "reviewed_on": content.now_stamp()})
        content.save_rules(rules_list)
        return RedirectResponse(f"/manage/topics?rule=added{q}{where}", status_code=303)
    if not rule:
        return RedirectResponse("/manage/topics", status_code=303)
    if action == "save":
        rule["name"] = str(form.get("name") or "").strip()[:120]
        rule["topic"] = topic
        msg = "saved"
    elif action == "reviewed":
        rule["reviewed_on"] = content.now_stamp()
        msg, where = "saved", "#rules"
    elif action == "delete":
        users = [l for l in content.load_lessons().values() if any(x["ref"] == ref for x in l["citations_parsed"])]
        if users:
            return RedirectResponse(f"/manage/topics?rule=inuse{q}#t-{rule['topic'] or 'other'}", status_code=303)
        rules_list.remove(rule)
        msg = "deleted"
    else:
        return RedirectResponse("/manage/topics", status_code=303)
    content.save_rules(rules_list)
    return RedirectResponse(f"/manage/topics?rule={msg}{q}{where}", status_code=303)


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


@app.post("/api/manage/topics")
async def order_topics(request: Request):
    """Saves the Topics page after a drag or a toggle: folder order, each folder's
    lessons in order, and whether workers must take them in order. Lessons left out
    of every folder go to Other lessons; folder names stay as they are."""
    body = await request.json()
    known = {t["id"]: t for t in content.load_topics()}
    lessons = content.load_lessons()
    sent = [t for t in body.get("topics") or [] if isinstance(t, dict) and t.get("id") in known]
    if {t["id"] for t in sent} != set(known) or len(sent) != len(known):
        return JSONResponse({"error": "The folders changed in another window. Reload the page."}, status_code=409)
    seen, out = set(), []
    for t in sent:
        ids = [str(x) for x in t.get("lessons") or [] if str(x) in lessons and str(x) not in seen]
        seen.update(ids)
        out.append({**known[t["id"]], "lessons": ids, "in_order": bool(t.get("in_order"))})
    content.save_topics(out)
    return {"ok": True}


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
    return render(request, "manager_signin.html", setup=True, bad=bad, legacy=[])


def _manager_form_problem(con, name, email, pin, pin2=None, mid=None):
    """What's wrong with a new manager's details, or '' if they're fine."""
    if not name.strip():
        return "name"
    if not auth.EMAIL.match(email.strip()):
        return "email"
    other = db.manager_by_email(con, email)
    if other and other["id"] != mid:
        return "taken"
    if not auth.MANAGER_PIN.match(pin.strip()) or (pin2 is not None and pin.strip() != pin2.strip()):
        return "pin"
    return ""


@app.post("/manage/setup")
def setup(request: Request, name: str = Form(""), email: str = Form(""), pin: str = Form(""), pin2: str = Form("")):
    with db.connect() as con:
        if db.managers(con):
            return RedirectResponse("/manage/signin", status_code=303)
        bad = _manager_form_problem(con, name, email, pin, pin2)
        if bad:
            return RedirectResponse(f"/manage/setup?bad={bad}", status_code=303)
        mid = db.add_manager(con, name, auth.hash_pin(pin.strip()), email)
    response = _show_recovery_code(request, mid, after="setup")
    auth.set_session(response, request, "m", mid)
    return response


@app.get("/manage/signin", response_class=HTMLResponse)
def manager_signin_page(request: Request, next: str = "", wrong: int = 0):
    if auth.manager(request):         # already signed in: go straight on
        return RedirectResponse(_safe_next(next, "/manage/dashboard"), status_code=303)
    with db.connect() as con:
        people = db.managers(con)
    if not people:
        return RedirectResponse("/manage/setup", status_code=303)
    # Managers made before work emails were asked for pick their name until they add one.
    legacy = [m for m in people if not m.get("email")]
    return render(request, "manager_signin.html", setup=False, legacy=legacy, wrong=wrong, next=next)


@app.post("/manage/signin")
def manager_signin(request: Request, email: str = Form(""), manager: int = Form(0), pin: str = Form(""), next: str = Form("")):
    with db.connect() as con:
        if email.strip():
            m = db.manager_by_email(con, email)
        else:
            m = db.manager(con, manager)
            m = m if m and not m.get("email") else None
    if not m or not m["active"]:
        auth.note_ip_fail(auth.client_ip(request))    # a made-up email counts as a wrong try too
    if not m or not m["active"] or not auth.check_pin("m", m["id"], pin.strip(), m["pin_hash"], request):
        return RedirectResponse(f"/manage/signin?wrong=1&next={next if _safe_next(next, '') else ''}", status_code=303)
    response = RedirectResponse(_safe_next(next, "/manage/dashboard"), status_code=303)
    auth.set_session(response, request, "m", m["id"])
    return response


def _show_recovery_code(request, mid, after=""):
    """Makes a new recovery code (the old one stops working) and shows it once."""
    code = auth.new_recovery_code()
    with db.connect() as con:
        db.set_manager_recovery(con, mid, auth.hash_pin(auth.clean_recovery_code(code)))
        m = db.manager(con, mid)
    return render(request, "recovery_code.html", code=code, manager=m, after=after)


@app.post("/manage/recovery-code")
def make_recovery_code(request: Request):
    return _show_recovery_code(request, auth.manager(request)["id"])


@app.get("/manage/recover", response_class=HTMLResponse)
def recover_page(request: Request, bad: str = ""):
    return render(request, "recover.html", bad=bad)


@app.post("/manage/recover")
def recover(request: Request, email: str = Form(""), code: str = Form(""), pin: str = Form(""), pin2: str = Form("")):
    """Forgotten PIN: work email + recovery code -> a new PIN (and a new recovery code)."""
    if not auth.MANAGER_PIN.match(pin.strip()) or pin.strip() != pin2.strip():
        return RedirectResponse("/manage/recover?bad=pin", status_code=303)
    with db.connect() as con:
        m = db.manager_by_email(con, email) if auth.EMAIL.match(email.strip()) else None
    if not m or not m.get("recovery_hash"):
        auth.note_ip_fail(auth.client_ip(request))
        return RedirectResponse("/manage/recover?bad=code", status_code=303)
    if not auth.check_pin("r", m["id"], auth.clean_recovery_code(code), m["recovery_hash"], request):
        return RedirectResponse("/manage/recover?bad=code", status_code=303)
    with db.connect() as con:
        db.set_manager_pin(con, m["id"], auth.hash_pin(pin.strip()))
    auth._fails.pop(("m", m["id"]), None)          # a PIN lock no longer applies to the new PIN
    response = _show_recovery_code(request, m["id"], after="reset")
    auth.set_session(response, request, "m", m["id"])
    return response


@app.post("/manage/managers")
def add_manager(name: str = Form(""), email: str = Form(""), pin: str = Form("")):
    with db.connect() as con:
        bad = _manager_form_problem(con, name, email, pin)
        if bad:
            return RedirectResponse(f"/manage?manager_bad={bad}#managers", status_code=303)
        db.add_manager(con, name, auth.hash_pin(pin.strip()), email)
    return RedirectResponse("/manage?manager_added=1#managers", status_code=303)


@app.post("/manage/managers/{mid}/email")
def manager_email(mid: int, email: str = Form("")):
    with db.connect() as con:
        m = db.manager(con, mid)
        if not m:
            raise HTTPException(404)
        bad = _manager_form_problem(con, m["name"], email, "000000", mid=mid)
        if bad:
            return RedirectResponse(f"/manage?manager_bad={bad}#managers", status_code=303)
        db.set_manager_email(con, mid, email)
    return RedirectResponse("/manage?manager_saved=1#managers", status_code=303)


@app.post("/manage/managers/{mid}/pin")
def manager_pin(mid: int, pin: str = Form("")):
    """Any manager can reset a manager's PIN (their own, or a colleague who forgot theirs)."""
    if not auth.MANAGER_PIN.match(pin.strip()):
        return RedirectResponse("/manage?manager_bad=pin#managers", status_code=303)
    with db.connect() as con:
        if not db.manager(con, mid):
            raise HTTPException(404)
        db.set_manager_pin(con, mid, auth.hash_pin(pin.strip()))
    auth._fails.pop(("m", mid), None)
    return RedirectResponse("/manage?manager_saved=1#managers", status_code=303)


@app.post("/manage/check")
def manage_check():
    threading.Thread(target=check_rules_now, daemon=True).start()
    return RedirectResponse("/manage/topics?checking=1#rules", status_code=303)


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
    return RedirectResponse("/manage/topics", status_code=303)


@app.post("/manage/lesson/{lesson_id}/delete")
def delete_lesson(lesson_id: str):
    _get_lesson(lesson_id)
    content.trash_lesson(lesson_id)
    return RedirectResponse("/manage/topics?trashed=1#trash", status_code=303)


@app.post("/manage/trash/{name}/restore")
def restore_lesson(name: str):
    new_id = content.restore_lesson(name)
    if not new_id:
        raise HTTPException(404, "Not in the trash")
    return RedirectResponse("/manage/topics", status_code=303)


@app.post("/manage/trash/{name}/purge")
def purge_lesson(name: str):
    if not content.purge_lesson(name):
        raise HTTPException(404, "Not in the trash")
    _sweep_media(content.load_lessons())
    return RedirectResponse("/manage/topics#trash", status_code=303)


def _editor(request, lesson, is_new=False):
    status = rules.load_status().get("rules", {})
    return render(request, "edit.html", lesson=lesson, is_new=is_new, roles=content.load_roles(),
                  data=editor.editor_payload(lesson), rule_status=status, research_ready=research.available(),
                  research_mode=research.mode(),
                  rule_list=[{"ref": r["ref"], "name": r["name"] or status.get(r["ref"], {}).get("name", "")}
                             for r in content.load_rules()])


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
        lesson, notes = result["lesson"], []
        result["checks"] = await run_in_threadpool(_verify, lesson["citations"])
        wrong = [c["ref"] for c in result["checks"] if c["found"] is False]
        if wrong:          # the source says there's no such section: keep it out of the lesson
            lesson["citations"] = [c for c in lesson["citations"] if content.parse_citation(c)["ref"] not in wrong]
            notes.append(f"Took out {', '.join(wrong)}: {'it is' if len(wrong) == 1 else 'they are'} not at eCFR / DIR.")
        if not lesson["citations"]:
            notes.append("This draft has no confirmed rule citation yet. Ask Claude to find the rule before saving.")
        if await run_in_threadpool(_settle_pictures, lesson):
            notes.append("A picture Claude picked couldn't be checked as free to use, so it's left off.")
        found = await run_in_threadpool(_find_pictures, lesson)
        if found:
            notes.append(f"Added {found} matching photo{'' if found == 1 else 's'} from Wikimedia Commons (credited in sources).")
        for sl in lesson.get("slides", []):
            sl.pop("picture_search", None)
        if notes:
            result["reply"] = ((result.get("reply") or "") + "\n\n" + " ".join(f"({n})" for n in notes)).strip()
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
        kept = []
        for link in s.get("images") or []:
            if link.startswith("/media/"):
                name = link.rsplit("/", 1)[1]
                if MEDIA_NAME.match(name) and (media_dir() / name).is_file():
                    kept.append(link)
                    continue
            else:
                try:
                    found = _commons_picture(link, get)
                except Exception as e:  # Commons unreachable or an odd answer
                    print(f"Picture check failed for {link}: {e.__class__.__name__}")
                    found = None
                if found:
                    kept.append(found[0])
                    lesson["sources"] = [x for x in lesson.get("sources", []) if x.get("url") != link] + [found[1]]
                    continue
            dropped += 1
        if "images" in s:
            s["images"] = kept
    return dropped


PICTURE_CHOICES = 4        # candidate photos Claude looks at per slide
PICTURE_SLIDES = 8         # most slides searched per draft


def _commons_search(terms, get=None):
    """Free-to-use Commons photos for a few words: [{page, thumb, title}], best first."""
    import requests
    get = get or requests.get
    r = get(COMMONS_API, params={"action": "query", "generator": "search", "gsrsearch": f"{terms} filetype:bitmap",
                                 "gsrnamespace": 6, "gsrlimit": 12, "prop": "imageinfo", "iiprop": "url|mime|extmetadata",
                                 "iiurlwidth": 480, "format": "json", "formatversion": 2}, headers=WEB_HEADERS, timeout=20)
    pages = sorted(r.json().get("query", {}).get("pages", []), key=lambda pg: pg.get("index", 0))
    out = []
    for pg in pages:
        info = (pg.get("imageinfo") or [None])[0] or {}
        lic = info.get("extmetadata", {}).get("LicenseShortName", {}).get("value", "").strip()
        if info.get("mime") in ("image/jpeg", "image/png", "image/webp") and FREE_LICENSE.match(lic) \
                and not re.search(r"\b(nc|nd)\b", lic, re.I) and info.get("thumburl"):
            out.append({"page": info.get("descriptionurl") or "https://commons.wikimedia.org/wiki/" + pg["title"].replace(" ", "_"),
                        "thumb": info["thumburl"], "title": pg["title"].removeprefix("File:").rsplit(".", 1)[0]})
    return out[:PICTURE_CHOICES]


def _find_pictures(lesson, get=None, matcher=None):
    """Pictures that match the slides: for each reading slide Claude described
    (picture_search), the app finds free photos on Wikimedia Commons, Claude looks at
    them next to the slide's words and picks the one that shows it (or none), and the
    app saves the pick with its credit. Returns how many slides got a photo."""
    import tempfile

    import requests
    get = get or requests.get
    matcher = matcher or research.match_pictures
    wanted = [(i, s) for i, s in enumerate(lesson.get("slides", []))
              if s.get("type") == "reading" and not s.get("images") and s.get("picture_search")][:PICTURE_SLIDES]
    if not wanted:
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        asks = []
        for i, s in wanted:
            try:
                found = _commons_search(s["picture_search"], get)
            except Exception as e:  # Commons unreachable: the slide just has no photo
                print(f"Picture search failed for {s['picture_search']!r}: {e.__class__.__name__}")
                continue
            cands = []
            for k, c in enumerate(found):
                try:
                    img = get(c["thumb"], headers=WEB_HEADERS, timeout=20)
                except Exception:
                    continue
                if img.status_code == 200 and 0 < len(img.content) <= MAX_IMAGE_BYTES:
                    path = os.path.join(tmp, f"slide{i}-{k}{os.path.splitext(c['thumb'].split('?')[0])[1].lower() or '.jpg'}")
                    with open(path, "wb") as f:
                        f.write(img.content)
                    cands.append({**c, "path": path})
            if cands:
                asks.append({"slide": i, "heading": s.get("heading", ""), "text": s.get("text", ""),
                             "search": s["picture_search"], "candidates": cands})
        if not asks:
            return 0
        try:
            picks = matcher(asks)
        except Exception as e:
            print(f"Picture matching failed: {e.__class__.__name__}: {e}")
            return 0
        added, used = 0, set()
        for a in asks:
            k = picks.get(a["slide"], -1)
            if not isinstance(k, int) or not 0 <= k < len(a["candidates"]) or a["candidates"][k]["page"] in used:
                continue
            page = a["candidates"][k]["page"]
            try:
                saved = _commons_picture(page, get)     # re-checks the license and keeps a full copy
            except Exception:
                saved = None
            if saved:
                link, source = saved
                lesson["slides"][a["slide"]]["images"] = [link]
                lesson["sources"] = lesson.get("sources", []) + [source]
                used.add(page)
                added += 1
        return added


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
    name = f"{content.slugify(lesson_id)}-{dt.datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}{ext}"
    with open(media_dir() / name, "wb") as out:
        shutil.copyfileobj(upload.file, out, 1024 * 1024)
    return f"/media/{name}"


def _save_picture(ext, data):
    name = f"attached-{dt.datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}{ext}"
    (media_dir() / name).write_bytes(data)
    return f"/media/{name}"


def _remove_unused_upload(video, lessons):
    """Deletes an old uploaded video once no lesson points to it."""
    kept = list(lessons.values()) + [t["data"] for t in content.trashed_lessons()]
    if not video.startswith("/media/") or any(l.get("video") == video for l in kept):
        return
    name = video.rsplit("/", 1)[1]
    if MEDIA_NAME.match(name):
        (media_dir() / name).unlink(missing_ok=True)


def _sweep_media(lessons, older_than_hours=24):
    """Deletes uploads nobody saved into a lesson (an editor closed without saving)."""
    kept = list(lessons.values()) + [t["data"] for t in content.trashed_lessons()]  # trash keeps its media
    used = {l.get("video") for l in kept}
    used |= {p for l in kept for s in l.get("sections") or [] for p in s.get("images") or s.get("image") and [s["image"]] or []}
    cutoff = time.time() - older_than_hours * 3600
    for path in media_dir().iterdir():
        if MEDIA_NAME.match(path.name) and f"/media/{path.name}" not in used and path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)


@app.get("/health")
def health():
    return JSONResponse({"ok": True})
