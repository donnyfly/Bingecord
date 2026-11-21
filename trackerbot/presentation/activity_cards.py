"""Optional episode/film cards over the existing, fully resolved activity embed."""
import asyncio
from collections import OrderedDict
from io import BytesIO
from datetime import datetime,timezone
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from pathlib import Path
from functools import lru_cache
import hashlib
import json
import re
import time
from urllib.parse import urlparse,parse_qs
from PIL import Image,ImageDraw,ImageOps,ImageFilter,ImageEnhance
import aiohttp
import discord
from .card_theme import background,panel,cover,fit,short,png
from .typography import font,text as paint,label,SHAPING
from .card_details import details_view

WHITE=(241,243,248);MUTED=(161,166,180);LINE=(48,51,62)
HOSTS={'image.tmdb.org','simkl.in','cdn.simkl.in','simkl.net','cdn.simkl.net','cdn.discordapp.com','media.discordapp.net','discord.com','s4.anilist.co'}
_images=OrderedDict();_cards=OrderedDict();_download_gate=asyncio.Semaphore(4);_render_gate=asyncio.Semaphore(2)


def image_url(value):
    if not isinstance(value,str):return None
    parsed=urlparse(value)
    if parsed.hostname=='wsrv.nl':
        target=(parse_qs(parsed.query).get('url') or [''])[0]
        return image_url(target) if urlparse(target).hostname=='simkl.in' else None
    if parsed.scheme!='https' or parsed.hostname not in HOSTS or parsed.username or parsed.password:return None
    try:
        if parsed.port not in (None,443):return None
    except ValueError:return None
    return value


def clean(value):
    value=re.sub(r'<@!?\d+>','Member',str(value or ''))
    return str(value).replace('**','').replace('`','').strip('* _')


def card_model(embed,part,*,preview=False,sample=False):
    reading=part.get('meta',{}).get('media_type')=='manga'
    lines=[line.strip() for line in (embed.description or '').splitlines() if line.strip()]
    ratings=[];notes=[];episode_title=[];first=lines[0] if lines else ('Read' if reading else 'Watched')
    for line in lines[1:]:
        if re.search(r'\b(IMDb|MAL)\b',line):
            # Episode embeds must never acquire MAL or show-level fallback ratings.
            if 'MAL' not in line or part.get('meta',{}).get('movie') or part.get('meta',{}).get('status'):
                segments=line.split(' · ') if 'IMDb' in line and 'MAL' in line else [line]
                ratings.extend(clean(re.sub(r'^[^\w]+','',segment)) for segment in segments)
        elif 'Your ' in line and 'rating' in line:
            ratings.append(clean(re.sub(r'^[^\w]+','',line)))
        elif line.startswith(('🆕','🔁')) or any(word in line for word in ('Finished Season','Finished **Season','Finished the series','Completed the series','Completed this series','Caught up')):
            notes.append(('NEW · ' if line.startswith('🆕') else '')+clean(re.sub(r'^[^\w]+','',line)))
        else:episode_title.append(clean(line))
    activity='Reading activity' if reading else 'Watch activity'
    title=clean(embed.title) or activity;action=clean(first)
    action=re.sub(r'\s+of\s+'+re.escape(title)+r'\s*$','',action,flags=re.I)
    if part.get('meta',{}).get('movie') or part.get('meta',{}).get('status'):
        action=re.sub(re.escape(title)+r'\s*$','',action,flags=re.I).strip() or ('Read' if reading else 'Watched')
    footer=embed.footer.text or part.get('source','Tracker')
    return {'title':title,'author':clean(embed.author.name) or activity,'action':action,
            'episode_title':' · '.join(episode_title),'ratings':ratings,'notes':notes,'source':footer,
            'image':getattr(embed.image,'url',None),'logo':getattr(embed.thumbnail,'url',None),
            'avatar':getattr(embed.author,'icon_url',None),'timestamp':embed.timestamp.astimezone(timezone.utc).isoformat() if embed.timestamp else None,
            'tracker':str(part.get('source','')).lower(),'timezone':part.get('display_timezone','UTC'),
            'movie':bool(part.get('meta',{}).get('movie')),'reading':reading,'preview':preview,'sample':sample}


def together_model(embed,parts,*,preview=False,sample=False):
    """Keep each member's rating and tracker separate from the public score."""
    names={str(p['user']):p.get('card_name') or 'Viewer '+str(n+1) for n,p in enumerate(parts)}
    description=embed.description or ''
    public=embed.copy()
    public.description='\n'.join(line for line in description.splitlines() if not ('Your ' in line and 'rating' in line))
    public.description=re.sub(r'<@!?(\d+)>',lambda m:names.get(m[1],'Viewer'),public.description)
    model=card_model(public,parts[0],preview=preview,sample=sample)
    first=description.splitlines()[0] if description else ''
    code=re.search(r'\bS\d+E\d+(?:[-–](?:S\d+)?E?\d+)?\b',clean(first),re.I)
    model['action']=('Rewatched' if 'rewatched' in first.lower() else 'Watched')+(' '+code[0] if code else '')
    model['author']='Watched Together';model['avatar']=None;model['members']=[]
    for part in parts[:5]:
        uid=str(part['user']);score=None
        for line in description.splitlines():
            if re.search(r'<@!?'+re.escape(uid)+r'>',line) and 'Your ' in line and 'rating' in line:
                match=re.search(r'(\d+(?:\.\d+)?/\d+(?:\.\d+)?)',line)
                if match:score=match[1]
        model['members'].append({'name':names[uid],'avatar':part.get('card_avatar'),'score':score,'tracker':part['source']})
    model['extra_members']=max(0,len(parts)-5)
    return model


async def _download(session,url):
    url=image_url(url)
    if not url:return None
    now=time.monotonic();saved=_images.get(url)
    if saved and saved[0]>now:
        _images.move_to_end(url);return saved[1]
    result=None
    async with _download_gate:
        try:
            async with session.get(url,allow_redirects=False) as response:
                if response.status!=200:raise ValueError('Image response unavailable')
                blob=bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    blob.extend(chunk)
                    if len(blob)>4_000_000:raise ValueError('Image exceeds card byte limit')
            def decode():
                with Image.open(BytesIO(blob)) as image:
                    if image.width*image.height>12_000_000:return None
                    image.thumbnail((1200,1200));return image.convert('RGBA').copy()
            result=await asyncio.to_thread(decode)
        except Exception:pass
    _images[url]=(now+(21600 if result else 60),result);_images.move_to_end(url)
    while len(_images)>32 or sum(im.width*im.height*4 for _,im in _images.values() if im)>32_000_000:
        _images.popitem(last=False)
    return result


def _wrap(draw,value,size,width,max_lines=2,bold=False):
    face=font(size,bold);words=str(value).split();lines=[];current=''
    for word in words:
        candidate=(current+' '+word).strip()
        if draw.textlength(candidate,font=face)>width and current:
            lines.append(current);current=word
        else:current=candidate
    if current:lines.append(current)
    if len(lines)>max_lines:lines=lines[:max_lines];lines[-1]=short(draw,lines[-1]+'…',size,width,bold)
    return lines or ['']


def cinematic_art(art,size):
    """Keep the complete frame; fill aspect-ratio gaps with its blurred colours."""
    source=art.convert('RGB')
    base=ImageOps.fit(source,size,method=Image.Resampling.LANCZOS)
    base=ImageEnhance.Brightness(base.filter(ImageFilter.GaussianBlur(28))).enhance(.48).convert('RGBA')
    frame=ImageOps.contain(source,size,method=Image.Resampling.LANCZOS)
    # Feather only the narrow seam against the blurred fill; never crop the frame.
    x,y=(size[0]-frame.width)//2,(size[1]-frame.height)//2
    mask=Image.new('L',frame.size,255)
    edge=max(1,min(40,min(frame.size)//2))
    pen=ImageDraw.Draw(mask)
    for n in range(edge):
        alpha=round(255*(n+1)/edge)
        if x:
            pen.line((n,0,n,frame.height-1),fill=alpha)
            pen.line((frame.width-1-n,0,frame.width-1-n,frame.height-1),fill=alpha)
        if y:
            pen.line((0,n,frame.width-1,n),fill=alpha)
            pen.line((0,frame.height-1-n,frame.width-1,frame.height-1-n),fill=alpha)
    base.paste(frame,(x,y),mask)
    return base


def ambient_background(size,accent,art,hero_bottom):
    """Carry subdued backdrop colours through the caption area and footer."""
    if art is None:return background(size,accent)
    palette=art.convert('RGB').resize((8,4),Image.Resampling.BOX)
    field=Image.new('RGB',(48,60));pixels=field.load()
    for y in range(60):
        relative=y*size[1]/59
        distance=abs(relative-hero_bottom)/max(1,size[1])
        strength=.12+.25*max(0,1-distance*1.8)
        for x in range(48):
            column=min(7,x*8//48)
            tint=tuple(sum(palette.getpixel((column,row))[c] for row in (1,2,3))/3 for c in range(3))
            pixels[x,y]=tuple(round(8+tint[c]*strength) for c in range(3))
    return field.filter(ImageFilter.GaussianBlur(5)).resize(size,Image.Resampling.BICUBIC).convert('RGBA')


def artwork_mask(size):
    """Blend full-width artwork into the header and ambient caption area."""
    width,height=size
    fade=Image.new('L',(1,height),255);pixels=fade.load();start=round(height*.58)
    for y in range(start,height):
        t=(y-start)/max(1,height-1-start)
        pixels[0,y]=round(255*(1-t*t*(3-2*t)))
    top_fade=min(200,round(height*.38))
    for y in range(top_fade):
        t=y/max(1,top_fade-1)
        pixels[0,y]=min(pixels[0,y],round(255*t*t*(3-2*t)))
    return fade.resize(size)


def _flows(items,width,gap=12):
    x=0;row=0;positions=[]
    for item_width in items:
        if x and x+item_width>width:x=0;row+=1
        positions.append((x,row));x+=item_width+gap
    return positions,(row+1 if items else 0)


@lru_cache(maxsize=8)
def service_logo(service):
    path=Path(__file__).with_name('service_logos')/(service+'.png')
    if not path.is_file():return None
    with Image.open(path) as image:return image.convert('RGBA').copy()


def rating_service(name,tracker):
    if name.lower().startswith('your'):return tracker if tracker in {'simkl','wetrakr','mdblist'} else None
    if name.lower()=='imdb':return 'imdb'
    if name.lower()=='mal':return 'mal'
    return None


def local_timestamp(model):
    if not model.get('timestamp'):return ''
    try:zone=ZoneInfo(model.get('timezone') or 'UTC')
    except (ValueError,ZoneInfoNotFoundError):zone=ZoneInfo('UTC')
    at=datetime.fromisoformat(model['timestamp']).astimezone(zone)
    offset=at.strftime('%z');offset=offset[:3]+':'+offset[3:]
    name='SGT' if zone.key in {'Asia/Singapore','Singapore'} else at.tzname()
    if not name or name.startswith(('+','-')):name='UTC'+offset
    return at.strftime('%d %b')+' · '+at.strftime('%I:%M %p').lstrip('0')+' '+name


def activity_layout(model):
    """Keep individual status/title and ratings in a stable two-column metadata area."""
    draw=ImageDraw.Draw(Image.new('RGB',(1,1)));ratings=[];notes=[]
    text_width=516
    rating_label_size=15;rating_size=34
    rating_gap=98;rating_height=86
    for rating in model.get('ratings',[])[:3]:
        match=re.search(r'(\d+(?:\.\d+)?/\d+(?:\.\d+)?)',rating)
        name=rating[:match.start()].strip(' :') if match else rating
        name=name.replace('Your episode rating','Your episode').replace('Your series rating','Your series').replace('Your season-entry rating','Your season entry').replace('Your season rating','Your season')
        value=match.group() if match else '—'
        width=min(444,max(132,int(max(draw.textlength(name.upper(),font=font(rating_label_size,weight=600),**SHAPING),draw.textlength(value,font=font(rating_size,True),**SHAPING)))+94))
        ratings.append((name,value,width))
    for note in (model.get('notes') or [])[:3]:
        width=min(984,int(draw.textlength(note,font=font(17,True),**SHAPING))+66)
        notes.append((note,width))
    rating_positions,rating_rows=_flows([r[2] for r in ratings],444)
    row_ends={row:max(x+width for (x,r),(_,_,width) in zip(rating_positions,ratings) if r==row) for _,row in rating_positions}
    rating_positions=[(984-row_ends[row]+x,row) for x,row in rating_positions]
    hero_top=126;hero_height=608;hero_bottom=hero_top+hero_height
    action_lines=_wrap(draw,model['action'],34,text_width,2,True)
    caption_lines=_wrap(draw,model.get('episode_title') or model['title'],40,text_width,2,True)
    action_top=hero_bottom+22;caption_top=action_top+len(action_lines)*42+8
    ratings_height=(rating_rows-1)*rating_gap+rating_height if ratings else 0
    ratings_top=action_top
    caption_bottom=caption_top+len(caption_lines)*48
    ratings_bottom=ratings_top+ratings_height
    metadata_bottom=max(caption_bottom,ratings_bottom)
    members=model.get('members',[])
    note_width=984
    if members:
        notes_top=metadata_bottom+14
    else:
        notes_top=caption_bottom+16
        if notes_top<=ratings_bottom:
            if all(width<=text_width for _,width in notes):
                note_width=text_width
            else:
                notes_top=ratings_bottom+16
    note_positions,note_rows=_flows([width for _,width in notes],note_width)
    if members:
        members_top=notes_top+note_rows*48
    else:
        notes_bottom=notes_top+(note_rows-1)*48+38 if notes else caption_bottom
        members_top=max(metadata_bottom,notes_bottom)
    member_positions=[(index%2*498,index//2) for index in range(len(members))]
    members_height=((len(member_positions)+1)//2)*124-12 if member_positions else 0
    bottom=members_top+members_height+(30 if model.get('extra_members') else 0)
    return dict(ratings=ratings,notes=notes,rating_positions=rating_positions,note_positions=note_positions,
                hero_top=hero_top,hero_height=hero_height,hero_bottom=hero_bottom,
                action_top=action_top,caption_top=caption_top,action_lines=action_lines,caption_lines=caption_lines,
                ratings_top=ratings_top,notes_top=notes_top,members_top=members_top,height=bottom+58,
                text_width=text_width,rating_gap=rating_gap,rating_height=rating_height,
                member_positions=member_positions,members_height=members_height)


def render_reading_png(model, artwork):
    accent=(154,140,232)
    art=artwork.get('image')
    text_x=400
    text_width=632
    size=(1080,640)
    image=background(size,accent,art)
    draw=ImageDraw.Draw(image)

    def text(x, y, value, size, colour=WHITE, bold=False, width=text_width):
        face=fit(draw,value,size,width,bold)
        paint(draw,(x,y),short(draw,value,face.size,width,bold),font=face,fill=colour)

    preview=' · Sample preview' if model.get('sample') else ' · Preview' if model.get('preview') else ''
    label(draw,(48,25),'Bingecord · Reading activity'+preview)
    avatar=artwork.get('avatar')
    if avatar:
        cover(image,avatar,(48,62,92,106),22)
    else:
        draw.ellipse((48,62,92,106),fill=LINE)
        paint(draw,(70,84),model['author'][:1].upper(),font=font(20,True),fill=accent,anchor='mm')
    text(106,68,model['author'],28,bold=True,width=926)
    cover_box=(48,128,364,582)
    if art:
        cover(image,art,cover_box,16,contain=True)
    else:
        panel(image,cover_box,16)
        draw.rounded_rectangle((175,317,237,385),6,outline=accent,width=3)
        draw.line((206,317,206,385),fill=accent,width=3)

    y=158
    for line in _wrap(draw,model['action'],34,text_width,2,True):
        text(text_x,y,line,34,accent,bold=True)
        y+=42
    y+=8
    for line in _wrap(draw,model['title'],46,text_width,3,True):
        text(text_x,y,line,46,bold=True)
        y+=53
    y+=14
    for note in model.get('notes',[])[:3]:
        width=min(text_width,round(draw.textlength(note,font=font(19,True),**SHAPING))+36)
        panel(image,(text_x,y,text_x+width,y+40),12)
        text(text_x+18,y+7,note,19,accent if note.startswith('NEW') else (82,196,166),True,width-36)
        y+=48

    text(48,604,'Manga · AniList',18,MUTED,width=350)
    timestamp=local_timestamp(model)
    if timestamp:
        paint(draw,(1032,604),timestamp,font=font(18),fill=MUTED,anchor='ra')
    return png(image)


def render_activity_png(model,artwork=None):
    if model.get('reading'):
        return render_reading_png(model,artwork or {})
    artwork=artwork or {};accent=(239,190,105) if model['movie'] else (154,140,232)
    layout=activity_layout(model);height=layout['height'];hero=artwork.get('image')
    image=ambient_background((1080,height),accent,hero,layout['hero_bottom']);draw=ImageDraw.Draw(image)
    def text(x,y,value,size=20,colour=WHITE,bold=False,width=900,italic=False):
        face=fit(draw,str(value),size,width,bold)
        if italic:face=font(face.size,italic=True)
        paint(draw,(x,y),short(draw,str(value),face.size,width,bold),font=face,fill=colour)
    label(draw,(48,25),'Bingecord · Watch activity'+(' · Sample preview' if model.get('sample') else ' · Preview' if model.get('preview') else ''))
    avatar=artwork.get('avatar')
    if avatar:cover(image,avatar,(48,62,92,106),22)
    else:
        draw.ellipse((48,62,92,106),fill=LINE)
        if model.get('members'):
            draw.ellipse((58,72,68,82),outline=accent,width=2)
            draw.ellipse((72,72,82,82),outline=accent,width=2)
            draw.arc((53,83,73,100),180,350,fill=accent,width=2)
            draw.arc((67,83,87,100),190,360,fill=accent,width=2)
        else:paint(draw,(70,84),model['author'][:1].upper(),font=font(20,True),fill=accent,anchor='mm')
    text(106,68,model['author'],28,WHITE,True,width=650)
    top=layout['hero_top'];h=layout['hero_height']
    if hero:tile=cinematic_art(hero,(1080,h))
    else:
        tile=background((1080,h),accent)
        td=ImageDraw.Draw(tile);td.rounded_rectangle((425,h//2-48,559,h//2+48),18,outline=accent,width=3)
        td.polygon(((480,h//2-25),(480,h//2+25),(516,h//2)),fill=accent)
    mask=artwork_mask(tile.size) if hero else Image.new('L',tile.size,255)
    image.paste(tile,(0,top),mask)
    logo=artwork.get('logo')
    if logo:
        fitted=ImageOps.contain(logo.convert('RGBA'),(242,76),method=Image.Resampling.LANCZOS)
        image.paste(fitted,(1032-fitted.width,42+(76-fitted.height)//2),fitted)
    else:
        for index,line in enumerate(_wrap(draw,model['title'],24,242,2,True)):
            text(790,48+index*29,line,24,WHITE,True,width=242)
    for index,line in enumerate(layout['action_lines']):
        text(48,layout['action_top']+index*42,line,34,accent,True,width=layout['text_width'])
    for index,line in enumerate(layout['caption_lines']):
        text(48,layout['caption_top']+index*48,line,40,WHITE,True,width=layout['text_width'])
    for (name,value,width),(x,row) in zip(layout['ratings'],layout['rating_positions']):
        x+=48;y=layout['ratings_top']+row*layout['rating_gap']
        panel(image,(x,y,x+width,y+layout['rating_height']),16)
        icon_size=42;text_x=x+70
        service=rating_service(name,model.get('tracker'))
        mark=service_logo(service) if service else None
        if mark:
            fitted=ImageOps.contain(mark,(icon_size,icon_size),method=Image.Resampling.LANCZOS)
            image.paste(fitted,(x+14+(icon_size-fitted.width)//2,y+(layout['rating_height']-fitted.height)//2),fitted)
        label(draw,(text_x,y+11),name,size=15,fill=MUTED)
        text(text_x,y+33,value,34,accent,True,width=width-82)
    for (note,width),(x,row) in zip(layout['notes'],layout['note_positions']):
        x+=48;y=layout['notes_top']+row*48
        colour=accent if note.startswith('NEW') else (82,196,166)
        panel(image,(x,y,x+width,y+38),12)
        draw.ellipse((x+13,y+10,x+31,y+28),outline=colour,width=2)
        if note.startswith('NEW'):
            draw.line((x+22,y+14,x+22,y+24),fill=colour,width=2)
            draw.line((x+17,y+19,x+27,y+19),fill=colour,width=2)
        else:draw.line((x+17,y+19,x+21,y+23,x+27,y+15),fill=colour,width=2)
        text(x+42,y+7,note,17,colour,True,width=width-54)
    for index,(member,(column,row)) in enumerate(zip(model.get('members',[]),layout['member_positions'])):
        x=48+column;y=layout['members_top']+row*124
        panel(image,(x,y,x+486,y+112),18)
        avatar=artwork.get('member:'+str(index))
        if avatar:cover(image,avatar,(x+18,y+22,x+70,y+74),26)
        else:
            draw.ellipse((x+18,y+22,x+70,y+74),fill=LINE)
            paint(draw,(x+44,y+48),member['name'][:1].upper(),font=font(24,True),fill=accent,anchor='mm')
        text(x+88,y+15,member['name'],26,WHITE,True,width=380)
        if member['score']:
            mark=service_logo(member['tracker'])
            if mark:
                fitted=ImageOps.contain(mark,(32,32),method=Image.Resampling.LANCZOS)
                image.paste(fitted,(x+88,y+58),fitted)
            text(x+132,y+53,member['score'],32,accent,True,width=334)
        else:text(x+88,y+60,'No rating shared',18,MUTED,width=380)
    if model.get('extra_members'):
        text(48,layout['members_top']+layout['members_height']+8,'+'+str(model['extra_members'])+' more · View details',18,MUTED)
    footer_y=height-32
    # Draw a media glyph rather than relying on an unsupported emoji font.
    if model['movie']:
        draw.rounded_rectangle((48,footer_y+1,66,footer_y+15),2,outline=MUTED,width=1)
        for x in (51,57,63):
            draw.point((x,footer_y+3),fill=MUTED);draw.point((x,footer_y+13),fill=MUTED)
    else:
        draw.rounded_rectangle((48,footer_y,66,footer_y+12),2,outline=MUTED,width=1)
        draw.line((57,footer_y+12,57,footer_y+15),fill=MUTED);draw.line((52,footer_y+15,62,footer_y+15),fill=MUTED)
    source=re.sub(r'^[^\w]+','',model['source'])
    text(76,footer_y,source,16,MUTED,width=320)
    if model.get('timestamp'):
        paint(draw,(1032,footer_y),local_timestamp(model),font=fit(draw,local_timestamp(model),16,620),fill=MUTED,anchor='ra')
    return png(image)


async def activity_card_kwargs(embed,part,*,preview=False,sample=False,parts=None):
    model=together_model(embed,parts,preview=preview,sample=sample) if parts and len(parts)>1 else card_model(embed,part,preview=preview,sample=sample)
    key=hashlib.sha256(json.dumps(model,sort_keys=True).encode()).hexdigest()
    async with _render_gate:
        data=_cards.get(key)
        if data is None:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session:
                assets=dict(model);keys=('image','logo','avatar')
                for index,member in enumerate(model.get('members',[])):
                    name='member:'+str(index);assets[name]=member['avatar'];keys+= (name,)
                async def load(name):return name,await _download(session,assets.get(name))
                tasks=[asyncio.create_task(load(name)) for name in keys]
                art={}
                try:
                    async with asyncio.timeout(5):
                        for name,value in await asyncio.gather(*tasks):
                            if value is not None:art[name]=value
                except TimeoutError:pass
                finally:
                    for task in tasks:
                        if not task.done():task.cancel()
                    await asyncio.gather(*tasks,return_exceptions=True)
            missing_art=bool(model.get('image') and not art.get('image'))
            if missing_art and not model.get('reading'):
                raise ValueError('Activity artwork is temporarily unavailable')
            data=(await asyncio.to_thread(render_activity_png,model,art)).getvalue()
            if not missing_art:
                _cards[key]=data
                while len(_cards)>16 or sum(map(len,_cards.values()))>24_000_000:_cards.popitem(last=False)
        else:_cards.move_to_end(key)
    view=await details_view(embed,owner=part.get('preview_owner',part.get('user')) if preview else None,guild=part.get('guild'))
    target=str(embed.url or '')
    if urlparse(target).scheme=='https':
        service={'simkl':'SIMKL','wetrakr':'WeTrakr','mdblist':'MDBList','anilist':'AniList'}.get(model['tracker'],'tracker')
        view.add_item(discord.ui.Button(label='View on '+service,url=target))
    # Explicit None also removes the legacy rich embed when refreshing an old card.
    return {'embed':None,'file':discord.File(BytesIO(data),filename='activity.png'),'view':view}



def eligible(parts):
    if any(p.get('meta',{}).get('media_type')=='manga' for p in parts):
        return len(parts)==1 and parts[0].get('source')=='anilist'
    if len(parts)==1 and parts[0].get('meta',{}).get('status'):return True
    return bool(parts) and all(not p.get('meta',{}).get('status') and bool(p.get('meta',{}).get('movie') or p.get('meta',{}).get('episodes')) for p in parts)
