import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from trackerbot.core import storage as module
from trackerbot.core.progression_rebuild import fetch_provider_entries,provider_markers,rebuild_from_history
from trackerbot.core.providers import ProviderAccount
from test_history_repair_apply import fixture_store
from test_progression_rebuild import entry

STAMP='2026-10-01T00:00:00Z'


def native_app(provider,*,changed=False,failed=False,duplicate=False):
    class Adapter:
        async def link(self,uid):return ProviderAccount(provider,'7','Account',str(uid))
        async def _token(self,account):return 'SECRET'
        async def resolve_play(self,play,cache=None):
            ids=play.get('show_ids') if play['media_type']=='episode' else play['ids']
            kind=('anime_'+play['media_type']) if ids.get('mal') else play['media_type']
            item=f"{provider}:movie:{ids['tmdb']}" if 'movie' in kind else f"{provider}:episode:{ids['tmdb']}:{play['season']}:{play['episode']}"
            return {**play,'ids':ids,'media_type':kind,'item_key':item,'title':'Title','runtime_minutes':play.get('runtime_minutes') or 25}
    class Client:
        def __init__(self):self.calls=0
        async def last_activities(self,token):
            self.calls+=1;revision='new' if changed and self.calls>1 else 'old'
            return {'all':revision} if provider=='wetrakr' else {'server_time':f'clock-{self.calls}','journal_at':revision}
        async def pages(self,kind):
            if failed and kind=='episode':raise TimeoutError()
            if provider=='wetrakr':
                rows=([{'type':'movie','id':10,'play_id':'1','watched_at':STAMP,'ids':{'tmdb':10},'runtime':120},
                       {'type':'movie','id':20,'play_id':'2','watched_at':STAMP,'ids':{'tmdb':20,'mal':20},'runtime':90}]
                      if kind=='movie' else [{'type':'episode','id':301,'play_id':'3','watched_at':STAMP,
                                             'ids':{'tmdb':301},'show_id':30,'show_ids':{'tmdb':30,'mal':30},
                                             'season_number':2,'number':1,'runtime':24}])
            else:
                rows=([{'play_id':'1','watched_at':STAMP,'runtime':120,'movie':{'ids':{'tmdb':10}}},
                       {'play_id':'2','watched_at':STAMP,'runtime':90,'movie':{'ids':{'tmdb':20,'mal':20}}}]
                      if kind=='movie' else [{'play_id':'3','watched_at':STAMP,'runtime':24,'show':{'ids':{'tmdb':30,'mal':30}},
                                             'season_number':2,'number':1}])
            if duplicate and kind=='episode':rows[0]['play_id']='1'
            for row in rows:
                yield [row] if provider=='wetrakr' else {'movies' if kind=='movie' else 'episodes':[row]}
            if kind=='episode':
                row={'play_id':'unknown','watched_at_unknown':True,'watched_at':'1970-01-01T00:00:00Z'}
                yield [row] if provider=='wetrakr' else {'episodes':[row]}
        def compact_history(self,token,target):return self.pages('movie' if target=='movies' else 'episode')
        def history(self,token,*,media_type):return self.pages(media_type)
    adapter=Adapter();client=Client()
    app=SimpleNamespace(provider_registry=SimpleNamespace(get=lambda source:adapter),wetrakr=client,mdblist_tracking=client)
    return app


@pytest.mark.parametrize('provider',['wetrakr','mdblist'])
def test_full_native_reads_preserve_episode_ids_classification_runtime_and_skip_explicit_unknown_dates(provider):
    async def run():
        app=native_app(provider)
        entries,markers,skipped=await fetch_provider_entries(app,'42',{},provider,{})
        assert len(entries)==3 and skipped==1
        assert {e['media_type'] for e in entries}=={'movie','anime_movie','anime_episode'}
        episode=next(e for e in entries if e['media_type']=='anime_episode')
        assert episode['ids']['tmdb']==30 and episode['season']==2 and episode['episode']==1
        after,report=rebuild_from_history({'community_rewards':{'week':100}},entries,{'123':'UTC'},STAMP,provider=provider,account_id='7')
        assert report['breakdown']['watch']==120*4+90*4+24*4
        assert report['rebuild_provider']==provider and after['history_repair_primary']==provider
        assert len(after[provider+'_plays'])==3 and all(play['xp_key'] for play in after[provider+'_plays'].values())
        assert 'SECRET' not in json.dumps(report)
        excluded=entries[0]['excluded_native_refs'][0]
        assert excluded in after['watch_xp_keys']
        unknown={'event_key':excluded,'media_type':'movie','title':'Unknown date','at':'1970-01-01T00:00:00Z','amount':400,'ids':{'tmdb':999}}
        assert module._add_watch_xp_events(after,[unknown])==(0,[])
    asyncio.run(run())


@pytest.mark.parametrize('provider',['wetrakr','mdblist'])
@pytest.mark.parametrize('failure',['changed','failed','duplicate'])
def test_native_rebuild_refuses_partial_changing_or_duplicate_history(provider,failure):
    async def run():
        app=native_app(provider,**{failure:True})
        with pytest.raises(TimeoutError if failure=='failed' else ValueError):
            await fetch_provider_entries(app,'42',{},provider,{})
    asyncio.run(run())


def test_wetrakr_waits_for_recently_written_changes_to_become_visible():
    async def run():
        app=native_app('wetrakr')
        app.wetrakr.last_activities=AsyncMock(return_value={'all':'2026-10-01T00:00:10Z','journal_visible_until':'2026-10-01T00:00:05Z'})
        with pytest.raises(ValueError):await provider_markers(app,'42',{},'wetrakr')
    asyncio.run(run())


async def native_store(tmp_path,monkeypatch,provider):
    store,a,b=await fixture_store(tmp_path,monkeypatch)
    await store.link_provider_account('123','42',{'access_token':'SECRET','refresh_token':'REFRESH'},
                                     {'id':7,'username':'Account'},provider=provider)
    return store,a,b


async def native_plan(store,provider,*,actor_id=None,guild_id=None):
    context=await store.get_rebuild_context('42',provider,origin_guild=guild_id)
    entries,markers,skipped=await fetch_provider_entries(native_app(provider),'42',{},provider,context['baseline'])
    _,report=rebuild_from_history(context['baseline'],entries,context['guild_timezones'],STAMP,provider=provider,account_id='7')
    report.update(rebuild_entries=entries,source_markers=markers,guild_timezones=context['guild_timezones'],
                  guild_fingerprint=context['guild_fingerprint'],source_account_id='7')
    plan=await store.prepare_history_repair('42',context['baseline'],report,actor_id=actor_id,guild_id=guild_id)
    return plan,report


@pytest.mark.parametrize('provider',['wetrakr','mdblist'])
def test_native_apply_and_followup_full_reconcile_do_not_recredit_history(tmp_path,monkeypatch,provider):
    async def run():
        store,a,b=await native_store(tmp_path,monkeypatch,provider)
        await store.set_activity_provider('123','42',provider)
        other=await store.get_user('43')
        plan,report=await native_plan(store,provider)
        receipt=await store.apply_history_repair('42',plan['token'],source_markers=report['source_markers'])
        before=(await store.get_progression('42'))['lifetime_xp']
        assert receipt['rebuild_provider']==provider
        plays=[e['native_play'] for e in report['rebuild_entries']]
        result=await store.reconcile_provider_plays('123','42',plays,complete=True,account_id='7',provider=provider)
        assert result['xp']==0 and (await store.get_progression('42'))['lifetime_xp']==before
        assert await store.get_user('43')==other
        stats=await store.get_statistics('123','42')
        assert stats['movies_watched']==2 and stats['episodes_watched']==1
        assert stats['anime_movies_watched']==1 and stats['anime_episodes_watched']==1
        current=await store.get_progression('42')
        assert any(a['event_key'] in row['observations'] for row in current['occurrence_ledger'].values())
        assert await module.Storage().get_progression('42')==current
    asyncio.run(run())


@pytest.mark.parametrize('wrong',['actor','target','guild','self_service'])
def test_admin_tokens_are_bound_to_actor_target_and_server(tmp_path,monkeypatch,wrong):
    async def run():
        store,_,_=await native_store(tmp_path,monkeypatch,'mdblist')
        plan,report=await native_plan(store,'mdblist',actor_id='99',guild_id='123')
        before=await store.get_all()
        uid='43' if wrong=='target' else '42'
        actor='100' if wrong=='actor' else None if wrong=='self_service' else '99'
        guild='321' if wrong=='guild' else '123'
        with pytest.raises(ValueError):await store.apply_history_repair(uid,plan['token'],source_markers=report['source_markers'],actor_id=actor,guild_id=guild)
        assert await store.get_all()==before
        assert not list(tmp_path.glob('repair-backups/*'))
    asyncio.run(run())


def test_admin_apply_is_audited_and_member_unlink_invalidates_plan(tmp_path,monkeypatch):
    async def run():
        store,_,_=await native_store(tmp_path,monkeypatch,'wetrakr')
        plan,report=await native_plan(store,'wetrakr',actor_id='99',guild_id='123')
        receipt=await store.apply_history_repair('42',plan['token'],source_markers=report['source_markers'],actor_id='99',guild_id='123')
        assert receipt['scope']=={'actor':'99','target':'42','guild':'123'}
        with pytest.raises(ValueError):await store.apply_history_repair('42',plan['token'],actor_id='100',guild_id='123')
        plan,report=await native_plan(store,'wetrakr',actor_id='99',guild_id='123')
        store._data['guilds']['123']['users']['42']['wetrakr_linked']=False
        before=await store.get_progression('42')
        with pytest.raises(ValueError):await store.apply_history_repair('42',plan['token'],source_markers=report['source_markers'],actor_id='99',guild_id='123')
        assert await store.get_progression('42')==before
    asyncio.run(run())


def interaction(admin=True,guild='123',actor='99'):
    return SimpleNamespace(guild_id=int(guild),guild=SimpleNamespace(id=int(guild)),
          user=SimpleNamespace(id=int(actor),guild_permissions=SimpleNamespace(manage_guild=admin)),
          response=SimpleNamespace(defer=AsyncMock(),send_message=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()))


def test_admin_command_checks_permissions_and_current_target_membership(monkeypatch):
    async def run():
        from test_notification_preview import bot
        helper=AsyncMock();monkeypatch.setattr(bot,'prepare_rebuild_preview',helper)
        member=SimpleNamespace(id=42,guild=SimpleNamespace(id=123))
        i=interaction(admin=False)
        await bot.tracker_rebuild.callback(i,member)
        helper.assert_not_awaited();i.response.send_message.assert_awaited_once()
        i=interaction(guild='321')
        await bot.tracker_rebuild.callback(i,member)
        helper.assert_not_awaited()
        i=interaction()
        source=bot.app_commands.Choice(name='MDBList',value='mdblist')
        await bot.tracker_rebuild.callback(i,member,source=source)
        helper.assert_awaited_once_with(i,'42','mdblist',admin=True)
        # Revoked permission is checked before any apply request can run.
        i=interaction(admin=False)
        apply=AsyncMock();monkeypatch.setattr(bot,'execute_history_repair',apply)
        await bot.tracker_rebuild.callback(i,member,apply_token='a'*16)
        apply.assert_not_awaited()
    asyncio.run(run())


def test_admin_preview_and_apply_uses_target_credentials_privately(tmp_path,monkeypatch):
    async def run():
        from test_notification_preview import bot
        import trackerbot.core.progression_rebuild as rebuild
        store,_,_=await native_store(tmp_path,monkeypatch,'mdblist')
        app=native_app('mdblist');context=await store.get_rebuild_context('42','mdblist')
        entries,markers,skipped=await fetch_provider_entries(app,'42',{},'mdblist',context['baseline'])
        read=AsyncMock(return_value=(entries,markers,skipped));monkeypatch.setattr(rebuild,'fetch_provider_entries',read)
        monkeypatch.setattr(bot,'storage',store)
        i=interaction();member=SimpleNamespace(id=42,guild=i.guild)
        await bot.tracker_rebuild.callback(i,member,source=bot.app_commands.Choice(name='MDBList',value='mdblist'))
        sent=i.followup.send.await_args.kwargs;report=json.loads(sent['file'].fp.read());sent['file'].close()
        assert sent['ephemeral'] and report['requested_by']=='99' and report['target_user']=='42'
        assert read.await_args.args[1]=='42' and read.await_args.args[2]['mdblist']['access_token']=='SECRET'
        assert 'SECRET' not in json.dumps(report) and '/bingecord rebuild' in report['apply_command']
        monkeypatch.setattr(rebuild,'provider_markers',AsyncMock(return_value=(markers,'SECRET')))
        await bot.tracker_rebuild.callback(i,member,apply_token=report['apply_token'])
        assert (await store.get_progression('42'))['lifetime_xp']==report['projected']['lifetime_xp']
        assert 'MDBList progression rebuilt' in i.followup.send.await_args.args[0]
    asyncio.run(run())


def test_persisted_legacy_simkl_plan_still_applies_after_upgrade(tmp_path,monkeypatch):
    async def run():
        from test_progression_rebuild import prepare_rebuild
        store,_,_=await fixture_store(tmp_path,monkeypatch)
        plan,report,_=await prepare_rebuild(store,[entry()])
        persisted=store._data['history_repair_plans']['42']
        persisted.pop('scope',None)
        settings=persisted['rebuild']
        settings.pop('provider',None);settings.pop('account_id',None)
        settings['simkl_markers']=settings.pop('source_markers')
        receipt=await store.apply_history_repair('42',plan['token'],simkl_markers=report['simkl_markers'])
        assert receipt['rebuild_provider']=='simkl' and receipt['projected']==report['projected']
    asyncio.run(run())


def test_admin_command_rejects_ordinary_repair_token_before_any_mutation(tmp_path,monkeypatch):
    async def run():
        from test_notification_preview import bot
        from test_history_repair_apply import prepare
        store,_,_=await fixture_store(tmp_path,monkeypatch)
        plan,_,_=await prepare(store)
        monkeypatch.setattr(bot,'storage',store)
        before=await store.get_all()
        i=interaction(actor='42');member=SimpleNamespace(id=42,guild=i.guild)
        await bot.tracker_rebuild.callback(i,member,apply_token=plan['token'])
        assert await store.get_all()==before
        assert 'rebuild preview token' in i.followup.send.await_args.args[0]
    asyncio.run(run())
