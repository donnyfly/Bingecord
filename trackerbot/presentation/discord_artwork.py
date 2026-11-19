"""Small, optional Discord avatars and icons for rendered cards."""
import asyncio
from io import BytesIO
from PIL import Image


async def load_discord_artwork(assets,*,limit=5):
    """Read bounded avatars and an optional server icon within three seconds."""
    result={}
    gate=asyncio.Semaphore(3)
    async def load(key,asset):
        async with gate:
            try:
                blob=await asset.with_size(128).with_format('png').read()
                if len(blob)>1_000_000:return
                def decode():
                    with Image.open(BytesIO(blob)) as image:
                        if image.width*image.height>1_000_000:return None
                        image.thumbnail((128,128));return image.convert('RGBA').copy()
                image=await asyncio.to_thread(decode)
                if image:result[key]=image
            except Exception:
                pass
    tasks=[asyncio.create_task(load(key,asset)) for key,asset in list(assets.items())[:max(0,min(6,limit))] if asset]
    try:
        async with asyncio.timeout(3):await asyncio.gather(*tasks)
    except TimeoutError:pass
    finally:
        for task in tasks:
            if not task.done():task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
    return result


async def resolve_card_members(guild,user_ids,client=None):
    """Resolve only displayed contributors; REST fills an unpopulated member cache."""
    import time
    ids=list(dict.fromkeys(map(str,user_ids)))[:5];result={};gate=asyncio.Semaphore(3)
    cache=getattr(client,'_card_member_cache',None) if client is not None else None
    if client is not None and cache is None:
        cache={};client._card_member_cache=cache
    async def resolve(uid):
        async with gate:
            key=(str(getattr(guild,'id','')),uid)
            member=guild.get_member(int(uid)) if guild and hasattr(guild,'get_member') else None
            saved=cache.get(key) if cache is not None else None
            if not member and saved and saved[0]>time.monotonic():
                if saved[1] is not None:result[uid]=saved[1]
                return
            if not member and guild and hasattr(guild,'fetch_member'):
                try:member=await guild.fetch_member(int(uid))
                except Exception:pass
            if not member and client is not None:
                member=client.get_user(int(uid)) if hasattr(client,'get_user') else None
                ready=not hasattr(client,'is_ready') or client.is_ready()
                if not member and ready and hasattr(client,'fetch_user'):
                    try:member=await client.fetch_user(int(uid))
                    except Exception:pass
            if member:result[uid]=member
            if cache is not None:cache[key]=(time.monotonic()+(600 if member else 60),member)
    tasks=[asyncio.create_task(resolve(uid)) for uid in ids]
    try:
        async with asyncio.timeout(5):await asyncio.gather(*tasks)
    except TimeoutError:pass
    finally:
        for task in tasks:
            if not task.done():task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
    if cache is not None:
        while len(cache)>512:cache.pop(next(iter(cache)))
    return result
