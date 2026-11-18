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


def label(draw,xy,value,*,size=14,fill=(172,176,185),spacing=1.2):
    x,y=xy;face=font(size,weight=600)
    for char in str(value).upper():
        text(draw,(x,y),char,font=face,fill=fill)
        x+=draw.textlength(char,font=face,**SHAPING)+spacing
