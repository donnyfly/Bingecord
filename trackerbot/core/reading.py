"""AniList manga snapshots and dated reading activity."""
from collections import Counter
from datetime import timedelta

from .community_events import parse_stamp

COMMUNITY_TARGET = 100
COMMUNITY_MINIMUM = 3
COMMUNITY_XP = 350

STATUS_LABELS = {
    "CURRENT": "Reading",
    "PLANNING": "Planned",
    "COMPLETED": "Completed",
    "PAUSED": "Paused",
    "DROPPED": "Dropped",
    "REPEATING": "Rereading",
}
READING_ACHIEVEMENTS = {
    "manga_chapters_100": ("Manga Reader", "Record 100 manga chapters.", "chapters", 100, 350),
    "manga_completed_10": ("Finished Volumes", "Complete 10 manga.", "completed", 10, 500),
    "manga_streak_7": ("Reading Streak", "Read manga on 7 consecutive days.", "streak", 7, 250),
}


def lifetime(snapshot):
    public = [state for state in snapshot.values() if not state[4]]
    counts = Counter(state[1] for state in public)
    return {
        "chapters": sum(max(0, state[2] or 0) for state in public),
        "completed": sum(state[1] == "COMPLETED" or bool(state[3]) for state in public),
        "statuses": {status: counts[status] for status in STATUS_LABELS},
    }


def observe(snapshot, previous, ceilings, account, now, *, seed=False):
    """Record new ranges once per reading pass, even after progress corrections."""
    for media_id, state in snapshot.items():
        entry_id, status, chapter, repeat, private = state
        if private:
            continue
        chapter, repeat = max(0, chapter or 0), repeat or 0
        old = previous.get(media_id)
        old_status = old[1] if old else None
        old_chapter = (old[2] or 0) if old else 0
        old_repeat = (old[3] or 0) if old else 0
        # The repeat counter counts completed rereads. Include the ongoing pass.
        reading_pass = repeat + int(status == "REPEATING")
        old_pass = old_repeat + int(old_status == "REPEATING")
        if status in {"PAUSED", "DROPPED"} and old_status == "REPEATING":
            reading_pass = max(reading_pass, old_pass)
        key = f"{account}:{media_id}:{reading_pass}"
        floor = ceilings.get(key, old_chapter if reading_pass == old_pass else 0)
        if status == "REPEATING" and old_status == "COMPLETED" and chapter >= old_chapter:
            ceilings.setdefault(key, 0)
            if not seed:
                chapter = 0
        same_entry = not old or (old[0] == entry_id and not old[4])
        if seed or not same_entry:
            ceilings[key] = max(floor, chapter)
            continue
        chapter_changed = chapter != old_chapter or (status == "COMPLETED" and repeat > old_repeat and old_status != "REPEATING")
        advanced = chapter_changed and chapter > floor and status in {"CURRENT", "COMPLETED", "REPEATING", "PAUSED", "DROPPED"}
        changed_status = status if status != old_status else None
        ceilings[key] = chapter if advanced else floor
        if advanced or changed_status:
            yield {
                "at": now.isoformat(),
                "media": f"{account}:{media_id}",
                "pass_key": key,
                "first": floor + 1 if advanced else 0,
                "last": chapter if advanced else 0,
                "chapters": chapter - floor if advanced else 0,
                "status": changed_status,
                "started": status == "CURRENT" and old_status in {None, "PLANNING"} and old_chapter == 0,
            }


def in_period(events, start, end):
    for event in events:
        stamp = parse_stamp(event["at"])
        if stamp and start <= stamp < end:
            yield event


def summary(events):
    counts = Counter()
    chapters = started = 0
    for event in events:
        chapters += event["chapters"]
        started += event["started"]
        if event["status"] and not event["started"]:
            counts[event["status"]] += 1
    return {"chapters": chapters, "started": started, "statuses": dict(counts)}


def longest_streak(events, zone):
    days = {parse_stamp(event["at"]).astimezone(zone).date() for event in events if event["chapters"]}
    best = run = 0
    previous = None
    for day in sorted(days):
        run = run + 1 if previous and day - previous == timedelta(days=1) else 1
        best = max(best, run)
        previous = day
    return best


def challenges(scope):
    if scope == "daily":
        return [dict(id="reading_daily", name="Daily Reading", kind="reading_chapters", target=3, xp=250)]
    return [
        dict(id="reading_weekly", name="Weekly Reading", kind="reading_chapters", target=15, xp=1000),
        dict(id="reading_variety", name="Reading Variety", kind="reading_variety", target=2, xp=750),
    ]


def challenge_value(events, kind):
    if kind == "reading_chapters":
        return sum(event["chapters"] for event in events)
    titles = Counter()
    for event in events:
        titles[event["media"].split(":", 1)[1]] += event["chapters"]
    return sum(chapters >= 3 for chapters in titles.values())
