# Adding a tracking provider

This is the standing specification for future integrations. A request such as “add provider X” means implement its equivalents of the existing tracker features, using its own account, links, history, lists, and activity data. The requester should not have to restate the SIMKL or WeTrakr behavior.

## Before writing code

Read `docs/multi-tracker-design.md`, `providers.py`, `tracker_mapping.py`, and the current `/tracker-*` handlers. Verify X's official authentication, watch history, changes/removals, pagination, rate limits, statuses, rewatches, and list endpoints. Record API evidence and unavailable capabilities here. A title/ratings metadata service without user watch history is an enrichment source, not a watch tracker.

## Implementation contract

1. Declare `ProviderManifest` for X and implement `TrackerProvider` in a dedicated `*_provider.py`. Register it in the provider registry. Keep credentials, pagination, refresh, and API-specific errors inside the adapter.
2. Return `WatchChange` objects with stable provider/account/event/change IDs, observed and watched timestamps, movie or episode kind, scoped title IDs, and source episode coordinates. Validate pages with `ProviderPage.validate`. Preserve raw source IDs for audit and removals. Never substitute another provider's API when a capability is unavailable.
3. Import history silently and checkpoint it durably before live posts. Make duplicate pages and retries idempotent. Treat edits, removals, rewatches, journal expiry, and a switch during activity as first-class cases. Maintain each account's cursor independently.
4. Feed observations to the shared mapping and occurrence ledger. Prefer verified media-scoped TMDB/TVDB/IMDb/MAL IDs and explicit episode crosswalks. Preserve uncertain matches separately; never merge on a conflicting ID. One watch earns XP once. A deletion revokes XP only if no linked provider still confirms that occurrence.
5. Wire the same commands and cards as SIMKL: `/tracker-link`, `unlink`, `source`, `status`, `checknow`, `stats`, `achievements`, `challenges`, `community`, `leaderboard`, `server-stats`, `weekly-recap`, `watching`, `random`, `recommend`, `style`, `style-server`, `features`, `setchannel`, `timezone`, `user-reset`, and `debug`. Source-specific titles, profiles, poster/backdrop, IMDb/MAL ratings, first-episode text, anime film counts, ranges, and shared progression notifications must render correctly.
6. Test a provider-only user, a SIMKL-only user, a mixed server, linked accounts with overlapping watches and rewatches, switches in both directions, deletions from either account, reimports, invalid tokens, failed posts, rate limits, and normal and anime movies/episodes. Assert persisted XP and statistics as well as card output. Run `python3 -m pytest -q`.

## Release gate

The new provider is complete only when the above command and reward tests pass with realistic API fixtures and a live test account. Record its manifest limitations and any unverified endpoints. Do not advertise 1:1 parity for unsupported API capabilities or unfinished mapping. The selected-source behavior and shared XP rules remain the same for every provider.

## Current migration state

SIMKL and WeTrakr still have separate ingestion loops and command implementations. The provider contract and ID-aware matcher are the first shared components; adapters, canonical occurrence persistence, full historical ID backfill, and command projections must move to the contract before adding a third watch tracker. Legacy title matches are explicitly marked as such, and existing duplicate XP is not automatically deducted during migration.
