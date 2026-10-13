import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from types import SimpleNamespace

from trackerbot.core.community import watchathon_goal, watchathon_rewards, minute_contributions, community_week
from trackerbot.core import storage as storage_module


def test_watchathon_scaling_and_reward_split():
    assert watchathon_goal(2,40)['target']==360
    assert watchathon_goal(2,40)['required_contributors']==2
    assert watchathon_goal(0,40)['target']==360
    assert watchathon_goal(1,40)['required_contributors']==1
    assert watchathon_goal(20,40)['required_contributors']==3
    record=watchathon_goal(2,40)
    assert watchathon_rewards({'1':360},record)=={}
    assert watchathon_rewards({'1':331,'2':29},record)=={}
    assert watchathon_rewards({'1':300,'2':60},record)=={'1':480,'2':240}
    record={**record,'pool':721}
    assert sum(watchathon_rewards({'1':300.25,'2':60.5},record).values())==721


def test_occurrences_count_once_and_respect_selected_source_and_account():
    start=datetime(2026,10,19,tzinfo=timezone.utc);end=start+timedelta(days=7)
    stamp='2026-10-20T12:00:00Z'
    user={'mdblist':{'account_id':9},'progression':{
        'xp_events':[{'event_key':'a','runtime_minutes':72,'at':stamp},
                     {'event_key':'b','runtime_minutes':72,'at':stamp}],
        'occurrence_ledger':{'o':{'award_keys':['a','b'],'observations':{
            'mdblist:9/1:'+stamp:{'provider':'mdblist','identity':{'watched_at':stamp}},
            'simkl:1':{'provider':'simkl','identity':{'watched_at':stamp}}}}}}}
    assert minute_contributions({'1':user},['1'],start,end,{'1':'mdblist'})=={'1':72}
    assert minute_contributions({'1':user},['1'],start,end,{'1':'wetrakr'})=={}
    user['mdblist']['account_id']=10
    assert minute_contributions({'1':user},['1'],start,end,{'1':'mdblist'})=={}


def test_fixed_goal_milestones_payout_restart_and_reversal(tmp_path,monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module,'DATA_PATH',str(tmp_path/'store.json'))
        store=storage_module.Storage()
        for uid in ('1','2','3'):
            await store.link_user('10',uid,'token',None,'Viewer','2026-10-01T00:00:00Z')
        async def watch(uid,minutes,stamp,number):
            key=f'episode:{uid}:{number}'
            await store.record_activity_batch('10',uid,[key],{key:stamp},[
                {'media_type':'episode','title':'Show','item_key':f'series:shows:{uid}:1:{number}',
                 'watched_at':stamp,'amount':round(minutes*4),'runtime_minutes':minutes,'ids':{'tmdb':int(uid)}}])
        await watch('1',60,'2026-10-15T12:00:00Z',1)
        await watch('2',60,'2026-10-15T12:00:00Z',1)
        now=datetime(2026,10,20,tzinfo=timezone.utc)
        key,start,end=community_week(now,'UTC')
        first=await store.get_community_state('10',key,start,end,now)
        assert first['kind']=='minutes' and first['target']==360 and first['required_contributors']==2
        await watch('1',300,'2026-10-20T12:00:00Z',2)
        state=await store.get_community_state('10',key,start,end,now)
        assert state['pending_milestone']==75 and state['status']=='active'
        await store.ack_community_milestone('10',key,75)
        assert not (await store.get_community_state('10',key,start,end,now))['pending_milestone']
        await watch('2',60,'2026-10-20T12:00:00Z',2)
        reached=await store.get_community_state('10',key,start,end,now)
        assert reached['status']=='goal_reached' and not reached['awards']
        await store.link_user('10','4','token',None,'New','2026-10-20T00:00:00Z')
        assert (await store.get_community_state('10',key,start,end,now))['target']==360
        paid=await store.get_community_state('10',key,start,end,end+timedelta(hours=1))
        assert paid['awards']=={'1':480,'2':240}
        reloaded=storage_module.Storage()
        again=await reloaded.get_community_state('10',key,start,end,end+timedelta(hours=1))
        assert again['changes']==[] and again['awards']==paid['awards']
        # Remove the second member's credited occurrence; the contributor requirement fails.
        await reloaded.reconcile_watch_xp('2',set(),{'episode'})
        reversed_state=await reloaded.get_community_state('10',key,start,end,end+timedelta(hours=2))
        assert reversed_state['awards']=={} and reversed_state['status']=='missed'
        assert {change['uid']:change['delta'] for change in reversed_state['changes']}=={'1':-480,'2':-240}
    asyncio.run(run())


def test_milestone_post_failure_retries_and_success_acknowledges(monkeypatch):
    from test_notification_preview import bot
    async def run():
        now=datetime.now(timezone.utc)
        state={'key':'week','end':(now+timedelta(days=1)).isoformat(),'pending_milestone':50,
               'total':90,'target':180,'qualified_contributors':1,'required_contributors':1,
               'remaining':90,'pending_notifications':[],'changes':[]}
        monkeypatch.setattr(bot,'feature_enabled',AsyncMock(return_value=True))
        monkeypatch.setattr(bot.storage,'get_timezone',AsyncMock(return_value={'name':'UTC'}))
        monkeypatch.setattr(bot.storage,'get_community_state',AsyncMock(return_value=state))
        monkeypatch.setattr(bot.storage,'get_channel',AsyncMock(return_value='9'))
        ack=AsyncMock();monkeypatch.setattr(bot.storage,'ack_community_milestone',ack)
        channel=SimpleNamespace(send=AsyncMock(side_effect=RuntimeError('Discord unavailable')))
        monkeypatch.setattr(bot.bot,'get_channel',lambda _:channel)
        await bot.refresh_community_state('10');ack.assert_not_awaited()
        channel.send.side_effect=None
        await bot.refresh_community_state('10');ack.assert_awaited_once()
    asyncio.run(run())
