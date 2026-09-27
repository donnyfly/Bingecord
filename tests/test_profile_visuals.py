import asyncio
from datetime import date
from io import BytesIO
import tempfile

from PIL import Image, ImageDraw, ImageFont

import storage as storage_module
from level_visuals import accent_for_level, accent_for_tier, prestige_style, render_prestige_gif
from profile_visuals import _short, profile_snapshot, render_profile_png, render_leaderboard_png, render_summary_png
from progression import xp_for_level


def test_profile_recomputes_watch_and_genre_totals_each_view():
    stats={
        "episodes_watched":5,"movies_watched":1,
        "anime_episodes_watched":2,"anime_movies_watched":0,
        "watch_dates":{"2026-09-26":{"total":6}},
        "titles":{"one":{"title":"Example","count":5,"last_watched":"2026-09-26T03:00:00Z","genres":["Drama"]}},
    }
    progression={"xp":xp_for_level(10),"prestige":1,"lifetime_xp":100000,"xp_events":[],"challenge_completions":{}}
    first=profile_snapshot(stats,progression,{},1,3,today=date(2026,9,26))
    assert (first["level"],first["total"],first["top_genres"]) == (10,6,[("Drama",5)])
    first_image=render_profile_png("Viewer",first)
    assert Image.open(first_image).size==(1080,1065)
    stats["episodes_watched"]+=1
    stats["titles"]["one"]["count"]+=1
    second=profile_snapshot(stats,progression,{"first_watch":{}},1,3,today=date(2026,9,26))
    assert second["total"]==7
    assert second["achievements"]==1
    assert second["top_genres"]==[("Drama",6)]
    assert first_image.getvalue()!=render_profile_png("Viewer",second).getvalue()


def test_prestige_profile_uses_rank_accent_and_distinct_backdrop():
    stats={"episodes_watched":1,"movies_watched":0,"titles":{},"watch_dates":{}}
    cards=[]
    for prestige in (1,2,3,6):
        snapshot=profile_snapshot(stats,{"xp":xp_for_level(43),"prestige":prestige}, {},0,0,today=date(2026,9,26))
        card=Image.open(render_profile_png("Viewer",snapshot)).convert("RGB")
        assert card.getpixel((250,117))==accent_for_tier(43,prestige)
        cards.append(card.tobytes())
    assert len(set(cards))==4


def test_empty_genre_state_and_leaderboard_card():
    stats={"episodes_watched":1,"movies_watched":0,"titles":{},"watch_dates":{}}
    snapshot=profile_snapshot(stats,{"xp":0}, {},0,0,today=date(2026,9,26))
    assert snapshot["top_genres"]==[]
    board=render_leaderboard_png("Server","XP progression",[
        {"name":"Viewer","prestige":1,"level":9,"xp":500,"total":5},
    ])
    assert Image.open(board).size==(1080,845)
    assert _short(ImageDraw.Draw(Image.new("RGB",(100,100))),0,ImageFont.load_default(),80)=="0"
    for heading in ("weekly recap","server statistics"):
        card=render_summary_png("Server",heading,"This week",[("Episodes",32),("Movies",4)],[("Top watcher","Viewer · 12 watches")])
        assert Image.open(card).size==(1080,735)


def test_prestige_emblems_and_accents_vary():
    styles=[prestige_style(n) for n in range(1,13)]
    assert len(set(color for color,_ in styles))==12
    assert len(set(icon for _,icon in styles))==6
    assert prestige_style(1)[0] != accent_for_level(90)
    assert min(prestige_style(1)[0]) > min(accent_for_level(1))
    for number in (1,2,3,6):
        gif=Image.open(render_prestige_gif(number))
        assert gif.size==(720,280)
        assert gif.n_frames>1
    assert Image.open(render_prestige_gif(1000)).size==(720,280)


def test_long_profile_name_and_wide_prestige_render():
    stats={"episodes_watched":0,"movies_watched":0,"titles":{},"watch_dates":{}}
    snapshot=profile_snapshot(stats,{"xp":0,"prestige":1000}, {},0,0,today=date(2026,9,26))
    card=Image.open(render_profile_png("A Very Long Discord Display Name That Should Fit Properly",snapshot))
    assert card.size==(1080,1065)


def test_prestige_rollover_is_automatic_and_atomic():
    async def scenario():
        with tempfile.TemporaryDirectory() as directory:
            previous=storage_module.DATA_PATH
            storage_module.DATA_PATH=f"{directory}/store.json"
            try:
                store=storage_module.Storage()
                await store.link_user("123","42","token",None,"viewer","2026-09-26T00:00:00Z")
                threshold=xp_for_level(100)
                await store.award_watch_xp("42","seed","episode","Example","2026-09-26T00:00:00Z",2*threshold+123)
                state=await store.get_progression("42")
                assert state["prestige"]==2 and state["xp"]==123
                assert state["lifetime_xp"]==2*threshold+123
                assert await store.claim_prestige_notifications("42")==[1,2]
                assert await store.claim_prestige_notifications("42")==[]
            finally:
                storage_module.DATA_PATH=previous
    asyncio.run(scenario())


def test_existing_level_100_xp_rolls_over_without_relinking():
    async def scenario():
        with tempfile.TemporaryDirectory() as directory:
            previous=storage_module.DATA_PATH
            storage_module.DATA_PATH=f"{directory}/store.json"
            try:
                store=storage_module.Storage()
                await store.link_user("123","42","token",None,"viewer","2026-09-26T00:00:00Z")
                await store.get_progression("42")
                progression=store._data["users"]["42"]["progression"]
                progression["xp"]=xp_for_level(100)+567
                progression["lifetime_xp"]=xp_for_level(100)+567
                assert (await store.get_progression("42"))["xp"]==567
                assert await store.claim_prestige_notifications("42")==[1]
                await store.flush()
                restored=storage_module.Storage()
                assert (await restored.get_progression("42"))["prestige"]==1
                assert await restored.claim_prestige_notifications("42")==[]
            finally:
                storage_module.DATA_PATH=previous
    asyncio.run(scenario())


def test_manual_prestige_restores_only_missing_carry_once():
    async def scenario():
        with tempfile.TemporaryDirectory() as directory:
            previous=storage_module.DATA_PATH
            storage_module.DATA_PATH=f"{directory}/store.json"
            try:
                store=storage_module.Storage()
                await store.link_user("123","donny","token",None,"donny","2026-09-26T00:00:00Z")
                await store.link_user("123","korene","token",None,"korene","2026-09-26T00:00:00Z")
                await store.link_user("123","past_two","token",None,"viewer","2026-09-26T00:00:00Z")
                await store.get_progression("donny")
                await store.get_progression("korene")
                await store.get_progression("past_two")
                donny=store._data["users"]["donny"]["progression"]
                donny.update(prestige=1,xp=22_750,lifetime_xp=697_750,prestige_notified=1)
                korene=store._data["users"]["korene"]["progression"]
                korene.update(prestige=2,xp=12_500,lifetime_xp=712_500,prestige_notified=2)
                past_two=store._data["users"]["past_two"]["progression"]
                past_two.update(prestige=1,xp=1_000,lifetime_xp=720_000,prestige_notified=1)

                actual=await store.get_progression("donny")
                assert (actual["prestige"],actual["xp"],actual["lifetime_xp"]) == (1,347_750,697_750)
                assert await store.claim_prestige_notifications("donny")==[]
                other=await store.get_progression("korene")
                assert (other["prestige"],other["xp"],other["lifetime_xp"]) == (2,12_500,712_500)
                assert await store.claim_prestige_notifications("korene")==[]
                advanced=await store.get_progression("past_two")
                assert (advanced["prestige"],advanced["xp"],advanced["lifetime_xp"]) == (2,20_000,720_000)
                assert await store.claim_prestige_notifications("past_two")==[2]

                await store.flush()
                restored=storage_module.Storage()
                assert (await restored.get_progression("donny"))["xp"]==347_750
                assert (await restored.get_progression("korene"))["xp"]==12_500
            finally:
                storage_module.DATA_PATH=previous
    asyncio.run(scenario())
