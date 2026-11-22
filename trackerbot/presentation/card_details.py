"""Compact image embeds with private, restart-safe text details."""
import copy
import secrets
import discord

_store=lambda:None


def configure(factory):
    global _store
    _store=factory


class CardDetails(discord.ui.DynamicItem[discord.ui.Button],template=r'bingecord:details:(?P<token>[0-9a-f]{24})'):
    def __init__(self,token):
        self.token=token
        super().__init__(discord.ui.Button(label='View details',style=discord.ButtonStyle.secondary,custom_id='bingecord:details:'+token))

    @classmethod
    async def from_custom_id(cls,interaction,item,match):return cls(match['token'])

    async def callback(self,interaction):
        record=await _store().get_card_details(self.token) if _store() else None
        if not record:
            await interaction.response.send_message('These details have expired. Run the command again for a fresh card.',ephemeral=True);return
        if record.get('owner') and str(interaction.user.id)!=record['owner']:
            await interaction.response.send_message('Run the command to view your own private preview.',ephemeral=True);return
        if record.get('guild') and str(interaction.guild_id)!=record['guild']:
            await interaction.response.send_message('These details belong to another server.',ephemeral=True);return
        await interaction.response.send_message(embed=discord.Embed.from_dict(record['embed']),ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())


class MemoryDetails(discord.ui.View):
    def __init__(self,embed,owner=None):
        super().__init__(timeout=900);self.embed=embed;self.owner=owner
    @discord.ui.button(label='View details',style=discord.ButtonStyle.secondary)
    async def details(self,interaction,button):
        if self.owner and str(interaction.user.id)!=str(self.owner):
            await interaction.response.send_message('Run the command for your own private preview.',ephemeral=True);return
        await interaction.response.send_message(embed=self.embed,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())


async def details_view(embed,*,owner=None,guild=None):
    full=discord.Embed.from_dict(copy.deepcopy(embed.to_dict()));full.set_image(url=None)
    store=_store()
    if store and hasattr(store,'save_card_details'):
        token=secrets.token_hex(12)
        await store.save_card_details(token,{'embed':full.to_dict(),'owner':str(owner) if owner else None,'guild':str(guild) if guild else None})
        view=discord.ui.View(timeout=None);view.add_item(CardDetails(token));return view
    return MemoryDetails(full,owner)


async def standalone_card(embed,data,filename,*,owner=None,guild=None,transient=False):
    """Image-only delivery with the complete accessible text behind its button."""
    view=MemoryDetails(embed.copy(),owner) if transient else await details_view(embed,owner=owner,guild=guild)
    return {'embed':None,'file':discord.File(data,filename=filename),'view':view}
