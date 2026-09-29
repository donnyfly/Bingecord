"""WeTrakr compact history and journal ingestion with durable acknowledgements."""

from datetime import datetime, timedelta, timezone
import logging

from wetrakr_client import WeTrakrError
from wetrakr_events import normalize_compact_play, normalize_journal_entry

log = logging.getLogger("simkl-bot")


def overlap(iso: str) -> str:
    """Re-read the last second because journal entries can share action_at."""
    stamp = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (stamp - timedelta(seconds=1)).isoformat()


class WeTrakrSync:
    def __init__(self, client, auth, store):
        self.client, self.auth, self.store = client, auth, store

    async def poll(self, target: dict, deliver, deliver_group=None, resolve_play=None) -> int:
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
            plays = []
            for kind in ("movies", "episodes"):
                async for page in self.client.compact_history(token, kind):
                    rows = page if isinstance(page, list) else page.get("items", page.get("history", []))
                    for row in rows:
                        play = normalize_compact_play(row)
                        if play:
                            plays.append(await resolve_play(play) if resolve_play else play)
            result = await self.store.reconcile_wetrakr_plays(gid, uid, plays, complete=True,
                                                             account_id=account_id)
            saved = await self.store.save_wetrakr_sync(
                gid, uid, account_id, seeded=True, checkpoint=current)
            if not saved:
                raise ValueError("WeTrakr link changed during baseline")
            log.info("WeTrakr baseline for user %s in guild %s: %d plays, %d added, %d removed, %+d XP; no historical embeds.",
                     uid, gid, len(plays), result["added"], result["removed"], result["xp"])
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
            plays = []
            for kind in ("movies", "episodes"):
                async for page in self.client.compact_history(token, kind):
                    rows = page if isinstance(page, list) else page.get("items", page.get("history", []))
                    for row in rows:
                        play = normalize_compact_play(row)
                        if play:
                            plays.append(await resolve_play(play) if resolve_play else play)
            await self.store.reconcile_wetrakr_plays(gid, uid, plays, complete=True,
                                                    account_id=account_id)
            await self.store.save_wetrakr_sync(
                gid, uid, account_id, seeded=True, checkpoint=current,
                last_activity=current)
            log.warning("WeTrakr journal expired for user %s in guild %s; baseline re-seeded.", uid, gid)
            return 0
        seen = set(state["recent_entry_ids"])
        checkpoint = state["checkpoint"]
        posted = 0
        watch_added = watch_removed = xp_delta = 0
        pending = []
        duplicate_count = 0
        for row in rows:
            entry_id = str(row.get("entry_id") or "")
            action_at = row.get("action_at")
            if not entry_id or not action_at:
                raise ValueError("WeTrakr journal entry lacks entry_id or action_at")
            if entry_id in seen:
                duplicate_count += 1
                continue
            change = normalize_journal_entry(row)
            pending.append((change, row))

        def episode_pair(item):
            change = item[0]
            if not change or change.get("action") != "added" or change.get("media_type") != "episode":
                return None
            try:
                return (str(change["show_id"]), int(change["season"]), int(change["episode"]))
            except (KeyError, TypeError, ValueError):
                return None

        index = 0
        range_count = 0
        while index < len(pending):
            batch = [pending[index]]
            first = episode_pair(batch[0]) if deliver_group else None
            if first:
                direction = None
                while index + len(batch) < len(pending):
                    candidate = pending[index + len(batch)]
                    following = episode_pair(candidate)
                    previous = episode_pair(batch[-1])
                    if not following or following[:2] != first[:2]:
                        break
                    step = following[2] - previous[2]
                    if abs(step) != 1 or direction is not None and step != direction:
                        break
                    direction = step
                    batch.append(candidate)
            plays = []
            if resolve_play:
                for change, _ in batch:
                    if change and change.get("source_event_id"):
                        play = await resolve_play(change)
                        play["removed"] = change["action"] == "removed"
                        plays.append(play)
            if len(batch) > 1:
                accepted = await deliver_group(batch)
            else:
                change, row = batch[0]
                accepted = not change or await deliver(change, row)
            if not accepted:
                # Retry the entire batch, including every episode in its embed.
                log.warning("WeTrakr delivery pending retry for user %s in guild %s: %d journal row(s) in batch.",
                            uid, gid, len(batch))
                return posted
            if plays:
                result = await self.store.reconcile_wetrakr_plays(gid, uid, plays,
                                                                account_id=account_id)
                watch_added += result["added"]
                watch_removed += result["removed"]
                xp_delta += result["xp"]
            checkpoint = max(checkpoint, *(row["action_at"] for _, row in batch))
            entry_ids = [str(row["entry_id"]) for _, row in batch]
            if not await self.store.save_wetrakr_sync(
                    gid, uid, account_id, checkpoint=checkpoint, entry_ids=entry_ids):
                raise ValueError("WeTrakr link changed during journal delivery")
            seen.update(entry_ids)
            range_count += int(len(batch) > 1)
            for change, _ in batch:
                if change and change.get("action") == "added":
                    posted += int(change.get("media_type") in {"movie", "episode"}
                                  or change.get("status") in {"planning", "dropped", "paused", "completed"})
            index += len(batch)
        if not await self.store.save_wetrakr_sync(
                gid, uid, account_id, checkpoint=checkpoint, last_activity=current):
            raise ValueError("WeTrakr link changed during journal sync")
        log.info("WeTrakr sync for user %s in guild %s: %d journal row(s), %d duplicate(s), "
                 "%d new row(s), %d episode range(s), %d activity item(s), "
                 "%d watches added, %d removed, %+d XP.",
                 uid, gid, len(rows), duplicate_count, len(pending), range_count, posted,
                 watch_added, watch_removed, xp_delta)
        return posted
