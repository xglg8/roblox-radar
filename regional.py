"""Country-filtered discovery rankings; never regional concurrent-user estimates."""
import hashlib
import json
import logging
import uuid
from datetime import date, timedelta
from urllib.parse import urlencode

ROOT_URL = "https://apis.roblox.com/explore-api/v1/get-sort-content?"
METHOD = "top100_linear_equal_country_v1"
LOG = logging.getLogger("radar.regional")


def settings():
    from radar import ROOT
    c = json.loads((ROOT / "regions.json").read_text(encoding="utf-8"))
    if c["device"] != "all" or c["sort_id"] != "top-trending":
        raise ValueError("Regional method requires all-device Top Trending")
    for key, g in c["groups"].items():
        if not g["countries"] or len(set(g["countries"])) != len(g["countries"]):
            raise ValueError(f"Invalid country panel: {key}")
    return c


def cohort(c, group):
    payload = {"countries": sorted(group["countries"]), "device": c["device"], "sort_id": c["sort_id"], "method": METHOD}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def initialize(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS region_samples (
      run_id TEXT NOT NULL REFERENCES runs(id), region TEXT NOT NULL, country TEXT NOT NULL,
      observed_at TEXT NOT NULL, filters TEXT NOT NULL, payload TEXT NOT NULL,
      PRIMARY KEY(run_id,region,country));
    CREATE TABLE IF NOT EXISTS region_snapshots (
      id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), day TEXT NOT NULL,
      region TEXT NOT NULL, name TEXT NOT NULL, cohort TEXT NOT NULL, countries TEXT NOT NULL,
      started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
      UNIQUE(run_id,region));
    CREATE INDEX IF NOT EXISTS region_baseline ON region_snapshots(region,cohort,day);
    CREATE TABLE IF NOT EXISTS region_entries (
      snapshot_id INTEGER NOT NULL REFERENCES region_snapshots(id), game_id TEXT NOT NULL,
      name TEXT NOT NULL, rank INTEGER NOT NULL, score REAL NOT NULL, country_ranks TEXT NOT NULL,
      PRIMARY KEY(snapshot_id,game_id), UNIQUE(snapshot_id,rank));
    """)


def parse_sample(payload, country, device="all"):
    actual = dict(part.strip().split("=", 1) for part in payload.get("appliedFilters", "").split(",") if "=" in part)
    if payload.get("sortId") != "top-trending" or actual.get("country") != country or actual.get("device") != device or actual.get("age") != "all":
        raise ValueError(f"Country/device filters not confirmed: {country}")
    games = payload.get("games")
    if not isinstance(games, list) or not games:
        raise ValueError(f"Empty country ranking: {country}")
    seen, rows = set(), []
    for index, g in enumerate(games[:100], 1):
        gid = str(g["rootPlaceId"])
        if not gid.isdigit() or gid in seen or not isinstance(g.get("name"), str):
            raise ValueError(f"Invalid country ranking: {country}")
        seen.add(gid)
        # Preserve upstream order; playerCount is global CCU and intentionally ignored.
        rows.append({"game_id": gid, "name": g["name"], "rank": index,
                     "universe_id": g.get("universeId"), "genre_l1": g.get("genreL1")})
    return rows


def aggregate(samples):
    games = {}
    for country, rows in samples.items():
        for row in rows:
            g = games.setdefault(row["game_id"], {"game_id": row["game_id"], "name": row["name"], "country_ranks": {}, "points": 0})
            g["country_ranks"][country] = row["rank"]
            g["points"] += 101 - row["rank"]
    ordered = sorted(games.values(), key=lambda g: (-g["points"], int(g["game_id"])))
    return [dict(game_id=g["game_id"], name=g["name"], rank=i, score=g["points"] / len(samples), country_ranks=g["country_ranks"])
            for i, g in enumerate(ordered, 1)]


def collect(db, http, run_id, day, c=None):
    from radar import now
    c = c or settings()
    initialize(db)
    errors = []
    for region, group in c["groups"].items():
        started = now()
        samples, raw, failed = {}, [], []
        for country in group["countries"]:
            try:
                observed = now()
                url = ROOT_URL + urlencode({"sortId": c["sort_id"], "sessionId": str(uuid.uuid4()), "country": country, "device": c["device"], "age": "all"})
                payload = http.get(url)
                samples[country] = parse_sample(payload, country, c["device"])
                raw.append((run_id, region, country, observed, payload["appliedFilters"], json.dumps(samples[country], ensure_ascii=False)))
            except Exception as exc:
                failed.append(country)
                LOG.warning("Regional sample %s/%s failed: %s", region, country, exc)
        with db:
            db.executemany("INSERT INTO region_samples VALUES (?,?,?,?,?,?)", raw)
        if failed:
            errors.append({"platform": "roblox", "source": "regional/" + region,
                           "error": "Incomplete country panel; no regional index published: " + ",".join(failed)})
            continue
        rows = aggregate(samples)
        with db:
            sid = db.execute("INSERT INTO region_snapshots(run_id,day,region,name,cohort,countries,started_at,finished_at) VALUES (?,?,?,?,?,?,?,?)",
                             (run_id, day, region, group["name"], cohort(c, group), json.dumps(group["countries"]), started, now())).lastrowid
            db.executemany("INSERT INTO region_entries VALUES (?,?,?,?,?,?)",
                           [(sid, g["game_id"], g["name"], g["rank"], g["score"], json.dumps(g["country_ranks"])) for g in rows])
        LOG.info("Regional %s: %s countries, %s games", region, len(samples), len(rows))
    return errors


def entries(db, sid):
    rows = [dict(r) for r in db.execute("SELECT game_id,name,rank,score,country_ranks FROM region_entries WHERE snapshot_id=? ORDER BY rank", (sid,))]
    for row in rows:
        row["country_ranks"] = json.loads(row["country_ranks"])
        row["country_count"] = len(row["country_ranks"])
    return rows


def report(db, day=None):
    initialize(db)
    if day is None:
        row = db.execute("SELECT MAX(day) FROM region_snapshots").fetchone()
        day = row[0]
    if not day:
        return {"day": None, "regions": [], "missing_regions": list(settings()["groups"])}
    date.fromisoformat(day)
    main = db.execute("SELECT id FROM snapshots WHERE platform='roblox' AND source='rolimons' AND day=? ORDER BY observed_at DESC,id DESC LIMIT 1", (day,)).fetchone()
    main_ids = {r[0] for r in db.execute("SELECT game_id FROM entries WHERE snapshot_id=?", (main[0],))} if main else None
    result, missing = [], []
    c = settings()
    for region, group in c["groups"].items():
        key = cohort(c, group)
        s = db.execute("SELECT * FROM region_snapshots WHERE region=? AND cohort=? AND day=? ORDER BY started_at DESC,id DESC LIMIT 1", (region, key, day)).fetchone()
        if not s:
            missing.append(region); continue
        rows, baselines = entries(db, s["id"]), {}
        for period, days in (("daily", 1), ("weekly", 7)):
            target = (date.fromisoformat(day) - timedelta(days=days)).isoformat()
            old = db.execute("SELECT * FROM region_snapshots WHERE region=? AND cohort=? AND day=? ORDER BY started_at DESC,id DESC LIMIT 1", (region, key, target)).fetchone()
            baselines[period] = dict(old) if old else None
            previous = {g["game_id"]: g for g in entries(db, old["id"])} if old else {}
            for g in rows:
                p = previous.get(g["game_id"])
                g[period] = {"state": "no_baseline" if not old else "new_in_sample" if not p else "matched",
                             "rank_change": p["rank"] - g["rank"] if p else None,
                             "score_change": round(g["score"] - p["score"], 4) if p else None}
        for g in rows:
            g["in_global_top500"] = g["game_id"] in main_ids if main_ids is not None else None
        result.append({"snapshot": dict(s), "countries": json.loads(s["countries"]), "baselines": baselines, "rows": rows})
    return {"day": day, "regions": result, "missing_regions": missing}


def message_parts(db, day, keyword="Roblox日报"):
    r = report(db, day)
    parts = []
    for region in r["regions"]:
        s = region["snapshot"]
        text = [f"{keyword} | {day} | {s['name']}地区趋势", "指标：Roblox Top Trending 国家样本榜；不是地区 CCU／用户数。",
                "上游含义：过去一周活跃用户相对增长趋势，不是绝对热度规模。",
                "采样国家：" + ", ".join(region["countries"]).upper(),
                "设备：all；指数=各国前100名积分(101−名次)的等权平均，未上榜计0分。",
                "国家等权，不按玩家数加权；仅代表上述样本。", f"采集窗口（UTC）：{s['started_at']} → {s['finished_at']}"]
        for g in region["rows"][:10]:
            def delta(period):
                d = g[period]
                if d["state"] == "no_baseline": return "暂无基准"
                if d["state"] == "new_in_sample": return "新入样本"
                return f"名次{d['rank_change']:+d}，指数{d['score_change']:+.2f}"
            label = "是" if g["in_global_top500"] else "否" if g["in_global_top500"] is False else "未知"
            name = " ".join(g["name"].split())[:100]
            text.append(f"#{g['rank']} {name} | 指数{g['score']:.2f} | 上榜{g['country_count']}/{len(region['countries'])}国 | 日:{delta('daily')} | 周:{delta('weekly')} | 总CCU榜Top500:{label}")
        text.append("完整地区榜保存在本机，可查看 /api/regions；新地区历史不从总CCU反推。")
        parts.append("\n".join(text))
    if r["missing_regions"]:
        names = settings()["groups"]
        parts.append(f"{keyword} | {day} | 地区采集缺失\n" + "、".join(names[k]["name"] for k in r["missing_regions"]) + "：当日无完整国家样本，不生成指数，不用旧日期代替。")
    return parts


def main():
    from radar import ROOT, config, connect, Http, now
    from datetime import datetime, timezone
    c = config(); db = connect(ROOT / c["database"])
    day = datetime.now(timezone(timedelta(hours=c["timezone_offset_hours"]))).date().isoformat()
    rid = str(uuid.uuid4())
    logging.basicConfig(level=logging.INFO)
    db.execute("INSERT INTO runs(id,started_at,status) VALUES (?,?,'running')", (rid, now())); db.commit()
    try:
        errors = collect(db, Http(c), rid, day)
        status = "partial" if errors else "success"
        db.execute("UPDATE runs SET status=?,errors=?,finished_at=? WHERE id=?", (status, json.dumps(errors), now(), rid)); db.commit()
        out = ROOT / "data" / f"regions-{day}.json"
        out.write_text(json.dumps(report(db, day), ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"day": day, "status": status, "errors": errors, "output": str(out)}))
    finally:
        db.close()


if __name__ == "__main__":
    main()
