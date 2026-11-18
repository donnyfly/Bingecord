"""Fresh, self-contained PNG cards for profile and server leaderboards."""

from collections import Counter
from datetime import date, timedelta
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from trackerbot.core.achievements import ACHIEVEMENTS
from trackerbot.presentation.level_visuals import accent_for_tier, draw_prestige_backdrop, prestige_style, _prestige_emblem
from trackerbot.core.progression import level_progress, rank_for_level

BG=(12,14,20)
PANEL=(19,22,31)
LINE=(43,48,62)
WHITE=(241,243,248)
MUTED=(151,158,174)


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _short(draw, value, font, width):
    value="—" if value is None or value == "" else str(value)
    if draw.textbbox((0,0),value,font=font)[2] <= width:
        return value
    while value and draw.textbbox((0,0),value+"…",font=font)[2] > width:
        value=value[:-1]
    return value+"…"


def _panel(draw, box):
    draw.rounded_rectangle(box,radius=21,fill=PANEL,outline=LINE,width=1)


def _metric(draw, x, y, label, value, accent, width=200):
    draw.text((x,y),label.upper(),font=_font(15),fill=MUTED)
    draw.text((x,y+25),_short(draw,value,_font(29),width),font=_font(29),fill=accent)


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
        draw.text((x,y),value,font=face,fill=colour)
    def label(x,y,value):text(x,y,value.upper(),14,MUTED,True)
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
    face=font(142,True);number=str(data['level']);mask=Image.new('L',(470,180))
    ImageDraw.Draw(mask).text((0,-12),number,font=face,fill=255)
    fill=Image.new('RGBA',mask.size,WHITE)
    if hero:
        from PIL import ImageOps, ImageEnhance
        fill=ImageEnhance.Brightness(ImageOps.fit(hero.convert('RGB'),mask.size)).enhance(1.25).convert('RGBA')
    image.paste(fill,(50,158),mask)
    text(60,302,data['rank'],24,accent,True,width=520)
    label(666,140,'Shared progression')
    text(666,166,f"Prestige {data['prestige']}",32,WHITE,True,width=260)
    finish,emblem=prestige_style(data['prestige'])
    draw.ellipse((946,151,1016,221),fill=finish,outline=WHITE,width=2)
    _prestige_emblem(draw,981,186,emblem)
    text(666,220,f"{data['xp']:,} XP",29,WHITE,True,width=350)
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
    # A clipped fade keeps the artwork readable behind white type.
    fade=Image.new('RGBA',(532,442));fd=ImageDraw.Draw(fade)
    for y in range(442):fd.line((0,y,532,y),fill=(8,10,12,int(230*(y/441)**1.7)))
    rounded=Image.new('L',(532,442));ImageDraw.Draw(rounded).rounded_rectangle((0,0,531,441),25,fill=255)
    fade.putalpha(Image.composite(fade.getchannel('A'),Image.new('L',fade.size),rounded));image.paste(Image.alpha_composite(image.crop(box),fade),(56,501))
    label(82,746,'Most watched title' if featured else 'Your watch history')
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


def render_leaderboard_png(guild_name, category, rows, *, rank_offset=0):
    image=Image.new("RGB",(1080,235+min(len(rows),10)*61),BG)
    draw=ImageDraw.Draw(image)
    accent=(239,190,105)
    draw.text((48,35),"TRACKER / LEADERBOARD",font=_font(19),fill=accent)
    draw.text((48,67),_short(draw,guild_name,_font(35),830),font=_font(35),fill=WHITE)
    draw.text((48,112),category.upper(),font=_font(17),fill=MUTED)
    draw.rounded_rectangle((48,149,1032,154),radius=2,fill=accent)
    draw.text((68,171),"RANK / MEMBER",font=_font(16),fill=MUTED)
    draw.text((488,171),"PRESTIGE / LEVEL",font=_font(16),fill=MUTED)
    draw.text((755,171),"XP / WATCHES",font=_font(16),fill=MUTED)
    for index,row in enumerate(rows[:10]):
        y=207+index*61
        _panel(draw,(48,y,1032,y+54))
        draw.text((67,y+10),f"{rank_offset+index+1:02}",font=_font(25),fill=accent if rank_offset+index<3 else MUTED)
        draw.text((125,y+11),_short(draw,row["name"],_font(22),335),font=_font(22),fill=WHITE)
        draw.text((488,y+13),f"P{row['prestige']}  /  L{row['level']}",font=_font(19),fill=WHITE)
        draw.text((755,y+7),f"{row['xp']:,} XP",font=_font(19),fill=accent)
        draw.text((755,y+30),f"{row['total']:,} watches",font=_font(15),fill=MUTED)
    output=BytesIO()
    image.save(output,format="PNG",optimize=True)
    output.seek(0)
    return output


def render_summary_png(guild_name, heading, subtitle, metrics, leaders):
    """Shared visual language for weekly recaps and all-time server stats."""
    image=Image.new("RGB",(1080,735),BG)
    draw=ImageDraw.Draw(image)
    accent=(239,190,105)
    draw.text((48,35),f"TRACKER / {heading.upper()}",font=_font(19),fill=accent)
    draw.text((48,67),_short(draw,guild_name,_font(35),830),font=_font(35),fill=WHITE)
    draw.text((48,112),_short(draw,subtitle,_font(17),940),font=_font(17),fill=MUTED)
    draw.rounded_rectangle((48,149,1032,154),radius=2,fill=accent)
    _panel(draw,(48,178,1032,405))
    for index,(label,value) in enumerate(metrics[:6]):
        x=76+(index%3)*320
        y=205+(index//3)*103
        _metric(draw,x,y,label,f"{value:,}" if isinstance(value,int) else value,accent,270)
    _panel(draw,(48,429,1032,690))
    draw.text((75,454),"HIGHLIGHTS",font=_font(17),fill=accent)
    for index,(label,value) in enumerate(leaders[:5]):
        y=495+index*38
        draw.text((75,y),_short(draw,label.upper(),_font(15),215),font=_font(15),fill=MUTED)
        draw.text((310,y-2),_short(draw,value,_font(20),690),font=_font(20),fill=WHITE)
    output=BytesIO()
    image.save(output,format="PNG",optimize=True)
    output.seek(0)
    return output
