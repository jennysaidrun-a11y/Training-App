"""PIN sign-in. Workers tap their name and type a 4-digit PIN; managers sign in
with their name and a 6-digit PIN. The manager pages need a manager; a worker
only sees their own lessons and certificates.

PINs are stored as salted PBKDF2 hashes. The session is a signed cookie
("w:<id>" or "m:<id>" plus an expiry), signed with a key kept in data/ (never in
git). Five wrong PINs in a row lock that account for five minutes."""
import hashlib
import hmac
import os
import re
import secrets
import time

from fastapi import HTTPException, Request

from . import db

COOKIE = "ub_session"
WORKER_PIN = re.compile(r"^\d{4}$")
MANAGER_PIN = re.compile(r"^\d{6}$")
WORKER_MINUTES = 30        # a shared tablet on the floor: sign out soon after
MANAGER_HOURS = 12
MAX_TRIES = 5
LOCK_SECONDS = 300
_fails = {}                # (kind, id) -> (count, locked_until)


def hash_pin(pin):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 200_000).hex()
    return f"{salt}${digest}"


def pin_matches(pin, stored):
    if not stored or "$" not in stored:
        return False
    salt, digest = stored.split("$", 1)
    return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 200_000).hex(), digest)


def _key():
    env = os.environ.get("TRAINING_SECRET")
    if env:
        return env.encode()
    path = db.db_path().parent / "session.key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32))
        os.chmod(path, 0o600)
    return path.read_text().strip().encode()


def _sign(text):
    return hmac.new(_key(), text.encode(), hashlib.sha256).hexdigest()


def make_cookie(kind, account_id):
    ttl = WORKER_MINUTES * 60 if kind == "w" else MANAGER_HOURS * 3600
    text = f"{kind}:{account_id}:{int(time.time()) + ttl}"
    return f"{text}:{_sign(text)}", ttl


def read_cookie(value):
    """Returns ("w" | "m", id) or None."""
    try:
        kind, account_id, expires, sig = (value or "").split(":")
        text = f"{kind}:{account_id}:{expires}"
        if kind in ("w", "m") and hmac.compare_digest(sig, _sign(text)) and int(expires) > time.time():
            return kind, int(account_id)
    except ValueError:
        pass
    return None


def locked_for(kind, account_id):
    count, until = _fails.get((kind, account_id), (0, 0))
    return max(0, int(until - time.time()))


def check_pin(kind, account_id, pin, stored):
    """True if right. Wrong tries count toward a short lock."""
    if locked_for(kind, account_id):
        return False
    if pin_matches(pin, stored):
        _fails.pop((kind, account_id), None)
        return True
    count = _fails.get((kind, account_id), (0, 0))[0] + 1
    _fails[(kind, account_id)] = (0, time.time() + LOCK_SECONDS) if count >= MAX_TRIES else (count, 0)
    return False


def current(request: Request):
    """{"kind": "w"|"m", "id", "name", ...} for whoever is signed in, else None."""
    found = read_cookie(request.cookies.get(COOKIE))
    if not found:
        return None
    kind, account_id = found
    with db.connect() as con:
        who = db.worker(con, account_id) if kind == "w" else db.manager(con, account_id)
    if not who or not who.get("active", 1):
        return None
    return {**who, "kind": kind}


def set_session(response, request, kind, account_id):
    value, ttl = make_cookie(kind, account_id)
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(COOKIE, value, max_age=ttl, httponly=True, samesite="lax", secure=secure, path="/")


def clear_session(response):
    response.delete_cookie(COOKIE, path="/")


class NeedsSignIn(Exception):
    def __init__(self, to):
        self.to = to


def require_manager(request: Request):
    who = current(request)
    if who and who["kind"] == "m":
        return who
    if request.url.path.startswith("/api/"):
        raise HTTPException(401, "Sign in as a manager first.")
    raise NeedsSignIn("/manage/signin?next=" + request.url.path)
