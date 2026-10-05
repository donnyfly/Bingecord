import asyncio
import copy
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import trackerbot.core.storage as storage_module
from trackerbot.integrations.mdblist_provider import MDBListProvider, normalize_play
from trackerbot.integrations.mdblist_tracking_client import MDBListTrackingError
from trackerbot.core.providers import ProviderRegistry

STAMP='2026-10-05T10:00:00Z'


class API:
    def __init__(self):
        self.movies=[];self.episodes=[];self.planned=[];self.dropped_rows=[];self.revision=0
        self.history_reads=0
    async def last_activities(self,token):
        return {'server_time':STAMP,'journal_at':str(self.revision)}
    async def history(self,token,*,media_type):
        self.history_reads+=1
        yield {'movies':copy.deepcopy(self.movies)} if media_type=='movie' else {'episodes':copy.deepcopy(self.episodes)}
    async def planning(self,token):yield {'movies':self.planned,'shows':[]}
    async def dropped(self,token):yield {'shows':self.dropped_rows}
    async def watching(self,token):yield {'items':[],'has_more':False}


def movie(n=1,stamp=STAMP):
    return {'play_id':n,'watched_at':stamp,'movie':{'title':'Film','ids':{'tmdb':50,'imdb':'tt50','mdblist':'film'}}}


async def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(storage_module,'_lock',asyncio.Lock())
    monkeypatch.setattr(storage_module,'_write_lock',asyncio.Lock())
    monkeypatch.setattr(storage_module,'DATA_PATH',str(tmp_path/'store.json'))
    store=storage_module.Storage()
    await store.link_provider_account('1','42',{'access_token':'a','refresh_token':'r'}, {'id':7,'username':'Viewer'},provider='mdblist')
    await store.set_channel('1','9')
    api=API()
    app=SimpleNamespace(storage=store,mdblist_tracking=api,mdblist_auth=SimpleNamespace(access_token=AsyncMock(return_value='a')),
        tmdb=SimpleNamespace(get_movie_details=AsyncMock(return_value={'title':'Film'}),_get_series_details=AsyncMock(return_value={'name':'Series'})),
        is_wetrakr_anime_movie=AsyncMock(return_value=False),is_wetrakr_anime=AsyncMock(return_value=False),
        bot=SimpleNamespace(get_channel=lambda _:SimpleNamespace(send=AsyncMock())),resolve_member=AsyncMock(return_value=(object(),'Viewer')),
        deliver_mdblist_play=AsyncMock(return_value=True),deliver_mdblist_status=AsyncMock(return_value=True),
        evaluate_achievements=AsyncMock(),notify_challenge_rewards=AsyncMock(),notify_level_up=AsyncMock(),
        feature_enabled=AsyncMock(return_value=False),parse_iso=lambda s:__import__('datetime').datetime.fromisoformat(s.replace('Z','+00:00')),select_sources=lambda s:s,log=logging.getLogger('test'),MAX_CONSECUTIVE_FAILURES=5)
    provider=MDBListProvider(app)
    return store,api,app,provider


def test_quiet_import_new_play_retry_rewatch_and_removal(tmp_path,monkeypatch):
    async def run():
        store,api,app,provider=await setup(tmp_path,monkeypatch)
        api.movies=[movie()]
        assert await provider.poll('1')==0
        app.deliver_mdblist_play.assert_not_awaited()
        before=(await store.get_progression('42'))['xp']
        reads=api.history_reads
        assert await provider.poll('1')==0 and api.history_reads==reads
        api.movies.append(movie(2,'2026-10-06T10:00:00Z'));api.revision+=1
        app.deliver_mdblist_play.return_value=False
        assert await provider.poll('1')==0
        assert (await store.get_progression('42'))['xp']==before
        app.deliver_mdblist_play.return_value=True
        assert await provider.poll('1')==1
        assert (await store.get_progression('42'))['xp']>before
        assert await provider.poll('1')==0
        assert len(await store.get_provider_plays('42',provider='mdblist'))==2
        api.movies=[movie(2,'2026-10-06T10:00:00Z')];api.revision+=1
        assert await provider.poll('1')==0
        assert len(await store.get_provider_plays('42',provider='mdblist'))==1
        assert len((await store.get_progression('42'))['occurrence_ledger'])==1
        stats=(await store.get_guild_statistics('1'))[0]
        assert stats['simkl_username']=='Viewer' and stats['statistics']['movies_watched']==1
        assert stats['history_seeded']
        assert await store.unlink_provider_account('1','42',provider='mdblist')
        assert not await store.get_provider_targets('mdblist')
    asyncio.run(run())


def test_duplicate_between_mdblist_and_wetrakr_keeps_award_until_last_source(tmp_path,monkeypatch):
    async def run():
        store,api,app,provider=await setup(tmp_path,monkeypatch)
        await store.link_wetrakr('1','42',{'access_token':'wa','refresh_token':'wr'},{'id':8})
        play={'source_event_id':'w1','media_type':'movie','title':'Film','ids':{'tmdb':50,'imdb':'tt50'},'item_key':'wetrakr:movie:50','watched_at':STAMP}
        await store.reconcile_wetrakr_plays('1','42',[play])
        before=(await store.get_progression('42'))['xp']
        api.movies=[movie()]
        await provider.poll('1')
        assert (await store.get_progression('42'))['xp']==before
        await store.reconcile_wetrakr_plays('1','42',[{**play,'removed':True}])
        assert (await store.get_progression('42'))['xp']==before
        api.movies=[];api.revision+=1
        await provider.poll('1')
        assert not (await store.get_progression('42'))['occurrence_ledger']
        assert (await store.get_progression('42'))['xp']<before
    asyncio.run(run())


def test_range_planning_and_discovery_use_mdblist(tmp_path,monkeypatch):
    async def run():
        store,api,app,provider=await setup(tmp_path,monkeypatch)
        await provider.poll('1')
        api.episodes=[{'play_id':n,'watched_at':STAMP,'show':{'title':'Series','ids':{'tmdb':100,'mdblist':'series'}},'episode':{'season':1,'number':n}} for n in (1,2,3)]
        api.planned=[{'title':'Film','ids':{'tmdb':50,'mdblist':'film'}}];api.revision+=1
        assert await provider.poll('1')==2
        app.deliver_mdblist_play.assert_awaited_once()
        first,last=app.deliver_mdblist_play.call_args.args[-2:]
        assert (first['episode'],last['episode'])==(1,3)
        account=await provider.link('42')
        assert (await provider.planning(account))[0]['url']=='https://mdblist.com/movie/film'
        sources,excluded=await provider.recommendation_sources(account,'all')
        assert ('tv',100) in excluded and ('movie',50) in excluded and sources
        assert await provider.poll('1')==0
        assert (await store.get_guild_leaderboard_snapshot('1'))[0]['episodes']==3
        registry=ProviderRegistry();registry.register(provider)
        assert registry.get('mdblist') is provider
    asyncio.run(run())


def test_bad_history_does_not_remove_seeded_plays(tmp_path,monkeypatch):
    async def run():
        store,api,app,provider=await setup(tmp_path,monkeypatch)
        api.movies=[movie()];await provider.poll('1')
        before=await store.get_progression('42')
        api.movies=[{'movie':{'ids':{'tmdb':50}}}];api.revision+=1
        assert await provider.poll('1')==0
        assert await store.get_progression('42')==before
        state=(await store.get_provider_targets('mdblist','1',active_only=True))[0]['guild_user_data']['mdblist_sync']
        assert state['health']['error']=='UNSUPPORTED_HISTORY_SHAPE'
        assert state['status_snapshot']['activities']['journal_at']=='0'
    asyncio.run(run())


def test_anime_film_and_episode_parent_ids_are_preserved():
    film=normalize_play(movie(),'movie')
    episode=normalize_play({'play_id':4,'watched_at':STAMP,'show':{'ids':{'tmdb':100,'imdb':'ttSeries'}},
        'episode':{'ids':{'imdb':'ttEpisode'},'season':1,'number':1}},'episode')
    assert film['ids']['tmdb']==50
    assert episode['ids']['imdb']=='ttSeries'
    assert episode['episode_ids']['imdb']=='ttEpisode'


def test_simkl_and_mdblist_both_ingestion_and_removal_orders(tmp_path,monkeypatch):
    async def run():
        for mdblist_first in (True,False):
            store,api,app,provider=await setup(tmp_path,monkeypatch)
            await store.link_user('1','42','s',None,'SIMKL',STAMP)
            await store.set_activity_provider('1','42','mdblist')
            record={'media_type':'movie','title':'Film','item_key':'50','ids':{'tmdb':50,'imdb':'tt50'},'watched_at':STAMP,'amount':300}
            if mdblist_first:
                api.movies=[movie()];await provider.poll('1')
                before=(await store.get_progression('42'))['xp']
                await store.record_activity_batch('1','42',[],{},[record])
            else:
                await store.record_activity_batch('1','42',[],{},[record])
                before=(await store.get_progression('42'))['xp']
                api.movies=[movie()];await provider.poll('1')
            assert (await store.get_progression('42'))['xp']==before
            if mdblist_first:
                api.movies=[];api.revision+=1;await provider.poll('1')
                assert (await store.get_progression('42'))['xp']==before
                await store.reconcile_watch_xp('42',set(),{'movie'})
            else:
                await store.reconcile_watch_xp('42',set(),{'movie'})
                assert (await store.get_progression('42'))['xp']==before
                # A SIMKL-only statistics reconciliation must preserve MDBList rows.
                await store.reconcile_watch_statistics('1','42',set(),{'movie'})
                assert (await store.get_statistics('1','42'))['movies_watched']==1
                api.movies=[];api.revision+=1;await provider.poll('1')
            assert not (await store.get_progression('42'))['occurrence_ledger']
    asyncio.run(run())


def test_reset_switch_restart_and_anime_counts(tmp_path,monkeypatch):
    async def run():
        store,api,app,provider=await setup(tmp_path,monkeypatch)
        app.is_wetrakr_anime_movie.return_value=True
        api.movies=[movie()];await provider.poll('1')
        assert (await store.get_statistics('1','42'))['anime_movies_watched']==1
        before=(await store.get_progression('42'))['xp']
        await store.reset_user_tracking('1','42',STAMP)
        assert await store.get_activity_provider('1','42')=='mdblist'
        await provider.poll('1')
        assert (await store.get_progression('42'))['xp']==before
        app.storage=storage_module.Storage()
        assert (await app.storage.get_provider_plays('42',provider='mdblist'))[0]['source_event_id']=='1'
        api.movies=[];api.revision+=1
        await provider.poll('1')
        assert not (await app.storage.get_progression('42'))['occurrence_ledger']
    asyncio.run(run())


def test_episode_specials_keep_season_zero():
    play=normalize_play({'play_id':9,'watched_at':STAMP,'show':{'ids':{'tmdb':100}},
        'episode':{'season':0,'number':1}},'episode')
    assert play['season']==0 and play['source_season']==0


def test_nested_episode_show_and_explicit_flat_parent_ids():
    base={'play_id':9,'watched_at':STAMP}
    nested=normalize_play({**base,'episode':{'show':{'ids':{'tmdb':100}},'ids':{'tmdb':200},'season':1,'number':2}},'episode')
    assert nested['ids']['tmdb']==100 and nested['episode_ids']['tmdb']==200
    flat=normalize_play({**base,'show_tmdb_id':100,'season_num':1,'episode_num':2},'episode')
    assert flat['ids']=={'tmdb':100}


def test_absolute_anime_number_maps_by_episode_identity(tmp_path,monkeypatch):
    async def run():
        _,_,app,provider=await setup(tmp_path,monkeypatch)
        app.is_wetrakr_anime.return_value=True
        app.tmdb.get_episode_details=AsyncMock(return_value={'air_date':'2023-07-06'})
        app.tmdb.map_anime_episode_to_tvmaze=AsyncMock(return_value=(2,1))
        app.tmdb.map_anime_calendar_episode=AsyncMock(return_value=None)
        play=normalize_play({'play_id':25,'watched_at':STAMP,'show':{'title':'Jujutsu Kaisen','ids':{'tmdb':100,'tvdb':200}},'episode':{'season':1,'number':25}},'episode')
        mapped=await provider.resolve_play(play)
        assert (mapped['season'],mapped['episode'])==(2,1)
        assert (mapped['source_season'],mapped['source_episode'])==(1,25)
        app.tmdb.map_anime_episode_to_tvmaze.assert_awaited_once_with(200,air_date='2023-07-06',title=None)
    asyncio.run(run())
