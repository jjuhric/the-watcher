import logging
import time
from datetime import timedelta

from flask import Flask, Response, abort, jsonify, request, send_from_directory
from waitress import serve

import auth
import config
import db
import web_auth
from engine import Engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
app = Flask(__name__, static_folder=None)
engine = Engine()
WEB = config.BASE / "web"


@app.after_request
def no_cache(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------- pages ----------
@app.get("/")
def index():
    return send_from_directory(WEB, "index.html")


@app.get("/camera")
def camera_page():
    return send_from_directory(WEB, "camera.html")


@app.get("/logs")
def logs_page():
    return send_from_directory(WEB, "logs.html")


ASSETS = {"style.css", "menu.js", "logo.svg", "favicon.ico", "icon-192.png", "icon-512.png", "manifest.webmanifest"}

web_auth.init(app, ASSETS)


@app.get("/<any_asset>")
def asset(any_asset):
    if any_asset not in ASSETS:
        abort(404)
    return send_from_directory(WEB, any_asset)


@app.get("/account")
def account_page():
    return send_from_directory(WEB, "account.html")


@app.get("/img/<path:rel>")
def image(rel):
    return send_from_directory(config.IMG_DIR, rel, max_age=86400)


# ---------- live stream ----------
@app.get("/stream.mjpg")
def stream():
    def gen():
        engine.viewers += 1
        try:
            last = -1
            while True:
                jpg, seq = engine.live_jpeg()
                if jpg is None or seq == last:
                    time.sleep(0.08)
                    continue
                last = seq
                yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n"
                time.sleep(0.1)
        finally:
            engine.viewers -= 1
    return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")


# ---------- API ----------
@app.post("/api/camera")
def api_camera():
    on = (request.get_json(silent=True) or {}).get("on")
    if not isinstance(on, bool):
        abort(400)
    return jsonify(enabled=engine.set_enabled(on))


@app.get("/api/status")
def api_status():
    return jsonify(engine.status())


@app.get("/api/events")
def api_events():
    a = request.args
    kinds = a.get("kinds")
    return jsonify(db.fetch_events(
        limit=min(int(a.get("limit", 60)), 200), before_id=int(a["before_id"]) if a.get("before_id") else None,
        day=a.get("day") or None, cls=a.get("cls") or None,
        kinds=kinds.split(",") if kinds else None, named=a.get("named") == "1"))


@app.post("/api/detections/<int:det_id>/name")
def api_name(det_id):
    name = ((request.get_json(silent=True) or {}).get("name") or "").strip()[:60] or None
    with db.conn() as c:
        if not c.execute("SELECT 1 FROM detections WHERE id=?", (det_id,)).fetchone():
            abort(404)
        c.execute("UPDATE detections SET name=? WHERE id=?", (name, det_id))
    return jsonify(ok=True, name=name)


@app.delete("/api/events/<int:event_id>")
def api_delete(event_id):
    return jsonify(deleted=engine.delete_event(event_id))


@app.route("/api/zones", methods=["GET", "POST"])
def api_zones():
    if request.method == "POST":
        engine.set_zones((request.get_json(silent=True) or {}).get("zones", []))
    return jsonify(zones=engine.zones)


@app.get("/api/summary")
def api_summary():
    """Plain-text digest so Claude (or you) can read what happened without opening the UI."""
    hours = min(float(request.args.get("hours", 24)), 24 * 30)
    since = (db.now_utc() - timedelta(hours=hours)).isoformat(timespec="seconds")
    events = list(reversed(db.fetch_events(limit=200, since_iso=since)))
    st = engine.status()
    lines = [f"The Watcher (living room camera) - {st['now_local']}",
             f"Camera: {'ONLINE' if st['camera_online'] else 'OFFLINE'} | detector: "
             f"{'loaded' if st['detector_loaded'] else 'NOT loaded'} | free disk: {st['disk_free_gb']} GB",
             f"Right now: {', '.join(st['scene_now'])}", f"Last {hours:g} h: {len(events)} log entries", ""]
    for e in events:
        lines.append(f"[{e['local']}] #{e['id']} {e['summary']}")
    if not events:
        lines.append("(nothing logged)")
    return Response("\n".join(lines) + "\n", mimetype="text/plain")


if __name__ == "__main__":
    db.init()
    if not auth.AUTH_PATH.exists() and not (config.USER and config.PASSWORD):
        raise SystemExit("First run needs LC_USER and LC_PASS (see .env.example)")
    engine.start()
    logging.info("serving on http://%s:%s", config.BIND, config.PORT)
    serve(app, host=config.BIND, port=config.PORT, threads=8)
