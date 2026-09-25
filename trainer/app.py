"""The training app: worker pages, the lesson player and the manager pages."""
import datetime as dt
import os
import re
import shutil
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import content, db, editor, research, rules

HERE = content.ROOT / "trainer"
templates = Jinja2Templates(directory=HERE / "templates")

# Uploaded videos live next to the database (never in git: the repo is public
# and videos are big). Served with range requests so the player can seek.
VIDEO_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime"}
MAX_VIDEO_BYTES = 2 * 1024**3
MEDIA_NAME = re.compile(r"^[a-z0-9-]+\.(mp4|m4v|webm|mov)$")


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


app = FastAPI(title="Bakery Training", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


# ---- Worker side ------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    with db.connect() as con:
        people = db.workers(con)
    return render(request, "home.html", workers=people)


def _my_lessons(con, person, lessons):
    rows = []
    for lesson in content.lessons_for_role(lessons, person["role"]):
        state, last = db.lesson_state(con, person["id"], lesson)
        rows.append({"lesson": lesson, "state": state, "label": STATE_LABELS[state], "last": last})
    # Path order: finished lessons first, then what needs retaking, then new ones.
    order = {"done": 0, "updated": 1, "refresh": 2, "due": 3}
    rows.sort(key=lambda r: (order[r["state"]], r["lesson"]["title"]))
    return rows


@app.get("/me/{wid}", response_class=HTMLResponse)
def my_page(request: Request, wid: int):
    with db.connect() as con:
        person = db.worker(con, wid)
        if not person:
            raise HTTPException(404, "No one with that id")
        rows = _my_lessons(con, person, content.load_lessons())
    return render(request, "me.html", person=person, rows=rows)


def _get_lesson(lesson_id):
    lesson = content.load_lessons().get(lesson_id)
    if not lesson:
        raise HTTPException(404, "No such lesson")
    return lesson


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


@app.get("/lesson/{lesson_id}", response_class=HTMLResponse)
def lesson_page(request: Request, lesson_id: str, worker: int | None = None):
    lesson = _get_lesson(lesson_id)
    person = None
    if worker is not None:
        with db.connect() as con:
            person = db.worker(con, worker)
    status = rules.load_status()
    return render(request, "lesson.html", lesson=lesson, person=person, data=_public_lesson(lesson),
                  rule_status=status.get("rules", {}))


@app.post("/api/lesson/{lesson_id}/check")
async def check_answer(lesson_id: str, request: Request):
    """Feedback for one answer while the lesson plays."""
    body = await request.json()
    lesson = _get_lesson(lesson_id)
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
    wid = body.get("worker")
    if wid is not None:
        with db.connect() as con:
            if not db.worker(con, int(wid)):
                raise HTTPException(404, "No one with that id")
            passed = db.record(con, int(wid), lesson, score)
    return {"score": round(score * 100), "passed": passed, "right": right, "total": total,
            "pass_mark": round(db.PASS_MARK * 100)}


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
    return render(request, "manage.html", lessons=lessons, flagged=flagged, progress=progress, inactive=inactive,
                  roles=content.load_roles(), status=status, broken=content.broken_lesson_files())


@app.post("/manage/workers")
def add_worker(name: str = Form(...), role: str = Form(...)):
    if name.strip():
        with db.connect() as con:
            db.add_worker(con, name, role)
    return RedirectResponse("/manage#people", status_code=303)


@app.post("/manage/workers/{wid}/active")
def worker_active(wid: int, active: int = Form(...)):
    with db.connect() as con:
        db.set_worker_active(con, wid, bool(active))
    return RedirectResponse("/manage#people", status_code=303)


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
                  data=editor.editor_payload(lesson), rule_status=status, research_ready=research.available())


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
    except Exception as e:  # shown in the panel; the manager can try again
        print(f"Research failed: {e.__class__.__name__}: {e}")
        return JSONResponse({"error": f"Claude couldn't finish that ({e.__class__.__name__}). Try again in a minute."}, status_code=502)
    if result.get("lesson"):
        result["checks"] = await run_in_threadpool(_verify, result["lesson"]["citations"])
    return result


@app.get("/media/{name}")
def media(name: str):
    if not MEDIA_NAME.match(name):
        raise HTTPException(404, "No such video")
    path = media_dir() / name
    if not path.is_file():
        raise HTTPException(404, "No such video")
    return FileResponse(path, media_type=VIDEO_TYPES["." + name.rsplit(".", 1)[1]])


def _save_upload(upload, lesson_id):
    ext = os.path.splitext(upload.filename)[1].lower()
    name = f"{content.slugify(lesson_id)}-{dt.datetime.now().strftime('%Y%m%d%H%M%S')}{ext}"
    with open(media_dir() / name, "wb") as out:
        shutil.copyfileobj(upload.file, out, 1024 * 1024)
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
    cutoff = time.time() - older_than_hours * 3600
    for path in media_dir().iterdir():
        if MEDIA_NAME.match(path.name) and f"/media/{path.name}" not in used and path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)


@app.get("/health")
def health():
    return JSONResponse({"ok": True})
