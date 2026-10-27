"""Conservative historical overlap preview. Never changes live XP."""
import asyncio
import copy
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime,timezone
import hashlib
import json
import unicodedata

from . import watch_ledger
from .tracker_mapping import WatchIdentity,normalized_ids,match_reason
from .progression import xp_for_level,level_progress


def title_key(value):
    value=unicodedata.normalize('NFKD',value or '').casefold()
    return ''.join(c for c in value if c.isalnum())


def simkl_key(reference,identity):
    stamp=identity.get('watched_at') or ''
    parts=reference[:-(len(stamp)+1)].split(':') if stamp and reference.endswith(':'+stamp) else []
    if len(parts)>=4 and parts[1]=='series':return 'episode',parts[3]
    if len(parts)>=3 and parts[1]=='movies':return 'movie',parts[2]
    return None


def hydrate_simkl(progression,catalog):
    """Fill legacy identity metadata by native SIMKL ID; no fuzzy title joins."""
    lookup={('movie' if row.media_type=='movie' else 'episode',str(row.title_ids['simkl'])):row
            for row in catalog if row.title_ids.get('simkl') is not None}
    count=0
    events={event.get('event_key'):event for event in progression.get('xp_events',[])}
    for row in watch_ledger.ensure(progression).values():
        for ref,obs in row['observations'].items():
            if obs['provider']!='simkl' or not obs.get('identity'):continue
            identity=obs['identity'];fresh=lookup.get(simkl_key(ref,identity))
            if not fresh:continue
            old=normalized_ids(identity.get('ids'));new=normalized_ids(fresh.title_ids)
            if any(old[key]!=new[key] for key in old.keys()&new.keys()):
                row['needs_review']=True;continue
            if new.items()<=old.items():continue
            identity['ids']={**(identity.get('ids') or {}),**fresh.title_ids};count+=1
            if ref in events:events[ref]['ids']=copy.deepcopy(identity['ids'])
    return count


async def canonicalize(progression,tmdb):
    """Compare exact TMDB episode IDs/coordinates and proven complete absolute runs.

    Partial absolute runs and uncertain seasonal aliases stay unresolved rather
    than being guessed from series names or total watch counts alone.
    """
    rows=watch_ledger.ensure(progression);groups=defaultdict(list);bridges=defaultdict(set)
    observations=[obs for row in rows.values() for obs in row['observations'].values() if obs.get('identity')]
    for obs in observations:
        ids=normalized_ids(obs['identity'].get('ids'))
        if ids.get('tmdb'):
            for key,value in ids.items():bridges[(key,value)].add(ids['tmdb'])
    for obs in observations:
        identity=obs['identity'];ids=normalized_ids(identity.get('ids'))
        options={value for key,anchor in ids.items() for value in bridges[(key,anchor)]}
        sid=ids.get('tmdb') or (next(iter(options)) if len(options)==1 else None)
        if sid and 'episode' in identity['media_type']:groups[sid].append(obs)
    native={}
    for provider in ('mdblist','wetrakr'):
        for play_id,play in progression.get(provider+'_plays',{}).items():
            native[f"{provider}:{play_id}:{play['watched_at']}"]=play
    refs={id(obs):ref for row in rows.values() for ref,obs in row['observations'].items()}
    semaphore=asyncio.Semaphore(4);changed=0;unresolved=0
    async def normalize(sid,items):
        nonlocal changed,unresolved
        if len({o['provider'] for o in items})<2:return
        async with semaphore:
            series=await tmdb._get_series_details(sid) or {}
            summaries=[s for s in series.get('seasons') or [] if isinstance(s.get('season_number'),int) and s['season_number']>0]
            seasons={}
            for season in summaries:
                seasons[season['season_number']]=await tmdb._get_season_details(sid,season['season_number']) or {}
        complete=bool(summaries) and all(len(seasons[s['season_number']].get('episodes') or [])==s.get('episode_count') for s in summaries)
        episodes=[(sn,ep) for sn,data in sorted(seasons.items()) for ep in sorted(data.get('episodes') or [],key=lambda e:e.get('episode_number',0))]
        by_id={str(ep['id']):(sn,ep['episode_number']) for sn,ep in episodes if ep.get('id')}
        points={(sn,ep.get('episode_number')) for sn,ep in episodes}
        simkl_groups=defaultdict(list)
        for obs in items:
            if obs['provider']=='simkl':
                anchor=simkl_key(refs[id(obs)],obs['identity'])
                if anchor:simkl_groups[anchor].append(obs)
        absolute=set()
        for observations in simkl_groups.values():
            identities=[o['identity'] for o in observations]
            numbers={i['episode'] for i in identities}
            if (complete and episodes and all(i['media_type']=='anime_episode' and i['season']==1 for i in identities)
                    and numbers==set(range(1,len(episodes)+1))):
                absolute.update(id(o) for o in observations)
        for obs in items:
            identity=obs['identity'];point=None;reason=None
            play=native.get(refs[id(obs)]) or {}
            own_id=(play.get('episode_ids') or {}).get('tmdb')
            if own_id and str(own_id) in by_id:point=by_id[str(own_id)];reason='exact_episode_id'
            elif id(obs) in absolute:
                sn,ep=episodes[identity['episode']-1];point=(sn,ep['episode_number']);reason='complete_absolute_run'
            elif obs['provider']!='simkl' and (play.get('source_season'),play.get('source_episode')) in points:
                point=(play['source_season'],play['source_episode']);reason='source_catalog_coordinates'
            elif identity['media_type']=='episode' and (identity['season'],identity['episode']) in points:
                point=(identity['season'],identity['episode']);reason='catalog_coordinates'
            if point:
                obs['canonical_identity']={**identity,'ids':{**identity.get('ids',{}),'tmdb':str(sid)},'season':point[0],'episode':point[1]}
                obs['crosswalk_evidence']=reason;changed+=1
            else:unresolved+=1
    await asyncio.gather(*(normalize(sid,items) for sid,items in groups.items()))
    return {'canonicalized_observations':changed,'unresolved_episode_observations':unresolved}


def build_preview(progression,revoke_challenges):
    data=copy.deepcopy(progression);rows=watch_ledger.ensure(data)
    fingerprint=hashlib.sha256(json.dumps(progression,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    primary=progression.get('history_repair_primary')
    awards={e['event_key']:e for e in data.get('xp_events',[])}
    index=defaultdict(set);titles=defaultdict(set);entries=[]
    for key,row in rows.items():
        for ref,obs in row['observations'].items():
            value=obs.get('canonical_identity') or obs.get('identity')
            if not value:continue
            identity=WatchIdentity(**value);kind=identity.kind
            anchors=[(kind,name,value) for name,value in normalized_ids(identity.ids).items()]
            anchors.append((kind,'title',title_key(identity.title)))
            entries.append((key,ref,obs,identity,anchors))
            scope=(identity.season,identity.episode) if kind=='episode' else (None,None)
            for anchor in anchors:
                index[(anchor,scope)].add(len(entries)-1)
                titles[anchor].add(obs['provider'])
    confirmed=[];review=[];unmatched=[];seen=set();remove=set()
    for n,(key,ref,obs,identity,anchors) in enumerate(entries):
        scope=(identity.season,identity.episode) if identity.kind=='episode' else (None,None)
        candidates=set().union(*(index[(a,scope)] for a in anchors))
        cross=[other for other in candidates if entries[other][2]['provider']!=obs['provider']]
        if not any(titles[a]-{obs['provider']} for a in anchors):
            unmatched.append({'reference':ref,'provider':obs['provider'],'title':identity.title,
                              'award_keys':rows[key]['award_keys'],'reason':'no_cross_provider_overlap_found; may be additional history or missing metadata'})
        if not cross and any(titles[a]-{obs['provider']} for a in anchors):
            review.append({'references':[ref],'title':identity.title,'reason':'episode_crosswalk_needs_review','award_keys':rows[key]['award_keys']})
        for other in candidates:
            if other<=n:continue
            ok,oref,oobs,oi,_=entries[other]
            if obs['provider']==oobs['provider']:continue
            pair=tuple(sorted((key,ok)))
            if pair in seen:continue
            # Shared ID conflicts are never overridden by a title alias.
            a,b=normalized_ids(identity.ids),normalized_ids(oi.ids)
            if any(a[x]!=b[x] for x in a.keys()&b.keys()):
                if pair not in seen:
                    seen.add(pair);review.append({'references':[ref,oref],'title':identity.title,'reason':'conflicting_title_ids','award_keys':list(dict.fromkeys(rows[key]['award_keys']+rows[ok]['award_keys']))})
                continue
            same_episode=identity.kind=='movie' or (identity.season,identity.episode)==(oi.season,oi.episode)
            reason=match_reason(identity,oi) if same_episode else None
            if key==ok and len(rows[key]['award_keys'])<2:continue
            seen.add(pair)
            keys=list(dict.fromkeys(rows[key]['award_keys']+rows[ok]['award_keys']))
            amount=sum(int(awards.get(k,{}).get('amount',0)) for k in keys)
            item={'references':[ref,oref],'title':identity.title,'award_keys':keys,'combined_watch_xp':amount,
                  'identities':[asdict(identity),asdict(oi)],'providers':[obs['provider'],oobs['provider']],
                  'reason':reason or ('different_watch_dates_or_missing_ids' if same_episode else 'episode_crosswalk_needs_review')}
            # Only exact catalog IDs AND the existing strict time rule justify
            # an automatic deduction preview. Different dates remain reviewable.
            oscope=(oi.season,oi.episode) if oi.kind=='episode' else (None,None)
            reverse=set().union(*(index[(a,oscope)] for a in entries[other][4]))
            reverse_cross=[i for i in reverse if entries[i][2]['provider']!=oobs['provider']]
            if reason=='verified_id' and len(keys)>1 and len(cross)==1 and len(reverse_cross)==1:
                # Prefer the primary provider's own award, even for a merged row.
                def priority(k):
                    provider=k.split(':',1)[0] if k.startswith(('mdblist:','wetrakr:')) else 'simkl'
                    return (provider!=primary if primary else k.startswith(('mdblist:','wetrakr:')),
                            str(awards.get(k,{}).get('at','')),k)
                keep=min(keys,key=priority)
                item['remove_award_keys']=[k for k in keys if k!=keep]
                confirmed.append(item);remove.update(item['remove_award_keys'])
            else:
                if primary in item['providers']:
                    position=item['providers'].index(primary)
                    item['primary_reference']=item['references'][position]
                    item['primary_watched_at']=item['identities'][position]['watched_at']
                    item['review_action']='Preserve the primary date if confirmed as imported history; keep separate if a genuine rewatch.'
                review.append(item)
    removed=sum(int(awards[k].get('amount',0)) for k in remove if k in awards)
    data['xp_events']=[e for e in data.get('xp_events',[]) if e['event_key'] not in remove]
    data['lifetime_xp']=max(0,int(data.get('lifetime_xp',0))-removed)
    challenge_delta=revoke_challenges(data) if remove else 0
    offset=(int(progression.get('prestige',0))*xp_for_level(100)+int(progression.get('xp',0))
            -int(progression.get('lifetime_xp',0)))
    prestige,xp=divmod(max(0,int(data.get('lifetime_xp',0))+offset),xp_for_level(100))
    return {'read_only':True,'fingerprint':fingerprint,'generated_at':datetime.now(timezone.utc).isoformat(),
            'primary_history':primary,'primary_scope':'This Discord user only; does not change active tracking source or provider data.',
            'before':{k:progression.get(k) for k in ('xp','prestige','lifetime_xp')},
            'confirmed_duplicate_groups':confirmed,'uncertain_overlaps':review,
            'unmatched_observations':unmatched,
            'unresolved_history_awards':len(data.get('pending_history_awards',{})),
            'held_history_awards':[{field:event.get(field) for field in ('event_key','media_type','title','at','amount','ids')}
                                   for event in data.get('pending_history_awards',{}).values()],
            'confirmed_watch_xp_reduction':removed,'dependent_challenge_xp_reduction':challenge_delta,
            'preserved_progression_offset':offset,
            'projected':{'prestige':prestige,'xp':xp,'level':level_progress(xp)[0],'lifetime_xp':data['lifetime_xp']},
            'note':'No live XP is changed. Different dates, numbering and title-only matches require review; potential overlap is not a confirmed duplicate.'}
