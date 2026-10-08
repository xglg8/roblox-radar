"""Feishu custom bot text reports, signed webhook and persistent per-part outbox."""
import argparse
import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from radar import ROOT, comparison, config, connect, now, ranking

LOG = logging.getLogger("radar.feishu")


def settings():
    path = ROOT / "feishu.local.json"
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {"enabled": False}


def validate(f):
    p = urlparse(f.get("webhook_url", ""))
    if (p.scheme != "https" or p.hostname != "open.feishu.cn" or p.port not in (None, 443)
            or p.username or p.password or p.query or p.fragment
            or not p.path.startswith("/open-apis/bot/v2/hook/") or not p.path.rsplit("/", 1)[-1]):
        raise ValueError("Configure a valid https://open.feishu.cn/open-apis/bot/v2/hook/... webhook")
    if len(f.get("keyword", "Roblox日报")) > 100:
        raise ValueError("Feishu keyword exceeds 100 characters")


def signed_payload(text, secret="", timestamp=None):
    payload = {"msg_type": "text", "content": {"text": text}}
    if secret:
        stamp = str(int(time.time()) if timestamp is None else timestamp)
        key = (stamp + "\n" + secret).encode()
        payload.update(timestamp=stamp, sign=base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode())
    return payload


def change(item):
    if item["state"] == "no_baseline":
        return "无基准"
    if item["state"] != "matched":
        return "前期未入样本"
    return f"{item['rank_change']:+d}"


def ccu_change(item):
    if item["state"] == "no_baseline":
        return "暂无基准"
    if item["state"] != "matched":
        return "前期未入样本"
    delta, pct = item["ccu_change"], item["ccu_pct"]
    rate = f"{pct:+.2f}%" if pct is not None else "基准为0，涨跌幅不适用"
    return f"{delta:+,}人（{rate}）"


def ccu_summary(report):
    lines = ["", "CCU 变化（采集时刻快照对比，并非日均或周均）："]
    for period, title in (("daily", "日"), ("weekly", "周")):
        baseline = report["baselines"][period]
        if not baseline:
            lines.append(f"{title} CCU：暂无基准")
            continue
        games = [g for g in report["rows"] if g[period]["state"] == "matched"]
        lines.append(f"{title}对比基准（UTC）：{baseline['observed_at']}；两期均在榜 {len(games)} 款")
        for rising, label in ((True, "增加"), (False, "减少")):
            selected = [g for g in games if (g[period]["ccu_change"] > 0 if rising else g[period]["ccu_change"] < 0)]
            selected.sort(key=lambda g: ((-1 if rising else 1) * g[period]["ccu_change"], int(g["game_id"])))
            lines.append(f"{title} CCU {label} Top 3（按人数）：")
            for g in selected[:3]:
                name = " ".join(g["name"].split())[:100]
                lines.append(f"  {name}：{ccu_change(g[period])}")
            if not selected:
                lines.append("  无符合条件的可比游戏")
    return lines


def report_parts(db, day, keyword="Roblox日报"):
    date.fromisoformat(day)
    r = ranking(db, day=day)
    if not r["snapshot"]:
        raise ValueError("No Roblox main snapshot for requested day")
    s = r["snapshot"]
    run = db.execute("SELECT status,errors FROM runs WHERE id=?", (s["run_id"],)).fetchone()
    c = comparison(db, day)
    summary = [f"{keyword} | {day}", f"采集时间（UTC）：{s['observed_at']}",
               f"Roblox 榜单 {len(r['rows'])} 款 / 候选 {s['candidate_count']} 款",
               f"样本 CCU 合计：{sum(g['ccu'] for g in r['rows']):,}",
               f"日基准：{(r['baselines']['daily'] or {}).get('day', '暂无')}；周基准：{(r['baselines']['weekly'] or {}).get('day', '暂无')}",
               "排名变化 = 前期排名 − 当前排名；正数为上升。", "", "Roblox 同日各来源样本 Top 10 CCU："]
    for p in c["platforms"]:
        if p["platform"] != "roblox":
            continue
        summary.append(f"{p['platform']}/{p['source']}: {p['top10_ccu']:,}（样本 {p['sample_size']} 款，候选 {p['snapshot']['candidate_count']} 款）")
    summary.extend(["", "Roblox 官方与第三方同游戏核对：" + str(len(c["source_checks"])) + " 款",
                    "任务状态：" + (run["status"] if run else "unknown")])
    if run:
        for e in json.loads(run["errors"]):
            if e["platform"] != "roblox":
                continue
            summary.append(f"异常来源：{e['platform']}/{e['source']}；请查看本地采集日志。")
    summary.extend(ccu_summary(r))
    summary.extend(["", "口径：Rolimon’s 收录范围 Top，不保证全站覆盖；CCU 是读取时在线人数。",
                    "不同来源采集时间和缓存可能不同；样本总和不代表 Roblox 全站总在线。",
                    "CCU变化 = 当前CCU − 前期CCU；涨跌幅 = 变化 ÷ 前期CCU。",
                    "日对比昨日，周对比7天前；基准缺失不补零。首次基准可能不是同一时刻。",
                    "以下分批发送完整榜单；CSV 同步保存在本机。"])
    parts = ["\n".join(summary)]
    lines = []
    for g in r["rows"]:
        name = " ".join(g["name"].split())[:100]
        lines.append(f"#{g['rank']} {name} | ID {g['game_id']} | CCU {g['ccu']:,} | 日排名 {change(g['daily'])} | 周排名 {change(g['weekly'])} | 日CCU {ccu_change(g['daily'])} | 周CCU {ccu_change(g['weekly'])}")
    prefix = f"{keyword} | {day} | 完整榜单（正数为排名上升）\n"
    chunk = prefix
    for line in lines:
        candidate = chunk + line + "\n"
        if len(json.dumps(signed_payload(candidate), ensure_ascii=False).encode()) > 17000:
            parts.append(chunk); chunk = prefix
        chunk += line + "\n"
    if chunk != prefix:
        parts.append(chunk)
    if config().get("regional_enabled", False):
        from regional import message_parts
        parts.extend(message_parts(db, day, keyword))
    return parts


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Rejected(RuntimeError):
    """Provider explicitly rejected this message; safe to retry later."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def post(f, text):
    body = json.dumps(signed_payload(text, f.get("signing_secret", "")), ensure_ascii=False).encode()
    req = Request(f["webhook_url"], data=body, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    try:
        with build_opener(NoRedirect()).open(req, timeout=30) as response:
            result = json.load(response)
    except HTTPError as exc:
        if exc.code in (400, 401, 403, 404, 429):
            raise Rejected(f"Feishu HTTP {exc.code}", code=exc.code) from None
        raise RuntimeError("Feishu HTTP response uncertain; check group before retrying") from None
    except (URLError, TimeoutError, ValueError, OSError):
        raise RuntimeError("Feishu delivery uncertain; check group before retrying") from None
    code = result.get("code", result.get("StatusCode"))
    if code != 0:
        if code is None:
            raise RuntimeError("Unrecognized Feishu response; check group before retrying")
        detail = str(result.get("msg", result.get("StatusMessage", "")))[:300]
        for value in (f["webhook_url"], f["webhook_url"].rsplit("/", 1)[-1], f.get("signing_secret")):
            if value:
                detail = detail.replace(value, "[redacted]")
        raise Rejected(f"Feishu rejected message, code={code}, detail={detail}", code=code)


def send_with_retry(f, text, sender, pause):
    # Only retry explicit rejections. Timeouts/unknown outcomes may already be delivered.
    delays = (3, 10, 30)
    for attempt in range(len(delays) + 1):
        try:
            sender(f, text)
            return
        except Rejected as exc:
            if exc.code not in (429, 11233) or attempt == len(delays):
                raise
            LOG.warning("Feishu explicit rejection %s; retry %s/3 in %ss", exc, attempt + 1, delays[attempt])
            pause(delays[attempt])


def deliver(db, day, f, sender=post, pause=time.sleep, edition="daily"):
    validate(f)
    if edition not in ("daily", "ccu-update", "regions"):
        raise ValueError("Unknown report edition")
    target = hashlib.sha256((f["webhook_url"] + ("|" + edition if edition != "daily" else "")).encode()).hexdigest()
    db.execute("""CREATE TABLE IF NOT EXISTS feishu_outbox (
        target TEXT NOT NULL, day TEXT NOT NULL, part INTEGER NOT NULL,
        body TEXT NOT NULL, state TEXT NOT NULL, sent_at TEXT,
        PRIMARY KEY(target,day,part))""")
    # Freeze the report on first send, including across retries and newer snapshots.
    with db:
        if not db.execute("SELECT 1 FROM feishu_outbox WHERE target=? AND day=?", (target, day)).fetchone():
            keyword = f.get("keyword", "Roblox日报") + (" · CCU变化补充" if edition == "ccu-update" else "")
            if edition == "regions":
                from regional import message_parts
                parts = message_parts(db, day, keyword)
            else:
                parts = report_parts(db, day, keyword)
            db.executemany("INSERT INTO feishu_outbox VALUES (?,?,?,?,'pending',NULL)",
                           [(target, day, i, f"{body}\n（日报第 {i+1}/{len(parts)} 条）") for i, body in enumerate(parts)])
    rows = db.execute("SELECT * FROM feishu_outbox WHERE target=? AND day=? ORDER BY part", (target, day)).fetchall()
    sent = 0
    for row in rows:
        if row["state"] == "sent":
            continue
        if row["state"] in ("sending", "uncertain"):
            raise RuntimeError(f"Part {row['part']+1} outcome uncertain; inspect group and resolve before continuing")
        key = (target, day, row["part"])
        with db:
            updated = db.execute("UPDATE feishu_outbox SET state='sending' WHERE target=? AND day=? AND part=? AND state='pending'", key)
            if updated.rowcount != 1:
                raise RuntimeError("Another sender owns this report part")
        try:
            send_with_retry(f, row["body"], sender, pause)
        except Exception as exc:
            state = "pending" if isinstance(exc, Rejected) else "uncertain"
            with db:
                db.execute("UPDATE feishu_outbox SET state=? WHERE target=? AND day=? AND part=?", (state, *key))
            raise
        with db:
            db.execute("UPDATE feishu_outbox SET state='sent',sent_at=? WHERE target=? AND day=? AND part=?", (now(), *key))
        sent += 1
        LOG.info("Feishu report %s: part %s/%s confirmed sent", day, row["part"] + 1, len(rows))
        pause(1.1)
    return f"{sent} parts sent; daily report complete"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--day", required=True)
    p.add_argument("--send", action="store_true", help="Actually send to configured group")
    p.add_argument("--edition", choices=("daily", "ccu-update", "regions"), default="daily", help="Optional separately deduplicated supplement")
    args = p.parse_args()
    db = connect(ROOT / config()["database"])
    try:
        f = settings()
        if args.send:
            if not f.get("enabled"):
                raise ValueError("Feishu is not enabled")
            print(deliver(db, args.day, f, edition=args.edition))
        else:
            parts = report_parts(db, args.day, f.get("keyword", "Roblox日报"))
            out = ROOT / "data" / f"feishu-preview-{args.day}.txt"
            out.write_text("\n\n========== 下一条消息 ==========\n\n".join(parts), encoding="utf-8")
            print(f"Preview only: {len(parts)} messages -> {out}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
