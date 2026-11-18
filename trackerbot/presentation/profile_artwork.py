"""Optional TMDB artwork for cards; failure never blocks a statistics response."""
import asyncio
from io import BytesIO
from PIL import Image
import aiohttp
from trackerbot.metadata.tmdb_client import API_BASE

async def load_profile_artwork(tmdb,data):
    entries=data.get('featured_titles') or []
    gate=asyncio.Semaphore(4)
    result={'posters':{}}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6)) as session:
        async def download(path,width):
            if not isinstance(path,str) or not path.startswith('/') or not path.lower().endswith(('.jpg','.png','.webp')):return None
            async with session.get(f'https://image.tmdb.org/t/p/{width}{path}',allow_redirects=False) as response:
                if response.status!=200:return None
                blob=await response.content.read(4_000_001)
                if len(blob)>4_000_000:return None
            def decode():
                with Image.open(BytesIO(blob)) as image:
                    if image.width*image.height>12_000_000:return None
                    image.thumbnail((1200,1200));return image.convert('RGBA').copy()
            return await asyncio.to_thread(decode)
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
        tasks=[asyncio.create_task(load(index,entry)) for index,entry in enumerate(entries[:7])]
        try:
            async with asyncio.timeout(10):await asyncio.gather(*tasks)
        except TimeoutError:pass
        finally:
            for task in tasks:
                if not task.done():task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
    return result
