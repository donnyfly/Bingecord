import asyncio
from datetime import datetime, timezone
import tempfile

import storage as storage_module
from community import community_week, split_pool


def test_week_boundaries_and_exact_pool_split():
    key,start,end=community_week(datetime(2026,9,26,10,tzinfo=timezone.utc),"Asia/Singapore")
    assert key=="2026-09-21"
    assert start==datetime(2026,9,20,16,tzinfo=timezone.utc)
    assert end==datetime(2026,9,27,16,tzinfo=timezone.utc)
    assert split_pool({"2":1,"1":2},100)=={"1":67,"2":33}


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
                assert (await store.get_progression("41"))["xp"]==7500
                repeated=await store.get_community_state("123",week,start,end,after)
                assert repeated["changes"]==[]
                store._data["users"]["42"]["progression"]["xp_events"].pop()
                reversed_state=await store.get_community_state("123",week,start,end,after)
                assert reversed_state["status"]=="missed" and reversed_state["awards"]=={}
                assert (await store.get_progression("41"))["xp"]==0
                assert (await store.get_progression("42"))["community_rewards"]=={}
            finally:
                storage_module.DATA_PATH=previous
    asyncio.run(scenario())
