"""Bounded title artwork for leaderboard podiums and recap galleries."""
import asyncio
from datetime import datetime,timedelta,timezone
from .profile_artwork import download_card_image
from trackerbot.metadata.community_metadata import enrich_community_metadata

_cache={}

async def load_server_artwork(tmdb,entries):
    unique={e['key']:e for e in entries if e and e.get('ids')};now=datetime.now(timezone.utc)
    candidates=[]
    for key,e in list(unique.items())[:6]:
        saved=_cache.get(key)
        if not saved or saved[0]<now:
            candidates.append(dict(e,meta_key=key))
    metadata=await enrich_community_metadata(tmdb,candidates) if candidates else {}
    for key,info in metadata.items():
        if info.get('canonical'):_cache[key]=(now+timedelta(hours=6),info)
        else:_cache[key]=(now+timedelta(minutes=5),{})
    while len(_cache)>256:del _cache[next(iter(_cache))]
    result={};gate=asyncio.Semaphore(4)
    import aiohttp
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session:
        async def load(key):
            async with gate:
                info=(_cache.get(key) or (None,{}))[1];art={}
                try:
                    for name,path,width in [('poster',info.get('poster_path'),'w185'),('backdrop',info.get('backdrop_path'),'w780')]:
                        image=await download_card_image(session,path,width)
                        if image:art[name]=image
                except Exception:pass
                result[key]=art
        tasks=[asyncio.create_task(load(key)) for key in list(unique)[:6]]
        try:
            async with asyncio.timeout(4):await asyncio.gather(*tasks)
        except TimeoutError:pass
        finally:
            for task in tasks:
                if not task.done():task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
    return result
