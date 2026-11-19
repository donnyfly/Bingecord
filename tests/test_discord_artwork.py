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


def test_uncached_members_resolve_display_names_and_cache_rest_results():
    from types import SimpleNamespace
    from trackerbot.presentation.discord_artwork import resolve_card_members
    async def scenario():
        member=SimpleNamespace(display_name='Adele',display_avatar='avatar')
        guild=SimpleNamespace(id=9,get_member=lambda uid:None,fetch_member=AsyncMock(return_value=member))
        client=SimpleNamespace()
        assert (await resolve_card_members(guild,['123'],client))['123'] is member
        assert (await resolve_card_members(guild,['123'],client))['123'] is member
        guild.fetch_member.assert_awaited_once_with(123)
        guild.fetch_member.side_effect=RuntimeError('member left')
        client.get_user=lambda uid:None
        client.fetch_user=AsyncMock(return_value=member)
        assert (await resolve_card_members(guild,['456'],client))['456'] is member
        client.fetch_user.assert_awaited_once_with(456)
        client.fetch_user.side_effect=RuntimeError('unavailable')
        assert await resolve_card_members(guild,['789'],client)=={}
    asyncio.run(scenario())


def test_server_icon_is_loaded_alongside_five_avatars():
    async def scenario():
        data=BytesIO();Image.new('RGB',(32,32),'blue').save(data,format='PNG')
        assets={str(i):Asset(data.getvalue()) for i in range(5)}
        assets['server_icon']=Asset(data.getvalue())
        result=await load_discord_artwork(assets,limit=6)
        assert set(result)==set(assets)
    asyncio.run(scenario())
