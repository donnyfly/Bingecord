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

CHALLENGES['minutes']={'name':'Community Watchathon','unit':'minute',
                       'media_types':{'episode','anime_episode','movie','anime_movie'}}


def challenge_for_week(week_key: str, member_count: int) -> dict:
    """Rotate in calendar order; keep the rollout week on its original episode goal."""
    week_number=(datetime.fromisoformat(week_key).date()-datetime(2026,9,21).date()).days//7
    kind=("episodes","movies","anime","all")[week_number%4]
    challenge=CHALLENGES[kind]
    target=max(challenge["minimum"],challenge["per_member"]*member_count)
    return {"kind":kind,"target":target,"pool":target*challenge["xp_per_watch"]}


def watch_contributions(users: dict, member_ids, start: datetime, end: datetime,
                        kind: str = "episodes", selected_events: dict | None = None):
    """Count watches from each member's selected source when supplied."""
    media_types=CHALLENGES.get(kind,CHALLENGES["episodes"])["media_types"]
    counts={}
    for uid in member_ids:
        if selected_events is None or str(uid) not in selected_events:
            events=((users.get(str(uid)) or {}).get("progression") or {}).get("xp_events") or []
        else:
            events=(selected_events.get(str(uid)) or {}).values()
        keys=set()
        for event in events:
            if event.get("media_type") not in media_types:
                continue
            stamp=event.get("at") or event.get("watched_at")
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


def minute_contributions(users, member_ids, start, end, providers):
    """Count each credited occurrence once, supported by this server's selected source."""
    from trackerbot.core.progression import event_watch_minutes
    result={}
    for uid in member_ids:
        uid=str(uid)
        user=users.get(uid) or {}
        progression=user.get('progression') or {}
        provider=providers.get(uid)
        account=str((user.get(provider) or {}).get('account_id')) if provider in {'wetrakr','mdblist'} else None
        events={event.get('event_key'):event for event in progression.get('xp_events') or []}
        total=0.0
        for row in (progression.get('occurrence_ledger') or {}).values():
            observations=[obs for key,obs in row.get('observations',{}).items()
                          if obs.get('provider')==provider and
                          (account is None or key.startswith(f'{provider}:{account}/'))]
            if not observations:
                continue
            event=next((events[key] for key in row.get('award_keys',[])
                        if key in events and event_watch_minutes(events[key])>0),None)
            if not event:
                continue
            stamp=next(((obs.get('identity') or {}).get('watched_at') for obs in observations
                        if (obs.get('identity') or {}).get('watched_at')),event.get('at'))
            try:
                watched=datetime.fromisoformat(str(stamp).replace('Z','+00:00'))
                if watched.tzinfo is None:watched=watched.replace(tzinfo=timezone.utc)
            except (TypeError,ValueError):
                continue
            if start<=watched<end:
                total+=event_watch_minutes(event)
        if total>0:result[uid]=round(total,6)
    return result


def watchathon_goal(active_count, linked_count):
    # A new community with no recent duration data starts with at most two members.
    size=max(1,active_count or min(2,linked_count))
    target=180*size
    return {'kind':'minutes','target':target,'pool':target*2,'active_members':active_count,
            'required_contributors':min(3,max(1,(size+1)//2 if size!=2 else 2)),
            'qualifying_minutes':30,'milestones_notified':[]}


def watchathon_rewards(contributions, record):
    qualifying={uid:minutes for uid,minutes in contributions.items()
                if minutes>=record.get('qualifying_minutes',30)}
    if sum(contributions.values())<record['target'] or len(qualifying)<record['required_contributors']:
        return {}
    pool=int(record['pool'])
    equal=split_pool({uid:1 for uid in qualifying},pool//2)
    # Preserve fractional minutes in deterministic, integer micro-minute weights.
    weighted=split_pool({uid:round(minutes*1000000) for uid,minutes in qualifying.items()},pool-pool//2)
    return {uid:equal.get(uid,0)+weighted.get(uid,0) for uid in qualifying}
