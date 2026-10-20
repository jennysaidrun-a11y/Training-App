"""Build a lesson from a project: the manager's videos and photos, a template for how the
slides go, and a transcript of what's said in the videos. Claude writes the whole slide
lesson (the same Claude panel research, so every rule is still looked up and checked).

Steps, run in the background so the page can show progress:
1. Join the videos into one lesson video (ffmpeg, trainer/video.py), if there's more than one.
2. The transcript: the manager's own, or written from the video's sound with faster-whisper
   (free, open source, runs on the server's processor; the model downloads once into data/models).
3. Claude builds the slides following the template, using the transcript (questions can pause
   the video at the right moment) and looking at the photos (each goes on the slide it shows).
"""
import datetime as dt
import secrets
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import yaml

from . import content, research, video

WHISPER_MODEL = "base"          # ~145 MB, fast enough on one shared processor
MAX_VIDEOS = 10
MAX_PHOTOS = 30
MAX_TRANSCRIPT = 60000           # characters sent to Claude

DEFAULT_TEMPLATES = [{
    "id": "standard",
    "name": "Standard safety lesson",
    "outline": (
        "1. Why it matters: one reading slide on the hazard, who it affects, and what can go wrong.\n"
        "2. What the rule says: the regulation's requirements in plain words.\n"
        "3. The video: 2-3 questions that pause the video right after the key moments (use the transcript times).\n"
        "4. Step by step: our plant's procedure from the video as numbered steps, one reading slide per stage, "
        "with the project photos on the slides they show.\n"
        "5. A question after each reading slide.\n"
        "6. Recap: 3-5 short points to remember."),
}]


# ---- Templates (content/templates.yaml, saved by the app and auto-saved like lessons) ----

def load_templates():
    path = content.CONTENT / "templates.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    out = [t for t in (data or []) if isinstance(t, dict) and t.get("id") and t.get("name")]
    return out or [dict(t) for t in DEFAULT_TEMPLATES]


def save_template(name, outline, template_id=""):
    """Adds a template, or changes the one with template_id. Returns its id."""
    name, outline = " ".join(str(name or "").split())[:80], str(outline or "").strip()[:4000]
    if not name or not outline:
        raise ValueError("A template needs a name and an outline.")
    templates = load_templates()
    hit = next((t for t in templates if t["id"] == template_id), None)
    if hit:
        hit.update(name=name, outline=outline)
    else:
        base = content.slugify(name) or "template"
        template_id = base
        n = 2
        while any(t["id"] == template_id for t in templates):
            template_id, n = f"{base}-{n}", n + 1
        templates.append({"id": template_id, "name": name, "outline": outline})
    header = "# Lesson templates for 'Build from a project' (edited in the lesson editor).\n"
    (content.CONTENT / "templates.yaml").write_text(
        header + yaml.safe_dump(templates, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")
    return template_id


def delete_template(template_id):
    templates = [t for t in load_templates() if t["id"] != template_id]
    if not templates:
        raise ValueError("Keep at least one template.")
    header = "# Lesson templates for 'Build from a project' (edited in the lesson editor).\n"
    (content.CONTENT / "templates.yaml").write_text(
        header + yaml.safe_dump(templates, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")


# ---- Transcript ------------------------------------------------------------------------

def can_transcribe():
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


_model, _model_lock = None, threading.Lock()


def transcribe(path, model_dir):
    """[(start_seconds, text)] for what's said in a video, or [] when there's no speech."""
    global _model
    from faster_whisper import WhisperModel
    with _model_lock:
        if _model is None:
            _model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8", download_root=str(model_dir))
        segments, _info = _model.transcribe(str(path), vad_filter=True)
        return [(s.start, s.text.strip()) for s in segments if s.text.strip()]


def clock(t):
    t = int(t)
    return f"{t // 60}:{t % 60:02d}"


def as_text(segments):
    return "\n".join(f"[{clock(t)}] {text}" for t, text in segments)


# ---- Photos for Claude to look at ---------------------------------------------------------

def small_copy(src, out_path, runner=subprocess.run):
    """A JPG at most 1280 wide, so Claude can look at it without a huge upload."""
    runner([video.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-frames:v", "1",
            "-vf", "scale='min(1280,iw)':-2", "-q:v", "4", str(out_path)], capture_output=True, timeout=60)
    return Path(out_path).is_file()


# ---- The prompt -----------------------------------------------------------------------------

def prompt(template, transcript, photos, notes, video_link, video_seconds):
    parts = ["Build a complete lesson from this project, following the template. "
             "Write it in your own words for our workers; the transcript is what our trainer says in the video, "
             "so keep our plant's steps, machine names and order from it. Look up and cite the rules as usual.",
             f"<template name=\"{template['name']}\">\n{template['outline']}\n</template>"]
    if video_link:
        parts.append(f"<lesson_video length=\"{clock(video_seconds)}\">The lesson plays this video ({video_link}). "
                     "A question slide can pause the video and ask at a moment: set its \"at\" to the seconds from the "
                     "transcript times (for example 95 for 1:35), right after the thing it checks is said. "
                     f"Keep every \"at\" under {int(video_seconds)}. Use -1 for questions asked after a reading slide.</lesson_video>")
    if transcript:
        parts.append(f"<transcript>\n{transcript[:MAX_TRANSCRIPT]}\n</transcript>")
    else:
        parts.append("<transcript>(none: there was no speech to write down, so build the lesson from the template, "
                     "the photos and the rules)</transcript>")
    if photos:
        lines = "\n".join(f"- {p['file']}: {p['label']} -> use the link {p['link']}" for p in photos)
        parts.append("<photos>\nOur own photos. Look at each one (they are image files in your working folder, or "
                     "attached), then put it in \"images\" of the reading slide it shows, using its link exactly. "
                     "A photo can be left out if it shows nothing useful.\n" + lines + "\n</photos>")
    if notes:
        parts.append(f"<manager_notes>\n{notes[:4000]}\n</manager_notes>")
    return "\n\n".join(parts)


# ---- Background jobs ----------------------------------------------------------------------

_jobs, _jobs_lock = {}, threading.Lock()


def job(job_id):
    with _jobs_lock:
        j = _jobs.get(job_id)
        return dict(j) if j else None


def busy():
    with _jobs_lock:
        return any(j["state"] == "running" for j in _jobs.values())


def start(spec, media_dir, finish, run=None):
    """spec: {videos: [Path], photos: [(link, Path, label)], template, transcript, notes, lesson}.
    finish(result) settles Claude's draft (citations checked, pictures) and returns it.
    Returns the job id; poll job(id)."""
    job_id = secrets.token_hex(8)
    with _jobs_lock:
        for k in [k for k, j in _jobs.items() if time.time() - j["started"] > 6 * 3600]:
            del _jobs[k]
        _jobs[job_id] = {"state": "running", "step": "Starting…", "started": time.time()}
    threading.Thread(target=_work, args=(job_id, spec, media_dir, finish, run or research.run), daemon=True).start()
    return job_id


def _set(job_id, **kw):
    with _jobs_lock:
        _jobs[job_id].update(kw)


def _work(job_id, spec, media_dir, finish, run):
    try:
        _set(job_id, result=build(spec, media_dir, finish, run, step=lambda s: _set(job_id, step=s)), state="done", step="Done")
    except research.NotSignedIn:
        _set(job_id, state="failed", error="signin")
    except (video.EditError, ValueError) as e:
        _set(job_id, state="failed", error=str(e))
    except Exception as e:  # shown to the manager; they can try again
        print(f"Project build failed: {e.__class__.__name__}: {e}")
        _set(job_id, state="failed", error=f"Claude couldn't finish that ({e.__class__.__name__}). Try again in a minute.")


def build(spec, media_dir, finish, run=research.run, step=lambda s: None):
    videos, notes_for_manager = list(spec.get("videos") or []), []
    video_link, seconds = "", 0.0
    if len(videos) > 1:
        step(f"Joining {len(videos)} videos into one…")
        out = f"{content.slugify(spec.get('lesson') or 'lesson')}-project-{dt.datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}.mp4"
        made = video.make([{"start": 0, "end": None, "cuts": []} for _ in videos], videos, media_dir / out)
        joined, seconds = media_dir / out, made["duration"]
        video_link = f"/media/{out}"
    elif videos:
        joined = videos[0]
        seconds = video.probe(joined)["duration"]
        video_link = f"/media/{joined.name}"
    transcript = str(spec.get("transcript") or "").strip()
    if not transcript and videos:
        if can_transcribe():
            step("Writing down what's said in the video…")
            transcript = as_text(transcribe(joined, media_dir.parent / "models"))
            if not transcript:
                notes_for_manager.append("No speech was found in the video, so the slides come from the template and photos.")
        else:
            notes_for_manager.append("The transcript tool isn't installed on this copy yet, so Claude worked without one. "
                                     "You can paste a transcript and build again.")
    step("Claude is building the slides and checking the rules… this can take a few minutes.")
    with tempfile.TemporaryDirectory() as tmp:
        photos = []
        for n, (link, path, label) in enumerate(spec.get("photos") or [], 1):
            small = Path(tmp) / f"photo-{n}.jpg"
            if small_copy(path, small):
                photos.append({"file": small.name, "path": small, "link": link, "label": label or link.rsplit("/", 1)[-1]})
        text = prompt(spec["template"], transcript, photos, spec.get("notes"), video_link, seconds)
        result = run([{"role": "user", "text": text}], None, images=[(p["file"], p["path"]) for p in photos])
    if not result.get("lesson"):
        raise ValueError((result.get("reply") or "Claude didn't send back a lesson.") + " Try again, or add a note on what you want.")
    lesson = result["lesson"]
    lesson["video"] = video_link
    for s in lesson["slides"]:
        if s.get("type") == "question" and s.get("at") is not None and (not video_link or s["at"] >= seconds):
            s["at"] = None
    result = finish(result)
    result["transcript"] = transcript
    if notes_for_manager:
        result["reply"] = ((result.get("reply") or "") + "\n\n" + " ".join(f"({n})" for n in notes_for_manager)).strip()
    return result
