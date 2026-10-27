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
from .tracker_mapping import identity_from_event,identity_from_play,WatchIdentity,match_reason,normalized_ids,normalized_title
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


def rebuild_from_history(progression,entries,guild_timezones,rebuilt_at,*,provider='simkl',account_id=None):
    """Pure rebuild. No source writes or notifications; old progression is backed up by the caller."""
    from .storage import _complete_watch_challenges
    if provider not in {'simkl','wetrakr','mdblist'}:raise ValueError('Unknown rebuild provider')
    if not entries:raise ValueError('Cannot rebuild from an empty dated history snapshot')
    data=copy.deepcopy(progression)
    old_rows=watch_ledger.ensure(copy.deepcopy(progression))
    data.update(xp=0,lifetime_xp=0,prestige=0,xp_events=[],watch_xp_keys={},occurrence_ledger={},
                challenge_completions={},pending_challenge_notifications=[],achievement_xp_awarded={},
                pending_history_awards={},pending_history_challenge_awards={},history_repair_primary=provider,
                history_xp_seeded=True,history_xp_notification_sent=True,prestige_carry_repaired=True)
    rows=data['occurrence_ledger'];index=defaultdict(set);identities={};counts=Counter();lookup_count=0
    def anchors(identity):
        scope=(identity.season,identity.episode) if identity.kind=='episode' else (None,None)
        return [(identity.kind,name,value,scope) for name,value in normalized_ids(identity.ids).items()]+[
            (identity.kind,'title',normalized_title(identity.title),scope)]
    for entry in entries:
        kind=entry.get('media_type');stamp=str(entry.get('at') or '');key=entry.get('event_key')
        expected=(f"{kind}:{entry.get('item_key')}:{stamp}" if provider=='simkl' else
                  f"{provider}:{entry.get('source_play_id')}:{stamp}")
        if kind not in WATCH_TYPES or key!=expected:raise ValueError('Invalid rebuild watch key')
        try:datetime.fromisoformat(stamp.replace('Z','+00:00'))
        except ValueError:raise ValueError('Invalid SIMKL watch date') from None
        if key in rows:raise ValueError('Duplicate provider snapshot watch')
        runtime=watch_minutes(kind,entry.get('runtime_minutes'))
        event={'event_key':key,'media_type':kind,'title':entry.get('title') or 'Untitled','at':stamp,
               'ids':copy.deepcopy(entry.get('ids') or {}),'amount':xp_for_watch(kind,runtime),
               'runtime_minutes':runtime,'xp_rule':WATCH_XP_RULE}
        identity=(identity_from_event(event) if provider=='simkl' else identity_from_play({**event,'item_key':entry['item_key'],'watched_at':stamp}))
        if identity is None:raise ValueError('Unrecognized provider snapshot identity')
        rows[key]={'observations':{key:{'provider':provider,'identity':copy.deepcopy(identity.__dict__)}},'award_keys':[key],'needs_review':False}
        identities[key]=identity
        for anchor in anchors(identity):index[anchor].add(key)
        data['xp_events'].append(event);data['watch_xp_keys'][key]=stamp
        data['xp']+=event['amount'];data['lifetime_xp']+=event['amount'];counts[kind]+=1
        lookup_count+=entry.get('runtime_source')=='metadata_or_default'
    for ref in entries[0].get('excluded_native_refs',[]):
        if not ref.startswith(provider+':'):raise ValueError('Excluded reference belongs to another provider')
        data['watch_xp_keys'][ref]=''
    watch_xp=data['lifetime_xp']
    old_events={event['event_key']:event for event in progression.get('xp_events',[])}
    if provider!='simkl':
        if account_id is None:raise ValueError('Native rebuild requires an account ID')
        ledger=data.setdefault(provider+'_plays',{})
        ledger={key:{**value,'xp_key':None} for key,value in ledger.items() if str(value.get('account_id'))!=str(account_id)}
        for entry in entries:
            play=copy.deepcopy(entry.get('native_play') or {})
            if not play.get('source_event_id') or str(play.get('watched_at'))!=entry['at']:
                raise ValueError('Native rebuild requires a stable play record')
            ledger[entry['source_play_id']]={**play,'account_id':str(account_id),'xp_key':entry['event_key']}
        data[provider+'_plays']=ledger
    paired=held=0
    for old_row in old_rows.values():
        retained_primary=[ref for ref,obs in old_row['observations'].items() if obs['provider']==provider and ref in identities]
        for ref,obs in old_row['observations'].items():
            if obs['provider']==provider:
                if ref not in identities:data['watch_xp_keys'][ref]=(obs.get('identity') or {}).get('watched_at') or ''
                continue
            data['watch_xp_keys'][ref]=(obs.get('identity') or {}).get('watched_at') or ''
            candidates=set()
            if obs.get('identity'):
                identity=WatchIdentity(**obs['identity'])
                candidates=set().union(*(index[a] for a in anchors(identity)))
                candidates={key for key in candidates if match_reason(identity,identities[key])}
            # Preserve an established ledger pairing, including already reviewed
            # anime crosswalks, if its SIMKL occurrence exists in this snapshot.
            if len(retained_primary)==1:candidates={retained_primary[0]}
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
                if old:data['pending_history_awards'][ref]={**copy.deepcopy(old),'hold_reason':f'Excluded from {provider}-only rebuild; not a confirmed duplicate'}
    # Native play IDs and watch observations stay intact, but no longer point to
    # retired awards. Removal checks support through the occurrence ledger.
    for secondary in ('wetrakr','mdblist'):
        if secondary==provider:continue
        for play in data.get(secondary+'_plays',{}).values():play['xp_key']=None
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
    data[provider+'_rebuilt_at']=rebuilt_at
    report={'repair_mode':'simkl_rebuild' if provider=='simkl' else 'provider_rebuild','rebuild_provider':provider,'read_only':True,'generated_at':rebuilt_at,'fingerprint':fingerprint(progression),
            'before':{key:progression.get(key) for key in ('xp','prestige','lifetime_xp')},
            'projected':{'xp':data['xp'],'prestige':data['prestige'],'level':level_progress(data['xp'])[0],'lifetime_xp':data['lifetime_xp']},
            'breakdown':{'watch':watch_xp,'achievements':achievement_xp,'challenges':challenge_xp,'community_preserved':community},
            'watch_counts':dict(counts),'achievement_count':len(eligible),'guild_achievements':guild_unlocks,
            'matched_secondary_observations':paired,'held_secondary_observations':held,'runtime_lookup_or_default_count':lookup_count,
            'note':f'Fresh {provider}-only watch XP at 4 XP/minute. Existing challenge calendar rules (history since 2025-01-01) apply. Recorded community rewards are preserved. Missing runtimes use metadata or 25-minute episode/90-minute movie defaults. This is a new balance, not restoration of a previous level; prior manual XP offsets are discarded.'}
    return data,report


def rebuild_simkl(progression,entries,guild_timezones,rebuilt_at):
    """Backward-compatible SIMKL entry point and persisted-plan support."""
    return rebuild_from_history(progression,entries,guild_timezones,rebuilt_at)


async def provider_markers(app,uid,user,provider):
    if provider=='simkl':
        return await simkl_markers(app,uid,user,await app.valid_token(uid,user))
    adapter=app.provider_registry.get(provider)
    account=await adapter.link(uid);token=await adapter._token(account)
    client=app.wetrakr if provider=='wetrakr' else app.mdblist_tracking
    value=await client.last_activities(token)
    if not isinstance(value,dict):raise ValueError('Invalid provider activity response')
    if provider=='wetrakr':
        if not value.get('all'):raise ValueError('WeTrakr activity marker unavailable')
        visible=value.get('journal_visible_until')
        if visible:
            from .historical_repair import _stamp
            if _stamp(value['all'])>_stamp(visible):
                raise ValueError('WeTrakr has pending changes; wait a few seconds and retry')
        markers={'all':value['all']}
    else:
        # server_time is a response clock, not an activity revision.
        markers={key:value.get(key) for key in ('journal_at','watched_at','season_watched_at','episode_watched_at')}
        if not any(value is not None for value in markers.values()):raise ValueError('MDBList activity markers unavailable')
    return markers,token


async def fetch_provider_entries(app,uid,user,provider,baseline):
    """Native full-play reads preserve IDs, anime classification and runtime."""
    if provider=='simkl':return await fetch_simkl_entries(app,uid,user)
    if provider not in {'wetrakr','mdblist'}:raise ValueError('Unknown rebuild provider')
    from trackerbot.integrations.wetrakr_client import page_rows
    from trackerbot.integrations.wetrakr_events import normalize_compact_play
    from trackerbot.integrations.mdblist_provider import normalize_play
    adapter=app.provider_registry.get(provider);account=await adapter.link(uid)
    before,token=await provider_markers(app,uid,user,provider)
    plays=[];undated=0;excluded=[];scanned=set()
    def scoped_id(source_id):
        old=baseline.get(provider+'_plays',{}).get(source_id)
        return (source_id if provider=='wetrakr' and (old is None or str(old.get('account_id'))==account.account_id)
                else f'{account.account_id}/{source_id}')
    for kind in ('movie','episode'):
        pages=(app.wetrakr.compact_history(token,'movies' if kind=='movie' else 'episodes') if provider=='wetrakr'
               else app.mdblist_tracking.history(token,media_type=kind))
        async for page in pages:
            if provider=='wetrakr':raw=page_rows(page)
            else:
                bucket='movies' if kind=='movie' else 'episodes'
                if not isinstance(page.get(bucket),list):raise ValueError('Incomplete MDBList history page')
                raw=page[bucket]
            for row in raw:
                if not isinstance(row,dict) or not row.get('play_id'):raise ValueError('History entry lacks a stable play ID')
                native_id=str(row['play_id'])
                if native_id in scanned:raise ValueError('Duplicate native play ID in history')
                scanned.add(native_id)
                if row.get('watched_at_unknown'):
                    undated+=1
                    if row.get('watched_at'):excluded.append(f"{provider}:{scoped_id(native_id)}:{row['watched_at']}")
                    continue
                play=normalize_compact_play(row) if provider=='wetrakr' else normalize_play(row,kind)
                if not play or not play.get('watched_at') or play.get('media_type')!=kind:
                    raise ValueError('Incomplete provider watch history')
                from .historical_repair import _stamp
                _stamp(play['watched_at'])
                plays.append(play)
    if not plays:raise ValueError('No dated watches returned; refusing an empty rebuild')
    seen=set();cache={}
    async def resolve(play):
        source_id=str(play['source_event_id'])
        if source_id in seen:raise ValueError('Duplicate native play ID in history')
        seen.add(source_id)
        raw=play.get('runtime_minutes')
        try:valid=not isinstance(raw,bool) and math.isfinite(float(raw)) and float(raw)>0
        except (ValueError,TypeError,OverflowError):valid=False
        resolved=(await adapter.resolve_play(play,cache) if provider=='wetrakr' else await adapter.resolve_play(play))
        if resolved.get('media_type') not in WATCH_TYPES or not resolved.get('item_key'):
            raise ValueError('Provider metadata could not resolve a watch identity')
        play_id=scoped_id(source_id)
        return {'event_key':f"{provider}:{play_id}:{resolved['watched_at']}",
                'source_play_id':play_id,'media_type':resolved['media_type'],'item_key':resolved['item_key'],
                'at':resolved['watched_at'],'title':resolved.get('title') or 'Untitled','ids':resolved.get('ids') or {},
                'season':resolved.get('season'),'episode':resolved.get('episode'),
                'runtime_minutes':resolved.get('runtime_minutes'),'runtime_source':provider if valid else 'metadata_or_default',
                'native_play':resolved}
    entries=await bounded_map(plays,resolve,asyncio.Semaphore(4))
    if excluded:entries[0]['excluded_native_refs']=excluded
    after,_=await provider_markers(app,uid,user,provider)
    if before!=after:raise ValueError('Provider activity changed during the snapshot; generate a fresh preview')
    return entries,after,undated
