import asyncio
from datetime import datetime, timedelta, timezone

from trackerbot.core import storage as storage_module
from trackerbot.core.personal_challenges import snapshot


def test_manga_baseline_live_rewards_and_relink(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_module, "DATA_PATH", str(tmp_path / "state.json"))
    store = storage_module.Storage()
    start = datetime(2026, 11, 23, 0, tzinfo=timezone.utc)
    imported = {"10": [1, "CURRENT", 200, 0, False], "11": [2, "PLANNING", 0, 0, False],
                "12": [3, "COMPLETED", 400, 0, True]}

    async def link(guild):
        await store.link_provider_account(guild, "2", {"access_token": "token"},
                                          {"id": 42, "username": "Reader"}, provider="anilist")
        await store.set_anilist_manga(guild, "2", True)

    async def observe(guild, manga, at, *, seed=False):
        assert await store.record_reading(guild, "2", "token", manga, at)
        await store.anilist_snapshot(guild, "2", "token", "MANGA", replace=manga, seed=seed)

    async def run():
        for guild in ("1", "3"):
            await link(guild)
            await observe(guild, imported, start, seed=True)
        stats = await store.get_reading_statistics("1", "2")
        assert stats["chapters"] == 200
        assert stats["statuses"]["PLANNING"] == 1
        assert not stats["completed"]
        assert (await store.get_progression("2"))["lifetime_xp"] == 350
        assert await store.get_guild_reading_summary("1", start, start + timedelta(hours=1)) == {
            "chapters": 0, "started": 0, "statuses": {},
        }

        update = {**imported, "10": [1, "CURRENT", 212, 0, False], "11": [2, "CURRENT", 3, 0, False]}
        for guild in ("1", "3"):
            await observe(guild, update, start + timedelta(hours=1))
        assert (await store.get_progression("2"))["lifetime_xp"] == 350 + 250 + 1000 + 750
        state = await store.get_challenge_state("2", reading_enabled=True)
        cards = snapshot(state, start + timedelta(hours=1))["sections"]
        assert [(row["value"], row["done"]) for card in cards if card.get("reading") for row in card["rows"]] == [
            (15, True), (15, True), (2, True),
        ]

        corrected = {**update, "10": [1, "PAUSED", 210, 0, False]}
        await observe("1", corrected, start + timedelta(hours=2))
        await observe("1", update, start + timedelta(hours=3))
        assert await store.get_guild_reading_summary("1", start, start + timedelta(days=1)) == {
            "chapters": 15, "started": 1, "statuses": {"PAUSED": 1, "CURRENT": 1},
        }
        await store.unlink_provider_account("1", "2", provider="anilist")
        await link("1")
        imported_later = {**update, "10": [1, "CURRENT", 260, 0, False]}
        await observe("1", imported_later, start + timedelta(hours=4), seed=True)
        assert (await store.get_reading_statistics("1", "2"))["chapters"] == 263
        assert (await store.get_guild_statistics("1"))[0]["reading"]["chapters"] == 263
        assert (await store.get_guild_leaderboard_snapshot("1"))[0]["periods"]["all"]["manga"] == 263
        assert (await store.get_guild_reading_summary("1", start, start + timedelta(days=1)))["chapters"] == 15

        end = start + timedelta(days=7)
        week = start.date().isoformat()
        goal = await store.get_reading_community("1", week, start, end, start)
        assert goal["chapters"] == 15
        bulk = {**imported_later, "10": [1, "COMPLETED", 445, 0, False]}
        await observe("1", bulk, start + timedelta(hours=5))
        goal = await store.get_reading_community("1", week, start, end, end)
        assert goal["chapters"] == goal["target"] == 200
        assert goal["reached"]
        await store.get_reading_community("1", week, start, end, end)
        assert (await store.get_progression("2"))["community_rewards"] == {"1:reading:2026-11-23": 350}

    asyncio.run(run())


def test_rereading_counts_new_pass_without_counting_status_only_changes():
    from trackerbot.core.reading import observe

    now = datetime(2026, 11, 23, tzinfo=timezone.utc)
    completed = {"10": [1, "COMPLETED", 100, 0, False]}
    repeating = {"10": [1, "REPEATING", 100, 0, False]}
    reset = {"10": [1, "REPEATING", 2, 0, False]}
    ceilings = {}
    assert not list(observe(completed, {}, ceilings, "42", now, seed=True))
    assert [row["chapters"] for row in observe(repeating, completed, ceilings, "42", now)] == [0]
    assert not list(observe(repeating, repeating, ceilings, "42", now))
    assert [row["chapters"] for row in observe(reset, repeating, ceilings, "42", now)] == [2]
    finished = {"10": [1, "COMPLETED", 100, 1, False]}
    assert [row["chapters"] for row in observe(finished, reset, ceilings, "42", now)] == [98]
