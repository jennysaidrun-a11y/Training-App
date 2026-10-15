# Bakery Training

Training app for the bakery's production roles (roles in `content/roles.yaml`),
California site: federal OSHA (29 CFR 1910), FDA food plant rules (21 CFR 117) and
Cal/OSHA (8 CCR, Title 8) apply.

## Layout
- `trainer/`: FastAPI app (`app.py`), lesson files (`content.py`), SQLite records
  (`db.py`), rules checker (`rules.py`), Jinja templates, `static/player.js` (the
  lesson player: video cue questions, section checkpoints, end quiz).
- Lesson editor: slides (`editor.py` converts sections + questions to/from slides,
  `static/editor.js`, JSON save at `/api/manage/lesson/<id>`). Its Claude panel is
  `research.py` (web search/fetch + a strict `propose_lesson` tool; citations are then
  checked live with `rules.check_rule`). Uses the API when `ANTHROPIC_API_KEY` is set, else
  Claude Code (`claude -p`, the user's signed-in account; `update.sh` installs it). Tests stub both.
- Media studio (Lessons > Media, `/manage/studio`, `static/studio.js`, shared by the lesson editor): media
  library (`data/media/library.json`: every upload, Drive copy and edit is kept until a manager deletes it, never swept;
  files used by a lesson can't be deleted; `/api/manage/media*`), picture editor (canvas: marker, highlighter,
  arrow, box, circle, numbers, text, blur, crop, turn, undo; saves a new copy), video editor and Google Drive.
- Video editor ("✂ Edit video": trim, cut out parts, slice a clip in two, join clips, mute, turn, "📸 Draw on this frame":
  `/api/manage/video/frame` takes the frame, the picture editor marks it up, and it goes in as a still between the slices).
  Play/Pause and Undo (every clip change; Ctrl+Z) sit under the picture; frames are taken from the player when the browser
  shows the video, else by the server. Effects are toggle buttons that change the preview at once (CSS filters/turn, Web Audio filters); "Exact preview"
  renders 5 s with `preview: true` (not kept in the library). `trainer/video.py`
  runs ffmpeg from the `imageio-ffmpeg` pip package (no other install) and makes a new 720p MP4; routes
  `/api/manage/video/edit` and `/api/manage/video/info`. Only files already in `data/media` can be edited.
  Pictures can be clips too (shown still for N seconds), so a video can be made from pictures alone.
  Touch-ups (`video.EXTRAS`): clean up sound (highpass/lowpass + afftdn), even volume (loudnorm), fade,
  brighter/sharper (eq + unsharp), steady (vid.stab two-pass after the main encode).
- Build from a project (`trainer/project.py`, button on Lessons and in the editor's Claude panel): the manager's
  videos + photos + a template (`content/templates.yaml`, edited in the dialog) + transcript. A background job
  (`/api/manage/project`, poll `/api/manage/project/<job>`) joins the videos, writes the transcript with faster-whisper
  (free, CPU, model in `data/models`) unless one is pasted, and runs the Claude panel research with the photos
  (CLI: copied into its working folder with Read allowed; API: image blocks). Questions get `at` (video pause times) from
  the transcript. The result goes through `app._settle_draft` (citations checked) and lands as a draft to preview.
- Text to speech (`trainer/tts.py`): Piper (free, open source, CPU), voices in `tts.VOICES` (downloaded once into
  `data/models/piper` from the Piper voices collection), speech cached in `data/speech` (`/speech/<hash>.m4a`, signed-in only).
  `POST /api/speech`: managers pick voice/speed (video editor "🎙 Voiceover": replace the sound or play over it, sent to
  `video.make(voice=...)`); workers get their language's voice ("🔊 Listen" on slides and questions in `player.js`).
- Google Drive: managers paste a share link ("Anyone with the link") in the video editor, the Video box or a
  picture slide; `/api/manage/drive` (`app._drive_copy`) copies the file into `data/media` from Google's fixed
  download address only, with size caps and a file-signature check. No Google account or key is stored.
  "Choose from Google Drive" (Google's sign-in + Picker, scope `drive.file`) shows once `GOOGLE_CLIENT_ID` and
  `GOOGLE_API_KEY` are set (GitHub secrets, staged to Fly by the deploy workflow); the browser's short-lived
  Google token is sent with the picked file id, used once by the server and never stored.
  The Drive button opens the app's own Drive browser (`studio.js` `browseDrive`: scope `drive.readonly`, the browser calls the
  Drive API itself; folders, Shared with me, Recent, search, Drive thumbnails, ▶ plays a video in Google's preview, tick videos
  and pictures together); Google's picker (`pickFromDrive` + `reviewPicked`) is the backup under ⋯. iPhone HEIC photos are
  turned into JPG on the way in (`app.heic_to_jpg`, pillow-heif) for uploads and Drive copies.
- Reading slides take videos (uploaded, Drive, library or YouTube links) as well as pictures, all in the slide's `images`
  list. Each reading slide has the same "🎬 Video ▾" menu as the cover, plus "🎙 Voiceover: read this slide aloud" (opens
  the video editor with the slide's words in the voiceover box; pictures stretch to fit the voice).
- Preview: the top bar's Preview and each slide's "👁 Preview slide" open `previewDeck` on the editor's current (unsaved)
  lesson; "Saved copy ↗" opens the saved one.
  The picker picks several files (list view; tabs for My Drive, Shared with me, shared drives; the signed-in account in
  its title), then a check step plays each video in Google's own preview (`drive.google.com/file/d/<id>/preview`) before
  anything is copied. Several files (or pictures) picked for the lesson video open the video editor to join them.
- Reading slides take videos as well as pictures: both live in the slide's `images` list (video by file extension);
  the player shows videos full width with their own controls and pauses them when the slide changes.
- iPad/iPhone: `static/manifest.json` + `icon-*.png` (from `app.ico`) so Add to Home Screen opens it full screen.
- `content/lessons/*.yaml`: one lesson per file. `version` = last change that makes
  everyone retake it; `reviewed_on` = last time it was checked against its rules.
  Citations must parse (`29 CFR 1910.147`, `8 CCR 3314`).
- `content/rules.yaml`: the managed rules list (ref, name, folder), edited in each folder on Manager > Lessons;
  rules a lesson cites are added by themselves (`content.sync_rules`) and can't be deleted while cited.
  New rules are checked at the official source before they are added.
- `content/rules_status.json`: written by the rules checker (app + nightly workflow).
- `data/`: employee records (`training.db`) and uploaded videos (`media/`, served at
  `/media/<name>` with range requests). Gitignored; the repo is public. Never commit it.

## Rules
- Lesson facts come from the cited government rule; every lesson cites at least one.
  Verify a new citation exists at the source (eCFR / dir.ca.gov) before adding it.
- Screens: one tab style (`.subnav` underline tabs) in Manager and Lessons; one short line per page, longer help behind
  `{{ info("...") }}` (ⓘ, hover or tap); one main button per page or item, extras in a `details.more-menu` (⋯); long lists fold in `<details>`.
- Drag and drop: every place a manager adds a file (slide pictures, videos, attached training files)
  must take a dragged-in file as well as tap-to-choose, with a visible drop area. Keep it that way in new screens.
- Videos: only add ones the user picks or that are clearly public domain / openly
  licensed; don't guess.
- Pictures: from the user's own files, or Wikimedia Commons photos whose license the app itself
  checks (public domain, CC0, CC BY, CC BY-SA; `app._settle_pictures`), saved to `data/media` and credited in sources.
- The user never commits, pushes or rebuilds by hand: `update.sh` (started detached
  from the Codespace's postStartCommand) auto-saves `content/` and pulls new code every minute. On the Windows work PC
  the same job is `update.ps1`, started by double-clicking `start.bat` (http://localhost:8001); it also puts a "Training App" desktop shortcut (`open.ps1`: start if needed, then open it in its own Edge/Chrome app window via `--app`; once moved online it opens the address in `data\moved_online` instead). Keep both working and in step.
- Online copy (Fly.io): `Dockerfile`, `fly.toml`, `fly/start.sh` (keeps its own clone on the /data volume
  and runs `update.sh`, so it updates itself like the PC); `.github/workflows/fly-deploy.yml` deploys when
  the `FLY_API_TOKEN` secret exists. Its Claude Code signs in with the user's Claude account through the
  `CLAUDE_CODE_OAUTH_TOKEN` secret (`claude setup-token`), not an API key. Only one copy may be live with real records at a time.
- Run `python -m pytest` before committing.
