# Multi-tracker design

## Product rules

- A member links any supported accounts and selects one activity source per server. A server can mix members using different sources.
- `/tracker-*` commands read each member's selected source for watch counts, activity, watching lists, planned titles, random picks, and recommendations. XP, achievements, challenges, level, and prestige belong to the Discord user once.
- Switching source imports the selected account's history quietly, retains all earned XP, and announces only new activity after its checkpoint. Unlinking a source does not erase verified shared watches.
- A removed watch revokes its XP only when no linked source still confirms that occurrence. Recalculating challenges and achievements must be idempotent. Imports and corrections do not fire old reward cards.

## Provider contract

`providers.py` defines `TrackerProvider`, `ProviderManifest`, a registry, scoped `WatchChange` and validated pages. Each provider supplies link/refresh, paginated changes and history, watching and planning lists, title metadata and links. Optional capabilities are explicit; unsupported commands report that capability rather than querying a different provider. Rate limits, pagination, and retry policy stay inside the adapter. `docs/adding-provider.md` is the standing implementation and parity checklist.

An observation carries `provider`, `account_id`, stable `source_event_id`, `action` (add/remove/update), `occurred_at`, `watched_at`, `media_type`, external IDs, show/season/episode/absolute number, and source metadata. Keep raw observations and a per-account cursor even when an account is inactive. Import and journal delivery must be transactional and replayable by source event ID.

## Identity and XP

1. Maintain a title identity graph keyed by media type and verified TMDB/TVDB/IMDb/MAL IDs. `tracker_mapping.py` now checks shared external IDs, rejects conflicts, and records whether a match is verified or a fallback for legacy ID-less XP. Numeric IDs are scoped to their provider and media type. Do not merge modern ID-bearing titles on name alone; hold ambiguous matches for review or correction.
2. Normalize episodes to a canonical show plus episode identity using verified crosswalks (TVDB absolute order for continuous anime, season and episode for seasonal TV). Keep source numbering for presentation and audit. Special episodes need explicit IDs; never infer them by position.
3. Match watch occurrences by canonical episode/movie and a configurable time window, respecting rewatches. A movie watched twice remains two watches. Store the observations supporting each occurrence, plus one XP award key. Changes in classification or metadata update the occurrence without adding XP.
4. Compute selected-source watch statistics from that account's observations; compute shared rewards from supported canonical occurrences. Deletions are reconciled against every linked account. A source switch changes the view and notification cursor, never the global XP ledger.

## Command and card projections

| Surface | Source of data |
| --- | --- |
| Link, unlink, source, status, checknow | Provider registry and selected account |
| Watching, random, recommend | Selected provider's watching/planning/history capabilities |
| Stats and activity cards | Selected provider's observations and links; global XP shown separately |
| Leaderboard, server stats, weekly recap, community | One selected-source projection per server member |
| Achievements, challenges, level, prestige | One deduplicated occurrence ledger per Discord user |
| Style, features, timezone, channel, debug | Provider-independent preferences and previews |
| User reset | Explicit selected-source server state reset; global progression requires a separate, carefully defined operation |

## Migration order

1. Add contract tests and normalized observations behind SIMKL and WeTrakr adapters. Keep current commands functional during migration.
2. Backfill canonical title and episode IDs, recording match confidence and leaving uncertain matches separate. Replay both sources without activity posts or reward notifications.
3. Build the occurrence ledger and compare XP to existing awards in a dry-run report. Migrate only verified matches, preserving earned XP and rewatches.
4. Move stats, rewards, discovery commands, and cards to projections. Test each command for SIMKL-only, WeTrakr-only, both linked, and switching accounts.
5. Add further providers after their available account, history, change, and list endpoints are verified. A metadata-only source can enrich titles but cannot supply watch activity.

## Current limitations (experimental)

The current implementation has separate SIMKL and WeTrakr ingestion and an approximate title/episode match. It does not yet have a canonical occurrence ledger, verified ID crosswalks for all episodes, or inactive-source cursors. It must not advertise perfect cross-provider deduplication or deletion reconciliation. IMDb, TMDB, TVDB, MAL, and MDBList are currently enrichment sources; future tracking integrations require their own confirmed history APIs and account semantics.
