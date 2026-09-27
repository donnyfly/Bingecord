import asyncio
import tempfile

import storage as storage_module
from progression import challenges_for, level_from_xp, rank_for_level, xp_for_level, xp_for_watch
from achievements import ACHIEVEMENTS


def test_progression_curve():
    assert xp_for_watch("episode") == 100
    assert xp_for_watch("movie") == 300
    assert level_from_xp(0) == 1
    assert level_from_xp(xp_for_level(10)) == 10
    assert rank_for_level(1) == "Newcomer"
    assert rank_for_level(50) == "Media Enthusiast"
    assert rank_for_level(90) == "Screen Immortal"


def test_activity_based_episode_xp():
    assert xp_for_watch("episode") == 100
    assert xp_for_watch("episode", 39) == 100
    assert xp_for_watch("episode", 40) == 125
    assert xp_for_watch("episode", 59) == 125
    assert xp_for_watch("episode", 60) == 150
    assert xp_for_watch("episode", 90) == 175
    assert xp_for_watch("episode", 120) == 200
    assert xp_for_watch("episode", 180) == 250
    assert xp_for_watch("movie", 140) == 300


def test_challenge_rotation():
    from progression import challenges_for

    daily, weekly = challenges_for(__import__("datetime").date(2026, 9, 26))
    assert len(daily) == 3
    assert len(weekly) == 3
    assert all("target" in item and "xp" in item for item in daily + weekly)

def test_expanded_achievement_catalog_has_reconciled_categories():
    assert len(ACHIEVEMENTS) >= 35
    assert {item["category"] for item in ACHIEVEMENTS.values()} <= {
        "total","episodes","movies","anime_episodes","anime_movies","unique_titles","active_days","streak"
    }


def test_storage_watch_xp_is_idempotent():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            old_path = storage_module.DATA_PATH
            storage_module.DATA_PATH = f"{tmp}/store.json"
            try:
                store = storage_module.Storage()
                await store.link_user(
                    "123", "42", "token", "refresh", "tester",
                    "2026-09-26T00:00:00Z", token_expires_at=None, simkl_account_id=123,
                )
                first = await store.award_watch_xp(
                    "42", "episode:show:1:1:1:2026-09-26T10:00:00Z",
                    "episode", "Test Episode", "2026-09-26T10:00:00Z", 100,
                )
                second = await store.award_watch_xp(
                    "42", "episode:show:1:1:1:2026-09-26T10:00:00Z",
                    "episode", "Test Episode", "2026-09-26T10:00:00Z", 100,
                )
                progression = await store.get_progression("42")
                assert first["awarded"] is True
                assert second["awarded"] is False
                assert progression["xp"] == 100
                assert progression["lifetime_xp"] == 100
                assert len(progression["xp_events"]) == 1
            finally:
                storage_module.DATA_PATH = old_path

    asyncio.run(scenario())


def test_bulk_history_seed_writes_once_and_preserves_rewards(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_module,"DATA_PATH",str(tmp_path / "store.json"))
    actual_write=storage_module._write_to_disk
    writes=[]
    def counted_write(payload):
        writes.append(len(payload))
        actual_write(payload)
    monkeypatch.setattr(storage_module,"_write_to_disk",counted_write)

    async def scenario():
        store=storage_module.Storage()
        await store.link_user("123","42","token",None,"tester","2026-09-20T00:00:00Z")
        writes.clear()
        records=[("episode","Show",f"series:shows:1:1:{n}","2026-09-26T10:00:00Z",["Drama"])
                 for n in range(1,501)]
        amount=await store.seed_guild_history("123","42",[f"show:{n}" for n in range(500)],{},
                                               {},records)
        assert amount==50000
        assert len(writes)==1
        stats=await store.get_statistics("123","42")
        progression=await store.get_progression("42")
        assert stats["episodes_watched"]==500
        assert len(stats["watch_events"])==500
        assert len(progression["xp_events"])==500
        day=__import__("datetime").date(2026,9,26)
        daily,weekly=challenges_for(day)
        expected_bonus=sum(ch["xp"] for ch in daily+weekly if ch["kind"] in {"episodes","watches"})
        assert progression["xp"]==50000+expected_bonus
        assert await store.seed_guild_history("123","42",[],{},{},records)==0
        assert len(writes)==1
        assert await store.seed_progression_batch("42",[
            {"event_key":f"episode:{key}","media_type":"episode","title":"Show",
             "at":"2026-09-26T10:00:00Z","amount":100}
            for key in ("series:shows:1:1:1:2026-09-26T10:00:00Z",)
        ])==0
        assert len(writes)==2
        reloaded=storage_module.Storage()
        assert (await reloaded.get_statistics("123","42"))["episodes_watched"]==500
        assert (await reloaded.get_progression("42"))["xp"]==50000+expected_bonus
    asyncio.run(scenario())


def test_ranged_activity_is_recorded_in_one_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_module,"DATA_PATH",str(tmp_path / "store.json"))
    writes=[]
    real_write=storage_module._write_to_disk
    def counted_write(payload):
        writes.append(1)
        real_write(payload)
    monkeypatch.setattr(storage_module,"_write_to_disk",counted_write)

    async def scenario():
        store=storage_module.Storage()
        await store.link_user("123","42","token",None,"tester","2026-09-20T00:00:00Z")
        writes.clear()
        records=[{
            "media_type":"episode","title":"Show","item_key":f"series:shows:1:1:{n}",
            "watched_at":"2026-09-26T10:00:00Z","genres":["Drama"],
            "amount":125 if n==1 else 100,
        } for n in range(1,11)]
        await store.record_activity_batch("123","42",[str(n) for n in range(1,11)],
                                          {str(n):"2026-09-26T10:00:00Z" for n in range(1,11)},records)
        assert len(writes)==1
        progression=await store.get_progression("42")
        assert sum(event["amount"] for event in progression["xp_events"])==1025
        assert progression["challenge_completions"]
        assert (await store.get_statistics("123","42"))["episodes_watched"]==10
        await store.record_activity_batch("123","42",[str(n) for n in range(1,11)],{},records)
        assert (await store.get_statistics("123","42"))["episodes_watched"]==10
        assert (await store.get_progression("42"))["xp"]==progression["xp"]
    asyncio.run(scenario())


def test_older_history_is_retained_for_achievements_and_cross_server_dedup(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_module,"DATA_PATH",str(tmp_path / "store.json"))

    async def scenario():
        store=storage_module.Storage()
        for guild in ("123","456"):
            await store.link_user(guild,"42","token",None,"tester","2020-01-01T00:00:00Z")
        record=("episode","Older show","series:shows:1:1:1","2020-01-02T10:00:00Z",[])
        for guild in ("123","456"):
            await store.seed_guild_history(guild,"42",["episode:1"],{}, {},[record])
        progression=await store.get_progression("42")
        assert progression["xp"]==100
        assert len(progression["xp_events"])==1
        assert len(progression["watch_xp_keys"])==1
        assert (await store.get_statistics("456","42"))["episodes_watched"]==1
    asyncio.run(scenario())
