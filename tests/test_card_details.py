import asyncio
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
import discord
from trackerbot.presentation.card_details import CardDetails,details_view,configure


def test_persistent_details_survive_reload_and_enforce_private_scope(tmp_path,monkeypatch):
    import trackerbot.core.storage as module
    async def run():
        monkeypatch.setattr(module,'DATA_PATH',str(tmp_path/'details.json'));store=module.Storage()
        configure(lambda:store)
        embed=discord.Embed(title='Personal',description='Full text');embed.add_field(name='Daily',value='Objective details')
        embed.set_image(url='attachment://card.png')
        view=await details_view(embed,owner=42,guild=123)
        assert view.is_persistent()
        item=view.children[0];token=item.token
        store=module.Storage();record=await store.get_card_details(token)
        assert record['embed']['fields'][0]['value']=='Objective details'
        assert not record['embed'].get('image')
        button=CardDetails(token)
        i=SimpleNamespace(user=SimpleNamespace(id=7),guild_id=123,response=SimpleNamespace(send_message=AsyncMock()))
        await button.callback(i)
        assert 'private preview' in i.response.send_message.await_args.args[0]
        i.user.id=42;i.guild_id=999;await button.callback(i)
        assert 'another server' in i.response.send_message.await_args.args[0]
        i.guild_id=123;await button.callback(i)
        kwargs=i.response.send_message.await_args.kwargs
        assert kwargs['ephemeral'] and kwargs['embed'].fields[0].value=='Objective details'
        store._data['card_details'][token]['expires_at']=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
        await button.callback(i)
        assert 'expired' in i.response.send_message.await_args.args[0]
        configure(lambda:None)
    asyncio.run(run())


def test_successful_personal_card_is_compact_but_details_retain_objectives(monkeypatch):
    import trackerbot.presentation.challenge_delivery as delivery
    from trackerbot.core.personal_challenges import snapshot
    from io import BytesIO
    async def run():
        configure(lambda:None)
        user=SimpleNamespace(id=42,display_name='Viewer',display_avatar=None)
        state=snapshot({},datetime(2026,11,24,tzinfo=timezone.utc),preview=True)
        monkeypatch.setattr(delivery,'load_discord_artwork',AsyncMock(return_value={}))
        monkeypatch.setattr(delivery,'render_challenges_png',Mock(return_value=BytesIO(b'png')))
        send=AsyncMock();await delivery.challenge_card(send,user,state,now=datetime(2026,11,24,tzinfo=timezone.utc),ephemeral=True)
        kwargs=send.await_args.kwargs
        assert kwargs['embed'] is None and kwargs['file'].filename=='challenges.png'
        assert 'Preview' in kwargs['view'].embed.title
        fields=kwargs['view'].embed.fields
        assert len(fields)==4
        assert 'Read 15 manga chapters' in fields[3].value
    asyncio.run(run())


def test_community_compact_results_and_text_fallback(monkeypatch):
    import trackerbot.presentation.community_delivery as delivery
    from io import BytesIO
    async def run():
        configure(lambda:None)
        s={'kind':'minutes','status':'completed','total':720,'target':720,'pool':1440,'contributions':{'42':720},
           'awards':{'42':1440},'deltas':{'42':1440},'end':'2026-11-23T00:00:00Z'}
        guild=SimpleNamespace(id=1,name='Server',get_member=lambda _:None)
        monkeypatch.setattr(delivery,'load_discord_artwork',AsyncMock(return_value={}))
        monkeypatch.setattr(delivery,'render_community_png',Mock(return_value=BytesIO(b'png')))
        send=AsyncMock();await delivery.community_card(send,guild,s)
        kwargs=send.await_args.kwargs
        assert kwargs['embed'] is None and kwargs['file'].filename=='community.png'
        detail=kwargs['view'].embed
        assert detail.fields[0].name=='Reward results' and '+1,440 XP' in detail.fields[0].value
        monkeypatch.setattr(delivery,'render_community_png',Mock(side_effect=ValueError('unavailable')))
        await delivery.community_card(send,guild,s)
        kwargs=send.await_args.kwargs
        assert 'file' not in kwargs and 'XP pool' in kwargs['embed'].description
        assert kwargs['embed'].fields[0].name=='Reward results'
    asyncio.run(run())
