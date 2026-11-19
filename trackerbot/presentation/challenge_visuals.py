"""Personal challenge dashboard, shared by live commands and private previews."""
from datetime import datetime,timezone
from PIL import ImageDraw
from .card_theme import background,panel,cover,fit,short,png
from .typography import text as paint,label,font
from trackerbot.core.personal_challenges import requirement

WHITE=(241,243,248);MUTED=(161,166,180);ACCENT=(154,140,232);GREEN=(82,196,166);LINE=(48,51,62)


def render_challenges_png(name,state,*,avatar=None,posters=None,now=None):
    now=now or datetime.now(timezone.utc);posters=posters or {}
    image=background((1080,1500),ACCENT);draw=ImageDraw.Draw(image)
    def text(x,y,value,size=20,fill=WHITE,bold=False,width=900):
        face=fit(draw,str(value),size,width,bold)
        paint(draw,(x,y),short(draw,str(value),face.size,width,bold),font=face,fill=fill)
    label(draw,(56,36),'Bingecord · Personal challenges'+(' · Preview' if state.get('preview') else ''))
    if avatar:cover(image,avatar,(56,78,116,138),30)
    else:
        draw.ellipse((56,78,116,138),fill=LINE)
        paint(draw,(86,108),str(name)[:1].upper(),font=font(25,True),fill=ACCENT,anchor='mm')
    text(134,78,name,32,bold=True,width=520)
    text(134,120,'Your next watch, with a little extra purpose.',16,MUTED,width=520)
    panel(image,(712,70,1024,164),22);label(draw,(734,85),'Earned this week')
    text(734,109,f"{state.get('earned',0):,} XP",30,ACCENT,True,width=268)
    for index,section in enumerate(state['sections']):
        y=195+index*526;daily=section['scope']=='daily';accent=ACCENT if daily else (121,169,235)
        label(draw,(56,y),'Daily picks' if daily else 'Weekly pursuits',size=17,fill=WHITE)
        end=datetime.fromisoformat(section['end']);hours=max(0,int((end-now).total_seconds()//3600));remaining=f'{hours//24}d {hours%24}h' if hours>=24 else f'{hours}h'
        count=sum(r['done'] for r in section['rows'])
        text(702,y,f'{count} / 3 complete · Resets in {remaining}',14,MUTED,width=322)
        for row_index,row in enumerate(section['rows']):
            top=y+36+row_index*153;done=row['done'];colour=GREEN if done else accent
            panel(image,(56,top,1024,top+137),20)
            text(80,top+15,row['name'],24,colour,True,width=610)
            text(80,top+49,requirement(row),15,MUTED,width=630)
            value=row['target'] if done else row['value'];ratio=min(1,value/max(1,row['target']))
            draw.rounded_rectangle((80,top+87,670,top+94),3,fill=LINE)
            if ratio:draw.rounded_rectangle((80,top+87,80+max(6,int(590*ratio)),top+94),3,fill=colour)
            metric=f"{value:g} / {row['target']}"
            if row.get('pending') and not done:metric+=' · Awaiting title details'
            text(80,top+105,metric,13,MUTED,width=590)
            label(draw,(706,top+20),'Complete' if done and state.get('preview') else 'Earned' if done else 'Bonus',size=12,fill=colour)
            text(706,top+45,f"+{row['xp']:,}",24,colour,True,width=112)
            for n,entry in enumerate(row.get('titles',[])[:2]):
                art=posters.get(entry['canonical']);x=834+n*86
                if art:cover(image,art,(x,top+10,x+74,top+121),10,contain=True)
                else:
                    draw.rounded_rectangle((x,top+10,x+74,top+121),10,fill=LINE)
                    text(x+7,top+49,entry['title'],12,MUTED,width=60)
            if not row.get('titles'):
                draw.ellipse((914,top+40,966,top+92),outline=colour,width=2)
                if done:draw.line((927,top+66,937,top+76,954,top+55),fill=colour,width=3)
                else:paint(draw,(940,top+66),'+' if row['kind']=='minutes' else '?',font=font(27,True),fill=colour,anchor='mm')
    panel(image,(56,1255,1024,1440),22);label(draw,(80,1276),'How your progress counts')
    text(80,1310,'Shared across SIMKL, WeTrakr and MDBList.',21,bold=True,width=900)
    text(80,1346,'Weekly discovery: a film, 3 regular episodes, or a verified season finish.',15,MUTED,width=900)
    text(80,1376,'Daily genre picks need a film or 2 regular episodes. Collection goals count each title once.',14,MUTED,width=900)
    text(56,1460,'Resets at 00:00 UTC · Rewards are additional to watch XP · Removed watches can reverse rewards',12,MUTED,width=968)
    return png(image)
