import asyncio
from io import BytesIO
from unittest.mock import AsyncMock
from PIL import Image
from trackerbot.presentation.discord_artwork import load_discord_artwork


class Asset:
    def __init__(self,blob):
        self.read=AsyncMock(return_value=blob)
    def with_size(self,size):
        assert size==128
        return self
    def with_format(self,format):
        assert format=='png'
        return self


def test_optional_avatars_decode_with_missing_and_invalid_assets():
    async def scenario():
        data=BytesIO();Image.new('RGB',(128,128),'blue').save(data,format='PNG')
        good=Asset(data.getvalue());bad=Asset(b'not an image');failed=Asset(b'')
        failed.read.side_effect=TimeoutError()
        result=await load_discord_artwork({'1':good,'2':bad,'3':None,'4':failed})
        assert list(result)==['1'] and result['1'].size==(128,128)
        assert await load_discord_artwork({})=={}
    asyncio.run(scenario())
