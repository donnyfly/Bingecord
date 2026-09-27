import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from simkl_client import SimklClient


class Response:
    def __init__(self, location):
        self.status=301
        self.headers={"Location":location}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def test_recommendation_links_accept_exact_titles_and_reject_search_fallback():
    async def scenario():
        client=SimklClient("test-id")
        session=SimpleNamespace(get=lambda *args, **kwargs: Response("//simkl.com/tv/123/my-show?client_id=test"))
        client._get_session=AsyncMock(return_value=session)
        assert await client.resolve_title_url(456,"tv")=="https://simkl.com/tv/123"
        session.get=lambda *args, **kwargs: Response("https://simkl.com/search/?type=tv&q=Missing")
        assert await client.resolve_title_url(456,"tv") is None
        session.get=lambda *args, **kwargs: Response("https://evil.example/tv/123")
        assert await client.resolve_title_url(456,"tv") is None
    asyncio.run(scenario())
