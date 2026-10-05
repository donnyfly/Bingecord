# SIMKLTrackerBot

A self-hosted Discord bot that shares watch activity from **SIMKL, WeTrakr and MDBList**. One set of `/tracker-*` commands supports watch statistics, recommendations and shared XP, levels, achievements and prestige.

**Branch:** `experimental` · **Docker image:** `ghcr.io/donnyfly/simkltrackerbot:experimental`

Multi-tracker development is on this branch. The `latest` image follows `main` and may have different features.

## Features

- Link multiple accounts and select one tracking source per member, per server. Members using different providers can share the same server.
- Post movies, episodes, episode ranges and supported status changes, with provider links, title logos and artwork when available.
- Combine matching activity into **Watched Together** embeds, including mixed-provider groups. Watches must share a title identity and movie/episode range, happen within 30 minutes, and use the same posting channel.
- Show selected-source watch statistics, watching/planned lists, random picks and recommendations.
- Track shared XP, ranks and automatic prestige, with achievements, daily/weekly challenges, community goals, leaderboards and recaps.
- Configure personal/server styles, timezone and optional features, including an activity-only mode. Server member lists use bounded displays; `/tracker-status` can inspect one member.

Watched TV/anime episodes show **individual episode IMDb ratings**, when available. MAL is reserved for anime movies and anime status activities. Status embeds use posters; watch embeds can use episode stills or backdrops. A range starting at S1E1 includes the Started watching notice below the ratings.

### History, switching and XP

Initial imports and catch-up after switching sources are quiet. Existing history does not generate old activity posts or progression notifications. Switching sources preserves shared XP.

A shared watch ledger uses catalog IDs and episode identities to match confirmed duplicates across providers. A matched watch earns XP once; genuine rewatches remain separate. Removing a watch reverses its contribution only when no other imported source observation supports it. Inactive-account changes become known when that source is synced again.

`/tracker-mapping` is a private, read-only view of matches, unpaired watches and possible duplicate awards. Uncertain mapping is kept separate for review. The integration is experimental; see the [live validation checklist](docs/testing-multi-tracker.md) for outstanding acceptance checks.

## Installation

### 1. Configure credentials

Create a Discord bot and invite it using the `bot` and `applications.commands` scopes. Give it **View Channel, Send Messages, Embed Links, Attach Files and Read Message History** permissions in the activity channel.

Copy `.env.example` to `.env` and set the following:

| Variable | Purpose |
| --- | --- |
| `DISCORD_BOT_TOKEN` | Required Discord bot token. |
| `SIMKL_CLIENT_ID` | Required SIMKL application ID; startup currently requires it even when another provider is selected. |
| `TMDB_API_KEY` | Required metadata and artwork key. |
| `WETRAKR_API_KEY` | Application key enabling WeTrakr. |
| `MDBLIST_CLIENT_ID` | OAuth application ID enabling MDBList tracking. Register a device-authorization application at [MDBList Developer](https://mdblist.com/developer/). |
| `MDBLIST_CLIENT_SECRET` | Only needed if your MDBList application requires it for token refresh. |
| `MDBLIST_API_KEY` | Optional IMDb/MAL ratings enrichment; separate from MDBList tracking authorization. |

The host supplies application credentials. Members link their own accounts through `/tracker-link` and do not need the host's API keys. Keep `.env` and the data directory private: persisted data includes account tokens.

### 2. Start the bot

Save the included `docker-compose.yml` alongside `.env`, then run:

```bash
docker compose pull
docker compose up -d
docker compose logs -f
```

Compose mounts `./data` at `/app/data` to preserve accounts, sync state and progression. Keep this volume across upgrades and back it up privately. After changing `.env`, recreate the container with `docker compose up -d --force-recreate`.

To run from source instead, use Python 3.13 (matching the Docker image):

```bash
git clone --branch experimental https://github.com/donnyfly/SIMKLTrackerBot.git
cd SIMKLTrackerBot
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Configure .env, then start:
python bot.py
```

### 3. Set up your server

1. Run `/tracker-setchannel`. Configure `/tracker-features` and `/tracker-timezone` as desired.
2. Each member runs `/tracker-link`, then chooses their provider with `/tracker-source`.
3. Run `/tracker-checknow` to import the initial history quietly.
4. Mark a new watch after the import, then wait for polling or check again. Review `/tracker-stats` and `/tracker-status` to confirm the result.

## Commands

All providers use the same command names. Commands marked **Admin** require server permissions.

| Command | Purpose |
| --- | --- |
| `/tracker-link` | Authorize and link a SIMKL, WeTrakr or MDBList account. |
| `/tracker-unlink` | Remove a provider link in this server. |
| `/tracker-source` | Select the linked provider to track in this server. |
| `/tracker-status` | Admin: inspect links, selected sources and sync health; optionally select `user: @username`. Server results are paginated in groups of five members. |
| `/tracker-checknow` | Admin: check members' selected sources immediately. |
| `/tracker-stats` | Show a visual profile with selected-source watches and shared progression. |
| `/tracker-mapping` | Privately inspect cross-provider watch matching and possible duplicate awards. |
| `/tracker-achievements` | Show achievements and progress. |
| `/tracker-challenges` | Show daily and weekly challenges. |
| `/tracker-community` | Show the server's weekly community challenge. |
| `/tracker-leaderboard` | Show the server leaderboard with bounded member displays. |
| `/tracker-server-stats` | Show combined server watch statistics. |
| `/tracker-weekly-recap` | Admin: post a weekly watch recap. |
| `/tracker-watching` | Show the selected account's watching list and next episodes where available. |
| `/tracker-random` | Pick from the selected account's planned list. |
| `/tracker-recommend` | Recommend titles using selected-source history and exclusions. |
| `/tracker-style` | Choose your activity presentation. |
| `/tracker-style-server` | Admin: set server presentation defaults. |
| `/tracker-setchannel` | Admin: choose the activity channel. |
| `/tracker-features` | Admin: configure optional features, including activity-only mode and Watched Together. |
| `/tracker-timezone` | Admin: view or set the server timezone. |
| `/tracker-user-reset` | Reset this server's imported tracking state and achievements while retaining account links, global XP and personal style; history is quietly reimported.  |
| `/tracker-debug` | Admin: privately preview level, rank, achievement and prestige notifications without changing XP.  |

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `POLL_INTERVAL_MINUTES` | `60` | Scheduled polling interval for all providers. |
| `POLL_CONCURRENCY` | `5` | Concurrent SIMKL user checks. |
| `HISTORY_BACKFILL_CONCURRENCY` | `2` | Concurrent SIMKL history imports. |
| `SIMKL_DEFAULT_TIMEZONE` | `Asia/Singapore` | Default statistics/streak timezone; override with `/tracker-timezone`. |
| `DISCORD_DEV_GUILD_ID` | Unset | Optional immediate development-server command sync. |
| `IMDB_RATINGS_DB_PATH` | `data/imdb_ratings.db` | Generated episode ratings database location. |

## Troubleshooting

- **No activity:** confirm the selected source, linked account, posting channel and permissions. The first history import is intentionally quiet.
- **Missing ratings or artwork:** metadata may be unavailable, especially individual episode ratings. The bot does not substitute a show's score for an episode score.
- **Provider errors or quota delays:** inspect `/tracker-status`. Account backoff avoids repeatedly hitting provider limits.
- **No active SIMKL targets:** expected when members select WeTrakr or MDBList.
- **Missing slash commands:** verify the invitation includes `applications.commands`. Global command registration can take time; the optional development guild setting supports immediate server sync.

## Development

| Directory | Contents |
| --- | --- |
| `trackerbot/core/` | Storage, mapping, watch delivery and progression. |
| `trackerbot/integrations/` | Provider adapters, authentication and sync clients. |
| `trackerbot/metadata/` | Catalog, artwork and ratings resolution. |
| `trackerbot/presentation/` | Embeds, cards and member pagination. |
| `trackerbot/validation/` | Read-only live audit tooling. |
| `tests/` | Automated coverage. |
| `docs/` | Architecture, provider integration and live checks. |

The root `bot.py` is the launch entry point. For provider development and validation:

- [Multi-tracker architecture](docs/multi-tracker-design.md)
- [Adding a provider](docs/adding-provider.md)
- [MDBList integration and limitations](docs/mdblist-integration.md)
- [WeTrakr API review](docs/wetrakr-api-review.md)
- [Live validation checklist](docs/testing-multi-tracker.md)

Additional providers require an adapter, authentication and API-specific validation. Capabilities depend on what each provider exposes.

## License

[MIT](LICENSE)
