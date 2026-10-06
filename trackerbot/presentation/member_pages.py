"""Five-member pages for server leaderboards."""
import discord

PAGE_SIZE = 5


class MemberPages(discord.ui.View):
    def __init__(self, owner_id, rows, renderer):
        super().__init__(timeout=180)
        self.owner_id, self.rows, self.renderer = owner_id, rows, renderer
        self.page = 0
        self.message = None
        self.update_buttons()

    @property
    def pages(self):
        return (len(self.rows) + PAGE_SIZE - 1) // PAGE_SIZE

    def update_buttons(self):
        self.previous.disabled = self.page == 0
        self.next.disabled = self.page + 1 >= self.pages

    async def interaction_check(self, interaction):
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message('Run /bingecord leaderboard to browse your own pages.', ephemeral=True)
        return False

    async def render(self):
        start = self.page * PAGE_SIZE
        return await self.renderer(self.rows[start:start + PAGE_SIZE], start, self.page + 1, self.pages)

    async def show(self, interaction):
        await interaction.response.defer()
        self.update_buttons()
        embed, file = await self.render()
        await interaction.edit_original_response(embed=embed, attachments=[file] if file else [], view=self,
                                                 allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label='Previous', style=discord.ButtonStyle.secondary)
    async def previous(self, interaction, button):
        self.page = max(0, self.page - 1)
        await self.show(interaction)

    @discord.ui.button(label='Next', style=discord.ButtonStyle.primary)
    async def next(self, interaction, button):
        self.page = min(self.pages - 1, self.page + 1)
        await self.show(interaction)

    async def on_timeout(self):
        for button in self.children:
            button.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
