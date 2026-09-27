"""Pure selection and ranking rules for personalized discovery."""

from collections import Counter


def rating_value(value):
    try:
        rating = float(value)
    except (TypeError, ValueError):
        return None
    return rating if 1 <= rating <= 10 else None


def select_sources(items, limit=8):
    """Balance explicit favourites, recency, and media types by unique title."""
    unique = {}
    for source in items:
        key = (source["kind"], source["tmdb_id"])
        current = unique.get(key)
        if current is None or (
            rating_value(source.get("rating")) or 0,
            source.get("watched_at") or "",
        ) > (
            rating_value(current.get("rating")) or 0,
            current.get("watched_at") or "",
        ):
            unique[key] = source

    eligible = [s for s in unique.values() if (rating_value(s.get("rating")) or 10) >= 6]
    favourites = [s for s in eligible if (rating_value(s.get("rating")) or 0) >= 8]
    # Favourite score first; within equal scores prefer the most recent watch.
    favourites.sort(key=lambda s: s.get("watched_at") or "", reverse=True)
    favourites.sort(key=lambda s: rating_value(s["rating"]) or 0, reverse=True)
    recent = sorted(eligible, key=lambda s: s.get("watched_at") or "", reverse=True)
    selected = []
    counts = Counter()

    def add(source):
        key = (source["kind"], source["tmdb_id"])
        if any((s["kind"], s["tmdb_id"]) == key for s in selected):
            return
        if counts[source["media_type"]] >= 3:
            return
        selected.append(source)
        counts[source["media_type"]] += 1

    for source in favourites[:4]:
        add(source)
    for source in recent:
        if len(selected) >= limit:
            break
        add(source)
    # Single-category histories can still supply a full set of sources.
    for source in favourites + recent:
        if len(selected) >= limit:
            break
        if source not in selected:
            selected.append(source)
    return selected


def source_weight(source, channel):
    rating = rating_value(source.get("rating"))
    preference = 1.7 if rating and rating >= 9 else 1.35 if rating and rating >= 8 else 0.8 if rating and rating <= 6 else 1.0
    return preference * (1.0 if channel == "recommendation" else 0.72)


def rank_candidates(candidates):
    """Reward personal matches and confidence without ranking obscure 10s first."""
    for item in candidates:
        votes = max(0, int(item.get("vote_count") or 0))
        average = max(0, min(10, float(item.get("vote_average") or 0)))
        # Bayesian prior limits the influence of a tiny rating sample.
        quality = (votes * average + 80 * 6.5) / (votes + 80)
        matches = item.get("_matches") or []
        item["_score"] = (
            sum(match["weight"] for match in matches)
            + min(1.2, len(item.get("_matched_genres") or []) * 0.4)
            + (quality - 6.5) * 0.35
        )
    return sorted(candidates, key=lambda item: (-item["_score"], -int(item.get("vote_count") or 0), str(item.get("name") or item.get("title") or "")))


def recommendation_lineup(ranked, limit=5):
    """Mix strong matches with one broader pick and one different genre."""
    if len(ranked) <= 3:
        return list(ranked)
    close = ranked[:min(3, limit)]
    remaining = [item for item in ranked if item not in close]
    if len(close) == limit:
        return close
    # A broader pick has a credible rating and fewer source matches.
    broad = next((item for item in remaining if len(item.get("_matches") or []) == 1
                  and int(item.get("vote_count") or 0) >= 25), remaining[0])
    close.append(broad)
    remaining.remove(broad)
    if remaining and len(close) < limit:
        popular_genres = set().union(*(set(item.get("genre_ids") or []) for item in close[:3]))
        wildcard = next((item for item in remaining
                         if len(item.get("_matches") or []) <= 2
                         and not popular_genres.intersection(item.get("genre_ids") or [])
                         and int(item.get("vote_count") or 0) >= 50), None)
        close.append(wildcard or remaining[0])
    return close
