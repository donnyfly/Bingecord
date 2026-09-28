import asyncio
from datetime import datetime, timezone

import storage as storage_module


def test_community_uses_selected_source_and_preserves_completed_week(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module,"DATA_PATH",str(tmp_path/"store.json"))
        store=storage_module.Storage()
        await store.link_user("1","2","token",None,"simkl","2026-09-28T00:00:00Z")
        await store.link_wetrakr("1","2",{"access_token":"a","refresh_token":"r"},{"id":4})
        await store.set_activity_provider("1","2","wetrakr")
        start=datetime(2026,9,28,tzinfo=timezone.utc)
        end=datetime(2026,10,5,tzinfo=timezone.utc)
        await store.get_community_state("1","2026-09-28",start,end,start)
        for number in range(3):
            await store.reconcile_wetrakr_plays("1","2",[{
                "source_event_id":f"play-{number}","media_type":"movie",
                "title":f"Movie {number}","item_key":f"wetrakr:movie:{number}",
                "watched_at":f"2026-09-29T0{number}:00:00Z"}])
        before=await store.get_community_state("1","2026-09-28",start,end,end)
        assert before["contributions"]=={"2":3}
        assert before["awards"].get("2",0)>0
        await store.set_activity_provider("1","2","simkl")
        after=await store.get_community_state("1","2026-09-28",start,end,end)
        assert after["awards"]==before["awards"]
        assert (await store.get_progression("2"))["community_rewards"]
    asyncio.run(run())
