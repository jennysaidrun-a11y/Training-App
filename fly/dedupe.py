"""Frees space on the online server's disk before the app starts (run by fly/start.sh).

The same video copied in more than once (same size and contents) is kept once: every copy a
lesson points to stays, and of the copies no lesson uses, only one is kept (when no lesson uses
any). Old links to a removed copy keep working (aliases.json, read by the app's /media route). Half-made videos left by a restart (.making-* folders, .tidy-* files) are removed too.
Standalone on purpose: it runs even when the app's own copy can't update because the disk is full.

    python3 dedupe.py <media folder> <content folder>
"""
import hashlib
import json
import pathlib
import re
import shutil
import sys

VIDEO = {".mp4", ".m4v", ".mov", ".webm"}


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def used_names(content):
    names = set()
    for path in pathlib.Path(content).rglob("*"):
        if path.is_file() and path.suffix in (".yaml", ".yml", ".json"):
            try:
                names |= set(re.findall(r"/media/([A-Za-z0-9._-]+)", path.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                pass
    return names


def dedupe(media, content):
    media = pathlib.Path(media)
    if not media.is_dir():
        return 0
    freed = 0
    for leftover in list(media.glob(".making-*")) + list(media.glob(".tidy-*")):
        size = sum(p.stat().st_size for p in leftover.rglob("*") if p.is_file()) if leftover.is_dir() else leftover.stat().st_size
        shutil.rmtree(leftover, ignore_errors=True) if leftover.is_dir() else leftover.unlink(missing_ok=True)
        freed += size
    used = used_names(content)
    by_size = {}
    for path in media.iterdir():
        if path.is_file() and path.suffix.lower() in VIDEO and not path.name.startswith("."):
            by_size.setdefault(path.stat().st_size, []).append(path)
    lib_path = media / "library.json"
    try:
        lib = json.loads(lib_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        lib = None
    alias_path = media / "aliases.json"
    try:
        aliases = json.loads(alias_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        aliases = {}
    for paths in by_size.values():
        if len(paths) < 2:
            continue
        groups = {}
        for path in paths:
            groups.setdefault(sha(path), []).append(path)
        for same in groups.values():
            if len(same) < 2:
                continue
            same.sort(key=lambda p: p.name)          # names start with the upload time, so oldest first
            keep = [p for p in same if p.name in used] or same[:1]
            for path in same:
                if path in keep:
                    continue
                if lib is not None and path.name in lib:
                    entry = lib.pop(path.name)
                    lib.setdefault(keep[0].name, entry)
                aliases[path.name] = keep[0].name       # the app answers the old link with the kept copy
                freed += path.stat().st_size
                path.unlink(missing_ok=True)
                print(f"Removed a repeat copy: {path.name} (same as {keep[0].name})")
    if aliases:
        alias_path.write_text(json.dumps(aliases, indent=1), encoding="utf-8")
    if lib is not None:
        lib_path.write_text(json.dumps(lib, indent=1, ensure_ascii=False), encoding="utf-8")
    return freed


if __name__ == "__main__":
    freed = dedupe(sys.argv[1], sys.argv[2])
    print(f"Freed {freed / 1e6:.0f} MB of repeat videos and leftovers.")
