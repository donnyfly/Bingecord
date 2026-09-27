"""
TMDB API client for fetching TV episode stills, episode details,
and movie backdrops.

Uses TMDB's API:
https://developer.themoviedb.org/
"""

import asyncio
import logging

import aiohttp


API_BASE = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p/w780"

log = logging.getLogger("simkl-bot")


class TmdbClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self._session: aiohttp.ClientSession | None = None

        # Cache successful and unsuccessful lookups.
        self._episode_cache: dict[
            tuple[int, int, int],
            dict | None,
        ] = {}

        self._movie_backdrop_cache: dict[
            int,
            str | None,
        ] = {}

        self._tv_backdrop_cache: dict[
            int,
            str | None,
        ] = {}

        self._movie_logo_cache: dict[
            int,
            str | None,
        ] = {}

        self._tv_logo_cache: dict[
            int,
            str | None,
        ] = {}

        # Cache TVDB -> TMDB series lookups.
        self._tvdb_series_cache: dict[
            int,
            int | None,
        ] = {}

        # Cache season details.
        self._season_cache: dict[
            tuple[int, int],
            dict | None,
        ] = {}

        # Cache TV series details used by anime fallback resolution.
        self._series_cache: dict[
            int,
            dict | None,
        ] = {}

        # Cache English-localized title lookups.
        self._tv_title_cache: dict[
            tuple[int, bool],
            str | None,
        ] = {}

        self._movie_title_cache: dict[
            tuple[int, bool],
            str | None,
        ] = {}

        # Cache TVMaze show lookups used when TMDB is missing newer anime
        # seasons/episodes.
        self._tvmaze_show_cache: dict[
            int,
            dict | None,
        ] = {}

        # Cache TVMaze episode lookups.
        self._tvmaze_episode_cache: dict[
            tuple[int, int, int],
            dict | None,
        ] = {}

        # Cache the result of the more expensive anime episode resolver.
        self._anime_episode_cache: dict[
            tuple,
            dict | None,
        ] = {}

    async def _get_session(self) -> aiohttp.ClientSession:
        """Return the shared HTTP session, creating it if needed."""
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
        """Close the shared HTTP session."""
        if self._session is not None and not self._session.closed:
            await self._session.close()

        self._session = None

    # ------------------------------------------------------------------
    # Generic GET helper
    # ------------------------------------------------------------------

    async def _get_json(
        self,
        path: str,
        params: dict | None = None,
    ) -> dict | None:
        try:
            session = await self._get_session()

            request_params = {
                "api_key": self.api_key,
            }

            if params:
                request_params.update(params)

            for attempt in range(4):
                async with session.get(
                    path,
                    params=request_params,
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()

                    if resp.status != 429 or attempt >= 3:
                        if resp.status == 429:
                            log.warning(
                                "TMDB rate limit reached for %s after %s retries.",
                                path,
                                attempt,
                            )
                        return None

                    retry_after = resp.headers.get("Retry-After")
                    try:
                        delay = float(retry_after)
                    except (TypeError, ValueError):
                        delay = 2 ** attempt

                    delay = min(max(delay, 1.0), 30.0)

                    log.warning(
                        "TMDB rate limit reached for %s; retrying in %.1fs.",
                        path,
                        delay,
                    )
                    await asyncio.sleep(delay)

        except (aiohttp.ClientError, TimeoutError):
            log.warning(
                "TMDB request failed: %s",
                path,
                exc_info=True,
            )
            return None

    # ------------------------------------------------------------------
    # TVDB -> TMDB
    # ------------------------------------------------------------------

    async def find_series_by_tvdb(
        self,
        tvdb_id,
    ) -> int | None:
        """
        Find the TMDB TV series corresponding to a TVDB ID.

        TMDB supports TheTVDB as an external source for TV shows,
        seasons and episodes.
        """

        try:
            tvdb_id = int(tvdb_id)
        except (TypeError, ValueError):
            return None

        if tvdb_id in self._tvdb_series_cache:
            return self._tvdb_series_cache[tvdb_id]

        path = f"{API_BASE}/find/{tvdb_id}"

        data = await self._get_json(
            path,
            {
                "external_source": "tvdb_id",
            },
        )

        if not data:
            self._tvdb_series_cache[tvdb_id] = None
            return None

        results = data.get("tv_results") or []

        if not results:
            self._tvdb_series_cache[tvdb_id] = None
            return None

        series_id = results[0].get("id")

        try:
            series_id = int(series_id)
        except (TypeError, ValueError):
            series_id = None

        self._tvdb_series_cache[tvdb_id] = series_id

        if series_id:
            log.info(
                "Resolved TVDB series %s -> TMDB series %s.",
                tvdb_id,
                series_id,
            )

        return series_id

    # ------------------------------------------------------------------
    # TV episode details
    # ------------------------------------------------------------------

    async def get_episode_details(
        self,
        series_id,
        season_number,
        episode_number,
    ) -> dict | None:
        """
        Return TMDB episode details.

        The returned dict contains useful fields such as:
        - name
        - still_path
        - episode_number
        - season_number

        Returns None when TMDB doesn't contain that episode.
        """

        try:
            series_id = int(series_id)
            season_number = int(season_number)
            episode_number = int(episode_number)
        except (TypeError, ValueError):
            return None

        cache_key = (
            series_id,
            season_number,
            episode_number,
        )

        if cache_key in self._episode_cache:
            return self._episode_cache[cache_key]

        path = (
            f"{API_BASE}/tv/{series_id}/season/"
            f"{season_number}/episode/{episode_number}"
        )

        data = await self._get_json(
            path,
            {
                "language": "en-US",
                "append_to_response": "external_ids",
            },
        )

        if not data:
            log.info(
                "TMDB episode not found: "
                "series=%s season=%s episode=%s",
                series_id,
                season_number,
                episode_number,
            )

            self._episode_cache[cache_key] = None
            return None

        self._episode_cache[cache_key] = data

        return data

    # ------------------------------------------------------------------
    # Episode stills
    # ------------------------------------------------------------------

    async def get_episode_still(
        self,
        series_id,
        season_number,
        episode_number,
    ) -> str | None:
        """
        Return a TMDB episode still URL.

        Uses the episode details endpoint first, because it gives us
        both the episode title and still_path in one request.
        """

        episode = await self.get_episode_details(
            series_id,
            season_number,
            episode_number,
        )

        if not episode:
            return None

        still_path = episode.get("still_path")

        if not still_path:
            return None

        return f"{IMAGE_BASE}{still_path}"

    # ------------------------------------------------------------------
    # Episode title
    # ------------------------------------------------------------------

    async def get_episode_title(
        self,
        series_id,
        season_number,
        episode_number,
    ) -> str | None:
        """Return the TMDB episode title."""

        episode = await self.get_episode_details(
            series_id,
            season_number,
            episode_number,
        )

        if not episode:
            return None

        title = episode.get("name")

        if not title:
            return None

        return str(title)

    # ------------------------------------------------------------------
    # TVMaze fallback
    # ------------------------------------------------------------------

    async def _get_tvmaze_json(
        self,
        path: str,
        params: dict | None = None,
    ) -> dict | list | None:
        """Get JSON from TVMaze without requiring a separate API key."""

        try:
            session = await self._get_session()

            async with session.get(
                f"https://api.tvmaze.com{path}",
                params=params,
            ) as resp:
                if resp.status == 200:
                    return await resp.json()

                return None

        except (aiohttp.ClientError, TimeoutError):
            log.warning(
                "TVMaze request failed: %s",
                path,
                exc_info=True,
            )
            return None

    async def find_tvmaze_show_by_tvdb(
        self,
        tvdb_id,
    ) -> dict | None:
        """Find the TVMaze show corresponding to a TVDB series ID."""

        try:
            tvdb_id = int(tvdb_id)
        except (TypeError, ValueError):
            return None

        if tvdb_id in self._tvmaze_show_cache:
            return self._tvmaze_show_cache[tvdb_id]

        # TVMaze documents that this lookup returns an HTTP redirect to the
        # matched show page, rather than JSON. Keep redirects disabled so we
        # can extract the show ID without accidentally parsing the HTML page.
        try:
            session = await self._get_session()
            async with session.get(
                "https://api.tvmaze.com/lookup/shows",
                params={"thetvdb": tvdb_id},
                allow_redirects=False,
            ) as resp:
                location = resp.headers.get("Location") or ""
                log.info(
                    "TVMaze TVDB lookup: TVDB=%s HTTP=%s Location=%r.",
                    tvdb_id,
                    resp.status,
                    location,
                )
                if resp.status not in (301, 302, 303, 307, 308):
                    self._tvmaze_show_cache[tvdb_id] = None
                    return None
        except (aiohttp.ClientError, TimeoutError):
            log.warning(
                "TVMaze show lookup failed for TVDB=%s.",
                tvdb_id,
                exc_info=True,
            )
            self._tvmaze_show_cache[tvdb_id] = None
            return None

        try:
            show_id = int(location.rstrip("/").rsplit("/", 1)[-1])
        except (TypeError, ValueError):
            self._tvmaze_show_cache[tvdb_id] = None
            return None

        data = await self._get_tvmaze_json(f"/shows/{show_id}")
        if not isinstance(data, dict):
            data = None

        self._tvmaze_show_cache[tvdb_id] = data
        return data

    async def get_tvmaze_episode(
        self,
        tvdb_id,
        season_number,
        episode_number,
    ) -> dict | None:
        """Return a TVMaze episode for an exact TVDB show season/episode."""

        try:
            tvdb_id = int(tvdb_id)
            season_number = int(season_number)
            episode_number = int(episode_number)
        except (TypeError, ValueError):
            return None

        cache_key = (
            tvdb_id,
            season_number,
            episode_number,
        )

        if cache_key in self._tvmaze_episode_cache:
            return self._tvmaze_episode_cache[cache_key]

        show = await self.find_tvmaze_show_by_tvdb(tvdb_id)
        if not show or show.get("id") is None:
            self._tvmaze_episode_cache[cache_key] = None
            return None

        data = await self._get_tvmaze_json(
            f"/shows/{show['id']}/episodebynumber",
            {
                "season": season_number,
                "number": episode_number,
            },
        )

        if not isinstance(data, dict):
            data = None

        self._tvmaze_episode_cache[cache_key] = data
        return data

    # ------------------------------------------------------------------
    # Anime-aware episode lookup
    # ------------------------------------------------------------------

    async def find_anime_episode(
        self,
        series_id,
        tvdb_id,
        season_candidates,
        episode_number,
        episode_title=None,
    ) -> dict | None:
        """
        Try to resolve an anime episode when SIMKL's anime season
        numbering doesn't line up perfectly with TMDB.

        Strategy:

        1. Resolve the TVDB ID to the canonical TMDB series when available.
        2. Try the supplied season candidates using TVMaze.
        3. Try the same season candidates directly on TMDB.
        4. For high absolute episode numbers, inspect TMDB seasons for an
           exact episode-number match.

        This avoids assuming that SIMKL/TVDB/TMDB all use identical
        season numbering.
        """

        candidate_series_ids = []

        def add_series_id(value):
            try:
                value = int(value)
            except (TypeError, ValueError):
                return

            if value not in candidate_series_ids:
                candidate_series_ids.append(value)

        # Anime seasons on SIMKL are separate entries, while TMDB often
        # keeps them under one canonical TV series. Prefer the TVDB mapping
        # first because the SIMKL season entry's TMDB ID can resolve to a
        # season-specific/alternate record whose season 1 would otherwise
        # incorrectly win the lookup.
        if tvdb_id:
            resolved = await self.find_series_by_tvdb(tvdb_id)
            add_series_id(resolved)

        add_series_id(series_id)

        # --------------------------------------------------------------
        # Direct season candidates
        # --------------------------------------------------------------

        candidates = []

        for value in season_candidates or []:
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue

            if value < 0:
                continue

            if value not in candidates:
                candidates.append(value)

        try:
            episode_number = int(episode_number)
        except (TypeError, ValueError):
            return None

        normalized_title = (
            str(episode_title).strip().casefold()
            if episode_title
            else ""
        )
        cache_key = (
            tuple(candidate_series_ids),
            tuple(candidates),
            int(episode_number),
            normalized_title,
        )

        if cache_key in self._anime_episode_cache:
            return self._anime_episode_cache[cache_key]

        # --------------------------------------------------------------
        # TVMaze is the authoritative anime episode source when a TVDB ID
        # is available. SIMKL/Kitsu-style anime seasons align with TVDB much
        # more reliably than TMDB's canonical anime TV-series records.
        # Check TVMaze first so a TMDB season-1 record can never win for a
        # later anime season.
        # --------------------------------------------------------------

        if tvdb_id:
            log.info(
                "Trying TVMaze anime episode: TVDB=%s candidates=%s E%02d.",
                tvdb_id,
                candidates,
                episode_number,
            )
            for season_number in candidates:
                episode = await self.get_tvmaze_episode(
                    tvdb_id,
                    season_number,
                    episode_number,
                )

                if not episode:
                    continue

                imdb_id = (episode.get("externals") or {}).get("imdb")

                # TVMaze can resolve the episode correctly but may not expose
                # an IMDb ID. First try the same season/episode on TMDB, then
                # search the candidate TMDB series' seasons by the TVMaze
                # episode title. Anime season numbering can differ between
                # TVDB/TVMaze and TMDB, so title matching is needed here.
                if not imdb_id:
                    for current_series_id in candidate_series_ids:
                        tmdb_episode = await self.get_episode_details(
                            current_series_id,
                            season_number,
                            episode_number,
                        )
                        if not tmdb_episode:
                            continue
                        imdb_id = (
                            (tmdb_episode.get("external_ids") or {}).get(
                                "imdb_id"
                            )
                        )
                        if imdb_id:
                            break

                # TVMaze is useful for resolving SIMKL/TVDB's season
                # structure, but its episode metadata can use the anime's
                # original-language title and may omit IMDb IDs. Once TVMaze
                # identifies the episode, prefer the matching canonical TMDB
                # episode for the final title, artwork, and IMDb ID.
                found = None

                # First try the same season/episode on TMDB. This is the
                # cheapest and most precise match when both providers agree.
                for current_series_id in candidate_series_ids:
                    tmdb_episode = await self.get_episode_details(
                        current_series_id,
                        season_number,
                        episode_number,
                    )
                    if tmdb_episode:
                        found = (
                            current_series_id,
                            season_number,
                            episode_number,
                            tmdb_episode,
                        )
                        break

                # If season numbering differs, use TVMaze's air date to find
                # the canonical TMDB episode across all seasons. This handles
                # cases such as Hunter x Hunter, where TVMaze/SIMKL exposes
                # S01E84 while TMDB stores the same episode as S02E84.
                airdate = str(episode.get("airdate") or "").strip()
                if found is None and airdate:
                    for current_series_id in candidate_series_ids:
                        series = await self._get_series_details(current_series_id)
                        seasons = (series or {}).get("seasons") or []

                        for season in seasons:
                            try:
                                tmdb_season_number = int(season.get("season_number"))
                            except (TypeError, ValueError):
                                continue

                            season_data = await self._get_season_details(
                                current_series_id,
                                tmdb_season_number,
                            )
                            if not season_data:
                                continue

                            matching_episode = next(
                                (
                                    item
                                    for item in (season_data.get("episodes") or [])
                                    if str(item.get("air_date") or "").strip() == airdate
                                ),
                                None,
                            )
                            if matching_episode is None:
                                continue

                            tmdb_episode = await self.get_episode_details(
                                current_series_id,
                                tmdb_season_number,
                                matching_episode.get("episode_number"),
                            )
                            if tmdb_episode:
                                found = (
                                    current_series_id,
                                    tmdb_season_number,
                                    int(matching_episode.get("episode_number")),
                                    tmdb_episode,
                                )
                                break

                        if found is not None:
                            break

                # Finally, use title matching as a fallback. This remains
                # useful when air dates are unavailable, but it is deliberately
                # secondary because anime providers often localize titles.
                if found is None and episode.get("name"):
                    target_title = str(episode["name"]).strip().casefold()
                    for current_series_id in candidate_series_ids:
                        series = await self._get_series_details(current_series_id)
                        seasons = (series or {}).get("seasons") or []

                        for season in seasons:
                            try:
                                tmdb_season_number = int(season.get("season_number"))
                            except (TypeError, ValueError):
                                continue

                            season_data = await self._get_season_details(
                                current_series_id,
                                tmdb_season_number,
                            )
                            if not season_data:
                                continue

                            matching_episode = next(
                                (
                                    item
                                    for item in (season_data.get("episodes") or [])
                                    if str(item.get("name") or "").strip().casefold()
                                    == target_title
                                ),
                                None,
                            )
                            if matching_episode is None:
                                continue

                            tmdb_episode = await self.get_episode_details(
                                current_series_id,
                                tmdb_season_number,
                                matching_episode.get("episode_number"),
                            )
                            if tmdb_episode:
                                found = (
                                    current_series_id,
                                    tmdb_season_number,
                                    int(matching_episode.get("episode_number")),
                                    tmdb_episode,
                                )
                                break

                        if found is not None:
                            break

                if found is not None:
                    found_series_id, found_season_number, found_episode_number, found_episode = found
                    imdb_id = (
                        (found_episode.get("external_ids") or {}).get("imdb_id")
                        or imdb_id
                    )
                    result = {
                        "series_id": found_series_id,
                        "season_number": found_season_number,
                        "episode_number": found_episode_number,
                        "episode": found_episode,
                        "still_url": (
                            f"{IMAGE_BASE}{found_episode['still_path']}"
                            if found_episode.get("still_path")
                            else (
                                (episode.get("image") or {}).get("original")
                                or (episode.get("image") or {}).get("medium")
                            )
                        ),
                        "source": "tmdb_tvmaze",
                    }
                    self._anime_episode_cache[cache_key] = result
                    log.info(
                        "Resolved anime episode via TMDB using TVMaze: "
                        "TVDB=%s SIMKL=%s -> TMDB S%02dE%02d.",
                        tvdb_id,
                        f"S{season_number:02d}E{episode_number:02d}",
                        found_season_number,
                        found_episode_number,
                    )
                    return result

                # No canonical TMDB match was found, so retain TVMaze as the
                # metadata fallback rather than losing the otherwise-correct
                # episode artwork.
                result = {
                    "series_id": None,
                    "season_number": season_number,
                    "episode_number": episode_number,
                    "episode": {
                        "name": episode.get("name"),
                        "external_ids": {
                            "imdb_id": imdb_id,
                        },
                    },
                    "still_url": (
                        (episode.get("image") or {}).get("original")
                        or (episode.get("image") or {}).get("medium")
                    ),
                    "source": "tvmaze",
                }
                self._anime_episode_cache[cache_key] = result
                log.info(
                    "Resolved anime episode via TVMaze: TVDB=%s S%02dE%02d.",
                    tvdb_id,
                    season_number,
                    episode_number,
                )
                return result

        # --------------------------------------------------------------
        # TMDB fallback for exact season/episode only.
        # --------------------------------------------------------------

        for current_series_id in candidate_series_ids:
            for season_number in candidates:
                episode = await self.get_episode_details(
                    current_series_id,
                    season_number,
                    episode_number,
                )

                if not episode:
                    continue

                # When we have a known TMDB series and season number,
                # the season + episode numbers are the authoritative match.
                # SIMKL/ANIDB episode titles can differ in language or can be
                # stale, so never reject a direct season/episode match because
                # the title text differs.
                result = {
                    "series_id": current_series_id,
                    "season_number": season_number,
                    "episode_number": episode_number,
                    "episode": episode,
                }
                self._anime_episode_cache[cache_key] = result
                return result

        # --------------------------------------------------------------
        # TMDB absolute-episode fallback.
        #
        # Some long-running anime (for example One Piece) are represented by
        # TMDB as one canonical series with year/arc-based seasons, while
        # SIMKL/TVDB can expose the same show as a single season with the
        # absolute episode number. In that case an exact S01E891 lookup will
        # fail even though TMDB contains the correct episode as S20E891.
        #
        # Only use this fallback for high absolute episode numbers, where the
        # episode number itself is a strong identifier. We deliberately do not
        # do this for E01/E02/etc., because matching those across arbitrary
        # seasons could reintroduce the old "wrong S01" problem.
        # --------------------------------------------------------------

        if episode_number >= 100:
            for current_series_id in candidate_series_ids:
                series = await self._get_series_details(current_series_id)
                seasons = (series or {}).get("seasons") or []

                for season in seasons:
                    season_number = season.get("season_number")
                    try:
                        season_number = int(season_number)
                    except (TypeError, ValueError):
                        continue

                    season_data = await self._get_season_details(
                        current_series_id,
                        season_number,
                    )
                    if not season_data:
                        continue

                    matching_episode = next(
                        (
                            item
                            for item in (season_data.get("episodes") or [])
                            if item.get("episode_number") == episode_number
                        ),
                        None,
                    )
                    if matching_episode is None:
                        continue

                    episode = await self.get_episode_details(
                        current_series_id,
                        season_number,
                        episode_number,
                    )
                    if not episode:
                        continue

                    result = {
                        "series_id": current_series_id,
                        "season_number": season_number,
                        "episode_number": episode_number,
                        "episode": episode,
                        "source": "tmdb_absolute_episode",
                    }
                    self._anime_episode_cache[cache_key] = result
                    log.info(
                        "Resolved anime episode via TMDB absolute episode: "
                        "series=%s S%02dE%02d.",
                        current_series_id,
                        season_number,
                        episode_number,
                    )
                    return result

        # No safe match was found. Returning None is preferable to reusing
        # S01 (or another season) and producing a plausible-looking but
        # incorrect notification.

        self._anime_episode_cache[cache_key] = None
        return None

    async def _get_series_details(
        self,
        series_id,
    ) -> dict | None:
        """Get and cache TMDB TV series details."""

        try:
            series_id = int(series_id)
        except (TypeError, ValueError):
            return None

        if series_id in self._series_cache:
            return self._series_cache[series_id]

        data = await self._get_json(
            f"{API_BASE}/tv/{series_id}",
            {
                "language": "en-US",
            },
        )
        self._series_cache[series_id] = data
        return data

    async def _get_season_details(
        self,
        series_id,
        season_number,
    ) -> dict | None:
        """Get and cache TMDB season details."""

        try:
            series_id = int(series_id)
            season_number = int(season_number)
        except (TypeError, ValueError):
            return None

        cache_key = (
            series_id,
            season_number,
        )

        if cache_key in self._season_cache:
            return self._season_cache[cache_key]

        path = (
            f"{API_BASE}/tv/{series_id}/season/"
            f"{season_number}"
        )

        data = await self._get_json(
            path,
            {
                "language": "en-US",
            },
        )

        self._season_cache[cache_key] = data

        return data

    async def get_tv_episode_count(self, series_id) -> int | None:
        """Return the total number of episodes in a TMDB TV series."""
        data = await self._get_series_details(series_id)
        if not data:
            return None

        try:
            count = int(data.get("number_of_episodes"))
        except (TypeError, ValueError):
            return None

        return count if count > 0 else None

    # ------------------------------------------------------------------
    # TV series title
    # ------------------------------------------------------------------

    async def get_tv_title(
        self,
        series_id,
        prefer_english: bool = False,
    ) -> str | None:
        """Return the TV series title, optionally preferring an English translation."""

        try:
            series_id = int(series_id)
        except (TypeError, ValueError):
            return None

        cache_key = (series_id, bool(prefer_english))
        if cache_key in self._tv_title_cache:
            return self._tv_title_cache[cache_key]

        data = await self._get_series_details(series_id)
        if not data:
            self._tv_title_cache[cache_key] = None
            return None

        title = data.get("name")
        original_title = data.get("original_name")

        if prefer_english:
            # Prefer an English translation that is actually different from
            # the original/Romaji title.
            translations = await self._get_json(
                f"{API_BASE}/tv/{series_id}/translations",
            )
            english_titles = []
            if translations:
                for translation in translations.get("translations") or []:
                    if translation.get("iso_639_1") != "en":
                        continue
                    translated_title = (
                        (translation.get("data") or {}).get("name")
                    )
                    if translated_title:
                        english_titles.append(
                            (
                                translation.get("iso_3166_1") == "US",
                                str(translated_title).strip(),
                            )
                        )

            for is_us, translated_title in sorted(
                english_titles,
                key=lambda item: not item[0],
            ):
                if (
                    translated_title
                    and translated_title.casefold()
                    != str(original_title or "").strip().casefold()
                ):
                    title = translated_title
                    break

            # TMDB's search endpoint searches original, translated and
            # also-known-as names. Query it even when the details endpoint's
            # current name differs from original_name: an anime can have a
            # Romaji/localized primary name while still having an English
            # search result for the same TMDB series.
            query = original_title or title
            if query:
                search = await self._get_json(
                    f"{API_BASE}/search/tv",
                    {
                        "query": query,
                        "language": "en-US",
                        "include_adult": "false",
                    },
                )
                results = search.get("results") if search else None
                matching = next(
                    (
                        result
                        for result in (results or [])
                        if result.get("id") == series_id
                    ),
                    None,
                )
                if matching and matching.get("name"):
                    title = str(matching["name"]).strip()

        result = str(title) if title else None
        self._tv_title_cache[cache_key] = result
        return result

    # ------------------------------------------------------------------
    # Movie title
    # ------------------------------------------------------------------

    async def get_movie_title(
        self,
        movie_id,
        prefer_english: bool = False,
    ) -> str | None:
        """Return the movie title, optionally preferring an English translation."""

        try:
            movie_id = int(movie_id)
        except (TypeError, ValueError):
            return None

        cache_key = (movie_id, bool(prefer_english))
        if cache_key in self._movie_title_cache:
            return self._movie_title_cache[cache_key]

        data = await self._get_json(
            f"{API_BASE}/movie/{movie_id}",
            {
                "language": "en-US",
            },
        )
        if not data:
            self._movie_title_cache[cache_key] = None
            return None

        title = data.get("title")
        original_title = data.get("original_title")

        if prefer_english:
            translations = await self._get_json(
                f"{API_BASE}/movie/{movie_id}/translations",
            )
            english_titles = []
            if translations:
                for translation in translations.get("translations") or []:
                    if translation.get("iso_639_1") != "en":
                        continue
                    translated_title = (
                        (translation.get("data") or {}).get("title")
                    )
                    if translated_title:
                        english_titles.append(
                            (
                                translation.get("iso_3166_1") == "US",
                                str(translated_title).strip(),
                            )
                        )

            for is_us, translated_title in sorted(
                english_titles,
                key=lambda item: not item[0],
            ):
                if (
                    translated_title
                    and translated_title.casefold()
                    != str(original_title or "").strip().casefold()
                ):
                    title = translated_title
                    break

            query = original_title or title
            if query:
                search = await self._get_json(
                    f"{API_BASE}/search/movie",
                    {
                        "query": query,
                        "language": "en-US",
                        "include_adult": "false",
                    },
                )
                results = search.get("results") if search else None
                matching = next(
                    (
                        result
                        for result in (results or [])
                        if result.get("id") == movie_id
                    ),
                    None,
                )
                if matching and matching.get("title"):
                    title = str(matching["title"]).strip()

        result = str(title) if title else None
        self._movie_title_cache[cache_key] = result
        return result

    # ------------------------------------------------------------------
    # TV backdrops
    # ------------------------------------------------------------------

    async def get_tv_backdrop(self, series_id) -> str | None:
        try:
            series_id = int(series_id)
        except (TypeError, ValueError):
            return None
        if series_id in self._tv_backdrop_cache:
            return self._tv_backdrop_cache[series_id]
        data = await self._get_json(
            f"{API_BASE}/tv/{series_id}/images",
            {"include_image_language": "en,null"},
        )
        if not data:
            self._tv_backdrop_cache[series_id] = None
            return None
        backdrops = data.get("backdrops") or []
        if not backdrops:
            self._tv_backdrop_cache[series_id] = None
            return None
        backdrops = sorted(
            backdrops,
            key=lambda image: (
                image.get("vote_average", 0),
                image.get("vote_count", 0),
            ),
            reverse=True,
        )
        path = backdrops[0].get("file_path")
        result = f"{IMAGE_BASE}{path}" if path else None
        self._tv_backdrop_cache[series_id] = result
        return result


    # ------------------------------------------------------------------
    # Title logos
    # ------------------------------------------------------------------

    async def _get_title_logo(
        self,
        path: str,
        cache: dict[int, str | None],
        item_id: int,
    ) -> str | None:
        """Return the highest-rated English/neutral transparent title logo."""

        if item_id in cache:
            return cache[item_id]

        data = await self._get_json(
            path,
            {
                "include_image_language": "en,null",
            },
        )

        if not data:
            cache[item_id] = None
            return None

        logos = data.get("logos") or []
        if not logos:
            cache[item_id] = None
            return None

        logos = sorted(
            logos,
            key=lambda image: (
                image.get("vote_average", 0),
                image.get("vote_count", 0),
            ),
            reverse=True,
        )

        logo_path = logos[0].get("file_path")
        result = f"https://image.tmdb.org/t/p/w500{logo_path}" if logo_path else None
        cache[item_id] = result
        return result

    async def get_movie_logo(self, movie_id) -> str | None:
        try:
            movie_id = int(movie_id)
        except (TypeError, ValueError):
            return None

        return await self._get_title_logo(
            f"{API_BASE}/movie/{movie_id}/images",
            self._movie_logo_cache,
            movie_id,
        )

    async def get_tv_logo(self, series_id) -> str | None:
        try:
            series_id = int(series_id)
        except (TypeError, ValueError):
            return None

        return await self._get_title_logo(
            f"{API_BASE}/tv/{series_id}/images",
            self._tv_logo_cache,
            series_id,
        )

    # ------------------------------------------------------------------
    # Recommendations
    # ------------------------------------------------------------------

    async def get_tv_recommendations(self, series_id, page=1) -> list[dict]:
        """Return TMDB recommendations for a TV series."""
        try:
            series_id=int(series_id)
            page=int(page)
        except (TypeError,ValueError):
            return []

        data=await self._get_json(
            f"{API_BASE}/tv/{series_id}/recommendations",
            {"language":"en-US","page":page},
        )
        results=(data or {}).get("results") if isinstance(data,dict) else None
        return results if isinstance(results,list) else []

    async def get_movie_recommendations(self, movie_id, page=1) -> list[dict]:
        """Return TMDB recommendations for a movie."""
        try:
            movie_id=int(movie_id)
            page=int(page)
        except (TypeError,ValueError):
            return []

        data=await self._get_json(
            f"{API_BASE}/movie/{movie_id}/recommendations",
            {"language":"en-US","page":page},
        )
        results=(data or {}).get("results") if isinstance(data,dict) else None
        return results if isinstance(results,list) else []

    async def get_tv_similar(self, series_id, page=1) -> list[dict]:
        """Return TMDB titles similar to a TV series."""
        try:
            series_id=int(series_id)
            page=int(page)
        except (TypeError,ValueError):
            return []

        data=await self._get_json(
            f"{API_BASE}/tv/{series_id}/similar",
            {"language":"en-US","page":page},
        )
        results=(data or {}).get("results") if isinstance(data,dict) else None
        return results if isinstance(results,list) else []

    async def get_movie_similar(self, movie_id, page=1) -> list[dict]:
        """Return TMDB movies similar to a movie."""
        try:
            movie_id=int(movie_id)
            page=int(page)
        except (TypeError,ValueError):
            return []

        data=await self._get_json(
            f"{API_BASE}/movie/{movie_id}/similar",
            {"language":"en-US","page":page},
        )
        results=(data or {}).get("results") if isinstance(data,dict) else None
        return results if isinstance(results,list) else []

    # ------------------------------------------------------------------
    # Movie search
    # ------------------------------------------------------------------

    async def find_movie_by_title(self, title: str) -> dict | None:
        """Find a TMDB movie by title using the English-localized search."""
        if not title:
            return None

        query = str(title).strip()
        if not query:
            return None

        data = await self._get_json(
            f"{API_BASE}/search/movie",
            {
                "query": query,
                "language": "en-US",
                "include_adult": "false",
            },
        )
        results = (data or {}).get("results") if isinstance(data, dict) else None
        if not results:
            return None

        result = results[0]
        movie_id = result.get("id")
        try:
            movie_id = int(movie_id)
        except (TypeError, ValueError):
            return None

        return {
            "id": movie_id,
            "title": result.get("title"),
            "original_title": result.get("original_title"),
        }

    # ------------------------------------------------------------------
    # Movies
    # ------------------------------------------------------------------

    async def get_movie_backdrop(
        self,
        movie_id,
    ) -> str | None:
        """
        Return the highest-rated TMDB movie backdrop.

        This intentionally uses a landscape backdrop rather than
        the portrait poster.
        """

        try:
            movie_id = int(movie_id)
        except (TypeError, ValueError):
            return None

        if movie_id in self._movie_backdrop_cache:
            return self._movie_backdrop_cache[movie_id]

        path = f"{API_BASE}/movie/{movie_id}/images"

        data = await self._get_json(
            path,
            {
                "include_image_language": "en,null",
            },
        )

        if not data:
            self._movie_backdrop_cache[movie_id] = None
            return None

        backdrops = data.get("backdrops") or []

        if not backdrops:
            self._movie_backdrop_cache[movie_id] = None
            return None

        backdrops = sorted(
            backdrops,
            key=lambda image: (
                image.get("vote_average", 0),
                image.get("vote_count", 0),
            ),
            reverse=True,
        )

        backdrop_path = backdrops[0].get(
            "file_path"
        )

        if not backdrop_path:
            self._movie_backdrop_cache[movie_id] = None
            return None

        backdrop_url = (
            f"{IMAGE_BASE}{backdrop_path}"
        )

        self._movie_backdrop_cache[movie_id] = (
            backdrop_url
        )

        return backdrop_url
