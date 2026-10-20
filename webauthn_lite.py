"""Minimal WebAuthn (passkeys: fingerprint, face, Windows Hello) for one user. Registration with 'none' attestation (done only from an
already signed-in session) and assertion checking. Needs the 'cryptography' package for the signature check; everything else is stdlib.
Checks on every assertion: challenge (single use, short life), origin, rpIdHash, user presence AND user verification (the biometric/PIN),
signature, and the signature counter. """
import base64, hashlib, json, secrets, struct, time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.exceptions import InvalidSignature

CHALLENGE_TTL = 120


class WebAuthnError(ValueError):
    pass


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ------------------------------------------------------------------ CBOR (just enough for attestation objects and COSE keys)
def cbor_decode(data: bytes, i: int = 0):
    """Returns (value, next_index)."""
    ib = data[i]
    major, info = ib >> 5, ib & 31
    i += 1
    if info < 24:
        val = info
    elif info in (24, 25, 26, 27):
        n = {24: 1, 25: 2, 26: 4, 27: 8}[info]
        val = int.from_bytes(data[i:i + n], "big")
        i += n
    else:
        raise WebAuthnError("unsupported CBOR")
    if major == 0:
        return val, i
    if major == 1:
        return -1 - val, i
    if major in (2, 3):
        raw = data[i:i + val]
        if len(raw) != val:
            raise WebAuthnError("short CBOR")
        return (raw if major == 2 else raw.decode("utf-8")), i + val
    if major == 4:
        out = []
        for _ in range(val):
            v, i = cbor_decode(data, i)
            out.append(v)
        return out, i
    if major == 5:
        out = {}
        for _ in range(val):
            k, i = cbor_decode(data, i)
            v, i = cbor_decode(data, i)
            out[k] = v
        return out, i
    if major == 7:
        return {20: False, 21: True, 22: None}.get(info), i
    raise WebAuthnError("unsupported CBOR")


# ------------------------------------------------------------------ authenticator data
def parse_auth_data(ad: bytes) -> dict:
    if len(ad) < 37:
        raise WebAuthnError("authenticator data too short")
    d = {"rp_hash": ad[:32], "flags": ad[32], "count": struct.unpack(">I", ad[33:37])[0], "cred_id": None, "cose": None}
    if d["flags"] & 0x40:                                   # attested credential data present
        if len(ad) < 55:
            raise WebAuthnError("short credential data")
        n = struct.unpack(">H", ad[53:55])[0]
        d["cred_id"] = ad[55:55 + n]
        key, _ = cbor_decode(ad, 55 + n)
        d["cose_map"] = key
        d["cose"] = ad[55 + n:55 + n + _len_of(ad, 55 + n)]
    return d


def _len_of(data: bytes, i: int) -> int:
    _, j = cbor_decode(data, i)
    return j - i


def _check_client_data(client_b64: str, kind: str, challenge: str, origins) -> bytes:
    raw = unb64u(client_b64)
    try:
        c = json.loads(raw)
    except ValueError:
        raise WebAuthnError("bad client data")
    if c.get("type") != kind:
        raise WebAuthnError("wrong type")
    if c.get("challenge") != challenge:
        raise WebAuthnError("wrong challenge")
    if c.get("origin") not in origins:
        raise WebAuthnError("wrong origin")
    if c.get("crossOrigin"):
        raise WebAuthnError("cross origin")
    return raw


def _public_key(cose: bytes):
    k, _ = cbor_decode(cose)
    if k.get(1) == 2 and k.get(3) == -7 and k.get(-1) == 1:                    # EC2 / ES256 / P-256
        return ec.EllipticCurvePublicNumbers(int.from_bytes(k[-2], "big"), int.from_bytes(k[-3], "big"), ec.SECP256R1()).public_key(), -7
    if k.get(1) == 3 and k.get(3) == -257:                                    # RSA / RS256 (common for Windows Hello)
        return rsa.RSAPublicNumbers(int.from_bytes(k[-2], "big"), int.from_bytes(k[-1], "big")).public_key(), -257
    raise WebAuthnError("unsupported key type")


# ------------------------------------------------------------------ registration and assertion
def verify_registration(resp: dict, challenge: str, rp_id: str, origins) -> dict:
    _check_client_data(resp["clientDataJSON"], "webauthn.create", challenge, origins)
    att, _ = cbor_decode(unb64u(resp["attestationObject"]))
    ad = parse_auth_data(att["authData"])
    if ad["rp_hash"] != hashlib.sha256(rp_id.encode()).digest():
        raise WebAuthnError("wrong rp")
    if not (ad["flags"] & 0x01 and ad["flags"] & 0x04 and ad["flags"] & 0x40):
        raise WebAuthnError("needs user presence, user verification and a credential")
    _, alg = _public_key(ad["cose"])
    return {"id": b64u(ad["cred_id"]), "cose": b64u(ad["cose"]), "alg": alg, "count": ad["count"]}


def verify_assertion(resp: dict, challenge: str, rp_id: str, origins, stored: dict) -> int:
    """Returns the new signature counter. Raises WebAuthnError on anything wrong."""
    client_raw = _check_client_data(resp["clientDataJSON"], "webauthn.get", challenge, origins)
    ad_raw = unb64u(resp["authenticatorData"])
    ad = parse_auth_data(ad_raw)
    if ad["rp_hash"] != hashlib.sha256(rp_id.encode()).digest():
        raise WebAuthnError("wrong rp")
    if not (ad["flags"] & 0x01 and ad["flags"] & 0x04):
        raise WebAuthnError("needs user presence and user verification")
    key, alg = _public_key(unb64u(stored["cose"]))
    signed = ad_raw + hashlib.sha256(client_raw).digest()
    sig = unb64u(resp["signature"])
    try:
        if alg == -7:
            key.verify(sig, signed, ec.ECDSA(hashes.SHA256()))
        else:
            key.verify(sig, signed, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature:
        raise WebAuthnError("bad signature")
    if (ad["count"] or stored.get("count", 0)) and ad["count"] <= stored.get("count", 0):
        raise WebAuthnError("counter went backwards (cloned authenticator?)")
    return ad["count"]


# ------------------------------------------------------------------ one-time challenges
class Challenges:
    def __init__(self):
        self.items: dict[str, tuple[str, str, float]] = {}

    def issue(self, purpose: str, now: float | None = None) -> str:
        now = now if now is not None else time.time()
        self.items = {k: v for k, v in self.items.items() if v[2] > now}
        ch = b64u(secrets.token_bytes(32))
        self.items[ch] = (purpose, ch, now + CHALLENGE_TTL)
        return ch

    def take(self, ch: str, purpose: str, now: float | None = None) -> bool:
        """Single use: a challenge is removed whether or not the check that follows succeeds."""
        now = now if now is not None else time.time()
        v = self.items.pop(ch, None)
        return bool(v and v[0] == purpose and v[2] > now)
