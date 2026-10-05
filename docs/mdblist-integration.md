# MDBList tracking integration — 2026-10-05

## Verified sources

- Official OpenAPI schema: https://api.mdblist.com/schema/?format=json (retrieved successfully; SHA-256 `4582f4ffe91913dc579102ee3976178d81092da37bdfc494d348e7972c20100f`). Endpoint descriptions and parameters below come from this schema.
- Official API index: https://api.mdblist.com/docs/
- Official authentication documentation: https://mdblist.docs.apiary.io/reference/external-lists/list-items (search-indexed authentication section; direct authentication page retrieval failed).
- Developer's device-flow implementation: https://github.com/linaspurinis/mdblistarr/blob/93f156442a622f82fc9d68f1d86eb27f4d427f7f/mdblistarr/mdblistrr/views.py

## What is implemented

`trackerbot/integrations/mdblist_tracking_client.py` is a separate account transport. It never uses the bot's existing `MDBLIST_API_KEY` ratings key for somebody else's watch history. It supports device authorization, token exchange/refresh, account reads, activity stamps, paginated individual history, paginated journal reads, watchlist reads and bounded per-item play histories. OAuth requests use form encoding and retain required trailing slashes. API calls use user bearer tokens. Errors omit arbitrary response text and surface quota retry delays without a retry loop.

MDBList is now a **selectable experimental tracker**. Device linking, token refresh, selected-source polling, quiet history imports, stable play reconciliation, shared XP/removal support, grouped episode activity, planned/dropped notices, statistics/rewards and registry discovery commands are connected. Missing or malformed full-history rows fail closed rather than removing credited watches. API-contract and reconciliation fixtures pass; no MDBList live account has been tested.

## Configure and test

1. Register your own MDBList OAuth application with device authorization enabled. Set `MDBLIST_CLIENT_ID` on the bot host; set `MDBLIST_CLIENT_SECRET` only if your application requires it. Members authorize through `/tracker-link provider:MDBList`, without supplying API keys. Keep host credentials and persisted member tokens private.
2. Deploy the experimental image after its build succeeds, preserving the existing data volume. Select `/tracker-source provider:MDBList`, then `/tracker-checknow`. The first complete import is quiet.
3. Mark one new episode or movie in MDBList and check again. Verify the activity, selected-source statistics and shared progression. Follow `docs/testing-multi-tracker.md` for duplicate, removal and discovery checks.

Polling follows the bot's shared polling interval. Unchanged activity stamps skip expensive history/list reads. Watched/journal changes invalidate affected title histories. Up to four titles use bounded per-title histories; larger batches or truncated histories use complete paginated movie/episode snapshots. Planned, dropped and paused lists are read only when their own invalidation stamps change. Expired journals reimport quietly. Quota errors pause that account until its retry delay expires and are visible in `/tracker-status`. The journal is an invalidation feed, never a source of invented play IDs or watch XP.

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

Paused notices come from `/sync/playback` rows with a non-null `paused_at`. Watched-show journal rows produce Completed for ended/cancelled productions and Caught up for other productions. Partial show/season rows never remove all child watches. Activity links use native movie/show IDs. Account links use the verified public `/@username` profile route. Anime classification uses shared metadata and crosswalks. Episode IMDb ratings use exact episode metadata; watched episodes omit MAL.

## Quotas

Official docs give a free-account daily limit of 1,000 calls, plus fixed five-minute limits of 1,000 reads and 300 writes. Limits are shared across that account's keys and OAuth apps. A 429 carries `Retry-After`. Poll using activity invalidation stamps and cached source data; do not fetch every list and full history every cycle. One stamp call every five minutes is 288 daily calls before history, commands and other apps, so the implementation must budget additional work and display quota pauses clearly.

## Remaining before claiming full parity

- Validate our registered OAuth device grant and real `plays=all` movie/episode payloads, including rewatches and removals. The official schema does not completely describe individual play rows; unsupported shapes stop reconciliation safely.
- Verify native title/account destinations, calendar-numbered anime and anime movies against live accounts.
- Live-check paused, Completed and Caught up notices against the registered account. Newly introduced paused snapshots are seeded quietly on upgrade.
- Measure quota usage with real large libraries. Targeted journal reconciliation, changed-list invalidation and safe truncated/expired fallbacks are implemented.
- Complete the provider-only and mixed-server live checklist before describing the integration as 1:1 parity.

## Shared watched-together delivery

All three providers submit watch embeds to one polling-cycle coordinator when the server's Watched Together feature is enabled. Grouping uses a shared title ID, matching canonical movie/episode coordinates, the same server/channel, different Discord members and the existing 30-minute watch-time window. Exact episode ranges must match; overlapping but different ranges remain separate. Missing shared IDs remain separate rather than grouping by title text. The footer names the participating trackers and descriptions mention at most five members.

Providers keep their existing post-before-acknowledgement contract: a failed grouped post leaves its observations/checkpoints pending. A global cycle lock prevents manual and scheduled multi-provider checks from acquiring provider locks in conflicting orders. Source-specific direct adapter tests remain usable outside a coordinated cycle.

Public profile route evidence: https://mdblist.com/@official and https://mdblist.com/@apollocat. Additional API contracts were verified from `https://api.mdblist.com/schema/?format=json`: `/sync/playback` returns an array; expired `/sync/journal` can respond HTTP 409 with `requires_full_sync=true`; per-title histories are capped at 200 plays.
