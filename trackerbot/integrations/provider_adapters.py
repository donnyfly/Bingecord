"""Built-in adapters expose account operations and normalized discovery titles.

The application context keeps transport clients and Discord delivery injectable.
Provider-specific sync retains its retry/checkpoint behavior behind poll().
"""
from trackerbot.core.providers import BUILTIN_TRACKERS, ProviderAccount, ProviderPage, WatchChange
from .wetrakr_client import page_rows
from .wetrakr_events import normalize_compact_play, normalize_journal_entry
from .wetrakr_sync import overlap
from trackerbot.core.tracker_mapping import WatchIdentity, match_reason, identity_from_play, normalized_ids


class BaseProvider:
    def __init__(self, app, manifest):
        self.app, self.manifest = app, manifest

    async def _user(self, account):
        if account.provider != self.manifest.name or not account.discord_user_id:
            raise ValueError('Provider account requires its Discord owner')
        user = await self.app.storage.get_user(account.discord_user_id)
        current = await self.link(account.discord_user_id)
        if current.account_id != account.account_id:
            raise ValueError('Linked account changed during provider operation')
        return user

    async def authorize(self, interaction):
        await getattr(self.app, self.manifest.name + '_link')(interaction)

    async def unlink(self, interaction):
        await getattr(self.app, self.manifest.name + '_unlink')(interaction)


class SimklProvider(BaseProvider):
    def __init__(self, app):
        super().__init__(app, BUILTIN_TRACKERS[0])

    async def link(self, discord_user_id):
        user = await self.app.storage.get_user(discord_user_id)
        if not user or not user.get('simkl_token'):
            raise ValueError('Link SIMKL first')
        return ProviderAccount('simkl', str(user.get('simkl_account_id') or 'unknown:' + str(discord_user_id)),
                               str(user.get('simkl_username') or 'SIMKL user'), str(discord_user_id))

    async def refresh_auth(self, account):
        await self.app.valid_token(account.discord_user_id, await self._user(account))

    async def poll(self, guild_id=None, *, manual=False):
        return await self.app.poll_all(guild_id, force_reconcile=manual, ignore_failure_threshold=manual)

    def profile_url(self, account):
        return None if account.account_id.startswith('unknown:') else self.app.simkl_profile_url(account.account_id)

    def title_url(self, media_type, title_ids):
        sid = title_ids.get('simkl')
        return self.app.simkl_title_url(media_type, sid, title_ids.get('slug')) if sid else None

    async def resolve_title_url(self, media_type, title_ids):
        return self.title_url(media_type, title_ids) or (await self.app.simkl.resolve_title_url(
            title_ids['tmdb'], 'movie' if media_type == 'movies' else 'tv') if title_ids.get('tmdb') else None)

    async def title(self, media_type, title_ids):
        # Metadata enrichment stays in the shared TMDB service.
        tmdb_id = title_ids.get('tmdb')
        if not tmdb_id:
            return {}
        return (await self.app.tmdb.get_movie_details(tmdb_id) if media_type == 'movies'
                else await self.app.tmdb._get_series_details(tmdb_id)) or {}

    async def _catalog(self, account):
        user = await self._user(account)
        token = await self.app.valid_token(account.discord_user_id, user)
        result = []
        for media_type in self.app.MEDIA_TYPES:
            items, token = await self.app.cached_simkl_items(account.discord_user_id, user, token,
                                                            media_type, timeout=self.app.HISTORY_FETCH_TIMEOUT_SECONDS)
            result.extend((media_type, item) for item in items or [])
        return result

    async def watching(self, account):
        user = await self._user(account)
        token = await self.app.valid_token(account.discord_user_id, user)
        items, _ = await self.app._currently_watching_items(account.discord_user_id, user, token, self.app.MEDIA_TYPES)
        return [{**item, 'anime': item.get('anime', item['media_type'] == 'anime' or bool(item['ids'].get('mal'))),
                 'url': self.title_url(item['media_type'], item['ids'])} for item in items]

    async def planning(self, account):
        result = []
        for scope, item in await self._catalog(account):
            if item.get('status') != 'plantowatch':
                continue
            anime_movie = scope == 'anime' and await self.app.is_anime_movie_item(item)
            media_type = 'movies' if anime_movie else scope
            media = item.get('movie') or item.get('show') or {}
            normalized_item = {**item, 'movie': media} if anime_movie else item
            title, episode_count, ids = await self.app.random_picker_media_details(normalized_item, media_type if anime_movie else scope)
            result.append({'title': title, 'ids': ids, 'media_type': media_type,
                           'anime': scope == 'anime' or bool(ids.get('mal')), 'episode_count': episode_count,
                           'added_at': self.app.random_picker_added_at(item),
                           'poster': self.app.simkl_poster_url(media.get('poster')),
                           'genres': media.get('genres') or item.get('genres') or [],
                           'url': self.title_url(scope, ids), 'native_item': normalized_item, 'native_scope': media_type if anime_movie else scope})
        return result

    async def recommendation_sources(self, account, media_filter):
        user = await self._user(account)
        token = await self.app.valid_token(account.discord_user_id, user)
        sources, excluded, _ = await self.app._recommendation_sources(account.discord_user_id, user, token, media_filter)
        return sources, excluded

    async def history(self, account, cursor=None):
        result = {}
        for scope, item in await self._catalog(account):
            is_movie = scope == 'movies' or (scope == 'anime' and await self.app.is_anime_movie_item(item))
            if is_movie:
                media = item.get('movie') or item.get('show') or {}
                ids, stamp = media.get('ids') or {}, item.get('last_watched_at')
                sid = ids.get('simkl')
                if sid is None or not stamp:
                    continue
                event_id = f"movie:{sid}:{stamp}"
                result[event_id] = WatchChange('simkl', account.account_id, event_id, event_id, 'added',
                                               'movie', stamp, ids, title=media.get('title') or 'Untitled')
            else:
                for episode in self.app.iter_show_episodes(scope, [item]):
                    stamp = episode.get('watched_raw')
                    if not stamp:
                        continue
                    event_id = f"episode:{scope}:{episode['simkl_id']}:{episode['season_num']}:{episode['episode_number']}:{stamp}"
                    result[event_id] = WatchChange('simkl', account.account_id, event_id, event_id, 'added', 'episode',
                        stamp, episode.get('ids') or {}, season=episode['season_num'], episode=episode['episode_number'],
                        title=episode.get('show_title') or 'Untitled')
        page = ProviderPage(tuple(result.values()), None)
        page.validate(account)
        return page

    async def changes(self, account, cursor):
        page = await self.history(account)
        changes = [c for c in page.changes if not cursor or c.watched_at > cursor]
        progression = await self.app.storage.get_progression(account.discord_user_id)
        current = [WatchIdentity(c.media_type, c.title, c.watched_at, c.season, c.episode, c.title_ids)
                   for c in page.changes]
        for row in progression.get('occurrence_ledger', {}).values():
            for ref, observation in row['observations'].items():
                if observation['provider'] != 'simkl' or not observation.get('identity'):
                    continue
                identity = WatchIdentity(**observation['identity'])
                # Snapshot presence retains older rewatches of the same item.
                present = any(match_reason(identity, WatchIdentity(candidate.media_type, candidate.title,
                              identity.watched_at, candidate.season, candidate.episode, candidate.ids))
                              for candidate in current)
                if not present:
                    changes.append(WatchChange('simkl', account.account_id, ref, 'removed:' + ref,
                        'removed', identity.kind, identity.watched_at, dict(identity.ids),
                        season=identity.season, episode=identity.episode, title=identity.title))
        result = ProviderPage(tuple(changes), self.app.now_iso())
        result.validate(account)
        return result



class WeTrakrProvider(BaseProvider):
    def __init__(self, app):
        super().__init__(app, BUILTIN_TRACKERS[1])

    async def link(self, discord_user_id):
        user = await self.app.storage.get_user(discord_user_id)
        link = (user or {}).get('wetrakr')
        if not link:
            raise ValueError('Link WeTrakr first')
        return ProviderAccount('wetrakr', str(link['account_id']), str(link.get('username') or 'WeTrakr user'), str(discord_user_id))

    async def refresh_auth(self, account):
        await self._token(account)

    async def _token(self, account):
        await self._user(account)
        if not self.app.wetrakr_sync:
            raise ValueError('WeTrakr is not configured')
        return await self.app.wetrakr_sync.auth.access_token(account.discord_user_id)

    async def poll(self, guild_id=None, *, manual=False):
        return await self.app.poll_wetrakr_all(guild_id)

    def profile_url(self, account):
        return self.app.wetrakr_profile_url({'username': account.username})

    def title_url(self, media_type, title_ids):
        return self.app.wetrakr_title_url('movie' if media_type == 'movies' else 'show', title_ids.get('tmdb'))

    async def resolve_title_url(self, media_type, title_ids):
        return self.title_url(media_type, title_ids)

    async def title(self, media_type, title_ids):
        title_id = title_ids.get('wetrakr')
        if title_id is None:
            return {}
        return await self.app.wetrakr.title('movie' if media_type == 'movies' else 'show', title_id)

    async def _list(self, account, status):
        await self._user(account)
        rows = await self.app.wetrakr_tracking_rows(account.discord_user_id, status, ('shows', 'movies'))
        result = []
        metadata_cache = {}
        for target, row in rows:
            media = (row.get('movie') if target == 'movies' else row.get('show')) or row
            native_id = media.get('id')
            if native_id and (not media.get('title') or not media.get('genres')):
                cache_key = (target, str(native_id))
                if cache_key not in metadata_cache:
                    metadata_cache[cache_key] = await self.app.wetrakr.title('movie' if target == 'movies' else 'show', native_id)
                media = {**media, **metadata_cache[cache_key]}
            ids = media.get('ids') or {}
            anime = (await self.app.is_wetrakr_anime_movie(media, ids.get('tmdb')) if target == 'movies'
                     else await self.app.is_wetrakr_anime(media, ids.get('tmdb')))
            poster = media.get('poster_path')
            if poster and str(poster).startswith('/'):
                poster = 'https://image.tmdb.org/t/p/w500' + poster
            result.append({'media_type': 'movies' if target == 'movies' else 'anime' if anime else 'shows',
                           'anime': anime, 'title': media.get('title') or row.get('title') or 'Untitled',
                           'ids': ids, 'url': self.title_url(target, ids), 'poster': poster,
                           'genres': media.get('genres') or [], 'latest': None,
                           'added_at': row.get('added_at'), 'episode_count': media.get('episodes') if isinstance(media.get('episodes'), int) else None})
        return result

    async def watching(self, account):
        items = await self._list(account, 'watching')
        plays = await self.app.storage.get_wetrakr_plays(account.discord_user_id)
        for item in items:
            if item['media_type'] == 'movies':
                continue
            ids = normalized_ids(item['ids'])
            latest = None
            for play in plays:
                identity = identity_from_play(play)
                if not identity or identity.kind != 'episode':
                    continue
                shared = set(ids) & set(normalized_ids(identity.ids))
                if not shared or any(ids[k] != normalized_ids(identity.ids)[k] for k in shared):
                    continue
                stamp = self.app.parse_iso(identity.watched_at)
                if latest is None or stamp > latest[0]:
                    latest = (stamp, identity.season, identity.episode, None)
            item['latest'] = latest
        return items

    async def planning(self, account):
        return await self._list(account, 'planning')

    async def recommendation_sources(self, account, media_filter):
        await self._user(account)
        sources, excluded = [], set()
        for play in await self.app.storage.get_wetrakr_plays(account.discord_user_id):
            media_type = play.get('media_type', '')
            kind = 'movie' if 'movie' in media_type else 'tv'
            anime = media_type.startswith('anime')
            try:
                tmdb_id = int((play.get('ids') or {}).get('tmdb'))
            except (ValueError, TypeError):
                continue
            excluded.add((kind, tmdb_id))
            if not matches_filter({'media_type': 'movies' if kind == 'movie' else 'shows', 'anime': anime}, media_filter):
                continue
            sources.append({'kind': kind, 'tmdb_id': tmdb_id, 'title': play.get('title') or 'Untitled',
                            'watched_at': play.get('watched_at') or '', 'anime': anime,
                            'media_type': media_type, 'rating': None, 'genres': play.get('genres') or []})
        # Planning, dropped and paused titles must not be recommended as fresh.
        for status in ('watching', 'planning', 'dropped', 'paused', 'waiting'):
            for target, row in await self.app.wetrakr_tracking_rows(account.discord_user_id, status, ('shows', 'movies')):
                media = (row.get('movie') if target == 'movies' else row.get('show')) or row
                try:
                    excluded.add(('movie' if target == 'movies' else 'tv', int(media['ids']['tmdb'])))
                except (KeyError, ValueError, TypeError):
                    continue
        return self.app.select_sources(sources), excluded

    async def resolve_play(self, play, cache=None):
        cache = cache if cache is not None else {}
        result = dict(play)
        kind = result.get('media_type')
        title_id = result.get('wetrakr_id') if kind == 'movie' else result.get('show_id')
        if kind not in {'movie', 'episode'} or not title_id:
            return result
        cache_key = (kind, str(title_id))
        if cache_key not in cache:
            metadata = await self.app.wetrakr.title('movie' if kind == 'movie' else 'show', title_id)
            anime = (await self.app.is_wetrakr_anime(metadata, (metadata.get('ids') or {}).get('tmdb'))
                     if kind == 'episode' else await self.app.is_wetrakr_anime_movie(metadata, (metadata.get('ids') or {}).get('tmdb')))
            cache[cache_key] = metadata, anime
        metadata, anime = cache[cache_key]
        result.update(title=metadata.get('title') or result.get('title') or 'Untitled',
                      ids=metadata.get('ids') or result.get('ids') or {}, genres=metadata.get('genres') or [])
        result['media_type'] = ('anime_movie' if kind == 'movie' else 'anime_episode') if anime else kind
        if anime and kind == 'episode':
            ids = result['ids']
            tvdb_id = ids.get('tvdb') or ids.get('thetvdb')
            if not tvdb_id and ids.get('tmdb'):
                tvdb_id = await self.app.tmdb.get_tvdb_id_for_tmdb(ids['tmdb'])
            mapped = await self.app.tmdb.map_anime_calendar_episode(tvdb_id, result.get('season'), result.get('episode')) if tvdb_id else None
            if mapped:
                result['season'], result['episode'] = mapped
        result['item_key'] = (f'wetrakr:movie:{title_id}' if kind == 'movie' else
                              f"wetrakr:episode:{title_id}:{result.get('season')}:{result.get('episode')}")
        return result

    async def _change(self, account, play, cache):
        if play.get('action') != 'removed':
            play = await self.resolve_play(play, cache)
        kind = 'movie' if 'movie' in play.get('media_type', '') else 'episode'
        return WatchChange('wetrakr', account.account_id, play['source_event_id'],
            play.get('change_id') or play['source_event_id'], play.get('action', 'added'), kind,
            play.get('watched_at'), play.get('ids') or {}, show_ids=play.get('show_ids'),
            season=play.get('season'), episode=play.get('episode'), title=play.get('title') or '',
            observed_at=play.get('action_at'))

    async def history(self, account, cursor=None):
        token, changes, cache = await self._token(account), [], {}
        for target in ('movies', 'episodes'):
            async for page in self.app.wetrakr.compact_history(token, target):
                rows = page_rows(page)
                for row in rows:
                    play = normalize_compact_play(row)
                    if play:
                        changes.append(await self._change(account, play, cache))
        result = ProviderPage(tuple(changes), None)
        result.validate(account)
        return result

    async def changes(self, account, cursor):
        token, changes, cache = await self._token(account), [], {}
        rows = await self.app.wetrakr.journal(token, overlap(cursor), category='watched')
        for row in rows:
            play = normalize_journal_entry(row)
            if play and play.get('source_event_id'):
                changes.append(await self._change(account, play, cache))
        result = ProviderPage(tuple(changes), max((row['action_at'] for row in rows), default=cursor))
        result.validate(account)
        return result


def matches_filter(item, media_filter):
    if media_filter == 'all':
        return True
    if media_filter == 'anime':
        return bool(item.get('anime'))
    if media_filter == 'shows':
        return item['media_type'] != 'movies' and not item.get('anime')
    return item['media_type'] == 'movies'
