import asyncio
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from trackerbot.metadata.community_completion import refresh_community_completions
from trackerbot.core.community_events import event_goal,score_event


def test_verified_completion_qualifies_one_episode_and_error_retains_last_proof():
    async def run():
        now=datetime(2026,12,1,tzinfo=timezone.utc)
        part={'guild':'1','user':'42','source':'simkl','account':'123','milestone':{'kind':'season'},
              'meta':{'movie':False,'ids':{'tmdb':99},'season':1,'episodes':[12]}}
        watch={'item_key':'series:shows:9:1:12','watched_at':now.isoformat(),'ids':{'tmdb':99}}
        storage=SimpleNamespace(community_completion_inputs=AsyncMock(return_value=({},'UTC')),
            get_activity_messages=AsyncMock(return_value=[{'base':{'timestamp':now.isoformat()},'parts':[part]}]),
            cache_community_completions=AsyncMock())
        service=SimpleNamespace(valid=AsyncMock(return_value=True),milestone=AsyncMock(return_value={'kind':'season'}),
                                watches=AsyncMock(return_value=[watch]))
        app=SimpleNamespace(storage=storage,activity_details=service)
        await refresh_community_completions(app,'1',now)
        proofs=storage.cache_community_completions.await_args.args[1]
        proof=next(iter(proofs.values()))
        assert proof['canonical']=='tv:tmdb:99' and proof['support']==['series:shows:9:1:12|'+now.isoformat()]
        row={'uid':'42','movie':False,'meta_key':'tv:tmdb:99','title':'Series','season':1,'episode':12,'completion_verified':True}
        meta={'tv:tmdb:99':{'canonical':'tv:tmdb:99','title':'Series','genres':['Drama'],'year':2020}}
        assert score_event(event_goal('2026-11-30',1),[row],meta)['eligible_titles']==1
        storage.community_completion_inputs.return_value=(proofs,'UTC');service.milestone.side_effect=TimeoutError()
        await refresh_community_completions(app,'1',now+timedelta(hours=1))
        assert next(iter(storage.cache_community_completions.await_args.args[1].values()))['support']==proof['support']
        service.milestone.side_effect=None;service.milestone.return_value=None
        await refresh_community_completions(app,'1',now+timedelta(hours=2))
        assert next(iter(storage.cache_community_completions.await_args.args[1].values()))['support']==[]
    asyncio.run(run())


def test_completion_support_disappears_when_a_supporting_watch_is_removed(tmp_path,monkeypatch):
    import trackerbot.core.storage as module
    async def run():
        monkeypatch.setattr(module,'DATA_PATH',str(tmp_path/'store.json'));store=module.Storage()
        at='2026-12-01T00:00:00+00:00';key='series:shows:9:1:12'
        await store.link_user('1','42','token',None,'Viewer',at,simkl_account_id=123)
        await store.record_watch('1','42','episode','Series',key,at)
        await store.cache_community_completions('1',{'proof':{'uid':'42','provider':'simkl','account':'123',
            'at':at,'canonical':'tv:tmdb:99','support':[key+'|'+at]}})
        guild=store._data['guilds']['1'];guild['community_metadata']={'tv:tmdb:99':{'canonical':'tv:tmdb:99'}}
        start=datetime(2026,11,30,tzinfo=timezone.utc);end=start+timedelta(days=7)
        watches=[{'uid':'42','meta_key':'tv:tmdb:99'}]
        store._community_completion_flags(guild,watches,{'42':'simkl'},start,end)
        assert watches[0]['completion_verified']
        guild['users']['42']['statistics']['watch_events'].clear()
        watches=[{'uid':'42','meta_key':'tv:tmdb:99'}]
        store._community_completion_flags(guild,watches,{'42':'simkl'},start,end)
        assert not watches[0].get('completion_verified')
    asyncio.run(run())
