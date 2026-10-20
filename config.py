import glob
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent


def _f(name, default):
    return type(default)(os.environ.get(name, default))


USER = os.environ.get("LC_USER", "")
PASSWORD = os.environ.get("LC_PASS", "")
BIND = os.environ.get("LC_BIND", "127.0.0.1")
PORT = _f("LC_PORT", 5070)

# Passkeys (fingerprint / Windows Hello) only work on the https address. RP id = hostname (no port);
# origins = full https origin(s) including the port. Leave LC_RP_ID empty to switch passkeys off.
RP_ID = os.environ.get("LC_RP_ID", "")
ORIGINS = [o.strip() for o in os.environ.get("LC_ORIGINS", "").split(",") if o.strip()]
PUBLIC_URL = os.environ.get("LC_PUBLIC_URL", ORIGINS[0] if ORIGINS else "")

CAMERA_SOURCE = os.environ.get("CAMERA_SOURCE", "auto")
CAMERA_WIDTH = _f("CAMERA_WIDTH", 1920)
CAMERA_HEIGHT = _f("CAMERA_HEIGHT", 1080)
CAMERA_FPS = _f("CAMERA_FPS", 10)

DATA_DIR = Path(os.environ.get("LC_DATA_DIR", BASE / "data")).resolve()
IMG_DIR = DATA_DIR / "images"
DB_PATH = DATA_DIR / "livingcam.db"
ZONES_PATH = DATA_DIR / "zones.json"
MODEL_PATH = Path(os.environ.get("MODEL_PATH", BASE / "models" / "yolo11n.onnx"))

TZ = "America/Chicago"
RETENTION_DAYS = _f("RETENTION_DAYS", 90)
MIN_FREE_GB = _f("MIN_FREE_GB", 15)

# detection tuning
MOTION_MIN = _f("MOTION_MIN", 0.004)        # fraction of frame that must change to wake the detector
LIGHT_CHANGE = _f("LIGHT_CHANGE", 0.5)      # fraction of frame changed = lighting/exposure, not a subject
UNKNOWN_MOTION_MIN = _f("UNKNOWN_MOTION_MIN", 0.02)
IDLE_DETECT_SEC = _f("IDLE_DETECT_SEC", 15)  # detector runs this often even with no motion
REPEAT_SEC = _f("REPEAT_SEC", 300)          # re-snapshot a subject that stays in view
DEPART_SEC = _f("DEPART_SEC", 20)           # empty this long = subject left
CHANGE_GAP_SEC = _f("CHANGE_GAP_SEC", 8)    # min seconds between scene-change snapshots
MOTION_EVENT_GAP_SEC = _f("MOTION_EVENT_GAP_SEC", 120)
CONF_PERSON = _f("CONF_PERSON", 0.5)
CONF_OTHER = _f("CONF_OTHER", 0.45)

# What triggers a snapshot. Everything else the model sees (couch, tv, cup...) is
# recorded inside the snapshot but never triggers one on its own.
TRIGGER_CLASSES = {"person", "bird", "cat", "dog", "horse", "sheep", "cow", "bear"}


def find_camera():
    src = CAMERA_SOURCE
    if src == "auto":
        hits = sorted(glob.glob("/dev/v4l/by-id/*-video-index0"))
        return hits[0] if hits else None
    return int(src) if src.isdigit() else src
