"""
Lightweight persistent storage for the SIMKL Discord bot.

Global user records contain SIMKL authentication and personal preferences.
Guild records contain server configuration and per-server tracking state.
"""

import asyncio
import copy
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from progression import challenges_for, roll_prestige, xp_for_level
from community import episode_contributions, split_pool

DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "store.json")

DEFAULT_POLL_INTERVAL_MINUTES = 60
EPOCH_ISO = "1970-01-01T00:00:00Z"
DEFAULT_TIMEZONE = "Asia/Singapore"


def _default_timezone_name() -> str:
    value = os.getenv("SIMKL_DEFAULT_TIMEZONE", DEFAULT_TIMEZONE).strip()
    return value or DEFAULT_TIMEZONE


def _resolve_timezone(name: str | None):
    value = (name or "").strip()
    if not value:
        value = _default_timezone_name()
    try:
        return ZoneInfo(value)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        try:
            return ZoneInfo(_default_timezone_name())
        except (TypeError, ValueError, ZoneInfoNotFoundError):
            return timezone.utc

_lock = asyncio.Lock()
_write_lock = asyncio.Lock()

DEFAULT_EMBED_PREFERENCES = {
    "style": "rich",
    "artwork": "backdrop",
    "activity_text": "detailed",
    "show_imdb": True,
    "show_mal": True,
    "episode_code": False,
}

DEFAULT_FEATURES = dict.fromkeys(("progression", "achievements", "challenges", "community",
                                "weekly_recaps", "leaderboards", "statistics", "discovery", "watched_together"), True)


def _default_guild() -> dict:
    return {
        "channel_id": None,
        "features": dict(DEFAULT_FEATURES),
        "embed_preferences": copy.deepcopy(DEFAULT_EMBED_PREFERENCES),
        "force_embed_preferences": False,
        "timezone": None,
        "weekly_recap_last_sent": None,
        "community_challenges": {},
        "users": {},
    }


def _default_data() -> dict:
    return {
        "poll_interval_minutes": DEFAULT_POLL_INTERVAL_MINUTES,
        "users": {},
        "guilds": {},
    }


def _load_from_disk() -> dict:
    if not os.path.exists(DATA_PATH):
        return _default_data()

    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return _default_data()

    if not isinstance(data, dict):
        return _default_data()

    data.setdefault("poll_interval_minutes", DEFAULT_POLL_INTERVAL_MINUTES)
    if not isinstance(data["users"], dict):
        data["users"] = {}
    if not isinstance(data.get("guilds"), dict):
        data["guilds"] = {}

    return data


def _write_to_disk(text: str) -> None:
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    tmp_path = DATA_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, DATA_PATH)


def _json_default(obj):
    if isinstance(obj, set):
        return sorted(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serialisable")


def _default_statistics() -> dict:
    return {
        "episodes_watched": 0,
        "movies_watched": 0,
        "anime_episodes_watched": 0,
        "anime_movies_watched": 0,
        "watch_dates": {},
        "titles": {},
        "watch_events": {},
    }


def _watch_event_id(event: dict) -> str:
    return f"{event['media_type']}:{event['item_key']}:{event['watched_at']}"


def _watch_base(event: dict) -> str:
    return f"{event['media_type']}:{event['item_key']}:"


def _genre_names(genres) -> list[str]:
    values=[genres] if isinstance(genres,(str,dict)) else (genres or [])
    names=[]
    for value in values:
        name=value.get("name") if isinstance(value,dict) else value
        if isinstance(name,str) and name.strip():
            names.append(name.strip().title())
    return sorted(set(names))


def _rebuild_watch_statistics(events: dict, timezone_name: str | None) -> dict:
    stats=_default_statistics()
    stats["watch_events"]=events
    tz=_resolve_timezone(timezone_name)
    for event in events.values():
        media_type=event["media_type"]
        if media_type not in {"episode","anime_episode","movie","anime_movie"}:
            continue
        category=("anime_episodes" if media_type=="anime_episode" else
                  "anime_movies" if media_type=="anime_movie" else
                  "episodes" if media_type=="episode" else "movies")
        counter="episodes_watched" if "episode" in media_type else "movies_watched"
        stats[counter]+=1
        if media_type.startswith("anime_"):
            stats["anime_"+counter]+=1
        stamp=event.get("watched_at")
        if stamp:
            try:
                watched=datetime.fromisoformat(str(stamp).replace("Z","+00:00"))
                if watched.tzinfo is None:
                    watched=watched.replace(tzinfo=timezone.utc)
                day=watched.astimezone(tz).date().isoformat()
            except (TypeError,ValueError):
                day=str(stamp)[:10]
            if day:
                daily=stats["watch_dates"].setdefault(day,{})
                daily[category]=int(daily.get(category,0))+1
                daily["total"]=int(daily.get("total",0))+1
        item_key=event["item_key"]
        title=stats["titles"].setdefault(item_key,{
            "title":event.get("title") or "Untitled", "type":media_type,
            "count":0,"last_watched":None,
        })
        title["count"]+=1
        if not title["last_watched"] or str(stamp or "")>str(title["last_watched"]):
            title["last_watched"]=stamp
        if event.get("genres"):
            title["genres"]=event["genres"]
    return stats


def _record_watch_stats(stats: dict, media_type: str, title: str, item_key: str,
                        watched_at: str, genres, timezone_name: str | None) -> bool:
    event={"media_type":media_type,"title":title or "Untitled","item_key":item_key,"watched_at":watched_at}
    names=_genre_names(genres)
    if names:
        event["genres"]=names
    if isinstance(stats.get("watch_events"),dict):
        event_id=_watch_event_id(event)
        if event_id in stats["watch_events"]:
            return False
        stats["watch_events"][event_id]=event
    category=("anime_episodes" if media_type=="anime_episode" else
              "anime_movies" if media_type=="anime_movie" else
              "movies" if media_type=="movie" else "episodes")
    stats["episodes_watched" if "episode" in media_type else "movies_watched"]+=1
    if media_type.startswith("anime_"):
        stats["anime_episodes_watched" if media_type=="anime_episode" else "anime_movies_watched"]+=1
    if watched_at:
        try:
            watched=datetime.fromisoformat(watched_at.replace("Z","+00:00"))
            if watched.tzinfo is None:
                watched=watched.replace(tzinfo=timezone.utc)
            day=watched.astimezone(_resolve_timezone(timezone_name)).date().isoformat()
        except (TypeError,ValueError):
            day=watched_at[:10]
        if day:
            daily=stats["watch_dates"].setdefault(day,{})
            daily[category]=int(daily.get(category,0))+1
            daily["total"]=int(daily.get("total",0))+1
    record=stats["titles"].setdefault(item_key,{
        "title":title or "Untitled","type":media_type,"count":0,"last_watched":None,
    })
    record["title"]=title or record.get("title") or "Untitled"
    record["type"]=media_type
    record["count"]=int(record.get("count",0))+1
    record["last_watched"]=watched_at
    if names:
        record["genres"]=names
    return True


def _add_watch_xp_events(progression: dict, events: list[dict]) -> tuple[int,list[dict]]:
    """Apply one imported history batch without copying or pruning per watch."""
    known=progression["watch_xp_keys"]
    added=[]
    amount=0
    for event in events:
        key=event["event_key"]
        if key in known:
            continue
        stamp=event["at"]
        xp=max(0,int(event["amount"]))
        known[key]=stamp
        added.append({"at":stamp,"amount":xp,"media_type":event["media_type"],
                      "title":event.get("title") or "Untitled","event_key":key})
        amount+=xp
    if added:
        progression["xp"]+=amount
        progression["lifetime_xp"]+=amount
        progression["xp_events"].extend(added)
        roll_prestige(progression)
    return amount,added


def _complete_watch_challenges(progression: dict, added: list[dict]) -> None:
    """Count each touched day/week once instead of scanning history per watch."""
    watch_types={"episode","anime_episode","movie","anime_movie"}
    touched=set()
    for event in added:
        if event["media_type"] in watch_types and event["at"] >= "2025-01-01T00:00:00Z":
            touched.add(event["at"][:10])
    if not touched:
        return
    from datetime import date, timedelta
    daily=defaultdict(lambda: {"episodes":0,"movies":0,"watches":0})
    weekly=defaultdict(lambda: {"episodes":0,"movies":0,"watches":0})
    touched_weeks=set()
    for day in touched:
        parsed=date.fromisoformat(day)
        touched_weeks.add((parsed-timedelta(days=parsed.weekday())).isoformat())
    for event in progression["xp_events"]:
        media_type=event.get("media_type")
        if media_type not in watch_types:
            continue
        day=str(event.get("at") or "")[:10]
        try:
            parsed=date.fromisoformat(day)
        except ValueError:
            continue
        week=(parsed-timedelta(days=parsed.weekday())).isoformat()
        if day not in touched and week not in touched_weeks:
            continue
        kind="episodes" if "episode" in media_type else "movies"
        if day in touched:
            daily[day][kind]+=1
            daily[day]["watches"]+=1
        if week in touched_weeks:
            weekly[week][kind]+=1
            weekly[week]["watches"]+=1
    now=datetime.now(timezone.utc).isoformat()
    completions=progression["challenge_completions"]
    for day in touched:
        for challenge in challenges_for(date.fromisoformat(day))[0]:
            key=f"daily:{day}"
            if daily[day][challenge["kind"]]>=challenge["target"] and challenge["id"] not in completions.get(key,{}):
                completions.setdefault(key,{})[challenge["id"]]={"completed_at":now,"xp":challenge["xp"]}
                progression["xp"]+=challenge["xp"]
                progression["lifetime_xp"]+=challenge["xp"]
                roll_prestige(progression)
    for week in touched_weeks:
        for challenge in challenges_for(date.fromisoformat(week))[1]:
            key=f"weekly:{week}"
            if weekly[week][challenge["kind"]]>=challenge["target"] and challenge["id"] not in completions.get(key,{}):
                completions.setdefault(key,{})[challenge["id"]]={"completed_at":now,"xp":challenge["xp"]}
                progression["xp"]+=challenge["xp"]
                progression["lifetime_xp"]+=challenge["xp"]
                roll_prestige(progression)


def _default_activity_state() -> dict:
    return {
        "statuses": {},
        "watch_times": {},
        "statuses_seeded": False,
    }


def _default_guild_user(start_time_iso: str | None = None) -> dict:
    start = start_time_iso or EPOCH_ISO
    return {
        "history_seeded": False,
        "history_stats_repaired": False,
        "last_checked": {
            "shows": start,
            "movies": start,
            "anime": start,
        },
        "announced": set(),
        "activity_state": _default_activity_state(),
        "statistics": _default_statistics(),
        "achievements": {},
        "last_poll_at": None,
        "last_success_at": None,
        "last_error": None,
        "consecutive_failures": 0,
    }


def _normalise_user(user: dict) -> None:
    user.setdefault("simkl_token", None)
    user.setdefault("refresh_token", None)
    user.setdefault("token_expires_at", None)
    user.setdefault("simkl_username", "unknown")
    user.setdefault("simkl_account_id", None)
    user.setdefault("embed_preferences", copy.deepcopy(DEFAULT_EMBED_PREFERENCES))
    user.setdefault("progression", {"xp": 0, "lifetime_xp": 0, "prestige": 0, "watch_xp_keys": {}, "xp_events": [], "challenge_completions": {}, "achievement_xp_awarded": {}, "history_xp_seeded": False, "history_xp_notification_sent": False})
    if not isinstance(user["progression"], dict):
        user["progression"] = {"xp": 0, "lifetime_xp": 0, "prestige": 0, "watch_xp_keys": {}, "xp_events": [], "challenge_completions": {}, "achievement_xp_awarded": {}}
    progression = user["progression"]
    progression.setdefault("xp", 0)
    progression.setdefault("lifetime_xp", 0)
    progression.setdefault("prestige", 0)
    progression.setdefault("prestige_notified", int(progression.get("prestige", 0)))
    progression.setdefault("watch_xp_keys", {})
    progression.setdefault("xp_events", [])
    progression.setdefault("challenge_completions", {})
    progression.setdefault("community_rewards", {})
    progression.setdefault("achievement_xp_awarded", {})
    progression.setdefault("history_xp_seeded", False)
    progression.setdefault("history_xp_notification_sent", False)
    progression["history_xp_notification_sent"] = bool(progression.get("history_xp_notification_sent", False))
    if not isinstance(progression["watch_xp_keys"], dict): progression["watch_xp_keys"] = {}
    if not isinstance(progression["xp_events"], list): progression["xp_events"] = []
    if not isinstance(progression["challenge_completions"], dict): progression["challenge_completions"] = {}
    if not isinstance(progression["community_rewards"], dict): progression["community_rewards"] = {}
    if not isinstance(progression["achievement_xp_awarded"], dict): progression["achievement_xp_awarded"] = {}
    progression["history_xp_seeded"] = bool(progression.get("history_xp_seeded", False))

    prefs = user["embed_preferences"]
    if not isinstance(prefs, dict):
        prefs = copy.deepcopy(DEFAULT_EMBED_PREFERENCES)
        user["embed_preferences"] = prefs
    # Migrate the removed legacy "Poster" style to its equivalent
    # Rich layout + Poster artwork combination.
    if prefs.get("style") == "poster":
        prefs["style"] = "rich"
        prefs["artwork"] = "poster"
    prefs.setdefault("style", "rich")
    prefs.setdefault("artwork", "backdrop")
    prefs.setdefault("activity_text", "detailed")
    prefs.setdefault("show_imdb", True)
    prefs.setdefault("show_mal", True)
    prefs.setdefault("episode_code", False)
    user.setdefault(
        "embed_preferences_custom",
        prefs != DEFAULT_EMBED_PREFERENCES,
    )


def _normalise_guild_user(user: dict) -> None:
    defaults = _default_guild_user()
    user.setdefault("history_seeded", defaults["history_seeded"])
    user.setdefault("history_stats_repaired", False)
    user.setdefault("last_checked", copy.deepcopy(defaults["last_checked"]))
    if not isinstance(user["last_checked"], dict):
        user["last_checked"] = copy.deepcopy(defaults["last_checked"])
    for media_type in ("shows", "movies", "anime"):
        user["last_checked"].setdefault(media_type, EPOCH_ISO)

    user.setdefault("activity_state", _default_activity_state())
    user.setdefault("statistics", _default_statistics())
    stats = user["statistics"]
    if not isinstance(stats, dict):
        stats = _default_statistics()
        user["statistics"] = stats
    for key, default in _default_statistics().items():
        stats.setdefault(key, None if key == "watch_events" else copy.deepcopy(default))
    if not isinstance(stats.get("watch_dates"), dict):
        stats["watch_dates"] = {}
    if not isinstance(stats.get("titles"), dict):
        stats["titles"] = {}
    if stats.get("watch_events") is not None and not isinstance(stats["watch_events"], dict):
        stats["watch_events"] = None
    achievements = user.get("achievements")
    if not isinstance(achievements, dict):
        user["achievements"] = {}
    state = user["activity_state"]
    if not isinstance(state, dict):
        state = _default_activity_state()
        user["activity_state"] = state
    state.setdefault("statuses", {})
    state.setdefault("watch_times", {})
    state.setdefault("statuses_seeded", False)

    user.setdefault("last_poll_at", defaults["last_poll_at"])
    user.setdefault("last_success_at", defaults["last_success_at"])
    user.setdefault("last_error", defaults["last_error"])
    try:
        user["consecutive_failures"] = max(int(user.get("consecutive_failures", 0)), 0)
    except (TypeError, ValueError):
        user["consecutive_failures"] = 0

    announced = user.get("announced", [])
    if isinstance(announced, set):
        user["announced"] = announced
    elif isinstance(announced, list):
        user["announced"] = set(announced)
    else:
        user["announced"] = set()


def _normalise_guild(guild: dict) -> None:
    defaults = _default_guild()
    guild.setdefault("channel_id", defaults["channel_id"])
    if not isinstance(guild.get("features"), dict):
        guild["features"] = dict(DEFAULT_FEATURES)
    for key, value in DEFAULT_FEATURES.items():
        guild["features"].setdefault(key, value)
    guild.setdefault("embed_preferences", copy.deepcopy(DEFAULT_EMBED_PREFERENCES))
    guild.setdefault("force_embed_preferences", False)
    guild.setdefault("timezone", None)
    guild.setdefault("community_challenges", {})
    if not isinstance(guild["community_challenges"], dict):
        guild["community_challenges"] = {}
    guild.setdefault("weekly_recap_last_sent", None)
    if guild.get("timezone") is not None and not isinstance(guild.get("timezone"), str):
        guild["timezone"] = None
    if not isinstance(guild["embed_preferences"], dict):
        guild["embed_preferences"] = copy.deepcopy(DEFAULT_EMBED_PREFERENCES)
    # Migrate the removed legacy "Poster" style to its equivalent
    # Rich layout + Poster artwork combination.
    if guild["embed_preferences"].get("style") == "poster":
        guild["embed_preferences"]["style"] = "rich"
        guild["embed_preferences"]["artwork"] = "poster"
    guild["embed_preferences"].setdefault("style", "rich")
    guild["embed_preferences"].setdefault("artwork", "backdrop")
    guild["embed_preferences"].setdefault("activity_text", "detailed")
    guild["embed_preferences"].setdefault("show_imdb", True)
    guild["embed_preferences"].setdefault("show_mal", True)
    guild["embed_preferences"].setdefault("episode_code", False)
    guild.setdefault("users", {})
    if not isinstance(guild["users"], dict):
        guild["users"] = {}
    for user in guild["users"].values():
        if isinstance(user, dict):
            _normalise_guild_user(user)


class Storage:
    async def get_features(self, guild_id: str | int) -> dict:
        async with _lock:
            guild = self._guild(str(guild_id))
            return dict(guild["features"]) if guild else dict(DEFAULT_FEATURES)

    async def set_features(self, guild_id: str | int, changes: dict) -> None:
        if any(key not in DEFAULT_FEATURES or not isinstance(value, bool) for key,value in changes.items()):
            raise ValueError("Unknown feature or invalid feature value")
        async with _lock:
            guild = self._guild(str(guild_id), create=True)
            guild["features"].update(changes)
            self._dirty = True
        await self.flush()

    def __init__(self):
        self._data = _load_from_disk()
        migrated_legacy_preferences = False

        for user in self._data["users"].values():
            if isinstance(user, dict):
                had_legacy_poster_style = (
                    isinstance(user.get("embed_preferences"), dict)
                    and user["embed_preferences"].get("style") == "poster"
                )
                _normalise_user(user)
                migrated_legacy_preferences |= had_legacy_poster_style

        for guild in self._data["guilds"].values():
            if isinstance(guild, dict):
                had_legacy_poster_style = (
                    isinstance(guild.get("embed_preferences"), dict)
                    and guild["embed_preferences"].get("style") == "poster"
                )
                _normalise_guild(guild)
                migrated_legacy_preferences |= had_legacy_poster_style

        self._dirty = migrated_legacy_preferences

    def _user(self, discord_user_id: str) -> dict | None:
        user = self._data["users"].get(discord_user_id)
        if not isinstance(user, dict):
            return None
        _normalise_user(user)
        progression = user["progression"]
        # The retired /simkl-prestige command set current XP to zero, even if
        # the user had earned well past the Level 100 threshold. Lifetime XP
        # kept those points. Restore the missing carry exactly once for older
        # prestige records; do not add XP to the lifetime total or XP ledger.
        if int(progression.get("prestige", 0)) and not progression.get("prestige_carry_repaired"):
            expected = max(0, int(progression.get("lifetime_xp", 0))
                           - int(progression["prestige"]) * xp_for_level(100))
            progression["xp"] = max(int(progression.get("xp", 0)), expected)
            progression["prestige_carry_repaired"] = True
            self._dirty = True
        if roll_prestige(progression):
            self._dirty = True
        return user

    def _guild(self, guild_id: str, create: bool = False) -> dict | None:
        guild_id = str(guild_id)
        guild = self._data["guilds"].get(guild_id)
        if guild is None and create:
            guild = _default_guild()
            self._data["guilds"][guild_id] = guild
            self._dirty = True
        # Loaded guilds are normalized once in __init__; new guilds already
        # contain defaults. Avoid rescanning every member on every lookup.
        return guild

    def _guild_user(self, guild_id: str, discord_user_id: str, create: bool = False) -> dict | None:
        guild = self._guild(guild_id, create=create)
        if guild is None:
            return None
        user = guild["users"].get(str(discord_user_id))
        if user is None and create:
            user = _default_guild_user()
            guild["users"][str(discord_user_id)] = user
            self._dirty = True
        if isinstance(user, dict):
            _normalise_guild_user(user)
        return user

    def _migrate_legacy_guild_locked(self, guild_id: str) -> None:
        guild_id = str(guild_id)
        if guild_id in self._data["guilds"]:
            return

        old_channel = self._data.get("channel_id")
        old_prefs = self._data.get("server_embed_preferences")
        legacy_users = []

        for uid, old_user in self._data["users"].items():
            if not isinstance(old_user, dict):
                continue
            if any(
                key in old_user
                for key in ("history_seeded", "last_checked", "announced", "activity_state")
            ):
                legacy_users.append((uid, old_user))

        guild = _default_guild()
        if old_channel:
            guild["channel_id"] = old_channel
        if isinstance(old_prefs, dict):
            guild["embed_preferences"].update({
                k: old_prefs.get(k, v)
                for k, v in DEFAULT_EMBED_PREFERENCES.items()
            })

        for uid, old_user in legacy_users:
            guild_user = _default_guild_user()
            guild_user["history_seeded"] = bool(old_user.get("history_seeded", False))
            guild_user["history_stats_repaired"] = bool(old_user.get("history_stats_repaired", False))
            guild_user["last_checked"] = copy.deepcopy(
                old_user.get("last_checked") or guild_user["last_checked"]
            )
            guild_user["announced"] = set(old_user.get("announced", []))
            guild_user["activity_state"] = copy.deepcopy(
                old_user.get("activity_state") or _default_activity_state()
            )
            guild_user["statistics"] = copy.deepcopy(
                old_user.get("statistics") or _default_statistics()
            )
            guild["users"][uid] = guild_user

            # Preserve the user's personal preferences in the new global user record.
            _normalise_user(old_user)
            for key in (
                "simkl_token", "refresh_token", "token_expires_at",
                "simkl_username", "simkl_account_id",
                "embed_preferences", "embed_preferences_custom",
            ):
                if key in old_user:
                    self._data["users"].setdefault(uid, {})[key] = copy.deepcopy(old_user[key])

        self._data["guilds"][guild_id] = guild
        self._data.pop("channel_id", None)
        self._data.pop("server_embed_preferences", None)
        self._dirty = True

    async def flush(self) -> None:
        async with _write_lock:
            async with _lock:
                if not self._dirty:
                    return
                text = json.dumps(self._data, indent=2, default=_json_default) + "\n"
                self._dirty = False
            try:
                await asyncio.to_thread(_write_to_disk, text)
            except Exception:
                self._dirty = True
                raise

    async def ensure_guild(self, guild_id: int | str) -> None:
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            self._guild(str(guild_id), create=True)
            self._dirty = True
        await self.flush()

    async def get_all(self) -> dict:
        async with _lock:
            return copy.deepcopy(self._data)

    async def get_user(self, discord_user_id: str) -> dict | None:
        async with _lock:
            user = self._user(discord_user_id)
            return copy.deepcopy(user) if user else None

    async def get_poll_targets(self, guild_id: str | None = None) -> list[dict]:
        async with _lock:
            guild_ids = [str(guild_id)] if guild_id is not None else list(self._data["guilds"].keys())
            targets = []
            for gid in guild_ids:
                guild = self._guild(gid)
                if not guild:
                    continue
                for uid in guild["users"].keys():
                    user = self._user(uid)
                    if not user or not user.get("simkl_token"):
                        continue
                    guild_user = self._guild_user(gid, uid)
                    if not guild_user:
                        continue
                    targets.append({
                        "guild_id": gid,
                        "channel_id": guild["channel_id"],
                        "discord_user_id": uid,
                        "user_data": copy.deepcopy(user),
                        "guild_user_data": copy.deepcopy(guild_user),
                    })
            return targets

    async def get_channel(self, guild_id: int | str) -> int | None:
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            guild = self._guild(guild_id, create=True)
            return guild.get("channel_id")

    async def set_channel(self, guild_id: int | str, channel_id: int) -> None:
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            guild = self._guild(guild_id, create=True)
            guild["channel_id"] = channel_id
            self._dirty = True
        await self.flush()

    async def get_timezone(self, guild_id: int | str) -> dict:
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            guild = self._guild(guild_id, create=True)
            configured = guild.get("timezone")
            if configured:
                try:
                    ZoneInfo(configured)
                    return {"name": configured, "source": "server"}
                except (TypeError, ValueError, ZoneInfoNotFoundError):
                    pass
            default_name = _default_timezone_name()
            try:
                ZoneInfo(default_name)
            except (TypeError, ValueError, ZoneInfoNotFoundError):
                default_name = "UTC"
            return {"name": default_name, "source": "environment" if os.getenv("SIMKL_DEFAULT_TIMEZONE") else "built-in"}

    async def set_timezone(self, guild_id: int | str, timezone_name: str | None) -> None:
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            guild = self._guild(guild_id, create=True)
            guild["timezone"] = timezone_name.strip() if timezone_name else None
            self._dirty = True
        await self.flush()

    async def get_weekly_recap_last_sent(self, guild_id: int | str) -> str | None:
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            guild = self._guild(guild_id, create=True)
            return guild.get("weekly_recap_last_sent")

    async def set_weekly_recap_last_sent(self, guild_id: int | str, week_key: str) -> None:
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            guild = self._guild(guild_id, create=True)
            guild["weekly_recap_last_sent"] = week_key
            self._dirty = True
        await self.flush()

    async def get_server_embed_preferences(self, guild_id: int | str) -> dict:
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            guild = self._guild(guild_id, create=True)
            return copy.deepcopy(guild["embed_preferences"])

    async def set_server_embed_preferences(self, guild_id: int | str, style=None, artwork=None, activity_text=None, show_imdb=None, show_mal=None, episode_code=None, force_override=None) -> None:
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            guild = self._guild(guild_id, create=True)
            prefs = guild["embed_preferences"]
            if style is not None:
                prefs["style"] = style
            if artwork is not None:
                prefs["artwork"] = artwork
            if activity_text is not None:
                prefs["activity_text"] = activity_text
            if show_imdb is not None:
                prefs["show_imdb"] = bool(show_imdb)
            if show_mal is not None:
                prefs["show_mal"] = bool(show_mal)
            if episode_code is not None:
                prefs["episode_code"] = bool(episode_code)
            if force_override is not None:
                guild["force_embed_preferences"] = bool(force_override)
            self._dirty = True
        await self.flush()

    async def reset_server_embed_preferences(self, guild_id: int | str) -> None:
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            guild = self._guild(guild_id, create=True)
            guild["embed_preferences"] = copy.deepcopy(DEFAULT_EMBED_PREFERENCES)
            guild["force_embed_preferences"] = False
            self._dirty = True
        await self.flush()

    async def get_server_embed_force_override(self, guild_id: int | str) -> bool:
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            guild = self._guild(guild_id, create=True)
            return bool(guild.get("force_embed_preferences", False))

    async def get_embed_preferences(self, guild_id: str | int, discord_user_id: str) -> dict:
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            guild = self._guild(guild_id, create=True)
            user = self._user(discord_user_id)
            if guild.get("force_embed_preferences", False):
                return copy.deepcopy(guild["embed_preferences"])
            if user and user.get("embed_preferences_custom", False):
                return copy.deepcopy(user["embed_preferences"])
            return copy.deepcopy(guild["embed_preferences"])

    async def set_embed_preferences(self, discord_user_id: str, style=None, artwork=None, activity_text=None, show_imdb=None, show_mal=None, episode_code=None) -> None:
        async with _lock:
            user = self._user(discord_user_id)
            if not user:
                return
            prefs = user["embed_preferences"]
            if style is not None:
                prefs["style"] = style
            if artwork is not None:
                prefs["artwork"] = artwork
            if activity_text is not None:
                prefs["activity_text"] = activity_text
            if show_imdb is not None:
                prefs["show_imdb"] = bool(show_imdb)
            if show_mal is not None:
                prefs["show_mal"] = bool(show_mal)
            if episode_code is not None:
                prefs["episode_code"] = bool(episode_code)
            user["embed_preferences_custom"] = True
            self._dirty = True
        await self.flush()

    async def reset_embed_preferences(self, discord_user_id: str) -> None:
        async with _lock:
            user = self._user(discord_user_id)
            if not user:
                return
            user["embed_preferences_custom"] = False
            self._dirty = True
        await self.flush()

    async def reset_user_tracking(self, guild_id: str | int, discord_user_id: str, start_time_iso: str) -> bool:
        """Reset one user's server-local tracking state while preserving their SIMKL link and preferences."""
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            guild = self._guild(guild_id)
            if not guild or str(discord_user_id) not in guild["users"]:
                return False
            guild["users"][str(discord_user_id)] = _default_guild_user(start_time_iso)
            self._dirty = True
        await self.flush()
        return True

    async def get_progression(self, discord_user_id: str) -> dict:
        async with _lock:
            user = self._user(discord_user_id)
            return copy.deepcopy(user.get("progression", {})) if user else {}

    async def seed_progression_batch(self, discord_user_id: str, events: list[dict]) -> int:
        """Backfill global XP and its completion marker in one durable write."""
        async with _lock:
            user=self._user(discord_user_id)
            if not user or user["progression"].get("history_xp_seeded"):
                return 0
            progression=user["progression"]
            amount,_=_add_watch_xp_events(progression,events)
            progression["history_xp_seeded"]=True
            self._dirty=True
        await self.flush()
        return amount

    async def repair_missing_watch_xp(self, discord_user_id: str, entries: list[dict]) -> int:
        """Fill gaps from a full SIMKL snapshot without duplicating existing rewatches."""
        async with _lock:
            user=self._user(discord_user_id)
            if not user or not user["progression"].get("history_xp_seeded"):
                return 0
            progression=user["progression"]
            existing={key[:-len(stamp)] for key,stamp in progression["watch_xp_keys"].items()
                      if isinstance(key,str) and isinstance(stamp,str) and stamp and key.endswith(stamp)}
            seen=set()
            missing=[]
            for entry in entries:
                base=f"{entry['media_type']}:{entry['item_key']}:"
                if base in seen or base in existing:
                    continue
                seen.add(base)
                missing.append({"event_key":base+entry["watched_at"],
                                "media_type":entry["media_type"],"title":entry["title"],
                                "at":entry["watched_at"],
                                "amount":300 if entry["media_type"] in {"movie","anime_movie"} else 100})
            amount,_=_add_watch_xp_events(progression,missing)
            if amount:
                self._dirty=True
        if amount:
            await self.flush()
        return amount

    async def mark_history_xp_notification_sent(self, discord_user_id: str) -> None:
        async with _lock:
            user = self._user(discord_user_id)
            if not user:
                return
            user["progression"]["history_xp_notification_sent"] = True
            self._dirty = True
        await self.flush()

    async def award_watch_xp(self, discord_user_id: str, event_key: str, media_type: str, title: str, watched_at: str, amount: int) -> dict:
        """Award watch XP once globally per SIMKL watch event."""
        async with _lock:
            user = self._user(discord_user_id)
            if not user:
                return {"awarded": False, "amount": 0, "progression": {}}
            progression = user["progression"]
            if event_key in progression["watch_xp_keys"]:
                return {"awarded": False, "amount": 0, "progression": copy.deepcopy(progression)}
            xp = max(0, int(amount))
            _add_watch_xp_events(progression,[{"event_key":event_key,"media_type":media_type,
                                                "title":title,"at":watched_at,"amount":xp}])
            self._dirty = True
            result = copy.deepcopy(progression)
        await self.flush()
        return {"awarded": True, "amount": xp, "progression": result}

    async def seed_guild_history(self, guild_id: str | int, discord_user_id: str,
                                 keys: list[str], statuses: dict, watches: dict,
                                 records: list[tuple]) -> int:
        """Persist a guild's history, global watch XP, and earned challenges atomically."""
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            guild_user=self._guild_user(guild_id,discord_user_id)
            global_user=self._user(discord_user_id)
            if not guild_user or not global_user or guild_user["history_seeded"]:
                return 0
            guild=self._guild(guild_id)
            watches_to_record=[{
                "media_type":media_type,"title":title,"item_key":item_key,
                "watched_at":watched_at,"genres":genres,
                "amount":300 if media_type in {"movie","anime_movie"} else 100,
            } for media_type,title,item_key,watched_at,genres in records]
            amount=self._apply_watch_records_locked(guild_user,global_user,watches_to_record,
                                                     guild.get("timezone"))
            guild_user["announced"].update(keys)
            guild_user["activity_state"]["statuses"].update(statuses)
            guild_user["activity_state"]["watch_times"].update(watches)
            guild_user["activity_state"]["statuses_seeded"]=True
            guild_user["history_seeded"]=True
            guild_user["history_stats_repaired"]=True
            self._dirty=True
        await self.flush()
        return amount

    async def prepare_empty_history_repair(self, guild_id: str | int, discord_user_id: str) -> bool:
        """Reimport older linked accounts whose completed history has no statistics."""
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            user=self._guild_user(guild_id,discord_user_id)
            if not user or not user["history_seeded"] or user.get("history_stats_repaired"):
                return False
            stats=user["statistics"]
            if (int(stats.get("episodes_watched",0)) or int(stats.get("movies_watched",0))
                    or stats.get("watch_events")):
                return False
            user["history_seeded"]=False
            user["last_checked"]={media_type:EPOCH_ISO for media_type in ("shows","movies","anime")}
            # Keep announced events, progression, and the link. The seed uses
            # stable watch keys, so already awarded XP remains idempotent.
            self._dirty=True
        await self.flush()
        return True

    @staticmethod
    def _apply_watch_records_locked(guild_user: dict, global_user: dict,
                                     records: list[dict], timezone_name: str | None) -> int:
        xp_events=[]
        for record in records:
            media_type=record["media_type"]
            title=record["title"]
            item_key=record["item_key"]
            watched_at=record["watched_at"]
            _record_watch_stats(guild_user["statistics"],media_type,title,item_key,watched_at,
                                record.get("genres"),timezone_name)
            xp_events.append({"event_key":f"{media_type}:{item_key}:{watched_at}",
                              "media_type":media_type,"title":title,"at":watched_at,
                              "amount":record["amount"]})
        progression=global_user["progression"]
        amount,added=_add_watch_xp_events(progression,xp_events)
        _complete_watch_challenges(progression,added)
        return amount

    async def record_activity_batch(self, guild_id: str | int, discord_user_id: str,
                                    keys: list[str], watch_times: dict,
                                    records: list[dict]) -> int:
        """Record a posted activity group with one update and one disk write."""
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            guild_user=self._guild_user(guild_id,discord_user_id)
            global_user=self._user(discord_user_id)
            if not guild_user or not global_user:
                return 0
            guild=self._guild(guild_id)
            amount=self._apply_watch_records_locked(guild_user,global_user,records,
                                                     guild.get("timezone"))
            guild_user["announced"].update(keys)
            guild_user["activity_state"]["watch_times"].update(watch_times)
            self._dirty=True
        await self.flush()
        return amount

    async def reconcile_watch_xp(self, discord_user_id: str, active_watch_bases: set[str], media_types: set[str]) -> dict:
        """Remove watch XP for media items that no longer exist in SIMKL watch history.

        active_watch_bases contains the stable item prefix for each currently
        watched episode/movie. Rewatch events intentionally share the same
        base, so removing an item revokes all XP earned from that item while
        keeping valid rewatch XP for items that remain in SIMKL history.
        """
        async with _lock:
            user = self._user(discord_user_id)
            if not user:
                return {"removed": False, "amount": 0, "events": 0, "progression": {}}

            progression = user["progression"]
            xp_events = progression.get("xp_events", [])
            removed_keys = set()
            removed_amount = 0

            def event_base(event_key: str) -> str | None:
                if not isinstance(event_key, str):
                    return None
                if not event_key.startswith(("episode:", "anime_episode:", "movie:", "anime_movie:")):
                    return None
                timestamp_marker=event_key.rfind("T")
                if timestamp_marker <= 0:
                    return None
                delimiter=event_key.rfind(":", 0, timestamp_marker)
                if delimiter <= 0:
                    return None
                return event_key[:delimiter + 1]

            kept_events = []
            for event in xp_events:
                media_type = event.get("media_type")
                event_key = event.get("event_key")
                if media_type not in media_types:
                    kept_events.append(event)
                    continue
                base = event_base(event_key)
                if base is None or base in active_watch_bases:
                    kept_events.append(event)
                    continue
                removed_keys.add(event_key)
                removed_amount += max(0, int(event.get("amount", 0)))

            if not removed_keys:
                return {"removed": False, "amount": 0, "events": 0, "progression": copy.deepcopy(progression)}

            progression["xp_events"] = kept_events
            progression["watch_xp_keys"] = {
                key: stamp
                for key, stamp in progression.get("watch_xp_keys", {}).items()
                if key not in removed_keys
            }
            progression["xp"] = max(0, int(progression.get("xp", 0)) - removed_amount)
            progression["lifetime_xp"] = max(0, int(progression.get("lifetime_xp", 0)) - removed_amount)
            self._dirty = True
            result = copy.deepcopy(progression)

        await self.flush()
        return {
            "removed": True,
            "amount": removed_amount,
            "events": len(removed_keys),
            "progression": result,
        }

    async def get_challenge_state(self, discord_user_id: str) -> dict:
        async with _lock:
            user = self._user(discord_user_id)
            if not user:
                return {}
            return {"xp_events": copy.deepcopy(user["progression"].get("xp_events", [])), "challenge_completions": copy.deepcopy(user["progression"].get("challenge_completions", {}))}

    async def claim_prestige_notifications(self, discord_user_id: str) -> list[int]:
        """Claim each new prestige once, even across servers and polling workers."""
        async with _lock:
            user = self._user(discord_user_id)
            if not user:
                return []
            progression = user["progression"]
            previous = int(progression.get("prestige_notified", 0))
            current = int(progression.get("prestige", 0))
            if current <= previous:
                return []
            progression["prestige_notified"] = current
            self._dirty = True
            return list(range(previous + 1, current + 1))

    async def retry_prestige_notification(self, discord_user_id: str, number: int) -> None:
        async with _lock:
            user = self._user(discord_user_id)
            if user:
                progression = user["progression"]
                progression["prestige_notified"] = min(int(progression["prestige_notified"]), number - 1)
                self._dirty = True

    async def get_activity_state(self, guild_id: str | int, discord_user_id: str) -> dict:
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            user = self._guild_user(guild_id, discord_user_id)
            return copy.deepcopy(user["activity_state"]) if user else _default_activity_state()

    async def update_activity_state(self, guild_id: str | int, discord_user_id: str, statuses=None, watch_times=None, statuses_seeded=None, flush: bool = True) -> None:
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            user = self._guild_user(guild_id, discord_user_id)
            if not user:
                return
            if statuses:
                user["activity_state"]["statuses"].update(statuses)
            if watch_times:
                user["activity_state"]["watch_times"].update(watch_times)
            if statuses_seeded is not None:
                user["activity_state"]["statuses_seeded"] = statuses_seeded
            self._dirty = True
        if flush:
            await self.flush()

    async def get_statistics(self, guild_id: str | int, discord_user_id: str) -> dict:
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            user = self._guild_user(guild_id, discord_user_id)
            return copy.deepcopy(user["statistics"]) if user else _default_statistics()

    async def get_history_import_state(self, guild_id: str | int, discord_user_id: str) -> dict:
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            user=self._guild_user(guild_id,discord_user_id)
            return {"linked":bool(user),"complete":bool(user and user["history_seeded"]),
                    "last_error":user.get("last_error") if user else None}

    async def get_achievements(self, guild_id: str | int, discord_user_id: str) -> dict:
        """Return unlocked achievements keyed by achievement ID."""
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            user = self._guild_user(guild_id, discord_user_id)
            return copy.deepcopy(user.get("achievements", {})) if user else {}

    async def unlock_achievement(
        self,
        guild_id: str | int,
        discord_user_id: str,
        achievement_id: str,
        unlocked_at: str,
        flush: bool = True,
    ) -> bool:
        """Unlock an achievement once; return True only for a new unlock."""
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            user = self._guild_user(guild_id, discord_user_id)
            if not user:
                return False
            achievements = user.setdefault("achievements", {})
            if achievement_id in achievements:
                return False
            achievements[achievement_id] = {"unlocked_at": unlocked_at}
            self._dirty = True
        if flush:
            await self.flush()
        return True

    async def relock_achievement(
        self,
        guild_id: str | int,
        discord_user_id: str,
        achievement_id: str,
    ) -> dict:
        """Relock an achievement and revoke its XP when no guild still owns the unlock."""
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            guild_user = self._guild_user(guild_id, discord_user_id)
            if not guild_user:
                return {"relocked": False, "xp_removed": 0}

            achievements = guild_user.setdefault("achievements", {})
            if achievement_id not in achievements:
                return {"relocked": False, "xp_removed": 0}
            del achievements[achievement_id]

            # Achievement unlocks are guild-local, while progression is global.
            # Keep the global reward if this user still has the same achievement
            # unlocked in another guild.
            unlocked_elsewhere = any(
                str(gid) != str(guild_id)
                and isinstance(guild, dict)
                and achievement_id in (
                    ((guild.get("users") or {}).get(str(discord_user_id)) or {}).get("achievements", {})
                )
                for gid, guild in self._data.get("guilds", {}).items()
            )

            removed = 0
            global_user = self._user(discord_user_id)
            if global_user and not unlocked_elsewhere:
                progression = global_user["progression"]
                awarded = progression.setdefault("achievement_xp_awarded", {})
                if achievement_id in awarded:
                    matching = [
                        event for event in progression.get("xp_events", [])
                        if event.get("media_type") == "achievement"
                        and event.get("achievement_id") == achievement_id
                    ]
                    removed = sum(max(0, int(event.get("amount", 0))) for event in matching)
                    progression["xp_events"] = [
                        event for event in progression.get("xp_events", [])
                        if not (
                            event.get("media_type") == "achievement"
                            and event.get("achievement_id") == achievement_id
                        )
                    ]
                    awarded.pop(achievement_id, None)
                    progression["xp"] = max(0, int(progression.get("xp", 0)) - removed)
                    progression["lifetime_xp"] = max(
                        0, int(progression.get("lifetime_xp", 0)) - removed
                    )

            self._dirty = True

        await self.flush()
        return {"relocked": True, "xp_removed": removed}

    async def award_achievement_xp(
        self,
        discord_user_id: str,
        achievement_id: str,
        amount: int,
        title: str,
        awarded_at: str,
        *, flush: bool = True, include_progression: bool = True,
    ) -> dict:
        """Award achievement XP exactly once, including for old unlocks."""
        async with _lock:
            user = self._user(discord_user_id)
            if not user:
                return {"awarded": False, "amount": 0, "progression": {}}
            progression = user["progression"]
            awarded = progression.setdefault("achievement_xp_awarded", {})
            if achievement_id in awarded:
                return {"awarded": False, "amount": 0, "progression": copy.deepcopy(progression) if include_progression else {}}
            xp = max(0, int(amount))
            awarded[achievement_id] = awarded_at
            progression["xp"] = int(progression.get("xp", 0)) + xp
            progression["lifetime_xp"] = int(progression.get("lifetime_xp", 0)) + xp
            progression["xp_events"].append({
                "at": awarded_at,
                "amount": xp,
                "media_type": "achievement",
                "title": title or "Achievement",
                "event_key": f"achievement:{achievement_id}",
                "achievement_id": achievement_id,
            })
            roll_prestige(progression)
            self._dirty = True
            result = copy.deepcopy(progression) if include_progression else {}
        if flush:
            await self.flush()
        return {"awarded": True, "amount": xp, "progression": result}

    async def needs_watch_statistics_rebuild(self, guild_id: str | int, discord_user_id: str) -> bool:
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            user=self._guild_user(guild_id,discord_user_id)
            return bool(user and user["statistics"].get("watch_events") is None)

    async def reconcile_watch_statistics(
        self, guild_id: str | int, discord_user_id: str,
        active_watch_bases: set[str], media_types: set[str],
        baseline_entries: list[dict] | None = None,
        full_snapshot: bool = False,
    ) -> int:
        """Remove absent watches, or bootstrap old aggregate-only data from SIMKL."""
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            user=self._guild_user(guild_id,discord_user_id)
            if not user:
                return 0
            stats=user["statistics"]
            events=stats.get("watch_events")
            if events is None:
                if baseline_entries is None:
                    raise ValueError("Legacy statistics require a complete SIMKL history snapshot")
                old_titles=stats.get("titles") or {}
                events={}
                for entry in baseline_entries:
                    entry=copy.deepcopy(entry)
                    prior=old_titles.get(entry["item_key"]) or {}
                    entry["title"]=prior.get("title") or entry.get("title") or "Untitled"
                    entry["genres"]=_genre_names(prior.get("genres") or entry.get("genres"))
                    events[_watch_event_id(entry)]=entry
            else:
                events={key:event for key,event in events.items()
                        if event.get("media_type") not in media_types or _watch_base(event) in active_watch_bases}
                if full_snapshot and baseline_entries is not None:
                    present_bases={_watch_base(event) for event in events.values()}
                    for entry in baseline_entries:
                        base=_watch_base(entry)
                        if base not in present_bases:
                            entry=copy.deepcopy(entry)
                            entry["genres"]=_genre_names(entry.get("genres"))
                            events[_watch_event_id(entry)]=entry
                            present_bases.add(base)
            if events != stats.get("watch_events"):
                previous=int(stats.get("episodes_watched",0))+int(stats.get("movies_watched",0))
                guild=self._guild(guild_id)
                user["statistics"]=_rebuild_watch_statistics(events,guild.get("timezone") if guild else None)
                self._dirty=True
                difference=previous-(user["statistics"]["episodes_watched"]+user["statistics"]["movies_watched"])
            else:
                difference=0
            if full_snapshot:
                user["last_statistics_reconciled_at"]=datetime.now(timezone.utc).isoformat()
                self._dirty=True
        await self.flush()
        return difference

    async def get_statistics_reconcile_time(self, guild_id: str | int, discord_user_id: str) -> str | None:
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            user=self._guild_user(guild_id,discord_user_id)
            return user.get("last_statistics_reconciled_at") if user else None

    async def record_watch(
        self,
        guild_id: str | int,
        discord_user_id: str,
        media_type: str,
        title: str,
        item_key: str,
        watched_at: str,
        flush: bool = True,
        genres=None,
    ) -> None:
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            user = self._guild_user(guild_id, discord_user_id)
            if not user:
                return
            guild=self._guild(guild_id)
            if _record_watch_stats(user["statistics"],media_type,title,item_key,watched_at,genres,
                                   guild.get("timezone") if guild else None):
                self._dirty=True
        if flush:
            await self.flush()

    async def get_last_checked(self, guild_id: str | int, discord_user_id: str) -> dict:
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            user = self._guild_user(guild_id, discord_user_id)
            return copy.deepcopy(user["last_checked"]) if user else {}

    async def update_last_checked(self, guild_id: str | int, discord_user_id: str, category: str, iso_timestamp: str) -> None:
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            user = self._guild_user(guild_id, discord_user_id)
            if not user:
                return
            user["last_checked"][category] = iso_timestamp
            self._dirty = True

    async def update_poll_health(
        self,
        guild_id: str | int,
        discord_user_id: str,
        last_poll_at: str | None = None,
        last_success_at: str | None = None,
        last_error: str | None = None,
        consecutive_failures: int | None = None,
        flush: bool = True,
    ) -> None:
        """Update persistent polling health information for one guild/user."""
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            user = self._guild_user(guild_id, discord_user_id)
            if not user:
                return
            if last_poll_at is not None:
                user["last_poll_at"] = last_poll_at
            if last_success_at is not None:
                user["last_success_at"] = last_success_at
            if last_error is not None:
                user["last_error"] = last_error
            if consecutive_failures is not None:
                user["consecutive_failures"] = max(int(consecutive_failures), 0)
            self._dirty = True
        if flush:
            await self.flush()

    async def is_announced(self, guild_id: str | int, discord_user_id: str, key: str) -> bool:
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            user = self._guild_user(guild_id, discord_user_id)
            return bool(user) and key in user["announced"]

    async def get_announced(self, guild_id: str | int, discord_user_id: str) -> set[str]:
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            user = self._guild_user(guild_id, discord_user_id)
            return set(user["announced"]) if user else set()

    async def link_user(self, guild_id: str | int, discord_user_id: str, access_token: str, refresh_token: str | None, simkl_username: str, start_time_iso: str, token_expires_at: str | None = None, simkl_account_id: int | str | None = None) -> None:
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            existing = self._user(discord_user_id)
            if existing:
                personal = {
                    "embed_preferences": copy.deepcopy(existing.get("embed_preferences", DEFAULT_EMBED_PREFERENCES)),
                    "embed_preferences_custom": existing.get("embed_preferences_custom", False),
                    "progression": copy.deepcopy(existing.get("progression", {"xp": 0, "lifetime_xp": 0, "prestige": 0, "watch_xp_keys": {}, "xp_events": [], "challenge_completions": {}, "achievement_xp_awarded": {}})),
                }
            else:
                personal = {
                    "embed_preferences": copy.deepcopy(DEFAULT_EMBED_PREFERENCES),
                    "embed_preferences_custom": False,
                }
            self._data["users"][discord_user_id] = {
                "simkl_token": access_token,
                "refresh_token": refresh_token,
                "token_expires_at": token_expires_at,
                "simkl_username": simkl_username,
                "simkl_account_id": simkl_account_id,
                **personal,
            }
            self._data["guilds"][str(guild_id)]["users"][discord_user_id] = _default_guild_user(start_time_iso)
            self._dirty = True
        await self.flush()

    async def unlink_user(self, guild_id: str | int, discord_user_id: str) -> bool:
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            guild = self._guild(guild_id)
            if not guild or discord_user_id not in guild["users"]:
                return False
            del guild["users"][discord_user_id]

            # Authentication is global and can be reused in other servers.
            still_linked = any(
                isinstance(g, dict) and discord_user_id in (g.get("users") or {})
                for g in self._data["guilds"].values()
            )
            if not still_linked:
                self._data["users"].pop(discord_user_id, None)

            self._dirty = True
        await self.flush()
        return True

    async def update_tokens(self, discord_user_id: str, access_token: str, refresh_token: str | None, token_expires_at: str | None = None) -> None:
        async with _lock:
            user = self._user(discord_user_id)
            if not user:
                return
            user["simkl_token"] = access_token
            if refresh_token:
                user["refresh_token"] = refresh_token
            user["token_expires_at"] = token_expires_at
            self._dirty = True
        await self.flush()

    async def get_guild_statistics(self, guild_id: str | int) -> list[dict]:
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            guild = self._guild(guild_id)
            if not guild:
                return []
            results = []
            for uid, user in guild["users"].items():
                _normalise_guild_user(user)
                global_user = self._user(uid) or {}
                results.append({
                    "discord_user_id": uid,
                    "simkl_username": global_user.get("simkl_username", "unknown"),
                    "statistics": copy.deepcopy(user["statistics"]),
                    "history_seeded":bool(user.get("history_seeded")),
                })
            return results

    async def get_guild_leaderboard_snapshot(self, guild_id: str | int) -> list[dict]:
        """Read only the scalar fields needed for a ranked board in one lock."""
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            guild=self._guild(guild_id)
            if not guild:
                return []
            rows=[]
            for uid, user in guild["users"].items():
                _normalise_guild_user(user)
                global_user=self._user(uid) or {}
                p=global_user.get("progression") or {}
                s=user["statistics"]
                rows.append({
                    "discord_user_id":uid,
                    "simkl_username":global_user.get("simkl_username") or "Unknown",
                    "xp":int(p.get("xp",0)),
                    "prestige":int(p.get("prestige",0)),
                    "episodes":int(s.get("episodes_watched",0)),
                    "movies":int(s.get("movies_watched",0)),
                    "anime":int(s.get("anime_episodes_watched",0))+int(s.get("anime_movies_watched",0)),
                    "history_seeded":bool(user.get("history_seeded")),
                })
            return rows

    async def get_community_state(self, guild_id: str | int, week_key: str, start: datetime, end: datetime, now: datetime) -> dict:
        """Create a fixed weekly goal and reconcile ended-week rewards atomically."""
        gid=str(guild_id)
        changes=[]
        async with _lock:
            self._migrate_legacy_guild_locked(gid)
            guild=self._guild(gid)
            if not guild:
                return {}
            records=guild.setdefault("community_challenges", {})
            if week_key not in records:
                target=max(25,20*len(guild.get("users") or {}))
                records[week_key]={
                    "start":start.isoformat(),"end":end.isoformat(),
                    "target":target,"pool":target*300,
                    "members":sorted(guild.get("users") or {}),"awards":{},
                }
                self._dirty=True
            current=records[week_key]
            members=set(current.get("members") or []) | set(guild.get("users") or {})
            if now < end and sorted(members) != current.get("members"):
                current["members"]=sorted(members)
                self._dirty=True

            def counts_for(record):
                period_start=datetime.fromisoformat(record["start"])
                period_end=datetime.fromisoformat(record["end"])
                return episode_contributions(self._data["users"],record.get("members") or [],period_start,period_end)

            for key,record in records.items():
                period_end=datetime.fromisoformat(record["end"])
                if now < period_end:
                    continue
                contributions=counts_for(record)
                desired=(split_pool(contributions,int(record["pool"]))
                         if sum(contributions.values()) >= int(record["target"]) else {})
                previous=record.get("awards") or {}
                if desired==previous:
                    continue
                for uid in sorted(set(previous)|set(desired)):
                    user=self._user(uid)
                    if not user:
                        continue
                    before=int(user["progression"].get("xp",0))
                    delta=int(desired.get(uid,0))-int(previous.get(uid,0))
                    user["progression"]["xp"]=max(0,before+delta)
                    user["progression"]["lifetime_xp"]=max(0,int(user["progression"].get("lifetime_xp",0))+delta)
                    roll_prestige(user["progression"])
                    reward_key=f"{gid}:{key}"
                    if desired.get(uid):
                        user["progression"].setdefault("community_rewards",{})[reward_key]=int(desired[uid])
                    else:
                        user["progression"].setdefault("community_rewards",{}).pop(reward_key,None)
                    if delta:
                        changes.append({"uid":uid,"before":before,"after":user["progression"]["xp"],"delta":delta})
                record["awards"]=desired
                self._dirty=True
            contributions=counts_for(current)
            total=sum(contributions.values())
            state={
                "key":week_key,"start":current["start"],"end":current["end"],
                "target":int(current["target"]),"pool":int(current["pool"]),
                "contributions":contributions,"total":total,
                "awards":copy.deepcopy(current.get("awards") or {}),
                "status":("completed" if current.get("awards") else "missed") if now >= end else ("goal_reached" if total >= int(current["target"]) else "active"),
                "changes":changes,
            }
        await self.flush()
        return state

    async def set_account_id(self, discord_user_id: str, simkl_account_id: int | str) -> None:
        async with _lock:
            user = self._user(discord_user_id)
            if not user:
                return
            user["simkl_account_id"] = simkl_account_id
            self._dirty = True
        await self.flush()


storage = Storage()
