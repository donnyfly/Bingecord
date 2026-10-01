import asyncio
import pytest
from trackerbot.core import storage as storage_module


@pytest.mark.parametrize('first', ['simkl', 'wetrakr'])
@pytest.mark.parametrize('remove_first', ['simkl', 'wetrakr'])
def test_occurrence_survives_either_removal_order_and_reload(tmp_path, monkeypatch, first, remove_first):
    async def run():
        monkeypatch.setattr(storage_module, 'DATA_PATH', str(tmp_path / 'store.json'))
        store = storage_module.Storage()
        await store.link_user('1', '2', 'token', None, 'viewer', '2026-09-28T00:00:00Z')
        await store.link_wetrakr('1', '2', {'access_token': 'a', 'refresh_token': 'r'}, {'id': 7})
        stamp = '2026-09-28T12:00:00Z'
        key = 'anime_episode:series:anime:1:1:1:' + stamp
        play = {'source_event_id': 'p1', 'media_type': 'anime_episode', 'title': 'Title',
                'item_key': 'wetrakr:episode:70:1:1', 'ids': {'tmdb': 123}, 'watched_at': stamp}
        async def add(provider):
            if provider == 'simkl':
                await store.award_watch_xp('2', key, 'anime_episode', 'Title', stamp, 100, {'tmdb': 123})
            else:
                await store.reconcile_wetrakr_plays('1', '2', [play])
        async def remove(provider):
            if provider == 'simkl':
                await store.reconcile_watch_xp('2', set(), {'anime_episode'})
            else:
                await store.reconcile_wetrakr_plays('1', '2', [], complete=True)
        await add(first)
        await add('wetrakr' if first == 'simkl' else 'simkl')
        await add(first)
        progression = await store.get_progression('2')
        assert progression['lifetime_xp'] == 100
        assert len(progression['occurrence_ledger']) == 1
        assert len(next(iter(progression['occurrence_ledger'].values()))['observations']) == 2
        await remove(remove_first)
        assert (await store.get_progression('2'))['lifetime_xp'] == 100
        store = storage_module.Storage()
        # Re-observing a previously removed copy restores its support, not XP.
        await add(remove_first)
        assert (await store.get_progression('2'))['lifetime_xp'] == 100
        await remove(remove_first)
        await remove('wetrakr' if remove_first == 'simkl' else 'simkl')
        assert (await store.get_progression('2'))['lifetime_xp'] == 0
        assert not (await store.get_progression('2'))['occurrence_ledger']
    asyncio.run(run())


def test_simkl_removal_revokes_challenge_rewards(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module, 'DATA_PATH', str(tmp_path / 'store.json'))
        store = storage_module.Storage()
        await store.link_user('1', '2', 'token', None, 'viewer', '2026-09-28T00:00:00Z')
        records = [('anime_episode', 'Title', f'series:anime:1:1:{e}',
                    '2026-09-28T12:00:00Z', {}) for e in range(1, 11)]
        await store.seed_guild_history('1', '2', [], {}, {}, records)
        before = await store.get_progression('2')
        assert before['challenge_completions']
        result = await store.reconcile_watch_xp('2', set(), {'anime_episode'})
        after = await store.get_progression('2')
        assert not after['challenge_completions']
        assert after['lifetime_xp'] == 0
        assert result['amount'] == before['lifetime_xp']
    asyncio.run(run())


def test_ledger_supports_third_provider_without_provider_specific_matching():
    from trackerbot.core import watch_ledger
    from trackerbot.core.tracker_mapping import WatchIdentity
    progression = {'xp_events': [], 'wetrakr_plays': {}}
    rows = watch_ledger.ensure(progression)
    identity = WatchIdentity('episode', 'Show', '2026-09-28T12:00:00Z', 1, 1, {'tmdb': 123})
    first = watch_ledger.observe(rows, 'simkl', 'simkl:account-1:event-1', identity)
    first['award_keys'].append('award-1')
    assert watch_ledger.observe(rows, 'wetrakr', 'wetrakr:account-2:play-1', identity) is first
    assert watch_ledger.observe(rows, 'future_tracker', 'future_tracker:account-3:event-1', identity) is first
    assert watch_ledger.remove(progression, 'simkl:account-1:event-1') == []
    assert watch_ledger.remove(progression, 'wetrakr:account-2:play-1') == []
    assert watch_ledger.remove(progression, 'future_tracker:account-3:event-1') == ['award-1']
