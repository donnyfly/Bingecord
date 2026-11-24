# Bingecord agent handoff

Bingecord is a self-hosted Discord bot for tracking movies, TV, anime, and manga. It syncs members' activity from linked trackers, posts visual cards and recaps, and awards shared progression and community rewards. The repository is [donnyfly/Bingecord](https://github.com/donnyfly/Bingecord). Older clones may still point to `donnyfly/SIMKLTrackerBot`. User-facing commands belong under `/bingecord`; older `simkl_*` Python function names often serve all providers.

Start with this file, [README.md](README.md), and the relevant source and tests. Older docs contain useful design history and sometimes describe superseded commands or provider counts. Check the current command registration, feature flags, and provider capabilities when they disagree. Keep this handoff current when a change alters an invariant, deployment practice, or roadmap decision.

## How work ships

- Use a clean checkout of `experimental` for implementation. Fetch the remote branch before editing, preserve existing user changes, test the patch, and commit and push requested changes to `experimental`. Verify the remote commit and report what was tested and whether Discord was checked live.
- `main` is the stable release branch, and `test` also has a dedicated image. Merging into `main`, publishing a stable release, and deploying a running bot are separate operations to perform when requested. Use the intended commit and tag range when drafting patch notes. Ask only for decisions that require the maintainer.
- The maintainer self-hosts with Docker Compose via Dockhand. The repo's Compose file uses `ghcr.io/donnyfly/bingecord:experimental`. GitHub Actions publishes branch images for `experimental` and `test`. Stable version tags can publish versioned images and update `:latest`. Verify the Docker job or image tag separately from a successful commit or GitHub release.
- Preserve the existing `./data:/app/data` volume on deployment. `data/store.json` contains persisted links and bot state. `.env.example` documents configuration, including the distinction between MDBList tracking credentials and optional ratings enrichment. Keep credentials, OAuth tokens, real member data, local stores, repair backups, and webhook values out of commits, logs, screenshots, and public handoffs.

## Code map

| Area | Start here |
| --- | --- |
| Startup, slash commands, scheduled checks, and delivery orchestration | [bot.py](bot.py), [trackerbot/bot.py](trackerbot/bot.py) |
| Provider contracts, OAuth, polling, and provider-native history | [providers.py](trackerbot/core/providers.py), [trackerbot/integrations/](trackerbot/integrations/), [import_work.py](trackerbot/core/import_work.py) |
| Persisted state, event identity, and reconciliation | [storage.py](trackerbot/core/storage.py), [tracker_mapping.py](trackerbot/core/tracker_mapping.py), [watch_ledger.py](trackerbot/core/watch_ledger.py) |
| Grouped activity, ongoing message updates, and completion | [watch_delivery.py](trackerbot/core/watch_delivery.py), [activity_details.py](trackerbot/core/activity_details.py), [completion.py](trackerbot/core/completion.py) |
| Metadata and artwork lookups | [trackerbot/metadata/](trackerbot/metadata/), chiefly `tmdb_client.py` plus IMDb and MDBList enrichment |
| XP, achievements, personal goals, and repair | [progression.py](trackerbot/core/progression.py), [achievements.py](trackerbot/core/achievements.py), [personal_challenges.py](trackerbot/core/personal_challenges.py), [historical_repair.py](trackerbot/core/historical_repair.py), [progression_rebuild.py](trackerbot/core/progression_rebuild.py) |
| Reading history and community goals | [reading.py](trackerbot/core/reading.py), [manga_events.py](trackerbot/core/manga_events.py), [community_events.py](trackerbot/core/community_events.py) |
| Card models, Pillow rendering, and Discord UI | [trackerbot/presentation/](trackerbot/presentation/). Start with `activity_cards.py`, `card_details.py`, `media_pages.py`, `profile_visuals.py`, and `manga_pages.py` |
| Tests and live validation | [tests/](tests/), [trackerbot/validation/](trackerbot/validation/), [live checklist](docs/testing-multi-tracker.md), [ratings and completion checklist](docs/testing-ratings-completion.md) |

## Data and behavior to preserve

### Trackers and watch identity

- A member selects one watch provider per server. Different members of the same server can use different providers. SIMKL, WeTrakr, and MDBList support movie, TV, and anime watch tracking. AniList supplies anime and manga through snapshots of public list entries. Selecting AniList for anime currently supplies activity without watch XP or Watched Together.
- Manga has an independent per-server AniList setting. It can run alongside any watch source. New AniList links on `experimental` enable it and attempt a silent first sync. Existing links retain their saved setting on reauthorization. Members can use `/bingecord source manga:Off` or `manga:On`. The sync notice distinguishes a ready baseline from a pending one. The next poll or an admin's `/bingecord checknow` can finish a pending baseline.
- Keep provider-native account, title and play IDs, original coordinates, display order, and metadata lookup coordinates distinct. Verified crosswalks can connect them. Use exact external evidence to merge identities. Treat conflicting IDs and ambiguous history as review cases. Specials need explicit mapping. Long-running absolute anime numbering and split seasonal entries deserve dedicated regression coverage.
- Watch stats and links follow the selected provider. XP, levels, ranks, and prestige are shared per Discord user. A watch occurrence can have multiple supporting source observations and awards watch XP once. A genuine rewatch remains a distinct occurrence. The final supporting removal revokes the watch credit and can recalculate dependent rewards.
- Initial imports and source-switch catch-up seed silently. A first import can count toward achievements and progression under the current rules. Unmatched secondary historical XP can be held for review. Preserve account-scoped checkpoints and replay safety. Do not advance a checkpoint on a partial or failed history read. Use bounded, resumable imports and shared metadata requests where available.
- `/bingecord mapping` is the entry point for matching and XP audits. Repair and rebuild flows preview changes, require a user-bound apply token, and preserve backups before committing. `user-reset` concerns imported server state and achievements while keeping links, global XP, and personal style. Use the documented workflow for live data.

### Progression, schedules, and cards

- Base watch XP is **4 XP per catalog minute** on SIMKL, WeTrakr, and MDBList. Runtime fallbacks are 25 minutes per episode and 90 minutes per movie. Achievements and personal/community challenges award bonus XP. Current rules are in code and the README, including preservation of historical challenge definitions.
- AniList's initial manga snapshot contributes to lifetime totals and eligible achievements. Subsequent detected chapter progress drives period statistics, reading streaks, and reading goals. Baseline imports and list-update date hints supply no dated reading activity. Manga XP comes from achievements, challenges, and community rewards.
- Personal watch and reading challenges reset in UTC. Weekly recaps, community events, and server period reports use the server timezone, with Asia/Singapore as the configuration default. Watch community events rotate through Watchathon, Genre Expedition, Community Bingo, and Decade Discovery. Manga runs a parallel rotation of Community Readathon, Manga Genre Expedition, Manga Bingo, and Manga Decades Tour.
- Visual cards are the default style for new or reset preferences. Preserve saved user settings and legacy styles. Watch cards keep a compact, faded footer, full-size ratings on the right, and milestone chips near the activity/title. Use activity labels and source links from the selected provider. Cards are direct attachments without attachment descriptions. This keeps Discord's ALT badge off visual cards and GIFs. Provide the readable text through `View details` and retain embed fallbacks. Use `View on tracker` when a title URL is available.
- Prefer the correct episode still, then a series backdrop, then a placeholder. Use verified title or season logos, with title text as the final fallback. Episode cards use episode IMDb ratings when available. Anime film and status cards may include title-level IMDb and MAL. Personal ratings are read-only and retain their scope, including series, season, and episode.
- Watched Together joins matching films, episodes, or identical ranges in one server/channel within 30 minutes. Each participant keeps independent history and awards. Partial overlaps stay separate, and mentions stay quiet. Verified start, season-finish, caught-up, and completion milestones use tracker and catalog evidence.
- Reading cards use the AniList cover and banner, a blurred cover when the banner is missing, and an exact MAL-linked cover fallback when available. Preserve public list statuses and chapter/reread wording. Watch and Manga pages share messages on stats, weekly recaps, and community. The manga profile mirrors the Watch layout with shared progression and reading-specific spotlight, genres, recent covers, streaks, and rewards.
- `/bingecord debug` is the private preview path for activity, profiles, personal challenges, community events, recaps, and progression. Preserve sample labels and keep previews free of rewards and public posts. Bounded artwork fetches, output fallbacks, allowed image URLs, and disabled mentions apply to new renderers too.

## Latest checkpoint and unfinished work

- The published stable checkpoint is [v1.0.0](https://github.com/donnyfly/Bingecord/releases/tag/v1.0.0). On `experimental`, the changes since that release include [manga enabled on new AniList links](https://github.com/donnyfly/Bingecord/commit/f673e0e), [full manga profile cards](https://github.com/donnyfly/Bingecord/commit/46bba8f), the [deterministic WeTrakr notification test](https://github.com/donnyfly/Bingecord/commit/d8e5e48), and the [Monogatari artwork fix](https://github.com/donnyfly/Bingecord/commit/5529c11). The last full run before this handoff reported **503 passing tests**. Check tags and branch heads for newer work.
- The Monogatari fix is scoped to SIMKL anime entry `2352394` (*Off & Monster Season*). All 14 regular episodes use TMDB series `46195`, season `5`, and their matching episode number for the still. Existing TVDB-derived display numbering can show `S6E10`. The official season logo is used, and only episode 10 has a verified direct still fallback. The workaround is for presentation. Keep watch identity separate. A real Discord post from the updated build was still unverified.
- The WeTrakr challenge test now fixes the dates and runtimes. It verifies a silent qualifying history import and an exactly-once queued notification when a live watch crosses the daily threshold. Keep calendar-sensitive test fixtures deterministic.
- **Discovery is on hold.** `/bingecord recommend`, `/bingecord random`, and `/bingecord watching` were intentionally removed from registration, help, and feature menus. Their internal handlers and renderer code are retained. Resume only when the maintainer asks. For the random reveal, the agreed prototype was an MP4 poster reel with a clear winner, tracker link, and static fallback. The prototype is in `trackerbot/presentation/random_reveal.py`; ffmpeg is in the Docker image. Live Discord delivery is a validation task if it returns.
- **Recommendation direction to revisit.** Use recently watched titles and longer-term taste, including highly rated and recurring interests. Read the selected provider's available history and exclusions. A metadata-backed shortlist can run on its own. AI reranking is an optional idea and remains undecided. AniList would need dedicated anime history/progress and metadata handling. Treat any new AI service, data sharing, or hosting dependency as a product choice to confirm.
- **Outstanding live validation.** Verify real synced artwork and milestones across providers, mixed-provider Watched Together and correct account links, and AniList baseline/reading/profile behavior after deployment. Test fresh link, existing link, source switch, rewatch, removal, retry, and missing metadata on relevant changes. Use [the live checklist](docs/testing-multi-tracker.md) and [provider guide](docs/adding-provider.md) as starting points. Outstanding adapter work includes account-scoped legacy history, reliable inactive-source reconciliation, and verified episode crosswalks. Provider parity is capability-specific.
- **Other deferred ideas.** Community stretch rewards, nominated watch clubs, and country-specific events are documented as later ideas. Earlier discussion also parked Just Watched, On This Day, profile comparison, smarter notifications, and AI summaries. These are ideas to confirm, with no scheduled implementation.

## Local validation

CI and Docker use Python 3.13. The app uses discord.py, aiohttp, Pillow, and a local JSON store. Run from the repo root in a virtual environment.

```bash
python -m venv ../bingecord-venv
source ../bingecord-venv/bin/activate
python -m pip install -r requirements.txt pytest
python -m pytest -q
python -m compileall -q trackerbot bot.py
```

For normal development, start with the tests for the changed command, adapter, storage projection, or renderer. Mock provider traffic and Discord sends and use temporary state files. For visual changes, render an example when possible, check common and edge states, and disclose whether a live Discord post was verified. Real startup uses `python bot.py` with the required settings in [`.env.example`](.env.example). Run `python -m trackerbot.validation.live --help` for the read-only validator and follow the live checklist when running it on the bot host. For production sync investigations, start with the private `/bingecord status` report and sanitized logs. For XP discrepancies, prefer the private `/bingecord mapping export:True` audit over the raw store.
