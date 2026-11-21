"""Watch and Manga buttons for a single Discord message."""
import asyncio
from io import BytesIO

import discord

from .card_details import CardDetails, MemoryDetails


class MediaPages(discord.ui.View):
    def __init__(self, manga, owner=None):
        super().__init__(timeout=604800 if owner is None else 900)
        self.owner = owner
        self.pages = {}
        self.manga = manga
        self.lock = asyncio.Lock()

    async def interaction_check(self, interaction):
        if self.owner is None or str(interaction.user.id) == str(self.owner):
            return True
        await interaction.response.send_message("Run the command to switch your own card.", ephemeral=True)
        return False

    async def save_page(self, key, payload):
        file = payload.pop("file", None)
        attachment = None
        if file:
            try:
                attachment = (file.fp.read(), file.filename, file.description)
            finally:
                # discord.File temporarily replaces fp.close.
                fp = file.fp
                file.close()
                fp.close()
        self.pages[key] = (payload.pop("embed", None), attachment, payload.pop("view", None))

    def select(self, key):
        embed, attachment, details = self.pages[key]
        self.watch.style = discord.ButtonStyle.primary if key == "watch" else discord.ButtonStyle.secondary
        self.read.style = discord.ButtonStyle.primary if key == "manga" else discord.ButtonStyle.secondary
        self.watch.disabled = key == "watch"
        self.read.disabled = key == "manga"
        for item in list(self.children)[2:]:
            self.remove_item(item)
        if isinstance(details, MemoryDetails):
            async def show_details(interaction):
                await interaction.response.send_message(embed=details.embed, ephemeral=True,
                                                        allowed_mentions=discord.AllowedMentions.none())
            button = discord.ui.Button(label="View details")
            button.callback = show_details
            self.add_item(button)
        elif details:
            for item in details.children:
                if isinstance(item, CardDetails):
                    self.add_item(CardDetails(item.token))
        files = []
        if attachment:
            data, filename, description = attachment
            files.append(discord.File(BytesIO(data), filename=filename, description=description))
        return {"embed": embed, "view": self, "attachments": files}

    async def switch(self, interaction, key):
        await interaction.response.defer()
        async with self.lock:
            if key not in self.pages:
                try:
                    payload = await self.manga()
                except Exception:
                    await interaction.followup.send("Could not load the Manga page. Try again shortly.", ephemeral=True)
                    raise
                await self.save_page(key, payload)
            await interaction.edit_original_response(**self.select(key), allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Watch")
    async def watch(self, interaction, button):
        await self.switch(interaction, "watch")

    @discord.ui.button(label="Manga")
    async def read(self, interaction, button):
        await self.switch(interaction, "manga")


async def send_media_pages(send, watch, manga, *, owner=None):
    view = MediaPages(manga, owner=owner)
    await view.save_page("watch", watch)
    page = view.select("watch")
    files = page.pop("attachments")
    if files:
        page["file"] = files[0]
    return await send(**watch, **page)
