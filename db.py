import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import config

CHI = ZoneInfo(config.TZ)

SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,              -- UTC ISO-8601
  kind TEXT NOT NULL,            -- snapshot | motion | departed | light | system
  note TEXT,
  motion REAL,
  img TEXT, thumb TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS detections(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  cls TEXT NOT NULL, conf REAL,
  x REAL, y REAL, w REAL, h REAL,   -- normalized 0..1 of the full frame
  name TEXT
);
CREATE INDEX IF NOT EXISTS ix_det_event ON detections(event_id);
"""


@contextmanager
def conn():
    c = sqlite3.connect(config.DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.IMG_DIR.mkdir(parents=True, exist_ok=True)
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)


def now_utc():
    return datetime.now(timezone.utc)


def to_local(ts_iso):
    return datetime.fromisoformat(ts_iso).astimezone(CHI)


def fmt_local(ts_iso):
    d = to_local(ts_iso)
    t = d.strftime("%I:%M:%S %p %Z").lstrip("0")
    return f"{d.strftime('%a %b %d')} {t}"


def add_event(kind, note=None, motion=None, img=None, thumb=None, dets=(), ts=None):
    ts = (ts or now_utc()).isoformat(timespec="seconds")
    with conn() as c:
        cur = c.execute("INSERT INTO events(ts,kind,note,motion,img,thumb) VALUES(?,?,?,?,?,?)",
                        (ts, kind, note, motion, img, thumb))
        eid = cur.lastrowid
        c.executemany("INSERT INTO detections(event_id,cls,conf,x,y,w,h) VALUES(?,?,?,?,?,?,?)",
                      [(eid, d["cls"], d["conf"], d["x"], d["y"], d["w"], d["h"]) for d in dets])
    return eid


def describe(kind, note, dets):
    """Plain-text summary of one event, using user-given names where they exist."""
    trig = [d for d in dets if d["cls"] in config.TRIGGER_CLASSES]
    other = sorted({d["cls"] for d in dets if d["cls"] not in config.TRIGGER_CLASSES})
    people = [d for d in trig if d["cls"] == "person"]
    parts = []
    if len(people) == 1:
        parts.append(people[0]["name"] or "1 person")
    elif len(people) > 1:
        named = [d["name"] for d in people if d["name"]]
        s = f"group of {len(people)} people"
        parts.append(s + (f" ({', '.join(named)} named)" if named else ""))
    animals = {}
    for d in trig:
        if d["cls"] != "person":
            k = d["name"] or d["cls"]
            animals[k] = animals.get(k, 0) + 1
    parts += [k if v == 1 else f"{v} x {k}" for k, v in animals.items()]
    text = " + ".join(parts)
    if kind == "motion":
        text = "Unidentified motion (no person/animal found; likely shadow or light)"
    elif kind == "light":
        text = "Lighting / exposure change"
    elif kind == "departed":
        text = "Area is empty again"
    elif kind == "system":
        text, note = (note or "System"), None
    elif not text:
        text = "Snapshot"
    if note:
        text += f" - {note}"
    if other and kind == "snapshot":
        text += f" [also in view: {', '.join(other)}]"
    return text


def event_dict(row, dets):
    d = dict(row)
    d["local"] = fmt_local(d["ts"])
    d["detections"] = [dict(x) for x in dets]
    d["summary"] = describe(d["kind"], d["note"], d["detections"])
    return d


def fetch_events(limit=60, before_id=None, since_iso=None, day=None, cls=None, kinds=None, named=False):
    q, args = "SELECT * FROM events WHERE 1=1", []
    if before_id:
        q += " AND id < ?"
        args.append(before_id)
    if since_iso:
        q += " AND ts >= ?"
        args.append(since_iso)
    if day:  # YYYY-MM-DD in Central Time
        s = datetime.fromisoformat(day).replace(tzinfo=CHI)
        e = s.replace(hour=23, minute=59, second=59)
        q += " AND ts BETWEEN ? AND ?"
        args += [s.astimezone(timezone.utc).isoformat(timespec="seconds"),
                 e.astimezone(timezone.utc).isoformat(timespec="seconds")]
    if kinds:
        q += f" AND kind IN ({','.join('?' * len(kinds))})"
        args += kinds
    if cls:
        q += " AND id IN (SELECT event_id FROM detections WHERE cls=?)"
        args.append(cls)
    if named:
        q += " AND id IN (SELECT event_id FROM detections WHERE name IS NOT NULL)"
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with conn() as c:
        rows = c.execute(q, args).fetchall()
        out = []
        for r in rows:
            dets = c.execute("SELECT * FROM detections WHERE event_id=?", (r["id"],)).fetchall()
            out.append(event_dict(r, dets))
    return out
