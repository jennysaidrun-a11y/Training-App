"""Keeps the cited rules current by checking the government's own sources.

- Federal rules (CFR): the eCFR versioner API gives each section's latest
  amendment date and official name.
- Upcoming federal changes: the Federal Register API lists recent rules and
  proposed rules for each CFR part we cite.
- California Title 8 (CCR): the DIR page for the section; a fingerprint of its
  text shows when it changes.

The result is content/rules_status.json. A lesson is flagged for review when a
rule it cites changed after the lesson was last reviewed, or when a citation
can't be found. A failed check never erases what we already knew: the old
result is kept, the error is recorded, and the next run tries again.

Run by hand:  python -m trainer.rules
"""
import datetime as dt
import hashlib
import html
import json
import re
import sys

import requests

from .content import CONTENT, load_lessons

STATUS_PATH = CONTENT / "rules_status.json"
TIMEOUT = 30
HEADERS = {"User-Agent": "bakery-training-app (rules checker)"}


def today():
    return dt.date.today().isoformat()


def load_status(path=None):
    path = path or STATUS_PATH
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"checked_at": None, "rules": {}, "federal_register": {}}


def save_status(status, path=None):
    path = path or STATUS_PATH
    path.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")


# ---- Sources ----------------------------------------------------------------

def fetch_cfr(c, get=requests.get):
    url = f"https://www.ecfr.gov/api/versioner/v1/versions/title-{c['title']}.json"
    r = get(url, params={"part": c["part"], "section": c["section"]}, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    versions = [v for v in r.json().get("content_versions", []) if v.get("identifier") == c["section"]]
    if not versions:
        return {"found": False}
    latest = max(versions, key=lambda v: v.get("amendment_date") or "")
    name = re.sub(r"\s+", " ", latest.get("name", "")).strip()
    return {
        "found": not latest.get("removed", False),
        "name": name,
        "changed_on": latest.get("amendment_date"),
    }


def _page_text(raw):
    raw = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<!--.*?-->", " ", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    return re.sub(r"\s+", " ", text).strip()


def fetch_ccr(c, get=requests.get):
    r = get(c["url"], headers=HEADERS, timeout=TIMEOUT)
    if r.status_code == 404:
        return {"found": False}
    r.raise_for_status()
    raw = r.content.decode("latin-1")
    m = re.search(r"(?is)<title>(.*?)</title>", raw)
    name = re.sub(r"\s+", " ", html.unescape(m.group(1))).strip() if m else ""
    name = re.sub(r"^California Code of Regulations, Title 8, ", "", name)
    text = _page_text(raw)
    # Only the rule itself: from its section heading onward.
    start = text.find(f"§{c['section']}.")
    body = text[start:] if start >= 0 else text
    return {
        "found": bool(name) and start >= 0,
        "name": name,
        "fingerprint": hashlib.sha256(body.encode()).hexdigest()[:16],
    }


def fetch_federal_register(title, part, get=requests.get):
    r = get(
        "https://www.federalregister.gov/api/v1/documents.json",
        params={
            "per_page": 5,
            "order": "newest",
            "conditions[cfr][title]": title,
            "conditions[cfr][part]": part,
            "conditions[type][]": ["RULE", "PRORULE"],
            "fields[]": ["title", "publication_date", "html_url", "type", "effective_on"],
        },
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json().get("results", [])


# ---- Checking ---------------------------------------------------------------

def cited_rules(lessons):
    rules = {}
    for lesson in lessons.values():
        for c in lesson["citations_parsed"]:
            if c["kind"] in ("cfr", "ccr"):
                rules[c["ref"]] = c
    return rules


def check_rule(c, old, get=requests.get):
    """Returns the new status entry for one rule; never loses what we knew."""
    entry = dict(old or {})
    entry.update({"ref": c["ref"], "url": c["url"], "kind": c["kind"]})
    try:
        got = fetch_cfr(c, get) if c["kind"] == "cfr" else fetch_ccr(c, get)
    except (requests.RequestException, ValueError) as e:
        entry["last_error"] = f"{today()}: {e.__class__.__name__}: {str(e)[:200]}"
        return entry
    entry.pop("last_error", None)
    entry["last_checked"] = today()
    entry["found"] = got["found"]
    if got.get("name"):
        entry["name"] = got["name"]
    if c["kind"] == "cfr":
        if got.get("changed_on"):
            entry["changed_on"] = got["changed_on"]
    elif got.get("fingerprint"):
        if entry.get("fingerprint") and entry["fingerprint"] != got["fingerprint"]:
            entry["changed_on"] = dt.datetime.now().isoformat(timespec="minutes")  # when we first saw the new text
        entry["fingerprint"] = got["fingerprint"]
    return entry


def run_check(lessons=None, path=None, get=requests.get):
    lessons = lessons if lessons is not None else load_lessons()
    status = load_status(path)
    rules = cited_rules(lessons)
    for ref, c in rules.items():
        status["rules"][ref] = check_rule(c, status["rules"].get(ref), get)
    parts = sorted({(c["title"], c["part"]) for c in rules.values() if c["kind"] == "cfr"})
    for title, part in parts:
        key = f"{title} CFR {part}"
        try:
            status["federal_register"][key] = {
                "documents": fetch_federal_register(title, part, get),
                "last_checked": today(),
            }
        except (requests.RequestException, ValueError) as e:
            status["federal_register"].setdefault(key, {})["last_error"] = f"{today()}: {e.__class__.__name__}"
    status["checked_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    save_status(status, path)
    return status


def lesson_flags(lesson, status):
    """Reasons this lesson needs a manager's review; empty list if it's current."""
    flags = []
    reviewed = lesson.get("reviewed_on") or "0000-00-00"
    for c in lesson["citations_parsed"]:
        if c["kind"] == "unknown":
            flags.append({"ref": c["ref"], "why": "This citation isn't in a format the checker knows (use '29 CFR 1910.147' or '8 CCR 3314').", "url": None})
            continue
        s = status.get("rules", {}).get(c["ref"])
        if not s or "found" not in s:
            continue  # not checked yet
        if not s["found"]:
            flags.append({"ref": c["ref"], "why": "This rule wasn't found at the official source. It may have been renumbered or removed.", "url": c["url"]})
        elif s.get("changed_on") and s["changed_on"] > reviewed:
            flags.append({"ref": c["ref"], "why": f"The rule changed on {s['changed_on'][:10]}, after this lesson was last reviewed ({reviewed[:10]}).", "url": c["url"]})
    return flags


def main():
    status = run_check()
    lessons = load_lessons()
    errors = [r for r, s in status["rules"].items() if s.get("last_error")]
    flagged = {lid: lesson_flags(l, status) for lid, l in lessons.items()}
    flagged = {k: v for k, v in flagged.items() if v}
    print(f"Checked {len(status['rules'])} rules. Lessons needing review: {len(flagged)}.")
    for lid, fl in flagged.items():
        for f in fl:
            print(f"  {lid}: {f['ref']}: {f['why']}")
    if errors:
        print("Couldn't reach the source for: " + ", ".join(errors) + " (kept the last known result).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
