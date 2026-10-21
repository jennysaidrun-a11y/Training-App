"""The Kokoro (English and Spanish) and VieNeu-TTS (Vietnamese) voices, run by tts.py in a short process of
their own: `python -m trainer.tts_engines job.json`. The job names the engine, its voice, the
speed and the lines ({text, wav}); each line is written as a WAV file. The models download once
into data/models (Kokoro from its project's release page, VieNeu from Hugging Face via HF_HOME),
and leave memory when this process ends (the online copy has 1 GB).
"""
import json
import sys
from pathlib import Path

KOKORO_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO_FILES = ("kokoro-v1.0.int8.onnx", "voices-v1.0.bin")


def _download(url, path):
    import requests
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(url, timeout=120, stream=True)
    r.raise_for_status()
    tmp = path.with_suffix(path.suffix + ".part")
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(1 << 20):
            f.write(chunk)
    tmp.replace(path)


def kokoro(job):
    import soundfile as sf
    from kokoro_onnx import Kokoro
    folder = Path(job["model_dir"]) / "kokoro"
    for name in KOKORO_FILES:
        _download(KOKORO_BASE + name, folder / name)
    k = Kokoro(str(folder / KOKORO_FILES[0]), str(folder / KOKORO_FILES[1]))
    for it in job["items"]:
        samples, rate = k.create(it["text"], voice=job["voice"], speed=job["speed"], lang="en-us" if job["voice"][0] in "ab" else "es")
        sf.write(it["wav"], samples, rate)


def vieneu(job):
    import soundfile as sf
    from vieneu import Vieneu
    tts = Vieneu(mode="v3nano")          # the small model: fits the online copy's memory
    for it in job["items"]:
        sf.write(it["wav"], tts.infer(it["text"], voice=job["voice"], speed=job["speed"]), tts.sample_rate)


if __name__ == "__main__":
    job = json.loads(Path(sys.argv[1]).read_text())
    {"kokoro": kokoro, "vieneu": vieneu}[job["engine"]](job)
