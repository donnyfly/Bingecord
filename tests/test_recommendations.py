import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault("DISCORD_BOT_TOKEN", "test-token")
os.environ.setdefault("SIMKL_CLIENT_ID", "test-client")
os.environ.setdefault("TMDB_API_KEY", "test-key")

import bot  # noqa: E402
from recommendation_engine import recommendation_lineup, select_sources
from recommendation_ui import RecommendationView, different_candidate, related_candidate


def source(name, ident, rating, media="anime", stamp="2026-09-01"):
    return {"title":name,"tmdb_id":ident,"kind":"tv","media_type":media,
            "rating":rating,"watched_at":stamp,"anime":media=="anime","genres":["Drama"]}


def candidate(ident, *, matches=1, genres=(18,), votes=200):
    return {"id":ident,"name":f"Title {ident}","_recommendation_kind":"tv",
            "_matches":[{"source_id":("tv",i),"weight":1} for i in range(matches)],
            "genre_ids":list(genres),"vote_count":votes,"_score":20-ident}


def test_sources_prioritize_ratings_without_counting_seasons_or_dislikes():
    items=[source("Favourite",1,10,stamp="2025-01-01"),
           source("Recent",2,None,stamp="2026-09-20"),
           source("Disliked",3,3,stamp="2026-09-27"),
           source("Favourite season two",1,10,stamp="2026-09-22"),
           source("Movie",4,9,media="movies",stamp="2025-01-02")]
    selected=select_sources(items)
    assert [item["tmdb_id"] for item in selected]==[1,4,2]
    assert selected[0]["title"]=="Favourite season two"


def test_lineup_includes_broad_and_different_picks():
    ranked=[candidate(ident,matches=3 if ident<4 else 1,
                      genres=(18,) if ident<5 else (878,)) for ident in range(1,9)]
    lineup=recommendation_lineup(ranked)
    assert [item["id"] for item in lineup]==[1,2,3,4,5]
    assert related_candidate(ranked[0],ranked[3:])["id"]==4
    assert different_candidate(ranked[0],ranked[3:])["id"]==5


def test_candidate_ranking_uses_rated_matches_and_genres(monkeypatch):
    async def scenario():
        monkeypatch.setattr(bot.tmdb,"get_genres",AsyncMock(return_value={18:"Drama"}))
        rec=lambda ident: [{"id":ident,"name":f"Title {ident}","genre_ids":[18],
                            "vote_average":8.0,"vote_count":300,"original_language":"ja"}]
        monkeypatch.setattr(bot.tmdb,"get_tv_recommendations",AsyncMock(side_effect=lambda ident: rec(99)))
        monkeypatch.setattr(bot.tmdb,"get_tv_similar",AsyncMock(return_value=[]))
        sources=[source("Loved",1,10),source("Okay",2,6)]
        ranked=await bot._get_recommendation_candidates(sources,set(),"all")
        assert len(ranked)==1
        assert ranked[0]["_matched_genres"]=={"Drama"}
        assert [match["rating"] for match in ranked[0]["_matches"]]==[10,6]
        assert ranked[0]["_matches"][0]["weight"]>ranked[0]["_matches"][1]["weight"]
    asyncio.run(scenario())


def test_command_shows_private_card_and_buttons(monkeypatch):
    async def scenario():
        monkeypatch.setattr(bot.storage,"get_user",AsyncMock(return_value={"simkl_token":"token"}))
        monkeypatch.setattr(bot,"valid_token",AsyncMock(return_value="token"))
        monkeypatch.setattr(bot,"_recommendation_sources",AsyncMock(return_value=([source("Loved",1,9)],set(),"token")))
        picks=[candidate(ident) for ident in range(1,7)]
        for result in picks:
            result.update(_anime=True,_matched_genres={"Drama"},overview="A compelling story.",
                          first_air_date="2026-01-01",poster_path="/poster.jpg",
                          vote_average=8.1)
            result["_matches"]=[{"source_id":("tv",1),"title":"Loved","rating":9,
                                  "weight":1.7,"genres":["Drama"]}]
        monkeypatch.setattr(bot,"_get_recommendation_candidates",AsyncMock(return_value=picks))
        monkeypatch.setattr(bot,"mdblist",None)
        monkeypatch.setattr(bot.simkl,"resolve_title_url",AsyncMock(return_value="https://simkl.com/anime/123"))
        interaction=SimpleNamespace(
            guild=SimpleNamespace(id=1),user=SimpleNamespace(id=42,display_name="Viewer"),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock()))),
        )
        await bot.simkl_recommend.callback(interaction)
        sent=interaction.followup.send.await_args.kwargs
        assert sent["ephemeral"] and sent["embed"].url=="https://simkl.com/anime/123"
        assert "you rated *Loved* 9/10" in sent["embed"].fields[0].value
        assert sent["embed"].thumbnail.url.endswith("/poster.jpg")
        view=sent["view"]
        assert isinstance(view,RecommendationView) and len(view.children)==3
        assert view.owner_id==42
    asyncio.run(scenario())


def test_buttons_browse_and_keep_other_users_out():
    async def scenario():
        items=[candidate(n) for n in range(1,7)]
        renderer=AsyncMock(side_effect=lambda item,index,total: f"{item['id']} of {total}")
        view=RecommendationView(42,items[:5],items,renderer)
        stranger=SimpleNamespace(user=SimpleNamespace(id=99),response=SimpleNamespace(send_message=AsyncMock()))
        assert await view.interaction_check(stranger) is False
        stranger.response.send_message.assert_awaited_once()
        interaction=SimpleNamespace(user=SimpleNamespace(id=42),
            response=SimpleNamespace(defer=AsyncMock()),edit_original_response=AsyncMock(),
            followup=SimpleNamespace(send=AsyncMock()))
        await view.children[0].callback(interaction)
        assert view.index==1
        interaction.edit_original_response.assert_awaited_with(embed="2 of 5",view=view)
        await view.children[1].callback(interaction)
        assert view.lineup[-1]["id"]==6 and view.index==5
    asyncio.run(scenario())
