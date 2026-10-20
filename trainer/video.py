"""The lesson editor's video editor: trim, cut out parts, join clips and pictures, mute, turn,
plus touch-ups: clean up sound (noise reduction), even out the volume, fade in and out,
brighter/sharper picture, steady a shaky video.

The work is done by ffmpeg, the free open-source video tool, from the imageio-ffmpeg
package (pip installs a ready-made copy on Windows, Mac and Linux, so nothing else to
set up). Every edit makes a new 720p MP4 (H.264 + AAC) that plays on iPads, phones and
computers; the clips it came from are left as they were.
"""
import os
import pathlib
import re
import subprocess
import tempfile
import threading
from pathlib import Path

MAX_CLIPS = 20          # slicing a video makes more clips, so leave room
MAX_PIECES = 40          # kept pieces across all clips
MIN_PIECE = 0.05         # seconds; shorter slivers are dropped
STILL_SECONDS = 5        # how long a picture shows unless the manager picks another time
MAX_STILL = 120
TIMEOUT = 30 * 60
TURNS = {0: "", 90: "transpose=1,", 180: "transpose=1,transpose=1,", 270: "transpose=2,"}
FADE = 0.6               # seconds
# Touch-ups (all free ffmpeg filters).
CLEAN = "highpass=f=80,lowpass=f=12000,afftdn=nr=14:nf=-35:tn=1"   # hum, hiss and steady background noise
LEVEL = "loudnorm=I=-16:TP=-1.5:LRA=11"                            # same loudness all the way through
BRIGHT = "eq=brightness=0.04:contrast=1.08:saturation=1.15,unsharp=5:5:0.5"
EXTRAS = ("clean", "level", "fade", "bright", "steady")
lock = threading.Lock()  # one edit at a time, so a small server isn't swamped


class EditError(ValueError):
    """Something about the edit the manager should fix (shown to them as is)."""


def ffmpeg_exe():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def probe(path, runner=subprocess.run):
    """{"duration": seconds, "audio": bool} for a video file."""
    r = runner([ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True,
               encoding="utf-8", errors="replace", timeout=120)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr or "")
    if not m:
        raise EditError("That video couldn't be read. Try uploading it again.")
    h, mi, s = m.groups()
    return {"duration": int(h) * 3600 + int(mi) * 60 + float(s), "audio": bool(re.search(r"Stream #.*Audio:", r.stderr))}


def tidy(path, runner=subprocess.run):
    """A video made in a browser (recorded as it played) gets a proper index, so it shows its length
    and can be jumped around in. Copies the streams as they are: seconds of work, nothing re-made."""
    path = pathlib.Path(path)
    out = path.with_name(".tidy-" + path.name)
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(path), "-c", "copy"]
    cmd += ["-movflags", "+faststart"] if path.suffix == ".mp4" else []
    r = runner(cmd + [str(out)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    if r.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
        out.unlink(missing_ok=True)
        return False
    os.replace(out, path)
    return True


def frame(src, t, out_path, runner=subprocess.run):
    """Saves the picture at second t of a video as a JPG (for drawing on it), at most 1920 wide."""
    t = max(0.0, float(t))
    r = runner([ffmpeg_exe(), "-hide_banner", "-y", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1",
                "-vf", "scale='min(1920,iw)':-2", "-q:v", "2", str(out_path)],
               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    if r.returncode != 0 or not pathlib.Path(out_path).is_file() or pathlib.Path(out_path).stat().st_size == 0:
        pathlib.Path(out_path).unlink(missing_ok=True)
        raise EditError("That frame couldn't be taken. Try a moment a little earlier.")
    return out_path


def keep_pieces(start, end, cuts, duration):
    """The parts of one clip that stay: [start, end] minus each cut, in order."""
    start = max(0.0, min(float(start or 0), duration))
    end = duration if end in (None, "") else max(start, min(float(end), duration))
    pieces = [(start, end)]
    for a, b in sorted((max(0.0, float(a)), float(b)) for a, b in cuts or []):
        if b <= a:
            continue
        nxt = []
        for p, q in pieces:
            if b <= p or a >= q:
                nxt.append((p, q))
                continue
            if a > p:
                nxt.append((p, a))
            if b < q:
                nxt.append((b, q))
        pieces = nxt
    return [(round(p, 3), round(q, 3)) for p, q in pieces if q - p >= MIN_PIECE]


def plan(clips, infos):
    """[(input index, from, to)] for every kept piece of every clip, in play order."""
    if not clips:
        raise EditError("Add at least one clip.")
    if len(clips) > MAX_CLIPS:
        raise EditError(f"Use at most {MAX_CLIPS} clips in one video.")
    out = []
    for i, c in enumerate(clips):
        out += [(i, a, b) for a, b in keep_pieces(c.get("start"), c.get("end"), c.get("cuts"), infos[i]["duration"])]
    if not out:
        raise EditError("Nothing would be left of the video. Keep at least part of one clip.")
    if len(out) > MAX_PIECES:
        raise EditError(f"That's more than {MAX_PIECES} pieces. Use fewer cuts.")
    return out


# Kept small so a 1 GB server never runs out of memory: each piece is made on its own (one
# input decoded at a time, two threads), then the pieces are joined and finished at 720p.
ENCODE = ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-threads", "2"]


def piece_command(path, a, b, still, has_audio, want_audio, turn, out_path):
    """One kept piece (a to b seconds of a clip, or a picture shown b - a seconds) as a 720p,
    30 fps piece with stereo 48 kHz sound (silence if the clip has none), ready to be joined."""
    secs = str(round(b - a, 3))
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-threads", "2"]
    cmd += ["-loop", "1", "-framerate", "30", "-t", secs, "-i", str(path)] if still else ["-ss", str(a), "-t", secs, "-i", str(path)]
    silent = want_audio and not has_audio
    if silent:
        cmd += ["-f", "lavfi", "-t", secs, "-i", "anullsrc=r=48000:cl=stereo"]
    cmd += ["-filter_threads", "1", "-vf", f"fps=30,{TURNS.get(int(turn or 0), '')}scale=1280:720:force_original_aspect_ratio=decrease:flags=fast_bilinear,"
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p", "-map", "0:v:0"]
    if want_audio:
        cmd += ["-map", "1:a:0" if silent else "0:a:0", "-af", "aresample=48000,aformat=channel_layouts=stereo",
                "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-an"]
    return cmd + ENCODE + ["-crf", "18", "-t", secs, str(out_path)]


def finish_command(join_list, total, want_audio, out_path, extras=(), voice=None):
    """Joins the pieces (listed in join_list for ffmpeg's concat reader) and adds the touch-ups
    from EXTRAS ("steady" is a second pass, see make). voice: (speech file, "replace" | "mix"):
    a voiceover laid over the whole video, replacing its sound or over it with the sound quieter."""
    audio = want_audio and not (voice and voice[1] == "replace")
    total = round(total, 3)
    fade = "fade" in extras and total > 3 * FADE
    vpost = [BRIGHT] if "bright" in extras else []
    if fade:
        vpost += [f"fade=t=in:st=0:d={FADE}", f"fade=t=out:st={round(total - FADE, 3)}:d={FADE}"]
    parts = [f"[0:v]{','.join(vpost) or 'null'}[v]"]
    fades = [f"afade=t=in:st=0:d={FADE}", f"afade=t=out:st={round(total - FADE, 3)}:d={FADE}"] if fade else []
    mix = bool(voice and audio and voice[1] == "mix")
    if audio:
        apost = ([CLEAN] if "clean" in extras else []) + ([LEVEL] if "level" in extras else []) + fades
        apost.append("aresample=48000")   # loudnorm works at a high rate; back to normal
        parts.append(f"[0:a]{','.join(apost)}[{'ao' if mix else 'a'}]")
    if voice:
        vo = f"[1:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=duration={total}"
        if mix:   # the voice over the video's own sound, which drops to a quarter
            parts.append(vo + "[vo]")
            parts.append("[ao]volume=0.25[bg];[bg][vo]amix=inputs=2:duration=first:normalize=0" + (f",{','.join(fades)}" if fades else "") + "[a]")
        else:
            parts.append(vo + (f",{','.join(fades)}" if fades else "") + "[a]")
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-threads", "2",
           "-f", "concat", "-safe", "0", "-i", str(join_list)]
    if voice:
        cmd += ["-i", str(voice[0])]
    cmd += ["-filter_threads", "1", "-filter_complex", ";".join(parts), "-map", "[v]"]
    cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "128k"] if audio or voice else ["-an"]
    return cmd + ENCODE + ["-crf", "23", "-movflags", "+faststart", str(out_path)]


def steady_commands(src, out_path):
    """Two ffmpeg passes that steady a shaky video (vid.stab): measure the shake, then
    smooth it out. Run in the output's folder so the shake file needs no path."""
    trf = out_path.stem + ".trf"
    return [[ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-i", src.name,
             "-vf", f"vidstabdetect=shakiness=6:accuracy=12:result={trf}", "-f", "null", "-"],
            [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-i", src.name,
             "-vf", f"vidstabtransform=input={trf}:smoothing=20:zoom=2,unsharp=5:5:0.6", "-c:a", "copy",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", out_path.name]], out_path.parent / trf


def make(clips, paths, out_path, mute=False, turn=0, runner=subprocess.run, extras=(), voice=None, progress=None):
    """Makes the edited video at out_path. clips: [{start, end, cuts}] matching paths;
    a picture's clip also has "seconds" (how long it shows). extras: touch-ups (EXTRAS).
    progress(text), if given, hears each step. Returns {"duration": new length in seconds}."""
    say = progress or (lambda text: None)
    stills, infos = {}, []
    for i, (c, p) in enumerate(zip(clips, paths)):
        if "seconds" in c:
            secs = round(min(max(float(c["seconds"] or STILL_SECONDS), 0.5), MAX_STILL), 2)
            stills[i] = secs
            infos.append({"duration": secs, "audio": False})
        else:
            infos.append(probe(p, runner))
    pieces = plan(clips, infos)
    audio = None if mute else [x["audio"] for x in infos]
    extras = tuple(x for x in extras if x in EXTRAS)
    out_path = Path(out_path)
    first = out_path.with_name(out_path.stem + "-pass1.mp4") if "steady" in extras else out_path
    if not lock.acquire(timeout=1):
        raise EditError("Another video is being made right now. Try again when it's done.")
    want_audio = bool(audio and any(audio))
    run = lambda cmd, **kw: runner(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT, **kw)
    try:
        with tempfile.TemporaryDirectory(prefix=".making-", dir=str(out_path.parent)) as tmp:
            names = []
            for n, (i, a, b) in enumerate(pieces):
                say(f"Preparing part {n + 1} of {len(pieces)}…")
                piece = Path(tmp) / f"piece{n:03d}.mp4"
                r = run(piece_command(paths[i], a, b, i in stills, bool(audio and audio[i]), want_audio, turn, piece))
                if r.returncode != 0:
                    break
                names.append(piece.name)
            else:
                join_list = Path(tmp) / "join.txt"
                join_list.write_text("".join(f"file '{name}'\n" for name in names))
                say("Putting the parts together…")
                r = run(finish_command(join_list, sum(b - a for _, a, b in pieces), want_audio, first, extras, voice))
        if r.returncode == 0 and "steady" in extras:
            say("Steadying the picture…")
            cmds, trf = steady_commands(first, out_path)
            try:
                for cmd in cmds:
                    r = runner(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=TIMEOUT, cwd=str(out_path.parent))
                    if r.returncode != 0:
                        break
            finally:
                first.unlink(missing_ok=True)
                trf.unlink(missing_ok=True)
    except subprocess.TimeoutExpired:
        raise EditError("Making the video took too long. Try a shorter video.")
    finally:
        lock.release()
    if first != out_path:
        first.unlink(missing_ok=True)
    if r.returncode != 0:
        raise EditError("The video couldn't be made: " + (r.stderr or "unknown error").strip()[-300:])
    return {"duration": round(sum(b - a for _, a, b in pieces), 2)}
