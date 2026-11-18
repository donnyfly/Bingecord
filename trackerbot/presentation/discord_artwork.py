"""Small, optional Discord avatars and icons for rendered cards."""
import asyncio
from io import BytesIO
from PIL import Image


async def load_discord_artwork(assets):
    """Read at most five resized Discord Assets within one three-second budget."""
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
    tasks=[asyncio.create_task(load(key,asset)) for key,asset in list(assets.items())[:5] if asset]
    try:
        async with asyncio.timeout(3):await asyncio.gather(*tasks)
    except TimeoutError:pass
    finally:
        for task in tasks:
            if not task.done():task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
    return result
