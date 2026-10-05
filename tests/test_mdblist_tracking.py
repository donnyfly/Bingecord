import asyncio
from unittest.mock import AsyncMock

from trackerbot.integrations.mdblist_tracking_client import MDBListTrackingClient, MDBListTrackingError, DEVICE_GRANT
from tests.test_wetrakr_prototype import Response, Session


def test_device_flow_does_not_use_metadata_api_key():
    async def run():
        session=Session([Response({'device_code':'private','user_code':'public'}),
            Response({'access_token':'token'}),Response({'user_id':7,'username':'Viewer'})])
        client=MDBListTrackingClient('app',session=session)
        await client.device_code()
        await client.device_token('private')
        await client.account('token')
        assert session.calls[0][1].endswith('/oauth/device-authorization/')
        assert session.calls[0][2]['data']=={'client_id':'app','scope':'read'}
        assert session.calls[1][2]['data']['grant_type']==DEVICE_GRANT
        assert session.calls[2][2]['headers']['Authorization']=='Bearer token'
        assert session.calls[2][2]['params'] is None
    asyncio.run(run())


def test_journal_cursor_drops_since_and_keeps_partial_opt_in():
    async def run():
        client=MDBListTrackingClient('app')
        client._request=AsyncMock(side_effect=[{'journal':[],'pagination':{'next_cursor':'one','has_more':True}},
            {'journal':[],'pagination':{'has_more':False},'server_time':'2026-10-05T00:00:00Z'}])
        pages=[page async for page in client.journal('token','2026-10-04T00:00:00Z')]
        assert len(pages)==2
        params=client._request.call_args.kwargs['params']
        assert params['cursor']=='one' and 'since' not in params
        assert params['statuses']=='partial'
    asyncio.run(run())


def test_bad_pages_and_expired_journal_stop_reconciliation():
    async def run():
        for payload,code in [({'requires_full_sync':True},'FULL_SYNC_REQUIRED'),
            ({'journal':[],'pagination':{'has_more':True}},'INVALID_RESPONSE'),
            ({'journal':['invalid'],'pagination':{}},'INVALID_RESPONSE')]:
            client=MDBListTrackingClient('app')
            client._request=AsyncMock(return_value=payload)
            try:
                _=[p async for p in client.journal('token','2026-10-01T00:00:00Z')]
            except MDBListTrackingError as error:
                assert error.code==code
            else:
                raise AssertionError('Incomplete responses must not become empty success')
        client=MDBListTrackingClient('app')
        client._request=AsyncMock(return_value={'movies':[],'pagination':{'next_cursor':'repeat'}})
        try:
            _=[p async for p in client.history('token',media_type='movie')]
        except MDBListTrackingError as error:
            assert error.code=='CURSOR_CYCLE'
        else:
            raise AssertionError('Repeated cursor must stop')
    asyncio.run(run())


def test_rate_limit_and_errors_never_echo_secrets():
    async def run():
        for response,code in [(Response({'error':'SECRET_TOKEN'},status=401),'HTTP_ERROR'),
            (Response({},headers={'Retry-After':'120'},status=429),'RATE_LIMITED')]:
            session=Session([response]);client=MDBListTrackingClient('app',session=session)
            try:
                await client.account('SECRET_TOKEN')
            except MDBListTrackingError as error:
                assert error.code==code and 'SECRET_TOKEN' not in str(error)
                if code=='RATE_LIMITED': assert error.retry_after==120
            assert len(session.calls)==1
    asyncio.run(run())


def test_truncated_play_history_cannot_establish_removals():
    async def run():
        client=MDBListTrackingClient('app')
        client._request=AsyncMock(return_value={'plays':[],'truncated':True})
        try:
            await client.item_plays('token','movie','tmdb',1)
        except MDBListTrackingError as error:
            assert error.code=='INCOMPLETE_PLAY_HISTORY'
        else:
            raise AssertionError('Truncated data is not a complete removal snapshot')
    asyncio.run(run())
