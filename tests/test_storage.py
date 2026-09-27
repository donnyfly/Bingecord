import asyncio
import json

import storage as storage_module


def test_removed_watch_reconciles_profile_server_totals_titles_and_streak(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_module,"DATA_PATH",str(tmp_path / "store.json"))

    async def scenario():
        store=storage_module.Storage()
        await store.link_user("123","42","token",None,"tester","2026-09-20T00:00:00Z")
        await store.record_watch("123","42","episode","One Piece","series:shows:99:1:1","2026-09-24T10:00:00Z",genres=["Adventure"])
        await store.record_watch("123","42","episode","One Piece","series:shows:99:1:2","2026-09-25T10:00:00Z",genres=["Adventure"])
        await store.record_watch("123","42","anime_movie","Film","movies:88","2026-09-26T10:00:00Z")
        # Repeated processing of the same SIMKL watch is idempotent.
        await store.record_watch("123","42","episode","One Piece","series:shows:99:1:2","2026-09-25T10:00:00Z")
        assert (await store.get_statistics("123","42"))["episodes_watched"]==2

        await store.reconcile_watch_statistics("123","42",{"episode:series:shows:99:1:2:"},{"episode"})
        stats=await store.get_statistics("123","42")
        assert (stats["episodes_watched"],stats["movies_watched"],stats["anime_movies_watched"])==(1,1,1)
        assert "series:shows:99:1:1" not in stats["titles"]
        assert "2026-09-24" not in stats["watch_dates"]
        assert sum(row["statistics"]["episodes_watched"] for row in await store.get_guild_statistics("123"))==1
        assert (await store.get_guild_leaderboard_snapshot("123"))[0]["episodes"]==1
        reloaded=storage_module.Storage()
        assert (await reloaded.get_statistics("123","42"))["episodes_watched"]==1
        await store.reconcile_watch_statistics("123","42",{"episode:series:shows:99:1:2:"},{"episode"})
        assert (await store.get_statistics("123","42"))["episodes_watched"]==1
    asyncio.run(scenario())


def test_legacy_statistics_bootstrap_from_current_history(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_module,"DATA_PATH",str(tmp_path / "store.json"))

    async def scenario():
        store=storage_module.Storage()
        await store.link_user("123","42","token",None,"tester","2026-09-20T00:00:00Z")
        stats=store._data["guilds"]["123"]["users"]["42"]["statistics"]
        stats.pop("watch_events")
        stats["episodes_watched"]=99
        stats["titles"]={"series:shows:99:1:2":{"title":"One Piece","type":"episode","count":99,"genres":["Adventure"]}}
        assert await store.needs_watch_statistics_rebuild("123","42")
        current={"media_type":"episode","item_key":"series:shows:99:1:2",
                 "title":"OP","watched_at":"2026-09-25T10:00:00Z"}
        await store.reconcile_watch_statistics("123","42",{"episode:series:shows:99:1:2:"},{"episode"},[current],full_snapshot=True)
        result=await store.get_statistics("123","42")
        assert result["episodes_watched"]==1
        assert result["titles"]["series:shows:99:1:2"]["title"]=="One Piece"
        assert result["titles"]["series:shows:99:1:2"]["genres"]==["Adventure"]
        assert not await store.needs_watch_statistics_rebuild("123","42")
    asyncio.run(scenario())


def test_poll_health_is_persisted_and_loaded(tmp_path, monkeypatch):
    data_path = tmp_path / "store.json"
    monkeypatch.setattr(storage_module, "DATA_PATH", str(data_path))

    async def scenario():
        store = storage_module.Storage()
        store._data["users"]["42"] = {
            "simkl_token": "token",
            "simkl_username": "tester",
        }
        store._guild("123", create=True)["users"]["42"] = storage_module._default_guild_user()
        await store.update_poll_health(
            "123",
            "42",
            last_poll_at="2026-09-23T10:00:00Z",
            last_success_at="2026-09-23T10:00:05Z",
            last_error="",
        )

        with data_path.open("r", encoding="utf-8") as f:
            saved = json.load(f)
        saved_user = saved["guilds"]["123"]["users"]["42"]
        assert saved_user["last_poll_at"] == "2026-09-23T10:00:00Z"
        assert saved_user["last_success_at"] == "2026-09-23T10:00:05Z"
        assert saved_user["last_error"] == ""

        reloaded = storage_module.Storage()
        loaded_user = reloaded._guild_user("123", "42")
        assert loaded_user["last_poll_at"] == "2026-09-23T10:00:00Z"
        assert loaded_user["last_success_at"] == "2026-09-23T10:00:05Z"
        assert loaded_user["last_error"] == ""

    asyncio.run(scenario())


def test_existing_guild_user_gets_new_health_defaults(tmp_path, monkeypatch):
    data_path = tmp_path / "store.json"
    data_path.write_text(
        json.dumps(
            {
                "poll_interval_minutes": 60,
                "users": {},
                "guilds": {
                    "123": {
                        "channel_id": 456,
                        "embed_preferences": {},
                        "users": {
                            "42": {
                                "history_seeded": True,
                                "last_checked": {},
                                "announced": [],
                                "activity_state": {},
                            }
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(storage_module, "DATA_PATH", str(data_path))

    store = storage_module.Storage()
    user = store._guild_user("123", "42")
    assert user["last_poll_at"] is None
    assert user["last_success_at"] is None
    assert user["last_error"] is None


def test_failed_flush_keeps_previous_file(tmp_path, monkeypatch):
    data_path = tmp_path / "store.json"
    data_path.write_text(
        json.dumps(
            {
                "poll_interval_minutes": 60,
                "users": {},
                "guilds": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(storage_module, "DATA_PATH", str(data_path))

    store = storage_module.Storage()
    store._data["users"]["42"] = {"simkl_username": "before"}
    store._dirty = True

    def fail_write(_text):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(storage_module, "_write_to_disk", fail_write)

    async def scenario():
        try:
            await store.flush()
        except OSError:
            pass
        else:
            raise AssertionError("flush should propagate the write failure")

    asyncio.run(scenario())

    saved = json.loads(data_path.read_text(encoding="utf-8"))
    assert saved["users"] == {}
    assert store._dirty is True


def test_poll_health_consecutive_failures_round_trip(tmp_path, monkeypatch):
    data_path = tmp_path / "store.json"
    monkeypatch.setattr(storage_module, "DATA_PATH", str(data_path))

    async def scenario():
        store = storage_module.Storage()
        store._data["users"]["42"] = {"simkl_token": "token", "simkl_username": "tester"}
        store._guild("123", create=True)["users"]["42"] = storage_module._default_guild_user()
        await store.update_poll_health("123", "42", last_error="temporary failure", consecutive_failures=3)
        user = store._guild_user("123", "42")
        assert user["consecutive_failures"] == 3
        assert user["last_error"] == "temporary failure"
        await store.update_poll_health("123", "42", last_success_at="2026-09-24T10:00:00Z", last_error="", consecutive_failures=0)
        reloaded = storage_module.Storage()
        loaded_user = reloaded._guild_user("123", "42")
        assert loaded_user["consecutive_failures"] == 0
        assert loaded_user["last_success_at"] == "2026-09-24T10:00:00Z"
        assert loaded_user["last_error"] == ""
    asyncio.run(scenario())


def test_legacy_poll_health_defaults_include_consecutive_failures(tmp_path, monkeypatch):
    data_path = tmp_path / "store.json"
    data_path.write_text(json.dumps({"poll_interval_minutes": 60, "users": {}, "guilds": {"123": {"channel_id": 456, "embed_preferences": {}, "users": {"42": {"history_seeded": True, "last_checked": {}, "announced": [], "activity_state": {}, "last_poll_at": None, "last_success_at": None, "last_error": None}}}}}), encoding="utf-8")
    monkeypatch.setattr(storage_module, "DATA_PATH", str(data_path))
    store = storage_module.Storage()
    user = store._guild_user("123", "42")
    assert user["consecutive_failures"] == 0

def test_reset_user_tracking_preserves_link_and_resets_server_state(tmp_path, monkeypatch):
    data_path = tmp_path / "store.json"
    monkeypatch.setattr(storage_module, "DATA_PATH", str(data_path))

    async def scenario():
        store = storage_module.Storage()
        store._data["users"]["42"] = {
            "simkl_token": "token",
            "refresh_token": "refresh",
            "simkl_username": "tester",
            "simkl_account_id": 123,
            "embed_preferences": {"style": "minimal"},
            "embed_preferences_custom": True,
        }
        guild_user = storage_module._default_guild_user("2026-09-25T00:00:00Z")
        guild_user["history_seeded"] = True
        guild_user["announced"] = {"movie:1"}
        guild_user["activity_state"]["statuses"]["movie:1"] = "completed"
        guild_user["statistics"]["movies_watched"] = 10
        guild_user["achievements"]["movies_25"] = {"unlocked_at": "2026-09-25T00:00:00Z"}
        guild_user["last_poll_at"] = "2026-09-25T01:00:00Z"
        store._guild("123", create=True)["users"]["42"] = guild_user
        store._dirty = True
        await store.flush()

        reset_at = "2026-09-26T00:00:00Z"
        assert await store.reset_user_tracking("123", "42", reset_at)

        global_user = await store.get_user("42")
        assert global_user["simkl_token"] == "token"
        assert global_user["refresh_token"] == "refresh"
        assert global_user["simkl_username"] == "tester"
        assert global_user["simkl_account_id"] == 123
        assert global_user["embed_preferences"]["style"] == "minimal"
        assert global_user["embed_preferences_custom"] is True

        reset_user = store._guild_user("123", "42")
        assert reset_user["history_seeded"] is False
        assert reset_user["last_checked"] == {
            "shows": reset_at,
            "movies": reset_at,
            "anime": reset_at,
        }
        assert reset_user["announced"] == set()
        assert reset_user["activity_state"]["statuses"] == {}
        assert reset_user["activity_state"]["watch_times"] == {}
        assert reset_user["statistics"]["movies_watched"] == 0
        assert reset_user["statistics"]["episodes_watched"] == 0
        assert reset_user["achievements"] == {}
        assert reset_user["last_poll_at"] is None
        assert reset_user["last_success_at"] is None
        assert reset_user["last_error"] is None
        assert reset_user["consecutive_failures"] == 0

    asyncio.run(scenario())


def test_achievement_xp_is_idempotent(tmp_path, monkeypatch):
    data_path = tmp_path / "store.json"
    monkeypatch.setattr(storage_module, "DATA_PATH", str(data_path))

    async def scenario():
        store = storage_module.Storage()
        await store.link_user(
            "123", "42", "token", "refresh", "tester",
            "2026-09-26T00:00:00Z", token_expires_at=None, simkl_account_id=123,
        )
        first = await store.award_achievement_xp(
            "42", "episodes_50", 250, "Seasoned Watcher", "2026-09-26T10:00:00Z"
        )
        second = await store.award_achievement_xp(
            "42", "episodes_50", 250, "Seasoned Watcher", "2026-09-26T10:00:00Z"
        )
        progression = await store.get_progression("42")
        assert first["awarded"] is True
        assert second["awarded"] is False
        assert progression["xp"] == 250
        assert progression["lifetime_xp"] == 250
        assert progression["achievement_xp_awarded"]["episodes_50"]
        assert len(progression["xp_events"]) == 1

    asyncio.run(scenario())
