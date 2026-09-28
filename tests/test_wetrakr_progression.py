import asyncio

import storage as storage_module
from wetrakr_sync import WeTrakrSync


class Auth:
    async def access_token(self, uid):
        return "token"


class Client:
    def __init__(self, pages):
        self.pages = pages
        self.rows = []

    async def last_activities(self, token):
        return {"all": "2026-09-28T00:00:00Z"}

    async def compact_history(self, token, target):
        yield self.pages.get(target, [])

    async def journal(self, token, since, *, category):
        return self.rows


def test_wetrakr_import_edit_remove_and_source_switch(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module, "DATA_PATH", str(tmp_path / "store.json"))
        store = storage_module.Storage()
        await store.link_wetrakr("123", "42", {"access_token": "a", "refresh_token": "r"}, {"id": 19})
        client = Client({"movies": [{"type": "movie", "id": 50, "play_id": "play-1",
                                     "watched_at": "2024-09-27T12:00:00Z"}]})
        sync = WeTrakrSync(client, Auth(), store)

        async def resolve(play):
            return {**play, "title": "Film", "item_key": "movie:50"}

        async def deliver(*_):
            return True

        target = (await store.get_provider_targets("wetrakr", "123", active_only=True))[0]
        assert await sync.poll(target, deliver, resolve_play=resolve) == 0
        assert (await store.get_statistics("123", "42"))["movies_watched"] == 1
        assert (await store.get_progression("42"))["lifetime_xp"] == 300
        client.rows = [{"entry_id": "edit", "action_at": "2026-09-28T00:01:00Z",
                        "category": "watched", "status": "updated", "type": "movie",
                        "id": 50, "play_id": "play-1", "watched_at": "2024-09-27T13:00:00Z"}]
        target = (await store.get_provider_targets("wetrakr", "123", active_only=True))[0]
        await sync.poll(target, deliver, resolve_play=resolve)
        assert (await store.get_statistics("123", "42"))["movies_watched"] == 1
        assert (await store.get_progression("42"))["lifetime_xp"] == 300
        client.rows = [{"entry_id": "delete", "action_at": "2026-09-28T00:02:00Z",
                        "category": "watched", "status": "removed", "type": "movie",
                        "id": 50, "play_id": "play-1"}]
        target = (await store.get_provider_targets("wetrakr", "123", active_only=True))[0]
        await sync.poll(target, deliver, resolve_play=resolve)
        assert (await store.get_statistics("123", "42"))["movies_watched"] == 0
        assert (await store.get_progression("42"))["lifetime_xp"] == 0
        # A fresh compact import after switching must not credit a play twice.
        client.pages["movies"] = [{"type": "movie", "id": 50, "play_id": "play-2",
                                   "watched_at": "2024-09-27T14:00:00Z"}]
        await store.set_activity_provider("123", "42", "wetrakr")
        target = (await store.get_provider_targets("wetrakr", "123", active_only=True))[0]
        await store.reconcile_wetrakr_plays("123", "42", [await resolve({"source_event_id": "play-2",
                                                 "media_type": "movie", "watched_at": "2024-09-27T14:00:00Z"})])
        await sync.poll(target, deliver, resolve_play=resolve)
        assert (await store.get_progression("42"))["lifetime_xp"] == 300

    asyncio.run(run())


def test_existing_simkl_watch_is_not_credited_twice(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module, "DATA_PATH", str(tmp_path / "store.json"))
        store = storage_module.Storage()
        await store.link_user("123", "42", "token", None, "user", "2026-09-28T00:00:00Z")
        await store.link_wetrakr("123", "42", {"access_token": "a", "refresh_token": "r"}, {"id": 19})
        await store.award_watch_xp("42", "movie:movies:simkl-id:2024-09-27T12:00:00Z",
                                   "movie", "Film", "2024-09-27T12:00:00Z", 300)
        await store.reconcile_wetrakr_plays("123", "42", [{"source_event_id": "we-1",
            "media_type": "movie", "title": "Film", "watched_at": "2024-09-27T12:00:01Z"}])
        assert (await store.get_progression("42"))["lifetime_xp"] == 300
        await store.reconcile_wetrakr_plays("123", "42", [{"source_event_id": "we-1",
            "media_type": "movie", "title": "Film", "removed": True}])
        assert (await store.get_progression("42"))["lifetime_xp"] == 300

    asyncio.run(run())


def test_simkl_deletion_preserves_confirmed_wetrakr_award(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module, "DATA_PATH", str(tmp_path / "store.json"))
        store = storage_module.Storage()
        await store.link_user("123", "42", "token", None, "user", "2024-09-28T00:00:00Z")
        await store.link_wetrakr("123", "42", {"access_token": "a", "refresh_token": "r"}, {"id": 19})
        simkl_key = "movie:movies:100:2024-09-27T12:00:00Z"
        await store.award_watch_xp("42", simkl_key, "movie", "Film", "2024-09-27T12:00:00Z", 300)
        await store.reconcile_wetrakr_plays("123", "42", [{"source_event_id": "we-1",
            "media_type": "movie", "title": "Film", "watched_at": "2024-09-27T12:00:01Z"}])
        result = await store.reconcile_watch_xp("42", set(), {"movie"})
        assert result["amount"] == 0
        progression = await store.get_progression("42")
        assert progression["lifetime_xp"] == 300
        assert len([e for e in progression["xp_events"] if e["media_type"] == "movie"]) == 1
        await store.reconcile_wetrakr_plays("123", "42", [{"source_event_id": "we-1",
            "media_type": "movie", "title": "Film", "removed": True}])
        assert (await store.get_progression("42"))["lifetime_xp"] == 0

    asyncio.run(run())


def test_switching_back_to_simkl_transfers_existing_wetrakr_award(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module, "DATA_PATH", str(tmp_path / "store.json"))
        store = storage_module.Storage()
        await store.link_user("123", "42", "token", None, "user", "2024-09-28T00:00:00Z")
        await store.link_wetrakr("123", "42", {"access_token": "a", "refresh_token": "r"}, {"id": 19})
        await store.reconcile_wetrakr_plays("123", "42", [{"source_event_id": "we-1",
            "media_type": "movie", "title": "Film", "watched_at": "2024-09-27T12:00:00Z"}])
        result = await store.award_watch_xp("42", "movie:movies:100:2024-09-27T12:01:00Z",
                                            "movie", "Film", "2024-09-27T12:01:00Z", 300)
        assert not result["awarded"]
        assert (await store.get_progression("42"))["lifetime_xp"] == 300
        await store.reconcile_watch_xp("42", set(), {"movie"})
        assert (await store.get_progression("42"))["lifetime_xp"] == 300

    asyncio.run(run())


def test_deleted_watch_revokes_earned_challenge_reward(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module, "DATA_PATH", str(tmp_path / "store.json"))
        store = storage_module.Storage()
        await store.link_wetrakr("123", "42", {"access_token": "a", "refresh_token": "r"}, {"id": 19})
        await store.reconcile_wetrakr_plays("123", "42", [{"source_event_id": "we-1",
            "media_type": "movie", "title": "Film", "watched_at": "2026-09-27T12:00:00Z"}])
        before = await store.get_progression("42")
        assert before["lifetime_xp"] >= 300
        await store.reconcile_wetrakr_plays("123", "42", [{"source_event_id": "we-1",
            "media_type": "movie", "removed": True}])
        after = await store.get_progression("42")
        assert after["lifetime_xp"] == 0
        assert not after["challenge_completions"]

    asyncio.run(run())


def test_statistics_follow_selected_provider_while_xp_stays_shared(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module, "DATA_PATH", str(tmp_path / "store.json"))
        store=storage_module.Storage()
        await store.link_user("123","42","token",None,"simkl-user","2026-09-28T00:00:00Z")
        await store.record_watch("123","42","movie","SIMKL Film","movies:100",
                                 "2026-09-27T10:00:00Z")
        await store.award_watch_xp("42","movie:movies:100:2026-09-27T10:00:00Z",
                                   "movie","SIMKL Film","2026-09-27T10:00:00Z",300)
        await store.link_wetrakr("123","42",{"access_token":"a","refresh_token":"r"},
                                 {"id":19,"user":{"username":"we-user"}})
        await store.reconcile_wetrakr_plays("123","42",[{
            "source_event_id":"we-1","media_type":"episode","title":"WeTrakr Show",
            "show_id":55,"ids":{"tmdb":555},"item_key":"wetrakr:episode:55:1:1",
            "watched_at":"2026-09-27T11:00:00Z",
        }])

        simkl_stats=await store.get_statistics("123","42")
        assert simkl_stats["movies_watched"] == 1
        assert simkl_stats["episodes_watched"] == 0

        assert await store.set_activity_provider("123","42","wetrakr")
        wetrakr_stats=await store.get_statistics("123","42")
        assert wetrakr_stats["movies_watched"] == 0
        assert wetrakr_stats["episodes_watched"] == 1

        guild_row=(await store.get_guild_statistics("123"))[0]
        board_row=(await store.get_guild_leaderboard_snapshot("123"))[0]
        assert guild_row["statistics"]["episodes_watched"] == 1
        assert board_row["episodes"] == 1
        assert guild_row["simkl_username"] == "we-user"

        shared=await store.get_progression("42")
        assert shared["lifetime_xp"] == 400
    asyncio.run(run())
