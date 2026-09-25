import datetime as dt
import json
import os
import shutil
import time

import pytest

os.environ["TRAINING_NO_RULES_LOOP"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from trainer import content, db, editor, research, rules  # noqa: E402
from trainer.app import app  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A private copy of content/ and a fresh database for each test."""
    shutil.copytree(content.CONTENT, tmp_path / "content")
    (tmp_path / "content" / "rules_status.json").unlink(missing_ok=True)
    monkeypatch.setattr(content, "CONTENT", tmp_path / "content")
    monkeypatch.setattr(content, "LESSONS", tmp_path / "content" / "lessons")
    monkeypatch.setattr(rules, "STATUS_PATH", tmp_path / "content" / "rules_status.json")
    monkeypatch.setenv("TRAINING_DB", str(tmp_path / "t.db"))
    return tmp_path


@pytest.fixture
def client(env):
    return TestClient(app)


def test_every_lesson_is_valid():
    lessons = content.load_lessons()
    assert len(lessons) >= 9
    assert not content.broken_lesson_files()
    roles = {r["id"] for r in content.load_roles()} | {"all"}
    for lid, l in lessons.items():
        assert l["title"] and l["sections"] and l["questions"], lid
        assert set(l["roles"]) <= roles, lid
        assert l["citations"], f"{lid} cites no rules"
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


def test_questions_text_round_trip():
    qs = content.load_lessons()["lockout-tagout"]["questions"]
    back, errors = content.questions_from_text(content.questions_to_text(qs))
    assert not errors
    assert [(q["q"], q["answer"], q["choices"], q.get("after_section")) for q in back] == \
           [(q["q"], q["answer"], q["choices"], q.get("after_section")) for q in qs]
    timed, errors = content.questions_from_text("@ 1:05\nQ: Hi?\n* yes\n- no")
    assert not errors and timed[0]["at"] == 65
    _, errors = content.questions_from_text("Q: No right answer?\n- a\n- b")
    assert errors


def test_worker_takes_a_lesson(client):
    r = client.post("/manage/workers", data={"name": "Sam", "role": "packaging"}, follow_redirects=False)
    assert r.status_code == 303
    with db.connect() as con:
        sam = db.workers(con)[0]
    page = client.get(f"/me/{sam['id']}").text
    assert "Forklifts" in page and "Mixers, sheeters" not in page  # role filter

    lesson = content.load_lessons()["forklifts"]
    assert "answer" not in client.get(f"/lesson/forklifts?worker={sam['id']}").text.split("window.LESSON")[1].split(";")[0]
    assert client.post("/api/lesson/forklifts/check", json={"question": 0, "choice": 1}).json()["correct"]

    wrong = {i: (q["answer"] + 1) % len(q["choices"]) for i, q in enumerate(lesson["questions"])}
    r = client.post("/api/lesson/forklifts/finish", json={"worker": sam["id"], "answers": wrong}).json()
    assert not r["passed"]
    right = {i: q["answer"] for i, q in enumerate(lesson["questions"])}
    r = client.post("/api/lesson/forklifts/finish", json={"worker": sam["id"], "answers": right}).json()
    assert r["passed"] and r["score"] == 100
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
    assert "Needs your review" in client.get("/manage").text

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

    bad = _deck(slides=[{"type": "question", "q": "", "choices": ["a"], "answer": 3, "at": None}], citations=["some rule"], title="")
    r = client.post("/api/manage/lesson/allergens", json=bad)
    assert r.status_code == 400
    errors = r.json()["errors"]
    by_slide = {e["slide"] for e in errors}
    assert {0, 1, "rules", None} <= by_slide
    assert any("some rule" in e["error"] for e in errors)
    assert content.load_lessons()["allergens"]["title"] == "Allergen cross-contact"  # nothing saved

    r = client.post("/api/manage/lesson/new", json=_deck(title="Brand new lesson"))
    assert r.json()["id"] == "brand-new-lesson" and "brand-new-lesson" in content.load_lessons()


def test_slides_round_trip_every_lesson():
    for lid, lesson in content.load_lessons().items():
        slides = editor.to_slides(lesson)
        sections, questions, errors = editor.from_slides(slides)
        assert not errors, (lid, errors)
        assert sections == [{"heading": s["heading"], "text": s["text"].strip() + "\n"} for s in lesson["sections"]], lid
        assert [(q["q"], q["choices"], q["answer"], q.get("at")) for q in questions] == \
               [(q["q"], q["choices"], q["answer"], q.get("at")) for q in lesson["questions"]], lid
        last = len(sections) - 1
        for want, got in zip(lesson["questions"], questions):
            if want.get("after_section") is not None:
                assert got["after_section"] == want["after_section"], (lid, want["q"])
            elif want.get("at") is None:
                assert got["after_section"] == last, (lid, want["q"])   # end questions follow the last reading


def test_pages_render(client):
    for path in ["/", "/manage", "/rules", "/lesson/indoor-heat", "/manage/lesson/new", "/health"]:
        assert client.get(path).status_code == 200, path


def test_broken_lesson_file_does_not_break_app(client, env):
    (env / "content" / "lessons" / "oops.yaml").write_text("title: [unclosed")
    assert client.get("/manage").status_code == 200
    assert "oops.yaml" in client.get("/manage").text


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

    # Replacing the video removes the old file.
    time.sleep(1.1)
    new = client.post("/api/manage/video", data={"lesson": "allergens"}, files={"video_file": ("second.webm", b"webm" * 100, "video/webm")}).json()["video"]
    client.post("/api/manage/lesson/allergens", json=_deck(video=new))
    assert new != video and new.endswith(".webm")
    assert client.get(video).status_code == 404

    # Clearing the video removes it.
    client.post("/api/manage/lesson/allergens", json=_deck(video=""))
    assert content.load_lessons()["allergens"]["video"] == ""
    assert client.get(new).status_code == 404


def test_unsaved_uploads_are_swept(client, env):
    from trainer import app as app_module
    stale = app_module.media_dir() / "abandoned-20200101000000.mp4"
    stale.write_bytes(b"x")
    os.utime(stale, (0, 0))
    fresh = app_module.media_dir() / "in-progress-20990101000000.mp4"
    fresh.write_bytes(b"x")
    client.post("/api/manage/lesson/allergens", json=_deck(video=""))
    assert not stale.exists() and fresh.exists()


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
    deck["slides"][1]["image"] = image
    assert client.post("/api/manage/lesson/allergens", json=deck).status_code == 200
    lesson = content.load_lessons()["allergens"]
    assert lesson["sections"][0]["image"] == image and "image" not in lesson["sections"][1]
    assert editor.to_slides(lesson)[1]["image"] == image
    assert image in client.get("/lesson/allergens").text              # the player gets it

    # Kept by the upload sweep while a lesson uses it, even when old.
    from trainer import app as app_module
    os.utime(app_module.media_dir() / image.rsplit("/", 1)[1], (0, 0))
    client.post("/api/manage/lesson/allergens", json=deck)
    assert client.get(image).status_code == 200

    bad = client.post("/api/manage/image", files={"image_file": ("IMG_1.HEIC", b"x", "image/heic")})
    assert bad.status_code == 400 and "JPEG" in bad.json()["error"]
    deck["slides"][1]["image"] = "javascript:alert(1)"
    assert client.post("/api/manage/lesson/allergens", json=deck).status_code == 400


def test_static_links_are_versioned(client):
    from trainer.app import templates
    v = templates.env.globals["asset_v"]
    assert f'/static/style.css?v={v}' in client.get("/manage").text
    assert f'/static/editor.js?v={v}' in client.get("/manage/lesson/new").text
