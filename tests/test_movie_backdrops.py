import asyncio
from unittest.mock import AsyncMock

from tmdb_client import TmdbClient


def test_movie_backdrop_prefers_english_then_falls_back(monkeypatch):
    async def scenario():
        client = TmdbClient('test')
        english = {'file_path': '/en.jpg', 'iso_639_1': 'en', 'vote_average': 1}
        neutral = {'file_path': '/neutral.jpg', 'iso_639_1': None, 'vote_average': 9}
        monkeypatch.setattr(client, '_get_json', AsyncMock(side_effect=[
            {'backdrops': [neutral, english]}, {'backdrops': [neutral]}]))
        assert (await client.get_movie_backdrop(1)).endswith('/en.jpg')
        assert (await client.get_movie_backdrop(2)).endswith('/neutral.jpg')
    asyncio.run(scenario())
