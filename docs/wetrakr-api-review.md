# WeTrakr API review — 2026-10-05

## Evidence

Reviewed the user-supplied changelog for **1.0.7 beta (2026-10-01)** and **1.0.8 beta (2026-10-05)**. Direct documentation retrieval remains unavailable; release details below come from that supplied text. The API header remains `wetrakr-api-version: 1`.

Official changelog: https://api.wetrakr.com/#/changelog

## Applied changes

- Initial imports and journal-expiry reseeds use `journal_visible_until` rather than acknowledging a newer write-time activity stamp. The changelog documents a five-second journal publication delay.
- Journal batches retain `visible_until`. Across pagination, the earliest readable watermark is used conservatively. Timestamps are validated and compared by instant, including timezone offsets.
- After all rows are acknowledged, sync advances to the journal's readable watermark, including quiet or filtered reads. Failed deliveries retain their retry position. `last_activity` does not acknowledge changes beyond readable visibility.
- The provider adapter exposes the journal watermark as its change cursor.
- Compact episode history preserves `show_ids`; title resolution merges these parent IDs with metadata without mistaking episode IDs for show IDs. Journal normalization accepts `show_id` as well as the existing parent shapes.
- Watched show rollups without a `play_id` no longer imply completion. Since 1.0.7 they can represent ongoing caught-up shows or a user toggling the watched-list setting. Individual episode plays still supply activity, XP and removal events. This deliberately suppresses completion-only rollup notifications until an unambiguous completion signal is available.
- The read-only validation report includes both visibility watermarks and counts explicitly unknown watch dates as undated, including flagged January 1970 imports.

Existing malformed-response, cursor-cycle, page-count and gateway-retry safeguards remain.

## Reviewed changes needing no current transport update

The bot does not currently call WeTrakr's Discover, comment writes, calendar, friends feed, rating-distribution, tracking writes or note endpoints. Their new filters, fields and breaking validation rules do not alter these existing calls. Refresh errors remain surfaced by code; neither `INVALID_TOKEN` nor `TOKEN_REVOKED` is treated as a successful authentication response.

Show episode trees via `append=episodes` and WeTrakr community scores are useful future optimizations. They are not IMDb episode ratings. Watched TV/anime episode embeds continue to require the individual episode's IMDb rating and omit MAL; anime movies and status activities retain their applicable title-level ratings.

## Remaining live validation

A check immediately after a watch may legitimately see zero rows during WeTrakr's five-second publication delay. The next scheduled poll should pick it up; the visibility fix prevents acknowledging an unread change. No polling interval change is required by these release notes.

Run the host-side report and interactive watch/switch/removal sequence in `docs/testing-multi-tracker.md`. Automated fixtures establish checkpoint behavior; they do not establish actual Discord delivery, rewards or live payload compatibility. Exact unshown endpoint envelopes and production-status codes have not been guessed.
