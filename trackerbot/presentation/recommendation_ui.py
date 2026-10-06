"""Private, browsable Discord recommendation cards."""

import discord


def candidate_key(item):
    return item["_recommendation_kind"], item["id"]


def related_candidate(current, remaining):
    genres=set(current.get("genre_ids") or [])
    sources={match["source_id"] for match in current.get("_matches") or []}
    return max(remaining, key=lambda item:(
        len(sources.intersection(match["source_id"] for match in item.get("_matches") or []))*3
        + len(genres.intersection(item.get("genre_ids") or [])),
        item.get("_score",0),
    ), default=None)


def different_candidate(current, remaining):
    genres=set(current.get("genre_ids") or [])
    sources={match["source_id"] for match in current.get("_matches") or []}
    viable=[item for item in remaining if int(item.get("vote_count") or 0)>=25] or remaining
    return max(viable, key=lambda item:(
        -len(sources.intersection(match["source_id"] for match in item.get("_matches") or [])),
        -len(genres.intersection(item.get("genre_ids") or [])),
        item.get("_score",0),
    ), default=None)


class RecommendationView(discord.ui.View):
    def __init__(self, owner_id, lineup, ranked, renderer):
        super().__init__(timeout=600)
        self.owner_id=owner_id
        self.lineup=list(lineup)
        self.ranked=list(ranked)
        self.renderer=renderer
        self.index=0
        self.seen={candidate_key(item) for item in self.lineup}
        self.message=None

    async def interaction_check(self, interaction):
        if interaction.user.id!=self.owner_id:
            await interaction.response.send_message("Run /simkl-recommend to get your own picks.",ephemeral=True)
            return False
        return True

    async def show(self, interaction):
        await interaction.response.defer()
        try:
            embed=await self.renderer(self.lineup[self.index],self.index+1,len(self.lineup))
            await interaction.edit_original_response(embed=embed,view=self)
        except Exception:
            await interaction.followup.send("I couldn't load that pick. Please try again.",ephemeral=True)

    @discord.ui.button(label="Next",style=discord.ButtonStyle.primary)
    async def next_pick(self, interaction, button):
        self.index=(self.index+1)%len(self.lineup)
        await self.show(interaction)

    @discord.ui.button(label="Another like this",style=discord.ButtonStyle.secondary)
    async def another_like_this(self, interaction, button):
        remaining=[item for item in self.ranked if candidate_key(item) not in self.seen]
        choice=related_candidate(self.lineup[self.index],remaining)
        if choice is None:
            await interaction.response.send_message("That's every fresh pick I found. Run the command again after more watches.",ephemeral=True)
            return
        self.seen.add(candidate_key(choice))
        self.lineup.append(choice)
        self.index=len(self.lineup)-1
        await self.show(interaction)

    @discord.ui.button(label="Different direction",style=discord.ButtonStyle.secondary)
    async def different_direction(self, interaction, button):
        remaining=[item for item in self.ranked if candidate_key(item) not in self.seen]
        choice=different_candidate(self.lineup[self.index],remaining)
        if choice is None:
            await interaction.response.send_message("That's every fresh pick I found. Run the command again after more watches.",ephemeral=True)
            return
        self.seen.add(candidate_key(choice))
        self.lineup.append(choice)
        self.index=len(self.lineup)-1
        await self.show(interaction)

    async def on_timeout(self):
        for item in self.children:
            item.disabled=True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
