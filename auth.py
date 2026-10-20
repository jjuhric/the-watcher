"""Login store. First run seeds from LC_USER / LC_PASS; after that data/auth.json (salted PBKDF2) is
the source of truth, so the account page can change the username and password."""
import hashlib
import hmac
import json
import os
import re
import secrets
import threading

import config

AUTH_PATH = config.DATA_DIR / "auth.json"
ITER = 200_000
_lock = threading.Lock()
_cache = None


def _hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITER).hex()


def _write(user, password):
    global _cache
    salt = secrets.token_bytes(16)
    data = {"user": user, "salt": salt.hex(), "hash": _hash(password, salt)}
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = AUTH_PATH.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, AUTH_PATH)
    _cache = data


def _load():
    global _cache
    if _cache is None:
        if AUTH_PATH.exists():
            _cache = json.loads(AUTH_PATH.read_text())
        else:
            _write(config.USER, config.PASSWORD)
    return _cache


def verify(user, password):
    with _lock:
        d = _load()
        ok_pw = hmac.compare_digest(_hash(password or "", bytes.fromhex(d["salt"])), d["hash"])
        ok_user = hmac.compare_digest((user or "").encode(), d["user"].encode())
        return ok_pw and ok_user


def current_user():
    with _lock:
        return _load()["user"]


def change(current_password, new_user=None, new_password=None):
    """Returns (ok, message). Needs the current password even though the caller is already signed in."""
    with _lock:
        d = _load()
        if not hmac.compare_digest(_hash(current_password or "", bytes.fromhex(d["salt"])), d["hash"]):
            return False, "Current password is wrong."
        user = (new_user or d["user"]).strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{3,32}", user):
            return False, "Username must be 3-32 characters: letters, numbers, . _ -"
        if new_password:
            if len(new_password) < 10:
                return False, "New password must be at least 10 characters."
            pw = new_password
        else:
            pw = current_password
        if user == d["user"] and not new_password:
            return False, "Nothing to change."
        _write(user, pw)
        return True, "Saved. Your browser will ask you to sign in again."
