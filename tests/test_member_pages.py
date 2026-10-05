import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

from trackerbot.core.watch_delivery import mentions
from trackerbot.presentation.member_pages import MemberPages


def test_page_boundaries_and_owner_controls():
    async def run():
        renderer=AsyncMock(return_value=(None,None))
        view=MemberPages(42,list(range(12)),renderer)
        await view.render()
        assert renderer.call_args.args == ([0,1,2,3,4],0,1,3)
        assert view.previous.disabled and not view.next.disabled
        view.page=2
        view.update_buttons()
        await view.render()
        assert renderer.call_args.args == ([10,11],10,3,3)
        assert view.next.disabled and not view.previous.disabled
        response=SimpleNamespace(send_message=AsyncMock())
        assert not await view.interaction_check(SimpleNamespace(user=SimpleNamespace(id=7),response=response))
        assert await view.interaction_check(SimpleNamespace(user=SimpleNamespace(id=42),response=response))
    asyncio.run(run())


def test_group_mentions_are_unique_and_bounded():
    result=mentions([str(n) for n in range(20)]+['0'])
    assert result.count('<@') == 5
    assert '15 others' in result


def test_status_member_filter_and_bounded_summary(monkeypatch):
    os.environ.setdefault('DISCORD_BOT_TOKEN','test-token')
    os.environ.setdefault('SIMKL_CLIENT_ID','test-client')
    os.environ.setdefault('TMDB_API_KEY','test-key')
    import bot
    async def run():
        users={str(n):{'activity_provider':'wetrakr','wetrakr_linked':True,'wetrakr_sync':{'seeded':True}} for n in range(20)}
        global_users={str(n):{'wetrakr':{'username':f'Viewer{n}'}} for n in range(20)}
        monkeypatch.setattr(bot,'storage',SimpleNamespace(get_all=AsyncMock(return_value={'guilds':{'123':{'users':users}},'users':global_users})))
        monkeypatch.setattr(bot,'is_admin',lambda _:True)
        guild=SimpleNamespace(id=123,get_member=lambda uid:SimpleNamespace(id=uid),fetch_member=AsyncMock())
        interaction=SimpleNamespace(guild_id=123,guild=guild,response=SimpleNamespace(defer=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()))
        await bot.simkl_status.callback(interaction,user=SimpleNamespace(id=17))
        text=interaction.followup.send.call_args.kwargs['embed'].description
        assert '<@17>' in text and 'Viewer17' in text and '<@0>' not in text
        await bot.simkl_status.callback(interaction)
        text=interaction.followup.send.call_args.kwargs['embed'].description
        assert text.count('<@') == 5
        assert '15' in text and len(text)<4096
        guild.fetch_member.assert_not_awaited()
    asyncio.run(run())
