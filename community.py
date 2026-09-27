"""Rotating weekly cooperative watch goals and contribution-based rewards."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def community_week(now: datetime, timezone_name: str):
    local=now.astimezone(ZoneInfo(timezone_name))
    monday=local.date()-timedelta(days=local.weekday())
    next_monday=monday+timedelta(days=7)
    tz=ZoneInfo(timezone_name)
    start=datetime.combine(monday,time.min,tzinfo=tz).astimezone(timezone.utc)
    end=datetime.combine(next_monday,time.min,tzinfo=tz).astimezone(timezone.utc)
    return monday.isoformat(),start,end


CHALLENGES={
    "episodes": {"name":"Episode Marathon","unit":"episode","media_types":{"episode","anime_episode"},"per_member":20,"minimum":25,"xp_per_watch":300},
    "movies": {"name":"Movie Night","unit":"movie","media_types":{"movie","anime_movie"},"per_member":3,"minimum":3,"xp_per_watch":900},
    "anime": {"name":"Anime Spotlight","unit":"anime watch","media_types":{"anime_episode","anime_movie"},"per_member":12,"minimum":15,"xp_per_watch":350},
    "all": {"name":"Watch Party","unit":"watch","media_types":{"episode","anime_episode","movie","anime_movie"},"per_member":22,"minimum":25,"xp_per_watch":300},
}


def challenge_for_week(week_key: str, member_count: int) -> dict:
    """Rotate in calendar order; keep the rollout week on its original episode goal."""
    week_number=(datetime.fromisoformat(week_key).date()-datetime(2026,9,21).date()).days//7
    kind=tuple(CHALLENGES)[week_number%len(CHALLENGES)]
    challenge=CHALLENGES[kind]
    target=max(challenge["minimum"],challenge["per_member"]*member_count)
    return {"kind":kind,"target":target,"pool":target*challenge["xp_per_watch"]}


def watch_contributions(users: dict, member_ids, start: datetime, end: datetime, kind: str = "episodes"):
    """Count distinct, still-active SIMKL watch events for linked members."""
    media_types=CHALLENGES.get(kind,CHALLENGES["episodes"])["media_types"]
    counts={}
    for uid in member_ids:
        events=((users.get(str(uid)) or {}).get("progression") or {}).get("xp_events") or []
        keys=set()
        for event in events:
            if event.get("media_type") not in media_types:
                continue
            stamp=event.get("at")
            try:
                watched=datetime.fromisoformat(str(stamp).replace("Z","+00:00"))
                if watched.tzinfo is None:
                    watched=watched.replace(tzinfo=timezone.utc)
            except (TypeError,ValueError):
                continue
            if start <= watched < end:
                keys.add(str(event.get("event_key") or f"{stamp}:{event.get('title')}"))
        if keys:
            counts[str(uid)]=len(keys)
    return counts


def episode_contributions(users: dict, member_ids, start: datetime, end: datetime):
    """Compatibility helper for previously stored episode challenges."""
    return watch_contributions(users,member_ids,start,end)


def split_pool(contributions: dict[str,int], pool: int):
    """Largest-remainder split: exact total, deterministic ties, no zero-contributor payouts."""
    counts={str(uid):max(0,int(count)) for uid,count in contributions.items() if int(count)>0}
    total=sum(counts.values())
    if not total:
        return {}
    pool=max(0,int(pool))
    awards={uid:(pool*count)//total for uid,count in counts.items()}
    left=pool-sum(awards.values())
    order=sorted(counts,key=lambda uid:(-((pool*counts[uid])%total),uid))
    for uid in order[:left]:
        awards[uid]+=1
    return awards
