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
    # Episode coordinates identify the title, not a particular watch. A
    # rewatch of the same episode must remain a separate XP occurrence.
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


def build_occurrence_index(xp_events: list[dict], plays: dict[str, dict],
                           simkl_observations: dict[str, dict] | None = None,
                           simkl_account_id=None) -> dict[str, dict]:
    """Index current observations without changing any existing XP awards.

    This is a safe migration view of the legacy data. It records ambiguous
    pairings and preexisting double awards for later reconciliation.
    """
    result: dict[str, dict] = {}
    simkl = []
    source_events = {str(event.get("event_key")): event for event in xp_events
                     if identity_from_event(event) is not None
                     and not str(event.get("event_key") or "").startswith("wetrakr:")}
    for key, observed in (simkl_observations or {}).items():
        if key in source_events:
            if not source_events[key].get("ids") and observed.get("ids"):
                source_events[key] = {**source_events[key], "ids": observed["ids"]}
        else:
            source_events[key] = {"event_key": key, "media_type": observed.get("media_type"),
                                  "title": observed.get("title"), "at": observed.get("watched_at"),
                                  "ids": observed.get("ids") or {}}
    awarded_keys = {str(event.get("event_key")) for event in xp_events}
    for event in source_events.values():
        key = str(event.get("event_key") or "")
        if key.startswith("wetrakr:"):
            continue
        identity = identity_from_event(event)
        if identity is None:
            continue
        occurrence_id = "simkl:" + key
        result[occurrence_id] = {
            "observations": [{"provider": "simkl", "account_id": str(simkl_account_id or ""),
                              "event_id": key}],
            "award_keys": [key] if key in awarded_keys else [],
            "match_reason": None, "needs_review": False,
        }
        simkl.append((occurrence_id, identity))

    claimed: set[str] = set()
    for play_id, play in sorted(plays.items(), key=lambda pair: (str(pair[1].get("watched_at") or ""), pair[0])):
        identity = identity_from_play(play)
        if identity is None:
            continue
        matches = [(occurrence_id, reason) for occurrence_id, other in simkl
                   if occurrence_id not in claimed
                   for reason in [match_reason(other, identity)] if reason]
        matches.sort(key=lambda pair: 0 if pair[1] == "verified_id" else 1)
        if matches:
            occurrence_id, reason = matches[0]
            claimed.add(occurrence_id)
            occurrence = result[occurrence_id]
            occurrence["observations"].append({"provider": "wetrakr",
                                               "account_id": str(play.get("account_id") or ""),
                                               "event_id": play.get("source_event_id", play_id)})
            occurrence["match_reason"] = reason
            occurrence["needs_review"] = reason != "verified_id" or len(matches) > 1
        else:
            occurrence_id = "wetrakr:" + play_id
            result[occurrence_id] = {
                "observations": [{"provider": "wetrakr",
                                  "account_id": str(play.get("account_id") or ""),
                                  "event_id": play.get("source_event_id", play_id)}],
                "award_keys": [], "match_reason": None, "needs_review": False,
            }
        award_key = play.get("xp_key")
        if award_key and award_key not in result[occurrence_id]["award_keys"]:
            result[occurrence_id]["award_keys"].append(award_key)
        if len(result[occurrence_id]["award_keys"]) > 1:
            result[occurrence_id]["needs_review"] = True

    # A transferred WeTrakr award is keyed by its source play even though the
    # old SIMKL event has been removed. Keep it visible in the index.
    for event in xp_events:
        key = str(event.get("event_key") or "")
        if not key.startswith("wetrakr:"):
            continue
        occurrence_id = "wetrakr:" + key[len("wetrakr:"):].split(":", 1)[0]
        occurrence = result.get(occurrence_id)
        if occurrence and key not in occurrence["award_keys"]:
            occurrence["award_keys"].append(key)
    return result
