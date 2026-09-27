"""Weekly cooperative episode goals, with a fixed pool split by contribution."""

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


def episode_contributions(users: dict, member_ids, start: datetime, end: datetime):
    """Count distinct, still-active SIMKL watch events for linked members."""
    counts={}
    for uid in member_ids:
        events=((users.get(str(uid)) or {}).get("progression") or {}).get("xp_events") or []
        keys=set()
        for event in events:
            if event.get("media_type") not in {"episode","anime_episode"}:
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
