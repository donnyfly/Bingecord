"""Versioned personal objectives over shared, credited watch occurrences."""
import copy
from collections import defaultdict
from datetime import date,datetime,timedelta,timezone
from .community_events import metadata_key,parse_stamp
from . import reading

DAILY_START=date(2026,11,20)
WEEKLY_START=date(2026,11,23)
WATCH_TYPES={'movie','anime_movie','episode','anime_episode'}


def definitions(scope,day):
    """One time objective, one discovery objective, one optional larger goal."""
    n=day.toordinal() if scope=='daily' else day.toordinal()//7
    def item(key,name,kind,target,xp,**extra):
        return dict(id=f'{scope}_v2_{key}',name=name,kind=kind,target=target,xp=xp,version=2,**extra)
    if scope=='daily':
        genre=('Mystery','Comedy','Drama','Science Fiction','Animation','Action','Romance')[n%7]
        discovery=(item('genre_'+genre.lower().replace(' ','_'),f'{genre} Pick','genre_watch',1,450,genre=genre),
                   item('classic','Back in Time','classic_film',1,450))
        bigger=(item('double','Double Feature','distinct_films',2,700),
                item('new_series','Something New','new_series',1,350))
        return [item('minutes_30','Settle In','minutes',30,250),discovery[n%2],bigger[n%2]]
    discovery=(item('genres','Genre Explorer','genres',4,2000),
               item('decades','Across the Decades','decades',3,2000))
    larger=(item('festival','Film Festival','distinct_films',4,2500),
            item('credits','Roll Credits','film_and_season',2,2500))
    return [item('minutes_600','Weekly Watchtime','minutes',600,2000),discovery[n%2],larger[(n//2)%2]]


def period_definitions(progression,scope,day,*,save=False):
    from .progression import challenges_for
    key=f'{scope}:{day.isoformat()}'
    saved=(progression.get('challenge_sets') or {}).get(key)
    if saved is not None:return copy.deepcopy(saved)
    result=challenges_for(day)[0 if scope=='daily' else 1]
    if save and any(ch.get('version')==2 for ch in result):progression.setdefault('challenge_sets',{})[key]=copy.deepcopy(result)
    return result


def watches(progression):
    """Merge provider references into the already awarded occurrence, never a title guess."""
    identities={};blocked=set();occurrences={}
    for occurrence_id,occurrence in (progression.get('occurrence_ledger') or {}).items():
        keys=occurrence.get('award_keys',[])
        if occurrence.get('needs_review'):
            blocked.update(keys);continue
        choices=[o.get('identity') or {} for o in occurrence.get('observations',{}).values()]
        identity=dict(max(choices,key=lambda i:len(i.get('ids') or {}),default={}))
        merged={}
        for choice in choices:merged.update(choice.get('ids') or {})
        identity['ids']=merged
        for key in keys:identities[key]=identity;occurrences[key]=occurrence_id
    seen=set();result=[];metadata=progression.get('challenge_metadata') or {}
    for event in progression.get('xp_events',[]):
        key=event.get('event_key');kind=event.get('media_type')
        if kind not in WATCH_TYPES or occurrences.get(key,key) in seen or key in blocked:continue
        seen.add(occurrences.get(key,key));identity=identities.get(key,{})
        ids={**(event.get('ids') or {}),**(identity.get('ids') or {})}
        movie='movie' in kind;meta_key=metadata_key(movie,ids)
        meta=next((metadata.get(metadata_key(movie,{name:ids[name]})) for name in ('tmdb','tvdb','imdb')
                   if ids.get(name) and (metadata.get(metadata_key(movie,{name:ids[name]})) or {}).get('canonical')), {})
        canonical=meta.get('canonical') or meta_key
        stamp=parse_stamp(event.get('at') or identity.get('watched_at'))
        if not stamp:continue
        result.append(dict(event,at=stamp,canonical=canonical,meta=meta,movie=movie,
                           season=identity.get('season'),episode=identity.get('episode'),
                           title=meta.get('title') or event.get('title') or identity.get('title') or 'Untitled'))
    return result


def progress(progression,challenge,start,end,*,watch_rows=None):
    from .progression import event_watch_minutes
    start=parse_stamp(start);end=parse_stamp(end)
    all_rows=watches(progression) if watch_rows is None else watch_rows;rows=[r for r in all_rows if start<=r['at']<end]
    kind=challenge['kind'];target=challenge['target'];pending=0;used=[]
    groups=defaultdict(list)
    for row in rows:
        if row['canonical']:groups[row['canonical']].append(row)
        elif kind!='minutes':pending+=1
    finishes={f['canonical'] for f in progression.get('challenge_finishes',[])
              if parse_stamp(f.get('at')) and start<=parse_stamp(f['at'])<end}
    def eligible(items,episodes=3):
        if any(r['movie'] for r in items):return True
        coords={(r['season'],r['episode']) for r in items if r['season'] is not None and r['season']>0 and r['episode'] is not None}
        return len(coords)>=episodes or items[0]['canonical'] in finishes
    if kind=='minutes':
        value=sum(event_watch_minutes(r) for r in rows);used=rows
    elif kind in {'distinct_films','classic_film'}:
        used=[items[0] for items in groups.values() if items[0]['movie'] and
              (kind!='classic_film' or isinstance(items[0]['meta'].get('year'),int) and items[0]['meta']['year']<2000)]
        if kind=='classic_film':pending+=sum(not items[0]['meta'].get('year') for items in groups.values() if items[0]['movie'])
        value=len(used)
    elif kind=='new_series':
        for key,items in groups.items():
            if items[0]['movie']:continue
            first=next((r for r in items if r['season']==1 and r['episode']==1),None)
            if first and not any(r['canonical']==key and r['at']<start for r in all_rows):used.append(first)
            elif any(r['episode'] is None or r['season'] is None for r in items):pending+=1
        value=len(used)
    elif kind=='genre_watch':
        for items in groups.values():
            if not items[0]['meta'].get('genres'):pending+=1;continue
            if challenge['genre'] in items[0]['meta']['genres'] and eligible(items,2):used.append(items[0])
        value=len(used)
    elif kind in {'genres','decades'}:
        values=set()
        for items in groups.values():
            if not eligible(items):
                if not items[0]['movie'] and any(r['season'] is None or r['episode'] is None for r in items):pending+=1
                continue
            meta=items[0]['meta'];facts=meta.get('genres') if kind=='genres' else [meta['year']//10*10] if isinstance(meta.get('year'),int) else []
            if not facts:pending+=1;continue
            values.update(facts);used.append(items[0])
        value=min(len(values),len(used))
    elif kind=='film_and_season':
        film=next((items[0] for items in groups.values() if items[0]['movie']),None)
        series=next((items[0] for key,items in groups.items() if not items[0]['movie'] and key in finishes),None)
        used=[r for r in (film,series) if r];value=len(used)
    else:value=0
    distinct={}
    for row in sorted(used,key=lambda r:r['at'],reverse=True):
        if row['canonical']:distinct.setdefault(row['canonical'],row)
    return {'value':min(target,value),'pending':pending,'titles':[{'canonical':k,'title':r['title'],
            'poster_path':r['meta'].get('poster_path')} for k,r in list(distinct.items())[:6]]}


def reading_periods(now):
    day = now.astimezone(timezone.utc).date()
    monday = day - timedelta(days=day.weekday())
    for scope, at, days in (("daily", day, 1), ("weekly", monday, 7)):
        start = datetime.combine(at, datetime.min.time(), tzinfo=timezone.utc)
        yield scope, start, start + timedelta(days=days)


def snapshot(progression,now,*,preview=False):
    day=now.astimezone(timezone.utc).date();monday=day-timedelta(days=day.weekday());sections=[]
    earned=0;watch_rows=watches(progression)
    for key,rewards in progression.get('challenge_completions',{}).items():
        try:at=date.fromisoformat(key.split(':',1)[1])
        except (ValueError,IndexError):continue
        if monday<=at<=day:earned+=sum(r.get('xp',0) for r in rewards.values())
    for scope,at,days in [('daily',day,1),('weekly',monday,7)]:
        start=datetime.combine(at,datetime.min.time(),tzinfo=timezone.utc);end=start+timedelta(days=days)
        choices=definitions(scope,at) if preview else period_definitions(progression,scope,at)
        rows=[]
        for ch in choices:
            if ch.get('version')==2:state=progress(progression,ch,start,end,watch_rows=watch_rows)
            else:
                from .progression import challenge_progress
                state={'value':challenge_progress(progression.get('xp_events',[]),ch,start.isoformat(),(end-timedelta(seconds=1)).isoformat()),'pending':0,'titles':[]}
                artwork={}
                for row in reversed(watch_rows):
                    if not start<=row['at']<end or not row['canonical']:continue
                    if ch['kind']=='movies' and not row['movie'] or ch['kind']=='episodes' and row['movie']:continue
                    artwork.setdefault(row['canonical'],{'canonical':row['canonical'],'title':row['title'],'poster_path':row['meta'].get('poster_path')})
                state['titles']=list(artwork.values())[:6]
            done=ch['id'] in progression.get('challenge_completions',{}).get(f'{scope}:{at.isoformat()}',{}) if not preview else state['value']>=ch['target']
            rows.append({**ch,**state,'done':done})
        sections.append({'scope':scope,'end':end.isoformat(),'rows':rows})
    if "reading_events" in progression:
        for scope, start, end in reading_periods(now):
            events = list(reading.in_period(progression["reading_events"], start, end))
            completed = progression.get("challenge_completions", {}).get(f"{scope}:{start.date().isoformat()}", {})
            sections.append({
                "scope": scope, "reading": True, "end": end.isoformat(),
                "rows": [
                    {**ch, "value": reading.challenge_value(events, ch["kind"]), "done": ch["id"] in completed}
                    for ch in reading.challenges(scope)
                ],
            })
    return {'sections':sections,'earned':earned,'preview':preview}


def requirement(challenge):
    kind=challenge['kind'];target=challenge['target']
    return {'minutes':f'Watch {target} minutes','genre_watch':f"A {challenge.get('genre','genre')} film or 2 regular episodes",
            'classic_film':'Watch a film released before 2000','distinct_films':f'Watch {target} different films',
            'new_series':'Start S1E1 of a series new to your credited history',
            'genres':f'{target} genres across {target} qualifying titles','decades':f'Titles from {target} different decades',
             "reading_chapters": f"Read {target} manga chapters",
            "reading_variety": f"Read at least 3 chapters each from {target} manga",
            'film_and_season':'Watch a film and finish a verified season' }.get(kind,challenge['name'])
