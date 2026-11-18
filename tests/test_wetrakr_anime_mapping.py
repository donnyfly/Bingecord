import asyncio
from unittest.mock import AsyncMock

from trackerbot.metadata.tmdb_client import TmdbClient


def test_tvmaze_episode_identity_mapping_and_ambiguity():
    async def run():
        client = TmdbClient("test-key")
        client.find_tvmaze_show_by_tvdb = AsyncMock(return_value={"id": 48450})
        client._get_tvmaze_json = AsyncMock(return_value=[
            {"season": 1, "number": 24, "name": "Accomplices", "airdate": "2021-03-26"},
            {"season": 2, "number": 1, "name": "Hidden Inventory", "airdate": "2023-07-06"},
            {"season": 2, "number": 2, "name": "Hidden Inventory, Part 2", "airdate": "2023-07-13"},
        ])
        assert await client.map_anime_episode_to_tvmaze(377543, title="Hidden Inventory") == (2, 1)
        assert await client.map_anime_episode_to_tvmaze(377543, air_date="2023-07-13") == (2, 2)
        assert await client.map_anime_episode_to_tvmaze(377543, title="Not available") is None
        client._get_tvmaze_json.assert_awaited_once()
    asyncio.run(run())


def test_calendar_year_seasons_use_continuous_episode_numbers():
    async def run():
        client = TmdbClient("test-key")
        client.find_tvmaze_show_by_tvdb = AsyncMock(return_value={"id": 1})
        client._get_tvmaze_json = AsyncMock(return_value=[
            {"season": 0, "number": 1, "name": "Special", "airdate": "1999-01-01"},
            {"season": 1999, "number": 1, "name": "First", "airdate": "1999-10-20"},
            {"season": 1999, "number": 2, "name": "Second", "airdate": "1999-10-27"},
            {"season": 2000, "number": 1, "name": "Third", "airdate": "2000-01-01"},
        ])
        assert await client.map_anime_episode_to_tvmaze(1, title="First") == (1, 1)
        assert await client.map_anime_episode_to_tvmaze(1, title="Third") == (1, 3)
        assert await client.map_anime_calendar_episode(1, 2000, 1) == (1, 3)
    asyncio.run(run())


def test_regular_anime_fallback_never_selects_same_day_special():
    async def run():
        for airdate in ('2026-04-08', None):
            client=TmdbClient('test-key')
            client.find_series_by_tvdb=AsyncMock(return_value=10)
            client.get_tvmaze_episode=AsyncMock(return_value={
                'season':4,'number':1,'name':'Regular premiere','airdate':airdate})
            client._get_series_details=AsyncMock(return_value={'seasons':[{'season_number':0},{'season_number':3}]})
            special={'episode_number':8,'name':'Regular premiere','air_date':'2026-04-08','still_path':'/wrong.jpg'}
            regular={'episode_number':17,'name':'Regular premiere','air_date':'2026-04-08','still_path':'/right.jpg'}
            client._get_season_details=AsyncMock(side_effect=lambda _,s:{'episodes':[special if s==0 else regular]})
            client.get_episode_details=AsyncMock(side_effect=lambda _,s,e: regular if (s,e)==(3,17) else special if s==0 else None)
            result=await client.find_anime_episode(10,20,[4],1)
            assert (result['season_number'],result['episode_number'])==(3,17)
            assert result['still_url'].endswith('/right.jpg')
            assert all(call.args[1]!=0 for call in client._get_season_details.await_args_list)
    asyncio.run(run())


def test_regular_anime_keeps_tvmaze_when_only_special_matches_and_explicit_special_still_works():
    async def run():
        client=TmdbClient('test-key')
        client.find_series_by_tvdb=AsyncMock(return_value=10)
        client.get_tvmaze_episode=AsyncMock(return_value={
            'season':4,'number':1,'name':'Premiere','airdate':'2026-04-08',
            'image':{'original':'https://example.com/regular.jpg'}})
        client._get_series_details=AsyncMock(return_value={'seasons':[{'season_number':0}]})
        special={'name':'Premiere','episode_number':1,'air_date':'2026-04-08','still_path':'/wrong.jpg'}
        client._get_season_details=AsyncMock(return_value={'episodes':[special]})
        client.get_episode_details=AsyncMock(side_effect=lambda _,s,e:special if s==0 else None)
        result=await client.find_anime_episode(10,20,[4],1)
        assert result['source']=='tvmaze' and result['still_url']=='https://example.com/regular.jpg'
        client.get_tvmaze_episode.return_value={'season':0,'number':1,'name':'Special'}
        result=await client.find_anime_episode(10,20,[0],1)
        assert result['season_number']==0 and result['episode']==special
    asyncio.run(run())
