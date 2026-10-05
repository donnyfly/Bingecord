# Adding a tracking provider

This is the standing specification for future integrations. A request such as “add provider X” means implement its equivalents of the existing tracker features, using its own account, links, history, lists, and activity data. The requester should not have to restate the SIMKL or WeTrakr behavior.

## Before writing code

Read `docs/multi-tracker-design.md`, `trackerbot/core/providers.py`, `trackerbot/core/tracker_mapping.py`, and the current `/tracker-*` handlers. Verify X's official authentication, watch history, changes/removals, pagination, rate limits, statuses, rewatches, and list endpoints. Record API evidence and unavailable capabilities here. A title/ratings metadata service without user watch history is an enrichment source, not a watch tracker.

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

SIMKL and WeTrakr are registered adapters in `trackerbot/integrations/provider_adapters.py`. Account authorization, polling, watching lists, planned picks, recommendation inputs and destination links route through the registry. Both adapters expose validated history/change pages. Their existing delivery/checkpoint engines remain behind `poll()`; settings and shared reward/statistic commands use provider-independent storage projections.

`trackerbot/core/watch_ledger.py` controls new watch awards and removals. The persisted `occurrence_ledger` stores supporting source observations and award references; `xp_events` is its reward projection. Migration preserves existing awards and flags previously duplicated awards without automatically deducting them. The adapter history/change contracts are available, but delivery still uses the native sync engines; migrating both to one generic page consumer and independent inactive-source cursors remains work before a third tracker is enabled. Legacy SIMKL observation keys are not yet account-scoped. Verify these migration gaps rather than assuming registering an adapter alone is sufficient.


MDBList is registered in `trackerbot/integrations/mdblist_provider.py` as an experimental third adapter. Reuse `link_provider_account`, `rotate_provider_tokens`, `unlink_provider_account`, `reconcile_provider_plays` and `save_provider_sync` for account-scoped native-play providers; existing WeTrakr wrappers preserve compatibility. MDBList uses targeted journal invalidation with complete snapshot fallbacks, per-play delivery acknowledgements and a shared occurrence ledger. New providers should submit WatchActivity objects to the shared polling cycle, retain canonical display coordinates separately from metadata/source coordinates, and wait for Discord acceptance before committing. Its outstanding live-contract checks and unsupported notices are documented in `docs/mdblist-integration.md`.
