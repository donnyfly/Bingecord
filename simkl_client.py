"""
SIMKL API client for the Discord watch activity bot.

- Uses SIMKL AUTH V2 Device / PIN flow
- Supports automatic token refresh
- Reuses a single aiohttp session for all API requests
- Identifies the application with app-name and app-version
- Returns token expiry information for proactive refresh
"""

import asyncio
import json
import logging

import aiohttp

API_BASE = "https://api.simkl.com"

APP_NAME = "simkl-tracker-bot"
APP_VERSION = "1.0.0"

USER_AGENT = f"{APP_NAME}/{APP_VERSION}"

FORM_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": USER_AGENT,
}

log = logging.getLogger("simkl-bot")


class SimklAuthError(Exception):
    """Raised when a stored token is no longer valid, or a PIN was refused."""


class SimklSlowDown(Exception):
    """Raised when SIMKL asks the PIN poller to poll less often."""


def _parse_json(text: str):
    """Parse a JSON body, returning None if it isn't valid JSON."""
    try:
        return json.loads(text)
    except ValueError:
        return None


class SimklClient:
    def __init__(self, client_id: str):
        self.client_id = client_id
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Return the shared HTTP session, creating it if needed."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": USER_AGENT},
            )
        return self._session

    async def close(self) -> None:
        """Close the shared HTTP session."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    def _headers(self, token: str | None = None) -> dict:
        """Build common SIMKL API headers."""
        headers = {
            "Content-Type": "application/json",
            "simkl-api-key": self.client_id,
            "User-Agent": USER_AGENT,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _params(self, **extra) -> dict:
        """Build common SIMKL API query parameters (identifies the app)."""
        return {
            "client_id": self.client_id,
            "app-name": APP_NAME,
            "app-version": APP_VERSION,
            **extra,
        }

    def _form(self, **extra) -> dict:
        """Build the common form body for OAuth requests."""
        return {
            "client_id": self.client_id,
            "app-name": APP_NAME,
            "app-version": APP_VERSION,
            **extra,
        }

    async def _post_form(self, path: str, data: dict) -> tuple[int, str]:
        """POST a form to SIMKL and return (status, body text)."""
        session = await self._get_session()
        async with session.post(
            f"{API_BASE}{path}", data=data, headers=FORM_HEADERS
        ) as resp:
            return resp.status, await resp.text()

    async def _get(
        self,
        path: str,
        token: str,
        params: dict | None = None,
        timeout: float | None = None,
    ):
        """Authenticated GET with retry/backoff for transient failures."""
        session = await self._get_session()
        kwargs = {}
        if timeout is not None:
            kwargs["timeout"] = aiohttp.ClientTimeout(total=timeout)

        request_params = params if params is not None else self._params()

        for attempt in range(4):
            try:
                async with session.get(
                    f"{API_BASE}{path}",
                    params=request_params,
                    headers=self._headers(token),
                    **kwargs,
                ) as resp:
                    if resp.status == 401:
                        raise SimklAuthError("Token invalid or revoked")

                    if resp.status == 429 or 500 <= resp.status < 600:
                        if attempt < 3:
                            retry_after = resp.headers.get("Retry-After")
                            try:
                                delay = float(retry_after)
                            except (TypeError, ValueError):
                                delay = 2 ** attempt
                            delay = min(max(delay, 1.0), 30.0)
                            log.warning(
                                "SIMKL returned HTTP %s for %s; retrying in %.1fs.",
                                resp.status,
                                path,
                                delay,
                            )
                            await asyncio.sleep(delay)
                            continue

                    resp.raise_for_status()
                    return await resp.json()

            except SimklAuthError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if attempt >= 3:
                    raise
                delay = min(2 ** attempt, 30)
                log.warning(
                    "SIMKL request failed for %s; retrying in %.1fs.",
                    path,
                    delay,
                    exc_info=True,
                )
                await asyncio.sleep(delay)

        raise RuntimeError(f"SIMKL request failed after retries: {path}")

    # -----------------------------------------------------------------------
    # AUTH V2 Device / PIN flow
    # -----------------------------------------------------------------------

    async def start_pin_auth(self) -> dict:
        """
        Start the SIMKL AUTH V2 device flow.

        Returns a dict containing device_code, user_code, verification_uri,
        expires_in and interval.
        """
        session = await self._get_session()
        async with session.post(
            f"{API_BASE}/oauth2/device",
            data=self._form(scope="media:read media:write"),
            headers=FORM_HEADERS,
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def poll_pin(self, device_code: str) -> dict | None:
        """
        Poll for SIMKL device authorization.

        Returns the token dict once approved, or None while still pending.
        Raises SimklSlowDown if SIMKL asks us to poll less often, and
        SimklAuthError if the code expired or the user denied access.
        """
        status, text = await self._post_form(
            "/oauth2/token",
            self._form(
                grant_type="urn:ietf:params:oauth:grant-type:device_code",
                device_code=device_code,
            ),
        )
        body = _parse_json(text)

        if status == 200:
            if isinstance(body, dict) and "access_token" in body:
                return {
                    "access_token": body["access_token"],
                    "refresh_token": body.get("refresh_token"),
                    "expires_in": body.get("expires_in"),
                }
            log.warning("200 response but no usable access_token: %s", text)
            return None

        error = body.get("error", "") if isinstance(body, dict) else ""

        if error == "authorization_pending":
            return None
        if error == "slow_down":
            raise SimklSlowDown()
        if error in ("expired_token", "access_denied"):
            raise SimklAuthError(error)

        log.warning("Token poll error %s: %s", status, text)
        return None

    async def refresh_token(self, refresh_token: str) -> dict | None:
        """
        Exchange a refresh token for a new access token.

        Returns the new access token, refresh token and expiry information,
        or None if the refresh failed.
        """
        status, text = await self._post_form(
            "/oauth2/token",
            self._form(grant_type="refresh_token", refresh_token=refresh_token),
        )

        if status != 200:
            log.warning("Refresh failed %s: %s", status, text)
            return None

        body = _parse_json(text)
        if not isinstance(body, dict):
            log.warning("SIMKL refresh returned invalid JSON: %s", text)
            return None

        access_token = body.get("access_token")
        if not access_token:
            log.warning("SIMKL refresh response had no access_token")
            return None

        return {
            "access_token": access_token,
            "refresh_token": body.get("refresh_token", refresh_token),
            "expires_in": body.get("expires_in"),
        }

    # -----------------------------------------------------------------------
    # Authenticated API calls
    # -----------------------------------------------------------------------

    async def get_user_settings(self, token: str) -> dict:
        """Get the authenticated user's SIMKL settings."""
        return await self._get("/users/settings", token)

    async def get_activities(self, token: str) -> dict:
        """Get the authenticated user's activity timestamps."""
        return await self._get("/sync/activities", token)

    async def get_all_items(
        self,
        token: str,
        media_type: str,
        date_from: str | None = None,
        timeout: float | None = None,
    ) -> list:
        """
        Get watched-item data for one media type.

        With date_from, only items changed since then are returned.
        Without it, the user's full history is returned (used once per
        user to record what they had already watched).
        """
        # Anime needs the additional season mapping because SIMKL stores
        # seasonal anime as separate entries but the bot should display
        # their normal TVDB/American-style season number (S01, S02, etc.).
        extended = "full_anime_seasons" if media_type == "anime" else "full"
        # SIMKL omits episode arrays for completed and dropped shows by default.
        # `yes` also supplies virtual rows for shows marked complete in one action,
        # matching SIMKL's watched-episode counter when individual dates are absent.
        params = self._params(extended=extended, episode_watched_at="yes", include_all_episodes="yes", language="en")
        if date_from:
            params["date_from"] = date_from

        data = await self._get(
            f"/sync/all-items/{media_type}", token, params=params, timeout=timeout
        )

        if isinstance(data, dict):
            return data.get(media_type, [])
        return data or []
