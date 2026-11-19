import asyncio
from datetime import date
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
from trackerbot.core.server_cards import board_facts,recap_facts,title_entries


def stats():
    return {'episodes_watched':3,'movies_watched':1,'anime_episodes_watched':2,'anime_movies_watched':0,
        'watch_dates':{'2026-11-18':{'total':1,'episodes':1},'2026-11-19':{'total':3,'movies':1,'anime_episodes':2},
                       '2099-01-01':{'total':999}},
        'watch_events':{
            'a':{'media_type':'anime_episode','item_key':'series:anime:7:1:1','title':'Anime','ids':{'tmdb':'7'},'watched_at':'2026-11-18T23:00:00Z'},
            'b':{'media_type':'anime_episode','item_key':'series:anime:7:1:2','title':'Anime','ids':{'tmdb':'7'},'watched_at':'2026-11-19T01:00:00Z'},
            'c':{'media_type':'movie','item_key':'film','title':'Film','ids':{'tmdb':'8'},'watched_at':'2026-11-19T01:00:00Z'}},
        'titles':{'series:anime:7:1:1':{'type':'anime_episode','title':'Anime','count':1,'ids':{'tmdb':'7'}},
                  'series:anime:7:1:2':{'type':'anime_episode','title':'Anime','count':1,'ids':{'tmdb':'7'}},
                  'film':{'type':'movie','title':'Film','count':1,'ids':{'tmdb':'8'}}}}


def test_period_rankings_streaks_and_distinct_series():
    s=stats();facts=board_facts(s,'Asia/Singapore',date(2026,11,19))
    assert facts['periods']['all']['total']==4 and facts['periods']['all']['titles']==2
    assert facts['periods']['week']['total']==4 and facts['periods']['week']['episodes']==3
    assert facts['periods']['week']['movies']==1 and facts['periods']['week']['anime']==2
    assert facts['streak']==2
    titles=title_entries(s,start=date(2026,11,19),end=date(2026,11,19),zone='Asia/Singapore')
    assert titles[0]['title']=='Anime' and titles[0]['count']==2


def test_recap_filters_titles_and_charts_to_the_requested_window():
    rows=[{'discord_user_id':'42','simkl_username':'Viewer','statistics':stats()}]
    story=recap_facts(rows,date(2026,11,19),date(2026,11,19),'Asia/Singapore')
    assert story['days']==[('2026-11-19',3)] and story['members'][0]['total']==3
    assert story['titles'][0]['title']=='Anime' and story['titles'][0]['members']==1
    assert recap_facts(rows,date(2026,11,1),date(2026,11,7),'Asia/Singapore')['titles']==[]


def test_leaderboard_dropdown_switches_reset_pages_and_keep_progression_alltime(monkeypatch):
    from test_notification_preview import bot,_interaction
    async def run():
        rows=[dict(discord_user_id=str(n),simkl_username='Viewer '+str(n),xp=100,prestige=1,episodes=10-n,movies=1,anime=2,
                   streak=n+1,periods={'all':{'total':11-n,'episodes':10-n,'movies':1,'anime':2,'titles':n+1},
                   'week':{'total':n,'episodes':n,'movies':0,'anime':0,'titles':min(1,n)}}) for n in range(6)]
        monkeypatch.setattr(bot.storage,'get_guild_leaderboard_snapshot',AsyncMock(return_value=rows))
        monkeypatch.setattr(bot,'feature_enabled',AsyncMock(return_value=True))
        monkeypatch.setattr(bot,'render_leaderboard_png',Mock(return_value=BytesIO(b'png')))
        i=_interaction();i.guild.name='Server';i.guild.get_member=lambda _:None
        await bot.show_leaderboard(i)
        view=i.followup.send.await_args.kwargs['view'];selectors=[c for c in view.children if hasattr(c,'options')]
        category,period=selectors
        assert {'titles','streak','xp'}<={o.value for o in category.options}
        view.page=1;period._values=['week']
        click=SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()),edit_original_response=AsyncMock())
        await period.callback(click)
        assert view.page==0 and view.rows[0]['total']==5 and view.next.disabled
        category._values=['xp'];await category.callback(click)
        assert period.disabled and any(o.value=='all' and o.default for o in period.options)
        assert bot.render_leaderboard_png.call_args.args[1]=='XP progression'
        assert all(r['progression_xp']==350100 for r in view.rows)
    asyncio.run(run())


def test_artwork_joins_exact_old_watch_keys_without_changing_statistics():
    from trackerbot.core.server_cards import with_artwork_ids
    import copy
    s=stats()
    for row in s['watch_events'].values():row.pop('ids',None)
    for row in s['titles'].values():row.pop('ids',None)
    original=copy.deepcopy(s)
    p={'xp_events':[{'event_key':'anime_episode:series:anime:7:1:1:2026-11-18T23:00:00Z',
        'media_type':'anime_episode','at':'2026-11-18T23:00:00Z','ids':{'tmdb':'7'}}]}
    joined=with_artwork_ids(s,p)
    assert joined['titles']['series:anime:7:1:1']['ids']=={'tmdb':'7'}
    assert s==original
    facts=board_facts(s,'UTC',date(2026,11,19),p)
    assert facts['periods']['all']['featured'][0]['ids']=={'tmdb':'7'}
