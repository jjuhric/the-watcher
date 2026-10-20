"""Sign-in routes: password form, passkeys (fingerprint / Windows Hello), sessions, account changes."""
import html
import json
import time

from flask import Blueprint, Response, jsonify, redirect, request, send_from_directory

import auth
import config
import webauthn_lite as wa

bp = Blueprint("auth", __name__)
chal = wa.Challenges()
COOKIE = "watcher_session"
PUBLIC_PATHS = {"/login", "/api/login", "/webauthn/login/options", "/webauthn/login/verify"}


class Limiter:
    """Too many failed sign-ins from one place locks it out for a while."""
    def __init__(self, limit=6, window=600):
        self.limit, self.window, self.fails = limit, window, {}

    def locked(self, key):
        now = time.time()
        self.fails[key] = [t for t in self.fails.get(key, []) if t > now - self.window]
        return len(self.fails[key]) >= self.limit

    def fail(self, key):
        self.fails.setdefault(key, []).append(time.time())


per_ip, overall = Limiter(6), Limiter(30)


def client_ip():
    return (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()) or request.remote_addr or "?"


def is_https():
    """Behind `tailscale serve` the app itself sees plain http, so trust the proxy header or the configured origin."""
    if request.headers.get("X-Forwarded-Proto", request.scheme) == "https":
        return True
    host = request.headers.get("X-Forwarded-Host") or request.host
    return any(o.startswith("https://") and o[len("https://"):] == host for o in config.ORIGINS)


def same_origin():
    """State-changing calls made with the session cookie must come from this site."""
    o = request.headers.get("Origin")
    if not o:
        return True
    host = request.headers.get("X-Forwarded-Host") or request.host
    proto = "https" if is_https() else "http"
    return o == f"{proto}://{host}" or o in config.ORIGINS


def passkeys_ready():
    return bool(config.RP_ID and config.ORIGINS)


def set_cookie(resp, token):
    resp.set_cookie(COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True,
                    samesite="Strict", secure=is_https(), path="/")
    return resp


def init(app, assets):
    public_assets = {"/" + a for a in assets}

    @app.before_request
    def require_auth():
        p = request.path
        if p in PUBLIC_PATHS or p in public_assets:
            return None
        tok = request.cookies.get(COOKIE)
        if tok and auth.check_session(tok):
            if request.method not in ("GET", "HEAD") and not same_origin():
                return jsonify(error="origin"), 403
            return None
        a = request.authorization  # scripts / curl
        if a and auth.verify(a.username, a.password):
            return None
        if request.method == "GET" and "text/html" in request.headers.get("Accept", "") \
                and not p.startswith(("/api/", "/webauthn/")):
            return redirect("/login?next=" + p)
        return Response("Sign in required", 401)

    app.register_blueprint(bp)


# ---------------- pages / password sign-in ----------------
@bp.get("/login")
def login_page():
    return send_from_directory(config.BASE / "web", "login.html")


@bp.post("/api/login")
def api_login():
    ip = client_ip()
    if per_ip.locked(ip) or overall.locked("*"):
        return jsonify(ok=False, message="Too many tries. Wait a few minutes."), 429
    j = request.get_json(silent=True) or {}
    if not same_origin() or not auth.verify(j.get("username"), j.get("password")):
        per_ip.fail(ip)
        overall.fail("*")
        time.sleep(1)
        return jsonify(ok=False, message="Wrong username or password."), 401
    return set_cookie(jsonify(ok=True), auth.create_session("password"))


@bp.post("/api/logout")
def api_logout():
    auth.revoke_session(request.cookies.get(COOKIE))
    resp = jsonify(ok=True)
    resp.delete_cookie(COOKIE, path="/")
    return resp


# ---------------- account ----------------
@bp.get("/api/account")
def api_account_get():
    return jsonify(**auth.info(), passkeys_ready=passkeys_ready(), https=is_https(),
                   passkeys=[{"id": k["id"], "name": k.get("name", "device"), "created": k.get("created"),
                              "used": k.get("used")} for k in auth.passkeys()])


@bp.post("/api/account")
def api_account_post():
    j = request.get_json(silent=True) or {}
    ok, msg = auth.change(j.get("current_password"), j.get("new_username"), j.get("new_password"), j.get("share"))
    if not ok:
        time.sleep(1)  # slow down password guessing
    resp = jsonify(ok=ok, message=msg)
    if ok:
        resp.delete_cookie(COOKIE, path="/")
    return resp, (200 if ok else 400)


# ---------------- passkeys ----------------
def _body():
    return request.get_json(silent=True)


@bp.post("/webauthn/login/options")
def wa_login_options():
    if not passkeys_ready():
        return jsonify(error="passkeys are not switched on"), 404
    if per_ip.locked(client_ip()):
        return jsonify(error="too many tries"), 429
    keys = auth.passkeys()
    if not keys:
        return jsonify(error="no passkeys registered"), 404
    return jsonify(challenge=chal.issue("login"), rpId=config.RP_ID, timeout=120000, userVerification="required",
                   allowCredentials=[{"type": "public-key", "id": k["id"]} for k in keys])


@bp.post("/webauthn/login/verify")
def wa_login_verify():
    if not passkeys_ready():
        return jsonify(error="passkeys are not switched on"), 404
    ip = client_ip()
    if per_ip.locked(ip) or overall.locked("*") or not same_origin():
        return jsonify(error="too many tries"), 429
    d = _body() or {}
    keys = auth.passkeys()
    try:
        resp = d["response"]
        ch = json.loads(wa.unb64u(resp["clientDataJSON"])).get("challenge", "")
        stored = next((k for k in keys if k["id"] == d.get("id")), None)
        if not stored or not chal.take(ch, "login"):
            raise wa.WebAuthnError("unknown credential or challenge")
        stored["count"] = wa.verify_assertion(resp, ch, config.RP_ID, config.ORIGINS, stored)
        stored["used"] = time.time()
        auth.save_passkeys(keys)
    except Exception:
        per_ip.fail(ip)
        overall.fail("*")
        return jsonify(error="sign-in failed"), 401
    return set_cookie(jsonify(ok=True), auth.create_session("passkey: " + stored.get("name", "device")))


@bp.post("/webauthn/register/options")
def wa_register_options():
    if not passkeys_ready():
        return jsonify(error="passkeys are not switched on"), 404
    user = auth.info()["user"]
    return jsonify(challenge=chal.issue("register"), rp={"name": "The Watcher", "id": config.RP_ID},
                   user={"id": wa.b64u(b"watcher-owner"), "name": user, "displayName": user},
                   pubKeyCredParams=[{"type": "public-key", "alg": -7}, {"type": "public-key", "alg": -257}],
                   authenticatorSelection={"authenticatorAttachment": "platform", "residentKey": "preferred",
                                           "userVerification": "required"},
                   attestation="none", timeout=120000,
                   excludeCredentials=[{"type": "public-key", "id": k["id"]} for k in auth.passkeys()])


@bp.post("/webauthn/register/verify")
def wa_register_verify():
    if not passkeys_ready():
        return jsonify(error="passkeys are not switched on"), 404
    d = _body() or {}
    try:
        resp = d["response"]
        ch = json.loads(wa.unb64u(resp["clientDataJSON"])).get("challenge", "")
        if not chal.take(ch, "register"):
            raise wa.WebAuthnError("challenge")
        cred = wa.verify_registration(resp, ch, config.RP_ID, config.ORIGINS)
    except Exception:
        return jsonify(error="could not verify this device"), 400
    keys = auth.passkeys()
    if any(k["id"] == cred["id"] for k in keys):
        return jsonify(error="already registered"), 409
    name = html.escape(str(d.get("name") or "device")[:40]) or "device"
    keys.append({**cred, "name": name, "created": time.time(), "used": None})
    auth.save_passkeys(keys)
    return jsonify(ok=True)


@bp.post("/webauthn/remove")
def wa_remove():
    d = _body() or {}
    auth.save_passkeys([k for k in auth.passkeys() if k["id"] != d.get("id")])
    return jsonify(ok=True)
