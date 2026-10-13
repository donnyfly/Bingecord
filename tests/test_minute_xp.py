import asyncio
from datetime import date
from unittest.mock import AsyncMock

import pytest
from trackerbot.core.progression import xp_for_watch, watch_minutes, challenge_progress, challenges_for
from trackerbot.metadata.tmdb_client import TmdbClient
from trackerbot.core import storage as storage_module


@pytest.mark.parametrize('runtime', [None, '', 'bad', 0, -1, True, float('nan'), float('inf')])
def test_invalid_runtime_fallbacks(runtime):
    assert xp_for_watch('episode', runtime) == 100
    assert xp_for_watch('anime_movie', runtime) == 360


def test_fractional_runtime_and_legacy_duration():
    assert xp_for_watch('episode', 24.125) == 97
    assert xp_for_watch('movie', '90.5') == 362
    events = [{'at':'2026-10-14T12:00:00Z','media_type':'episode','runtime_minutes':24.5},
              {'at':'2026-10-14T13:00:00Z','media_type':'movie','runtime_minutes':90},
              {'at':'2026-10-14T14:00:00Z','media_type':'episode','amount':100},
              {'at':'2026-10-14T15:00:00Z','media_type':'achievement','runtime_minutes':500}]
    assert challenge_progress(events, {'kind':'minutes','target':200}, '2026-10-14','2026-10-15') == 114.5
    assert all(c['kind'] != 'minutes' for c in challenges_for(date(2026,10,12))[0])
    assert any(c['kind'] == 'minutes' for c in challenges_for(date(2026,10,13))[0])
    assert all(c['kind'] != 'minutes' for c in challenges_for(date(2026,10,11))[1])
    assert any(c['kind'] == 'minutes' for c in challenges_for(date(2026,10,12))[1])


def test_runtime_lookup_uses_one_season_request_for_range(monkeypatch):
    async def run():
        client=TmdbClient('test-key')
        fetch=AsyncMock(return_value={'episodes':[{'episode_number':1,'runtime':24},
                                                  {'episode_number':2,'runtime':72}]})
        monkeypatch.setattr(client,'_get_json',fetch)
        assert await client.get_watch_runtime('anime_episode',{'tmdb':1},season=2,episode=1) == 24
        assert await client.get_watch_runtime('anime_episode',{'tmdb':1},season=2,episode=2) == 72
        assert fetch.await_count == 1
        assert await client.get_watch_runtime('movie',{'tmdb':2},runtime=120) == 120
        assert fetch.await_count == 1
    asyncio.run(run())


@pytest.mark.parametrize('provider', ['wetrakr','mdblist'])
@pytest.mark.parametrize('kind, runtime, amount', [('episode',72,288), ('anime_episode',24,96), ('movie',120,480), ('anime_movie',120,480)])
def test_provider_awards_preserve_on_metadata_edit_and_reverse_exactly(tmp_path, monkeypatch, provider, kind, runtime, amount):
    async def run():
        monkeypatch.setattr(storage_module,'DATA_PATH',str(tmp_path/'store.json'))
        store=storage_module.Storage()
        await store.link_provider_account('1','42',{'access_token':'a','refresh_token':'r'},
                                          {'id':9},provider=provider)
        play={'source_event_id':'1','media_type':kind,'title':'Drama','item_key':'show:1:1:1',
              'season':1,'episode':1,'ids':{'tmdb':1},'runtime_minutes':runtime,
              'watched_at':'2024-01-01T12:00:00Z'}
        await store.reconcile_provider_plays('1','42',[play],complete=True,account_id='9',provider=provider)
        progression=await store.get_progression('42')
        assert progression['lifetime_xp'] == amount
        assert progression['xp_events'][0]['runtime_minutes'] == runtime
        assert progression['xp_events'][0]['xp_rule'] == 'minutes_v1'
        # Catalog corrections and watch-date edits must not reprice an award.
        play={**play,'runtime_minutes':100,'watched_at':'2024-01-02T12:00:00Z'}
        await store.reconcile_provider_plays('1','42',[play],complete=True,account_id='9',provider=provider)
        assert (await store.get_progression('42'))['lifetime_xp'] == amount
        await store.reconcile_provider_plays('1','42',[],complete=True,account_id='9',provider=provider)
        assert (await store.get_progression('42'))['lifetime_xp'] == 0
    asyncio.run(run())


def test_minute_challenge_rewards_and_removals(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module,'DATA_PATH',str(tmp_path/'store.json'))
        store=storage_module.Storage()
        await store.link_provider_account('1','42',{'access_token':'a','refresh_token':'r'}, {'id':9},provider='mdblist')
        play={'source_event_id':'1','media_type':'episode','title':'Drama','item_key':'show:1:1:1',
              'season':1,'episode':1,'ids':{'tmdb':1},'runtime_minutes':120,
              'watched_at':'2026-10-14T12:00:00Z'}
        await store.reconcile_provider_plays('1','42',[play],complete=True,account_id='9',provider='mdblist')
        rewards=sum(c['xp'] for c in challenges_for(date(2026,10,14))[0] if c['kind']=='minutes')
        assert (await store.get_progression('42'))['lifetime_xp'] == 480 + rewards + sum(c['xp'] for c in challenges_for(date(2026,10,14))[1] if c['kind']=='minutes' and c['target']<=120)
        await store.reconcile_provider_plays('1','42',[],complete=True,account_id='9',provider='mdblist')
        assert (await store.get_progression('42'))['lifetime_xp'] == 0
    asyncio.run(run())


def test_legacy_award_is_not_repriced_and_last_support_removes_original(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module,'DATA_PATH',str(tmp_path/'store.json'))
        store=storage_module.Storage()
        await store.link_user('1','42','token',None,'Viewer','2024-01-01T00:00:00Z')
        await store.link_provider_account('1','42',{'access_token':'a','refresh_token':'r'}, {'id':9},provider='mdblist')
        await store.set_activity_provider('1','42','mdblist')
        key='movie:movies:1:2024-01-01T12:00:00Z'
        await store.award_watch_xp('42',key,'movie','Film','2024-01-01T12:00:00Z',300,ids={'tmdb':1})
        play={'source_event_id':'1','media_type':'movie','title':'Film','item_key':'movie:1',
              'ids':{'tmdb':1},'runtime_minutes':120,'watched_at':'2024-01-01T12:00:00Z'}
        await store.reconcile_provider_plays('1','42',[play],complete=True,account_id='9',provider='mdblist')
        # A restart and a richer catalog response do not award another 480 XP.
        store=storage_module.Storage()
        await store.reconcile_provider_plays('1','42',[play],complete=True,account_id='9',provider='mdblist')
        assert (await store.get_progression('42'))['lifetime_xp'] == 300
        await store.reconcile_watch_xp('42',set(),{'movie'})
        assert (await store.get_progression('42'))['lifetime_xp'] == 300
        await store.reconcile_provider_plays('1','42',[],complete=True,account_id='9',provider='mdblist')
        assert (await store.get_progression('42'))['lifetime_xp'] == 0
    asyncio.run(run())


def test_simkl_history_repair_uses_runtime_and_retains_credited_amount(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module,'DATA_PATH',str(tmp_path/'store.json'))
        store=storage_module.Storage()
        await store.link_user('1','42','token',None,'Viewer','2024-01-01T00:00:00Z')
        await store.seed_progression_batch('42',[])
        entry={'media_type':'episode','item_key':'series:shows:1:1:1','title':'Drama',
               'watched_at':'2024-01-01T12:00:00Z','runtime_minutes':72,'ids':{'tmdb':1}}
        assert await store.repair_missing_watch_xp('42',[entry]) == 288
        assert await store.repair_missing_watch_xp('42',[{**entry,'runtime_minutes':100}]) == 0
        assert (await store.get_progression('42'))['lifetime_xp'] == 288
        await store.reconcile_watch_xp('42',set(),{'episode'})
        assert (await store.get_progression('42'))['lifetime_xp'] == 0
    asyncio.run(run())
