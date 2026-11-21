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
from .card_theme import background, fit, panel, png, short
from .community_visuals import ACCENTS, render_community_png
from .discord_artwork import load_discord_artwork, resolve_card_members
from .typography import label, text as paint

ACCENT = (165, 147, 245)
WHITE = (241, 243, 248)
MUTED = (167, 171, 186)
COMMUNITY_THEMES = {"manga_genres": "genres", "manga_bingo": "bingo", "manga_decades": "decades"}
COMMUNITY_RULES = {
    "manga_genres": "Read 5 chapters of a manga to qualify it. Each title can fill one genre slot.",
    "manga_decades": "Read 5 chapters of a manga to qualify it for its first publication decade.",
    "manga_bingo": "Complete one line with three different titles. Genre, era and shared squares require 5 chapters per reader.",
}


def community_title(state):
    return ROTATION.get(state["kind"], "Community Reading Goal")


async def community_page(guild, state, viewer, *, preview=False, client=None):
    ranked = ranked_readers(state["contributions"])
    members = await resolve_card_members(guild, [uid for uid, _ in ranked], client)
    names = {uid: member.display_name for uid, member in members.items()}
    readers = [(names.get(uid, f"Member {uid[-4:]}"), count) for uid, count in ranked]
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
    rules = COMMUNITY_RULES.get(state["kind"], f"Read {required} chapters to qualify.")
    description += "\n" + rules
    if state["xp"]:
        description += f"\n**+{state['xp']:,} XP** per qualifying reader if the team completes the goal."
    theme = COMMUNITY_THEMES.get(state["kind"], "watchathon")
    embed = discord.Embed(title=community_title(state), description=description, color=discord.Color.from_rgb(*ACCENTS[theme]))
    if state["objectives"]:
        embed.add_field(name="Objectives", value="\n".join(
            ("✓" if o["done"] else "○") + " " + o["label"]
            + (f" · {o['titles'][0][:65]}" if o.get("titles") else "")
            for o in state["objectives"][:12]
        )[:1024], inline=False)
    if state["pending"]:
        embed.add_field(name="Metadata pending", value=f"{state['pending']} titles awaiting AniList metadata", inline=False)
    add_readers(embed, readers)
    if preview:
        embed.set_footer(text="Private preview · Cached activity")
    titles = state["titles"]
    if titles:
        embed.add_field(name="Manga read this week", value="\n".join(f"{title['title'][:120]} · {title['count']:,} chapters" for title in titles), inline=False)
    assets = {uid: member.display_avatar for uid, member in members.items()}
    assets["icon"] = getattr(guild, "icon", None)
    covers, artwork = await asyncio.gather(load_covers(titles), load_discord_artwork(assets, limit=6))
    deadline_label = deadline.astimezone(ZoneInfo(state["timezone"])).strftime("%d %b %Y")
    art_entries = [{"canonical": str(index), "title": title["title"]} for index, title in enumerate(titles)]
    layout = {
        "kind": theme, "name": embed.title,
        "preview": preview, "manga": True, "total": state["total"], "target": state["target"],
        "goal_unit": unit, "unit": "chapters", "reward": state["xp"], "deadline": deadline_label,
        "required_contributors": state["required"], "qualified_contributors": eligible,
        "qualified": state["qualified"], "contributions": state["contributions"],
        "eligible_titles": state["eligible_titles"], "pending_metadata": state["pending"],
        "objectives": state["objectives"], "art_entries": art_entries, "rules": rules,
        "minimum": required, "qualification": qualification,
        "status": "goal_reached" if state["reached"] else "active",
        "estimated_awards": {uid: state["xp"] for uid in state["qualified"]} if state["reached"] and state["xp"] else {},
    }
    art = await asyncio.to_thread(
        render_community_png, guild.name, layout, names=names, avatars=artwork,
        viewer=viewer, posters={str(index): cover for index, cover in enumerate(covers)},
        server_icon=artwork.pop("icon", None),
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


def ranked_readers(counts):
    return sorted(((uid, value) for uid, value in counts.items() if value), key=lambda pair: (-pair[1], pair[0]))[:5]


async def reader_names(guild, counts, client):
    ranked = ranked_readers(counts)
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


def render_page(name, heading, subtitle, metrics, sections, readers=()):
    height = 340 + 62*len(sections) + (58+46*len(readers) if readers else 0)
    image = background((1080,height), ACCENT)
    draw = ImageDraw.Draw(image)

    def text(x, y, value, size=20, color=WHITE, bold=False, width=940):
        face = fit(draw, value, size, width, bold)
        paint(draw, (x,y), short(draw,value,face.size,width,bold), font=face, fill=color)

    label(draw, (48,28), "Bingecord · Manga", fill=MUTED)
    text(48,63,name,28,bold=True)
    text(48,105,heading,44,ACCENT,True)
    text(48,168,subtitle,19,MUTED)
    width = 984//len(metrics)
    for index, (key, value) in enumerate(metrics):
        x = 48+index*width
        panel(image,(x,211,x+width-12,294))
        label(draw,(x+16,222),key,fill=MUTED)
        text(x+16,247,value,27,ACCENT,True,width=width-44)
    y = 310
    for key, value in sections:
        panel(image,(48,y,1032,y+54),12)
        text(64,y+15,key,18,MUTED,width=284)
        text(362,y+14,value,19,WHITE,True,width=641)
        y += 62
    if readers:
        label(draw,(48,y+12),"Top readers",fill=MUTED)
        y += 48
        for index,(reader,count) in enumerate(readers,1):
            text(60,y,f"{index:02d}  {reader}",20,WHITE,True,width=670)
            text(770,y,f"{count:,} chapters",20,ACCENT,width=240)
            y += 46
    return png(image)
