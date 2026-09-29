"""IMDb rating lookup using IMDb's public non-commercial ratings dataset.

The dataset is refreshed daily by IMDb and contains ratings for individual
titles, including TV episodes. We use it for episode-level IMDb ratings
because MDBList does not expose those ratings through its API.
"""

import asyncio
import gzip
import logging
import os
import sqlite3
import time
from pathlib import Path

import aiohttp


DATASET_URL = "https://datasets.imdbws.com/title.ratings.tsv.gz"
DATABASE_PATH = Path(os.getenv("IMDB_RATINGS_DB_PATH", "data/imdb_ratings.db"))
REFRESH_INTERVAL_SECONDS = 24 * 60 * 60
CACHE_TTL_SECONDS = 6 * 60 * 60
NEGATIVE_CACHE_TTL_SECONDS = 60 * 60

log = logging.getLogger("simkl-bot")


class ImdbClient:
    def __init__(self):
        self._session: aiohttp.ClientSession | None = None
        self._cache: dict[str, tuple[float, float | None]] = {}
        self._inflight: dict[str, asyncio.Task] = {}
        self._refresh_task: asyncio.Task | None = None
        self._db_lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=300),
                headers={
                    "User-Agent": "simkl-tracker-bot/1.0.0",
                    "Accept": "*/*",
                },
            )
        return self._session

    async def close(self) -> None:
        if self._refresh_task is not None and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass

        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    def _database_is_fresh(self) -> bool:
        try:
            return (
                DATABASE_PATH.exists()
                and time.time() - DATABASE_PATH.stat().st_mtime < REFRESH_INTERVAL_SECONDS
            )
        except OSError:
            return False

    async def start(self) -> None:
        """Start a background daily dataset refresh."""
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(self._refresh_if_needed())

    async def _refresh_if_needed(self) -> None:
        if self._database_is_fresh():
            return

        async with self._db_lock:
            if self._database_is_fresh():
                return
            try:
                await self._download_and_build_database()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Failed to refresh the IMDb ratings dataset.")

    async def _download_and_build_database(self) -> None:
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp_gz = DATABASE_PATH.with_suffix(".tsv.gz.tmp")
        temp_db = DATABASE_PATH.with_suffix(".db.tmp")

        log.info("Downloading IMDb ratings dataset...")
        session = await self._get_session()
        try:
            async with session.get(DATASET_URL) as resp:
                resp.raise_for_status()
                with temp_gz.open("wb") as output:
                    async for chunk in resp.content.iter_chunked(1024 * 1024):
                        output.write(chunk)
        except Exception:
            temp_gz.unlink(missing_ok=True)
            raise

        log.info("Building local IMDb ratings database...")
        try:
            if temp_db.exists():
                temp_db.unlink()

            await asyncio.to_thread(self._build_database, temp_gz, temp_db)
            os.replace(temp_db, DATABASE_PATH)
            log.info("IMDb ratings database refreshed successfully.")
        finally:
            temp_gz.unlink(missing_ok=True)
            temp_db.unlink(missing_ok=True)

    @staticmethod
    def _build_database(source_path: Path, database_path: Path) -> None:
        connection = sqlite3.connect(database_path)
        try:
            connection.execute("PRAGMA journal_mode=OFF")
            connection.execute("PRAGMA synchronous=OFF")
            connection.execute(
                "CREATE TABLE ratings (tconst TEXT PRIMARY KEY, rating REAL NOT NULL)"
            )

            batch: list[tuple[str, float]] = []
            with gzip.open(source_path, "rt", encoding="utf-8", newline="") as source:
                next(source, None)  # header
                for line in source:
                    tconst, average_rating, _num_votes = line.rstrip("\n").split("\t")
                    batch.append((tconst, float(average_rating)))
                    if len(batch) >= 10000:
                        connection.executemany(
                            "INSERT INTO ratings (tconst, rating) VALUES (?, ?)",
                            batch,
                        )
                        batch.clear()

                if batch:
                    connection.executemany(
                        "INSERT INTO ratings (tconst, rating) VALUES (?, ?)",
                        batch,
                    )

            connection.commit()
        finally:
            connection.close()

    async def _wait_for_database(self) -> None:
        if self._database_is_fresh():
            return

        await self._refresh_if_needed()

    @staticmethod
    def _lookup_rating(imdb_id: str) -> float | None:
        if not DATABASE_PATH.exists():
            return None

        connection = sqlite3.connect(DATABASE_PATH)
        try:
            row = connection.execute(
                "SELECT rating FROM ratings WHERE tconst = ?",
                (imdb_id,),
            ).fetchone()
            return float(row[0]) if row else None
        finally:
            connection.close()

    async def _fetch_rating(self, imdb_id: str) -> float | None:
        await self._wait_for_database()
        return await asyncio.to_thread(self._lookup_rating, imdb_id)

    async def get_rating(self, imdb_id: str | None) -> float | None:
        if not imdb_id or not str(imdb_id).startswith("tt"):
            return None

        imdb_id = str(imdb_id)
        now = time.monotonic()
        cached = self._cache.get(imdb_id)
        if cached is not None:
            expires_at, value = cached
            if now < expires_at:
                return value
            self._cache.pop(imdb_id, None)

        task = self._inflight.get(imdb_id)
        if task is None:
            task = asyncio.create_task(self._fetch_rating(imdb_id))
            self._inflight[imdb_id] = task

        try:
            value = await task
        finally:
            if self._inflight.get(imdb_id) is task:
                self._inflight.pop(imdb_id, None)

        ttl = CACHE_TTL_SECONDS if value is not None else NEGATIVE_CACHE_TTL_SECONDS
        self._cache[imdb_id] = (time.monotonic() + ttl, value)
        return value
