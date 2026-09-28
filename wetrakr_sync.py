"""Read-only WeTrakr journal ingestion with per-guild delivery acknowledgement."""

from datetime import datetime, timedelta, timezone

from wetrakr_client import WeTrakrError
from wetrakr_events import normalize_journal_entry


def overlap(iso: str) -> str:
    """Re-read the last second because journal entries can share action_at."""
    stamp = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (stamp - timedelta(seconds=1)).isoformat()


class WeTrakrSync:
    def __init__(self, client, auth, store):
        self.client, self.auth, self.store = client, auth, store

    async def poll(self, target: dict, deliver) -> int:
        gid, uid = target["guild_id"], target["discord_user_id"]
        link = target["user_data"]["wetrakr"]
        account_id = link["account_id"]
        state = target["guild_user_data"]["wetrakr_sync"]
        token = await self.auth.access_token(uid)
        activities = await self.client.last_activities(token)
        current = activities.get("all")
        if not current:
            raise ValueError("WeTrakr last activities response has no all timestamp")
        if not state["seeded"]:
            # Capture the mark before the slow history walk. Changes during
            # import are picked up by the journal on the next poll.
            for kind in ("movies", "episodes"):
                async for _ in self.client.compact_history(token, kind):
                    pass
            saved = await self.store.save_wetrakr_sync(
                gid, uid, account_id, seeded=True, checkpoint=current)
            if not saved:
                raise ValueError("WeTrakr link changed during baseline")
            return 0
        # The journal can lag last_activities by a few seconds. Re-reading
        # with overlap avoids losing an entry if it arrives after a quiet poll.
        try:
            rows = await self.client.journal(
                token, overlap(state["checkpoint"]),
                category="watched,watching,waiting,planning,dropped,paused")
        except WeTrakrError as exc:
            if exc.code != "JOURNAL_EXPIRED":
                raise
            # Retention expired: re-seed instead of posting a partial history.
            for kind in ("movies", "episodes"):
                async for _ in self.client.compact_history(token, kind):
                    pass
            await self.store.save_wetrakr_sync(
                gid, uid, account_id, seeded=True, checkpoint=current,
                last_activity=current)
            return 0
        seen = set(state["recent_entry_ids"])
        checkpoint = state["checkpoint"]
        posted = 0
        for row in rows:
            entry_id = str(row.get("entry_id") or "")
            action_at = row.get("action_at")
            if not entry_id or not action_at:
                raise ValueError("WeTrakr journal entry lacks entry_id or action_at")
            if entry_id in seen:
                continue
            change = normalize_journal_entry(row)
            if change and not await deliver(change, row):
                # Every preceding entry is acknowledged; retry this one later.
                return posted
            checkpoint = max(checkpoint, action_at)
            if not await self.store.save_wetrakr_sync(
                    gid, uid, account_id, checkpoint=checkpoint, entry_id=entry_id):
                raise ValueError("WeTrakr link changed during journal delivery")
            seen.add(entry_id)
            if change and change.get("action") == "added":
                posted += int(change.get("media_type") in {"movie", "episode"}
                              or change.get("status") in {"planning", "dropped", "paused", "completed"})
        if not await self.store.save_wetrakr_sync(
                gid, uid, account_id, checkpoint=checkpoint, last_activity=current):
            raise ValueError("WeTrakr link changed during journal sync")
        return posted
