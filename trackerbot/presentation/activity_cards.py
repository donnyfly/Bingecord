"""Optional episode/film cards over the existing, fully resolved activity embed."""
import asyncio
from collections import OrderedDict
from io import BytesIO
from datetime import timezone
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
HOSTS={'image.tmdb.org','simkl.in','cdn.simkl.in','simkl.net','cdn.simkl.net','cdn.discordapp.com','media.discordapp.net','discord.com'}
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
    lines=[line.strip() for line in (embed.description or '').splitlines() if line.strip()]
    ratings=[];notes=[];episode_title=[];first=lines[0] if lines else 'Watched'
    for line in lines[1:]:
        if re.search(r'\b(IMDb|MAL)\b',line):
            # Episode embeds must never acquire MAL or show-level fallback ratings.
            if 'MAL' not in line or part.get('meta',{}).get('movie'):ratings.append(clean(re.sub(r'^[^\w]+','',line)))
        elif 'Your ' in line and 'rating' in line:
            ratings.append(clean(re.sub(r'^[^\w]+','',line)))
        elif line.startswith(('🆕','🔁')) or any(word in line for word in ('Finished Season','Finished **Season','Finished the series','Completed the series','Completed this series','Caught up')):
            notes.append(('NEW · ' if line.startswith('🆕') else '')+clean(re.sub(r'^[^\w]+','',line)))
        else:episode_title.append(clean(line))
    title=clean(embed.title) or 'Watch activity';action=clean(first)
    action=re.sub(r'\s+of\s+'+re.escape(title)+r'\s*$','',action,flags=re.I)
    if part.get('meta',{}).get('movie'):
        action=re.sub(re.escape(title)+r'\s*$','',action,flags=re.I).strip() or 'Watched'
    footer=embed.footer.text or part.get('source','Tracker')
    return {'title':title,'author':clean(embed.author.name) or 'Watch activity','action':action,
            'episode_title':' · '.join(episode_title),'ratings':ratings,'notes':notes,'source':footer,
            'image':getattr(embed.image,'url',None),'logo':getattr(embed.thumbnail,'url',None),
            'avatar':getattr(embed.author,'icon_url',None),'timestamp':embed.timestamp.astimezone(timezone.utc).isoformat() if embed.timestamp else None,
            'movie':bool(part.get('meta',{}).get('movie')),'preview':preview,'sample':sample}


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
    base.paste(frame,((size[0]-frame.width)//2,(size[1]-frame.height)//2))
    return base


def _flows(items,width,gap=12):
    x=0;row=0;positions=[]
    for item_width in items:
        if x and x+item_width>width:x=0;row+=1
        positions.append((x,row));x+=item_width+gap
    return positions,(row+1 if items else 0)


def activity_layout(model):
    """Size chips and rows from real text, without reserving empty sidebar space."""
    draw=ImageDraw.Draw(Image.new('RGB',(1,1)));ratings=[];notes=[]
    for rating in model.get('ratings',[])[:3]:
        match=re.search(r'(\d+(?:\.\d+)?/\d+(?:\.\d+)?)',rating)
        name=rating[:match.start()].strip(' :') if match else rating
        name=name.replace('Your episode rating','Your episode').replace('Your series rating','Your series').replace('Your season-entry rating','Your season entry').replace('Your season rating','Your season')
        value=match.group() if match else '—'
        width=min(984,max(132,int(max(draw.textlength(name.upper(),font=font(12,weight=600),**SHAPING),draw.textlength(value,font=font(26,True),**SHAPING)))+36))
        ratings.append((name,value,width))
    for note in (model.get('notes') or [])[:3]:
        width=min(984,int(draw.textlength(note,font=font(17,True),**SHAPING))+66)
        notes.append((note,width))
    rating_positions,rating_rows=_flows([r[2] for r in ratings],984)
    note_positions,note_rows=_flows([n[1] for n in notes],984)
    hero_top=126;hero_height=460 if model['movie'] else 520;hero_bottom=hero_top+hero_height
    ratings_top=hero_bottom+16;ratings_height=rating_rows*76-12 if ratings else 26
    notes_top=ratings_top+ratings_height+14
    bottom=notes_top+(note_rows*48-10 if notes else 0)
    return dict(ratings=ratings,notes=notes,rating_positions=rating_positions,note_positions=note_positions,
                hero_top=hero_top,hero_height=hero_height,hero_bottom=hero_bottom,
                ratings_top=ratings_top,notes_top=notes_top,height=bottom+54)


def render_activity_png(model,artwork=None):
    artwork=artwork or {};accent=(239,190,105) if model['movie'] else (154,140,232)
    layout=activity_layout(model);height=layout['height'];hero=artwork.get('image')
    image=background((1080,height),accent,art=hero);draw=ImageDraw.Draw(image)
    def text(x,y,value,size=20,colour=WHITE,bold=False,width=900,italic=False):
        face=fit(draw,str(value),size,width,bold)
        if italic:face=font(face.size,italic=True)
        paint(draw,(x,y),short(draw,str(value),face.size,width,bold),font=face,fill=colour)
    label(draw,(48,25),'Bingecord · Watch activity'+(' · Sample preview' if model.get('sample') else ' · Preview' if model.get('preview') else ''))
    avatar=artwork.get('avatar')
    if avatar:cover(image,avatar,(48,62,92,106),22)
    else:
        draw.ellipse((48,62,92,106),fill=LINE)
        paint(draw,(70,84),model['author'][:1].upper(),font=font(20,True),fill=accent,anchor='mm')
    text(106,71,model['author'],20,WHITE,width=760)
    top=layout['hero_top'];h=layout['hero_height'];bottom=layout['hero_bottom']
    if hero:tile=cinematic_art(hero,(984,h))
    else:
        tile=background((984,h),accent)
        td=ImageDraw.Draw(tile);td.rounded_rectangle((425,h//2-48,559,h//2+48),18,outline=accent,width=3)
        td.polygon(((480,h//2-25),(480,h//2+25),(516,h//2)),fill=accent)
    # Independently fade the brand and episode/title captions, leaving the
    # centre of the scene unobscured and the source frame uncropped.
    gradient=Image.new('RGBA',(1,h));pixels=gradient.load()
    for y in range(h):
        upper=max(0,1-y/175)*.65
        lower=max(0,(y-(h-190))/190)*.92
        pixels[0,y]=(5,7,12,int(255*max(upper,lower)))
    tile=Image.alpha_composite(tile,gradient.resize(tile.size))
    mask=Image.new('L',tile.size);ImageDraw.Draw(mask).rounded_rectangle((0,0,983,h-1),22,fill=255)
    image.paste(tile,(48,top),mask)
    logo=artwork.get('logo')
    if logo:
        fitted=ImageOps.contain(logo.convert('RGBA'),(430,108),method=Image.Resampling.LANCZOS)
        image.paste(fitted,(72,top+24),fitted)
    else:
        for index,line in enumerate(_wrap(draw,model['title'],36,936,2,True)):
            text(72,top+22+index*43,line,36,WHITE,True,width=936)
    action=model['action'];code=re.search(r'\bS\d+E\d+(?:[-–](?:S\d+)?E?\d+)?\b',action,re.I)
    verb=action[:code.start()].strip() if code else action
    caption=model.get('episode_title')
    badge_y=bottom-(111 if caption else 64)
    verb_width=min(680,int(draw.textlength(verb.upper(),font=font(14,weight=600),**SHAPING))+28)
    draw.rounded_rectangle((72,badge_y,72+verb_width,badge_y+32),10,fill=(12,15,23,255))
    label(draw,(86,badge_y+7),short(draw,verb,14,verb_width-28,True),size=14,fill=WHITE)
    if code:
        value=code.group();x=84+verb_width;w=int(draw.textlength(value,font=font(17,True),**SHAPING))+28
        draw.rounded_rectangle((x,badge_y,x+w,badge_y+32),10,fill=accent)
        text(x+14,badge_y+4,value,17,(14,16,24),True,width=936-(x-72)-28)
    if caption:
        lines=_wrap(draw,caption,30,936,2,True)
        for n,line in enumerate(lines):text(72,bottom-(78 if len(lines)>1 else 64)+n*34,line,30,WHITE,True,width=936)
    for (name,value,width),(x,row) in zip(layout['ratings'],layout['rating_positions']):
        x+=48;y=layout['ratings_top']+row*76
        panel(image,(x,y,x+width,y+64),16)
        label(draw,(x+18,y+9),name,size=12,fill=MUTED)
        text(x+18,y+27,value,26,accent,True,width=width-36)
    if not layout['ratings']:text(48,layout['ratings_top']+3,'No ratings shown',14,MUTED,width=984)
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
    footer_y=height-28
    # Draw a media glyph rather than relying on an unsupported emoji font.
    if model['movie']:
        draw.rounded_rectangle((48,footer_y+1,66,footer_y+15),2,outline=MUTED,width=1)
        for x in (51,57,63):
            draw.point((x,footer_y+3),fill=MUTED);draw.point((x,footer_y+13),fill=MUTED)
    else:
        draw.rounded_rectangle((48,footer_y,66,footer_y+12),2,outline=MUTED,width=1)
        draw.line((57,footer_y+12,57,footer_y+15),fill=MUTED);draw.line((52,footer_y+15,62,footer_y+15),fill=MUTED)
    source=re.sub(r'^[^\w]+','',model['source'])
    text(76,footer_y,source,12,MUTED,width=620)
    if model.get('timestamp'):
        paint(draw,(1032,footer_y),model['timestamp'].replace('T',' ')[:16]+' UTC',font=font(12),fill=MUTED,anchor='ra')
    return png(image)


async def activity_card_kwargs(embed,part,*,preview=False,sample=False):
    model=card_model(embed,part,preview=preview,sample=sample)
    key=hashlib.sha256(json.dumps(model,sort_keys=True).encode()).hexdigest()
    async with _render_gate:
        data=_cards.get(key)
        if data is None:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session:
                keys=('image','logo','avatar')
                async def load(name):return name,await _download(session,model.get(name))
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
            if model.get('image') and not art.get('image'):raise ValueError('Activity artwork is temporarily unavailable')
            data=(await asyncio.to_thread(render_activity_png,model,art)).getvalue()
            _cards[key]=data
            while len(_cards)>16 or sum(map(len,_cards.values()))>24_000_000:_cards.popitem(last=False)
        else:_cards.move_to_end(key)
    compact=embed.copy();compact.description='Preview only · No XP changes.' if preview else None
    compact.clear_fields();compact.set_thumbnail(url=None);compact.set_image(url='attachment://activity.png')
    view=await details_view(embed,owner=part.get('user') if preview else None,guild=part.get('guild'))
    return {'embed':compact,'file':discord.File(BytesIO(data),filename='activity.png'),'view':view}


def eligible(parts):
    return len(parts)==1 and not parts[0].get('meta',{}).get('status') and bool(parts[0].get('meta',{}).get('movie') or parts[0].get('meta',{}).get('episodes'))
