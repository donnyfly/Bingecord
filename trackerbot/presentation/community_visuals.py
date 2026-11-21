"""Editorial community progress and result cards, shared with private previews."""
from PIL import ImageDraw
from .card_theme import background,panel,cover,fit,short,png
from .typography import text as paint,label,font,number_unit,unit_label
from trackerbot.core.community import CHALLENGES
from trackerbot.core.reading import COMMUNITY_MINIMUM, COMMUNITY_TARGET

WHITE=(241,243,248);MUTED=(161,166,180);LINE=(48,51,62)
ACCENTS={'watchathon':(154,140,232),'minutes':(154,140,232),'genres':(82,196,166),
         'bingo':(239,190,105),'decades':(121,169,235)}


def render_community_png(guild_name,state,*,names=None,avatars=None,viewer=None,posters=None,heading=None,server_icon=None):
    names=names or {};avatars=avatars or {};posters=posters or {}
    accent=ACCENTS.get(state.get('kind'),(154,140,232));modern=state.get('version')==2
    reading=state.get("reading")
    reading_only=state.get("kind")=="reading"
    height=480 if reading_only else 1500+(146 if reading else 0)
    image=background((1080,height),accent);draw=ImageDraw.Draw(image)
    def text(x,y,value,size=20,colour=WHITE,bold=False,width=900):
        value=str(value);face=fit(draw,value,size,width,bold)
        paint(draw,(x,y),short(draw,value,face.size,width,bold),font=face,fill=colour)
    def tag(x,y,value,colour=MUTED):label(draw,(x,y),value,fill=colour)
    def number(value):return f'{value:,.1f}'.removesuffix('.0') if isinstance(value,(int,float)) else str(value)
    tag(56,36,'Bingecord · Community event'+(' · Preview' if state.get('preview') else ''))
    if server_icon:cover(image,server_icon,(56,66,100,110),14)
    else:
        draw.rounded_rectangle((56,66,100,110),14,fill=LINE)
        paint(draw,(78,88),str(guild_name)[:1].upper(),font=font(22,True),fill=accent,anchor='mm')
    text(116,68,short(draw,guild_name,30,908,True),30,bold=True,width=908)
    title=state.get('name') or CHALLENGES.get(state.get('kind'),CHALLENGES['minutes'])['name']
    text(56,119,title,37,accent,True,width=968)
    if reading_only:
        chapters=reading["chapters"]
        text(56,172,f"Week of {state['key']} · Chapters logged through AniList",16,MUTED,width=968)
        panel(image,(56,210,1024,438))
        tag(80,230,"Server progress")
        text(80,254,f"{chapters:,} / {COMMUNITY_TARGET:,} chapters",50,accent,True,width=920)
        draw.rounded_rectangle((80,330,1000,341),5,fill=LINE)
        if chapters:
            draw.rounded_rectangle((80,330,80+max(10,int(920*min(1,chapters/COMMUNITY_TARGET))),341),5,fill=accent)
        if viewer:
            count=reading["contributions"].get(str(viewer),0)
            text(80,360,f"You contributed {count:,} {'chapter' if count==1 else 'chapters'}",20,WHITE,width=920)
        if reading["xp"]:
            text(80,398,f"+{reading['xp']:,} XP for {COMMUNITY_MINIMUM}+ chapters if the server reaches the goal",16,MUTED,width=920)
        return png(image)
    status=state.get('status','active')
    caption=heading or {'active':'In progress','goal_reached':'All requirements met · Rewards estimated',
                       'settling':'Sync grace period · Rewards pending','completed':'Event completed · Final rewards',
                       'missed':'Event ended · Requirements not met'}.get(status,status)
    text(56,172,caption,16,MUTED,width=968)
    total=state.get('total',0);target=max(1,state.get('target',1));pool=state.get('pool',0)
    progress=min(1,total/target)
    unit=('minutes' if state.get('kind') in {'watchathon','minutes'} else 'genres' if state.get('kind')=='genres' else 'squares' if state.get('kind')=='bingo' else 'decades')
    if not modern:unit=state.get('unit') or CHALLENGES.get(state.get('kind'),CHALLENGES['minutes'])['unit']+'s'
    panel(image,(56,210,650,390));tag(80,230,'Team progress')
    text(80,255,number(total),65,bold=True,width=280)
    text(370,286,f'/ {number(target)} {unit}',21,MUTED,width=252)
    draw.rounded_rectangle((80,346,626,357),5,fill=LINE)
    if progress:draw.rounded_rectangle((80,346,80+max(10,int(546*progress)),357),5,fill=accent)
    panel(image,(674,210,1024,390));tag(698,230,'Bonus XP pool')
    text(698,263,f'{pool:,}',48,accent,True,width=300)
    text(698,336,'Paid after the week + sync grace' if modern else 'Paid after the week ends',13,MUTED,width=300)
    qualified=state.get('qualified_contributors',0);required=state.get('required_contributors',1)
    for x,value,caption in ((56,f'{qualified} / {required}','qualifying members'),
                            (384,number(state.get('eligible_titles',0)) if modern and unit!='minutes' else number(max(0,target-total)), 'distinct eligible titles' if modern and unit!='minutes' else f'{unit} remaining'),
                            (712,number(state.get('qualifying_minutes',120)) if unit=='minutes' else number(state.get('pending_metadata',0)), 'minutes to qualify' if unit=='minutes' else 'titles awaiting metadata')):
        panel(image,(x,410,x+312,490),20);text(x+18,420,value,30,accent,True,width=276);text(x+18,460,caption,14,MUTED,width=276)
    objectives=state.get('objectives') or []
    panel(image,(56,510,1024,878));tag(80,531,'Watched by the team' if unit=='minutes' else 'Community board' if state.get('kind')=='bingo' else 'What the team needs')
    if state.get('kind')=='bingo':
        for index,objective in enumerate(objectives[:9]):
            x=80+(index%3)*310;y=570+(index//3)*96;done=objective['done']
            draw.rounded_rectangle((x,y,x+294,y+82),14,fill=(38,43,43) if done else (28,29,34),outline=accent if done else LINE)
            text(x+12,y+8,short(draw,objective['label'],15,270,True),15,accent if done else WHITE,True,width=270)
            text(x+12,y+34,'Complete' if done else 'Still needed',12,MUTED,width=270)
            if objective.get('titles'):text(x+12,y+54,short(draw,objective['titles'][0],12,270),12,MUTED,width=270)
    elif objectives:
        for index,objective in enumerate(objectives[:12]):
            x=80+(index%2)*468;y=570+(index//2)*47
            draw.ellipse((x,y+6,x+9,y+15),fill=accent if objective['done'] else LINE)
            suffix=f" · {objective['count']} / {objective['target']} titles" if 'count' in objective else ''
            text(x+22,y,short(draw,objective['label']+suffix,17,420,True),17,accent if objective['done'] else WHITE,True,width=420)
            if objective.get('titles'):text(x+22,y+23,short(draw,objective['titles'][0],11,420),11,MUTED,width=420)
    elif unit=='minutes':
        entries=(state.get('art_entries') or [])[:5]
        for index,entry in enumerate(entries):
            x=80+index*116;art=posters.get(entry.get('canonical'))
            if art:cover(image,art,(x,574,x+104,730),12,contain=True)
            else:
                draw.rounded_rectangle((x,574,x+104,730),12,fill=LINE)
                text(x+9,640,entry.get('title') or 'Watched title',13,width=86)
        if not entries:
            for index,name in enumerate(('Movies','TV series','Anime')):
                x=80+index*194
                draw.rounded_rectangle((x,574,x+178,730),16,fill=LINE)
                draw.rounded_rectangle((x+60,603,x+118,651),10,outline=accent,width=2)
                draw.polygon(((x+84,615),(x+84,639),(x+100,627)),fill=accent)
                paint(draw,(x+89,692),name,font=font(18,True),fill=WHITE,anchor='mm')
        text(80,756,'Team goal reached' if total>=target and qualified>=required else 'Every watch brings the team closer',22,accent,True,width=620)
        text(80,795,f'{number(target)} minutes together · {qualified} / {required} qualifying members',16,MUTED,width=620)
        text(80,833,f"Each member: {state.get('qualifying_minutes',30)} minutes · Movies, TV & anime count",13,MUTED,width=620)
        draw.arc((754,564,994,804),0,360,fill=LINE,width=13)
        if progress:draw.arc((754,564,994,804),-90,-90+360*progress,fill=accent,width=13)
        paint(draw,(874,668),f'{progress:.0%}',font=font(44,True),fill=accent,anchor='mm')
        paint(draw,(874,712),'MINUTE GOAL MET' if total>=target else 'OF TEAM GOAL',font=font(12,weight=600),fill=MUTED,anchor='mm')
    else:
        text(80,580,'Objectives will appear as eligible titles resolve.',20,width=916)
    if modern and unit!='minutes':
        requirement=(f"Each member: {state.get('qualifying_minutes',120)} minutes" if unit=='minutes' else 'Each member: 2 eligible titles' if unit!='squares' else 'Each member: support 2 squares')
        if state.get('kind')=='genres':requirement+=f" · Team titles: {state.get('eligible_titles',0)} / {state['minimum_titles']} · {max(0,target-total):g} new genres needed"
        text(80,846,requirement,13,MUTED,width=916)
    panel(image,(56,900,1024,1254));tag(80,922,'Contributors · Top five')
    contributions=state.get('contributions') or {};rows=sorted(contributions.items(),key=lambda kv:(-kv[1],kv[0]))
    awards=state.get('awards') if status in {'completed','missed'} else state.get('estimated_awards') or {}
    largest=max((value for _,value in rows),default=1) or 1
    for index,(uid,value) in enumerate(rows[:5]):
        y=960+index*52;avatar=avatars.get(uid)
        if avatar:cover(image,avatar,(80,y,116,y+36),18)
        else:
            draw.ellipse((80,y,116,y+36),fill=LINE)
            text(91,y+4,str(index+1),18,accent,True,width=22)
        text(134,y,short(draw,names.get(uid,'Member '+uid[-4:]),19,410,True),19,bold=True,width=410)
        text(134,y+26,f"{number(value)} {unit_label(state.get('unit','minutes' if unit=='minutes' else 'titles'),value)}",12,MUTED,width=410)
        text(660,y+5,f"+{awards[uid]:,} XP" if awards and uid in awards else 'No reward' if status in {'completed','missed'} else 'Not qualified yet' if modern and uid not in state.get('qualified',[]) else 'Awaiting team goal' if modern else 'Settles after deadline',15,accent if awards and uid in awards else MUTED,width=330)
        draw.rounded_rectangle((660,y+33,990,y+37),2,fill=LINE)
        if value>0:draw.rounded_rectangle((660,y+33,660+max(4,int(330*value/largest)),y+37),2,fill=accent)
    if not rows:text(80,982,'Be the first to contribute this week.',21,MUTED)
    if len(rows)>5:text(80,1228,f'+ {len(rows)-5} more contributors',11,MUTED)
    panel(image,(56,1274,1024,1444));tag(80,1295,'Your progress' if viewer else 'Next event')
    if viewer:
        count=contributions.get(str(viewer),0)
        number_unit(draw,(80,1365),number(count),unit_label(state.get('unit',unit),count),fill=accent,unit_fill=MUTED)
        qualifies=str(viewer) in state.get('qualified',[]) if modern else count>=state.get('qualifying_minutes',1)
        text(80,1390,'Qualified for rewards' if qualifies else 'Keep going to qualify',16,MUTED,width=420)
    else:
        text(80,1330,state.get('next_event','Keep watching together'),25,accent,True,width=440)
        text(80,1390,'Rewards are additional to normal watch XP.',13,MUTED,width=440)
    for index,entry in enumerate([] if unit=='minutes' else (state.get('art_entries') or [])[:4]):
        art=posters.get(entry.get('canonical'))
        if art:cover(image,art,(548+index*112,1287,644+index*112,1431),12,contain=True)
    text(56,1461,f"Week of {state.get('key','')} · Next: {state.get('next_event','Community Watchathon')} · No automatic pings",12,MUTED)
    if reading:
        panel(image,(56,1500,1024,1614))
        tag(80,1517,'Community reading goal')
        personal=f" · You contributed {reading['contributions'].get(str(viewer),0):,}" if viewer else ""
        text(80,1546,f"{reading['chapters']:,} / {COMMUNITY_TARGET:,} chapters"+personal,23,accent,True,width=920)
        if reading["xp"]:
            text(80,1580,f"+{reading['xp']:,} XP for {COMMUNITY_MINIMUM}+ chapters if the server reaches the goal",15,MUTED,width=920)
    return png(image)
