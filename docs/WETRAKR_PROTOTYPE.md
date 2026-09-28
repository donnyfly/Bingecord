# WeTrakr integration prototype

This branch starts from `test` and keeps the existing SIMKL bot behavior. The
first slice contains an isolated WeTrakr client, normalized watch changes,
and independent `/wetrakr-link` and `/wetrakr-unlink` commands. Linked accounts
are not yet polled or posted to Discord.

## Configuration

Set `WETRAKR_API_KEY` on the experimental Docker deployment. It is WeTrakr's
application `client_id`, sent in the `wetrakr-api-key` header. Do not commit the
key or put it in a Discord command. WeTrakr also requires
`wetrakr-api-version: 1` and a per-user OAuth bearer token for private data.
The app key alone cannot read someone's watch history.

## Implemented foundation

- `wetrakr_client.py`: device-code initiation/polling, refresh-token rotation,
  account lookup, activity timestamps, paginated journal, and compact history
  with opaque `after` cursors. Quota responses are returned as errors instead
  of retried in a tight loop.
- `wetrakr_events.py`: source-aware play identities based on `play_id`,
  preserving rewatches and date edits; show and season roll-up rows cannot
  become additional episode XP. Status rows are separate from watched plays.
- `storage.py` and `bot.py`: OAuth device approval and separate linked-account
  state. A WeTrakr-only user is never accidentally polled as a SIMKL user.
- `providers.py`: a source-scoped watch change contract and shared target
  selection. Existing SIMKL state and polling retain their old behavior.
- `wetrakr_auth.py`: an isolated, rotation-safe access token refresh helper.
  Polling will invoke it when WeTrakr activity ingestion is enabled.

## Account isolation

The application key identifies this bot, not its owner's WeTrakr account.
Each `/wetrakr-link` creates its own device-code request. WeTrakr releases an
account token only after the person who is signed in approves that request.
Tokens are stored under the Discord user's ID, and each server has its own
link flag. A different user's link does not inherit the app owner's token.
The operator who controls the bot's data file can access stored credentials,
so protect the persistent volume and back it up securely.

## Shared-provider migration

The long-term shape has three boundaries:

1. **Source adapters** own OAuth, rate limits, cursors, and raw API shapes.
   SIMKL's current poller can be moved behind this boundary in stages.
2. **Watch changes** carry provider, account ID, source event ID, media IDs,
   timestamp, and add/update/remove. Provider names scope IDs. A TMDB/IMDb
   title match alone cannot prove two episode plays are the same watch.
3. **Delivery and progression** consume verified watch changes. Per-guild
   checkpoints and delivery acknowledgements move together. A user chooses
   one active activity provider per server at first; linking a second account
   does not automatically double-post or double-award XP. Once title and play
   matching are measured, dual-source merging can be opt-in.

The `providers.py` contract and WeTrakr sync state are preparatory. Current
polling still runs SIMKL only. Do not select WeTrakr as an active activity
provider until history seed, incremental journal delivery, and XP provenance
are implemented and tested.

The API is currently beta (1.0.3, 2026-09-27). Check the breaking changelog
before wiring live traffic. Documentation:

- https://api.wetrakr.com/#/authentication
- https://api.wetrakr.com/#/sync
- https://api.wetrakr.com/#/conventions
- https://api.wetrakr.com/#/limits
- https://api.wetrakr.com/#/changelog

## Next implementation gates

1. Smoke-test account linking with a real development key. Device approval and
   `GET /account/settings` should complete while SIMKL continues working.
   Invoke `WeTrakrAuth.access_token` on upcoming private calls and test the
   rotating token with a live account before unattended polling.
2. Add a per-guild WeTrakr sync checkpoint. On
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
