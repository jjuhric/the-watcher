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
- **Times** are stored in UTC and shown in America/Chicago (DST aware).
- **Plain-text digest**: `GET /api/summary?hours=24` (handy for an assistant or a quick `curl`).
- Retention: auto-deletes snapshots older than `RETENTION_DAYS` and trims oldest if free disk < `MIN_FREE_GB`.

## Setup (Pi, Debian/Raspberry Pi OS)

```bash
git clone https://github.com/jjuhric/the-watcher.git ~/Sandbox/living-cam
cd ~/Sandbox/living-cam
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && chmod 600 .env        # set LC_USER / LC_PASS / LC_BIND
```

Model: export once (needs `pip install ultralytics onnx` in a throwaway venv) and put it at `models/yolo11n.onnx`:

```bash
yolo export model=yolo11n.pt format=onnx imgsz=640 simplify=True
```

Run as a user service (linger enabled so it starts at boot):

```bash
mkdir -p ~/.config/systemd/user && cp deploy/livingcam.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now livingcam
journalctl --user -u livingcam -f
```

Then open `http://<LC_BIND>:5070` (e.g. over Tailscale) and sign in.

## Secrets

`.env` (credentials), `data/` (images + database) and `models/*.onnx` are git-ignored. Never commit them.
The server refuses to start without `LC_USER` / `LC_PASS`, and every route requires HTTP Basic auth.
Run it with `LC_BIND=127.0.0.1` and publish it privately with `tailscale serve --bg --https=5070 http://127.0.0.1:5070`,
which gives a tailnet-only HTTPS address with a real certificate (nothing is exposed on the LAN or internet).

## Archiving to a PC

`scripts/sync_to_laptop.ps1` pulls a consistent copy of the database plus new images (incremental).

## Logo

`python scripts/make_logo.py` regenerates `web/logo.svg`, `favicon.ico` and the app icons (needs Pillow).

## Tuning

All thresholds are env vars (see `config.py`): `MOTION_MIN`, `CONF_PERSON`, `CONF_OTHER`, `REPEAT_SEC`,
`DEPART_SEC`, `IDLE_DETECT_SEC`, ... Use `CAMERA_SOURCE=/path/to/video.mp4` to test without a camera.
