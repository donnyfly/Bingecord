import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from test_notification_preview import bot


def test_logo_and_started_line_after_episode_ratings(monkeypatch):
    async def run():
        monkeypatch.setattr(bot,'prefs',AsyncMock(return_value={'style':'rich','episode_code':False,'show_imdb':True}))
        monkeypatch.setattr(bot.storage,'get_provider_plays',AsyncMock(return_value=[]))
        monkeypatch.setattr(bot.tmdb,'get_tv_logo',AsyncMock(return_value='https://example.com/logo.png'))
        monkeypatch.setattr(bot,'episode_media',AsyncMock(return_value=('https://example.com/still.jpg','Pilot','ttEpisode',24)))
        monkeypatch.setattr(bot.imdb,'get_rating',AsyncMock(return_value=8.5))
        send=AsyncMock(return_value=True);monkeypatch.setattr(bot,'send_embed',send)
        provider=SimpleNamespace(title=AsyncMock(return_value={}),profile_url=lambda _: 'https://mdblist.com/lists/viewer',title_url=lambda *_:'https://mdblist.com/show/test')
        play={'media_type':'anime_episode','title':'Anime','ids':{'tmdb':100},'item_key':'episode:1','source_event_id':'1','show_id':100,'season':1,'episode':1,'watched_at':'2026-10-05T00:00:00Z'}
        member=SimpleNamespace(display_avatar=SimpleNamespace(url='https://example.com/avatar.png'))
        for last in (None,{**play,'episode':3}):
            await bot.deliver_mdblist_play(object(),'1','42','Viewer',member,provider,object(),play,last)
            embed=send.await_args.args[1]
            assert embed.thumbnail.url=='https://example.com/logo.png'
            assert embed.description.endswith('\n\n🆕 Started watching this series.')
            assert embed.description.index('Pilot')<embed.description.index('⭐ IMDb')<embed.description.index('🆕')
            assert 'MAL' not in embed.description
    asyncio.run(run())
