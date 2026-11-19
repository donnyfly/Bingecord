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
from PIL import Image,ImageDraw,ImageOps
import aiohttp
import discord
from .card_theme import background,panel,cover,fit,short,png
from .typography import font,text as paint,label
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


def render_activity_png(model,artwork=None):
    artwork=artwork or {};accent=(239,190,105) if model['movie'] else (154,140,232)
    notes=(model.get('notes') or [])[:3];height=(770 if model['movie'] and not notes else 850+max(0,len(notes)-1)*28)
    image=background((1080,height),accent);draw=ImageDraw.Draw(image)
    def text(x,y,value,size=20,colour=WHITE,bold=False,width=900,italic=False):
        face=fit(draw,str(value),size,width,bold)
        if italic:face=font(face.size,italic=True)
        paint(draw,(x,y),short(draw,str(value),face.size,width,bold),font=face,fill=colour)
    label(draw,(48,28),'Bingecord · Watch activity'+(' · Sample preview' if model.get('sample') else ' · Preview' if model.get('preview') else ''))
    avatar=artwork.get('avatar')
    if avatar:cover(image,avatar,(48,68,96,116),24)
    else:
        draw.ellipse((48,68,96,116),fill=LINE)
        paint(draw,(72,92),model['author'][:1].upper(),font=font(20,True),fill=accent,anchor='mm')
    text(112,79,model['author'],21,MUTED,width=680)
    logo=artwork.get('logo')
    if logo:
        fitted=ImageOps.contain(logo.convert('RGBA'),(194,90),method=Image.Resampling.LANCZOS)
        image.paste(fitted,(1032-fitted.width,62+(90-fitted.height)//2),fitted)
    for index,line in enumerate(_wrap(draw,model['title'],34,750,2,True)):text(48,132+index*39,line,34,bold=True,width=750)
    hero=artwork.get('image')
    if hero:cover(image,hero,(48,224,784,638),20,contain=True)
    else:
        panel(image,(48,224,784,638),20)
        draw.rounded_rectangle((340,346,492,454),18,outline=accent,width=3)
        draw.polygon(((402,372),(402,429),(442,400)),fill=accent)
        text(74,576,'Artwork unavailable',16,MUTED,width=660)
    panel(image,(804,224,1032,638),20);label(draw,(824,246),'Ratings')
    cursor=286
    if not model['ratings']:
        for index,line in enumerate(_wrap(draw,'No ratings shown',17,182,2)):text(824,cursor+index*23,line,17,MUTED,width=182)
    for rating in model['ratings'][:3]:
        match=re.search(r'(\d+(?:\.\d+)?/\d+(?:\.\d+)?)',rating)
        title=rating[:match.start()].strip(' :') if match else rating
        for line in _wrap(draw,title,14,182,2):text(824,cursor,line,14,MUTED,width=182);cursor+=19
        if match:text(824,cursor,match.group(),29,accent,True,width=182);cursor+=43
        cursor+=15
    text(48,664,model['action'],30,accent,True,width=984)
    if model.get('episode_title'):
        for index,line in enumerate(_wrap(draw,model['episode_title'],20,984,2)):text(48,706+index*25,line,20,MUTED,width=984,italic=True)
    for index,note in enumerate(notes[:3]):text(48,770+index*28,note,18,WHITE,True,width=984)
    text(48,height-29,model['source'],12,MUTED,width=650)
    if model.get('timestamp'):text(710,height-29,model['timestamp'].replace('T',' ')[:16]+' UTC',12,MUTED,width=322)
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
