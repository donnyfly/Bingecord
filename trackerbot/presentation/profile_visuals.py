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
    return {
        "level":level,"rank":rank_for_level(level),"xp":int(progression.get("xp",0)),
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


def render_profile_png(name, data):
    width,height=1080,1065
    image=Image.new("RGB",(width,height),BG)
    draw=ImageDraw.Draw(image)
    prestige=int(data["prestige"])
    accent=accent_for_tier(data["level"],prestige)
    if prestige:
        # A clipped motif behind the header marks the prestige while keeping
        # the statistics panels and their small labels on a solid dark surface.
        header=Image.new("RGB",(984,108),tuple(round(b*0.85+a*0.15) for b,a in zip(BG,accent)))
        header_draw=ImageDraw.Draw(header)
        draw_prestige_backdrop(header_draw,(0,0,984,108),accent,prestige)
        mask=Image.new("L",header.size,0)
        ImageDraw.Draw(mask).rounded_rectangle((0,0,983,107),radius=20,fill=255)
        image.paste(header,(48,24),mask)
        draw=ImageDraw.Draw(image)
    draw.text((48,36),f"{data.get('provider_label', 'TRACKER').upper()} / PROFILE",font=_font(19),fill=accent)
    badge_left=933 if prestige else 960
    name_width=badge_left-48-28
    draw.text((48,67),_short(draw,name,_font(37),name_width),font=_font(37),fill=WHITE)
    if prestige:
        finish, emblem=prestige_style(prestige)
        draw.ellipse((953,42,1019,108),fill=finish,outline=WHITE,width=2)
        _prestige_emblem(draw,986,75,emblem)
    else:
        draw.text((badge_left,72),"P0",font=_font(31),fill=accent)
    draw.rounded_rectangle((48,115,1032,120),radius=2,fill=accent)

    _panel(draw,(48,144,1032,320))
    if prestige:
        draw.rounded_rectangle((49,145,54,319),radius=2,fill=accent)
        draw.arc((815,153,1020,310),190,350,fill=tuple(round(x*0.8+y*0.2) for x,y in zip(PANEL,accent)),width=2)
    draw.text((75,169),f"LEVEL {data['level']}",font=_font(52),fill=WHITE)
    draw.text((75,238),_short(draw,data["rank"],_font(27),460),font=_font(27),fill=accent)
    draw.text((617,172),"CURRENT XP",font=_font(16),fill=MUTED)
    draw.text((617,199),f"{data['xp']:,}",font=_font(38),fill=WHITE)
    progress=1 if data["level"]>=100 else min(1,data["xp_within"]/max(1,data["xp_needed"]))
    draw.rounded_rectangle((617,266,994,274),radius=4,fill=LINE)
    if progress:
        draw.rounded_rectangle((617,266,617+round(377*progress),274),radius=4,fill=accent)
    draw.text((617,285),"MAX LEVEL" if data["level"]>=100 else f"{data['xp_within']:,} / {data['xp_needed']:,} XP to next level",font=_font(15),fill=MUTED)

    _panel(draw,(48,338,1032,542))
    draw.text((75,355),"WATCH HISTORY",font=_font(17),fill=accent)
    fields=[("Total watches",data["total"]),("Episodes",data["episodes"]),("Movies",data["movies"]),
            ("Anime episodes",data["anime_episodes"]),("Anime movies",data["anime_movies"]),("Unique titles",data["unique_titles"])]
    for index,(label,value) in enumerate(fields):
        _metric(draw,75+(index%3)*320,394+(index//3)*75,label,f"{value:,}",WHITE)

    _panel(draw,(48,560,1032,755))
    draw.text((75,578),"MOMENTUM & MILESTONES",font=_font(17),fill=accent)
    fields=[("Current streak",f"{data['current_streak']} days"),("Longest streak",f"{data['longest_streak']} days"),
            ("Achievements",f"{data['achievements']} / {data['achievement_total']}" if data.get("achievements_enabled",True) else "Off"),
            ("Last 30 days",data["last_30"]),("Active days",data["active_days"]),
            ("Lifetime XP",f"{data['lifetime_xp']:,}")]
    for index,(label,value) in enumerate(fields):
        _metric(draw,75+(index%3)*320,615+(index//3)*77,label,value,WHITE)

    _panel(draw,(48,773,1032,895))
    genre=" · ".join(f"{name} ({count:,})" for name,count in data["top_genres"]) or "No genre data yet"
    top=" · ".join(f"{name} ({count:,})" for name,count in data["top_titles"]) or "No titles yet"
    draw.text((75,792),"TOP GENRES",font=_font(15),fill=accent)
    draw.text((75,813),_short(draw,genre,_font(18),925),font=_font(18),fill=WHITE)
    draw.text((75,841),"MOST WATCHED",font=_font(15),fill=accent)
    draw.text((75,862),_short(draw,top,_font(18),925),font=_font(18),fill=WHITE)

    _panel(draw,(48,913,1032,1030))
    draw.text((75,929),"XP BREAKDOWN & RECENT WATCH",font=_font(17),fill=accent)
    draw.text((75,960),f"Watch {data['watch_xp']:,}",font=_font(17),fill=WHITE)
    draw.text((305,960),f"Achievements {data['achievement_xp']:,}",font=_font(17),fill=WHITE)
    draw.text((580,960),f"Challenges {data['challenge_xp']:,}",font=_font(17),fill=WHITE)
    draw.text((810,960),f"Community {data['community_xp']:,}",font=_font(17),fill=WHITE)
    draw.text((75,994),"LATEST",font=_font(15),fill=MUTED)
    draw.text((150,992),_short(draw,data["recent_title"],_font(18),850),font=_font(18),fill=WHITE)

    output=BytesIO()
    image.save(output,format="PNG",optimize=True)
    output.seek(0)
    return output


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
