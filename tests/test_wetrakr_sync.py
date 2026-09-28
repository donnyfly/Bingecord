import asyncio

import storage as storage_module
from wetrakr_sync import WeTrakrSync


class Auth:
    async def access_token(self, uid):
        return "user-token"


class Client:
    def __init__(self):
        self.rows = []
        self.calls = []
        self.latest = "2026-09-28T02:00:00Z"

    async def last_activities(self, token):
        self.calls.append("activities")
        return {"all": self.latest}

    async def compact_history(self, token, target):
        self.calls.append(("seed", target))
        yield []

    async def journal(self, token, since, *, category):
        self.calls.append(("journal", since))
        return list(self.rows)


def test_baseline_journal_retry_and_source_switch(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module, "DATA_PATH", str(tmp_path / "store.json"))
        store = storage_module.Storage()
        await store.link_user("123", "42", "simkl-token", None, "simkl-user", "2026-09-28T00:00:00Z")
        await store.link_wetrakr("123", "42", {"access_token": "a", "refresh_token": "r"}, {"id": 19})
        assert len(await store.get_poll_targets("123")) == 1
        assert await store.set_activity_provider("123", "42", "wetrakr")
        assert await store.get_poll_targets("123") == []
        target = (await store.get_provider_targets("wetrakr", "123", active_only=True))[0]
        client = Client()
        sync = WeTrakrSync(client, Auth(), store)
        async def deliver(change, row):
            raise AssertionError("Baseline must never post historical watches")
        assert await sync.poll(target, deliver) == 0
        assert client.calls == ["activities", ("seed", "movies"), ("seed", "episodes")]
        target = (await store.get_provider_targets("wetrakr", "123", active_only=True))[0]
        client.latest = "2026-09-28T03:00:00Z"
        client.rows = [
            {"entry_id": "one", "action_at": "2026-09-28T03:00:00Z", "category": "watched",
             "status": "added", "type": "movie", "play_id": "play-1", "id": 50, "title": "Film"},
            {"entry_id": "two", "action_at": "2026-09-28T03:00:00Z", "category": "watched",
             "status": "added", "type": "movie", "play_id": "play-2", "id": 51, "title": "Film 2"},
        ]
        attempts = []
        async def sometimes_fail(change, row):
            attempts.append(row["entry_id"])
            return row["entry_id"] != "two" or attempts.count("two") > 1
        assert await sync.poll(target, sometimes_fail) == 1
        target = (await store.get_provider_targets("wetrakr", "123", active_only=True))[0]
        assert target["guild_user_data"]["wetrakr_sync"]["recent_entry_ids"] == ["one"]
        assert await sync.poll(target, sometimes_fail) == 1
        assert attempts == ["one", "two", "two"]
        target = (await store.get_provider_targets("wetrakr", "123", active_only=True))[0]
        assert await sync.poll(target, sometimes_fail) == 0
        assert await store.set_activity_provider("123", "42", "simkl")
        assert len(await store.get_poll_targets("123")) == 1
        assert (await store.get_user("42"))["wetrakr"]["access_token"] == "a"
    asyncio.run(run())
