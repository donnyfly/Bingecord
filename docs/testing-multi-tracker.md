# Experimental multi-tracker validation

## Automated coverage

Run `python3 -m pytest -q` and `python3 -m compileall -q trackerbot tests`.

The suite checks both watch ingestion orders, both removal orders, repeated imports, restarts between removals, relinks, distinct rewatches, external ID conflicts, metadata/episode corrections, challenge reward reversals, selected-source statistics and community projections, discovery cards, account guards, adapter history/change contracts and registry poll dispatch. Existing tests cover activity delivery and progression notifications. Rendered profiles for SIMKL and WeTrakr and the rank/prestige/achievement animations have also been visually inspected.

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
