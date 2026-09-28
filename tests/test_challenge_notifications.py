import asyncio
import os
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault("DISCORD_BOT_TOKEN","test-token")
os.environ.setdefault("SIMKL_CLIENT_ID","test-client")
os.environ.setdefault("TMDB_API_KEY","test-key")

import bot  # noqa: E402
import storage as storage_module  # noqa: E402
from progression import challenges_for  # noqa: E402


def test_live_challenges_announce_exact_xp_once_and_retry(tmp_path,monkeypatch):
    async def scenario():
        monkeypatch.setattr(storage_module,"DATA_PATH",str(tmp_path/"store.json"))
        store=storage_module.Storage()
        monkeypatch.setattr(bot,"storage",store)
        await store.link_user("123","42","token",None,"tester","2026-09-27T00:00:00Z")
        records=[{"media_type":"episode","title":"Show","item_key":f"series:1:{n}",
                  "watched_at":"2026-09-27T10:00:00Z","amount":100} for n in range(20)]
        records += [{"media_type":"movie","title":"Movie","item_key":f"movie:{n}",
                     "watched_at":"2026-09-27T10:00:00Z","amount":300} for n in range(5)]
        await store.record_activity_batch("123","42",[],{},records)
        pending=await store.get_pending_challenge_notifications("42")
        daily,weekly=challenges_for(date(2026,9,27))
        assert len(pending)==6
        assert sum(item["xp"] for item in pending)==sum(item["xp"] for item in daily+weekly)
        channel=SimpleNamespace(send=AsyncMock(side_effect=RuntimeError("Missing Access")))
        assert not await bot.notify_challenge_rewards("123","42",channel)
        assert await store.get_pending_challenge_notifications("42")==pending
        reloaded=storage_module.Storage()
        assert await reloaded.get_pending_challenge_notifications("42")==pending
        monkeypatch.setattr(bot,"storage",reloaded)
        channel.send=AsyncMock()
        assert await bot.notify_challenge_rewards("123","42",channel)
        embed=channel.send.await_args.kwargs["embed"]
        assert f"+{sum(item['xp'] for item in pending):,} XP" in embed.description
        assert "Daily" in embed.description and "Weekly" in embed.description
        assert channel.send.await_args.kwargs["allowed_mentions"].users
        assert await reloaded.get_pending_challenge_notifications("42")==[]
        assert not await bot.notify_challenge_rewards("123","42",channel)
        channel.send.assert_awaited_once()
    asyncio.run(scenario())


def test_backfill_and_disabled_challenges_do_not_queue_notifications(tmp_path,monkeypatch):
    async def scenario():
        monkeypatch.setattr(storage_module,"DATA_PATH",str(tmp_path/"store.json"))
        store=storage_module.Storage()
        await store.link_user("123","42","token",None,"tester","2026-09-27T00:00:00Z")
        records=[("episode","Show",f"show:{n}","2026-09-26T10:00:00Z",[]) for n in range(20)]
        await store.seed_guild_history("123","42",[],{},{},records)
        assert await store.get_pending_challenge_notifications("42")==[]
        await store.set_features("123",{"challenges":False})
        live=[{"media_type":"movie","title":"Movie","item_key":f"movie:{n}",
               "watched_at":"2026-09-27T10:00:00Z","amount":300} for n in range(5)]
        await store.record_activity_batch("123","42",[],{},live)
        assert await store.get_pending_challenge_notifications("42")==[]
    asyncio.run(scenario())


def test_community_announcement_names_every_contributor_and_acknowledges(tmp_path,monkeypatch):
    async def scenario():
        monkeypatch.setattr(storage_module,"DATA_PATH",str(tmp_path/"store.json"))
        store=storage_module.Storage()
        monkeypatch.setattr(bot,"storage",store)
        for uid in ("41","42"):
            await store.link_user("123",uid,"token",None,uid,"2026-09-21T00:00:00Z")
        from datetime import datetime,timezone
        start=datetime(2026,9,21,tzinfo=timezone.utc)
        end=datetime(2026,9,28,tzinfo=timezone.utc)
        await store.get_community_state("123","2026-09-21",start,end,start)
        for uid,count in (("41",25),("42",15)):
            await store.get_progression(uid)
            store._data["users"][uid]["progression"]["xp_events"]=[
                {"at":"2026-09-23T12:00:00Z","event_key":f"episode:{uid}:{n}","media_type":"episode"}
                for n in range(count)
            ]
        state=await store.get_community_state("123","2026-09-21",start,end,end)
        notification=state["pending_notifications"][0]
        channel=SimpleNamespace(send=AsyncMock(side_effect=RuntimeError("Missing Access")))
        assert not await bot.notify_community_rewards("123",channel,notification)
        assert (await store.get_community_state("123","2026-09-21",start,end,end))["pending_notifications"]
        reloaded=storage_module.Storage()
        assert (await reloaded.get_community_state("123","2026-09-21",start,end,end))["pending_notifications"]
        monkeypatch.setattr(bot,"storage",reloaded)
        channel.send=AsyncMock()
        assert await bot.notify_community_rewards("123",channel,notification)
        embed=channel.send.await_args.kwargs["embed"]
        assert "<@41>" in embed.description and "+7,500 XP" in embed.description
        assert "<@42>" in embed.description and "+4,500 XP" in embed.description
        assert channel.send.await_args.kwargs["allowed_mentions"].users
        assert (await reloaded.get_community_state("123","2026-09-21",start,end,end))["pending_notifications"]==[]
    asyncio.run(scenario())


def test_community_notification_precedes_level_up(monkeypatch):
    async def scenario():
        calls=[]
        channel=SimpleNamespace(send=AsyncMock())
        monkeypatch.setattr(bot,"feature_enabled",AsyncMock(return_value=True))
        monkeypatch.setattr(bot.storage,"get_timezone",AsyncMock(return_value={"name":"UTC"}))
        monkeypatch.setattr(bot.storage,"get_channel",AsyncMock(return_value="1234"))
        monkeypatch.setattr(bot.bot,"get_channel",lambda channel_id:channel)
        notification={"key":"2026-09-21","awards":{"42":100},"members":["42"]}
        monkeypatch.setattr(bot.storage,"get_community_state",AsyncMock(return_value={
            "pending_notifications":[notification],
            "changes":[{"uid":"42","before":100,"after":200,"delta":100}],
        }))
        monkeypatch.setattr(bot.storage,"get_progression",AsyncMock(return_value={"lifetime_xp":200}))
        monkeypatch.setattr(bot.storage,"claim_prestige_notifications",AsyncMock(return_value=[]))
        monkeypatch.setattr(bot.storage,"flush",AsyncMock())
        async def announce(*args): calls.append("community")
        async def level(*args, **kwargs): calls.append("level")
        monkeypatch.setattr(bot,"notify_community_rewards",announce)
        monkeypatch.setattr(bot,"notify_level_up",level)
        await bot.refresh_community_state("123")
        assert calls==["community","level"]
    asyncio.run(scenario())
