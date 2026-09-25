"""The editor's Claude panel: draft a lesson from an existing training link, or
research one by chat. Claude searches the web and reads pages itself (server
tools), then hands back a lesson as slides through the `propose_lesson` tool.
Every rule it cites is checked against eCFR / DIR before the manager sees it.

Two ways to reach Claude:
- "api": the Claude API, when ANTHROPIC_API_KEY is set.
- "cli": otherwise, Claude Code (`claude -p`) signed in with the user's own Claude
  account. update.sh installs it; the user signs in once in a terminal.
Without either, the panel explains how to set one up.
"""
import json
import os
import shutil
import subprocess
import tempfile

from . import content

MODEL = "claude-opus-5"
MAX_CONTINUATIONS = 5

SYSTEM = """You help a manager at a commercial bakery in California write short safety and \
food-safety training lessons for hourly production workers (roles: {roles}).

How lessons work in this app: a lesson is a deck of slides that a worker goes through on a phone \
in about 5-10 minutes. Reading slides have a short heading and 2-5 plain sentences (or a short \
numbered list). Question slides have one multiple-choice question with 2-4 answers, one right \
answer, and a one-sentence "why". Put a question after the reading it checks. Aim for 3-6 reading \
slides and 2-5 questions. Write at a 6th-8th grade reading level, in the second person, about the \
actual jobs in a bakery (mixers, sheeters, ovens, proofers, packaging lines, forklifts, sanitation).

Rules and sources:
- Base every fact on the government rule it comes from. Federal: OSHA 29 CFR 1910, FDA 21 CFR 117. \
California: Cal/OSHA Title 8 (8 CCR). Look the rule up (ecfr.gov, dir.ca.gov, osha.gov, fda.gov) \
before citing it, and cite exact sections in the form "29 CFR 1910.147" or "8 CCR 3314". \
Never guess a section number; leave a citation out if you couldn't confirm it.
- When the manager gives a link to an existing training, read it and use it as the starting \
point, but write the lesson in your own words (don't copy it) and list the link as a source.
- When the manager attaches a file (in <attached_file>), it's their existing training: keep its \
order and the plant-specific details (machines, steps, names of areas), fix anything the rules \
don't support, and rewrite it as slides in the app's format. Mention in your reply anything you \
dropped or changed and why.
- Keep it to what the rule actually says; don't add requirements that aren't there.

When you have a lesson (or an updated one), call propose_lesson with the whole lesson, then reply \
in 1-3 short sentences: what you drafted, and anything the manager should check or decide \
(for example a rule you couldn't confirm, or where the plant's own procedure should be added). \
If the manager is only asking a question, answer it briefly without calling the tool. \
The manager's current draft, if any, is included with their message; edit it rather than \
starting over when they ask for changes."""


def lesson_tool(role_ids):
    return {
        "name": "propose_lesson",
        "description": "Put a drafted lesson into the manager's editor. Always send the whole lesson.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["title", "summary", "roles", "slides", "citations", "sources"],
            "properties": {
                "title": {"type": "string", "description": "Short lesson title."},
                "summary": {"type": "string", "description": "One sentence: what the worker will learn."},
                "roles": {"type": "array", "items": {"type": "string", "enum": ["all", *role_ids]},
                          "description": "Who takes it. Use ['all'] for everyone."},
                "slides": {
                    "type": "array",
                    "description": "In the order the worker sees them.",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["type", "heading", "text", "q", "choices", "answer", "why"],
                        "properties": {
                            "type": {"type": "string", "enum": ["reading", "question"]},
                            "heading": {"type": "string", "description": "Reading slides only; '' for questions."},
                            "text": {"type": "string", "description": "Reading slides only; '' for questions."},
                            "q": {"type": "string", "description": "Question slides only; '' for readings."},
                            "choices": {"type": "array", "items": {"type": "string"}, "description": "Question slides only; [] for readings."},
                            "answer": {"type": "integer", "description": "Index of the right choice; 0 for readings."},
                            "why": {"type": "string", "description": "Why the right answer is right; '' for readings."},
                        },
                    },
                },
                "citations": {"type": "array", "items": {"type": "string"},
                              "description": "Exact sections you confirmed, e.g. '29 CFR 1910.147', '8 CCR 3314'."},
                "sources": {
                    "type": "array",
                    "items": {"type": "object", "additionalProperties": False, "required": ["title", "url"],
                              "properties": {"title": {"type": "string"}, "url": {"type": "string"}}},
                },
            },
        },
    }


CLI_MODEL = os.environ.get("CLAUDE_MODEL", "opus")
CLI_TIMEOUT = 600


def claude_command():
    found = shutil.which(os.environ.get("CLAUDE_COMMAND", "claude"))
    if found:
        return found
    # Installed after the app started, so not on its PATH yet.
    import glob
    for path in [os.path.expanduser("~/.local/bin/claude"), os.path.expanduser("~/.claude/local/claude"),
                 "/usr/local/bin/claude", *sorted(glob.glob("/home/*/.local/bin/claude"))]:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def mode():
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return "api"
    return "cli" if claude_command() else None


def available():
    return mode() is not None


class NotSignedIn(RuntimeError):
    pass


def _clean_slides(slides):
    out = []
    for s in slides:
        if s.get("type") == "reading":
            out.append({"type": "reading", "heading": s.get("heading", "").strip(), "text": s.get("text", "").strip()})
        elif s.get("type") == "question":
            choices = [c for c in s.get("choices", []) if c.strip()]
            answer = s.get("answer", 0)
            out.append({"type": "question", "q": s.get("q", "").strip(), "choices": choices,
                        "answer": answer if 0 <= answer < len(choices) else 0, "why": s.get("why", "").strip(), "at": None})
    return out


def run(history, draft, client=None):
    """history: [{"role": "user"|"assistant", "text": ...}], newest last (a user turn).
    draft: the editor's current lesson (editor_payload shape) or None.
    Returns {"reply": str, "lesson": dict | None, "searched": [urls]}."""
    if client is None and mode() == "cli":
        return run_cli(history, draft)
    return run_api(history, draft, client)


def _with_draft(text, draft):
    if draft and (draft.get("slides") or draft.get("title")):
        text += "\n\n<current_draft>\n" + json.dumps(
            {k: draft.get(k) for k in ("title", "summary", "roles", "slides", "citations", "sources")}, indent=1
        ) + "\n</current_draft>"
    return text


FORMAT_RULES = """

The lesson format the app accepts (it rejects anything else):
- title: short. summary: one sentence. roles: ["all"] or role ids.
- slides, in the order the worker sees them. A reading slide has a heading and text (blank line \
between paragraphs; lines starting "1." "2." show as numbered steps). A question slide has q, 2-4 \
choices, answer (index of the right choice) and why. At least one reading; every question comes \
after the reading it checks.
- citations: exact sections like "29 CFR 1910.147" or "8 CCR 3314". sources: title + https url.

Finished lessons from this bakery, as slides. Match their tone, length and reading level:
"""
EXAMPLE_LESSONS = ("lockout-tagout", "allergens")


def _examples():
    from . import editor
    lessons = content.load_lessons()
    out = []
    for lid in EXAMPLE_LESSONS:
        if lid in lessons:
            p = editor.editor_payload(lessons[lid])
            out.append(json.dumps({k: p[k] for k in ("title", "summary", "roles", "slides", "citations", "sources")}, indent=1))
    return "".join(f"<example_lesson>\n{e}\n</example_lesson>\n" for e in out)


def _system():
    roles = content.load_roles()
    system = SYSTEM.format(roles=", ".join(f"{r['name']} ({r['id']})" for r in roles)) + FORMAT_RULES + _examples()
    return system, [r["id"] for r in roles]


def _finish(proposed, role_ids):
    if not proposed:
        return None
    return {
        "title": proposed.get("title", "").strip(),
        "summary": proposed.get("summary", "").strip(),
        "roles": [r for r in proposed.get("roles", []) if r == "all" or r in role_ids] or ["all"],
        "slides": _clean_slides(proposed.get("slides", [])),
        "citations": [content.parse_citation(c)["ref"] for c in proposed.get("citations", []) if content.parse_citation(c)],
        "sources": [s for s in proposed.get("sources", []) if str(s.get("url", "")).startswith(("http://", "https://"))],
    }


CLI_NOTE = """

You are running without the propose_lesson tool. Instead, your final answer is JSON matching the \
given schema: "reply" is your 1-3 sentence message to the manager; set "has_lesson" true and fill \
"lesson" with the whole lesson when you drafted or changed one; otherwise has_lesson is false and \
lesson can be empty. Use WebSearch and WebFetch to look up the rules."""


def run_cli(history, draft, command=None, runner=subprocess.run):
    """The same research through Claude Code, signed in with the user's Claude account."""
    system, role_ids = _system()
    lesson_schema = lesson_tool(role_ids)["input_schema"]
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["reply", "has_lesson", "lesson"],
        "properties": {"reply": {"type": "string"}, "has_lesson": {"type": "boolean"}, "lesson": lesson_schema},
    }
    earlier = "".join(f"<{t['role']}>\n{t['text']}\n</{t['role']}>\n" for t in history[:-1])
    prompt = (f"<conversation_so_far>\n{earlier}</conversation_so_far>\n\n" if earlier else "") + \
        _with_draft(history[-1]["text"], draft)
    cmd = [command or claude_command(), "-p", "--output-format", "json", "--json-schema", json.dumps(schema),
           "--system-prompt", system + CLI_NOTE, "--tools", "WebSearch,WebFetch",
           "--allowedTools", "WebSearch", "WebFetch", "--model", CLI_MODEL, "--no-session-persistence"]
    with tempfile.TemporaryDirectory() as empty:   # no project files for it to read
        try:
            r = runner(cmd, input=prompt, capture_output=True, text=True, timeout=CLI_TIMEOUT, cwd=empty)
        except subprocess.TimeoutExpired:
            raise RuntimeError("Claude took too long. Try a narrower topic.")
    try:
        out = json.loads(r.stdout)
    except ValueError:
        out = {}
    if r.returncode != 0 or out.get("is_error") or not out:
        tail = (r.stdout + r.stderr)[-400:].lower()
        if any(w in tail for w in ("login", "log in", "/login", "api key", "authenticat", "oauth", "not signed")):
            raise NotSignedIn("Claude Code isn't signed in yet.")
        raise RuntimeError(f"Claude Code stopped: {(r.stdout + r.stderr)[-300:].strip()}")
    result = out.get("structured_output")
    if not isinstance(result, dict):
        try:
            result = json.loads(out.get("result") or "")
        except ValueError:
            result = {"reply": out.get("result", ""), "has_lesson": False}
    lesson = _finish(result.get("lesson"), role_ids) if result.get("has_lesson") else None
    reply = (result.get("reply") or "").strip() or ("The draft is ready." if lesson else "")
    return {"reply": reply, "lesson": lesson, "searched": []}


def run_api(history, draft, client=None):
    """history: [{"role": "user"|"assistant", "text": ...}], newest last (a user turn).
    draft: the editor's current lesson (editor_payload shape) or None.
    Returns {"reply": str, "lesson": dict | None, "searched": [urls]}."""
    import anthropic

    client = client or anthropic.Anthropic(timeout=300.0)
    system, role_ids = _system()

    messages = []
    for turn in history[:-1]:
        messages.append({"role": turn["role"], "content": turn["text"] or "…"})
    messages.append({"role": "user", "content": _with_draft(history[-1]["text"], draft)})

    tools = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 6},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 6},
        lesson_tool(role_ids),
    ]
    proposed, texts, searched = None, [], []
    for _ in range(MAX_CONTINUATIONS):
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=32000,
            system=system,
            messages=messages,
            tools=tools,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            response = stream.get_final_message()

        for block in response.content:
            if block.type == "text":
                texts.append(block.text)
            elif block.type == "tool_use" and block.name == "propose_lesson":
                proposed = block.input if isinstance(block.input, dict) else json.loads(block.input)
            elif block.type == "web_fetch_tool_result":
                url = getattr(getattr(block, "content", None), "url", None)
                if url:
                    searched.append(url)

        if response.stop_reason == "refusal":
            return {"reply": "Claude couldn't help with that request. Try rewording it.", "lesson": None, "searched": searched}
        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue
        if response.stop_reason == "tool_use" and proposed is not None and not any(t.strip() for t in texts):
            # Let Claude say what it drafted, now that the draft is in hand.
            tool_id = next(b.id for b in response.content if b.type == "tool_use" and b.name == "propose_lesson")
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tool_id, "content": "The draft is in the editor."}]})
            continue
        break

    lesson = _finish(proposed, role_ids)
    reply = "\n\n".join(t.strip() for t in texts if t.strip()) or ("The draft is ready." if lesson else "")
    return {"reply": reply, "lesson": lesson, "searched": searched}
