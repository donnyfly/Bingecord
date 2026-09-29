# WeTrakr integration prototype

This branch starts from `test` and keeps the existing SIMKL bot behavior. The
first slice contains an isolated WeTrakr client, normalized watch changes,
and `/tracker-link` and `/tracker-unlink` provider choices. An opt-in
read-only activity poller now posts new WeTrakr watches and selected statuses.

## Configuration

Set `WETRAKR_API_KEY` on the experimental Docker deployment. It is WeTrakr's
application `client_id`, sent in the `wetrakr-api-key` header. Do not commit the
key or put it in a Discord command. WeTrakr also requires
`wetrakr-api-version: 1` and a per-user OAuth bearer token for private data.
The app key alone cannot read someone's watch history.

## Implemented foundation

- `trackerbot/integrations/wetrakr_client.py`: device-code initiation/polling, refresh-token rotation,
  account lookup, activity timestamps, paginated journal, and compact history
  with opaque `after` cursors. Quota responses are returned as errors instead
  of retried in a tight loop.
- `trackerbot/integrations/wetrakr_events.py`: source-aware play identities based on `play_id`,
  preserving rewatches and date edits; show and season roll-up rows cannot
  become additional episode XP. Status rows are separate from watched plays.
- `trackerbot/core/storage.py` and `trackerbot/bot.py`: OAuth device approval and separate linked-account
  state. A WeTrakr-only user is never accidentally polled as a SIMKL user.
- `trackerbot/core/providers.py`: a source-scoped watch change contract and shared target
  selection. Existing SIMKL state and polling retain their old behavior.
- `trackerbot/integrations/wetrakr_auth.py`: an isolated, rotation-safe access token refresh helper,
  invoked by WeTrakr polling.
- `trackerbot/integrations/wetrakr_sync.py`: a compact-history baseline and an incremental journal
  poller. It imports play IDs into the shared statistics and XP store, applies
  edits and removals, acknowledges each successfully delivered entry, and
  re-reads with overlap so equal timestamps cannot lose watches.

## Trying activity on experimental

1. Link WeTrakr with `/tracker-link` provider **WeTrakr** and choose **WeTrakr** with
   `/tracker-source` in the server. A user with both links defaults to SIMKL
   until they make this choice.
2. Run `/tracker-checknow` (admin) once to seed the history baseline. This
   reads compact history, seeds XP, statistics, challenges and achievements,
   but does not post past watch activity.
3. Mark a *new* movie or episode watched, or change a planning/dropped/paused
   status in WeTrakr. Run `/tracker-checknow` again or await the poll interval.
   The journal may lag by several seconds, so retry on the next cycle if needed.
4. Switch back with `/tracker-source` → SIMKL. The inactive tracker does not
   post activity. Switching resets its activity baseline without removing
   either link or the existing progression.

WeTrakr watch plays award XP and update statistics, challenges, achievements,
recaps and leaderboards. `/tracker-watching`, `/tracker-random`, and
`/tracker-recommend` use the selected source. Its watches are not yet combined
into Watched Together embeds. Cross-provider matches currently use the same
title, media category and number of plays. Catalog title differences and
partially overlapping episode histories can still result in inaccurate
matching. Verify profiles on a development copy of the data before promoting
this branch. API calls and payloads have been tested with fakes; an end-to-end
run requires a linked development account. Statuses are source-specific;
automatic watching/waiting transitions do not create extra status posts.

## Account isolation

The application key identifies this bot, not its owner's WeTrakr account.
Each WeTrakr choice in `/tracker-link` creates its own device-code request. WeTrakr releases an
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

The `trackerbot/core/providers.py` contract is preparatory for moving SIMKL behind the same
adapter boundary. The current SIMKL poller retains its legacy state and XP
model; the WeTrakr poller has its own checkpoint and posts only for users who
select it in that server.

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
2. Validate journal and metadata shapes with live movie, episode, rewatch,
   status, edit and removal changes; refine artwork and anime classification.
3. Improve cross-provider identity matching with shared catalog IDs and
   episode coordinates. Title and play-count matching is provisional when
   the two trackers have different names or only partially overlap.
4. Reuse Watched Together grouping for both providers. Group episodes as
   before while keeping status cards separate; verify show/season cascades.
5. A future dual-source posting mode needs a canonical per-play provenance set.
   The current source choice is per server and cross-source XP transfers
   only when media category and title agree.
6. Measure calls and quota headers during a private pilot. A quiet 10-minute
   poll means 144 last-activities GETs per day per user. WeTrakr's beta limits
   are per user for authenticated calls: free 1,000/day and 200 GET/minute;
   production app keys have higher limits. Avoid full account fetches per poll.

The current SIMKL data file should be backed up before any schema migration.
Existing `simkl_token`, XP keys and SIMKL checkpoints must retain their
meaning; a new WeTrakr link must never silently re-award imported history.
