"""Daily CCU snapshots. Python 3.11+, standard library only."""
from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import sqlite3
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("radar")
ROLIMONS = "https://api.rolimons.com/games/v1/gamelist"
OFFICIAL = "https://apis.roblox.com/explore-api/v1/get-sort-content?sortId=top-playing-now&sessionId="
STEAM = "https://api.steampowered.com/"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def config():
    c = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    if not 1 <= c["top_n"] <= 5000:
        raise ValueError("top_n must be between 1 and 5000")
    return c


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS runs (
      id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
      status TEXT NOT NULL, errors TEXT NOT NULL DEFAULT '[]');
    CREATE TABLE IF NOT EXISTS snapshots (
      id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
      platform TEXT NOT NULL, source TEXT NOT NULL, day TEXT NOT NULL,
      observed_at TEXT NOT NULL, finished_at TEXT NOT NULL,
      candidate_count INTEGER NOT NULL, scope TEXT NOT NULL,
      UNIQUE(run_id, platform, source));
    CREATE INDEX IF NOT EXISTS snapshot_day ON snapshots(platform, source, day);
    CREATE TABLE IF NOT EXISTS entries (
      snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
      game_id TEXT NOT NULL, name TEXT NOT NULL, ccu INTEGER NOT NULL CHECK(ccu>=0),
      rank INTEGER NOT NULL, url TEXT NOT NULL, extra TEXT NOT NULL,
      PRIMARY KEY(snapshot_id,game_id), UNIQUE(snapshot_id,rank));
    CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    return db


class Http:
    def __init__(self, c):
        self.c, self.lock, self.next_request = c, threading.Lock(), 0.0
        self.issues, self.candidate_count = [], None

    def get(self, url):
        for attempt in range(self.c["request_attempts"]):
            with self.lock:
                delay = self.next_request - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                self.next_request = time.monotonic() + self.c["request_interval_seconds"]
            try:
                req = Request(url, headers={"User-Agent": "RobloxRadar/1.0 (public statistics collector)", "Accept": "application/json"})
                with urlopen(req, timeout=self.c["request_timeout_seconds"]) as r:
                    return json.load(r)
            except (HTTPError, URLError, TimeoutError) as exc:
                if isinstance(exc, HTTPError) and exc.code not in (408, 429, 500, 502, 503, 504):
                    raise
                if attempt + 1 == self.c["request_attempts"]:
                    raise
                retry = exc.headers.get("Retry-After", "") if isinstance(exc, HTTPError) else ""
                wait = min(60, int(retry)) if retry.isdigit() else 2 ** (attempt + 1)
                LOG.warning("Retrying %s in %ss: %s", urlparse(url).hostname, wait, exc)
                time.sleep(wait)


def count(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"Invalid CCU: {value!r}")
    return value


def rolimons(http):
    d = http.get(ROLIMONS)
    if d.get("success") is not True or not isinstance(d.get("games"), dict):
        raise ValueError("Rolimon's schema changed")
    rows = []
    for gid, g in d["games"].items():
        if not str(gid).isdigit() or not isinstance(g, list) or len(g) < 2:
            raise ValueError("Invalid Rolimon's game record")
        rows.append(dict(game_id=str(gid), name=str(g[0]), ccu=count(g[1]),
                         url=f"https://www.roblox.com/games/{gid}", extra={}))
    return rows


def official(http):
    d = http.get(OFFICIAL + str(uuid.uuid4()))
    return [dict(game_id=str(g["rootPlaceId"]), name=g["name"], ccu=count(g["playerCount"]),
                 url=f"https://www.roblox.com/games/{g['rootPlaceId']}",
                 extra={"universe_id": g["universeId"], "genre": g.get("genreL1")})
            for g in d["games"]]


def official_details(http, db, run_id):
    anchor = db.execute("SELECT id FROM snapshots WHERE run_id=? AND platform='roblox' AND source='rolimons'", (run_id,)).fetchone()
    if not anchor:
        raise ValueError("No current-run Top 500 candidate set")
    candidates = entries(db, anchor['id'])
    mapping = {r['key'][9:]: r['value'] for r in db.execute("SELECT * FROM cache WHERE key LIKE 'universe:%'")}
    # Reuse public identifiers already obtained from official charts and regional samples.
    for r in db.execute("SELECT game_id,extra FROM entries e JOIN snapshots s ON e.snapshot_id=s.id WHERE s.platform='roblox' AND s.source='official'"):
        uid = json.loads(r['extra']).get('universe_id')
        if uid:
            mapping[r['game_id']] = str(uid)
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='region_samples'").fetchone():
        for r in db.execute('SELECT payload FROM region_samples ORDER BY observed_at'):
            for g in json.loads(r['payload']):
                if g.get('universe_id'):
                    mapping[g['game_id']] = str(g['universe_id'])
    def resolve(g):
        gid = g['game_id']
        if gid in mapping:
            return gid, mapping[gid], None
        try:
            uid = http.get(f'https://apis.roblox.com/universes/v1/places/{gid}/universe')['universeId']
            if isinstance(uid, bool) or not isinstance(uid, int) or uid <= 0:
                raise ValueError('Invalid universe ID')
            return gid, str(uid), None
        except Exception as exc:
            return gid, None, f'{gid}: {exc}'
    resolved = [(g['game_id'], mapping[g['game_id']], None) for g in candidates if g['game_id'] in mapping]
    pending = [g for g in candidates if g['game_id'] not in mapping]
    for start in range(0, len(pending), 12):
        with ThreadPoolExecutor(max_workers=3) as pool:
            batch_results = list(pool.map(resolve, pending[start:start+12]))
        resolved.extend(batch_results)
        with db:
            for gid, uid, _ in batch_results:
                if uid:
                    db.execute('INSERT OR REPLACE INTO cache VALUES (?,?)', ('universe:'+gid, uid))
        if sum(uid is None for _, uid, _ in batch_results) >= 6:
            LOG.warning('Official ID lookup unstable; stopping further lookups for this run')
            break
    issues = [error for _, _, error in resolved if error]
    for gid, uid, error in resolved:
        if uid:
            db.execute('INSERT OR REPLACE INTO cache VALUES (?,?)', ('universe:'+gid, uid))
    db.commit()
    ids = sorted({uid for _, uid, _ in resolved if uid})
    details = {}
    for start in range(0, len(ids), 50):
        batch = ids[start:start+50]
        try:
            payload = http.get('https://games.roblox.com/v1/games?universeIds='+','.join(batch))
            measured = now()
            for g in payload['data']:
                if str(g['id']) in batch:
                    count(g['playing'])
                    details[str(g['id'])] = (g, measured)
        except Exception as exc:
            issues.append(f'Official detail batch {start//50+1}: {exc}')
    rows = []
    for gid, uid, _ in resolved:
        if uid not in details:
            continue
        g, measured = details[uid]
        if str(g.get('rootPlaceId')) != gid:
            issues.append(f'{gid}: root place mismatch'); continue
        rows.append(dict(game_id=gid, name=g['name'], ccu=count(g['playing']),
                         url=f'https://www.roblox.com/games/{gid}',
                         extra={'universe_id': int(uid), 'measured_at': measured,
                                'visits': g.get('visits'), 'favorites': g.get('favoritedCount'),
                                'max_players': g.get('maxPlayers'), 'genre': g.get('genre_l1')}))
    if len(rows) != len(candidates):
        issues.append(f'Official details coverage {len(rows)}/{len(candidates)}; missing values are not zero')
    http.issues, http.candidate_count = issues, len(candidates)
    return rows


def steam(http, db):
    chart = http.get(STEAM + "ISteamChartsService/GetMostPlayedGames/v1/")["response"]
    names = {r["key"][6:]: r["value"] for r in db.execute("SELECT * FROM cache WHERE key LIKE 'steam:%'")}

    def game(g):
        gid = str(g["appid"])
        r = http.get(STEAM + f"ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid={gid}")["response"]
        if r.get("result") != 1:
            raise ValueError(f"Steam CCU unavailable: {gid}")
        name = names.get(gid)
        if not name:
            try:
                detail = http.get(f"https://store.steampowered.com/api/appdetails?appids={gid}&filters=basic&l=english")[gid]
                name = detail.get("data", {}).get("name")
            except Exception as exc:
                LOG.warning("Steam name lookup %s: %s", gid, exc)
        return dict(game_id=gid, name=name or f"Steam App {gid}", ccu=count(r["player_count"]),
                    url=f"https://store.steampowered.com/app/{gid}",
                    extra={"chart_rank": g["rank"], "peak_in_game": g["peak_in_game"],
                           "chart_rollup_date": chart.get("rollup_date"), "measured_at": now()})

    def safe_game(g):
        try:
            return game(g), None
        except Exception as exc:
            LOG.warning("Steam CCU unavailable for app %s: %s", g["appid"], exc)
            return None, f"Steam App {g['appid']}: {exc}"

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(safe_game, chart["ranks"]))
    rows = [row for row, error in results if row is not None]
    http.issues = [error for row, error in results if error]
    http.candidate_count = len(chart["ranks"])
    if len(rows) < max(1, len(chart["ranks"]) * 0.8):
        raise ValueError(f"Steam CCU coverage too low: {len(rows)}/{len(chart['ranks'])}")
    for g in rows:
        if not g["name"].startswith("Steam App "):
            db.execute("INSERT OR REPLACE INTO cache VALUES (?,?)", ("steam:" + g["game_id"], g["name"]))
    db.commit()
    return rows


def save_snapshot(db, run_id, platform, source, day, observed_at, rows, limit, scope, candidate_count=None):
    if len(rows) < limit:
        raise ValueError(f"{source}: expected at least {limit} games, got {len(rows)}")
    if len({g["game_id"] for g in rows}) != len(rows):
        raise ValueError(f"{source}: duplicate game IDs")
    ordered = sorted(rows, key=lambda g: (-count(g["ccu"]), int(g["game_id"])))[:limit]
    with db:
        sid = db.execute("INSERT INTO snapshots(run_id,platform,source,day,observed_at,finished_at,candidate_count,scope) VALUES (?,?,?,?,?,?,?,?)",
                         (run_id, platform, source, day, observed_at, now(), candidate_count or len(rows), scope)).lastrowid
        db.executemany("INSERT INTO entries VALUES (?,?,?,?,?,?,?)", [
            (sid, g["game_id"], g["name"], g["ccu"], rank, g["url"], json.dumps(g.get("extra", {})))
            for rank, g in enumerate(ordered, 1)])
    return sid


def collect(c, db):
    # Hold a process-wide file lock, including across Windows scheduled/manual launches.
    lock_path = ROOT / "data" / "collect.lock"
    lock_path.parent.mkdir(exist_ok=True)
    with lock_path.open("a+b") as lock:
        lock.seek(0); lock.write(b"0"); lock.flush(); lock.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _collect(c, db)


def _collect(c, db):
    rid, started, errors = str(uuid.uuid4()), now(), []
    db.execute("INSERT INTO runs(id,started_at,status) VALUES (?,?,?)", (rid, started, "running")); db.commit()
    http = Http(c)
    jobs = [("roblox", "rolimons", lambda: rolimons(http), c["top_n"], "Rolimon’s 收录游戏中的 CCU Top；非全站穷举，源端更新时间未知"),
            ("roblox", "official", lambda: official(http), None, "Roblox 官方 Top Playing Now 返回样本，约 100 款")]
    if c.get('official_details_enabled', False):
        jobs.append(('roblox', 'official_details', lambda: official_details(http, db, rid), None,
                     'Roblox 官方免费详情接口，对本轮 Rolimon’s Top 500 候选逐款核对；非独立全站榜单'))
    if c["steam_enabled"]:
        jobs.append(("steam", "official", lambda: steam(http, db), None, "Steam Most Played 榜单候选集，逐款获取当前 CCU；非 Steam 全站 CCU Top"))
    for platform, source, get_rows, limit, scope in jobs:
        http.issues, http.candidate_count = [], None
        observed = now()
        day = datetime.fromisoformat(observed).astimezone(timezone(timedelta(hours=c["timezone_offset_hours"]))).date().isoformat()
        try:
            LOG.info("Collecting %s/%s", platform, source)
            rows = get_rows()
            if not rows:
                raise ValueError("Empty upstream response")
            if http.issues:
                scope += f"；当前 CCU 缺失 {len(http.issues)} 款，已从样本中排除"
            sid = save_snapshot(db, rid, platform, source, day, observed, rows, limit or len(rows), scope, http.candidate_count)
            if http.issues:
                errors.append({"platform": platform, "source": source, "error": "; ".join(http.issues)})
            LOG.info("Saved snapshot %s: %s candidates, %s ranked", sid, http.candidate_count or len(rows), limit or len(rows))
        except Exception as exc:
            db.rollback()
            errors.append({"platform": platform, "source": source, "error": str(exc)})
            LOG.exception("Collection failed: %s/%s", platform, source)
    if c.get("regional_enabled", False):
        try:
            import regional
            day = datetime.fromisoformat(started).astimezone(timezone(timedelta(hours=c["timezone_offset_hours"]))).date().isoformat()
            errors.extend(regional.collect(db, http, rid, day))
        except Exception as exc:
            db.rollback()
            errors.append({"platform": "roblox", "source": "regional", "error": str(exc)})
            LOG.exception("Regional collection failed")
    if c.get("genre_trends_enabled", False):
        try:
            import genre_trends
            day = datetime.fromisoformat(started).astimezone(timezone(timedelta(hours=c["timezone_offset_hours"]))).date().isoformat()
            genre_trends.collect(db, http, day)
        except Exception as exc:
            db.rollback()
            errors.append({"platform": "roblox", "source": "genre_trends", "error": str(exc)})
            LOG.exception("Genre trends collection failed")
    successes = db.execute("SELECT COUNT(*) FROM snapshots WHERE run_id=?", (rid,)).fetchone()[0]
    status = "success" if not errors else "partial" if successes else "failed"
    db.execute("UPDATE runs SET finished_at=?,status=?,errors=? WHERE id=?", (now(), status, json.dumps(errors), rid)); db.commit()
    return {"run_id": rid, "status": status, "errors": errors}


def latest(db, platform, source, day=None):
    return db.execute("SELECT * FROM snapshots WHERE platform=? AND source=?" + (" AND day=?" if day else "") +
                      " ORDER BY observed_at DESC,id DESC LIMIT 1", (platform, source, day) if day else (platform, source)).fetchone()


def entries(db, sid):
    return [dict(r) for r in db.execute("SELECT game_id,name,ccu,rank,url FROM entries WHERE snapshot_id=? ORDER BY rank", (sid,))]


def ranking(db, platform="roblox", source="rolimons", day=None):
    snap = latest(db, platform, source, day)
    if not snap:
        return {"snapshot": None, "rows": [], "baselines": {}}
    rows, baselines = entries(db, snap["id"]), {}
    for label, days in (("daily", 1), ("weekly", 7)):
        target = (date.fromisoformat(snap["day"]) - timedelta(days=days)).isoformat()
        base = latest(db, platform, source, target)
        baselines[label] = dict(base) if base else None
        old = {r["game_id"]: r for r in entries(db, base["id"])} if base else {}
        for g in rows:
            previous = old.get(g["game_id"])
            g[label] = {"state": "no_baseline" if not base else "outside_previous_sample" if not previous else "matched",
                        "rank_change": previous["rank"] - g["rank"] if previous else None,
                        "ccu_change": g["ccu"] - previous["ccu"] if previous else None,
                        "ccu_pct": round((g["ccu"] / previous["ccu"] - 1) * 100, 2) if previous and previous["ccu"] else None}
    return {"snapshot": dict(snap), "baselines": baselines, "rows": rows}


def comparison(db, day=None):
    # Select all platforms on the same calendar date; never silently mix latest dates.
    anchor = latest(db, "roblox", "rolimons", day)
    day = day or (anchor["day"] if anchor else None)
    if not day:
        return {"day": None, "platforms": [], "source_checks": []}
    platforms = []
    samples = {}
    selected = {}
    for platform, source in (("roblox", "rolimons"), ("steam", "official"), ("roblox", "official"), ('roblox', 'official_details')):
        s = (db.execute('SELECT * FROM snapshots WHERE run_id=? AND platform=? AND source=?',
                        (anchor['run_id'], platform, source)).fetchone() if platform == 'roblox' and anchor
             else latest(db, platform, source, day))
        if not s:
            continue
        rows = entries(db, s["id"])
        selected[(platform, source)] = dict(s)
        samples[(platform, source)] = rows
        platforms.append({"platform": platform, "source": source, "snapshot": dict(s), "sample_size": len(rows),
                          "sample_ccu": sum(g["ccu"] for g in rows), "top10_ccu": sum(g["ccu"] for g in rows[:10]),
                          "top100_ccu": sum(g["ccu"] for g in rows[:100]) if len(rows) >= 100 else None})
    source = 'official_details' if ('roblox', 'official_details') in samples else 'official'
    secondary = {g['game_id']: g for g in samples.get(('roblox', source), [])}
    official_snapshot = selected.get(('roblox', source))
    metadata = ({r['game_id']: json.loads(r['extra']) for r in db.execute(
        'SELECT game_id,extra FROM entries WHERE snapshot_id=?', (official_snapshot['id'],))}
        if official_snapshot else {})
    checks = []
    for g in samples.get(('roblox', 'rolimons'), []):
        official_ccu = secondary.get(g['game_id'], {}).get('ccu')
        diff = g['ccu'] - official_ccu if official_ccu is not None else None
        pct = diff / official_ccu * 100 if official_ccu else None
        # A review heuristic, never a claim that one provider is wrong.
        flagged = diff is not None and abs(diff) >= 100 and (official_ccu == 0 or abs(pct) >= 10)
        checks.append(dict(game_id=g['game_id'], name=g['name'], rolimons_ccu=g['ccu'],
                           official_ccu=official_ccu, difference=diff, difference_pct=pct,
                           official_metadata=metadata.get(g['game_id']),
                           status='missing' if diff is None else 'review' if flagged else 'matched'))
    return {'day': day, 'platforms': platforms, 'source_checks': checks,
            'official_source': source, 'official_snapshot': official_snapshot,
            'matched_count': sum(g['official_ccu'] is not None for g in checks),
            'review_count': sum(g['status'] == 'review' for g in checks)}


def csv_data(report):
    out = io.StringIO(newline="")
    w = csv.writer(out)
    w.writerow(["day", "platform", "source", "rank", "game_id", "name", "ccu", "daily_state", "daily_rank_change", "weekly_state", "weekly_rank_change", "daily_ccu_change", "weekly_ccu_change", "daily_ccu_pct", "weekly_ccu_pct"])
    s = report["snapshot"]
    for g in report["rows"]:
        # Prevent spreadsheet formula execution in third-party game titles.
        name = g["name"]
        if name.lstrip().startswith(("=", "+", "-", "@")) or name.startswith(("\t", "\r", "\n")):
            name = "'" + name
        w.writerow([s["day"], s["platform"], s["source"], g["rank"], g["game_id"], name, g["ccu"],
                    g["daily"]["state"], g["daily"]["rank_change"], g["weekly"]["state"], g["weekly"]["rank_change"],
                    g["daily"]["ccu_change"], g["weekly"]["ccu_change"], g["daily"]["ccu_pct"], g["weekly"]["ccu_pct"]])
    return ("\ufeff" + out.getvalue()).encode("utf-8")


def serve(path, port, host="127.0.0.1"):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            db = None
            try:
                day = q.get("day") or None
                if day:
                    date.fromisoformat(day)
                db = connect(path)
                kind = "application/json; charset=utf-8"
                if parsed.path == "/":
                    body = (ROOT / "dashboard.html").read_bytes(); kind = "text/html; charset=utf-8"
                elif parsed.path in ("/api/rankings", "/api/export.csv"):
                    report = ranking(db, q.get("platform", "roblox"), q.get("source", "rolimons"), day)
                    body = csv_data(report) if parsed.path.endswith(".csv") else json.dumps(report, ensure_ascii=False).encode()
                    if parsed.path.endswith(".csv"):
                        kind = "text/csv; charset=utf-8"
                elif parsed.path == "/api/compare":
                    body = json.dumps(comparison(db, day), ensure_ascii=False).encode()
                elif parsed.path == "/api/regions":
                    from regional import report as regional_report
                    body = json.dumps(regional_report(db, day), ensure_ascii=False).encode()
                elif parsed.path == "/api/genres":
                    from genre_trends import report as genre_report
                    body = json.dumps(genre_report(db, day), ensure_ascii=False).encode()
                elif parsed.path == "/api/status":
                    body = json.dumps({"runs": [dict(r) for r in db.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 20")],
                                       "days": [r[0] for r in db.execute("SELECT DISTINCT day FROM snapshots ORDER BY day DESC")]}, ensure_ascii=False).encode()
                elif parsed.path == "/api/history":
                    body = json.dumps([dict(r) for r in db.execute("""SELECT s.day,s.observed_at,e.rank,e.ccu FROM entries e JOIN snapshots s ON s.id=e.snapshot_id
                        WHERE s.platform=? AND s.source=? AND e.game_id=? ORDER BY s.observed_at,s.id""",
                        (q.get("platform", "roblox"), q.get("source", "rolimons"), q.get("game_id", "")))], ensure_ascii=False).encode()
                else:
                    self.send_error(404); return
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                if parsed.path.endswith(".csv"):
                    self.send_header("Content-Disposition", 'attachment; filename="rankings.csv"')
                self.end_headers(); self.wfile.write(body)
            except ValueError:
                self.send_error(400, "Invalid date or parameters")
            except Exception:
                LOG.exception("API failure"); self.send_error(500)
            finally:
                if db is not None:
                    db.close()

    LOG.info("Dashboard: http://%s:%s", host, port)
    ThreadingHTTPServer((host, port), Handler).serve_forever()


def hourly_config(c, current=None):
    current = current or datetime.now(timezone(timedelta(hours=c['timezone_offset_hours'])))
    result = dict(c)
    if current.hour != 10:
        result.update(regional_enabled=False, genre_trends_enabled=False)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("collect")
    sub.add_parser("hourly", help="Collect CCU; also collect regions/genres at 10:00; never send Feishu")
    sub.add_parser("daily", help="Collect and send the configured Feishu daily report")
    p = sub.add_parser("serve"); p.add_argument("--port", type=int, default=8765)
    p.add_argument("--host", default="127.0.0.1", help="Use 0.0.0.0 only behind a reverse proxy")
    p = sub.add_parser("export"); p.add_argument("--day"); p.add_argument("--platform", default="roblox"); p.add_argument("--source", default="rolimons"); p.add_argument("--out", default="data/rankings.csv")
    sub.add_parser("status")
    args = parser.parse_args()
    (ROOT / "data").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[logging.StreamHandler(), logging.FileHandler(ROOT / "data" / "radar.log", encoding="utf-8")])
    c = config(); path = ROOT / c["database"]
    if args.command == "serve":
        serve(path, args.port, args.host); return 0
    db = connect(path)
    try:
        if args.command in ("collect", "daily", "hourly"):
            if args.command == "hourly":
                c = hourly_config(c)
            result = collect(c, db); print(json.dumps(result))
            if args.command == "daily":
                from feishu import deliver, settings
                f = settings()
                day = datetime.now(timezone(timedelta(hours=c["timezone_offset_hours"]))).date().isoformat()
                snapshot = latest(db, "roblox", "rolimons", day)
                if snapshot and snapshot["run_id"] == result["run_id"]:
                    (ROOT / "data" / f"roblox-{day}.csv").write_bytes(csv_data(ranking(db, day=day)))
                if f.get("enabled"):
                    try:
                        if not snapshot or snapshot["run_id"] != result["run_id"]:
                            raise ValueError("Current run has no Roblox main snapshot; refusing to send stale data")
                        LOG.info("Feishu: %s", deliver(db, day, f))
                    except Exception as exc:
                        LOG.error("Feishu report failed: %s", exc)
                        return 1
                else:
                    LOG.info("Feishu not configured; collection retained locally")
            return 0 if result["status"] == "success" else 1
        if args.command == "export":
            report = ranking(db, args.platform, args.source, args.day)
            if not report["snapshot"]:
                raise ValueError("No snapshot available for requested date/source")
            out = ROOT / args.out; out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(csv_data(report)); print(out)
        if args.command == "status":
            print(json.dumps([dict(r) for r in db.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 5")], indent=2))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
