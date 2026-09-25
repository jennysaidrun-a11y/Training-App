# Bakery Training

Training for the bakery's production crews (Baking, Mixing, Packaging / Warehouse,
Sanitation, Sanitation Supervisor), with lessons that stay tied to the current
government rules.

## Using it

Open this repo in a GitHub Codespace (**Code → Codespaces → Create codespace**). The
app starts by itself and opens in the browser. After that it looks after itself:
every minute it saves lesson edits back to GitHub and picks up any new version of
the app, then restarts. Nothing to push, pull or rebuild.

- **Workers** pick their name, see the lessons for their role, and take them.
  Lessons play in steps: an optional video that pauses to ask questions, short
  sections with checkpoint questions, then a quiz. A wrong answer shows why and
  lets them try again; the first try is what's scored (80% to pass).
- **Manager page** (`/manage`):
  - **Needs your review:** lessons whose rules changed since you last checked them,
    with links to the official text. Update the lesson, or mark it still correct.
  - **People:** add people by role and see who still has lessons to do. Lessons
    come back as to-dos when you make a real change to them, and once a year as a
    refresher.
  - **Lessons:** each lesson is edited as a deck of slides, in the order workers
    see them: a cover (title, who takes it, video), reading slides, question slides
    after the reading they check, and a last slide with the rules it's based on and
    source links. Add, reorder (drag, or Earlier/Later) and delete slides. Reading slides can
    have a picture (upload a photo; it shows above the text). A question
    can also pause the video at a time (like `1:30`).
  - **Research with Claude** (the panel beside the slides): paste a link to an
    existing training, or ask about a topic. Claude reads the official rules, drafts
    the slides, and every rule it cites is looked up on eCFR / DIR and marked Found or
    Not found. "Use this draft" puts it in the editor; nothing is saved until you
    press Save. It uses your own Claude account through Claude Code, which the
    Codespace installs by itself. You can also attach a PowerPoint, Word or PDF of an
    existing training (📎 in the chat); its text goes to Claude, the file isn't kept; sign in once (the panel shows the steps: run
    `claude` in a Codespace terminal). An `ANTHROPIC_API_KEY` secret works instead.
- **Rules page** (`/rules`): every cited rule, when it last changed, and recent or
  proposed federal rules for the same parts.

## How the rules stay current

Each lesson lists the rules it's based on, like `29 CFR 1910.147` or `8 CCR 3314`.
Every day (inside the app, and on GitHub by the "Nightly rules check" workflow even
when no one has it open) the app checks them against the official sources:

| Rules | Source | What it looks at |
|---|---|---|
| Federal (OSHA 29 CFR, FDA 21 CFR) | [eCFR](https://www.ecfr.gov) API | Each section's latest amendment date and name |
| Upcoming federal changes | [Federal Register](https://www.federalregister.gov) API | Rules and proposed rules for each cited CFR part |
| California (Cal/OSHA Title 8) | [DIR Title 8](https://www.dir.ca.gov/samples/search/query.htm) pages | A fingerprint of the rule's text |

If a rule changed after the lesson was last reviewed, or a cited rule can't be found
any more, the lesson shows up under **Needs your review**. If a source can't be
reached, the last known result is kept and the next check tries again.

## Privacy

This repo is public. Names, training records and uploaded videos live only in the
Codespace (`data/`) and are never saved to GitHub. Deleting the Codespace deletes them. Lessons and the rules status
are saved to GitHub.

## Starter content

Nine lessons written from the cited rules, with links to OSHA, FDA and Cal/OSHA
material: safety basics (IIPP), personal hygiene (GMPs), allergen cross-contact,
lockout/tagout, chemical safety (HazCom), mixers and machine guards, forklifts,
sanitation crew safety (chemicals, PPE, confined spaces) and indoor heat. None has a
video yet: add one on the lesson's edit page. They are a starting point for a
manager to check against the plant's own procedures, not legal advice.

## For developers

```
pip install -r requirements.txt
python -m trainer          # http://localhost:8000
python -m trainer.rules    # run the rules check by hand
python -m pytest
```
