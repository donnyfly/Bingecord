"""Bundled Inter typography shared by static and animated Bingecord cards."""
from functools import lru_cache
from pathlib import Path
from PIL import ImageFont, features

FONT_DIR=Path(__file__).with_name('fonts')
SHAPING={'features':['tnum']} if features.check_feature('raqm') else {}

@lru_cache(maxsize=256)
def font(size, bold=False, *, weight=None, italic=False):
    size=int(size);weight=int(weight if weight is not None else 700 if bold else 400)
    path=FONT_DIR/('InterVariable-Italic.ttf' if italic else 'InterVariable.ttf')
    face=ImageFont.truetype(str(path),size)
    face.set_variation_by_axes([max(14,min(32,size)),max(100,min(900,weight))])
    return face


def text(draw,xy,value,**kwargs):
    draw.text(xy,str(value),**SHAPING,**kwargs)


def label(draw,xy,value,*,size=14,fill=(172,176,185)):
    # Shape the full string so Inter keeps its kerning and natural word spaces.
    text(draw,xy,str(value).upper(),font=font(size,weight=600),fill=fill)


def unit_label(unit, value):
    """Return a readable count noun rather than blindly stripping a final s."""
    pairs={'minutes':'minute','titles':'title','squares':'square','watches':'watch',
           'episodes':'episode','movies':'movie','genres':'genre','decades':'decade','chapters':'chapter','days':'day'}
    reverse={singular:plural for plural,singular in pairs.items()}
    if value==1:return pairs.get(unit,unit)
    return reverse.get(unit,unit)


def number_unit(draw,xy,value,unit,*,size=39,unit_size=18,fill=(241,243,248),
                unit_fill=(151,158,174),gap=12,width=440):
    """Draw a metric and suffix on one baseline, separated by a measured gap."""
    x,baseline=xy;value=str(value);suffix=font(unit_size)
    suffix_width=draw.textlength(unit,font=suffix,**SHAPING)
    face=font(size,weight=700)
    while size>14 and draw.textlength(value,font=face,**SHAPING)+gap+suffix_width>width:
        size-=1;face=font(size,weight=700)
    unit_x=x+draw.textlength(value,font=face,**SHAPING)+gap
    text(draw,(x,baseline),value,font=face,fill=fill,anchor='ls')
    text(draw,(unit_x,baseline),unit,font=suffix,fill=unit_fill,anchor='ls')
    return unit_x
