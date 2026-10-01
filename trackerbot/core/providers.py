"""Shared contracts for tracker ingestion; providers retain their own credentials.

Registered adapters expose native sync and normalized read operations. A source event ID is scoped to its provider and account;
it is never a cross-provider XP key by itself.
"""

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable


ProviderName = str
ChangeAction = Literal["added", "updated", "removed"]


@dataclass(frozen=True)
class ProviderManifest:
    """A new source declares its supported commands before being exposed."""
    name: str
    display_name: str
    supports_history: bool
    supports_changes: bool
    supports_watching: bool
    supports_planning: bool
    supports_removals: bool
    supports_rewatches: bool
    supports_account_link: bool
    profile_url_pattern: str
    title_url_pattern: str
    limitations: tuple[str, ...] = ()

    def validate_tracker(self) -> None:
        if not self.name or not self.name.isidentifier():
            raise ValueError("Provider name must be a stable identifier")
        if not (self.supports_history and self.supports_changes and self.supports_account_link):
            raise ValueError("A watch tracker needs account linking, history and changes")
        if not (self.profile_url_pattern.startswith("https://")
                and self.title_url_pattern.startswith("https://")):
            raise ValueError("Provider links must use HTTPS")


BUILTIN_TRACKERS = (
    ProviderManifest("simkl", "SIMKL", True, True, True, True, True, True, True,
                     "https://simkl.com/{id}", "https://simkl.com/{type}/{id}"),
    ProviderManifest("wetrakr", "WeTrakr", True, True, True, True, True, True, True,
                     "https://wetrakr.com/{id}", "https://wetrakr.com/tmdb/{type}/{id}"),
)


@dataclass(frozen=True)
class ProviderAccount:
    provider: str
    account_id: str
    username: str
    discord_user_id: str | None = None


@dataclass(frozen=True)
class ProviderPage:
    changes: tuple["WatchChange", ...]
    next_cursor: str | None

    def validate(self, account: ProviderAccount) -> None:
        seen = set()
        for change in self.changes:
            if change.provider != account.provider or change.account_id != account.account_id:
                raise ValueError("Provider observation belongs to another account")
            if not change.event_id or not change.change_id or not change.watched_at and change.action != "removed":
                raise ValueError("Provider observation lacks a stable event ID or watch time")
            if change.source_key in seen and change.action == "added":
                raise ValueError("Provider page contains a duplicate added event")
            if change.action != "removed" and change.media_type == "episode" and (change.episode is None or
                    change.season is None and change.absolute_episode is None):
                raise ValueError("Episode requires a season/episode or absolute number")
            seen.add(change.source_key)


@runtime_checkable
class TrackerProvider(Protocol):
    """Every tracking integration adapts its API to these shared operations."""
    manifest: ProviderManifest

    async def link(self, discord_user_id: str) -> ProviderAccount: ...
    async def refresh_auth(self, account: ProviderAccount) -> None: ...
    async def history(self, account: ProviderAccount, cursor: str | None) -> ProviderPage: ...
    async def changes(self, account: ProviderAccount, cursor: str | None) -> ProviderPage: ...
    async def watching(self, account: ProviderAccount) -> list[dict]: ...
    async def planning(self, account: ProviderAccount) -> list[dict]: ...
    async def title(self, media_type: str, title_ids: dict) -> dict: ...
    async def poll(self, guild_id=None, *, manual=False) -> int: ...
    async def authorize(self, interaction) -> None: ...
    async def unlink(self, interaction) -> None: ...
    async def recommendation_sources(self, account: ProviderAccount, media_filter: str) -> tuple[list[dict], set]: ...
    async def resolve_title_url(self, media_type: str, title_ids: dict) -> str | None: ...
    def profile_url(self, account: ProviderAccount) -> str | None: ...
    def title_url(self, media_type: str, title_ids: dict) -> str | None: ...


class ProviderRegistry:
    def __init__(self):
        self._providers: dict[str, TrackerProvider] = {}

    def register(self, provider: TrackerProvider) -> None:
        if not isinstance(provider, TrackerProvider):
            raise TypeError("Provider must implement TrackerProvider")
        provider.manifest.validate_tracker()
        if provider.manifest.name in self._providers:
            raise ValueError(f"Duplicate provider: {provider.manifest.name}")
        self._providers[provider.manifest.name] = provider

    def get(self, name: str) -> TrackerProvider:
        return self._providers[name]

    def manifests(self) -> tuple[ProviderManifest, ...]:
        return tuple(provider.manifest for provider in self._providers.values())


@dataclass(frozen=True)
class WatchChange:
    provider: ProviderName
    account_id: str
    event_id: str
    change_id: str
    action: ChangeAction
    media_type: Literal["movie", "episode"]
    watched_at: str | None
    title_ids: dict
    show_ids: dict | None = None
    season: int | None = None
    episode: int | None = None

    title: str = ""
    absolute_episode: int | None = None
    observed_at: str | None = None
    source_media_id: str | None = None

    @property
    def source_key(self) -> str:
        """Stable across edits, unique across providers and linked accounts."""
        return f"{self.provider}:{self.account_id}:{self.event_id}"


def provider_linked(guild_user: dict, user: dict, provider: ProviderName) -> bool:
    """Legacy SIMKL links default to enabled; new providers require an opt-in."""
    if provider == "simkl":
        return bool(guild_user.get("simkl_linked", True) and user.get("simkl_token"))
    if provider == "wetrakr":
        return bool(guild_user.get("wetrakr_linked", False) and user.get("wetrakr"))
    raise ValueError(f"Unknown tracking provider: {provider}")
