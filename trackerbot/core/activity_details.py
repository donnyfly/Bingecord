"""Account-scoped personal ratings and conservative completion annotations.

Only recent bot messages are edited. Neither snapshots nor edits mutate watches/XP.
"""
import asyncio
from contextvars import ContextVar
import copy
from datetime import datetime, timedelta, timezone
import logging
import time

import discord
from .completion import outcome
from trackerbot.integrations.personal_ratings import fetch

log=logging.getLogger('bingecord')
current_post=ContextVar('activity_detail_post',default=None)
WINDOW=timedelta(days=7)


def ids_match(left,right):
    # Provider-native entries can have seasonal TMDB aliases; the exact entry wins.
    for key in ('simkl','wetrakr','mdblist'):
        if left.get(key) is not None and right.get(key) is not None and str(left[key])==str(right[key]):return True
    common=[key for key in ('tmdb','tvdb','imdb','mal') if left.get(key) is not None and right.get(key) is not None]
    return bool(common) and all(str(left[key])==str(right[key]) for key in common)


def rating_for(rows,meta,milestone=None):
    movie=meta.get('movie',False)
    scope='movie' if movie else 'show' if meta.get('status') else 'episode'
    candidates=[]
    if milestone and milestone['kind']=='season':
        candidates.append(('season','Your season rating',milestone['season'],None))
    if scope=='episode' and len(meta.get('episodes') or [])==1:
        candidates.append(('episode','Your episode rating',meta.get('season'),meta['episodes'][0]))
    if scope in {'movie','show'} or milestone:
        candidates.append(('movie' if movie else 'show','Your rating' if movie else 'Your series rating',None,None))
    for kind,label,season,episode in candidates:
        matches=[row for row in rows if row['kind']==kind and ids_match(row['ids'],meta['ids'])
                 and (season is None or (row.get('season')==season and (episode is None or row.get('episode')==episode))
                      or (kind=='episode' and row.get('season')==meta.get('source_season')
                          and row.get('episode')==((meta.get('source_episodes') or [None])[0])))]
        if len(matches)==1:
            row=matches[0]
            # A SIMKL seasonal entry is only labelled as a season when its scope is explicit.
            if kind=='show' and meta.get('entry_seasons')==[meta.get('season')] and milestone and milestone['kind']=='season':
                label='Your season-entry rating'
            return f"{label}: **{row['score']:g}/{row['scale']:g}**"
    return None


class ActivityDetails:
    def __init__(self,app):
        self.app=app
        self.contexts={}
        self.catalogs={}
        self.catalog_lock=asyncio.Lock()
        self.sent=[]
        self.errors={}

    async def begin(self,guild_id=None):
        self.contexts={};self.sent=[];self.errors={}
        accounts={}
        for manifest in self.app.provider_registry.manifests():
            source=manifest.name
            configured={'simkl':bool(self.app.SIMKL_CLIENT_ID),'wetrakr':bool(self.app.wetrakr_sync),
                        'mdblist':bool(self.app.mdblist_tracking)}[source]
            if not configured:continue
            targets=await self.app.storage.get_provider_targets(source,guild_id,active_only=True)
            for target in targets:
                gid,uid=str(target['guild_id']),str(target['discord_user_id'])
                account=None
                try:
                    account=await self.app.provider_registry.get(source).link(uid)
                    state=await self.app.storage.get_activity_detail_state(gid,uid,source,account.account_id)
                    provider=self.app.provider_registry.get(source)
                    if getattr(provider,'paused_until',{}).get(str(account.account_id),0)>time.monotonic():
                        raise RuntimeError('Provider backoff active')
                    cache_key=(uid,source,account.account_id)
                    if cache_key not in accounts:
                        mark,rows=await fetch(self.app,source,account,state.get('ratings_signature'))
                        accounts[cache_key]=(mark,rows if rows is not None else state.get('ratings',[]))
                    mark,rows=accounts[cache_key]
                    if rows is not None:state['ratings']=rows
                    state['ratings_signature']=mark
                    state.pop('error',None)
                    await self.app.storage.save_activity_detail_state(gid,uid,source,account.account_id,state)
                except Exception as exc:
                    if account is not None and getattr(exc,'retry_after',None):
                        provider=self.app.provider_registry.get(source)
                        if hasattr(provider,'paused_until'):
                            provider.paused_until[str(account.account_id)]=time.monotonic()+exc.retry_after
                    log.warning('Activity detail sync failed for %s/%s: %s',gid,source,type(exc).__name__)
                    self.errors[(gid,uid)]=type(exc).__name__
                    # Keep a validated old snapshot; never interpret an error as an unrating.
                    if account is None:continue
                    state=await self.app.storage.get_activity_detail_state(gid,uid,source,account.account_id)
                    state['error']=type(exc).__name__
                    await self.app.storage.save_activity_detail_state(gid,uid,source,account.account_id,state)
                self.contexts[(gid,uid)]={'source':source,'account':account.account_id,'state':state}
                part={'guild':gid,'user':uid,'source':source,'account':str(account.account_id)}
                self.contexts[(gid,uid)]['before_watches']=await self.watches(part)

    async def valid(self,part):
        ctx=self.contexts.get((str(part['guild']),str(part['user'])))
        if not ctx or ctx['source']!=part['source'] or str(ctx['account'])!=str(part['account']):return False
        if await self.app.storage.get_activity_provider(part['guild'],part['user'])!=part['source']:return False
        try:account=await self.app.provider_registry.get(part['source']).link(part['user'])
        except ValueError:return False
        return str(account.account_id)==str(part['account'])

    async def watches(self,part):
        if part['source']!='simkl':
            return await self.app.storage.get_provider_plays(part['user'],provider=part['source'])
        stats=await self.app.storage.get_statistics(part['guild'],part['user'])
        rows=[]
        for row in (stats.get('watch_events') or {}).values():
            if 'episode' not in row.get('media_type',''):continue
            pieces=str(row.get('item_key','')).split(':')
            if len(pieces)==5 and pieces[0]=='series':
                try:rows.append({**row,'season':int(pieces[-2]),'episode':int(pieces[-1])})
                except ValueError:continue
        return rows

    async def catalog(self,meta,rows=()):
        sid=meta.get('ids',{}).get('tmdb')
        if not sid:return None
        key=str(sid)
        async with self.catalog_lock:
            cached=self.catalogs.get(key)
            fresh=bool(cached and time.monotonic()-cached[0]<3600)
            if fresh:
                series,seasons=cached[1]
            else:
                if cached:self.app.tmdb._series_cache.pop(int(sid),None)
                series=await self.app.tmdb._get_series_details(sid) or {}
                seasons=dict(cached[1][1]) if cached else {}
            summaries={row['season_number']:row for row in series.get('seasons') or []
                       if isinstance(row.get('season_number'),int) and row['season_number']>0}
            current=meta.get('source_season') or meta.get('season')
            if current not in summaries:current=meta.get('season')
            if current is None and summaries:current=max(summaries)
            required={current} if current in summaries else set()
            known={(row.get('source_season') or row.get('season'),row.get('source_episode') or row.get('episode'))
                   for row in rows if ids_match(row.get('ids') or {},meta['ids'])}
            total=sum(max(0,row.get('episode_count') or 0) for row in summaries.values())
            last=series.get('last_episode_to_air') or {}
            last_id=(meta.get('episode_ids') or {}).get('tmdb')
            near_end=(len(known)+len(meta.get('episodes') or [])>=max(1,total*.8)
                      or (last_id and str(last_id)==str(last.get('id'))))
            if near_end or meta.get('status')=='completed':required.update(summaries)
            if not fresh:
                airing=last.get('season_number')
                for number in required:
                    previous=seasons.get(number) or {}
                    if number==airing or len(previous.get('episodes') or [])!=(summaries[number].get('episode_count') or 0):
                        seasons.pop(number,None)
                        self.app.tmdb._season_cache.pop((int(sid),number),None)
            semaphore=asyncio.Semaphore(5)
            async def load(number):
                async with semaphore:
                    return number,await self.app.tmdb._get_season_details(sid,number) or {}
            needed=[number for number in required if number not in seasons]
            for number,data in await asyncio.gather(*(load(number) for number in needed)):
                seasons[number]=data
            value=(series,seasons)
            self.catalogs[key]=(time.monotonic() if not fresh else cached[0],value)
            if len(self.catalogs)>500:self.catalogs.pop(next(iter(self.catalogs)))
            return value

    async def milestone(self,part,*,additional=False,include_sent=False):
        meta=part['meta']
        if meta.get('movie') or (meta.get('season') is None and meta.get('status') not in {'completed','caught_up'}):return None
        ctx=self.contexts.get((part['guild'],part['user'])) or {}
        rows=ctx.get('before_watches') if include_sent else None
        if rows is None:rows=await self.watches(part)
        catalog=await self.catalog(meta,rows)
        if not catalog:return None
        series,seasons=catalog
        if meta.get('season') is None:
            if not seasons:return None
            meta={**meta,'season':max(seasons)}
        catalog_points={(number,row.get('episode_number')) for number,data in seasons.items()
                        for row in data.get('episodes') or []}
        by_id={str(row['id']):(number,row.get('episode_number')) for number,data in seasons.items()
               for row in data.get('episodes') or [] if row.get('id')}
        def point(row):
            eid=(row.get('episode_ids') or {}).get('tmdb')
            if eid and str(eid) in by_id:return by_id[str(eid)]
            for keys in (('source_season','source_episode'),('season','episode')):
                try:value=(int(row[keys[0]]),int(row[keys[1]]))
                except (TypeError,ValueError,KeyError):continue
                if value in catalog_points:return value
            return None
        points=set()
        for row in rows:
            if ids_match(row.get('ids') or {},meta['ids']):
                value=point(row)
                if value:points.add(value)
        if include_sent:
            for record in self.sent:
                for other in record['parts']:
                    if (other['guild'],other['user'],other['source'],other['account'])!=(part['guild'],part['user'],part['source'],part['account']):continue
                    if not ids_match(other['meta']['ids'],meta['ids']) or other['meta'].get('movie'):continue
                    seen_meta=other['meta']
                    sources=seen_meta.get('source_episodes') or seen_meta.get('episodes') or []
                    for index,number in enumerate(seen_meta.get('episodes') or []):
                        value=point({'season':seen_meta['season'],'episode':number,
                                     'source_season':seen_meta.get('source_season',seen_meta['season']),
                                     'source_episode':sources[index] if index<len(sources) else number})
                        if value:points.add(value)
        raw_numbers=meta.get('source_episodes') or meta.get('episodes') or []
        added=[]
        for index,number in enumerate(meta.get('episodes') or []):
            value=point({'season':meta['season'],'episode':number,
                         'source_season':meta.get('source_season',meta['season']),
                         'source_episode':raw_numbers[index] if index<len(raw_numbers) else number,
                         'episode_ids':meta.get('episode_ids') if len(meta.get('episodes') or [])==1 else {}})
            if value:added.append(value)
        if not additional and meta.get('new_watch') and not meta.get('rewatched') and part['source']=='simkl':
            points.difference_update(added)
        if additional:points.update(added)
        raw_season=added[-1][0] if added else meta.get('source_season') or meta['season']
        # Never announce a season in a different numbering namespace as the display season.
        return outcome(series,seasons,points,raw_season,display_season=meta['season'])

    def participant(self,guild,user,source,meta):
        ctx=self.contexts.get((str(guild),str(user)))
        if not ctx or ctx['source']!=source:return None
        return {'guild':str(guild),'user':str(user),'source':source,'account':str(ctx['account']),
                'meta':copy.deepcopy(meta),'transition':False,'lines':[]}

    async def prepare(self,part):
        if not await self.valid(part):return part
        before=await self.milestone(part,include_sent=True)
        after=await self.milestone(part,additional=True,include_sent=True)
        # Silent imports and already-completed seasons cannot create new milestones.
        part['transition']=bool(after and (not before or after!=before))
        part['milestone']=after if part['transition'] else None
        return part

    async def render(self,base,parts,*,refresh=False):
        embed=base.copy();extras=[]
        grouped=len(parts)>1
        if len(parts)==1 and parts[0]['meta'].get('status')=='completed' and not parts[0]['meta'].get('movie') and await self.valid(parts[0]):
            try:current=await self.milestone(parts[0])
            except Exception:current=None
            parts[0]['rating_scope']=current
            verb=('Completed' if current and current['kind']=='series' else
                  f"Finished Season {current['season']} of" if current and current['kind']=='season' else
                  'Caught up with' if current else 'Marked as watched')
            lines=(embed.description or '').splitlines()
            if lines:lines[0]=lines[0].replace('Completed',verb,1)
            embed.description='\n'.join(lines)
        for part in parts[:5]:
            if await self.valid(part):
                ctx=self.contexts[(part['guild'],part['user'])]
                prefs=await self.app.storage.get_embed_preferences(part['guild'],part['user'])
                milestone=part.get('milestone')
                if refresh and part.get('transition'):
                    milestone=await self.milestone(part)
                    part['milestone']=milestone
                lines=[]
                score=rating_for(ctx['state'].get('ratings') or [],part['meta'],milestone or part.get('rating_scope')) if prefs.get('show_personal',True) else None
                if score:
                    lines.append(('💬 <@'+part['user']+'> · ' if grouped else '💬 ')+score)
                if milestone:
                    lines.append((f"<@{part['user']}> · " if grouped else '')+milestone['text'])
                part['lines']=lines
            extras.extend(part.get('lines') or [])
        # Ratings precede milestones, and the Started line stays last.
        lines=(embed.description or '').splitlines()
        started=[line for line in lines if line.startswith('🆕')]
        lines=[line for line in lines if not line.startswith('🆕')]
        while lines and not lines[-1].strip():lines.pop()
        for extra in extras:
            proposed='\n'.join(lines+['',extra]+(['']+started if started else []))
            if len(proposed)<=4096 and len(embed)-len(embed.description or '')+len(proposed)<=6000:
                lines.extend(['',extra])
        if started:lines.extend(['']+started)
        embed.description='\n'.join(lines)
        return embed

    async def send(self,sender,channel,base,parts):
        parts=[part for part in parts if part]
        if not parts:return await sender(channel,base,'activity')
        for part in parts:
            try:await self.prepare(part)
            except Exception:
                log.exception('Could not resolve activity completion; leaving base watch intact.')
        try:embed=await self.render(base,parts)
        except Exception:
            log.exception('Activity detail rendering failed; posting the base activity.')
            embed=base.copy()
        token=current_post.set({'base':base.to_dict(),'parts':parts,'rendered':embed.to_dict()})
        try:return await sender(channel,embed,'activity')
        finally:current_post.reset(token)

    async def single(self,sender,channel,embed,guild,user,source,meta):
        part=self.participant(guild,user,source,meta)
        if part and meta.get('status')=='completed':
            # Native rollups arriving after the final watch must not duplicate its milestone.
            recent=await self.app.storage.get_activity_messages(guild)
            for previous in reversed(recent+self.sent):
                for other in previous['parts']:
                    if (other['guild'],other['user'],other['source'],other['account'])==(part['guild'],part['user'],part['source'],part['account']) and ids_match(other['meta']['ids'],meta['ids']) and not other['meta'].get('status') and not other['meta'].get('movie') and other.get('milestone'):
                        if not await self.milestone(part):continue
                        return True
        return await self.send(sender,channel,embed,[part])

    async def remember(self,message):
        post=current_post.get()
        if not post or not getattr(message,'id',None):return
        record=copy.deepcopy(post)
        record.update(message=str(message.id),channel=str(message.channel.id),
                      posted_at=datetime.now(timezone.utc).isoformat())
        await self.app.storage.save_activity_message(record)
        self.sent.append(record)

    async def finish(self,guild_id=None):
        records=await self.app.storage.get_activity_messages(guild_id)
        # Only the newest related activity for each account/title is eligible for a late edit.
        latest={}
        for record in records:
            for part in record['parts']:
                identity=(part['guild'],part['user'],part['source'],part['account'],
                          'movie' if part['meta'].get('movie') else 'show',json_ids(part['meta']['ids']))
                latest[identity]=record['message']
        for record in records:
            if not any([await self.valid(part) for part in record['parts']]):continue
            parts=copy.deepcopy(record['parts'])
            for part in parts:
                identity=(part['guild'],part['user'],part['source'],part['account'],
                          'movie' if part['meta'].get('movie') else 'show',json_ids(part['meta']['ids']))
                if latest[identity]!=record['message']:
                    # Freeze older related posts at their original snapshot.
                    part['source']='snapshot:'+part['source']
            try:
                base=discord.Embed.from_dict(record['base'])
                rendered=await self.render(base,parts,refresh=True)
                if rendered.to_dict()==record['rendered']:continue
                channel=self.app.bot.get_channel(int(record['channel']))
                if channel is None:channel=await self.app.bot.fetch_channel(int(record['channel']))
                message=channel.get_partial_message(int(record['message']))
                await message.edit(embed=rendered,allowed_mentions=discord.AllowedMentions.none())
                # Retain true source IDs; only the computed lines/milestone changed.
                for original,updated in zip(record['parts'],parts):
                    original['lines']=updated.get('lines',[]);original['milestone']=updated.get('milestone')
                record['rendered']=rendered.to_dict()
                await self.app.storage.save_activity_message(record)
            except (discord.NotFound,discord.Forbidden):
                await self.app.storage.delete_activity_message(record['message'])
            except Exception:
                log.warning('Activity detail edit pending retry for message %s.',record['message'],exc_info=True)


def json_ids(ids):
    # Prefer a stable provider-native entry when present, otherwise a shared anchor.
    return next(((key,str(ids[key])) for key in ('simkl','wetrakr','mdblist','tmdb','tvdb','imdb','mal') if ids.get(key)),('unknown',''))
