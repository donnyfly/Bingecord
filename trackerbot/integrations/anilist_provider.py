"""AniList list-progress polling and Discord account linking."""

from datetime import datetime, timezone
import logging
import time
from urllib.parse import quote, urlencode

import aiohttp
import discord

from trackerbot.core.activity_details import current_post
from trackerbot.core.providers import BUILTIN_TRACKERS, ProviderAccount

from .anilist_client import PIN_URL, AniListAccount, AniListClient, AniListError
from .provider_adapters import BaseProvider


log = logging.getLogger(__name__)


def progress_state(entry):
    return [entry.id, entry.status, entry.progress, entry.repeat, entry.private]


def list_update(entry, previous):
    """Group a progress increase into one activity and retain status-only changes."""
    if entry.private or previous and (previous[0] != entry.id or previous[4]):
        return None
    reading = entry.media_type == "MANGA"
    movie = not reading and entry.format == "MOVIE"
    old_status = previous[1] if previous else None
    old_progress = previous[2] if previous else 0
    old_repeat = previous[3] if previous else 0
    repeating = entry.status == "REPEATING" or old_status == "REPEATING" or bool(entry.repeat and entry.repeat > (old_repeat or 0))
    progress = entry.progress
    first = (old_progress or 0) + 1
    if entry.status == "REPEATING" and old_status != "REPEATING" and (progress or 0) < (old_progress or 0):
        first = 1
    if repeating and old_status != "REPEATING" and entry.status == "COMPLETED" and entry.repeat and entry.repeat > (old_repeat or 0):
        first = 1
    advanced = bool(progress and progress >= first)
    if advanced and entry.status in {"CURRENT", "COMPLETED", "REPEATING"}:
        verb = ("Reread" if repeating else "Read") if reading else ("Rewatched" if repeating else "Watched")
        if movie:
            return f"{verb} **{entry.title}**", {"movie": True}
        if reading:
            label = f"Chapter {first}" if first == progress else f"Chapters {first}–{progress}"
        else:
            label = f"E{first:02d}" + (f"-E{progress:02d}" if first != progress else "")
        text = f"{verb} {label} of **{entry.title}**"
        if entry.status == "COMPLETED" and old_status != "COMPLETED":
            text += "\n✅ Completed this manga." if reading else "\n✅ Completed this series."
        if first == 1 and not repeating:
            text += "\n🆕 Started reading this manga." if reading else "\n🆕 Started watching this series."
        return text, {"episodes": [first, progress] if not reading else []}
    if old_status == entry.status:
        return None
    if entry.status == "REPEATING" and old_status in {None, "PLANNING"}:
        return None
    actions = {
        "CURRENT": "Started reading" if reading else "Started watching",
        "PLANNING": "Planned to read" if reading else "Planned to watch",
        "COMPLETED": "Completed",
        "DROPPED": "Dropped",
        "PAUSED": "Paused",
        "REPEATING": "Started rereading" if reading else "Started rewatching",
    }
    return f"{actions[entry.status]} **{entry.title}**", {"status": entry.status.lower(), "movie": movie}


class AniListLinkModal(discord.ui.Modal, title="Link your AniList account"):
    code = discord.ui.TextInput(label="AniList authorization code", max_length=4000, style=discord.TextStyle.paragraph)

    def __init__(self, provider):
        super().__init__(timeout=600)
        self.provider = provider

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            async with aiohttp.ClientSession() as session:
                client = AniListClient(session)
                app = self.provider.app
                tokens = await client.exchange_code(self.code.value.strip(), app.ANILIST_CLIENT_ID, app.ANILIST_CLIENT_SECRET)
                account = await client.viewer(tokens["access_token"])
            await self.provider.app.storage.link_provider_account(
                interaction.guild_id, str(interaction.user.id),
                {"access_token": tokens["access_token"], "expires_at": app.calculate_token_expiry(tokens.get("expires_in"))},
                {"id": account.id, "username": account.name}, provider="anilist",
            )
        except (AniListError, ValueError):
            await interaction.followup.send(
                "I couldn't link AniList. Check the authorization code and unlink any different AniList account first.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            "AniList linked. Use `/bingecord source provider:AniList` for anime and "
            "`/bingecord source manga:On` for manga.",
            ephemeral=True,
        )


class AniListProvider(BaseProvider):
    def __init__(self, app):
        super().__init__(app, next(manifest for manifest in BUILTIN_TRACKERS if manifest.name == "anilist"))
        self.retry_at = 0.0

    async def authorize(self, interaction):
        if not interaction.guild_id or not self.app.ANILIST_CLIENT_ID or not self.app.ANILIST_CLIENT_SECRET:
            await interaction.response.send_message(
                "AniList linking requires a server and configured AniList OAuth credentials.", ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        query = urlencode({"client_id": self.app.ANILIST_CLIENT_ID, "redirect_uri": PIN_URL, "response_type": "code"})
        view = discord.ui.View(timeout=600)
        view.add_item(discord.ui.Button(label="Authorize on AniList", url=f"https://anilist.co/api/v2/oauth/authorize?{query}"))
        button = discord.ui.Button(label="Enter authorization code", style=discord.ButtonStyle.primary)

        async def open_modal(reply):
            if reply.user.id == interaction.user.id:
                await reply.response.send_modal(AniListLinkModal(self))

        button.callback = open_modal
        view.add_item(button)
        await interaction.edit_original_response(
            content="Authorize on AniList, then paste the code into the form below.",
            view=view,
        )

    async def unlink(self, interaction):
        if not interaction.guild_id:
            await interaction.response.send_message("Use this command in a server.", ephemeral=True)
            return
        removed = await self.app.storage.unlink_provider_account(
            interaction.guild_id, str(interaction.user.id), provider="anilist",
        )
        await interaction.response.send_message(
            "AniList unlinked from this server." if removed else "AniList is not linked here.", ephemeral=True,
        )

    async def link(self, discord_user_id):
        member = await self.app.storage.get_user(discord_user_id)
        linked = (member or {}).get("anilist")
        if not linked:
            raise ValueError("Link AniList first.")
        return ProviderAccount("anilist", str(linked["account_id"]), linked["username"], str(discord_user_id))

    async def refresh_auth(self, account):
        user = await self._user(account)
        async with aiohttp.ClientSession() as session:
            await AniListClient(session).viewer(user["anilist"]["access_token"])

    async def history(self, account, cursor=None):
        raise ValueError("AniList list progress does not supply a complete dated watch history.")

    async def changes(self, account, cursor=None):
        raise ValueError("AniList progress is reconciled against each server's list baseline.")

    def profile_url(self, account):
        return f"https://anilist.co/user/{quote(account.username, safe='')}"

    def title_url(self, media_type, title_ids):
        media_id = title_ids.get("anilist")
        if str(media_id).isdigit():
            return f"https://anilist.co/{'manga' if media_type == 'manga' else 'anime'}/{media_id}"
        return None

    async def resolve_title_url(self, media_type, title_ids):
        return self.title_url(media_type, title_ids)

    async def title(self, media_type, title_ids):
        return {}

    async def recommendation_sources(self, account, media_filter):
        raise ValueError("AniList recommendation matching is unavailable.")

    async def watching(self, account):
        return await self._discovery(account, {"CURRENT", "REPEATING"})

    async def planning(self, account):
        return await self._discovery(account, {"PLANNING"})

    async def _discovery(self, account, statuses):
        linked = (await self._user(account))["anilist"]
        async with aiohttp.ClientSession() as session:
            entries = await self._read_list(AniListClient(session), linked, "ANIME")
        return [{
            "title": entry.title, "url": entry.site_url, "poster": entry.cover_url,
            "ids": {"anilist": entry.media_id}, "image": entry.banner_url,
            "genres": entry.genres, "episode_count": entry.total if entry.format != "MOVIE" else None,
            "anime": True, "media_type": "movies" if entry.format == "MOVIE" else "anime",
            "latest": (None, None, entry.progress, None) if entry.progress and entry.format != "MOVIE" else None,
        } for entry in entries if entry.status in statuses and not entry.private]

    async def _read_list(self, client, linked, media_type):
        if self.retry_at > time.monotonic():
            raise AniListError(429, "rate limit cooldown")
        account = AniListAccount(int(linked["account_id"]), linked["username"])
        entries = {}
        page = 1
        try:
            while page is not None:
                result = await client.list_page(linked["access_token"], account, media_type, page)
                for entry in result.entries:
                    if entry.media_id in entries:
                        raise AniListError(200, "list changed during pagination")
                    entries[entry.media_id] = entry
                page = result.next_page
        except AniListError as exc:
            if exc.retry_after:
                self.retry_at = time.monotonic() + exc.retry_after
            raise
        return tuple(entries.values())

    async def poll(self, guild_id=None, *, manual=False):
        if not self.app.ANILIST_CLIENT_ID or self.retry_at > time.monotonic():
            return 0
        count = 0
        fetched = {}
        targets = await self.app.storage.get_provider_targets("anilist", guild_id)
        async with aiohttp.ClientSession() as session:
            client = AniListClient(session)
            for target in targets:
                member = target["guild_user_data"]
                for media_type, enabled in (
                    ("ANIME", member["activity_provider"] == "anilist"), ("MANGA", member["anilist_manga"]),
                ):
                    if not enabled or not target["channel_id"]:
                        continue
                    linked = target["user_data"]["anilist"]
                    key = (linked["account_id"], linked["access_token"], media_type)
                    try:
                        if key not in fetched:
                            fetched[key] = None
                            fetched[key] = await self._read_list(client, linked, media_type)
                        if fetched[key] is not None:
                            count += await self._publish(target, linked, media_type, fetched[key])
                    except (AniListError, discord.HTTPException) as exc:
                        detail = str(exc) if isinstance(exc, AniListError) else f"Discord HTTP {exc.status}"
                        log.warning("AniList %s sync for guild %s failed: %s", media_type, target["guild_id"], detail)
                    if self.retry_at > time.monotonic():
                        return count
        return count

    async def initialize_manga(self, guild_id, uid):
        """Capture the baseline when manga is enabled, before the next chapter update."""
        storage = self.app.storage
        user = await storage.get_user(uid)
        linked = (user or {}).get("anilist")
        if not linked:
            return False
        token = linked["access_token"]
        active, baseline = await storage.anilist_snapshot(guild_id, uid, token, "MANGA")
        if not active or baseline is not None:
            return active
        try:
            async with aiohttp.ClientSession() as session:
                entries = await self._read_list(AniListClient(session), linked, "MANGA")
        except AniListError as exc:
            log.warning("AniList manga baseline for guild %s failed: %s", guild_id, exc)
            return False
        active, _ = await storage.anilist_snapshot(
            guild_id, uid, token, "MANGA",
            replace={str(entry.media_id): progress_state(entry) for entry in entries}, seed=True,
        )
        return active

    async def _publish(self, target, linked, media_type, entries):
        guild_id, uid = target["guild_id"], target["discord_user_id"]
        token = linked["access_token"]
        storage = self.app.storage
        active, baseline = await storage.anilist_snapshot(guild_id, uid, token, media_type)
        if not active:
            return 0
        latest = {str(entry.media_id): progress_state(entry) for entry in entries}
        if baseline is None:
            await storage.anilist_snapshot(guild_id, uid, token, media_type, replace=latest, seed=True)
            log.info("AniList %s baseline checked for guild %s, member %s (%s entries).",
                     media_type, guild_id, uid, len(latest))
            return 0
        channel_id = int(target["channel_id"])
        count = 0
        for entry in sorted(entries, key=lambda item: item.updated_at or 0):
            key = str(entry.media_id)
            update = list_update(entry, baseline.get(key))
            if update is None:
                continue
            channel = self.app.bot.get_channel(channel_id) or await self.app.bot.fetch_channel(channel_id)
            if not (await storage.anilist_snapshot(guild_id, uid, token, media_type))[0]:
                return count
            if not await self._send(channel, target, linked, entry, update):
                log.warning("AniList %s activity could not post in guild %s for member %s; update remains pending.",
                            media_type, guild_id, uid)
                return count
            count += 1
            baseline[key] = latest[key]
            active, _ = await storage.anilist_snapshot(guild_id, uid, token, media_type, replace=baseline)
            if not active:
                return count
        if baseline != latest:
            await storage.anilist_snapshot(guild_id, uid, token, media_type, replace=latest)
        return count

    async def _send(self, channel, target, linked, entry, update):
        guild_id, uid = target["guild_id"], target["discord_user_id"]
        guild = self.app.bot.get_guild(int(guild_id))
        if not guild:
            return False
        member = guild.get_member(int(uid))
        if member is None:
            try:
                member = await guild.fetch_member(int(uid))
            except discord.NotFound:
                return False
        name = member.display_name
        desc, meta = update
        media = "manga" if entry.media_type == "MANGA" else "anime"
        meta.update(media_type=media, ids={"anilist": entry.media_id})
        prefs = await self.app.prefs(guild_id, uid)
        if media == "manga":
            if entry.mal_id:
                meta["ids"]["mal"] = entry.mal_id
            if entry.banner_url:
                meta["banner_url"] = entry.banner_url
            prefs = {**prefs, "style": "card"}
        elif entry.score and prefs.get("show_personal", True):
            desc += f"\n💬 Your list rating: {entry.score:g}/10"
        stamp = datetime.fromtimestamp(entry.updated_at, timezone.utc) if entry.updated_at else datetime.now(timezone.utc)
        account = ProviderAccount("anilist", str(linked["account_id"]), linked["username"], uid)
        art = entry.cover_url if media == "manga" else entry.banner_url
        embed = self.app.build_embed(media, desc, stamp, name, member, art, self.profile_url(account),
                                     title=entry.title, title_url=entry.site_url, poster=entry.cover_url,
                                     preferences=prefs, status_activity=bool(meta.get("status")), provider="AniList")
        participant = {"guild": guild_id, "user": uid, "source": "anilist", "account": account.account_id, "meta": meta}
        marker = current_post.set({"parts": [participant], "standalone": True,
                                   "base": embed.to_dict(), "rendered": embed.to_dict()})
        try:
            return await self.app.send_embed(channel, embed, "AniList activity")
        finally:
            current_post.reset(marker)
