"""Bounded history enrichment, durable staging and shared read requests."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import time

from .watch_delivery import current_cycle

log=logging.getLogger('bingecord')


class SingleFlight:
    def __init__(self):self.pending={}
    async def run(self,key,read):
        if key in self.pending:return await self.pending[key]
        task=asyncio.create_task(read());self.pending[key]=task
        try:return await task
        finally:
            if self.pending.get(key) is task:self.pending.pop(key,None)


class ImportJobs:
    def __init__(self,provider='tracker'):
        self.tasks={};self.provider=provider;self.limit=asyncio.Semaphore(2)
    def start(self,guild,uid,account,run):
        key=(str(guild),str(uid),str(account))
        if key in self.tasks and not self.tasks[key].done():return
        async def worker():
            token=current_cycle.set(None)
            try:
                async with self.limit:await run()
            except Exception:log.exception('Background history import failed for guild %s user %s.',guild,uid)
            finally:current_cycle.reset(token)
        self.tasks[key]=asyncio.create_task(worker())
        log.info('%s history import queued for guild %s user %s.',self.provider,guild,uid)
    def active(self,guild):
        for key,task in list(self.tasks.items()):
            if task.done():self.tasks.pop(key,None)
        return [task for key,task in self.tasks.items() if key[0]==str(guild)]


async def bounded_map(rows,resolve,semaphore):
    results=[None]*len(rows);queue=iter(enumerate(rows))
    async def worker():
        for index,row in queue:
            async with semaphore:results[index]=await resolve(row)
    tasks=[asyncio.create_task(worker()) for _ in range(min(4,len(rows)))]
    try:await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
    return results


@asynccontextmanager
async def import_slot():
    # Let the coordinator deliver unrelated watches while history awaits I/O.
    cycle=current_cycle.get();task=asyncio.current_task()
    already=cycle is not None and task in cycle.blocked
    if cycle is not None:cycle.blocked.add(task);cycle.changed.set()
    try:yield
    finally:
        if cycle is not None and not already:cycle.blocked.discard(task);cycle.changed.set()


class ImportPage(dict):pass
class ImportRows(list):pass


def checkpoint_page(data,cursor):
    page=ImportRows(data) if isinstance(data,list) else ImportPage(data)
    page.next_cursor=cursor
    return page


def digest(row):
    return hashlib.sha256(json.dumps(row,sort_keys=True,separators=(',',':')).encode()).hexdigest()


class HistoryImport:
    def __init__(self,provider):
        self.provider=provider;self.locks={};self.semaphore=asyncio.Semaphore(4)
        self.jobs=ImportJobs(provider)

    async def run(self,store,guild,uid,account,signature,mark,kinds,reader,normalizer,resolver):
        key=(str(uid),str(account))
        lock=self.locks.setdefault(key,asyncio.Lock())
        async with import_slot(),lock:
            stage=await store.get_import_stage(uid,self.provider,account)
            # Changed histories restart pagination; exact normalized rows can
            # still reuse their already enriched result. Never finalize a mix.
            stale=time.time()-stage.get('started',0)>86400
            if stale:stage={}
            if stage.get('signature')!=signature:
                stage={'signature':signature,'mark':mark,'started':time.time(),
                       'resolved':stage.get('resolved',{}),'phases':{}}
            stage.setdefault('phases',{});stage.setdefault('resolved',{})
            hashes={}
            def row_key(row):
                key=id(row)
                if key not in hashes:hashes[key]=digest(row)
                return hashes[key]
            async def save(status,kind=None):
                done=sum(len(p.get('rows',[])) for p in stage['phases'].values())
                pending=sum(len((p.get('pending') or {}).get('rows',[])) for p in stage['phases'].values())
                enriched=done+sum(1 for p in stage['phases'].values() for row in (p.get('pending') or {}).get('rows',[])
                                  if row_key(row) in stage['resolved'])
                stage['progress']={'status':status,'phase':kind,'downloaded':done+pending,
                                   'enriched':enriched,'updated_at':datetime.now(timezone.utc).isoformat()}
                await store.save_import_stage(guild,uid,self.provider,account,stage)
                log.info('%s history import user %s: %s, %s, %d downloaded, %d enriched.',
                         self.provider,uid,status,kind or 'history',done+pending,enriched)
            try:
                for kind in kinds:
                    phase=stage['phases'].setdefault(kind,{'rows':[],'cursor':None,'done':False})
                    while not phase['done']:
                        if 'pending' not in phase:
                            # Re-open at the last completed cursor. Fake/legacy
                            # readers without cursor metadata safely restart a phase.
                            await save('downloading',kind)
                            iterator=reader(kind,phase['cursor']).__aiter__()
                            try:
                                page=await anext(iterator)
                                rows=normalizer(page,kind)
                                if not hasattr(page,'next_cursor'):
                                    async for legacy in iterator:rows.extend(normalizer(legacy,kind))
                            except StopAsyncIteration:
                                phase['done']=True;break
                            finally:await iterator.aclose()
                            ids=[str(row.get('source_event_id') or '') for row in rows]
                            known={str(row.get('source_event_id')) for p in stage['phases'].values() for row in p['rows']}
                            if not all(ids) or len(ids)!=len(set(ids)) or known.intersection(ids):
                                raise ValueError('Duplicate or missing import play identity')
                            cursor=getattr(page,'next_cursor',None)
                            if cursor and (not isinstance(cursor,str) or cursor==phase['cursor'] or cursor in phase.get('cursors',[])):
                                raise ValueError('Repeated import cursor')
                            phase['pending']={'rows':rows,'cursor':cursor}
                            await save('enriching',kind)
                        pending=phase['pending']
                        missing=[row for row in pending['rows'] if row_key(row) not in stage['resolved']]
                        for offset in range(0,len(missing),128):
                            batch=missing[offset:offset+128]
                            values=await bounded_map(batch,resolver,self.semaphore)
                            for row,value in zip(batch,values):stage['resolved'][row_key(row)]=value
                            await save('enriching',kind)
                        phase['rows'].extend(pending['rows']);phase['cursor']=pending['cursor']
                        if phase['cursor']:phase.setdefault('cursors',[]).append(phase['cursor'])
                        phase['done']=not bool(phase['cursor']);phase.pop('pending')
                        await save('downloading' if not phase['done'] else 'history ready',kind)
                # Prune enriched records from earlier snapshots only after a
                # complete read. Staging never awards/revokes XP itself.
                rows=[row for kind in kinds for row in stage['phases'][kind]['rows']]
                stage['resolved']={row_key(row):stage['resolved'][row_key(row)] for row in rows}
                await save('history ready')
                return [stage['resolved'][row_key(row)] for row in rows],stage['mark']
            except BaseException as exc:
                if isinstance(exc,Exception):
                    # Expired/invalid cursors must be retried from a fresh read.
                    if getattr(exc,'status',0) in {400,404,409,410}:stage['phases']={}
                    await save('interrupted')
                raise
