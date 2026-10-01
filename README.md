# SIMKLTrackerBot

A self-hosted Discord bot that turns your **SIMKL watch activity** into clean Discord updates for **TV shows, anime, and movies**.

It supports automatic SIMKL syncing, episode grouping, artwork and ratings, personal/server statistics, achievements, XP progression, challenges, leaderboards, and optional weekly recaps. All linked-account data is stored locally on your own server.

<p>
  <img width="400" alt="SIMKLTrackerBot activity example" src="https://github.com/user-attachments/assets/62b58b48-a792-4b67-99df-6741f9d742b4" />
  <img width="400" alt="SIMKLTrackerBot activity example" src="https://github.com/user-attachments/assets/82ffdb47-6a17-4cfb-96bc-a6ae2fa1c929" />
</p>
<p>
  <img width="400" alt="SIMKLTrackerBot activity example" src="https://github.com/user-attachments/assets/c51ab135-a55f-4b6f-b711-01d4b9171095" />
  <img width="400" alt="SIMKLTrackerBot activity example" src="https://github.com/user-attachments/assets/e3c04ba3-47b1-4026-987b-87161fc6ac7b" />
</p>

## What you get

- 🎬 Automatic SIMKL activity tracking for TV, anime, and movies
- 👥 Watched Together combines matching watches from the same polling cycle
- 📺 Consecutive episode grouping for cleaner Discord posts
- 🖼️ TMDB artwork with SIMKL fallback
- ⭐ IMDb ratings and 🌸 MyAnimeList ratings where available
- 🎨 Rich/minimal embeds, artwork choices, and short/detailed activity text
- 📊 Personal and server watch statistics
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

On the experimental branch, link either service with `/tracker-link` and
select its provider option. `/tracker-source` chooses SIMKL or WeTrakr
activity per user and server. `/tracker-checknow` checks both selected
sources. See
[`docs/WETRAKR_PROTOTYPE.md`](docs/WETRAKR_PROTOTYPE.md) for setup, the
silent first sync, and cross-provider matching limits.

| Command | Who can use it | Purpose |
| --- | --- | --- |
| `/tracker-link` | Everyone | Link your SIMKL or WeTrakr account (choose provider) |
| `/tracker-unlink` | Everyone | Unlink one provider (choose provider) |
| `/tracker-source` | Everyone | Choose the activity source for this server |
| `/tracker-stats` | Everyone | View your watch/progression profile |
| `/tracker-mapping` | Everyone | Privately inspect cross-provider watch matches and possible duplicate awards |
| `/tracker-achievements` | Everyone | View achievements and XP rewards |
| `/tracker-challenges` | Everyone | View daily and weekly challenges |
| `/tracker-leaderboard` | Everyone | View server leaderboards |
| `/tracker-server-stats` | Everyone | View server watch statistics |
| `/tracker-community` | Everyone | View the rotating weekly cooperative watch goal |
| `/tracker-watching` | Everyone | View your active tracker's watching list |
| `/tracker-random` | Everyone | Pick from your active tracker's plan-to-watch list |
| `/tracker-recommend` | Everyone | Get recommendations based on your active tracker's history |
| `/tracker-style` | Everyone | Change personal activity-post preferences |
| `/tracker-user-reset` | Everyone | Reset your tracking history for the current server |
| `/tracker-setchannel` | Manage Server | Choose the activity channel |
| `/tracker-style-server` | Manage Server | Set server-wide style defaults |
| `/tracker-features` | Manage Server | Enable or disable optional feature groups |
| `/tracker-timezone` | Manage Server | Set the server timezone |
| `/tracker-weekly-recap` | Manage Server | Post/test a weekly recap |
| `/tracker-status` | Manage Server | View configuration and sync health |
| `/tracker-checknow` | Manage Server | Check selected SIMKL and WeTrakr accounts immediately |
| `/tracker-debug` | Manage Server | Preview progression notifications without changing XP |

WeTrakr compact history now seeds watches and progression without sending old
activity posts. Journal additions, date edits and removals update statistics
and XP. Challenges, achievements, recaps and leaderboards use the shared
progression state. The discovery commands read the selected provider.
Cross-provider matching prefers shared external IDs and episode coordinates;
older XP records without IDs can still use a title fallback. The mapping
preview never changes XP. See the [multi-tracker design](docs/multi-tracker-design.md)
and [provider integration contract](docs/adding-provider.md) for remaining
limitations and the parity requirements for future services.

### Prefer the simple tracker experience?

Admins can use:

```text
/tracker-features preset: Activity only
```

This keeps the core SIMKL activity tracker while disabling optional progression, achievements, challenges, recaps, community goals, leaderboards, and similar extras.

**Watched Together:** matching movies, episodes, or identical episode ranges in the same channel combine when each watch timestamp is within 30 minutes of the others. Mentions do not ping. Everyone keeps their own XP and history. Admins can toggle it with `/tracker-features feature: Watched Together enabled: False`. First watches include “🆕 Started watching this series.” inside the watch post; standalone Started Watching posts are omitted.


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
- A SIMKL Client ID
- A TMDB API key
- An optional MDBList API key for additional ratings

### 1. Create a Discord bot

Create an application in the [Discord Developer Portal](https://discord.com/developers/applications), add a bot, and copy its token.

When inviting it to your server, include:

- `bot`
- `applications.commands`

The bot needs permission to send messages and read message history in the channel you choose.

### 2. Create a SIMKL application

Create an application in the SIMKL developer settings and copy its **Client ID**.

Individual Discord users will connect their own SIMKL accounts later with `/tracker-link`.

### 3. Get a TMDB API key

TMDB is used for artwork and media metadata.

MDBList is optional and is used for additional IMDb/MyAnimeList ratings.

### 4. Create the Docker setup

```bash
mkdir -p ~/tracker-discord-bot
cd ~/tracker-discord-bot
```

Create `docker-compose.yml`:

```yaml
services:
  simkltrackerbot:
    image: ghcr.io/donnyfly/simkltrackerbot:latest
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
SIMKL_CLIENT_ID=your_simkl_client_id_here
TMDB_API_KEY=your_tmdb_api_key_here
MDBLIST_API_KEY=your_mdblist_api_key_here

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
2. Run `/tracker-link` to connect your SIMKL account.
3. Watch something and let the bot handle the rest.

---

## Running with Python

Docker is recommended, but the bot can also run directly with Python 3.10+.

Application code is grouped under `trackerbot/`: `core/` contains shared
progression, storage, mapping, and delivery logic; `integrations/` contains
tracker clients and sync; `metadata/` contains title/rating clients; and
`presentation/` contains cards and recommendation UI. The root `bot.py` keeps
the existing launch command working.

```bash
git clone https://github.com/donnyfly/SIMKLTrackerBot.git
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

The main settings live in `.env`.

| Setting | Required | Default | Purpose |
| --- | --- | --- | --- |
| `DISCORD_BOT_TOKEN` | Yes | — | Discord bot token |
| `SIMKL_CLIENT_ID` | Yes | — | SIMKL application Client ID |
| `TMDB_API_KEY` | Yes | — | Artwork and media metadata |
| `MDBLIST_API_KEY` | No | — | Additional IMDb/MAL ratings |
| `POLL_INTERVAL_MINUTES` | No | `60` | How often SIMKL is checked |
| `POLL_CONCURRENCY` | No | `5` | Number of users processed together |
| `HISTORY_BACKFILL_CONCURRENCY` | No | `2` | Limits simultaneous first-time history imports |
| `SIMKL_DEFAULT_TIMEZONE` | No | `Asia/Singapore` | Default timezone for statistics and streaks |

Server admins can override the timezone with `/tracker-timezone`.

## Activity customization

Users can run `/tracker-style` to choose:

- **Rich** or **Minimal** embeds
- Automatic, poster, or backdrop artwork
- Short or detailed activity text
- Rating visibility

Server admins can set defaults with `/tracker-style-server`. Personal settings override server defaults.

---

# Progression & statistics

When enabled, the bot adds a progression layer on top of normal SIMKL tracking.

- Episodes award watch XP
- Movies award watch XP
- Achievements and challenges can award bonus XP
- Users progress through levels, ranks, and prestige tiers
- `/tracker-stats` shows watch history, XP, streaks, achievements, recent activity, and more
- `/tracker-leaderboard` compares server members across watch/progression categories
- Weekly recaps summarize recent server activity

These systems are optional. Servers that only want SIMKL activity posts can use the **Activity only** feature preset.

---

# Updating

## Docker Compose

```bash
cd ~/tracker-discord-bot
docker compose pull
docker compose up -d
```

## Python

```bash
git pull
python -m pip install -r requirements.txt
```

Then restart the bot.

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

1. You linked your account with `/tracker-link`.
2. An activity channel is configured with `/tracker-setchannel`.
3. The bot can send messages in that channel.
4. The bot has detected new SIMKL activity.

Admins can run `/tracker-checknow` and `/tracker-status` for an immediate check.

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
- SIMKL authentication tokens
- `data/store.json`

If a Discord bot token is exposed, regenerate it immediately in the Discord Developer Portal.

---

# License

SIMKLTrackerBot is licensed under the MIT License.

Live validation for experimental changes: [multi-tracker test checklist](docs/testing-multi-tracker.md).
