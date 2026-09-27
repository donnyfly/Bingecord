"""Chronically failing SIMKL links must stop being polled automatically."""
import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

os.environ.setdefault('DISCORD_BOT_TOKEN','test-token')
os.environ.setdefault('SIMKL_CLIENT_ID','test-client')
os.environ.setdefault('TMDB_API_KEY','test-key')
import bot  # noqa: E402
import storage as storage_module  # noqa: E402
from simkl_client import SimklAuthError  # noqa: E402

GUILD='123'
BROKEN='41'
HEALTHY='42'


async def make_store(monkeypatch,tmp_path):
    monkeypatch.setattr(storage_module,'_write_lock',asyncio.Lock())
    monkeypatch.setattr(storage_module,'_lock',asyncio.Lock())
    monkeypatch.setattr(storage_module,'DATA_PATH',str(tmp_path/'store.json'))
    store=storage_module.Storage()
    monkeypatch.setattr(bot,'storage',store)
    await store.set_channel(GUILD,'55')
    for uid in (BROKEN,HEALTHY):
        await store.link_user(GUILD,uid,'token',None,uid,'2026-09-20T00:00:00Z',simkl_account_id=1)
    channel=SimpleNamespace(id=55,send=AsyncMock())
    monkeypatch.setattr(bot.bot,'get_channel',lambda _:channel)
    monkeypatch.setattr(bot.bot,'get_guild',lambda _:SimpleNamespace(name='Test Server'))
    monkeypatch.setattr(bot,'refresh_community_state',AsyncMock())
    return store


def install_dm(monkeypatch,send=None):
    user=SimpleNamespace(send=send or AsyncMock())
    monkeypatch.setattr(bot.bot,'get_user',lambda _:user)
    return user


def health(store,uid):
    return store._data['guilds'][GUILD]['users'][uid]


def test_background_poll_skips_target_at_threshold(monkeypatch,tmp_path):
    async def scenario():
        store=await make_store(monkeypatch,tmp_path)
        install_dm(monkeypatch)
        await store.update_poll_health(GUILD,BROKEN,consecutive_failures=bot.MAX_CONSECUTIVE_FAILURES)
        await store.update_poll_health(GUILD,HEALTHY,consecutive_failures=bot.MAX_CONSECUTIVE_FAILURES-1)
        poll_one=AsyncMock(return_value=0)
        monkeypatch.setattr(bot,'poll_one',poll_one)
        await bot.poll_all(GUILD)
        polled={call.args[2] for call in poll_one.await_args_list}
        assert polled=={HEALTHY}
        # Skipping does not touch the counter.
        assert health(store,BROKEN)['consecutive_failures']==bot.MAX_CONSECUTIVE_FAILURES
    asyncio.run(scenario())


def test_failures_accumulate_then_skip_and_dm_only_once(monkeypatch,tmp_path):
    async def scenario():
        store=await make_store(monkeypatch,tmp_path)
        user=install_dm(monkeypatch)
        monkeypatch.setattr(store,'prepare_empty_history_repair',AsyncMock(return_value=False))
        await store.unlink_user(GUILD,HEALTHY)
        valid_token=AsyncMock(side_effect=SimklAuthError('token revoked'))
        monkeypatch.setattr(bot,'valid_token',valid_token)

        for expected in range(1,bot.MAX_CONSECUTIVE_FAILURES+1):
            await bot.poll_all(GUILD)
            assert health(store,BROKEN)['consecutive_failures']==expected
        assert valid_token.await_count==bot.MAX_CONSECUTIVE_FAILURES
        user.send.assert_not_awaited()

        # Next background cycles skip SIMKL entirely and DM exactly once.
        for _ in range(3):
            await bot.poll_all(GUILD)
        assert valid_token.await_count==bot.MAX_CONSECUTIVE_FAILURES
        user.send.assert_awaited_once()
        assert 'Test Server' in user.send.await_args.args[0]
        assert '/simkl-link' in user.send.await_args.args[0]
        assert health(store,BROKEN)['failure_notified'] is True

        # The flag survives a restart, so a reboot doesn't re-send the DM.
        reloaded=storage_module.Storage()
        assert reloaded._data['guilds'][GUILD]['users'][BROKEN]['failure_notified'] is True
    asyncio.run(scenario())


def test_closed_dms_count_as_notified_but_transient_errors_retry(monkeypatch,tmp_path):
    async def scenario():
        store=await make_store(monkeypatch,tmp_path)
        await store.update_poll_health(GUILD,BROKEN,consecutive_failures=bot.MAX_CONSECUTIVE_FAILURES)
        monkeypatch.setattr(bot,'poll_one',AsyncMock(return_value=0))

        flaky=install_dm(monkeypatch,AsyncMock(side_effect=RuntimeError('gateway hiccup')))
        await bot.poll_all(GUILD)
        assert health(store,BROKEN)['failure_notified'] is False

        forbidden=discord.Forbidden(SimpleNamespace(status=403,reason='Forbidden'),'Cannot send messages to this user')
        closed=install_dm(monkeypatch,AsyncMock(side_effect=forbidden))
        await bot.poll_all(GUILD)
        await bot.poll_all(GUILD)
        assert flaky.send.await_count==1
        closed.send.assert_awaited_once()
        assert health(store,BROKEN)['failure_notified'] is True
    asyncio.run(scenario())


def test_manual_check_still_attempts_and_success_resets(monkeypatch,tmp_path):
    async def scenario():
        store=await make_store(monkeypatch,tmp_path)
        install_dm(monkeypatch)
        await store.update_poll_health(GUILD,BROKEN,consecutive_failures=bot.MAX_CONSECUTIVE_FAILURES+3,
                                       failure_notified=True,last_error='SIMKL authentication failed')

        async def successful_poll(ch,g,uid,u,gu,*args,**kwargs):
            await bot.storage.update_poll_health(g,uid,last_success_at=bot.now_iso(),last_error='',consecutive_failures=0)
            return 0
        poll_one=AsyncMock(side_effect=successful_poll)
        monkeypatch.setattr(bot,'poll_one',poll_one)

        await bot.poll_all(GUILD,force_reconcile=True,ignore_failure_threshold=True)
        assert BROKEN in {call.args[2] for call in poll_one.await_args_list}
        assert health(store,BROKEN)['consecutive_failures']==0
        assert health(store,BROKEN)['failure_notified'] is False
        assert health(store,BROKEN)['last_error']==''

        # Back under the threshold, so background polling resumes.
        poll_one.reset_mock()
        await bot.poll_all(GUILD)
        assert BROKEN in {call.args[2] for call in poll_one.await_args_list}
    asyncio.run(scenario())


def test_checknow_command_bypasses_failure_threshold(monkeypatch):
    async def scenario():
        poll_all=AsyncMock(return_value=0)
        monkeypatch.setattr(bot,'poll_all',poll_all)
        monkeypatch.setattr(bot,'last_checknow_at',0.0)
        monkeypatch.setattr(bot.time,'monotonic',lambda:10**9)
        i=SimpleNamespace(guild_id=1,guild=SimpleNamespace(id=1),
                          user=SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=True)),
                          response=SimpleNamespace(send_message=AsyncMock()),
                          followup=SimpleNamespace(send=AsyncMock()))
        await bot.simkl_checknow.callback(i)
        poll_all.assert_awaited_once()
        assert poll_all.await_args.kwargs.get('ignore_failure_threshold') is True
    asyncio.run(scenario())
