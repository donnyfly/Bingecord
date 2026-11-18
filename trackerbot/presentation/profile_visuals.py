"""Fresh, self-contained PNG cards for profile and server leaderboards."""

from collections import Counter
from datetime import date, timedelta

from PIL import Image, ImageDraw

from trackerbot.core.achievements import ACHIEVEMENTS
from trackerbot.presentation.level_visuals import accent_for_tier, draw_prestige_badge
from trackerbot.core.progression import level_progress, rank_for_level

BG=(12,14,20)
PANEL=(19,22,31)
LINE=(43,48,62)
WHITE=(241,243,248)
MUTED=(151,158,174)


from trackerbot.presentation.typography import text as card_text, label as card_label


def _short(draw, value, font, width):
    value="—" if value is None or value == "" else str(value)
    if draw.textbbox((0,0),value,font=font)[2] <= width:
        return value
    while value and draw.textbbox((0,0),value+"…",font=font)[2] > width:
        value=value[:-1]
    return value+"…"


def profile_snapshot(stats, progression, achievements, current_streak, longest_streak, *, today=None):
    """Summarize persisted state; no external lookups or writes occur here."""
    today=today or date.today()
    titles=stats.get("titles") or {}
    watch_dates=stats.get("watch_dates") or {}
    episodes=int(stats.get("episodes_watched",0))
    movies=int(stats.get("movies_watched",0))
    level,within,needed=level_progress(int(progression.get("xp",0)))
    genres=Counter()
    title_counts=Counter()
    recent=None
    for record in titles.values():
        count=max(0,int(record.get("count",0)))
        name=str(record.get("title") or "Untitled")
        title_counts[name]+=count
        for genre in record.get("genres") or []:
            genres[str(genre)]+=count
        stamp=str(record.get("last_watched") or "")
        if stamp and (recent is None or stamp > recent[0]):
            recent=(stamp,name)
    last_30=sum(int(v.get("total",0)) for day,v in watch_dates.items() if (today-timedelta(days=29)).isoformat() <= day <= today.isoformat())
    events=progression.get("xp_events") or []
    watch_xp=sum(int(e.get("amount",0)) for e in events if e.get("media_type") in {"episode","anime_episode","movie","anime_movie"})
    achievement_xp=sum(int(e.get("amount",0)) for e in events if e.get("media_type")=="achievement")
    challenge_xp=sum(int(c.get("xp",0)) for values in (progression.get("challenge_completions") or {}).values() for c in values.values())
    community_xp=sum(int(value) for value in (progression.get("community_rewards") or {}).values())
    top_genres=sorted(genres.items(),key=lambda item:(-item[1],item[0]))[:3]
    top_titles=sorted(title_counts.items(),key=lambda item:(-item[1],item[0]))[:3]
    grouped={}
    last_watched={}
    missing_genres={}
    events_by_item={row.get('item_key'):row for row in (stats.get('watch_events') or {}).values()}
    progression_ids={}
    for event in events:
        kind=event.get('media_type');stamp=event.get('at');key=event.get('event_key') or ''
        if kind in {'movie','anime_movie','episode','anime_episode'} and stamp and key.startswith(kind+':') and key.endswith(':'+stamp):
            item_key=key[len(kind)+1:-(len(stamp)+1)]
            progression_ids.setdefault(item_key,{}).update(event.get('ids') or {})
    for key,record in titles.items():
        event=events_by_item.get(key) or {}
        pieces=str(key).split(':')
        title_key=':'.join(pieces[:3]) if pieces[0]=='series' and len(pieces)==5 else key
        entry=grouped.setdefault(title_key,{'key':title_key,'title':record.get('title') or 'Untitled','count':0,
                                           'movie':'movie' in str(record.get('type') or event.get('media_type') or ''),'ids':{}})
        entry['count']+=max(0,int(record.get('count',0)))
        if not record.get('genres'):missing_genres[title_key]=missing_genres.get(title_key,0)+max(0,int(record.get('count',0)))
        stamp=str(record.get('last_watched') or event.get('watched_at') or '')
        if stamp:last_watched[title_key]=max(last_watched.get(title_key,''),stamp)
        # Old per-server statistics may lack the external IDs already hydrated
        # in shared progression. Join by the exact watch key, never title text.
        entry['ids']={**entry['ids'],**(progression_ids.get(key) or {}),
                      **(record.get('ids') or {}),**(event.get('ids') or {})}
    featured=sorted(grouped.values(),key=lambda entry:(-entry['count'],entry['key']))[:7]
    recent_titles=[grouped[key] for key in sorted(last_watched,key=lambda key:(last_watched[key],key),reverse=True)[:7]]
    return {
        "featured_titles":featured,"recent_titles":recent_titles,
        "history_titles":list(grouped.values()),"genre_counts":dict(genres),"genre_missing":missing_genres,
        "genre_covered_watches":max(0,episodes+movies-sum(missing_genres.values())),"level":level,"rank":rank_for_level(level),"xp":int(progression.get("xp",0)),
        "xp_within":within,"xp_needed":needed,"prestige":int(progression.get("prestige",0)),
        "lifetime_xp":int(progression.get("lifetime_xp",0)),
        "watch_xp":watch_xp,"achievement_xp":achievement_xp,"challenge_xp":challenge_xp,"community_xp":community_xp,
        "episodes":episodes,"movies":movies,"total":episodes+movies,
        "anime_episodes":int(stats.get("anime_episodes_watched",0)),
        "anime_movies":int(stats.get("anime_movies_watched",0)),
        "current_streak":current_streak,"longest_streak":longest_streak,
        "achievements":len(achievements),"achievement_total":len(ACHIEVEMENTS),
        "unique_titles":len(title_counts),"active_days":len(watch_dates),"last_30":last_30,
        "top_genres":top_genres,"top_titles":top_titles,
        "recent_title":recent[1] if recent else "No recent watch",
    }


def render_profile_png(name, data, artwork=None):
    """Render the editorial profile; optional images never change the numbers."""
    from trackerbot.presentation.card_theme import background, panel, cover, font, fit, short, png
    artwork=artwork or {};featured=data.get('featured_titles') or []
    hero=artwork.get('hero')
    accent=accent_for_tier(data['level'],data['prestige'])
    image=background((1080,1380),accent,hero);draw=ImageDraw.Draw(image)
    def text(x,y,value,size=20,colour=WHITE,bold=False,width=None):
        value=str(value);face=fit(draw,value,size,width,bold) if width else font(size,bold)
        card_text(draw,(x,y),value,font=face,fill=colour)
    def label(x,y,value):card_label(draw,(x,y),value,fill=MUTED)
    def metric(box,value,title,sub=None):
        panel(image,box);x,y,r,b=box;text(x+20,y+16,value,36,WHITE,True,width=r-x-40)
        text(x+20,y+64,title,16,MUTED)
        if sub:text(x+20,y+88,sub,13,MUTED)
    label(56,38,'Bingecord · Profile preview' if data.get('preview') else 'Bingecord · Tracker profile')
    text(56,70,short(draw,name,32,720,True),32,WHITE,True)
    provider=str(data.get('provider_label','Tracker')).upper()
    panel(image,(830,48,1024,93),20);text(850,60,provider,16,accent,True,width=154)
    label(60,132,'Level')
    # Masked type echoes the reference without compromising the smaller labels.
    face=font(142,weight=800);number=str(data['level']);mask=Image.new('L',(470,180))
    ImageDraw.Draw(mask).text((0,-12),number,font=face,fill=255)
    fill=Image.new('RGBA',mask.size,WHITE)
    if hero:
        from PIL import ImageOps, ImageEnhance
        fill=ImageEnhance.Brightness(ImageOps.fit(hero.convert('RGB'),mask.size)).enhance(1.25).convert('RGBA')
    image.paste(fill,(50,158),mask)
    text(60,302,data['rank'],24,accent,True,width=520)
    label(666,140,'Shared progression')
    text(666,166,f"Prestige {data['prestige']}",32,WHITE,True,width=260)
    draw_prestige_badge(image,981,186,data['prestige'])
    card_label(draw,(666,208),'Current prestige XP',size=12,fill=MUTED)
    text(666,229,f"{data['xp']:,}",27,WHITE,True,width=350)
    progress=min(1,data['xp_within']/max(1,data['xp_needed'])) if data['level']<100 else 1
    draw.rounded_rectangle((666,270,1024,280),5,fill=LINE)
    if progress:draw.rounded_rectangle((666,270,666+max(10,int(358*progress)),280),5,fill=accent)
    text(666,291,'MAX LEVEL' if data['level']>=100 else f"{data['xp_within']:,} / {data['xp_needed']:,} to next level",14,MUTED,width=358)
    for idx,(value,title,sub) in enumerate([
        (f"{data['total']:,}",'total watches',None),
        (f"{data['movies']:,}",'movies',f"{data['anime_movies']:,} anime movies included"),
        (f"{data['episodes']:,}",'episodes',f"{data['anime_episodes']:,} anime episodes included"),
        (f"{data['unique_titles']:,}",'unique titles',None)]):
        metric((56+idx*246,354,286+idx*246,479),value,title,sub)
    box=(56,501,588,943)
    if hero:cover(image,hero,box)
    else:panel(image,box)
    # Darken the caption and logo area as well as the bottom count.
    fade=Image.new('RGBA',(532,442));fd=ImageDraw.Draw(fade)
    for y in range(442):fd.line((0,y,532,y),fill=(8,10,12,int(35+205*min(1,y/330)**1.2)))
    rounded=Image.new('L',(532,442));ImageDraw.Draw(rounded).rounded_rectangle((0,0,531,441),25,fill=255)
    fade.putalpha(Image.composite(fade.getchannel('A'),Image.new('L',fade.size),rounded));image.paste(Image.alpha_composite(image.crop(box),fade),(56,501))
    card_label(draw,(82,746),'Most watched title' if featured else 'Your watch history',fill=WHITE)
    title=featured[0]['title'] if featured else 'Your next favourite awaits'
    # Two-line wrapping preserves long anime titles.
    words=title.split();lines=['']
    for word in words:
        if draw.textlength((lines[-1]+' '+word).strip(),font=font(27,True))>470 and len(lines)<2:lines.append(word)
        else:lines[-1]=(lines[-1]+' '+word).strip()
    if artwork.get('logo'):
        logo=artwork['logo'].copy();logo.thumbnail((470,82),Image.Resampling.LANCZOS)
        image.paste(logo.convert('RGBA'),(82,778),logo.convert('RGBA'))
    else:
        for idx,line in enumerate(lines):text(82,778+idx*36,short(draw,line,27,470,True),27,WHITE,True)
    count=featured[0]['count'] if featured else 0
    text(82,872,f"{count:,}",42,WHITE,True,width=150);text(246,897,'watches',17,MUTED)
    panel(image,(608,501,1024,778))
    coverage=min(1,data.get('genre_covered_watches',data['total'])/max(1,data['total']))
    text(634,524,f'TOP GENRES · {coverage:.0%} OF WATCHES MAPPED',13,MUTED,True,width=364)
    if not data['top_genres']:text(634,580,'Metadata refresh pending' if data['total'] else 'No watches yet',19,MUTED)
    for idx,(genre,count) in enumerate(data['top_genres']):
        y=570+idx*66;ratio=min(1,count/max(1,data.get('genre_covered_watches',data['total'])))
        text(634,y,short(draw,genre,19,260,True),19,WHITE,True)
        text(945,y,f"{ratio:.0%}",18,MUTED)
        draw.rounded_rectangle((634,y+31,998,y+38),4,fill=LINE)
        if ratio:draw.rounded_rectangle((634,y+31,634+max(8,int(364*ratio)),y+38),4,fill=accent)
    panel(image,(608,796,1024,943))
    for x,key,title in [(634,'current_streak','Streak'),(830,'achievements','Achievements')]:
        label(x,817,title)
        value=(f"{data[key]} days" if key=='current_streak' else f"{data[key]}/{data['achievement_total']}" if data.get('achievements_enabled',True) else 'Off')
        text(x,849,value,28,WHITE,True,width=170)
    text(634,902,f"Best streak {data['longest_streak']} days · {data['active_days']:,} active days",14,MUTED,width=364)
    label(56,969,'Last watched titles')
    recent_titles=data.get('recent_titles',featured)
    posters=artwork.get('posters') or {}
    for idx,entry in enumerate(recent_titles[:7]):
        x=56+idx*140;art=posters.get(entry['key'])
        if art:cover(image,art,(x,1000,x+128,1192),14,contain=True)
        else:
            panel(image,(x,1000,x+128,1192),14)
            text(x+12,1020,f"{idx+1:02}",27,accent,True)
            text(x+10,1092,short(draw,entry['title'],13,108),13,WHITE)
    if not recent_titles:text(56,1024,'No dated watches yet.',18,MUTED)
    panel(image,(56,1212,1024,1317))
    for idx,(key,title) in enumerate([('watch_xp','Watch XP'),('achievement_xp','Achievements'),('challenge_xp','Challenges'),('community_xp','Community')]):
        x=78+idx*240;label(x,1231,title);text(x,1258,f"{data[key]:,}",24,WHITE,True,width=200)
    text(56,1335,f"{data['lifetime_xp']:,} lifetime XP · {data['last_30']:,} watches in 30 days",14,MUTED)
    return png(image)


def render_leaderboard_png(guild_name, category, rows, *, rank_offset=0, avatars=None, show_progression=True):
    """Five-member editorial pages; the selected category is the primary value."""
    from trackerbot.presentation.card_theme import background, panel, cover, font, fit, short, png
    rows=rows[:5];avatars=avatars or {}
    accent=(239,190,105)
    image=background((1080,250+len(rows)*110),accent);draw=ImageDraw.Draw(image)
    def text(x,y,value,size=20,colour=WHITE,bold=False,width=900):
        card_text(draw,(x,y),value,font=fit(draw,value,size,width,bold),fill=colour)
    card_label(draw,(56,38),'Bingecord · Leaderboard',fill=MUTED)
    text(56,70,short(draw,guild_name,32,968,True),32,bold=True)
    text(56,120,category,19,accent,True)
    card_label(draw,(56,165),'Rank / member',fill=MUTED)
    card_label(draw,(748,165),'Current prestige XP' if category=='XP progression' else category,fill=MUTED)
    key={'Total watches':'total','Episodes':'episodes','Movies':'movies','Anime':'anime',
         'XP progression':'xp','Level':'level','Prestige':'prestige'}.get(category,'total')
    for index,row in enumerate(rows):
        y=200+index*110;position=rank_offset+index+1
        panel(image,(56,y,1024,y+96),22)
        text(76,y+26,f'{position:02}',27,accent if position<=3 else MUTED,True,width=62)
        uid=str(row.get('discord_user_id',''))
        avatar=avatars.get(uid)
        if avatar:cover(image,avatar,(152,y+17,214,y+79),31)
        else:
            draw.ellipse((152,y+17,214,y+79),fill=LINE)
            text(170,y+31,str(row.get('name','?'))[:1].upper(),24,accent,True,width=35)
        text(236,y+15,short(draw,row['name'],24,480,True),24,bold=True,width=480)
        text(236,y+54,f"Prestige {row['prestige']} · Level {row['level']}" if show_progression else "Server watch history",15,MUTED,width=470)
        value=row.get(key,0)
        text(748,y+12,f'{value:,}',32,accent,True,width=250)
        secondary=(f"{row['total']:,} watches" if key in {'xp','level','prestige'}
                   else f"{row['xp']:,} current prestige XP")
        if show_progression:text(748,y+57,secondary,13,MUTED,width=250)
    text(56,211+len(rows)*110,'Server watch counts · Shared XP, levels and prestige' if show_progression else 'Server watch counts · Members use their selected source',14,MUTED)
    return png(image)


def render_summary_png(guild_name, heading, subtitle, metrics, leaders, *, icon=None):
    """Provider-neutral server and weekly cards using the profile's visual language."""
    from trackerbot.presentation.card_theme import background, panel, cover, font, fit, short, png
    accent=(121,159,235) if 'week' in heading.lower() else (239,190,105)
    image=background((1080,980),accent);draw=ImageDraw.Draw(image)
    def text(x,y,value,size=20,colour=WHITE,bold=False,width=900):
        card_text(draw,(x,y),str(value),font=fit(draw,value,size,width,bold),fill=colour)
    card_label(draw,(56,38),f'Bingecord · {heading}',fill=MUTED)
    text(56,70,short(draw,guild_name,32,840,True),32,bold=True)
    text(56,116,short(draw,subtitle,16,900),16,MUTED)
    if icon:cover(image,icon,(952,42,1024,114),36)
    main_label,main_value=metrics[0] if metrics else ('Total watches',0)
    panel(image,(56,160,600,350))
    card_label(draw,(80,182),main_label,fill=MUTED)
    text(80,210,f'{main_value:,}' if isinstance(main_value,int) else main_value,78,WHITE,True,width=492)
    text(80,312,'Across members’ selected trackers',15,MUTED,width=492)
    panel(image,(620,160,1024,350))
    people_label,people_value=leaders[0] if leaders else ('Active users','0')
    card_label(draw,(646,182),people_label,fill=MUTED)
    text(646,210,people_value,70,accent,True,width=350)
    text(646,312,'One count per member',15,MUTED,width=350)
    for idx,(label,value) in enumerate(metrics[1:6]):
        x=56+idx*196;panel(image,(x,372,x+184,486),20)
        text(x+16,387,f'{value:,}' if isinstance(value,int) else value,32,WHITE,True,width=152)
        text(x+16,440,label.lower(),14,MUTED,width=152)
    panel(image,(56,508,1024,926))
    card_label(draw,(82,532),'Community highlights',fill=MUTED)
    highlights=leaders[1:5]
    if not highlights:text(82,596,'No activity recorded for this period.',23,MUTED,width=900)
    for idx,(label,value) in enumerate(highlights):
        y=578+idx*82
        card_label(draw,(82,y),label,fill=accent)
        text(82,y+24,short(draw,value,24,914,True),24,WHITE,True,width=914)
        if idx<len(highlights)-1:draw.line((82,y+69,998,y+69),fill=LINE)
    text(56,944,'Anime totals are included in episodes and movies · Members use their selected source',14,MUTED)
    return png(image)
