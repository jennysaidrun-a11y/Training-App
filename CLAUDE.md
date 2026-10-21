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
  arrow, box, circle, numbers, text, blur, crop, turn, undo/redo; ↖ Select (and every new shape) shows handles to move or
  resize a mark after it's drawn (`opBox`/`opHandles`/`opResized`), 🗑 Delete; "🖼 Choose picture" switches to another
  library picture or edit; saves a new copy), video editor and Google Drive.
- Video editor ("✂ Edit video": trim, cut out parts, slice a clip in two, join clips, mute, turn, "📸 Draw on this frame":
  `/api/manage/video/frame` takes the frame, the picture editor marks it up, and it goes in as a still between the slices).
  Laid out like a video app: a vertical toolbox on the left (`.vedit-toolbox`: Slice, Start, End, Cut, Draw, Voice, Add,
  Sound, Undo; a row on phones), and under the picture ◀︎| Play (runs through every clip and picture in order) |▶︎ (one frame; ← → or , .), Undo (every clip change; Ctrl+Z)
  and a timeline (`.tl`: ruler, a block per clip (pictures show as thumbnails; drag one to change the order, tap to jump
  there), a block per voiceover line). Dragging on the ruler or voice track scrubs with the
  picture following; dragging a voice block moves that line (the picture shows where it lands) and re-makes the voice.
  Draw and Text (a filled text box typed right on the picture) work on a video frame or a picture clip. Nothing closes on a
  click outside; the clips and voiceover lines auto save in the browser (`localStorage` "video-draft:…") and come back when
  the same videos are opened again ("Start over" drops them). They're also saved in the app as drafts (`/api/manage/drafts`,
  `data/media/drafts.json`, every 2 s after a change; Media > Drafts lists and reopens them on any computer; a blank "Make a video"
  starts fresh; making the video deletes its draft; a draft's clips count as used). The lesson editor keeps unsaved changes the same way
  ("lesson-draft:<id>"; workers only see changes after Save lesson, since a saved lesson is live).
  Frames are taken from the player when the browser shows the video, else by the server. Effects are toggle buttons that change the preview at once (CSS filters/turn, Web Audio filters); "Exact preview"
  renders 5 s with `preview: true` (not kept in the library). `trainer/video.py`
  runs ffmpeg from the `imageio-ffmpeg` pip package (no other install) and makes a new 720p MP4 piece by piece
  (`piece_command` per kept piece, two threads, then `finish_command` joins them and adds touch-ups/voice) so it fits
  the 1 GB Fly machine (one big filter graph got ffmpeg OOM-killed on 4K phone video); routes
  `/api/manage/video/edit` and `/api/manage/video/info`. "Make the video" runs as a background job
  (`background: true` returns `{job}`; the editor polls `/api/manage/video/edit/<job>` for step/state) so no request hangs.
  Normally it's made on the manager's own computer instead (`makeHere`: clips are first copied into the browser (blob URLs) so playback never waits on the network; the browser plays each kept part into a 1280x720 canvas
  with the turn/effects/fades and records it with the sound and voiceover via MediaRecorder, H.264 MP4 only, in real time),
  pausing while the window is hidden; stuck 10 s, or any failure there, switches to the server way by itself;
  then uploaded with `made_here` so the server only re-indexes it ("Keep working ↘" tucks the editor away while it's made;
  `makingBar` shows progress in the corner, tap to reopen; `video.tidy`, stream copy). The server way is used when the
  browser can't record H.264, for "Steady", or from "Make it on the server instead" after a failure.
  Fly's auto-stop is off (`fly.toml`): the app turns the server off itself (`_idle_watch`: 15 quiet minutes, no open
  request, nothing being made; writes `IDLE_FLAG`, and `update.sh` then saves and exits 0), and Fly starts it on the next visit.
  While a video or project job runs, `app.keep_awake` keeps one slow request open to the app's own Fly address (`/awake`,
  secret key per start) so Fly's auto-stop doesn't stop the machine mid-job. A video copied in twice (upload or Drive) is kept
  once (`_keep_one_copy`, same size + sha256); renders and uploads refuse when the disk is nearly full (`FULL`). On each Fly start `fly/start.sh` runs `fly/dedupe.py`
  (standalone, baked into the image): removes repeat copies of the same video no lesson points to and leftover `.making-*`;
  removed names go in `media/aliases.json`, which the `/media` route and `_media_file` follow (`_same_as`). Only files already in `data/media` can be edited.
  A made video keeps its edit (clips, cuts, pictures, voiceover lines/voice, effects) in its library entry ("project",
  `/api/manage/media/project`); opening that video in the editor reopens the edit ("Edit the finished video instead" for
  the flat file). Clips an edit uses count as used (`_media_users`, `fly/dedupe.py`), so they can't be deleted.
  Polling a server job uses `?wait=1` (held up to 25 s) so a request is always open and Fly doesn't auto-stop mid-job; a
  restart (404) re-starts the job; uploads of a browser-made video retry for a few minutes.
  The toolbox ➕ Add (and "🖼 Choose pictures & edits") opens the selector (`openLibrary` with `multi`, `upload`): tabs All /
  Pictures / Videos / My edits (`isEdit`: made videos, marked-up pictures, frames), numbered in the order tapped, files from
  this computer added right there (tap or drag) come in ticked.
  Pictures can be clips too (shown still for N seconds), so a video can be made from pictures alone.
  Touch-ups (`video.EXTRAS`): clean up sound (highpass/lowpass + afftdn), even volume (loudnorm), fade,
  brighter/sharper (eq + unsharp), steady (vid.stab two-pass after the main encode).
- Build from a project (`trainer/project.py`, button on Lessons and in the editor's Claude panel): the manager's
  videos + photos + a template (`content/templates.yaml`, edited in the dialog) + transcript. A background job
  (`/api/manage/project`, poll `/api/manage/project/<job>`) joins the videos, writes the transcript with faster-whisper
  (free, CPU, model in `data/models`) unless one is pasted, and runs the Claude panel research with the photos
  (CLI: copied into its working folder with Read allowed; API: image blocks). Questions get `at` (video pause times) from
  the transcript. The result goes through `app._settle_draft` (citations checked) and lands as a draft to preview.
- Text to speech (`trainer/tts.py`): Google Chirp 3 HD voices (`GOOGLE_VOICES`, REST with the `GOOGLE_TTS_KEY` GitHub secret,
  staged to Fly) when set, capped at `FREE_CHARS` a month (`data/speech/google-usage.json`) so it stays in Google's free
  allowance; otherwise free, open source, CPU. English and Spanish are Kokoro (`kokoro-onnx`, int8 model from its
  release page into `data/models/kokoro`), Vietnamese is VieNeu-TTS v3 Nano (`vieneu`, Hugging Face cache in `data/models/hf`;
  needs onnxruntime < 1.23), the rest Piper (`data/models/piper`), which is also the backup when those can't run. Kokoro and
  VieNeu run in a short child process (`trainer/tts_engines.py`, all of a voiceover's lines at once) so their models leave
  memory (Fly has 1 GB). Voices in `tts.VOICES`; speech cached in `data/speech` (`/speech/<hash>.m4a`, signed-in only).
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
- Lesson facts come from the cited government rule. A lesson cites at least one government rule, or, for an in-house
  procedure, lists the company's own rules in plain words (`company_rules`; plain-words entries typed in the citation box
  move there, `editor.split_rules`); sources are optional. Claude panel drafts still need a citation.
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
