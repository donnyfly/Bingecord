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
