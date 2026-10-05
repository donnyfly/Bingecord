"""
Persistent tracker accounts, observations and shared Discord progression.

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
from trackerbot.core.progression import challenges_for, roll_prestige, xp_for_level
from trackerbot.core.community import challenge_for_week, watch_contributions, split_pool
from trackerbot.core import watch_ledger
from trackerbot.core.providers import provider_linked
from trackerbot.core.tracker_mapping import identity_from_event, identity_from_play, normalized_ids

DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "store.json")

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
    source_counts=defaultdict(int)
    paired_counts=defaultdict(int)
    for key,event in events.items():
        if not str(key).startswith(("wetrakr:","mdblist:")):
            source_counts[(event.get("media_type"),str(event.get("title") or "").casefold())]+=1
    for key,event in events.items():
        if str(key).startswith("wetrakr:"):
            identity=(event.get("media_type"),str(event.get("title") or "").casefold())
            paired_counts[identity]+=1
            if paired_counts[identity]<=source_counts[identity]:
                continue
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


def _statistics_for_provider(guild_user: dict, provider: str, timezone_name: str | None) -> dict:
    """Build a read-only statistics view for the selected activity provider."""
    stats=guild_user.get("statistics") or _default_statistics()
    events=stats.get("watch_events")
    if not isinstance(events,dict):
        return copy.deepcopy(stats) if provider == "simkl" else _default_statistics()
    filtered={key:copy.deepcopy(event) for key,event in events.items()
              if (str(key).split(":",1)[0] if str(key).startswith(("wetrakr:","mdblist:")) else "simkl") == provider}
    return _rebuild_watch_statistics(filtered,timezone_name)


def _record_watch_stats(stats: dict, media_type: str, title: str, item_key: str,
                        watched_at: str, genres, timezone_name: str | None, ids=None) -> bool:
    event={"media_type":media_type,"title":title or "Untitled","item_key":item_key,"watched_at":watched_at}
    if ids:
        event["ids"]=copy.deepcopy(ids)
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
    rows = watch_ledger.ensure(progression)
    known=progression["watch_xp_keys"]
    added=[]
    amount=0
    for event in events:
        key=event["event_key"]
        provider = str(key).split(":",1)[0] if str(key).startswith(("wetrakr:","mdblist:")) else "simkl"
        identity = (identity_from_play({**event, "watched_at": event["at"]})
                    if provider != "simkl" else identity_from_event(event))
        row = watch_ledger.observe(rows, provider, key, identity)
        if row["award_keys"] or key in known:
            # Classification enrichment updates the shared reward projection;
            # a generic movie observation must not downgrade an anime film.
            if event['media_type'].startswith('anime'):
                for award in progression['xp_events']:
                    if award.get('event_key') in row['award_keys']:
                        award['media_type'] = event['media_type']
            continue
        row["award_keys"].append(key)
        stamp=event["at"]
        xp=max(0,int(event["amount"]))
        known[key]=stamp
        added.append({"at":stamp,"amount":xp,"media_type":event["media_type"],
                      "title":event.get("title") or "Untitled","event_key":key,
                      "ids":copy.deepcopy(event.get("ids") or {})})
        amount+=xp
    if added:
        progression["xp"]+=amount
        progression["lifetime_xp"]+=amount
        progression["xp_events"].extend(added)
        roll_prestige(progression)
    return amount,added


def _complete_watch_challenges(progression: dict, added: list[dict], *, notify: bool = False) -> None:
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
                if notify:
                    progression.setdefault("pending_challenge_notifications",[]).append({
                        "key":f"{key}:{challenge['id']}","name":challenge["name"],
                        "period":"Daily","xp":challenge["xp"],
                    })
                progression["xp"]+=challenge["xp"]
                progression["lifetime_xp"]+=challenge["xp"]
                roll_prestige(progression)
    for week in touched_weeks:
        for challenge in challenges_for(date.fromisoformat(week))[1]:
            key=f"weekly:{week}"
            if weekly[week][challenge["kind"]]>=challenge["target"] and challenge["id"] not in completions.get(key,{}):
                completions.setdefault(key,{})[challenge["id"]]={"completed_at":now,"xp":challenge["xp"]}
                if notify:
                    progression.setdefault("pending_challenge_notifications",[]).append({
                        "key":f"{key}:{challenge['id']}","name":challenge["name"],
                        "period":"Weekly","xp":challenge["xp"],
                    })
                progression["xp"]+=challenge["xp"]
                progression["lifetime_xp"]+=challenge["xp"]
                roll_prestige(progression)


def _revoke_unmet_watch_challenges(progression: dict) -> int:
    """Remove rewards whose watch requirements no longer hold after a deletion."""
    from datetime import date, timedelta
    daily = defaultdict(lambda: {"episodes": 0, "movies": 0, "watches": 0})
    weekly = defaultdict(lambda: {"episodes": 0, "movies": 0, "watches": 0})
    for event in progression["xp_events"]:
        kind = event.get("media_type")
        if kind not in {"episode", "anime_episode", "movie", "anime_movie"}:
            continue
        try:
            day = date.fromisoformat(str(event.get("at") or "")[:10])
        except ValueError:
            continue
        week = day - timedelta(days=day.weekday())
        category = "episodes" if "episode" in kind else "movies"
        for counts, key in ((daily, day.isoformat()), (weekly, week.isoformat())):
            counts[key][category] += 1
            counts[key]["watches"] += 1
    revoked = 0
    for period, completions in list(progression["challenge_completions"].items()):
        scope, _, day = period.partition(":")
        if scope not in {"daily", "weekly"}:
            continue
        try:
            definitions = challenges_for(date.fromisoformat(day))[0 if scope == "daily" else 1]
        except ValueError:
            continue
        requirements = {item["id"]: item for item in definitions}
        counts = daily[day] if scope == "daily" else weekly[day]
        for challenge_id, reward in list(completions.items()):
            definition = requirements.get(challenge_id)
            if definition and counts[definition["kind"]] < definition["target"]:
                revoked += max(0, int(reward.get("xp", 0)))
                del completions[challenge_id]
        if not completions:
            del progression["challenge_completions"][period]
    if revoked:
        progression["xp"] = max(0, progression["xp"] - revoked)
        progression["lifetime_xp"] = max(0, progression["lifetime_xp"] - revoked)
    return revoked


def _default_activity_state() -> dict:
    return {
        "statuses": {},
        "watch_times": {},
        "statuses_seeded": False,
    }


def _default_guild_user(start_time_iso: str | None = None) -> dict:
    start = start_time_iso or EPOCH_ISO
    return {
        "simkl_linked": True,
        "wetrakr_linked": False,
        "mdblist_linked": False,
        "mdblist_sync": {"seeded": False, "checkpoint": None, "last_activity": None, "recent_entry_ids": []},
        "activity_provider": "simkl",
        "wetrakr_sync": {"seeded": False, "checkpoint": None, "last_activity": None, "recent_entry_ids": []},
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
        "failure_notified": False,
    }


def _normalise_user(user: dict) -> None:
    user.setdefault("simkl_token", None)
    user.setdefault("wetrakr", None)
    user.setdefault("mdblist", None)
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
    progression.setdefault("wetrakr_plays", {})
    progression.setdefault("mdblist_plays", {})
    progression.setdefault("watch_occurrences", {})
    progression.setdefault("xp_events", [])
    progression.setdefault("challenge_completions", {})
    progression.setdefault("pending_challenge_notifications", [])
    progression.setdefault("community_rewards", {})
    progression.setdefault("achievement_xp_awarded", {})
    progression.setdefault("history_xp_seeded", False)
    progression.setdefault("history_xp_notification_sent", False)
    progression["history_xp_notification_sent"] = bool(progression.get("history_xp_notification_sent", False))
    if not isinstance(progression["watch_xp_keys"], dict): progression["watch_xp_keys"] = {}
    if not isinstance(progression["wetrakr_plays"], dict): progression["wetrakr_plays"] = {}
    if not isinstance(progression["watch_occurrences"], dict): progression["watch_occurrences"] = {}
    current_wetrakr_account=(user.get("wetrakr") or {}).get("account_id")
    for legacy_play in progression["wetrakr_plays"].values():
        if isinstance(legacy_play, dict) and current_wetrakr_account is not None:
            legacy_play.setdefault("account_id", str(current_wetrakr_account))
    if not isinstance(progression["xp_events"], list): progression["xp_events"] = []
    if not isinstance(progression["challenge_completions"], dict): progression["challenge_completions"] = {}
    if not isinstance(progression["pending_challenge_notifications"], list): progression["pending_challenge_notifications"] = []
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
    user.setdefault("simkl_linked", True)
    user.setdefault("wetrakr_linked", False)
    user.setdefault("mdblist_linked", False)
    user.setdefault("mdblist_sync", {"seeded": False, "checkpoint": None, "last_activity": None, "recent_entry_ids": []})
    user.setdefault("activity_provider", "simkl" if user.get("simkl_linked", True) else "wetrakr")
    if user["activity_provider"] not in {"simkl", "wetrakr", "mdblist"}:
        user["activity_provider"] = "simkl"
    if not isinstance(user.get("wetrakr_sync"), dict):
        user["wetrakr_sync"] = {"seeded": False, "checkpoint": None, "last_activity": None, "recent_entry_ids": []}
    sync = user["wetrakr_sync"]
    sync.setdefault("seeded", False)
    sync.setdefault("checkpoint", None)
    sync.setdefault("last_activity", None)
    if not isinstance(sync.get("recent_entry_ids"), list):
        sync["recent_entry_ids"] = []
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
    user["failure_notified"] = bool(user.get("failure_notified", False))

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
    async def save_wetrakr_sync(self, *args, **kwargs):
        return await self.save_provider_sync(*args, **kwargs)

    async def reconcile_wetrakr_plays(self, *args, **kwargs):
        return await self.reconcile_provider_plays(*args, **kwargs)

    async def get_wetrakr_plays(self, *args, **kwargs):
        return await self.get_provider_plays(*args, **kwargs)

    async def rotate_wetrakr_tokens(self, *args, **kwargs):
        return await self.rotate_provider_tokens(*args, **kwargs)

    async def unlink_wetrakr(self, *args, **kwargs):
        return await self.unlink_provider_account(*args, **kwargs)

    async def link_wetrakr(self, *args, **kwargs):
        return await self.link_provider_account(*args, **kwargs)

    async def link_provider_account(self, guild_id: str | int, discord_user_id: str,
                           tokens: dict, account: dict, provider: str = "wetrakr") -> None:
        """Add an independent WeTrakr link without resetting SIMKL or XP."""
        gid, uid = str(guild_id), str(discord_user_id)
        async with _lock:
            self._migrate_legacy_guild_locked(gid)
            guild = self._guild(gid, create=True)
            user = self._data["users"].get(uid)
            if not user:
                user = {}
                _normalise_user(user)
                self._data["users"][uid] = user
            prior = user.get(provider)
            if prior and str(prior.get("account_id")) != str(account.get("id")):
                if any((other.get("users") or {}).get(uid, {}).get(f"{provider}_linked")
                       for other in self._data["guilds"].values()):
                    raise ValueError("Unlink the existing WeTrakr account in all servers before switching accounts")
            account_user=account.get("user") if isinstance(account.get("user"),dict) else {}
            account_info=account.get("info") if isinstance(account.get("info"),dict) else {}
            user[provider] = {
                "access_token": tokens["access_token"],
                "refresh_token": tokens["refresh_token"],
                "expires_at": tokens.get("expires_at"),
                "account_id": account.get("id") or account_user.get("id"),
                "username": account.get("username") or account_user.get("username") or account_info.get("username") or "WeTrakr user",
                "profile_url": account.get("profile_url") or account_user.get("profile_url"),
            }
            if uid not in guild["users"]:
                guild["users"][uid] = _default_guild_user()
                guild["users"][uid]["simkl_linked"] = False
                guild["users"][uid]["activity_provider"] = provider
            guild["users"][uid][f"{provider}_linked"] = True
            if not prior or str(prior.get("account_id")) != str(account.get("id")):
                guild["users"][uid][f"{provider}_sync"] = {"seeded": False, "checkpoint": None, "last_activity": None, "recent_entry_ids": []}
            self._dirty = True
        await self.flush()

    async def rotate_provider_tokens(self, discord_user_id: str, account_id: str | int,
                                    old_refresh_token: str, tokens: dict, provider: str = "wetrakr") -> bool:
        """Persist a rotated refresh token only for the same account and session."""
        async with _lock:
            user = self._user(str(discord_user_id))
            link = user.get(provider) if user else None
            if not link or str(link.get("account_id")) != str(account_id) or link.get("refresh_token") != old_refresh_token:
                return False
            link["access_token"] = tokens["access_token"]
            link["refresh_token"] = tokens["refresh_token"]
            link["expires_at"] = tokens["expires_at"]
            self._dirty = True
        await self.flush()
        return True

    async def unlink_provider_account(self, guild_id: str | int, discord_user_id: str, provider: str = "wetrakr") -> bool:
        gid, uid = str(guild_id), str(discord_user_id)
        async with _lock:
            user = self._user(uid)
            guild = self._guild(gid)
            if not user or not user.get(provider) or not guild or uid not in guild["users"] or not guild["users"][uid].get(f"{provider}_linked"):
                return False
            guild["users"][uid][f"{provider}_linked"] = False
            if guild["users"][uid]["activity_provider"] == provider:
                guild["users"][uid]["activity_provider"] = next((p for p in ("simkl","wetrakr","mdblist") if p != provider and provider_linked(guild["users"][uid],user,p)), "simkl")
            # Keep the guild membership while the SIMKL link still uses it.
            if not any(provider_linked(guild["users"][uid],user,p) for p in ("simkl","wetrakr","mdblist")):
                guild["users"].pop(uid, None)
            if not any((other.get("users") or {}).get(uid, {}).get(f"{provider}_linked") for other in self._data["guilds"].values()):
                user[provider] = None
                # Keep shared XP and historical observations for a later relink.
            self._dirty = True
        await self.flush()
        return True

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
        return await self.get_provider_targets("simkl", guild_id, active_only=True)

    async def get_provider_targets(self, provider: str, guild_id: str | None = None,
                                   active_only: bool = False) -> list[dict]:
        if provider not in {"simkl", "wetrakr", "mdblist"}:
            raise ValueError(f"Unknown tracking provider: {provider}")
        async with _lock:
            guild_ids = [str(guild_id)] if guild_id is not None else list(self._data["guilds"].keys())
            targets = []
            for gid in guild_ids:
                guild = self._guild(gid)
                if not guild:
                    continue
                for uid in guild["users"].keys():
                    user = self._user(uid)
                    if not user or not provider_linked(guild["users"][uid], user, provider):
                        continue
                    if active_only and guild["users"][uid].get("activity_provider", "simkl") != provider:
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

    async def set_activity_provider(self, guild_id: str | int, discord_user_id: str,
                                    provider: str) -> bool:
        if provider not in {"simkl", "wetrakr", "mdblist"}:
            raise ValueError(f"Unknown tracking provider: {provider}")
        async with _lock:
            guild_user = self._guild_user(str(guild_id), str(discord_user_id))
            user = self._user(str(discord_user_id))
            if not guild_user or not user or not provider_linked(guild_user, user, provider):
                return False
            if guild_user.get("activity_provider") != provider:
                guild_user["activity_provider"] = provider
                if provider in {"wetrakr","mdblist"}:
                    guild_user[f"{provider}_sync"] = {"seeded": False, "checkpoint": None,
                                                  "last_activity": None, "recent_entry_ids": []}
                elif guild_user.get("history_seeded"):
                    stamp = datetime.now(timezone.utc).isoformat()
                    guild_user["last_checked"] = {kind: stamp for kind in ("shows", "anime", "movies")}
                self._dirty = True
        await self.flush()
        return True

    async def get_activity_provider(self, guild_id: str | int, discord_user_id: str) -> str | None:
        async with _lock:
            guild_user = self._guild_user(str(guild_id), str(discord_user_id))
            return guild_user.get("activity_provider", "simkl") if guild_user else None

    async def get_provider_plays(self, discord_user_id: str, provider: str = "wetrakr") -> list[dict]:
        async with _lock:
            user = self._user(str(discord_user_id))
            if not user:
                return []
            account=str((user.get(provider) or {}).get("account_id"))
            return [{**copy.deepcopy(play), "source_event_id": play.get("source_event_id", play_id)}
                    for play_id, play in user["progression"].get(f"{provider}_plays", {}).items()
                    if str(play.get("account_id"))==account]

    async def classify_wetrakr_movie(self, discord_user_id: str, movie_id, anime: bool) -> None:
        """Correct previously imported movies without awarding XP a second time."""
        uid = str(discord_user_id)
        async with _lock:
            user = self._user(uid)
            if not user:
                return
            progression = user["progression"]
            account=str((user.get("wetrakr") or {}).get("account_id"))
            changed = False
            for play_id, play in progression["wetrakr_plays"].items():
                if str(play.get("account_id")) != account:
                    continue
                if play.get("media_type") not in {"movie", "anime_movie"}:
                    continue
                if str(play.get("item_key") or "").split(":")[-1] != str(movie_id):
                    continue
                kind = "anime_movie" if anime else "movie"
                if play.get("media_type") != kind or not play.get("anime_classified"):
                    play["media_type"] = kind
                    play["anime_classified"] = True
                    watch_ledger.observe(watch_ledger.ensure(progression), 'wetrakr',
                                         f"wetrakr:{play_id}:{play['watched_at']}", identity_from_play(play))
                    for event in progression["xp_events"]:
                        if event.get("event_key") == play.get("xp_key"):
                            event["media_type"] = kind
                    for guild in self._data["guilds"].values():
                        guild_user = (guild.get("users") or {}).get(uid)
                        if not guild_user:
                            continue
                        events = (guild_user.get("statistics") or {}).get("watch_events")
                        if isinstance(events, dict) and "wetrakr:" + play_id in events:
                            events["wetrakr:" + play_id]["media_type"] = kind
                            guild_user["statistics"] = _rebuild_watch_statistics(
                                events, guild.get("timezone"))
                    changed = True
            if changed:
                self._dirty = True
        if changed:
            await self.flush()

    async def has_wetrakr_show_history(self, discord_user_id: str, show_id=None, ids=None,
                                       exclude_event_id=None) -> bool:
        """Return whether WeTrakr has a different episode play for this show."""
        async with _lock:
            user=self._user(str(discord_user_id))
            if not user:
                return False
            expected_show=str(show_id) if show_id is not None else None
            account=str((user.get("wetrakr") or {}).get("account_id"))
            expected_ids=normalized_ids(ids)
            for play in user["progression"].get("wetrakr_plays",{}).values():
                if str(play.get("account_id")) != account:
                    continue
                if (exclude_event_id is not None and
                        str(play.get("source_event_id")) == str(exclude_event_id)):
                    continue
                if play.get("media_type") not in {"episode","anime_episode"}:
                    continue
                play_ids=play.get("show_ids") or play.get("ids") or {}
                prior_ids=normalized_ids(play_ids)
                shared=set(expected_ids) & set(prior_ids)
                if any(expected_ids[key] != prior_ids[key] for key in shared):
                    # Provider-local numeric IDs can collide across catalogs;
                    # a conflicting external title ID wins over that number.
                    continue
                if expected_ids and any(expected_ids[key] == prior_ids[key] for key in shared):
                    return True
                if not shared and expected_show is not None and str(play.get("show_id")) == expected_show:
                    return True
            return False

    async def reconcile_provider_plays(self, guild_id: str | int, discord_user_id: str,
                                      plays: list[dict], *, complete: bool = False,
                                      account_id: str | int | None = None, notify: bool | None = None, provider: str = "wetrakr") -> dict:
        """Apply stable WeTrakr play IDs; a full import can revoke deleted plays.

        Keep source observations separately from XP so switching sources never
        discards the other provider's progression. Match verified external IDs
        when present, with a conservative fallback for older ID-less awards.
        """
        async with _lock:
            guild_user = self._guild_user(guild_id, discord_user_id)
            global_user = self._user(discord_user_id)
            if not guild_user or not global_user:
                return {"added": 0, "removed": 0, "xp": 0}
            if provider == "mdblist" and (not provider_linked(guild_user,global_user,provider) or guild_user.get("activity_provider") != provider):
                raise ValueError("MDBList source changed during reconciliation")
            current_account=(global_user.get(provider) or {}).get("account_id")
            if account_id is not None and str(current_account) != str(account_id):
                raise ValueError("WeTrakr account changed during watch reconciliation")
            account=str(current_account)
            progression = global_user["progression"]
            watch_ledger.ensure(progression)
            ledger = progression.setdefault(f"{provider}_plays", {})
            def scoped_id(source_id):
                source_id=str(source_id)
                existing=ledger.get(source_id)
                if existing is None or str(existing.get("account_id"))==account:
                    return f"{account}/{source_id}" if provider != "wetrakr" else source_id
                return f"{account}/{source_id}"
            stats = guild_user["statistics"]
            if not isinstance(stats.get("watch_events"), dict):
                stats["watch_events"] = {}
            events = stats["watch_events"]
            observed = set()
            added = removed = xp_delta = 0
            revised = False
            challenge_events = []
            for play in plays:
                source_id = str(play.get("source_event_id") or "")
                play_id = scoped_id(source_id) if source_id else ""
                previous = ledger.get(play_id)
                stamp = play.get("watched_at") or (previous or {}).get("watched_at")
                if not play_id or not stamp:
                    continue
                observed.add(play_id)
                key = f"{provider}:" + play_id
                if previous and previous.get("watched_at") == stamp and not play.get("removed"):
                    # Re-imports can discover a better title ID or corrected
                    # anime coordinates. Update the observation without
                    # replaying XP or posting historical activity.
                    changed_metadata = False
                    for field in ("item_key", "ids", "title", "media_type", "genres"):
                        value = play.get(field)
                        if field == "genres" and value:
                            value = _genre_names(value)
                        if value and value != previous.get(field):
                            previous[field] = copy.deepcopy(value)
                            changed_metadata = True
                    if changed_metadata:
                        watch_ledger.observe(progression['occurrence_ledger'], provider,
                                             f"{provider}:{play_id}:{stamp}", identity_from_play(previous))
                        for award in progression["xp_events"]:
                            if award.get("event_key") == previous.get("xp_key"):
                                award["media_type"] = previous["media_type"]
                                award["title"] = previous["title"]
                                award["ids"] = copy.deepcopy(previous.get("ids") or {})
                        events[key] = {"media_type": previous["media_type"],
                                       "title": previous["title"], "item_key": previous["item_key"],
                                       "watched_at": stamp, "ids": previous.get("ids") or {},
                                       "genres": previous.get("genres") or []}
                        revised = True
                    if key not in events:
                        events[key] = {"media_type": previous["media_type"],
                                       "title": previous["title"], "item_key": previous["item_key"],
                                       "watched_at": stamp, "ids": previous.get("ids") or {},
                                       "genres": previous.get("genres") or []}
                        added += 1
                    continue
                if previous:
                    revised = True
                    previous_key = f"{provider}:{play_id}:{previous['watched_at']}"
                    for award_key in watch_ledger.remove(progression, previous_key):
                        xp_delta -= self._remove_watch_award_locked(progression, award_key)
                    events.pop(key, None)
                elif not play.get("removed"):
                    added += 1
                if play.get("removed"):
                    ledger.pop(play_id, None)
                    removed += 1
                    continue
                kind = play.get("media_type")
                if kind not in {"movie", "episode", "anime_movie", "anime_episode"}:
                    continue
                title = play.get("title") or "Untitled"
                item_key = play.get("item_key") or key
                events[key] = {"media_type": kind, "title": title,
                               "item_key": item_key, "watched_at": stamp,
                               "ids": copy.deepcopy(play.get("ids") or {}),
                               "genres": _genre_names(play.get("genres"))}
                xp_key = key + ":" + stamp
                amount, added_events = _add_watch_xp_events(progression, [{"event_key": xp_key,
                    "media_type": kind, "title": title, "at": stamp, "item_key": item_key,
                    "ids": play.get("ids") or {},
                    "amount": 300 if "movie" in kind else 100}])
                duplicate = not added_events
                xp_delta += amount
                challenge_events.extend(added_events)
                ledger[play_id] = {"watched_at": stamp, "xp_key": None if duplicate else xp_key,
                                   "account_id":account,"source_event_id":source_id,
                                   "media_type": kind, "title": title, "item_key": item_key,
                                   "show_id": play.get("show_id"),
                                   "show_ids": play.get("show_ids") or {},
                                   "season": play.get("season"), "episode": play.get("episode"),
                                   "source_season": play.get("source_season"), "source_episode": play.get("source_episode"),
                                   "ids": play.get("ids") or {}, "genres": _genre_names(play.get("genres"))}
            if complete:
                for play_id in {key for key, value in ledger.items()
                                if str(value.get("account_id"))==account} - observed:
                    old = ledger.pop(play_id)
                    for award_key in watch_ledger.remove(progression, f"{provider}:{play_id}:{old['watched_at']}"):
                        xp_delta -= self._remove_watch_award_locked(progression, award_key)
                    events.pop(f"{provider}:" + play_id, None)
                    removed += 1
            if challenge_events:
                before = progression["lifetime_xp"]
                _complete_watch_challenges(progression, challenge_events, notify=(not complete if notify is None else notify))
                xp_delta += progression["lifetime_xp"] - before
            if removed or revised:
                xp_delta -= _revoke_unmet_watch_challenges(progression)
            if added or removed or xp_delta or revised:
                guild_user["statistics"] = _rebuild_watch_statistics(events, self._guild(guild_id).get("timezone"))
                self._dirty = True
        if added or removed or xp_delta or revised:
            await self.flush()
        return {"added": added, "removed": removed, "xp": xp_delta}

    @staticmethod
    def _remove_watch_award_locked(progression: dict, key: str) -> int:
        removed = sum(max(0, int(event.get("amount", 0))) for event in progression["xp_events"]
                      if event.get("event_key") == key)
        progression["xp_events"] = [event for event in progression["xp_events"]
                                    if event.get("event_key") != key]
        progression["watch_xp_keys"].pop(key, None)
        progression["xp"] = max(0, progression["xp"] - removed)
        progression["lifetime_xp"] = max(0, progression["lifetime_xp"] - removed)
        return removed

    async def save_provider_sync(self, guild_id: str | int, discord_user_id: str,
                                account_id: str | int, *, seeded=None, checkpoint=None,
                                last_activity=None, entry_id=None, entry_ids=None, status_snapshot=None, health=None, provider: str = "wetrakr") -> bool:
        async with _lock:
            guild_user = self._guild_user(str(guild_id), str(discord_user_id))
            user = self._user(str(discord_user_id))
            if not guild_user or not user or not provider_linked(guild_user, user, provider):
                return False
            if guild_user.get("activity_provider") != provider or str(user[provider]["account_id"]) != str(account_id):
                return False
            sync = guild_user[f"{provider}_sync"]
            if health is not None:
                sync["health"] = copy.deepcopy(health)
            if status_snapshot is not None:
                sync["status_snapshot"] = copy.deepcopy(status_snapshot)
            if seeded is not None:
                sync["seeded"] = bool(seeded)
            if checkpoint is not None:
                sync["checkpoint"] = checkpoint
            if last_activity is not None:
                sync["last_activity"] = last_activity
            if entry_id or entry_ids:
                ids = sync["recent_entry_ids"]
                for candidate in ([entry_id] if entry_id else []) + list(entry_ids or []):
                    if candidate not in ids:
                        ids.append(candidate)
                del ids[:-2000]
            self._dirty = True
        await self.flush()
        return True

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
        """Reset server-local state, preserving linked accounts and global progression."""
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            guild = self._guild(guild_id)
            if not guild or str(discord_user_id) not in guild["users"]:
                return False
            prior = guild["users"][str(discord_user_id)]
            replacement = _default_guild_user(start_time_iso)
            for key in ("simkl_linked", "wetrakr_linked", "mdblist_linked", "activity_provider"):
                replacement[key] = copy.deepcopy(prior.get(key, replacement[key]))
            guild["users"][str(discord_user_id)] = replacement
            self._dirty = True
        await self.flush()
        return True

    async def get_progression(self, discord_user_id: str) -> dict:
        async with _lock:
            user = self._user(discord_user_id)
            return copy.deepcopy(user.get("progression", {})) if user else {}

    async def get_mapping_audit(self, discord_user_id: str) -> dict:
        """Preview the cross-provider occurrence index without changing XP."""
        async with _lock:
            user = self._user(discord_user_id)
            if not user:
                return {"occurrences": 0, "verified": 0, "legacy": 0,
                        "unpaired": 0, "review": 0, "double_awards": 0}
            progression = user["progression"]
            # A read-only preview can migrate a copy without altering saved data.
            index = watch_ledger.audit(watch_ledger.ensure(copy.deepcopy(progression)))
        return {
            "occurrences": len(index),
            "verified": sum(row["match_reason"] == "verified_id" for row in index.values()),
            "legacy": sum(row["match_reason"] == "legacy_title" for row in index.values()),
            "unpaired": sum(len(row["observations"]) == 1 for row in index.values()),
            "review": sum(row["needs_review"] for row in index.values()),
            "double_awards": sum(len(row["award_keys"]) > 1 for row in index.values()),
        }

    async def refresh_watch_occurrences(self, discord_user_id: str) -> dict:
        """Persist the authoritative ledger and its read-only command projection."""
        async with _lock:
            user = self._user(discord_user_id)
            if not user:
                return {}
            progression = user["progression"]
            index = watch_ledger.audit(watch_ledger.ensure(progression))
            if progression.get("watch_occurrences") != index:
                progression["watch_occurrences"] = index
                self._dirty = True
        await self.flush()
        return index

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
            rows = watch_ledger.ensure(progression)
            existing = {key[:-(len(o['identity']['watched_at']))]
                        for row in rows.values() for key, o in row['observations'].items()
                        if o['provider'] == 'simkl' and o['identity']
                        and key.endswith(o['identity']['watched_at'])}
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
                                "ids":entry.get("ids") or {},
                                "amount":300 if entry["media_type"] in {"movie","anime_movie"} else 100})
            amount,_=_add_watch_xp_events(progression,missing)
            if missing:
                self._dirty=True
        if missing:
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

    async def award_watch_xp(self, discord_user_id: str, event_key: str, media_type: str, title: str, watched_at: str, amount: int, ids: dict | None = None) -> dict:
        """Award watch XP once globally per SIMKL watch event."""
        async with _lock:
            user = self._user(discord_user_id)
            if not user:
                return {"awarded": False, "amount": 0, "progression": {}}
            progression = user["progression"]
            xp = max(0, int(amount))
            amount,_=_add_watch_xp_events(progression,[{"event_key":event_key,"media_type":media_type,
                                                "title":title,"at":watched_at,"amount":xp,
                                                "ids":ids or {}}])
            self._dirty = True
            result = copy.deepcopy(progression)
        await self.flush()
        return {"awarded": bool(amount), "amount": amount, "progression": result}

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
                "media_type":record[0],"title":record[1],"item_key":record[2],
                "watched_at":record[3],"genres":record[4],
                "ids":record[5] if len(record)>5 else {},
                "amount":300 if record[0] in {"movie","anime_movie"} else 100,
            } for record in records]
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
                                     records: list[dict], timezone_name: str | None,
                                     notify_challenges: bool = False) -> int:
        xp_events=[]
        for record in records:
            media_type=record["media_type"]
            title=record["title"]
            item_key=record["item_key"]
            watched_at=record["watched_at"]
            _record_watch_stats(guild_user["statistics"],media_type,title,item_key,watched_at,
                                record.get("genres"),timezone_name,record.get("ids"))
            xp_events.append({"event_key":f"{media_type}:{item_key}:{watched_at}",
                              "media_type":media_type,"title":title,"at":watched_at,
                              "ids":record.get("ids") or {},
                              "amount":record["amount"]})
        if records and any(str(key).startswith("wetrakr:")
                           for key in (guild_user["statistics"].get("watch_events") or {})):
            guild_user["statistics"]=_rebuild_watch_statistics(
                guild_user["statistics"]["watch_events"],timezone_name)
        progression=global_user["progression"]
        amount,added=_add_watch_xp_events(progression,xp_events)
        _complete_watch_challenges(progression,added,notify=notify_challenges)
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
                                                     guild.get("timezone"),
                                                     notify_challenges=bool(guild["features"]["progression"] and guild["features"]["challenges"]))
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
            rows = watch_ledger.ensure(progression)
            removed_keys = set()
            removed_amount = 0
            changed = False

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

            observations = [(key, observation) for row in rows.values()
                            for key, observation in row["observations"].items()
                            if observation["provider"] == "simkl"]
            for key, observation in observations:
                identity = observation.get("identity") or {}
                if identity.get("media_type", key.split(':', 1)[0]) not in media_types:
                    continue
                base = event_base(key)
                if base is None or base in active_watch_bases:
                    continue
                changed = True
                for award_key in watch_ledger.remove(progression, key):
                    removed_keys.add(award_key)
                    removed_amount += self._remove_watch_award_locked(progression, award_key)
            if not changed:
                return {"removed": False, "amount": 0, "events": 0, "progression": copy.deepcopy(progression)}
            removed_amount += _revoke_unmet_watch_challenges(progression)
            self._dirty = True
            result = copy.deepcopy(progression)

        await self.flush()
        return {
            "removed": bool(removed_keys),
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

    async def get_pending_challenge_notifications(self, discord_user_id: str) -> list[dict]:
        async with _lock:
            user=self._user(discord_user_id)
            return copy.deepcopy(user["progression"].get("pending_challenge_notifications",[])) if user else []

    async def ack_challenge_notifications(self, discord_user_id: str, keys: set[str]) -> None:
        async with _lock:
            user=self._user(discord_user_id)
            if not user:
                return
            progression=user["progression"]
            progression["pending_challenge_notifications"]=[
                item for item in progression.get("pending_challenge_notifications",[])
                if item.get("key") not in keys
            ]
            self._dirty=True
        await self.flush()

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
            guild=self._guild(guild_id)
            user = self._guild_user(guild_id, discord_user_id)
            if not user:
                return _default_statistics()
            return _statistics_for_provider(
                user,user.get("activity_provider","simkl"),
                guild.get("timezone") if guild else None,
            )

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
                        if str(key).startswith(("wetrakr:","mdblist:")) or event.get("media_type") not in media_types or _watch_base(event) in active_watch_bases}
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
        failure_notified: bool | None = None,
        flush: bool = True,
    ) -> None:
        """Update persistent polling health information for one guild/user.

        Resetting ``consecutive_failures`` to 0 also clears ``failure_notified``
        so the user is warned again if their link breaks in the future.
        """
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
                if user["consecutive_failures"] == 0:
                    user["failure_notified"] = False
            if failure_notified is not None:
                user["failure_notified"] = bool(failure_notified)
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
                    "wetrakr": copy.deepcopy(existing.get("wetrakr")),
                    "mdblist": copy.deepcopy(existing.get("mdblist")),
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
            prior_guild_user=self._data["guilds"][str(guild_id)]["users"].get(discord_user_id)
            if prior_guild_user and (prior_guild_user.get("wetrakr_linked") or prior_guild_user.get("mdblist_linked")):
                prior_guild_user["simkl_linked"] = True
            else:
                self._data["guilds"][str(guild_id)]["users"][discord_user_id] = _default_guild_user(start_time_iso)
            self._dirty = True
        await self.flush()

    async def unlink_user(self, guild_id: str | int, discord_user_id: str) -> bool:
        async with _lock:
            self._migrate_legacy_guild_locked(str(guild_id))
            guild = self._guild(guild_id)
            if not guild or discord_user_id not in guild["users"]:
                return False
            guild_user = guild["users"][discord_user_id]
            if not guild_user.get("simkl_linked", True):
                return False
            guild_user["simkl_linked"] = False
            if guild_user.get("activity_provider") == "simkl" and guild_user.get("mdblist_linked") and not guild_user.get("wetrakr_linked"):
                guild_user["activity_provider"] = "mdblist"
                guild_user["mdblist_sync"]["seeded"] = False
            if guild_user.get("activity_provider") == "simkl" and guild_user.get("wetrakr_linked"):
                guild_user["activity_provider"] = "wetrakr"
                guild_user["wetrakr_sync"] = {"seeded": False, "checkpoint": None, "last_activity": None, "recent_entry_ids": []}
            if not guild_user.get("wetrakr_linked") and not guild_user.get("mdblist_linked"):
                del guild["users"][discord_user_id]

            # Authentication is global and can be reused in other servers.
            still_linked = any(
                isinstance(g, dict) and (g.get("users") or {}).get(discord_user_id, {}).get("simkl_linked", True)
                and discord_user_id in (g.get("users") or {})
                for g in self._data["guilds"].values()
            )
            if not still_linked:
                if any((g.get("users") or {}).get(discord_user_id, {}).get("wetrakr_linked") for g in self._data["guilds"].values()):
                    user = self._user(discord_user_id)
                    user["simkl_token"] = None
                    user["refresh_token"] = None
                    user["token_expires_at"] = None
                else:
                    user = self._user(discord_user_id)
                    user["simkl_token"] = None
                    user["refresh_token"] = None
                    user["token_expires_at"] = None

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
                provider=user.get("activity_provider","simkl")
                stats=_statistics_for_provider(user,provider,guild.get("timezone"))
                results.append({
                    "discord_user_id": uid,
                    "simkl_username": ((global_user.get(provider) or {}).get("username")
                                       if provider!="simkl" else
                                       global_user.get("simkl_username")) or uid,
                    "statistics": stats,
                    "history_seeded":(bool((user.get(f"{provider}_sync") or {}).get("seeded"))
                                      if provider!="simkl" else bool(user.get("history_seeded"))),
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
                provider=user.get("activity_provider","simkl")
                stats=_statistics_for_provider(user,provider,guild.get("timezone"))
                rows.append({
                    "discord_user_id":uid,
                    "simkl_username":((global_user.get(provider) or {}).get("username")
                                      if provider!="simkl" else
                                      global_user.get("simkl_username")) or uid,
                    "xp":int(p.get("xp",0)),
                    "prestige":int(p.get("prestige",0)),
                    "episodes":int(stats.get("episodes_watched",0)),
                    "movies":int(stats.get("movies_watched",0)),
                    "anime":int(stats.get("anime_episodes_watched",0))+int(stats.get("anime_movies_watched",0)),
                    "history_seeded":(bool((user.get(f"{provider}_sync") or {}).get("seeded"))
                                      if provider!="simkl" else bool(user.get("history_seeded"))),
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
                challenge=challenge_for_week(week_key,len(guild.get("users") or {}))
                records[week_key]={
                    "start":start.isoformat(),"end":end.isoformat(),
                    **challenge,
                    "members":sorted(guild.get("users") or {}),"awards":{},
                    "notified_awards":{},
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
                providers=record.get("providers") or {}
                selected={uid:_statistics_for_provider(member,providers.get(uid,member.get("activity_provider","simkl")),
                                                       guild.get("timezone")).get("watch_events",{})
                          for uid,member in guild.get("users",{}).items()
                          if providers.get(uid,member.get("activity_provider")) in {"wetrakr","mdblist"} or
                          (member.get("statistics") or {}).get("watch_events")}
                return watch_contributions(self._data["users"],record.get("members") or [],
                                           period_start,period_end,record.get("kind","episodes"),
                                           selected_events=selected)

            for key,record in records.items():
                period_end=datetime.fromisoformat(record["end"])
                if now < period_end:
                    continue
                if "providers" not in record:
                    record["providers"]={uid:member.get("activity_provider","simkl")
                                         for uid,member in guild.get("users",{}).items()}
                    self._dirty=True
                if not isinstance(record.get("notified_awards"),dict):
                    # Existing paid weeks predate these notifications.
                    record["notified_awards"]=copy.deepcopy(record.get("awards") or {})
                    self._dirty=True
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
            pending=[]
            for key,record in sorted(records.items()):
                awards=record.get("awards") or {}
                notified=record.get("notified_awards") or {}
                if not isinstance(record.get("notified_awards"),dict) or awards==notified:
                    continue
                changes_to_report={uid:int(awards.get(uid,0))-int(notified.get(uid,0))
                                   for uid in set(awards)|set(notified)}
                initial=not bool(notified) and bool(awards)
                members=sorted((counts_for(record) if initial else changes_to_report),
                               key=lambda uid:(-int(awards.get(uid,0)),uid))
                pending.append({"key":key,"target":int(record["target"]),
                                "kind":record.get("kind","episodes"),
                                "pool":int(record["pool"]),"awards":copy.deepcopy(awards),
                                "contributions":counts_for(record),"deltas":changes_to_report,
                                "members":members,"initial":initial})
            contributions=counts_for(current)
            total=sum(contributions.values())
            state={
                "key":week_key,"start":current["start"],"end":current["end"],
                "target":int(current["target"]),"pool":int(current["pool"]),
                "kind":current.get("kind","episodes"),
                "contributions":contributions,"total":total,
                "awards":copy.deepcopy(current.get("awards") or {}),
                "status":("completed" if current.get("awards") else "missed") if now >= end else ("goal_reached" if total >= int(current["target"]) else "active"),
                "changes":changes,
                "pending_notifications":pending,
            }
        await self.flush()
        return state

    async def ack_community_notification(self, guild_id: str | int, week_key: str, awards: dict) -> None:
        """Acknowledge only the payout snapshot that was actually delivered."""
        async with _lock:
            guild=self._guild(str(guild_id))
            record=(guild or {}).get("community_challenges",{}).get(week_key)
            if not record or record.get("awards") != awards:
                return
            record["notified_awards"]=copy.deepcopy(awards)
            self._dirty=True
        await self.flush()

    async def set_account_id(self, discord_user_id: str, simkl_account_id: int | str) -> None:
        async with _lock:
            user = self._user(discord_user_id)
            if not user:
                return
            user["simkl_account_id"] = simkl_account_id
            self._dirty = True
        await self.flush()


storage = Storage()
