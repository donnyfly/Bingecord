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
