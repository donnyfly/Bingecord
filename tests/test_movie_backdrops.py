import asyncio
from unittest.mock import AsyncMock

from trackerbot.metadata.tmdb_client import TmdbClient


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


def test_tv_artwork_falls_back_to_other_languages_and_retries_a_missing_logo(monkeypatch):
    async def scenario():
        client = TmdbClient("test")
        request = AsyncMock(side_effect=[
            {"backdrops": [], "logos": []},
            {"backdrops": [{"file_path": "/thai.jpg", "iso_639_1": "th"}]},
            {"logos": []}, {"logos": []},
            {"logos": []}, {"logos": [{"file_path": "/thai.svg", "iso_639_1": "th"}]},
        ])
        monkeypatch.setattr(client, "_get_json", request)
        assert (await client.get_tv_backdrop(306936)).endswith("/thai.jpg")
        assert await client.get_tv_logo(306936) is None
        assert (await client.get_tv_logo(306936)).endswith("/thai.png")
        assert request.await_args_list[0].args[1] == {"include_image_language": "en,null"}
        assert request.await_args_list[1].args[1] is None
        assert request.await_count == 6

    asyncio.run(scenario())
