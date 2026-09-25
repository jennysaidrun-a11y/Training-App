"""The slide editor's view of a lesson.

A lesson is stored as sections plus questions (each question either follows a
section, pauses the video at a time, or comes at the end). The editor shows the
same thing as an ordered deck of slides, the way workers go through it:

    {"type": "reading",  "heading": ..., "text": ..., "image": "/media/x.jpg" | ""}
    {"type": "question", "q": ..., "choices": [...], "answer": 0, "why": ..., "at": 90 | None}

A question belongs to the reading slide before it. Questions before the first
reading only make sense with a time on the video.
"""
from . import content


def to_slides(lesson):
    slides = []
    questions = lesson.get("questions", [])
    placed = set()

    def q_slide(q):
        return {"type": "question", "q": q.get("q", ""), "choices": list(q.get("choices", [])),
                "answer": q.get("answer", 0), "why": q.get("why", ""), "at": q.get("at")}

    # Timed questions that aren't tied to a section go first: they belong to the video.
    for i, q in enumerate(questions):
        if q.get("at") is not None and q.get("after_section") is None:
            slides.append(q_slide(q))
            placed.add(i)
    for si, s in enumerate(lesson.get("sections", [])):
        slides.append({"type": "reading", "heading": s.get("heading", ""), "text": s.get("text", "").strip(),
                       "image": s.get("image") or ""})
        for i, q in enumerate(questions):
            if i not in placed and q.get("after_section") == si:
                slides.append(q_slide(q))
                placed.add(i)
    for i, q in enumerate(questions):
        if i not in placed:
            slides.append(q_slide(q))
    return slides


def from_slides(slides):
    """Returns (sections, questions, errors). Slide numbers in errors start at 1."""
    sections, questions, errors = [], [], []
    for n, s in enumerate(slides, 1):
        kind = s.get("type")
        if kind == "reading":
            heading, text = (s.get("heading") or "").strip(), (s.get("text") or "").strip()
            if not heading:
                errors.append({"slide": n, "error": "This reading slide needs a heading."})
            if not text:
                errors.append({"slide": n, "error": "This reading slide needs some text."})
            section = {"heading": heading, "text": text + "\n"}
            image = (s.get("image") or "").strip()
            if image:
                if image.startswith("/media/") or image.startswith("https://"):
                    section["image"] = image
                else:
                    errors.append({"slide": n, "error": "The picture link should start with https://"})
            sections.append(section)
        elif kind == "question":
            choices = [c.strip() for c in s.get("choices") or [] if c and c.strip()]
            q = {"q": (s.get("q") or "").strip(), "choices": choices, "answer": s.get("answer", 0)}
            if not q["q"]:
                errors.append({"slide": n, "error": "This question slide needs a question."})
            if len(choices) < 2:
                errors.append({"slide": n, "error": "A question needs at least two answers."})
            if not isinstance(q["answer"], int) or not 0 <= q["answer"] < max(len(choices), 1):
                errors.append({"slide": n, "error": "Mark which answer is right."})
            if (s.get("why") or "").strip():
                q["why"] = s["why"].strip()
            at = s.get("at")
            if at not in (None, ""):
                try:
                    q["at"] = int(at)
                except (TypeError, ValueError):
                    errors.append({"slide": n, "error": "The video time should be like 1:30."})
            if sections:
                q["after_section"] = len(sections) - 1
            elif q.get("at") is None:
                errors.append({"slide": n, "error": "Put a reading slide before this question, or set a time to ask it during the video."})
            questions.append(q)
        else:
            errors.append({"slide": n, "error": "Unknown slide type."})
    if not sections:
        errors.append({"slide": None, "error": "Add at least one reading slide."})
    return sections, questions, errors


def editor_payload(lesson):
    """Everything the editor page needs, answers included (it's the manager side)."""
    return {
        "id": lesson.get("id", ""),
        "title": lesson.get("title", ""),
        "summary": lesson.get("summary", ""),
        "roles": lesson.get("roles", ["all"]),
        "video": lesson.get("video") or "",
        "slides": to_slides(lesson),
        "citations": list(lesson.get("citations", [])),
        "sources": [{"title": s.get("title", ""), "url": s.get("url", "")} for s in lesson.get("sources", [])],
    }


def check_meta(data):
    errors = []
    if not (data.get("title") or "").strip():
        errors.append({"slide": 0, "error": "The lesson needs a title."})
    video = (data.get("video") or "").strip()
    if video and not (video.startswith(("http://", "https://")) or video.startswith("/media/")):
        errors.append({"slide": 0, "error": "The video link should start with https://"})
    for c in data.get("citations") or []:
        if not content.parse_citation(c):
            errors.append({"slide": "rules", "error": f"'{c}' should look like '29 CFR 1910.147' or '8 CCR 3314'."})
    for s in data.get("sources") or []:
        if not str(s.get("url", "")).startswith(("http://", "https://")):
            errors.append({"slide": "rules", "error": f"The source link for '{s.get('title', '')}' should start with https://"})
    return errors
