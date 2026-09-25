"""Lessons, roles and citations, stored as YAML files under content/."""
import datetime as dt
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"
LESSONS = CONTENT / "lessons"

CFR_RE = re.compile(r"^\s*(\d+)\s*CFR\s*(\d+)\.(\w+)\s*$", re.I)
CCR_RE = re.compile(r"^\s*8\s*CCR\s*(\d+(?:\.\d+)?)\s*$", re.I)


def parse_citation(ref):
    """'29 CFR 1910.147' or '8 CCR 3314' -> dict with where to check it, or None."""
    m = CFR_RE.match(ref)
    if m:
        title, part, sec = m.groups()
        section = f"{part}.{sec}"
        return {
            "ref": f"{title} CFR {section}",
            "kind": "cfr",
            "title": title,
            "part": part,
            "section": section,
            "url": f"https://www.ecfr.gov/current/title-{title}/section-{section}",
        }
    m = CCR_RE.match(ref)
    if m:
        section = m.group(1)
        return {
            "ref": f"8 CCR {section}",
            "kind": "ccr",
            "section": section,
            "url": f"https://www.dir.ca.gov/title8/{section}.html",
        }
    return None


def now_stamp():
    """Review times are stored to the minute so a same-day rule change and review compare correctly."""
    return dt.datetime.now().isoformat(timespec="minutes")


def load_roles():
    return yaml.safe_load((CONTENT / "roles.yaml").read_text()) or []


def role_names():
    return {r["id"]: r["name"] for r in load_roles()}


def _normalize(data, path):
    data.setdefault("id", path.stem)
    data.setdefault("roles", ["all"])
    data.setdefault("sections", [])
    data.setdefault("questions", [])
    data.setdefault("citations", [])
    data.setdefault("sources", [])
    data.setdefault("video", "")
    data.setdefault("summary", "")
    rev = data.get("reviewed_on")
    if isinstance(rev, dt.datetime):
        data["reviewed_on"] = rev.isoformat(timespec="minutes")
    elif isinstance(rev, dt.date):
        data["reviewed_on"] = rev.isoformat()
    ver = data.get("version") or data.get("reviewed_on")
    data["version"] = ver.isoformat() if isinstance(ver, dt.date) else ver
    data["citations_parsed"] = [parse_citation(c) or {"ref": c, "kind": "unknown"} for c in data["citations"]]
    return data


def load_lessons():
    lessons = {}
    for path in sorted(LESSONS.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError:
            continue  # a broken file never takes the app down; the manager page lists it
        lessons[data.get("id", path.stem)] = _normalize(data, path)
    return lessons


def broken_lesson_files():
    bad = []
    for path in sorted(LESSONS.glob("*.yaml")):
        try:
            yaml.safe_load(path.read_text())
        except yaml.YAMLError as e:
            bad.append((path.name, str(e).splitlines()[0]))
    return bad


def lessons_for_role(lessons, role):
    return [l for l in lessons.values() if "all" in l["roles"] or role in l["roles"]]


def save_lesson(lesson):
    keep = ["id", "title", "roles", "version", "reviewed_on", "summary", "video", "sections", "questions", "citations", "sources"]
    data = {k: lesson.get(k) for k in keep}
    path = LESSONS / f"{data['id']}.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100))
    return path


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "lesson"


# ---- Questions as plain text, for the manager's editor ----------------------
#
#   @ 1:30                    (optional: pause the video here)
#   after section 2           (optional: ask after this section; numbered from 1)
#   Q: What do you do first?
#   * The right answer
#   - A wrong answer
#   Why: explanation
#
# Questions are separated by a blank line.

def _fmt_time(sec):
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}"


def _parse_time(text):
    parts = [int(p) for p in text.strip().split(":")]
    total = 0
    for p in parts:
        total = total * 60 + p
    return total


def questions_to_text(questions):
    blocks = []
    for q in questions:
        lines = []
        if q.get("at") is not None:
            lines.append(f"@ {_fmt_time(q['at'])}")
        if q.get("after_section") is not None:
            lines.append(f"after section {q['after_section'] + 1}")
        lines.append(f"Q: {q['q']}")
        for i, c in enumerate(q["choices"]):
            lines.append(f"{'*' if i == q['answer'] else '-'} {c}")
        if q.get("why"):
            lines.append(f"Why: {q['why']}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def questions_from_text(text):
    """Returns (questions, errors)."""
    questions, errors = [], []
    for n, block in enumerate(re.split(r"\n\s*\n", text.strip()), 1):
        if not block.strip():
            continue
        q = {"choices": []}
        answer = None
        for line in block.splitlines():
            s = line.strip()
            if not s:
                continue
            if s.startswith("@"):
                try:
                    q["at"] = _parse_time(s[1:])
                except ValueError:
                    errors.append(f"Question {n}: couldn't read the time '{s}'. Use @ 1:30.")
            elif s.lower().startswith("after section"):
                try:
                    q["after_section"] = int(s.split()[-1]) - 1
                except ValueError:
                    errors.append(f"Question {n}: couldn't read '{s}'.")
            elif s[:2].lower() == "q:":
                q["q"] = s[2:].strip()
            elif s[:4].lower() == "why:":
                q["why"] = s[4:].strip()
            elif s[0] in "*-":
                if s[0] == "*":
                    answer = len(q["choices"])
                q["choices"].append(s[1:].strip())
            else:
                errors.append(f"Question {n}: didn't understand the line '{s}'.")
        if not q.get("q"):
            errors.append(f"Question {n}: needs a line starting with Q:")
        if len(q["choices"]) < 2:
            errors.append(f"Question {n}: needs at least two answers (lines starting with * or -).")
        if answer is None:
            errors.append(f"Question {n}: mark the right answer with * at the start of its line.")
        q["answer"] = answer or 0
        questions.append(q)
    return questions, errors


def sections_to_text(sections):
    return "\n\n".join(f"## {s['heading']}\n{s['text'].strip()}" for s in sections)


def sections_from_text(text):
    sections = []
    for chunk in re.split(r"^##\s*", text.strip(), flags=re.M):
        if not chunk.strip():
            continue
        heading, _, body = chunk.partition("\n")
        sections.append({"heading": heading.strip(), "text": body.strip() + "\n"})
    return sections
