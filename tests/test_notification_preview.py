"""Debug previews must use real notification cards without changing progression."""

import asyncio
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

from discord import app_commands
from PIL import Image

os.environ.setdefault("DISCORD_BOT_TOKEN", "test-token")
os.environ.setdefault("SIMKL_CLIENT_ID", "test-client")
os.environ.setdefault("TMDB_API_KEY", "test-key")

import bot  # noqa: E402
import storage as storage_module  # noqa: E402
from achievements import ACHIEVEMENTS  # noqa: E402
from level_visuals import accent_for_level, accent_for_tier, prestige_style, render_achievement_gif, render_level_up_gif  # noqa: E402
from progression import RANKS, rank_for_level  # noqa: E402
from simkl_client import SimklClient  # noqa: E402


def test_full_history_requests_completed_and_dropped_episode_rows(monkeypatch):
    async def scenario():
        client=SimklClient("test-client")
        request=AsyncMock(return_value={"anime":[]})
        monkeypatch.setattr(client,"_get",request)
        assert await client.get_all_items("token","anime")==[]
        params=request.await_args.kwargs["params"]
        assert params["extended"]=="full_anime_seasons"
        assert params["episode_watched_at"]=="yes"
        assert params["include_all_episodes"]=="yes"
        assert params["language"]=="en"
    asyncio.run(scenario())


def _interaction():
    return SimpleNamespace(
        guild=SimpleNamespace(id=123),
        user=SimpleNamespace(
            id=42, mention="<@42>",
            guild_permissions=SimpleNamespace(manage_guild=True),
        ),
        channel=SimpleNamespace(id=1),
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def test_achievement_animation_renders_every_defined_name():
    for achievement in ACHIEVEMENTS.values():
        animation=render_achievement_gif(achievement["name"], achievement["xp"])
        image=Image.open(animation)
        assert image.size == (720, 280)
        assert image.n_frames > 1
        assert image.info["duration"] >= 60
        assert len(animation.getvalue()) < 8_000_000


def test_level_animation_uses_each_rank_color():
    accents=[accent_for_level(minimum) for minimum, _ in RANKS]
    assert len(set(accents)) == len(RANKS)
    assert all(accent_for_level(minimum + 1) == color for (minimum, _), color in zip(RANKS, accents))
    footer_colors=[]
    for minimum in (1, 10, 50, 90):
        level=max(2, minimum)
        animation=render_level_up_gif(
            level, rank_for_level(level), previous_level=level - 1,
            previous_rank=rank_for_level(level - 1),
        )
        image=Image.open(animation)
        image.seek(image.n_frames - 1)
        footer_colors.append(image.convert("RGB").getpixel((400, 247)))
    assert len(set(footer_colors)) == 4


def test_each_prestige_has_a_rotating_ten_rank_palette():
    for prestige in (1,2,3,6):
        accents=[accent_for_tier(minimum,prestige) for minimum,_ in RANKS]
        assert len(set(accents))==len(RANKS)
        for (minimum,_),color in zip(RANKS,accents):
            assert accent_for_tier(minimum+1,prestige)==color
    assert accent_for_tier(1,1)==(255,142,74)  # orange
    assert accent_for_tier(1,2)==(255,85,181)  # pink
    assert accent_for_tier(1,3)==(153,98,246) # purple
    assert accent_for_tier(40,1)!=accent_for_tier(40,2)
    assert accent_for_tier(40,1)==accent_for_tier(40,6)
    assert accent_for_tier(40,0)==accent_for_level(40)
    first=render_level_up_gif(40,rank_for_level(40),previous_level=39,previous_rank=rank_for_level(39),prestige=1)
    second=render_level_up_gif(40,rank_for_level(40),previous_level=39,previous_rank=rank_for_level(39),prestige=2)
    assert first.getvalue()!=second.getvalue()


def test_debug_previews_are_private_and_do_not_write(monkeypatch):
    async def scenario():
        command=bot.bot.tree.get_command("simkl-debug")
        assert command is not None
        assert bot.bot.tree.get_command("simkl-achievement-test") is None
        assert command.default_permissions.manage_guild
        forbidden=AsyncMock(side_effect=AssertionError("preview wrote to storage"))
        monkeypatch.setattr(bot.storage, "unlock_achievement", forbidden)
        monkeypatch.setattr(bot.storage, "award_achievement_xp", forbidden)
        monkeypatch.setattr(bot.storage, "flush", forbidden)
        monkeypatch.setattr(bot.storage, "get_progression", AsyncMock(return_value={"xp": 0}))

        achievement=_interaction()
        await command.callback(
            achievement, app_commands.Choice(name="Achievement unlocked", value="achievement"),
            "first_watch",
        )
        achievement.response.defer.assert_awaited_once_with(ephemeral=True)
        sent=achievement.followup.send.await_args.kwargs
        assert sent["ephemeral"] is True
        assert sent["embed"].image.url == "attachment://achievement.gif"
        assert sent["file"].filename == "achievement.gif"

        rank=_interaction()
        await command.callback(rank, app_commands.Choice(name="Rank up", value="rank"))
        rank.response.defer.assert_awaited_once_with(ephemeral=True)
        sent=rank.followup.send.await_args.kwargs
        assert sent["ephemeral"] is True
        assert sent["embed"].title == "Rank Up"
        assert sent["embed"].image.url == "attachment://level-up.gif"
        selected=_interaction()
        await command.callback(
            selected,app_commands.Choice(name="Level up",value="level"),
            level=43,rank=app_commands.Choice(name="Dedicated Viewer",value=40),prestige=6,
        )
        selected.response.defer.assert_awaited_once_with(ephemeral=True)
        sent=selected.followup.send.await_args.kwargs
        assert "Prestige **6**" in sent["embed"].description
        assert "Level 43" in sent["embed"].description
        assert sent["embed"].color.value == int.from_bytes(bytes(accent_for_tier(43,6)),"big")
        prestige=_interaction()
        await command.callback(prestige, app_commands.Choice(name="Prestige unlocked",value="prestige"), prestige=3)
        prestige.response.defer.assert_awaited_once_with(ephemeral=True)
        sent=prestige.followup.send.await_args.kwargs
        assert sent["ephemeral"] is True
        assert sent["embed"].image.url == "attachment://prestige.gif"
        assert "Prestige 3" in sent["embed"].title
        forbidden.assert_not_awaited()

    asyncio.run(scenario())


def test_achievement_notification_falls_back_to_embed(monkeypatch):
    async def scenario():
        monkeypatch.setattr(bot, "render_achievement_gif", lambda *args: (_ for _ in ()).throw(RuntimeError("render failed")))
        send=AsyncMock()
        assert await bot.send_achievement_notification(send, "<@42>", "first_watch", preview=True)
        send.assert_awaited_once()
        assert send.await_args.kwargs["embed"].image.url is None
        assert send.await_args.kwargs["ephemeral"] is True

    asyncio.run(scenario())


def test_simkl_reconciliation_updates_existing_stats_and_xp(monkeypatch):
    async def scenario():
        with tempfile.TemporaryDirectory() as directory:
            monkeypatch.setattr(storage_module,"DATA_PATH",f"{directory}/store.json")
            store=storage_module.Storage()
            monkeypatch.setattr(bot,"storage",store)
            await store.link_user("123","42","token",None,"tester","2026-09-20T00:00:00Z")
            for number in (1,2):
                key=f"series:shows:99:1:{number}"
                stamp=f"2026-09-2{number}T10:00:00Z"
                await store.record_watch("123","42","episode","Series",key,stamp)
                await store.award_watch_xp("42",f"episode:{key}:{stamp}","episode","Series",stamp,100)
            stats=store._data["guilds"]["123"]["users"]["42"]["statistics"]
            stats.pop("watch_events")  # Existing installations only had aggregate counts.

            def episode(number):
                return {"show":{"title":"Series","ids":{"simkl":99}},"seasons":[
                    {"number":1,"episodes":[{"number":number,"watched_at":f"2026-09-2{number}T10:00:00Z"}]}
                ]}

            current=[episode(2)]
            async def fetch(uid,user,token,media_type,**kwargs):
                return (current if media_type=="shows" else []),token
            monkeypatch.setattr(bot,"cached_simkl_items",fetch)
            await bot.reconcile_watch_progression("123","42",{},"token",{"shows"})
            assert (await store.get_statistics("123","42"))["episodes_watched"]==1
            assert (await store.get_progression("42"))["xp"]==100
            current.clear()
            await bot.reconcile_watch_progression("123","42",{},"token",{"shows"})
            assert (await store.get_statistics("123","42"))["episodes_watched"]==0
            assert (await store.get_progression("42"))["xp"]==0
    asyncio.run(scenario())


def test_anime_classification_is_reused_within_one_poll(monkeypatch):
    async def scenario():
        classify=AsyncMock(return_value=([{"show":{"title":"Series"}}],[]))
        monkeypatch.setattr(bot,"split_anime_items",classify)
        items=[{"show":{"title":"Series"}}]
        cache={}
        first=await bot.cached_split_anime_items("42",items,cache)
        second=await bot.cached_split_anime_items("42",items,cache)
        assert first is second
        classify.assert_awaited_once_with(items)
    asyncio.run(scenario())


def test_anime_movie_with_virtual_episode_uses_movie_activity_and_english_title(monkeypatch):
    async def scenario():
        with tempfile.TemporaryDirectory() as directory:
            monkeypatch.setattr(storage_module,"DATA_PATH",f"{directory}/store.json")
            store=storage_module.Storage()
            monkeypatch.setattr(bot,"storage",store)
            await store.link_user("123","42","token",None,"tester","2026-09-20T00:00:00Z")
            item={"anime_type":"movie","status":"completed","last_watched_at":"2026-09-26T10:00:00Z",
                  "show":{"title":"Romaji Title","ids":{"simkl":77,"tmdb":79707,"tvdb":1902}},
                  "seasons":[{"number":1,"episodes":[{"number":1,"watched_at":"2026-09-26T10:00:00Z"}]}]}
            shows,movies=await bot.split_anime_items([item])
            assert not shows and len(movies)==1
            assert movies[0]["movie"]["type"]=="movie"
            assert movies[0]["seasons"]==item["seasons"]
            monkeypatch.setattr(bot,"prefs",AsyncMock(return_value={
                "episode_code":False,"show_imdb":False,"show_mal":False,
                "activity_text":"detailed","artwork":"poster"}))
            finder=AsyncMock(return_value={"id":42,"title":"English Movie Title"})
            monkeypatch.setattr(bot.tmdb,"find_movie_by_title",finder)
            monkeypatch.setattr(bot.tmdb,"get_movie_backdrop",AsyncMock(return_value=None))
            desc=[]
            monkeypatch.setattr(bot,"build_embed",lambda t,d,*args:desc.append((t,d)) or object())
            monkeypatch.setattr(bot,"send_embed",AsyncMock(return_value=True))
            monkeypatch.setattr(bot,"evaluate_achievements",AsyncMock(return_value=[]))
            count,ok=await bot.process_movies(SimpleNamespace(),"123","42","Tester",SimpleNamespace(),
                                              movies,bot.parse_iso("2026-09-20T00:00:00Z"),None)
            assert (count,ok)==(1,True)
            assert desc==[("movies","Watched a movie **English Movie Title**")]
            assert (await store.get_statistics("123","42"))["anime_movies_watched"]==1
            assert (await store.get_statistics("123","42"))["anime_episodes_watched"]==0
            finder.assert_awaited_once_with("Romaji Title")
    asyncio.run(scenario())


def test_episode_range_records_stats_xp_and_challenges_as_one_group(monkeypatch):
    async def scenario():
        with tempfile.TemporaryDirectory() as directory:
            monkeypatch.setattr(storage_module,"DATA_PATH",f"{directory}/store.json")
            store=storage_module.Storage()
            monkeypatch.setattr(bot,"storage",store)
            await store.link_user("123","42","token",None,"tester","2026-09-20T00:00:00Z")
            monkeypatch.setattr(bot,"prefs",AsyncMock(return_value={
                "episode_code":False,"show_imdb":False,"activity_text":"short","artwork":"poster",
            }))
            monkeypatch.setattr(bot,"episode_media",AsyncMock(return_value=(None,None,None,45)))
            monkeypatch.setattr(bot,"build_embed",lambda *args,**kwargs:object())
            monkeypatch.setattr(bot,"send_embed",AsyncMock(return_value=True))
            monkeypatch.setattr(bot,"evaluate_achievements",AsyncMock(return_value=[]))
            item={"show":{"title":"Show","ids":{"simkl":1}},"seasons":[{
                "number":1,"episodes":[{"number":n,"watched_at":"2026-09-26T10:00:00Z"}
                                        for n in range(1,6)],
            }]}
            count,ok=await bot.process_shows(SimpleNamespace(),123,"42","Tester",SimpleNamespace(),
                                              "shows",[item],None)
            assert (count,ok)==(5,True)
            assert (await store.get_statistics("123","42"))["episodes_watched"]==5
            progression=await store.get_progression("42")
            assert sum(event["amount"] for event in progression["xp_events"])==550
            assert progression["challenge_completions"]
    asyncio.run(scenario())


def test_fresh_server_backfills_without_activity_channel(monkeypatch):
    async def scenario():
        with tempfile.TemporaryDirectory() as directory:
            monkeypatch.setattr(storage_module,"DATA_PATH",f"{directory}/store.json")
            store=storage_module.Storage()
            monkeypatch.setattr(bot,"storage",store)
            for uid in ("41","42","43"):
                await store.link_user("123",uid,"token",None,uid,"2026-09-26T10:00:00Z",
                                      simkl_account_id=123)
            targets=await store.get_poll_targets("123")
            assert len(targets)==3 and all(row["channel_id"] is None for row in targets)
            monkeypatch.setattr(bot,"valid_token",AsyncMock(return_value="token"))
            monkeypatch.setattr(bot,"cached_simkl_activities",AsyncMock(return_value=({},"token")))
            async def history(uid,user,token,media_type,**kwargs):
                if media_type!="shows":
                    return [],token
                return [{"show":{"title":"Series","ids":{"simkl":99}},"seasons":[{
                    "number":1,"episodes":[{"number":n,"watched_at":"2026-09-25T10:00:00Z"}
                                            for n in range(1,11)],
                }]}],token
            monkeypatch.setattr(bot,"cached_simkl_items",history)
            assert await bot.poll_all("123")==0
            rows=await store.get_guild_statistics("123")
            assert len(rows)==3
            assert all(row["statistics"]["episodes_watched"]==10 for row in rows)
            leaderboard=await store.get_guild_leaderboard_snapshot("123")
            assert all(row["episodes"]==10 and row["xp"]>0 for row in leaderboard)
            await store.link_user("123","44","token",None,"44","2026-09-26T10:00:00Z",
                                  simkl_account_id=123)
            fail_once=True
            async def transient_history(uid,user,token,media_type,**kwargs):
                nonlocal fail_once
                if uid=="44" and fail_once:
                    fail_once=False
                    raise RuntimeError("temporary history fetch failure")
                return await history(uid,user,token,media_type,**kwargs)
            monkeypatch.setattr(bot,"cached_simkl_items",transient_history)
            await bot.poll_all("123")
            assert not (await store.get_history_import_state("123","44"))["complete"]
            await bot.poll_all("123")
            assert (await store.get_history_import_state("123","44"))["complete"]
            assert (await store.get_statistics("123","44"))["episodes_watched"]==10
    asyncio.run(scenario())


def test_existing_link_with_empty_legacy_stats_repairs_without_relink(monkeypatch):
    async def scenario():
        with tempfile.TemporaryDirectory() as directory:
            monkeypatch.setattr(storage_module,"DATA_PATH",f"{directory}/store.json")
            store=storage_module.Storage()
            monkeypatch.setattr(bot,"storage",store)
            await store.link_user("123","42","token",None,"tester","2026-09-20T00:00:00Z",simkl_account_id=123)
            old=store._data["guilds"]["123"]["users"]["42"]
            old["history_seeded"]=True
            old["last_checked"]={t:"2026-09-26T00:00:00Z" for t in bot.MEDIA_TYPES}
            old["announced"].add("already-posted")
            monkeypatch.setattr(bot,"valid_token",AsyncMock(return_value="token"))
            monkeypatch.setattr(bot,"cached_simkl_activities",AsyncMock(return_value=({},"token")))
            monkeypatch.setattr(bot,"evaluate_achievements",AsyncMock(return_value=[]))
            async def history(uid,user,token,media_type,**kwargs):
                if media_type!="shows": return [],token
                return [{"show":{"title":"Series","ids":{"simkl":99}},"seasons":[{
                    "number":1,"episodes":[{"number":1,"watched_at":"2024-09-25T10:00:00Z"}]
                }]}],token
            fetch=AsyncMock(side_effect=history)
            monkeypatch.setattr(bot,"cached_simkl_items",fetch)
            await bot.poll_all("123")
            assert (await store.get_statistics("123","42"))["episodes_watched"]==1
            assert (await store.get_progression("42"))["xp"]>=100
            assert "already-posted" in store._data["guilds"]["123"]["users"]["42"]["announced"]
            assert (await store.get_history_import_state("123","42"))["complete"]
            calls=fetch.await_count
            await bot.poll_all("123")
            assert fetch.await_count <= calls+len(bot.MEDIA_TYPES)
            assert (await store.get_statistics("123","42"))["episodes_watched"]==1
    asyncio.run(scenario())


def test_full_snapshot_fills_partial_history_without_duplicate_rewatch(monkeypatch):
    async def scenario():
        with tempfile.TemporaryDirectory() as directory:
            monkeypatch.setattr(storage_module,"DATA_PATH",f"{directory}/store.json")
            store=storage_module.Storage()
            monkeypatch.setattr(bot,"storage",store)
            await store.link_user("123","42","token",None,"tester","2026-09-20T00:00:00Z",simkl_account_id=123)
            first="series:shows:99:1:1"
            old_stamp="2026-09-20T10:00:00Z"
            await store.record_watch("123","42","episode","Series",first,old_stamp)
            await store.award_watch_xp("42",f"episode:{first}:{old_stamp}","episode","Series",old_stamp,100)
            user=store._data["users"]["42"]
            user["progression"]["history_xp_seeded"]=True
            guild_user=store._data["guilds"]["123"]["users"]["42"]
            guild_user["history_seeded"]=True
            guild_user["history_stats_repaired"]=True
            async def history(uid,user,token,media_type,**kwargs):
                if media_type!="shows": return [],token
                return [{"show":{"title":"Series","ids":{"simkl":99}},"seasons":[{
                    "number":1,"episodes":[
                        {"number":1,"watched_at":"2026-09-25T10:00:00Z"},
                        {"number":2,"watched_at":"2026-09-25T10:00:00Z"},
                    ]}]}],token
            monkeypatch.setattr(bot,"cached_simkl_items",AsyncMock(side_effect=history))
            await bot.reconcile_watch_progression("123","42",{},"token",set(bot.MEDIA_TYPES))
            assert (await store.get_statistics("123","42"))["episodes_watched"]==2
            assert (await store.get_progression("42"))["xp"]==200
            await bot.reconcile_watch_progression("123","42",{},"token",set(bot.MEDIA_TYPES))
            assert (await store.get_statistics("123","42"))["episodes_watched"]==2
            assert (await store.get_progression("42"))["xp"]==200
    asyncio.run(scenario())


def test_consolidated_xp_leaderboard_orders_prestige_then_xp(monkeypatch):
    async def scenario():
        rows=[
            {"discord_user_id":"1","simkl_username":"One","xp":900,"prestige":0,"episodes":10,"movies":0,"anime":0},
            {"discord_user_id":"2","simkl_username":"Two","xp":100,"prestige":1,"episodes":5,"movies":0,"anime":0},
            {"discord_user_id":"3","simkl_username":"Three","xp":500,"prestige":1,"episodes":8,"movies":0,"anime":0},
        ]
        monkeypatch.setattr(bot.storage,"get_guild_leaderboard_snapshot",AsyncMock(return_value=rows))
        captured=[]
        def render(guild,category,values):
            captured.extend(row["discord_user_id"] for row in values)
            from io import BytesIO
            return BytesIO(b"preview")
        monkeypatch.setattr(bot,"render_leaderboard_png",render)
        interaction=_interaction()
        interaction.guild.name="Server"
        interaction.guild.get_member=lambda uid: None
        command=bot.bot.tree.get_command("simkl-leaderboard")
        await command.callback(interaction,app_commands.Choice(name="XP / progression",value="xp"))
        assert captured==["3","2","1"]
        interaction.response.defer.assert_awaited_once()
        assert interaction.followup.send.await_args.kwargs["file"].filename=="leaderboard.png"
        assert interaction.followup.send.await_args.kwargs["embed"].image.url=="attachment://leaderboard.png"
        assert bot.bot.tree.get_command("simkl-xp-leaderboard") is None
        assert bot.bot.tree.get_command("simkl-xp") is None
        assert bot.bot.tree.get_command("simkl-profile") is None
        assert bot.bot.tree.get_command("simkl-streak") is None
        assert bot.bot.tree.get_command("simkl-community") is not None
    asyncio.run(scenario())
