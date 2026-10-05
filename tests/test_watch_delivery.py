import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from trackerbot.core.watch_delivery import WatchActivity, WatchBatch, together_embed


def activity(user, minute=0, *, channel=10, guild='1', key=('episode', 'anime', '99', 2, (3,)), started=False, rewatched=False):
    return WatchActivity(guild,str(user),'anime',SimpleNamespace(id=channel),key,
        (datetime(2026,9,27,tzinfo=timezone.utc)+timedelta(minutes=minute),),
        discord.Embed(title='Title',url='https://simkl.com/anime/99',
                      description='Watched **S2E03**\n*Episode title*'),
        '**S2E03** of **Title**',AsyncMock(),started=started,rewatched=rewatched)


def test_grouping_boundaries_and_unique_users():
    batch=WatchBatch(AsyncMock(return_value=True))
    for item in [activity(1),activity(2,30),activity(3,31),activity(4,60),
                 activity(5,channel=11),activity(6,guild='2'),
                 activity(7,key=('episode','anime','99',3,(3,))),
                 activity(8,key=('episode','anime','99',2,(3,4))),activity(1)]:
        batch.add(item)
    groups=list(batch.groups())
    assert [[x.user for x in g] for g in groups[:3]]==[['1','2'],['1'],['3','4']]
    assert all(len({x.user for x in g})==len(g) for g in groups)
    assert sum(map(len,groups))==9


def test_combined_layout_starts_and_rewatches():
    first=activity(1,started=True)
    second=activity(2,started=True)
    embed=together_embed([first,second])
    assert embed.author.name=='Watched Together'
    assert embed.description.splitlines()[0]=='<@1> and <@2> watched **S2E03** of **Title** together'
    assert '🆕 Both started this series.' in embed.description
    assert embed.url==first.embed.url
    second.started=False
    second.rewatched=True
    embed=together_embed([first,second])
    assert '🆕 <@1> started this series.' in embed.description
    assert '🔁 <@2> rewatched this episode.' in embed.description
    first.rewatched=True
    assert 'rewatched **S2E03**' in together_embed([first,second]).description


def test_combined_watch_keeps_available_title_logo():
    first,second=activity(1),activity(2)
    second.embed.set_thumbnail(url='https://image.tmdb.org/t/p/w500/logo.png')
    combined=together_embed([first,second])
    assert combined.thumbnail.url==second.embed.thumbnail.url
    assert first.embed.thumbnail.url is None


@pytest.mark.parametrize('success',[True,False])
def test_failed_delivery_never_acknowledges_participants(success):
    async def scenario():
        sender=AsyncMock(return_value=success)
        batch=WatchBatch(sender)
        items=[activity(1),activity(2)]
        for item in items: batch.add(item)
        finalizer=AsyncMock()
        batch.finalizers.append(finalizer)
        await batch.deliver()
        sender.assert_awaited_once()
        finalizer.assert_awaited_once()
        for item in items:
            assert item.commit.await_count==int(success)
            assert batch.failed('1',item.user,'anime') is not success
    asyncio.run(scenario())


def test_movies_and_large_groups():
    batch=WatchBatch(AsyncMock(return_value=True))
    for n in range(200):
        item=activity(100000000000000000+n,key=('movie','movies','20'))
        item.movie=True
        item.subject='**Movie**'
        batch.add(item)
    groups=list(batch.groups())
    assert len(groups)==1
    assert len(groups[0])==200
    assert sum(map(len,groups))==200
    assert all(len(together_embed(g).description)<=4096 for g in groups)
