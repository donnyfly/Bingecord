"""Server controls must isolate guilds and stop disabled work."""
import asyncio
import os
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from discord import app_commands
os.environ.setdefault('DISCORD_BOT_TOKEN','test-token')
os.environ.setdefault('SIMKL_CLIENT_ID','test-client')
os.environ.setdefault('TMDB_API_KEY','test-key')
import bot
import storage as storage_module


def test_feature_defaults_isolation_and_restart(tmp_path,monkeypatch):
    async def scenario():
        monkeypatch.setattr(storage_module,'DATA_PATH',str(tmp_path/'store.json'))
        store=storage_module.Storage()
        assert all((await store.get_features('1')).values())
        await store.set_features('1',dict.fromkeys(storage_module.DEFAULT_FEATURES,False))
        assert not any((await store.get_features('1')).values())
        assert all((await store.get_features('2')).values())
        reloaded=storage_module.Storage()
        assert not any((await reloaded.get_features('1')).values())
        await reloaded.set_features('1',{'leaderboards':True})
        assert (await reloaded.get_features('1'))['leaderboards']
        assert not (await reloaded.get_features('1'))['progression']
    asyncio.run(scenario())


def test_disabled_commands_and_core_commands(monkeypatch):
    async def scenario():
        monkeypatch.setattr(bot.storage,'get_features',AsyncMock(return_value=dict.fromkeys(storage_module.DEFAULT_FEATURES,False)))
        i=SimpleNamespace(guild_id=1,data={'name':'tracker-leaderboard'},type=discord.InteractionType.application_command,
                          response=SimpleNamespace(send_message=AsyncMock()))
        assert not await bot.bot.tree.interaction_check(i)
        i.response.send_message.assert_awaited_once()
        for name in ('tracker-link','tracker-checknow','tracker-features','tracker-debug'):
            i.data={'name':name}
            assert await bot.bot.tree.interaction_check(i)
    asyncio.run(scenario())


def test_disabled_background_features_do_no_work(monkeypatch):
    async def scenario():
        monkeypatch.setattr(bot.storage,'get_features',AsyncMock(return_value=dict.fromkeys(storage_module.DEFAULT_FEATURES,False)))
        forbidden=AsyncMock(side_effect=AssertionError('disabled feature did work'))
        monkeypatch.setattr(bot.storage,'get_progression',forbidden)
        monkeypatch.setattr(bot.storage,'get_community_state',forbidden)
        monkeypatch.setattr(bot.storage,'get_channel',forbidden)
        assert await bot.refresh_community_state(1) is None
        assert not await bot.notify_level_up(1,'42',{}, {},SimpleNamespace())
        assert not await bot.notify_history_backfill(1,'42',100,{},SimpleNamespace())
        assert not await bot.send_weekly_recap(SimpleNamespace(id=1))
        forbidden.assert_not_awaited()
    asyncio.run(scenario())


def test_feature_command_requires_admin(monkeypatch):
    async def scenario():
        write=AsyncMock()
        monkeypatch.setattr(bot.storage,'set_features',write)
        i=SimpleNamespace(guild_id=1,guild=SimpleNamespace(id=1),user=SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=False,administrator=False)),
                          response=SimpleNamespace(send_message=AsyncMock()))
        await bot.simkl_features.callback(i,preset=app_commands.Choice(name='Activity only',value='classic'))
        write.assert_not_awaited()
        i.response.send_message.assert_awaited_once()
    asyncio.run(scenario())


def test_streaks_long_history_and_gaps():
    start=datetime(2010,1,1)
    dates={(start+timedelta(days=n)).date().isoformat():{} for n in range(2000)}
    assert bot.calculate_streaks(dates,'UTC')==(0,2000)
    del dates[(start+timedelta(days=1000)).date().isoformat()]
    assert bot.calculate_streaks(dates,'UTC')==(0,1000)


def test_disabled_achievement_notifications_still_reconcile(tmp_path,monkeypatch):
    async def scenario():
        monkeypatch.setattr(storage_module,'DATA_PATH',str(tmp_path/'store.json'))
        store=storage_module.Storage()
        monkeypatch.setattr(bot,'storage',store)
        await store.link_user('1','42','token',None,'viewer','2026-09-27T00:00:00Z')
        await store.set_features('1',{'achievements':False})
        stamp='2026-09-27T00:00:00Z'
        await store.award_watch_xp('42',f'episode:series:shows:99:1:1:{stamp}','episode','Example',stamp,100)
        channel=SimpleNamespace(send=AsyncMock())
        await bot.evaluate_achievements('1','42',channel)
        assert 'first_watch' in await store.get_achievements('1','42')
        await store.reconcile_watch_xp('42',set(),{'episode'})
        await bot.evaluate_achievements('1','42',channel)
        assert 'first_watch' not in await store.get_achievements('1','42')
        channel.send.assert_not_awaited()
    asyncio.run(scenario())
