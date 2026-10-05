"""Render fictional Discord-style README examples; no credentials or network needed."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs' / 'images'
FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
BOLD = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'

def render(filename, author, title, lines, source, color, poster=False):
    im = Image.new('RGB', (960, 660), '#313338')
    d = ImageDraw.Draw(im)
    def text(x,y,s,size=22,fill='#dbdee1',bold=False):
        d.text((x,y),s,font=ImageFont.truetype(BOLD if bold else FONT,size),fill=fill)
    text(30,20,'ILLUSTRATIVE MOCKUP • FICTIONAL TITLES AND SAMPLE RATINGS',15,'#aeb3bb')
    d.ellipse((30,62,78,110),fill=color)
    text(44,70,'T',24,'white',True)
    text(92,66,'Tracker Bot',22,'white',True)
    text(240,71,'APP',12,'#b5baff',True)
    text(290,70,'Today at 20:15',15,'#aeb3bb')
    d.rounded_rectangle((92,112,930,614),radius=8,fill='#2b2d31')
    d.rounded_rectangle((92,112,98,614),radius=3,fill=color)
    text(118,132,author,19,'#ffffff',True)
    text(118,174,title,25,'#8ebaff',True)
    y=220
    for line in lines:
        if line:
            text(118,y,line,20,'#dbdee1',line.startswith('★'))
        y+=34
    # Original geometric artwork represents metadata imagery, not a real title asset.
    if poster:
        box=(744,174,906,482)
    else:
        box=(118,390,906,562)
    d.rounded_rectangle(box,radius=6,fill='#173e55')
    x0,y0,x1,y1=box
    d.ellipse((x1-130,y0+20,x1-35,y0+115),fill='#e6ac6b')
    d.polygon([(x0,y1),(x0+(x1-x0)//3,y0+65),(x0+(x1-x0)//2,y1)],fill='#295a6d')
    d.polygon([(x0+(x1-x0)//3,y1),(x0+int((x1-x0)*.7),y0+90),(x1,y1)],fill='#386f7a')
    if not poster:
        text(136,522,'Sample episode artwork',15,'#d2e8ef')
        d.rounded_rectangle((750,129,906,163),radius=5,fill='#173e55')
        text(762,136,'STARBOUND',16,'#f5d6a8',True)
    text(118,583,source+'  •  Today at 20:15',15,'#aeb3bb')
    OUT.mkdir(parents=True,exist_ok=True)
    im.save(OUT/filename,optimize=True)

render('episode-preview.png','Alex watched on MDBList','Starbound Academy',[
    'Watched S1E01 of Starbound Academy',
    'The first signal',
    '★ IMDb 8.5/10',
    '',
    'NEW  Started watching this series.',
], 'Anime • MDBList', '#ba87ef')
render('together-preview.png','Watched Together','Starbound Academy',[
    '@Alex, @Jordan and @Sam watched S2E01–03',
    'A new horizon → Beyond the gate',
    '★ IMDb 8.3/10 → 8.8/10',
], 'Anime • SIMKL, WeTrakr, MDBList', '#ba87ef')
render('status-preview.png','Jordan updated their WeTrakr activity','Starbound Academy',[
    'Paused Starbound Academy',
    '',
    '★ IMDb 8.4/10',
    '★ MAL 8.2/10',
], 'Anime • WeTrakr', '#ba87ef', poster=True)
