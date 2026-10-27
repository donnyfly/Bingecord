import asyncio
import copy
from datetime import datetime,timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from trackerbot.core import storage as module
from trackerbot.core.progression_rebuild import rebuild_simkl,fetch_simkl_entries
from test_history_repair_apply import fixture_store


def entry(kind='movie',sid=1,stamp='2024-01-01T10:00:00Z',runtime=120):
    item=f'movies:{sid}' if 'movie' in kind else f'series:{"anime" if kind.startswith("anime") else "shows"}:{sid}:1:1'
    return {'event_key':f'{kind}:{item}:{stamp}','item_key':item,'media_type':kind,'at':stamp,
            'title':f'Title {sid}','ids':{'simkl':sid,'tmdb':sid},'runtime_minutes':runtime,'runtime_source':'simkl'}


def test_rebuild_is_a_new_minute_based_balance_preserving_only_recorded_community_xp():
    p={'xp':999999,'prestige':3,'lifetime_xp':2049999,'xp_events':[],
       'challenge_completions':{},'community_rewards':{'1:week':321},'pending_challenge_notifications':[{'key':'old'}]}
    before=copy.deepcopy(p)
    entries=[entry(),entry('episode',2,runtime=60),entry('anime_episode',3,runtime=24),entry('anime_movie',4,runtime=90)]
    after,report=rebuild_simkl(p,entries,{'1':'Asia/Singapore'},'2026-10-28T00:00:00Z')
    assert report['breakdown']=={'watch':1176,'achievements':350,'challenges':0,'community_preserved':321}
    assert after['lifetime_xp']==1847 and after['prestige']==0 and after['xp']==1847
    assert after['community_rewards']==p['community_rewards'] and not after['pending_challenge_notifications']
    assert after['history_xp_seeded'] and after['history_xp_notification_sent']
    assert after['prestige_notified']==after['prestige'] and p==before
    assert len(after['xp_events'])==7  # Four watches, three earned achievements.


async def prepare_rebuild(store,entries):
    context=await store.get_simkl_rebuild_context('42')
    _,report=rebuild_simkl(context['baseline'],entries,context['guild_timezones'],'2026-10-28T00:00:00Z')
    markers={'tv_shows':'2026-10-27T00:00:00Z','anime':'2026-10-27T00:00:00Z','movies':'2026-10-27T00:00:00Z'}
    report.update(rebuild_entries=entries,guild_timezones=context['guild_timezones'],
                  guild_fingerprint=context['guild_fingerprint'],source_account_id=context['source_account_id'],simkl_markers=markers)
    plan=await store.prepare_history_repair('42',context['baseline'],report)
    return plan,report,context


def test_rebuild_apply_preserves_other_users_links_sources_and_seeds_secondary_observations(tmp_path,monkeypatch):
    async def run():
        store,a,b=await fixture_store(tmp_path,monkeypatch)
        old=await store.get_all();entries=[entry(stamp=a['at'])]
        plan,report,context=await prepare_rebuild(store,entries)
        assert await store.get_progression('42')==context['baseline']
        receipt=await store.apply_history_repair('42',plan['token'],simkl_markers=report['simkl_markers'])
        after=await store.get_all();p=await store.get_progression('42')
        assert receipt['mode']=='simkl_rebuild' and receipt['breakdown']==report['breakdown']
        assert after['users']['43']==old['users']['43']
        assert {k:v for k,v in after['users']['42'].items() if k!='progression'}=={k:v for k,v in old['users']['42'].items() if k!='progression'}
        assert after['guilds']['123']['users']['42']['activity_provider']==old['guilds']['123']['users']['42']['activity_provider']
        assert set(after['guilds']['123']['users']['42']['achievements'])==set(report['guild_achievements']['123'])
        import json
        backup=json.loads((tmp_path/'repair-backups'/receipt['backup']).read_text())
        assert backup['progression']==context['baseline'] and '123' in backup['guild_achievements']
        assert len(p['mdblist_plays'])==1 and p['mdblist_plays']['7/2']['xp_key'] is None
        assert any(b['event_key'] in row['observations'] for row in p['occurrence_ledger'].values())
        before=p['lifetime_xp'];b['ids']={'tmdb':1}
        assert module._add_watch_xp_events(p,[b])==(0,[]) and p['lifetime_xp']==before
        repeat=await store.apply_history_repair('42',plan['token'])
        assert repeat['already_applied']
        assert await module.Storage().get_progression('42')==await store.get_progression('42')
    asyncio.run(run())


@pytest.mark.parametrize('change',['remote','guild','missing_markers'])
def test_rebuild_rejects_changed_remote_history_or_guild_context(tmp_path,monkeypatch,change):
    async def run():
        store,_,_=await fixture_store(tmp_path,monkeypatch)
        plan,report,_=await prepare_rebuild(store,[entry()])
        markers=report['simkl_markers']
        if change=='remote':markers={'movies':'new'}
        if change=='missing_markers':markers=None
        if change=='guild':store._data['guilds']['123']['timezone']='UTC'
        before=await store.get_all()
        with pytest.raises(ValueError):await store.apply_history_repair('42',plan['token'],simkl_markers=markers)
        assert await store.get_all()==before and not list(tmp_path.glob('repair-backups/*'))
    asyncio.run(run())


def test_rebuild_store_write_failure_preserves_progression_and_guild_achievements(tmp_path,monkeypatch):
    async def run():
        store,_,_=await fixture_store(tmp_path,monkeypatch)
        plan,report,_=await prepare_rebuild(store,[entry()])
        before=await store.get_all();original=module._write_to_disk
        def fail(*args):raise OSError('disk full')
        monkeypatch.setattr(module,'_write_to_disk',fail)
        with pytest.raises(OSError):await store.apply_history_repair('42',plan['token'],simkl_markers=report['simkl_markers'])
        assert await store.get_all()==before
        monkeypatch.setattr(module,'_write_to_disk',original)
        await store.apply_history_repair('42',plan['token'],simkl_markers=report['simkl_markers'])
    asyncio.run(run())


def test_unmatched_secondary_history_stays_seeded_and_does_not_refill_rebuilt_xp(tmp_path,monkeypatch):
    async def run():
        store,a,b=await fixture_store(tmp_path,monkeypatch)
        baseline=await store.get_progression('42')
        after,report=rebuild_simkl(baseline,[entry(sid=99)],{'123':'Asia/Singapore'},'2026-10-28T00:00:00Z')
        assert report['held_secondary_observations']==1 and b['event_key'] in after['pending_history_awards']
        before=after['lifetime_xp']
        assert module._add_watch_xp_events(after,[b])==(0,[]) and after['lifetime_xp']==before
        live={**b,'event_key':'mdblist:7/3:2026-10-29T00:00:00Z','at':'2026-10-29T00:00:00Z'}
        assert module._add_watch_xp_events(after,[live])[0]==400
    asyncio.run(run())


def fake_app(changed=False,fail=False):
    markers={'tv_shows':{'all':'one'},'anime':{'all':'one'},'movies':{'all':'one'}}
    async def refresh(uid,user,token,method):
        value=copy.deepcopy(markers)
        if changed and refresh.count:value['movies']['all']='two'
        refresh.count+=1
        return value,token
    refresh.count=0
    media={'title':'Film','ids':{'simkl':1,'tmdb':1},'runtime':120}
    async def catalogs(uid,user,token,scope,**kw):
        if fail and scope=='anime':raise TimeoutError()
        return ([{'show':media,'last_watched_at':'2024-01-01T00:00:00Z'}] if scope=='anime' else
                [{'movie':media,'last_watched_at':'2024-01-01T00:00:00Z'}] if scope=='movies' else []),token
    return SimpleNamespace(valid_token=AsyncMock(return_value='SECRET'),call_refresh=refresh,
        simkl=SimpleNamespace(get_activities=object()),MEDIA_TYPES=('shows','anime','movies'),
        ACTIVITY_KEYS={'shows':'tv_shows','anime':'anime','movies':'movies'},HISTORY_FETCH_TIMEOUT_SECONDS=120,
        cached_simkl_items=catalogs,is_anime_movie_item=AsyncMock(return_value=True),
        iter_show_episodes=lambda *args:[],tmdb=SimpleNamespace(get_watch_runtime=AsyncMock(return_value=120)))


def test_fresh_snapshot_deduplicates_anime_movie_across_catalogs():
    async def run():
        entries,markers,skipped=await fetch_simkl_entries(fake_app(),'42',{})
        assert len(entries)==1 and entries[0]['media_type']=='anime_movie' and skipped==0
        assert 'SECRET' not in str(entries) and markers['movies']=='one'
    asyncio.run(run())


@pytest.mark.parametrize('case',['changed','failed'])
def test_failed_or_changing_full_snapshot_cannot_return_rebuild_entries(case):
    async def run():
        with pytest.raises(ValueError if case=='changed' else TimeoutError):
            await fetch_simkl_entries(fake_app(changed=case=='changed',fail=case=='failed'),'42',{})
    asyncio.run(run())


def test_rebuild_command_is_private_and_only_prepares_a_plan(tmp_path,monkeypatch):
    async def run():
        from test_notification_preview import bot
        import trackerbot.core.progression_rebuild as rebuild
        store,_,_=await fixture_store(tmp_path,monkeypatch)
        monkeypatch.setattr(bot,'storage',store);monkeypatch.setattr(bot,'SIMKL_CLIENT_ID','configured')
        markers={'tv_shows':'one','anime':'one','movies':'one'}
        monkeypatch.setattr(rebuild,'fetch_simkl_entries',AsyncMock(return_value=([entry()],markers,0)))
        i=SimpleNamespace(guild_id=123,guild=SimpleNamespace(id=123),user=SimpleNamespace(id=42),
                          response=SimpleNamespace(defer=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()))
        before=await store.get_progression('42')
        await bot.tracker_mapping.callback(i,rebuild_simkl=True)
        sent=i.followup.send.await_args.kwargs
        import json
        report=json.loads(sent['file'].fp.read());sent['file'].close()
        assert report['repair_mode']=='simkl_rebuild' and sent['ephemeral']
        assert await store.get_progression('42')==before and 'apply_token' in report
        monkeypatch.setattr(bot,'valid_token',AsyncMock(return_value='SECRET'))
        monkeypatch.setattr(rebuild,'simkl_markers',AsyncMock(return_value=(markers,'SECRET')))
        await bot.tracker_mapping.callback(i,repair_apply=report['apply_token'])
        assert (await store.get_progression('42'))['lifetime_xp']==report['projected']['lifetime_xp']
        assert 'SIMKL progression rebuilt' in i.followup.send.await_args.args[0]
    asyncio.run(run())


def test_snapshot_classifies_tv_and_anime_episodes_and_reports_undated_rows():
    async def run():
        from test_notification_preview import bot
        app=fake_app()
        show={'show':{'title':'Series','ids':{'simkl':2,'tmdb':2},'runtime':24},
              'seasons':[{'number':1,'episodes':[{'number':1,'watched_at':'2024-01-01T00:00:00Z'},{'number':2}]}]}
        async def catalogs(uid,user,token,scope,**kw):return ([copy.deepcopy(show)] if scope!='movies' else []),token
        app.cached_simkl_items=catalogs;app.is_anime_movie_item=AsyncMock(return_value=False)
        app.iter_show_episodes=bot.iter_show_episodes
        entries,_,undated=await fetch_simkl_entries(app,'42',{})
        assert {e['media_type'] for e in entries}=={'episode','anime_episode'} and undated==2
        assert all(e['runtime_source']=='simkl' for e in entries)
    asyncio.run(run())


def test_empty_snapshot_and_invalid_rebuild_keys_are_rejected():
    async def run():
        app=fake_app()
        async def empty(uid,user,token,scope,**kw):return [],token
        app.cached_simkl_items=empty
        with pytest.raises(ValueError):await fetch_simkl_entries(app,'42',{})
    asyncio.run(run())
    bad=entry();bad['event_key']='invalid'
    with pytest.raises(ValueError):rebuild_simkl({},[bad],{'1':'UTC'},'2026-10-28T00:00:00Z')


def test_historical_challenges_are_rebuilt_once_and_balance_reconciles():
    entries=[entry(sid=n,stamp='2026-10-28T10:00:00Z') for n in range(1,7)]
    before={'xp':999999,'lifetime_xp':999999,'prestige':0,'community_rewards':{'guild:week':500},
            'xp_events':[],'challenge_completions':{'daily:2026-10-28':{'obsolete':{'xp':999999}}}}
    after,report=rebuild_simkl(before,entries,{'1':'Asia/Singapore'},'2026-10-28T11:00:00Z')
    assert report['breakdown']['challenges']>0 and not after['pending_challenge_notifications']
    challenge_sum=sum(r['xp'] for rs in after['challenge_completions'].values() for r in rs.values())
    assert after['lifetime_xp']==sum(e['amount'] for e in after['xp_events'])+challenge_sum+500
    again,repeated=rebuild_simkl(after,entries,{'1':'Asia/Singapore'},'2026-10-28T12:00:00Z')
    assert repeated['breakdown']==report['breakdown'] and again['lifetime_xp']==after['lifetime_xp']
    assert 'obsolete' not in str(after['challenge_completions'])
