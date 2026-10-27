import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from trackerbot.core import storage as storage_module
from trackerbot.integrations.provider_adapters import WeTrakrProvider
from trackerbot.integrations.wetrakr_client import WeTrakrError
from trackerbot.integrations.wetrakr_sync import WeTrakrSync


def app_fixture():
    return SimpleNamespace(
        wetrakr=SimpleNamespace(title=AsyncMock(side_effect=WeTrakrError(404,'HTTP_ERROR','Media not found'))),
        is_wetrakr_anime=AsyncMock(return_value=False),is_wetrakr_anime_movie=AsyncMock(return_value=False),
        tmdb=SimpleNamespace(get_watch_runtime=AsyncMock(return_value=24)))


@pytest.mark.parametrize('kind',['movie','episode'])
def test_missing_catalogue_uses_watch_identity_without_mixing_episode_and_show_ids(kind):
    async def run():
        app=app_fixture();adapter=WeTrakrProvider(app);cache={}
        play={'media_type':kind,'source_event_id':'p1','action':'added','wetrakr_id':11,
              'title':'Film' if kind=='movie' else 'Episode title','ids':{'tmdb':1001},
              'show_id':10,'show_ids':{'tmdb':100},'show_title':'Series',
              'season':2019,'episode':26,'runtime_minutes':24}
        first=await adapter.resolve_play(play,cache)
        second=await adapter.resolve_play({**play,'source_event_id':'p2'},cache)
        assert first['source_event_id']=='p1' and second['source_event_id']=='p2'
        assert first['ids']=={'tmdb':1001 if kind=='movie' else 100}
        assert first['title']==('Film' if kind=='movie' else 'Series')
        assert first['runtime_minutes']==24 and first['item_key'].startswith('wetrakr:')
        if kind=='episode':
            assert first['episode_ids']=={'tmdb':1001}
            assert (first['source_season'],first['source_episode'])==(2019,26)
        app.wetrakr.title.assert_awaited_once_with('movie' if kind=='movie' else 'show',11 if kind=='movie' else 10)
        assert play['ids']=={'tmdb':1001}  # Input payload stays unchanged.
    asyncio.run(run())


@pytest.mark.parametrize('kind',['movie','episode'])
def test_removed_play_never_depends_on_catalogue(kind):
    async def run():
        app=app_fixture();adapter=WeTrakrProvider(app)
        play={'media_type':kind,'source_event_id':'p1','action':'removed','wetrakr_id':11,'show_id':10}
        assert await adapter.resolve_play(play)==play
        app.wetrakr.title.assert_not_awaited();app.tmdb.get_watch_runtime.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize('status',[401,429,500])
def test_authentication_and_transient_failures_are_not_missing_metadata(status):
    async def run():
        app=app_fixture();adapter=WeTrakrProvider(app)
        app.wetrakr.title.side_effect=WeTrakrError(status,'ERROR','Request failed')
        cache={}
        with pytest.raises(WeTrakrError):
            await adapter.resolve_play({'media_type':'movie','source_event_id':'p1','wetrakr_id':11},cache)
        assert cache=={}
        app.tmdb.get_watch_runtime.assert_not_awaited()
    asyncio.run(run())


def test_episode_with_no_parent_id_does_not_return_child_ids_as_series_ids():
    async def run():
        app=app_fixture();adapter=WeTrakrProvider(app)
        play=await adapter.resolve_play({'media_type':'episode','source_event_id':'p1','wetrakr_id':11,
                                         'ids':{'tmdb':1001},'show_ids':{'tmdb':100}})
        assert play['ids']=={'tmdb':100} and play['episode_ids']=={'tmdb':1001}
        app.wetrakr.title.assert_not_awaited()
    asyncio.run(run())


def test_journal_removes_missing_title_then_processes_next_watch_and_retries_idempotently(tmp_path,monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module,'DATA_PATH',str(tmp_path/'store.json'))
        store=storage_module.Storage()
        await store.link_wetrakr('1','42',{'access_token':'a','refresh_token':'r'},{'id':7})
        app=app_fixture();adapter=WeTrakrProvider(app)
        client=SimpleNamespace(rows=[],last_activities=AsyncMock(return_value={'all':'2026-10-27T06:00:00Z'}))
        async def history(token,target):
            yield ([{'type':'movie','id':50,'play_id':'old','title':'Old film','ids':{'tmdb':50},
                     'watched_at':'2026-10-27T05:00:00Z'}] if target=='movies' else [])
        async def journal(*args,**kwargs):return client.rows
        client.compact_history=history;client.journal=journal
        sync=WeTrakrSync(client,SimpleNamespace(access_token=AsyncMock(return_value='a')),store)
        async def target():return (await store.get_provider_targets('wetrakr','1',active_only=True))[0]
        delivered=[]
        async def deliver(change,row):
            delivered.append(row['entry_id'])
            return True
        await sync.poll(await target(),deliver,resolve_play=adapter.resolve_play)
        assert len(await store.get_wetrakr_plays('42'))==1
        app.wetrakr.title.reset_mock()
        client.rows=[{'entry_id':'remove','type':'movie','category':'watched','status':'removed','id':50,
                      'play_id':'old','action_at':'2026-10-27T06:01:00Z'},
                     {'entry_id':'new','type':'movie','category':'watched','status':'added','id':51,
                      'play_id':'new','ids':{'tmdb':51},'title':'New film','watched_at':'2026-10-27T06:01:01Z',
                      'action_at':'2026-10-27T06:01:01Z'}]
        assert await sync.poll(await target(),deliver,resolve_play=adapter.resolve_play)==1
        assert delivered==['remove','new']
        app.wetrakr.title.assert_awaited_once_with('movie',51)
        plays=await store.get_wetrakr_plays('42')
        assert len(plays)==1 and plays[0]['source_event_id']=='new'
        xp=await store.get_progression('42')
        assert (await target())['guild_user_data']['wetrakr_sync']['checkpoint']=='2026-10-27T06:01:01Z'
        assert await sync.poll(await target(),deliver,resolve_play=adapter.resolve_play)==0
        assert await store.get_progression('42')==xp and delivered==['remove','new']
    asyncio.run(run())
