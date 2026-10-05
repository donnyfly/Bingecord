# MDBList tracking integration — 2026-10-05

## Verified sources

- Official OpenAPI schema: https://api.mdblist.com/schema/?format=json (retrieved successfully; SHA-256 `4582f4ffe91913dc579102ee3976178d81092da37bdfc494d348e7972c20100f`). Endpoint descriptions and parameters below come from this schema.
- Official API index: https://api.mdblist.com/docs/
- Official authentication documentation: https://mdblist.docs.apiary.io/reference/external-lists/list-items (search-indexed authentication section; direct authentication page retrieval failed).
- Developer's device-flow implementation: https://github.com/linaspurinis/mdblistarr/blob/93f156442a622f82fc9d68f1d86eb27f4d427f7f/mdblistarr/mdblistrr/views.py

## What is implemented

`trackerbot/integrations/mdblist_tracking_client.py` is a separate account transport. It never uses the bot's existing `MDBLIST_API_KEY` ratings key for somebody else's watch history. It supports device authorization, token exchange/refresh, account reads, activity stamps, paginated individual history, paginated journal reads, watchlist reads and bounded per-item play histories. OAuth requests use form encoding and retain required trailing slashes. API calls use user bearer tokens. Errors omit arbitrary response text and surface quota retry delays without a retry loop.

This is an integration foundation, **not a selectable third tracker yet**. No MDBList credentials are stored, watches imported, Discord activity posted, or shared XP changed by this new module. API-contract fixtures pass; no MDBList live account has been tested.

## Authentication

MDBList supports both per-user API keys and OAuth. Use OAuth for Discord members so they do not need to paste keys. The developer-owned mdblistarr implementation verifies a device flow at `POST /oauth/device-authorization/`, followed by `POST /oauth/token/` with `grant_type=urn:ietf:params:oauth:grant-type:device_code`. Use our own registered OAuth client ID, not another application's ID. Request `read` access for the bot's read-only watch tracking; confirm scope authorization with a live test before enabling it.

The separate authorization-code flow requires PKCE and a redirect URI. A device flow fits the existing Discord link interaction without adding a public callback server. OAuth registration/device-grant availability for our own client must still be confirmed. Optional client-secret handling is supported for refresh requests; the verified public device implementation supplies only client ID for device exchange.

## Critical data contracts

| Operation | Verified behavior | Integration consequence |
| --- | --- | --- |
| `/user` | `user_id`, username, limits and quota information | Bind the returned account to its Discord owner; persist only necessary profile fields. |
| `/sync/watched?plays=all` | One row per movie/episode play; cursor pagination; max 1,000 with cursor | Import individual occurrences, not aggregated watched counts. Exact per-play movie/episode row shapes still need live fixtures; schema does not fully describe them. |
| `/sync/journal` | Latest retained state per item, not an append-only play log; 30-day retention | Treat rows as invalidations and reconcile affected items' current plays. Do not manufacture play IDs from journal timestamps. |
| Journal pagination | First request uses `since`; subsequent requests use `cursor`, never both | Preserve `statuses=partial` on every page; acknowledge only after successful reconciliation/delivery. |
| Journal expiry | `requires_full_sync=true`, reason `sync_window_expired` | Reimport quietly; never interpret expiry as an empty history or mass removal. |
| Journal episode identity | `ids` belongs to parent show; optional `episode_tmdb_id`, `episode_tvdb_id` | Keep show and episode ID namespaces separate and retain original numbering. |
| `partial` show/season status | Completion lost while some episodes remain watched | Never remove all child plays based on a rollup. |
| `/sync/history/{mediatype}/{provider}/{provider_id}` | Stable integer `play_id`; most recent 200; `truncated` flag; episode lookups require episode TMDB ID | A truncated response cannot prove older plays were removed. Use full plays snapshots when necessary. |
| Activity/journal `server_time` | Server clock captured before request processing | Use a server checkpoint, not local clock or arbitrary maximum item timestamp. |
| `/upnext` | In-progress shows with next episode; offset pagination and `has_more` | Basis for watching list, not equivalent to paused playback alone. |
| `/watchlist/items` | Planned movies/shows and cursor pagination | Basis for random picks and source-specific recommendation exclusions. |

Dropped endpoints exist. A paused activity stamp exists, but the exact paused list and status-notification semantics remain to verify. Series completion also needs an explicit distinction between ended/completed and currently caught-up entries. Native profile/title URL patterns, anime classification and exact episode-rating retrieval are not guessed from other trackers.

## Quotas

Official docs give a free-account daily limit of 1,000 calls, plus fixed five-minute limits of 1,000 reads and 300 writes. Limits are shared across that account's keys and OAuth apps. A 429 carries `Retry-After`. Poll using activity invalidation stamps and cached source data; do not fetch every list and full history every cycle. One stamp call every five minutes is 288 daily calls before history, commands and other apps, so the implementation must budget additional work and display quota pauses clearly.

## Work before exposing MDBList

1. Finish provider-neutral account storage, reconciliation and selected-source projections currently hard-coded for SIMKL/WeTrakr. Keep legacy SIMKL observation migration safe and account-scoped; retain independent source cursors.
2. Capture redacted `plays=all` movie, TV episode and anime fixtures with real stable play IDs, edits, rewatches and removals. Preserve null/unknown dates without inventing watch times.
3. Add a journal invalidation consumer: refresh affected individual plays, diff stable play IDs, and feed shared ledger observations. Use full paginated fallback when an item response is truncated; never widen partial rollup removals.
4. Add Discord OAuth link/unlink and registry manifest, source selection/status, quiet import, scheduled/manual polls, title/profile links, first-episode/range notices, exact episode IMDb ratings and anime film classification.
5. Connect stats, achievements, challenges, community, recaps, leaderboards, watching, random and recommendations to MDBList projections. Preserve shared XP/rewards and five-member display limits.
6. Test both existing-provider ingestion/removal orders, switches, retries, quota handling, journal expiry, restarts and failed posts. Run a live provider-only and mixed-server validation before advertising parity.
