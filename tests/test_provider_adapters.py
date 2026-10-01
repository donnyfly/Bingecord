import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

os.environ.setdefault('DISCORD_BOT_TOKEN', 'test-token')
os.environ.setdefault('SIMKL_CLIENT_ID', 'test-client')
os.environ.setdefault('TMDB_API_KEY', 'test-key')
import bot
from trackerbot.core import storage as storage_module
from trackerbot.core.providers import ProviderAccount, ProviderPage, WatchChange


def interaction():
    return SimpleNamespace(guild=SimpleNamespace(id=1),
        user=SimpleNamespace(id=42, display_name='Viewer', display_avatar=SimpleNamespace(url='https://example.com/avatar.png')),
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock()))))


async def setup_store(tmp_path, monkeypatch, source):
    monkeypatch.setattr(storage_module, 'DATA_PATH', str(tmp_path / 'store.json'))
    store = storage_module.Storage()
    monkeypatch.setattr(bot, 'storage', store)
    await store.link_user('1', '42', 'token', None, 'viewer', '2026-09-28T00:00:00Z', simkl_account_id=123)
    await store.link_wetrakr('1', '42', {'access_token': 'a', 'refresh_token': 'r'}, {'id': 7, 'username': 'viewer'})
    await store.set_activity_provider('1', '42', source)
    return store


def fixtures(monkeypatch):
    movie = {'title': 'Anime Film', 'ids': {'simkl': 9, 'tmdb': 10, 'mal': 11}, 'genres': ['Drama']}
    simkl_items = [{'movie': movie, 'status': 'watching'}, {'movie': movie, 'status': 'plantowatch'}]
    async def items(uid, user, token, media_type, **kwargs):
        return (simkl_items if media_type == 'movies' else []), token
    monkeypatch.setattr(bot, 'cached_simkl_items', items)
    monkeypatch.setattr(bot, 'valid_token', AsyncMock(return_value='token'))
    async def tracking(uid, status, targets):
        return [('movies', {'movie': movie})] if status in {'watching', 'planning'} else []
    monkeypatch.setattr(bot, 'wetrakr_tracking_rows', tracking)
    monkeypatch.setattr(bot, 'is_wetrakr_anime_movie', AsyncMock(return_value=True))
    monkeypatch.setattr(bot.tmdb, 'get_movie_title', AsyncMock(return_value='Anime Film'))
    monkeypatch.setattr(bot.tmdb, 'get_movie_backdrop', AsyncMock(return_value=None))
    monkeypatch.setattr(bot.tmdb, 'get_movie_logo', AsyncMock(return_value=None))


@pytest.mark.parametrize('source,label,title_url', [('simkl', 'SIMKL', 'https://simkl.com/movies/9'),
                                                    ('wetrakr', 'WeTrakr', 'https://wetrakr.com/tmdb/movie/10')])
def test_selected_source_discovery_cards(tmp_path, monkeypatch, source, label, title_url):
    async def run():
        await setup_store(tmp_path, monkeypatch, source)
        fixtures(monkeypatch)
        i = interaction()
        await bot.simkl_watching.callback(i, SimpleNamespace(value='anime'))
        sent = i.followup.send.await_args.kwargs
        assert sent['ephemeral']
        assert label in sent['embed'].footer.text
        assert title_url in sent['embed'].description
        assert '🌸' in sent['embed'].description
        i = interaction()
        await bot.simkl_random.callback(i, SimpleNamespace(value='anime'), SimpleNamespace(value='drama'))
        embed = i.followup.send.await_args.kwargs['embed']
        assert embed.url == title_url
        assert 'Anime Movie' in embed.description
        assert label in embed.footer.text
        assert label.lower() in embed.author.url.lower()
    asyncio.run(run())


def test_wetrakr_recommendations_exclude_planned_titles(tmp_path, monkeypatch):
    async def run():
        store = await setup_store(tmp_path, monkeypatch, 'wetrakr')
        fixtures(monkeypatch)
        await store.reconcile_wetrakr_plays('1', '42', [{'source_event_id': 'p1',
            'media_type': 'anime_episode', 'title': 'Watched Anime', 'item_key': 'wetrakr:episode:1:1:1',
            'ids': {'tmdb': 99}, 'watched_at': '2026-09-28T12:00:00Z'}])
        adapter, account = await bot.selected_provider(1, '42')
        sources, excluded = await adapter.recommendation_sources(account, 'anime')
        assert sources[0]['tmdb_id'] == 99
        assert ('tv', 99) in excluded and ('movie', 10) in excluded
    asyncio.run(run())


def test_registry_dispatches_both_pollers(monkeypatch):
    async def run():
        monkeypatch.setattr(bot, 'poll_all', AsyncMock(return_value=2))
        monkeypatch.setattr(bot, 'poll_wetrakr_all', AsyncMock(return_value=3))
        assert await bot.poll_providers(1, manual=True) == {'simkl': 2, 'wetrakr': 3}
        bot.poll_all.assert_awaited_once_with(1, force_reconcile=True, ignore_failure_threshold=True)
        bot.poll_wetrakr_all.assert_awaited_once_with(1)
    asyncio.run(run())


def test_removed_episode_page_does_not_require_deleted_metadata():
    account = ProviderAccount('wetrakr', '7', 'viewer')
    ProviderPage((WatchChange('wetrakr', '7', 'play-1', 'change-2', 'removed', 'episode', None, {}),), None).validate(account)


def test_selected_account_guard_rejects_unlinked_server(tmp_path, monkeypatch):
    async def run():
        store = await setup_store(tmp_path, monkeypatch, 'wetrakr')
        await store.unlink_wetrakr('1', '42')
        await store.unlink_user('1', '42')
        with pytest.raises(ValueError, match='Link'):
            await bot.selected_provider(1, '42')
    asyncio.run(run())


def test_simkl_history_contract_and_snapshot_removal(tmp_path, monkeypatch):
    async def run():
        store = await setup_store(tmp_path, monkeypatch, 'simkl')
        fixtures(monkeypatch)
        stamp = '2026-09-28T12:00:00Z'
        key = 'episode:series:shows:88:1:1:' + stamp
        await store.award_watch_xp('42', key, 'episode', 'Removed Show', stamp, 100, {'tmdb': 88})
        adapter, account = await bot.selected_provider(1, '42')
        page = await adapter.changes(account, '2026-09-28T00:00:00Z')
        page.validate(account)
        assert any(c.action == 'removed' and c.event_id == key for c in page.changes)
        # Reading a provider snapshot never changes progression.
        assert (await store.get_progression('42'))['lifetime_xp'] == 100
    asyncio.run(run())


def test_wetrakr_history_and_partial_removal_contract(tmp_path, monkeypatch):
    async def run():
        await setup_store(tmp_path, monkeypatch, 'wetrakr')
        adapter, account = await bot.selected_provider(1, '42')
        async def compact(token, target):
            yield ([{'type': 'movie', 'id': 50, 'play_id': 'p1', 'watched_at': '2026-09-28T12:00:00Z'}]
                   if target == 'movies' else [])
        client = SimpleNamespace(compact_history=compact,
            title=AsyncMock(return_value={'title': 'Film', 'ids': {'tmdb': 50}, 'genres': ['Drama']}),
            journal=AsyncMock(return_value=[{'entry_id': 'r1', 'action_at': '2026-09-28T13:00:00Z',
                'category': 'watched', 'status': 'removed', 'type': 'episode', 'play_id': 'p2'}]))
        monkeypatch.setattr(bot, 'wetrakr', client)
        monkeypatch.setattr(bot, 'wetrakr_sync', SimpleNamespace(auth=SimpleNamespace(access_token=AsyncMock(return_value='token'))))
        monkeypatch.setattr(bot, 'is_wetrakr_anime_movie', AsyncMock(return_value=False))
        page = await adapter.history(account)
        assert page.changes[0].source_key == 'wetrakr:7:p1'
        assert page.changes[0].title_ids == {'tmdb': 50}
        removals = await adapter.changes(account, '2026-09-28T12:00:00Z')
        assert removals.changes[0].action == 'removed'
        assert removals.next_cursor == '2026-09-28T13:00:00Z'
    asyncio.run(run())
