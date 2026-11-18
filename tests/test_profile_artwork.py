import asyncio
from io import BytesIO
from types import SimpleNamespace
from PIL import Image
from trackerbot.presentation.profile_artwork import download_card_image


def test_artwork_download_decodes_complete_chunked_file_and_limits_bytes():
    class Response:
        status=200
        def __init__(self,chunks):self.content=self;self.chunks=chunks
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def iter_chunked(self,size):
            for chunk in self.chunks:yield chunk
    async def run():
        buffer=BytesIO();Image.new('RGB',(80,100),'blue').save(buffer,'PNG');blob=buffer.getvalue()
        session=SimpleNamespace(get=lambda *a,**k:Response([blob[:16],blob[16:40],blob[40:]]))
        result=await download_card_image(session,'/poster.png','w342')
        assert result.size==(80,100) and result.getpixel((0,0))==(0,0,255,255)
        session.get=lambda *a,**k:Response([b'x'*2_000_000]*3)
        assert await download_card_image(session,'/poster.png','w342') is None
    asyncio.run(run())
