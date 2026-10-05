import asyncio

import trackerbot.core.storage as storage_module
from trackerbot.integrations.wetrakr_sync import WeTrakrSync


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


def test_bulk_episode_range_acknowledges_only_after_delivery(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module, "DATA_PATH", str(tmp_path / "store.json"))
        store = storage_module.Storage()
        await store.link_wetrakr("123", "42", {"access_token": "a", "refresh_token": "r"}, {"id": 19})
        target = (await store.get_provider_targets("wetrakr", "123", active_only=True))[0]
        client = Client()
        sync = WeTrakrSync(client, Auth(), store)
        assert await sync.poll(target, lambda *_: None) == 0
        client.rows = [
            {"entry_id": str(n), "action_at": "2026-09-28T03:00:00Z", "category": "watched",
             "status": "added", "type": "episode", "play_id": f"play-{n}", "id": 100+n,
             "media_id": 10, "season_number": 1, "number": n} for n in (1, 2, 3)
        ]
        client.rows += [{"entry_id": "movie", "action_at": "2026-09-28T03:00:01Z",
                         "category": "watched", "status": "added", "type": "movie",
                         "play_id": "movie-play", "id": 50}]
        target = (await store.get_provider_targets("wetrakr", "123", active_only=True))[0]
        attempts = []
        async def deliver(change, row):
            attempts.append(row["entry_id"])
            return True
        async def group(batch):
            attempts.append([row["entry_id"] for _, row in batch])
            return len(attempts) > 1
        assert await sync.poll(target, deliver, group) == 0
        target = (await store.get_provider_targets("wetrakr", "123", active_only=True))[0]
        assert target["guild_user_data"]["wetrakr_sync"]["recent_entry_ids"] == []
        assert await sync.poll(target, deliver, group) == 4
        target = (await store.get_provider_targets("wetrakr", "123", active_only=True))[0]
        assert target["guild_user_data"]["wetrakr_sync"]["recent_entry_ids"] == ["1", "2", "3", "movie"]
        assert attempts == [["1", "2", "3"], ["1", "2", "3"], "movie"]
        assert await sync.poll(target, deliver, group) == 0
    asyncio.run(run())


def test_unrecognized_baseline_does_not_revoke_existing_xp(tmp_path, monkeypatch):
    async def run():
        from trackerbot.integrations.wetrakr_client import WeTrakrError
        monkeypatch.setattr(storage_module, 'DATA_PATH', str(tmp_path / 'store.json'))
        store = storage_module.Storage()
        await store.link_wetrakr('123', '42', {'access_token': 'a', 'refresh_token': 'r'}, {'id': 19})
        await store.reconcile_wetrakr_plays('123', '42', [{'source_event_id': 'old', 'media_type': 'movie',
            'title': 'Existing Film', 'item_key': 'wetrakr:movie:50', 'watched_at': '2026-10-01T00:00:00Z'}])
        before = await store.get_progression('42')
        class Changed(Client):
            async def compact_history(self, token, target):
                yield {'unexpected_response': []}
        target = (await store.get_provider_targets('wetrakr', '123', active_only=True))[0]
        try:
            await WeTrakrSync(Changed(), Auth(), store).poll(target, lambda *_: None)
        except WeTrakrError as exc:
            assert exc.code == 'INVALID_RESPONSE'
        else:
            raise AssertionError('Changed baseline must stop before reconciliation')
        assert await store.get_progression('42') == before
        after = (await store.get_provider_targets('wetrakr', '123', active_only=True))[0]
        assert not after['guild_user_data']['wetrakr_sync']['seeded']
    asyncio.run(run())


def test_visibility_lag_quiet_reads_and_failed_delivery(tmp_path, monkeypatch):
    async def run():
        from trackerbot.integrations.wetrakr_client import JournalEntries
        monkeypatch.setattr(storage_module, 'DATA_PATH', str(tmp_path / 'store.json'))
        store = storage_module.Storage()
        await store.link_wetrakr('123', '42', {'access_token': 'a', 'refresh_token': 'r'}, {'id': 19})
        class Visible(Client):
            async def last_activities(self, token):
                return {'all': '2026-10-05T00:00:10Z', 'journal_visible_until': '2026-10-05T00:00:05Z'}
            async def journal(self, token, since, *, category):
                self.calls.append(since)
                result = JournalEntries()
                result.extend(self.rows)
                result.visible_until = self.mark
                return result
        client = Visible()
        client.mark = '2026-10-05T00:00:06Z'
        sync = WeTrakrSync(client, Auth(), store)
        async def target():
            return (await store.get_provider_targets('wetrakr', '123', active_only=True))[0]
        async def accept(*args):
            return True
        async def reject(*args):
            return False
        await sync.poll(await target(), accept)
        assert (await target())['guild_user_data']['wetrakr_sync']['checkpoint'] == '2026-10-05T00:00:05Z'
        await sync.poll(await target(), accept)
        state = (await target())['guild_user_data']['wetrakr_sync']
        assert state['checkpoint'] == state['last_activity'] == client.mark
        client.mark = '2026-10-05T00:00:09Z'
        client.rows = [{'entry_id': 'new', 'action_at': '2026-10-05T00:00:07Z',
            'category': 'watched', 'status': 'added', 'type': 'movie', 'play_id': 'p', 'id': 1}]
        assert await sync.poll(await target(), reject) == 0
        assert (await target())['guild_user_data']['wetrakr_sync']['checkpoint'] == '2026-10-05T00:00:06Z'
        assert await sync.poll(await target(), accept) == 1
        state = (await target())['guild_user_data']['wetrakr_sync']
        assert state['checkpoint'] == client.mark
        assert state['recent_entry_ids'] == ['new']
        assert await sync.poll(await target(), accept) == 0
    asyncio.run(run())
