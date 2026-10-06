import asyncio
from datetime import datetime, timezone
import tempfile

import trackerbot.core.storage as storage_module
from trackerbot.core.community import challenge_for_week, community_week, split_pool, watch_contributions


def test_week_boundaries_and_exact_pool_split():
    key,start,end=community_week(datetime(2026,9,26,10,tzinfo=timezone.utc),"Asia/Singapore")
    assert key=="2026-09-21"
    assert start==datetime(2026,9,20,16,tzinfo=timezone.utc)
    assert end==datetime(2026,9,27,16,tzinfo=timezone.utc)
    assert split_pool({"2":1,"1":2},100)=={"1":67,"2":33}


def test_weekly_goals_rotate_and_count_only_matching_active_watches():
    weeks=["2026-09-21","2026-09-28","2026-10-05","2026-10-12","2026-10-19"]
    assert [challenge_for_week(key,2)["kind"] for key in weeks]==[
        "episodes","movies","anime","all","episodes"]
    start=datetime(2026,9,28,tzinfo=timezone.utc)
    end=datetime(2026,10,5,tzinfo=timezone.utc)
    users={"41":{"progression":{"xp_events":[
        {"at":"2026-09-29T12:00:00Z","event_key":"film","media_type":"movie"},
        {"at":"2026-09-29T12:00:00Z","event_key":"anime-film","media_type":"anime_movie"},
        {"at":"2026-09-29T12:00:00Z","event_key":"anime-ep","media_type":"anime_episode"},
        {"at":"2026-09-29T12:00:00Z","event_key":"film","media_type":"movie"},
        {"at":"2026-10-05T12:00:00Z","event_key":"late","media_type":"movie"},
    ]}}}
    assert watch_contributions(users,["41"],start,end,"movies")=={"41":2}
    assert watch_contributions(users,["41"],start,end,"anime")=={"41":2}
    assert watch_contributions(users,["41"],start,end,"all")=={"41":3}


def test_existing_episode_week_keeps_its_goal_after_rotation():
    async def scenario():
        with tempfile.TemporaryDirectory() as directory:
            previous=storage_module.DATA_PATH
            storage_module.DATA_PATH=f"{directory}/store.json"
            try:
                store=storage_module.Storage()
                await store.link_user("123","41","token",None,"user","2026-09-27T00:00:00Z")
                start=datetime(2026,9,28,tzinfo=timezone.utc)
                end=datetime(2026,10,5,tzinfo=timezone.utc)
                store._data["guilds"]["123"]["community_challenges"]={
                    "2026-09-28":{"start":start.isoformat(),"end":end.isoformat(),"target":25,
                                   "pool":7500,"members":["41"],"awards":{},"notified_awards":{}}
                }
                state=await store.get_community_state("123","2026-09-28",start,end,start)
                assert (state["kind"],state["target"],state["pool"])==("episodes",25,7500)
                new_start=end
                new_end=datetime(2026,10,12,tzinfo=timezone.utc)
                new_state=await store.get_community_state("123","2026-10-05",new_start,new_end,new_start)
                assert new_state["kind"]=="anime"
            finally:
                storage_module.DATA_PATH=previous
    asyncio.run(scenario())


def test_pool_payout_is_idempotent_and_reverses_when_goal_is_lost():
    async def scenario():
        with tempfile.TemporaryDirectory() as directory:
            previous=storage_module.DATA_PATH
            storage_module.DATA_PATH=f"{directory}/store.json"
            try:
                store=storage_module.Storage()
                for uid in ("41","42"):
                    await store.link_user("123",uid,"token",None,uid,"2026-09-21T00:00:00Z")
                week="2026-09-21"
                start=datetime(2026,9,21,tzinfo=timezone.utc)
                end=datetime(2026,9,28,tzinfo=timezone.utc)
                now=datetime(2026,9,26,tzinfo=timezone.utc)
                first=await store.get_community_state("123",week,start,end,now)
                assert first["target"]==40 and first["pool"]==12000
                for uid,count in (("41",25),("42",15)):
                    await store.get_progression(uid)
                    progression=store._data["users"][uid]["progression"]
                    progression["xp_events"]=[
                        {"at":"2026-09-23T12:00:00Z","event_key":f"episode:{uid}:{number}","media_type":"episode","amount":100}
                        for number in range(count)
                    ]
                reached=await store.get_community_state("123",week,start,end,now)
                assert reached["status"]=="goal_reached" and not reached["awards"]
                after=datetime(2026,9,29,tzinfo=timezone.utc)
                paid=await store.get_community_state("123",week,start,end,after)
                assert paid["awards"]=={"41":7500,"42":4500}
                notice=paid["pending_notifications"]
                assert len(notice)==1
                assert notice[0]["initial"] and notice[0]["members"]==["41","42"]
                assert notice[0]["deltas"]=={"41":7500,"42":4500}
                assert (await store.get_progression("41"))["xp"]==7500
                repeated=await store.get_community_state("123",week,start,end,after)
                assert repeated["changes"]==[]
                assert len(repeated["pending_notifications"])==1
                await store.ack_community_notification("123",week,paid["awards"])
                assert (await store.get_community_state("123",week,start,end,after))["pending_notifications"]==[]
                store._data["users"]["42"]["progression"]["xp_events"].pop()
                reversed_state=await store.get_community_state("123",week,start,end,after)
                assert reversed_state["status"]=="missed" and reversed_state["awards"]=={}
                assert reversed_state["pending_notifications"][0]["deltas"]=={"41":-7500,"42":-4500}
                assert (await store.get_progression("41"))["xp"]==0
                assert (await store.get_progression("42"))["community_rewards"]=={}
            finally:
                storage_module.DATA_PATH=previous
    asyncio.run(scenario())
