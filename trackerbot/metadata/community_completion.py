"""Reuse verified activity completion, retaining its supporting watch signatures."""
import asyncio
import copy
from datetime import datetime,timedelta,timezone
from trackerbot.core.community import community_week
from trackerbot.core.community_events import parse_stamp
from trackerbot.core.activity_details import ids_match


def watch_signature(row):
    return str(row.get('item_key') or '')+'|'+str(row.get('watched_at') or '')


async def refresh_community_completions(app,guild_id,now):
    saved,zone=await app.storage.community_completion_inputs(guild_id)
    candidates={key:value for key,value in saved.items()
                if parse_stamp(value.get('at')) and parse_stamp(value['at'])>=now-timedelta(days=21)}
    for record in await app.storage.get_activity_messages(guild_id):
        at=parse_stamp((record.get('base') or {}).get('timestamp'))
        if not at or not now-timedelta(days=21)<=at<=now:continue
        for part in record.get('parts',[]):
            meta=part.get('meta') or {};sid=(meta.get('ids') or {}).get('tmdb')
            if str(part.get('guild'))!=str(guild_id) or meta.get('movie') or not sid:continue
            if not (part.get('milestone') or part.get('transition') or meta.get('completion_reported')):continue
            try:canonical=f'tv:tmdb:{int(sid)}'
            except (TypeError,ValueError):continue
            week,_,_=community_week(at,zone)
            key='|'.join((week,str(part['user']),part['source'],str(part['account']),canonical))
            candidates[key]={**candidates.get(key,{}),'part':copy.deepcopy(part),'at':at.isoformat(),'canonical':canonical,
                             'uid':str(part['user']),'provider':part['source'],'account':str(part['account'])}
    result={};gate=asyncio.Semaphore(3)
    async def verify(key,entry):
        async with gate:
            result[key]={**entry,'attempted_at':now.isoformat()}
            try:
                part=entry['part']
                if not await app.activity_details.valid(part):return
                milestone=await app.activity_details.milestone(part)
                if not milestone or milestone.get('kind') not in {'season','series'}:
                    result[key]={**result[key],'support':[]};return
                rows=await app.activity_details.watches(part)
                support=sorted({watch_signature(row) for row in rows if ids_match(row.get('ids') or {},part['meta'].get('ids') or {})})
                if support:result[key]={**result[key],'support':support,'verified_at':now.isoformat()}
            except Exception:
                # An API error is not proof of an unwatch. Stored support is
                # independently checked against the current selected-source view.
                return
    tasks=[asyncio.create_task(verify(key,value)) for key,value in sorted(candidates.items(),key=lambda item:(item[1].get('attempted_at',''),item[0]))[:12]]
    try:
        async with asyncio.timeout(6):await asyncio.gather(*tasks)
    except TimeoutError:pass
    finally:
        for task in tasks:
            if not task.done():task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
    await app.storage.cache_community_completions(guild_id,result)
