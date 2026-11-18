"""Optional TMDB artwork for cards; failure never blocks a statistics response."""
import asyncio
from io import BytesIO
from PIL import Image
import aiohttp
from trackerbot.metadata.tmdb_client import API_BASE

async def download_card_image(session,path,width):
    if not isinstance(path,str) or not path.startswith('/') or not path.lower().endswith(('.jpg','.png','.webp')):
        return None
    async with session.get(f'https://image.tmdb.org/t/p/{width}{path}',allow_redirects=False) as response:
        if response.status!=200:return None
        # StreamReader.read(n) can return the first network packet rather
        # than the entire file. Decode only after EOF, with a hard byte cap.
        blob=bytearray()
        async for chunk in response.content.iter_chunked(65536):
            blob.extend(chunk)
            if len(blob)>4_000_000:return None
    def decode():
        with Image.open(BytesIO(blob)) as image:
            if image.width*image.height>12_000_000:return None
            image.thumbnail((1200,1200));return image.convert('RGBA').copy()
    return await asyncio.to_thread(decode)

async def load_profile_artwork(tmdb,data):
    featured=data.get('featured_titles') or []
    entries=featured[:1]+[entry for entry in data.get('recent_titles',featured) if not featured or entry['key']!=featured[0]['key']]
    gate=asyncio.Semaphore(4)
    result={'posters':{}}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6)) as session:
        async def download(path,width):
            return await download_card_image(session,path,width)
        async def load(index,entry):
            async with gate:
                try:
                    ids=entry.get('ids') or {};sid=ids.get('tmdb')
                    if not entry['movie'] and not sid and ids.get('tvdb'):
                        sid=await tmdb.find_series_by_tvdb(ids['tvdb'])
                    if not sid:return
                    info=await (tmdb._get_json(f'{API_BASE}/movie/{int(sid)}',{'language':'en-US'}) if entry['movie']
                                else tmdb._get_series_details(sid)) or {}
                    poster=await download(info.get('poster_path'),'w342')
                    if poster:result['posters'][entry['key']]=poster
                    if index==0:
                        hero=await download(info.get('backdrop_path') or info.get('poster_path'),'w780')
                        if hero:result['hero']=hero
                        logo_url=await (tmdb.get_movie_logo(sid) if entry['movie'] else tmdb.get_tv_logo(sid))
                        logo=await download('/'+logo_url.rsplit('/',1)[-1],'w500') if logo_url else None
                        if logo:result['logo']=logo
                except Exception:
                    # Missing catalogue entries, network limits and invalid
                    # images all fall back to the renderer's own title panel.
                    return
        tasks=[asyncio.create_task(load(index,entry)) for index,entry in enumerate(entries[:8])]
        try:
            async with asyncio.timeout(10):await asyncio.gather(*tasks)
        except TimeoutError:pass
        finally:
            for task in tasks:
                if not task.done():task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
    return result


async def enrich_profile_genres(tmdb,data):
    """Incrementally hydrate exact title IDs, with explicit partial coverage."""
    from collections import Counter
    import time
    cache=getattr(tmdb,'_profile_genre_cache',None)
    if cache is None:
        cache={};tmdb._profile_genre_cache=cache
    missing=data.get('genre_missing') or {}
    entries=[entry for entry in data.get('history_titles',[]) if missing.get(entry['key'])]
    def key(entry):
        return (entry['movie'],tuple(sorted((k,str(v)) for k,v in entry['ids'].items() if k in {'tmdb','tvdb','imdb'})))
    def cached(entry):
        value=cache.get(key(entry))
        return value if value and value[0]>time.monotonic() else None
    gate=asyncio.Semaphore(4)
    async def fetch(entry):
        async with gate:
            genres=[]
            try:
                ids=entry['ids'];sid=ids.get('tmdb')
                if not sid and not entry['movie'] and ids.get('tvdb'):sid=await tmdb.find_series_by_tvdb(ids['tvdb'])
                if not sid and ids.get('imdb'):
                    result=await tmdb._get_json(f"{API_BASE}/find/{ids['imdb']}",{'external_source':'imdb_id'}) or {}
                    matches=result.get('movie_results' if entry['movie'] else 'tv_results') or []
                    if len(matches)==1:sid=matches[0].get('id')
                if sid:
                    info=await (tmdb._get_json(f'{API_BASE}/movie/{int(sid)}',{'language':'en-US'}) if entry['movie'] else tmdb._get_series_details(sid)) or {}
                    genres=[row['name'] for row in info.get('genres',[]) if isinstance(row,dict) and row.get('name')]
            except Exception:pass
            cache[key(entry)]=(time.monotonic()+(86400 if genres else 300),genres)
    # Every invocation progresses to the next uncached group; no full-library
    # metadata crawl or tracker API call is needed for a profile response.
    pending=[entry for entry in entries if key(entry)[1] and not cached(entry)][:24]
    tasks=[asyncio.create_task(fetch(entry)) for entry in pending]
    try:
        async with asyncio.timeout(6):await asyncio.gather(*tasks)
    except TimeoutError:pass
    finally:
        for task in tasks:
            if not task.done():task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
    counts=Counter(data.get('genre_counts') or {})
    covered=max(0,data.get('total',0)-sum(missing.values()))
    for entry in entries:
        value=cached(entry)
        if value and value[1]:
            count=missing[entry['key']];covered+=count
            for genre in value[1]:counts[genre]+=count
    data['top_genres']=sorted(counts.items(),key=lambda item:(-item[1],item[0]))[:3]
    data['genre_covered_watches']=covered
    while len(cache)>1024:cache.pop(next(iter(cache)))
