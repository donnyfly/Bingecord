"""Artwork-led podiums and weekly editorial recaps."""
from datetime import date
from PIL import Image,ImageDraw,ImageEnhance
from trackerbot.core.reading import STATUS_LABELS
from .card_theme import background,panel,cover,fit,short,png
from .typography import text as paint,label,font

WHITE=(241,243,248);MUTED=(161,166,180);LINE=(48,51,62)
KEYS={'Total watches':'total','Episodes':'episodes','Movies':'movies','Anime':'anime','Manga chapters':'manga','Unique titles':'titles',
      'Current streak':'streak','XP progression':'progression_xp','Level':'level','Prestige':'prestige'}


def _tools(image):
    draw=ImageDraw.Draw(image)
    def text(x,y,value,size=20,colour=WHITE,bold=False,width=900):
        face=fit(draw,str(value),size,width,bold)
        paint(draw,(x,y),short(draw,str(value),face.size,width,bold),font=face,fill=colour)
    return draw,text


def _art_panel(image,art,box,accent):
    if art:
        cover(image,ImageEnhance.Brightness(art.convert('RGB')).enhance(.28),box,24)
        ImageDraw.Draw(image).rounded_rectangle(box,24,outline=accent,width=1)
    else:panel(image,box,24)


def _avatar(image,avatar,box,name,colour):
    if avatar:cover(image,avatar,box,(box[2]-box[0])//2)
    else:
        draw=ImageDraw.Draw(image);draw.ellipse(box,fill=LINE,outline=colour,width=2)
        paint(draw,((box[0]+box[2])//2,(box[1]+box[3])//2),str(name)[:1].upper(),font=font(23,True),fill=colour,anchor='mm')


def render_board(guild_name,category,rows,*,rank_offset=0,avatars=None,show_progression=True,artwork=None,icon=None,period_label='All-time',max_value=None):
    rows=rows[:5];avatars=avatars or {};artwork=artwork or {};key=KEYS.get(category,'total')
    accent=(154,140,232) if key in {'xp','progression_xp','level','prestige'} else (82,196,166) if key=='streak' else (239,190,105)
    podium=rank_offset==0;top_count=min(3,len(rows)) if podium else 0
    height=(600+(len(rows)-top_count)*112) if podium else 250+len(rows)*112
    if not rows:height=360
    image=background((1080,height),accent);draw,text=_tools(image)
    label(draw,(56,36),'Bingecord · Leaderboard')
    if icon:cover(image,icon,(56,76,100,120),14)
    text(116 if icon else 56,74,guild_name,32,bold=True,width=850)
    text(56,127,category,23,accent,True,width=620);text(730,135,period_label,16,MUTED,width=294)
    if not rows:text(56,220,'No activity for this category and period yet.',25,MUTED,width=968)
    maximum=max_value or max((r.get(key,r.get('xp',0)) for r in rows),default=1) or 1
    if podium:
        order=[1,0,2] if top_count==3 else list(range(top_count));w=(968-16*(top_count-1))//max(1,top_count)
        medals=[(239,190,105),(189,202,221),(193,144,113)]
        for slot,index in enumerate(order):
            row=rows[index];x=56+slot*(w+16);y=192+(24 if index else 0);colour=medals[index]
            art=(artwork.get(str(row.get('discord_user_id'))) or {}).get('backdrop')
            _art_panel(image,art,(x,y,x+w,y+308),colour)
            label(draw,(x+20,y+17),f'#{index+1}',size=20,fill=colour)
            uid=str(row.get('discord_user_id',''));cx=x+w//2
            _avatar(image,avatars.get(uid),(cx-37,y+35,cx+37,y+109),row['name'],colour)
            paint(draw,(cx,y+140),short(draw,row['name'],24,w-36,True),font=font(24,True),fill=WHITE,anchor='mm')
            value=row.get(key,row.get('xp',0))
            paint(draw,(cx,y+189),f'{value:,}',font=fit(draw,f'{value:,}',45,w-36,True),fill=colour,anchor='mm')
            paint(draw,(cx,y+225),'progression XP' if key=='progression_xp' else 'days' if key=='streak' else category.lower(),font=font(13),fill=MUTED,anchor='mm')
            subtitle=f"P{row.get('prestige',0)} · Level {row.get('level',1)}" if show_progression else 'Selected tracker history'
            paint(draw,(cx,y+271),subtitle,font=font(15),fill=WHITE,anchor='mm')
    for index,row in enumerate(rows[top_count:]):
        position=rank_offset+top_count+index+1;y=(554 if podium else 188)+index*112
        panel(image,(56,y,1024,y+98),20);colour=accent if position<=3 else MUTED
        text(76,y+24,f'{position:02}',27,colour,True,width=48)
        uid=str(row.get('discord_user_id',''));_avatar(image,avatars.get(uid),(140,y+19,200,y+79),row['name'],accent)
        text(224,y+12,row['name'],24,bold=True,width=430)
        sub=f"P{row.get('prestige',0)} · Level {row.get('level',1)}" if show_progression else 'Selected tracker history'
        text(224,y+46,sub,14,MUTED,width=420)
        value=row.get(key,row.get('xp',0));text(702,y+20,f'{value:,}',32,accent,True,width=198)
        draw.rounded_rectangle((224,y+77,650,y+82),2,fill=LINE)
        if value>0:draw.rounded_rectangle((224,y+77,224+max(4,int(426*value/maximum)),y+82),2,fill=accent)
        poster=(artwork.get(uid) or {}).get('poster')
        if poster:cover(image,poster,(938,y+7,994,y+91),8,contain=True)
    footer='AniList manga chapter progress' if key=='manga' else ('Selected-source watch history · Shared global progression' if show_progression else 'Members use their selected source')
    text(56,height-40,footer,13,MUTED,width=968)
    return png(image)


def render_recap(guild_name,subtitle,metrics,leaders,*,icon=None,story=None,avatars=None,artwork=None):
    story=story or {};avatars=avatars or {};artwork=artwork or {};accent=(121,169,235)
    reading=story.get("reading")
    image=background((1080,1420+(155 if reading else 0)),accent);draw,text=_tools(image)
    label(draw,(56,36),'Bingecord · Weekly recap');text(56,73,guild_name,32,bold=True,width=850)
    text(56,119,subtitle,16,MUTED,width=850)
    if icon:cover(image,icon,(952,48,1024,120),36)
    values=dict(metrics);total=values.get('Total watches',0);members=story.get('members',[])
    for box,title,value in [((56,164,600,346),'Together this week',total),((620,164,1024,346),'Active members',leaders[0][1] if leaders else len(members))]:
        panel(image,box);label(draw,(box[0]+24,box[1]+20),title)
        text(box[0]+24,box[1]+48,f'{value:,}' if isinstance(value,int) else value,76,accent if title=='Active members' else WHITE,True,width=box[2]-box[0]-48)
    for n,(title,value) in enumerate(metrics[1:6]):
        x=56+n*196;panel(image,(x,368,x+184,472),20)
        text(x+16,381,f'{value:,}',32,bold=True,width=152)
        text(x+16,430,'TV episodes' if title=='Episodes' else title.lower(),14,MUTED,width=152)
    entries=story.get('titles',[]);featured=entries[0] if entries else None
    art=artwork.get(featured['key'],{}) if featured else {}
    _art_panel(image,art.get('backdrop') or art.get('poster'),(56,494,600,864),accent)
    label(draw,(80,518),'In the spotlight',fill=WHITE)
    if featured:
        text(80,742,featured['title'],32,bold=True,width=490)
        text(80,802,f"{featured['count']:,} watches · {featured.get('members',1)} members",18,accent,width=490)
    else:
        text(80,684,'A week worth sharing',34,bold=True,width=490)
        text(80,754,'Title artwork appears as watch IDs resolve.',16,MUTED,width=490)
    panel(image,(620,494,1024,864));label(draw,(644,516),'The watch crew')
    for n,row in enumerate(members[:5]):
        y=555+n*58;uid=str(row['discord_user_id'])
        _avatar(image,avatars.get(uid),(644,y,682,y+38),row['name'],accent)
        text(695,y,row['name'],17,bold=True,width=222)
        text(695,y+24,f"{row['total']:,} watch{'es' if row['total']!=1 else ''}",13,MUTED,width=222)
        text(955,y+5,f'{n+1:02}',19,accent,True,width=45)
    if not members:text(644,596,'No activity yet',21,MUTED,width=350)
    panel(image,(56,886,1024,1070));label(draw,(80,906),'Your week in watches')
    days=story.get('days') or [];maximum=max((v for _,v in days),default=1) or 1;w=904/max(1,len(days))
    for n,(day,value) in enumerate(days[:7]):
        x=88+n*w;bar=max(3,70*value/maximum)
        draw.rounded_rectangle((x,1020-bar,x+w-20,1020),4,fill=accent if value else LINE)
        paint(draw,(x+(w-20)/2,1039),date.fromisoformat(day).strftime('%a'),font=font(12),fill=MUTED,anchor='mm')
        paint(draw,(x+(w-20)/2,1010-bar),str(value),font=font(13,True),fill=WHITE,anchor='mm')
    if not days:text(80,968,'Daily activity appears after your next watch.',17,MUTED)
    label(draw,(56,1098),'On the server’s screens')
    for n,entry in enumerate(entries[:6]):
        x=56+n*164;poster=(artwork.get(entry['key']) or {}).get('poster')
        if poster:cover(image,poster,(x,1132,x+144,1348),12,contain=True)
        else:
            panel(image,(x,1132,x+144,1348),12)
            text(x+12,1224,entry['title'],15,MUTED,width=120)
    if not entries:text(56,1190,'No dated title records are available for this period yet.',20,MUTED)
    if reading:
        panel(image,(56,1370,1024,1515))
        label(draw,(80,1390),'Reading · AniList' + (' · Sample data' if reading.get('sample') else ''))
        statuses=reading["statuses"]
        text(80,1416,f"{reading['chapters']:,} chapters logged · {reading['started']:,} started · {statuses.get('COMPLETED',0):,} finished",26,bold=True,width=920)
        changes=" · ".join(f"{statuses[key]:,} {'resumed' if key == 'CURRENT' else name.lower()}" for key,name in STATUS_LABELS.items()
                           if key != "COMPLETED" and statuses.get(key))
        if changes:text(80,1464,changes,16,MUTED,width=920)
    text(56,1536 if reading else 1381,'Each member uses their selected tracker',13,MUTED,width=968)
    return png(image)
