"""Durable, user-scoped confirmed repair transactions and later ledger support."""
import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from trackerbot.core import storage as module,watch_ledger
from trackerbot.core.historical_repair import build_preview


async def fixture_store(tmp_path,monkeypatch):
    monkeypatch.setattr(module,'DATA_PATH',str(tmp_path/'store.json'))
    store=module.Storage()
    for uid in ('42','43'):
        await store.link_user('123',uid,'SECRET_TOKEN',None,'account','2026-09-28T00:00:00Z')
        await store.get_progression(uid)
    p=store._data['users']['42']['progression']
    p['history_repair_primary']='simkl'
    a={'event_key':'movie:movies:1:2026-10-01T00:00:00Z','media_type':'movie','title':'Film',
       'at':'2026-10-01T00:00:00Z','amount':400,'ids':{'tmdb':1}}
    b={**a,'event_key':'mdblist:7/2:2026-10-01T00:00:01Z','at':'2026-10-01T00:00:01Z','ids':{'tmdb':2}}
    module._add_watch_xp_events(p,[a,b])
    p['occurrence_ledger'][b['event_key']]['observations'][b['event_key']]['identity']['ids']={'tmdb':1}
    p['mdblist_plays']={'7/2':{'watched_at':b['at'],'xp_key':b['event_key'],'account_id':'7',
                             'title':'Film','media_type':'movie','ids':{'tmdb':1},'item_key':'movie:1'}}
    store._dirty=True
    await store.flush()
    return store,a,b


async def prepare(store):
    baseline=await store.get_progression('42')
    report=build_preview(baseline,module._revoke_unmet_watch_challenges)
    plan=await store.prepare_history_repair('42',baseline,report)
    assert plan and report['confirmed_watch_xp_reduction']==400
    return plan,baseline,report


def test_apply_is_backed_up_persistent_user_scoped_idempotent_and_keeps_both_sources(tmp_path,monkeypatch):
    async def run():
        store,a,b=await fixture_store(tmp_path,monkeypatch)
        other=await store.get_user('43');guilds=(await store.get_all())['guilds']
        plan,before,report=await prepare(store)
        receipt=await store.apply_history_repair('42',plan['token'])
        assert not receipt['already_applied'] and receipt['watch_reduction']==400
        after=await store.get_progression('42')
        assert after['lifetime_xp']==after['xp']==400
        assert await store.get_user('43')==other and (await store.get_all())['guilds']==guilds
        backup=tmp_path/'repair-backups'/receipt['backup']
        assert json.loads(backup.read_text())['progression']==before
        assert 'SECRET_TOKEN' not in backup.read_text() and backup.stat().st_mode & 0o777==0o600
        restarted=module.Storage()
        assert await restarted.get_progression('42')==after
        repeated=await restarted.apply_history_repair('42',plan['token'])
        assert repeated['already_applied'] and await restarted.get_progression('42')==after
        assert after['mdblist_plays']['7/2']['xp_key'] is None
        # Re-observation of the duplicate cannot restore its removed award.
        b['ids']={'tmdb':1}
        assert module._add_watch_xp_events(after,[b])==(0,[])
        assert after['lifetime_xp']==400
        assert watch_ledger.remove(after,a['event_key'])==[]
        assert watch_ledger.remove(after,b['event_key'])==[a['event_key']]
    asyncio.run(run())


@pytest.mark.parametrize('change',['xp','account','expiry','other_user'])
def test_stale_expired_and_other_user_tokens_cannot_apply(tmp_path,monkeypatch,change):
    async def run():
        store,_,_=await fixture_store(tmp_path,monkeypatch)
        plan,_,_=await prepare(store)
        uid='42'
        if change=='xp':store._data['users']['42']['progression']['xp']+=1
        if change=='account':store._data['users']['42']['simkl_account_id']='relinked'
        if change=='expiry':store._data['history_repair_plans']['42']['created_at']='2000-01-01T00:00:00+00:00'
        if change=='other_user':uid='43'
        before=await store.get_all()
        with pytest.raises(ValueError):await store.apply_history_repair(uid,plan['token'])
        assert await store.get_all()==before
        assert not list(tmp_path.glob('repair-backups/*'))
    asyncio.run(run())


@pytest.mark.parametrize('failure',['backup','store'])
def test_failed_write_leaves_xp_unchanged_and_same_token_can_retry(tmp_path,monkeypatch,failure):
    async def run():
        store,_,_=await fixture_store(tmp_path,monkeypatch)
        plan,before,_=await prepare(store)
        name='_write_repair_backup' if failure=='backup' else '_write_to_disk'
        original=getattr(module,name)
        def fail(*args):raise OSError('disk unavailable')
        monkeypatch.setattr(module,name,fail)
        with pytest.raises(OSError):await store.apply_history_repair('42',plan['token'])
        assert await store.get_progression('42')==before
        assert await module.Storage().get_progression('42')==before
        monkeypatch.setattr(module,name,original)
        assert not (await store.apply_history_repair('42',plan['token']))['already_applied']
        assert (await store.get_progression('42'))['xp']==400
    asyncio.run(run())


def test_correction_reverses_prestige_and_dependent_challenges_without_notifications(tmp_path,monkeypatch):
    async def run():
        store,_,_=await fixture_store(tmp_path,monkeypatch)
        p=store._data['users']['42']['progression']
        p.update(xp=100,prestige=1,lifetime_xp=350100,prestige_notified=1)
        p['challenge_completions']={'daily:2026-10-01':{'double':{'xp':1200,'definition':{'kind':'movies','target':2}}}}
        p['pending_challenge_notifications']=[{'key':'daily:2026-10-01:double'},{'key':'unrelated'}]
        plan,_,report=await prepare(store)
        receipt=await store.apply_history_repair('42',plan['token'])
        assert receipt['challenge_reduction']==1200
        after=await store.get_progression('42')
        assert after['prestige']==0 and after['xp']==after['lifetime_xp']==348500
        assert after['pending_challenge_notifications']==[{'key':'unrelated'}]
        assert after['prestige_notified']==1 and not after['challenge_completions']
        assert build_preview(after,module._revoke_unmet_watch_challenges)['confirmed_watch_xp_reduction']==0
    asyncio.run(run())


def test_cancelled_caller_cannot_leave_disk_and_memory_with_different_xp(tmp_path,monkeypatch):
    async def run():
        import threading
        store,_,_=await fixture_store(tmp_path,monkeypatch)
        plan,_,_=await prepare(store)
        entered=threading.Event();release=threading.Event();original=module._write_repair_backup
        def block(*args):
            entered.set()
            assert release.wait(5)
            original(*args)
        monkeypatch.setattr(module,'_write_repair_backup',block)
        task=asyncio.create_task(store.apply_history_repair('42',plan['token']))
        assert await asyncio.to_thread(entered.wait,5)
        task.cancel();release.set()
        with pytest.raises(asyncio.CancelledError):await task
        assert (await store.get_progression('42'))['xp']==400
        assert await module.Storage().get_progression('42')==await store.get_progression('42')
    asyncio.run(run())


def test_preview_preparation_rejects_changed_progression(tmp_path,monkeypatch):
    async def run():
        store,_,_=await fixture_store(tmp_path,monkeypatch)
        baseline=await store.get_progression('42');report=build_preview(baseline,module._revoke_unmet_watch_challenges)
        store._data['users']['42']['progression']['xp']+=1
        with pytest.raises(ValueError):await store.prepare_history_repair('42',baseline,report)
    asyncio.run(run())


def test_apply_command_only_targets_requester_and_is_private(monkeypatch):
    async def run():
        from test_notification_preview import bot
        monkeypatch.setattr(bot.storage,'get_user',AsyncMock(return_value={'simkl_token':'SECRET'}))
        receipt={'already_applied':False,'watch_reduction':400,'challenge_reduction':0,
                 'projected':{'prestige':0,'level':1,'lifetime_xp':400},'backup':'backup.json'}
        apply=AsyncMock(return_value=receipt);monkeypatch.setattr(bot.storage,'apply_history_repair',apply)
        i=SimpleNamespace(guild_id=1,guild=SimpleNamespace(id=1),user=SimpleNamespace(id=42),
                          response=SimpleNamespace(defer=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()))
        await bot.tracker_mapping.callback(i,repair_apply='a'*16)
        apply.assert_awaited_once_with('42','a'*16)
        assert i.followup.send.await_args.kwargs['ephemeral']
        assert 'SECRET' not in str(i.followup.send.await_args)
        apply.reset_mock()
        await bot.tracker_mapping.callback(i,repair_apply='a'*16,export=True)
        apply.assert_not_awaited()
    asyncio.run(run())


def test_import_rollback_holds_older_history_keeps_later_watches_and_repeats_safely(tmp_path,monkeypatch):
    async def run():
        from trackerbot.core.historical_repair import rollback_import
        store,a,b=await fixture_store(tmp_path,monkeypatch)
        p=store._data['users']['42']['progression']
        later={**b,'event_key':'mdblist:7/3:2026-10-03T00:00:00Z','at':'2026-10-03T00:00:00Z','ids':{'tmdb':3}}
        module._add_watch_xp_events(p,[later])
        p['xp']+=1200;p['lifetime_xp']+=1200
        stamp='2026-10-02T10:00:00Z'
        p['challenge_completions']={'daily:2026-10-01':{'historical':{'xp':1200,'completed_at':stamp,'definition':{'kind':'movies','target':1}}}}
        baseline=await store.get_progression('42');other=await store.get_user('43')
        _,report=rollback_import(baseline,'mdblist','2026-10-02T09:00:00Z',stamp,module._revoke_unmet_watch_challenges)
        assert report['selected_watch_xp_reduction']==400 and report['historical_batch_xp_reduction']==1200
        assert report['projected']['lifetime_xp']==800
        assert await store.get_progression('42')==baseline
        plan=await store.prepare_history_repair('42',baseline,report)
        receipt=await store.apply_history_repair('42',plan['token'])
        assert receipt['mode']=='import_rollback' and await store.get_user('43')==other
        after=await store.get_progression('42')
        assert after['xp']==800 and b['event_key'] in after['pending_history_awards']
        assert after['pending_history_challenge_awards']['daily:2026-10-01:historical']['reward']['xp']==1200
        assert after['challenge_completions']['daily:2026-10-01']['historical']['xp']==0
        assert {e['event_key'] for e in after['xp_events']}=={a['event_key'],later['event_key']}
        # Retrying the same old event as live cannot accidentally release held XP.
        assert module._add_watch_xp_events(after,[b])==(0,[])
        new={**later,'event_key':'mdblist:7/4:2026-10-04T00:00:00Z','at':'2026-10-04T00:00:00Z'}
        assert module._add_watch_xp_events(after,[new])[0]==400
        again=await store.apply_history_repair('42',plan['token'])
        assert again['already_applied']
    asyncio.run(run())


def test_import_rollback_requires_explicit_primary_and_valid_dates():
    from trackerbot.core.historical_repair import rollback_import
    p={'xp_events':[],'challenge_completions':{},'xp':0,'lifetime_xp':0}
    with pytest.raises(ValueError):rollback_import(p,'mdblist','2026-10-01T00:00:00Z','2026-10-01T00:00:00Z',module._revoke_unmet_watch_challenges)
    p['history_repair_primary']='simkl'
    with pytest.raises(ValueError):rollback_import(p,'simkl','2026-10-01T00:00:00Z','2026-10-01T00:00:00Z',module._revoke_unmet_watch_challenges)
    with pytest.raises(ValueError):rollback_import(p,'mdblist','2026-10-01','invalid',module._revoke_unmet_watch_challenges)


def test_import_rollback_command_previews_privately_then_applies_saved_plan(tmp_path,monkeypatch):
    async def run():
        from test_notification_preview import bot
        store,_,_=await fixture_store(tmp_path,monkeypatch)
        monkeypatch.setattr(bot,'storage',store)
        i=SimpleNamespace(guild_id=123,guild=SimpleNamespace(id=123),user=SimpleNamespace(id=42),
                          response=SimpleNamespace(defer=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()))
        before=await store.get_progression('42')
        await bot.tracker_mapping.callback(i,rollback_source=bot.app_commands.Choice(name='MDBList',value='mdblist'),
                    rollback_import_before='2026-10-02T00:00:00Z',rollback_rewards_at='2026-10-02T00:00:00Z')
        sent=i.followup.send.await_args.kwargs
        assert sent['ephemeral']
        report=json.loads(sent['file'].fp.read());sent['file'].close()
        assert report['repair_mode']=='import_rollback' and report['apply_token']
        assert await store.get_progression('42')==before
        await bot.tracker_mapping.callback(i,repair_apply=report['apply_token'])
        assert (await store.get_progression('42'))['xp']==400
        assert 'import rollback' in i.followup.send.await_args.args[0]
    asyncio.run(run())


def test_import_rollback_keeps_a_sole_award_supported_by_primary_history():
    from trackerbot.core.historical_repair import rollback_import
    p={'xp':400,'lifetime_xp':400,'prestige':0,'history_repair_primary':'simkl','challenge_completions':{},
       'xp_events':[{'event_key':'mdblist:7/2:2026-10-01T00:00:00Z','media_type':'movie','amount':400,'at':'2026-10-01T00:00:00Z'}],
       'occurrence_ledger':{'shared':{'award_keys':['mdblist:7/2:2026-10-01T00:00:00Z'],'needs_review':False,
            'observations':{'simkl-ref':{'provider':'simkl','identity':None},'mdblist-ref':{'provider':'mdblist','identity':None}}}}}
    after,report=rollback_import(p,'mdblist','2026-10-02T00:00:00Z','2026-10-02T00:00:00Z',module._revoke_unmet_watch_challenges)
    assert report['selected_watch_xp_reduction']==0 and after['lifetime_xp']==400
