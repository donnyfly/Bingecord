import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import trackerbot.core.storage as storage_module
from trackerbot.core.providers import WatchChange
from trackerbot.integrations.wetrakr_auth import WeTrakrAuth


class FakeClient:
    def __init__(self):
        self.calls = []

    async def refresh_token(self, old):
        self.calls.append(old)
        return {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 604800}


def test_provider_targets_keep_accounts_separate(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module, "DATA_PATH", str(tmp_path / "store.json"))
        store = storage_module.Storage()
        await store.link_user("123", "42", "simkl-token", None, "simkl-user", "2026-09-28T00:00:00Z")
        await store.link_wetrakr("123", "42", {"access_token": "access", "refresh_token": "refresh"},
                                 {"id": 19, "username": "we-user"})
        assert [x["discord_user_id"] for x in await store.get_provider_targets("simkl")] == ["42"]
        assert [x["discord_user_id"] for x in await store.get_provider_targets("wetrakr")] == ["42"]
        assert (await store.get_provider_targets("wetrakr"))[0]["guild_user_data"]["wetrakr_sync"]["seeded"] is False
        assert await store.get_poll_targets() == await store.get_provider_targets("simkl")
        await store.link_wetrakr("456", "99", {"access_token": "other", "refresh_token": "other-refresh"},
                                 {"id": 20, "username": "another-user"})
        assert [x["discord_user_id"] for x in await store.get_provider_targets("wetrakr")] == ["42", "99"]
        assert (await store.get_user("42"))["wetrakr"]["access_token"] == "access"
        assert (await store.get_user("99"))["wetrakr"]["access_token"] == "other"
    asyncio.run(run())


def test_refresh_rotates_per_user_and_does_not_reuse_token(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module, "DATA_PATH", str(tmp_path / "store.json"))
        store = storage_module.Storage()
        await store.link_wetrakr("123", "42", {"access_token": "old", "refresh_token": "old-refresh",
                                             "expires_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()},
                                 {"id": 19})
        client = FakeClient()
        auth = WeTrakrAuth(client, store)
        assert await asyncio.gather(auth.access_token("42"), auth.access_token("42")) == ["new-access", "new-access"]
        assert client.calls == ["old-refresh"]
        assert (await store.get_user("42"))["wetrakr"]["refresh_token"] == "new-refresh"
        assert not await store.rotate_wetrakr_tokens("42", 19, "old-refresh",
                                                     {"access_token": "stale", "refresh_token": "stale", "expires_at": "now"})
        with pytest.raises(ValueError):
            await auth.access_token("99")
    asyncio.run(run())


def test_account_switch_requires_unlink_in_all_servers(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module, "DATA_PATH", str(tmp_path / "store.json"))
        store = storage_module.Storage()
        await store.link_wetrakr("123", "42", {"access_token": "first", "refresh_token": "first-r"}, {"id": 19})
        with pytest.raises(ValueError):
            await store.link_wetrakr("456", "42", {"access_token": "second", "refresh_token": "second-r"}, {"id": 20})
        assert (await store.get_user("42"))["wetrakr"]["access_token"] == "first"
        assert (await store.get_provider_targets("wetrakr", "456")) == []
    asyncio.run(run())


def test_watch_change_scopes_same_play_id_by_account_and_provider():
    base = dict(event_id="123", change_id="456", action="added", media_type="episode",
                watched_at=None, title_ids={"tmdb": 777})
    first = WatchChange(provider="wetrakr", account_id="19", **base)
    second = WatchChange(provider="wetrakr", account_id="20", **base)
    third = WatchChange(provider="simkl", account_id="19", **base)
    assert len({first.source_key, second.source_key, third.source_key}) == 3
