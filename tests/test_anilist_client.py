import asyncio

import pytest

from trackerbot.integrations.anilist_client import AniListAccount, AniListClient, AniListError


class Response:
    def __init__(self, payload, status=200, headers=None):
        self.payload = payload
        self.status = status
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def json(self):
        return self.payload


class Session:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.requests = []

    def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return next(self.responses)


def page(media_type="MANGA", owner=42):
    return {"data": {"Page": {"pageInfo": {"hasNextPage": True}, "mediaList": [{
        "id": 12, "userId": owner, "mediaId": 5, "status": "CURRENT", "progress": 16,
        "repeat": 1, "updatedAt": 1760000000, "private": True,
        "media": {"id": 5, "type": media_type, "format": media_type, "episodes": 24,
                  "chapters": 60, "title": {"english": None, "romaji": "Example", "native": None},
                  "coverImage": {"large": "https://s4.anilist.co/file/cover.jpg"}, "bannerImage": None},
    }]}}}


def test_authentication_and_lists_use_explicit_media_type_and_preserve_privacy():
    session = Session(Response({"data": {"Viewer": {"id": 42, "name": "Viewer"}}}),
                      Response(page()), Response(page("ANIME")))
    client = AniListClient(session)

    async def run():
        account = await client.viewer("test-token")
        manga = await client.list_page("test-token", account, "MANGA")
        anime = await client.list_page("test-token", account, "ANIME", 2)
        assert account == AniListAccount(42, "Viewer")
        assert manga.next_page == 2 and anime.next_page == 3
        chapter, episode = manga.entries[0], anime.entries[0]
        assert chapter.title == "Example" and chapter.private and chapter.progress == 16
        assert chapter.total == 60 and episode.total == 24
        assert chapter.site_url == "https://anilist.co/manga/5"
        assert episode.site_url == "https://anilist.co/anime/5"

    asyncio.run(run())
    assert session.requests[1][1]["json"]["variables"] == {"userId": 42, "type": "MANGA", "page": 1}
    assert session.requests[2][1]["json"]["variables"] == {"userId": 42, "type": "ANIME", "page": 2}
    assert all(not request["allow_redirects"] for _, request in session.requests)


@pytest.mark.parametrize("payload", [page("ANIME"), page(owner=7), {**page(), "errors": [{"message": "bad"}]}])
def test_rejects_wrong_account_wrong_media_and_partial_graphql_results(payload):
    client = AniListClient(Session(Response(payload)))
    with pytest.raises(AniListError):
        asyncio.run(client.list_page("test-token", AniListAccount(42, "Viewer"), "MANGA"))


def test_rate_limit_exposes_retry_delay_without_response_body():
    client = AniListClient(Session(Response({"secret": "must not leak"}, 429, {"Retry-After": "120"})))
    with pytest.raises(AniListError) as caught:
        asyncio.run(client.viewer("test-token"))
    assert caught.value.status == 429 and caught.value.retry_after == 120
    assert "must not leak" not in str(caught.value)
