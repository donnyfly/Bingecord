import asyncio

import pytest

import storage as storage_module
from providers import ProviderAccount, ProviderManifest, ProviderPage, ProviderRegistry, WatchChange
from tracker_mapping import WatchIdentity, match_reason, same_watch


def watch(kind="anime_episode", title="English Title", season=1, episode=892,
          ids=None, at="2026-09-28T12:00:00Z"):
    return WatchIdentity(kind, title, at, season, episode, ids or {})


def test_verified_ids_match_different_titles_but_conflicts_do_not():
    simkl = watch(ids={"tmdb": 37854, "simkl": 1})
    wetrakr = watch(title="Original Japanese Title", ids={"tmdb": "37854"})
    assert match_reason(simkl, wetrakr) == "verified_id"
    assert not same_watch(simkl, watch(ids={"tmdb": 99}))
    assert not same_watch(simkl, watch(episode=893, ids={"tmdb": 37854}))
    assert not same_watch(simkl, watch(ids={"tvdb": 81797}))
    assert match_reason(watch(ids={}), watch(ids={"tmdb": 37854})) == "legacy_title"


def test_movie_rewatch_window_and_media_kind():
    original = watch("movie", "Film", None, None, {"imdb": "tt123"})
    same = watch("anime_movie", "Film (English)", None, None,
                 {"imdb": "tt123"}, "2026-09-28T12:04:59Z")
    assert same_watch(original, same)
    assert not same_watch(original, watch("movie", "Film", None, None,
                                        {"imdb": "tt123"}, "2026-09-28T12:05:01Z"))
    assert not same_watch(original, watch("episode", "Film", 1, 1, {"imdb": "tt123"}))


def test_registry_requires_history_changes_and_unique_names():
    good = ProviderManifest("future_tracker", "Future Tracker", True, True, True,
                            True, True, True, True,
                            "https://example.com/u/{id}", "https://example.com/t/{id}")
    good.validate_tracker()
    with pytest.raises(ValueError, match="history and changes"):
        ProviderManifest("metadata_only", "Metadata", False, False, False,
                         False, False, False, True,
                         "https://example.com/u", "https://example.com/t").validate_tracker()
    with pytest.raises(ValueError, match="HTTPS"):
        ProviderManifest("unsafe", "Unsafe", True, True, True, True, True, True,
                         True, "http://example.com", "https://example.com").validate_tracker()
    assert ProviderRegistry().manifests() == ()


def test_provider_page_requires_scoped_stable_observations():
    account = ProviderAccount("future_tracker", "account-1", "viewer")
    entry = WatchChange("future_tracker", "account-1", "play-1", "change-1",
                        "added", "episode", "2026-09-28T12:00:00Z",
                        {"tmdb": 123}, season=1, episode=3)
    ProviderPage((entry,), "cursor-2").validate(account)
    with pytest.raises(ValueError, match="another account"):
        ProviderPage((entry,), None).validate(ProviderAccount("future_tracker", "account-2", "viewer"))
    with pytest.raises(ValueError, match="duplicate"):
        ProviderPage((entry, entry), None).validate(account)


def test_cross_provider_xp_uses_external_ids_and_preserves_rewatch(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module, "DATA_PATH", str(tmp_path / "store.json"))
        store = storage_module.Storage()
        await store.link_user("1", "2", "token", None, "user", "2026-09-28T00:00:00Z")
        await store.link_wetrakr("1", "2", {"access_token": "a", "refresh_token": "r"}, {"id": 7})
        key = "anime_episode:series:anime:1:1:892:2026-09-28T12:00:00Z"
        await store.award_watch_xp("2", key, "anime_episode", "English Title",
                                   "2026-09-28T12:00:00Z", 100, {"tmdb": 37854})
        base = {"media_type": "anime_episode", "title": "Japanese Title",
                "item_key": "wetrakr:episode:70:1:892", "ids": {"tmdb": 37854}}
        first = await store.reconcile_wetrakr_plays("1", "2", [{**base,
            "source_event_id": "p1", "watched_at": "2026-09-28T12:00:01Z"}])
        assert first["xp"] == 0
        audit = await store.get_mapping_audit("2")
        assert audit["verified"] == 1 and audit["double_awards"] == 0
        index = await store.refresh_watch_occurrences("2")
        assert len(index) == 1
        assert next(iter(index.values()))["match_reason"] == "verified_id"
        restored = storage_module.Storage()
        assert (await restored.get_progression("2"))["watch_occurrences"] == index
        second = await store.reconcile_wetrakr_plays("1", "2", [{**base,
            "source_event_id": "p2", "watched_at": "2026-09-29T12:00:00Z"}])
        assert second["xp"] == 100
        conflicting = await store.reconcile_wetrakr_plays("1", "2", [{**base,
            "source_event_id": "p3", "title": "English Title", "ids": {"tmdb": 999},
            "watched_at": "2026-09-28T12:00:01Z"}])
        assert conflicting["xp"] == 100
        assert (await store.get_progression("2"))["lifetime_xp"] >= 300
    asyncio.run(run())


def test_simkl_removal_transfers_verified_award_to_wetrakr(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module, "DATA_PATH", str(tmp_path / "store.json"))
        store = storage_module.Storage()
        await store.link_user("1", "2", "token", None, "user", "2026-09-28T00:00:00Z")
        await store.link_wetrakr("1", "2", {"access_token": "a", "refresh_token": "r"}, {"id": 7})
        simkl_key = "anime_episode:series:anime:1:1:892:2026-09-28T12:00:00Z"
        await store.award_watch_xp("2", simkl_key, "anime_episode", "English Title",
                                   "2026-09-28T12:00:00Z", 100, {"tmdb": 37854})
        await store.reconcile_wetrakr_plays("1", "2", [{"source_event_id": "p1",
            "media_type": "anime_episode", "title": "Japanese Title",
            "item_key": "wetrakr:episode:70:1:892", "ids": {"tmdb": 37854},
            "watched_at": "2026-09-28T12:00:01Z"}])
        result = await store.reconcile_watch_xp("2", set(), {"anime_episode"})
        assert result["amount"] == 0
        assert (await store.get_progression("2"))["lifetime_xp"] == 100
        await store.reconcile_wetrakr_plays("1", "2", [{"source_event_id": "p1",
            "media_type": "anime_episode", "removed": True}])
        assert (await store.get_progression("2"))["lifetime_xp"] == 0
    asyncio.run(run())


def test_wetrakr_account_relink_does_not_delete_prior_account_plays(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module, "DATA_PATH", str(tmp_path / "store.json"))
        store = storage_module.Storage()
        await store.link_wetrakr("1", "2", {"access_token": "a", "refresh_token": "r"}, {"id": 7})
        old = {"source_event_id": "shared-play-id", "media_type": "movie",
               "item_key": "wetrakr:movie:70", "title": "First Film",
               "watched_at": "2026-09-28T12:00:00Z"}
        await store.reconcile_wetrakr_plays("1", "2", [old], complete=True, account_id=7)
        first_xp = (await store.get_progression("2"))["lifetime_xp"]
        assert await store.unlink_wetrakr("1", "2")
        await store.link_wetrakr("1", "2", {"access_token": "b", "refresh_token": "s"}, {"id": 8})
        new = {**old, "item_key": "wetrakr:movie:80", "title": "Second Film",
               "watched_at": "2026-09-29T12:00:00Z"}
        await store.reconcile_wetrakr_plays("1", "2", [new], complete=True, account_id=8)
        progression = await store.get_progression("2")
        assert len(progression["wetrakr_plays"]) == 2
        assert progression["lifetime_xp"] >= first_xp + 300
        assert len(await store.get_wetrakr_plays("2")) == 1
        await store.reconcile_wetrakr_plays("1", "2", [], complete=True, account_id=8)
        progression = await store.get_progression("2")
        assert len(progression["wetrakr_plays"]) == 1
        assert progression["lifetime_xp"] >= first_xp
        with pytest.raises(ValueError, match="account changed"):
            await store.reconcile_wetrakr_plays("1", "2", [old], account_id=7)
    asyncio.run(run())


def test_reimport_corrects_old_anime_coordinates_without_reawarding_xp(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module, "DATA_PATH", str(tmp_path / "store.json"))
        store = storage_module.Storage()
        await store.link_user("1", "2", "token", None, "user", "2026-09-28T00:00:00Z")
        await store.link_wetrakr("1", "2", {"access_token": "a", "refresh_token": "r"}, {"id": 7})
        await store.award_watch_xp("2", "anime_episode:series:anime:1:1:892:2026-09-28T12:00:00Z",
                                   "anime_episode", "One Piece", "2026-09-28T12:00:00Z", 100,
                                   {"tmdb": 37854})
        old = {"source_event_id": "p1", "media_type": "anime_episode", "title": "One Piece",
               "item_key": "wetrakr:episode:70:2019:26", "ids": {"tmdb": 37854},
               "watched_at": "2026-09-28T12:00:00Z"}
        await store.reconcile_wetrakr_plays("1", "2", [old])
        before = (await store.get_progression("2"))["lifetime_xp"]
        assert (await store.get_mapping_audit("2"))["verified"] == 0
        result = await store.reconcile_wetrakr_plays("1", "2", [{**old,
            "item_key": "wetrakr:episode:70:1:892"}], complete=True)
        assert result["xp"] == 0
        assert (await store.get_mapping_audit("2"))["verified"] == 1
        assert (await store.get_mapping_audit("2"))["double_awards"] == 1
        assert (await store.get_progression("2"))["lifetime_xp"] == before
    asyncio.run(run())


def test_mapping_command_is_read_only_and_private(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from test_notification_preview import bot

    async def run():
        response = SimpleNamespace(send_message=AsyncMock())
        interaction = SimpleNamespace(guild=SimpleNamespace(id=123),
                                      user=SimpleNamespace(id=42), response=response)
        monkeypatch.setattr(bot.storage, "get_user", AsyncMock(return_value={"wetrakr": {"account_id": 7}}))
        audit = AsyncMock(return_value={"verified": 1, "legacy": 0, "unpaired": 2,
                                        "review": 0, "double_awards": 0})
        monkeypatch.setattr(bot.storage, "get_mapping_audit", audit)
        await bot.tracker_mapping.callback(interaction)
        assert response.send_message.await_args.kwargs["ephemeral"]
        assert "Verified cross-tracker matches: **1**" in response.send_message.await_args.args[0]
        audit.assert_awaited_once_with("42")
    asyncio.run(run())
