import asyncio
import copy
from datetime import date,datetime,timedelta,timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from PIL import Image
from trackerbot.core.personal_challenges import definitions,progress,snapshot,period_definitions
from trackerbot.core.progression import challenges_for,xp_for_level
from trackerbot.core.storage import _complete_watch_challenges,_revoke_unmet_watch_challenges

START=datetime(2026,11,24,tzinfo=timezone.utc)


def initial():
    return {'xp':0,'lifetime_xp':0,'prestige':0,'xp_events':[],'challenge_completions':{},
            'occurrence_ledger':{},'challenge_metadata':{}}


def watch(p,key,title_id,*,movie=True,season=1,episode=1,minutes=120,at=START,genres=('Drama',),year=1990,provider='simkl',historical=False):
    ids={'tmdb':str(title_id)};canonical=f"{'movie' if movie else 'tv'}:tmdb:{title_id}"
    event={'event_key':key,'media_type':'movie' if movie else 'anime_episode','at':at.isoformat(),
           'amount':minutes*4,'runtime_minutes':minutes,'title':f'Title {title_id}','ids':ids,'historical':historical}
    p['xp_events'].append(event)
    p['occurrence_ledger'][key]={'award_keys':[key],'observations':{provider+':'+key:{'provider':provider,
        'identity':{'ids':ids,'watched_at':at.isoformat(),'season':None if movie else season,'episode':None if movie else episode}}}}
    p['challenge_metadata'][canonical]={'canonical':canonical,'genres':list(genres),'year':year,'title':f'Title {title_id}','poster_path':'/poster.jpg'}
    return event


def objective(kind,target=1,**kwargs):
    return dict(id='test_'+kind,version=2,name='Example',kind=kind,target=target,xp=450,**kwargs)


def test_rollout_rotation_and_frozen_definitions():
    assert all(c.get('version')!=2 for c in challenges_for(date(2026,11,19))[0])
    assert all(c.get('version')==2 for c in challenges_for(date(2026,11,20))[0])
    assert all(c.get('version')!=2 for c in challenges_for(date(2026,11,22))[1])
    weekly=challenges_for(date(2026,11,23))[1]
    assert weekly==challenges_for(date(2026,11,29))[1]
    for offset in range(28):
        d=date(2026,11,23)+timedelta(days=offset)
        for group in challenges_for(d):
            assert len(group)==3 and sum(c['kind']=='minutes' for c in group)==1
    p=initial();saved=period_definitions(p,'daily',date(2026,11,24),save=True)
    saved[0]['xp']=999
    assert period_definitions(p,'daily',date(2026,11,24))[0]['xp']!=999


def test_episode_genre_progress_dedupes_rewatches_and_provider_references():
    p=initial();a=watch(p,'one',1,movie=False,episode=1)
    watch(p,'rewatch',1,movie=False,episode=1,provider='wetrakr')
    p['occurrence_ledger']['one']['observations']['mdblist:ref']=copy.deepcopy(p['occurrence_ledger']['one']['observations']['simkl:one'])
    ch=objective('genre_watch',genre='Drama')
    assert progress(p,ch,START,START+timedelta(days=1))['value']==0
    watch(p,'two',1,movie=False,episode=2,provider='mdblist')
    assert progress(p,ch,START,START+timedelta(days=1))['value']==1
    # Even malformed older duplicate awards on one confirmed occurrence count once.
    p['occurrence_ledger']['one']['award_keys'].append('duplicate')
    p['xp_events'].append(dict(a,event_key='duplicate'))
    assert progress(p,objective('minutes',9999),START,START+timedelta(days=1))['value']==360


def test_metadata_pending_no_name_guesses_and_distinct_era_genre_rules():
    p=initial();watch(p,'a',1);p['challenge_metadata']={}
    ch=objective('classic_film')
    assert progress(p,ch,START,START+timedelta(days=1))=={'value':0,'pending':1,'titles':[]}
    p['challenge_metadata']['movie:tmdb:1']={'canonical':'movie:tmdb:1','year':1990,'genres':['Drama','Comedy','Crime','Mystery']}
    assert progress(p,ch,START,START+timedelta(days=1))['value']==1
    assert progress(p,objective('genres',4),START,START+timedelta(days=7))['value']==1
    for i,year in [(2,2000),(3,2010)]:watch(p,str(i),i,year=year)
    assert progress(p,objective('decades',3),START,START+timedelta(days=7))['value']==3
    watch(p,'same',1)
    assert progress(p,objective('distinct_films',4),START,START+timedelta(days=7))['value']==3


def test_new_series_and_verified_finish_have_support_requirements():
    p=initial();watch(p,'first',1,movie=False)
    assert progress(p,objective('new_series'),START,START+timedelta(days=1))['value']==1
    watch(p,'old',1,movie=False,episode=2,at=START-timedelta(days=1))
    assert progress(p,objective('new_series'),START,START+timedelta(days=1))['value']==0
    watch(p,'film',2)
    ch=objective('film_and_season',2)
    assert progress(p,ch,START,START+timedelta(days=7))['value']==1
    p['challenge_finishes']=[{'canonical':'tv:tmdb:1','at':START.isoformat()}]
    assert progress(p,ch,START,START+timedelta(days=7))['value']==2
    # Special episodes alone are never a regular-series collection.
    q=initial()
    for n in range(3):watch(q,str(n),1,movie=False,season=0,episode=n)
    assert progress(q,objective('genres',4),START,START+timedelta(days=7))['value']==0


def test_awards_delayed_metadata_idempotence_and_reversal_across_prestige():
    p=initial();event=watch(p,'a',1);ch=objective('classic_film')
    p['challenge_sets']={'daily:2026-11-24':[ch],'weekly:2026-11-23':[]}
    p['challenge_metadata']={};p['xp']=xp_for_level(100)-100;p['lifetime_xp']=p['xp']
    _complete_watch_challenges(p,[event],notify=True)
    assert not p['challenge_completions']
    p['challenge_metadata']={'movie:tmdb:1':{'canonical':'movie:tmdb:1','year':1990}}
    _complete_watch_challenges(p,[event],notify=True)
    assert p['prestige']==1 and p['xp']==350
    once=p['lifetime_xp'];_complete_watch_challenges(p,[event],notify=True)
    assert p['lifetime_xp']==once and len(p['pending_challenge_notifications'])==1
    p['xp_events']=[];p['occurrence_ledger']={}
    assert _revoke_unmet_watch_challenges(p)==450
    assert p['prestige']==0 and p['xp']==xp_for_level(100)-100
    assert p['pending_challenge_notifications']==[]
    assert _revoke_unmet_watch_challenges(p)==0


def test_silent_imports_do_not_notify_and_metadata_refresh_ignores_legacy_rewards():
    p=initial();event=watch(p,'old',1,historical=True);ch=objective('classic_film')
    p['challenge_sets']={'daily:2026-11-24':[ch],'weekly:2026-11-23':[]}
    _complete_watch_challenges(p,[event],notify=True)
    assert p['lifetime_xp']==450 and not p.get('pending_challenge_notifications')
    p['challenge_completions']['daily:2026-01-01']={'legacy':{'xp':999,'definition':{'kind':'movies','target':1000}}}
    _revoke_unmet_watch_challenges(p,modern_only=True)
    assert 'daily:2026-01-01' in p['challenge_completions']


def test_card_snapshot_is_readonly_and_preview_uses_new_rules():
    p=initial();watch(p,'a',1,at=START-timedelta(days=5));before=copy.deepcopy(p)
    live=snapshot(p,datetime(2026,11,19,12,tzinfo=timezone.utc))
    preview=snapshot(p,datetime(2026,11,19,12,tzinfo=timezone.utc),preview=True)
    assert p==before
    assert all(r.get('version')!=2 for r in live['sections'][0]['rows'])
    assert all(r.get('version')==2 for r in preview['sections'][0]['rows'])
    from trackerbot.presentation.challenge_visuals import render_challenges_png
    image=render_challenges_png('A long member display name with unicode 日本語',preview,now=datetime(2026,11,19,12,tzinfo=timezone.utc))
    assert Image.open(image).size==(1080,2058)


def test_storage_refresh_validates_completion_support_and_keeps_shared_metadata(tmp_path,monkeypatch):
    import trackerbot.core.storage as module
    async def run():
        monkeypatch.setattr(module,'DATA_PATH',str(tmp_path/'personal.json'));store=module.Storage()
        await store.link_user('1','a','token',None,'Viewer','2026-11-01T00:00:00Z')
        await store.get_progression('a');p=store._data['users']['a']['progression']
        # Use normal regular episodes but insufficient for an unverified collection.
        p.update(initial());watch(p,'film',1);watch(p,'finale',2,movie=False,episode=8)
        guild=store._data['guilds']['1'];member=guild['users']['a']
        member['statistics']['watch_events']={'episode':{'item_key':'show:2:1:8','watched_at':START.isoformat(),'media_type':'episode','title':'Series','ids':{'tmdb':'2'}}}
        guild['community_completions']={'proof':{'uid':'a','provider':'simkl','account':'account','canonical':'tv:tmdb:2',
            'at':START.isoformat(),'support':['show:2:1:8|'+START.isoformat()]}}
        guild['community_metadata']=copy.deepcopy(p['challenge_metadata'])
        ch=objective('film_and_season',2)
        p['challenge_sets']={'daily:2026-11-24':[],'weekly:2026-11-23':[ch]}
        await store.refresh_personal_challenge_facts('1',START+timedelta(hours=1))
        assert p['challenge_completions']['weekly:2026-11-23'][ch['id']]['xp']==450
        assert p['challenge_metadata']['tv:tmdb:2']['genres']==['Drama']
        member['statistics']['watch_events']={}
        await store.refresh_personal_challenge_facts('1',START+timedelta(hours=2))
        assert not p['challenge_finishes'] and not p['challenge_completions']
    asyncio.run(run())


def test_private_debug_preview_does_not_enrich_or_award(monkeypatch):
    from discord import app_commands
    from test_notification_preview import bot,_interaction
    import trackerbot.presentation.challenge_delivery as delivery
    async def run():
        i=_interaction();i.user.display_name='Viewer'
        p=initial();monkeypatch.setattr(bot.storage,'get_challenge_state',AsyncMock(return_value=p))
        refresh=AsyncMock();monkeypatch.setattr(bot,'refresh_personal_challenges',refresh)
        send=AsyncMock();monkeypatch.setattr(delivery,'challenge_card',send)
        await bot.simkl_debug.callback(i,app_commands.Choice(name='Personal challenges card',value='challenges'))
        refresh.assert_not_awaited();assert send.await_args.kwargs['ephemeral']
        preview=send.await_args.args[2]
        assert preview['preview']
        assert [row['target'] for section in preview['sections'] if section.get('reading') for row in section['rows']]==[3,15,2]
        bot.storage.get_challenge_state.assert_awaited_once_with('42',reading_enabled=True)
    asyncio.run(run())


def test_utc_period_edges_and_metadata_aliases():
    from zoneinfo import ZoneInfo
    p=initial()
    at=datetime(2026,11,25,0,10,tzinfo=ZoneInfo('Asia/Singapore'))
    event=watch(p,'edge',1,at=at,minutes=40)
    _complete_watch_challenges(p,[event],notify=True)
    assert 'daily:2026-11-24' in p['challenge_completions']
    assert 'daily:2026-11-25' not in p['challenge_completions']
    assert progress(p,objective('minutes',60),START,START+timedelta(days=1))['value']==40
    assert progress(p,objective('minutes',60),START+timedelta(days=1),START+timedelta(days=2))['value']==0
    # A cached exact IMDb alias resolves the canonical TMDB title as well.
    p['challenge_metadata']={'movie:imdb:tt123':{'canonical':'movie:tmdb:1','genres':['Drama']}}
    p['occurrence_ledger']['edge']['observations']['wetrakr:ref']={'provider':'wetrakr','identity':{'ids':{'imdb':'tt123'}}}
    assert progress(p,objective('genre_watch',genre='Drama'),START,START+timedelta(days=1))['value']==1


def test_delivery_keeps_readable_objectives_when_renderer_fails(monkeypatch):
    import trackerbot.presentation.challenge_delivery as delivery
    from unittest.mock import Mock
    async def run():
        state=snapshot(initial(),START,preview=True)
        user=SimpleNamespace(display_name='Viewer',display_avatar=None)
        monkeypatch.setattr(delivery,'load_discord_artwork',AsyncMock(return_value={}))
        monkeypatch.setattr(delivery,'render_challenges_png',Mock(side_effect=RuntimeError('Pillow unavailable')))
        send=AsyncMock();await delivery.challenge_card(send,user,state,now=START,ephemeral=True)
        kwargs=send.await_args.kwargs
        assert kwargs['ephemeral'] and 'file' not in kwargs
        assert len(kwargs['embed'].fields)==4
        assert 'Watch 30 minutes' in kwargs['embed'].fields[0].value
        assert 'Read 3 manga chapters' in kwargs['embed'].fields[2].value
    asyncio.run(run())
