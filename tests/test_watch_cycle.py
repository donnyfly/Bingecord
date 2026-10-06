import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import discord
from datetime import datetime,timezone
from trackerbot.core.watch_delivery import WatchActivity,WatchBatch,WatchCycle,deliver_watch,watch_key


def activity(user,provider,commit=None):
    embed=discord.Embed(description='Watched **S2E01** of **Anime**\n*Pilot*\n⭐ IMDb 8.5/10')
    embed.set_footer(text='Anime · '+provider)
    return WatchActivity('1',str(user),'anime',SimpleNamespace(id=9),watch_key('episode',{'tmdb':100},2,(1,)),
        (datetime(2026,10,5,tzinfo=timezone.utc),),embed,'**S2E01** of **Anime**',commit)


def test_mixed_provider_cycle_waits_for_post_before_acknowledging():
    async def run():
        sender=AsyncMock(return_value=True);cycle=WatchCycle(sender);ack=[]
        async def simkl():
            async def commit():ack.append('simkl')
            batch=WatchBatch(sender);batch.add(activity(1,'SIMKL',commit))
            await batch.deliver()
        async def native(uid,provider):
            assert await deliver_watch(sender,activity(uid,provider))
            ack.append(provider)
        async def natives():
            await asyncio.gather(cycle.run(native(2,'WeTrakr')),cycle.run(native(3,'MDBList')))
        await asyncio.wait_for(cycle.execute([cycle.run(simkl()),natives()]),2)
        assert sender.await_count==1 and len(ack)==3
        embed=sender.await_args.args[1]
        assert embed.author.name=='Watched Together'
        assert all(f'<@{u}>' in embed.description for u in (1,2,3))
        assert 'SIMKL + WeTrakr + MDBList' in embed.footer.text
    asyncio.run(run())


def test_failed_cycle_never_acknowledges_and_can_retry():
    async def run():
        sender=AsyncMock(return_value=False);cycle=WatchCycle(sender);committed=AsyncMock()
        async def work():
            batch=WatchBatch(sender);batch.add(activity(1,'SIMKL',committed));await batch.deliver()
            assert not batch.activities[0].delivered
        await asyncio.wait_for(cycle.execute([cycle.run(work())]),2)
        committed.assert_not_awaited()
        sender.return_value=True
        await asyncio.wait_for(cycle.execute([cycle.run(deliver_watch(sender,activity(2,'MDBList')))]),2)
    asyncio.run(run())


def test_different_seasons_and_same_user_do_not_merge():
    assert watch_key('episode',{'tmdb':100},1,(1,))!=watch_key('episode',{'tmdb':100},2,(1,))
    batch=WatchBatch(None)
    batch.add(activity(1,'MDBList'));batch.add(activity(1,'MDBList'))
    assert len(list(batch.groups()))==2


def test_delayed_provider_registration_joins_existing_poll():
    async def run():
        sender=AsyncMock(return_value=True);cycle=WatchCycle(sender)
        gate=asyncio.Event()
        async def early():
            gate.set()
            return await deliver_watch(sender,activity(1,'SIMKL'))
        async def late():
            await gate.wait()
            await asyncio.sleep(0)
            return await cycle.join([deliver_watch(sender,activity(2,'WeTrakr'))])
        await asyncio.wait_for(cycle.execute([cycle.run(early()),cycle.run(late())]),2)
        assert sender.await_count==1
    asyncio.run(run())


def test_cancelled_cycle_cleans_up_pending_workers():
    async def run():
        sender=AsyncMock(return_value=True);cycle=WatchCycle(sender);cancelled=asyncio.Event()
        async def stuck():
            try:await asyncio.Event().wait()
            finally:cancelled.set()
        task=asyncio.create_task(cycle.execute([cycle.run(stuck()),cycle.run(deliver_watch(sender,activity(1,'MDBList')))]))
        await asyncio.sleep(0);await asyncio.sleep(0)
        task.cancel()
        try:await task
        except asyncio.CancelledError:pass
        assert cancelled.is_set() and not cycle.running
        sender.assert_not_awaited()
    asyncio.run(run())


def test_native_second_watch_can_join_waiting_simkl_batch():
    async def run():
        sender=AsyncMock(return_value=True);cycle=WatchCycle(sender)
        def make(user,provider,number,commit=None):
            item=activity(user,provider,commit);item.key=watch_key('episode',{'tmdb':100},2,(number,));return item
        async def simkl():
            batch=WatchBatch(sender)
            for number in (1,2):batch.add(make(1,'SIMKL',number,AsyncMock()))
            await batch.deliver()
        async def native():
            for number in (1,2):assert await deliver_watch(sender,make(2,'MDBList',number))
        await asyncio.wait_for(cycle.execute([cycle.run(simkl()),cycle.run(native())]),2)
        assert sender.await_count==2
        assert all(call.args[1].author.name=='Watched Together' for call in sender.await_args_list)
    asyncio.run(run())
