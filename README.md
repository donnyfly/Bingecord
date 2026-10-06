# SIMKLTrackerBot

A self-hosted Discord bot that turns your **SIMKL, WeTrakr, or MDBList watch activity** into clean Discord updates for **TV shows, anime, and movies**.

It supports automatic provider syncing, episode grouping, artwork and ratings, personal/server statistics, achievements, XP progression, challenges, leaderboards, and optional weekly recaps. All linked-account data is stored locally on your own server.

> **Multi-tracker preview:** these changes are currently on `experimental`. Use the `:experimental` image below to try them. The published stable `:latest` image may not include the new providers yet.

## What you get

- 🎬 Automatic SIMKL, WeTrakr and MDBList activity tracking for TV, anime, and movies
- 👥 Watched Together combines matching watches from the same polling cycle, including mixed-provider groups
- 📺 Consecutive episode grouping for cleaner Discord posts
- 🖼️ Title logos, episode stills and TMDB artwork with available provider fallbacks
- ⭐ IMDb ratings and 🌸 MyAnimeList ratings where available
- 🎨 Rich/minimal embeds, artwork choices, and short/detailed activity text
- 🔗 Per-member, per-server source selection with multiple linked accounts
- 📊 Personal and server watch statistics
- 🎲 Watching lists, planned-list random picks, and recommendations
- 🧩 Cross-provider watch mapping and shared XP duplicate protection
- 🔥 Watch streaks
- 🏆 Server leaderboards
- 📈 XP, levels, ranks, and prestige
- 🎯 Daily and weekly watch challenges
- 🏅 Achievements with XP rewards
- 📅 Automatic weekly recaps
- ⚙️ Server-wide feature switches for communities that only want basic activity tracking
- 🏠 Multi-server support
- 💾 Local persistent storage
- 🐳 Docker support with a pre-built image

## Commands

| Command | Who can use it | Purpose |
| --- | --- | --- |
| `/tracker-link` | Everyone | Link a SIMKL, WeTrakr or MDBList account |
| `/tracker-source` | Everyone | Select the linked account used in this server |
| `/tracker-mapping` | Everyone | Privately inspect cross-provider matches and possible duplicate awards |
| `/tracker-unlink` | Everyone | Unlink a provider account |
| `/tracker-stats` | Everyone | View your watch/progression profile |
| `/tracker-achievements` | Everyone | View achievements and XP rewards |
| `/tracker-challenges` | Everyone | View daily and weekly challenges |
| `/tracker-leaderboard` | Everyone | View server leaderboards |
| `/tracker-server-stats` | Everyone | View server watch statistics |
| `/tracker-community` | Everyone | View the rotating weekly cooperative watch goal |
| `/tracker-watching` | Everyone | View your selected account's watching list and next episodes where available |
| `/tracker-random` | Everyone | Pick from your selected account's planned list |
| `/tracker-recommend` | Everyone | Get recommendations using selected-source history and exclusions |
| `/tracker-style` | Everyone | Change personal activity-post preferences |
| `/tracker-user-reset` | Everyone | Reset your tracking history for the current server |
| `/tracker-setchannel` | Manage Server | Choose the activity channel |
| `/tracker-style-server` | Manage Server | Set server-wide style defaults |
| `/tracker-features` | Manage Server | Enable or disable optional feature groups |
| `/tracker-timezone` | Manage Server | Set the server timezone |
| `/tracker-weekly-recap` | Manage Server | Post/test a weekly recap |
| `/tracker-status` | Manage Server | View configuration and sync health; optionally check `user: @username` |
| `/tracker-checknow` | Manage Server | Check selected providers immediately |
| `/tracker-debug` | Manage Server | Preview progression notifications without changing XP |

### Prefer the simple tracker experience?

Admins can use:

```text
/tracker-features preset: Activity only
```

This keeps the core activity tracker while disabling optional progression, achievements, challenges, recaps, community goals, leaderboards, and similar extras.

**Watched Together:** matching movies, episodes, or identical episode ranges in the same channel combine when each watch timestamp is within 30 minutes of the others. Mentions do not ping. Everyone keeps their own XP and history. Matching requires a shared title identity and the same movie or episode/range in the same server. Partial-overlap ranges remain separate. Group descriptions display up to five member mentions. Admins can toggle it with `/tracker-features feature: Watched Together enabled: False`. Watches beginning at S1E1, including ranges starting there, include “🆕 Started watching this series.” on the last line after the ratings and a blank line.


Use:

```text
/tracker-features preset: All features
```

to restore the full experience.

---

# Installation

## Recommended: Docker Compose

### Requirements

You will need:

- A machine that can run Docker
- A Discord bot token
- A SIMKL Client ID if you want SIMKL tracking
- A TMDB API key
- A WeTrakr application key if you want WeTrakr tracking
- An MDBList OAuth Client ID if you want MDBList tracking
- An optional MDBList API key for additional ratings

### 1. Create a Discord bot

Create an application in the [Discord Developer Portal](https://discord.com/developers/applications), add a bot, and copy its token.

When inviting it to your server, include:

- `bot`
- `applications.commands`

Allow **View Channel, Send Messages, Embed Links, Attach Files and Read Message History** in the channel you choose.

### 2. Enable SIMKL tracking (optional)

Create an application in the SIMKL developer settings and copy its **Client ID**.

Individual Discord users will connect their own SIMKL accounts later with `/tracker-link provider: SIMKL`.

Leave `SIMKL_CLIENT_ID` blank to disable SIMKL. It is not required for WeTrakr or MDBList. Each provider is enabled independently by its own host credentials.

### 3. Get a TMDB API key

TMDB is used for artwork and media metadata.

The optional `MDBLIST_API_KEY` supplies additional IMDb/MyAnimeList ratings. It is separate from MDBList tracking authorization.

### 4. Enable additional tracking providers (optional)

#### WeTrakr

1. Register or obtain a WeTrakr application key through its developer/API access process. See the [official API documentation](https://api.wetrakr.com/#/authentication) for authentication requirements.
2. Set `WETRAKR_API_KEY` in the host's `.env`. This is the application's client ID, sent as the WeTrakr API key; it is not a member's account token.
3. Start or recreate the bot. Each member runs `/tracker-link provider: WeTrakr`, follows the private authorization instructions, and approves with their own WeTrakr account.
4. Select `/tracker-source provider: WeTrakr`.

The host configures the application once. Members do not need individual API keys. Private account data uses their separate OAuth tokens.

#### MDBList

1. Register your own OAuth application at [MDBList Developer](https://mdblist.com/developer/), with device authorization available and read access for the bot.
2. Set `MDBLIST_CLIENT_ID` in `.env`. Set `MDBLIST_CLIENT_SECRET` only if your registered application requires it for refresh.
3. Start or recreate the bot. Each member runs `/tracker-link provider: MDBList` and approves the device authorization with their own MDBList account.
4. Select `/tracker-source provider: MDBList`.

`MDBLIST_CLIENT_ID` enables **tracking**. `MDBLIST_API_KEY` enables optional **ratings enrichment**. They serve different purposes; the ratings key does not replace member OAuth authorization. See [MDBList integration details](docs/mdblist-integration.md) for provider limitations and validation.

Enable any combination of SIMKL, WeTrakr and MDBList. None is the primary provider; leave unused provider credentials blank.

### 5. Create the Docker setup

```bash
mkdir -p ~/simkl-discord-bot
cd ~/simkl-discord-bot
```

Create `docker-compose.yml`:

```yaml
services:
  simkltrackerbot:
    image: ghcr.io/donnyfly/simkltrackerbot:experimental
    container_name: simkltrackerbot
    restart: unless-stopped
    env_file:
      - .env
    volumes:
      - ./data:/app/data
```

Create `.env`:

```env
DISCORD_BOT_TOKEN=your_discord_bot_token_here
TMDB_API_KEY=your_tmdb_api_key_here
# Optional tracking providers (configure only those you use):
SIMKL_CLIENT_ID=
WETRAKR_API_KEY=
MDBLIST_CLIENT_ID=
MDBLIST_CLIENT_SECRET=

# Optional additional ratings:
MDBLIST_API_KEY=

SIMKL_DEFAULT_TIMEZONE=Asia/Singapore

POLL_INTERVAL_MINUTES=60
POLL_CONCURRENCY=5
HISTORY_BACKFILL_CONCURRENCY=2
```

Start the bot:

```bash
docker compose up -d
```

Check it:

```bash
docker compose ps
docker compose logs -f
```

Once the bot is online:

1. Run `/tracker-setchannel` to choose where activity should be posted.
2. Each member runs `/tracker-link` for their provider, then `/tracker-source` to select it.
3. An admin runs `/tracker-checknow` to import the initial history quietly.
4. Mark a new watch after that import. Wait for scheduled polling or run `/tracker-checknow` again.
5. Use `/tracker-stats` and `/tracker-status` to verify the selected account and sync health.

---

## Running with Python

Docker is recommended, but the bot can also run directly with Python 3.13, matching the Docker image.

```bash
git clone --branch experimental https://github.com/donnyfly/SIMKLTrackerBot.git
cd SIMKLTrackerBot

python -m venv venv
```

Activate the environment:

**Linux/macOS**

```bash
source venv/bin/activate
```

**Windows PowerShell**

```powershell
.\venv\Scripts\Activate.ps1
```

Check that `python --version` reports the intended Python installation before creating the environment.

Then:

```bash
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env`, fill in your credentials, and start:

```bash
python bot.py
```

---

# Configuration

The main settings live in `.env`. Use `.env.example` as the complete template; leave optional credentials blank until configured.

| Setting | Required | Default | Purpose |
| --- | --- | --- | --- |
| `DISCORD_BOT_TOKEN` | Yes | — | Discord bot token |
| `SIMKL_CLIENT_ID` | For SIMKL | — | Host application ID enabling SIMKL tracking |
| `TMDB_API_KEY` | Yes | — | Artwork and media metadata |
| `WETRAKR_API_KEY` | For WeTrakr | — | Host application key for WeTrakr tracking |
| `MDBLIST_CLIENT_ID` | For MDBList tracking | — | Host OAuth application ID |
| `MDBLIST_CLIENT_SECRET` | Application-dependent | — | MDBList token refresh secret, if required |
| `MDBLIST_API_KEY` | No | — | Additional IMDb/MAL ratings; separate from tracking |
| `POLL_INTERVAL_MINUTES` | No | `60` | Polling interval in minutes for all selected providers |
| `POLL_CONCURRENCY` | No | `5` | Concurrent SIMKL user checks |
| `HISTORY_BACKFILL_CONCURRENCY` | No | `2` | Limits simultaneous SIMKL history imports |
| `SIMKL_DEFAULT_TIMEZONE` | No | `Asia/Singapore` | Default timezone for all providers; legacy variable name |
| `DISCORD_DEV_GUILD_ID` | No | — | Optional immediate development-server command sync |
| `IMDB_RATINGS_DB_PATH` | No | `data/imdb_ratings.db` | Generated episode-rating database path |

Server admins can override the timezone with `/tracker-timezone`.

## Activity customization

Users can run `/tracker-style` to choose:

- **Rich** or **Minimal** embeds
- Automatic, poster, or backdrop artwork
- Short or detailed activity text
- Rating visibility

Server admins can set defaults with `/tracker-style-server`. Personal settings override server defaults.

Titles and activity headers link to the provider entry and member profile when available. Status activities use posters. Episode watches can use episode stills/backdrops and show individual episode IMDb ratings. They omit MAL and do not substitute the whole show's rating for a missing episode rating. Anime movies and anime status activities can show title-level IMDb and MAL scores.

For MDBList, supported playback changes can produce Paused notices. Ended/cancelled shows use Completed; ongoing shows use Caught up with. Available notices depend on each provider's API.

---

# Progression & statistics

When enabled, the bot adds a progression layer on top of normal watch tracking.

- Episodes award watch XP
- Movies award watch XP
- Achievements and challenges can award bonus XP
- Users progress through levels, ranks, and prestige tiers
- `/tracker-stats` shows watch history, XP, streaks, achievements, recent activity, and more
- `/tracker-leaderboard` compares server members across watch/progression categories
- Weekly recaps summarize recent server activity

These systems are optional. Servers that only want activity posts can use the **Activity only** feature preset.

## Multiple providers, one progression

Each member selects one source **per server**. The selected account supplies watch totals, history, watching/planned lists and recommendation exclusions. Servers can combine members using different providers.

XP, levels, ranks and prestige remain shared for the Discord user. Initial imports and source-switch catch-up do not post old watches or replay progression animations. Switching itself does not remove XP.

A shared watch ledger matches confirmed cross-provider occurrences using catalog IDs and episode identities. The same matched watch earns XP once; genuine rewatches remain separate. Uncertain matches remain separate for review. A watch removal reverses its contribution only when no other imported provider observation supports it; inactive-account changes become known when that source syncs again.

`/tracker-mapping` privately shows matches, unpaired watches and possible duplicate awards without changing XP. `/tracker-user-reset` resets this server's imported tracking state and achievements while retaining links, global XP and personal style; subsequent history import is quiet.

---

# Updating

## Docker Compose

```bash
cd ~/simkl-discord-bot
docker compose pull
docker compose up -d
```

## Python

```bash
git pull
python -m pip install -r requirements.txt
```

Then restart the bot.

Keep your existing data directory when upgrading from SIMKL-only releases. Back it up before a migration; do not unlink accounts or delete state as a routine update step. The unified commands use `/tracker-*` names. After editing `.env`, recreate Docker containers with `docker compose up -d --force-recreate` to load the new values.

When this branch is promoted and a stable image containing multi-tracker support is published, switch the image tag to `:latest` and remove `--branch experimental` from new source installations.

---

# Data & backups

Persistent data is stored in the mounted `data` directory.

The important file to back up is:

```text
data/store.json
```

It contains bot state and linked-account authentication information, so **keep it private**.

The IMDb ratings database can be rebuilt automatically and does not normally need to be backed up.

---

# Troubleshooting

### Bot is online but does not post activity

Check that:

1. You linked your account with `/tracker-link` and selected it with `/tracker-source`.
2. An activity channel is configured with `/tracker-setchannel`.
3. The bot can send messages in that channel.
4. The initial quiet import has finished and there is new provider activity to post.

Admins can run `/tracker-checknow` and `/tracker-status` for an immediate check. Use `/tracker-status user: @username` to inspect one member; server results are paginated in groups of five.

A “no active SIMKL targets” log is normal when everyone selects another provider. Provider quota delays and authorization errors appear in sync health. WeTrakr journal entries can take several seconds to become visible; a check immediately after marking a watch may return zero, with the next poll picking it up.

### Ratings or artwork are missing

Availability depends on provider/catalog metadata. Individual episode IMDb scores may be missing even when the series has a rating. The bot keeps source episode coordinates for metadata lookups separately from mapped anime display numbering.

### Slash commands are missing

Make sure the bot was invited with both the `bot` and `applications.commands` scopes.

Discord global command changes can also take some time to appear.

### Container keeps restarting

```bash
docker compose ps
docker compose logs --tail=100
```

Most startup/configuration problems will be shown in the logs.

---

# Security

Never publicly share or commit:

- `.env`
- Discord bot tokens
- Host application keys and client secrets
- SIMKL, WeTrakr and MDBList account tokens
- `data/store.json`

If a Discord bot token is exposed, regenerate it immediately in the Discord Developer Portal.

---

# Development & validation

The root `bot.py` launches the application. Source lives in `trackerbot/`, organized into shared `core/`, provider `integrations/`, `metadata/`, `presentation/` and read-only `validation/` modules. Automated coverage lives in `tests/`.

- [Multi-tracker design and mapping](docs/multi-tracker-design.md)
- [Adding a provider](docs/adding-provider.md)
- [MDBList integration](docs/mdblist-integration.md)
- [WeTrakr API review](docs/wetrakr-api-review.md)
- [Live validation checklist](docs/testing-multi-tracker.md)

The shared interface supports adding providers, but each adapter still needs authentication, API-specific reconciliation and capability checks. Complete the outstanding live checks before promoting multi-tracker support as full production parity.

---

# License

SIMKLTrackerBot is licensed under the MIT License.
