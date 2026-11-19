import asyncio
import copy
from datetime import datetime,timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
import discord
import pytest
from PIL import Image
from trackerbot.presentation.activity_cards import card_model,render_activity_png,eligible,image_url,activity_card_kwargs


def example(movie=False):
    embed=discord.Embed(title='Example Film' if movie else 'Example Series',url='https://simkl.com/tv/123',
        description='Watched **Example Film**\n⭐ IMDb 8.0/10\n🌸 MAL 8.50/10\n\n💬 Your rating: **9/10**' if movie else
                    'Watched **S1E01** of **Example Series**\n*The first episode*\n⭐ IMDb 8.0/10\n🌸 MAL 9.0/10\n\n💬 Your episode rating: **9/10**\n\n🏁 Finished **Season 1**.\n\n🆕 Started watching this series.',timestamp=datetime(2026,11,19,tzinfo=timezone.utc))
    embed.set_author(name="Viewer's Activity",url='https://simkl.com/1')
    embed.set_footer(text='Anime · SIMKL' if not movie else 'Anime movie · MDBList')
    part={'guild':'10','user':'1','source':'simkl','meta':{'movie':movie,'episodes':[] if movie else [1],'ids':{'tmdb':'1'}}}
    return embed,part


def test_model_preserves_episode_ratings_completion_and_started_last():
    embed,part=example();before=embed.to_dict();model=card_model(embed,part)
    assert model['action']=='Watched S1E01' and model['episode_title']=='The first episode'
    assert model['ratings']==['IMDb 8.0/10','Your episode rating: 9/10']
    assert model['notes']==['Finished Season 1.','NEW · Started watching this series.']
    assert embed.to_dict()==before
    assert eligible([part]) and not eligible([part,part])
    assert not eligible([{**part,'meta':{**part['meta'],'status':'completed'}}])
    film,part=example(True);model=card_model(film,part)
    assert model['action']=='Watched' and any('MAL' in r for r in model['ratings'])
    film.description='Rewatched **Example Film**\n⭐ IMDb 8.0/10'
    assert card_model(film,part)['action']=='Rewatched'


def test_artwork_hosts_proxy_and_limits():
    assert image_url('https://image.tmdb.org/t/p/w780/a.jpg')
    assert image_url('https://wsrv.nl/?url=https://simkl.in/posters/a.webp&q=90')=='https://simkl.in/posters/a.webp'
    for bad in ('http://image.tmdb.org/a','https://localhost/a','https://image.tmdb.org:8080/a',
                'https://image.tmdb.org.evil.test/a','https://user@image.tmdb.org/a','https://wsrv.nl/?url=http://127.0.0.1/a'):
        assert image_url(bad) is None


def test_episode_and_film_render_shapes_missing_art_and_long_names():
    for movie in (False,True):
        embed,part=example(movie);model=card_model(embed,part,preview=True)
        model['title']='A long title that needs more than a single line in the activity header'
        art={'image':Image.new('RGB',(736,414),'navy'),'logo':Image.new('RGBA',(300,90),(240,220,150,255))}
        assert Image.open(render_activity_png(model,art)).size==(1080,878 if not movie else 770)
        assert Image.open(render_activity_png(model)).width==1080


def test_render_cache_and_native_links_preserved(monkeypatch):
    import trackerbot.presentation.activity_cards as module
    from trackerbot.presentation.card_details import configure
    async def run():
        configure(lambda:None);module._cards.clear()
        embed,part=example();before=embed.to_dict()
        render=Mock(return_value=BytesIO(b'card'));monkeypatch.setattr(module,'render_activity_png',render)
        one=await activity_card_kwargs(embed,part,preview=True)
        two=await activity_card_kwargs(embed,part,preview=True)
        assert render.call_count==1
        assert one['embed'].url==embed.url and one['embed'].author.url==embed.author.url
        assert not one['embed'].thumbnail and one['file'].filename=='activity.png'
        assert one['embed'].image.url=='attachment://activity.png' and embed.to_dict()==before
        embed.description+='\n\n✅ Completed this series.'
        await activity_card_kwargs(embed,part,preview=True)
        assert render.call_count==2
        module._cards.clear()
    asyncio.run(run())


def test_opt_in_send_persists_card_binding_and_default_stays_native(monkeypatch):
    from test_notification_preview import bot
    from trackerbot.core.activity_details import current_post
    import trackerbot.presentation.activity_cards as cards
    async def run():
        embed,part=example();post={'base':embed.to_dict(),'rendered':embed.to_dict(),'parts':[part]}
        monkeypatch.setattr(bot,'prefs',AsyncMock(return_value={'style':'rich'}))
        render=AsyncMock(return_value={'embed':discord.Embed(title='Compact'),'file':'card','view':'details'})
        monkeypatch.setattr(cards,'activity_card_kwargs',render)
        remember=AsyncMock();monkeypatch.setattr(bot.activity_details,'remember',remember)
        channel=SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=7)))
        token=current_post.set(post)
        try:
            assert await bot.send_embed(channel,embed,'watch')
            render.assert_not_awaited();assert 'file' not in channel.send.await_args.kwargs
            bot.prefs.return_value={'style':'card'}
            assert await bot.send_embed(channel,embed,'watch')
            assert channel.send.await_args.kwargs['file']=='card' and post['presentation']=='card'
            render.side_effect=ValueError('Artwork unavailable');post.pop('presentation')
            assert await bot.send_embed(channel,embed,'watch')
            assert 'file' not in channel.send.await_args.kwargs and 'presentation' not in post
            assert remember.await_count==3
        finally:current_post.reset(token)
    asyncio.run(run())


def test_late_rating_card_regenerates_attachment_and_retries_failures(tmp_path,monkeypatch):
    from test_activity_details import setup
    import trackerbot.presentation.activity_cards as cards
    from trackerbot.integrations.personal_ratings import normalize
    async def run():
        store,app,engine,ch,message,sent,sender=await setup(tmp_path,monkeypatch)
        embed,part=example(True)
        assert await engine.single(sender,ch,embed,'10','1','simkl',part['meta'])
        record=(await store.get_activity_messages('10'))[0];record['presentation']='card';await store.save_activity_message(record)
        before=await store.get_progression('1')
        render=AsyncMock(return_value={'embed':discord.Embed(title='Updated card'),'file':'updated-png','view':'new-details'})
        monkeypatch.setattr(cards,'activity_card_kwargs',render)
        engine.contexts[('10','1')]['state']['ratings']=[normalize({'tmdb':'1','rating':7},'movie','mdblist')]
        await engine.finish()
        assert message.edit.await_args.kwargs['attachments']==['updated-png']
        assert '**7/10**' in render.await_args.args[0].description
        saved=(await store.get_activity_messages('10'))[0]
        assert saved['presentation']=='card' and 'attachment://' not in str(saved['base'])
        calls=message.edit.await_count
        await engine.finish();assert message.edit.await_count==calls
        engine.contexts[('10','1')]['state']['ratings']=[]
        render.side_effect=ValueError('temporary image failure');await engine.finish()
        assert message.edit.await_count==calls
        assert (await store.get_activity_messages('10'))[0]['rendered']==saved['rendered']
        render.side_effect=None;await engine.finish()
        assert message.edit.await_count==calls+1
        assert await store.get_progression('1')==before
    asyncio.run(run())


def test_debug_activity_previews_are_private_without_sync_or_preferences_changes(monkeypatch):
    from test_notification_preview import bot,_interaction
    from discord import app_commands
    import trackerbot.presentation.activity_cards as cards
    async def run():
        i=_interaction();i.user.display_name='Viewer'
        monkeypatch.setattr(bot.storage,'get_activity_provider',AsyncMock(return_value='wetrakr'))
        monkeypatch.setattr(bot.storage,'get_activity_messages',AsyncMock(return_value=[]))
        render=AsyncMock(return_value={'embed':discord.Embed(title='Preview'),'file':'preview.png'})
        monkeypatch.setattr(cards,'activity_card_kwargs',render)
        writer=AsyncMock();monkeypatch.setattr(bot.storage,'set_embed_preferences',writer)
        poll=AsyncMock();monkeypatch.setattr(bot,'poll_providers',poll)
        for feature in ('activity_episode','activity_movie'):
            await bot.simkl_debug.callback(i,app_commands.Choice(name=feature,value=feature))
            assert i.followup.send.await_args.kwargs['ephemeral']
            assert render.await_args.kwargs=={'preview':True,'sample':True}
            assert render.await_args.args[1]['source']=='wetrakr'
        writer.assert_not_awaited();poll.assert_not_awaited()
    asyncio.run(run())
