# The Watcher

<img src="web/logo.svg" alt="The Watcher logo" width="96" align="right">

Small self-hosted home camera for a Raspberry Pi. A USB webcam watches a room; OpenCV detects motion,
a small YOLO11n ONNX model (run via OpenCV DNN, no cloud, no LLM) says what moved, and a still is saved
whenever a person or animal appears. Web UI: live view + searchable log where you click a box and name it.

- **Snapshots only** - no video is recorded.
- **Person / group / animal / shadow**: people and animals are labelled by the model; 2+ people are logged
  as a group; MOG2 shadow pixels are discarded, and motion that matches nothing is logged as
  "unidentified motion"; whole-frame changes are logged as "lighting change".
- **Ignore zones**: draw boxes over a TV, window, or fan on the live page; motion and detections inside are skipped.
- **Click-to-name**: open a log entry, click a box, type a name. Names show up in the log text.
- **Camera on/off switch** on the dashboard and camera pages. Off releases the camera, pauses detection and (with
  `uhubctl`) cuts power to the camera's USB port so its light goes out; the state survives restarts. Every switch is
  logged with who did it ("Camera turned off (viewer, web page)", "Camera USB port powered off").
- **Times** are stored in UTC and shown in America/Chicago (DST aware).
- **Menu** (top right, every page): Dashboard, Change Username / Password, Sign out. Add items in `web/menu.js`.
- **Digests for other programs**: `GET /api/summary?hours=24` (plain text, needs sign-in) and `python report.py`
  (read-only, no sign-in, reads the database directly; see below).
- Retention: auto-deletes snapshots older than `RETENTION_DAYS` and trims oldest if free disk < `MIN_FREE_GB`.

## Setup (Pi, Debian/Raspberry Pi OS)

```bash
git clone https://github.com/jjuhric/the-watcher.git ~/Sandbox/living-cam
cd ~/Sandbox/living-cam
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && chmod 600 .env        # set LC_USER / LC_PASS / LC_RP_ID / LC_ORIGINS
```

Model: export once (needs `pip install ultralytics onnx` in a throwaway venv) and put it at `models/yolo11n.onnx`:

```bash
yolo export model=yolo11n.pt format=onnx imgsz=640 simplify=True
```

USB power switching for the camera switch (optional; without it the switch pauses the camera in software only):

```bash
sudo apt install uhubctl
sudo cp deploy/99-watcher-usb-power.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=usb
```

The rule lets the `plugdev` group switch USB port power (no sudo needed by the service) and keeps the camera out of
USB autosuspend. The service learns which hub/port the camera is on the first time it opens it (saved in `data/state.json`).
Set `LC_USB_POWER=0` to turn this off. If you move the camera to another port, the next start re-learns it.

Run as a user service (linger enabled so it starts at boot):

```bash
mkdir -p ~/.config/systemd/user && cp deploy/livingcam.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now livingcam
journalctl --user -u livingcam -f
```

Publish it privately over Tailscale (the app listens on 127.0.0.1 only):

```bash
tailscale serve --bg --https=5070 http://127.0.0.1:5070    # -> https://<pi>.<tailnet>.ts.net:5070
```

## Sign-in

- Browser: `/login` page with username + password, or **fingerprint / face / Windows Hello** (WebAuthn passkeys).
  Add a device from the menu -> *Change Username / Password* while on the https address. Needs `LC_RP_ID` and
  `LC_ORIGINS` in `.env`. Passkey code: `webauthn_lite.py` (user presence + verification, challenge, origin, counter checks).
- Scripts/curl: HTTP Basic still works (`curl -u user:pass .../api/summary`).
- Sessions are random tokens in an HttpOnly, Secure, SameSite=Strict cookie (only a hash is stored). Failed sign-ins lock out.
- The account page changes the username/password (stored as a salted PBKDF2 hash in `data/auth.json`) and signs everyone out.
- Optional: "keep a copy for my assistant" writes `data/claude-access.json` (plaintext, mode 600) on every change;
  `scripts/pull_creds.ps1` copies it to a local credentials file (it also runs at the end of `sync_to_laptop.ps1`).
  Untick the box to delete the copy.
- Tests: `.venv/bin/python -m pytest tests -q`.

## Secrets

`.env` (credentials), `data/` (images, database, logins, passkeys, state) and `models/*.onnx` are git-ignored. Never commit them.
The server needs `LC_USER` / `LC_PASS` for the first run only, and every route except the sign-in page and static assets
requires a session or HTTP Basic.

## Report for other programs

`python report.py [--hours 24 | --since-midnight] [--json]` prints what the camera saw: visits (start, end, most people at once,
animals, names), unidentified motion, camera dropouts, and the camera's last state. It reads the database only; it never touches
the camera or the service. A visit ends when the person leaves, the camera is switched off, or the service restarts.
[Foreman](https://github.com/jjuhric/foreman) (a separate project) uses this for its "how do things look at home?" chat report.

## Archiving to a PC

`scripts/sync_to_laptop.ps1` pulls a consistent copy of the database plus new images (incremental) and then refreshes the
local credentials file. Schedule it (Windows Task Scheduler) for a nightly archive. The laptop must be on the tailnet.

## Logo

`python scripts/make_logo.py` regenerates `web/logo.svg`, `favicon.ico` and the app icons (needs Pillow).

## Tuning

All thresholds are env vars (see `config.py`): `MOTION_MIN`, `CONF_PERSON`, `CONF_OTHER`, `REPEAT_SEC`,
`DEPART_SEC`, `IDLE_DETECT_SEC`, ... Use `CAMERA_SOURCE=/path/to/video.mp4` to test without a camera.

## Files

| File | What it is |
|---|---|
| `app.py`, `web_auth.py`, `auth.py`, `webauthn_lite.py` | web server, sign-in routes, login/session/passkey store |
| `engine.py`, `detector.py`, `db.py`, `config.py` | capture + motion + detection + snapshots + on/off switch, YOLO via OpenCV, SQLite log, settings |
| `report.py` | read-only digest for other programs |
| `web/` | the pages (dashboard, camera, logs, account, login), `menu.js`, logo and icons |
| `deploy/` | systemd user unit, udev rule for USB power |
| `scripts/` | `sync_to_laptop.ps1`, `pull_creds.ps1`, `make_logo.py` |
| `tests/` | sign-in, passkeys (software authenticator), camera switch, report |
