"""Read AniList account identity and typed anime or manga list entries."""

from dataclasses import dataclass
from typing import Literal

import aiohttp


ANILIST_GRAPHQL = "https://graphql.anilist.co"
ANILIST_TOKEN_URL = "https://anilist.co/api/v2/oauth/token"
PIN_URL = "https://anilist.co/api/v2/oauth/pin"
VIEWER_QUERY = "query BingecordViewer { Viewer { id name } }"
ENTRIES_QUERY = """
query BingecordList($userId: Int!, $type: MediaType!, $page: Int!) {
  Page(page: $page, perPage: 50) {
    pageInfo { hasNextPage }
    mediaList(userId: $userId, type: $type, sort: MEDIA_ID) {
      id userId mediaId status progress repeat updatedAt private score(format: POINT_10_DECIMAL)
      media {
        id type format episodes chapters bannerImage genres
        title { english romaji native }
        coverImage { large }
      }
    }
  }
}
"""
MediaType = Literal["ANIME", "MANGA"]
STATUSES = frozenset({"CURRENT", "PLANNING", "COMPLETED", "DROPPED", "PAUSED", "REPEATING"})


class AniListError(Exception):
    def __init__(self, status: int, reason: str, retry_after: int | None = None):
        self.status = status
        self.retry_after = retry_after
        super().__init__(f"AniList ({status}): {reason}")


@dataclass(frozen=True)
class AniListAccount:
    id: int
    name: str


@dataclass(frozen=True)
class AniListEntry:
    id: int
    media_id: int
    media_type: MediaType
    title: str
    format: str | None
    status: str
    progress: int | None
    repeat: int | None
    updated_at: int | None
    private: bool
    total: int | None
    cover_url: str | None
    banner_url: str | None
    score: float | None = None
    genres: tuple[str, ...] = ()

    @property
    def site_url(self) -> str:
        return f"https://anilist.co/{self.media_type.lower()}/{self.media_id}"


@dataclass(frozen=True)
class AniListPage:
    entries: tuple[AniListEntry, ...]
    next_page: int | None


class AniListClient:
    def __init__(self, session: aiohttp.ClientSession):
        self._session = session

    async def exchange_code(self, code: str, client_id: str, client_secret: str) -> dict:
        try:
            async with self._session.post(
                ANILIST_TOKEN_URL,
                json={"grant_type": "authorization_code", "client_id": client_id,
                      "client_secret": client_secret, "redirect_uri": PIN_URL, "code": code},
                headers={"Accept": "application/json"},
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=20, connect=5),
            ) as response:
                if response.status != 200:
                    raise AniListError(response.status, "authorization code exchange failed")
                payload = await response.json()
        except (aiohttp.ClientConnectionError, aiohttp.ClientPayloadError, TimeoutError):
            raise AniListError(0, "authorization connection failed") from None
        except (aiohttp.ContentTypeError, ValueError):
            raise AniListError(200, "invalid authorization response") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("access_token"), str) or not payload["access_token"]:
            raise AniListError(200, "missing access token")
        return payload

    async def _query(self, token: str, query: str, variables: dict | None = None) -> dict:
        if not token:
            raise ValueError("An AniList access token is required")
        try:
            async with self._session.post(
                ANILIST_GRAPHQL,
                json={"query": query, "variables": variables or {}},
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=20, connect=5),
            ) as response:
                if response.status == 429:
                    try:
                        delay = max(1, int(response.headers.get("Retry-After", "60")))
                    except ValueError:
                        delay = 60
                    raise AniListError(429, "rate limited", delay)
                if response.status != 200:
                    raise AniListError(response.status, "request failed")
                payload = await response.json()
        except (aiohttp.ClientConnectionError, aiohttp.ClientPayloadError, TimeoutError):
            raise AniListError(0, "connection failed") from None
        except (aiohttp.ContentTypeError, ValueError):
            raise AniListError(200, "invalid response") from None

        # Partial GraphQL results cannot safely be used for change detection.
        if not isinstance(payload, dict) or payload.get("errors") or not isinstance(payload.get("data"), dict):
            raise AniListError(200, "incomplete GraphQL response")
        return payload["data"]

    async def viewer(self, token: str) -> AniListAccount:
        viewer = (await self._query(token, VIEWER_QUERY)).get("Viewer")
        if (
            not isinstance(viewer, dict) or type(viewer.get("id")) is not int
            or viewer["id"] < 1 or not isinstance(viewer.get("name"), str) or not viewer["name"]
        ):
            raise AniListError(200, "missing account identity")
        return AniListAccount(viewer["id"], viewer["name"])

    async def list_page(
        self, token: str, account: AniListAccount, media_type: MediaType, page: int = 1,
    ) -> AniListPage:
        if media_type not in ("ANIME", "MANGA") or page < 1:
            raise ValueError("Request an anime or manga list with a positive page number")
        data = await self._query(
            token, ENTRIES_QUERY, {"userId": account.id, "type": media_type, "page": page},
        )
        try:
            result = data["Page"]
            rows = result["mediaList"]
            has_next = result["pageInfo"]["hasNextPage"]
            if not isinstance(rows, list) or type(has_next) is not bool:
                raise ValueError
            entries = tuple(_parse_entry(row, account.id, media_type) for row in rows)
            if len({entry.media_id for entry in entries}) != len(entries):
                raise ValueError
        except (TypeError, KeyError, AttributeError, ValueError):
            raise AniListError(200, "invalid media list page") from None
        return AniListPage(entries, page + 1 if has_next else None)


def _parse_entry(row: dict, account_id: int, media_type: MediaType) -> AniListEntry:
    media = row["media"]
    title = media["title"]
    ids = (row["id"], row["mediaId"], row["userId"], media["id"])
    if (
        any(type(value) is not int or value < 1 for value in ids)
        or row["userId"] != account_id or row["mediaId"] != media["id"]
        or media["type"] != media_type or row["status"] not in STATUSES
        or type(row["private"]) is not bool
    ):
        raise ValueError

    progress, repeat, updated_at = (row[key] for key in ("progress", "repeat", "updatedAt"))
    total = media["episodes" if media_type == "ANIME" else "chapters"]
    if any(value is not None and (type(value) is not int or value < 0)
           for value in (progress, repeat, updated_at, total)):
        raise ValueError
    return AniListEntry(
        row["id"], media["id"], media_type,
        title["english"] or title["romaji"] or title["native"] or f"AniList #{media['id']}",
        media["format"], row["status"], progress, repeat, updated_at, row["private"],
        total, (media["coverImage"] or {}).get("large"), media["bannerImage"],
        row.get("score"), tuple(media.get("genres") or ()),
    )
