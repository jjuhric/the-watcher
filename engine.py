"""Camera capture + motion gate + detection + snapshot logging."""
import json
import logging
import shutil
import threading
import time
from collections import Counter
from datetime import timedelta

import cv2
import numpy as np

import config
import db
from detector import Detector

log = logging.getLogger("engine")
ANALYSIS_W, ANALYSIS_H = 640, 360


class Engine:
    def __init__(self):
        self.detector = None
        self.frame = None
        self.seq = 0
        self.frame_ts = 0.0
        self.online = False
        self.source = None
        self.motion = 0.0
        self.last_dets = (0.0, [])
        self.last_event = None
        self.viewers = 0
        self.fps = 0.0
        self._lock = threading.Lock()
        self._jpeg_cache = (-1, b"")
        self.zones = self._load_zones()
        self._zone_mask = None
        self._stop = threading.Event()
        # scene state
        self._pending_sig, self._pending_n = (), 0
        self._cur_sig = ()
        self._empty_since = None
        self._last_snap = 0.0
        self._last_change_snap = 0.0
        self._last_motion_evt = 0.0
        self._last_light_evt = 0.0

    # ---------- lifecycle ----------
    def start(self):
        try:
            self.detector = Detector()
        except Exception as e:  # keep serving the live view even if the model is missing
            log.error("detector failed to load: %s", e)
        cv2.setNumThreads(2)
        for fn in (self._capture_loop, self._analyze_loop, self._housekeeping_loop):
            threading.Thread(target=fn, daemon=True).start()
        db.add_event("system", note="Service started" + ("" if self.detector else " (detector NOT loaded)"))

    # ---------- zones ----------
    def _load_zones(self):
        try:
            return json.loads(config.ZONES_PATH.read_text())
        except Exception:
            return []

    def set_zones(self, zones):
        clean = []
        for z in zones:
            x, y, w, h = (min(1.0, max(0.0, float(z[k]))) for k in ("x", "y", "w", "h"))
            if w > 0.01 and h > 0.01:
                clean.append({"x": x, "y": y, "w": w, "h": h})
        self.zones = clean
        self._zone_mask = None
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        config.ZONES_PATH.write_text(json.dumps(clean))

    def _mask(self):
        if self._zone_mask is None:
            m = np.full((ANALYSIS_H, ANALYSIS_W), 255, np.uint8)
            for z in self.zones:
                x0, y0 = int(z["x"] * ANALYSIS_W), int(z["y"] * ANALYSIS_H)
                x1, y1 = int((z["x"] + z["w"]) * ANALYSIS_W), int((z["y"] + z["h"]) * ANALYSIS_H)
                m[y0:y1, x0:x1] = 0
            self._zone_mask = m
        return self._zone_mask

    def _in_zone(self, d):
        cx, cy = d["x"] + d["w"] / 2, d["y"] + d["h"] / 2
        return any(z["x"] <= cx <= z["x"] + z["w"] and z["y"] <= cy <= z["y"] + z["h"] for z in self.zones)

    # ---------- capture ----------
    def _open(self):
        src = config.find_camera()
        if src is None:
            return None
        cap = cv2.VideoCapture(src, cv2.CAP_V4L2) if isinstance(src, int) or str(src).startswith("/dev/") \
            else cv2.VideoCapture(src)
        if not cap.isOpened():
            return None
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_HEIGHT)
        cap.set(cv2.CAP_PROP_FPS, config.CAMERA_FPS)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.source = str(src)
        return cap

    def _capture_loop(self):
        interval = 1.0 / max(1, config.CAMERA_FPS)
        while not self._stop.is_set():
            cap = self._open()
            if cap is None:
                if self.online:
                    db.add_event("system", note="Camera went offline")
                self.online = False
                time.sleep(5)
                continue
            log.info("camera open: %s", self.source)
            db.add_event("system", note=f"Camera online ({self.source})")
            fails, last, n, t0 = 0, 0.0, 0, time.time()
            while not self._stop.is_set():
                if not cap.grab():
                    fails += 1
                    if fails > 30:
                        break
                    time.sleep(0.1)
                    continue
                fails = 0
                now = time.time()
                if now - last < interval * 0.9:
                    continue
                ok, frame = cap.retrieve()
                if not ok:
                    continue
                last = now
                with self._lock:
                    self.frame, self.frame_ts = frame, now
                    self.seq += 1
                self.online = True
                n += 1
                if now - t0 >= 5:
                    self.fps, n, t0 = n / (now - t0), 0, now
            cap.release()
            if self.online:
                db.add_event("system", note="Camera went offline")
            self.online = False
            time.sleep(2)

    def get_frame(self):
        with self._lock:
            return self.frame, self.seq

    # ---------- analysis ----------
    def _analyze_loop(self):
        sub = cv2.createBackgroundSubtractorMOG2(history=300, varThreshold=32, detectShadows=True)
        kernel = np.ones((3, 3), np.uint8)
        last_seq, last_detect = -1, 0.0
        while not self._stop.is_set():
            frame, seq = self.get_frame()
            if frame is None or seq == last_seq or time.time() - self.frame_ts > 5:
                time.sleep(0.05)
                continue
            last_seq = seq
            try:
                small = cv2.resize(frame, (ANALYSIS_W, ANALYSIS_H), interpolation=cv2.INTER_AREA)
                fg = sub.apply(small)
                # MOG2 marks shadows as 127; keep only solid foreground (255) so shadows are ignored
                _, fg = cv2.threshold(fg, 200, 255, cv2.THRESH_BINARY)
                fg = cv2.bitwise_and(fg, self._mask())
                fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, kernel)
                self.motion = float(np.count_nonzero(fg)) / (ANALYSIS_W * ANALYSIS_H)
                now = time.time()
                moving = self.motion >= config.MOTION_MIN
                if moving and self.motion >= config.LIGHT_CHANGE:
                    self._light(frame)
                    continue
                if self.detector and ((moving and now - last_detect >= 0.3)
                                      or now - last_detect >= config.IDLE_DETECT_SEC):
                    dets = [d for d in self.detector.detect(frame) if not self._in_zone(d)]
                    last_detect = time.time()
                    self.last_dets = (last_detect, dets)
                    self._handle(frame, dets, moving)
            except Exception:
                log.exception("analysis error")
                time.sleep(1)

    def _light(self, frame):
        now = time.time()
        if now - self._last_light_evt >= 300:
            self._last_light_evt = now
            self.last_event = db.add_event("light", motion=self.motion)

    def _handle(self, frame, dets, moving):
        now = time.time()
        trig = [d for d in dets if d["cls"] in config.TRIGGER_CLASSES]
        sig = tuple(sorted(Counter(d["cls"] for d in trig).items()))

        if sig == self._pending_sig:
            self._pending_n += 1
        else:
            self._pending_sig, self._pending_n = sig, 1
        stable = self._pending_n >= 3 or (not moving and self._pending_n >= 2)

        if sig:
            self._empty_since = None
        elif self._empty_since is None:
            self._empty_since = now

        if sig and stable and sig != self._cur_sig:
            if now - self._last_change_snap >= config.CHANGE_GAP_SEC:
                note = "appeared" if not self._cur_sig else "group changed"
                self._snapshot(frame, dets, note)
                self._cur_sig = sig
                self._last_change_snap = now
        elif sig and stable and now - self._last_snap >= config.REPEAT_SEC:
            self._snapshot(frame, dets, "still in view")
        elif not sig and self._cur_sig and self._empty_since and now - self._empty_since >= config.DEPART_SEC:
            self.last_event = db.add_event("departed")
            self._cur_sig = ()
        elif (not sig and moving and self.motion >= config.UNKNOWN_MOTION_MIN
              and now - self._last_motion_evt >= config.MOTION_EVENT_GAP_SEC):
            self._last_motion_evt = now
            self._snapshot(frame, dets, None, kind="motion")

    def _snapshot(self, frame, dets, note, kind="snapshot"):
        ts = db.now_utc()
        local = ts.astimezone(db.CHI)
        folder = config.IMG_DIR / local.strftime("%Y/%m/%d")
        folder.mkdir(parents=True, exist_ok=True)
        stem = local.strftime("%H%M%S")
        full, thumb = folder / f"{stem}.jpg", folder / f"{stem}_t.jpg"
        cv2.imwrite(str(full), frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
        th = cv2.resize(frame, (480, int(480 * frame.shape[0] / frame.shape[1])), interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(thumb), th, [cv2.IMWRITE_JPEG_QUALITY, 70])
        rel = lambda p: str(p.relative_to(config.IMG_DIR)).replace("\\", "/")
        self._last_snap = time.time()
        self.last_event = db.add_event(kind, note=note, motion=round(self.motion, 4),
                                       img=rel(full), thumb=rel(thumb), dets=dets, ts=ts)
        log.info("snapshot #%s %s %s", self.last_event, kind, note)

    # ---------- live view ----------
    def live_jpeg(self):
        frame, seq = self.get_frame()
        if frame is None:
            return None, -1
        if self._jpeg_cache[0] == seq:
            return self._jpeg_cache[1], seq
        img = cv2.resize(frame, (960, int(960 * frame.shape[0] / frame.shape[1])), interpolation=cv2.INTER_AREA)
        h, w = img.shape[:2]
        age, dets = self.last_dets
        if time.time() - age < 3:
            for d in dets:
                trig = d["cls"] in config.TRIGGER_CLASSES
                color = (60, 220, 60) if trig else (200, 160, 60)
                p1 = (int(d["x"] * w), int(d["y"] * h))
                p2 = (int((d["x"] + d["w"]) * w), int((d["y"] + d["h"]) * h))
                cv2.rectangle(img, p1, p2, color, 2)
                cv2.putText(img, f"{d['cls']} {d['conf']:.0%}", (p1[0], max(14, p1[1] - 5)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2, cv2.LINE_AA)
        stamp = db.now_utc().astimezone(db.CHI).strftime("%a %b %d %I:%M:%S %p %Z")
        cv2.putText(img, stamp, (10, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(img, stamp, (10, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 72])
        if not ok:
            return None, -1
        self._jpeg_cache = (seq, buf.tobytes())
        return self._jpeg_cache[1], seq

    # ---------- housekeeping ----------
    def _housekeeping_loop(self):
        while not self._stop.is_set():
            try:
                self.purge()
            except Exception:
                log.exception("purge error")
            self._stop.wait(3600)

    def purge(self):
        cutoff = (db.now_utc() - timedelta(days=config.RETENTION_DAYS)).isoformat(timespec="seconds")
        self._delete_where("ts < ?", (cutoff,))
        guard = 0
        while shutil.disk_usage(config.DATA_DIR).free / 1e9 < config.MIN_FREE_GB and guard < 50:
            guard += 1
            if not self._delete_where("id IN (SELECT id FROM events ORDER BY id LIMIT 200)", ()):
                break

    def _delete_where(self, where, args):
        with db.conn() as c:
            rows = c.execute(f"SELECT id,img,thumb FROM events WHERE {where}", args).fetchall()
            for r in rows:
                for rel in (r["img"], r["thumb"]):
                    if rel:
                        (config.IMG_DIR / rel).unlink(missing_ok=True)
            c.execute(f"DELETE FROM events WHERE {where}", args)
        return len(rows)

    def delete_event(self, event_id):
        return self._delete_where("id = ?", (event_id,))

    # ---------- status ----------
    def status(self):
        du = shutil.disk_usage(config.DATA_DIR)
        last = db.fetch_events(limit=1)
        return {
            "camera_online": self.online, "source": self.source, "fps": round(self.fps, 1),
            "motion_pct": round(self.motion * 100, 2), "detector_loaded": self.detector is not None,
            "scene_now": [f"{n} x {c}" for c, n in self._cur_sig] or ["empty"],
            "viewers": self.viewers, "disk_free_gb": round(du.free / 1e9, 1),
            "now_local": db.now_utc().astimezone(db.CHI).strftime("%a %b %d %I:%M:%S %p %Z"),
            "last_event": last[0] if last else None, "zones": self.zones,
        }
