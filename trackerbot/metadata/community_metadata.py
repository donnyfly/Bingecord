"""Bounded exact-ID enrichment for community discovery events."""
import asyncio
from datetime import datetime, timezone
from .tmdb_client import API_BASE


def canonical_genres(rows):
    aliases={'Action & Adventure':['Action','Adventure'],'Sci-Fi & Fantasy':['Science Fiction','Fantasy'],
             'Kids':['Family'],'War & Politics':['War','Politics']}
    return sorted({name for row in rows if isinstance(row,dict) and row.get('name')
                   for name in aliases.get(row['name'],[row['name']])})


async def enrich_community_metadata(tmdb,candidates):
    gate=asyncio.Semaphore(4);result={}
    async def load(entry):
        key=entry['meta_key']
        result[key]={'checked_at':datetime.now(timezone.utc).isoformat()}
        async with gate:
            try:
                ids=entry['ids'];movie=entry['movie'];sid=ids.get('tmdb')
                if not sid and not movie and ids.get('tvdb'):sid=await tmdb.find_series_by_tvdb(ids['tvdb'])
                if not sid and ids.get('imdb'):
                    data=await tmdb._get_json(f"{API_BASE}/find/{ids['imdb']}",{'external_source':'imdb_id'}) or {}
                    matches=data.get('movie_results' if movie else 'tv_results') or []
                    if len(matches)==1:sid=matches[0].get('id')
                if not sid:return
                info=await (tmdb._get_json(f'{API_BASE}/movie/{int(sid)}',{'language':'en-US'}) if movie else tmdb._get_series_details(sid)) or {}
                if not info.get('id'):return
                date=str(info.get('release_date' if movie else 'first_air_date') or '')
                result[key]={'canonical':f"{'movie' if movie else 'tv'}:tmdb:{int(sid)}",
                             'title':info.get('title' if movie else 'name') or entry['title'],
                             'genres':canonical_genres(info.get('genres',[])),
                             'year':int(date[:4]) if date[:4].isdigit() else None,
                             'seasons':{str(s['season_number']):s['episode_count'] for s in info.get('seasons',[])
                                        if s.get('season_number',0)>0 and s.get('episode_count') and info.get('status') in {'Ended','Canceled'}},
                             'poster_path':info.get('poster_path'),'backdrop_path':info.get('backdrop_path'),
                             'checked_at':datetime.now(timezone.utc).isoformat()}
            except Exception:return
    tasks=[asyncio.create_task(load(entry)) for entry in candidates[:24]]
    try:
        async with asyncio.timeout(8):await asyncio.gather(*tasks)
    except TimeoutError:pass
    finally:
        for task in tasks:
            if not task.done():task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
    return result
