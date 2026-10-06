import asyncio
import copy
import json
from collections import Counter

from trackerbot.validation.live import collect_report, sample_pages
from trackerbot.integrations.wetrakr_client import WeTrakrError


def snapshot():
    return {'users': {'42': {'wetrakr': {'account_id': '7', 'access_token': 'SECRET_ACCESS',
        'refresh_token': 'SECRET_REFRESH'}, 'simkl_token': 'SECRET_SIMKL',
        'progression': {'xp': 100, 'lifetime_xp': 100, 'xp_events': [], 'wetrakr_plays': {}}}},
        'guilds': {'1': {'users': {'42': {'activity_provider': 'wetrakr',
            'wetrakr_sync': {'seeded': True, 'checkpoint': '2026-10-05T00:00:00Z', 'recent_entry_ids': []}}}}}}


class Client:
    request_counts = Counter()
    closed = False
    def __init__(self, key):
        self.key = key
    async def last_activities(self, token):
        return {'all': '2026-10-05T01:00:00Z'}
    async def compact_history(self, token, target):
        yield ([{'type': 'movie', 'play_id': 'p1', 'id': 10, 'watched_at': '2026-10-05T01:00:00Z'}]
               if target == 'movies' else [])
    async def tracking(self, token, status, target):
        yield []
    async def journal(self, token, since, **kwargs):
        return [{'entry_id': 'new', 'action_at': '2026-10-05T01:00:00Z', 'category': 'watched',
                 'status': 'added', 'type': 'movie', 'play_id': 'p1', 'id': 10}]
    async def close(self):
        self.closed = True


def test_live_read_checks_are_read_only_and_secret_free():
    async def run():
        data = snapshot()
        before = copy.deepcopy(data)
        report = await collect_report(data, '1', '42', ('wetrakr',), 2, {'wetrakr': 'SECRET_KEY'}, {'wetrakr': Client})
        assert data == before
        assert report['providers']['wetrakr']['status'] == 'read'
        assert report['providers']['wetrakr']['comparison_complete']
        assert report['providers']['wetrakr']['sampled_live_plays_missing_from_store'] == 1
        assert report['providers']['wetrakr']['journal']['unacknowledged_rows'] == 1
        assert report['local']['ledger_state'] == 'migration_preview'
        assert report['live_posting_and_reward_checks'].startswith('pending')
        assert 'SECRET' not in json.dumps(report)
    asyncio.run(run())


def test_page_bound_is_conservative_and_does_not_fetch_extra():
    async def run():
        calls = []
        async def pages():
            for i in range(10):
                calls.append(i)
                yield [{'play_id': str(i)}]
        rows, complete, count = await sample_pages(pages(), 2)
        assert len(rows) == 2 and count == 2 and not complete
        assert calls == [0, 1]
    asyncio.run(run())


def test_expired_live_credentials_are_reported_without_refresh_or_error_text():
    async def run():
        class Expired(Client):
            async def last_activities(self, token):
                raise WeTrakrError(401, 'AUTH_FAILED', 'SECRET_ACCESS SECRET_REFRESH')
        report = await collect_report(snapshot(), '1', '42', ('wetrakr',), 2,
            {'wetrakr': 'key'}, {'wetrakr': Expired})
        assert report['providers']['wetrakr']['code'] == 'AUTH_FAILED'
        assert 'SECRET' not in json.dumps(report)
    asyncio.run(run())


def test_simkl_and_missing_link_checks_do_not_use_another_provider():
    async def run():
        class Simkl(Client):
            async def get_user_settings(self, token):
                return {'account': {'id': 123}}
            async def get_all_items(self, token, kind):
                return [{'status': 'watching', 'seasons': [{'episodes': [{'watched_at': '2026-10-05T01:00:00Z'}]}]}] if kind == 'shows' else []
        report = await collect_report(snapshot(), '1', '42', ('simkl', 'wetrakr'), 2,
            {'simkl': 'key'}, {'simkl': Simkl, 'wetrakr': Client})
        assert report['providers']['simkl']['catalogs']['shows']['dated_episode_watches'] == 1
        assert report['providers']['wetrakr']['code'] == 'MISSING_KEY_OR_LINK'
    asyncio.run(run())


def test_cli_cannot_overwrite_live_store(tmp_path, monkeypatch):
    import pytest
    from trackerbot.validation import live
    store = tmp_path / 'store.json'
    contents = json.dumps(snapshot())
    store.write_text(contents)
    monkeypatch.setattr('sys.argv', ['live', '--store', str(store), '--guild-id', '1', '--user-id', '42', '--output', str(store)])
    with pytest.raises(SystemExit) as result:
        live.main()
    assert result.value.code == 2
    assert store.read_text() == contents
