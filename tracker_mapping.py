"""Provider-neutral watch identity and conservative cross-account matching.

External IDs are scoped to a media kind. A title is only a legacy fallback;
conflicting IDs never match merely because two names look alike.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Mapping


EXTERNAL_ID_NAMES = ("tmdb", "tvdb", "imdb", "mal")


def normalized_ids(values: Mapping | None) -> dict[str, str]:
    values = values or {}
    return {name: str(values[name]).strip().casefold()
            for name in EXTERNAL_ID_NAMES if values.get(name) is not None
            and str(values[name]).strip()}


def normalized_title(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


@dataclass(frozen=True)
class WatchIdentity:
    media_type: str
    title: str
    watched_at: str
    season: int | None = None
    episode: int | None = None
    ids: Mapping[str, str] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        return "movie" if "movie" in self.media_type else "episode"


def match_reason(left: WatchIdentity | None, right: WatchIdentity | None) -> str | None:
    """Return a durable reason for a match, or None when evidence is insufficient."""
    if left is None or right is None or left.kind != right.kind:
        return None
    if left.kind == "episode":
        if None in (left.season, left.episode, right.season, right.episode):
            return None
        if (left.season, left.episode) != (right.season, right.episode):
            return None
    else:
        try:
            a = datetime.fromisoformat(left.watched_at.replace("Z", "+00:00"))
            b = datetime.fromisoformat(right.watched_at.replace("Z", "+00:00"))
            if a.tzinfo is None:
                a = a.replace(tzinfo=timezone.utc)
            if b.tzinfo is None:
                b = b.replace(tzinfo=timezone.utc)
            if abs((a - b).total_seconds()) > 300:
                return None
        except (ValueError, TypeError, AttributeError):
            return None
    left_ids, right_ids = normalized_ids(left.ids), normalized_ids(right.ids)
    shared = set(left_ids) & set(right_ids)
    if any(left_ids[name] != right_ids[name] for name in shared):
        return None
    if any(left_ids[name] == right_ids[name] for name in shared):
        return "verified_id"
    # Legacy SIMKL XP events did not record external IDs. Allow a matching
    # title only when at least one observation has no IDs to compare.
    if ((not left_ids or not right_ids) and normalized_title(left.title)
            and normalized_title(left.title) == normalized_title(right.title)):
        return "legacy_title"
    return None


def same_watch(left: WatchIdentity | None, right: WatchIdentity | None) -> bool:
    return match_reason(left, right) is not None


def identity_from_event(event: dict) -> WatchIdentity | None:
    stamp = str(event.get("at") or "")
    key = str(event.get("event_key") or "")
    if not stamp or not key.endswith(":" + stamp):
        return None
    parts = key[:-(len(stamp) + 1)].split(":")
    kind = str(event.get("media_type") or "")
    if kind not in {"movie", "anime_movie", "episode", "anime_episode"}:
        return None
    try:
        season, episode = (int(parts[-2]), int(parts[-1])) if "episode" in kind else (None, None)
    except (IndexError, TypeError, ValueError):
        return None
    return WatchIdentity(kind, str(event.get("title") or ""), stamp,
                         season, episode, event.get("ids") or {})


def identity_from_play(play: dict) -> WatchIdentity | None:
    kind = str(play.get("media_type") or "")
    if kind not in {"movie", "anime_movie", "episode", "anime_episode"}:
        return None
    parts = str(play.get("item_key") or "").split(":")
    try:
        season, episode = (int(parts[-2]), int(parts[-1])) if "episode" in kind else (None, None)
    except (IndexError, TypeError, ValueError):
        return None
    return WatchIdentity(kind, str(play.get("title") or ""),
                         str(play.get("watched_at") or ""), season, episode,
                         play.get("ids") or {})
