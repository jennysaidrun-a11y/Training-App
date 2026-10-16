"""Text to speech: a natural voice reads a script, for video voiceovers and for "Listen" on
lesson slides. All free, open source and run on the server's processor, no account:
Kokoro reads Spanish and VieNeu-TTS reads Vietnamese (both clearer than Piper in those
languages); Piper reads the rest and is the backup when those two can't run. Kokoro and
VieNeu run in their own short process (`tts_engines.py`) so their models leave memory when
the speech is made. Each voice downloads once into data/models. Made speech is kept in
data/speech (same text, same voice = same file).
"""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import wave
from pathlib import Path

from . import video

VOICE_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
# key: (label, language, path in the Piper voices collection, engine, the engine's voice).
# The Piper voice is also the backup for the Kokoro and VieNeu voices.
VOICES = {
    "en-f": ("English · woman", "en", "en/en_US/lessac/medium/en_US-lessac-medium", "piper", None),
    "en-m": ("English · man", "en", "en/en_US/ryan/medium/en_US-ryan-medium", "piper", None),
    "es": ("Español · mujer", "es", "es/es_MX/claude/high/es_MX-claude-high", "kokoro", "ef_dora"),
    "es-m": ("Español · hombre", "es", "es/es_MX/claude/high/es_MX-claude-high", "kokoro", "em_alex"),
    "zh": ("中文", "zh", "zh/zh_CN/huayan/medium/zh_CN-huayan-medium", "piper", None),
    "vi": ("Tiếng Việt · nữ", "vi", "vi/vi_VN/vais1000/medium/vi_VN-vais1000-medium", "vieneu", "Ái Hân"),
    "vi-m": ("Tiếng Việt · nam", "vi", "vi/vi_VN/vais1000/medium/vi_VN-vais1000-medium", "vieneu", "Adam"),
    "ar": ("العربية", "ar", "ar/ar_JO/kareem/medium/ar_JO-kareem-medium", "piper", None),
}
LANG_VOICE = {"en": "en-f", "es": "es", "zh": "zh", "vi": "vi", "ar": "ar"}
SPEEDS = {"slow": 1.2, "normal": 1.0, "fast": 0.85}   # Piper's length scale: bigger is slower
ENGINE_SPEEDS = {"slow": 0.85, "normal": 1.0, "fast": 1.15}   # Kokoro / VieNeu: bigger is faster
ENGINE_MODULES = {"kokoro": "kokoro_onnx", "vieneu": "vieneu"}
MAX_CHARS = 5000


class SpeechError(RuntimeError):
    pass


def available():
    try:
        import piper  # noqa: F401
        return True
    except ImportError:
        return False


def engine_ready(engine):
    return engine == "piper" or bool(importlib.util.find_spec(ENGINE_MODULES[engine]))


def engine_of(voice):
    engine = VOICES[voice][3]
    return engine if engine_ready(engine) else "piper"


def voice_list():
    return [{"id": k, "label": v[0], "lang": v[1]} for k, v in VOICES.items()]


_voices, _lock = {}, threading.Lock()


def _voice(key, model_dir):
    import requests
    from piper import PiperVoice
    path = VOICES[key][2]
    if path not in _voices:
        model_dir = Path(model_dir) / "piper"
        model_dir.mkdir(parents=True, exist_ok=True)
        name = VOICES[key][2].rsplit("/", 1)[-1]
        onnx, conf = model_dir / f"{name}.onnx", model_dir / f"{name}.onnx.json"
        for path, suffix in ((conf, ".onnx.json"), (onnx, ".onnx")):
            if not path.exists():
                r = requests.get(VOICE_BASE + VOICES[key][2] + suffix, timeout=120, stream=True)
                r.raise_for_status()
                tmp = path.with_suffix(path.suffix + ".part")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                tmp.replace(path)
        _voices[path] = PiperVoice.load(str(onnx))
    return _voices[path]


def file_name(text, voice, speed, engine="piper"):
    key = f"{voice}|{speed}|{text}" if engine == "piper" else f"{engine}|{voice}|{speed}|{text}"
    return hashlib.sha1(key.encode()).hexdigest()[:20] + ".m4a"


def _clean(text):
    return " ".join(str(text or "").split())[:MAX_CHARS]


def _encode(wav, out, runner):
    part = out.with_name(out.stem + "-part.m4a")
    r = runner([video.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav),
                "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(part)],
               capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        part.unlink(missing_ok=True)
        raise SpeechError("The speech couldn't be made. Try again.")
    part.replace(out)


def _engine_make(texts, voice, speed, out_dir, model_dir, runner):
    """Kokoro / VieNeu: every missing line in one short process (the model loads once, then
    leaves memory). Lines it couldn't make are left to Piper."""
    engine = engine_of(voice)
    todo = [t for t in dict.fromkeys(texts) if not (out_dir / file_name(t, voice, speed, engine)).exists()]
    if engine == "piper" or not todo:
        return
    with tempfile.TemporaryDirectory() as tmp:
        items = [{"text": t, "wav": str(Path(tmp) / f"{i}.wav")} for i, t in enumerate(todo)]
        job = Path(tmp) / "job.json"
        job.write_text(json.dumps({"engine": engine, "voice": VOICES[voice][4], "speed": ENGINE_SPEEDS[speed],
                                   "model_dir": str(Path(model_dir).resolve()), "items": items}))
        env = dict(os.environ, HF_HOME=str(Path(model_dir).resolve() / "hf"))
        try:
            runner([sys.executable, "-m", "trainer.tts_engines", str(job)], capture_output=True, text=True,
                   timeout=1800, env=env, cwd=str(Path(__file__).resolve().parent.parent))
        except (OSError, subprocess.SubprocessError):
            return
        for it in items:
            if Path(it["wav"]).is_file() and Path(it["wav"]).stat().st_size > 1000:
                try:
                    _encode(it["wav"], out_dir / file_name(it["text"], voice, speed, engine), runner)
                except SpeechError:
                    pass


def speak(text, voice, speed, out_dir, model_dir, runner=subprocess.run):
    """Makes (or reuses) the speech for text. Returns (file name in out_dir, seconds)."""
    text = _clean(text)
    if not text:
        raise SpeechError("Write something to read out.")
    if voice not in VOICES or speed not in SPEEDS:
        raise SpeechError("Pick a voice and a speed.")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with _lock:
        _engine_make([text], voice, speed, out_dir, model_dir, runner)
        out = out_dir / file_name(text, voice, speed, engine_of(voice))
        if not out.exists():          # Piper: its own voice, or the backup
            out = out_dir / file_name(text, voice, speed)
        if not out.exists():
            from piper import SynthesisConfig
            with tempfile.TemporaryDirectory() as tmp:
                wav = Path(tmp) / "speech.wav"
                with wave.open(str(wav), "wb") as w:
                    _voice(voice, model_dir).synthesize_wav(text, w, syn_config=SynthesisConfig(length_scale=SPEEDS[speed]))
                _encode(wav, out, runner)
    return out.name, video.probe(out)["duration"]


MAX_LINES = 80


def track(lines, voice, speed, out_dir, model_dir, runner=subprocess.run, speak_fn=None):
    """A voiceover paced to the video: each line is spoken on its own and starts at its time
    ("at", seconds on the new video); a line without a time follows the one before it.
    Returns (file name in out_dir, seconds, [{at, duration}] per line)."""
    speak_fn = speak_fn or speak
    lines = [l for l in lines if " ".join(str(l.get("text") or "").split())][:MAX_LINES]
    if not lines:
        raise SpeechError("Write something to read out.")
    out_dir = Path(out_dir)
    if speak_fn is speak and voice in VOICES and speed in SPEEDS:   # Kokoro / VieNeu: all lines in one go
        out_dir.mkdir(parents=True, exist_ok=True)
        with _lock:
            _engine_make([_clean(l["text"]) for l in lines], voice, speed, out_dir, model_dir, runner)
    parts, placed, t = [], [], 0.0
    for l in lines:
        name, secs = speak_fn(l["text"], voice, speed, out_dir, model_dir)
        at = l.get("at")
        try:
            at = max(0.0, float(at)) if at not in (None, "") else None
        except (TypeError, ValueError):
            at = None
        at = round(t + (0.3 if placed else 0.0), 2) if at is None else round(at, 2)
        parts.append((out_dir / name, at))
        placed.append({"at": at, "duration": round(secs, 2)})
        t = at + secs
    if len(parts) == 1 and parts[0][1] == 0:
        return parts[0][0].name, placed[0]["duration"], placed
    key = "|".join(f"{p.name}@{at}" for p, at in parts)
    name = hashlib.sha1(("track|" + key).encode()).hexdigest()[:20] + ".m4a"
    out = out_dir / name
    with _lock:
        if not out.exists():
            cmd = [video.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y"]
            for p, _ in parts:
                cmd += ["-i", str(p)]
            chains = [f"[{i}:a]aresample=48000,adelay={int(at * 1000)}:all=1[a{i}]" for i, (_, at) in enumerate(parts)]
            mix = "".join(f"[a{i}]" for i in range(len(parts))) + f"amix=inputs={len(parts)}:duration=longest:normalize=0[out]"
            part = out.with_name(out.stem + "-part.m4a")
            r = runner(cmd + ["-filter_complex", ";".join(chains + [mix]), "-map", "[out]", "-c:a", "aac", "-b:a", "96k",
                              "-movflags", "+faststart", str(part)], capture_output=True, text=True, timeout=600)
            if r.returncode != 0:
                part.unlink(missing_ok=True)
                raise SpeechError("The voiceover couldn't be put together. Try again.")
            part.replace(out)
    return name, max(p["at"] + p["duration"] for p in placed), placed
