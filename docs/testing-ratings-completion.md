# Personal ratings and completion validation

Use `experimental` and keep the existing data volume. This feature needs no new credentials or environment variables. Existing provider authorization, polling intervals and public IMDb/MAL sources remain in use. Live account results still need verification; automated fixtures do not prove a provider's deployed API response or Discord permissions.

## Setup and command order

Repeat the checks for **SIMKL, WeTrakr and MDBList**, selecting that provider with `/bingecord source` first. Use `/bingecord checknow` for a complete poll, or wait for scheduled polling. Respect provider publication delays and rate limits; a pending rating sync appears in `/bingecord status user:@yourself`. Record the source, title/episode IDs, XP before/after, and relevant sanitized logs when something fails.

1. Link/select the account, run a baseline check, and record `/bingecord stats` XP.
2. Choose an unwatched movie and a short series with a verified episode catalogue. Record which regular episodes are watched in the selected provider. TMDB metadata supplies completion evidence; uncertain episode crosswalks deliberately suppress a milestone.
3. For group checks, enable Watched Together and use two members with different selected providers. Their watches must match the existing grouping rules.

## Acceptance checks

| Action | Expected result |
| --- | --- |
| Rate a movie, then mark it watched | One normal watch post with public IMDb and a separate `Your rating: 8/10` line; normal watch XP only |
| Mark a movie watched, then rate it | Next successful poll edits that same recent post; no additional post or XP |
| Change 8 to 7, then remove the rating | Same post changes to 7, then loses only the personal score; public scores remain |
| Remove the last rating in the account | Empty validated snapshot clears the recent personal score, rather than leaving a stale score |
| Rate a series, then watch an ordinary episode | Series personal score is not presented as an episode score |
| Set a personal episode rating, where supported | Exact episode score appears on a single-episode watch; a range does not borrow one episode's score |
| Rate a season, then finish it | Available season score takes priority; a series score is explicitly labelled if used instead |
| Finish a season of an ongoing show | Final watch/range includes `Finished Season N` when the finale is confirmed; it does not claim series completion |
| Watch every aired episode of a still-airing season | `Caught up` when the complete aired catalogue is verified; no invented season finale |
| Finish an ended/cancelled series | `Completed this series` only when all regular episodes across the complete catalogue are watched |
| Leave a regular episode unwatched | No completion claim; unwatched specials do not block a valid regular-season finish |
| Finish with a multi-episode range | Milestone appears once on the range, alongside existing ratings/artwork |
| Receive the native completed status after the final watch | Recent milestone post handles it without a duplicate completion post, including on a later poll |
| Unwatch a required episode | A recent completion annotation is removed or downgraded; existing ledger rules handle watch XP separately |
| Mark S01E01 on a new series | Started watching stays last, separated from ratings and milestone text |
| Rate WeTrakr 0 or MDBList 8.5 | Exact personal score displays as 0/10 or 8.5/10 |
| Two members watch together, rating 8 and 7 | One group post with each member's own score; mixed providers work; no copied first-member score |
| More than five members watch together | Existing group accounting includes everyone; personal details mention at most five members |
| Hide personal scores via style | Only personal score disappears; public scores and completion annotations remain |
| Apply server force override | Effective server setting wins over the member's preference |
| Switch sources or relink another account | Old account messages keep their original scores; the new account cannot rewrite them |
| Initial import or source-switch catch-up | No historical watch/completion flood; existing XP deduplication stays in control |
| Restart after posting, then rate | Recent message binding survives and the same post is edited |
| Rate more than seven days after the post | No historical edit or new post |
| Delete the Discord message or revoke edit access | No repost; obsolete binding is discarded |
| Temporary API/edit failure | Old validated score remains; transient Discord edits retry on a later cycle |
| A WeTrakr journal removal references a deleted catalogue title | Stored play is removed by account/play ID without a metadata request; later journal watches continue |
| A WeTrakr title lookup returns 404 for a new watch | Preserve the watch and the journal's scoped IDs; missing artwork/title detail does not discard it or block following rows. Episode IDs must never be used as show IDs. Authentication, quota and server errors still remain pending |
| Delete a watch and mark it watched again before the next poll | MDBList's current history and WeTrakr's projected journal exclude the deleted play when choosing the verb: the replacement is `Watched`, not `Rewatched`. Repeat for movies and episodes, with removals both before and after additions in the journal |
| Keep an earlier play and add a later viewing | MDBList and WeTrakr label the later play `Rewatched`; retries and duplicate rows do not create a rewatch. Episode ranges split when their rewatch classification differs |
| Change the canonical SIMKL watch date or delete/re-add | A changed timestamp remains eligible for activity delivery but does not prove a rewatch. Use `Watched` unless a returned row explicitly carries a `rewatch_id`. The current SIMKL library reader does not request expanded rewatch sessions; enabling and importing those sessions is separate work |

## Examples

Movie:

```text
Watched Film
⭐ IMDb 7.5/10

💬 Your rating: 8/10
```

Ongoing show with a confirmed season finale:

```text
Watched S2E12 of Show
Episode title
⭐ IMDb 8.2/10

💬 Your season rating: 9/10

🏁 Finished Season 2.
```

A series personal rating would instead say `Your series rating`. SIMKL does not expose native season/episode personal ratings through its rated-title endpoint; a verified seasonal anime entry is explicitly labelled as a season-entry score. Missing scoped ratings are omitted. No rating is inferred from another account, a public community score or a different episode.

## Automated checks and API references

### Import performance acceptance checks

- Link an MDBList or WeTrakr account with a large history. Run checknow and confirm private progress refreshes about every 15 seconds and status shows downloaded/enriched counts. No historical activity, level-up or prestige flood should occur.
- During that first import, mark a new watch for an already imported member. Scheduled checks must continue and deliver their normal activity; importing accounts should not start duplicate jobs.
- Interrupt a large import after at least one enriched batch, preserve the data volume, and restart. For unchanged activity stamps, completed pages and enriched rows should be reused. Watch XP and live history must not be reconciled from partial staging.
- Change provider history while an interrupted import is paused. On retry, expect a fresh page traversal and no deleted watch restored from old staging. The baseline sync boundary remains the original capture for each resumed snapshot.
- Exercise an expired or repeated cursor, failed metadata request, source switch and account relink. Incomplete reads remain pending; account guards prevent stale imports writing to the new selection.
- Verify mixed-provider Watched Together still merges live watches. Historical imports never join those groups. Let an interaction expire or dismiss it while importing: the background job must continue.

Run `python -m pytest -q` and `python -m compileall -q trackerbot tests`. Tests cover snapshot pagination/precision, account isolation, mixed-provider grouping, late edits/removals, restart/expiry, failed edits, completion invalidation, pre-delivery reconciliation, delayed completion rollups, bounded catalogue fetching and deferred SIMKL status failure checkpoint handling.

The readers follow these official contracts:

- [SIMKL personal rated items](https://api.simkl.org/api-reference/simkl/get-user-ratings): read explicit 1–10 snapshots by type only after rating stamps change. An omitted score filter returns unrated library entries too. The documented empty response is `{}`.
- [WeTrakr API](https://api.wetrakr.com/): authenticated `/sync/ratings/{target}` compact rows, opaque `X-Pagination-Next` / `after` cursors, parent show IDs and decimal 0–10 scores.
- [MDBList API schema](https://api.mdblist.com/schema/?format=json): cursor-paginated `/sync/ratings`, `rating_precise`, and season/episode targets with scoped parent `show.ids`. Complete snapshots detect removals.

No live authenticated account calls were required to run the automated suite. Complete this table with real accounts before calling the feature live-validated.
