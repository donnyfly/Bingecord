"""Read-only WeTrakr API prototype (API version 1, September 2026).

The app key identifies this bot; user history additionally requires OAuth.
Never log or persist the device code, access token, or refresh token here.
"""

from __future__ import annotations

import asyncio
import os
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import aiohttp


BASE_URL = "https://api.wetrakr.com"


class WeTrakrError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        super().__init__(f"WeTrakr {status} {code}: {message}")


def page_rows(payload, *, journal=False):
    """Reject changed/error envelopes instead of treating them as empty history."""
    if isinstance(payload, list) and not journal:
        rows = payload
    elif isinstance(payload, dict):
        keys = ('journal',) if journal else ('items', 'history', 'tracking', 'results', 'data')
        rows = next((payload[key] for key in keys if isinstance(payload.get(key), list)), None)
        if rows is None:
            raise WeTrakrError(200, 'INVALID_RESPONSE', 'Expected a recognized list envelope')
    else:
        raise WeTrakrError(200, 'INVALID_RESPONSE', 'Expected a list response')
    if any(not isinstance(row, dict) for row in rows):
        raise WeTrakrError(200, 'INVALID_RESPONSE', 'Expected object entries in list response')
    return rows


def timestamp(value: str) -> datetime:
    """Compare API timestamps by instant, never by their timezone spelling."""
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        raise WeTrakrError(200, 'INVALID_RESPONSE', 'Invalid visibility timestamp') from None


class JournalEntries(list):
    """List-compatible journal batch with its conservative readable watermark."""
    def __init__(self):
        super().__init__()
        self.visible_until = None


class WeTrakrClient:
    @classmethod
    def from_environment(cls, session: aiohttp.ClientSession | None = None):
        return cls(os.getenv("WETRAKR_API_KEY", ""), session)

    def __init__(self, app_key: str, session: aiohttp.ClientSession | None = None):
        if not app_key:
            raise ValueError("WETRAKR_API_KEY is required")
        self.app_key = app_key
        self._session = session
        self._owns_session = session is None
        self.request_counts = Counter()

    async def close(self):
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _request(self, method: str, path: str, token: str | None = None,
                       *, params: dict | None = None, body: dict | None = None):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        headers = {"wetrakr-api-key": self.app_key, "wetrakr-api-version": "1",
                   "User-Agent": "WatchRelayBot/experimental"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if body is not None:
            headers["Content-Type"] = "application/json"
        # A limited retry for transient transport and server failures. Quota
        # responses must reach the caller so polling can pause until reset.
        for attempt in range(3):
            try:
                parts = path.strip("/").split("/")
                category = (parts[1] if parts[0] == "sync" and len(parts) > 1
                            else parts[0])
                self.request_counts[category] += 1
                async with self._session.request(method, BASE_URL + path, headers=headers,
                                                 params=params, json=body) as response:
                    if response.status in {500, 502, 503, 504} and attempt < 2:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    try:
                        payload = await response.json(content_type=None)
                    except (ValueError, aiohttp.ContentTypeError):
                        raise WeTrakrError(response.status, 'INVALID_RESPONSE', 'Response was not JSON') from None
                    if response.status >= 400:
                        error = payload.get("error") if isinstance(payload, dict) else None
                        code = error.get("code") if isinstance(error, dict) else error
                        message = payload.get("message", "Request failed") if isinstance(payload, dict) else "Request failed"
                        raise WeTrakrError(response.status, str(code or "HTTP_ERROR"), message)
                    return payload, response.headers
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if attempt == 2:
                    raise
                await asyncio.sleep(2 ** attempt)
        raise RuntimeError("WeTrakr retry loop exhausted")

    async def device_code(self) -> dict:
        data, _ = await self._request("POST", "/oauth/device/code", body={"client_id": self.app_key})
        return data

    async def device_token(self, device_code: str) -> dict:
        """Call no more frequently than device_code()['interval'] seconds."""
        data, _ = await self._request("POST", "/oauth/device/token",
                                      body={"client_id": self.app_key, "code": device_code})
        return data

    async def refresh_token(self, refresh_token: str) -> dict:
        data, _ = await self._request("POST", "/oauth/token/refresh",
                                      body={"refresh_token": refresh_token})
        # The refresh response currently calls the rotated token new_refresh_token.
        if "new_refresh_token" in data:
            data["refresh_token"] = data["new_refresh_token"]
        return data

    async def account(self, token: str) -> dict:
        data, _ = await self._request("GET", "/account/settings", token)
        return data

    async def episode(self, episode_id: int | str) -> dict:
        data, _ = await self._request("GET", f"/episodes/{int(episode_id)}",
                                      params={"extended": "episode_level_1"})
        return data

    async def title(self, media_type: str, title_id: int | str) -> dict:
        if media_type not in {"movie", "show"}:
            raise ValueError("WeTrakr title must be a movie or show")
        path = "movies" if media_type == "movie" else "shows"
        data, _ = await self._request("GET", f"/{path}/{int(title_id)}")
        return data

    async def last_activities(self, token: str) -> dict:
        data, _ = await self._request("GET", "/sync/last_activities", token)
        return data

    async def journal(self, token: str, from_date: str, *, category: str | None = None,
                      limit: int = 1000, max_pages: int | None = None) -> list[dict]:
        """Read all pages before advancing a stored mark; retain entry_ids for overlap."""
        if not from_date:
            raise ValueError("A journal checkpoint is required")
        params: dict[str, Any] = {"from_date": from_date, "limit": min(limit, 1000)}
        if category:
            params["category"] = category
        entries = JournalEntries()
        visibility_complete = True
        page = 1
        while True:
            data, headers = await self._request("GET", "/sync/journal", token,
                                                params={**params, "page": page})
            rows = page_rows(data, journal=True)
            entries.extend(rows)
            mark = data.get('visible_until')
            if mark is None:
                visibility_complete = False
            else:
                timestamp(mark)
                if entries.visible_until is None or timestamp(mark) < timestamp(entries.visible_until):
                    entries.visible_until = mark
            try:
                pages = int(headers.get("X-Pagination-Page-Count", "1"))
                if pages == 0 and page == 1 and not rows:
                    pages = 1  # Empty result sets may report zero total pages.
                if pages < page:
                    raise ValueError
            except (TypeError, ValueError):
                raise WeTrakrError(200, 'INVALID_RESPONSE', 'Invalid journal pagination count') from None
            if page >= pages:
                if not visibility_complete:
                    entries.visible_until = None
                return entries
            if max_pages is not None and page >= max_pages:
                raise WeTrakrError(200, 'VALIDATION_PAGE_LIMIT', 'Journal exceeds the validation page limit')
            page += 1

    async def compact_history(self, token: str, target: str, *, limit: int = 5000):
        """Yield one small page per play; the cursor must never be used as page."""
        if target not in {"movies", "episodes"}:
            raise ValueError("target must be movies or episodes")
        after = None
        seen_cursors = set()
        while True:
            params = {"compact": "true", "limit": min(limit, 5000)}
            if after:
                params["after"] = after
            data, headers = await self._request(
                "GET", f"/sync/tracking/watched/history/{target}", token, params=params)
            page_rows(data)
            yield data
            next_cursor = headers.get("X-Pagination-Next")
            if not next_cursor:
                return
            if next_cursor in seen_cursors:
                raise WeTrakrError(200, 'INVALID_RESPONSE', 'History pagination cursor repeated')
            seen_cursors.add(next_cursor)
            after = next_cursor

    async def tracking(self, token: str, status: str, target: str, *, limit: int = 5000):
        """Yield cursor-paginated tracking list pages for discovery commands."""
        if status not in {"watching", "waiting", "planning", "dropped", "paused", "watched"}:
            raise ValueError("Unknown WeTrakr tracking status")
        if target not in {"movies", "shows"}:
            raise ValueError("WeTrakr tracking target must be movies or shows")
        after = None
        seen_cursors = set()
        while True:
            params = {"compact": "true", "limit": min(limit, 5000)}
            if after:
                params["after"] = after
            data, headers = await self._request(
                "GET", f"/sync/tracking/{status}/{target}", token, params=params)
            page_rows(data)
            yield data
            next_cursor = headers.get("X-Pagination-Next")
            if not next_cursor:
                return
            if next_cursor in seen_cursors:
                raise WeTrakrError(200, 'INVALID_RESPONSE', 'Tracking pagination cursor repeated')
            seen_cursors.add(next_cursor)
            after = next_cursor
