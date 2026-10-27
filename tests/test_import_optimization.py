import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from trackerbot.core import storage as storage_module
from trackerbot.core.import_work import HistoryImport,SingleFlight,bounded_map,checkpoint_page,import_slot
from trackerbot.core.import_work import ImportJobs
from trackerbot.core.watch_delivery import WatchCycle,deliver_watch
from trackerbot.metadata.tmdb_client import TmdbClient


async def store_fixture(tmp_path,monkeypatch):
    monkeypatch.setattr(storage_module,'DATA_PATH',str(tmp_path/'store.json'))
    store=storage_module.Storage()
    await store.link_provider_account('1','42',{'access_token':'a','refresh_token':'r'},
                                      {'id':7,'username':'Viewer'},provider='mdblist')
    return store


def test_bounded_enrichment_is_parallel_and_preserves_order():
    async def run():
        active=peak=0;gate=asyncio.Event()
        async def resolve(row):
            nonlocal active,peak
            active+=1;peak=max(peak,active)
            if active==4:gate.set()
            await gate.wait();await asyncio.sleep(0);active-=1
            return row*2
        result=await asyncio.wait_for(bounded_map(list(range(30)),resolve,asyncio.Semaphore(4)),2)
        assert result==[n*2 for n in range(30)] and peak==4
    asyncio.run(run())


def test_failed_enrichment_cancels_sibling_workers():
    async def run():
        stopped=asyncio.Event()
        async def resolve(row):
            if row==0:await asyncio.sleep(0);raise ValueError('bad metadata')
            try:await asyncio.Event().wait()
            finally:stopped.set()
        with pytest.raises(ValueError):await bounded_map(list(range(4)),resolve,asyncio.Semaphore(4))
        assert stopped.is_set()
    asyncio.run(run())


def test_singleflight_and_season_reads_share_one_request(monkeypatch):
    async def run():
        client=TmdbClient('test');gate=asyncio.Event();started=asyncio.Event()
        async def read(*args):started.set();await gate.wait();return {'episodes':[{'episode_number':1,'runtime':24}]}
        request=AsyncMock(side_effect=read);monkeypatch.setattr(client,'_get_json_request',request)
        tasks=[asyncio.create_task(client._get_season_details(100,1)) for _ in range(4)]
        await started.wait();await asyncio.sleep(0);assert request.await_count==1
        gate.set();await asyncio.gather(*tasks)
        await client._get_season_details(100,1);assert request.await_count==1
        shared=SingleFlight();attempt=AsyncMock(side_effect=[ValueError('temporary'),42])
        with pytest.raises(ValueError):await shared.run('same',attempt)
        assert await shared.run('same',attempt)==42
    asyncio.run(run())


def test_restart_resumes_pending_page_and_enrichment_without_touching_xp(tmp_path,monkeypatch):
    async def run():
        store=await store_fixture(tmp_path,monkeypatch);before=await store.get_progression('42')
        reads=[];resolved=[]
        rows=[{'source_event_id':str(i),'watched_at':'2026-10-27T06:00:00Z'} for i in range(160)]
        async def reader(kind,cursor):
            reads.append(cursor)
            yield checkpoint_page({'rows':rows if cursor is None else [{'source_event_id':'last'}]},'next' if cursor is None else None)
        async def fail(row):
            if row['source_event_id']=='130':raise RuntimeError('temporary')
            return {**row,'enriched':True}
        importer=HistoryImport('mdblist')
        with pytest.raises(RuntimeError):
            await importer.run(store,'1','42','7','same','old mark',('movies',),reader,lambda p,k:p['rows'],fail)
        progress=await store.get_import_progress('42','mdblist','7')
        assert progress['enriched']==128 and progress['downloaded']==160 and progress['status']=='interrupted'
        assert await store.get_progression('42')==before
        assert not (await store.get_provider_targets('mdblist','1',active_only=True))[0]['guild_user_data']['mdblist_sync']['seeded']
        store=storage_module.Storage();importer=HistoryImport('mdblist')
        async def resolve(row):resolved.append(row['source_event_id']);return {**row,'enriched':True}
        result,mark=await importer.run(store,'1','42','7','same','new mark',('movies',),reader,lambda p,k:p['rows'],resolve)
        assert len(result)==161 and mark=='old mark' and reads==[None,'next']
        assert not set(resolved)&{str(i) for i in range(128)}
        assert await store.get_progression('42')==before
        # Neither tokens nor staged data are copied into routine user reads.
        assert 'import_staging' not in await store.get_user('42')
    asyncio.run(run())


def test_changed_snapshot_restarts_paging_but_reuses_exact_enrichment(tmp_path,monkeypatch):
    async def run():
        store=await store_fixture(tmp_path,monkeypatch);importer=HistoryImport('mdblist');rows=[{'source_event_id':'old'},{'source_event_id':'keep'}]
        reads=[];resolve=AsyncMock(side_effect=lambda p:p)
        async def reader(kind,cursor):reads.append(cursor);yield checkpoint_page({'rows':list(rows)},None)
        args=(store,'1','42','7')
        await importer.run(*args,'first','first mark',('movies',),reader,lambda p,k:p['rows'],resolve)
        rows[:]=[{'source_event_id':'keep'},{'source_event_id':'new'}]
        result,mark=await importer.run(*args,'changed','second mark',('movies',),reader,lambda p,k:p['rows'],resolve)
        assert [row['source_event_id'] for row in result]==['keep','new'] and mark=='second mark'
        assert resolve.await_count==3 and reads==[None,None]
        assert await store.get_import_stage('42','mdblist','other')=={}
        await store.unlink_provider_account('1','42',provider='mdblist')
        with pytest.raises(ValueError):await store.save_import_stage('1','42','mdblist','7',{})
    asyncio.run(run())


def test_repeated_cursor_cannot_finish_partial_snapshot(tmp_path,monkeypatch):
    async def run():
        store=await store_fixture(tmp_path,monkeypatch);importer=HistoryImport('mdblist')
        async def reader(kind,cursor):yield checkpoint_page({'rows':[{'source_event_id':str(cursor)}]},'repeat')
        with pytest.raises(ValueError,match='cursor'):
            await importer.run(store,'1','42','7','same','mark',('movies',),reader,lambda p,k:p['rows'],AsyncMock(side_effect=lambda p:p))
        assert not (await store.get_provider_targets('mdblist','1',active_only=True))[0]['guild_user_data']['mdblist_sync']['seeded']
    asyncio.run(run())


def test_live_watch_is_delivered_while_unrelated_import_is_waiting():
    async def run():
        from tests.test_watch_cycle import activity
        release=asyncio.Event();sender=AsyncMock(side_effect=lambda *args:release.set() or True);cycle=WatchCycle(sender)
        async def importing():
            async with import_slot():await release.wait()
        async def watching():return await deliver_watch(sender,activity(1,'SIMKL'))
        await asyncio.wait_for(cycle.execute([cycle.run(importing()),cycle.run(watching())]),2)
        assert sender.await_count==1
    asyncio.run(run())


def test_checknow_edits_progress_while_work_runs(monkeypatch):
    async def run():
        from test_notification_preview import bot
        release=asyncio.Event()
        async def poll(*args,**kwargs):await release.wait();return {'mdblist':0}
        async def edit(**kwargs):release.set()
        monkeypatch.setattr(bot,'poll_providers',poll)
        monkeypatch.setattr(bot,'import_progress_text',AsyncMock(return_value='MDBList: 128/160 downloaded plays enriched'))
        i=SimpleNamespace(edit_original_response=AsyncMock(side_effect=edit))
        assert await asyncio.wait_for(bot.poll_with_progress(i,'1',update_interval=0),2)=={'mdblist':0}
        assert '128/160' in i.edit_original_response.await_args.kwargs['content']
    asyncio.run(run())


def test_mdblist_resumed_baseline_retains_original_sync_boundary(tmp_path,monkeypatch):
    async def run():
        from tests.test_mdblist_provider import setup,movie,STAMP
        store,api,app,provider=await setup(tmp_path,monkeypatch)
        api.movies=[movie(1),movie(2)]
        original=provider.resolve_play
        failed=False
        async def resolve(play):
            nonlocal failed
            if play['source_event_id']=='2' and not failed:
                failed=True;raise RuntimeError('interrupted metadata')
            return await original(play)
        monkeypatch.setattr(provider,'resolve_play',resolve)
        await provider.poll('1')
        assert not (await store.get_provider_targets('mdblist','1',active_only=True))[0]['guild_user_data']['mdblist_sync']['seeded']
        original_activities=api.last_activities
        async def later(token):return {**await original_activities(token),'server_time':'2026-10-28T10:00:00Z'}
        api.last_activities=later
        await provider.poll('1')
        state=(await store.get_provider_targets('mdblist','1',active_only=True))[0]['guild_user_data']['mdblist_sync']
        assert state['seeded'] and state['checkpoint']==STAMP
        app.deliver_mdblist_play.assert_not_awaited()
    asyncio.run(run())


def test_we_history_resume_cursor_and_shared_title_request(monkeypatch):
    async def run():
        from trackerbot.integrations.wetrakr_client import WeTrakrClient
        from tests.test_wetrakr_prototype import Session,Response
        client=WeTrakrClient('key',Session([Response([{'play_id':'one'}],{'X-Pagination-Next':'second'}),Response([])]))
        first=[page async for page in client.compact_history('token','movies',after='saved')]
        assert first[0].next_cursor=='second' and client._session.calls[0][2]['params']['after']=='saved'
        gate=asyncio.Event();started=asyncio.Event()
        async def request(*args,**kwargs):started.set();await gate.wait();return {'title':'Title'},{}
        read=AsyncMock(side_effect=request);monkeypatch.setattr(client,'_request',read)
        tasks=[asyncio.create_task(client.title('show',10)) for _ in range(4)]
        await started.wait();await asyncio.sleep(0);assert read.await_count==1
        gate.set();await asyncio.gather(*tasks)
    asyncio.run(run())


def test_expired_progress_message_does_not_cancel_import(monkeypatch):
    async def run():
        import discord
        from test_notification_preview import bot
        release=asyncio.Event()
        async def poll(*args,**kwargs):await release.wait();return {'mdblist':0}
        async def edit(**kwargs):
            release.set()
            raise discord.NotFound(SimpleNamespace(status=404,reason='Not found'),{'message':'Expired'})
        monkeypatch.setattr(bot,'poll_providers',poll)
        monkeypatch.setattr(bot,'import_progress_text',AsyncMock(return_value='Importing'))
        i=SimpleNamespace(edit_original_response=AsyncMock(side_effect=edit))
        assert await asyncio.wait_for(bot.poll_with_progress(i,'1',update_interval=0),2)=={'mdblist':0}
    asyncio.run(run())


def test_background_import_does_not_hold_poll_cycle_or_start_duplicate_job():
    async def run():
        jobs=ImportJobs();release=asyncio.Event();started=asyncio.Event();calls=0
        async def importing():
            nonlocal calls
            calls+=1;started.set();await release.wait()
        async def poll():
            jobs.start('1','42','7',importing)
            return 0
        cycle=WatchCycle(AsyncMock(return_value=True))
        assert await asyncio.wait_for(cycle.execute([cycle.run(poll())]),2)==[0]
        await started.wait()
        assert len(jobs.active('1'))==1
        # A later polling cycle can complete while the same import remains pending.
        assert await asyncio.wait_for(cycle.execute([cycle.run(poll())]),2)==[0]
        assert calls==1 and jobs.active('other')==[]
        release.set();await asyncio.gather(*jobs.active('1'))
        assert jobs.active('1')==[]
    asyncio.run(run())


def test_manual_progress_waits_for_background_import_after_poll_finishes(monkeypatch):
    async def run():
        from test_notification_preview import bot
        jobs=ImportJobs();release=asyncio.Event()
        async def importing():await release.wait()
        async def poll(*args,**kwargs):jobs.start('1','42','7',importing);return {'mdblist':0}
        provider=bot.provider_registry.get('mdblist')
        monkeypatch.setattr(provider.imports,'jobs',jobs)
        monkeypatch.setattr(bot,'poll_providers',poll)
        monkeypatch.setattr(bot,'import_progress_text',AsyncMock(return_value='MDBList: enriching'))
        async def edit(**kwargs):release.set()
        i=SimpleNamespace(edit_original_response=AsyncMock(side_effect=edit))
        assert await asyncio.wait_for(bot.poll_with_progress(i,'1',update_interval=0),2)=={'mdblist':0}
        assert i.edit_original_response.await_count>=1 and jobs.active('1')==[]
    asyncio.run(run())


def test_source_switch_is_checked_atomically_at_import_reconciliation(tmp_path,monkeypatch):
    async def run():
        store=await store_fixture(tmp_path,monkeypatch)
        await store.link_wetrakr('1','42',{'access_token':'a','refresh_token':'r'},{'id':9})
        await store.set_activity_provider('1','42','mdblist')
        before=await store.get_progression('42')
        with pytest.raises(ValueError,match='selection'):
            await store.reconcile_wetrakr_plays('1','42',[],complete=True,account_id='9',require_selected=True)
        assert await store.get_progression('42')==before
    asyncio.run(run())
