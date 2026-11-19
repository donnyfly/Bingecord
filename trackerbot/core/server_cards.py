"""Read-only, selected-source facts for rankings and weekly editorial cards."""
from collections import defaultdict
from datetime import date,timedelta
from zoneinfo import ZoneInfo
from .community_events import metadata_key,parse_stamp
from .streaks import analyze_streaks


def with_artwork_ids(stats,progression):
    """Join old per-server rows to hydrated XP IDs by exact item key."""
    ids_by_item={}
    for event in progression.get('xp_events',[]):
        kind=event.get('media_type');stamp=event.get('at');key=str(event.get('event_key') or '')
        if kind not in {'episode','anime_episode','movie','anime_movie'} or not stamp:continue
        if key.startswith(kind+':') and key.endswith(':'+stamp):
            item=key[len(kind)+1:-(len(stamp)+1)];ids_by_item.setdefault(item,{}).update(event.get('ids') or {})
    events={k:{**r,'ids':{**ids_by_item.get(r.get('item_key'),{}),**(r.get('ids') or {})}}
            for k,r in (stats.get('watch_events') or {}).items()}
    for row in events.values():ids_by_item.setdefault(row.get('item_key'),{}).update(row.get('ids') or {})
    titles={k:{**r,'ids':{**ids_by_item.get(k,{}),**(r.get('ids') or {})}} for k,r in (stats.get('titles') or {}).items() if isinstance(r,dict)}
    return {**stats,'watch_events':events,'titles':titles}


def title_entries(stats,*,start=None,end=None,zone='UTC'):
    grouped={}
    records=(stats.get('watch_events') or {}).values() if start else (stats.get('titles') or {}).values()
    if not start:
        ids_by_item={r.get('item_key'):r.get('ids') or {} for r in (stats.get('watch_events') or {}).values()}
        records=[dict(r,item_key=k,media_type=r.get('type'),ids={**ids_by_item.get(k,{}),**(r.get('ids') or {})}) for k,r in (stats.get('titles') or {}).items() if isinstance(r,dict)]
    records=list(records);parent_ids={}
    for row in records:
        bits=str(row.get('item_key') or '').split(':')
        if bits[0]=='series' and len(bits)>=5:parent_ids.setdefault(':'.join(bits[:3]),{}).update(row.get('ids') or {})
    for row in records:
        kind=row.get('media_type') or '';movie='movie' in kind
        if kind not in {'movie','anime_movie','episode','anime_episode'}:continue
        if start:
            stamp=parse_stamp(row.get('watched_at'))
            if not stamp or not start<=stamp.astimezone(ZoneInfo(zone)).date()<=end:continue
        item=str(row.get('item_key') or '')
        bits=item.split(':');fallback=':'.join(bits[:3]) if bits[0]=='series' and len(bits)>=5 else item
        ids={**parent_ids.get(fallback,{}),**(row.get('ids') or {})}
        key=metadata_key(movie,ids) or fallback
        if not key:continue
        entry=grouped.setdefault(key,{'key':key,'meta_key':metadata_key(movie,ids),'ids':ids,'movie':movie,
                                     'title':row.get('title') or 'Untitled','count':0})
        entry['count']+=1 if start else max(0,int(row.get('count',0)))
    return sorted(grouped.values(),key=lambda r:(-r['count'],r['key']))


def board_facts(stats,zone,today,progression=None):
    if progression is not None:stats=with_artwork_ids(stats,progression)
    all_entries=title_entries(stats)
    all_values={'episodes':int(stats.get('episodes_watched',0)),'movies':int(stats.get('movies_watched',0)),
                'anime':int(stats.get('anime_episodes_watched',0))+int(stats.get('anime_movies_watched',0)),'titles':len(all_entries)}
    all_values['total']=all_values['episodes']+all_values['movies'];all_values['featured']=all_entries[:1]
    periods={'all':all_values}
    for period,start in [('week',today-timedelta(days=today.weekday())),('month',today-timedelta(days=29))]:
        counts=defaultdict(int)
        for day,row in (stats.get('watch_dates') or {}).items():
            try:valid=start<=date.fromisoformat(day)<=today
            except (ValueError,TypeError):continue
            if not valid:continue
            for key in ('total','episodes','movies','anime_episodes','anime_movies'):counts[key]+=max(0,int(row.get(key,0)))
        entries=title_entries(stats,start=start,end=today,zone=zone)
        periods[period]={'total':counts['total'],'episodes':counts['episodes']+counts['anime_episodes'],
                         'movies':counts['movies']+counts['anime_movies'],'anime':counts['anime_episodes']+counts['anime_movies'],
                         'titles':len(entries),'featured':entries[:1]}
    return {'periods':periods,'streak':analyze_streaks(stats.get('watch_dates'),zone,today=today)['current']}


def recap_facts(rows,start,end,zone):
    days={};titles={};members=[]
    cursor=start
    while cursor<=end:days[cursor.isoformat()]=0;cursor+=timedelta(days=1)
    for row in rows:
        stats=row['statistics'];total=0
        for day,counts in (stats.get('watch_dates') or {}).items():
            if day in days:
                count=max(0,int(counts.get('total',0)));days[day]+=count;total+=count
        if total:members.append({'discord_user_id':row['discord_user_id'],'name':row.get('simkl_username') if row.get('simkl_username') and not str(row['simkl_username']).isdigit() else 'Member '+str(row['discord_user_id'])[-4:],
                                 'total':total})
        for title in title_entries(stats,start=start,end=end,zone=zone):
            entry=titles.setdefault(title['key'],{**title,'count':0,'members':set()})
            entry['count']+=title['count'];entry['members'].add(row['discord_user_id'])
    members.sort(key=lambda r:(-r['total'],r['discord_user_id']))
    entries=sorted(titles.values(),key=lambda r:(-len(r['members']),-r['count'],r['key']))[:6]
    return {'members':members[:5],'days':list(days.items()),'titles':[dict(e,members=len(e['members'])) for e in entries]}
