"""XP, level, rank, prestige, and challenge rules for Bingecord."""

from __future__ import annotations

from datetime import date
import math

RANKS = (
    (1, "Newcomer"),
    (10, "Casual Watcher"),
    (20, "Regular Viewer"),
    (30, "Binge Watcher"),
    (40, "Dedicated Viewer"),
    (50, "Media Enthusiast"),
    (60, "Watch Veteran"),
    (70, "Watch Master"),
    (80, "Watch Legend"),
    (90, "Screen Immortal"),
)

# Cumulative XP required to reach a level. Level 1 starts at zero XP.
def xp_for_level(level: int) -> int:
    level = max(1, min(int(level), 100))
    return round(350 * (level ** 1.5))

def level_from_xp(xp: int) -> int:
    xp = max(0, int(xp))
    level = 1
    for candidate in range(2, 101):
        if xp < xp_for_level(candidate):
            break
        level = candidate
    return level

def roll_prestige(progression: dict) -> int:
    """Carry XP beyond level 100 into successive prestige tiers."""
    threshold = xp_for_level(100)
    count, remainder = divmod(max(0, int(progression.get("xp", 0))), threshold)
    if count:
        progression["xp"] = remainder
        progression["prestige"] = int(progression.get("prestige", 0)) + count
    return count

def level_progress(xp: int) -> tuple[int, int, int]:
    level = level_from_xp(xp)
    current_floor = 0 if level == 1 else xp_for_level(level)
    next_floor = xp_for_level(level + 1) if level < 100 else xp_for_level(100)
    if level >= 100:
        return level, xp, xp_for_level(100)
    return level, max(0, int(xp) - current_floor), next_floor - current_floor

def rank_for_level(level: int) -> str:
    current = RANKS[0][1]
    for minimum, name in RANKS:
        if level >= minimum:
            current = name
        else:
            break
    return current

XP_PER_MINUTE = 4
WATCH_XP_RULE = "minutes_v1"


def watch_minutes(media_type: str, runtime_minutes=None) -> float:
    """Use catalog runtime; invalid data falls back to 25-minute TV/90-minute film."""
    try:
        runtime = float(runtime_minutes)
        if isinstance(runtime_minutes, bool) or not math.isfinite(runtime) or runtime <= 0:
            raise ValueError
        return runtime
    except (TypeError, ValueError, OverflowError):
        return 90.0 if media_type in {"movie", "anime_movie"} else 25.0


def xp_for_watch(media_type: str, runtime_minutes=None) -> int:
    """Four XP per catalog minute for all media; round once, half up."""
    return max(1, math.floor(watch_minutes(media_type, runtime_minutes) * XP_PER_MINUTE + 0.5))


def event_watch_minutes(event: dict) -> float:
    # Never infer minutes from legacy tier-based XP awards.
    value = event.get("runtime_minutes")
    try:
        value = float(value)
        return value if math.isfinite(value) and value > 0 else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


def challenge_pool(period: str, day_number: int, *, minutes_based=False) -> list[dict]:
    if period == "daily":
        pools = (
            {"id": "daily_episodes_2", "name": "Watch 2 episodes", "target": 2, "kind": "episodes", "xp": 250},
            {"id": "daily_movie_1", "name": "Watch 1 movie", "target": 1, "kind": "movies", "xp": 400},
            {"id": "daily_watches_5", "name": "Watch 5 items", "target": 5, "kind": "watches", "xp": 500},
            {"id": "daily_episodes_5", "name": "Watch 5 episodes", "target": 5, "kind": "episodes", "xp": 500},
            {"id": "daily_movies_2", "name": "Watch 2 movies", "target": 2, "kind": "movies", "xp": 650},
        )
    else:
        pools = (
            {"id": "weekly_episodes_10", "name": "Watch 10 episodes", "target": 10, "kind": "episodes", "xp": 1000},
            {"id": "weekly_movies_3", "name": "Watch 3 movies", "target": 3, "kind": "movies", "xp": 1500},
            {"id": "weekly_watches_15", "name": "Watch 15 items", "target": 15, "kind": "watches", "xp": 1800},
            {"id": "weekly_episodes_20", "name": "Watch 20 episodes", "target": 20, "kind": "episodes", "xp": 2000},
            {"id": "weekly_movies_5", "name": "Watch 5 movies", "target": 5, "kind": "movies", "xp": 2500},
        )
    if minutes_based:
        targets = (30, 60, 90, 120) if period == "daily" else (180, 300, 450, 600)
        rewards = (250, 400, 500, 650) if period == "daily" else (1000, 1500, 1800, 2000)
        pools = tuple({"id": f"{period}_minutes_{target}", "name": f"Watch {target} minutes",
                       "target": target, "kind": "minutes", "xp": xp}
                      for target, xp in zip(targets, rewards)) + (
            {"id": "daily_movie_1", "name": "Watch 1 movie", "target": 1, "kind": "movies", "xp": 400}
            if period == "daily" else
            {"id": "weekly_movies_3", "name": "Watch 3 movies", "target": 3, "kind": "movies", "xp": 1500},)
    offset = day_number % len(pools)
    return [pools[offset], pools[(offset + 2) % len(pools)], pools[(offset + 4) % len(pools)]]

def challenges_for(day: date) -> tuple[list[dict], list[dict]]:
    ordinal = day.toordinal()
    daily = challenge_pool("daily", ordinal, minutes_based=day >= date(2026, 10, 13))
    # Keep the weekly set stable from Monday through Sunday.
    monday_ordinal = ordinal - day.weekday()
    weekly = challenge_pool("weekly", monday_ordinal, minutes_based=monday_ordinal >= date(2026, 10, 12).toordinal())
    return daily, weekly

def challenge_progress(events: list[dict], challenge: dict, start_iso: str, end_iso: str) -> int | float:
    total = 0
    for event in events:
        stamp = str(event.get("at") or "")
        if not start_iso <= stamp <= end_iso:
            continue
        media_type = event.get("media_type")
        if challenge["kind"] == "episodes" and media_type not in {"episode", "anime_episode"}:
            continue
        if challenge["kind"] == "movies" and media_type not in {"movie", "anime_movie"}:
            continue
        if media_type not in {"episode", "anime_episode", "movie", "anime_movie"}:
            continue
        total += event_watch_minutes(event) if challenge["kind"] == "minutes" else 1
    return min(total, int(challenge["target"]))
