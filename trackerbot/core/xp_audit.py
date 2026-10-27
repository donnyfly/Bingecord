"""Credential-free, read-only XP ledger export for the requesting member."""
import copy
from collections import Counter
from datetime import datetime,timezone
from . import watch_ledger

ID_KEYS={'tmdb','tvdb','imdb','mal','simkl','mdblist','wetrakr'}


def safe_identity(value):
    if not value:return None
    result={key:value.get(key) for key in ('media_type','title','watched_at','season','episode')}
    result['ids']={key:val for key,val in (value.get('ids') or {}).items() if key in ID_KEYS}
    return result


def build_xp_audit(progression):
    data=copy.deepcopy(progression)
    rows=watch_ledger.ensure(data)
    totals=Counter();counts=Counter();events=[]
    for event in data.get('xp_events',[]):
        kind=event.get('media_type','unknown');key=str(event.get('event_key') or '')
        source=(key.split(':',1)[0] if key.startswith(('mdblist:','wetrakr:')) else 'simkl') if kind in {'movie','episode','anime_movie','anime_episode'} else kind
        totals[source]+=int(event.get('amount') or 0);counts[source]+=1
        item={key:event.get(key) for key in ('event_key','at','amount','media_type','title','runtime_minutes','xp_rule') if key in event}
        item['ids']={key:value for key,value in (event.get('ids') or {}).items() if key in ID_KEYS}
        events.append(item)
    observations=Counter(o['provider'] for row in rows.values() for o in row['observations'].values())
    return {'generated_at':datetime.now(timezone.utc).isoformat(),
            'read_only':True,'warning':'Unpaired observations may include unmatched duplicates or genuine distinct viewings. Zero recognized double awards does not rule out duplicate XP across separate occurrences.',
            'progression':{key:data.get(key) for key in ('xp','lifetime_xp','prestige','history_xp_seeded')},
            'xp_by_source_or_reward':dict(totals),'event_counts':dict(counts),
            'observation_counts':dict(observations),'event_xp_sum':sum(totals.values()),
            'awards':events,'occurrences':{key:{'award_keys':row['award_keys'],'needs_review':row.get('needs_review',False),
                'observations':{ref:{'provider':obs['provider'],'identity':safe_identity(obs.get('identity'))}
                                for ref,obs in row['observations'].items()}} for key,row in rows.items()},
            'challenge_completions':copy.deepcopy(data.get('challenge_completions') or {})}
