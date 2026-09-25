import datetime as dt
import json
import os
import shutil

import pytest

os.environ["TRAINING_NO_RULES_LOOP"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from trainer import content, db, rules  # noqa: E402
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


def test_manager_edits_a_lesson(client):
    page = client.get("/manage/lesson/allergens").text
    assert "Allergen cross-contact" in page
    form = {
        "title": "Allergen cross-contact",
        "summary": "s",
        "video": "https://www.youtube.com/watch?v=abcdefghijk",
        "roles": ["mixing", "baking"],
        "sections": "## One\nText one.\n\n## Two\nText two.",
        "questions": "@ 0:30\nQ: First?\n* yes\n- no\nWhy: because\n\nafter section 2\nQ: Second?\n- a\n* b",
        "citations": "21 cfr 117.35",
        "sources": "FDA | https://www.fda.gov",
        "retake": "on",
    }
    r = client.post("/manage/lesson/allergens", data=form, follow_redirects=False)
    assert r.status_code == 303
    l = content.load_lessons()["allergens"]
    assert l["roles"] == ["mixing", "baking"] and l["questions"][0]["at"] == 30
    assert l["questions"][1]["after_section"] == 1 and l["citations"] == ["21 CFR 117.35"]
    assert l["version"] == dt.date.today().isoformat()

    bad = dict(form, questions="Q: no answer\n- a\n- b", citations="some rule")
    page = client.post("/manage/lesson/allergens", data=bad).text
    assert "Nothing was saved" in page and "mark the right answer" in page and "some rule" in page

    r = client.post("/manage/lesson/new", data=dict(form, title="Brand new lesson"), follow_redirects=False)
    assert r.status_code == 303 and "brand-new-lesson" in content.load_lessons()


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
    base = {
        "title": "Allergen cross-contact",
        "sections": "## One\nText one.",
        "questions": "@ 0:01\nQ: First?\n* yes\n- no",
        "citations": "21 CFR 117.35",
        "video": "",
    }
    clip = b"\x00\x00\x00\x18ftypmp42" + b"x" * 5000
    r = client.post("/manage/lesson/allergens", data=base,
                    files={"video_file": ("Floor clip.MP4", clip, "video/mp4")}, follow_redirects=False)
    assert r.status_code == 303
    video = content.load_lessons()["allergens"]["video"]
    assert video.startswith("/media/allergens-") and video.endswith(".mp4")

    full = client.get(video)
    assert full.status_code == 200 and full.content == clip and full.headers["content-type"] == "video/mp4"
    part = client.get(video, headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100  # seeking works
    assert "<video" in client.get("/manage/lesson/allergens").text

    # Replacing the video removes the old file.
    r = client.post("/manage/lesson/allergens", data=dict(base, video=video),
                    files={"video_file": ("second.webm", b"webm" * 100, "video/webm")}, follow_redirects=False)
    assert r.status_code == 303
    new = content.load_lessons()["allergens"]["video"]
    assert new != video and new.endswith(".webm")
    assert client.get(video).status_code == 404

    # Clearing the link removes the video.
    client.post("/manage/lesson/allergens", data=dict(base, video=""), follow_redirects=False)
    assert content.load_lessons()["allergens"]["video"] == ""
    assert client.get(new).status_code == 404


def test_video_upload_rejects_non_videos(client):
    base = {"title": "T", "sections": "## One\nText.", "questions": "", "citations": "", "video": ""}
    page = client.post("/manage/lesson/allergens", data=base, files={"video_file": ("notes.pdf", b"%PDF", "application/pdf")}).text
    assert "Nothing was saved" in page and "notes.pdf" in page
    page = client.post("/manage/lesson/allergens", data=dict(base, video="javascript:alert(1)")).text
    assert "should start with https://" in page
    for bad in ["../training.db", "x.exe", "a.mp4.exe"]:
        assert client.get(f"/media/{bad}").status_code == 404
