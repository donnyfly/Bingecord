"""Versioned cooperative events. Pure scoring; no API calls or progression writes."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import math
from .community import split_pool
from .progression import event_watch_minutes

ROLLOUT='2026-11-23'
ROTATION=('watchathon','genres','bingo','decades')
NAMES={'watchathon':'Community Watchathon','genres':'Genre Expedition',
       'bingo':'Community Bingo','decades':'Decade Discovery'}
BINGO=(('comedy_film','Comedy film'),('classic','Pre-2000 title'),('first','First episode'),
       ('animation_film','Animated film'),('mystery','Mystery title'),('documentary','Documentary film'),
       ('shared','Shared film'),('fantasy','Sci-fi / fantasy title'),('drama_series','Drama series'))
WATCH_TYPES={'movie','anime_movie','episode','anime_episode'}


def event_goal(week,size):
    size=max(1,int(size));kind=ROTATION[((datetime.fromisoformat(week).date()-datetime.fromisoformat(ROLLOUT).date()).days//7)%4]
    required=min(6,max(1,math.ceil(size*.6)))
    if size==2:required=2
    slots=min(12,max(4,2*size)) if kind=='genres' else 9 if kind=='bingo' else min(5,max(3,size+1)) if kind=='decades' else 600*size
    decades=[1980,1990,2000,2010,2020][-slots:] if kind=='decades' else []
    per_decade=max(1,math.ceil(2*size/max(1,len(decades))))
    return {'version':2,'kind':kind,'name':NAMES[kind],'baseline':size,'active_members':size,
            'target':slots,'pool':3000*size,'required_contributors':required,
            'qualifying_minutes':120,'qualifying_titles':2,'minimum_titles':max(slots,2*size) if kind=='genres' else 0,
            'decades':decades,'per_decade':per_decade,'shared_members':required,
            'settlement_hours':6,'delivery':{},'metadata':{}}


def parse_stamp(value):
    try:
        stamp=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
    except (TypeError,ValueError):return None


def metadata_key(movie,ids):
    for name in ('tmdb','tvdb','imdb'):
        if ids.get(name):return f"{'movie' if movie else 'tv'}:{name}:{str(ids[name]).lower()}"
    return None


def credited_watches(users,member_ids,start,end,providers):
    """One live supported occurrence per user; uncredited history never participates."""
    result=[]
    for uid in sorted(map(str,member_ids)):
        user=users.get(uid) or {};p=user.get('progression') or {};provider=providers.get(uid,'simkl')
        account=str((user.get(provider) or {}).get('account_id')) if provider in {'wetrakr','mdblist'} else None
        native_refs={f"{provider}:{pid}:{play.get('watched_at')}" for pid,play in p.get(provider+'_plays',{}).items()
                     if str(play.get('account_id'))==account} if account is not None else set()
        awarded={e.get('event_key'):e for e in p.get('xp_events',[]) if e.get('media_type') in WATCH_TYPES}
        for occurrence,row in sorted((p.get('occurrence_ledger') or {}).items()):
            if row.get('needs_review'):continue
            event=next((awarded[k] for k in row.get('award_keys',[]) if k in awarded),None)
            if not event:continue
            observations=[(key,obs) for key,obs in sorted(row.get('observations',{}).items())
                          if obs.get('provider')==provider and obs.get('identity') and
                          (account is None or key.startswith(f'{provider}:{account}/') or key in native_refs)]
            if not observations:continue
            identity=observations[0][1]['identity'];stamp=parse_stamp(identity.get('watched_at'))
            if not stamp or not start<=stamp<end:continue
            ids={**(event.get('ids') or {}),**(identity.get('ids') or {})}
            movie='movie' in event['media_type'];key=metadata_key(movie,ids)
            result.append({'uid':uid,'occurrence':occurrence,'movie':movie,'ids':ids,'meta_key':key,
                           'title':identity.get('title') or event.get('title') or 'Untitled',
                           'at':stamp.isoformat(),'minutes':event_watch_minutes(event),
                           'season':identity.get('season'),'episode':identity.get('episode')})
    return result


def _matching(options,initial=None):
    """Maximum slot matching, stable ordering, then balance free equivalent tokens."""
    assigned={};owners={}
    def visit(slot,seen):
        for token in sorted(options[slot]):
            if token in seen:continue
            seen.add(token)
            if token not in owners or visit(owners[token],seen):
                assigned[slot]=token;owners[token]=slot;return True
        return False
    for slot in sorted(options,key=lambda k:(len(options[k]),k)):visit(slot,set())
    # Avoid giving one member a third square while another can fill their second.
    for _ in range(len(assigned)*2):
        counts=defaultdict(int,initial or {})
        for token in assigned.values():counts[token[0]]+=1
        swapped=False
        for slot,token in sorted(assigned.items()):
            alternatives=[other for other in options[slot] if other not in owners
                          and counts[other[0]]<2 and counts[token[0]]>2]
            if alternatives:
                other=min(alternatives,key=lambda t:(counts[t[0]],t));del owners[token]
                assigned[slot]=other;owners[other]=slot;swapped=True;break
        if not swapped:break
    return assigned


def _eligible_titles(watches,metadata):
    grouped={};pending=set()
    for watch in watches:
        info=metadata.get(watch['meta_key']) or {}
        canonical=info.get('canonical')
        if not canonical:
            pending.add(watch['meta_key'] or f"unresolved:{watch['uid']}:{watch['title']}");continue
        token=(watch['uid'],canonical)
        row=grouped.setdefault(token,{'uid':watch['uid'],'key':canonical,'title':info.get('title') or watch['title'],
                                     'movie':watch['movie'],'genres':info.get('genres') or [],'year':info.get('year'),
                                     'episodes':set(),'seasons':info.get('seasons') or {},'first':False,'completion_verified':False})
        row['completion_verified']|=bool(watch.get('completion_verified'))
        if not watch['movie'] and isinstance(watch['season'],int) and watch['season']>0 and isinstance(watch['episode'],int) and watch['episode']>0:
            row['episodes'].add((watch['season'],watch['episode']))
            row['first']|=(watch['season'],watch['episode'])==(1,1)
    eligible={}
    for token,row in grouped.items():
        short_season=any(0<int(count)<3 and set(range(1,int(count)+1))<={ep for sn,ep in row['episodes'] if sn==int(season)}
                         for season,count in row['seasons'].items() if str(season).isdigit() and int(season)>0 and str(count).isdigit())
        if row['movie'] or len(row['episodes'])>=3 or short_season or row['completion_verified']:eligible[token]=row
    return eligible,len(pending)


def _bingo_matches(cell,row):
    genres=set(row['genres']);year=row.get('year')
    return {'comedy_film':row['movie'] and 'Comedy' in genres,
            'classic':isinstance(year,int) and year<2000,
            'first':not row['movie'] and row['first'],
            'animation_film':row['movie'] and 'Animation' in genres,
            'mystery':'Mystery' in genres,'documentary':row['movie'] and 'Documentary' in genres,
            'fantasy':bool(genres & {'Sci-Fi & Fantasy','Science Fiction','Fantasy'}),
            'drama_series':not row['movie'] and 'Drama' in genres}.get(cell,False)


def score_event(record,watches,metadata,*,now=None):
    kind=record['kind'];eligible,pending=_eligible_titles(watches,metadata)
    if kind!='watchathon':
        pending+=len({row['key'] for row in eligible.values() if (kind in {'genres','bingo'} and not row['genres']) or (kind in {'decades','bingo'} and row['year'] is None)})
    titles_by_user=defaultdict(set);supporters=defaultdict(set);objectives=[];weights=defaultdict(float)
    for token,row in eligible.items():titles_by_user[token[0]].add(token[1])
    if kind=='watchathon':
        contributions=defaultdict(float)
        for watch in watches:contributions[watch['uid']]+=watch['minutes']
        contributions={u:round(v,6) for u,v in contributions.items() if v>0}
        qualified={u for u,v in contributions.items() if v>=record['qualifying_minutes']}
        weights.update(contributions);total=sum(contributions.values());target_met=total>=record['target']
        pending=0;unit='minutes'
    elif kind=='genres':
        genres=sorted({g for row in eligible.values() for g in row['genres']})
        # Titles, rather than members, occupy genre slots. Shared titles remain one discovery.
        options={g:{('',row['key']) for row in eligible.values() if g in row['genres']} for g in genres}
        assigned=_matching(options)
        title_genre={token[1]:genre for genre,token in assigned.items()}
        for token,row in eligible.items():
            genre=title_genre.get(row['key']) or next((g for g in sorted(row['genres']) if g in assigned),None)
            if genre:supporters[genre].add(token[0])
        objectives=[{'label':g,'done':g in assigned,'titles':sorted({r['title'] for r in eligible.values() if r['key']==assigned[g][1]}) if g in assigned else []}
                    for g in genres]
        contributions={u:len(keys) for u,keys in titles_by_user.items() if any(row['genres'] for token,row in eligible.items() if token[0]==u)}
        qualified={u for u in contributions if len({r['key'] for token,r in eligible.items() if token[0]==u and r['genres']})>=2}
        total=len(assigned);target_met=total>=record['target'] and len({r['key'] for r in eligible.values()})>=record['minimum_titles'];unit='titles'
    elif kind=='decades':
        valid={token:row for token,row in eligible.items() if isinstance(row.get('year'),int) and row['year']//10*10 in record['decades']}
        contributions={u:len({r['key'] for token,r in valid.items() if token[0]==u}) for u in titles_by_user}
        contributions={u:n for u,n in contributions.items() if n};qualified={u for u,n in contributions.items() if n>=2}
        for decade in record['decades']:
            matches=[(token,row) for token,row in valid.items() if row['year']//10*10==decade]
            count=len({r['key'] for _,r in matches});done=count>=record['per_decade']
            objectives.append({'label':f'{decade}s','done':done,'count':count,'target':record['per_decade'],
                               'titles':sorted({r['title'] for _,r in matches})[:4]})
            if done:supporters[str(decade)]={token[0] for token,_ in matches}
        total=sum(o['done'] for o in objectives);target_met=total>=record['target'];unit='titles'
    else:
        movies=defaultdict(set)
        for token,row in eligible.items():
            if row['movie']:movies[token[1]].add(token)
        shared=[tokens for _,tokens in sorted(movies.items()) if len(tokens)>=record['shared_members']]
        attempts=[]
        for reserved in [set()]+[set(tokens) for tokens in shared]:
            options={key:{token for token,row in eligible.items() if token not in reserved and _bingo_matches(key,row)} for key,_ in BINGO if key!='shared'}
            assigned=_matching(options,{token[0]:1 for token in reserved})
            personal=defaultdict(int)
            for token in list(assigned.values())+list(reserved):personal[token[0]]+=1
            qualifiers=sum(n>=2 for n in personal.values())
            attempts.append(((len(assigned)+bool(reserved),qualifiers),assigned,reserved))
            if len(assigned)==8 and reserved and qualifiers>=record['required_contributors']:break
        _,assigned,reserved=max(attempts,key=lambda item:item[0])
        contributions=defaultdict(int)
        for cell,token in assigned.items():supporters[cell].add(token[0]);contributions[token[0]]+=1
        if reserved:
            supporters['shared']={token[0] for token in reserved}
            for token in reserved:contributions[token[0]]+=1
        for cell,label in BINGO:
            tokens=reserved if cell=='shared' else {assigned[cell]} if cell in assigned else set()
            objectives.append({'label':label+(f" · {record['shared_members']} members" if cell=='shared' else ''),
                               'done':bool(tokens),'titles':sorted({eligible[t]['title'] for t in tokens})[:3]})
        contributions=dict(contributions);qualified={u for u,n in contributions.items() if n>=2}
        total=sum(o['done'] for o in objectives);target_met=total==9;unit='squares'
    if kind!='watchathon':
        for members in supporters.values():
            participants=members & qualified
            for uid in participants:weights[uid]+=1/max(1,len(participants))
    ready=target_met and len(qualified)>=record['required_contributors']
    pool=int(record['pool']);equal_pool=pool*(30 if kind=='watchathon' else 50)//100
    awards={}
    if ready:
        equal=split_pool({u:1 for u in qualified},equal_pool)
        weighted=split_pool({u:max(1,round(weights.get(u,0)*1000000)) for u in qualified},pool-equal_pool)
        awards={u:equal.get(u,0)+weighted.get(u,0) for u in sorted(qualified)}
    return {'total':round(total,6),'contributions':contributions,'qualified':sorted(qualified),
            'qualified_contributors':len(qualified),'goal_reached':ready,'target_met':target_met,
            'estimated_awards':awards,'objectives':objectives,'pending_metadata':pending,'unit':unit,
            'eligible_titles':len({row['key'] for row in eligible.values()}),'weights':dict(weights),
            'support':[{'uid':w['uid'],'occurrence':w.get('occurrence'),'at':w.get('at'),'meta_key':w.get('meta_key')} for w in watches]}


def community_art_entries(watches,metadata):
    """Recent distinct team titles, using the same exact-ID cache as scoring."""
    entries={}
    for watch in sorted(watches,key=lambda w:str(w.get('at') or ''),reverse=True):
        entry=metadata.get(watch.get('meta_key')) or {};key=entry.get('canonical')
        if key:entries.setdefault(key,entry)
    return list(entries.values())[:5]


def event_state(record,watches,metadata,now):
    end=parse_stamp(record['end']);score=score_event(record,watches,metadata)
    settled=now>=end+timedelta(hours=record.get('settlement_hours',6))
    status=('completed' if score['goal_reached'] else 'missed') if settled else 'settling' if now>=end else 'goal_reached' if score['goal_reached'] else 'active'
    return {**record,**score,'status':status,'awards':dict(record.get('awards') or {}),
            'remaining':max(0,record['target']-score['total']),
            'art_entries':community_art_entries(watches,metadata),
            'next_event':NAMES[ROTATION[(ROTATION.index(record['kind'])+1)%4]],'settled':settled}
