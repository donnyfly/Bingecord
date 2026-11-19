"""Render community cards with bounded optional artwork and readable text fallback."""
import asyncio
from datetime import datetime
import discord
from .community_visuals import render_community_png
from .discord_artwork import load_discord_artwork


async def community_card(send,guild,state,*,viewer=None,preview=False,heading=None):
    names={};assets={};rows=sorted((state.get('contributions') or {}).items(),key=lambda kv:(-kv[1],kv[0]))[:5]
    for uid,_ in rows:
        member=guild.get_member(int(uid)) if guild and hasattr(guild,'get_member') else None
        names[uid]=member.display_name if member else f'<@{uid}>'
        if member:assets[uid]=member.display_avatar
    avatars=await load_discord_artwork(assets)
    posters={}
    entries=state.get('art_entries') or []
    if entries:
        import aiohttp
        from .profile_artwork import download_card_image
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session:
            async def load(entry):
                try:
                    image=await download_card_image(session,entry.get('poster_path'),'w185')
                    if image:posters[entry['canonical']]=image
                except Exception:pass
            tasks=[asyncio.create_task(load(entry)) for entry in entries[:4]]
            try:
                async with asyncio.timeout(4):await asyncio.gather(*tasks)
            except TimeoutError:pass
            finally:
                for task in tasks:
                    if not task.done():task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
    title=state.get('name') or 'Community Watchathon'
    total=state.get('total',0);target=state.get('target',0);unit=state.get('unit','minutes')
    description=f"**{total:,.1f} / {target:,}** {unit} · **{state.get('pool',0):,} XP pool**"
    description+=f"\nQualifying contributors: **{state.get('qualified_contributors',0)} / {state.get('required_contributors',1)}**"
    try:description+='\nDeadline '+discord.utils.format_dt(datetime.fromisoformat(state['end']),style='F')
    except (KeyError,ValueError,TypeError):pass
    if state.get('version')==2:
        description+=' · 6-hour sync grace before settlement'
        if state.get('kind')!='watchathon':description+='\nEligible title: a completed movie, 3 regular episodes or a verified season finish.'
    if preview:description+='\nPrivate rules preview using cached activity. No event or XP changes.'
    embed=discord.Embed(title=heading or title,description=description,color=0x9A8CE8)
    if state.get('status') in {'completed','missed'}:
        awards=state.get('awards') or {};deltas=state.get('deltas') or {}
        recipients=sorted(set(awards)|set(deltas),key=lambda uid:(-awards.get(uid,0),uid))
        lines=[f"<@{uid}> · {deltas.get(uid,awards.get(uid,0)):+,} XP" for uid in recipients[:5]]
        if len(recipients)>5:lines.append(f'+ {len(recipients)-5} more recipients')
        if lines:embed.add_field(name='Reward results',value='\n'.join(lines),inline=False)
    kwargs={'embed':embed,'allowed_mentions':discord.AllowedMentions.none()}
    if preview:kwargs['ephemeral']=True
    try:
        image=await asyncio.to_thread(render_community_png,guild.name if guild else 'Community',state,
                                      names=names,avatars=avatars,viewer=viewer,posters=posters,heading=heading)
        embed.set_image(url='attachment://community.png');kwargs['file']=discord.File(image,filename='community.png')
    except Exception:
        lines=[f"{discord.utils.escape_markdown(names[uid])} · {value:,.1f} {unit}" for uid,value in rows]
        if lines:embed.add_field(name='Contributors · Top five',value='\n'.join(lines),inline=False)
        objectives=state.get('objectives') or []
        if objectives:embed.add_field(name='Objectives',value='\n'.join(('Complete: ' if o['done'] else 'Needed: ')+o['label'] for o in objectives[:12])[:1024],inline=False)
    return await send(**kwargs)
