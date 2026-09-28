import asyncio
from unittest.mock import AsyncMock

from tmdb_client import TmdbClient


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
    asyncio.run(run())
