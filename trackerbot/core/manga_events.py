"""Weekly manga goals and scoring over dated AniList activity."""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from .community_events import _matching
from .reading import COMMUNITY_MINIMUM, COMMUNITY_TARGET, in_period

ROTATION = {
    "readathon": "Community Readathon",
    "manga_genres": "Manga Genre Expedition",
    "manga_bingo": "Manga Bingo",
    "manga_decades": "Manga Decades Tour",
}
FIRST_WEEK = date(2026, 11, 23)
UNITS = {"manga_genres": "genres", "manga_decades": "decades", "manga_bingo": "squares"}
BINGO = (
    "Start at chapter 1", "Read a one-shot", "Read a comedy",
    "Read a pre-2000 manga", "Read a shared title", "Read a fantasy manga",
    "Finish a series", "Read 10 chapters of one title", "Read a mystery",
)
LINES = ((0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6))


def new_goal(week, start, end, records, enabled, kind=None):
    recent = {
        uid for uid in enabled
        if any(event["chapters"] for event in in_period(records.get(uid, {}).get("events", []), start-timedelta(days=14), start))
    }
    size = len(recent) or max(1, min(2, len(enabled)))
    kind = kind or tuple(ROTATION)[max(0, (date.fromisoformat(week)-FIRST_WEEK).days//7) % len(ROTATION)]
    target, minimum = {
        "readathon": (max(200, 150*size), 30),
        "manga_genres": (min(8, max(3, size+2)), 1),
        "manga_bingo": (3, 1),
        "manga_decades": (min(5, max(2, size+1)), 1),
    }[kind]
    return dict(kind=kind, start=start.isoformat(), end=end.isoformat(), target=target,
                minimum=minimum, required=min(size, 2))


def score(goal, records):
    start, end = (datetime.fromisoformat(goal[key]) for key in ("start", "end"))
    by_title = defaultdict(Counter)
    facts = defaultdict(dict)
    metadata = {}
    chapters = Counter()
    for uid, record in records.items():
        metadata.update(record.get("catalogue", {}))
        for event in in_period(record.get("events", []), start, end):
            title = event["media"].split(":", 1)[-1]
            if event["chapters"]:
                chapters[uid] += event["chapters"]
                by_title[title][uid] += event["chapters"]
                flags = facts[title].setdefault(uid, set())
                if event.get("first") == 1 and str(event.get("pass_key", "")).endswith(":0"):
                    flags.add("started")
            if event.get("status") == "COMPLETED":
                facts[title].setdefault(uid, set()).add("finished")

    kind = goal.get("kind", "legacy")
    target, minimum, required = (goal.get("target", COMMUNITY_TARGET), goal.get("minimum", COMMUNITY_MINIMUM), goal.get("required", 1))
    options = defaultdict(set)
    eligible_titles = set()
    support = defaultdict(set)
    objectives = []
    total = sum(chapters.values())
    pending = 0
    if kind in UNITS:
        for title, readers in by_title.items():
            meta = metadata.get(title, {})
            genres = set(meta.get("genres") or ())
            year = meta.get("year")
            eligible = {uid for uid, count in readers.items() if count >= 5}
            pending += bool(eligible and (not genres if kind == "manga_genres" else not year if kind == "manga_decades" else not meta))
            if kind == "manga_genres":
                matches = {genre: eligible for genre in genres}
            elif kind == "manga_decades":
                matches = {f"{year//10*10}s": eligible} if year else {}
            else:
                one_shot = meta.get("format") == "ONE_SHOT"
                matches = dict(zip(range(9), (
                    {uid for uid, flags in facts[title].items() if "started" in flags},
                    readers if one_shot else (),
                    eligible if "Comedy" in genres else (),
                    eligible if year and year < 2000 else (),
                    eligible if len(eligible) >= 2 else (),
                    eligible if "Fantasy" in genres else (),
                    {uid for uid, flags in facts[title].items() if "finished" in flags and uid in readers and meta and not one_shot},
                    {uid for uid, count in readers.items() if count >= 10},
                    eligible if "Mystery" in genres else (),
                )))
            for slot, users in matches.items():
                if users:
                    eligible_titles.add(title)
                    options[slot].add((title,))
                    support[slot].update(users)
        if kind == "manga_bingo":
            line_matches = [_matching({slot: options[slot] for slot in line}) for line in LINES]
            matched = max(line_matches, key=len)
            objectives = [
                {"label": label, "done": index in matched, "available": bool(options[index])}
                for index, label in enumerate(BINGO)
            ]
        else:
            matched = _matching(options)
            objectives = [{"label": str(slot), "done": True} for slot in sorted(matched)]
        for objective, slot in zip(objectives, range(len(BINGO)) if kind == "manga_bingo" else sorted(matched)):
            if slot in matched:
                title = metadata.get(matched[slot][0], {}).get("title")
                if title:
                    objective["titles"] = [title]
        total = len(matched)

    contributions = Counter(chapters)
    if kind in UNITS:
        contributions = Counter(uid for users in support.values() for uid in users)
    qualified = sorted(uid for uid, value in contributions.items() if value >= minimum)
    credited = {token[0] for token in matched.values()} if kind in UNITS else set()
    titles = sorted((title for title in by_title if title in metadata),
                    key=lambda title: (title not in credited, -sum(by_title[title].values()), title))
    return {
        "kind": kind, "target": target, "minimum": minimum, "required": required,
        "qualified": qualified, "contributions": dict(chapters), "eligible": dict(contributions),
        "total": total, "chapters": sum(chapters.values()), "objectives": objectives,
        "eligible_titles": len(eligible_titles), "pending": pending, "reached": total >= target and len(qualified) >= required, "end": goal["end"],
        "titles": [dict(metadata[title], count=sum(by_title[title].values())) for title in titles[:5] if title in metadata],
    }
