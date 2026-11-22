"""Render the Plan To Watch picker as a short H.264 poster reveal."""
import asyncio
from io import BytesIO
import os
import random
import shutil
import subprocess
import tempfile
from urllib.parse import urlparse

import aiohttp
import discord
from PIL import Image, ImageDraw, ImageEnhance, ImageOps

from .activity_cards import _download
from .card_theme import background, cover, fit, panel, png, short
from .typography import label, text

SIZE = (768, 432)
FPS = 24
ACCENT = (156, 139, 240)
WHITE = (242, 244, 250)
MUTED = (176, 181, 199)
STEP = 120
WINNER = 16


def _poster(entry, artwork):
    tile = Image.new("RGBA", (104, 156), (35, 36, 51))
    if artwork:
        cover(tile, artwork, (0, 0, 104, 156), 10, contain=True)
    else:
        draw = ImageDraw.Draw(tile)
        title = entry["title"]
        face = fit(draw, title, 17, 86, True)
        text(draw, (9, 68), short(draw, title, face.size, 86, True), fill=WHITE, font=face)
    return tile


def _final_card(winner, tile, poster, hero):
    finale = background(SIZE, ACCENT, hero)
    if hero:
        backdrop = ImageOps.fit(hero.convert("RGB"), SIZE)
        finale = Image.blend(finale, ImageEnhance.Brightness(backdrop).enhance(0.28).convert("RGBA"), 0.7)
    panel(finale, (28, 26, 740, 406), 24)
    draw = ImageDraw.Draw(finale)
    label(draw, (58, 52), "Bingecord · Your random pick", size=14, fill=MUTED)
    if poster:
        cover(finale, poster, (58, 90, 244, 369), 15, contain=True)
    else:
        finale.paste(tile.resize((186, 279)), (58, 90))
    title = winner["title"]
    face = fit(draw, title, 42, 430, True)
    text(draw, (270, 200), short(draw, title, face.size, 430, True), font=face, fill=WHITE)
    return finale


def _frames(tiles, finale):
    base = background(SIZE, ACCENT)
    header = ImageDraw.Draw(base)
    label(header, (38, 28), "Bingecord · Plan To Watch", size=13, fill=MUTED)
    panel(base, (38, 116, 730, 332), 20)
    # Keep the selected title out of the reel until the marker lands on it.
    others = tiles[1:] or tiles
    reel = [others[i % len(others)] for i in range(WINNER)] + [tiles[0], *others[:3]]
    final_frame = finale.convert("RGB")
    for frame_number in range(72):
        if frame_number >= 56:
            yield final_frame
            continue
        travel = 1 - (1 - min(1, frame_number / 50)) ** 3
        center = travel * WINNER
        frame = base.copy()
        strip = Image.new("RGBA", (684, 156))
        for index in range(max(0, int(center) - 3), min(len(reel), int(center) + 5)):
            x = round(342 - 52 + (index - center) * STEP)
            strip.paste(reel[index], (x, 0))
        frame.paste(strip, (42, 145), strip)
        draw = ImageDraw.Draw(frame)
        draw.rounded_rectangle((327, 140, 441, 306), radius=13, outline=ACCENT, width=3)
        draw.polygon(((374, 118), (394, 118), (384, 132)), fill=ACCENT)
        label(draw, (38, 361), "Choosing from your watchlist", size=14, fill=MUTED)
        if frame_number >= 50:
            frame = Image.blend(frame, finale, (frame_number - 49) / 7)
        yield frame.convert("RGB")


def render_reveal(winner, entries, posters, hero, limit):
    """Return a video, or a still final reveal if encoding is unavailable."""
    tiles = [_poster(entry, art) for entry, art in zip(entries, posters)]
    finale = _final_card(winner, tiles[0], posters[0], hero)
    encoder = shutil.which("ffmpeg")
    if not encoder:
        return "random-pick.png", png(finale)
    with tempfile.TemporaryDirectory(prefix="bingecord-reveal-") as directory:
        raw_path = os.path.join(directory, "frames.rgb")
        target = os.path.join(directory, "pick.mp4")
        with open(raw_path, "wb") as stream:
            for frame in _frames(tiles, finale):
                stream.write(frame.tobytes())
        try:
            subprocess.run(
                [encoder, "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                 "-s", "768x432", "-r", str(FPS), "-i", raw_path, "-an", "-c:v", "libx264",
                 "-threads", "2", "-preset", "fast", "-crf", "24", "-pix_fmt", "yuv420p",
                 "-movflags", "+faststart", target],
                check=True, timeout=12, capture_output=True,
            )
            if 0 < os.path.getsize(target) <= limit:
                with open(target, "rb") as stream:
                    return "random-pick.mp4", BytesIO(stream.read())
        except (OSError, subprocess.SubprocessError):
            pass
    return "random-pick.png", png(finale)


_RENDER_GATE = asyncio.Semaphore(1)


async def random_reveal(winner, candidates, backdrop, limit):
    others = [item for item in candidates if item is not winner]
    entries = [winner, *random.sample(others, min(7, len(others)))]
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
        art = await asyncio.gather(
            *(_download(session, item.get("poster")) for item in entries),
            _download(session, backdrop),
        )
    async with _RENDER_GATE:
        filename, data = await asyncio.to_thread(render_reveal, winner, entries, art[:-1], art[-1], limit)
    return discord.File(data, filename=filename)


def tracker_link(url, label):
    if not url or urlparse(url).scheme != "https":
        return None
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label=f"View on {label}", url=url))
    return view
