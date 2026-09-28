# WeTrakr integration prototype

This branch starts from `test` and keeps the existing SIMKL bot behavior. The
first slice contains an isolated WeTrakr client and normalized watch changes;
no live WeTrakr account is polled or posted to Discord yet.

## Configuration

Set `WETRAKR_API_KEY` on the experimental Docker deployment. It is WeTrakr's
application `client_id`, sent in the `wetrakr-api-key` header. Do not commit the
key or put it in a Discord command. WeTrakr also requires
`wetrakr-api-version: 1` and a per-user OAuth bearer token for private data.
The app key alone cannot read someone's watch history.

## Implemented read-only foundation

- `wetrakr_client.py`: device-code initiation/polling, refresh-token rotation,
  account lookup, activity timestamps, paginated journal, and compact history
  with opaque `after` cursors. Quota responses are returned as errors instead
  of retried in a tight loop.
- `wetrakr_events.py`: source-aware play identities based on `play_id`,
  preserving rewatches and date edits; show and season roll-up rows cannot
  become additional episode XP. Status rows are separate from watched plays.

The API is currently beta (1.0.3, 2026-09-27). Check the breaking changelog
before wiring live traffic. Documentation:

- https://api.wetrakr.com/#/authentication
- https://api.wetrakr.com/#/sync
- https://api.wetrakr.com/#/conventions
- https://api.wetrakr.com/#/limits
- https://api.wetrakr.com/#/changelog

## Next implementation gates

1. Add `/wetrakr-link` and `/wetrakr-unlink` as a distinct OAuth device flow.
   Only create a link after approval and `GET /account/settings` succeeds.
   Store access and **rotated** refresh tokens atomically. Respect the returned
   device polling interval and expiry. Keep SIMKL links untouched.
2. Add per-source account state and a per-guild WeTrakr sync checkpoint. On
   first link, walk compact movie and episode history with `after`, and seed
   records without announcing historical watches. Store a baseline timestamp
   from `/sync/last_activities` and account-specific identity.
3. On later polls, compare last activities, then read the journal from the
   saved checkpoint (categories: watched, watching, waiting, planning,
   dropped, paused, ignored). Apply all pages in order. Persist event IDs and
   checkpoint together after successful delivery. Keep a small overlap for
   several entries with the same `action_at` and deduplicate by `entry_id`.
   On `JOURNAL_EXPIRED` (30-day retention), rebuild a compact baseline.
4. Convert source changes to the existing activity/card pipeline. A `play_id`
   is one real watch: add, edit, and removal must update the same record.
   Group episode watches as before and keep status cards separate. Verify
   WeTrakr's show/season auto-watch cascade with a real account before XP.
5. Initially allow **one selected activity source** per user/server. Dual-source
   mode needs cross-provider title matching and a provenance set for every
   canonical watch so duplicate SIMKL/WeTrakr plays do not award XP twice,
   and removing one provider's record cannot remove the other provider's XP.
6. Measure calls and quota headers during a private pilot. A quiet 10-minute
   poll means 144 last-activities GETs per day per user. WeTrakr's beta limits
   are per user for authenticated calls: free 1,000/day and 200 GET/minute;
   production app keys have higher limits. Avoid full account fetches per poll.

The current SIMKL data file should be backed up before any schema migration.
Existing `simkl_token`, XP keys and SIMKL checkpoints must retain their
meaning; a new WeTrakr link must never silently re-award imported history.
