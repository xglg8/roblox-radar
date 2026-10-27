"""Public third-party observations; keep provider times and missing values explicit."""
from datetime import datetime, timezone
import json

PROVIDERS = {
    'rowatcher': ('RoWatcher', 'https://rowatcher.com/api/games/batch?ids=', 25),
    'romanum': ('Romanum', 'https://romanum.dev/api/public/stats?universeIds=', 10),
}


def parse(source, payload, expected):
    from radar import count, now
    if source == 'rowatcher':
        if payload.get('success') is not True:
            raise ValueError('RoWatcher rejected batch')
        games = payload['data']
    else:
        games = payload['games']
    if not isinstance(games, list):
        raise ValueError('Expected game list')
    rows = []
    seen = set()
    for g in games:
        uid, gid = str(g['universeId']), str(g['rootPlaceId'])
        if uid not in expected or expected[uid] != gid or gid in seen:
            raise ValueError('Unexpected or duplicate game identity')
        seen.add(gid)
        if g.get('playing') is None:
            continue
        ccu = count(g['playing'])
        stamp = (g.get('_latestSnapshot') or {}).get('time') or g.get('lastScraped') if source == 'rowatcher' else payload.get('fetchedAt')
        if stamp:
            dt = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                raise ValueError('Provider timestamp must include timezone')
            if (dt-datetime.now(timezone.utc)).total_seconds() > 300:
                raise ValueError('Provider timestamp is in the future')
        extra = {k:g.get(k) for k in ('visits','favorites','likes','dislikes','likeRatio','genre')}
        extra.update(universe_id=int(uid), provider_observed_at=stamp, retrieved_at=now(),
                     upstream='Roblox public data',
                     attribution_url=f'https://rowatcher.com/games/{uid}' if source == 'rowatcher' else f'https://romanum.dev/analytics/data')
        # Revenue and retention models are deliberately excluded from observed metrics.
        rows.append(dict(game_id=gid, name=g['name'], ccu=ccu,
                         url=f'https://www.roblox.com/games/{gid}', extra=extra))
    return rows


def collect(source, http, db, run_id):
    from radar import entries
    snap = db.execute("SELECT id FROM snapshots WHERE run_id=? AND platform='roblox' AND source='rolimons'", (run_id,)).fetchone()
    if not snap:
        raise ValueError('No current-run Top 500')
    candidates = entries(db, snap['id'])
    cache = {r['key'][9:]:r['value'] for r in db.execute("SELECT * FROM cache WHERE key LIKE 'universe:%'")}
    expected = {cache[g['game_id']]:g['game_id'] for g in candidates if g['game_id'] in cache}
    _, base, size = PROVIDERS[source]
    ids, rows, errors, failed = sorted(expected), [], [], 0
    for start in range(0, len(ids), size):
        batch = ids[start:start+size]
        try:
            rows.extend(parse(source, http.get(base+','.join(batch)), {uid:expected[uid] for uid in batch}))
            failed = 0
        except Exception as exc:
            errors.append(f'{source} batch {start//size+1}: {exc}')
            failed += 1
            if failed >= 3:
                errors.append('Stopped after three consecutive failed batches'); break
    if len(rows) != len(candidates):
        errors.append(f'{source} coverage {len(rows)}/{len(candidates)}; missing is not zero')
    http.issues, http.candidate_count = errors, len(candidates)
    return rows


def comparisons(db, anchor, checks):
    if not anchor:
        return []
    result = []
    reference_time = datetime.fromisoformat(anchor['observed_at'])
    for source, (label, _, _) in PROVIDERS.items():
        snap = db.execute('SELECT * FROM snapshots WHERE run_id=? AND platform=? AND source=?',
                          (anchor['run_id'], 'roblox', source)).fetchone()
        records = {r['game_id']:dict(r) for r in db.execute('SELECT * FROM entries WHERE snapshot_id=?', (snap['id'],))} if snap else {}
        result.append({'source':source, 'label':label, 'matched_count':len(records), 'snapshot':dict(snap) if snap else None})
        for row in checks:
            entry = records.get(row['game_id'])
            value = {'ccu':None, 'status':'missing', 'difference':None, 'difference_pct':None, 'metadata':None}
            if entry:
                meta = json.loads(entry['extra'])
                stamp = meta.get('provider_observed_at')
                gap = abs((datetime.fromisoformat(stamp.replace('Z','+00:00'))-reference_time).total_seconds()) if stamp else None
                status = 'time_unknown' if gap is None else 'time_mismatch' if gap > 900 else 'matched'
                baseline = row['official_ccu']
                diff = entry['ccu']-baseline if baseline is not None and status == 'matched' else None
                value = dict(ccu=entry['ccu'], status=status, difference=diff,
                             difference_pct=diff/baseline*100 if diff is not None and baseline else None, metadata=meta)
            row.setdefault('external', {})[source] = value
    return result
