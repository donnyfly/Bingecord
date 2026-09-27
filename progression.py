"""XP, level, rank, prestige, and challenge rules for SIMKLTrackerBot."""

from __future__ import annotations

from datetime import date

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

def xp_for_watch(media_type: str, runtime_minutes: int | float | None = None) -> int:
    """Return XP for one watch event, with longer episodes worth more."""
    if media_type in {"movie", "anime_movie"}:
        return 300
    try:
        runtime = float(runtime_minutes) if runtime_minutes is not None else 0
    except (TypeError, ValueError):
        runtime = 0
    if runtime >= 180:
        return 250
    if runtime >= 150:
        return 225
    if runtime >= 120:
        return 200
    if runtime >= 90:
        return 175
    if runtime >= 60:
        return 150
    if runtime >= 40:
        return 125
    return 100

def challenge_pool(period: str, day_number: int) -> list[dict]:
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
    offset = day_number % len(pools)
    return [pools[offset], pools[(offset + 2) % len(pools)], pools[(offset + 4) % len(pools)]]

def challenges_for(day: date) -> tuple[list[dict], list[dict]]:
    ordinal = day.toordinal()
    daily = challenge_pool("daily", ordinal)
    # Keep the weekly set stable from Monday through Sunday.
    monday_ordinal = ordinal - day.weekday()
    weekly = challenge_pool("weekly", monday_ordinal)
    return daily, weekly

def challenge_progress(events: list[dict], challenge: dict, start_iso: str, end_iso: str) -> int:
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
        total += 1
    return min(total, int(challenge["target"]))
