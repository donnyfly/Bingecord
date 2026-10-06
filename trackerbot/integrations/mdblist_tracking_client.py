"""MDBList account transport, separate from the shared ratings enrichment key.

Verified against the 2026-10-05 OpenAPI schema and developer device-flow
implementation. No watch writes or progression mutations are performed here.
"""
from collections import Counter
from urllib.parse import quote

import aiohttp

BASE_URL = 'https://api.mdblist.com'
DEVICE_GRANT = 'urn:ietf:params:oauth:grant-type:device_code'


class MDBListTrackingError(Exception):
    def __init__(self, status, code, retry_after=None):
        self.status, self.code, self.retry_after = status, code, retry_after
        # API bodies may echo credentials. Never expose them in exceptions.
        super().__init__(f'MDBList {status} {code}')


class MDBListTrackingClient:
    def __init__(self, client_id, client_secret=None, session=None):
        if not client_id:
            raise ValueError('An MDBList OAuth client ID is required')
        self.client_id, self.client_secret = client_id, client_secret
        self._session, self._owns_session = session, session is None
        self.request_counts = Counter()

    async def close(self):
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _request(self, method, path, token=None, *, params=None, form=None, allow_list=False):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        headers={'Accept':'application/json','User-Agent':'WatchRelayBot/experimental'}
        if token:
            headers['Authorization']=f'Bearer {token}'
        self.request_counts[path] += 1
        async with self._session.request(method, BASE_URL+path, headers=headers,
                                        params=params, data=form, allow_redirects=False) as response:
            if response.status == 429:
                try:
                    delay=max(1,int(response.headers.get('Retry-After','60')))
                except (TypeError, ValueError):
                    delay=60
                raise MDBListTrackingError(429,'RATE_LIMITED',delay)
            try:
                data=await response.json(content_type=None)
            except (ValueError,aiohttp.ContentTypeError):
                raise MDBListTrackingError(response.status,'INVALID_RESPONSE') from None
            if allow_list and response.status<300 and isinstance(data,list):
                if any(not isinstance(row,dict) for row in data):raise MDBListTrackingError(200,'INVALID_RESPONSE')
                return data
            if not isinstance(data,dict):
                raise MDBListTrackingError(response.status,'INVALID_RESPONSE')
            if data.get('requires_full_sync') is True:
                raise MDBListTrackingError(response.status,'FULL_SYNC_REQUIRED')
            if response.status >= 300 or data.get('error'):
                known={'authorization_pending','slow_down','expired_token','access_denied','invalid_grant','invalid_client','invalid_token'}
                error=data.get('error')
                code=error if isinstance(error,str) and error in known else 'HTTP_ERROR'
                raise MDBListTrackingError(response.status,code)
            return data

    async def device_code(self):
        return await self._request('POST','/oauth/device-authorization/',
                                   form={'client_id':self.client_id,'scope':'read'})

    async def device_token(self, device_code):
        return await self._request('POST','/oauth/token/',form={
            'client_id':self.client_id,'device_code':device_code,'grant_type':DEVICE_GRANT})

    async def refresh_token(self, refresh_token):
        form={'client_id':self.client_id,'refresh_token':refresh_token,'grant_type':'refresh_token'}
        if self.client_secret:
            form['client_secret']=self.client_secret
        return await self._request('POST','/oauth/token/',form=form)

    async def account(self, token):
        data=await self._request('GET','/user',token)
        if not data.get('user_id') or not data.get('username'):
            raise MDBListTrackingError(200,'INVALID_RESPONSE')
        return data

    async def last_activities(self, token):
        data=await self._request('GET','/sync/last_activities',token)
        if not data.get('server_time'):
            raise MDBListTrackingError(200,'INVALID_RESPONSE')
        return data

    async def _cursor_pages(self, token, path, params, buckets):
        seen=set()
        while True:
            data=await self._request('GET',path,token,params=params)
            if data.get('requires_full_sync') is True:
                raise MDBListTrackingError(200,'FULL_SYNC_REQUIRED')
            if not any(isinstance(data.get(key),list) for key in buckets):
                raise MDBListTrackingError(200,'INVALID_RESPONSE')
            for key in buckets:
                if key in data and (not isinstance(data[key],list) or any(not isinstance(row,dict) for row in data[key])):
                    raise MDBListTrackingError(200,'INVALID_RESPONSE')
            pagination=data.get('pagination')
            if not isinstance(pagination,dict):
                raise MDBListTrackingError(200,'INVALID_RESPONSE')
            cursor=pagination.get('next_cursor')
            if cursor is not None and (not isinstance(cursor,str) or not cursor):
                raise MDBListTrackingError(200,'INVALID_RESPONSE')
            if pagination.get('has_more') is True and not cursor:
                raise MDBListTrackingError(200,'INVALID_RESPONSE')
            if cursor and cursor in seen:
                raise MDBListTrackingError(200,'CURSOR_CYCLE')
            yield data
            if not cursor:
                return
            seen.add(cursor)
            params={key:value for key,value in params.items() if key!='since'}
            params['cursor']=cursor

    def history(self, token, *, media_type, limit=1000):
        if media_type not in {'movie','episode'}:
            raise ValueError('Individual history requires movie or episode')
        return self._cursor_pages(token,'/sync/watched',{'mediatype':media_type,
            'plays':'all','limit':min(max(1,limit),1000)},('movies','episodes'))

    def journal(self, token, since, *, limit=1000):
        if not since:
            raise ValueError('A journal timestamp is required')
        return self._cursor_pages(token,'/sync/journal',{'since':since,
            'statuses':'partial','limit':min(max(1,limit),1000)},('journal',))

    def planning(self, token, *, limit=1000):
        return self._cursor_pages(token,'/watchlist/items',{'limit':min(max(1,limit),1000),
            'append_to_response':'genres,poster,description'},('movies','shows'))

    async def item_plays(self, token, media_type, provider, provider_id):
        if media_type not in {'movie','show','episode'} or provider not in {'tmdb','mdblist'}:
            raise ValueError('Unsupported play-history identity')
        if media_type=='episode' and provider!='tmdb':
            raise ValueError('Episode play-history lookup requires its own TMDB ID')
        data=await self._request('GET',f'/sync/history/{media_type}/{provider}/{quote(str(provider_id),safe="")}',token)
        if not isinstance(data.get('plays'),list) or any(not isinstance(row,dict) for row in data['plays']):
            raise MDBListTrackingError(200,'INVALID_RESPONSE')
        # A capped history cannot establish that older plays were removed.
        if data.get('truncated') is not False:
            raise MDBListTrackingError(200,'INCOMPLETE_PLAY_HISTORY')
        return data

    def dropped(self, token):
        return self._cursor_pages(token,'/sync/dropped',{'limit':1000},('shows',))

    async def watching(self, token):
        offset=0
        while True:
            data=await self._request('GET','/upnext',token,params={'limit':100,'offset':offset,'air_date_format':'instant'})
            if not isinstance(data.get('items'),list) or not isinstance(data.get('has_more'),bool):
                raise MDBListTrackingError(200,'INVALID_RESPONSE')
            if any(not isinstance(row,dict) for row in data['items']) or data['has_more'] and not data['items']:
                raise MDBListTrackingError(200,'INVALID_RESPONSE')
            yield data
            if not data['has_more']:
                return
            offset+=len(data['items'])

    async def media(self, token, media_type, tmdb_id):
        if media_type not in {'movie','show'}:
            raise ValueError('Unsupported media type')
        return await self._request('GET',f'/tmdb/{media_type}/{int(tmdb_id)}/',token)

    async def paused(self,token):
        rows=await self._request('GET','/sync/playback',token,allow_list=True)
        if not isinstance(rows,list):raise MDBListTrackingError(200,'INVALID_RESPONSE')
        result={'movies':[],'shows':[]}
        for row in rows:
            if not row.get('paused_at'):continue
            kind=row.get('type')
            if kind not in {'movie','episode'}:raise MDBListTrackingError(200,'INVALID_RESPONSE')
            result['movies' if kind=='movie' else 'shows'].append(row)
        yield result
