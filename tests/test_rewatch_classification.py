import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from trackerbot.core.rewatches import rewatch_flags
from trackerbot.core import storage as storage_module
from trackerbot.integrations.wetrakr_sync import WeTrakrSync


@pytest.mark.parametrize('episode',[False,True])
@pytest.mark.parametrize('keep_old',[False,True])
def test_mdblist_delete_readd_uses_current_snapshot_and_retries_safely(tmp_path,monkeypatch,episode,keep_old):
    async def run():
        from tests.test_mdblist_provider import setup,movie,STAMP
        store,api,app,provider=await setup(tmp_path,monkeypatch)
        def row(n,stamp):
            return ({'play_id':n,'watched_at':stamp,'show':{'title':'Series','ids':{'tmdb':100}},
                     'episode':{'season':1,'number':1}} if episode else movie(n,stamp))
        bucket='episodes' if episode else 'movies'
        setattr(api,bucket,[row(1,STAMP)])
        await provider.poll('1')
        setattr(api,bucket,([row(1,STAMP)] if keep_old else [])+[row(2,'2026-10-06T10:00:00Z')]);api.revision+=1
        app.deliver_mdblist_play.return_value=False
        await provider.poll('1')
        assert app.deliver_mdblist_play.await_args.args[-2]['is_rewatch']==keep_old
        assert len(await store.get_provider_plays('42',provider='mdblist'))==1
        app.deliver_mdblist_play.return_value=True
        await provider.poll('1')
        assert app.deliver_mdblist_play.await_args.args[-2]['is_rewatch']==keep_old
        assert len(await store.get_provider_plays('42',provider='mdblist'))==(2 if keep_old else 1)
    asyncio.run(run())


@pytest.mark.parametrize('episode',[False,True])
@pytest.mark.parametrize('keep_old',[False,True])
@pytest.mark.parametrize('remove_first',[False,True])
def test_wetrakr_projects_journal_removals_before_rewatch_label(tmp_path,monkeypatch,episode,keep_old,remove_first):
    async def run():
        from tests.test_wetrakr_sync import Client,Auth
        monkeypatch.setattr(storage_module,'DATA_PATH',str(tmp_path/'store.json'))
        store=storage_module.Storage()
        await store.link_wetrakr('1','42',{'access_token':'a','refresh_token':'r'},{'id':7})
        client=Client();sync=WeTrakrSync(client,Auth(),store)
        old={'type':'episode' if episode else 'movie','id':50,'play_id':'old','show_id':10,
             'season_number':1,'number':1,'watched_at':'2026-09-28T01:00:00Z'}
        async def history(token,target):yield [old] if target==('episodes' if episode else 'movies') else []
        client.compact_history=history
        async def target():return (await store.get_provider_targets('wetrakr','1',active_only=True))[0]
        async def resolve(play):
            return {**play,'item_key':'wetrakr:episode:10:1:1' if episode else 'wetrakr:movie:50'}
        delivered=[]
        async def deliver(change,row):
            if change['action']=='added':delivered.append(change['is_rewatch'])
            return True
        await sync.poll(await target(),deliver,resolve_play=resolve)
        new={**old,'entry_id':'new','category':'watched','status':'added','play_id':'new',
             'action_at':'2026-09-28T03:00:00Z','watched_at':'2026-09-28T03:00:00Z'}
        removed={**old,'entry_id':'removed','category':'watched','status':'removed','action_at':new['action_at']}
        client.rows=[new] if keep_old else ([removed,new] if remove_first else [new,removed])
        await sync.poll(await target(),deliver,resolve_play=resolve)
        assert delivered==[keep_old]
        await sync.poll(await target(),deliver,resolve_play=resolve)
        assert delivered==[keep_old]
    asyncio.run(run())


@pytest.mark.parametrize('episode',[False,True])
@pytest.mark.parametrize('session',[None,123])
def test_simkl_changed_timestamp_requires_explicit_session_for_rewatch_label(tmp_path,monkeypatch,episode,session):
    async def run():
        from test_notification_preview import bot
        monkeypatch.setattr(storage_module,'DATA_PATH',str(tmp_path/'store.json'))
        store=storage_module.Storage();monkeypatch.setattr(bot,'storage',store)
        await store.link_user('1','42','token',None,'Viewer','2026-09-20T00:00:00Z')
        key=bot.episode_key('shows',77,1,1) if episode else bot.movie_key('movies',77)
        await store.record_activity_batch('1','42',[key],{key:'2026-10-01T10:00:00Z'},[])
        monkeypatch.setattr(bot,'prefs',AsyncMock(return_value={'episode_code':False,'show_imdb':False,'show_mal':False,'activity_text':'detailed','artwork':'poster'}))
        monkeypatch.setattr(bot,'episode_media',AsyncMock(return_value=(None,'Pilot',None,24)))
        monkeypatch.setattr(bot,'evaluate_achievements',AsyncMock(return_value=[]))
        monkeypatch.setattr(bot,'feature_enabled',AsyncMock(return_value=False))
        descriptions=[]
        monkeypatch.setattr(bot,'build_embed',lambda t,d,*args:descriptions.append(d) or object())
        monkeypatch.setattr(bot,'send_embed',AsyncMock(return_value=True))
        item={'rewatch_id':session,'last_watched_at':'2026-10-02T10:00:00Z',
              'show' if episode else 'movie':{'title':'Title','ids':{'simkl':77}},
              'seasons':[{'number':1,'episodes':[{'number':1,'watched_at':'2026-10-02T10:00:00Z'}]}]}
        if episode:await bot.process_shows(object(),'1','42','Viewer',object(),'shows',[item],None)
        else:await bot.process_movies(object(),'1','42','Viewer',object(),[item],bot.parse_iso('2026-09-20T00:00:00Z'),None)
        assert descriptions and descriptions[0].startswith('Rewatched' if session else 'Watched')
    asyncio.run(run())


def test_duplicate_rows_do_not_establish_another_viewing():
    row={'item_key':'same','source_event_id':'one','watched_at':'2026-10-27T06:00:00Z'}
    assert rewatch_flags([row,row],lambda p:p['item_key'])=={'one':False}
