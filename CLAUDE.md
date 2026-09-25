# Bakery Training

Training app for the bakery's production roles (roles in `content/roles.yaml`),
California site: federal OSHA (29 CFR 1910), FDA food plant rules (21 CFR 117) and
Cal/OSHA (8 CCR, Title 8) apply.

## Layout
- `trainer/`: FastAPI app (`app.py`), lesson files (`content.py`), SQLite records
  (`db.py`), rules checker (`rules.py`), Jinja templates, `static/player.js` (the
  lesson player: video cue questions, section checkpoints, end quiz).
- `content/lessons/*.yaml`: one lesson per file. `version` = last change that makes
  everyone retake it; `reviewed_on` = last time it was checked against its rules.
  Citations must parse (`29 CFR 1910.147`, `8 CCR 3314`).
- `content/rules_status.json`: written by the rules checker (app + nightly workflow).
- `data/`: employee records (`training.db`) and uploaded videos (`media/`, served at
  `/media/<name>` with range requests). Gitignored; the repo is public. Never commit it.

## Rules
- Lesson facts come from the cited government rule; every lesson cites at least one.
  Verify a new citation exists at the source (eCFR / dir.ca.gov) before adding it.
- Videos: only add ones the user picks or that are clearly public domain / openly
  licensed; don't guess.
- The user never commits, pushes or rebuilds by hand: `update.sh` (started detached
  from the Codespace's postStartCommand) auto-saves `content/` and pulls new code every minute. Keep it working.
- Run `python -m pytest` before committing.
