import asyncio
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
from PIL import Image
from discord import app_commands
from test_notification_preview import bot,_interaction
from trackerbot.core.community_events import event_goal,event_state
from trackerbot.presentation.community_visuals import render_community_png


def state(kind='watchathon'):
    weeks={'watchathon':'2026-11-23','genres':'2026-11-30','bingo':'2026-12-07','decades':'2026-12-14'}
    key=weeks[kind];start=datetime.fromisoformat(key).replace(tzinfo=timezone.utc)
    record=event_goal(key,4);record.update(members=['1','2','3','4'],key=key,start=start.isoformat(),end=(start+timedelta(days=7)).isoformat())
    return event_state(record,[],{},start+timedelta(days=1))


def test_all_event_card_shapes_empty_data_and_long_names():
    for kind in ('watchathon','genres','bingo','decades'):
        s=state(kind);s['preview']=True
        data=render_community_png('A very long server name that needs to fit its title area',s,viewer='42')
        assert Image.open(data).size==(1080,1500)
        assert len(data.getvalue())<8_000_000


def test_debug_community_does_not_refresh_or_publish_live_events(monkeypatch):
    async def run():
        snapshot=state('genres')
        monkeypatch.setattr(bot.storage,'get_timezone',AsyncMock(return_value={'name':'UTC'}))
        preview=AsyncMock(return_value=snapshot);monkeypatch.setattr(bot.storage,'preview_community_event',preview)
        refresh=AsyncMock();monkeypatch.setattr(bot,'refresh_community_state',refresh)
        send=AsyncMock();monkeypatch.setattr(bot,'send_community_card',send)
        i=_interaction();i.guild.name='Server'
        await bot.simkl_debug.callback(i,app_commands.Choice(name='Community event card',value='community'),
                                      community_event=app_commands.Choice(name='Genre Expedition',value='genres'))
        i.response.defer.assert_awaited_once_with(ephemeral=True)
        assert send.await_args.kwargs['preview'] is True
        assert preview.await_args.args[-1]=='genres'
        refresh.assert_not_awaited()
    asyncio.run(run())


def test_debug_community_reading_goal_without_a_live_event(tmp_path, monkeypatch):
    import trackerbot.core.storage as storage_module

    async def run():
        monkeypatch.setattr(storage_module, "DATA_PATH", str(tmp_path / "store.json"))
        store = storage_module.Storage()
        monkeypatch.setattr(store, "get_timezone", AsyncMock(return_value={"name": "UTC"}))
        monkeypatch.setattr(bot, "storage", store)
        send = AsyncMock()
        monkeypatch.setattr(bot, "send_community_card", send)
        interaction = _interaction()
        interaction.guild.name = "Server"
        before = deepcopy(store._data)
        await bot.simkl_debug.callback(
            interaction, app_commands.Choice(name="Community event card", value="community"),
            community_event=app_commands.Choice(name="Community Reading Goal", value="reading"),
        )
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        preview = send.await_args.args[2]
        assert send.await_args.kwargs["preview"]
        assert preview["kind"] == "reading"
        assert preview["reading"]["kind"] == "readathon"
        assert "total" not in preview and "art_entries" not in preview
        assert preview["reading"]["chapters"] == 0
        assert preview["reading"]["xp"] == 350
        from trackerbot.presentation.manga_pages import community_page
        payload = await community_page(interaction.guild, preview["reading"], "42", preview=True)
        assert payload["view"].embed.title == "Community Readathon"
        assert Image.open(payload["file"].fp).size == (1080, 1500)
        assert store._data == before

    asyncio.run(run())


def test_delivery_edits_are_throttled_and_notice_failure_retries(monkeypatch):
    async def run():
        s=state();now=datetime.fromisoformat(s['start'])+timedelta(days=1)
        sent=AsyncMock(return_value=SimpleNamespace(id=99));monkeypatch.setattr(bot,'send_community_card',sent)
        ack=AsyncMock();monkeypatch.setattr(bot.storage,'ack_community_delivery',ack)
        channel=SimpleNamespace(id=9,send=AsyncMock(),get_partial_message=lambda mid:SimpleNamespace(edit=AsyncMock()))
        await bot.update_community_delivery('1',channel,s,now)
        assert sent.await_count==1 and ack.await_args.args[2]=='opening'
        fingerprint=ack.await_args.kwargs['fingerprint']
        s['delivery']={'opening':True,'fingerprint':fingerprint,'updated_at':now.isoformat(),'message_id':'99','channel_id':'9'}
        s['total']=1
        await bot.update_community_delivery('1',channel,s,now+timedelta(minutes=10))
        assert sent.await_count==1
        await bot.update_community_delivery('1',channel,s,now+timedelta(minutes=21))
        assert sent.await_count==2
        late=datetime.fromisoformat(s['end'])-timedelta(hours=12)
        s['delivery']['updated_at']=late.isoformat();sent.side_effect=RuntimeError('offline')
        before=ack.await_count
        await bot.update_community_delivery('1',channel,s,late)
        assert ack.await_count==before
        sent.side_effect=None
        await bot.update_community_delivery('1',channel,s,late)
        assert ack.await_args.args[2]=='reminder'
    asyncio.run(run())


def test_failed_reward_notice_does_not_fall_through_to_second_results_post(monkeypatch):
    async def run():
        now=datetime.now(timezone.utc);s=state();s.update(end=(now-timedelta(hours=8)).isoformat(),
            changes=[],pending_milestone=0)
        notice={**s,'initial':True,'awards':{'42':3000},'contributions':{'42':600},'deltas':{'42':3000}}
        s.update(pending_notifications=[notice],pending_results=[notice])
        monkeypatch.setattr(bot,'feature_enabled',AsyncMock(return_value=True))
        monkeypatch.setattr(bot.storage,'get_timezone',AsyncMock(return_value={'name':'UTC'}))
        monkeypatch.setattr(bot.storage,'community_metadata_candidates',AsyncMock(return_value=[]))
        monkeypatch.setattr(bot.storage,'get_community_state',AsyncMock(return_value=s))
        monkeypatch.setattr(bot.storage,'get_channel',AsyncMock(return_value=9))
        ack=AsyncMock();monkeypatch.setattr(bot.storage,'ack_community_notification',ack)
        channel=SimpleNamespace(id=9,send=AsyncMock());monkeypatch.setattr(bot.bot,'get_channel',lambda _:channel)
        send=AsyncMock(side_effect=RuntimeError('Discord offline'));monkeypatch.setattr(bot,'send_community_card',send)
        await bot.refresh_community_state('1')
        assert send.await_count==1;ack.assert_not_awaited()
        send.side_effect=None
        await bot.refresh_community_state('1')
        assert send.await_count==2;ack.assert_awaited_once()
    asyncio.run(run())


def test_card_delivery_resolves_uncached_names_and_supplies_server_icon(monkeypatch):
    import trackerbot.presentation.community_delivery as delivery
    async def run():
        member=SimpleNamespace(display_name='Adele',display_avatar='avatar-asset')
        guild=SimpleNamespace(id=9,name='Basement buddies',icon='server-asset',
                              get_member=lambda uid:None,fetch_member=AsyncMock(return_value=member))
        s=state();s['contributions']={'123':120}
        artwork=AsyncMock(return_value={'123':'avatar-image','server_icon':'server-image'})
        monkeypatch.setattr(delivery,'load_discord_artwork',artwork)
        from unittest.mock import Mock
        render=Mock(return_value=BytesIO(b'png'))
        monkeypatch.setattr(delivery,'render_community_png',render)
        await delivery.community_card(AsyncMock(),guild,s,viewer='123')
        assert render.call_args.kwargs['names']=={'123':'Adele'}
        assert render.call_args.kwargs['avatars']=={'123':'avatar-image'}
        assert render.call_args.kwargs['server_icon']=='server-image'
        assert artwork.await_args.args[0]=={'123':'avatar-asset','server_icon':'server-asset'}
    asyncio.run(run())


def test_early_start_requires_manage_server(monkeypatch):
    async def run():
        i=_interaction();i.user.guild_permissions=SimpleNamespace(manage_guild=False)
        start=AsyncMock();monkeypatch.setattr(bot.storage,'start_new_community_event',start)
        await bot.simkl_community.callback(i,start_new=True)
        start.assert_not_awaited();i.response.defer.assert_not_awaited()
        assert 'Manage Server' in i.response.send_message.await_args.args[0]
    asyncio.run(run())
