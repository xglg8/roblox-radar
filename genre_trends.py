"""Snapshot official genres for the regional Top Trending game panels."""
import argparse
import hashlib
import json
import logging
from datetime import date, timedelta

METHOD = "official_exclusive_genre_v1"
LABELS = {"simulation": "模拟器/模拟经营", "fps": "FPS", "shooter": "其他射击/视角未明确",
          "rpg": "角色扮演/RPG", "action": "动作/格斗", "horror": "恐怖", "survival": "生存",
          "obby": "跑酷/平台跳跃", "strategy": "策略/塔防", "social": "社交/生活扮演",
          "sports": "体育/竞速", "puzzle": "解谜/探索", "casual": "休闲/派对", "other": "其他", "unknown": "未分类"}


def classify(g):
    legacy = (g.get("genre") or "").strip().lower()
    primary = (g.get("genre_l1") or "").strip().lower()
    secondary = (g.get("genre_l2") or "").strip().lower()
    if legacy == "fps" or "first person" in secondary or "first-person" in secondary:
        return "fps"
    if legacy == "horror" or "horror" in secondary or primary == "horror":
        return "horror"
    mapping = {"simulation": "simulation", "shooter": "shooter", "rpg": "rpg", "action": "action",
               "survival": "survival", "obby & platformer": "obby", "strategy": "strategy",
               "roleplay & avatar sim": "social", "social": "social", "sports & racing": "sports",
               "puzzle": "puzzle", "adventure": "puzzle", "party & casual": "casual", "entertainment": "casual"}
    return mapping.get(primary, "other" if primary else "unknown")


def initialize(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS genre_snapshots (
      id INTEGER PRIMARY KEY, day TEXT NOT NULL, scope TEXT NOT NULL, name TEXT NOT NULL,
      cohort TEXT NOT NULL, source_ids TEXT NOT NULL, observed_at TEXT NOT NULL,
      UNIQUE(day,scope,cohort,source_ids));
    CREATE TABLE IF NOT EXISTS genre_entries (
      snapshot_id INTEGER NOT NULL REFERENCES genre_snapshots(id), game_id TEXT NOT NULL,
      name TEXT NOT NULL, category TEXT NOT NULL, metadata TEXT NOT NULL, points REAL NOT NULL,
      PRIMARY KEY(snapshot_id,game_id));
    """)


def collect(db, http, day):
    import regional
    from radar import now
    initialize(db)
    regions = regional.report(db, day)
    if regions["missing_regions"]:
        raise ValueError("Genre panel incomplete; requires all five regional snapshots on this date")
    raw, ids = {}, []
    for region in regions["regions"]:
        s = region["snapshot"]; ids.append(s["id"])
        samples = db.execute("SELECT payload FROM region_samples WHERE run_id=? AND region=?", (s["run_id"], s["region"]))
        for sample in samples:
            for g in json.loads(sample[0]):
                raw.setdefault(g["game_id"], g)
    universe_ids = sorted({int(g["universe_id"]) for g in raw.values() if g.get("universe_id")})
    if not universe_ids:
        raise ValueError("No universe metadata in saved samples; collect regional rankings first")
    details = {}
    for start in range(0, len(universe_ids), 50):
        batch = universe_ids[start:start+50]
        try:
            response = http.get("https://games.roblox.com/v1/games?universeIds=" + ",".join(map(str, batch)))
            for g in response["data"]:
                if g["id"] in batch:
                    details[str(g["id"])] = {k: g.get(k) for k in ("id", "genre", "genre_l1", "genre_l2")}
        except Exception as exc:
            logging.getLogger("radar.genres").warning("Official genre batch unavailable: %s", exc)
    if not details:
        raise ValueError("Official genre service unavailable; no genre snapshot published")
    groups = [("all_regions", "五地区去重样本", list(raw.values()), ids,
               sorted(r["snapshot"]["cohort"] for r in regions["regions"]))]
    groups.extend((r["snapshot"]["region"], r["snapshot"]["name"], r["rows"], [r["snapshot"]["id"]], [r["snapshot"]["cohort"]]) for r in regions["regions"])
    for scope, name, games, sources, cohorts in groups:
        key = hashlib.sha256(json.dumps([METHOD, cohorts]).encode()).hexdigest()
        source_ids = json.dumps(sorted(sources))
        if db.execute("SELECT 1 FROM genre_snapshots WHERE day=? AND scope=? AND cohort=? AND source_ids=?", (day,scope,key,source_ids)).fetchone():
            continue
        with db:
            sid = db.execute("INSERT INTO genre_snapshots(day,scope,name,cohort,source_ids,observed_at) VALUES (?,?,?,?,?,?)",
                             (day, scope, name, key, source_ids, now())).lastrowid
            for g in games:
                meta = details.get(str(raw[g["game_id"]].get("universe_id")), {})
                # Missing metadata stays unknown; do not guess FPS from game titles.
                db.execute("INSERT INTO genre_entries VALUES (?,?,?,?,?,?)",
                           (sid, g["game_id"], g["name"], classify(meta), json.dumps(meta), g.get("score", 1.0)))
    return report(db, day)


def summarize(db, sid):
    rows = [dict(r) for r in db.execute("SELECT game_id,name,category,points FROM genre_entries WHERE snapshot_id=?", (sid,))]
    total, points = len(rows), sum(g["points"] for g in rows)
    result = []
    for key, label in LABELS.items():
        games = [g for g in rows if g["category"] == key]
        games.sort(key=lambda g: (-g["points"], int(g["game_id"])))
        result.append({"category": key, "label": label, "count": len(games),
                       "share_pct": len(games)/total*100 if total else 0,
                       "trend_points_share_pct": sum(g["points"] for g in games)/points*100 if points else 0,
                       "examples": games[:3]})
    return sorted(result, key=lambda g: (-g["count"], g["category"]))


def report(db, day=None):
    initialize(db)
    day = day or db.execute("SELECT MAX(day) FROM genre_snapshots").fetchone()[0]
    if not day:
        return {"day": None, "groups": []}
    date.fromisoformat(day)
    scopes = db.execute("SELECT DISTINCT scope FROM genre_snapshots WHERE day=?", (day,)).fetchall()
    groups = []
    for item in scopes:
        snap = db.execute("SELECT * FROM genre_snapshots WHERE day=? AND scope=? ORDER BY observed_at DESC,id DESC LIMIT 1", (day,item[0])).fetchone()
        rows, baselines = summarize(db, snap["id"]), {}
        for period, days in (("daily",1),("weekly",7),("half_monthly",15)):
            target = (date.fromisoformat(day)-timedelta(days=days)).isoformat()
            old = db.execute("SELECT * FROM genre_snapshots WHERE day=? AND scope=? AND cohort=? ORDER BY observed_at DESC,id DESC LIMIT 1", (target,snap["scope"],snap["cohort"])).fetchone()
            baselines[period] = dict(old) if old else None
            before = {g["category"]:g for g in summarize(db,old["id"])} if old else {}
            for g in rows:
                p = before.get(g["category"])
                g[period] = {"count_change": g["count"]-p["count"] if p else None,
                             "share_pp_change": g["share_pct"]-p["share_pct"] if p else None}
        groups.append({"snapshot":dict(snap),"baselines":baselines,"total_games":sum(g["count"] for g in rows),"rows":rows})
    return {"day":day,"groups":groups}


def message_parts(db, day, keyword="Roblox日报"):
    result=report(db,day)
    if not result["groups"]:
        return [f"{keyword} | {day} | 游戏类型趋势\n今日暂无类型快照；不以旧日期代替。"]
    lines=[f"{keyword} | {day} | 最近流行游戏类型趋势", "范围：五地区29国 Top Trending 国家样本，非全站类型市场份额。",
           "每款游戏只计一个主类型；占比按去重游戏数量计算，不是CCU占比。",
           "分类依据：官方类型及子类型。FPS仅在官方明确标注时单列，其余射击不猜测视角。"]
    for group in sorted(result["groups"],key=lambda g:(g["snapshot"]["scope"]!='all_regions',g["snapshot"]["scope"])):
        lines.append(f"\n【{group['snapshot']['name']}】{group['total_games']}款")
        lines.append('对比基准：' + '；'.join(f"{label} {(group['baselines'][period] or {}).get('day', '暂无基准')}" for period, label in [('daily', '1天'), ('weekly', '7天'), ('half_monthly', '15天')]))
        for row in group["rows"]:
            if not row["count"] and not any(row[p]['count_change'] for p in ('daily','weekly','half_monthly')):continue
            def delta(period):
                d=row[period]
                return '暂无基准' if d['count_change'] is None else f"{d['count_change']:+d}款/{d['share_pp_change']:+.2f}百分点"
            lines.append(f"{row['label']}：{row['count']}款（{row['share_pct']:.1f}%） | 1天{delta('daily')} | 7天{delta('weekly')} | 15天{delta('half_monthly')}")
        if group['snapshot']['scope']=='all_regions':
            for row in group['rows']:
                if row['count'] and row['category']!='unknown':
                    names='、'.join(' '.join(g['name'].split())[:50] for g in row['examples'])
                    lines.append(f"{row['label']} 样例：{names}")
    lines.extend(["", "类型历史从首次采集开始；未获取到官方类型的游戏计入未分类，保留在占比分母中。",
                  "类型构成变化可能来自榜单换入换出或官方分类修改，不等于某类型玩家增长。"])
    # Keep each message comfortably below Feishu payload limits.
    parts, chunk = [], lines[0]+'\n'
    for line in lines[1:]:
        if len((chunk+line).encode())>12000:
            parts.append(chunk);chunk=lines[0]+'（续）\n'
        chunk+=line+'\n'
    parts.append(chunk)
    return parts


if __name__ == "__main__":
    from radar import ROOT,config,connect,Http
    p=argparse.ArgumentParser();p.add_argument('--day',required=True);args=p.parse_args()
    c=config();db=connect(ROOT/c['database'])
    try:
        data=collect(db,Http(c),args.day)
        (ROOT/'data'/f'genres-{args.day}.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'day':args.day,'groups':len(data['groups']),'games':next(g['total_games'] for g in data['groups'] if g['snapshot']['scope']=='all_regions')}))
    finally:db.close()
