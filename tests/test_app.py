import datetime as dt
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
import time

import pytest

os.environ["TRAINING_NO_RULES_LOOP"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from trainer import auth, content, db, editor, research, rules  # noqa: E402
from trainer.app import app  # noqa: E402


SAMPLE_LESSONS = Path(__file__).parent / "lessons"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A private copy of content/ and a fresh database for each test."""
    shutil.copytree(content.CONTENT, tmp_path / "content")
    (tmp_path / "content" / "rules_status.json").unlink(missing_ok=True)
    (tmp_path / "content" / "rules.yaml").unlink(missing_ok=True)
    shutil.rmtree(tmp_path / "content" / "trash", ignore_errors=True)
    # Lessons and folders as shipped, so tests don't depend on what managers have changed in the live ones.
    shutil.rmtree(tmp_path / "content" / "lessons", ignore_errors=True)
    shutil.copytree(SAMPLE_LESSONS, tmp_path / "content" / "lessons")
    shutil.copy(Path(__file__).parent / "topics.yaml", tmp_path / "content" / "topics.yaml")
    monkeypatch.setattr(content, "CONTENT", tmp_path / "content")
    monkeypatch.setattr(content, "LESSONS", tmp_path / "content" / "lessons")
    monkeypatch.setattr(rules, "STATUS_PATH", tmp_path / "content" / "rules_status.json")
    monkeypatch.setenv("TRAINING_DB", str(tmp_path / "t.db"))
    auth._fails.clear()
    auth._ip_fails.clear()
    return tmp_path


@pytest.fixture
def anon(env):
    return TestClient(app)


@pytest.fixture
def client(env):
    """Signed in as a manager (the first manager is made on the setup page)."""
    c = TestClient(app)
    c.post("/manage/setup", data={"name": "Pat Manager", "email": "pat@bakery.example", "pin": "246810", "pin2": "246810"})
    return c


@pytest.fixture(params=["live", "sample"])
def any_lessons(request, monkeypatch):
    """Runs a test on the live lessons (whatever managers have made) and on the sample set."""
    if request.param == "sample":
        monkeypatch.setattr(content, "LESSONS", SAMPLE_LESSONS)
        assert len(content.load_lessons()) >= 9


def test_every_lesson_is_valid(any_lessons):
    lessons = content.load_lessons()
    assert not content.broken_lesson_files()
    roles = {r["id"] for r in content.load_roles()} | {"all"}
    for lid, l in lessons.items():
        assert l["title"] and l["sections"], lid          # questions are optional (a show-and-tell lesson)
        assert set(l["roles"]) <= roles, lid
        assert l["citations"] or l["company_rules"], f"{lid} is based on nothing"
        for c in l["citations_parsed"]:
            assert c["kind"] in ("cfr", "ccr"), (lid, c)
        for q in l["questions"]:
            assert 0 <= q["answer"] < len(q["choices"]), (lid, q["q"])
            if q.get("after_section") is not None:
                assert q["after_section"] < len(l["sections"]), (lid, q["q"])


def test_citation_parsing():
    assert content.parse_citation("29 CFR 1910.147")["url"] == "https://www.ecfr.gov/current/title-29/section-1910.147"
    assert content.parse_citation("8 ccr 3314")["url"] == "https://www.dir.ca.gov/title8/3314.html"
    assert content.parse_citation("OSHA lockout") is None


def test_questions_text_round_trip(env):
    qs = content.load_lessons()["lockout-tagout"]["questions"]
    back, errors = content.questions_from_text(content.questions_to_text(qs))
    assert not errors
    assert [(q["q"], q["answer"], q["choices"], q.get("after_section")) for q in back] == \
           [(q["q"], q["answer"], q["choices"], q.get("after_section")) for q in qs]
    timed, errors = content.questions_from_text("@ 1:05\nQ: Hi?\n* yes\n- no")
    assert not errors and timed[0]["at"] == 65
    _, errors = content.questions_from_text("Q: No right answer?\n- a\n- b")
    assert errors


def test_worker_takes_a_lesson(client, anon):
    r = client.post("/manage/workers", data={"name": "Sam", "role": "packaging", "pin": "1234"}, follow_redirects=False)
    assert r.status_code == 303
    with db.connect() as con:
        sam = db.workers(con)[0]
    assert anon.get(f"/me/{sam['id']}", follow_redirects=False).headers["location"] == f"/signin/{sam['id']}"
    assert anon.post(f"/signin/{sam['id']}", data={"pin": "9999"}, follow_redirects=False).headers["location"].endswith("wrong=1")
    assert anon.post(f"/signin/{sam['id']}", data={"pin": "1234"}, follow_redirects=False).headers["location"] == f"/me/{sam['id']}"
    page = anon.get(f"/me/{sam['id']}").text
    assert "Forklifts" in page and "Mixers, sheeters" not in page  # role filter
    assert anon.get("/manage", follow_redirects=False).status_code == 303          # workers can't open the manager side
    assert anon.post("/api/manage/lesson/new", json={}).status_code == 401

    lesson = content.load_lessons()["forklifts"]
    assert "answer" not in anon.get("/lesson/forklifts").text.split("window.LESSON")[1].split(";")[0]
    assert anon.post("/api/lesson/forklifts/check", json={"question": 0, "choice": 1}).json()["correct"]

    wrong = {i: (q["answer"] + 1) % len(q["choices"]) for i, q in enumerate(lesson["questions"])}
    r = anon.post("/api/lesson/forklifts/finish", json={"answers": wrong}).json()
    assert not r["passed"] and r["saved"] and "survey_ticket" not in r
    right = {i: q["answer"] for i, q in enumerate(lesson["questions"])}
    r = anon.post("/api/lesson/forklifts/finish", json={"answers": right}).json()
    assert r["passed"] and r["score"] == 100 and r["certificate"].startswith("/certificate/")
    cert = anon.get(r["certificate"]).text
    assert "Sam" in cert and lesson["title"] in cert and "United Bakery" in cert
    assert client.get(r["certificate"]).status_code == 200                        # managers see it too

    # The survey: once per completion, stored without the worker.
    ticket = r["survey_ticket"]
    assert anon.post("/api/survey", json={"ticket": ticket, "answers": [2, 1, 0]}).status_code == 400
    assert anon.post("/api/survey", json={"ticket": ticket, "answers": [2, 1, 3, 0]}).status_code == 400   # 0-2 only
    assert anon.post("/api/survey", json={"ticket": ticket, "answers": [2, 2, 1, 0], "next_topic": "Oven safety",
                                          "comments": "More pictures"}).json()["ok"]
    assert anon.post("/api/survey", json={"ticket": ticket, "answers": [2, 2, 1, 0]}).status_code == 400
    with db.connect() as con:
        rows = db.survey_rows(con)
        assert rows == [{"lesson_id": "forklifts", "month": dt.date.today().strftime("%Y-%m"), "answers": [2, 2, 1, 0],
                         "next_topic": "Oven safety", "comments": "More pictures"}]
        assert "worker" not in str(con.execute("PRAGMA table_info(survey_answers)").fetchall())

    # Someone else can't see Sam's record or certificate.
    other = TestClient(app)
    assert other.get(r["certificate"], follow_redirects=False).status_code == 303
    # Not signed in: the lesson is only a preview and nothing is saved.
    assert "saved" in other.post("/api/lesson/forklifts/finish", json={"answers": right}).json() and \
        not other.post("/api/lesson/forklifts/finish", json={"answers": right}).json()["saved"]
    with db.connect() as con:
        assert db.lesson_state(con, sam["id"], lesson)[0] == "done"
        # A year and a bit later it's due as a refresher.
        later = dt.date.today() + dt.timedelta(days=400)
        assert db.lesson_state(con, sam["id"], lesson, today=later)[0] == "refresh"


def _fake_get(cfr_dates, ccr_text="§3314. Lockout rule text"):
    class R:
        def __init__(self, data=None, text="", status=200):
            self._data, self.content, self.status_code = data, text.encode(), status

        def json(self):
            return self._data

        def raise_for_status(self):
            if self.status_code >= 400:
                raise rules.requests.HTTPError(str(self.status_code))

    def get(url, params=None, **kw):
        if "ecfr.gov" in url:
            sec = params["section"]
            if sec not in cfr_dates:
                return R({"content_versions": []})
            return R({"content_versions": [{"identifier": sec, "name": f"§ {sec} Name.", "amendment_date": cfr_dates[sec], "removed": False}]})
        if "federalregister" in url:
            return R({"results": [{"title": "A rule", "publication_date": "2026-01-01", "html_url": "https://x", "type": "Rule"}]})
        sec = url.rsplit("/", 1)[1].split(".")[0]
        return R(text=f"<title>California Code of Regulations, Title 8, Section {sec}. Name.</title>{ccr_text.replace('3314', sec)}")
    return get


def test_rule_change_flags_lesson_and_review_clears_it(client, env):
    lesson = content.load_lessons()["lockout-tagout"]
    old = {"1910.147": "2017-01-01"}
    rules.run_check({"lockout-tagout": lesson}, get=_fake_get(old))
    assert rules.lesson_flags(lesson, rules.load_status()) == []

    # The federal rule is amended after the lesson was reviewed, and California's text changes.
    rules.run_check({"lockout-tagout": lesson}, get=_fake_get({"1910.147": "2099-01-01"}, "§3314. New text"))
    flags = rules.lesson_flags(lesson, rules.load_status())
    assert {f["ref"] for f in flags} == {"29 CFR 1910.147", "8 CCR 3314"}
    assert "Needs your review" in client.get("/manage/topics").text

    client.post("/manage/lesson/lockout-tagout/reviewed")
    lesson = content.load_lessons()["lockout-tagout"]
    assert lesson["reviewed_on"][:10] == dt.date.today().isoformat()
    remaining = rules.lesson_flags(lesson, rules.load_status())
    assert [f["ref"] for f in remaining] == ["29 CFR 1910.147"]  # still in the future


def test_missing_rule_and_failed_check(env):
    lesson = content.load_lessons()["lockout-tagout"]
    rules.run_check({"lockout-tagout": lesson}, get=_fake_get({}))
    flags = rules.lesson_flags(lesson, rules.load_status())
    assert any("wasn't found" in f["why"] for f in flags)

    rules.run_check({"lockout-tagout": lesson}, get=_fake_get({"1910.147": "2017-01-01"}))

    def broken(*a, **k):
        raise rules.requests.ConnectionError("down")
    status = rules.run_check({"lockout-tagout": lesson}, get=broken)
    s = status["rules"]["29 CFR 1910.147"]
    assert s["changed_on"] == "2017-01-01" and s["found"] and "down" in s["last_error"]


def _deck(**over):
    d = {
        "title": "Allergen cross-contact",
        "summary": "s",
        "video": "https://www.youtube.com/watch?v=abcdefghijk",
        "roles": ["mixing", "baking"],
        "slides": [
            {"type": "question", "q": "First?", "choices": ["yes", "no"], "answer": 0, "why": "because", "at": 30},
            {"type": "reading", "heading": "One", "text": "Text one."},
            {"type": "reading", "heading": "Two", "text": "Text two."},
            {"type": "question", "q": "Second?", "choices": ["a", "b"], "answer": 1, "why": "", "at": None},
        ],
        "citations": ["21 cfr 117.35"],
        "sources": [{"title": "FDA", "url": "https://www.fda.gov"}],
        "retake": True,
    }
    d.update(over)
    return d


def test_manager_edits_a_lesson(client):
    page = client.get("/manage/lesson/allergens").text
    assert "Allergen cross-contact" in page and "window.DECK" in page
    r = client.post("/api/manage/lesson/allergens", json=_deck())
    assert r.status_code == 200 and r.json()["id"] == "allergens"
    l = content.load_lessons()["allergens"]
    assert l["roles"] == ["mixing", "baking"] and l["questions"][0]["at"] == 30
    assert "after_section" not in l["questions"][0]
    assert l["questions"][1]["after_section"] == 1 and l["citations"] == ["21 CFR 117.35"]
    assert [s["heading"] for s in l["sections"]] == ["One", "Two"]
    assert l["version"] == dt.date.today().isoformat()

    bad = _deck(slides=[{"type": "question", "q": "", "choices": ["a"], "answer": 3, "at": None}], citations=["29 CFR 99"], title="")
    r = client.post("/api/manage/lesson/allergens", json=bad)
    assert r.status_code == 400
    errors = r.json()["errors"]
    by_slide = {e["slide"] for e in errors}
    assert {0, 1, "rules", None} <= by_slide
    assert any("29 CFR 99" in e["error"] for e in errors)
    assert content.load_lessons()["allergens"]["title"] == "Allergen cross-contact"  # nothing saved

    r = client.post("/api/manage/lesson/new", json=_deck(title="Brand new lesson"))
    assert r.json()["id"] == "brand-new-lesson" and "brand-new-lesson" in content.load_lessons()


def test_slide_keeps_its_own_id():
    """A reading slide's id (which its video edit is kept under) survives saving; junk ids are dropped."""
    sections, _, errors = editor.from_slides([{"type": "reading", "heading": "A", "text": "x", "key": "abc123def"},
                                              {"type": "reading", "heading": "B", "text": "y", "key": "../x"}])
    assert not errors and sections[0]["key"] == "abc123def" and "key" not in sections[1]
    assert editor.to_slides({"sections": sections})[0]["key"] == "abc123def"


def test_slides_round_trip_every_lesson(any_lessons):
    for lid, lesson in content.load_lessons().items():
        slides = editor.to_slides(lesson)
        sections, questions, errors = editor.from_slides(slides)
        assert not errors, (lid, errors)
        assert sections == [{"heading": s["heading"], "text": s["text"].strip() + "\n" if s["text"].strip() else "",
                             **({"images": s["images"]} if s.get("images") else {}),
                             **({"key": s["key"]} if s.get("key") else {})} for s in lesson["sections"]], lid
        assert [(q["q"], q["choices"], q["answer"], q.get("at")) for q in questions] == \
               [(q["q"], q["choices"], q["answer"], q.get("at")) for q in lesson["questions"]], lid
        last = len(sections) - 1
        for want, got in zip(lesson["questions"], questions):
            if want.get("after_section") is not None:
                assert got["after_section"] == want["after_section"], (lid, want["q"])
            elif want.get("at") is None:
                assert got["after_section"] == last, (lid, want["q"])   # end questions follow the last reading


def test_pages_render(client):
    for path in ["/", "/manage", "/manage/dashboard", "/manage/requirements", "/manage/topics", "/manage/translations",
                 "/lesson/indoor-heat",
                 "/manage/lesson/new", "/health"]:
        assert client.get(path).status_code == 200, path


def test_broken_lesson_file_does_not_break_app(client, env):
    (env / "content" / "lessons" / "oops.yaml").write_text("title: [unclosed")
    assert client.get("/manage").status_code == 200
    assert "oops.yaml" in client.get("/manage/topics").text


def test_status_file_is_valid_json():
    if rules.STATUS_PATH.exists():
        json.loads(rules.STATUS_PATH.read_text())


def test_video_upload_plays_and_replaces(client, env):
    clip = b"\x00\x00\x00\x18ftypmp42" + b"x" * 5000
    r = client.post("/api/manage/video", data={"lesson": "allergens"}, files={"video_file": ("Floor clip.MP4", clip, "video/mp4")})
    assert r.status_code == 200
    video = r.json()["video"]
    assert video.startswith("/media/allergens-") and video.endswith(".mp4")
    assert client.post("/api/manage/lesson/allergens", json=_deck(video=video)).status_code == 200
    assert content.load_lessons()["allergens"]["video"] == video

    full = client.get(video)
    assert full.status_code == 200 and full.content == clip and full.headers["content-type"] == "video/mp4"
    part = client.get(video, headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100  # seeking works

    # Replacing the video keeps the old one in the media library (managers delete it there).
    time.sleep(1.1)
    new = client.post("/api/manage/video", data={"lesson": "allergens"}, files={"video_file": ("second.webm", b"webm" * 100, "video/webm")}).json()["video"]
    client.post("/api/manage/lesson/allergens", json=_deck(video=new))
    assert new != video and new.endswith(".webm")
    assert client.get(video).status_code == 200
    lib = {x["link"]: x for x in client.get("/api/manage/media").json()["items"]}
    assert lib[video]["label"] == "Floor clip" and lib[video]["used_by"] == [] and lib[new]["used_by"]

    # Clearing the video keeps it too.
    client.post("/api/manage/lesson/allergens", json=_deck(video=""))
    assert content.load_lessons()["allergens"]["video"] == ""
    assert client.get(new).status_code == 200


def test_unsaved_uploads_are_swept(client, env):
    from trainer import app as app_module
    stale = app_module.media_dir() / "abandoned-20200101000000.mp4"
    stale.write_bytes(b"x")
    os.utime(stale, (0, 0))
    fresh = app_module.media_dir() / "in-progress-20990101000000.mp4"
    fresh.write_bytes(b"x")
    client.post("/api/manage/lesson/allergens", json=_deck(video=""))
    assert not stale.exists() and fresh.exists()
    kept = app_module.media_dir() / "kept-20200101000000.mp4"   # in the media library: never swept
    kept.write_bytes(b"x")
    os.utime(kept, (0, 0))
    app_module._library_add("/media/" + kept.name, "Kept")
    client.post("/api/manage/lesson/allergens", json=_deck(video=""))
    assert kept.exists()


def test_video_upload_rejects_non_videos(client):
    r = client.post("/api/manage/video", files={"video_file": ("notes.pdf", b"%PDF", "application/pdf")})
    assert r.status_code == 400 and "notes.pdf" in r.json()["error"]
    r = client.post("/api/manage/lesson/allergens", json=_deck(video="javascript:alert(1)"))
    assert r.status_code == 400 and "should start with https://" in r.text
    for bad in ["../training.db", "x.exe", "a.mp4.exe"]:
        assert client.get(f"/media/{bad}").status_code == 404


# ---- Claude panel (the API is stubbed; no key or network needed) --------------

class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Stream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


class _FakeClient:
    """Replays canned responses and remembers what was sent."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = self
        self.messages = self

    def stream(self, **kw):
        self.calls.append(kw)
        return _Stream(self.responses.pop(0))


DRAFT = {
    "title": "Forklift checks", "summary": "Check the truck before each shift.", "roles": ["packaging", "chefs"],
    "slides": [
        {"type": "reading", "heading": "Before you drive", "text": "Check the forks, horn and brakes.", "q": "", "choices": [], "answer": 0, "why": ""},
        {"type": "question", "heading": "", "text": "", "q": "When?", "choices": ["Each shift", "Weekly", " "], "answer": 0, "why": "The rule says so."},
    ],
    "citations": ["29 CFR 1910.178", "OSHA forklift rule"],
    "sources": [{"title": "OSHA", "url": "https://www.osha.gov/powered-industrial-trucks"}, {"title": "bad", "url": "javascript:x"}],
}


def test_research_turns_claude_draft_into_slides(env):
    fake = _FakeClient([
        _Block(stop_reason="pause_turn", content=[_Block(type="server_tool_use", id="s1"),
                                                   _Block(type="web_fetch_tool_result", content=_Block(url="https://www.osha.gov/x"))]),
        _Block(stop_reason="tool_use", content=[_Block(type="tool_use", id="t1", name="propose_lesson", input=DRAFT)]),
        _Block(stop_reason="end_turn", content=[_Block(type="text", text="Drafted a forklift lesson. Add your plant's checklist.")]),
    ])
    out = research.run([{"role": "user", "text": "Forklift pre-shift checks"}],
                       {"title": "Old", "slides": [{"type": "reading", "heading": "h", "text": "t"}]}, client=fake)
    assert out["reply"].startswith("Drafted a forklift lesson")
    assert out["searched"] == ["https://www.osha.gov/x"]
    l = out["lesson"]
    assert l["roles"] == ["packaging"]                         # unknown role dropped
    assert l["citations"] == ["29 CFR 1910.178"]               # unparseable citation dropped
    assert [s["url"] for s in l["sources"]] == ["https://www.osha.gov/powered-industrial-trucks"]
    assert l["slides"][1]["choices"] == ["Each shift", "Weekly"]
    assert not editor.from_slides(l["slides"])[2]              # the draft is a valid deck
    first = fake.calls[0]
    assert "<current_draft>" in first["messages"][0]["content"] and first["model"] == research.MODEL
    assert {t["name"] for t in first["tools"]} == {"web_search", "web_fetch", "propose_lesson"}
    assert len(fake.calls) == 3 and fake.calls[2]["messages"][-1]["content"][0]["type"] == "tool_result"


def test_research_api(client, env, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDE_COMMAND", "no-such-claude")
    monkeypatch.setenv("HOME", str(env))
    assert research.mode() is None
    assert client.post("/api/manage/research", json={"history": [{"role": "user", "text": "hi"}]}).status_code == 503
    assert "isn't installed" in client.get("/manage/lesson/new").text   # setup steps shown

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    seen = {}

    def fake_run(history, draft):
        seen["history"] = history
        return {"reply": "ok", "lesson": {"citations": ["29 CFR 1910.178"], "slides": []}, "searched": []}

    monkeypatch.setattr(research, "run", fake_run)
    monkeypatch.setattr(rules, "check_rule", lambda c, old, get=None: {"found": True, "name": "Powered industrial trucks."})
    r = client.post("/api/manage/research", json={"history": [{"role": "user", "text": "forklifts"}], "draft": None}).json()
    assert r["checks"] == [{"ref": "29 CFR 1910.178", "url": "https://www.ecfr.gov/current/title-29/section-1910.178",
                            "found": True, "name": "Powered industrial trucks.", "error": ""}]
    assert seen["history"] == [{"role": "user", "text": "forklifts"}]
    assert "Research with Claude" in client.get("/manage/lesson/new").text


def test_claude_panel_reads_what_the_videos_say(client, env, monkeypatch):
    """Claude can't watch videos: it gets the voiceover lines typed for a made video, else a transcript."""
    from trainer import app as app_module, project
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    made = app_module.media_dir() / "vt-20990101000000.mp4"
    plain = app_module.media_dir() / "vt-20990101000001.mp4"
    _clip(made, 1)
    _clip(plain, 1)
    app_module._library_add("/media/" + made.name, "Made video")
    lib = app_module._library()
    lib[made.name]["project"] = {"clips": [], "lines": [{"text": "Turn the machine off first.", "at": 2, "dur": 2}]}
    app_module._library_write(lib)
    monkeypatch.setattr(project, "can_transcribe", lambda: True)
    calls = []
    monkeypatch.setattr(project, "transcribe", lambda path, model_dir: calls.append(path) or [(3, "Spray the knife twice.")])
    seen = {}
    monkeypatch.setattr(research, "run", lambda history, draft: seen.update(draft=draft) or {"reply": "ok", "lesson": None, "searched": []})
    draft = {"title": "Scoring", "video": "/media/" + made.name,
             "slides": [{"type": "reading", "heading": "Scoring Knife", "text": "", "images": ["/media/" + plain.name, "/media/x.jpg"]}]}
    for _ in range(2):
        assert client.post("/api/manage/research", json={"history": [{"role": "user", "text": "write notes"}], "draft": draft}).status_code == 200
    vt = seen["draft"]["video_transcripts"]
    assert vt[0]["where"] == "Lesson video" and "[0:02] Turn the machine off first." in vt[0]["transcript"]
    assert vt[1]["where"] == "Slide 1 (Scoring Knife)" and "[0:03] Spray the knife twice." in vt[1]["transcript"]
    assert len(calls) == 1          # kept, not written again
    assert "<video_transcripts>" in research._with_draft("hi", seen["draft"]) and "Spray the knife" in research._with_draft("hi", seen["draft"])


class _Done:
    def __init__(self, stdout, returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def test_research_through_claude_code(env, monkeypatch):
    """Without an API key the panel uses Claude Code, signed in with the user's Claude account."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    seen = {}

    def runner(cmd, input, **kw):
        seen.update(cmd=cmd, input=input, cwd=kw["cwd"])
        return _Done(json.dumps({"is_error": False, "result": "...",
                                 "structured_output": {"reply": "Drafted it.", "has_lesson": True, "lesson": DRAFT}}))

    out = research.run_cli([{"role": "user", "text": "Forklifts"}, {"role": "assistant", "text": "Done."},
                            {"role": "user", "text": "Add a question"}], {"title": "Old", "slides": []},
                           command="claude", runner=runner)
    assert out["reply"] == "Drafted it." and out["lesson"]["citations"] == ["29 CFR 1910.178"]
    cmd = seen["cmd"]
    assert cmd[cmd.index("--tools") + 1] == "WebSearch,WebFetch" and "--json-schema" in cmd
    assert "<conversation_so_far>" in seen["input"] and seen["input"].rstrip().endswith("</current_draft>")
    assert seen["cwd"] != str(content.ROOT)

    # A question with no lesson.
    ok = lambda cmd, input, **kw: _Done(json.dumps({"structured_output": {"reply": "Yes.", "has_lesson": False, "lesson": DRAFT}}))
    assert research.run_cli([{"role": "user", "text": "q"}], None, command="claude", runner=ok)["lesson"] is None

    # Not signed in: the panel shows the sign-in steps.
    signed_out = lambda cmd, input, **kw: _Done('{"is_error": true, "result": "Not logged in · Please run /login"}', 1)
    with pytest.raises(research.NotSignedIn):
        research.run_cli([{"role": "user", "text": "q"}], None, command="claude", runner=signed_out)


# ---- Attaching a training file in the Claude panel ---------------------------

def _zip(files):
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


def _pptx():
    slide = ('<p:sld xmlns:a="a" xmlns:p="p"><a:p><a:r><a:t>Mixer lockout</a:t></a:r></a:p>'
             '<a:p><a:r><a:t>Step 1: </a:t></a:r><a:r><a:t>shut off &amp; lock</a:t></a:r></a:p></p:sld>')
    return _zip({"ppt/slides/slide2.xml": slide.replace("Mixer lockout", "Second slide"),
                 "ppt/slides/slide1.xml": slide,
                 "ppt/notesSlides/notesSlide1.xml": '<a:p><a:t>Say this out loud</a:t></a:p>'})


def _pdf(text):
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>stream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)


def test_attach_reads_training_files(client):
    from trainer import extract
    r = client.post("/api/manage/attach", files={"file": ("Old LOTO training.pptx", _pptx())}).json()
    assert r["kind"] == "PowerPoint" and r["parts"] == 2
    assert r["text"].index("Mixer lockout") < r["text"].index("Second slide")          # slide order
    assert "Step 1: shut off & lock" in r["text"] and "[Speaker notes] Say this out loud" in r["text"]

    doc = _zip({"word/document.xml": '<w:p><w:r><w:t>Wash hands</w:t></w:r></w:p><w:p><w:r><w:t xml:space="preserve">for 20 s</w:t></w:r></w:p>'})
    assert client.post("/api/manage/attach", files={"file": ("sop.docx", doc)}).json()["text"] == "Wash hands\nfor 20 s"
    assert "Allergen changeover" in extract.extract("sop.pdf", _pdf("Allergen changeover"))["text"]

    for name, data, words in [("old.ppt", b"x", "Save As .pptx"), ("pic.png", b"x", "can't be read"),
                              ("broken.pptx", b"not a zip", "couldn't be opened"),
                              ("empty.pptx", _zip({"ppt/slides/slide1.xml": "<a:p></a:p>"}), "no text")]:
        r = client.post("/api/manage/attach", files={"file": (name, data)})
        assert r.status_code == 400 and words in r.json()["error"], name


def test_attached_file_reaches_claude(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    seen = {}
    monkeypatch.setattr(research, "run", lambda history, draft: seen.update(history=history) or {"reply": "ok", "lesson": None, "searched": []})
    text = 'Make a lesson\n\n<attached_file name="LOTO.pptx" kind="PowerPoint">\n--- Slide 1 ---\nMixer lockout\n</attached_file>'
    client.post("/api/manage/research", json={"history": [{"role": "user", "text": text}]})
    assert "Mixer lockout" in seen["history"][-1]["text"]
    assert "<attached_file>" in research.SYSTEM


def test_pictures_on_reading_slides(client, env):
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 200
    r = client.post("/api/manage/image", data={"lesson": "allergens"}, files={"image_file": ("Mixer guard.PNG", png, "image/png")})
    image = r.json()["image"]
    assert image.startswith("/media/allergens-") and image.endswith(".png")
    got = client.get(image)
    assert got.status_code == 200 and got.headers["content-type"] == "image/png"

    deck = _deck()
    deck["slides"][1]["images"] = [image]
    assert client.post("/api/manage/lesson/allergens", json=deck).status_code == 200
    lesson = content.load_lessons()["allergens"]
    assert lesson["sections"][0]["images"] == [image] and "images" not in lesson["sections"][1]
    assert editor.to_slides(lesson)[1]["images"] == [image]
    assert image in client.get("/lesson/allergens").text              # the player gets it

    # Several pictures on one slide keep their order; an old single `image` still loads.
    second = client.post("/api/manage/image", data={"lesson": "allergens"}, files={"image_file": ("b.png", png, "image/png")}).json()["image"]
    deck["slides"][1]["images"] = [second, image, second]
    assert client.post("/api/manage/lesson/allergens", json=deck).status_code == 200
    assert content.load_lessons()["allergens"]["sections"][0]["images"] == [second, image]
    old = {"id": "x", "title": "X", "sections": [{"heading": "h", "text": "t", "image": image}]}
    content._normalize(old, content.Path("x.yaml"))
    assert old["sections"][0]["images"] == [image] and "image" not in old["sections"][0]
    deck["slides"][1]["images"] = [image]
    assert client.post("/api/manage/lesson/allergens", json=deck).status_code == 200

    # Kept by the upload sweep while a lesson uses it, even when old.
    from trainer import app as app_module
    os.utime(app_module.media_dir() / image.rsplit("/", 1)[1], (0, 0))
    client.post("/api/manage/lesson/allergens", json=deck)
    assert client.get(image).status_code == 200

    bad = client.post("/api/manage/image", files={"image_file": ("notes.pdf", b"%PDF", "application/pdf")})
    assert bad.status_code == 400 and ".jpg" in bad.json()["error"]
    deck["slides"][1]["images"] = ["javascript:alert(1)"]
    assert client.post("/api/manage/lesson/allergens", json=deck).status_code == 400


def test_videos_on_reading_slides(client, env):
    """Any reading slide can show videos as well as pictures (not only the lesson's cover video)."""
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 200
    image = client.post("/api/manage/image", data={"lesson": "allergens"}, files={"image_file": ("a.png", png, "image/png")}).json()["image"]
    clip = client.post("/api/manage/video", data={"lesson": "allergens"}, files={"video_file": ("step.webm", b"webm" * 100, "video/webm")}).json()["video"]
    deck = _deck()
    deck["slides"][1]["images"] = [clip, image]
    assert client.post("/api/manage/lesson/allergens", json=deck).status_code == 200
    assert content.load_lessons()["allergens"]["sections"][0]["images"] == [clip, image]
    assert clip in client.get("/lesson/allergens").text
    # A slide can be just a video: heading and text are optional, but a slide can't be empty.
    deck["slides"][1].update(heading="", text="", images=[clip])
    assert client.post("/api/manage/lesson/allergens", json=deck).status_code == 200, "video-only slide"
    assert content.load_lessons()["allergens"]["sections"][0]["images"] == [clip]
    assert client.get("/lesson/allergens").status_code == 200
    deck["slides"][1].update(images=[])
    r = client.post("/api/manage/lesson/allergens", json=deck)
    assert r.status_code == 400 and "empty" in r.text
    deck["slides"][1].update(images=[clip])
    client.post("/api/manage/lesson/allergens", json=deck)
    r = client.post("/api/manage/media/delete", json={"link": clip})   # in use, so it can't be deleted
    assert r.status_code == 400 and "still used" in r.json()["error"]


def test_iphone_photos_become_jpg(client, env):
    """iPhone .heic photos (upload or Google Drive) are kept as JPG so every browser shows them."""
    import io
    import pillow_heif
    from PIL import Image
    pillow_heif.register_heif_opener()
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 30, 30)).save(buf, format="HEIF")
    heic = buf.getvalue()
    r = client.post("/api/manage/image", data={"lesson": "allergens"}, files={"image_file": ("IMG_5818.HEIC", heic, "")})
    assert r.status_code == 200, r.text
    link = r.json()["image"]
    assert link.endswith(".jpg") and client.get(link).content[:2] == b"\xff\xd8"

    from trainer import app as app_module

    class Resp:
        status_code = 200
        headers = {"Content-Type": "image/heic", "Content-Disposition": 'attachment; filename="IMG_5819.HEIC"'}
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def iter_content(self, n): yield heic
    kind, link = app_module._drive_copy("", "allergens", get=lambda *a, **k: Resp(), file_id="A" * 25, token="t")
    assert kind == "image" and link.endswith(".jpg") and client.get(link).content[:2] == b"\xff\xd8"
    bad = client.post("/api/manage/image", files={"image_file": ("x.heic", b"\x00\x00\x00\x18ftypheic" + b"0" * 50, "image/heic")})
    assert bad.status_code == 400 and "iPhone photo" in bad.json()["error"]


def test_company_procedure_lessons(client, env):
    """An in-house procedure needs no government rule: company rules in plain words are enough,
    and plain-words rules typed into the rule box move to the company rules."""
    deck = _deck()
    deck["citations"] = ["Be very gentle with the knife cleaning", "Always wear cut gloves"]
    deck["sources"] = []
    assert client.post("/api/manage/lesson/allergens", json=deck).status_code == 200
    lesson = content.load_lessons()["allergens"]
    assert lesson["citations"] == [] and lesson["company_rules"] == ["Be very gentle with the knife cleaning", "Always wear cut gloves"]
    assert editor.editor_payload(lesson)["company_rules"] == lesson["company_rules"]
    assert "Always wear cut gloves" in client.get("/lesson/allergens").text
    deck.update(citations=["29 CFR 1910.147"], company_rules=["Only Asiago requires soap cleaning in addition to sanitizer"])
    assert client.post("/api/manage/lesson/allergens", json=deck).status_code == 200
    lesson = content.load_lessons()["allergens"]
    assert lesson["citations"] == ["29 CFR 1910.147"] and len(lesson["company_rules"]) == 1
    deck.update(citations=["29 CFR 1910.14 7"], company_rules=[])          # a mistyped citation is still caught
    assert client.post("/api/manage/lesson/allergens", json=deck).status_code == 400
    deck.update(citations=[], company_rules=[])
    r = client.post("/api/manage/lesson/allergens", json=deck)
    assert r.status_code == 400 and "company" in r.text


def test_voiceover_lines_start_at_their_times(tmp_path):
    """Each voiceover line is spoken on its own and starts at the time set for it; untimed lines follow."""
    from trainer import tts

    def fake_speak(text, voice, speed, out_dir, model_dir):
        name = f"{abs(hash(text)) % 10**20:020d}.m4a"
        (out_dir / name).write_bytes(b"x")
        return name, 2.0
    cmds = []

    class Done:
        returncode = 0

    def runner(cmd, **kw):
        cmds.append(cmd)
        open(cmd[-1], "wb").write(b"m4a")
        return Done()
    name, secs, placed = tts.track([{"text": "Lock it out.", "at": 1.5}, {"text": "Then test it.", "at": None},
                                    {"text": "  "}, {"text": "Done.", "at": 10}], "en-f", "normal", tmp_path, tmp_path,
                                   runner=runner, speak_fn=fake_speak)
    assert [p["at"] for p in placed] == [1.5, 3.8, 10.0] and secs == 12.0
    graph = cmds[0][cmds[0].index("-filter_complex") + 1]
    assert "adelay=1500" in graph and "adelay=3800" in graph and "adelay=10000" in graph and "amix=inputs=3" in graph
    assert (tmp_path / name).is_file()
    one, _, _ = tts.track([{"text": "Just this."}], "en-f", "normal", tmp_path, tmp_path, runner=runner, speak_fn=fake_speak)
    assert len(cmds) == 1 and (tmp_path / one).is_file()       # one line at the start: no mixing needed


def test_spanish_and_vietnamese_use_their_own_voices(tmp_path, monkeypatch):
    """Spanish (Kokoro) and Vietnamese (VieNeu) lines are made together in one short process;
    when that process can't make a line, Piper reads it instead."""
    import json
    from trainer import tts, video
    monkeypatch.setattr(tts, "engine_ready", lambda engine: True)
    monkeypatch.setattr(video, "probe", lambda path: {"duration": 1.5})
    monkeypatch.setattr(tts, "_voice", lambda key, model_dir: _FakeVoice())
    jobs = []

    class Done:
        returncode = 0

    def runner(cmd, **kw):
        if "trainer.tts_engines" in cmd:
            job = json.loads(open(cmd[-1]).read())
            jobs.append(job)
            for it in job["items"]:
                if "fail" not in it["text"]:
                    open(it["wav"], "wb").write(b"w" * 2000)
        else:
            open(cmd[-1], "wb").write(b"m4a")
        return Done()
    name, secs, placed = tts.track([{"text": "Apague la máquina.", "at": 0}, {"text": "Use guantes.", "at": 2}],
                                   "es", "slow", tmp_path, tmp_path, runner=runner)
    assert len(jobs) == 1 and jobs[0]["engine"] == "kokoro" and jobs[0]["voice"] == "ef_dora" and jobs[0]["speed"] == 0.85
    assert [it["text"] for it in jobs[0]["items"]] == ["Apague la máquina.", "Use guantes."]
    tts.speak("Apague la máquina.", "es", "slow", tmp_path, tmp_path, runner=runner)
    assert len(jobs) == 1                                        # already made: no new process
    got, _ = tts.speak("Tắt máy.", "vi", "normal", tmp_path, tmp_path, runner=runner)
    assert jobs[-1]["engine"] == "vieneu" and got == tts.file_name("Tắt máy.", "vi", "normal", "vieneu")
    got, _ = tts.speak("fail here", "vi", "normal", tmp_path, tmp_path, runner=runner)
    assert got == tts.file_name("fail here", "vi", "normal")    # Piper stepped in


def test_static_links_are_versioned(client):
    from trainer.app import templates
    v = templates.env.globals["asset_v"]
    assert f'/static/style.css?v={v}' in client.get("/manage").text
    assert f'/static/editor.js?v={v}' in client.get("/manage/lesson/new").text


def test_attached_pictures_go_to_claude_and_onto_slides(client, monkeypatch):
    photo = b"\x89PNG\r\n\x1a\n" + b"p" * 5000
    rels = ('<Relationships><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            'Target="../media/image1.png"/><Relationship Id="rId3" Type="x/image" Target="../media/tiny.png"/></Relationships>')
    slide = '<p:sld><a:p><a:r><a:t>Mixer guard</a:t></a:r></a:p><p:pic><a:blip r:embed="rId2"/></p:pic><a:blip r:embed="rId3"/></p:sld>'
    deck = _zip({"ppt/slides/slide1.xml": slide, "ppt/slides/_rels/slide1.xml.rels": rels,
                 "ppt/media/image1.png": photo, "ppt/media/tiny.png": b"\x89PNG" + b"t" * 50})
    r = client.post("/api/manage/attach", files={"file": ("guards.pptx", deck)}).json()
    assert r["pictures"] == 1                                           # the tiny icon is skipped
    link = re.search(r"\[Picture: (/media/attached-[a-z0-9-]+\.png)\]", r["text"]).group(1)
    assert r["text"].index("Mixer guard") < r["text"].index(link)
    assert client.get(link).content == photo

    doc = _zip({"word/document.xml": '<w:p><w:r><w:t>Wear gloves</w:t></w:r><w:drawing><a:blip r:embed="rId9"/></w:drawing></w:p>',
                "word/_rels/document.xml.rels": '<Relationships><Relationship Id="rId9" Type="a/image" Target="media/glove.jpeg"/></Relationships>',
                "word/media/glove.jpeg": b"\xff\xd8" + b"g" * 4000})
    r = client.post("/api/manage/attach", files={"file": ("ppe.docx", doc)}).json()
    assert r["pictures"] == 1 and re.fullmatch(r"Wear gloves\n\[Picture: /media/attached-[a-z0-9-]+\.jpeg\]", r["text"])

    # Claude's draft keeps real picture links and loses made-up or broken ones.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    slides = [{"type": "reading", "heading": "Guards", "text": "Keep guards on.", "images": [link]},
              {"type": "reading", "heading": "Other", "text": "x", "images": ["https://evil.example/a.png"]},
              {"type": "reading", "heading": "Gone", "text": "x", "images": ["/media/attached-nope.png"]}]
    lesson = research._finish({"title": "T", "summary": "S", "roles": ["all"], "slides": slides, "citations": [], "sources": []}, [])
    assert [s["images"] for s in lesson["slides"]] == [[link], [], ["/media/attached-nope.png"]]
    monkeypatch.setattr(research, "run", lambda history, draft: {"reply": "Done.", "lesson": lesson, "searched": []})
    out = client.post("/api/manage/research", json={"history": [{"role": "user", "text": "go"}]}).json()
    assert [s["images"] for s in out["lesson"]["slides"]] == [[link], [], []] and "left off" in out["reply"]
    assert "images" in research.lesson_tool([])["input_schema"]["properties"]["slides"]["items"]["required"]


def test_commons_photo_license_is_checked_by_the_app(client):
    from trainer import app as app_module
    page = "https://commons.wikimedia.org/wiki/File:Forklift_tires.jpg"
    assert research.PICTURE_LINK.match(page) and not research.PICTURE_LINK.match("https://upload.wikimedia.org.evil.com/x.jpg")
    assert not research.PICTURE_LINK.match("https://example.com/wiki/File:x.jpg")

    class R:
        def __init__(self, data=None, content=b""):
            self.status_code, self._data, self.content = 200, data, content

        def json(self):
            return self._data

    def commons(license):
        def get(url, params=None, **k):
            if params:
                assert params["titles"] == "File:Forklift tires.jpg"
                meta = {"LicenseShortName": {"value": license}, "Artist": {"value": '<a href="x">S. John</a>'}}
                return R({"query": {"pages": [{"imageinfo": [{"mime": "image/jpeg", "url": "https://upload.wikimedia.org/o.jpg",
                                                               "thumburl": "https://upload.wikimedia.org/t.jpg", "extmetadata": meta}]}]}})
            assert url == "https://upload.wikimedia.org/t.jpg"
            return R(content=b"\xff\xd8jpeg")
        return get

    lesson = {"slides": [{"type": "reading", "images": [page]}], "sources": [{"title": "tires", "url": page}]}
    assert app_module._settle_pictures(lesson, get=commons("CC BY-SA 3.0")) == 0
    link = lesson["slides"][0]["images"][0]
    assert re.fullmatch(r"/media/commons-[a-z0-9-]+\.jpg", link) and client.get(link).content == b"\xff\xd8jpeg"
    assert lesson["sources"] == [{"title": "Photo: Forklift tires, by S. John, CC BY-SA 3.0", "url": page}]
    for bad in ("CC BY-NC-SA 4.0", "All rights reserved", "CC BY-ND 4.0"):
        lesson = {"slides": [{"type": "reading", "images": [page]}], "sources": []}
        assert app_module._settle_pictures(lesson, get=commons(bad)) == 1 and lesson["slides"][0]["images"] == [], bad


def test_manager_setup_and_sign_in(anon):
    from trainer import auth
    assert anon.get("/manage", follow_redirects=False).headers["location"] == "/manage/signin?next=/manage"
    assert anon.get("/manage/signin", follow_redirects=False).headers["location"] == "/manage/setup"
    pat_form = {"name": "Pat", "email": "Pat@Bakery.example", "pin": "135790", "pin2": "135790"}
    assert "bad=pin" in anon.post("/manage/setup", data={**pat_form, "pin": "1234", "pin2": "1234"}, follow_redirects=False).headers["location"]
    assert "bad=email" in anon.post("/manage/setup", data={**pat_form, "email": "pat"}, follow_redirects=False).headers["location"]
    anon.post("/manage/setup", data=pat_form)
    assert anon.get("/manage/dashboard").status_code == 200
    anon.post("/signout")
    assert anon.get("/manage/setup", follow_redirects=False).headers["location"] == "/manage/signin"   # only once
    with db.connect() as con:
        pat = db.managers(con)[0]
        assert "135790" not in pat["pin_hash"] and pat["email"] == "pat@bakery.example"
    page = anon.get("/manage/signin").text
    assert 'name="email"' in page and "Pat" not in page                         # no list of manager names
    r = anon.post("/manage/signin", data={"email": "PAT@bakery.example ", "pin": "135790", "next": "//evil.example"}, follow_redirects=False)
    assert r.headers["location"] == "/manage/dashboard"
    assert "wrong=1" in anon.post("/manage/signin", data={"email": "nobody@bakery.example", "pin": "135790"}, follow_redirects=False).headers["location"]
    assert "wrong=1" in anon.post("/manage/signin", data={"manager": pat["id"], "pin": "135790"}, follow_redirects=False).headers["location"]  # has an email: use it

    # Another manager: email required and unique; any manager can reset a forgotten PIN.
    assert "manager_bad=taken" in anon.post("/manage/managers", data={"name": "Kim", "email": "pat@bakery.example", "pin": "112233"}, follow_redirects=False).headers["location"]
    anon.post("/manage/managers", data={"name": "Kim", "email": "kim@bakery.example", "pin": "112233"})
    with db.connect() as con:
        kim = db.manager_by_email(con, "kim@bakery.example")
    anon.post(f"/manage/managers/{kim['id']}/pin", data={"pin": "445566"})
    other = TestClient(app)
    assert other.post("/manage/signin", data={"email": "kim@bakery.example", "pin": "445566"}, follow_redirects=False).headers["location"] == "/manage/dashboard"

    # A manager made before emails were asked for picks their name, then adds an email.
    with db.connect() as con:
        old = db.add_manager(con, "Lee", auth.hash_pin("778899"))
    legacy = TestClient(app)
    assert "Lee" in legacy.get("/manage/signin").text
    legacy.post("/manage/signin", data={"manager": old, "pin": "778899"})
    assert "Add your work email" in legacy.get("/manage").text
    legacy.post(f"/manage/managers/{old}/email", data={"email": "lee@bakery.example"})
    assert "Lee" not in TestClient(app).get("/manage/signin").text

    for _ in range(5):
        anon.post("/manage/signin", data={"email": "pat@bakery.example", "pin": "000000"})
    fresh = TestClient(app)
    r = fresh.post("/manage/signin", data={"email": "pat@bakery.example", "pin": "135790"}, follow_redirects=False)
    assert "wrong=1" in r.headers["location"]                                   # locked after 5 wrong tries
    auth._fails.clear()


def test_dashboard_requirements_and_survey_chart(client, anon):
    with db.connect() as con:
        amy = db.add_worker(con, "Amy", "baking")
        db.add_worker(con, "Zed Quill", "sanitation")
        lessons = content.load_lessons()
        db.record(con, amy, lessons["lockout-tagout"], 1.0)
        db.record(con, amy, lessons["allergens"], 0.5)
    page = client.get("/manage/dashboard?role=baking").text
    assert "Amy" in page and "Zed Quill" not in page
    page = client.get("/manage/dashboard?lesson=lockout-tagout").text
    assert "Who has done" in page and "/certificate/" in page
    assert "Failed, retake" in client.get("/manage/dashboard?role=baking&lesson=allergens").text
    page = client.get("/manage/dashboard").text
    assert "Completion: Everyone" in page and 'class="ring-seg r-done"' in page and "By position" in page
    page = client.get("/manage/dashboard?role=baking").text
    assert "Completion: Baking" in page and "By lesson" in page and 'href="?role=baking&amp;lesson=allergens"' in page
    assert "By position" in client.get("/manage/dashboard?lesson=allergens").text

    # Requirements: untick everything for Sanitation on the forklift lesson.
    form = [("req", f"{l['id']}|{r['id']}") for l in content.load_lessons().values() for r in content.load_roles()
            if r["id"] in l["roles"] or "all" in l["roles"]]
    form = [f for f in form if f[1] != "forklifts|packaging"]
    client.post("/manage/requirements", data=form)
    assert "packaging" not in content.load_lessons()["forklifts"]["roles"]

    # Survey bars show only with enough answers.
    page = client.get("/manage/dashboard").text
    assert "of 3 answers so far" in page
    with db.connect() as con:
        form = content.load_survey()["form"]
        for a in ([2, 2, 2, 2], [2, 1, 2, 2], [0, 2, 2, 1]):
            db.save_survey(con, db.survey_ticket(con, "allergens"), a, "Knife safety", "Good", form)
        db.save_survey(con, db.survey_ticket(con, "allergens"), [0, 0, 0, 0], "old form", "", "older form")
    page = client.get("/manage/dashboard").text
    assert "seg2" in page and "6.7" in page and "/ 8 average score" in page and "Knife safety" in page
    assert "old form" not in page                                   # answers to an older form aren't mixed in


def test_languages(client, anon):
    with db.connect() as con:
        wid = db.add_worker(con, "Lin", "baking", __import__("trainer.auth", fromlist=["x"]).hash_pin("4321"))
    anon.post("/lang", data={"lang": "es", "back": "/"})
    assert "¿Quién se capacita hoy?" in anon.get("/").text
    anon.post(f"/signin/{wid}", data={"pin": "4321"})
    anon.post("/lang", data={"lang": "ar", "back": f"/me/{wid}"})
    page = anon.get(f"/me/{wid}").text
    assert 'dir="rtl"' in page
    with db.connect() as con:
        assert db.worker(con, wid)["lang"] == "ar"
    # No translation yet: English lesson with a note; survey in Arabic.
    page = anon.get("/lesson/lockout-tagout").text
    assert "غير مترجم" in page and json.dumps("أختلف")[1:-1] in page
    # A translation made for the current English is used; a stale one isn't.
    from trainer import app as app_module
    lesson = content.load_lessons()["lockout-tagout"]
    tr = {"source": app_module.content_hash(lesson), "title": "القفل", "summary": "", "sections": [{"heading": "لماذا", "text": "نص"}],
          "questions": [{"q": "سؤال", "choices": [], "why": ""}]}
    lesson["translations"] = {"ar": tr}
    content.save_lesson(lesson)
    page = anon.get("/lesson/lockout-tagout").text
    assert "القفل" in page and "غير مترجم" not in page
    lesson = content.load_lessons()["lockout-tagout"]
    lesson["sections"][0]["text"] += " Changed."
    content.save_lesson(lesson)
    assert "غير مترجم" in anon.get("/lesson/lockout-tagout").text          # English changed: back to English


def test_translate_lesson(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    def fake(lesson, lang):
        return {"title": "Montacargas", "summary": "", "sections": [{"heading": "H", "text": "T"} for _ in lesson["sections"]],
                "questions": [{"q": "P", "choices": ["x"] * len(q["choices"]), "why": ""} for q in lesson["questions"]]}
    monkeypatch.setattr(research, "translate", fake)
    assert "Not yet" in client.get("/manage/translations").text
    assert client.post("/api/manage/lesson/forklifts/translate", json={"lang": "es"}).json()["ok"]
    assert content.translation_state(content.load_lessons()["forklifts"], "es") == "current"
    assert "Montacargas" in client.get("/lesson/forklifts?lang=es").text
    assert client.post("/api/manage/lesson/forklifts/translate", json={"lang": "en"}).status_code == 400


def test_translation_shape_is_checked(env, monkeypatch):
    lesson = content.load_lessons()["forklifts"]
    short = json.dumps({"structured_output": {"title": "x", "summary": "", "sections": [], "questions": []}})
    monkeypatch.setattr(research, "mode", lambda: "cli")
    monkeypatch.setattr(research, "claude_command", lambda: "claude")
    with pytest.raises(RuntimeError, match="missing pieces"):
        research.translate(lesson, "vi", runner=lambda *a, **k: _Done(short))


def test_topic_folders(client):
    client.post("/manage/topics", data={"action": "add", "name": "Ovens"})
    topics = content.load_topics()
    assert topics[-1] == {"id": "ovens", "name": "Ovens", "lessons": [], "in_order": False}
    client.post("/manage/topics", data={"action": "put", "lesson": "indoor-heat", "topic": "ovens"})
    topics = {t["id"]: t for t in content.load_topics()}
    assert topics["ovens"]["lessons"] == ["indoor-heat"] and "indoor-heat" not in topics["health"]["lessons"]
    client.post("/manage/topics", data={"action": "rename", "topic": "ovens", "name": "Ovens and heat"})
    client.post("/manage/topics", data={"action": "up", "topic": "ovens"})
    names = [t["name"] for t in content.load_topics()]
    assert names.index("Ovens and heat") < names.index("Health")
    client.post("/manage/topics", data={"action": "lesson-down", "topic": "machines", "lesson": "lockout-tagout"})
    machines = next(t for t in content.load_topics() if t["id"] == "machines")
    assert machines["lessons"].index("bakery-equipment") < machines["lessons"].index("lockout-tagout")
    client.post("/manage/topics", data={"action": "delete", "topic": "ovens"})
    groups = content.group_by_topic(list(content.load_lessons().values()))
    assert groups[-1][0]["id"] == "other" and [l["id"] for l in groups[-1][1]] == ["indoor-heat"]
    page = client.get("/manage/topics").text
    assert "Other lessons" in page and "Heat illness" in page
    # Workers see their lessons under folder headings.
    with db.connect() as con:
        wid = db.add_worker(con, "Tess", "baking")
    page = client.get(f"/me/{wid}").text
    assert 'class="topic-head"' in page and "Machines and equipment" in page


def test_drag_order_and_in_order_folders(client, anon):
    topics = content.load_topics()
    order = [{"id": t["id"], "lessons": t["lessons"], "in_order": False} for t in reversed(topics)]
    machines = next(t for t in order if t["id"] == "machines")
    machines["lessons"] = ["forklifts", "lockout-tagout"]          # bakery-equipment dragged out to Other
    machines["in_order"] = True
    assert client.post("/api/manage/topics", json={"topics": order}).json()["ok"]
    saved = content.load_topics()
    assert [t["id"] for t in saved] == [t["id"] for t in reversed(topics)]
    m = next(t for t in saved if t["id"] == "machines")
    assert m["lessons"] == ["forklifts", "lockout-tagout"] and m["in_order"] and m["name"] == "Machines and equipment"
    assert "in_order: true" in (content.CONTENT / "topics.yaml").read_text()
    # A folder missing from what was sent means the page is stale: nothing is saved.
    assert client.post("/api/manage/topics", json={"topics": order[1:]}).status_code == 409
    assert anon.post("/api/manage/topics", json={"topics": order}).status_code == 401
    # Workers: the second lesson in an in-order folder stays locked until the first is passed.
    from trainer import auth
    with db.connect() as con:
        wid = db.add_worker(con, "Lou Park", "packaging", auth.hash_pin("2468"))
    anon.post(f"/signin/{wid}", data={"pin": "2468"})
    page = anon.get(f"/me/{wid}").text
    assert page.count('class="node locked"') == 1 and 'href="/lesson/lockout-tagout"' not in page
    assert anon.get("/lesson/lockout-tagout", follow_redirects=False).headers["location"] == f"/me/{wid}"
    assert anon.post("/api/lesson/lockout-tagout/finish", json={"answers": {}}).status_code == 403
    with db.connect() as con:
        db.record(con, wid, content.load_lessons()["forklifts"], 1.0)
    page = anon.get(f"/me/{wid}").text
    assert 'class="node locked"' not in page and 'href="/lesson/lockout-tagout"' in page
    assert anon.get("/lesson/lockout-tagout", follow_redirects=False).status_code == 200


def test_manager_stays_signed_in(client):
    from trainer import auth
    # A long session, renewed on each manager page.
    r = client.get("/manage/dashboard")
    assert r.status_code == 200 and "Max-Age=2592000" in r.headers.get("set-cookie", "")
    # Opening the sign-in page while signed in goes straight on.
    assert client.get("/manage/signin?next=/manage/topics", follow_redirects=False).headers["location"] == "/manage/topics"
    # A worker signing in and out on the same device doesn't sign the manager out.
    with db.connect() as con:
        wid = db.add_worker(con, "Ivy Stone", "baking", auth.hash_pin("1357"))
    client.post(f"/signin/{wid}", data={"pin": "1357"})
    assert client.get("/manage/dashboard", follow_redirects=False).status_code == 200
    assert "Ivy" in client.get(f"/me/{wid}").text
    client.post("/signout", data={"kind": "w"})
    assert client.get("/manage/dashboard", follow_redirects=False).status_code == 200
    assert client.get(f"/me/{wid}", follow_redirects=False).status_code == 200    # managers see everyone's page
    client.post("/signout", data={"kind": "m"})
    assert client.get("/manage/dashboard", follow_redirects=False).headers["location"].startswith("/manage/signin")


def test_drafts_must_cite_a_rule(env, client, monkeypatch):
    uncited = {**DRAFT, "citations": ["OSHA forklift rule"]}
    fake = _FakeClient([
        _Block(stop_reason="tool_use", content=[_Block(type="tool_use", id="t1", name="propose_lesson", input=uncited)]),
        _Block(stop_reason="tool_use", content=[_Block(type="text", text="Added the rule."),
                                                _Block(type="tool_use", id="t2", name="propose_lesson", input=DRAFT)]),
    ])
    out = research.run([{"role": "user", "text": "Forklifts"}], None, client=fake)
    sent_back = fake.calls[1]["messages"][-1]["content"][0]
    assert sent_back["is_error"] and "at least one" in sent_back["content"]
    assert out["lesson"]["citations"] == ["29 CFR 1910.178"]

    # Claude Code: sent back once with the reason, and the retry is used.
    inputs = []

    def runner(cmd, input, **kw):
        inputs.append(input)
        return _Done(json.dumps({"structured_output": {"reply": "ok", "has_lesson": True,
                                                       "lesson": uncited if len(inputs) == 1 else DRAFT}}))
    out = research.run_cli([{"role": "user", "text": "Forklifts"}], None, command="claude", runner=runner)
    assert len(inputs) == 2 and "<app_note>" in inputs[1] and out["lesson"]["citations"] == ["29 CFR 1910.178"]

    # A section the source says doesn't exist is taken out of the draft.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(research, "run", lambda h, d: {"reply": "Drafted.", "searched": [],
                        "lesson": {"citations": ["29 CFR 1910.178", "29 CFR 1910.9999"], "slides": [], "sources": []}})
    monkeypatch.setattr(rules, "check_rule", lambda c, old, get=None: {"found": c["ref"] != "29 CFR 1910.9999"})
    r = client.post("/api/manage/research", json={"history": [{"role": "user", "text": "x"}]}).json()
    assert r["lesson"]["citations"] == ["29 CFR 1910.178"] and "Took out 29 CFR 1910.9999" in r["reply"]
    # Saving a lesson needs at least one citation.
    assert any("based on" in e["error"] for e in editor.check_meta({"title": "x", "citations": []}))
    assert not editor.check_meta({"title": "x", "citations": ["8 CCR 3314"]})


def test_photos_are_found_and_matched_to_slides(client):
    from trainer import app as app_module

    class R:
        def __init__(self, data=None, content=b""):
            self.status_code, self._data, self.content = 200, data, content

        def json(self):
            return self._data

    def photo(n, lic="CC BY 4.0"):
        return {"title": f"File:Mixer {n}.jpg", "index": n, "imageinfo": [{
            "mime": "image/jpeg", "url": f"https://upload.wikimedia.org/{n}.jpg", "thumburl": f"https://upload.wikimedia.org/t{n}.jpg",
            "descriptionurl": f"https://commons.wikimedia.org/wiki/File:Mixer_{n}.jpg",
            "extmetadata": {"LicenseShortName": {"value": lic}, "Artist": {"value": "A. Baker"}}}]}

    searches = []

    def get(url, params=None, **k):
        if params and params.get("generator") == "search":
            searches.append(params["gsrsearch"])
            return R({"query": {"pages": [photo(2), photo(1), photo(3, "CC BY-NC 4.0")]}})
        if params:                               # the license re-check when a pick is saved
            n = params["titles"].split()[-1].split(".")[0]
            return R({"query": {"pages": [photo(int(n))]}})
        return R(content=b"\xff\xd8" + url.encode())

    seen = {}

    def matcher(asks):
        seen["asks"] = asks
        return {0: 1, 2: -1}                    # slide 0 gets its second photo; slide 2 none fit

    lesson = {"slides": [{"type": "reading", "heading": "The mixer", "text": "t", "images": [], "picture_search": "industrial dough mixer"},
                         {"type": "question", "q": "?"},
                         {"type": "reading", "heading": "Rules", "text": "t", "images": [], "picture_search": "rule book"},
                         {"type": "reading", "heading": "Own", "text": "t", "images": ["/media/x.jpg"], "picture_search": "x"}],
              "sources": []}
    assert app_module._find_pictures(lesson, get=get, matcher=matcher) == 1
    assert searches == ["industrial dough mixer filetype:bitmap", "rule book filetype:bitmap"]   # slides that already have one are skipped
    first = seen["asks"][0]
    assert [c["title"] for c in first["candidates"]] == ["Mixer 1", "Mixer 2"]                  # the NC photo never reaches Claude
    assert lesson["slides"][0]["images"][0].startswith("/media/commons-") and lesson["slides"][2]["images"] == []
    assert lesson["sources"][0]["url"] == "https://commons.wikimedia.org/wiki/File:Mixer_2.jpg" and "CC BY 4.0" in lesson["sources"][0]["title"]


def test_manager_tab_only_for_managers(client, anon):
    with db.connect() as con:
        wid = db.add_worker(con, "Ivy Stone", "baking", __import__("trainer.auth", fromlist=["x"]).hash_pin("1357"))
    tab = 'href="/manage/dashboard">'
    assert tab not in anon.get("/").text and 'href="/manage/signin"' in anon.get("/").text   # just a small sign-in link
    worker = TestClient(app)
    worker.post(f"/signin/{wid}", data={"pin": "1357"})
    assert tab not in worker.get(f"/me/{wid}").text and 'href="/manage/topics"' not in worker.get(f"/me/{wid}").text
    assert tab in client.get("/").text and 'href="/manage/topics"' in client.get("/").text    # a signed-in manager: Lessons + Manager tabs
    assert client.get("/rules").url.path == "/manage/topics"                                   # the old Rules tab leads to Lessons


def test_workers_sign_up_and_wait_for_approval(client, anon):
    assert 'href="/join"' in anon.get("/").text
    assert "bad=pin" in anon.post("/join", data={"name": "Rosa Diaz", "role": "baking", "pin": "1234", "pin2": "4321"}, follow_redirects=False).headers["location"]
    assert "bad=role" in anon.post("/join", data={"name": "Rosa Diaz", "role": "boss", "pin": "1234", "pin2": "1234"}, follow_redirects=False).headers["location"]
    assert "done=1" in anon.post("/join", data={"name": " Rosa  Diaz ", "role": "baking", "pin": "2468", "pin2": "2468"}, follow_redirects=False).headers["location"]
    anon.post("/join", data={"name": "Spam Bot", "role": "mixing", "pin": "1111", "pin2": "1111"})
    with db.connect() as con:
        rosa, spam = db.pending_workers(con)
    assert rosa["name"] == "Rosa Diaz" and "Rosa" not in anon.get("/").text                  # not on the sign-in list yet
    assert anon.get(f"/signin/{rosa['id']}").status_code == 404
    assert anon.post(f"/signin/{rosa['id']}", data={"pin": "2468"}).status_code == 404
    assert anon.post(f"/manage/approvals/{rosa['id']}", data={"action": "approve", "role": "mixing"}, follow_redirects=False).headers["location"].startswith("/manage/signin")

    page = client.get("/manage/approvals").text
    assert "Rosa Diaz" in page and 'count-badge">2<' in page
    client.post(f"/manage/approvals/{rosa['id']}", data={"action": "approve", "role": "mixing"})
    client.post(f"/manage/approvals/{spam['id']}", data={"action": "decline"})
    with db.connect() as con:
        assert db.pending_workers(con) == [] and db.worker(con, spam["id"]) is None
        assert db.worker(con, rosa["id"])["role"] == "mixing"
        client.post(f"/manage/approvals/{rosa['id']}", data={"action": "decline"})       # approved people can't be declined
        assert db.worker(con, rosa["id"])
    assert "Rosa Diaz" in anon.get("/").text
    worker = TestClient(app)
    assert worker.post(f"/signin/{rosa['id']}", data={"pin": "2468"}, follow_redirects=False).headers["location"] == f"/me/{rosa['id']}"


def test_delete_lesson_goes_to_trash_and_comes_back(client, anon, env):
    client.post("/manage/workers", data={"name": "Sam", "role": "packaging", "pin": "1234"})
    with db.connect() as con:
        sam = db.workers(con)[0]
    anon.post(f"/signin/{sam['id']}", data={"pin": "1234"})
    right = {i: q["answer"] for i, q in enumerate(content.load_lessons()["forklifts"]["questions"])}
    cert = anon.post("/api/lesson/forklifts/finish", json={"answers": right}).json()["certificate"]

    assert anon.post("/manage/lesson/forklifts/delete", follow_redirects=False).status_code in (303, 401)
    assert "forklifts" in content.load_lessons()                      # workers can't delete
    r = client.post("/manage/lesson/forklifts/delete", follow_redirects=False)
    assert r.status_code == 303 and "forklifts" not in content.load_lessons()
    page = client.get("/manage/topics").text
    assert "Put back" in page and "Forklifts" in page.split('id="trash"')[1]
    # Pages that list past results still work while the lesson is in the trash.
    for path in ["/manage", "/manage/dashboard", "/manage/requirements", f"/me/{sam['id']}"]:
        assert client.get(path).status_code == 200, path
    assert anon.get(f"/me/{sam['id']}").status_code == 200
    assert anon.get(cert).status_code in (200, 404)
    assert client.post("/manage/lesson/forklifts/delete").status_code == 404

    name = content.trashed_lessons()[0]["file"]
    client.post(f"/manage/trash/{name}/restore")
    assert "forklifts" in content.load_lessons() and not content.trashed_lessons()

    client.post("/manage/lesson/forklifts/delete")
    assert client.post("/manage/trash/..%2Flessons%2Fallergens/purge").status_code == 404
    client.post(f"/manage/trash/{content.trashed_lessons()[0]['file']}/purge")
    assert not content.trashed_lessons() and "forklifts" not in content.load_lessons()
    assert "allergens" in content.load_lessons()


def test_new_lesson_after_every_lesson_was_deleted(client, env):
    for lid in list(content.load_lessons()):
        client.post(f"/manage/lesson/{lid}/delete")
    shutil.rmtree(env / "content" / "lessons")                  # git drops the empty folder
    assert client.get("/manage").status_code == 200
    r = client.post("/api/manage/lesson/new", json=_deck(title="Fresh start"))
    assert r.json()["id"] == "fresh-start" and list(content.load_lessons()) == ["fresh-start"]


def test_records_outlive_their_lesson(client, anon):
    client.post("/manage/workers", data={"name": "Sam", "role": "packaging", "pin": "1234"})
    with db.connect() as con:
        sam = db.workers(con)[0]
    anon.post(f"/signin/{sam['id']}", data={"pin": "1234"})
    lesson = content.load_lessons()["forklifts"]
    right = {i: q["answer"] for i, q in enumerate(lesson["questions"])}
    cert = anon.post("/api/lesson/forklifts/finish", json={"answers": right}).json()["certificate"]
    client.post("/manage/lesson/forklifts/delete")
    client.post(f"/manage/trash/{content.trashed_lessons()[0]['file']}/purge")
    page = anon.get(cert).text
    assert lesson["title"] in page and lesson["citations"][0] in page
    assert lesson["title"] in client.get(f"/me/{sam['id']}").text      # manager's view of the record


def test_training_records_page_and_download(client, anon):
    client.post("/manage/workers", data={"name": "Sam", "role": "packaging", "pin": "1234"})
    client.post("/manage/workers", data={"name": "=cmd", "role": "baking", "pin": "4321"})
    with db.connect() as con:
        sam, odd = sorted(db.workers(con), key=lambda w: w["id"])
    lesson = content.load_lessons()["forklifts"]
    right = {i: q["answer"] for i, q in enumerate(lesson["questions"])}
    wrong = {i: (q["answer"] + 1) % len(q["choices"]) for i, q in enumerate(lesson["questions"])}
    for w, pin in ((sam, "1234"), (odd, "4321")):
        c = TestClient(app)
        c.post(f"/signin/{w['id']}", data={"pin": pin})
        c.post("/api/lesson/forklifts/finish", json={"answers": wrong})
        c.post("/api/lesson/forklifts/finish", json={"answers": right})
    client.post(f"/manage/workers/{sam['id']}/active", data={"active": 0})                # Sam leaves; the record stays

    page = client.get("/manage/records").text
    assert page.count("Passed</span>") == 2 and "Not passed</span>" not in page and "left</span>" in page
    assert client.get("/manage/records?result=all").text.count("Not passed</span>") == 2
    assert client.get(f"/manage/records?person={sam['id']}").text.count("Passed</span>") == 1
    r = client.get("/manage/records.csv")
    assert r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    lines = r.text.lstrip("﻿").splitlines()
    assert lines[0].startswith("Topic,Name,Position,Lesson") and len(lines) == 3
    assert any(",'=cmd," in l for l in lines)                  # no spreadsheet formulas
    assert lesson["title"] in r.text and "UB-" in r.text
    assert anon.get("/manage/records", follow_redirects=False).status_code == 303
    assert anon.get("/manage/records.csv", follow_redirects=False).status_code in (303, 401)
    # Records follow the lesson folders: grouped under each folder, and filtered by one.
    assert '📁 Machines and equipment' in page and lines[1].startswith("Machines and equipment,")
    assert client.get("/manage/records?topic=Machines+and+equipment").text.count("Passed</span>") == 2
    assert client.get("/manage/records?topic=Food+safety").text.count("Passed</span>") == 0


def test_lessons_tab_and_manager_tabs(client):
    nav = client.get("/manage").text.split('class="subnav"')[1].split("</nav>")[0]
    assert [a.split(">")[1].split("<")[0].strip() for a in nav.split("<a ")[1:]] == ["Dashboard", "People", "Plans", "Records"]
    top = client.get("/manage/topics").text.split('class="tabs"')[1].split("</nav>")[0]
    assert "<span>Lessons</span>" in top and "<span>Rules</span>" not in top
    page = client.get("/manage/topics").text
    assert ">Lessons</a>" in page and "Who takes what" in page and 'id="trash"' in page and "New lesson" in page
    # one tab style everywhere: the Lessons section uses the same underline tabs as Manager
    lnav = page.split('aria-label="Lessons"')[1].split("</nav>")[0]
    assert [a.split(">")[1].split("<")[0].strip() for a in lnav.split("<a ")[1:]] == ["Lessons", "Who takes what", "Languages", "Media"]
    assert 'class="chips-nav"' not in page and 'id="federal"' in page


def test_safety_headers_and_forged_posts(client):
    r = client.get("/manage")
    h = r.headers
    assert "frame-ancestors 'self'" in h["content-security-policy"] and h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "SAMEORIGIN" and h["cache-control"] == "no-store"
    assert client.get("/docs").status_code == 404 and client.get("/openapi.json").status_code == 404
    # A form on another website can't post here with the manager's cookie.
    r = client.post("/manage/workers", data={"name": "Mallory", "role": "baking", "pin": "1234"},
                    headers={"origin": "https://evil.example"}, follow_redirects=False)
    assert r.status_code == 403
    r = client.post("/manage/workers", data={"name": "Sam", "role": "baking", "pin": "1234"},
                    headers={"origin": "http://testserver"}, follow_redirects=False)
    assert r.status_code == 303


def test_pin_guessing_gets_slower(client, anon):
    client.post("/manage/workers", data={"name": "Sam", "role": "packaging", "pin": "1234"})
    with db.connect() as con:
        sam = db.workers(con)[0]
    for i in range(5):
        anon.post(f"/signin/{sam['id']}", data={"pin": f"{i:04d}"})
    first = auth.locked_for("w", sam["id"])
    assert 0 < first <= auth.LOCK_SECONDS
    count, _ = auth._fails[("w", sam["id"])]
    auth._fails[("w", sam["id"])] = (count, 0)                       # wait out the first lock
    for i in range(5):
        anon.post(f"/signin/{sam['id']}", data={"pin": f"{i + 10:04d}"})
    assert auth.LOCK_SECONDS < auth.locked_for("w", sam["id"]) <= 2 * auth.LOCK_SECONDS   # longer the second time
    # One device can't spread guesses across everyone either.
    auth._fails.clear()
    auth._ip_fails["testclient"] = [time.time()] * auth.IP_MAX_FAILS
    assert "wrong=1" in anon.post(f"/signin/{sam['id']}", data={"pin": "1234"}, follow_redirects=False).headers["location"]
    auth._ip_fails.clear()
    assert anon.post(f"/signin/{sam['id']}", data={"pin": "1234"}, follow_redirects=False).headers["location"] == f"/me/{sam['id']}"


def test_manager_recovery_code(anon):
    page = anon.post("/manage/setup", data={"name": "Pat", "email": "pat@bakery.example", "pin": "135790", "pin2": "135790"}).text
    code = re.search(r'class="card recovery-code"[^>]*>([A-Z0-9-]{14})<', page).group(1)
    assert "only time it's shown" in page
    assert "recovery code yet" not in anon.get("/manage").text
    anon.post("/signout")

    bad = anon.post("/manage/recover", data={"email": "pat@bakery.example", "code": "AAAA-BBBB-CCCC", "pin": "246802", "pin2": "246802"}, follow_redirects=False)
    assert "bad=code" in bad.headers["location"]
    page = anon.post("/manage/recover", data={"email": "PAT@bakery.example", "code": code.lower().replace("-", " "), "pin": "246802", "pin2": "246802"}).text
    new_code = re.search(r'class="card recovery-code"[^>]*>([A-Z0-9-]{14})<', page).group(1)
    assert new_code != code and "new PIN is saved" in page
    assert anon.get("/manage/dashboard").status_code == 200                     # signed in
    other = TestClient(app)
    assert other.post("/manage/signin", data={"email": "pat@bakery.example", "pin": "246802"}, follow_redirects=False).headers["location"] == "/manage/dashboard"
    # The old code was used up.
    again = other.post("/manage/recover", data={"email": "pat@bakery.example", "code": code, "pin": "111111", "pin2": "111111"}, follow_redirects=False)
    assert "bad=code" in again.headers["location"]
    # A manager without a code is nudged to make one.
    with db.connect() as con:
        lee = db.add_manager(con, "Lee", auth.hash_pin("778899"), "lee@bakery.example")
    c = TestClient(app)
    c.post("/manage/signin", data={"email": "lee@bakery.example", "pin": "778899"})
    assert "recovery code yet" in c.get("/manage").text
    assert re.search(r'recovery-code"[^>]*>[A-Z0-9-]{14}<', c.post("/manage/recovery-code").text)


def _found(c, old, get=None):
    return {"ref": c["ref"], "url": c["url"], "kind": c["kind"], "found": True, "name": "Official name", "last_checked": "2026-10-06"}


def test_rules_list_add_edit_delete(client, monkeypatch):
    monkeypatch.setattr(rules, "check_rule", _found)
    r = client.post("/manage/rules", data={"action": "add", "ref": "29 cfr 1910.22", "name": "Walking surfaces", "topic": "machines"})
    assert "rule=added" in r.url.query.decode()
    listed = {x["ref"]: x for x in content.load_rules()}
    assert listed["29 CFR 1910.22"]["topic"] == "machines" and listed["29 CFR 1910.22"]["name"] == "Walking surfaces"
    page = client.get("/manage/topics").text
    assert "29 CFR 1910.22" in page and "Walking surfaces" in page and "Add rule" in page
    # Rename and move to no folder.
    client.post("/manage/rules", data={"action": "save", "ref": "29 CFR 1910.22", "name": "Floors", "topic": ""})
    listed = {x["ref"]: x for x in content.load_rules()}
    assert listed["29 CFR 1910.22"]["name"] == "Floors" and listed["29 CFR 1910.22"]["topic"] == ""
    assert "rule=dup" in client.post("/manage/rules", data={"action": "add", "ref": "29 CFR 1910.22"}).url.query.decode()
    assert "rule=bad" in client.post("/manage/rules", data={"action": "add", "ref": "rule 5"}).url.query.decode()
    client.post("/manage/rules", data={"action": "delete", "ref": "29 CFR 1910.22"})
    assert "29 CFR 1910.22" not in {x["ref"] for x in content.load_rules()}


def test_rules_list_checks_the_source_first(client, monkeypatch):
    monkeypatch.setattr(rules, "check_rule", lambda c, old, get=None: {**_found(c, old), "found": False})
    assert "rule=notfound" in client.post("/manage/rules", data={"action": "add", "ref": "29 CFR 1910.9999"}).url.query.decode()
    monkeypatch.setattr(rules, "check_rule", lambda c, old, get=None: {"ref": c["ref"], "last_error": "2026-10-06: Timeout"})
    assert "rule=unreachable" in client.post("/manage/rules", data={"action": "add", "ref": "8 CCR 9999"}).url.query.decode()
    assert not {"29 CFR 1910.9999", "8 CCR 9999"} & {x["ref"] for x in content.load_rules()}


def test_lesson_rules_filed_in_their_folder_and_kept(client):
    client.get("/manage/topics")
    listed = {x["ref"]: x for x in content.load_rules()}
    assert listed["29 CFR 1910.147"]["topic"] == "machines"     # lockout-tagout sits in Machines
    r = client.post("/manage/rules", data={"action": "delete", "ref": "29 CFR 1910.147"})
    assert "rule=inuse" in r.url.query.decode() and "29 CFR 1910.147" in {x["ref"] for x in content.load_rules()}
    # The checker also checks listed rules no lesson cites.
    content.save_rules(content.load_rules() + [{"ref": "8 CCR 9999", "name": "", "topic": "", "note": "", "reviewed_on": ""}])
    assert "8 CCR 9999" in rules.cited_rules(content.load_lessons())


# ---- Video editor (ffmpeg from imageio-ffmpeg) --------------------------------
def _clip(path, seconds, sound=True):
    from trainer import video
    cmd = [video.ffmpeg_exe(), "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=15:duration={seconds}"]
    if sound:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac"]
    subprocess.run(cmd + ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(path)], check=True)


def test_video_editor_trims_cuts_and_joins(client, env):
    from trainer import app as app_module, video
    a = app_module.media_dir() / "allergens-20990101000000.mp4"
    b = app_module.media_dir() / "allergens-20990101000001.mp4"
    _clip(a, 6)
    _clip(b, 3, sound=False)
    r = client.post("/api/manage/video/edit", json={"lesson": "allergens", "turn": 90, "clips": [
        {"video": "/media/" + a.name, "start": 1, "end": 5, "cuts": [[2, 3]]},
        {"video": "/media/" + b.name, "start": 0, "end": None, "cuts": []}]})
    assert r.status_code == 200, r.text
    made = r.json()
    assert made["video"].startswith("/media/allergens-edited-") and made["video"].endswith(".mp4")
    assert abs(made["duration"] - 6) < 0.01
    info = video.probe(app_module.media_dir() / made["video"].rsplit("/", 1)[-1])
    assert abs(info["duration"] - 6) < 0.3 and info["audio"]
    assert a.exists() and b.exists()  # the clips themselves aren't changed

    muted = client.post("/api/manage/video/edit", json={"mute": True, "clips": [{"video": "/media/" + a.name}]}).json()
    assert not video.probe(app_module.media_dir() / muted["video"].rsplit("/", 1)[-1])["audio"]


def test_video_editor_refuses_bad_edits(client, anon, env):
    from trainer import app as app_module
    a = app_module.media_dir() / "allergens-20990101000000.mp4"
    _clip(a, 2)
    ok = {"video": "/media/" + a.name}
    for clips in ([{"video": "https://example.com/x.mp4"}], [{"video": "/media/../training.db"}],
                  [{"video": "/media/missing-20990101000000.mp4"}], ["oops"]):
        r = client.post("/api/manage/video/edit", json={"clips": clips})
        assert r.status_code == 400 and "uploaded to the app" in r.json()["error"]
    assert client.post("/api/manage/video/edit", json={"clips": [dict(ok, start="soon")]}).status_code == 400
    assert client.post("/api/manage/video/edit", json={"turn": 45, "clips": [ok]}).status_code == 400
    assert client.post("/api/manage/video/edit", json={"turn": "x", "clips": [ok]}).status_code == 400
    r = client.post("/api/manage/video/edit", json={"clips": [dict(ok, cuts=[[0, 5]])]})
    assert r.status_code == 400 and "Nothing would be left" in r.json()["error"]
    assert not list(app_module.media_dir().glob("*-edited-*"))
    assert anon.post("/api/manage/video/edit", json={"clips": [ok]}).status_code in (303, 401, 403)


def test_video_editor_reads_the_length(client, env):
    from trainer import app as app_module
    a = app_module.media_dir() / "allergens-20990101000000.mp4"
    _clip(a, 2)
    r = client.get("/api/manage/video/info", params={"v": "/media/" + a.name}).json()
    assert abs(r["duration"] - 2) < 0.2 and r["audio"]
    assert client.get("/api/manage/video/info", params={"v": "/media/../t.db"}).status_code == 400


def test_video_editor_grabs_a_frame(client, anon, env):
    from trainer import app as app_module
    a = app_module.media_dir() / "allergens-20990101000000.mp4"
    _clip(a, 3)
    r = client.post("/api/manage/video/frame", json={"video": "/media/" + a.name, "t": 1.5, "lesson": "allergens"})
    assert r.status_code == 200, r.text
    pic = app_module.media_dir() / r.json()["image"].rsplit("/", 1)[-1]
    assert r.json()["image"].startswith("/media/allergens-frame-") and pic.read_bytes()[:3] == b"\xff\xd8\xff"
    # It can go straight into a new video as a freeze frame between two halves of the clip.
    r = client.post("/api/manage/video/edit", json={"clips": [
        {"video": "/media/" + a.name, "end": 1.5}, {"video": "/media/" + pic.name, "seconds": 2},
        {"video": "/media/" + a.name, "start": 1.5}]})
    assert r.status_code == 200 and abs(r.json()["duration"] - 5) < 0.01
    # Exact preview: a few seconds with the effects, not kept in the library.
    r = client.post("/api/manage/video/edit", json={"preview": True, "extras": ["bright", "clean"], "clips": [{"video": "/media/" + a.name, "start": 0.5, "end": 2.5}]})
    assert r.status_code == 200 and "-preview-" in r.json()["video"]
    assert r.json()["video"] not in [i["link"] for i in client.get("/api/manage/media").json()["items"]]
    assert client.post("/api/manage/video/edit", json={"preview": True, "clips": [{"video": "/media/" + a.name}]}).status_code == 400
    assert client.post("/api/manage/video/frame", json={"video": "/media/../t.db", "t": 1}).status_code == 400
    assert client.post("/api/manage/video/frame", json={"video": "/media/" + a.name, "t": "soon"}).status_code == 400
    assert anon.post("/api/manage/video/frame", json={"video": "/media/" + a.name, "t": 1}).status_code in (303, 401, 403)


def test_video_editor_shows_pictures(client, env):
    from trainer import app as app_module, video
    a = app_module.media_dir() / "allergens-20990101000000.mp4"
    _clip(a, 3)
    pic = app_module.media_dir() / "allergens-20990101000002.png"
    subprocess.run([video.ffmpeg_exe(), "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=301x199",
                    "-frames:v", "1", str(pic)], check=True)
    r = client.post("/api/manage/video/edit", json={"clips": [
        {"video": "/media/" + pic.name, "seconds": 2.5}, {"video": "/media/" + a.name}]})
    assert r.status_code == 200, r.text
    assert abs(r.json()["duration"] - 5.5) < 0.01
    info = video.probe(app_module.media_dir() / r.json()["video"].rsplit("/", 1)[-1])
    assert abs(info["duration"] - 5.5) < 0.3 and info["audio"]  # the picture gets silence, the clip keeps its sound
    # A picture alone makes a silent video.
    r = client.post("/api/manage/video/edit", json={"clips": [{"video": "/media/" + pic.name}]})
    assert r.status_code == 200 and abs(r.json()["duration"] - 5) < 0.01


class _DriveReply:
    def __init__(self, body, ctype="video/mp4", name="Floor clip.mp4", status=200):
        self.status_code, self.body = status, body
        self.headers = {"Content-Type": ctype, "Content-Disposition": f'attachment; filename="{name}"' if name else ""}

    def iter_content(self, n):
        for i in range(0, len(self.body), n):
            yield self.body[i:i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_google_drive_copies_shared_files(client, env, monkeypatch):
    from trainer import app as app_module
    calls = []

    def fake_get(url, params=None, **kw):
        calls.append((url, params))
        return replies.pop(0)

    monkeypatch.setattr("requests.get", fake_get)
    fid = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
    mp4 = b"\x00\x00\x00\x18ftypmp42" + b"x" * 500
    replies = [_DriveReply(mp4)]
    r = client.post("/api/manage/drive", json={"link": f"https://drive.google.com/file/d/{fid}/view?usp=sharing", "lesson": "allergens"})
    assert r.status_code == 200 and r.json()["kind"] == "video"
    assert calls[0] == (app_module.DRIVE_DOWNLOAD, {"id": fid, "export": "download", "confirm": "t"})
    assert client.get(r.json()["video"]).content == mp4

    replies = [_DriveReply(b"\x89PNG\r\n\x1a\n" + b"p" * 50, "image/png", name="")]
    r = client.post("/api/manage/drive", json={"link": f"https://drive.google.com/open?id={fid}", "kind": "image"})
    assert r.status_code == 200 and r.json()["image"].endswith(".png")

    # Not shared (Drive answers with a sign-in page), wrong kind, fake file, not a Drive link.
    before = set(app_module.media_dir().iterdir())
    replies = [_DriveReply(b"<html>", "text/html; charset=utf-8", name="")]
    r = client.post("/api/manage/drive", json={"link": f"https://drive.google.com/file/d/{fid}/view"})
    assert r.status_code == 400 and "Anyone with the link" in r.json()["error"]
    replies = [_DriveReply(mp4)]
    r = client.post("/api/manage/drive", json={"link": f"https://drive.google.com/file/d/{fid}/view", "kind": "image"})
    assert r.status_code == 400 and "not a picture" in r.json()["error"]
    replies = [_DriveReply(b"MZ not a video at all", name="clip.mp4")]
    assert client.post("/api/manage/drive", json={"link": f"https://drive.google.com/file/d/{fid}/view"}).status_code == 400
    replies = [_DriveReply(b"x", "application/zip", name="files.zip")]
    assert client.post("/api/manage/drive", json={"link": f"https://drive.google.com/file/d/{fid}/view"}).status_code == 400
    for bad in ["https://evil.example/file/d/" + fid, "http://drive.google.com/file/d/" + fid, "not a link"]:
        r = client.post("/api/manage/drive", json={"link": bad})
        assert r.status_code == 400 and "Google Drive file link" in r.json()["error"]
    assert set(app_module.media_dir().iterdir()) == before  # nothing left behind
    from fastapi.testclient import TestClient as _TC
    assert _TC(app_module.app).post("/api/manage/drive", json={"link": "x"}, follow_redirects=False).status_code in (303, 401, 403)


def test_google_drive_picker(client, env, monkeypatch):
    from trainer import app as app_module
    page = client.get("/manage/lesson/allergens").text
    assert "window.DRIVE = null" in page          # no button until Google is set up
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "123456789012-abc.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_API_KEY", "AIzaPublicBrowserKey")
    page = client.get("/manage/lesson/allergens")
    assert '"appId": "123456789012"' in page.text and "https://apis.google.com" in page.headers["content-security-policy"]

    calls = []

    def fake_get(url, params=None, headers=None, **kw):
        calls.append((url, params, headers))
        return replies.pop(0)

    monkeypatch.setattr("requests.get", fake_get)
    fid = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
    replies = [_DriveReply(b"\xff\xd8\xff\xe0jpegdata", "image/jpeg", name="")]
    r = client.post("/api/manage/drive", json={"file_id": fid, "token": "ya29.short", "name": "Mixer guard.JPG", "kind": "image"})
    assert r.status_code == 200 and r.json()["image"].endswith(".jpg")
    assert calls[0][0] == app_module.DRIVE_API + fid and calls[0][1]["alt"] == "media"
    assert calls[0][2] == {"Authorization": "Bearer ya29.short"}
    # Google says no (expired pass), or a made-up file id: nothing is fetched or kept.
    replies = [_DriveReply(b"{}", "application/json", name="", status=401)]
    r = client.post("/api/manage/drive", json={"file_id": fid, "token": "old", "name": "a.jpg"})
    assert r.status_code == 400 and "sign in to Google again" in r.json()["error"]
    n = len(calls)
    assert client.post("/api/manage/drive", json={"file_id": "../../etc", "token": "t"}).status_code == 400
    assert client.post("/api/manage/drive", json={"file_id": fid}).status_code == 400
    assert len(calls) == n


def test_video_editor_touch_ups(client, env):
    from trainer import app as app_module, video
    a = app_module.media_dir() / "allergens-20990101000000.mp4"
    subprocess.run([video.ffmpeg_exe(), "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=4",
                    "-f", "lavfi", "-i", "anoisesrc=d=4:a=0.2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(a)], check=True)
    r = client.post("/api/manage/video/edit", json={"clips": [{"video": "/media/" + a.name}],
                                                     "extras": ["clean", "level", "fade", "bright", "steady", "nonsense"]})
    assert r.status_code == 200, r.text
    out = app_module.media_dir() / r.json()["video"].rsplit("/", 1)[-1]
    info = video.probe(out)
    assert abs(info["duration"] - 4) < 0.3 and info["audio"]
    leftovers = [p.name for p in app_module.media_dir().iterdir() if "pass1" in p.name or p.suffix == ".trf"]
    assert leftovers == []
    cmd = " ".join(video.finish_command("join.txt", 4, True, "o.mp4", extras=("clean", "fade")))
    assert "afftdn" in cmd and "fade=t=out:st=3.4" in cmd and "loudnorm" not in cmd


def test_video_made_in_the_background(client, env):
    """A long edit runs in the background; the page asks how it's going until the video is ready."""
    import time
    from trainer import app as app_module, video
    a = app_module.media_dir() / "bg-20990101000000.mp4"
    _clip(a, 2)
    r = client.post("/api/manage/video/edit", json={"background": True, "clips": [{"video": "/media/" + a.name}, {"video": "/media/" + a.name, "cuts": [[0.5, 1]]}]})
    assert r.status_code == 200 and r.json()["job"]
    for _ in range(120):
        j = client.get("/api/manage/video/edit/" + r.json()["job"]).json()
        if j["state"] != "running":
            break
        time.sleep(0.5)
    assert j["state"] == "done", j
    assert abs(video.probe(app_module.media_dir() / j["video"].rsplit("/", 1)[-1])["duration"] - 3.5) < 0.3
    # Asked with wait: answers at once when the job is already finished.
    t0 = time.time()
    assert client.get("/api/manage/video/edit/" + r.json()["job"] + "?wait=1").json()["state"] == "done" and time.time() - t0 < 2
    assert client.get("/api/manage/video/edit/nope").status_code == 404


def test_media_studio_library(client, anon, env):
    from trainer import app as app_module
    assert "<h1>Media</h1>" in client.get("/manage/studio").text
    pic = client.post("/api/manage/image", data={"lesson": "allergens", "label": "Mixer guard"},
                      files={"image_file": ("IMG_1.jpg", b"\xff\xd8\xff\xe0" + b"j" * 100, "image/jpeg")}).json()["image"]
    items = client.get("/api/manage/media").json()["items"]
    assert [(x["link"], x["label"], x["kind"]) for x in items] == [(pic, "Mixer guard", "image")]
    assert client.post("/api/manage/media/rename", json={"link": pic, "label": "  Guard on mixer 3 "}).json()["ok"]
    assert client.get("/api/manage/media").json()["items"][0]["label"] == "Guard on mixer 3"
    # Used in a lesson: can't delete. Not used: deleted for good.
    deck = _deck()
    deck["slides"][1]["images"] = [pic]
    assert client.post("/api/manage/lesson/allergens", json=deck).status_code == 200
    r = client.post("/api/manage/media/delete", json={"link": pic})
    assert r.status_code == 400 and "Allergen" in r.json()["error"]
    deck["slides"][1]["images"] = []
    client.post("/api/manage/lesson/allergens", json=deck)
    assert client.post("/api/manage/media/delete", json={"link": pic}).json()["ok"]
    assert client.get(pic).status_code == 404 and client.get("/api/manage/media").json()["items"] == []
    for bad in ["/media/../t.db", "https://x.example/a.jpg"]:
        assert client.post("/api/manage/media/delete", json={"link": bad}).status_code == 400
    assert client.get("/media/library.json").status_code == 404
    assert anon.get("/api/manage/media", follow_redirects=False).status_code in (303, 401, 403)


# ---- Build a lesson from a project -------------------------------------------------------

def test_project_templates(client, env):
    from trainer import project
    r = client.get("/api/manage/templates").json()
    assert r["templates"][0]["id"] == "standard" and "can_transcribe" in r
    r = client.post("/api/manage/templates", json={"name": "Machine walk-through", "outline": "1. What the machine does\n2. Steps"}).json()
    assert r["id"] == "machine-walk-through" and [t["id"] for t in r["templates"]] == ["standard", "machine-walk-through"]
    r = client.post("/api/manage/templates", json={"id": "machine-walk-through", "name": "Machine walk-through", "outline": "Changed"}).json()
    assert project.load_templates()[1]["outline"] == "Changed"
    assert client.post("/api/manage/templates", json={"name": "", "outline": "x"}).status_code == 400
    assert [t["id"] for t in client.post("/api/manage/templates", json={"delete": "standard"}).json()["templates"]] == ["machine-walk-through"]
    assert client.post("/api/manage/templates", json={"delete": "machine-walk-through"}).status_code == 400   # keep one


def test_project_builds_a_lesson(client, env, monkeypatch):
    """Two videos are joined, the transcript and photos go to Claude with the template, and the
    draft comes back checked, with the joined video and questions that pause it."""
    from trainer import app as app_module, project, video
    a = app_module.media_dir() / "proj-20990101000000.mp4"
    b = app_module.media_dir() / "proj-20990101000001.mp4"
    _clip(a, 3)
    _clip(b, 2)
    pic = app_module.media_dir() / "proj-20990101000002.png"
    subprocess.run([video.ffmpeg_exe(), "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=301x199", "-frames:v", "1", str(pic)], check=True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(project, "can_transcribe", lambda: True)
    monkeypatch.setattr(project, "transcribe", lambda path, model_dir: [(0.0, "Lock out the mixer."), (3.5, "Try the start button.")])
    seen = {}

    def fake_run(history, draft, images=None):
        seen.update(text=history[-1]["text"], images=images)
        lesson = dict(DRAFT, slides=[{"type": "reading", "heading": "Lock it out", "text": "Use your lock.", "images": ["/media/" + pic.name]},
                                     {"type": "question", "q": "When?", "choices": ["Before cleaning", "After"], "answer": 0, "why": "", "at": 4},
                                     {"type": "question", "q": "Too late?", "choices": ["a", "b"], "answer": 0, "why": "", "at": 99}])
        return {"reply": "Built it.", "lesson": research._finish(lesson, ["packaging"]), "searched": []}

    monkeypatch.setattr(research, "run", fake_run)
    monkeypatch.setattr(rules, "check_rule", lambda c, old, get=None: {"found": True, "name": "Powered industrial trucks."})
    monkeypatch.setattr(app_module, "_find_pictures", lambda lesson: 0)
    r = client.post("/api/manage/project", json={"lesson": "mixers", "template": "standard", "notes": "For bakers",
                                                  "videos": ["/media/" + a.name, "/media/" + b.name],
                                                  "photos": [{"link": "/media/" + pic.name, "label": "Mixer disconnect"}]})
    assert r.status_code == 200, r.text
    for _ in range(200):
        j = client.get("/api/manage/project/" + r.json()["job"]).json()
        if j["state"] != "running":
            break
        time.sleep(0.1)
    assert j["state"] == "done", j
    res = j["result"]
    assert res["lesson"]["video"].startswith("/media/mixers-project-") and res["checks"][0]["found"]
    assert [s.get("at") for s in res["lesson"]["slides"]] == [None, 4, None]     # a time past the end is dropped
    assert "[0:03] Try the start button." in res["transcript"]
    assert "Standard safety lesson" in seen["text"] and "Mixer disconnect" in seen["text"] and "For bakers" in seen["text"]
    assert seen["images"][0][0] == "photo-1.jpg"
    # Refusals
    assert client.post("/api/manage/project", json={"template": "standard", "videos": ["/media/../t.db"]}).status_code == 400
    assert client.post("/api/manage/project", json={"template": "nope", "videos": ["/media/" + a.name]}).status_code == 400
    assert client.post("/api/manage/project", json={"template": "standard"}).status_code == 400
    assert client.get("/api/manage/project/nope").status_code == 404


def test_research_cli_shows_photos(env, monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"\xff\xd8\xff fake")
    seen = {}

    def runner(cmd, input, **kw):
        seen.update(cmd=cmd, files=sorted(os.listdir(kw["cwd"])))
        return _Done(json.dumps({"structured_output": {"reply": "ok", "has_lesson": False, "lesson": DRAFT}}))

    research.run_cli([{"role": "user", "text": "q"}], None, command="claude", runner=runner, images=[("photo-1.jpg", photo)])
    assert seen["files"] == ["photo-1.jpg"] and seen["cmd"][seen["cmd"].index("--tools") + 1] == "WebSearch,WebFetch,Read"


# ---- Text to speech ---------------------------------------------------------------------------

class _FakeVoice:
    def synthesize_wav(self, text, w, syn_config=None):
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
        w.writeframes(b"\x00\x00" * 22050 * 2)      # two seconds


def test_speech_and_voiceover(client, anon, env, monkeypatch):
    from trainer import app as app_module, tts, video
    monkeypatch.setattr(tts, "_voice", lambda key, model_dir: _FakeVoice())
    monkeypatch.setattr(tts, "engine_ready", lambda engine: engine == "piper")   # the fake voice, even where Kokoro is installed
    assert anon.post("/api/speech", json={"text": "Hi"}).status_code == 401
    r = client.post("/api/speech", json={"text": "Lock out the mixer.", "voice": "en-m", "speed": "slow"})
    assert r.status_code == 200, r.text
    link = r.json()["audio"]
    assert link.startswith("/speech/") and abs(r.json()["duration"] - 2) < 0.2
    assert client.post("/api/speech", json={"text": "Lock out the mixer.", "voice": "en-m", "speed": "slow"}).json()["audio"] == link  # made once
    assert client.get(link).status_code == 200 and anon.get(link).status_code == 401
    assert client.get("/speech/../t.db").status_code in (404, 401)
    assert client.post("/api/speech", json={"text": "  "}).status_code == 400
    assert {v["id"] for v in client.get("/api/speech/voices").json()["voices"]} >= {"en-f", "es"}

    a = app_module.media_dir() / "vo-20990101000000.mp4"
    _clip(a, 3)
    for mode in ("replace", "mix"):
        r = client.post("/api/manage/video/edit", json={"voiceover": {"audio": link, "mode": mode}, "clips": [{"video": "/media/" + a.name}]})
        assert r.status_code == 200, r.text
        info = video.probe(app_module.media_dir() / r.json()["video"].rsplit("/", 1)[-1])
        assert abs(info["duration"] - 3) < 0.3 and info["audio"]
    # A silent picture video gets the voice as its sound.
    r = client.post("/api/manage/video/edit", json={"mute": True, "voiceover": {"audio": link, "mode": "mix"}, "clips": [{"video": "/media/" + a.name}]})
    assert r.status_code == 200 and video.probe(app_module.media_dir() / r.json()["video"].rsplit("/", 1)[-1])["audio"]
    assert client.post("/api/manage/video/edit", json={"voiceover": {"audio": "/speech/nope.m4a", "mode": "mix"},
                                                       "clips": [{"video": "/media/" + a.name}]}).status_code == 400


def test_video_made_in_the_browser_is_tidied(client, env, tmp_path):
    """A video recorded in the browser is saved as it is; the server only gives it a proper index."""
    from trainer import app as app_module, video
    src = tmp_path / "made.mp4"
    _clip(src, 2)
    r = client.post("/api/manage/video", data={"lesson": "allergens", "made_here": "1"},
                    files={"video_file": ("made.mp4", src.read_bytes(), "video/mp4")})
    path = app_module.media_dir() / r.json()["video"].rsplit("/", 1)[-1]
    assert abs(video.probe(path)["duration"] - 2) < 0.3 and r.json()["audio"] is True
    assert not list(app_module.media_dir().glob(".tidy-*"))
    # One that came out with no sound says so, so the browser can have the server make it instead.
    _clip(src, 2, sound=False)
    r = client.post("/api/manage/video", data={"lesson": "allergens", "made_here": "1"},
                    files={"video_file": ("made.mp4", src.read_bytes(), "video/mp4")})
    assert r.json()["audio"] is False
    # Something that isn't really a video is kept as uploaded (nothing to tidy).
    r = client.post("/api/manage/video", data={"lesson": "allergens", "made_here": "1"},
                    files={"video_file": ("made.webm", b"webm" * 100, "video/webm")})
    assert r.status_code == 200


def test_library_tiles_get_a_small_still(client, anon, env):
    """Library tiles show a still made from the video, not the (possibly huge) video itself."""
    from trainer import app as app_module
    a = app_module.media_dir() / "po-20990101000000.mp4"
    _clip(a, 2)
    assert anon.get("/api/manage/media/poster?link=/media/" + a.name).status_code in (401, 303, 307)
    r = client.get("/api/manage/media/poster?link=/media/" + a.name)
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"
    assert client.get("/api/manage/media/poster?link=https://evil/x.mp4").status_code == 404
    app_module._library_add("/media/" + a.name, "x")
    assert client.post("/api/manage/media/delete", json={"link": "/media/" + a.name}).status_code == 200
    assert not list((app_module.media_dir() / ".posters").iterdir())


def test_big_videos_get_a_light_copy_for_editing(client, env, monkeypatch):
    """A 4K or HEVC phone video stutters in the browser; the editor gets a 720p copy the server makes once."""
    import time as _time
    from trainer import app as app_module, video
    big = app_module.media_dir() / "lt-20990101000000.mp4"
    small = app_module.media_dir() / "lt-20990101000001.mp4"
    subprocess.run([video.ffmpeg_exe(), "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=2",
                    "-f", "lavfi", "-i", "sine=duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(big)], check=True)
    _clip(small, 2)
    assert client.get("/api/manage/video/info?v=/media/" + small.name).json()["light_state"] == "none"
    r = client.get("/api/manage/video/info?v=/media/" + big.name).json()
    assert r["width"] == 1920 and r["light_state"] in ("making", "ready")
    for _ in range(60):
        r = client.get("/api/manage/video/info?v=/media/" + big.name).json()
        if r["light_state"] != "making":
            break
        _time.sleep(0.5)
    assert r["light_state"] == "ready", r
    got = client.get(r["light"])
    assert got.status_code == 200
    copy = app_module._light_path(big)
    info = video.probe(copy)
    assert (info["width"], info["height"]) == (1280, 720) and info["audio"] and abs(info["duration"] - 2) < 0.3
    assert client.get("/api/manage/media/light/../../t.db").status_code == 404
    app_module._library_add("/media/" + big.name, "x")
    assert client.post("/api/manage/media/delete", json={"link": "/media/" + big.name}).status_code == 200
    assert not copy.exists()


def test_same_video_added_twice_is_kept_once(client, env):
    from trainer import app as app_module
    data = b"\x00\x00\x00\x18ftypmp42" + b"v" * 5000
    one = client.post("/api/manage/video", data={"lesson": "allergens"}, files={"video_file": ("a.mp4", data, "video/mp4")}).json()["video"]
    two = client.post("/api/manage/video", data={"lesson": "allergens"}, files={"video_file": ("b.mp4", data, "video/mp4")}).json()["video"]
    other = client.post("/api/manage/video", data={"lesson": "allergens"}, files={"video_file": ("c.mp4", data + b"x", "video/mp4")}).json()["video"]
    assert one == two != other
    assert len(list(app_module.media_dir().glob("*.mp4"))) == 2


def test_server_stays_awake_while_working(env, monkeypatch):
    """While a video is being made, the app keeps a request open to itself so Fly doesn't stop it."""
    import time
    from trainer import app as app_module
    calls = []

    class R:
        def read(self):
            return b""

    def get(url, timeout):
        calls.append(url)
        time.sleep(0.05)
        return R()
    app_module._video_jobs["t"] = {"at": time.time(), "state": "running"}
    try:
        app_module.keep_awake("https://example.fly.dev", get=get)
        app_module.keep_awake("https://example.fly.dev", get=get)   # only one keeper at a time
        time.sleep(0.3)
        assert calls and calls[0] == f"https://example.fly.dev/awake?key={app_module.AWAKE_KEY}"
    finally:
        app_module._video_jobs["t"]["state"] = "done"
    time.sleep(0.2)
    assert not app_module._awake["on"]


def test_video_drafts_save_list_and_delete(client, anon, env):
    """A video edit saves itself as a draft in the app; its clips can't be deleted while it's there."""
    from trainer import app as app_module
    a = app_module.media_dir() / "dr-20990101000000.mp4"
    _clip(a, 2)
    proj = {"clips": [{"video": "/media/" + a.name, "start": 0, "end": 1}], "lines": [], "opts": {}}
    assert anon.put("/api/manage/drafts/abcdef123456", json={"project": proj}).status_code == 401
    assert client.put("/api/manage/drafts/../x", json={"project": proj}).status_code in (400, 404, 405)
    assert client.put("/api/manage/drafts/abcdef123456", json={"project": {"clips": [{"video": "https://evil/x.mp4"}]}}).status_code == 400
    r = client.put("/api/manage/drafts/abcdef123456", json={"name": "Mixer video", "key": "k1", "project": proj})
    assert r.status_code == 200, r.text
    rows = client.get("/api/manage/drafts").json()["drafts"]
    assert [(d["id"], d["name"], d["clips"]) for d in rows] == [("abcdef123456", "Mixer video", 1)]
    assert client.get("/api/manage/drafts?key=k1").json()["drafts"] and not client.get("/api/manage/drafts?key=k2").json()["drafts"]
    assert client.get("/api/manage/drafts/abcdef123456").json()["project"] == proj
    r = client.post("/api/manage/media/delete", json={"link": "/media/" + a.name})
    assert r.status_code == 400 and "draft" in r.json()["error"]
    assert client.delete("/api/manage/drafts/abcdef123456").status_code == 200
    assert client.get("/api/manage/drafts/abcdef123456").status_code == 404
    assert client.post("/api/manage/media/delete", json={"link": "/media/" + a.name}).status_code == 200


def test_lesson_unsaved_changes_kept_in_the_app(client, anon):
    deck = {"title": "Draft title", "slides": []}
    assert anon.put("/api/manage/lesson-draft/new", json={"deck": deck}).status_code == 401
    assert client.put("/api/manage/lesson-draft/..%2Fx", json={"deck": deck}).status_code in (400, 404)
    assert client.get("/api/manage/lesson-draft/mixer-safety").json() == {"deck": None}
    assert client.put("/api/manage/lesson-draft/mixer-safety", json={"at": 5, "base": "b", "deck": deck}).status_code == 200
    assert client.get("/api/manage/lesson-draft/mixer-safety").json() == {"at": 5, "base": "b", "deck": deck}
    assert client.delete("/api/manage/lesson-draft/mixer-safety").status_code == 200
    assert client.get("/api/manage/lesson-draft/mixer-safety").json() == {"deck": None}


def test_server_turns_itself_off_only_when_idle(client, env, monkeypatch, tmp_path):
    """Online, the app stops the server after a quiet spell, but never while a video is being made."""
    import time
    from trainer import app as app_module
    monkeypatch.setattr(app_module, "IDLE_FLAG", tmp_path / "idle")
    client.get("/health")
    monkeypatch.setitem(app_module._visits, "last", time.time() - app_module.IDLE_STOP - 5)
    assert app_module._idle()                     # Fly's health checks don't count as visits
    app_module._video_jobs["idle-t"] = {"at": time.time(), "state": "running"}
    try:
        assert not app_module._idle()             # making a video: stays on
    finally:
        app_module._video_jobs["idle-t"]["state"] = "done"
    client.get("/")
    assert not app_module._idle()                 # someone just used it
    monkeypatch.setitem(app_module._visits, "last", time.time() - app_module.IDLE_STOP - 5)
    stopped = []
    app_module._idle_watch(stop=lambda: stopped.append(1), every=0)
    assert stopped and (tmp_path / "idle").exists()


def test_repeat_videos_removed_and_old_links_still_work(client, env):
    """fly/dedupe.py keeps one copy of a video saved several times; links to removed copies still play."""
    import importlib.util, json, pathlib
    from trainer import app as app_module
    spec = importlib.util.spec_from_file_location("dedupe", pathlib.Path(__file__).parent.parent / "fly" / "dedupe.py")
    dedupe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dedupe)
    media = app_module.media_dir()
    data = b"\x00\x00\x00\x18ftypmp42" + b"z" * 4000
    for n in ("a-1.mov", "a-2.mov", "a-3.mov"):
        (media / n).write_bytes(data)
    (media / "b-1.mov").write_bytes(data + b"!")
    (media / ".making-x").mkdir()
    lessons = media.parent / "content-test"
    lessons.mkdir()
    (lessons / "l.yaml").write_text("video: /media/a-2.mov\n")
    assert dedupe.dedupe(media, lessons) == 2 * len(data)
    assert sorted(p.name for p in media.iterdir() if p.suffix == ".mov") == ["a-2.mov", "b-1.mov"]
    assert not (media / ".making-x").exists()
    assert json.loads((media / "aliases.json").read_text()) == {"a-1.mov": "a-2.mov", "a-3.mov": "a-2.mov"}
    assert client.get("/media/a-3.mov").content == data
    assert client.get("/media/a-9.mov").status_code == 404


def test_google_voices_when_key_set_within_free_allowance(env, tmp_path, monkeypatch):
    """With GOOGLE_TTS_KEY, Google's natural voices read the lines; past the month's free allowance
    (or if Google fails) the free voices take over."""
    import base64, json, subprocess
    from trainer import tts, video
    monkeypatch.setenv("GOOGLE_TTS_KEY", "k")
    mp3 = tmp_path / "s.mp3"
    subprocess.run([video.ffmpeg_exe(), "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=1", str(mp3)], check=True)
    sent = []

    class R:
        status_code = 200
        def json(self):
            return {"audioContent": base64.b64encode(mp3.read_bytes()).decode()}

    def post(url, params, json, timeout):
        sent.append(json)
        return R()
    out = tmp_path / "speech"
    out.mkdir()
    tts._google_make(["Lock it out.", "Tag it."], "vi", "normal", out, subprocess.run, post=post)
    assert [s["voice"]["name"] for s in sent] == ["vi-VN-Chirp3-HD-Aoede"] * 2
    assert (out / tts.file_name("Tag it.", "vi", "normal", "google")).is_file()
    assert json.loads((out / "google-usage.json").read_text())["chars"] == len("Lock it out.") + len("Tag it.")
    monkeypatch.setattr(tts, "FREE_CHARS", 30)
    tts._google_make(["A much longer line than is left this month."], "vi", "normal", out, subprocess.run, post=post)
    assert len(sent) == 2      # over the allowance: nothing sent
    monkeypatch.delenv("GOOGLE_TTS_KEY")
    tts._google_make(["Other."], "vi", "normal", out, subprocess.run, post=post)
    assert len(sent) == 2


def test_made_video_keeps_its_edit(client, env):
    """The edit behind a made video is kept with it, and the clips it uses can't be deleted."""
    from trainer import app as app_module
    media = app_module.media_dir()
    (media / "src-1.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"s" * 100)
    (media / "made-1.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"m" * 100)
    app_module._library_add("/media/src-1.mp4", "Mixer clip")
    app_module._library_add("/media/made-1.mp4", "Made video")
    proj = {"clips": [{"video": "/media/src-1.mp4", "start": 1, "end": 3, "cuts": []}], "lines": [{"text": "Lock it out.", "at": 0}],
            "audio": "", "mode": "replace", "opts": {"mute": False, "turn": 0, "extras": ["fade"]}}
    assert client.post("/api/manage/media/project", json={"link": "/media/made-1.mp4", "project": proj}).json()["ok"]
    assert client.get("/api/manage/media/project?link=/media/made-1.mp4").json()["project"] == proj
    assert client.get("/api/manage/media/project?link=/media/src-1.mp4").json()["project"] is None
    r = client.post("/api/manage/media/delete", json={"link": "/media/src-1.mp4"})
    assert r.status_code == 400 and "the edit of Made video" in r.json()["error"]
    bad = {"clips": [{"video": "https://x.example/a.mp4"}]}
    assert client.post("/api/manage/media/project", json={"link": "/media/made-1.mp4", "project": bad}).status_code == 400


def test_training_plans_day_by_day(client, anon, env):
    """A plan lays out each day in order; a person put on it gets dated days, lessons tick off when
    passed and hands-on tasks when a manager signs them off. Records stay when the plan goes."""
    import datetime as _dt
    from trainer import plans
    r = client.post("/manage/plans", data={"name": "New baker"}, follow_redirects=False)
    pid = r.headers["location"].rsplit("/", 1)[1]
    assert "New baker" in client.get(f"/manage/plans/{pid}").text
    body = {"name": "New baker", "roles": ["baking"], "days": [
        {"title": "Safety first", "items": [{"lesson": "lockout-tagout"}, {"task": "Tour the plant\n- exits\n- eye wash", "note": "Bring ear plugs", "minutes": "90"}, {"lesson": "no-such"}]},
        {"title": "", "items": [{"task": "Shadow the mixer operator"}]}]}
    saved = client.post(f"/api/manage/plans/{pid}", json=body).json()["plan"]
    assert [len(d["items"]) for d in saved["days"]] == [2, 1]           # the missing lesson is dropped
    tour = saved["days"][0]["items"][1]["key"]
    assert saved["days"][0]["items"][1]["note"] == "Bring ear plugs" and saved["days"][0]["items"][1]["minutes"] == 90
    assert plans.duration(90) == "1 h 30 min" and plans.duration(0) == ""
    assert client.post(f"/api/manage/plans/{pid}", json={**saved, "name": "New baker"}).json()["plan"]["days"][0]["items"][1]["key"] == tour
    assert anon.post(f"/api/manage/plans/{pid}", json=body).status_code == 401

    # Dates skip the days off: a Friday start puts day 2 on Monday.
    assert plans.dates(_dt.date(2026, 10, 23), 2) == [_dt.date(2026, 10, 23), _dt.date(2026, 10, 26)]
    assert plans.dates(_dt.date(2026, 10, 23), 2, "0123456") == [_dt.date(2026, 10, 23), _dt.date(2026, 10, 24)]

    with db.connect() as con:
        wid = db.add_worker(con, "Rosa Diaz", "baking", __import__("trainer.auth", fromlist=["x"]).hash_pin("4321"))
    today = _dt.date.today()
    r = client.post("/manage/plans/assign", data={"worker": wid, "plan": pid, "start": today.isoformat(), "workdays": list("0123456")},
                    follow_redirects=False)
    aid = int(r.headers["location"].rsplit("/", 1)[1])
    page = client.get(f"/manage/plans/trainee/{aid}").text
    assert "Day 1" in page and "Tour the plant" in page and "Today" in page
    assert "Bring ear plugs" in page and "1 h 30 min" in page
    sheet = client.get(f"/manage/plans/trainee/{aid}/print").text      # the paper copy, filled in for this person
    assert "Rosa Diaz" in sheet and "Trainer initials" in sheet and "Bring ear plugs" in sheet and "Trainee signature" in sheet
    blank = client.get(f"/manage/plans/{pid}/print").text                  # blank, for anyone
    assert "Tour the plant" in blank and "Rosa" not in blank and "1 h 30 min" in blank
    assert anon.get(f"/manage/plans/{pid}/print", follow_redirects=False).status_code in (303, 401)
    cal = client.get("/manage/plans").text
    assert "Rosa · 1" in cal and "Rosa · 2" in cal and "Day 1 of 2" in cal

    client.post(f"/manage/plans/trainee/{aid}/check", data={"key": tour, "done": "1", "day": "1"})
    with db.connect() as con:
        checks = db.plan_checks(con, aid)
        assert checks[tour]["done_by"] == "Pat Manager" and "Tour the plant" in checks[tour]["item_text"]
        db.record(con, wid, content.load_lessons()["lockout-tagout"], 1.0)
    assert client.post(f"/manage/plans/trainee/{aid}/check", data={"key": "zzzzzzzzzz", "done": "1"}).status_code == 404
    assert "3 of 3 done" not in client.get(f"/manage/plans/trainee/{aid}").text
    assert "2 of 3 done" in client.get(f"/manage/plans/trainee/{aid}").text

    # The person sees today's list on their own page.
    anon.post(f"/signin/{wid}", data={"pin": "4321"})
    me = anon.get(f"/me/{wid}").text
    assert "My training plan" in me and "Today: day 1 of 2" in me and "Tour the plant" in me
    assert "Bring ear plugs" in me and "1 h 30 min" in me

    # Deleting the plan keeps the record.
    client.post(f"/manage/plans/{pid}/delete")
    assert plans.get(pid) is None
    assert "This plan was deleted" in client.get(f"/manage/plans/trainee/{aid}").text
    with db.connect() as con:
        assert db.plan_checks(con, aid) and db.assignment(con, aid)["plan_name"] == "New baker"


def test_claude_chat_is_kept_per_lesson(client, anon, env, monkeypatch):
    """The lesson editor's Claude chat (and its drafts) is kept in the app, and Claude's answer is
    added on the server too, so closing the page while Claude works loses nothing."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    assert anon.get("/api/manage/chat/allergens").status_code == 401
    assert client.get("/api/manage/chat/allergens").json()["log"] == []
    user = {"role": "user", "text": "Write the notes", "sent": "Write the notes"}
    assert client.put("/api/manage/chat/allergens", json={"at": 1, "log": [user]}).status_code == 200
    monkeypatch.setattr(research, "run", lambda history, draft: {"reply": "Here's a draft.", "lesson": None, "searched": []})
    client.post("/api/manage/research", json={"history": [{"role": "user", "text": "Write the notes"}], "draft": None, "chat": "allergens"})
    log = client.get("/api/manage/chat/allergens").json()["log"]
    assert [m["role"] for m in log] == ["user", "claude"] and log[1]["reply"] == "Here's a draft."
    # A page that saved before Claude answered doesn't wipe the answer.
    r = client.put("/api/manage/chat/allergens", json={"at": 2, "log": [user]}).json()
    assert len(r["log"]) == 2 and len(client.get("/api/manage/chat/allergens").json()["log"]) == 2
    assert client.put("/api/manage/chat/..%2Fx", json={"log": []}).status_code in (400, 404)
    assert client.delete("/api/manage/chat/allergens").status_code == 200
    assert client.get("/api/manage/chat/allergens").json()["log"] == []
