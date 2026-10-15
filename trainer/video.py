"""The lesson editor's video editor: trim, cut out parts, join clips and pictures, mute, turn,
plus touch-ups: clean up sound (noise reduction), even out the volume, fade in and out,
brighter/sharper picture, steady a shaky video.

The work is done by ffmpeg, the free open-source video tool, from the imageio-ffmpeg
package (pip installs a ready-made copy on Windows, Mac and Linux, so nothing else to
set up). Every edit makes a new 720p MP4 (H.264 + AAC) that plays on iPads, phones and
computers; the clips it came from are left as they were.
"""
import re
import subprocess
import threading
from pathlib import Path

MAX_CLIPS = 10
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


def command(paths, pieces, audio, turn, out_path, stills=None, extras=()):
    """The ffmpeg command line (a list, never run through a shell). audio: per input,
    whether it has sound; None or all False makes a silent video. A clip without
    sound gets silence, so it can sit next to clips that have some. stills: {input
    index: seconds} for pictures, which are shown still for that long. extras: touch-ups
    from EXTRAS ("steady" is a second pass, see make)."""
    stills = stills or {}
    audio = audio if audio and any(audio) else None
    rot = TURNS.get(int(turn or 0), "")
    parts, labels = [], []
    for n, (i, a, b) in enumerate(pieces):
        parts.append(f"[{i}:v]trim=start={a}:end={b},setpts=PTS-STARTPTS,{rot}"
                     "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,"
                     f"setsar=1,fps=30,format=yuv420p[v{n}]")
        labels.append(f"[v{n}]")
        if audio and audio[i]:
            parts.append(f"[{i}:a]atrim=start={a}:end={b},asetpts=PTS-STARTPTS,aresample=48000,"
                         f"aformat=channel_layouts=stereo[a{n}]")
        elif audio:
            parts.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={round(b - a, 3)}[a{n}]")
        if audio:
            labels.append(f"[a{n}]")
    parts.append("".join(labels) + f"concat=n={len(pieces)}:v=1:a={1 if audio else 0}[vc]" + ("[ac]" if audio else ""))
    total = round(sum(b - a for _, a, b in pieces), 3)
    fade = "fade" in extras and total > 3 * FADE
    vpost = [BRIGHT] if "bright" in extras else []
    if fade:
        vpost += [f"fade=t=in:st=0:d={FADE}", f"fade=t=out:st={round(total - FADE, 3)}:d={FADE}"]
    parts.append(f"[vc]{','.join(vpost) or 'null'}[v]")
    if audio:
        apost = ([CLEAN] if "clean" in extras else []) + ([LEVEL] if "level" in extras else [])
        if fade:
            apost += [f"afade=t=in:st=0:d={FADE}", f"afade=t=out:st={round(total - FADE, 3)}:d={FADE}"]
        apost.append("aresample=48000")   # loudnorm works at a high rate; back to normal
        parts.append(f"[ac]{','.join(apost)}[a]")
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y"]
    for i, p in enumerate(paths):
        if i in stills:
            cmd += ["-loop", "1", "-framerate", "30", "-t", str(stills[i])]
        cmd += ["-i", str(p)]
    cmd += ["-filter_complex", ";".join(parts), "-map", "[v]"]
    cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "128k"] if audio else ["-an"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(out_path)]
    return cmd


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


def make(clips, paths, out_path, mute=False, turn=0, runner=subprocess.run, extras=()):
    """Makes the edited video at out_path. clips: [{start, end, cuts}] matching paths;
    a picture's clip also has "seconds" (how long it shows). extras: touch-ups (EXTRAS).
    Returns {"duration": new length in seconds}."""
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
    if not lock.acquire(blocking=False):
        raise EditError("Another video is being made right now. Try again in a minute.")
    try:
        r = runner(command(paths, pieces, audio, turn, first, stills, extras), capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=TIMEOUT)
        if r.returncode == 0 and "steady" in extras:
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
