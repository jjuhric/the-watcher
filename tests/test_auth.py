"""Sign-in tests: password, sessions, passkeys with a software authenticator, and the assistant-access copy.
Run on the Pi (needs the app's dependencies):  .venv/bin/python -m pytest tests -q"""
import hashlib
import json
import os
import struct
import sys
import tempfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
RP, ORIGIN = "watcher.example", "https://watcher.example:5070"
_tmp = tempfile.mkdtemp()
os.environ.update(LC_DATA_DIR=_tmp, LC_USER="viewer", LC_PASS="first-password-123", LC_RP_ID=RP,
                  LC_ORIGINS=ORIGIN, CAMERA_SOURCE="/nonexistent.mp4")

import app as appmod          # noqa: E402
import auth                   # noqa: E402
import webauthn_lite as wa    # noqa: E402

appmod.db.init()


def cbor(v):
    if isinstance(v, bool) or v is None:
        return bytes([0xF5 if v else 0xF4 if v is False else 0xF6])
    if isinstance(v, int):
        return _hdr(0, v) if v >= 0 else _hdr(1, -1 - v)
    if isinstance(v, bytes):
        return _hdr(2, len(v)) + v
    if isinstance(v, str):
        b = v.encode()
        return _hdr(3, len(b)) + b
    if isinstance(v, list):
        return _hdr(4, len(v)) + b"".join(cbor(x) for x in v)
    return _hdr(5, len(v)) + b"".join(cbor(k) + cbor(x) for k, x in v.items())


def _hdr(major, n):
    if n < 24:
        return bytes([major << 5 | n])
    if n < 256:
        return bytes([major << 5 | 24, n])
    return bytes([major << 5 | 25]) + n.to_bytes(2, "big")


class Authenticator:
    def __init__(self, kind="ec"):
        self.kind, self.cred_id, self.count = kind, b"cred-" + kind.encode() * 8, 0
        self.key = ec.generate_private_key(ec.SECP256R1()) if kind == "ec" else rsa.generate_private_key(65537, 2048)

    def cose(self):
        n = self.key.public_key().public_numbers()
        if self.kind == "ec":
            return cbor({1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")})
        return cbor({1: 3, 3: -257, -1: n.n.to_bytes(256, "big"), -2: n.e.to_bytes(3, "big")})

    def _ad(self, flags, with_cred=False, rp=RP):
        ad = hashlib.sha256(rp.encode()).digest() + bytes([flags]) + struct.pack(">I", self.count)
        if with_cred:
            ad += b"\x00" * 16 + struct.pack(">H", len(self.cred_id)) + self.cred_id + self.cose()
        return ad

    def create(self, challenge, origin=ORIGIN, flags=0x45):
        cd = json.dumps({"type": "webauthn.create", "challenge": challenge, "origin": origin}).encode()
        att = cbor({"fmt": "none", "attStmt": {}, "authData": self._ad(flags, True)})
        return {"clientDataJSON": wa.b64u(cd), "attestationObject": wa.b64u(att)}

    def get(self, challenge, origin=ORIGIN, flags=0x05, bad_sig=False):
        self.count += 1
        cd = json.dumps({"type": "webauthn.get", "challenge": challenge, "origin": origin}).encode()
        ad = self._ad(flags)
        signed = ad + hashlib.sha256(cd).digest()
        sig = (self.key.sign(signed, ec.ECDSA(hashes.SHA256())) if self.kind == "ec"
               else self.key.sign(signed, padding.PKCS1v15(), hashes.SHA256()))
        if bad_sig:
            sig = sig[:-1] + bytes([sig[-1] ^ 1])
        return {"clientDataJSON": wa.b64u(cd), "authenticatorData": wa.b64u(ad), "signature": wa.b64u(sig)}


@pytest.fixture()
def client():
    appmod.web_auth.per_ip.fails.clear()
    appmod.web_auth.overall.fails.clear()
    appmod.app.testing = True
    c = appmod.app.test_client()
    return c


H = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "watcher.example:5070"}
BASIC = lambda u="viewer", p="first-password-123": {"Authorization": "Basic " + __import__("base64").b64encode(f"{u}:{p}".encode()).decode()}  # noqa: E731


def login(client, pw="first-password-123"):
    r = client.post("/api/login", json={"username": "viewer", "password": pw}, headers=H)
    return r


def test_pages_redirect_to_login_and_api_is_401(client):
    r = client.get("/", headers={"Accept": "text/html"})
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert client.get("/api/status").status_code == 401
    assert client.get("/login").status_code == 200 and client.get("/logo.svg").status_code == 200


def test_basic_auth_still_works_for_scripts(client):
    assert client.get("/api/summary", headers=BASIC()).status_code == 200
    assert client.get("/api/summary", headers=BASIC(p="wrong")).status_code == 401


def test_password_login_sets_secure_cookie_and_session_works(client):
    assert login(client, "bad").status_code == 401
    r = login(client)
    assert r.status_code == 200
    sc = r.headers["Set-Cookie"]
    assert "watcher_session=" in sc and "HttpOnly" in sc and "Secure" in sc and "SameSite=Strict" in sc
    assert client.get("/api/status", headers=H).status_code == 200
    client.post("/api/logout", headers=H)
    assert client.get("/api/status").status_code == 401


def test_cross_origin_post_with_cookie_is_refused(client):
    login(client)
    r = client.post("/api/account", json={}, headers={**H, "Origin": "https://evil.example"})
    assert r.status_code == 403


def test_lockout_after_repeated_failures(client):
    for _ in range(6):
        login(client, "nope")
    assert login(client).status_code == 429


@pytest.mark.parametrize("kind", ["ec", "rsa"])
def test_passkey_register_and_sign_in(client, kind):
    auth.save_passkeys([])
    a = Authenticator(kind)
    login(client)
    o = client.post("/webauthn/register/options", headers=H).get_json()
    assert o["authenticatorSelection"]["userVerification"] == "required"
    r = client.post("/webauthn/register/verify", json={"name": "Laptop", "response": a.create(o["challenge"])}, headers=H)
    assert r.status_code == 200
    client.post("/api/logout", headers=H)
    # sign in with the passkey alone
    o = client.post("/webauthn/login/options", headers=H).get_json()
    resp = a.get(o["challenge"])
    r = client.post("/webauthn/login/verify", json={"id": wa.b64u(a.cred_id), "response": resp}, headers=H)
    assert r.status_code == 200 and "watcher_session=" in r.headers["Set-Cookie"]
    assert client.get("/api/status", headers=H).status_code == 200
    # replay is refused
    r = client.post("/webauthn/login/verify", json={"id": wa.b64u(a.cred_id), "response": resp}, headers=H)
    assert r.status_code == 401


@pytest.mark.parametrize("kw", [{"flags": 0x01}, {"origin": "https://evil.example"}, {"bad_sig": True}])
def test_passkey_bad_assertions_refused(client, kw):
    auth.save_passkeys([])
    a = Authenticator()
    login(client)
    o = client.post("/webauthn/register/options", headers=H).get_json()
    client.post("/webauthn/register/verify", json={"name": "x", "response": a.create(o["challenge"])}, headers=H)
    client.post("/api/logout", headers=H)
    o = client.post("/webauthn/login/options", headers=H).get_json()
    r = client.post("/webauthn/login/verify", json={"id": wa.b64u(a.cred_id), "response": a.get(o["challenge"], **kw)}, headers=H)
    assert r.status_code == 401 and "Set-Cookie" not in r.headers


def test_registering_needs_sign_in(client):
    assert client.post("/webauthn/register/options", headers=H).status_code == 401


def test_changing_login_updates_assistant_copy_and_signs_everyone_out(client):
    login(client)
    access = auth.ACCESS_PATH
    assert json.loads(access.read_text())["user"] == "viewer"
    r = client.post("/api/account", json={"current_password": "first-password-123", "new_username": "owner",
                                          "new_password": "second-password-456", "share": True}, headers=H)
    assert r.status_code == 200
    d = json.loads(access.read_text())
    assert (d["user"], d["pass"]) == ("owner", "second-password-456")
    assert oct(access.stat().st_mode & 0o777) == "0o600"
    assert client.get("/api/status", headers=H).status_code == 401           # sessions revoked
    assert client.get("/api/summary", headers=BASIC("owner", "second-password-456")).status_code == 200
    assert client.get("/api/summary", headers=BASIC()).status_code == 401
    # opting out removes the plaintext copy
    ok, _ = auth.change("second-password-456", share=False)
    assert ok and not access.exists()


def test_camera_switch_pauses_and_resumes(client):
    login(client)
    assert client.post("/api/camera", json={"on": "yes"}, headers=H).status_code == 400
    assert client.post("/api/camera", json={"on": False}, headers=H).get_json() == {"enabled": False}
    assert client.get("/api/status", headers=H).get_json()["enabled"] is False
    jpg, seq = appmod.engine.live_jpeg()
    assert seq == -2 and jpg[:2] == b"\xff\xd8"                      # the "turned off" picture
    assert json.loads(config_state().read_text()) == {"enabled": False}
    assert client.post("/api/camera", json={"on": True}, headers=H).get_json() == {"enabled": True}
    assert appmod.app.test_client().post("/api/camera", json={"on": False}).status_code == 401    # needs sign-in
    # every switch is written to the log, with who did it
    notes = [e["note"] for e in appmod.db.fetch_events(limit=10, kinds=["system"])]
    assert "Camera turned on (viewer, web page)" in notes and "Camera turned off (viewer, web page)" in notes


def config_state():
    import config
    return config.STATE_PATH
