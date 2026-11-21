"""Manga profile, recap and community cards."""
import asyncio
from zoneinfo import ZoneInfo

import aiohttp
import discord
from PIL import ImageDraw

from trackerbot.core.manga_events import ROTATION, UNITS
from trackerbot.core.reading import STATUS_LABELS
from .activity_cards import _download
from .card_details import standalone_card
from .card_theme import background, cover, fit, panel, png, short
from .discord_artwork import resolve_card_members
from .typography import label, text as paint

ACCENT = (165, 147, 245)
WHITE = (241, 243, 248)
MUTED = (167, 171, 186)
TRACK = (54, 57, 74)


def community_title(state):
    return ROTATION.get(state["kind"], "Community Reading Goal")


async def community_page(guild, state, viewer, *, preview=False, client=None):
    readers = await reader_names(guild, state["contributions"], client)
    unit = UNITS.get(state["kind"], "chapters")
    count = state["contributions"].get(str(viewer), 0)
    personal = state["eligible"].get(str(viewer), 0)
    required = state["minimum"]
    qualification = f"{personal:,} / {required:,} chapters" if unit == "chapters" else (
        "Qualified" if personal >= required else "Contribute to at least one objective"
    )
    eligible = len(state["qualified"])
    deadline = discord.utils.parse_time(state["end"])
    ends = discord.utils.format_dt(deadline, style="F")
    description = (
        f"**{state['total']:,} / {state['target']:,} {unit}** · **{eligible} / {state['required']}** qualifying readers\n"
        f"Ends {ends}\nYour progress: **{count:,} chapters** · {qualification}"
    )
    rules = {
        "manga_genres": "Read 5 chapters of a manga to qualify it. Each title can fill one genre slot.",
        "manga_decades": "Read 5 chapters of a manga to qualify it for its first publication decade.",
        "manga_bingo": "Complete one line with three different titles. Genre, era and shared squares require 5 chapters per reader.",
    }
    if state["kind"] in rules:
        description += "\n" + rules[state["kind"]]
    if state["xp"]:
        description += f"\n**+{state['xp']:,} XP** per qualifying reader if the team completes the goal."
    embed = discord.Embed(title=community_title(state), description=description, color=0xA593F5)
    if state["objectives"]:
        embed.add_field(name="Objectives", value="\n".join(
            ("✓" if o["done"] else "○") + " " + o["label"] for o in state["objectives"]
        ), inline=False)
    if state["pending"]:
        embed.add_field(name="Metadata pending", value=f"{state['pending']} titles awaiting AniList metadata", inline=False)
    add_readers(embed, readers)
    if preview:
        embed.set_footer(text="Private preview · Cached activity")
    titles = state["titles"]
    if titles:
        embed.add_field(name="Manga read this week", value="\n".join(f"{title['title'][:120]} · {title['count']:,} chapters" for title in titles), inline=False)
    covers = await load_covers(titles)
    remaining = max(0, state["target"] - state["total"])
    sections = [("Team goal", "Reached" if state["reached"] else f"{remaining:,} {unit} and {max(0, state['required']-eligible)} qualifiers needed"),
                ("Qualifying readers", f"{eligible} / {state['required']}"),
                ("Your progress", f"{count:,} chapters · {qualification}")]
    if state["xp"]:
        sections.append(("Weekly reward", f"+{state['xp']:,} XP per qualifying reader"))
    if state["pending"]:
        sections.append(("Waiting for AniList metadata", f"{state['pending']} titles"))
    if state["kind"] in rules:
        sections.append(("How to qualify", rules[state["kind"]]))
    art = await asyncio.to_thread(
        render_page, guild.name, embed.title, ("Preview · " if preview else "") + f"Ends {deadline.astimezone(ZoneInfo(state['timezone'])):%d %b %Y}",
        ((f"{state['total']:,} / {state['target']:,}", unit), ("Chapters read", f"{state['chapters']:,}")),
        sections, readers, state["objectives"], titles, covers, min(1, state["total"]/state["target"]),
        state["kind"] == "manga_bingo",
    )
    return await standalone_card(embed, art, "manga-community.png", owner=viewer if preview else None,
                                 guild=guild.id, transient=preview)


async def profile_page(guild, name, stats, *, preview=False, owner=None):
    embed = discord.Embed(title=f"{name}'s Manga Stats", color=0xA593F5)
    if stats is None:
        embed.description = "No synced AniList manga list for this member."
        return {"embed": embed}
    metrics = (("Chapters logged", f"{stats['chapters']:,}"), ("Manga completed", f"{stats['completed']:,}"),
               ("Best reading streak", f"{stats['streak']:,} days"))
    sections = [(label, f"{stats['statuses'][key]:,}") for key, label in STATUS_LABELS.items()]
    for heading, value in (*metrics, *sections):
        embed.add_field(name=heading, value=value)
    art = await asyncio.to_thread(render_page, name, "Manga Stats", "AniList reading profile", metrics, sections)
    return await standalone_card(embed, art, "manga-stats.png", owner=owner, guild=guild.id, transient=preview)


async def recap_page(guild, subtitle, stats, *, preview=False, client=None):
    embed = discord.Embed(title=f"{guild.name} · Manga Weekly Recap", description=subtitle, color=0xA593F5)
    counts = stats["statuses"]
    metrics = (("Chapters logged", f"{stats['chapters']:,}"), ("Manga started", f"{stats['started']:,}"),
               ("Manga finished", f"{counts.get('COMPLETED',0):,}"))
    sections = [("Resumed" if key == "CURRENT" else name, f"{counts[key]:,}") for key, name in STATUS_LABELS.items()
                if key != "COMPLETED" and counts.get(key)]
    readers = await reader_names(guild, stats.get("readers", {}), client)
    for heading, value in (*metrics, *sections):
        embed.add_field(name=heading, value=value)
    add_readers(embed, readers)
    if stats.get("sample"):
        embed.set_footer(text="Sample data")
        subtitle += " · Sample data"
    art = await asyncio.to_thread(render_page, guild.name, "Manga Weekly Recap", subtitle, metrics, sections, readers)
    return await standalone_card(embed, art, "manga-weekly-recap.png", guild=guild.id, transient=preview)


async def reader_names(guild, counts, client):
    ranked = sorted(((uid, value) for uid, value in counts.items() if value), key=lambda pair: (-pair[1], pair[0]))[:5]
    members = await resolve_card_members(guild, [uid for uid, _ in ranked], client)
    return [(members[str(uid)].display_name if str(uid) in members else f"Member {str(uid)[-4:]}", value)
            for uid, value in ranked]


def add_readers(embed, readers):
    if readers:
        embed.add_field(name="Top readers", value="\n".join(
            f"{discord.utils.escape_markdown(name)[:120]} · {count:,} chapters" for name, count in readers
        ), inline=False)


async def load_covers(titles):
    if not titles:
        return []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4)) as session:
        return await asyncio.gather(*(_download(session, title.get("cover")) for title in titles))


def render_page(name, heading, subtitle, metrics, sections, readers=(), objectives=(), titles=(), covers=(), progress=None, bingo=False):
    objectives_height = (206 if bingo else 47+40*len(objectives)) if objectives else 0
    height = 340 + 62*len(sections) + (58+46*len(readers) if readers else 0) + objectives_height + (268 if titles else 0)
    image = background((1080,height), ACCENT)
    draw = ImageDraw.Draw(image)

    def text(x, y, value, size=20, color=WHITE, bold=False, width=940):
        face = fit(draw, value, size, width, bold)
        paint(draw, (x,y), short(draw,value,face.size,width,bold), font=face, fill=color)

    label(draw, (48,28), "Bingecord · Manga", fill=MUTED)
    text(48,63,name,28,bold=True)
    text(48,105,heading,44,ACCENT,True,width=835 if progress is not None else 940)
    text(48,168,subtitle,19,MUTED)
    width = 984//len(metrics)
    for index, (key, value) in enumerate(metrics):
        x = 48+index*width
        panel(image,(x,211,x+width-12,294))
        label(draw,(x+16,222),key,fill=MUTED)
        text(x+16,247,value,27,ACCENT,True,width=width-44)
    y = 310
    if progress is not None:
        draw.ellipse((921,94,1006,179), outline=TRACK, width=8)
        if progress:
            draw.arc((921,94,1006,179), -90, -90+360*progress, fill=ACCENT, width=8)
        text(934,119,f"{progress:.0%}",23,ACCENT,True,width=62)
    for key, value in sections:
        panel(image,(48,y,1032,y+54),12)
        text(64,y+15,key,18,MUTED,width=284)
        text(362,y+14,value,19,WHITE,True,width=641)
        y += 62
    if objectives:
        label(draw,(48,y+8),"Bingo board" if bingo else "Objectives completed",fill=MUTED)
        y += 40
        for index, objective in enumerate(objectives):
            x = 48+(index%3)*330 if bingo else 48
            top = y+(index//3)*52 if bingo else y+index*40
            color = ACCENT if objective["done"] else MUTED
            if bingo:
                panel(image,(x,top,x+316,top+46),10)
            prefix = "✓ " if objective["done"] else "○ "
            text(x+12,top+11,prefix+objective["label"],17,color,objective["done"],width=292 if bingo else 900)
        y += objectives_height-40
    if readers:
        label(draw,(48,y+12),"Top readers",fill=MUTED)
        y += 48
        for index,(reader,count) in enumerate(readers,1):
            text(60,y,f"{index:02d}  {reader}",20,WHITE,True,width=670)
            text(770,y,f"{count:,} chapters",20,ACCENT,width=240)
            y += 46
    if titles:
        label(draw,(48,y+8),"Manga read this week",fill=MUTED)
        for index,(title, image_cover) in enumerate(zip(titles,covers)):
            x = 48 + index*198
            box = (x,y+42,x+126,y+222)
            if image_cover:
                cover(image,image_cover,box,10)
            else:
                panel(image,box,10)
                text(x+9,y+103,title["title"],15,WHITE,width=108)
            text(x,y+232,title["title"],15,WHITE,width=174)
    return png(image)
