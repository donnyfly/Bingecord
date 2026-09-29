"""
Small MDBList API client used for movie/show IMDb and MyAnimeList ratings.

MDBList returns multiple ratings for a TMDB title. We expose the IMDb and
MyAnimeList ratings needed by the Discord embeds.

The cache is process-local because ratings are metadata rather than bot
state. A shared cache also prevents duplicate requests when the same
title is watched in multiple Discord servers.
"""

import asyncio
import logging
import time

import aiohttp


API_BASE = "https://api.mdblist.com"
CACHE_TTL_SECONDS = 6 * 60 * 60
NEGATIVE_CACHE_TTL_SECONDS = 60 * 60

log = logging.getLogger("simkl-bot")


class MdbListClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self._session: aiohttp.ClientSession | None = None
        self._cache: dict[tuple[str, int], tuple[float, float | None]] = {}
        self._inflight: dict[tuple[str, int], asyncio.Task] = {}

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
                headers={
                    "User-Agent": "simkl-tracker-bot/1.0.0",
                    "Accept": "application/json",
                },
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _fetch_ratings(self, media_type: str, tmdb_id: int) -> dict[str, float]:
        path = f"{API_BASE}/tmdb/{media_type}/{tmdb_id}"
        session = await self._get_session()

        for attempt in range(4):
            try:
                async with session.get(
                    path,
                    params={"apikey": self.api_key},
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        ratings = {}
                        for item in data.get("ratings") or []:
                            source = str(item.get("source", "")).lower()
                            if source not in {"imdb", "myanimelist"}:
                                continue
                            try:
                                ratings[source] = float(item.get("value"))
                            except (TypeError, ValueError):
                                continue
                        return ratings

                    if resp.status != 429 or attempt >= 3:
                        log.warning(
                            "MDBList request returned HTTP %s for %s.",
                            resp.status,
                            path,
                        )
                        return None

                    retry_after = resp.headers.get("Retry-After")
                    try:
                        delay = float(retry_after)
                    except (TypeError, ValueError):
                        delay = 2 ** attempt

                    delay = min(max(delay, 1.0), 30.0)
                    log.warning(
                        "MDBList rate limit reached; retrying in %.1fs.",
                        delay,
                    )
                    await asyncio.sleep(delay)

            except (aiohttp.ClientError, TimeoutError):
                log.warning("MDBList request failed: %s", path, exc_info=True)
                return None

        return {}

    async def get_ratings(self, media_type: str, tmdb_id) -> dict[str, float]:
        try:
            tmdb_id = int(tmdb_id)
        except (TypeError, ValueError):
            return None

        if media_type not in {"movie", "show"}:
            return {}

        key = (media_type, tmdb_id)
        now = time.monotonic()
        cached = self._cache.get(key)

        if cached is not None:
            expires_at, value = cached
            if now < expires_at:
                return value
            self._cache.pop(key, None)

        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._fetch_ratings(media_type, tmdb_id))
            self._inflight[key] = task

        try:
            value = await task
        finally:
            if self._inflight.get(key) is task:
                self._inflight.pop(key, None)

        ttl = CACHE_TTL_SECONDS if value else NEGATIVE_CACHE_TTL_SECONDS
        self._cache[key] = (time.monotonic() + ttl, value)
        return value

    async def get_imdb_rating(self, media_type: str, tmdb_id) -> float | None:
        ratings = await self.get_ratings(media_type, tmdb_id)
        if not ratings:
            return None
        return ratings.get("imdb")
