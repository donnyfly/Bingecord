"""Bounded optional artwork; personal objectives remain readable without images."""
import asyncio
from datetime import datetime
import discord
from .challenge_visuals import render_challenges_png
from .discord_artwork import load_discord_artwork
from trackerbot.core.personal_challenges import requirement


async def challenge_card(send,user,state,*,now,ephemeral=False):
    images=await load_discord_artwork({'avatar':getattr(user,'display_avatar',None)})
    entries={t['canonical']:t for s in state['sections'] for r in s['rows'] for t in r.get('titles',[])}
    posters={}
    if entries:
        import aiohttp
        from .profile_artwork import download_card_image
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session:
            gate=asyncio.Semaphore(4)
            async def load(key,entry):
                async with gate:
                    try:
                        art=await download_card_image(session,entry.get('poster_path'),'w185')
                        if art:posters[key]=art
                    except Exception:pass
            tasks=[asyncio.create_task(load(k,e)) for k,e in list(entries.items())[:12]]
            try:
                async with asyncio.timeout(4):await asyncio.gather(*tasks)
            except TimeoutError:pass
            finally:
                for task in tasks:
                    if not task.done():task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
    embed=discord.Embed(title='Personal Challenges'+(' · Preview' if state.get('preview') else ''),
                        description=f"**{state['earned']:,} bonus XP earned this week**"+('\nPrivate design/rules preview. No XP changes.' if state.get('preview') else ''),color=0x9A8CE8)
    kwargs={'embed':embed,'allowed_mentions':discord.AllowedMentions.none()}
    if ephemeral:kwargs['ephemeral']=True
    for section in state['sections']:
        text='\n'.join(f"{'✓' if row['done'] else '○'} **{row['name']}** · {row['value']:g}/{row['target']} · +{row['xp']:,} XP\n{requirement(row)}"+(' · Awaiting title details' if row.get('pending') and not row['done'] else '') for row in section['rows'])
        embed.add_field(name=section['scope'].title()+(' reading' if section.get('reading') else '')+' · Resets '+discord.utils.format_dt(datetime.fromisoformat(section['end']),'R'),value=text,inline=False)
    try:
        data=await asyncio.to_thread(render_challenges_png,user.display_name,state,avatar=images.get('avatar'),posters=posters,now=now)
        from .card_details import details_view
        detail=embed.copy();detail.title=user.display_name+' · Personal Challenges'+(' · Preview' if state.get('preview') else '')
        kwargs['view']=await details_view(detail,owner=user.id if ephemeral else None)
        kwargs.update(embed=None,file=discord.File(data,filename='challenges.png'))
    except Exception:
        embed.set_footer(text='Resets at 00:00 UTC · Shared progression across trackers')
    return await send(**kwargs)
