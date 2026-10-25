import asyncio
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from trackerbot.core import storage as storage_module
from trackerbot.core.activity_details import ActivityDetails, rating_for, ids_match
from trackerbot.core.completion import outcome
from trackerbot.core.watch_delivery import WatchActivity, WatchBatch, current_details
from trackerbot.core.providers import ProviderAccount
from trackerbot.integrations.personal_ratings import normalize, fetch, signature


def catalog(status='Returning Series',finale=True):
    return {'status':status,'seasons':[{'season_number':1,'episode_count':2,'air_date':'2020-01-01'}]}, {
        1:{'episodes':[{'id':11,'episode_number':1,'air_date':'2020-01-01'},
                       {'id':12,'episode_number':2,'air_date':'2020-01-08','episode_type':'finale' if finale else 'standard'}]}}


def test_completion_requires_full_verified_catalog():
    series,seasons=catalog()
    assert outcome(series,seasons,{(1,1)},1) is None
    assert outcome(series,seasons,{(1,1),(1,2)},1)['kind']=='season'
    assert outcome(series,seasons,{(1,1),(1,2)},1,display_season=9)['kind']=='caught_up'
    series,seasons=catalog(finale=False)
    assert outcome(series,seasons,{(1,1),(1,2)},1)['kind']=='caught_up'
    series['status']='Ended'
    assert outcome(series,seasons,{(1,1),(1,2)},1)['kind']=='series'
    seasons[1]['episodes'][1]['air_date']='2099-01-01'
    assert outcome(series,seasons,{(1,1)},1)['kind']=='caught_up'
    series['seasons'][0]['episode_count']=3
    assert outcome(series,seasons,{(1,1),(1,2)},1) is None
    seasons[1]['episodes'][1]['air_date']=None
    assert outcome(series,seasons,{(1,1),(1,2)},1) is None


def test_personal_scopes_do_not_substitute_series_for_episode():
    rows=[normalize({'show':{'ids':{'tmdb':1}},'user_rating':9},'show','simkl')]
    meta={'ids':{'tmdb':1},'movie':False,'season':1,'episodes':[2]}
    assert rating_for(rows,meta) is None
    assert 'series rating' in rating_for(rows,meta,{'kind':'series'})
    rows.append(normalize({'show':{'ids':{'tmdb':1}},'season':1,'rating_precise':8.5},'season','mdblist'))
    assert 'season rating' in rating_for(rows,meta,{'kind':'season','season':1})
    rows.append(normalize({'show_id':7,'season_number':2019,'number':26,'rating':0},'episode','wetrakr'))
    assert '**0/10**' in rating_for(rows,{'ids':{'wetrakr':7},'season':1,'episodes':[892],
                                        'source_season':2019,'source_episodes':[26]})
    assert not ids_match({'tmdb':1},{'tmdb':2})
    assert ids_match({'simkl':5,'tmdb':1},{'simkl':5,'tmdb':2})


@pytest.mark.parametrize('row,kind,source',[
    ({'rating':9},'movie','simkl'),
    ({'ids':{'tmdb':1},'rating':11},'movie','mdblist'),
    ({'ids':{'tmdb':100},'rating':8,'season':1},'season','mdblist'),
    ({'show_ids':{'tmdb':1},'rating':8,'season':1},'episode','wetrakr'),
])
def test_invalid_snapshots_cannot_erase_previous_ratings(row,kind,source):
    with pytest.raises(ValueError):normalize(row,kind,source)


def test_provider_rating_readers_cursor_precision_and_change_stamps():
    async def run():
        account=ProviderAccount('wetrakr','7','Viewer','1')
        calls=[]
        async def request(method,path,token,params):
            calls.append((path,params))
            if path.endswith('/movies'):
                if not params.get('after'):return [{'id':5,'ids':{'tmdb':1},'rating':8.5}],{'X-Pagination-Next':'opaque-token'}
                return [{'id':6,'ids':{'tmdb':2},'rating':0}],{}
            return [],{}
        app=SimpleNamespace(wetrakr_sync=SimpleNamespace(auth=SimpleNamespace(access_token=AsyncMock(return_value='a'))),
                            wetrakr=SimpleNamespace(last_activities=AsyncMock(return_value={'movies':{'last_rated_at':'x'}}),_request=request))
        mark,rows=await fetch(app,'wetrakr',account)
        assert [row['score'] for row in rows]==[8.5,0]
        assert calls[1][1]['after']=='opaque-token' and 'page' not in calls[1][1]
        calls.clear();assert (await fetch(app,'wetrakr',account,mark))[1] is None
        assert calls==[]
        async def pages(*args):
            yield {'movies':[{'tmdb':1,'rating':9,'rating_precise':8.5}],
                   'shows':[],'seasons':[{'show':{'ids':{'tmdb':2}},'season_number':1,'rating':7}],
                   'episodes':[{'rating':9,'rating_precise':8.5,'episode':{'season':2,'number':1,'ids':{'tmdb':100},
                               'show':{'title':'Show','ids':{'tmdb':2,'tvdb':3}}}}]}
        app=SimpleNamespace(mdblist_auth=SimpleNamespace(access_token=AsyncMock(return_value='a')),
                            mdblist_tracking=SimpleNamespace(last_activities=AsyncMock(return_value={'rated_at':'x'}),_cursor_pages=pages))
        _,rows=await fetch(app,'mdblist',ProviderAccount('mdblist','7','Viewer','1'))
        assert rows[0]['score']==8.5 and rows[1]['kind']=='season'
        assert rows[2]=={'kind':'episode','ids':{'tmdb':'2','tvdb':'3'},'score':8.5,'scale':10,'season':2,'episode':1}
        app=SimpleNamespace(storage=SimpleNamespace(get_user=AsyncMock(return_value={})),valid_token=AsyncMock(return_value='a'),
                            simkl=SimpleNamespace(get_activities=AsyncMock(return_value={'movies':{'rated_at':'x'}}),
                            _params=lambda **kw:{'client_id':'app',**kw},
                            _get=AsyncMock(return_value={'movies':[{'movie':{'ids':{'simkl':1}},'user_rating':8}], 'shows':[], 'anime':[]})))
        _,rows=await fetch(app,'simkl',ProviderAccount('simkl','7','Viewer','1'))
        assert rows[0]['ids']=={'simkl':'1'} and rows[0]['score']==8
        assert app.simkl._get.await_count==3
        assert app.simkl._get.await_args.kwargs['params']['client_id']=='app'
        app.simkl._get.return_value={}
        assert (await fetch(app,'simkl',ProviderAccount('simkl','7','Viewer','1')))[1]==[]
        assert signature({'movies':{'rated_at':'a'},'all':'b'})==signature({'movies':{'rated_at':'a'},'all':'c'})
    asyncio.run(run())


async def setup(tmp_path,monkeypatch,users=('1',)):
    monkeypatch.setattr(storage_module,'DATA_PATH',str(tmp_path/'store.json'))
    store=storage_module.Storage()
    for uid in users:await store.link_user('10',uid,'token',None,'Viewer','2020-01-01T00:00:00Z',simkl_account_id=uid)
    series,seasons=catalog()
    tmdb=SimpleNamespace(_get_series_details=AsyncMock(return_value=series),
                         _get_season_details=AsyncMock(side_effect=lambda _,n:seasons.get(n)),_series_cache={},_season_cache={})
    provider=SimpleNamespace(link=AsyncMock(side_effect=lambda uid:ProviderAccount('simkl',str(uid),'Viewer',str(uid))))
    app=SimpleNamespace(storage=store,tmdb=tmdb,provider_registry=SimpleNamespace(get=lambda _:provider),
                        bot=SimpleNamespace(get_channel=lambda _:channel))
    engine=ActivityDetails(app)
    for uid in users:engine.contexts[('10',uid)]={'source':'simkl','account':uid,'state':{'ratings':[]}}
    channel=SimpleNamespace(id=20,get_partial_message=lambda _:message)
    message=SimpleNamespace(id=30,channel=channel,edit=AsyncMock())
    sent=[]
    async def sender(ch,embed,what):
        sent.append(embed.copy());await engine.remember(message);return True
    return store,app,engine,channel,message,sent,sender


async def watch(store,number):
    stamp='2020-01-01T12:00:00Z'
    key=f'episode:show:1:1:{number}'
    await store.record_activity_batch('10','1',[key],{key:stamp},[
        {'media_type':'episode','item_key':f'series:shows:1:1:{number}','title':'Show',
         'ids':{'tmdb':1},'watched_at':stamp,'amount':96,'runtime_minutes':24}])


def test_late_movie_rating_edit_removal_restart_and_no_xp_changes(tmp_path,monkeypatch):
    async def run():
        store,app,engine,ch,message,sent,sender=await setup(tmp_path,monkeypatch)
        base=discord.Embed(title='Film',description='Watched **Film**\n⭐ IMDb 8.9/10')
        assert await engine.single(sender,ch,base,'10','1','simkl',{'ids':{'tmdb':1},'movie':True})
        original=await store.get_progression('1')
        row=normalize({'tmdb':1,'rating':8.5},'movie','mdblist')
        engine.contexts[('10','1')]['state']['ratings']=[row]
        await engine.finish()
        edited=message.edit.await_args.kwargs['embed']
        assert '**8.5/10**' in edited.description and 'IMDb 8.9/10' in edited.description
        assert message.edit.await_args.kwargs['allowed_mentions'].everyone is False
        await engine.finish();assert message.edit.await_count==1
        # Durable bindings survive a process restart.
        app.storage=storage_module.Storage();second=ActivityDetails(app);second.contexts=engine.contexts
        second.contexts[('10','1')]['state']['ratings']=[]
        await second.finish()
        assert 'Your rating' not in message.edit.await_args.kwargs['embed'].description
        assert await app.storage.get_progression('1')==original
        rows=await app.storage.get_activity_messages();rows[0]['posted_at']=(datetime.now(timezone.utc)-timedelta(days=8)).isoformat()
        await app.storage.save_activity_message(rows[0]);message.edit.reset_mock()
        await second.finish();message.edit.assert_not_awaited()
    asyncio.run(run())


def test_season_milestone_folded_into_final_watch_and_invalidated_on_removal(tmp_path,monkeypatch):
    async def run():
        store,app,engine,ch,message,sent,sender=await setup(tmp_path,monkeypatch)
        await watch(store,1)
        engine.contexts[('10','1')]['state']['ratings']=[normalize({'show_ids':{'tmdb':1},'season':1,'rating':9},'season','mdblist')]
        meta={'ids':{'tmdb':1},'movie':False,'season':1,'episodes':[2]}
        base=discord.Embed(description='Watched **S1E02**\n⭐ IMDb 8.2/10')
        await engine.single(sender,ch,base,'10','1','simkl',meta)
        assert 'Finished **Season 1**' in sent[-1].description and 'Your season rating' in sent[-1].description
        await watch(store,2)
        # A native completed rollup does not add a duplicate post.
        await engine.single(sender,ch,discord.Embed(description='Completed **Show**'),'10','1','simkl',{'ids':{'tmdb':1},'status':'completed'})
        assert len(sent)==1
        await store.reconcile_watch_xp('1',set(),{'episode'})
        # Remove statistics as the provider's actual unwatch reconciliation does.
        await store.reconcile_watch_statistics('10','1',set(),{'episode'})
        await engine.finish()
        assert 'Finished' not in message.edit.await_args.kwargs['embed'].description
    asyncio.run(run())


def test_mixed_member_ratings_and_visibility_are_not_copied_from_first(tmp_path,monkeypatch):
    async def run():
        store,app,engine,ch,message,sent,sender=await setup(tmp_path,monkeypatch,('1','2'))
        await store.link_wetrakr('10','2',{'access_token':'a','refresh_token':'r'},{'id':99})
        await store.set_activity_provider('10','2','wetrakr')
        original=app.provider_registry.get('simkl')
        wetrakr=SimpleNamespace(link=AsyncMock(return_value=ProviderAccount('wetrakr','99','Viewer','2')))
        app.provider_registry=SimpleNamespace(get=lambda source:wetrakr if source=='wetrakr' else original)
        engine.contexts[('10','2')].update(source='wetrakr',account='99')
        for uid,score in (('1',8),('2',7)):
            engine.contexts[('10',uid)]['state']['ratings']=[normalize({'tmdb':1,'rating':score},'movie','simkl')]
        meta={'ids':{'tmdb':1},'movie':True}
        batch=WatchBatch(sender)
        for uid in ('1','2'):
            batch.add(WatchActivity('10',uid,'movies',ch,('movie',1),(datetime.now(timezone.utc),),
                discord.Embed(description='Watched Film\n⭐ IMDb 8.9/10'), '**Film**', AsyncMock(), movie=True,
                details={'source':'simkl' if uid=='1' else 'wetrakr','meta':meta}))
        token=current_details.set(engine)
        try:await batch.deliver()
        finally:current_details.reset(token)
        assert len(sent)==1 and '<@1> · Your rating: **8/10**' in sent[0].description
        assert '<@2> · Your rating: **7/10**' in sent[0].description
        await store.set_embed_preferences('2',show_personal=False)
        await engine.finish()
        desc=message.edit.await_args.kwargs['embed'].description
        assert 'Your rating: **7/10**' not in desc and 'Your rating: **8/10**' in desc
        assert len(await store.get_activity_messages())==1
    asyncio.run(run())


def test_failed_edits_retry_and_source_switch_freezes_snapshot(tmp_path,monkeypatch):
    async def run():
        store,app,engine,ch,message,sent,sender=await setup(tmp_path,monkeypatch)
        await engine.single(sender,ch,discord.Embed(description='Watched Film'),'10','1','simkl',{'ids':{'tmdb':1},'movie':True})
        engine.contexts[('10','1')]['state']['ratings']=[normalize({'tmdb':1,'rating':8},'movie','simkl')]
        message.edit.side_effect=RuntimeError('temporary')
        await engine.finish();assert message.edit.await_count==1
        message.edit.side_effect=None
        await engine.finish();assert message.edit.await_count==2
        await store.link_wetrakr('10','1',{'access_token':'a','refresh_token':'r'},{'id':99})
        await store.set_activity_provider('10','1','wetrakr')
        engine.contexts[('10','1')]['state']['ratings']=[]
        await engine.finish();assert message.edit.await_count==2
    asyncio.run(run())


def test_rating_snapshot_shared_between_guilds_and_failed_refresh_retains_scores(tmp_path,monkeypatch):
    async def run():
        store,app,engine,*_=await setup(tmp_path,monkeypatch)
        await store.link_user('11','1','token',None,'Viewer','2020-01-01T00:00:00Z',simkl_account_id='1')
        row=normalize({'tmdb':1,'rating':8},'movie','simkl')
        await store.save_activity_detail_state('10','1','simkl','1',{'ratings_signature':'old','ratings':[row]})
        app.SIMKL_CLIENT_ID='app';app.wetrakr_sync=None;app.mdblist_tracking=None
        provider=app.provider_registry.get('simkl')
        app.provider_registry=SimpleNamespace(get=lambda _:provider,manifests=lambda:[SimpleNamespace(name='simkl')])
        reader=AsyncMock(return_value=('old',None))
        monkeypatch.setattr('trackerbot.core.activity_details.fetch',reader)
        await engine.begin()
        assert reader.await_count==1
        assert engine.contexts[('11','1')]['state']['ratings']==[row]
        reader.side_effect=ValueError('invalid response')
        await engine.begin()
        assert engine.contexts[('10','1')]['state']['ratings']==[row]
        assert engine.contexts[('10','1')]['state']['error']=='ValueError'
        provider.paused_until={'1':float('inf')};reader.reset_mock()
        await engine.begin();reader.assert_not_awaited()
    asyncio.run(run())


def test_simkl_import_before_delivery_and_next_cycle_rollup_do_not_hide_or_duplicate_milestone(tmp_path,monkeypatch):
    async def run():
        store,app,engine,ch,message,sent,sender=await setup(tmp_path,monkeypatch)
        await watch(store,1)
        part={'guild':'10','user':'1','source':'simkl','account':'1'}
        engine.contexts[('10','1')]['before_watches']=await engine.watches(part)
        await watch(store,2)  # Full reconciliation precedes actual activity delivery.
        meta={'ids':{'tmdb':1},'season':1,'episodes':[2],'new_watch':True}
        await engine.single(sender,ch,discord.Embed(description='Watched S1E02\n⭐ IMDb 8.2/10'),'10','1','simkl',meta)
        assert 'Finished **Season 1**' in sent[-1].description
        engine.sent=[]  # Native completed status arrives on the next poll.
        await engine.single(sender,ch,discord.Embed(description='Completed Show'),'10','1','simkl',{'ids':{'tmdb':1},'status':'completed'})
        assert len(sent)==1
    asyncio.run(run())


def test_catalog_fetches_current_season_only_until_near_completion(tmp_path,monkeypatch):
    async def run():
        _,app,engine,*_=await setup(tmp_path,monkeypatch)
        series={'status':'Returning Series','seasons':[{'season_number':n,'episode_count':20} for n in range(1,31)],
                'last_episode_to_air':{'season_number':30}}
        app.tmdb._get_series_details.return_value=series
        app.tmdb._get_season_details.side_effect=lambda _,n:{'episodes':[{'episode_number':i,'air_date':'2020-01-01'} for i in range(1,21)]}
        meta={'ids':{'tmdb':1},'season':1,'episodes':[1]}
        await engine.catalog(meta)
        assert app.tmdb._get_season_details.await_count==1
        stamp,value=engine.catalogs['1'];engine.catalogs['1']=(stamp-4000,value)
        await engine.catalog(meta)
        assert app.tmdb._get_series_details.await_count==2
        assert app.tmdb._get_season_details.await_count==1  # Immutable old season reused.
    asyncio.run(run())


def test_native_status_requires_evidence_and_ended_series_gets_correct_label(tmp_path,monkeypatch):
    async def run():
        store,app,engine,ch,message,sent,sender=await setup(tmp_path,monkeypatch)
        await watch(store,1);await watch(store,2)
        meta={'ids':{'tmdb':1},'status':'completed'}
        app.tmdb._get_series_details.return_value['status']='Ended'
        await engine.single(sender,ch,discord.Embed(description='Completed **Show**'),'10','1','simkl',meta)
        assert sent[-1].description.startswith('Completed **Show**')
        engine.catalogs.clear();app.tmdb._get_series_details.return_value['status']='Returning Series'
        await engine.single(sender,ch,discord.Embed(description='Completed **Show**'),'10','1','simkl',meta)
        assert sent[-1].description.startswith('Finished Season 1 of **Show**')
        engine.catalogs.clear();app.tmdb._get_series_details.return_value={}
        await engine.single(sender,ch,discord.Embed(description='Completed **Show**'),'10','1','simkl',meta)
        assert sent[-1].description.startswith('Marked as watched **Show**')
    asyncio.run(run())


def test_deleted_message_discards_binding_without_reposting(tmp_path,monkeypatch):
    async def run():
        store,app,engine,ch,message,sent,sender=await setup(tmp_path,monkeypatch)
        await engine.single(sender,ch,discord.Embed(description='Watched Film'),'10','1','simkl',{'ids':{'tmdb':1},'movie':True})
        engine.contexts[('10','1')]['state']['ratings']=[normalize({'tmdb':1,'rating':8},'movie','simkl')]
        message.edit.side_effect=discord.NotFound(SimpleNamespace(status=404,reason='Not Found'),{'message':'Unknown Message','code':10008})
        await engine.finish()
        assert await store.get_activity_messages()==[] and len(sent)==1
    asyncio.run(run())


def test_personal_visibility_server_override_and_group_binding_retention(tmp_path,monkeypatch):
    async def run():
        store,app,engine,ch,message,sent,sender=await setup(tmp_path,monkeypatch,('1','2'))
        await store.set_embed_preferences('1',show_personal=True)
        await store.set_server_embed_preferences('10',show_personal=False,force_override=True)
        assert (await store.get_embed_preferences('10','1'))['show_personal'] is False
        await store.set_server_embed_preferences('10',force_override=False)
        assert (await store.get_embed_preferences('10','1'))['show_personal'] is True
        meta={'ids':{'tmdb':1},'movie':True}
        parts=[engine.participant('10',uid,'simkl',meta) for uid in ('1','2')]
        await engine.send(sender,ch,discord.Embed(description='Watched Film together'),parts)
        message.id=31
        await engine.single(sender,ch,discord.Embed(description='Rewatched Film'),'10','1','simkl',meta)
        assert {row['message'] for row in await store.get_activity_messages()}=={'30','31'}
        message.id=32
        await engine.single(sender,ch,discord.Embed(description='Rewatched Film'),'10','2','simkl',meta)
        assert {row['message'] for row in await store.get_activity_messages()}=={'31','32'}
    asyncio.run(run())
