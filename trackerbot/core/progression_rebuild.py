"""Fresh SIMKL progression snapshots, with explicit reward and import policies."""
import asyncio
import copy
import math
from collections import Counter,defaultdict
from datetime import datetime,timezone
from zoneinfo import ZoneInfo

from .achievements import ACHIEVEMENTS
from .historical_repair import fingerprint
from .import_work import bounded_map
from .progression import WATCH_XP_RULE,watch_minutes,xp_for_watch,xp_for_level,level_progress
from .tracker_mapping import identity_from_event,WatchIdentity,match_reason,normalized_ids,normalized_title
from . import watch_ledger

WATCH_TYPES={'movie','anime_movie','episode','anime_episode'}


def achievement_counters(events,timezone_name='Asia/Singapore'):
    active=[e for e in events if e.get('media_type') in WATCH_TYPES]
    try:tz=ZoneInfo(timezone_name)
    except (ValueError,TypeError,KeyError):tz=ZoneInfo('Asia/Singapore')
    days=set()
    for e in active:
        try:
            stamp=datetime.fromisoformat(str(e.get('at')).replace('Z','+00:00'))
            if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=timezone.utc)
            days.add(stamp.astimezone(tz).date())
        except (ValueError,TypeError):pass
    longest=length=0;previous=None
    for day in sorted(days):
        length=length+1 if previous is not None and (day-previous).days==1 else 1
        longest=max(longest,length);previous=day
    counts=Counter(e['media_type'] for e in active)
    return {'total':len(active),'episodes':counts['episode']+counts['anime_episode'],
            'movies':counts['movie']+counts['anime_movie'],'anime_episodes':counts['anime_episode'],
            'anime_movies':counts['anime_movie'],'active_days':len(days),'streak':longest,
            'unique_titles':len({str(e['title']).casefold() for e in active if e.get('title')})}


async def simkl_markers(app,uid,user,token):
    value,token=await app.call_refresh(uid,user,token,app.simkl.get_activities)
    if not isinstance(value,dict):raise ValueError('Invalid SIMKL activity response')
    result={key:(value.get(key) or {}).get('all') for key in app.ACTIVITY_KEYS.values()}
    if not any(result.values()):raise ValueError('SIMKL activity markers unavailable')
    return result,token


async def fetch_simkl_entries(app,uid,user):
    """Read all three catalogs; fail if activity changes or a catalog read fails."""
    token=await app.valid_token(uid,user)
    before,token=await simkl_markers(app,uid,user,token)
    entries={};undated=0
    for scope in app.MEDIA_TYPES:
        items,token=await app.cached_simkl_items(uid,user,token,scope,request_cache={},timeout=app.HISTORY_FETCH_TIMEOUT_SECONDS)
        if not isinstance(items,list):raise ValueError('Incomplete SIMKL catalog response')
        for item in items:
            movie=scope=='movies' or scope=='anime' and await app.is_anime_movie_item(item)
            if movie:
                media=item.get('movie') or item.get('show') or {};ids=media.get('ids') or {};sid=ids.get('simkl')
                stamp=item.get('last_watched_at')
                if sid is None or not stamp:continue
                kind='anime_movie' if scope=='anime' or ids.get('mal') or media.get('anime_type')=='movie' or media.get('type')=='movie' else 'movie'
                event={'media_type':kind,'item_key':f'movies:{sid}','at':stamp,'title':media.get('title') or 'Untitled',
                       'ids':ids,'runtime_minutes':media.get('runtime')}
                anchor=('movie',str(sid),stamp)
                if anchor not in entries or kind=='anime_movie':entries[anchor]=event
            else:
                for episode in app.iter_show_episodes(scope,[item]):
                    if not episode.get('watched_raw'):undated+=1;continue
                    kind='anime_episode' if scope=='anime' else 'episode'
                    key=f"series:{scope}:{episode['simkl_id']}:{episode['season_num']}:{episode['episode_number']}"
                    event={'media_type':kind,'item_key':key,'at':episode['watched_raw'],
                           'title':episode.get('show_title') or 'Untitled','ids':episode.get('ids') or {},
                           'season':episode['season_num'],'episode':episode['episode_number'],
                           'runtime_minutes':episode.get('runtime_minutes')}
                    entries[(key,event['at'])]=event
    if not entries:raise ValueError('No dated SIMKL watches returned; refusing to prepare an empty rebuild')
    async def resolve(event):
        event=copy.deepcopy(event)
        raw=event.get('runtime_minutes')
        try:valid=not isinstance(raw,bool) and math.isfinite(float(raw)) and float(raw)>0
        except (ValueError,TypeError,OverflowError):valid=False
        event['runtime_source']='simkl' if valid else 'metadata_or_default'
        event['runtime_minutes']=await app.tmdb.get_watch_runtime(event['media_type'],event['ids'],
            season=event.get('season'),episode=event.get('episode'),runtime=raw)
        event['event_key']=f"{event['media_type']}:{event['item_key']}:{event['at']}"
        return event
    entries=await bounded_map(list(entries.values()),resolve,asyncio.Semaphore(4))
    after,_=await simkl_markers(app,uid,user,token)
    if before!=after:raise ValueError('SIMKL changed during the snapshot; generate a fresh rebuild preview')
    return entries,after,undated


def rebuild_simkl(progression,entries,guild_timezones,rebuilt_at):
    """Pure rebuild. No source writes or notifications; old progression is backed up by the caller."""
    from .storage import _complete_watch_challenges
    if not entries:raise ValueError('Cannot rebuild from an empty SIMKL snapshot')
    data=copy.deepcopy(progression)
    old_rows=watch_ledger.ensure(copy.deepcopy(progression))
    data.update(xp=0,lifetime_xp=0,prestige=0,xp_events=[],watch_xp_keys={},occurrence_ledger={},
                challenge_completions={},pending_challenge_notifications=[],achievement_xp_awarded={},
                pending_history_awards={},pending_history_challenge_awards={},history_repair_primary='simkl',
                history_xp_seeded=True,history_xp_notification_sent=True,prestige_carry_repaired=True)
    rows=data['occurrence_ledger'];index=defaultdict(set);identities={};counts=Counter();lookup_count=0
    def anchors(identity):
        scope=(identity.season,identity.episode) if identity.kind=='episode' else (None,None)
        return [(identity.kind,name,value,scope) for name,value in normalized_ids(identity.ids).items()]+[
            (identity.kind,'title',normalized_title(identity.title),scope)]
    for entry in entries:
        kind=entry.get('media_type');stamp=str(entry.get('at') or '');key=entry.get('event_key')
        if kind not in WATCH_TYPES or key!=f"{kind}:{entry.get('item_key')}:{stamp}":raise ValueError('Invalid rebuild watch key')
        try:datetime.fromisoformat(stamp.replace('Z','+00:00'))
        except ValueError:raise ValueError('Invalid SIMKL watch date') from None
        if key in rows:raise ValueError('Duplicate SIMKL snapshot watch')
        runtime=watch_minutes(kind,entry.get('runtime_minutes'))
        event={'event_key':key,'media_type':kind,'title':entry.get('title') or 'Untitled','at':stamp,
               'ids':copy.deepcopy(entry.get('ids') or {}),'amount':xp_for_watch(kind,runtime),
               'runtime_minutes':runtime,'xp_rule':WATCH_XP_RULE}
        identity=identity_from_event(event)
        if identity is None:raise ValueError('Unrecognized SIMKL snapshot identity')
        rows[key]={'observations':{key:{'provider':'simkl','identity':copy.deepcopy(identity.__dict__)}},'award_keys':[key],'needs_review':False}
        identities[key]=identity
        for anchor in anchors(identity):index[anchor].add(key)
        data['xp_events'].append(event);data['watch_xp_keys'][key]=stamp
        data['xp']+=event['amount'];data['lifetime_xp']+=event['amount'];counts[kind]+=1
        lookup_count+=entry.get('runtime_source')=='metadata_or_default'
    watch_xp=data['lifetime_xp']
    old_events={event['event_key']:event for event in progression.get('xp_events',[])}
    paired=held=0
    for old_row in old_rows.values():
        retained_simkl=[ref for ref,obs in old_row['observations'].items() if obs['provider']=='simkl' and ref in rows]
        for ref,obs in old_row['observations'].items():
            if obs['provider']=='simkl':continue
            data['watch_xp_keys'][ref]=(obs.get('identity') or {}).get('watched_at') or ''
            candidates=set()
            if obs.get('identity'):
                identity=WatchIdentity(**obs['identity'])
                candidates=set().union(*(index[a] for a in anchors(identity)))
                candidates={key for key in candidates if match_reason(identity,identities[key])}
            # Preserve an established ledger pairing, including already reviewed
            # anime crosswalks, if its SIMKL occurrence exists in this snapshot.
            if len(retained_simkl)==1:candidates={retained_simkl[0]}
            target=next(iter(candidates)) if len(candidates)==1 else None
            if target and any(o['provider']==obs['provider'] for o in rows[target]['observations'].values()):target=None
            if target:
                rows[target]['observations'][ref]=copy.deepcopy(obs);paired+=1
            else:
                rows[ref]={'observations':{ref:copy.deepcopy(obs)},'award_keys':[],'needs_review':True};held+=1
                old=old_events.get(ref) or progression.get('pending_history_awards',{}).get(ref)
                if not old and obs.get('identity'):
                    identity=obs['identity']
                    old={'event_key':ref,'media_type':identity['media_type'],'title':identity['title'],
                         'at':identity['watched_at'],'ids':identity.get('ids') or {},'amount':0}
                if old:data['pending_history_awards'][ref]={**copy.deepcopy(old),'hold_reason':'Excluded from SIMKL-only rebuild; not a confirmed duplicate'}
    # Native play IDs and watch observations stay intact, but no longer point to
    # retired awards. Removal checks support through the occurrence ledger.
    for provider in ('wetrakr','mdblist'):
        for play in data.get(provider+'_plays',{}).values():play['xp_key']=None
    _complete_watch_challenges(data,list(data['xp_events']),notify=False)
    challenge_xp=data['lifetime_xp']-watch_xp
    guild_unlocks={};eligible=set()
    for gid,tz in guild_timezones.items():
        metrics=achievement_counters(data['xp_events'],tz)
        unlocked=[aid for aid,definition in ACHIEVEMENTS.items() if metrics.get(definition['category'],0)>=definition['threshold']]
        guild_unlocks[gid]=unlocked;eligible.update(unlocked)
    for aid in sorted(eligible):
        definition=ACHIEVEMENTS[aid];amount=int(definition.get('xp',0))
        data['xp_events'].append({'event_key':f'achievement:{aid}','achievement_id':aid,'at':rebuilt_at,
                                 'media_type':'achievement','title':definition['name'],'amount':amount})
        data['achievement_xp_awarded'][aid]=rebuilt_at
        data['lifetime_xp']+=amount
    achievement_xp=data['lifetime_xp']-watch_xp-challenge_xp
    community=sum(max(0,int(value)) for value in data.get('community_rewards',{}).values())
    data['lifetime_xp']+=community
    data['prestige'],data['xp']=divmod(data['lifetime_xp'],xp_for_level(100))
    data['prestige_notified']=data['prestige'];data['watch_occurrences']=watch_ledger.audit(rows)
    data['simkl_rebuilt_at']=rebuilt_at
    report={'repair_mode':'simkl_rebuild','read_only':True,'generated_at':rebuilt_at,'fingerprint':fingerprint(progression),
            'before':{key:progression.get(key) for key in ('xp','prestige','lifetime_xp')},
            'projected':{'xp':data['xp'],'prestige':data['prestige'],'level':level_progress(data['xp'])[0],'lifetime_xp':data['lifetime_xp']},
            'breakdown':{'watch':watch_xp,'achievements':achievement_xp,'challenges':challenge_xp,'community_preserved':community},
            'watch_counts':dict(counts),'achievement_count':len(eligible),'guild_achievements':guild_unlocks,
            'matched_secondary_observations':paired,'held_secondary_observations':held,'runtime_lookup_or_default_count':lookup_count,
            'note':'Fresh SIMKL-only watch XP at 4 XP/minute. Existing challenge calendar rules (history since 2025-01-01) apply. Recorded community rewards are preserved. Missing runtimes use metadata or 25-minute episode/90-minute movie defaults. This is a new balance, not restoration of a previous level; prior manual XP offsets are discarded.'}
    return data,report
