# Experimental multi-tracker validation

## Automated coverage

Run `python3 -m pytest -q` and `python3 -m compileall -q trackerbot tests`.

The suite checks both watch ingestion orders, both removal orders, repeated imports, restarts between removals, relinks, distinct rewatches, external ID conflicts, metadata/episode corrections, challenge reward reversals, selected-source statistics and community projections, discovery cards, account guards, adapter history/change contracts and registry poll dispatch. Existing tests cover activity delivery and progression notifications. Rendered profiles for SIMKL and WeTrakr and the rank/prestige/achievement animations have also been visually inspected.

## Host-side read-only API report

The validator reads a snapshot of the existing store and uses the current access tokens for GET requests. It never connects to Discord, refreshes OAuth, imports history, updates checkpoints or changes XP. Its report contains counts and error codes, not credentials or title lists. A successful read is **not** a pass for the interactive watch/removal/card checks below.

On the bot host after updating the experimental image, replace the three uppercase placeholders:

```bash
docker exec YOUR_BOT_CONTAINER python -m trackerbot.validation.live --store /app/data/store.json --guild-id YOUR_SERVER_ID --user-id YOUR_DISCORD_USER_ID --provider both --max-pages 2 --output /tmp/tracker-validation-before.json
docker cp YOUR_BOT_CONTAINER:/tmp/tracker-validation-before.json ./tracker-validation-before.json
```

For a venv installation, run the same `python -m trackerbot.validation.live` command from the repository directory with the actual store path. The command loads local environment settings but never prints their values. `--provider wetrakr` or `--provider simkl` limits the checks to one source. Output must be a new file so it cannot overwrite the live store or an earlier report.

The report checks the saved ledger's award references, raw live history/list response shapes, sampled plays absent from the store, outstanding journal rows and one exact episode's IMDb-ID availability. SIMKL's raw catalogs are counted without metadata lookups. A legacy ledger is audited on a copy and explicitly marked `migration_preview`.

WeTrakr history and lists are sampled to avoid an unbounded scan. At the page limit the report conservatively marks the sample incomplete, and `stored_plays_missing_from_live` is `null`; never treat a partial sample as evidence that a watch was deleted. Increase `--max-pages` only when a complete comparison is needed. Journal reads have the same page budget. Retention expiry, missing keys and expired access tokens are reported as blocked checks. Let the running bot refresh its tokens normally and rerun; do not paste credentials into chat.

Create a new `tracker-validation-after.json` report after the interactive sequence below. Send both reports and the corresponding sanitized bot logs for review. Reports created by this command do not include OAuth/app keys, though server/user IDs identify the account being tested.

## Validation status — 2026-10-05

- Automated fixture and API-contract tests: run for this change; regression results recorded in the commit.
- Actual live account/Discord checks: **pending**. This development workspace has no connected bot credentials or access to the running container.
- WeTrakr 1.0.7/1.0.8 changelog: **reviewed from user-supplied release notes**. Visibility, parent-ID and caught-up rollup adaptations are covered by fixtures. See [API review](wetrakr-api-review.md).

## Live account checks

Use the experimental image and your existing persisted data. Do not reset shared XP to perform these checks. Capture `/tracker-stats` XP and `/tracker-mapping` before and after each step. Reward thresholds can add challenge or achievement XP; compare the watch contribution as well as total XP.

| Test | Expected result |
| --- | --- |
| Select WeTrakr, mark a previously untracked episode, check; select SIMKL and mark the same episode within five minutes, check | One watch award; the second observation increases verified matches when IDs agree and does not earn another watch award |
| Repeat with SIMKL first, then WeTrakr | Same result in the opposite direction |
| Remove the WeTrakr copy and check WeTrakr, leaving the SIMKL copy watched | The occurrence retains watch XP |
| Remove the SIMKL copy and check SIMKL | The last supporting observation revokes watch XP; challenges and achievements recalculate |
| Repeat the removal test with SIMKL removed first | Same result in the opposite direction |
| Restart the bot between the two removals | Support and awards persist; no duplicate awards or old notification posts |
| Watch the same episode again more than five minutes later | A distinct occurrence earns its own watch award |
| Switch sources without changing either account | Earned XP stays intact; historical activity and reward animations are silent |
| Recheck unchanged history twice | XP, matches, challenge rewards and watch totals remain stable |
| WeTrakr anime movie, TV episode, anime episode, S1E1 and an S1E1-starting range | Correct classification, title/profile links and started-series text; IMDb episode ratings when available; no MAL score on watched episodes |
| `/tracker-watching`, `/tracker-random`, `/tracker-recommend` on each source | Account-specific lists/history and source labels; anime films appear under Anime; planned/dropped/paused titles excluded from fresh recommendations |
| `/tracker-stats`, achievements, challenges, leaderboard, server stats, weekly recap, community | Selected-source watch counts, one shared progression; mixed-source server counts each member once |
| `/tracker-debug` level, rank, prestige and achievement previews | Private rendered previews; no XP mutation |

For a failed check, retain the exact command order, selected source, title and episode IDs, watched timestamps, before/after XP and mapping counts, plus logs covering those checks. Missing ratings alone can be a missing exact episode IMDb ID or an unavailable IMDb dataset rating; the logs distinguish those cases.

## Limits before a third provider

Delivery still uses the native sync/checkpoint engines behind the adapter interface. The normalized page API needs a generic consumer; legacy SIMKL observation keys need account scoping; inactive accounts need independent resume/import cursors. ID enrichment and episode crosswalks remain incomplete. Matches beyond the five-minute window are deliberately separate. Ambiguous mappings and legacy double awards remain flagged for review rather than silently changing existing XP. These limits must be resolved and the live checks must pass before claiming complete provider parity or enabling another tracker.

## WeTrakr 1.0.7/1.0.8 live checks

1. Select WeTrakr and mark one previously unwatched episode. A check within five seconds can return zero because the journal is not published yet. After at least five seconds, allow the next scheduled poll or run `/tracker-checknow` once. Expect one activity and one watch contribution; a repeated check must add neither.
2. Capture the read-only report around that watch. Compare journal `visible_until` and `journal_visible_until` with outstanding rows. A write-time stamp beyond visibility must remain pending, not be acknowledged as seen.
3. Toggle WeTrakr's “Show caught-up shows in Watched” setting for an ongoing caught-up series. Expect no completed-series notification, no duplicate episode XP, and no reversal of existing episode XP.
4. Repeat the existing cross-source duplicate and both-provider removal tests. New compact `show_ids` should support mapping even when a metadata response omits a parent external ID.

Series-level watched rollups alone currently do not produce completion notifications, because the API now uses those rows for both completion and caught-up settings. Individual watch embeds remain enabled.

## Server member lists

- `/tracker-status` remains an admin-only, ephemeral server summary. It displays at most five tracking records. `/tracker-status user:@username` inspects just that member; an unlinked member produces an explicit empty result.
- `/tracker-leaderboard` displays five members per page with Previous/Next buttons controlled by the command author. Rank numbers continue across pages, and both image and fallback text paths paginate.
- Community summaries, community reward announcements and watched-together mentions display at most five members followed by an additional-member count. Weekly recaps retain their top-five summary. Every member still participates in accounting, delivery acknowledgement and rewards.
- Live check: use a server with more than five linked members, inspect a member outside the summary, browse all leaderboard pages, and confirm group reward/watch totals include members omitted from display.


## MDBList experimental live validation

Configure `MDBLIST_CLIENT_ID` (and client secret if required by your registered OAuth app), deploy the latest experimental build and preserve the data volume. Never paste tokens into Discord or issue logs.

1. `/tracker-link provider:MDBList`: authorize the private device link with your own account. Check `/tracker-status user:@yourself` shows MDBList.
2. `/tracker-source provider:MDBList`, `/tracker-stats`, `/tracker-checknow`: the initial complete history import posts zero old activity. Record XP and `/tracker-mapping`.
3. Mark a new TV S01E01, then a consecutive range beginning at S01E01 on another series. Check title/source links, episode IMDb scores when available, no episode MAL score, and the Started watching line. Check anime movie classification separately.
4. Mark that same watch in SIMKL or WeTrakr after switching to that provider. Check XP does not increase for the matched occurrence. Switch back; history catch-up stays quiet.
5. Remove the watch on one provider: XP remains while another observation supports it. After both sources have synced their removals, the final support removal reverses XP. A genuine later rewatch remains a separate occurrence.
6. Check `/tracker-watching`, `/tracker-random`, `/tracker-recommend`, stats, leaderboard and reward cards use MDBList data and shared progression. Verify a new eligible watch can trigger achievement/challenge/prestige notifications.
7. Restart the bot and check again: no replay or duplicate XP. Check `/tracker-status` reports authorization/quota failures without exposing tokens. MDBList paused/completion-only notices remain outside this beta.

Optional read-only audit: `python3 -m trackerbot.validation.live --help`, then use its `--provider mdblist` option with your persisted store and account selection. The audit does not refresh tokens or mutate XP. Share only redacted report/log output when a live payload fails; never share the store or credentials.

## MDBList command audit (2026-10-05)

All 24 registered `/tracker-*` commands have a selected-source or shared-data path; none requires an active SIMKL account for an MDBList member. This is a code/fixture audit, not proof of every live endpoint or account configuration.

| Commands | MDBList path | Validation |
| --- | --- | --- |
| link, unlink, source, status, checknow | MDBList OAuth, account-scoped storage and selected-source snapshot poller | Adapter/storage tests; live linking and activity reported successful |
| watching, random, recommend | Selected adapter's up-next, watchlist and history/exclusions; MDBList destination IDs | Command fixtures for watching/random; adapter fixtures for recommendation inputs; real discovery payloads still need live checks |
| stats, leaderboard, server-stats, weekly-recap | Selected-source watch projections, shared XP and mixed-server aggregation | Storage/projection and summary tests; live MDBList summary cards still need checking |
| achievements, challenges, community | Shared ledger/reward events and server progression | Reward/storage tests; challenge command uses neutral branding |
| mapping, user-reset | Shared occurrence audit; provider-aware server reset and quiet reimport | Command audit and reset/restart/duplicate/removal fixtures |
| debug | Shared level, rank, prestige and achievement preview; no provider API needed | Existing rendered notification tests |
| features, timezone, style, style-server, setchannel | Server/member settings independent of provider | Existing settings tests |

MDBList paused/completion-only activity notices and cross-member watched-together delivery are not covered by this command audit. Anime lists and recommendation destinations should be verified with real MDBList up-next/watchlist responses before claiming full parity. Live reward delivery also depends on enabled server feature settings and the bot's channel permissions.
