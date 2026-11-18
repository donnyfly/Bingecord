"""Minimal animated progression cards for Discord notifications.

The renderer uses bundled Inter fonts and makes no remote asset requests.
It returns an in-memory GIF that discord.py can attach directly to a message.
"""

from __future__ import annotations

from io import BytesIO
import colorsys
import math

from PIL import Image, ImageDraw
from trackerbot.core.progression import RANKS, rank_for_level
from trackerbot.presentation.typography import font as card_font, text as card_text


WIDTH = 720
HEIGHT = 280
FRAMES = 32
DURATION_MS = 70

_BG = (12, 14, 20)
_PANEL = (18, 21, 30)
_TEXT = (241, 243, 248)
_MUTED = (145, 151, 166)
_LINE = (42, 47, 61)

# One accent for each rank, ordered by the progression table. Keep the
# palette separate from the XP rules so all levels in a rank share a color.
_RANK_COLORS = (
    (139, 152, 170),  # Newcomer — slate
    (121, 159, 195),  # Casual Watcher — steel blue
    (104, 174, 215),  # Regular Viewer — azure
    (88, 196, 199),   # Binge Watcher — teal
    (107, 197, 160),  # Dedicated Viewer — jade
    (115, 167, 232),  # Media Enthusiast — sapphire
    (154, 140, 232),  # Watch Veteran — amethyst
    (192, 148, 232),  # Watch Master — violet
    (229, 182, 116),  # Watch Legend — champagne gold
    (245, 216, 142),  # Screen Immortal — radiant gold
)
_RANK_ACCENTS = {name: color for (_, name), color in zip(RANKS, _RANK_COLORS)}

# A full ten-rank color wheel for every prestige. The starting point advances
# by two colors per tier: P1 begins orange, P2 pink, P3 purple, and so on.
# Colors repeat after five tiers while the numbered prestige emblems remain
# distinct. Keeping the wheel explicit makes previews and profile cards match.
_PRESTIGE_RANK_COLORS = (
    (255, 142, 74),   # bright orange
    (255, 105, 112),  # electric coral
    (255, 85, 181),   # neon pink
    (225, 83, 220),  # magenta
    (153, 98, 246),  # deep purple
    (109, 115, 255), # indigo
    (82, 168, 255),  # electric blue
    (51, 218, 221),  # turquoise
    (71, 224, 154),  # emerald
    (255, 209, 100), # luminous gold
)

# Prestiges begin beyond the rank palette: pale, luminous finishes rather
# than another saturated rainbow. Later cycles vary their hue while retaining
# the premium finish and the numbered emblem.
_PRESTIGE_FINISHES = (
    (226, 233, 244),  # 1 — moon silver
    (188, 228, 242),  # 2 — opal blue
    (220, 196, 244),  # 3 — royal amethyst
    (175, 235, 221),  # 4 — aurora jade
    (242, 199, 190),  # 5 — rose gold
    (249, 228, 175),  # 6 — celestial gold
)


def accent_for_level(level: int) -> tuple[int, int, int]:
    """Return the shared accent of the rank containing this level."""
    return _RANK_ACCENTS[rank_for_level(level)]


def accent_for_tier(level: int, prestige: int = 0) -> tuple[int, int, int]:
    """Return a rank accent from the selected prestige's rotating palette."""
    prestige=max(0,int(prestige))
    if prestige == 0:
        return accent_for_level(level)
    rank_index=sum(int(level) >= minimum for minimum,_ in RANKS)-1
    return _PRESTIGE_RANK_COLORS[((prestige-1)*2 + max(0,rank_index)) % len(_PRESTIGE_RANK_COLORS)]


def draw_prestige_backdrop(draw: ImageDraw.ImageDraw, box, accent, prestige: int):
    """Add a quiet, prestige-specific geometric backdrop behind card content."""
    left,top,right,bottom=box
    width,height=right-left,bottom-top
    dark=_mix(_PANEL,accent,0.08)
    glow=_mix(_PANEL,accent,0.18)
    motif=(max(1,int(prestige))-1)%6
    # Geometric marks stay in the corners and never obscure card labels.
    if motif == 0:  # rising rays
        for offset in range(0,width,42):
            x=left+offset
            draw.line((x,bottom-3,min(right-3,x+height//2),top+3),fill=dark,width=2)
        draw.arc((right-205,top+8,right-11,min(bottom-8,top+202)),0,310,fill=glow,width=3)
    elif motif == 1:  # nested diamonds
        for radius in (42,76,110):
            cx,cy=right-120,top+height//2
            draw.line(((cx,cy-radius),(cx+radius,cy),(cx,cy+radius),(cx-radius,cy),(cx,cy-radius)),fill=glow,width=2)
        for offset in range(0,width,78):
            draw.line((left+offset,top+4,left+offset+35,top+4),fill=dark,width=3)
    elif motif == 2:  # constellation arcs
        for radius in (52,87,122):
            cx,cy=right-130,top+height//2
            draw.arc((cx-radius,cy-radius,cx+radius,cy+radius),200,350,fill=glow,width=2)
        for offset in range(24,width-15,65):
            draw.ellipse((left+offset,top+18,left+offset+4,top+22),fill=dark)
    elif motif == 3:  # orbit paths
        cx,cy=right-135,top+height//2
        for radius in (35,65,95):
            draw.ellipse((cx-radius,cy-radius//2,cx+radius,cy+radius//2),outline=glow,width=2)
        draw.line((right-245,top+height//2,right-232,top+height//2),fill=dark,width=2)
    elif motif == 4:  # shield facets
        cx,cy=right-132,top+height//2
        for radius in (52,88):
            draw.line(((cx-radius,cy-radius//2),(cx,cy-radius),(cx+radius,cy-radius//2),
                       (cx+radius-15,cy+radius//2),(cx,cy+radius),(cx-radius+15,cy+radius//2),
                       (cx-radius,cy-radius//2)),fill=glow,width=2)
        for offset in range(0,width,62):
            draw.line((left+offset,bottom-8,left+offset+24,bottom-8),fill=dark,width=2)
    else:  # sunburst
        cx,cy=right-125,top+height//2
        for index in range(12):
            angle=index*math.pi/6
            x1,y1=cx+round(math.cos(angle)*48),cy+round(math.sin(angle)*48)
            x2,y2=cx+round(math.cos(angle)*100),cy+round(math.sin(angle)*100)
            draw.line((x1,y1,x2,y2),fill=glow,width=2)
        draw.ellipse((cx-30,cy-30,cx+30,cy+30),outline=glow,width=3)


def prestige_style(prestige: int) -> tuple[tuple[int, int, int], int]:
    """A luminous finish and numbered emblem for each prestige."""
    prestige = max(1, int(prestige))
    index = (prestige - 1) % len(_PRESTIGE_FINISHES)
    cycle = (prestige - 1) // len(_PRESTIGE_FINISHES)
    base = _PRESTIGE_FINISHES[index]
    if not cycle:
        return base, index
    hue, saturation, _ = colorsys.rgb_to_hsv(*(channel / 255 for channel in base))
    hue = (hue + cycle * 0.61803398875) % 1.0
    rgb = colorsys.hsv_to_rgb(hue, saturation, 0.97)
    return tuple(round(channel * 255) for channel in rgb), index


def _prestige_emblem(draw, cx, cy, style, *, scale=1, ink=(25,30,43)):
    """Consistent monoline geometry, scaled before rasterisation."""
    def points(values):
        return [(round(cx+x*scale),round(cy+y*scale)) for x,y in values]
    def line(values,width=2.4):
        draw.line(points(values),fill=ink,width=max(1,round(width*scale)),joint='curve')
    def ellipse(box,fill=False,width=2.4):
        coordinates=[round(cx+box[0]*scale),round(cy+box[1]*scale),
                     round(cx+box[2]*scale),round(cy+box[3]*scale)]
        draw.ellipse(coordinates,fill=ink if fill else None,outline=None if fill else ink,
                     width=max(1,round(width*scale)))
    if style==0:  # crown
        line([(-18,-9),(-10,0),(0,-13),(10,0),(18,-9),(14,12),(-14,12),(-18,-9)])
        line([(-13,18),(13,18)])
    elif style==1:  # star
        vertices=[]
        for i in range(10):
            angle=-math.pi/2+i*math.pi/5;radius=20 if i%2==0 else 9
            vertices.append((math.cos(angle)*radius,math.sin(angle)*radius))
        line(vertices+[vertices[0]])
    elif style==2:  # faceted diamond
        line([(0,-20),(18,0),(0,20),(-18,0),(0,-20)])
        line([(-18,0),(18,0)],1.5)
        line([(0,-20),(-6,0),(0,20),(6,0),(0,-20)],1.5)
    elif style==3:  # orbit
        ellipse((-17,-17,17,17));ellipse((-4,-4,4,4),True)
        ellipse((10,-20,19,-11),True)
    elif style==4:  # shield
        line([(0,-20),(17,-12),(14,8),(0,20),(-14,8),(-17,-12),(0,-20)])
        line([(-7,-1),(-1,5),(8,-6)])
    else:  # sun
        ellipse((-9,-9,9,9))
        for i in range(8):
            angle=i*math.pi/4
            line([(math.cos(angle)*15,math.sin(angle)*15),
                  (math.cos(angle)*21,math.sin(angle)*21)])


def draw_prestige_badge(image,cx,cy,prestige,*,radius=35):
    """An antialiased dark seal shared by profile PNGs and reward GIFs."""
    finish,style=prestige_style(prestige)
    diameter=radius*2;factor=4;size=diameter*factor
    badge=Image.new('RGBA',(size,size));draw=ImageDraw.Draw(badge)
    draw.rounded_rectangle((factor,factor,size-factor-1,size-factor-1),
                           radius=round(size*.24),fill=_mix(_BG,finish,.045),
                           outline=_mix(_BG,finish,.65),width=factor)
    _prestige_emblem(draw,size/2,size/2,style,scale=size/70,ink=finish)
    badge=badge.resize((diameter,diameter),Image.Resampling.LANCZOS)
    image.paste(badge,(cx-radius,cy-radius),badge)


def render_prestige_gif(prestige: int) -> BytesIO:
    prestige=max(1,int(prestige));accent,_=prestige_style(prestige)
    frames=[]
    for index in range(FRAMES):
        t=index/max(1,FRAMES-1);reveal=_ease_out_cubic(t/.72)
        image,draw=_reward_frame(accent,reveal,'Prestige unlocked')
        _reward_text(draw,(48,77),'PRESTIGE',14,_MUTED,width=150)
        _reward_text(draw,(48,100),str(prestige),78,_mix(_MUTED,_TEXT,reveal),weight=800,width=150)
        _reward_text(draw,(48,204),'A new chapter',13,accent,width=150)
        _reward_title(draw,'The next chapter')
        _reward_text(draw,(258,173),'XP carries forward',17,_TEXT,width=350)
        _reward_text(draw,(258,204),'Shared progression · All trackers',13,_MUTED,width=405)
        draw_prestige_badge(image,185,90,prestige,radius=18)
        frames.append(image)
    return _reward_gif(frames)


def _ease_out_cubic(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return 1.0 - (1.0 - value) ** 3


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], amount: float):
    amount = max(0.0, min(1.0, amount))
    return tuple(round(x + (y - x) * amount) for x, y in zip(a, b))


def _reward_text(draw,xy,value,size,colour,*,width,weight=600):
    from trackerbot.presentation.typography import SHAPING
    value=str(value)
    while size>14 and draw.textlength(value,font=card_font(size,weight=weight),**SHAPING)>width:
        size-=1
    face=card_font(size,weight=weight)
    while value and draw.textlength(value,font=face,**SHAPING)>width:
        value=value[:-2]+'…'
    card_text(draw,xy,value,font=face,fill=colour)


def _reward_frame(accent,reveal,heading,*,tint=None):
    from trackerbot.presentation.typography import label
    base=_mix(_BG,tint or accent,.035)
    image=Image.new('RGB',(WIDTH,HEIGHT),base);draw=ImageDraw.Draw(image)
    label(draw,(30,20),f'Bingecord · {heading}',size=13,fill=accent)
    for box in ((28,60,220,232),(236,60,692,232)):
        draw.rounded_rectangle(box,22,fill=_mix(base,_TEXT,.06),outline=_mix(base,_TEXT,.12),width=1)
    # The sweep shares the existing animation timing and stays clear of text.
    draw.rounded_rectangle((30,247,690,251),2,fill=_LINE)
    draw.rounded_rectangle((30,247,30+max(4,int(660*reveal)),251),2,fill=accent)
    return image,draw


def _reward_title(draw,value):
    from trackerbot.presentation.typography import SHAPING
    words=str(value).split();lines=['']
    for word in words:
        candidate=(lines[-1]+' '+word).strip()
        if lines[-1] and draw.textlength(candidate,font=card_font(27,True),**SHAPING)>405 and len(lines)<2:
            lines.append(word)
        else:lines[-1]=candidate
    for index,line in enumerate(lines):
        _reward_text(draw,(258,101+index*34),line,27,_TEXT,width=405,weight=700)


def _reward_gif(frames):
    # Flat fills and one shared palette avoid GIF gradient banding and colour noise.
    sample=Image.new('RGB',(WIDTH*2,HEIGHT))
    sample.paste(frames[0].convert('RGB'),(0,0))
    sample.paste(frames[-1].convert('RGB'),(WIDTH,0))
    palette=sample.quantize(colors=256,method=Image.Quantize.MEDIANCUT)
    frames=[frame.convert('RGB').quantize(palette=palette,dither=Image.Dither.NONE) for frame in frames]
    output=BytesIO()
    frames[0].save(output,format='GIF',save_all=True,append_images=frames[1:],
                   duration=DURATION_MS,loop=0,optimize=False,disposal=2)
    output.seek(0);return output


def render_level_up_gif(level: int, rank: str, *, previous_level: int | None=None,
                        previous_rank: str | None=None, prestige: int=0,
                        source_label: str | None=None) -> BytesIO:
    """Animated editorial card; progression rules and notification API are unchanged."""
    level=max(1,int(level));previous_level=max(1,int(previous_level or max(1,level-1)))
    prestige=max(0,int(prestige));rank=str(rank or 'Newcomer')
    rank_up=bool(previous_rank and previous_rank!=rank)
    before=accent_for_tier(previous_level,prestige);after=accent_for_tier(level,prestige)
    frames=[]
    for index in range(FRAMES):
        t=index/max(1,FRAMES-1);reveal=_ease_out_cubic(t/.72)
        accent=_mix(before,after,_ease_out_cubic((t-.14)/.55))
        image,draw=_reward_frame(accent,reveal,'Rank up' if rank_up else 'Level up',tint=after)
        _reward_text(draw,(48,77),'LEVEL',14,_MUTED,width=150)
        _reward_text(draw,(48,100),str(previous_level if t<.18 else level),78,
                     _mix(_MUTED,_TEXT,reveal),width=150,weight=800)
        _reward_text(draw,(48,204),f'Prestige {prestige}',13,accent,width=150)
        _reward_title(draw,rank)
        _reward_text(draw,(258,173),'New rank unlocked' if rank_up else 'Keep the story going',17,accent,width=405)
        _reward_text(draw,(258,204),f'Via {source_label}' if source_label else 'Shared progression · All trackers',13,_MUTED,width=405)
        if prestige:
            draw_prestige_badge(image,185,90,prestige,radius=18)
        frames.append(image)
    return _reward_gif(frames)


def _achievement_medallion(image,reveal,pulse):
    """Antialiased trophy and restrained laurels; no gradients or external assets."""
    factor=4;size=144;tile=Image.new('RGBA',(size*factor,size*factor))
    draw=ImageDraw.Draw(tile);gold=(239,193,104);soft=(88,72,44)
    def point(x,y):return (round((72+x)*factor),round((72+y)*factor))
    def line(points,colour=gold,width=2):
        draw.line([point(x,y) for x,y in points],fill=colour,width=round(width*factor),joint='curve')
    def circle(radius,colour,width=1,fill=None):
        draw.ellipse((*point(-radius,-radius),*point(radius,radius)),fill=fill,
                     outline=colour,width=round(width*factor))
    circle(51,soft,fill=(35,30,23))
    circle(45,_mix(soft,gold,.3))
    # The short arcs draw in with the unlock, rather than sweeping across text.
    extent=int(300*reveal)
    if extent:
        radius=55+2*pulse
        draw.arc((*point(-radius,-radius),*point(radius,radius)),210,210+extent,fill=gold,width=round(1.5*factor))
    cup=_mix(soft,gold,.5+.5*reveal)
    line([(-18,-19),(-14,0),(-7,8),(7,8),(14,0),(18,-19),(-18,-19)],cup,3)
    line([(-18,-15),(-28,-15),(-26,-3),(-13,3)],cup,2.5)
    line([(18,-15),(28,-15),(26,-3),(13,3)],cup,2.5)
    line([(0,8),(0,23)],cup,3)
    line([(-14,25),(14,25)],cup,3)
    # Paired leaves give the medal a celebratory silhouette without confetti noise.
    for side in (-1,1):
        for y,x in ((-12,61),(1,63),(14,60)):
            line([(side*x,y+6),(side*(x+5),y),(side*(x+5),y+5)],_mix(soft,gold,.4+.4*reveal),1.5)
    tile=tile.resize((size,size),Image.Resampling.LANCZOS)
    image.paste(tile,(52,60),tile)


def render_achievement_gif(name: str, xp: int) -> BytesIO:
    name=str(name or 'Achievement');xp=max(0,int(xp));accent=(239,193,104)
    frames=[]
    for index in range(FRAMES):
        t=index/max(1,FRAMES-1);reveal=_ease_out_cubic(t/.72)
        image,draw=_reward_frame(accent,reveal,'Achievement unlocked')
        _achievement_medallion(image,reveal,math.sin(t*math.pi)**2)
        _reward_text(draw,(82,204),'ACHIEVED',12,accent,width=130)
        _reward_title(draw,name)
        reward=f'+{xp:,} XP'
        from trackerbot.presentation.typography import SHAPING
        reward_font=card_font(21,weight=700)
        width=min(405,round(draw.textlength(reward,font=reward_font,**SHAPING))+32)
        draw.rounded_rectangle((258,183,258+width,221),12,fill=accent)
        _reward_text(draw,(274,188),reward,21,(28,24,18),width=width-32,weight=700)
        frames.append(image)
    return _reward_gif(frames)
