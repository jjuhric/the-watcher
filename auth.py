"""Login store: password account, browser sessions, passkeys (fingerprint / Windows Hello).

First run seeds the account from LC_USER / LC_PASS; after that data/auth.json (salted PBKDF2) is the source
of truth, so the account page can change the username and password. HTTP Basic still works for scripts.

Optional "assistant access" copy: when enabled, the current login is also written (plaintext, mode 0600) to
data/claude-access.json so a trusted assistant on another machine can pull it (scripts/pull_creds.ps1)."""
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time

import config

AUTH_PATH = config.DATA_DIR / "auth.json"
SESS_PATH = config.DATA_DIR / "sessions.json"
KEYS_PATH = config.DATA_DIR / "passkeys.json"
ACCESS_PATH = config.DATA_DIR / "claude-access.json"
ITER = 200_000
SESSION_DAYS = 30
_lock = threading.RLock()
_cache = None
_sessions = None


def _write_private(path, obj):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def _hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITER).hex()


def _sync_access_copy(user, password, share):
    if share:
        _write_private(ACCESS_PATH, {"url": config.PUBLIC_URL, "user": user, "pass": password})
    else:
        ACCESS_PATH.unlink(missing_ok=True)


def _write(user, password, share):
    global _cache
    salt = secrets.token_bytes(16)
    _cache = {"user": user, "salt": salt.hex(), "hash": _hash(password, salt), "share": share}
    _write_private(AUTH_PATH, _cache)
    _sync_access_copy(user, password, share)


def _load():
    global _cache
    if _cache is None:
        if AUTH_PATH.exists():
            _cache = json.loads(AUTH_PATH.read_text())
        else:
            _write(config.USER, config.PASSWORD, True)
    return _cache


def _pw_ok(d, password):
    return hmac.compare_digest(_hash(password or "", bytes.fromhex(d["salt"])), d["hash"])


def verify(user, password):
    with _lock:
        d = _load()
        return _pw_ok(d, password) and hmac.compare_digest((user or "").encode(), d["user"].encode())


def info():
    with _lock:
        d = _load()
        return {"user": d["user"], "share": bool(d.get("share"))}


def change(current_password, new_user=None, new_password=None, share=None):
    """Returns (ok, message). Needs the current password even though the caller is already signed in."""
    with _lock:
        d = _load()
        if not _pw_ok(d, current_password):
            return False, "Current password is wrong."
        user = (new_user or d["user"]).strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{3,32}", user):
            return False, "Username must be 3-32 characters: letters, numbers, . _ -"
        if new_password and len(new_password) < 10:
            return False, "New password must be at least 10 characters."
        pw = new_password or current_password
        share = bool(d.get("share")) if share is None else bool(share)
        if user == d["user"] and not new_password and share == bool(d.get("share")):
            return False, "Nothing to change."
        _write(user, pw, share)
        revoke_all_sessions()
        return True, "Saved. Please sign in again."


# ---------------- browser sessions (cookie holds a random token; only its hash is stored) ----------------
def _sess():
    global _sessions
    if _sessions is None:
        try:
            _sessions = json.loads(SESS_PATH.read_text())
        except Exception:
            _sessions = {}
    return _sessions


def _tok_hash(tok):
    return hashlib.sha256(tok.encode()).hexdigest()


def create_session(label):
    with _lock:
        s = _sess()
        now = time.time()
        for k in [k for k, v in s.items() if v["exp"] < now]:
            del s[k]
        tok = secrets.token_urlsafe(32)
        s[_tok_hash(tok)] = {"exp": now + SESSION_DAYS * 86400, "label": label}
        _write_private(SESS_PATH, s)
        return tok


def check_session(tok):
    if not tok:
        return False
    with _lock:
        v = _sess().get(_tok_hash(tok))
        return bool(v and v["exp"] > time.time())


def revoke_session(tok):
    with _lock:
        if _sess().pop(_tok_hash(tok or ""), None):
            _write_private(SESS_PATH, _sess())


def revoke_all_sessions():
    with _lock:
        _sess().clear()
        _write_private(SESS_PATH, _sess())


# ---------------- passkeys ----------------
def passkeys():
    with _lock:
        try:
            return json.loads(KEYS_PATH.read_text())
        except Exception:
            return []


def save_passkeys(keys):
    with _lock:
        _write_private(KEYS_PATH, keys)
