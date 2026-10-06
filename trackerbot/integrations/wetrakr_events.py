"""Convert WeTrakr's sync journal into stable, source-aware watch changes."""

from __future__ import annotations


def normalize_journal_entry(entry: dict) -> dict | None:
    """Ignore show/season roll-up rows: they are not individual plays."""
    category = entry.get("category")
    status = entry.get("status")
    media_type = entry.get("type")
    if category == "watched":
        play_id = entry.get("play_id")
        # Since 1.0.7, watched show rollups also include ongoing caught-up
        # shows and settings changes. They cannot establish series completion.
        if not play_id or media_type not in {"movie", "episode"}:
            return None
        if status not in {"added", "updated", "removed"}:
            return None
        show = entry.get("show") or {}
        return {
            "source": "wetrakr",
            "source_event_id": str(play_id),
            "change_id": str(entry.get("entry_id") or ""),
            "action": status,
            "media_type": media_type,
            "title": entry.get("title") or "",
            "wetrakr_id": entry.get("id"),
            "ids": entry.get("ids") or {},
            "show_id": entry.get("show_id") or entry.get("media_id") or show.get("id"),
            "show_ids": entry.get("show_ids") or show.get("ids") or {},
            "season": entry.get("season_number"),
            "episode": entry.get("number"),
            "watched_at": entry.get("watched_at"),
            "watched_at_unknown": bool(entry.get("watched_at_unknown")),
            "action_at": entry.get("action_at"),
        }
    if category in {"watching", "waiting", "planning", "dropped", "paused"} and status in {"added", "removed"}:
        if media_type not in {"movie", "show"}:
            return None
        return {"source": "wetrakr", "change_id": str(entry.get("entry_id") or ""),
                "action": status, "status": category, "media_type": media_type,
                "wetrakr_id": entry.get("id"), "ids": entry.get("ids") or {},
                "title": entry.get("title") or "", "action_at": entry.get("action_at")}
    return None


def normalize_compact_play(row: dict) -> dict | None:
    """Compact history separates title, parent-show and stable play identities."""
    if row.get("type") not in {"movie", "episode"} or not row.get("play_id"):
        return None
    return {"source": "wetrakr", "source_event_id": str(row["play_id"]),
            "media_type": row["type"], "wetrakr_id": row.get("id"),
            "ids": row.get("ids") or {}, "show_id": row.get("show_id"),
            "show_ids": row.get("show_ids") or {},
            "season": row.get("season_number"), "episode": row.get("number"),
            "watched_at": row.get("watched_at"),
            "watched_at_unknown": bool(row.get("watched_at_unknown"))}
