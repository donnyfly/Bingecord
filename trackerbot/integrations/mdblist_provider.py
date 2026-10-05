"""Experimental MDBList provider: stable plays, complete snapshot reconciliation."""
import asyncio
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

import discord

from trackerbot.core.providers import BUILTIN_TRACKERS, ProviderAccount, ProviderPage, WatchChange
from trackerbot.core.tracker_mapping import identity_from_play
from .provider_adapters import BaseProvider, matches_filter
from .mdblist_tracking_client import MDBListTrackingError


def normalize_play(row, kind):
    """Preserve play identity; refuse aggregate or undated rows as a full snapshot."""
    episode=row.get('episode') if isinstance(row.get('episode'),dict) else {}
    media=row.get('movie' if kind=='movie' else 'show') or (episode.get('show') if kind=='episode' else None) or {}
    play_id=row.get('play_id')
    stamp=row.get('watched_at')
    if not play_id or not stamp or row.get('watched_at_unknown'):
        raise MDBListTrackingError(200,'UNSUPPORTED_HISTORY_SHAPE')
    try:
        date=datetime.fromisoformat(stamp.replace('Z','+00:00'))
        if date.tzinfo is None: raise ValueError
    except (ValueError,AttributeError,TypeError):
        raise MDBListTrackingError(200,'INVALID_WATCH_TIME') from None
    ids=media.get('ids') or row.get('show_ids' if kind=='episode' else 'ids') or {}
    # Some responses embed a parent show inside the episode; root episode IDs
    # must never be treated as series IDs. Flat explicitly scoped IDs are safe.
    if not ids:
        prefix='show_' if kind=='episode' else ''
        ids={name:row[prefix+name+'_id'] for name in ('tmdb','tvdb','imdb','mdblist')
             if row.get(prefix+name+'_id') is not None}
    if not ids or not isinstance(ids,dict):
        raise MDBListTrackingError(200,'MISSING_TITLE_IDS')
    result={'source':'mdblist','source_event_id':str(play_id),'media_type':kind,
            'watched_at':stamp,'title':media.get('title') or row.get('title') or '',
            'ids':ids,'show_ids':ids if kind=='episode' else {},
            'show_id':ids.get('mdblist') or ids.get('tmdb'),'genres':media.get('genres') or []}
    if kind=='episode':
        season=next((v for v in (episode.get('season'),episode.get('season_number'),row.get('season_number'),row.get('season_num')) if v is not None),None)
        if season is None: season=row.get('season')
        number=episode.get('number') or episode.get('episode_number') or row.get('number') or row.get('episode_number') or row.get('episode_num')
        if number is None and isinstance(row.get('episode'),int): number=row['episode']
        try:
            result.update(season=int(season),episode=int(number),source_season=int(season),source_episode=int(number))
        except (TypeError,ValueError):
            raise MDBListTrackingError(200,'MISSING_EPISODE_COORDINATES') from None
        result['episode_ids']=episode.get('ids') or {}
        result['episode_title']=episode.get('title') or episode.get('name')
        result['air_date']=episode.get('air_date') or row.get('air_date')
    return result


class MDBListProvider(BaseProvider):
    def __init__(self, app):
        super().__init__(app,BUILTIN_TRACKERS[2])
        self.lock=asyncio.Lock()
        self.paused_until={}
        self.metadata={}
        self.native_metadata={}
        self.native_inflight={}

    async def link(self, discord_user_id):
        user=await self.app.storage.get_user(discord_user_id)
        link=(user or {}).get('mdblist')
        if not link: raise ValueError('Link MDBList first')
        return ProviderAccount('mdblist',str(link['account_id']),link['username'],str(discord_user_id))

    async def _token(self, account):
        await self._user(account)
        if not self.app.mdblist_tracking: raise ValueError('MDBList OAuth is not configured')
        return await self.app.mdblist_auth.access_token(account.discord_user_id)

    async def refresh_auth(self, account):
        await self._token(account)

    async def authorize(self, interaction):
        await self.app.mdblist_link(interaction)

    async def unlink(self, interaction):
        gid=self.app.guild_id(interaction)
        if not gid:
            await interaction.response.send_message('Use this command in a server.',ephemeral=True);return
        result=await self.app.storage.unlink_provider_account(gid,str(interaction.user.id),provider='mdblist')
        await interaction.response.send_message('MDBList unlinked from this server.' if result else 'MDBList is not linked here.',ephemeral=True)

    def profile_url(self, account):
        return f'https://mdblist.com/lists/{quote(account.username,safe="")}'

    def title_url(self, media_type, title_ids):
        native=title_ids.get('mdblist')
        return f'https://mdblist.com/{"movie" if media_type in {"movies","movie","anime_movie"} else "show"}/{quote(str(native),safe="")}' if native else None

    async def title(self, media_type, title_ids):
        if not title_ids.get('tmdb'): return {}
        kind='movie' if media_type in {'movie','movies','anime_movie'} else 'show'
        key=(kind,str(title_ids['tmdb']))
        cached=self.metadata.get(key)
        if cached and time.monotonic()-cached[0]<21600: return cached[1]
        data=(await self.app.tmdb.get_movie_details(title_ids['tmdb']) if kind=='movie'
              else await self.app.tmdb._get_series_details(title_ids['tmdb'])) or {}
        self.metadata[key]=(time.monotonic(),data)
        return data

    async def _native_media(self, account, movie, tmdb_id):
        await self._user(account)
        key=(account.account_id,movie,str(tmdb_id))
        cached=self.native_metadata.get(key)
        if cached and time.monotonic()-cached[0]<21600:return cached[1]
        if key in self.native_inflight:return await self.native_inflight[key]
        async def fetch():
            data=await self.app.mdblist_tracking.media(await self._token(account),'movie' if movie else 'show',tmdb_id)
            self.native_metadata[key]=(time.monotonic(),data)
            return data
        task=asyncio.create_task(fetch());self.native_inflight[key]=task
        try:return await task
        finally:self.native_inflight.pop(key,None)

    async def resolve_title_url(self, media_type, title_ids, account=None):
        direct=self.title_url(media_type,title_ids)
        if direct or not title_ids.get('tmdb'): return direct
        # Recommendations originate in TMDB. Resolve MDBList's native public ID
        # using this user's token, never another member's or the ratings app key.
        if account is None:return None
        data=await self._native_media(account,media_type=='movies',title_ids['tmdb'])
        return self.title_url(media_type,data.get('ids') or {})

    async def ratings(self, account, movie, ids):
        if not ids.get('tmdb'):return {}
        data=await self._native_media(account,movie,ids['tmdb'])
        result={}
        for row in data.get('ratings') or []:
            if row.get('source') not in {'imdb','myanimelist'}:continue
            try:result['mal' if row['source']=='myanimelist' else 'imdb']=float(row['value'])
            except (KeyError,TypeError,ValueError):pass
        return result

    async def resolve_play(self, play):
        result=dict(play)
        kind=play['media_type']
        data=await self.title('movies' if kind=='movie' else 'shows',play['ids'])
        metadata={**data,'ids':play['ids'],'genres':data.get('genres') or play.get('genres') or [],'title':data.get('title') or data.get('name') or play['title']}
        anime=bool(play['ids'].get('mal')) or (await self.app.is_wetrakr_anime_movie(metadata,play['ids'].get('tmdb')) if kind=='movie'
               else await self.app.is_wetrakr_anime(metadata,play['ids'].get('tmdb')))
        result.update(title=metadata['title'] or 'Untitled',genres=data.get('genres') or play.get('genres') or [])
        result['media_type']=('anime_movie' if kind=='movie' else 'anime_episode') if anime else kind
        if anime and kind=='episode':
            tvdb=play['ids'].get('tvdb') or await self.app.tmdb.get_tvdb_id_for_tmdb(play['ids'].get('tmdb'))
            if tvdb:
                # MDBList may return TMDB's absolute anime numbering. Match the
                # source episode by air date/title to the shared seasonal layout.
                air_date=play.get('air_date')
                if not air_date and play['ids'].get('tmdb'):
                    episode=await self.app.tmdb.get_episode_details(play['ids']['tmdb'],play['season'],play['episode'])
                    air_date=(episode or {}).get('air_date')
                mapped=await self.app.tmdb.map_anime_episode_to_tvmaze(tvdb,air_date=air_date,title=play.get('episode_title'))
                if not mapped:
                    mapped=await self.app.tmdb.map_anime_calendar_episode(tvdb,play['season'],play['episode'])
                if mapped: result['season'],result['episode']=mapped
        native=play['ids'].get('mdblist') or play['ids'].get('tmdb') or play['ids'].get('imdb')
        result['item_key']=f'mdblist:movie:{native}' if kind=='movie' else f'mdblist:episode:{native}:{result["season"]}:{result["episode"]}'
        return result

    async def _history(self, account, token):
        plays=[]
        for kind,bucket in (('movie','movies'),('episode','episodes')):
            async for page in self.app.mdblist_tracking.history(token,media_type=kind):
                if bucket not in page or not isinstance(page[bucket],list):
                    raise MDBListTrackingError(200,'UNSUPPORTED_HISTORY_SHAPE')
                for row in page[bucket]:
                    try:
                        normalized=normalize_play(row,kind)
                    except MDBListTrackingError:
                        # Field names only: no tokens, titles, dates or user values.
                        shape={key:sorted(value.keys()) if isinstance(value,dict) else type(value).__name__
                               for key,value in row.items()}
                        self.app.log.warning('MDBList %s history field structure: %s',kind,shape)
                        raise
                    plays.append(await self.resolve_play(normalized))
        seen=set()
        for play in plays:
            if play['source_event_id'] in seen: raise MDBListTrackingError(200,'DUPLICATE_PLAY_ID')
            seen.add(play['source_event_id'])
        return plays

    def _change(self, account, play, action='added'):
        return WatchChange('mdblist',account.account_id,play['source_event_id'],
            play['source_event_id']+':'+play['watched_at'],action,
            'movie' if 'movie' in play['media_type'] else 'episode',play['watched_at'],play['ids'],
            show_ids=play.get('show_ids'),season=play.get('season'),episode=play.get('episode'),title=play['title'])

    async def history(self, account, cursor=None):
        result=ProviderPage(tuple(self._change(account,p) for p in await self._history(account,await self._token(account))),None)
        result.validate(account);return result

    async def changes(self, account, cursor):
        # Journal is an invalidation feed. Complete play snapshots include
        # rewatches and timestamp edits that its latest-item row cannot express.
        token=await self._token(account)
        mark=(await self.app.mdblist_tracking.last_activities(token))["server_time"]
        live=await self._history(account,token)
        saved=await self.app.storage.get_provider_plays(account.discord_user_id,provider='mdblist')
        previous={p['source_event_id']:p for p in saved};current={p['source_event_id']:p for p in live}
        changes=[self._change(account,p,'updated' if key in previous else 'added') for key,p in current.items()
                 if key not in previous or previous[key]['watched_at']!=p['watched_at']]
        changes += [self._change(account,p,'removed') for key,p in previous.items() if key not in current]
        result=ProviderPage(tuple(changes),mark);result.validate(account);return result

    async def _lists(self, account, status, token=None):
        token=token or await self._token(account)
        stream=(self.app.mdblist_tracking.watching(token) if status=='watching' else
                self.app.mdblist_tracking.planning(token) if status=='planning' else self.app.mdblist_tracking.dropped(token))
        result=[]
        async for page in stream:
            buckets=(('shows',page['items']),) if status=='watching' else tuple((key,page.get(key,[])) for key in ('movies','shows'))
            for bucket,rows in buckets:
                for row in rows:
                    media=row.get('movie' if bucket=='movies' else 'show') or row
                    ids=media.get('ids') or {}
                    data=await self.title(bucket,ids)
                    metadata={**data,'title':data.get('title') or data.get('name') or media.get('title') or 'Untitled'}
                    anime=(await self.app.is_wetrakr_anime_movie(metadata,ids.get('tmdb')) if bucket=='movies' else
                           await self.app.is_wetrakr_anime(metadata,ids.get('tmdb')))
                    poster=data.get('poster_path') or media.get('poster')
                    if poster and str(poster).startswith('/'): poster='https://image.tmdb.org/t/p/w500'+poster
                    next_episode=row.get('next_episode') or {}
                    latest=None
                    if next_episode.get('number') and next_episode.get('season'):
                        latest=f'Next: S{next_episode["season"]}E{next_episode["number"]:02}'
                    result.append({'media_type':'movies' if bucket=='movies' else 'anime' if anime else 'shows',
                        'anime':anime,'title':metadata['title'],'ids':ids,'url':self.title_url(bucket,ids),
                        'poster':poster,'genres':data.get('genres') or [],'latest':latest,
                        'episode_count':data.get('number_of_episodes'),'added_at':row.get('added_at')})
        return result

    async def watching(self, account): return await self._lists(account,'watching')
    async def planning(self, account): return await self._lists(account,'planning')

    async def recommendation_sources(self, account, media_filter):
        await self._user(account)
        plays=await self.app.storage.get_provider_plays(account.discord_user_id,provider='mdblist')
        sources=[];excluded=set()
        for play in plays:
            tmdb=play['ids'].get('tmdb')
            if not tmdb: continue
            kind='movie' if 'movie' in play['media_type'] else 'tv'
            anime=play['media_type'].startswith('anime')
            excluded.add((kind,int(tmdb)))
            if matches_filter({'media_type':'movies' if kind=='movie' else 'shows','anime':anime},media_filter):
                sources.append({'kind':kind,'tmdb_id':int(tmdb),'title':play['title'],'watched_at':play['watched_at'],
                    'anime':anime,'media_type':'movies' if kind=='movie' else 'anime' if anime else 'shows',
                    'rating':None,'genres':play.get('genres') or []})
        for status in ('watching','planning','dropped'):
            for item in await self._lists(account,status):
                if item['ids'].get('tmdb'):
                    excluded.add(('movie' if item['media_type']=='movies' else 'tv',int(item['ids']['tmdb'])))
        return self.app.select_sources(sources),excluded

    async def poll(self, guild_id=None, *, manual=False):
        if not self.app.mdblist_tracking: return 0
        async with self.lock:
            posted=0
            for target in await self.app.storage.get_provider_targets('mdblist',guild_id,active_only=True):
                gid,uid=target['guild_id'],target['discord_user_id']
                link=target['user_data']['mdblist'];account_id=str(link['account_id'])
                health=target['guild_user_data']['mdblist_sync'].get('health') or {}
                if not manual and int(health.get('consecutive_failures',0))>=self.app.MAX_CONSECUTIVE_FAILURES:continue
                if self.paused_until.get(account_id,0)>time.monotonic():continue
                if not target.get('channel_id'): continue
                try:
                    posted+=await self._poll_target(target)
                except MDBListTrackingError as exc:
                    if exc.retry_after: self.paused_until[account_id]=time.monotonic()+exc.retry_after
                    await self.app.storage.save_provider_sync(gid,uid,account_id,provider='mdblist',health={
                        'error':exc.code,'consecutive_failures':int(health.get('consecutive_failures',0))+1,
                        'retry_at':(datetime.now(timezone.utc)+timedelta(seconds=exc.retry_after)).isoformat() if exc.retry_after else None})
                    self.app.log.warning('MDBList sync blocked user %s guild %s: %s',uid,gid,exc.code)
                except Exception:
                    self.app.log.exception('MDBList polling failed user %s guild %s.',uid,gid)
            return posted

    async def _poll_target(self, target):
        gid,uid=target['guild_id'],target['discord_user_id']
        account=await self.link(uid);token=await self._token(account)
        state=target['guild_user_data']['mdblist_sync']
        activities=await self.app.mdblist_tracking.last_activities(token)
        # Activity stamps are invalidations, never an assumed item event stream.
        signature={key:activities.get(key) for key in ('journal_at','watched_at','season_watched_at','episode_watched_at','watchlisted_at','dropped_at')}
        saved_signature=(state.get('status_snapshot') or {}).get('activities')
        if state['seeded'] and signature==saved_signature:
            await self.app.storage.save_provider_sync(gid,uid,account.account_id,provider='mdblist',health={})
            return 0
        captured=activities['server_time']
        if datetime.fromisoformat(captured.replace('Z','+00:00')).tzinfo is None:
            raise MDBListTrackingError(200,'INVALID_SERVER_TIME')
        if await self.app.storage.needs_watch_statistics_rebuild(gid,uid):
            simkl_user=target['user_data']
            if not simkl_user.get('simkl_token'):raise ValueError('Legacy statistics require their original history rebuild')
            simkl_token=await self.app.valid_token(uid,simkl_user)
            await self.app.reconcile_watch_progression(gid,uid,simkl_user,simkl_token,set(self.app.MEDIA_TYPES))
        plays=await self._history(account,token)
        planning=await self._lists(account,'planning',token)
        dropped=await self._lists(account,'dropped',token)
        statuses={status:{str(item['ids'].get('mdblist') or item['ids'].get('tmdb') or ''):item for item in items}
                  for status,items in (('planning',planning),('dropped',dropped))}
        if any('' in rows for rows in statuses.values()): raise MDBListTrackingError(200,'MISSING_TITLE_IDS')
        store=self.app.storage
        if not state['seeded']:
            await store.reconcile_provider_plays(gid,uid,plays,complete=True,account_id=account.account_id,provider='mdblist')
            await self.app.evaluate_achievements(gid,uid,notify_channel=None)
            await store.claim_prestige_notifications(uid)
            if not await store.save_provider_sync(gid,uid,account.account_id,seeded=True,checkpoint=captured,
                    status_snapshot={**statuses,'activities':signature},health={},provider='mdblist'):
                raise ValueError('MDBList selection changed during import')
            self.app.log.info('MDBList baseline user %s guild %s: %d plays; no historical activity.',uid,gid,len(plays))
            return 0
        channel=self.app.bot.get_channel(int(target['channel_id'])) or await self.app.bot.fetch_channel(int(target['channel_id']))
        member,name=await self.app.resolve_member(gid,uid)
        if not member:return 0
        before=await store.get_progression(uid)
        events=(target['guild_user_data'].get('statistics') or {}).get('watch_events') or {}
        async def rewards():
            await self.app.evaluate_achievements(gid,uid,notify_channel=channel)
            await self.app.notify_challenge_rewards(gid,uid,channel)
            after=await store.get_progression(uid)
            if await self.app.feature_enabled(gid,'progression'):
                for number in await store.claim_prestige_notifications(uid):
                    if not await self.app.send_prestige_notification(channel.send,f'<@{uid}>',number,after.get('lifetime_xp',0)):
                        await store.retry_prestige_notification(uid,number);break
            await self.app.notify_level_up(gid,uid,before,after,channel,source_label='MDBList')
        count=0
        fresh=[p for p in sorted(plays,key=lambda p:(str(p.get('show_id') or ''),p.get('season') or 0,p.get('episode') or 0,p['watched_at']))
               if f'mdblist:{account.account_id}/{p["source_event_id"]}' not in events]
        index=0
        while index<len(fresh):
            batch=[fresh[index]]
            while index+len(batch)<len(fresh) and 'episode' in batch[0]['media_type']:
                candidate=fresh[index+len(batch)];previous=batch[-1]
                if (candidate.get('show_id'),candidate.get('season')) != (previous.get('show_id'),previous.get('season')) or candidate.get('episode')!=previous.get('episode',0)+1:
                    break
                if abs((self.app.parse_iso(candidate['watched_at'])-self.app.parse_iso(previous['watched_at'])).total_seconds())>300:
                    break
                batch.append(candidate)
            if await store.get_activity_provider(gid,uid)!='mdblist':raise ValueError('MDBList source changed during delivery')
            if not await self.app.deliver_mdblist_play(channel,gid,uid,name,member,self,account,batch[0],batch[-1] if len(batch)>1 else None):
                await rewards();return count
            await store.reconcile_provider_plays(gid,uid,batch,account_id=account.account_id,provider='mdblist')
            count+=1;index+=len(batch)
        prior=state.get('status_snapshot') or {}
        for status,items in statuses.items():
            known=dict(prior.get(status) or {})
            for key,item in items.items():
                if key in known:continue
                if await store.get_activity_provider(gid,uid)!='mdblist':raise ValueError('MDBList source changed during status delivery')
                if not await self.app.deliver_mdblist_status(channel,gid,uid,name,member,self,account,status,item):
                    await rewards();return count
                known[key]=item
                prior[status]=known
                if not await store.save_provider_sync(gid,uid,account.account_id,status_snapshot=prior,provider='mdblist'):
                    raise ValueError('MDBList selection changed during status delivery')
                count+=1
        await store.reconcile_provider_plays(gid,uid,plays,complete=True,notify=True,account_id=account.account_id,provider='mdblist')
        await rewards()
        await store.refresh_watch_occurrences(uid)
        if not await store.save_provider_sync(gid,uid,account.account_id,checkpoint=captured,last_activity=captured,
                status_snapshot={**statuses,'activities':signature},health={},provider='mdblist'):
            raise ValueError('MDBList selection changed during sync')
        self.app.log.info('MDBList sync user %s guild %s: %d plays, %d activity items.',uid,gid,len(plays),count)
        return count
