import asyncio
from datetime import datetime,timedelta,timezone
import copy
from trackerbot.core.community_events import event_goal,score_event,credited_watches,event_state


def movie(uid,key,genres=('Drama',),year=2020):
    row={'uid':str(uid),'occurrence':f'{uid}:{key}','movie':True,'meta_key':key,'title':key,
         'at':'2026-11-24T12:00:00Z','minutes':120,'season':None,'episode':None,'ids':{}}
    info={'canonical':key,'title':key,'genres':list(genres),'year':year,'seasons':{}}
    return row,info


def collection(entries):
    watches=[];metadata={}
    for args in entries:
        row,info=movie(*args);watches.append(row);metadata[row['meta_key']]=info
    return watches,metadata


def test_rotation_goals_frozen_and_small_groups():
    weeks=['2026-11-23','2026-11-30','2026-12-07','2026-12-14','2026-12-21']
    assert [event_goal(w,4)['kind'] for w in weeks]==['watchathon','genres','bingo','decades','watchathon']
    first=event_goal(weeks[0],4)
    assert (first['target'],first['pool'],first['required_contributors'])==(2400,12000,3)
    assert event_goal(weeks[0],2)['required_contributors']==2
    assert event_goal(weeks[0],1)['required_contributors']==1


def test_watchathon_qualification_and_exact_reward_split():
    goal=event_goal('2026-11-23',4)
    watches,meta=collection([('a','one'),('b','two'),('c','three')])
    for row,minutes in zip(watches,[1200,800,400]):row['minutes']=minutes
    score=score_event(goal,watches,meta)
    assert score['estimated_awards']=={'a':5400,'b':4000,'c':2600}
    watches[-1]['minutes']=30
    score=score_event(goal,watches,meta)
    assert not score['goal_reached'] and score['estimated_awards']=={}


def test_genres_maximum_matching_and_shared_title_counts_once():
    goal=event_goal('2026-11-30',1);goal.update(target=2,minimum_titles=2)
    watches,meta=collection([('a','mixed',('Comedy','Drama')),('a','comedy',('Comedy',)),
                             ('b','mixed',('Comedy','Drama'))])
    score=score_event(goal,watches,meta)
    assert score['total']==2 and score['eligible_titles']==2
    assert score['goal_reached'] and sum(score['estimated_awards'].values())==3000
    assert {o['label']:o['titles'][0] for o in score['objectives']}=={'Comedy':'comedy','Drama':'mixed'}
    # A single title cannot fill multiple genre slots, regardless of rewatches/members.
    assert score_event(goal,[watches[0],watches[2]],meta)['total']==1


def test_series_require_distinct_regular_episodes_and_verified_short_seasons():
    row,info=movie('a','series',('Drama',));row['movie']=False
    row.update(season=1,episode=1);meta={'series':info}
    goal=event_goal('2026-11-30',1)
    repeated=[dict(row,occurrence=str(i)) for i in range(5)]
    assert score_event(goal,repeated,meta)['eligible_titles']==0
    regular=[dict(row,episode=n) for n in (1,2,3)]
    assert score_event(goal,regular,meta)['eligible_titles']==1
    assert score_event(goal,[dict(row,season=0,episode=n) for n in (1,2,3)],meta)['eligible_titles']==0
    info['seasons']={'1':2}
    assert score_event(goal,regular[:2],meta)['eligible_titles']==1


def test_decade_collections_and_unknown_metadata():
    goal=event_goal('2026-12-14',1)
    watches,meta=collection([('a','old',('Drama',),2001),('a','middle',('Drama',),2011),('a','new',('Drama',),2021)])
    score=score_event(goal,watches,meta)
    assert score['total']==3 and score['goal_reached']
    assert sum(score['estimated_awards'].values())==3000
    assert score_event(goal,watches,{})['pending_metadata']==3
    assert not score_event(goal,watches,{})['goal_reached']


def test_bingo_disallows_reusing_one_members_title_and_shared_credit():
    goal=event_goal('2026-12-07',2)
    watches,meta=collection([('a','film',('Comedy','Animation','Mystery','Fantasy','Documentary'),1990),
                             ('b','film',('Comedy','Animation','Mystery','Fantasy','Documentary'),1990)])
    score=score_event(goal,watches,meta)
    # Either two separate squares or one shared square; never all six matching cells.
    assert score['total']<=2 and not score['goal_reached']
    assert sum(score['contributions'].values())<=2


def test_ledger_requires_selected_account_credit_and_dedupes_occurrences():
    identity={'watched_at':'2026-11-24T12:00:00Z','title':'Film','ids':{'tmdb':'1'},'season':None,'episode':None}
    event={'event_key':'award','media_type':'movie','runtime_minutes':120}
    user={'mdblist':{'account_id':'abc'},'progression':{'xp_events':[event],
        'occurrence_ledger':{'x':{'award_keys':['award'],'observations':{
            'simkl:1':{'provider':'simkl','identity':identity},
            'mdblist:abc/1:stamp':{'provider':'mdblist','identity':identity}}},
            'held':{'award_keys':[],'observations':{'simkl:2':{'provider':'simkl','identity':identity}}}}}}
    start=datetime(2026,11,23,tzinfo=timezone.utc);end=start+timedelta(days=7)
    assert len(credited_watches({'a':user},['a'],start,end,{'a':'simkl'}))==1
    assert len(credited_watches({'a':user},['a'],start,end,{'a':'mdblist'}))==1
    user['mdblist']['account_id']='different'
    assert credited_watches({'a':user},['a'],start,end,{'a':'mdblist'})==[]


def test_storage_payout_grace_idempotence_removal_and_readonly_preview(tmp_path,monkeypatch):
    import trackerbot.core.storage as module
    async def run():
        monkeypatch.setattr(module,'DATA_PATH',str(tmp_path/'store.json'));store=module.Storage()
        await store.link_user('1','a','token',None,'Viewer','2026-11-01T00:00:00Z')
        start=datetime(2026,11,23,tzinfo=timezone.utc);end=start+timedelta(days=7)
        # A credited single member baseline, five movie occurrences = 600 minutes.
        await store.get_progression('a')
        p=store._data['users']['a']['progression'];p['xp_events']=[];p['occurrence_ledger']={}
        p.update(xp=349000,lifetime_xp=349000)
        for n in range(5):
            key=f'e{n}';p['xp_events'].append({'event_key':key,'media_type':'movie','runtime_minutes':120,'amount':480})
            p['occurrence_ledger'][key]={'award_keys':[key],'observations':{key:{'provider':'simkl','identity':{
                'watched_at':'2026-11-24T12:00:00Z','title':str(n),'ids':{'tmdb':str(n+1)}}}}}
        state=await store.get_community_state('1','2026-11-23',start,end,start+timedelta(days=2))
        assert state['goal_reached'] and state['target']==600 and not state['awards']
        before=copy.deepcopy(store._data)
        preview=await store.preview_community_event('1','2026-11-23',start,end,start+timedelta(days=2),'genres')
        assert preview['preview'] and store._data==before
        assert not (await store.get_community_state('1','2026-11-23',start,end,end+timedelta(hours=5)))['awards']
        paid=await store.get_community_state('1','2026-11-23',start,end,end+timedelta(hours=6))
        assert paid['awards']=={'a':3000}
        progression=await store.get_progression('a')
        assert (progression['prestige'],progression['xp'])==(1,2000)
        await store.ack_community_notification('1','2026-11-23',paid['awards'])
        assert store._data['guilds']['1']['community_challenges']['2026-11-23']['delivery']['results'] is True
        repeated=await store.get_community_state('1','2026-11-23',start,end,end+timedelta(hours=7))
        assert repeated['changes']==[]
        p['occurrence_ledger'].pop('e0')
        reversed=await store.get_community_state('1','2026-11-23',start,end,end+timedelta(hours=8))
        assert not reversed['awards'] and reversed['changes'][0]['delta']==-3000
        progression=await store.get_progression('a')
        assert (progression['prestige'],progression['xp'],progression['lifetime_xp'])==(0,349000,349000)
    asyncio.run(run())


def test_complete_bingo_balances_shared_support_and_keeps_exact_pool():
    goal=event_goal('2026-12-07',4);entries=[]
    for uid in ('a','b','c'):entries.append((uid,'shared',('Comedy',),2020))
    entries += [('a','comedy',('Comedy',),2020),('a','classic',('Drama',),1990),
                ('a','animated',('Animation',),2020),('b','mystery',('Mystery',),2020),
                ('c','doc',('Documentary',),2020),('c','fantasy',('Fantasy',),2020)]
    watches,meta=collection(entries)
    for uid,key in [('b','first'),('c','drama')]:
        row,info=movie(uid,key,('Drama',),2020);row.update(movie=False,season=1)
        meta[key]=info
        watches.extend(dict(row,episode=n,occurrence=f'{key}:{n}') for n in (1,2,3))
    score=score_event(goal,watches,meta)
    assert score['total']==9 and score['goal_reached']
    assert score['qualified']==['a','b','c']
    assert sum(score['estimated_awards'].values())==12000
    assert score['contributions']['b']>=2


def test_unscoped_wetrakr_observations_need_matching_native_account():
    identity={'watched_at':'2026-11-24T12:00:00Z','title':'Film','ids':{'tmdb':'1'}}
    user={'wetrakr':{'account_id':'account'},'progression':{'xp_events':[{'event_key':'award','media_type':'movie','runtime_minutes':90}],
          'wetrakr_plays':{'p1':{'account_id':'account','watched_at':identity['watched_at']}},
          'occurrence_ledger':{'x':{'award_keys':['award'],'observations':{
              'wetrakr:p1:'+identity['watched_at']:{'provider':'wetrakr','identity':identity}}}}}}
    start=datetime(2026,11,23,tzinfo=timezone.utc);end=start+timedelta(days=7)
    assert len(credited_watches({'a':user},['a'],start,end,{'a':'wetrakr'}))==1
    user['progression']['wetrakr_plays']['p1']['account_id']='old-account'
    assert credited_watches({'a':user},['a'],start,end,{'a':'wetrakr'})==[]


def test_existing_weeks_keep_rules_and_new_members_do_not_raise_goal(tmp_path,monkeypatch):
    import trackerbot.core.storage as module
    async def run():
        monkeypatch.setattr(module,'DATA_PATH',str(tmp_path/'store.json'));store=module.Storage()
        await store.link_user('1','1','token',None,'One','2026-11-01T00:00:00Z')
        start=datetime(2026,11,23,tzinfo=timezone.utc);end=start+timedelta(days=7)
        store._data['guilds']['1']['community_challenges']={'2026-11-23':{
            'start':start.isoformat(),'end':end.isoformat(),'kind':'minutes','target':720,'pool':1440,
            'members':['1'],'awards':{},'notified_awards':{},'required_contributors':2,'qualifying_minutes':30}}
        identity={'watched_at':'2026-11-23T01:00:00Z','title':'Film','ids':{'tmdb':'1'},'season':None,'episode':None}
        store._data['users']['1']['progression']={'xp_events':[{'event_key':'award','media_type':'movie','runtime_minutes':120}],
            'occurrence_ledger':{'x':{'award_keys':['award'],'observations':{'simkl:1':{'provider':'simkl','identity':identity}}}}}
        rows=credited_watches(store._data['users'],['1'],start,end,{'1':'simkl'})
        store._data['guilds']['1']['community_metadata']={rows[0]['meta_key']:{'canonical':'movie:tmdb:1','title':'Film','poster_path':'/film.jpg'}}
        legacy=await store.get_community_state('1','2026-11-23',start,end,start+timedelta(hours=2))
        assert (legacy['target'],legacy['pool'])==(720,1440)
        assert legacy['art_entries'][0]['poster_path']=='/film.jpg'
        next_start=end;next_end=end+timedelta(days=7)
        new=await store.get_community_state('1','2026-11-30',next_start,next_end,next_start)
        await store.link_user('1','2','token',None,'Two','2026-11-30T01:00:00Z')
        changed=await store.get_community_state('1','2026-11-30',next_start,next_end,next_start+timedelta(hours=2))
        assert changed['target']==new['target'] and changed['pool']==new['pool']
        assert '2' in changed['members']
        reloaded=module.Storage()
        assert (await reloaded.get_community_state('1','2026-11-30',next_start,next_end,next_start+timedelta(hours=3)))['pool']==new['pool']
    asyncio.run(run())


def test_community_artwork_is_recent_distinct_and_bounded():
    from trackerbot.core.community_events import community_art_entries
    watches,meta=collection([('a',str(n)) for n in range(7)])
    for n,row in enumerate(watches):row['at']=f'2026-11-{20+n:02d}T12:00:00Z'
    watches.append(dict(watches[-1],uid='b'))
    assert [r['canonical'] for r in community_art_entries(watches,meta)]==['6','5','4','3','2']
    assert community_art_entries(watches,{})==[]


def test_early_upgrade_pays_old_pool_once_and_excludes_earlier_watches(tmp_path,monkeypatch):
    import trackerbot.core.storage as module
    import pytest
    async def run():
        monkeypatch.setattr(module,'DATA_PATH',str(tmp_path/'early.json'));store=module.Storage()
        await store.link_user('1','1','token',None,'One','2026-11-01T00:00:00Z')
        start=datetime(2026,11,16,tzinfo=timezone.utc);end=start+timedelta(days=7);now=start+timedelta(days=3)
        user=store._data['users']['1'];p=user.setdefault('progression',{'xp':0,'lifetime_xp':0,'prestige':0});before=p['lifetime_xp']
        p['xp_events']=[{'event_key':'award','media_type':'movie','runtime_minutes':120,'at':(now-timedelta(hours=1)).isoformat()}]
        identity={'watched_at':(now-timedelta(hours=1)).isoformat(),'title':'Film','ids':{'tmdb':'1'},'season':None,'episode':None}
        p['occurrence_ledger']={'x':{'award_keys':['award'],'observations':{'simkl:1':{'provider':'simkl','identity':identity}}}}
        guild=store._data['guilds']['1']
        guild['community_challenges']={'2026-11-16':{'start':start.isoformat(),'end':end.isoformat(),
            'kind':'minutes','target':60,'pool':1440,'members':['1'],'awards':{},'notified_awards':{},
            'required_contributors':1,'qualifying_minutes':30}}
        guild['community_challenges']['2026-11-16']['target']=9999
        with pytest.raises(ValueError,match='not met'):
            await store.start_new_community_event('1','2026-11-16',start,end,now,'admin')
        assert '2026-11-16-early' not in guild['community_challenges']
        guild['community_challenges']['2026-11-16']['target']=60
        key=await store.start_new_community_event('1','2026-11-16',start,end,now,'admin')
        state=await store.get_community_state('1','2026-11-16',start,end,now)
        assert state['key']==key and state['version']==2 and state['kind']=='watchathon'
        assert state['total']==0 and state['start']==now.isoformat() and state['end']==end.isoformat()
        assert state['next_event']=='Community Watchathon'
        preview=await store.preview_community_event('1','2026-11-16',start,end,now)
        assert preview['total']==0 and preview['start']==now.isoformat()
        assert p['lifetime_xp']==before+1440
        assert p['community_rewards']=={'1:2026-11-16':1440}
        assert guild['community_challenges']['2026-11-16']['closed_early_by']=='admin'
        assert len(state['pending_notifications'])==1
        await store.ack_community_notification('1','2026-11-16',{'1':1440})
        with pytest.raises(ValueError,match='already active'):
            await store.start_new_community_event('1','2026-11-16',start,end,now,'admin')
        reloaded=module.Storage()
        again=await reloaded.get_community_state('1','2026-11-16',start,end,now+timedelta(minutes=1))
        assert not again['pending_notifications'] and again['total']==0
        assert reloaded._data['users']['1']['progression']['lifetime_xp']==before+1440
        # Boundary belongs exclusively to the newly opened event.
        p['xp_events'].append({'event_key':'new','media_type':'movie','runtime_minutes':90,'at':now.isoformat()})
        p['occurrence_ledger']['y']={'award_keys':['new'],'observations':{'simkl:2':{'provider':'simkl','identity':dict(identity,watched_at=now.isoformat())}}}
        latest=await store.get_community_state('1','2026-11-16',start,end,now+timedelta(minutes=1))
        assert latest['total']==90 and p['community_rewards']['1:2026-11-16']==1440
    asyncio.run(run())
