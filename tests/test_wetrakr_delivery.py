import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from test_notification_preview import bot


def test_episode_delivery_uses_parent_title_and_wetrakr_footer(monkeypatch):
    async def run():
        fake = SimpleNamespace(episode=AsyncMock(return_value={
            "title": "The Pilot", "season_number": 1, "number": 1,
            "media": {"title": "A New Show", "ids": {"tmdb": 100}, "poster_path": "/poster.jpg"},
        }))
        monkeypatch.setattr(bot, "wetrakr", fake)
        monkeypatch.setattr(bot, "prefs", AsyncMock(return_value={
            "style": "rich", "artwork": "backdrop", "activity_text": "detailed",
            "episode_code": False, "show_imdb": False, "show_mal": False}))
        monkeypatch.setattr(bot.tmdb, "get_episode_still", AsyncMock(return_value="https://example.com/still.jpg"))
        monkeypatch.setattr(bot.tmdb, "get_tv_logo", AsyncMock(return_value="https://example.com/logo.png"))
        send = AsyncMock(return_value=True)
        monkeypatch.setattr(bot, "send_embed", send)
        member = SimpleNamespace(display_avatar=SimpleNamespace(url="https://example.com/avatar.png"))
        change = {"action": "added", "media_type": "episode", "title": "The Pilot",
                  "wetrakr_id": 50, "show_id": 25, "season": 1, "episode": 1,
                  "watched_at": "2026-09-28T03:00:00Z"}
        assert await bot.deliver_wetrakr_change(SimpleNamespace(), "123", "42", "Viewer", member,
                                                 change, {}, {"25"})
        embed = send.await_args.args[1]
        assert "Watched **S1E01** of **A New Show**" in embed.description
        assert "🆕 Started watching this series." in embed.description
        assert embed.image.url.endswith("/still.jpg")
        assert embed.footer.text.endswith("WeTrakr")
    asyncio.run(run())


def test_status_poster_and_failed_send(monkeypatch):
    async def run():
        monkeypatch.setattr(bot, "wetrakr", SimpleNamespace(title=AsyncMock(return_value={
            "title": "A Film", "ids": {"tmdb": 99}, "poster_path": "/film.jpg"})))
        monkeypatch.setattr(bot, "prefs", AsyncMock(return_value={
            "style": "rich", "artwork": "backdrop", "activity_text": "detailed",
            "show_imdb": False, "show_mal": False}))
        sender = AsyncMock(return_value=False)
        monkeypatch.setattr(bot, "send_embed", sender)
        member = SimpleNamespace(display_avatar=SimpleNamespace(url="https://example.com/avatar.png"))
        change = {"action": "added", "status": "planning", "media_type": "movie",
                  "title": "A Film", "wetrakr_id": 10, "ids": {},
                  "action_at": "2026-09-28T03:00:00Z"}
        assert not await bot.deliver_wetrakr_change(SimpleNamespace(), "123", "42", "Viewer", member,
                                                     change, {}, set())
        embed = sender.await_args.args[1]
        assert embed.description == "Planned to watch **A Film**"
        assert embed.image.url.endswith("/film.jpg")
        assert embed.footer.text.endswith("WeTrakr")
    asyncio.run(run())


def test_anime_detail_season_and_bulk_range(monkeypatch):
    async def run():
        async def episode(episode_id):
            return {"title": f"Episode {episode_id}", "season": {"number": 2},
                    "number": {101: 3, 102: 4}[episode_id],
                    "media": {"title": "Anime", "ids": {"tmdb": 90}}}
        monkeypatch.setattr(bot, "wetrakr", SimpleNamespace(episode=episode))
        monkeypatch.setattr(bot, "prefs", AsyncMock(return_value={
            "style": "rich", "artwork": "backdrop", "activity_text": "detailed",
            "episode_code": False, "show_imdb": False, "show_mal": False}))
        still = AsyncMock(return_value=None)
        monkeypatch.setattr(bot.tmdb, "get_episode_still", still)
        monkeypatch.setattr(bot.tmdb, "get_tv_logo", AsyncMock(return_value=None))
        send = AsyncMock(return_value=True)
        monkeypatch.setattr(bot, "send_embed", send)
        member = SimpleNamespace(display_avatar=SimpleNamespace(url="https://example.com/avatar.png"))
        first = {"action": "added", "media_type": "episode", "title": "Anime", "wetrakr_id": 101,
                 "show_id": 10, "season": 1, "episode": 91, "watched_at": "2026-09-28T03:00:00Z"}
        last = {**first, "wetrakr_id": 102, "episode": 92}
        assert await bot.deliver_wetrakr_change(SimpleNamespace(), "123", "42", "Viewer", member,
                                                first, {}, set(), last)
        assert "Watched **S2E03-E04** of **Anime**" in send.await_args.args[1].description
        still.assert_awaited_once_with(90, 2, 3)
    asyncio.run(run())


def test_anime_uses_tvmaze_season_instead_of_wetrakr_absolute_number(monkeypatch):
    async def run():
        monkeypatch.setattr(bot, "wetrakr", SimpleNamespace(episode=AsyncMock(return_value={
            "title": "Hidden Inventory", "season": {"number": 1}, "number": 25,
            "media": {"title": "Jujutsu Kaisen", "ids": {"tmdb": 95479, "tvdb": 377543}},
        })))
        monkeypatch.setattr(bot, "prefs", AsyncMock(return_value={
            "style": "rich", "artwork": "backdrop", "activity_text": "detailed",
            "episode_code": False, "show_imdb": False, "show_mal": False}))
        monkeypatch.setattr(bot.tmdb, "_get_series_details", AsyncMock(return_value={
            "original_language": "ja", "origin_country": ["JP"], "genres": [{"id": 16}]}))
        monkeypatch.setattr(bot.tmdb, "get_tv_title", AsyncMock(return_value="Jujutsu Kaisen"))
        monkeypatch.setattr(bot.tmdb, "get_episode_details", AsyncMock(return_value={
            "air_date": "2023-07-06"}))
        mapper = AsyncMock(return_value=(2, 1))
        monkeypatch.setattr(bot.tmdb, "map_anime_episode_to_tvmaze", mapper)
        monkeypatch.setattr(bot.tmdb, "find_anime_episode", AsyncMock(return_value={
            "still_url": "https://example.com/hidden-inventory.jpg"}))
        monkeypatch.setattr(bot.tmdb, "get_tv_logo", AsyncMock(return_value=None))
        send = AsyncMock(return_value=True)
        monkeypatch.setattr(bot, "send_embed", send)
        change = {"action": "added", "media_type": "episode", "wetrakr_id": 101,
                  "show_id": 10, "season": 1, "episode": 25,
                  "watched_at": "2026-09-28T03:00:00Z"}
        member = SimpleNamespace(display_avatar=SimpleNamespace(url="https://example.com/avatar.png"))
        assert await bot.deliver_wetrakr_change(SimpleNamespace(), "123", "42", "Viewer", member,
                                                change, {}, set())
        embed = send.await_args.args[1]
        assert "Watched **S2E01** of **Jujutsu Kaisen**" in embed.description
        assert embed.footer.text.endswith("Anime · WeTrakr")
        assert embed.image.url.endswith("hidden-inventory.jpg")
        mapper.assert_awaited_once_with(377543, air_date="2023-07-06", title="Hidden Inventory")
    asyncio.run(run())


def test_wetrakr_url_helpers():
    assert bot.wetrakr_title_url("movie", 99) == "https://wetrakr.com/tmdb/movie/99"
    assert bot.wetrakr_title_url("show", 100) == "https://wetrakr.com/tmdb/show/100"
    assert bot.wetrakr_profile_url({"username": "viewer name"}) == "https://wetrakr.com/viewer%20name"


def test_wetrakr_episode_links_profile_and_marks_direct_s1e1_start(monkeypatch):
    async def run():
        monkeypatch.setattr(bot, "wetrakr", SimpleNamespace(episode=AsyncMock(return_value={
            "title":"Pilot","season_number":1,"number":1,
            "media":{"title":"Linked Show","ids":{"tmdb":100}},
        })))
        monkeypatch.setattr(bot, "prefs", AsyncMock(return_value={
            "style":"rich","artwork":"backdrop","activity_text":"detailed",
            "episode_code":False,"show_imdb":False,"show_mal":False}))
        monkeypatch.setattr(bot.storage, "get_user", AsyncMock(return_value={
            "wetrakr":{"username":"viewer"}}))
        history=AsyncMock(return_value=False)
        monkeypatch.setattr(bot.storage, "has_wetrakr_show_history", history)
        monkeypatch.setattr(bot.tmdb, "get_episode_still", AsyncMock(return_value=None))
        monkeypatch.setattr(bot.tmdb, "get_tv_logo", AsyncMock(return_value=None))
        send=AsyncMock(return_value=True)
        monkeypatch.setattr(bot, "send_embed", send)
        member=SimpleNamespace(display_avatar=SimpleNamespace(url="https://example.com/avatar.png"))
        change={"action":"added","media_type":"episode","wetrakr_id":50,"show_id":25,
                "season":1,"episode":1,"watched_at":"2026-09-28T03:00:00Z"}
        assert await bot.deliver_wetrakr_change(SimpleNamespace(),"123","42","Viewer",member,
                                                change,{},set())
        embed=send.await_args.args[1]
        assert embed.url == "https://wetrakr.com/tmdb/show/100"
        assert embed.author.url == "https://wetrakr.com/viewer"
        assert "🆕 Started watching this series." in embed.description
        history.assert_awaited_once()
    asyncio.run(run())


def test_wetrakr_s1e1_range_marks_series_start(monkeypatch):
    async def run():
        async def episode(episode_id):
            number={101:1,103:3}[episode_id]
            return {"title":f"Episode {number}","season_number":1,"number":number,
                    "media":{"title":"Range Show","ids":{"tmdb":77}}}
        monkeypatch.setattr(bot, "wetrakr", SimpleNamespace(episode=episode))
        monkeypatch.setattr(bot, "prefs", AsyncMock(return_value={
            "style":"rich","artwork":"backdrop","activity_text":"detailed",
            "episode_code":False,"show_imdb":False,"show_mal":False}))
        monkeypatch.setattr(bot.storage, "get_user", AsyncMock(return_value={"wetrakr":{"username":"viewer"}}))
        monkeypatch.setattr(bot.storage, "has_wetrakr_show_history", AsyncMock(return_value=False))
        monkeypatch.setattr(bot.tmdb, "get_episode_still", AsyncMock(return_value=None))
        monkeypatch.setattr(bot.tmdb, "get_tv_logo", AsyncMock(return_value=None))
        send=AsyncMock(return_value=True)
        monkeypatch.setattr(bot, "send_embed", send)
        member=SimpleNamespace(display_avatar=SimpleNamespace(url="https://example.com/avatar.png"))
        first={"action":"added","media_type":"episode","wetrakr_id":101,"show_id":25,
               "season":1,"episode":1,"watched_at":"2026-09-28T03:00:00Z"}
        last={**first,"wetrakr_id":103,"episode":3}
        assert await bot.deliver_wetrakr_change(SimpleNamespace(),"123","42","Viewer",member,
                                                first,{},set(),last)
        embed=send.await_args.args[1]
        assert "S1E01-E03" in embed.description
        assert "🆕 Started watching this series." in embed.description
    asyncio.run(run())


def test_wetrakr_episode_uses_its_imdb_rating_without_series_or_mal(monkeypatch):
    async def run():
        monkeypatch.setattr(bot, "wetrakr", SimpleNamespace(episode=AsyncMock(return_value={
            "title":"Episode 2","season_number":1,"number":2,
            "external_ids":{"imdb_id":"tt-episode-2"},
            "media":{"title":"Anime Show","ids":{"tmdb":90,"tvdb":900},"is_anime":True},
        })))
        monkeypatch.setattr(bot, "prefs", AsyncMock(return_value={
            "style":"rich","artwork":"backdrop","activity_text":"detailed",
            "episode_code":False,"show_imdb":True,"show_mal":True}))
        monkeypatch.setattr(bot.storage, "get_user", AsyncMock(return_value={"wetrakr":{"username":"anime-user"}}))
        series_ratings=AsyncMock(return_value={"imdb":8.4,"mal":9.12})
        monkeypatch.setattr(bot, "get_show_ratings", series_ratings)
        episode_rating=AsyncMock(return_value=7.3)
        monkeypatch.setattr(bot.imdb, "get_rating", episode_rating)
        monkeypatch.setattr(bot.tmdb, "get_tv_title", AsyncMock(return_value="Anime Show"))
        monkeypatch.setattr(bot, "wetrakr_anime_coordinates", AsyncMock(return_value=(1,2,900,False)))
        monkeypatch.setattr(bot.tmdb, "get_episode_still", AsyncMock(return_value=None))
        monkeypatch.setattr(bot.tmdb, "get_tv_logo", AsyncMock(return_value=None))
        send=AsyncMock(return_value=True)
        monkeypatch.setattr(bot, "send_embed", send)
        member=SimpleNamespace(display_avatar=SimpleNamespace(url="https://example.com/avatar.png"))
        change={"action":"added","media_type":"episode","wetrakr_id":102,"show_id":25,
                "season":1,"episode":2,"watched_at":"2026-09-28T03:00:00Z"}
        assert await bot.deliver_wetrakr_change(SimpleNamespace(),"123","42","Viewer",member,
                                                change,{},set())
        desc=send.await_args.args[1].description
        assert "⭐ IMDb 7.3/10" in desc
        assert "🌸 MAL" not in desc
        series_ratings.assert_not_awaited()
        episode_rating.assert_awaited_once_with("tt-episode-2")
    asyncio.run(run())


def test_wetrakr_tv_episode_resolves_imdb_id_from_tmdb(monkeypatch):
    async def run():
        monkeypatch.setattr(bot, "wetrakr", SimpleNamespace(episode=AsyncMock(return_value={
            "title": "Pilot", "season_number": 1, "number": 1,
            "media": {"title": "TV Series", "ids": {"tmdb": 90}}})))
        monkeypatch.setattr(bot, "prefs", AsyncMock(return_value={
            "style": "rich", "artwork": "auto", "activity_text": "detailed",
            "show_imdb": True, "show_mal": True}))
        monkeypatch.setattr(bot.tmdb, "_get_series_details", AsyncMock(return_value={
            "original_language": "en", "genres": []}))
        monkeypatch.setattr(bot.tmdb, "get_episode_still", AsyncMock(return_value=None))
        monkeypatch.setattr(bot.tmdb, "get_tv_logo", AsyncMock(return_value=None))
        details = AsyncMock(return_value={"external_ids": {"imdb_id": "tt-pilot"}})
        monkeypatch.setattr(bot.tmdb, "get_episode_details", details)
        monkeypatch.setattr(bot.imdb, "get_rating", AsyncMock(return_value=6.7))
        monkeypatch.setattr(bot, "get_show_ratings", AsyncMock(return_value={"imdb": 9.9, "mal": 9.9}))
        monkeypatch.setattr(bot.storage, "has_wetrakr_show_history", AsyncMock(return_value=True))
        monkeypatch.setattr(bot, "send_embed", AsyncMock(return_value=True))
        member = SimpleNamespace(display_avatar=SimpleNamespace(url="https://example.com/avatar.png"))
        change = {"action": "added", "media_type": "episode", "wetrakr_id": 1,
                  "show_id": 5, "season": 1, "episode": 1,
                  "watched_at": "2026-09-28T03:00:00Z"}
        await bot.deliver_wetrakr_change(SimpleNamespace(), "123", "42", "Viewer", member, change, {}, set())
        desc = bot.send_embed.await_args.args[1].description
        assert "⭐ IMDb 6.7/10" in desc and "9.9" not in desc and "🌸 MAL" not in desc
        details.assert_awaited_once_with(90, 1, 1)
    asyncio.run(run())


def test_wetrakr_anime_status_keeps_series_ratings(monkeypatch):
    async def run():
        monkeypatch.setattr(bot, "wetrakr", SimpleNamespace(title=AsyncMock(return_value={
            "title": "Anime Series", "ids": {"tmdb": 90}, "is_anime": True})))
        monkeypatch.setattr(bot, "prefs", AsyncMock(return_value={
            "style": "rich", "artwork": "auto", "activity_text": "detailed",
            "show_imdb": True, "show_mal": True}))
        monkeypatch.setattr(bot.tmdb, "get_tv_title", AsyncMock(return_value="Anime Series"))
        monkeypatch.setattr(bot, "get_show_ratings", AsyncMock(return_value={"imdb": 8.1, "mal": 9.2}))
        monkeypatch.setattr(bot, "send_embed", AsyncMock(return_value=True))
        member = SimpleNamespace(display_avatar=SimpleNamespace(url="https://example.com/avatar.png"))
        change = {"action": "added", "status": "planning", "media_type": "show",
                  "wetrakr_id": 1, "action_at": "2026-09-28T03:00:00Z"}
        await bot.deliver_wetrakr_change(SimpleNamespace(), "123", "42", "Viewer", member, change, {}, set())
        desc = bot.send_embed.await_args.args[1].description
        assert "⭐ IMDb 8.1/10" in desc and "🌸 MAL 9.20/10" in desc
    asyncio.run(run())


def test_wetrakr_movie_has_imdb_rating(monkeypatch):
    async def run():
        monkeypatch.setattr(bot, "wetrakr", SimpleNamespace(title=AsyncMock(return_value={
            "title":"A Film","ids":{"tmdb":99},"poster_path":"/film.jpg"})))
        monkeypatch.setattr(bot, "prefs", AsyncMock(return_value={
            "style":"rich","artwork":"backdrop","activity_text":"detailed",
            "show_imdb":True,"show_mal":False}))
        monkeypatch.setattr(bot.storage, "get_user", AsyncMock(return_value={"wetrakr":{"username":"movie-user"}}))
        monkeypatch.setattr(bot, "get_movie_ratings", AsyncMock(return_value={"imdb":7.5,"mal":None}))
        monkeypatch.setattr(bot.tmdb, "get_movie_backdrop", AsyncMock(return_value=None))
        monkeypatch.setattr(bot.tmdb, "get_movie_logo", AsyncMock(return_value=None))
        send=AsyncMock(return_value=True)
        monkeypatch.setattr(bot, "send_embed", send)
        member=SimpleNamespace(display_avatar=SimpleNamespace(url="https://example.com/avatar.png"))
        change={"action":"added","media_type":"movie","wetrakr_id":10,
                "watched_at":"2026-09-28T03:00:00Z"}
        assert await bot.deliver_wetrakr_change(SimpleNamespace(),"123","42","Viewer",member,
                                                change,{},set())
        embed=send.await_args.args[1]
        assert "⭐ IMDb 7.5/10" in embed.description
        assert embed.url == "https://wetrakr.com/tmdb/movie/99"
        assert embed.author.url == "https://wetrakr.com/movie-user"
    asyncio.run(run())


def test_wetrakr_anime_movie_uses_anime_card_and_mal(monkeypatch):
    async def run():
        monkeypatch.setattr(bot, "wetrakr", SimpleNamespace(title=AsyncMock(return_value={
            "title": "Animated Film", "ids": {"tmdb": 91}})))
        monkeypatch.setattr(bot.tmdb, "get_movie_details", AsyncMock(return_value={
            "original_language": "ja", "genres": [{"id": 16, "name": "Animation"}]}))
        monkeypatch.setattr(bot.tmdb, "get_movie_backdrop", AsyncMock(return_value=None))
        monkeypatch.setattr(bot.tmdb, "get_movie_logo", AsyncMock(return_value=None))
        monkeypatch.setattr(bot, "get_movie_ratings", AsyncMock(return_value={"imdb": 8.1, "mal": 8.75}))
        monkeypatch.setattr(bot, "prefs", AsyncMock(return_value={
            "style": "rich", "artwork": "backdrop", "activity_text": "detailed",
            "show_imdb": True, "show_mal": True}))
        monkeypatch.setattr(bot, "send_embed", AsyncMock(return_value=True))
        member = SimpleNamespace(display_avatar=SimpleNamespace(url="https://example.com/avatar.png"))
        change = {"action": "added", "media_type": "movie", "wetrakr_id": 10,
                  "watched_at": "2026-09-28T03:00:00Z"}
        assert await bot.deliver_wetrakr_change(SimpleNamespace(), "123", "42", "Viewer",
                                                member, change, {}, set())
        embed = bot.send_embed.await_args.args[1]
        assert "🌸 MAL 8.75/10" in embed.description
        assert "⭐ IMDb 8.1/10" in embed.description
        assert "Anime · WeTrakr" in embed.footer.text
    asyncio.run(run())
