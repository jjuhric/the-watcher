"""Read-only report of what The Watcher has seen, for other programs (and for a quick look in a terminal).

    python report.py --hours 24            # readable text
    python report.py --hours 24 --json     # structured, for another app to format
    python report.py --since-midnight      # since 12:00 AM Central today

It reads the Watcher's own database; it never touches the camera or the service."""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import config
import db


def _service_active():
    try:
        env = {**os.environ, "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}"}
        out = subprocess.run(["systemctl", "--user", "is-active", "livingcam"], capture_output=True, text=True,
                             timeout=5, env=env).stdout.strip()
        return out == "active"
    except Exception:
        return None


def build(since_utc, now_utc=None):
    now_utc = now_utc or db.now_utc()
    since_iso = since_utc.isoformat(timespec="seconds")
    events = list(reversed(db.fetch_events(limit=5000, since_iso=since_iso)))
    with db.conn() as c:
        last_sys = c.execute("SELECT ts, note FROM events WHERE kind='system' AND note LIKE 'Camera%' "
                             "ORDER BY id DESC LIMIT 1").fetchone()
        last_any = c.execute("SELECT ts FROM events WHERE kind IN ('snapshot','departed','motion') "
                             "ORDER BY id DESC LIMIT 1").fetchone()

    periods, cur = [], None
    people_max, animals, names, snaps = 0, {}, set(), 0
    for e in events:
        trig = [d for d in e["detections"] if d["cls"] in config.TRIGGER_CLASSES]
        if e["kind"] == "snapshot":
            snaps += 1
            n_people = sum(1 for d in trig if d["cls"] == "person")
            people_max = max(people_max, n_people)
            for d in trig:
                if d["cls"] != "person":
                    animals[d["name"] or d["cls"]] = animals.get(d["name"] or d["cls"], 0) + 1
                if d["name"]:
                    names.add(d["name"])
            if cur is None:
                cur = {"start": e["ts"], "start_local": e["local"], "end": None, "end_local": None,
                       "peak_people": 0, "animals": set(), "names": set(), "snapshots": 0, "first_id": e["id"]}
                periods.append(cur)
            cur["snapshots"] += 1
            cur["peak_people"] = max(cur["peak_people"], n_people)
            cur["animals"].update(d["name"] or d["cls"] for d in trig if d["cls"] != "person")
            cur["names"].update(d["name"] for d in trig if d["name"])
        elif e["kind"] == "departed" and cur is not None:
            cur["end"], cur["end_local"] = e["ts"], e["local"]
            cur = None
    for p in periods:
        p["animals"], p["names"] = sorted(p["animals"]), sorted(p["names"])
        end = datetime.fromisoformat(p["end"]) if p["end"] else None
        p["minutes"] = round((end - datetime.fromisoformat(p["start"])).total_seconds() / 60) if end else None

    other = [e for e in events if e["kind"] in ("motion", "light")]
    offline = [e for e in events if e["kind"] == "system" and "offline" in (e["note"] or "")]
    return {
        "generated_local": db.fmt_local(now_utc.isoformat(timespec="seconds")),
        "since_local": db.fmt_local(since_iso),
        "window_hours": round((now_utc - since_utc).total_seconds() / 3600, 1),
        "service_active": _service_active(),
        "camera_last_state": (last_sys["note"] if last_sys else None),
        "camera_last_state_local": (db.fmt_local(last_sys["ts"]) if last_sys else None),
        "last_activity_local": (db.fmt_local(last_any["ts"]) if last_any else None),
        "snapshots": snaps,
        "people_max_at_once": people_max,
        "animals": animals,
        "names": sorted(names),
        "periods": periods,
        "unidentified_motion": len(other),
        "went_offline": len(offline),
        "latest": [{"id": e["id"], "local": e["local"], "summary": e["summary"], "kind": e["kind"]}
                   for e in events if e["kind"] != "system"][-25:],
        "open_now": bool(cur),
    }


def as_text(r):
    L = [f"The Watcher - {r['generated_local']}",
         f"Window: since {r['since_local']} ({r['window_hours']} h)",
         f"Service: {'running' if r['service_active'] else 'NOT running' if r['service_active'] is False else 'unknown'}"
         f"; camera last state: {r['camera_last_state'] or 'unknown'}",
         f"Snapshots: {r['snapshots']}; most people at once: {r['people_max_at_once']}; "
         f"animals: {r['animals'] or 'none'}; named: {', '.join(r['names']) or 'none'}"]
    for p in r["periods"]:
        end = p["end_local"] or "still in view"
        L.append(f"- {p['start_local']} to {end}: up to {p['peak_people']} people"
                 + (f", {', '.join(p['animals'])}" if p["animals"] else "") + (f" ({', '.join(p['names'])})" if p["names"] else ""))
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--since-midnight", action="store_true", help="since 12:00 AM Central today")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    now = db.now_utc()
    if a.since_midnight:
        local = now.astimezone(db.CHI)
        since = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    else:
        since = now - timedelta(hours=min(a.hours, 24 * 90))
    if not config.DB_PATH.exists():
        print(json.dumps({"error": "no database yet"}) if a.json else "No database yet.")
        sys.exit(1)
    rep = build(since, now)
    print(json.dumps(rep) if a.json else as_text(rep))
