"""Shared contracts for tracker ingestion; providers retain their own credentials.

The old SIMKL data layout remains authoritative until each consumer has moved
to these contracts. A source event ID is scoped to its provider and account;
it is never a cross-provider XP key by itself.
"""

from dataclasses import dataclass
from typing import Literal


ProviderName = Literal["simkl", "wetrakr"]
ChangeAction = Literal["added", "updated", "removed"]


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
