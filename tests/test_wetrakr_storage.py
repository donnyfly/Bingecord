import asyncio

import storage as storage_module


def test_wetrakr_link_preserves_simkl_and_unlink_is_independent(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module,"DATA_PATH",str(tmp_path/"store.json"))
        store=storage_module.Storage()
        await store.link_user("123","42","simkl-token",None,"simkl-user","2026-09-28T00:00:00Z")
        await store.link_wetrakr("123","42",{"access_token":"we-token","refresh_token":"we-refresh",
                                           "expires_at":"2026-10-05T00:00:00Z"},
                                 {"id":19,"username":"we-user"})
        user=await store.get_user("42")
        assert user["simkl_token"]=="simkl-token"
        assert user["wetrakr"]["account_id"]==19
        assert len(await store.get_poll_targets("123"))==1
        await store.unlink_user("123","42")
        user=await store.get_user("42")
        assert user["simkl_token"] is None
        assert user["wetrakr"]["access_token"]=="we-token"
        assert await store.get_poll_targets("123")==[]
        await store.link_user("123","42","simkl-again",None,"simkl-user","2026-09-28T00:00:00Z")
        assert (await store.get_user("42"))["wetrakr"]["access_token"]=="we-token"
        await store.unlink_wetrakr("123","42")
        assert (await store.get_user("42"))["simkl_token"]=="simkl-again"
        assert (await store.get_user("42"))["wetrakr"] is None
        assert len(await store.get_poll_targets("123"))==1
    asyncio.run(run())


def test_wetrakr_only_link_is_not_polled_as_simkl(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module,"DATA_PATH",str(tmp_path/"store.json"))
        store=storage_module.Storage()
        await store.link_wetrakr("123","42",{"access_token":"we-token","refresh_token":"we-refresh"},
                                 {"id":19,"username":"we-user"})
        assert await store.get_poll_targets("123")==[]
        assert await store.unlink_wetrakr("123","42")
        assert (await store.get_user("42"))["wetrakr"] is None
    asyncio.run(run())


def test_started_series_history_ignores_colliding_provider_ids(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module, "DATA_PATH", str(tmp_path / "store.json"))
        store = storage_module.Storage()
        await store.link_wetrakr("1", "2", {"access_token": "a", "refresh_token": "r"}, {"id": 7})
        await store.reconcile_wetrakr_plays("1", "2", [{
            "source_event_id": "old-play", "media_type": "episode", "title": "Other Show",
            "item_key": "wetrakr:episode:25:1:1", "show_id": 25,
            "show_ids": {"tmdb": 111}, "ids": {"tmdb": 111},
            "watched_at": "2026-09-28T12:00:00Z"}])
        # A numeric provider ID collision must not hide the first episode of
        # a different series when its external IDs explicitly conflict.
        assert not await store.has_wetrakr_show_history("2", 25, {"tmdb": 222})
        assert await store.has_wetrakr_show_history("2", 25, {"tmdb": 111})
    asyncio.run(run())


def test_started_series_history_excludes_currently_delivered_play(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module, "DATA_PATH", str(tmp_path / "store.json"))
        store = storage_module.Storage()
        await store.link_wetrakr("1", "2", {"access_token": "a", "refresh_token": "r"}, {"id": 7})
        await store.reconcile_wetrakr_plays("1", "2", [{
            "source_event_id": "current-play", "media_type": "episode", "title": "New Show",
            "item_key": "wetrakr:episode:25:1:1", "show_id": 25,
            "show_ids": {"tmdb": 111}, "ids": {"tmdb": 111},
            "watched_at": "2026-09-28T12:00:00Z"}])
        # The journal entry can arrive just after the seed import. Its own
        # baseline row must not count as an earlier episode of the series.
        assert not await store.has_wetrakr_show_history(
            "2", 25, {"tmdb": 111}, exclude_event_id="current-play")
        assert await store.has_wetrakr_show_history("2", 25, {"tmdb": 111})
    asyncio.run(run())


def test_different_episode_of_same_show_keeps_its_xp(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module, "DATA_PATH", str(tmp_path / "store.json"))
        store = storage_module.Storage()
        await store.link_user("1", "2", "token", None, "simkl", "2026-09-28T00:00:00Z")
        await store.link_wetrakr("1", "2", {"access_token": "a", "refresh_token": "r"}, {"id": 7})
        simkl_key = "episode:series:shows:42:1:1:2026-09-28T00:00:00Z"
        await store.award_watch_xp("2", simkl_key, "episode", "Same Show",
                                   "2026-09-28T00:00:00Z", 100)
        first = await store.reconcile_wetrakr_plays("1", "2", [{
            "source_event_id": "p2", "media_type": "episode", "title": "Same Show",
            "item_key": "wetrakr:episode:70:1:2", "watched_at": "2026-09-28T01:00:00Z"}])
        assert first["xp"] == 100
        second = await store.reconcile_wetrakr_plays("1", "2", [{
            "source_event_id": "p1", "media_type": "episode", "title": "Same Show",
            "item_key": "wetrakr:episode:70:1:1", "watched_at": "2026-09-28T00:02:00Z"}])
        assert second["xp"] == 0
    asyncio.run(run())


def test_existing_wetrakr_movie_reclassifies_without_extra_xp(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module, "DATA_PATH", str(tmp_path / "store.json"))
        store = storage_module.Storage()
        await store.link_wetrakr("1", "2", {"access_token": "a", "refresh_token": "r"}, {"id": 7})
        await store.reconcile_wetrakr_plays("1", "2", [{
            "source_event_id": "p1", "media_type": "movie", "title": "Anime Film",
            "item_key": "wetrakr:movie:70", "ids": {"tmdb": 90},
            "watched_at": "2026-09-28T01:00:00Z"}])
        before = (await store.get_progression("2"))["lifetime_xp"]
        await store.classify_wetrakr_movie("2", 70, True)
        stats = await store.get_statistics("1", "2")
        assert stats["movies_watched"] == 1
        assert stats["anime_movies_watched"] == 1
        assert (await store.get_progression("2"))["lifetime_xp"] == before
        await store.classify_wetrakr_movie("2", 70, True)
        assert (await store.get_statistics("1", "2"))["anime_movies_watched"] == 1
    asyncio.run(run())


def test_selected_wetrakr_reset_reimports_without_clearing_shared_xp(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module, "DATA_PATH", str(tmp_path / "store.json"))
        store = storage_module.Storage()
        await store.link_wetrakr("1", "2", {"access_token": "a", "refresh_token": "r"}, {"id": 7})
        play = {"source_event_id": "p1", "media_type": "movie", "title": "Film",
                "item_key": "wetrakr:movie:70", "watched_at": "2026-09-28T01:00:00Z"}
        await store.reconcile_wetrakr_plays("1", "2", [play])
        before = (await store.get_progression("2"))["lifetime_xp"]
        assert await store.reset_user_tracking("1", "2", "2026-09-29T00:00:00Z")
        target = (await store.get_provider_targets("wetrakr", "1", active_only=True))[0]
        assert not target["guild_user_data"]["wetrakr_sync"]["seeded"]
        assert (await store.get_progression("2"))["lifetime_xp"] == before
        await store.reconcile_wetrakr_plays("1", "2", [play], complete=True)
        assert (await store.get_statistics("1", "2"))["movies_watched"] == 1
        assert (await store.get_progression("2"))["lifetime_xp"] == before
    asyncio.run(run())
