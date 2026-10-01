"""PIN sign-in. Workers tap their name and type a 4-digit PIN; managers sign in
with their name and a 6-digit PIN. The manager pages need a manager; a worker
only sees their own lessons and certificates.

PINs are stored as salted PBKDF2 hashes. Sessions are signed cookies
("w:<id>" or "m:<id>" plus an expiry), signed with a key kept in data/ (never in
git). Workers and managers have separate cookies, so a worker signing in on the
same device never signs the manager out. A manager stays signed in for 30 days
from their last visit to a manager page, until they sign out. Five wrong PINs in
a row lock that account for five minutes."""
import hashlib
import hmac
import os
import re
import secrets
import time

from fastapi import HTTPException, Request

from . import db

COOKIE = "ub_session"       # worker
MANAGER_COOKIE = "ub_manager"
WORKER_PIN = re.compile(r"^\d{4}$")
MANAGER_PIN = re.compile(r"^\d{6}$")
WORKER_MINUTES = 30        # a shared tablet on the floor: sign out soon after
MANAGER_DAYS = 30          # renewed on every manager page, so it only runs out after 30 days away
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
        path.write_text(secrets.token_hex(32), encoding="utf-8")
        os.chmod(path, 0o600)
    return path.read_text(encoding="utf-8").strip().encode()


def _sign(text):
    return hmac.new(_key(), text.encode(), hashlib.sha256).hexdigest()


def make_cookie(kind, account_id):
    ttl = WORKER_MINUTES * 60 if kind == "w" else MANAGER_DAYS * 86400
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


def _account(request, cookie, kind):
    found = read_cookie(request.cookies.get(cookie))
    if not found or found[0] != kind:
        return None
    with db.connect() as con:
        who = db.worker(con, found[1]) if kind == "w" else db.manager(con, found[1])
    if not who or not who.get("active", 1):
        return None
    return {**who, "kind": kind}


def worker(request: Request):
    return _account(request, COOKIE, "w")


def manager(request: Request):
    """The signed-in manager (older sign-ins kept theirs in the worker cookie)."""
    return _account(request, MANAGER_COOKIE, "m") or _account(request, COOKIE, "m")


def current(request: Request):
    """{"kind": "w"|"m", "id", "name", ...}: the signed-in worker, else the manager, else None."""
    return worker(request) or manager(request)


def set_session(response, request, kind, account_id):
    value, ttl = make_cookie(kind, account_id)
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(COOKIE if kind == "w" else MANAGER_COOKIE, value, max_age=ttl, httponly=True,
                        samesite="lax", secure=secure, path="/")
    old = read_cookie(request.cookies.get(COOKIE))
    if kind == "m" and old and old[0] == "m":
        response.delete_cookie(COOKIE, path="/")     # move an older manager sign-in to its own cookie


def clear_session(response, request, kind=None):
    """Signs out the worker ("w"), the manager ("m") or both."""
    old = read_cookie(request.cookies.get(COOKIE))
    if kind in (None, "m"):
        response.delete_cookie(MANAGER_COOKIE, path="/")
    if kind is None or (old and old[0] == kind):
        response.delete_cookie(COOKIE, path="/")
