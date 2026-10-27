"""New-game signals based on official creation dates, not title or first discovery."""
import json
from datetime import datetime, timezone

DEFAULTS = {'max_age_days':30, 'min_ccu':1000, 'rising_min_ccu':500, 'recent_seen_days':7}


def remember(db, game, checked_at):
    created = game.get('created')
    if not created:
        return False
    try:
        dt = datetime.fromisoformat(created.replace('Z','+00:00'))
    except (ValueError, AttributeError):
        return False
    if dt.tzinfo is None:
        return False
    data = {'created_at':dt.isoformat(), 'checked_at':checked_at, 'universe_id':str(game['id'])}
    db.execute('INSERT OR REPLACE INTO cache VALUES (?,?)', ('created:'+str(game['rootPlaceId']),json.dumps(data)))
    return True


def annotate(db, snapshot, rows, rules=None):
    rules = rules or DEFAULTS
    at = datetime.fromisoformat(snapshot['observed_at'])
    metadata = {r['key'][8:]:json.loads(r['value']) for r in db.execute("SELECT * FROM cache WHERE key LIKE 'created:%'")}
    first = {r['game_id']:r['seen'] for r in db.execute('''SELECT e.game_id,MIN(s.observed_at) seen FROM entries e
        JOIN snapshots s ON s.id=e.snapshot_id WHERE s.platform=? AND s.source=? AND s.observed_at<=?
        GROUP BY e.game_id''',(snapshot['platform'],snapshot['source'],snapshot['observed_at']))}
    beginning = db.execute('SELECT MIN(observed_at) FROM snapshots WHERE platform=? AND source=?',
                           (snapshot['platform'],snapshot['source'])).fetchone()[0]
    for g in rows:
        meta = metadata.get(g['game_id'], {})
        created = meta.get('created_at')
        age = (at-datetime.fromisoformat(created)).total_seconds()/86400 if created else None
        valid = age is not None and age >= 0
        age_days = int(age) if valid else None
        recently_created = valid and age <= rules['max_age_days']
        growing = bool(g.get('growth',{}).get('alerts'))
        hot = recently_created and (g['ccu'] >= rules['min_ccu'] or (growing and g['ccu'] >= rules['rising_min_ccu']))
        seen = first.get(g['game_id'])
        recent_seen = bool(seen and seen != beginning and 0 <= (at-datetime.fromisoformat(seen)).total_seconds() <= rules['recent_seen_days']*86400)
        g['novelty'] = dict(hot_new=bool(hot), age_days=age_days, created_at=created if valid else None,
                           metadata_checked_at=meta.get('checked_at'), first_seen=seen, recently_observed=recent_seen,
                           reason='CCU达到热门门槛' if hot and g['ccu']>=rules['min_ccu'] else 'CCU达标且触发上涨标记' if hot else None)
    return {'rules':rules, 'hot_count':sum(g['novelty']['hot_new'] for g in rows),
            'recent_count':sum(g['novelty']['recently_observed'] for g in rows),
            'known_creation_count':sum(g['novelty']['created_at'] is not None for g in rows)}


def refresh(db, http):
    from radar import latest, entries, now
    snap = latest(db,'roblox','rolimons')
    if not snap:
        return {'checked':0, 'errors':['No Top 500 snapshot']}
    candidates = {g['game_id'] for g in entries(db,snap['id'])}
    ids = {r['value']:r['key'][9:] for r in db.execute("SELECT * FROM cache WHERE key LIKE 'universe:%'") if r['key'][9:] in candidates}
    keys, checked, errors = sorted(ids), 0, []
    for start in range(0,len(keys),50):
        batch = keys[start:start+50]
        try:
            data = http.get('https://games.roblox.com/v1/games?universeIds='+','.join(batch))
            with db:
                for g in data['data']:
                    if str(g['id']) in batch and str(g['rootPlaceId']) == ids[str(g['id'])]:
                        checked += int(remember(db,g,now()))
        except Exception as exc:
            errors.append(str(exc))
    return {'checked':checked,'errors':errors}


if __name__=='__main__':
    from radar import ROOT, config, connect, Http
    c=config();db=connect(ROOT/c['database'])
    try:print(json.dumps(refresh(db,Http(c))))
    finally:db.close()
