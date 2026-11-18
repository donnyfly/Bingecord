"""Shared editorial card primitives; renderers own their information hierarchy."""
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps

WHITE=(246,246,247)
MUTED=(172,176,185)

@lru_cache(maxsize=80)
def font(size, bold=False):
    face='DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'
    for path in (Path('/usr/share/fonts/truetype/dejavu')/face,Path(face)):
        try:return ImageFont.truetype(str(path),size)
        except OSError:pass
    return ImageFont.load_default(size=size)

def fit(draw,text,size,width,bold=False):
    text=str(text)
    while size>14 and draw.textlength(text,font=font(size,bold))>width:size-=1
    return font(size,bold)

def short(draw,text,size,width,bold=False):
    text=str(text or '—');face=font(size,bold)
    while text and draw.textlength(text,font=face)>width:
        text=text[:-2]+'…' if not text.endswith('…') else text[:-2]+'…'
    return text

def background(size,accent,art=None):
    # A small colour field keeps this inexpensive even for large output PNGs.
    colours=Image.new('RGB',(48,60));pixels=colours.load()
    tint=art.convert('RGB').resize((1,1)).getpixel((0,0)) if art else accent
    for y in range(60):
        for x in range(48):
            glow=max(0,1-y/43)*.32
            pixels[x,y]=tuple(int(12+glow*(tint[c]*(1-x/65)+accent[c]*x/100)) for c in range(3))
    return colours.resize(size,Image.Resampling.BICUBIC).convert('RGBA')

def panel(image,box,radius=25):
    layer=Image.new('RGBA',image.size)
    ImageDraw.Draw(layer).rounded_rectangle(box,radius,fill=(255,255,255,16),outline=(255,255,255,27),width=1)
    image.paste(Image.alpha_composite(image,layer))

def cover(image,art,box,radius=25,*,contain=False):
    x,y,r,b=box
    if contain:
        tile=Image.new('RGBA',(r-x,b-y),(18,18,20,255))
        fitted=ImageOps.contain(art.convert('RGBA'),tile.size,method=Image.Resampling.LANCZOS)
        tile.paste(fitted,((tile.width-fitted.width)//2,(tile.height-fitted.height)//2),fitted)
    else:
        tile=ImageOps.fit(art.convert('RGBA'),(r-x,b-y),method=Image.Resampling.LANCZOS)
    mask=Image.new('L',tile.size);ImageDraw.Draw(mask).rounded_rectangle((0,0,tile.width-1,tile.height-1),radius,fill=255)
    image.paste(tile,(x,y),mask)

def png(image):
    output=BytesIO();image.convert('RGB').save(output,format='PNG',optimize=True);output.seek(0);return output
