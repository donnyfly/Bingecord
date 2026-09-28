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
            "style": "rich", "artwork": "backdrop", "activity_text": "detailed"}))
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
