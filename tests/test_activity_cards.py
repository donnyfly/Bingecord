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
    assert eligible([part]) and eligible([part,{**part,'user':'2'}])
    assert eligible([{**part,'meta':{**part['meta'],'status':'completed'}}])
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
        from trackerbot.presentation.activity_cards import activity_layout
        assert Image.open(render_activity_png(model,art)).size==(1080,activity_layout(model)['height'])
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
        assert one['embed'] is None and one['file'].filename=='activity.png'
        assert one['view'].children[-1].url==embed.url
        assert one['view'].children[-1].label=='View on SIMKL'
        assert embed.to_dict()==before
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
        render=AsyncMock(return_value={'embed':None,'file':'card','view':'details'})
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
            assert channel.send.await_args.kwargs['embed'] is None
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
        render=AsyncMock(return_value={'embed':None,'file':'updated-png','view':'new-details'})
        monkeypatch.setattr(cards,'activity_card_kwargs',render)
        engine.contexts[('10','1')]['state']['ratings']=[normalize({'tmdb':'1','rating':7},'movie','mdblist')]
        await engine.finish()
        assert message.edit.await_args.kwargs['attachments']==['updated-png']
        assert message.edit.await_args.kwargs['embed'] is None
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
        for feature in ('activity_episode','activity_movie','activity_status'):
            await bot.simkl_debug.callback(i,app_commands.Choice(name=feature,value=feature))
            assert i.followup.send.await_args.kwargs['ephemeral']
            assert render.await_args.kwargs=={'preview':True,'sample':True}
            assert render.await_args.args[1]['source']=='wetrakr'
        writer.assert_not_awaited();poll.assert_not_awaited()
    asyncio.run(run())


def test_cinematic_fill_preserves_full_four_three_frame_and_colours_sidebars():
    from trackerbot.presentation.activity_cards import cinematic_art,activity_layout
    frame=Image.new('RGB',(400,300),(40,100,170))
    # Distinct edge marks must survive the contained foreground frame.
    from PIL import ImageDraw
    draw=ImageDraw.Draw(frame);draw.rectangle((0,0,20,299),fill='red');draw.rectangle((379,0,399,299),fill='lime')
    art=cinematic_art(frame,(984,520)).convert('RGB')
    assert art.getpixel((179,260))[0]>200
    assert art.getpixel((805,260))[1]>200
    assert sum(art.getpixel((20,260)))>20
    embed,part=example();model=card_model(embed,part)
    full=activity_layout(model)
    simple=activity_layout(dict(model,notes=[],ratings=[]))
    assert simple['height']<full['height']
    assert full['ratings'][1][0]=='Your episode'
    for (name,value,width),(x,row) in zip(full['ratings'],full['rating_positions']):assert x+width<=984


def test_rating_marks_and_watcher_timezone():
    from trackerbot.presentation.activity_cards import rating_service,service_logo,local_timestamp
    for service in ('simkl','wetrakr','mdblist'):
        assert rating_service('Your season entry',service)==service
        assert service_logo(service).width>=48
    for label,service in (('IMDb','imdb'),('MAL','mal')):
        assert rating_service(label,'simkl')==service
        assert service_logo(service).mode=='RGBA'
    base={'timestamp':'2026-11-19T09:48:00+00:00','timezone':'Asia/Singapore'}
    assert local_timestamp(base)=='19 Nov · 5:48 PM SGT'
    assert local_timestamp(dict(base,timezone='America/New_York')).endswith('EST')
    assert local_timestamp(dict(base,timezone='America/New_York',timestamp='2026-07-19T09:48:00+00:00')).endswith('EDT')
    assert local_timestamp(dict(base,timezone='Invalid/Zone')).endswith('UTC')
    embed,part=example();model=card_model(embed,dict(part,display_timezone='Asia/Singapore'))
    assert model['timezone']=='Asia/Singapore' and model['tracker']=='simkl'


def test_title_mark_stays_in_header_outside_artwork():
    from trackerbot.presentation.activity_cards import activity_layout
    embed,part=example();model=card_model(embed,part)
    colour=(255,0,255,255)
    image=Image.open(render_activity_png(model,{'logo':Image.new('RGBA',(242,76),colour)})).convert('RGBA')
    assert image.getpixel((1031,117))==colour
    assert activity_layout(model)['hero_top']>118
    # The old top-left artwork placement must not contain the supplied mark.
    assert image.getpixel((73,149))!=colour


def test_personal_timezone_persists_isolated_and_reset_follows_server(tmp_path,monkeypatch):
    from trackerbot.core import storage as module
    async def run():
        monkeypatch.setattr(module,'DATA_PATH',str(tmp_path/'timezone.json'))
        store=module.Storage()
        for uid in ('1','2'):
            await store.link_user('10',uid,'token',None,'Viewer','2026-10-01T00:00:00Z')
        await store.set_timezone('10','UTC')
        await store.set_activity_timezone('1','America/New_York')
        assert (await store.get_activity_timezone('10','1'))=={'name':'America/New_York','source':'personal'}
        assert (await store.get_activity_timezone('10','2'))['name']=='UTC'
        assert (await store.get_timezone('10'))['name']=='UTC'
        restarted=module.Storage()
        assert (await restarted.get_activity_timezone('10','1'))['name']=='America/New_York'
        from zoneinfo import ZoneInfoNotFoundError
        with pytest.raises(ZoneInfoNotFoundError):await store.set_activity_timezone('1','Invalid/Zone')
        await store.set_activity_timezone('1',None)
        assert (await store.get_activity_timezone('10','1'))['name']=='UTC'
    asyncio.run(run())


def test_personal_timezone_command_available_to_non_admin(monkeypatch):
    from test_notification_preview import bot,_interaction
    async def run():
        i=_interaction();i.user.guild_permissions.manage_guild=False
        setter=AsyncMock();monkeypatch.setattr(bot.storage,'set_activity_timezone',setter)
        server=AsyncMock();monkeypatch.setattr(bot.storage,'set_timezone',server)
        monkeypatch.setattr(bot.storage,'get_activity_timezone',AsyncMock(return_value={'name':'Asia/Singapore','source':'personal'}))
        await bot.simkl_timezone.callback(i,'Asia/Singapore',personal=True)
        setter.assert_awaited_once_with('42','Asia/Singapore')
        assert 'Asia/Singapore' in i.response.send_message.await_args.args[0]
        await bot.simkl_timezone.callback(i,'UTC')
        server.assert_not_awaited()
        assert i.response.send_message.await_args.args[0]==bot.NOT_ADMIN_MESSAGE
    asyncio.run(run())


def test_ambient_artwork_dissolves_into_sampled_background():
    from trackerbot.presentation.activity_cards import ambient_background,artwork_mask,activity_layout
    mask=artwork_mask((984,520))
    assert mask.getpixel((492,200))==255
    assert mask.getpixel((0,200))==255 and mask.getpixel((983,200))==255
    assert mask.getpixel((492,0))==0
    assert 0<mask.getpixel((492,100))<200
    assert 0<mask.getpixel((492,430))<255
    assert mask.getpixel((492,519))==0
    embed,part=example(True);model=card_model(embed,part);layout=activity_layout(model)
    size=(1080,layout['height']);art=Image.new('RGB',(800,450),(30,90,190))
    ambient=ambient_background(size,(239,190,105),art,layout['hero_bottom'])
    result=Image.open(render_activity_png(model,{'image':art})).convert('RGB')
    seam=(540,layout['hero_bottom']-1)
    assert result.getpixel(seam)==ambient.convert('RGB').getpixel(seam)
    # Backdrop colour continues below the artwork rather than falling to flat black.
    red,green,blue=result.getpixel((1020,layout['height']-60))
    assert blue>green>red


def test_together_model_keeps_member_ratings_scopes_and_trackers():
    from trackerbot.presentation.activity_cards import together_model,activity_layout
    embed,part=example(True)
    embed.description='<@1> and <@2> watched **Example Film** together\n⭐ IMDb 8.0/10\n💬 <@1> · Your rating: **7/10**\n💬 <@2> · Your rating: **9/10**'
    parts=[dict(part,card_name='Donny'),dict(part,user='2',source='mdblist',card_name='Adele')]
    model=together_model(embed,parts)
    assert model['author']=='Watched Together' and model['action']=='Watched together'
    assert model['ratings']==['IMDb 8.0/10']
    assert [(m['name'],m['score'],m['tracker']) for m in model['members']]==[('Donny','7/10','simkl'),('Adele','9/10','mdblist')]
    layout=activity_layout(model)
    assert layout['members_top']+160<layout['height']
    assert Image.open(render_activity_png(model)).height==layout['height']
    embed.description='<@1> and <@2> watched **S4E02** of **Example Series** together\n*Episode title*\n⭐ IMDb 9.5/10\n🆕 <@2> started this series.'
    for p in parts:p['meta']={'movie':False,'episodes':[2]}
    model=together_model(embed,parts)
    assert model['action']=='Watched together · S4E02'
    assert model['episode_title']=='Episode title'
    assert 'Adele' in model['notes'][0] and '<@' not in str(model)
    assert all(m['score'] is None for m in model['members'])


def test_together_card_refresh_passes_all_participants_and_no_xp(tmp_path,monkeypatch):
    from test_activity_details import setup
    import trackerbot.presentation.activity_cards as cards
    from trackerbot.integrations.personal_ratings import normalize
    async def run():
        store,app,engine,ch,message,sent,sender=await setup(tmp_path,monkeypatch,users=('1','2'))
        embed,part=example(True)
        embed.description='<@1> and <@2> watched **Example Film** together\n⭐ IMDb 8.0/10'
        parts=[engine.participant('10',uid,'simkl',part['meta']) for uid in ('1','2')]
        for p,name in zip(parts,('Donny','Adele')):p['card_name']=name
        assert await engine.send(sender,ch,embed,parts)
        record=(await store.get_activity_messages('10'))[0];record['presentation']='card';await store.save_activity_message(record)
        before=[await store.get_progression(uid) for uid in ('1','2')]
        render=AsyncMock(return_value={'embed':None,'file':'group.png','view':'details'})
        monkeypatch.setattr(cards,'activity_card_kwargs',render)
        engine.contexts[('10','2')]['state']['ratings']=[normalize({'tmdb':1,'rating':9},'movie','simkl')]
        await engine.finish()
        assert message.edit.await_args.kwargs['embed'] is None
        assert message.edit.await_args.kwargs['attachments']==['group.png']
        assert [p['card_name'] for p in render.await_args.kwargs['parts']]==['Donny','Adele']
        assert '<@2>' in render.await_args.args[0].description and '**9/10**' in render.await_args.args[0].description
        assert [await store.get_progression(uid) for uid in ('1','2')]==before
    asyncio.run(run())


def test_live_together_send_resolves_and_retains_member_profiles(monkeypatch):
    from test_notification_preview import bot
    from trackerbot.core.activity_details import current_post
    import trackerbot.presentation.activity_cards as cards
    async def run():
        embed,part=example(True)
        parts=[part,{**part,'user':'2'}];post={'base':embed.to_dict(),'rendered':embed.to_dict(),'parts':parts}
        monkeypatch.setattr(bot,'prefs',AsyncMock(return_value={'style':'card'}))
        member=lambda uid:SimpleNamespace(display_name='Viewer '+str(uid),display_avatar=SimpleNamespace(url='https://cdn.discordapp.com/avatars/'+str(uid)+'.png'))
        monkeypatch.setattr(bot.bot,'get_guild',lambda _:SimpleNamespace(get_member=member))
        render=AsyncMock(return_value={'embed':None,'file':'group.png','view':'details'})
        monkeypatch.setattr(cards,'activity_card_kwargs',render)
        monkeypatch.setattr(bot.activity_details,'remember',AsyncMock())
        channel=SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=7)))
        token=current_post.set(post)
        try:
            assert await bot.send_embed(channel,embed,'watch')
            assert render.await_args.kwargs['parts'][1]['card_name']=='Viewer 2'
            assert parts[1]['card_avatar'].endswith('/2.png')
            assert post['presentation']=='card' and channel.send.await_args.kwargs['embed'] is None
        finally:current_post.reset(token)
    asyncio.run(run())


def test_together_debug_preview_is_private_and_does_not_poll(monkeypatch):
    from test_notification_preview import bot,_interaction
    from discord import app_commands
    import trackerbot.presentation.activity_cards as cards
    async def run():
        i=_interaction();i.user.display_name='Donny'
        monkeypatch.setattr(bot.storage,'get_activity_provider',AsyncMock(return_value='simkl'))
        monkeypatch.setattr(bot.storage,'get_activity_messages',AsyncMock(return_value=[]))
        render=AsyncMock(return_value={'embed':None,'file':'group.png'})
        monkeypatch.setattr(cards,'activity_card_kwargs',render)
        poll=AsyncMock();monkeypatch.setattr(bot,'poll_providers',poll)
        await bot.simkl_debug.callback(i,app_commands.Choice(name='Watched Together card',value='activity_together'))
        assert i.followup.send.await_args.kwargs['ephemeral']
        assert render.await_args.kwargs['preview'] and render.await_args.kwargs['sample']
        assert len(render.await_args.kwargs['parts'])==2
        assert render.await_args.args[1]['preview_owner']=='42'
        poll.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize('status,verb',[('planning','Planned to watch'),('dropped','Dropped'),('paused','Paused'),('watching','Started watching'),('completed','Completed')])
def test_status_models_are_cards_with_correct_action_and_anime_ratings(status,verb):
    embed,part=example();part['meta'].update(status=status,episodes=[])
    embed.description=f'{verb} **Example Series**\n⭐ IMDb 8.0/10 · 🌸 MAL 9.0/10\n💬 Your series rating: **7/10**'
    model=card_model(embed,part)
    assert eligible([part]) and model['action']==verb
    assert model['ratings']==['IMDb 8.0/10','MAL 9.0/10','Your series rating: 7/10']
    assert not model['episode_title']


def test_visual_status_artwork_never_falls_back_to_poster():
    from test_notification_preview import bot
    args=('shows','Planned to watch',datetime.now(timezone.utc),'Viewer',None)
    for style in ('rich','minimal','card'):
        embed=bot.build_embed(*args,'https://image.tmdb.org/backdrop.jpg',None,poster='https://image.tmdb.org/poster.jpg',
                              logo='https://image.tmdb.org/logo.png',preferences={'style':style,'artwork':'poster'},status_activity=True)
        if style=='card':
            assert embed.image.url.endswith('backdrop.jpg') and embed.thumbnail.url.endswith('logo.png')
            empty=bot.build_embed(*args,None,None,poster='https://image.tmdb.org/poster.jpg',preferences={'style':'card'},status_activity=True)
            assert not empty.image
        elif style=='minimal':assert embed.thumbnail.url.endswith('poster.jpg')
        else:assert embed.image.url.endswith('poster.jpg')


def test_together_debug_resolves_members_from_older_native_records(monkeypatch):
    from test_notification_preview import bot,_interaction
    from discord import app_commands
    import trackerbot.presentation.activity_cards as cards
    async def run():
        i=_interaction();i.user.display_name='Donny'
        embed,part=example(True)
        parts=[dict(part,guild='123',user='7'),dict(part,guild='123',user='42')]
        record={'rendered':embed.to_dict(),'parts':parts}
        monkeypatch.setattr(bot.storage,'get_activity_provider',AsyncMock(return_value='simkl'))
        monkeypatch.setattr(bot.storage,'get_activity_messages',AsyncMock(return_value=[record]))
        member=lambda uid:SimpleNamespace(display_name={7:'adele',42:'donny'}[uid],display_avatar=SimpleNamespace(url=f'https://cdn.discordapp.com/avatars/{uid}.png'))
        monkeypatch.setattr(bot.bot,'get_guild',lambda _:SimpleNamespace(get_member=member))
        render=AsyncMock(return_value={'embed':None,'file':'group.png'})
        monkeypatch.setattr(cards,'activity_card_kwargs',render)
        await bot.simkl_debug.callback(i,app_commands.Choice(name='Watched Together card',value='activity_together'))
        result=render.await_args.kwargs
        assert result['sample'] is False and result['preview'] is True
        assert [p['card_name'] for p in result['parts']]==['adele','donny']
        assert all(p['card_avatar'].startswith('https://cdn.discordapp.com/') for p in result['parts'])
        assert render.await_args.args[1]['preview_owner']=='42'
        assert all('card_name' not in p for p in parts)
        assert i.followup.send.await_args.kwargs['ephemeral']
    asyncio.run(run())


def test_single_card_ratings_share_title_area_without_changing_height():
    from trackerbot.presentation.activity_cards import activity_layout
    embed,part=example(True);model=card_model(embed,part)
    layouts=[activity_layout(dict(model,ratings=ratings)) for ratings in ([],['IMDb 8.0/10'],['IMDb 8.0/10','MAL 8.50/10','Your rating: 9/10'])]
    assert len({l['height'] for l in layouts})==1
    for layout in layouts:
        assert layout['ratings_top']==layout['action_top']
        for (x,row),(_,_,width) in zip(layout['rating_positions'],layout['ratings']):
            assert x>=layout['text_width']+24 and x+width<=984
            assert layout['ratings_top']+row*layout['rating_gap']+layout['rating_height']<layout['notes_top']
    long=activity_layout(dict(model,title='A title with many words that must wrap within its own column'))
    assert len(long['caption_lines'])==2 and long['notes_top']>long['caption_top']+90


def test_visual_default_and_legacy_preferences_remain_explicit(tmp_path,monkeypatch):
    from trackerbot.core import storage as module
    from test_notification_preview import bot
    async def run():
        monkeypatch.setattr(module,'DATA_PATH',str(tmp_path/'styles.json'));store=module.Storage()
        await store.link_user('10','1','token',None,'Viewer','2026-11-20T00:00:00Z')
        assert (await store.get_embed_preferences('10','1'))['style']=='card'
        await store.set_embed_preferences('1',style='rich')
        restarted=module.Storage()
        assert (await restarted.get_embed_preferences('10','1'))['style']=='rich'
        await restarted.set_server_embed_preferences('10',style='minimal',force_override=True)
        assert (await restarted.get_embed_preferences('10','1'))['style']=='minimal'
        await restarted.reset_server_embed_preferences('10')
        assert (await restarted.get_embed_preferences('10','1'))['style']=='rich'
        await restarted.reset_embed_preferences('1')
        assert (await restarted.get_embed_preferences('10','1'))['style']=='card'
        assert {c.value:c.name for c in bot.STYLE_CHOICES}=={'card':'Visual card','rich':'Rich (Legacy)','minimal':'Minimal (Legacy)'}
    asyncio.run(run())
