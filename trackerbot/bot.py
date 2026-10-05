import asyncio, logging, os, random, time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from urllib.parse import quote, urlencode
import discord
from discord import app_commands
from dotenv import load_dotenv
from trackerbot.integrations.simkl_client import SimklAuthError, SimklClient, SimklSlowDown
from trackerbot.integrations.wetrakr_client import WeTrakrClient, WeTrakrError, page_rows
from trackerbot.integrations.wetrakr_auth import WeTrakrAuth
from trackerbot.integrations.wetrakr_sync import WeTrakrSync
from trackerbot.integrations.mdblist_tracking_client import MDBListTrackingClient, MDBListTrackingError
from trackerbot.integrations.mdblist_provider import MDBListProvider
from trackerbot.core.storage import DEFAULT_FEATURES, EPOCH_ISO, storage
from trackerbot.core.watch_delivery import WatchActivity, WatchBatch
from trackerbot.core.achievements import ACHIEVEMENTS, all_achievements
from trackerbot.core.progression import RANKS, challenges_for, challenge_progress, level_progress, rank_for_level, xp_for_level, xp_for_watch
from trackerbot.presentation.level_visuals import accent_for_tier, prestige_style, render_achievement_gif, render_level_up_gif, render_prestige_gif
from trackerbot.presentation.profile_visuals import profile_snapshot, render_profile_png, render_leaderboard_png, render_summary_png
from trackerbot.core.community import CHALLENGES, community_week
from trackerbot.metadata.tmdb_client import TmdbClient
from trackerbot.metadata.mdblist_client import MdbListClient
from trackerbot.metadata.imdb_client import ImdbClient
from trackerbot.presentation.recommendation_engine import rating_value, select_sources, source_weight, rank_candidates, recommendation_lineup
from trackerbot.presentation.recommendation_ui import RecommendationView
from trackerbot.presentation.member_pages import MemberPages, PAGE_SIZE
from trackerbot.core.providers import BUILTIN_TRACKERS, ProviderRegistry
from trackerbot.integrations.provider_adapters import SimklProvider, WeTrakrProvider, matches_filter

load_dotenv()

def positive_int_env(name, default, minimum=1):
    raw=os.getenv(name, str(default)).strip()
    try:
        value=int(raw)
    except ValueError:
        raise SystemExit(f"Invalid {name}={raw!r}. It must be an integer of at least {minimum}.")
    if value < minimum:
        raise SystemExit(f"Invalid {name}={value}. It must be at least {minimum}.")
    return value

DISCORD_BOT_TOKEN=os.getenv("DISCORD_BOT_TOKEN")
SIMKL_CLIENT_ID=os.getenv("SIMKL_CLIENT_ID")
WETRAKR_API_KEY=os.getenv("WETRAKR_API_KEY", "").strip()
TMDB_API_KEY=os.getenv("TMDB_API_KEY")
MDBLIST_API_KEY=os.getenv("MDBLIST_API_KEY")
POLL_INTERVAL_MINUTES=positive_int_env("POLL_INTERVAL_MINUTES", 60)
DEFAULT_TIMEZONE_NAME=os.getenv("SIMKL_DEFAULT_TIMEZONE", "Asia/Singapore").strip() or "Asia/Singapore"
try:
    ZoneInfo(DEFAULT_TIMEZONE_NAME)
except ZoneInfoNotFoundError:
    log_placeholder = True
    DEFAULT_TIMEZONE_NAME = "UTC"
POLL_CONCURRENCY=positive_int_env("POLL_CONCURRENCY", 5)
# After this many consecutive failed polls, the automatic background poll stops
# calling SIMKL for that guild-user until a manual /tracker-checknow succeeds or
# the user relinks. This avoids burning an API call every cycle on a revoked token.
MAX_CONSECUTIVE_FAILURES=positive_int_env("MAX_CONSECUTIVE_FAILURES", 5)
HISTORY_BACKFILL_CONCURRENCY=positive_int_env("HISTORY_BACKFILL_CONCURRENCY", 2)
history_backfill_semaphore=asyncio.Semaphore(HISTORY_BACKFILL_CONCURRENCY)
if not DISCORD_BOT_TOKEN or not SIMKL_CLIENT_ID:
    raise SystemExit("Missing DISCORD_BOT_TOKEN or SIMKL_CLIENT_ID.")
if not TMDB_API_KEY:
    raise SystemExit("Missing TMDB_API_KEY.")

MEDIA_TYPES=("shows","anime","movies"); ACTIVITY_KEYS={"shows":"tv_shows","anime":"anime","movies":"movies"}
WATCHLIST_STATUSES=("watching","plantowatch","completed","dropped")
STATUS_TEXT={"watching":"Started watching","plantowatch":"Planned to watch","completed":"Completed","dropped":"Dropped"}
MEDIA_STYLES={"shows":(0x3498DB,"📺 TV"),"anime":(0xE91E63,"🌸 Anime"),"movies":(0xF1C40F,"🎬 Movie")}
HISTORY_FETCH_TIMEOUT_SECONDS=120; CHECKNOW_COOLDOWN_SECONDS=30
poll_lock=asyncio.Lock(); last_checknow_at=0.0; linking_users=set(); profile_lookup_attempted=set()
wetrakr_poll_lock=asyncio.Lock()
logging.basicConfig(level=logging.INFO,format="%(asctime)s [%(levelname)s] %(message)s"); log=logging.getLogger("tracker-bot")
if DEFAULT_TIMEZONE_NAME == "UTC" and os.getenv("SIMKL_DEFAULT_TIMEZONE"):
    log.warning("Invalid SIMKL_DEFAULT_TIMEZONE=%r; falling back to UTC.", os.getenv("SIMKL_DEFAULT_TIMEZONE"))
simkl=SimklClient(SIMKL_CLIENT_ID); tmdb=TmdbClient(TMDB_API_KEY); mdblist=MdbListClient(MDBLIST_API_KEY) if MDBLIST_API_KEY else None; imdb=ImdbClient()
wetrakr=WeTrakrClient(WETRAKR_API_KEY) if WETRAKR_API_KEY else None
wetrakr_sync=WeTrakrSync(wetrakr, WeTrakrAuth(wetrakr,storage),storage) if wetrakr else None
MDBLIST_CLIENT_ID=os.getenv("MDBLIST_CLIENT_ID", "").strip()
mdblist_tracking=MDBListTrackingClient(MDBLIST_CLIENT_ID,os.getenv("MDBLIST_CLIENT_SECRET") or None) if MDBLIST_CLIENT_ID else None
mdblist_auth=WeTrakrAuth(mdblist_tracking,storage,provider="mdblist") if mdblist_tracking else None
if mdblist is not None:
    log.info("MDBList IMDb ratings enabled.")
else:
    log.warning("MDBList IMDb ratings disabled: MDBLIST_API_KEY is not set.")

FEATURE_LABELS={"watched_together":"Watched Together", "progression":"Levels, ranks and prestige", "achievements":"Achievements",
                "challenges":"Daily and weekly challenges", "community":"Community challenges",
                "weekly_recaps":"Weekly recaps", "leaderboards":"Leaderboards",
                "statistics":"Statistics cards", "discovery":"Watching, random picks and recommendations"}
COMMAND_FEATURES={"tracker-challenges":"challenges","tracker-community":"community",
                  "tracker-achievements":"achievements","tracker-weekly-recap":"weekly_recaps",
                  "tracker-leaderboard":"leaderboards","tracker-stats":"statistics",
                  "tracker-server-stats":"statistics","tracker-watching":"discovery",
                  "tracker-random":"discovery","tracker-recommend":"discovery"}

async def feature_enabled(g, feature):
    features=await storage.get_features(g)
    return features[feature] and (feature not in {"challenges","community"} or features["progression"])

class FeatureCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction):
        name=(interaction.data or {}).get("name")
        feature=COMMAND_FEATURES.get(name)
        if feature and interaction.guild_id and not await feature_enabled(interaction.guild_id,feature):
            if interaction.type == discord.InteractionType.autocomplete:
                await interaction.response.autocomplete([])
                return False
            await interaction.response.send_message(
                "This feature is disabled in this server. An admin can enable it with `/tracker-features`.",ephemeral=True)
            return False
        return True

class SimklBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default()); self.tree=FeatureCommandTree(self)
    async def setup_hook(self):
        await self.tree.sync()

        dev_guild_id = os.getenv("DISCORD_DEV_GUILD_ID", "").strip()
        if dev_guild_id:
            try:
                dev_guild = discord.Object(id=int(dev_guild_id))
                self.tree.copy_global_to(guild=dev_guild)
                await self.tree.sync(guild=dev_guild)
                log.info("Slash commands synced to development guild %s.", dev_guild_id)
            except ValueError:
                log.warning("Invalid DISCORD_DEV_GUILD_ID=%r; expected a Discord guild ID.", dev_guild_id)

        await imdb.start()
        log.info("Slash commands synced globally.")
    async def close(self):
        poll_task = getattr(self, "_poll_task", None)
        if poll_task and not poll_task.done():
            log.info("Stopping polling task before shutdown.")
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("Polling task failed while shutting down.")

        try:
            await storage.flush()
        except Exception:
            log.exception("Failed to flush persistent storage during shutdown.")

        for client in (simkl,wetrakr,mdblist_tracking,tmdb,mdblist,imdb):
            if client is None:
                continue
            try:
                await client.close()
            except Exception:
                pass
        await super().close()
bot=SimklBot()

def to_iso(dt): return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
def now_iso(): return to_iso(datetime.now(timezone.utc))
def parse_iso(v):
    if not v: return datetime.min.replace(tzinfo=timezone.utc)
    try:
        d=datetime.fromisoformat(v.replace("Z","+00:00")); return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception: return datetime.min.replace(tzinfo=timezone.utc)
def calculate_token_expiry(v):
    try: s=int(v)
    except (TypeError,ValueError): return None
    return to_iso(datetime.now(timezone.utc)+timedelta(seconds=s)) if s>0 else None
def is_admin(i): return bool(i.guild and i.user.guild_permissions.manage_guild)
def guild_id(i): return i.guild.id if i.guild else None
def simkl_poster_url(p,size="_m"): return f"https://wsrv.nl/?url=https://simkl.in/posters/{p}{size}.webp&q=90" if p else None
def simkl_profile_url(i): return f"https://simkl.com/{i}/" if i else None
def account_id_from_settings(s): return (s.get("account") or {}).get("id") if isinstance(s,dict) else None
def simkl_title_url(t,i,slug=None):
    b={"movies":"https://simkl.com/movies","anime":"https://simkl.com/anime"}.get(t,"https://simkl.com/tv")
    return f"{b}/{i}/{slug}" if slug else f"{b}/{i}"

def wetrakr_profile_url(link):
    link=link or {}
    username=str(link.get("username") or "").strip()
    if username and username != "WeTrakr user":
        return f"https://wetrakr.com/{quote(username,safe='')}"
    return "https://wetrakr.com/profile"

def wetrakr_title_url(kind,tmdb_id):
    try:
        tmdb_id=int(tmdb_id)
    except (TypeError,ValueError):
        return None
    target="movie" if kind in {"movie","movies","anime_movie"} else "show"
    return f"https://wetrakr.com/tmdb/{target}/{tmdb_id}"

def simkl_redirect_url(tmdb_id,kind,title=None,year=None):
    """Build a direct Simkl redirect URL from a TMDB ID."""
    params={"to":"Simkl","tmdb":int(tmdb_id),"type":"movie" if kind=="movie" else "show"}
    if title:
        params["title"]=str(title)
    if year:
        try:
            params["year"]=int(year)
        except (TypeError,ValueError):
            pass
    return "https://api.simkl.com/redirect?" + urlencode(params)
def episode_key(t,i,s,e): return f"{t}:{i}:{s}:{e}"
def movie_key(t,i): return f"{t}:{i}"

async def resolve_anime_tmdb_id(ids):
    """Resolve an anime season entry to the canonical TMDB TV series."""
    ids = ids or {}
    tvdb_id = ids.get("tvdb")
    if tvdb_id is not None:
        resolved = await tmdb.find_series_by_tvdb(tvdb_id)
        if resolved is not None:
            return resolved
    return ids.get("tmdb")


async def is_anime_movie_item(item):
    """Return True only when SIMKL identifies an anime item as a movie."""

    if item.get("movie") is not None:
        return True

    show = item.get("show") or {}
    # SIMKL puts anime_type on the list entry, not necessarily on its show.
    # With include_all_episodes=yes a film can also have a virtual S01E01;
    # the explicit movie type must take precedence over episode rows.
    if item.get("anime_type") == "movie" or show.get("type") == "movie" or show.get("anime_type") == "movie":
        log.debug(
            "Classified anime item as movie: %s (SIMKL metadata).",
            show.get("title") or "Untitled",
        )
        return True

    # Explicit TV/anime-series metadata and episode/season data must always
    # win over third-party ID lookups. TMDB IDs can collide across media
    # types; never classify a series as a movie just because TMDB has a movie
    # record for the same numeric ID.
    if item.get("anime_type") in {"tv", "special", "ova", "ona", "music video"} or show.get("type") in {"tv", "show"} or show.get("anime_type") in {
        "tv", "special", "ova", "ona", "music video"
    }:
        return False
    if item.get("seasons") or item.get("episodes"):
        return False

    # If SIMKL provides a TVDB ID, use it only to confirm that the item maps
    # to a TV series. This is safe against TMDB movie-ID collisions.
    tvdb_id=(show.get("ids") or {}).get("tvdb")
    if tvdb_id is not None:
        try:
            series_id=await tmdb.find_series_by_tvdb(tvdb_id)
            if series_id is not None:
                return False
        except Exception:
            log.warning(
                "TVDB anime media classification failed for %s (TVDB=%s).",
                show.get("title") or "Untitled",
                tvdb_id,
                exc_info=True,
            )

    # With no explicit series evidence, verify the TMDB record itself. Anime
    # movies commonly arrive from SIMKL's anime endpoint as a show object
    # without seasons, so the TMDB movie record is the useful fallback here.
    tmdb_id=(show.get("ids") or {}).get("tmdb")
    if tmdb_id is None:
        return False

    try:
        movie_title=await tmdb.get_movie_title(tmdb_id)
    except Exception:
        log.warning(
            "TMDB anime media classification failed for %s (TMDB=%s).",
            show.get("title") or "Untitled",
            tmdb_id,
            exc_info=True,
        )
        return False

    if movie_title:
        log.debug(
            "Classified anime item as movie: %s (TMDB=%s).",
            show.get("title") or "Untitled",
            tmdb_id,
        )
        return True

    return False


async def split_anime_items(items):
    """Separate anime TV entries from anime movies for notification processing."""

    shows=[]
    movies=[]
    for item in items or []:
        if await is_anime_movie_item(item):
            movie_item=dict(item)
            movie_item["movie"]=dict(item.get("movie") or item.get("show") or {})
            movie_item["movie"]["type"]="movie"
            movies.append(movie_item)
        else:
            shows.append(item)
    return shows,movies


async def cached_split_anime_items(uid, items, request_cache=None):
    if request_cache is None:
        return await split_anime_items(items)
    key=("anime-classification",uid,id(items))
    if key not in request_cache:
        request_cache[key]=await split_anime_items(items)
    return request_cache[key]

def iter_show_episodes(t,items):
    for item in items or []:
        show=item.get("show") or {}; ids=show.get("ids") or {}; sid=ids.get("simkl")
        if sid is None: continue
        mapped=item.get("mapped_tvdb_seasons") or []
        for season in item.get("seasons") or []:
            original=season.get("number")
            if original is None: continue
            mapped_season=mapped[original-1] if t=="anime" and isinstance(original,int) and 0<original<=len(mapped) else None
            sn=mapped_season if mapped_season is not None else original
            for ep in season.get("episodes") or []:
                en=ep.get("number")
                if en is None: continue
                wr=ep.get("watched_at")
                yield {"show_title":show.get("title","a show"),"genres":show.get("genres") or item.get("genres") or [],"simkl_id":sid,"tmdb_id":ids.get("tmdb"),"tvdb_id":ids.get("tvdb"),"ids":ids,"slug":ids.get("slug"),"poster":show.get("poster"),"season_num":sn,"original_season_num":original,"mapped_tvdb_season_num":mapped_season,"episode_number":en,"episode_title":ep.get("title"),"watched_raw":wr,"watched_dt":parse_iso(wr) if wr else None,"key":episode_key(t,sid,sn,en)}

def format_episode_range(s,a,b):
    if s is None: return f"E{a:02d}" if a==b else f"E{a:02d}-E{b:02d}"
    return f"S{s}E{a:02d}" if a==b else f"S{s}E{a:02d}-E{b:02d}"

def format_episode_display(s,a,b,use_code=False):
    formatted=format_episode_range(s,a,b)
    return f"`{formatted}`" if use_code else f"**{formatted}**"
def group_consecutive(es):
    es=sorted(es,key=lambda x:x["episode_number"]); groups=[]
    for e in es:
        if not groups or e["episode_number"]!=groups[-1][-1]["episode_number"]+1: groups.append([e])
        else: groups[-1].append(e)
    return groups

async def episode_media(t,e):
    candidates=[]
    # For anime, SIMKL's season number is authoritative. Seasonal anime are
    # commonly stored as separate SIMKL/Kitsu-style entries, while TMDB may
    # expose a canonical series or a different season mapping. In particular,
    # original_season_num is often 1 for every seasonal entry, so it must never
    # be used as a fallback for the real season.
    if t=="anime":
        values=(e.get("season_num"),)
    else:
        values=(e.get("season_num"),e.get("mapped_tvdb_season_num"),e.get("original_season_num"))
    for v in values:
        try: v=int(v)
        except (TypeError,ValueError): continue
        if v not in candidates: candidates.append(v)
    if t=="anime": r=await tmdb.find_anime_episode(e.get("tmdb_id"),e.get("tvdb_id"),candidates,e["episode_number"],episode_title=e.get("episode_title"))
    else:
        r=None
        if e.get("tmdb_id") is not None:
            d=await tmdb.get_episode_details(e["tmdb_id"],e.get("season_num"),e["episode_number"])
            if d: r={"series_id":int(e["tmdb_id"]),"season_number":int(e["season_num"]),"episode_number":e["episode_number"],"episode":d}
        if not r and e.get("tvdb_id") is not None:
            sid=await tmdb.find_series_by_tvdb(e["tvdb_id"])
            if sid:
                d=await tmdb.get_episode_details(sid,e.get("season_num"),e["episode_number"])
                if d: r={"series_id":sid,"season_number":int(e["season_num"]),"episode_number":e["episode_number"],"episode":d}
    if not r: return None,e.get("episode_title"),None,None
    episode=r.get("episode") or {}
    imdb_id=(episode.get("external_ids") or {}).get("imdb_id")
    still=r.get("still_url")
    if not still and r.get("series_id") is not None:
        still=await tmdb.get_episode_still(
            r["series_id"],
            r["season_number"],
            r["episode_number"],
        )
    # SIMKL anime episode titles can be romanized/romaji. Prefer TMDB's
    # English-localized title (get_episode_details requests en-US), with
    # SIMKL's title only as a fallback when TMDB has no English title.
    runtime = episode.get("runtime")
    return still,episode.get("name") or e.get("episode_title"),imdb_id,runtime

async def prefs(g,u): return await storage.get_embed_preferences(g,u)
async def get_imdb_rating(media_type, tmdb_id):
    if mdblist is None or tmdb_id is None:
        return None
    try:
        return await mdblist.get_imdb_rating(media_type, tmdb_id)
    except Exception:
        log.warning("MDBList rating lookup failed for %s %s.", media_type, tmdb_id, exc_info=True)
        return None

async def get_movie_ratings(tmdb_id):
    if mdblist is None or tmdb_id is None:
        return None
    try:
        ratings = await mdblist.get_ratings("movie", tmdb_id)
        if not ratings:
            return None
        return {"imdb": ratings.get("imdb"), "mal": ratings.get("myanimelist")}
    except Exception:
        log.warning("MDBList movie rating lookup failed for %s.", tmdb_id, exc_info=True)
        return None

async def get_show_ratings(tmdb_id):
    if mdblist is None or tmdb_id is None:
        return None
    try:
        ratings = await mdblist.get_ratings("show", tmdb_id)
        if not ratings:
            return None
        return {"imdb": ratings.get("imdb"), "mal": ratings.get("myanimelist")}
    except Exception:
        log.warning("MDBList show rating lookup failed for %s.", tmdb_id, exc_info=True)
        return None

def build_embed(t,desc,ts,name,member,image,profile,title=None,title_url=None,poster=None,logo=None,preferences=None,status_activity=False,provider="SIMKL"):
    color,label=MEDIA_STYLES[t]; p={"style":"rich","artwork":"auto","activity_text":"short","show_imdb":True,"show_mal":True}; p.update(preferences or {})
    e=discord.Embed(title=title,url=title_url,description=desc,color=color,timestamp=ts)
    e.set_author(name=f"{name}'s Activity",url=profile,icon_url=member.display_avatar.url if member else None)
    # Status activities always use the title poster. /tracker-style and
    # /tracker-style-server only control watch activities.
    if status_activity:
        selected=poster
    elif p["artwork"]=="poster":
        selected=poster
    elif p["artwork"]=="backdrop":
        selected=image
    else:
        selected=image or poster
    if selected:
        if p["style"]=="minimal":
            e.set_thumbnail(url=selected)
        else:
            e.set_image(url=selected)
            if p["artwork"] in ("auto", "backdrop") and logo:
                e.set_thumbnail(url=logo)
    e.set_footer(text=f"{label} · {provider}"); return e
async def send_embed(ch,e,what):
    try:
        await ch.send(embed=e, allowed_mentions=discord.AllowedMentions.none())
        return True
    except discord.Forbidden:
        log.error("Discord denied permission while sending %s embed to channel %s.", what, getattr(ch, "id", "unknown"))
    except discord.NotFound:
        log.error("Discord channel or destination was not found while sending %s embed.", what)
    except discord.HTTPException as exc:
        # discord.py handles normal Discord rate limits. Avoid blind retries
        # because a failed response may still have created the message.
        log.error("Discord HTTP error while sending %s embed (status=%s): %s", what, exc.status, exc)
    except Exception:
        log.exception("Unexpected failure while sending %s embed.", what)
    return False

async def refresh_user_token(uid,u):
    rt=u.get("refresh_token")
    if not rt: raise SimklAuthError("No refresh token.")
    tok=await simkl.refresh_token(rt)
    if not tok or not tok.get("access_token"): raise SimklAuthError("Token refresh failed.")
    access=tok["access_token"]; newrt=tok.get("refresh_token"); exp=calculate_token_expiry(tok.get("expires_in"))
    await storage.update_tokens(uid,access,newrt,exp); u["simkl_token"]=access; u["refresh_token"]=newrt or rt; u["token_expires_at"]=exp; return access
async def valid_token(uid,u):
    if not u.get("simkl_token"): raise SimklAuthError("No SIMKL token.")
    if u.get("token_expires_at") and parse_iso(u["token_expires_at"])<=datetime.now(timezone.utc)+timedelta(days=1):
        try: return await refresh_user_token(uid,u)
        except SimklAuthError: pass
    return u["simkl_token"]
async def call_refresh(uid,u,token,fn,*a,**kw):
    try: return await fn(token,*a,**kw),token
    except SimklAuthError:
        token=await refresh_user_token(uid,u); return await fn(token,*a,**kw),token

async def cached_simkl_items(uid,u,token,t,date_from=None,request_cache=None,timeout=None):
    if request_cache is None:
        return await call_refresh(uid,u,token,simkl.get_all_items,t,date_from=date_from,timeout=timeout)

    key=("all-items",uid,t,date_from,timeout)
    cached=request_cache.get(key)
    if cached is not None:
        return cached

    result=await call_refresh(uid,u,token,simkl.get_all_items,t,date_from=date_from,timeout=timeout)
    request_cache[key]=result
    return result

async def cached_simkl_activities(uid,u,token,request_cache):
    key=("activities",uid)
    cached=request_cache.get(key)
    if cached is not None:
        return cached

    result=await call_refresh(uid,u,token,simkl.get_activities)
    request_cache[key]=result
    return result

async def seed_history(g,uid,u,token,request_cache=None):
    started=time.monotonic()
    last=await storage.get_last_checked(g,uid); keys=[]; statuses={}; watches={}; seeded_stats=[]
    for t in MEDIA_TYPES:
        since=parse_iso(last.get(t,EPOCH_ISO)); initial_seed=last.get(t,EPOCH_ISO) == EPOCH_ISO
        items,token=await cached_simkl_items(uid,u,token,t,request_cache=request_cache,timeout=HISTORY_FETCH_TIMEOUT_SECONDS)
        if t=="movies":
            for x in items or []:
                m=x.get("movie") or {}; sid=(m.get("ids") or {}).get("simkl"); wr=x.get("last_watched_at")
                if sid is None or (not initial_seed and wr and parse_iso(wr)>since): continue
                k=movie_key(t,sid); keys.append(k)
                if x.get("status"): statuses[f"{t}:{sid}"]=x["status"]
                if wr:
                    watches[k]=wr
                    seeded_stats.append(("anime_movie" if (m.get("anime_type") == "movie" or m.get("type") == "movie" or (m.get("ids") or {}).get("mal")) else "movie", m.get("title") or "Untitled", k, wr, m.get("genres") or x.get("genres"),m.get("ids") or {}))
        else:
            episode_items=items
            movie_items=[]
            if t=="anime":
                episode_items,movie_items=await cached_split_anime_items(uid,items,request_cache)
            for x in episode_items or []:
                m=x.get("show") or {}; sid=(m.get("ids") or {}).get("simkl")
                if sid is not None and x.get("status"): statuses[f"{t}:{sid}"]=x["status"]
            for x in movie_items or []:
                m=x.get("movie") or x.get("show") or {}; sid=(m.get("ids") or {}).get("simkl"); wr=x.get("last_watched_at")
                if sid is None or (not initial_seed and wr and parse_iso(wr)>since): continue
                k=movie_key("movies",sid); keys.append(k)
                if x.get("status"): statuses[f"movies:{sid}"]=x["status"]
                if wr:
                    watches[k]=wr
                    seeded_stats.append(("anime_movie",m.get("title") or "Untitled",k,wr,m.get("genres") or x.get("genres"),m.get("ids") or {}))
            for e in iter_show_episodes(t,episode_items):
                if initial_seed or e["watched_dt"] is None or e["watched_dt"]<=since:
                    keys.append(e["key"])
                    if e.get("watched_raw"):
                        watches[e["key"]]=e["watched_raw"]
                        seeded_stats.append(("anime_episode" if t == "anime" else "episode",e.get("show_title") or "Untitled",f"series:{t}:{e['simkl_id']}:{e['season_num']}:{e['episode_number']}",e["watched_raw"],e.get("genres"),e.get("ids") or {}))
    awarded=await storage.seed_guild_history(g,uid,keys,statuses,watches,seeded_stats)
    log.info("Seeded %d historical watches for user %s in guild %s (+%d watch XP) in %.2fs.",
             len(seeded_stats),uid,g,awarded,time.monotonic()-started)
    await evaluate_achievements(g,uid)
    return token

async def resolve_member(g,uid):
    guild=bot.get_guild(int(g))
    if not guild: return None,"Someone"
    m=guild.get_member(int(uid))
    if not m:
        try: m=await guild.fetch_member(int(uid))
        except Exception: return None,"Someone"
    return m,m.display_name

async def process_shows(ch,g,uid,name,member,t,items,profile,batch=None):
    announced=await storage.get_announced(g,uid); state=await storage.get_activity_state(g,uid); watches=state["watch_times"]; p=await prefs(g,uid); groups=defaultdict(list)
    for e in iter_show_episodes(t,items):
        if e["watched_dt"] is None: continue
        prev=watches.get(e["key"]); prevdt=parse_iso(prev) if prev else None
        rw=e["key"] in announced and prevdt and e["watched_dt"]>prevdt
        if e["key"] not in announced or rw: groups[(e["simkl_id"],e["season_num"],"rewatched" if rw else "watched")].append(e)
    count=0; ok=True
    starting_titles={((item.get("show") or {}).get("ids") or {}).get("simkl")
                     for item in items or [] if item.get("status")=="watching"}
    start_claimed=set()
    for (sid,sn,kind),es in groups.items():
        es=sorted(es,key=lambda x:x["episode_number"]); title=es[0]["show_title"]; url=simkl_title_url(t,sid,es[0]["slug"]); fallback=simkl_poster_url(es[0]["poster"])
        if t=="anime":
            try:
                anime_tmdb_id=await resolve_anime_tmdb_id({
                    "tmdb": es[0].get("tmdb_id"),
                    "tvdb": es[0].get("tvdb_id"),
                })
                english_title=None
                if anime_tmdb_id is not None:
                    english_title=await tmdb.get_tv_title(
                        anime_tmdb_id,
                        prefer_english=True,
                    )
                if english_title:
                    title=english_title
            except Exception:
                log.warning("TMDB anime series title lookup failed for %s.", title, exc_info=True)
        for grp in group_consecutive(es):
            try:
                image,ep_title,episode_imdb_id,episode_runtime=await episode_media(t,grp[0])
            except Exception:
                log.warning("TMDB episode lookup failed for %s.", title, exc_info=True)
                image,ep_title,episode_imdb_id,episode_runtime=None,grp[0].get("episode_title"),None
            label=format_episode_display(sn,grp[0]["episode_number"],grp[-1]["episode_number"],p.get("episode_code", False)); verb=kind.capitalize()
            rating = None
            if len(grp) == 1 and p.get("show_imdb", True):
                if episode_imdb_id:
                    rating = await imdb.get_rating(episode_imdb_id)
                    if rating is None:
                        log.info("IMDb has no episode rating for %s S%sE%s (IMDb=%s).",
                                 title, sn, grp[0]["episode_number"], episode_imdb_id)
                else:
                    log.info("No IMDb episode ID resolved for %s S%sE%s (TVDB=%s, TMDB=%s).",
                             title, sn, grp[0]["episode_number"],
                             grp[0].get("tvdb_id"), grp[0].get("tmdb_id"))
            desc=f"{verb} {label}"
            if p["activity_text"]=="detailed": desc=f"{verb} {label} of **{title}**"
            if len(grp)==1:
                if ep_title: desc+=f"\n*{ep_title}*"
                if rating is not None: desc+=f"\n⭐ IMDb {rating:.1f}/10"
            started=(sid in starting_titles and sid not in start_claimed and kind=="watched"
                     and not any(key.startswith(f"{t}:{sid}:") for key in watches))
            if started:
                desc+="\n\n🆕 Started watching this series."
                start_claimed.add(sid)
            logo=None
            if p["artwork"] in ("auto", "backdrop"):
                try:
                    series_tmdb_id=await resolve_anime_tmdb_id({
                        "tmdb": grp[0].get("tmdb_id"),
                        "tvdb": grp[0].get("tvdb_id"),
                    }) if t=="anime" else grp[0].get("tmdb_id")
                    if series_tmdb_id is None and grp[0].get("tvdb_id") is not None:
                        series_tmdb_id=await tmdb.find_series_by_tvdb(grp[0]["tvdb_id"])
                    logo=await tmdb.get_tv_logo(series_tmdb_id) if series_tmdb_id is not None else None
                except Exception:
                    log.warning("TMDB TV logo lookup failed for %s.", title, exc_info=True)
            # Ranged activity optimization:
            # The first episode supplies the artwork/title for the grouped
            # notification. The last episode is resolved as a boundary check,
            # while episodes in between do not need individual TMDB/TVMaze
            # lookups. Middle episodes fall back to the base episode XP value
            # when their runtime is not already known.
            runtime_by_key={grp[0]["key"]: episode_runtime}
            if len(grp) > 1:
                last_episode=grp[-1]
                try:
                    _,_,_,last_runtime=await episode_media(t,last_episode)
                    runtime_by_key[last_episode["key"]]=last_runtime
                except Exception:
                    runtime_by_key[last_episode["key"]]=None

            e=build_embed(t,desc,max(x["watched_dt"] for x in grp),name,member,image,profile,title,url,fallback,logo,p)
            keys=[x["key"] for x in grp]
            watch_times={x["key"]:x["watched_raw"] for x in grp}
            records=[{
                "media_type":"anime_episode" if t=="anime" else "episode",
                "title":title,
                "item_key":f"series:{t}:{sid}:{watched['season_num']}:{watched['episode_number']}",
                "watched_at":watched["watched_raw"],
                "genres":watched.get("genres"),
                "ids":watched.get("ids") or {},
                "amount":xp_for_watch("anime_episode" if t=="anime" else "episode",
                                       runtime_by_key.get(watched["key"])),
            } for watched in grp]
            async def commit(keys=keys, watch_times=watch_times, records=records, started=started, sid=sid):
                await storage.record_activity_batch(g,uid,keys,watch_times,records)
                if started:
                    await storage.update_activity_state(g,uid,statuses={f"{t}:{sid}":"watching"})
            if batch is not None:
                # SIMKL identity plus its mapped season prevents unrelated anime
                # seasons or colliding third-party IDs from grouping together.
                batch.add(WatchActivity(str(g),str(uid),t,ch,
                    ("episode",t,str(sid),sn,tuple(x["episode_number"] for x in grp)),
                    tuple(x["watched_dt"] for x in grp),e,
                    f"**{format_episode_range(sn,grp[0]['episode_number'],grp[-1]['episode_number'])}** of **{title}**",
                    commit,started=started,rewatched=kind=="rewatched",count=len(grp)))
            elif await send_embed(ch,e,"episode"):
                await commit()
            else:
                ok=False
                continue
            count+=len(grp)
    if count and batch is None: await evaluate_achievements(g,uid,notify_channel=ch)
    return count,ok

async def process_movies(ch,g,uid,name,member,items,since,profile,batch=None,scope="movies"):
    announced=await storage.get_announced(g,uid); state=await storage.get_activity_state(g,uid); watches=state["watch_times"]; p=await prefs(g,uid); count=0; ok=True
    for x in items or []:
        m=x.get("movie") or {}; ids=m.get("ids") or {}; sid=ids.get("simkl"); wr=x.get("last_watched_at")
        if sid is None or not wr: continue
        dt=parse_iso(wr); k=movie_key("movies",sid); prev=watches.get(k); prevdt=parse_iso(prev) if prev else None; rw=k in announced and prevdt and dt>prevdt
        if k in announced and not rw: continue
        if k not in announced and dt<=since: continue
        title=m.get("title","a movie"); poster=simkl_poster_url(m.get("poster")); image=None
        anime_movie = bool(
            ids.get("mal")
            or m.get("anime_type") == "movie"
            or m.get("type") == "movie"
        )
        tmdb_movie_id=ids.get("tmdb")
        if anime_movie:
            try:
                # SIMKL anime movie records can carry a stale/season-specific
                # TMDB ID. Search by the SIMKL title when the supplied ID is
                # not a valid TMDB movie, so artwork, English title, and
                # MDBList ratings all use the same canonical movie ID.
                english_title=None
                # Anime movie records are especially prone to carrying a
                # stale/season-specific TMDB ID. Resolve by title first so a
                # valid-but-wrong TMDB movie ID cannot silently win.
                match=await tmdb.find_movie_by_title(m.get("title"))
                if match:
                    tmdb_movie_id=match["id"]
                    english_title=match.get("title")
                    log.info(
                        "Resolved anime movie %r -> TMDB movie %s (%s).",
                        m.get("title") or "Untitled",
                        tmdb_movie_id,
                        english_title or "Untitled",
                    )
                elif tmdb_movie_id is not None:
                    english_title=await tmdb.get_movie_title(
                        tmdb_movie_id,
                        prefer_english=True,
                    )
                if english_title:
                    title=english_title
            except Exception:
                log.warning(
                    "TMDB anime movie resolution failed for %s.",
                    title,
                    exc_info=True,
                )
        if tmdb_movie_id is not None:
            try:
                image=await tmdb.get_movie_backdrop(tmdb_movie_id)
                if p["artwork"] == "backdrop" and image is None:
                    log.info(
                        "No TMDB movie backdrop available for %s (TMDB=%s).",
                        title,
                        tmdb_movie_id,
                    )
            except Exception:
                log.warning("TMDB movie backdrop lookup failed for %s.", title, exc_info=True)
        ratings = None
        if tmdb_movie_id is not None and (
            p.get("show_imdb", True)
            or (anime_movie and p.get("show_mal", True))
        ):
            ratings = await get_movie_ratings(tmdb_movie_id)

        verb="Rewatched" if rw else "Watched"
        desc=f"{verb}"
        if p["activity_text"]=="detailed":
            desc=f"{verb} **{title}**"
        if ratings:
            if p.get("show_imdb", True) and ratings.get("imdb") is not None:
                desc += f"\n⭐ IMDb {ratings['imdb']:.1f}/10"
            if anime_movie and p.get("show_mal", True) and ratings.get("mal") is not None:
                desc += f"\n🌸 MAL {ratings['mal']:.2f}/10"
        logo=None
        if tmdb_movie_id is not None and p["artwork"] in ("auto", "backdrop"):
            try:
                logo=await tmdb.get_movie_logo(tmdb_movie_id)
            except Exception:
                log.warning("TMDB movie logo lookup failed for %s.", title, exc_info=True)
        activity_url = simkl_title_url("movies", sid, ids.get("slug"))
        if anime_movie and tmdb_movie_id is not None:
            # Anime movie items can originate from SIMKL's /anime catalog and
            # therefore carry an anime URL even after we resolve them to the
            # canonical TMDB movie. Use SIMKL's TMDB redirect so the activity
            # opens the movie entry instead of the anime entry.
            activity_url = simkl_redirect_url(tmdb_movie_id, "movie", title)
        e=build_embed("movies",desc,dt,name,member,image,profile,title,activity_url,poster,logo,p)
        media_type="anime_movie" if anime_movie else "movie"
        record={"media_type":media_type,"title":title,"item_key":k,"watched_at":wr,
                "genres":m.get("genres") or x.get("genres"),"ids":ids,"amount":xp_for_watch(media_type)}
        async def commit(k=k, wr=wr, record=record):
            await storage.record_activity_batch(g,uid,[k],{k:wr},[record])
        if batch is not None:
            batch.add(WatchActivity(str(g),str(uid),scope,ch,
                ("movie",scope,str(sid)),(dt,),e,f"**{title}**",commit,
                rewatched=bool(rw),movie=True))
        elif await send_embed(ch,e,"movie"):
            await commit()
        else:
            ok=False
            continue
        count+=1
    if count and batch is None: await evaluate_achievements(g,uid,notify_channel=ch)
    return count,ok

async def process_status(ch,g,uid,name,member,t,items,profile):
    state=await storage.get_activity_state(g,uid)
    statuses=state["statuses"]
    baseline=not state["statuses_seeded"]
    p=await prefs(g,uid)
    successful={}
    count=0
    ok=True
    for x in items or []:
        m=(x.get("movie") if t=="movies" else x.get("show")) or {}
        ids=m.get("ids") or {}
        sid=ids.get("simkl")
        status=x.get("status")
        if sid is None or status not in WATCHLIST_STATUSES:
            continue
        key=f"{t}:{sid}"
        if baseline or statuses.get(key)==status:
            successful[key]=status
            continue
        if status=="watching":
            # Keep status transitions accurate even when no episode was marked.
            # The start annotation depends on successful watch history instead.
            await storage.update_activity_state(g,uid,statuses={key:status},flush=True)
            continue
        # Movies already generate a dedicated "watched" activity. Treat the
        # SIMKL "completed" status as internal state for movies so it does not
        # create a duplicate notification. TV/anime still use "completed".
        if t=="movies" and status=="completed":
            successful[key]=status
            continue

        anime_movie = (
            t=="anime"
            and (
                m.get("type") == "movie"
                or m.get("anime_type") == "movie"
            )
        )
        if anime_movie:
            successful[key]=status
            continue

        title=m.get("title") or "Untitled"
        artwork_tmdb_id=ids.get("tmdb")
        if t=="anime":
            try:
                anime_tmdb_id=await resolve_anime_tmdb_id(ids)
                if anime_tmdb_id is not None:
                    artwork_tmdb_id=anime_tmdb_id
                    english_title=await tmdb.get_tv_title(
                        anime_tmdb_id,
                        prefer_english=True,
                    )
                    if english_title:
                        title=english_title
            except Exception:
                log.warning("TMDB anime status title lookup failed for %s.", title)
        poster=simkl_poster_url(m.get("poster"))
        image=None
        if artwork_tmdb_id is not None:
            try:
                image=await (tmdb.get_movie_backdrop(artwork_tmdb_id) if t=="movies" else tmdb.get_tv_backdrop(artwork_tmdb_id))
            except Exception:
                log.warning("TMDB status artwork lookup failed for %s.", title)
        ratings = None
        if p.get("show_imdb", True) or (t=="anime" and p.get("show_mal", True)):
            ratings = await (
                get_movie_ratings(artwork_tmdb_id)
                if t=="movies"
                else get_show_ratings(artwork_tmdb_id)
            )
        desc=STATUS_TEXT[status]
        if p["activity_text"]=="detailed":
            desc=f"{STATUS_TEXT[status]} **{title}**"
        if ratings:
            if p.get("show_imdb", True) and ratings.get("imdb") is not None:
                desc += f"\n⭐ IMDb {ratings['imdb']:.1f}/10"
            if t=="anime" and p.get("show_mal", True) and ratings.get("mal") is not None:
                desc += f"\n🌸 MAL {ratings['mal']:.2f}/10"
        logo=None
        if artwork_tmdb_id is not None and p["artwork"] in ("auto", "backdrop"):
            try:
                logo=await (tmdb.get_movie_logo(artwork_tmdb_id) if t=="movies" else tmdb.get_tv_logo(artwork_tmdb_id))
            except Exception:
                log.warning("TMDB title logo lookup failed for %s.", title)
        e=build_embed(t,desc,datetime.now(timezone.utc),name,member,image,profile,title,simkl_title_url(t,sid,ids.get("slug")),poster,logo,p,status_activity=True)
        if not await send_embed(ch,e,status):
            ok=False
            continue
        count+=1
        successful[key]=status
        await storage.update_activity_state(g,uid,statuses={key:status},statuses_seeded=True,flush=True)
    if successful and baseline:
        await storage.update_activity_state(g,uid,statuses=successful,statuses_seeded=True,flush=True)
    return count,ok

async def mark_poll_failure(g,uid,error,previous_failures=0):
    failures=max(int(previous_failures or 0),0)+1
    await storage.update_poll_health(g,uid,last_error=error,consecutive_failures=failures,flush=True)

def poll_failures_exceeded(guild_user):
    """Return True when automatic polling should skip this guild-user."""
    try:
        failures=int((guild_user or {}).get("consecutive_failures",0) or 0)
    except (TypeError,ValueError):
        failures=0
    return failures>=MAX_CONSECUTIVE_FAILURES

async def notify_poll_failures_exceeded(g,uid,guild_user):
    """DM a user once when automatic polling is paused for their SIMKL link."""
    if (guild_user or {}).get("failure_notified"):
        return False
    guild=bot.get_guild(int(g))
    server=f"**{guild.name}**" if guild else "a server"
    last_error=(guild_user or {}).get("last_error") or "unknown error"
    message=(
        f"Hi! The SIMKL tracker in {server} has failed to check your SIMKL account "
        f"{MAX_CONSECUTIVE_FAILURES} times in a row, so automatic tracking there is paused.\n"
        f"Last error: `{str(last_error)[:300]}`\n\n"
        "Your SIMKL link probably needs attention (for example, access was revoked). "
        "Run `/tracker-link` in that server to reconnect; tracking resumes automatically after a successful check."
    )
    try:
        user=bot.get_user(int(uid)) or await bot.fetch_user(int(uid))
        await user.send(message)
    except discord.Forbidden:
        # DMs are closed; retrying every cycle would never succeed, so treat as notified.
        log.info("Couldn't DM user %s about paused SIMKL polling in guild %s: DMs are closed.",uid,g)
    except Exception as exc:
        log.warning("Couldn't DM user %s about paused SIMKL polling in guild %s: %s: %s",uid,g,type(exc).__name__,exc)
        return False
    else:
        log.info("Notified user %s that SIMKL polling is paused in guild %s.",uid,g)
    await storage.update_poll_health(g,uid,failure_notified=True,flush=True)
    return True

def watch_xp_base_key(media_type, media_key):
    """Return the stable SIMKL item prefix used by watch-XP event keys."""
    if media_type in {"episode", "anime_episode"}:
        return f"{media_type}:{media_key}:"
    if media_type in {"movie", "anime_movie"}:
        return f"{media_type}:{media_key}:"
    return None


async def reconcile_watch_progression(g, uid, u, token, changed_types, request_cache=None):
    """Reconcile watch XP and per-server totals with current SIMKL history."""
    if not changed_types:
        return token, 0

    legacy_statistics=await storage.needs_watch_statistics_rebuild(g,uid)
    if legacy_statistics:
        # Old aggregate counters have no per-item identities. Rebuild them once
        # from all three catalogs so a partial media update cannot erase them.
        changed_types=set(MEDIA_TYPES)
    elif {"movies","anime"} & set(changed_types):
        # Anime films can appear in either SIMKL catalog. Compare both before
        # deleting one or a partial catalog response could revoke valid watches.
        changed_types=set(changed_types) | {"movies","anime"}
    full_statistics_snapshot=set(changed_types)==set(MEDIA_TYPES)
    active_watch_bases=set()
    media_types=set()
    baseline_entries=[]

    for t in changed_types:
        if t == "movies":
            media_types.update({"movie", "anime_movie"})
        elif t == "anime":
            media_types.update({"anime_episode", "anime_movie"})
        else:
            media_types.add("episode")

        items, token = await cached_simkl_items(
            uid,
            u,
            token,
            t,
            request_cache=request_cache,
            timeout=HISTORY_FETCH_TIMEOUT_SECONDS,
        )

        if t == "movies":
            for item in items or []:
                movie=item.get("movie") or {}
                sid=(movie.get("ids") or {}).get("simkl")
                if sid is None or not item.get("last_watched_at"):
                    continue
                anime_movie=bool(
                    movie.get("anime_type") == "movie"
                    or movie.get("type") == "movie"
                    or (movie.get("ids") or {}).get("mal")
                )
                media_type="anime_movie" if anime_movie else "movie"
                base=watch_xp_base_key(media_type, f"movies:{sid}")
                if base:
                    active_watch_bases.add(base)
                if full_statistics_snapshot:
                    baseline_entries.append({"media_type":media_type,"item_key":f"movies:{sid}",
                                             "title":movie.get("title") or "Untitled","watched_at":item["last_watched_at"],
                                             "ids":movie.get("ids") or {},
                                             "genres":movie.get("genres") or item.get("genres") or []})
                media_types.add(media_type)
            continue

        episode_items=items or []
        movie_items=[]
        if t == "anime":
            episode_items,movie_items=await cached_split_anime_items(uid,items,request_cache)

        for item in movie_items:
            movie=item.get("movie") or item.get("show") or {}
            sid=(movie.get("ids") or {}).get("simkl")
            if sid is None or not item.get("last_watched_at"):
                continue
            base=watch_xp_base_key("anime_movie", f"movies:{sid}")
            if base:
                active_watch_bases.add(base)
            if full_statistics_snapshot:
                baseline_entries.append({"media_type":"anime_movie","item_key":f"movies:{sid}",
                                         "title":movie.get("title") or "Untitled","watched_at":item["last_watched_at"],
                                         "ids":movie.get("ids") or {},
                                         "genres":movie.get("genres") or item.get("genres") or []})
            media_types.add("anime_movie")

        media_type="anime_episode" if t == "anime" else "episode"
        for episode in iter_show_episodes(t, episode_items):
            if not episode.get("watched_raw"):
                continue
            base=watch_xp_base_key(
                media_type,
                f"series:{t}:{episode['simkl_id']}:{episode['season_num']}:{episode['episode_number']}",
            )
            if base:
                active_watch_bases.add(base)
            if full_statistics_snapshot:
                baseline_entries.append({"media_type":media_type,
                                         "item_key":f"series:{t}:{episode['simkl_id']}:{episode['season_num']}:{episode['episode_number']}",
                                         "title":episode.get("show_title") or "Untitled",
                                         "ids":episode.get("ids") or {},
                                         "watched_at":episode["watched_raw"],"genres":episode.get("genres") or []})

    result=await storage.reconcile_watch_xp(uid, active_watch_bases, media_types)
    if full_statistics_snapshot:
        restored_xp=await storage.repair_missing_watch_xp(uid,baseline_entries)
        if restored_xp:
            log.info("Restored %d missing historical watch XP for user %s.",restored_xp,uid)
    removed_watches=await storage.reconcile_watch_statistics(
        g,uid,active_watch_bases,media_types,
        baseline_entries=baseline_entries if full_statistics_snapshot else None,
        full_snapshot=full_statistics_snapshot,
    )
    if removed_watches:
        log.info("Reconciled SIMKL watch statistics for user %s in guild %s: %d fewer watches.",uid,g,removed_watches)
    removed=int(result.get("amount", 0))
    if removed:
        log.info(
            "Removed %d XP from deleted SIMKL watch history for user %s (%d watch event(s)).",
            removed,
            uid,
            int(result.get("events", 0)),
        )
    return token, removed


async def seed_progression_history(uid, u, token, request_cache=None):
    """Backfill XP from the user's existing SIMKL watch history exactly once."""
    progression = await storage.get_progression(uid)
    if progression.get("history_xp_seeded"):
        return token
    started=time.monotonic()
    events=[]
    for t in MEDIA_TYPES:
        items, token = await cached_simkl_items(uid, u, token, t, request_cache=request_cache, timeout=HISTORY_FETCH_TIMEOUT_SECONDS)
        if t == "movies":
            for item in items or []:
                movie = item.get("movie") or {}
                ids = movie.get("ids") or {}
                sid = ids.get("simkl")
                watched_at = item.get("last_watched_at")
                if sid is None or not watched_at:
                    continue
                anime_movie = bool(movie.get("anime_type") == "movie" or movie.get("type") == "movie" or ids.get("mal"))
                media_type = "anime_movie" if anime_movie else "movie"
                event_key = f"{media_type}:movies:{sid}:{watched_at}"
                events.append({"event_key":event_key,"media_type":media_type,"title":movie.get("title") or "Untitled",
                               "at":watched_at,"amount":xp_for_watch(media_type),"ids":ids})
            continue
        episode_items = items or []
        movie_items = []
        if t == "anime":
            episode_items, movie_items = await cached_split_anime_items(uid,items,request_cache)
        for item in movie_items:
            movie = item.get("movie") or item.get("show") or {}
            ids = movie.get("ids") or {}
            sid = ids.get("simkl")
            watched_at = item.get("last_watched_at")
            if sid is None or not watched_at:
                continue
            event_key = f"anime_movie:movies:{sid}:{watched_at}"
            events.append({"event_key":event_key,"media_type":"anime_movie","title":movie.get("title") or "Untitled",
                           "at":watched_at,"amount":xp_for_watch("anime_movie"),"ids":ids})
        for episode in iter_show_episodes(t, episode_items):
            watched_at = episode.get("watched_raw")
            if not watched_at:
                continue
            media_type = "anime_episode" if t == "anime" else "episode"
            event_key = f"{media_type}:series:{t}:{episode['simkl_id']}:{episode['season_num']}:{episode['episode_number']}:{watched_at}"
            events.append({"event_key":event_key,"media_type":media_type,"title":episode.get("show_title") or "Untitled",
                           "at":watched_at,"amount":xp_for_watch(media_type),"ids":episode.get("ids") or {}})
    seeded=await storage.seed_progression_batch(uid,events)
    log.info("Historical progression backfill completed for user %s: %d events, +%d XP in %.2fs.",
             uid,len(events),seeded,time.monotonic()-started)
    return token
async def notify_history_backfill(guild_id_value, uid, xp_earned, progression, channel):
    """Send a one-time summary after historical XP backfill completes."""
    if not await feature_enabled(guild_id_value,"progression"):
        return False
    if channel is None:
        log.warning("Historical progression notification skipped for user %s: no channel.", uid)
        return False

    if (await storage.get_progression(uid)).get("history_xp_notification_sent"):
        return False

    level = level_progress(int(progression.get("xp", 0)))[0]
    rank = rank_for_level(level)
    lifetime_xp = int(progression.get("lifetime_xp", 0))
    guild = bot.get_guild(int(guild_id_value))
    member = guild.get_member(int(uid)) if guild else None
    if member is None and guild:
        try:
            member = await guild.fetch_member(int(uid))
        except Exception:
            member = None
    mention = member.mention if member else f"<@{uid}>"

    embed = discord.Embed(
        title="📚 Historical Progression Imported",
        description=(
            f"{mention}, your existing SIMKL watch history has been added to your progression!\n\n"
            f"✨ **+{int(xp_earned):,} XP**\n"
            f"🏆 **Level {level} · {rank}**\n"
            f"💫 **{lifetime_xp:,} lifetime XP**"
        ),
        color=0x5865F2,
    )
    embed.set_footer(text="SIMKL Tracker · Historical XP Backfill")

    try:
        await channel.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
        await storage.mark_history_xp_notification_sent(uid)
        log.info(
            "Sent historical progression notification for user %s in guild %s: +%d XP, level %d.",
            uid, guild_id_value, int(xp_earned), level,
        )
        return True
    except discord.Forbidden:
        log.error(
            "Discord denied permission for historical progression notification in channel %s (guild %s, user %s).",
            getattr(channel, "id", "unknown"), guild_id_value, uid,
        )
    except discord.HTTPException as exc:
        log.error(
            "Discord HTTP error sending historical progression notification in channel %s (status=%s).",
            getattr(channel, "id", "unknown"), exc.status,
        )
    except Exception:
        log.exception("Unexpected failure sending historical progression notification for user %s.", uid)
    return False

async def poll_one(ch,g,uid,u,gu,request_cache=None,force_reconcile=False,batch=None):
    previous_failures=gu.get("consecutive_failures",0)
    await storage.update_poll_health(g,uid,last_poll_at=now_iso(),flush=False)
    if await storage.prepare_empty_history_repair(g,uid):
        gu["history_seeded"]=False
        log.info("Reimporting empty legacy watch statistics for user %s in guild %s.",uid,g)
    try:
        token=await valid_token(uid,u)
    except SimklAuthError as exc:
        error=f"SIMKL authentication failed: {exc}"
        await mark_poll_failure(g,uid,error,previous_failures)
        log.warning("SIMKL authentication failed for %s.",uid)
        return 0
    except Exception as exc:
        error=f"token validation: {type(exc).__name__}: {exc}"
        await mark_poll_failure(g,uid,error,previous_failures)
        log.error("Failed validating SIMKL token for user %s: %s: %s",uid,type(exc).__name__,exc)
        return 0
    if not gu.get("history_seeded"):
        try:
            async with history_backfill_semaphore:
                token=await seed_history(g,uid,u,token,request_cache)
            gu["history_seeded"]=True
        except Exception as exc:
            error=f"history seed: {type(exc).__name__}: {exc}"
            await mark_poll_failure(g,uid,error,previous_failures)
            log.error("Couldn't seed SIMKL history for user %s in guild %s: %s: %s",uid,g,type(exc).__name__,exc)
            return 0
    if not u.get("simkl_account_id") and uid not in profile_lookup_attempted:
        profile_lookup_attempted.add(uid)
        try:
            settings,token=await call_refresh(uid,u,token,simkl.get_user_settings)
            aid=account_id_from_settings(settings)
            if aid:
                await storage.set_account_id(uid,aid)
                u["simkl_account_id"]=aid
        except Exception:
            log.warning("Profile lookup failed for %s.",uid)
    profile=simkl_profile_url(u.get("simkl_account_id"))
    progression_before_backfill=await storage.get_progression(uid)
    history_backfill_needed=not progression_before_backfill.get("history_xp_seeded")
    try:
        if history_backfill_needed:
            async with history_backfill_semaphore:
                token=await seed_progression_history(uid,u,token,request_cache)
    except Exception as exc:
        error=f"progression history seed: {type(exc).__name__}: {exc}"
        await mark_poll_failure(g,uid,error,previous_failures)
        log.error("Could not backfill progression history for user %s: %s: %s",uid,type(exc).__name__,exc)
        return 0

    progression_after_backfill=await storage.get_progression(uid)
    if ch is not None and progression_after_backfill.get("history_xp_seeded") and not progression_after_backfill.get("history_xp_notification_sent"):
        backfill_xp=(
            int(progression_after_backfill.get("xp", 0))
            - int(progression_before_backfill.get("xp", 0))
        )
        if backfill_xp <= 0:
            # Compatibility path for users whose history was backfilled before
            # the completion notification feature was introduced.
            backfill_xp=sum(
                int(event.get("amount", 0))
                for event in progression_after_backfill.get("xp_events", [])
                if event.get("media_type") in {"episode", "anime_episode", "movie", "anime_movie"}
            )
        await notify_history_backfill(
            g, uid, backfill_xp, progression_after_backfill, ch
        )

    try:
        activities,token=await cached_simkl_activities(uid,u,token,request_cache if request_cache is not None else {})
    except Exception as exc:
        error=f"activity fetch: {type(exc).__name__}: {exc}"
        await mark_poll_failure(g,uid,error,previous_failures)
        log.error("Failed to get SIMKL activity timestamps for user %s: %s: %s",uid,type(exc).__name__,exc)
        return 0

    progression_before_poll=await storage.get_progression(uid)
    last=await storage.get_last_checked(g,uid)
    changed_types=set()
    last_statistics_reconcile=await storage.get_statistics_reconcile_time(g,uid)
    periodic_reconcile=(not last_statistics_reconcile or
                        datetime.now(timezone.utc)-parse_iso(last_statistics_reconcile)>=timedelta(days=1))
    for t in MEDIA_TYPES:
        stamp=(activities.get(ACTIVITY_KEYS[t]) or {}).get("all")
        if force_reconcile or periodic_reconcile or (stamp and parse_iso(stamp)>parse_iso(last.get(t,EPOCH_ISO))):
            changed_types.add(t)
    try:
        token,removed_xp=await reconcile_watch_progression(
            g,
            uid,
            u,
            token,
            changed_types,
            request_cache,
        )
    except Exception as exc:
        error=f"progression reconciliation: {type(exc).__name__}: {exc}"
        await mark_poll_failure(g,uid,error,previous_failures)
        log.error(
            "Could not reconcile deleted SIMKL watch XP for user %s: %s: %s",
            uid,
            type(exc).__name__,
            exc,
        )
        return 0

    # Reconcile achievement state immediately after deleted watch XP is removed.
    # This relocks thresholds that are no longer satisfied and revokes their XP.
    await evaluate_achievements(g,uid)

    if ch is None:
        await storage.update_poll_health(g,uid,last_success_at=now_iso(),last_error="",
                                         consecutive_failures=0,flush=True)
        return 0

    member,name=await resolve_member(g,uid)
    if not member:
        error=f"Discord member {uid} is no longer in guild {g}"
        await mark_poll_failure(g,uid,error,previous_failures)
        log.warning("User %s is no longer a member of guild %s; skipping activity posts.",uid,g)
        return 0

    posted=0
    cycle_errors=[]
    checkpoints=[]
    for t in MEDIA_TYPES:
        since=last.get(t,EPOCH_ISO)
        sdt=parse_iso(since)
        a=activities.get(ACTIVITY_KEYS[t]) or {}
        stamp=a.get("all")
        log.debug("Check %s/%s: SIMKL %s activity=%r checkpoint=%s",g,uid,t,stamp,since)
        if not stamp or parse_iso(stamp)<=sdt:
            continue
        try:
            items,token=await cached_simkl_items(
                uid,u,token,t,date_from=since,request_cache=request_cache
            )

            if t=="anime":
                anime_shows,anime_movies=await cached_split_anime_items(uid,items,request_cache)
                sc,so=await process_status(
                    ch,g,uid,name,member,t,anime_shows,profile
                )
                show_count,show_ok=await process_shows(
                    ch,g,uid,name,member,t,anime_shows,profile,batch=batch
                )
                movie_count,movie_ok=await process_movies(
                    ch,g,uid,name,member,anime_movies,sdt,profile,batch=batch,scope=t
                )
                wc=show_count+movie_count
                wo=show_ok and movie_ok
            else:
                sc,so=await process_status(
                    ch,g,uid,name,member,t,items,profile
                )
                if t=="shows":
                    wc,wo=await process_shows(ch,g,uid,name,member,t,items,profile,batch=batch)
                else:
                    wc,wo=await process_movies(ch,g,uid,name,member,items,sdt,profile,batch=batch)

            posted+=wc
            if so and wo:
                if batch is None:
                    await storage.update_last_checked(g,uid,t,to_iso(parse_iso(stamp)))
                else:
                    checkpoints.append((t,to_iso(parse_iso(stamp))))
            else:
                cycle_errors.append(f"{t}: partial post failure")
                log.warning("Some %s posts failed for user %s; checkpoint not advanced.",t,uid)
        except Exception as exc:
            cycle_errors.append(f"{t}: {type(exc).__name__}")
            log.error("Failed processing %s activity for user %s: %s: %s",t,uid,type(exc).__name__,exc)
        await storage.flush()
    async def finalize():
        if batch is not None:
            for media_type,stamp in checkpoints:
                if batch.failed(g,uid,media_type):
                    cycle_errors.append(f"{media_type}: watch delivery failed")
                else:
                    await storage.update_last_checked(g,uid,media_type,stamp)
            if any(item.delivered for item in batch.activities if item.guild==str(g) and item.user==str(uid)):
                await evaluate_achievements(g,uid,notify_channel=ch)
        if cycle_errors:
            error="; ".join(cycle_errors)
            await mark_poll_failure(g,uid,error,previous_failures)
        else:
            await storage.update_poll_health(
                g,uid,last_success_at=now_iso(),last_error="",
                consecutive_failures=0,flush=False
            )
            await storage.flush()

        progression_after_poll=await storage.get_progression(uid)
        await notify_challenge_rewards(g,uid,ch)
        for number in (await storage.claim_prestige_notifications(uid) if await feature_enabled(g,"progression") else []):
            if not await send_prestige_notification(ch.send,f"<@{uid}>",number,progression_after_poll.get("lifetime_xp",0)):
                await storage.retry_prestige_notification(uid,number)
                break
        await notify_level_up(g,uid,progression_before_poll,progression_after_poll,ch,source_label="SIMKL")
        await storage.flush()
    if batch is None:
        await finalize()
    else:
        batch.finalizers.append(finalize)
    return posted

async def poll_all(g=None, force_reconcile=False, ignore_failure_threshold=False):
    """Poll linked users.

    The automatic background poll skips guild-users that have reached
    MAX_CONSECUTIVE_FAILURES. Manual checks (/tracker-checknow) pass
    ignore_failure_threshold=True so they always attempt every target.
    """
    started = time.monotonic()

    async with poll_lock:
        requests_before=simkl.request_counts.copy()
        targets=await storage.get_poll_targets(g)


        if not targets:
            log.info("SIMKL polling: no active SIMKL targets (WeTrakr is checked separately).")
            return 0

        # Keep all guilds for the same Discord user in one worker. This
        # preserves per-user SIMKL request deduplication while allowing
        # different users to be processed concurrently.
        users={}
        for x in targets:
            users.setdefault(x["discord_user_id"],[]).append(x)

        batch=WatchBatch(send_embed)
        semaphore=asyncio.Semaphore(POLL_CONCURRENCY)
        channel_cache={}
        channel_errors={}
        skipped=[]

        async def resolve_channel(channel_id):
            channel=bot.get_channel(int(channel_id))
            if channel is not None:
                return channel,None
            try:
                return await bot.fetch_channel(int(channel_id)),None
            except Exception as exc:
                return None,f"Discord channel unavailable: {type(exc).__name__}: {exc}"

        async def process_user(uid,user_targets):
            async with semaphore:
                user_data=user_targets[0]["user_data"]
                request_cache={}
                posted=0

                for x in user_targets:
                    gid=x["guild_id"]
                    if not ignore_failure_threshold and poll_failures_exceeded(x["guild_user_data"]):
                        skipped.append((gid,uid))
                        try:
                            await notify_poll_failures_exceeded(gid,uid,x["guild_user_data"])
                        except Exception:
                            log.exception("Failed to handle paused SIMKL polling for %s in guild %s.",uid,gid)
                        continue
                    channel_id=x["channel_id"]
                    channel_key=str(channel_id) if channel_id else None
                    if channel_key:
                        if channel_key not in channel_cache:
                            channel_cache[channel_key]=asyncio.create_task(resolve_channel(channel_id))
                        ch,channel_error=await channel_cache[channel_key]
                    else:
                        ch,channel_error=None,None
                    if channel_error:
                        channel_errors[channel_key]=(gid,channel_error)

                    try:
                        posted+=await poll_one(ch,int(gid),uid,user_data,x["guild_user_data"],request_cache,force_reconcile=force_reconcile,
                                               batch=batch if await feature_enabled(gid,"watched_together") else None)
                        if channel_error:
                            await mark_poll_failure(gid,uid,channel_error,x["guild_user_data"].get("consecutive_failures",0))
                    except SimklAuthError as exc:
                        error=f"SIMKL authentication failed: {exc}"
                        await mark_poll_failure(gid,uid,error,x["guild_user_data"].get("consecutive_failures",0))
                        log.warning("SIMKL authentication failed for %s in guild %s: %s",uid,gid,exc)
                    except Exception as exc:
                        error=f"{type(exc).__name__}: {exc}"
                        await mark_poll_failure(gid,uid,error,x["guild_user_data"].get("consecutive_failures",0))
                        log.error("Polling failed for %s in guild %s: %s: %s",uid,gid,type(exc).__name__,exc)

                return posted

        results=await asyncio.gather(
            *(process_user(uid,user_targets) for uid,user_targets in users.items())
        )
        await batch.deliver()
        for uid in users:
            await storage.refresh_watch_occurrences(uid)
        for channel_id,(gid,error) in channel_errors.items():
            log.warning("Couldn't access channel %s for guild %s: %s",channel_id,gid,error)
        if skipped:
            log.info("Skipped %d target(s) with %d+ consecutive poll failures; use /tracker-checknow to retry.",
                     len(skipped),MAX_CONSECUTIVE_FAILURES)
        # Queued counts only become successful after Discord delivery.
        posted=sum(results) if not batch.activities else (
            max(0,sum(results)-sum(item.count for item in batch.activities))
            + sum(item.count for item in batch.activities if item.delivered))
        for gid in sorted({str(target["guild_id"]) for target in targets}):
            try:
                await refresh_community_state(gid)
            except Exception:
                log.exception("Community challenge reconciliation failed for guild %s.",gid)
        duration=time.monotonic()-started
        log.info(
            "Polling cycle complete: %d user(s), %d target(s), %d posted, %.2fs elapsed, concurrency=%d.",
            len(users),len(targets),posted,duration,POLL_CONCURRENCY
        )
        request_delta=simkl.request_counts-requests_before
        log.info("SIMKL GETs this cycle: activities=%d, full_history=%d, history_delta=%d, other=%d (includes retries).",
                 *(request_delta[k] for k in ("activities","full_history","history_delta","other")))
        return posted


async def is_wetrakr_anime(parent, tmdb_id):
    if parent.get("is_anime") or parent.get("type") == "anime":
        return True
    if any(str(g.get("name") if isinstance(g, dict) else g).casefold() == "anime"
           for g in parent.get("genres") or []):
        return True
    if not tmdb_id:
        return False
    series = await tmdb._get_series_details(tmdb_id)
    return bool(series and series.get("original_language") == "ja"
                and any(g.get("id") == 16 for g in series.get("genres") or [])
                and "JP" in (series.get("origin_country") or []))


async def is_wetrakr_anime_movie(movie, tmdb_id):
    """Identify anime films independently of WeTrakr's generic movie type."""
    ids = movie.get("ids") or {}
    if ids.get("mal") or movie.get("is_anime") or movie.get("anime_type") == "movie":
        return True
    if any(str(g.get("name") if isinstance(g, dict) else g).casefold() == "anime"
           for g in movie.get("genres") or []):
        return True
    if not tmdb_id:
        return False
    details = await tmdb.get_movie_details(tmdb_id)
    return bool(details and details.get("original_language") == "ja"
                and any(g.get("id") == 16 for g in details.get("genres") or []))


async def wetrakr_anime_coordinates(tmdb_id, ids, details, season, number):
    tvdb_id = ids.get("tvdb") or ids.get("thetvdb")
    if not tvdb_id and tmdb_id:
        tvdb_id = await tmdb.get_tvdb_id_for_tmdb(tmdb_id)
    air_date = details.get("air_date") or details.get("first_aired") or details.get("release_date")
    if not air_date and tmdb_id and season is not None and number is not None:
        source_episode = await tmdb.get_episode_details(tmdb_id, season, number)
        air_date = (source_episode or {}).get("air_date")
    mapped = await tmdb.map_anime_episode_to_tvmaze(
        tvdb_id, air_date=air_date, title=details.get("title") or details.get("name")) if tvdb_id else None
    if mapped:
        log.info("WeTrakr anime episode mapped: TMDB=%s TVDB=%s S%sE%s -> S%sE%s.",
                 tmdb_id, tvdb_id, season, number, *mapped)
        return (*mapped, tvdb_id, True)
    log.warning("WeTrakr anime episode mapping unavailable: TMDB=%s TVDB=%s S%sE%s; using source numbering.",
                tmdb_id, tvdb_id, season, number)
    return season, number, tvdb_id, False


async def deliver_wetrakr_change(ch, gid, uid, name, member, change, row, started_ids, episode_end=None):
    """Render a journal change without touching SIMKL history or shared XP."""
    action = change["action"]
    status = change.get("status")
    if action != "added":
        return True
    if status in {"watching", "waiting"}:
        if status == "watching":
            started_ids.add(str(change["wetrakr_id"]))
        return True
    if status not in {None, "planning", "dropped", "paused", "completed"}:
        return True
    p = await prefs(gid, uid)
    linked_user = await storage.get_user(uid)
    profile_url = wetrakr_profile_url((linked_user or {}).get("wetrakr"))
    title = change.get("title") or "Untitled"
    ids = change.get("ids") or {}
    poster = image = logo = None
    episode_title = None
    parent = {}
    details = {}
    end_details = {}
    season, number = change.get("season"), change.get("episode")
    source_season, source_number = season, number
    end_season, end_number = None, None
    media_type = change["media_type"]
    if media_type == "episode":
        try:
            details = await wetrakr.episode(change["wetrakr_id"])
            parent = details.get("media") or details.get("show") or {}
            title = parent.get("title") or title
            ids = parent.get("ids") or {}
            episode_title = details.get("title") or change.get("title")
            # This is WeTrakr's numbering; anime needs a separate TVDB/TVMaze
            # mapping before it becomes the displayed season/episode.
            detail_season = details.get("season") or {}
            season = detail_season.get("number", details.get("season_number", season)) if isinstance(detail_season, dict) else details.get("season_number", season)
            number = details.get("number", number)
            if episode_end:
                end_details = await wetrakr.episode(episode_end["wetrakr_id"])
                end_season_data = end_details.get("season") or {}
                end_season = end_season_data.get("number", end_details.get("season_number", episode_end.get("season"))) if isinstance(end_season_data, dict) else end_details.get("season_number", episode_end.get("season"))
                end_number = end_details.get("number", episode_end.get("episode"))
            poster_path = parent.get("poster_path")
            if poster_path:
                poster = f"https://image.tmdb.org/t/p/w500{poster_path}"
        except Exception:
            log.warning("WeTrakr episode metadata lookup failed for %s.", change.get("wetrakr_id"), exc_info=True)
    else:
        try:
            details = await wetrakr.title("movie" if media_type == "movie" else "show", change["wetrakr_id"])
            title = details.get("title") or title
            ids = details.get("ids") or ids
            poster_path = details.get("poster_path")
            if poster_path:
                poster = f"https://image.tmdb.org/t/p/w500{poster_path}"
        except Exception:
            log.warning("WeTrakr title metadata lookup failed for %s.", change.get("wetrakr_id"), exc_info=True)
    t = "movies" if media_type == "movie" else "shows"
    tmdb_id = ids.get("tmdb")
    mapped_anime = False
    anime_item = bool(
        details.get("is_anime")
        or details.get("type") == "anime"
        or ids.get("mal")
        or any(str(g.get("name") if isinstance(g, dict) else g).casefold() == "anime"
               for g in details.get("genres") or [])
    )
    tvdb_id = None
    resolved_episode = None
    if t == "shows":
        try:
            if await is_wetrakr_anime(parent if media_type == "episode" else details, tmdb_id):
                anime_item = True
                t = "anime"
                if tmdb_id:
                    title = await tmdb.get_tv_title(tmdb_id, prefer_english=True) or title
                if media_type == "episode":
                    anime_season, anime_number, tvdb_id, mapped_anime = await wetrakr_anime_coordinates(
                        tmdb_id, ids, details, season, number)
                    if episode_end:
                        anime_end_season, anime_end_number, _, end_mapped = await wetrakr_anime_coordinates(
                            tmdb_id, ids, end_details, end_season, end_number)
                        mapped_anime = mapped_anime and end_mapped
                        if mapped_anime:
                            end_season, end_number = anime_end_season, anime_end_number
                    if mapped_anime:
                        season, number = anime_season, anime_number
        except Exception:
            log.warning("WeTrakr anime classification/mapping failed for TMDB=%s.", tmdb_id, exc_info=True)
    elif t == "movies":
        try:
            anime_item = await is_wetrakr_anime_movie(details, tmdb_id)
        except Exception:
            log.warning("WeTrakr movie classification failed for TMDB=%s.", tmdb_id, exc_info=True)
    if tmdb_id and status is None:
        try:
            if t == "movies":
                image = await tmdb.get_movie_backdrop(tmdb_id)
                logo = await tmdb.get_movie_logo(tmdb_id)
            elif season is not None and number is not None:
                if t == "anime" and mapped_anime:
                    resolved_episode = await tmdb.find_anime_episode(tmdb_id, tvdb_id, [season], number,
                                                                     episode_title=episode_title)
                    image = (resolved_episode or {}).get("still_url")
                    if not image and resolved_episode and resolved_episode.get("series_id"):
                        image = await tmdb.get_episode_still(resolved_episode["series_id"],
                                                             resolved_episode["season_number"], resolved_episode["episode_number"])
                else:
                    image = await tmdb.get_episode_still(tmdb_id, season, number)
                logo = await tmdb.get_tv_logo(tmdb_id)
        except Exception:
            log.warning("TMDB WeTrakr artwork lookup failed for %s.", title, exc_info=True)
    ratings = {}
    if tmdb_id and (status or media_type == "movie") and (p.get("show_imdb", True) or p.get("show_mal", True)):
        try:
            ratings = await (get_movie_ratings(tmdb_id) if media_type == "movie"
                             else get_show_ratings(tmdb_id))
            ratings = ratings or {}
            anime_item = anime_item or ratings.get("mal") is not None
        except Exception:
            log.warning("WeTrakr rating lookup failed for TMDB=%s.", tmdb_id, exc_info=True)

    episode_rating = None
    if media_type == "episode" and status is None and not episode_end and p.get("show_imdb", True):
        try:
            episode_ids = details.get("ids") or {}
            imdb_id = ((details.get("external_ids") or {}).get("imdb_id")
                       or episode_ids.get("imdb") or details.get("imdb_id"))
            if not imdb_id and resolved_episode:
                imdb_id = (((resolved_episode.get("episode") or {}).get("external_ids") or {})
                           .get("imdb_id"))
            if not imdb_id and tmdb_id and season is not None and number is not None:
                episode_details = await tmdb.get_episode_details(tmdb_id,
                    source_season if t == "anime" and mapped_anime else season,
                    source_number if t == "anime" and mapped_anime else number)
                imdb_id = ((episode_details or {}).get("external_ids") or {}).get("imdb_id")
            if imdb_id:
                episode_rating = await imdb.get_rating(imdb_id)
        except Exception:
            log.warning("WeTrakr episode IMDb lookup failed for %s S%sE%s.",
                        title, season, number, exc_info=True)

    stamp = parse_iso(change.get("watched_at") or change.get("action_at"))
    if status:
        verb = {"planning": "Planned to watch", "dropped": "Dropped", "paused": "Paused",
                "completed": "Completed"}[status]
        desc = f"{verb} **{title}**" if p["activity_text"] == "detailed" else verb
    elif t == "movies":
        desc = f"Watched **{title}**" if p["activity_text"] == "detailed" else "Watched"
    else:
        if season is None or number is None:
            # Cannot safely identify an episode from a partial journal row.
            raise ValueError("WeTrakr episode lacks season or episode number")
        if episode_end:
            end_season = end_season if end_season is not None else episode_end.get("season")
            end_number = end_number if end_number is not None else episode_end.get("episode")
            if end_season is None or end_number is None:
                raise ValueError("WeTrakr episode range lacks an endpoint")
            if int(season) == int(end_season):
                label = format_episode_display(int(season), int(number), int(end_number), p.get("episode_code", False))
            else:
                span = f"S{season}E{int(number):02d}–S{end_season}E{int(end_number):02d}"
                label = f"`{span}`" if p.get("episode_code", False) else f"**{span}**"
        else:
            label = format_episode_display(int(season), int(number), int(number), p.get("episode_code", False))
        desc = f"Watched {label} of **{title}**" if p["activity_text"] == "detailed" else f"Watched {label}"
        if not episode_end and episode_title and episode_title != title:
            desc += f"\n*{episode_title}*"
        def is_first_episode(s, e):
            try:
                return int(s) == 1 and int(e) == 1
            except (TypeError, ValueError):
                return False
        started = False
        if is_first_episode(source_season, source_number) or is_first_episode(season, number):
            started = not await storage.has_wetrakr_show_history(
                uid, change.get("show_id"), ids,
                exclude_event_id=change.get("source_event_id"),
            )
        if episode_rating is not None:
            desc += f"\n⭐ IMDb {float(episode_rating):.1f}/10"
        if started:
            desc += "\n\n🆕 Started watching this series."
    if status or t == "movies":
        if p.get("show_imdb", True) and ratings.get("imdb") is not None:
            desc += f"\n⭐ IMDb {float(ratings['imdb']):.1f}/10"
        if anime_item and p.get("show_mal", True) and ratings.get("mal") is not None:
            desc += f"\n🌸 MAL {float(ratings['mal']):.2f}/10"
    title_url = wetrakr_title_url("movie" if media_type == "movie" else "show", tmdb_id)
    embed_type = "anime" if anime_item else t
    embed = build_embed(embed_type, desc, stamp, name, member, image, profile_url, title,
                        title_url=title_url, poster=poster, logo=logo, preferences=p,
                        status_activity=bool(status), provider="WeTrakr")
    sent = await send_embed(ch, embed, "WeTrakr activity")
    if sent and media_type == "episode":
        started_ids.discard(str(change.get("show_id")))
    return sent


async def poll_wetrakr_all(g=None):
    if not wetrakr_sync:
        return 0
    async with wetrakr_poll_lock:
        started_at = time.monotonic()
        requests_before = wetrakr.request_counts.copy()
        targets = await storage.get_provider_targets("wetrakr", g, active_only=True)
        posted = 0
        checked = 0
        for target in targets:
            gid, uid = target["guild_id"], target["discord_user_id"]
            channel_id = target["channel_id"]
            if not channel_id:
                continue
            try:
                ch = bot.get_channel(int(channel_id)) or await bot.fetch_channel(int(channel_id))
                member, name = await resolve_member(gid, uid)
                if not member:
                    continue
                if await storage.needs_watch_statistics_rebuild(gid, uid):
                    simkl_user = target["user_data"]
                    if not simkl_user.get("simkl_token"):
                        raise ValueError("Legacy watch statistics need a SIMKL history rebuild before WeTrakr import")
                    simkl_token = await valid_token(uid, simkl_user)
                    await reconcile_watch_progression(gid, uid, simkl_user, simkl_token, set(MEDIA_TYPES))
                checked += 1
                started_ids = set()
                metadata_cache = {}
                # Older WeTrakr imports stored every film as a generic movie.
                # Repair those records once, including their server statistics.
                pending_movies = {str(play.get("item_key") or "").split(":")[-1]
                                  for play in await storage.get_wetrakr_plays(uid)
                                  if play.get("media_type") in {"movie", "anime_movie"}
                                  and not play.get("anime_classified")
                                  and str(play.get("item_key") or "").startswith("wetrakr:movie:")}
                for movie_id in pending_movies:
                    try:
                        movie = await wetrakr.title("movie", movie_id)
                        anime = await is_wetrakr_anime_movie(movie, (movie.get("ids") or {}).get("tmdb"))
                        await storage.classify_wetrakr_movie(uid, movie_id, anime)
                    except Exception:
                        log.warning("Could not reclassify WeTrakr movie %s for user %s.",movie_id,uid,exc_info=True)
                async def resolve_play(play):
                    return await provider_registry.get('wetrakr').resolve_play(play, metadata_cache)
                async def deliver(change, row):
                    return await deliver_wetrakr_change(ch, gid, uid, name, member, change, row, started_ids)
                async def deliver_group(batch):
                    ordered = sorted(batch, key=lambda item: int(item[0]["episode"]))
                    first, last = ordered[0], ordered[-1]
                    return await deliver_wetrakr_change(ch, gid, uid, name, member,
                                                        first[0], first[1], started_ids, last[0])
                was_seeded=bool(target["guild_user_data"]["wetrakr_sync"].get("seeded"))
                before_progression=await storage.get_progression(uid) if was_seeded else None
                posted += await wetrakr_sync.poll(target, deliver, deliver_group, resolve_play)
                await evaluate_achievements(gid, uid, notify_channel=ch if was_seeded else None)
                if was_seeded:
                    after_progression=await storage.get_progression(uid)
                    await notify_challenge_rewards(gid,uid,ch)
                    if await feature_enabled(gid,"progression"):
                        for number in await storage.claim_prestige_notifications(uid):
                            if not await send_prestige_notification(ch.send,f"<@{uid}>",number,
                                                                 after_progression.get("lifetime_xp",0)):
                                await storage.retry_prestige_notification(uid,number)
                                break
                    await notify_level_up(gid,uid,before_progression,after_progression,ch,
                                          source_label="WeTrakr")
                await storage.refresh_watch_occurrences(uid)
            except Exception:
                log.exception("WeTrakr polling failed for user %s in guild %s.", uid, gid)
        request_delta = wetrakr.request_counts - requests_before
        log.info("WeTrakr polling complete: %d target(s), %d checked, %d activity item(s), %.2fs; "
                 "API requests: last_activities=%d, journal=%d, episodes=%d, shows=%d, movies=%d, history=%d.",
                 len(targets), checked, posted, time.monotonic() - started_at,
                 *(request_delta[k] for k in ("last_activities", "journal", "episodes", "shows", "movies", "tracking")))
        return posted


def achievement_progress_from_events(events, timezone_name=DEFAULT_TIMEZONE_NAME):
    """Build achievement counters from currently-active watch XP events.

    Unlike the legacy cumulative statistics, watch XP events are reconciled
    against SIMKL when watched items are removed, so this view can relock
    achievements accurately.
    """
    watch_types={"episode","anime_episode","movie","anime_movie"}
    active=[event for event in (events or []) if event.get("media_type") in watch_types]
    episodes=sum(1 for event in active if event.get("media_type") in {"episode","anime_episode"})
    movies=sum(1 for event in active if event.get("media_type") in {"movie","anime_movie"})
    anime_episodes=sum(1 for event in active if event.get("media_type") == "anime_episode")

    try:
        local_timezone=ZoneInfo(timezone_name)
    except (TypeError,ValueError,ZoneInfoNotFoundError):
        local_timezone=ZoneInfo(DEFAULT_TIMEZONE_NAME)

    watch_dates={}
    for event in active:
        stamp=event.get("at")
        if not stamp:
            continue
        try:
            dt=datetime.fromisoformat(str(stamp).replace("Z","+00:00"))
            if dt.tzinfo is None:
                dt=dt.replace(tzinfo=timezone.utc)
            day=dt.astimezone(local_timezone).date().isoformat()
        except (TypeError,ValueError):
            continue
        watch_dates[day]={"total":1}

    _,longest=calculate_streaks(watch_dates,timezone_name)
    return {
        "total": episodes+movies,
        "episodes": episodes,
        "movies": movies,
        "anime_episodes": anime_episodes,
        "anime_movies": sum(event.get("media_type") == "anime_movie" for event in active),
        "unique_titles": len({str(event.get("title")).casefold() for event in active if event.get("title")}),
        "active_days": len(watch_dates),
        "streak": longest,
    }


async def evaluate_achievements(g, uid, notify_channel=None):
    """Keep achievement locks and rewards in sync with current SIMKL history."""
    if not await feature_enabled(g,"achievements"):
        notify_channel=None
    timezone_info=await storage.get_timezone(g)
    progression=await storage.get_progression(uid)
    progress=achievement_progress_from_events(
        progression.get("xp_events", []),
        timezone_info["name"],
    )
    unlocked=await storage.get_achievements(g,uid)
    newly_unlocked=[]
    newly_relocked=[]
    now=datetime.now(timezone.utc).isoformat()

    for achievement_id, achievement in all_achievements():
        already_unlocked=achievement_id in unlocked
        qualifies=progress.get(achievement["category"],0) >= achievement["threshold"]

        if not qualifies:
            if already_unlocked:
                result=await storage.relock_achievement(g,uid,achievement_id)
                if result.get("relocked"):
                    newly_relocked.append(achievement_id)
                    unlocked.pop(achievement_id,None)
                    log.info(
                        "Relocked achievement %s for user %s in guild %s after SIMKL history reconciliation; removed %d XP.",
                        achievement_id,uid,g,int(result.get("xp_removed",0)),
                    )
            continue

        if not already_unlocked:
            unlocked_now=await storage.unlock_achievement(g,uid,achievement_id,now,flush=False)
            if unlocked_now:
                newly_unlocked.append(achievement_id)
                unlocked[achievement_id]={"unlocked_at":now}

        achievement_xp=int(achievement.get("xp",0))
        if achievement_xp > 0:
            await storage.award_achievement_xp(
                uid,
                achievement_id,
                achievement_xp,
                achievement["name"],
                now,
                flush=False,
                include_progression=False,
            )
    await storage.flush()

    if newly_unlocked and notify_channel is not None:
        guild=bot.get_guild(int(g))
        member=guild.get_member(int(uid)) if guild else None
        mention=member.mention if member else f"<@{uid}>"
        for aid in newly_unlocked:
            await send_achievement_notification(notify_channel.send, mention, aid)

    return newly_unlocked

def calculate_streaks(watch_dates, timezone_name=DEFAULT_TIMEZONE_NAME):
    dates=set()
    for value in (watch_dates or {}).keys():
        try:
            dates.add(datetime.strptime(value,"%Y-%m-%d").date())
        except (TypeError,ValueError):
            continue
    if not dates:
        return 0,0
    try:
        local_timezone=ZoneInfo(timezone_name)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        local_timezone=ZoneInfo(DEFAULT_TIMEZONE_NAME)
    today=datetime.now(local_timezone).date()
    # Keep today's streak alive until the end of the local calendar day.
    # If the user has not watched anything today yet, yesterday's streak
    # remains active instead of resetting immediately at midnight.
    current=0
    cursor=today if today in dates else today-timedelta(days=1)
    while cursor in dates:
        current+=1
        cursor-=timedelta(days=1)
    longest=length=0
    previous=None
    for date in sorted(dates):
        length=length+1 if previous is not None and date-previous==timedelta(days=1) else 1
        longest=max(longest,length)
        previous=date
    return current,longest


def achievement_notification_embed(mention, achievement_id):
    achievement=ACHIEVEMENTS[achievement_id]
    embed=discord.Embed(
        title="🏆 Achievement Unlocked!",
        description=f"{mention} unlocked {achievement['emoji']} **{achievement['name']}**",
        color=0xF1C40F,
    )
    embed.add_field(
        name="Achievement",
        value=f'{achievement["description"]}\n**Reward:** +{int(achievement.get("xp", 0)):,} XP',
        inline=False,
    )
    embed.set_footer(text="Tracker · Achievements")
    return embed


async def send_achievement_notification(send, mention, achievement_id, *, preview=False):
    achievement=ACHIEVEMENTS[achievement_id]
    embed=achievement_notification_embed(mention, achievement_id)
    options={
        "allowed_mentions": discord.AllowedMentions(users=not preview, roles=False, everyone=False),
    }
    if preview:
        options["ephemeral"]=True
    try:
        animation=await asyncio.to_thread(render_achievement_gif, achievement["name"], achievement.get("xp", 0))
        embed.set_image(url="attachment://achievement.gif")
        await send(embed=embed, file=discord.File(animation, filename="achievement.gif"), **options)
        return True
    except Exception:
        log.exception("Animated achievement notification failed for %s; trying embed fallback.", achievement_id)
        embed.set_image(url=None)
        try:
            await send(embed=embed, **options)
            return True
        except Exception:
            log.exception("Fallback achievement notification failed for %s.", achievement_id)
            return False


async def send_prestige_notification(send, mention, prestige, lifetime_xp, *, preview=False):
    accent,_=prestige_style(prestige)
    embed=discord.Embed(
        title=f"Prestige {prestige} Unlocked",
        description=(f"{mention} reached **Prestige {prestige}**. XP beyond Level 100 carries forward; "
                     f"your lifetime **{int(lifetime_xp):,} XP** is preserved."),
        color=discord.Color.from_rgb(*accent),
    )
    embed.set_footer(text="Tracker · Prestige")
    options={"allowed_mentions": discord.AllowedMentions(users=not preview, roles=False, everyone=False)}
    if preview:
        options["ephemeral"]=True
    try:
        animation=await asyncio.to_thread(render_prestige_gif, prestige)
        embed.set_image(url="attachment://prestige.gif")
        await send(embed=embed, file=discord.File(animation, filename="prestige.gif"), **options)
        return True
    except Exception:
        log.exception("Prestige animation failed for prestige %s; trying embed fallback.", prestige)
        embed.set_image(url=None)
        try:
            await send(embed=embed, **options)
            return True
        except Exception:
            log.exception("Prestige fallback notification failed for prestige %s.", prestige)
            return False


async def notify_level_up(guild_id_value, uid, before_progression, after_progression, channel,
                          *, preview_interaction=None, source_label=None):
    if preview_interaction is None and not await feature_enabled(guild_id_value,"progression"):
        return False
    if channel is None:
        log.warning("Level-up notification skipped for user %s: no channel.", uid)
        return False

    before_level = level_progress(int(before_progression.get("xp", 0)))[0]
    after_level = level_progress(int(after_progression.get("xp", 0)))[0]
    log.debug(
        "Progression check for user %s in guild %s: level %d -> %d (XP %d -> %d).",
        uid,
        guild_id_value,
        before_level,
        after_level,
        int(before_progression.get("xp", 0)),
        int(after_progression.get("xp", 0)),
    )
    if after_level <= before_level:
        return False

    guild = bot.get_guild(int(guild_id_value))
    member = preview_interaction.user if preview_interaction else (guild.get_member(int(uid)) if guild else None)
    if member is None and guild:
        try:
            member = await guild.fetch_member(int(uid))
        except Exception:
            member = None

    mention = member.mention if member else f"<@{uid}>"
    before_rank = rank_for_level(before_level)
    after_rank = rank_for_level(after_level)
    rank_up = after_rank != before_rank
    levels_gained = after_level - before_level

    title = "Rank Up" if rank_up else "Level Up"
    description = f"{mention} reached **Level {after_level}**"
    if levels_gained > 1:
        description += f" · **+{levels_gained} levels**"
    description += f"\n**{after_rank}**"
    after_prestige=int(after_progression.get("prestige",0))
    if after_prestige:
        description += f" · Prestige **{after_prestige}**"
    if rank_up:
        description += f"\n\n*New rank unlocked from {before_rank}.*"
    if source_label:
        description += f"\nVia **{source_label}**"

    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.from_rgb(*accent_for_tier(after_level,after_prestige)),
    )
    embed.set_footer(text="Tracker · Progression")
    send = preview_interaction.followup.send if preview_interaction else channel.send
    options = {"allowed_mentions": discord.AllowedMentions(users=preview_interaction is None, roles=False, everyone=False)}
    if preview_interaction:
        options["ephemeral"] = True

    try:
        animation = await asyncio.to_thread(
            render_level_up_gif,
            after_level,
            after_rank,
            previous_level=before_level,
            previous_rank=before_rank,
            prestige=after_prestige,
            source_label=source_label,
        )
        file = discord.File(animation, filename="level-up.gif")
        embed.set_image(url="attachment://level-up.gif")
        await send(
            embed=embed,
            file=file,
            **options,
        )
        log.info(
            "Sent animated level-up notification for user %s in guild %s: level %d -> %d%s.",
            uid,
            guild_id_value,
            before_level,
            after_level,
            " (rank up)" if rank_up else "",
        )
        return True
    except Exception:
        # A visual rendering/upload problem must never suppress the actual
        # progression notification. Fall back to the same clean embed.
        log.exception(
            "Animated level-up notification failed for user %s in guild %s; trying embed fallback.",
            uid,
            guild_id_value,
        )
        embed.set_image(url=None)
        try:
            await send(
                embed=embed,
                **options,
            )
            log.info(
                "Sent fallback level-up notification for user %s in guild %s: level %d.",
                uid,
                guild_id_value,
                after_level,
            )
            return True
        except discord.Forbidden:
            log.error(
                "Discord denied permission for level-up notification in channel %s (guild %s, user %s). Check Send Messages, Attach Files, and channel overrides.",
                getattr(channel, "id", "unknown"),
                guild_id_value,
                uid,
            )
        except discord.HTTPException as exc:
            log.error(
                "Discord HTTP error sending level-up notification in channel %s (status=%s): %s",
                getattr(channel, "id", "unknown"),
                exc.status,
                exc,
            )
        except Exception:
            log.exception(
                "Unexpected failure sending fallback level-up notification for user %s in guild %s.",
                uid,
                guild_id_value,
            )
    return False

async def notify_challenge_rewards(guild_id_value,uid,channel):
    if not channel or not await feature_enabled(guild_id_value,"challenges"):
        return False
    pending=await storage.get_pending_challenge_notifications(uid)
    if not pending:
        return False
    total=sum(int(item["xp"]) for item in pending)
    rows=[f"**{item['period']} · {item['name']}** — +{int(item['xp']):,} XP" for item in pending[:12]]
    if len(pending)>12:
        rows.append(f"…and {len(pending)-12} more completed challenges.")
    embed=discord.Embed(
        title="Challenges Completed",
        description=f"<@{uid}> earned **+{total:,} XP**\n\n"+"\n".join(rows),
        color=0x5865F2,
    )
    embed.set_footer(text="Tracker · Daily and weekly challenges")
    try:
        await channel.send(embed=embed,allowed_mentions=discord.AllowedMentions(users=True,roles=False,everyone=False))
    except Exception:
        log.exception("Could not send challenge reward notification for user %s in guild %s.",uid,guild_id_value)
        return False
    await storage.ack_challenge_notifications(uid,{item["key"] for item in pending})
    return True


async def notify_community_rewards(guild_id_value,channel,notification):
    challenge=CHALLENGES.get(notification.get("kind"),CHALLENGES["episodes"])
    unit=challenge["unit"]
    members=notification["members"] if notification["initial"] else [
        uid for uid in notification["members"] if notification["deltas"].get(uid)
    ]
    if not members:
        return True
    for offset in range(0,min(len(members),PAGE_SIZE),PAGE_SIZE):
        lines=[]
        for uid in members[offset:offset+PAGE_SIZE]:
            if notification["initial"]:
                watches=notification["contributions"].get(uid,0)
                lines.append(f"<@{uid}> · {watches:,} {unit}{'s' if watches!=1 else ''} · **+{int(notification['awards'].get(uid,0)):,} XP**")
            else:
                delta=int(notification["deltas"][uid])
                lines.append(f"<@{uid}> · **{delta:+,} XP**")
        if len(members)>PAGE_SIZE:
            lines.append(f"and {len(members)-PAGE_SIZE:,} others · rewards applied to every qualifying member")
        embed=discord.Embed(
            title="Community Challenge Completed" if notification["initial"] else "Community Challenge Rewards Updated",
            description=(
                f"Week of **{notification['key']}** · {challenge['name']} · {notification['target']:,} {unit} goal\n"
                f"**{notification['pool']:,} XP pool** shared by contribution\n\n"
                +"\n".join(lines)
            ),
            color=0xC9DCF0,
        )
        embed.set_footer(text="Tracker · Community challenge")
        try:
            await channel.send(embed=embed,allowed_mentions=discord.AllowedMentions(users=True,roles=False,everyone=False))
        except Exception:
            log.exception("Could not announce community reward for guild %s, week %s.",guild_id_value,notification["key"])
            return False
    await storage.ack_community_notification(guild_id_value,notification["key"],notification["awards"])
    return True


async def refresh_community_state(guild_id_value):
    if not await feature_enabled(guild_id_value,"community"):
        return None
    zone=(await storage.get_timezone(guild_id_value))["name"]
    now=datetime.now(timezone.utc)
    key,start,end=community_week(now,zone)
    state=await storage.get_community_state(guild_id_value,key,start,end,now)
    channel_id=await storage.get_channel(guild_id_value)
    channel=bot.get_channel(int(channel_id)) if channel_id else None
    if channel:
        for notification in state.get("pending_notifications",[]):
            await notify_community_rewards(guild_id_value,channel,notification)
    for change in state.get("changes",[]):
        if change["delta"] <= 0:
            continue
        if channel:
            progression=await storage.get_progression(change["uid"])
            for number in await storage.claim_prestige_notifications(change["uid"]):
                if not await send_prestige_notification(channel.send,f"<@{change['uid']}>",number,progression.get("lifetime_xp",0)):
                    await storage.retry_prestige_notification(change["uid"],number)
                    break
            await notify_level_up(guild_id_value,change["uid"],{"xp":change["before"]},{"xp":change["after"]},channel,
                                  source_label="Community reward")
            await storage.flush()
    return state

def weekly_period(timezone_name, period="current"):
    try:
        tz=ZoneInfo(timezone_name)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        tz=ZoneInfo(DEFAULT_TIMEZONE_NAME)
    today=datetime.now(tz).date()
    monday=today-timedelta(days=today.weekday())
    if period=="previous":
        start=monday-timedelta(days=7)
        end=monday-timedelta(days=1)
    else:
        start=monday
        end=today
    return start,end

def build_weekly_recap(rows, start, end, guild_name, period_label, *, with_visual=False):
    totals={"total":0,"episodes":0,"movies":0,"anime_episodes":0,"anime_movies":0}
    names={row["discord_user_id"]:row.get("simkl_username") or row["discord_user_id"] for row in rows}
    active_days=set()
    user_rows=[]
    for row in rows:
        stats=row["statistics"]
        weekly={"total":0,"episodes":0,"movies":0,"anime_episodes":0,"anime_movies":0}
        for day, counts in (stats.get("watch_dates") or {}).items():
            try:
                date=datetime.strptime(day,"%Y-%m-%d").date()
            except (TypeError,ValueError):
                continue
            if start <= date <= end:
                active_days.add(day)
                for key in weekly:
                    weekly[key] += int(counts.get(key,0))
        if weekly["total"]:
            user_rows.append((weekly["total"],row["discord_user_id"]))
            for key in totals:
                totals[key] += weekly[key]
    user_rows.sort(key=lambda x:(-x[0],x[1]))
    description=(f"**{period_label}**\n"
                 f"📺 Episodes: **{totals['episodes']:,}**\n"
                 f"🎬 Movies: **{totals['movies']:,}**\n"
                 f"🌸 Anime episodes: **{totals['anime_episodes']:,}**\n"
                 f"🎞️ Anime movies: **{totals['anime_movies']:,}**\n"
                 f"👀 Total watches: **{totals['total']:,}**\n"
                 f"📅 Active days: **{len(active_days):,}**\n"
                 f"👥 Active users: **{len(user_rows):,}**")
    if user_rows:
        lines=[]
        medals=["🥇","🥈","🥉"]
        for index,(total,uid) in enumerate(user_rows[:5]):
            prefix=medals[index] if index<3 else f"**{index+1}.**"
            lines.append(f"{prefix} <@{uid}> — **{total:,}** watch{'es' if total != 1 else ''}")
        if len(user_rows)>PAGE_SIZE:
            lines.append(f"and {len(user_rows)-PAGE_SIZE:,} others")
        description += "\n\n**Top Watchers**\n" + "\n".join(lines)
    else:
        description += "\n\nNo watch activity was recorded during this period."
    embed=discord.Embed(title=f"📅 {guild_name} · Weekly Recap",description=description,color=0x5865F2)
    embed.set_footer(text=f"{start.isoformat()} → {end.isoformat()}")
    if with_visual:
        metrics=[("Total watches",totals["total"]),("Episodes",totals["episodes"]),("Movies",totals["movies"]),
                 ("Anime episodes",totals["anime_episodes"]),("Anime movies",totals["anime_movies"]),("Active days",len(active_days))]
        leaders=[("Active users",str(len(user_rows)))]
        leaders.extend((f"#{n} watcher",f"{names[uid]} · {total:,} watches") for n,(total,uid) in enumerate(user_rows[:4],1))
        return embed,metrics,leaders
    return embed

async def generate_weekly_recap(guild, period="current"):
    timezone_info=await storage.get_timezone(guild.id)
    start,end=weekly_period(timezone_info["name"],period)
    rows=await storage.get_guild_statistics(guild.id)
    label="Current week" if period=="current" else "Previous week"
    return build_weekly_recap(rows,start,end,guild.name,label,with_visual=True),f"{start.isoformat()} → {end.isoformat()}"

async def send_visual_summary(send, guild_name, heading, result, filename):
    (embed,metrics,leaders),subtitle=result
    try:
        image=await asyncio.to_thread(render_summary_png,guild_name,heading,subtitle,metrics,leaders)
        embed.set_image(url=f"attachment://{filename}")
        await send(embed=embed,file=discord.File(image,filename=filename))
    except Exception:
        log.exception("Could not render or send %s summary; sending embed fallback.",heading)
        embed.set_image(url=None)
        await send(embed=embed)

async def send_weekly_recap(guild, period="current"):
    if not await feature_enabled(guild.id,"weekly_recaps"):
        return False
    channel_id=await storage.get_channel(guild.id)
    if channel_id is None:
        return False
    channel=bot.get_channel(int(channel_id))
    if channel is None:
        try:
            channel=await bot.fetch_channel(int(channel_id))
        except Exception:
            return False
    await send_visual_summary(channel.send,guild.name,"weekly recap",await generate_weekly_recap(guild,period),"weekly-recap.png")
    return True

async def send_due_weekly_recaps():
    for guild in bot.guilds:
        try:
            if not await feature_enabled(guild.id,"weekly_recaps"):
                continue
            timezone_info=await storage.get_timezone(guild.id)
            tz=ZoneInfo(timezone_info["name"])
            now=datetime.now(tz)
            if now.weekday() != 0 or now.hour < 9:
                continue
            previous_start,previous_end=weekly_period(timezone_info["name"],"previous")
            week_key=previous_end.isoformat()
            if await storage.get_weekly_recap_last_sent(guild.id) == week_key:
                continue
            if await send_weekly_recap(guild,"previous"):
                await storage.set_weekly_recap_last_sent(guild.id,week_key)
                log.info("Sent weekly recap for guild %s (%s to %s).",guild.id,previous_start,previous_end)
        except Exception:
            log.exception("Weekly recap failed for guild %s.",guild.id)

STYLE_CHOICES=[app_commands.Choice(name="Rich (large artwork)",value="rich"),app_commands.Choice(name="Minimal (small artwork)",value="minimal")]
ARTWORK_CHOICES=[app_commands.Choice(name="Automatic",value="auto"),app_commands.Choice(name="Poster only",value="poster"),app_commands.Choice(name="Backdrop",value="backdrop")]
TEXT_CHOICES=[app_commands.Choice(name="Short",value="short"),app_commands.Choice(name="Detailed",value="detailed")]
EPISODE_FORMAT_CHOICES=[
    app_commands.Choice(name="Bold Text Format",value="false"),
    app_commands.Choice(name="Code Block Format",value="true"),
]
RATING_CHOICES=[app_commands.Choice(name="Show",value="true"),app_commands.Choice(name="Hide",value="false")]

NOT_ADMIN_MESSAGE="You need the Manage Server permission to do that."

@bot.tree.command(name="tracker-features",description="(Admin) Configure optional features or use activity-only mode.")
@app_commands.default_permissions(manage_guild=True)
@app_commands.choices(
    preset=[app_commands.Choice(name="Activity only",value="classic"),app_commands.Choice(name="All features",value="all")],
    feature=[app_commands.Choice(name=label,value=key) for key,label in FEATURE_LABELS.items()],
)
async def simkl_features(i, preset: app_commands.Choice[str] | None = None,
                         feature: app_commands.Choice[str] | None = None, enabled: bool | None = None):
    g=guild_id(i)
    if not g or not is_admin(i):
        await i.response.send_message(NOT_ADMIN_MESSAGE,ephemeral=True)
        return
    if (preset and (feature is not None or enabled is not None)) or ((feature is None)!=(enabled is None)):
        await i.response.send_message("Choose a preset, or a feature with enabled True/False. Omit all options to view settings.",ephemeral=True)
        return
    if preset:
        await storage.set_features(g,dict.fromkeys(DEFAULT_FEATURES,preset.value=="all"))
    elif feature:
        await storage.set_features(g,{feature.value:enabled})
    features=await storage.get_features(g)
    lines=[f"{'✓' if value else '—'} **{FEATURE_LABELS[key]}:** {'On' if value else 'Off'}" for key,value in features.items() if key in FEATURE_LABELS]
    await i.response.send_message(
        "**Server features**\n"+"\n".join(lines)+
        "\n\nActivity tracking stays available. Challenges and community goals also require progression. "
        "Disabled commands may still appear in Discord's menu. Existing history and global XP are retained and watch XP continues syncing so shared accounts stay consistent.",ephemeral=True)

@bot.tree.command(name="tracker-timezone",description="(Admin) Set or view the server timezone.")
@app_commands.describe(timezone="IANA timezone such as Asia/Singapore, or 'reset' to use the environment default")
async def simkl_timezone(i, timezone: str | None = None):
    g=guild_id(i)
    if not g or not is_admin(i):
        await i.response.send_message(NOT_ADMIN_MESSAGE,ephemeral=True)
        return

    if timezone is None or not timezone.strip():
        info=await storage.get_timezone(g)
        now=datetime.now(ZoneInfo(info["name"]))
        source_label={"server":"Server setting","environment":"Environment default","built-in":"Built-in default"}.get(info["source"],"Default")
        await i.response.send_message(
            f"🕐 **Timezone:** {info['name']}\n**Source:** {source_label}\n**Current local time:** {now.strftime('%Y-%m-%d %H:%M:%S')}",
            ephemeral=True,
        )
        return

    value=timezone.strip()
    if value.lower() == "reset":
        await storage.set_timezone(g, None)
        info=await storage.get_timezone(g)
        await i.response.send_message(
            f"Reset the server timezone to **{info['name']}** (environment/default setting).",
            ephemeral=True,
        )
        return

    try:
        ZoneInfo(value)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        await i.response.send_message(
            f"❌ **{value}** is not a valid IANA timezone. Examples: **Asia/Singapore**, **America/New_York**, **Europe/London**.",
            ephemeral=True,
        )
        return

    await storage.set_timezone(g, value)
    now=datetime.now(ZoneInfo(value))
    await i.response.send_message(
        f"Set the server timezone to **{value}**.\nCurrent local time: **{now.strftime('%Y-%m-%d %H:%M:%S')}**",
        ephemeral=True,
    )

WEEKLY_PERIOD_CHOICES=[
    app_commands.Choice(name="Current week",value="current"),
    app_commands.Choice(name="Previous week",value="previous"),
]

@bot.tree.command(name="tracker-weekly-recap",description="(Admin) Post a weekly tracker watch recap.")
@app_commands.choices(period=WEEKLY_PERIOD_CHOICES)
@app_commands.describe(period="Choose the week to generate; use this to test without waiting for the weekly schedule")
async def simkl_weekly_recap(i, period: app_commands.Choice[str] | None = None):
    g=guild_id(i)
    if not g or not is_admin(i):
        await i.response.send_message(NOT_ADMIN_MESSAGE,ephemeral=True)
        return
    if not i.guild:
        return
    channel_id=await storage.get_channel(g)
    if channel_id is None:
        await i.response.send_message("No posting channel is configured for this server. Use `/tracker-setchannel` first.",ephemeral=True)
        return
    channel=bot.get_channel(int(channel_id))
    if channel is None:
        try:
            channel=await bot.fetch_channel(int(channel_id))
        except Exception:
            await i.response.send_message("The configured posting channel could not be accessed.",ephemeral=True)
            return
    selected=period.value if period else "current"
    await i.response.defer(ephemeral=True)
    await send_visual_summary(channel.send,i.guild.name,"weekly recap",await generate_weekly_recap(i.guild,selected),"weekly-recap.png")
    await i.followup.send(f"Posted the **{'current' if selected == 'current' else 'previous'} week** recap in {channel.mention}.",ephemeral=True)


async def achievement_autocomplete(i, current: str):
    needle=current.casefold()
    return [app_commands.Choice(name=f"{a['emoji']} {a['name']}",value=aid)
            for aid,a in all_achievements()
            if needle in a["name"].casefold() or needle in aid.casefold()][:25]



@bot.tree.command(name="tracker-challenges", description="View your current daily and weekly watch challenges.")
async def simkl_challenges(i):
    uid = str(i.user.id)
    today = datetime.now(timezone.utc).date()
    daily, weekly = challenges_for(today)
    state = await storage.get_challenge_state(uid)
    events = state.get("xp_events", [])
    completed = state.get("challenge_completions", {})
    daily_key = f"daily:{today.isoformat()}"
    monday = today - timedelta(days=today.weekday())
    weekly_key = f"weekly:{monday.isoformat()}"
    ds = f"{today.isoformat()}T00:00:00+00:00"
    de = f"{today.isoformat()}T23:59:59+00:00"
    ws = f"{monday.isoformat()}T00:00:00+00:00"
    we = f"{(monday + timedelta(days=6)).isoformat()}T23:59:59+00:00"

    def lines(challenges, key, start, end):
        out = []
        for ch in challenges:
            done = ch["id"] in completed.get(key, {})
            progress = ch["target"] if done else challenge_progress(events, ch, start, end)
            mark = "✓" if done else "□"
            out.append(f'{mark} **{ch["name"]}** — {progress}/{ch["target"]} · +{ch["xp"]:,} XP')
        return "\n".join(out)

    e = discord.Embed(title="SIMKL Challenges", color=0x5865F2)
    daily_reset=datetime(today.year,today.month,today.day,tzinfo=timezone.utc)+timedelta(days=1)
    weekly_reset=datetime(monday.year,monday.month,monday.day,tzinfo=timezone.utc)+timedelta(days=7)
    e.add_field(name="Daily", value=f"Resets {discord.utils.format_dt(daily_reset,style='R')} · {discord.utils.format_dt(daily_reset,style='F')}\n"+lines(daily,daily_key,ds,de), inline=False)
    e.add_field(name="Weekly", value=f"Resets {discord.utils.format_dt(weekly_reset,style='R')} · {discord.utils.format_dt(weekly_reset,style='F')}\n"+lines(weekly,weekly_key,ws,we), inline=False)
    e.set_footer(text="Daily and weekly challenges reset at 00:00 UTC.")
    await i.response.send_message(embed=e)


@bot.tree.command(name="tracker-community", description="View this server's rotating weekly watch challenge.")
async def simkl_community(i):
    g=guild_id(i)
    if not g:
        await i.response.send_message("This command must be used in a server.",ephemeral=True)
        return
    await i.response.defer()
    state=await refresh_community_state(g)
    if not state:
        await i.followup.send("No community challenge is available yet.",ephemeral=True)
        return
    total=state["total"]
    target=state["target"]
    challenge=CHALLENGES.get(state.get("kind"),CHALLENGES["episodes"])
    unit=challenge["unit"]
    filled=min(20,round(20*total/target))
    bar="█"*filled+"░"*(20-filled)
    ends=datetime.fromisoformat(state["end"])
    contributors=sorted(state["contributions"].items(),key=lambda item:(-item[1],item[0]))
    rows=[f"<@{uid}> · **{count:,}** {unit}{'s' if count!=1 else ''}" for uid,count in contributors[:PAGE_SIZE]]
    if len(contributors)>PAGE_SIZE:
        rows.append(f"and {len(contributors)-PAGE_SIZE:,} others")
    description=(f"**{challenge['name']}: {target:,} {unit}s together this week**\n{bar}\n"
                 f"**{total:,} / {target:,}** {unit}s · **{state['pool']:,} XP pool**\n"
                 f"Ends {discord.utils.format_dt(ends,style='R')} · {discord.utils.format_dt(ends,style='F')}\n\n"
                 f"Your contribution: **{state['contributions'].get(str(i.user.id),0):,}** {unit}s")
    if state["status"]=="goal_reached":
        description+="\n**Goal reached!** The pool is distributed by contribution after the week ends."
    elif state["status"]=="active":
        description+=f"\nContribute at least one {unit} before the deadline to qualify if the goal is reached."
    e=discord.Embed(title=f"{i.guild.name} · Community Challenge",description=description,color=0xC9DCF0)
    e.add_field(name="Contributors",value="\n".join(rows) if rows else f"No {unit}s contributed yet.",inline=False)
    e.set_footer(text="Server-local weekly goal · bonus XP is added to normal watch XP")
    await i.followup.send(embed=e,allowed_mentions=discord.AllowedMentions.none())

@bot.tree.command(name="tracker-user-reset",description="Reset your tracker history for this server.")
@app_commands.describe(confirm="Confirm that you want to reset your server-local tracking state")
async def simkl_user_reset(i, confirm: bool = False):
    g=guild_id(i)
    if not g:
        await i.response.send_message("This command must be used in a server.",ephemeral=True)
        return

    uid=str(i.user.id)
    user=await storage.get_user(uid)
    provider=await storage.get_activity_provider(g,uid)
    try:
        adapter, _ = await selected_provider(g, uid)
        linked = True
    except ValueError:
        linked = False
    if not linked:
        await i.response.send_message(
            "You don't have a linked account for your selected source. Use /tracker-link first.",
            ephemeral=True,
        )
        return

    if not confirm:
        await i.response.send_message(
            f"This resets your {adapter.manifest.display_name} server tracking state "
            "and achievements. Your linked accounts, shared XP and personal style stay intact. "
            "The selected account's history will be imported again without old activity posts. "
            "Run /tracker-user-reset with confirm set to True to continue.",
            ephemeral=True,
        )
        return

    reset_at=now_iso()
    if not await storage.reset_user_tracking(g,uid,reset_at):
        await i.response.send_message(
            "I couldn't find your tracking data for this server.",
            ephemeral=True,
        )
        return

    await i.response.send_message(
        "Your selected tracker state for this server has been reset. "
        "Your account remains linked; its history will be imported silently at the next poll.",
        ephemeral=True,
    )


@bot.tree.command(name="tracker-achievements",description="Show your tracker achievements.")
@app_commands.describe(user="Optional server member to view")
async def simkl_achievements(i,user: discord.Member | None = None):
    g=guild_id(i)
    if not g:
        await i.response.send_message("This command must be used in a server.",ephemeral=True)
        return
    target=user or i.user
    await evaluate_achievements(g,str(target.id))
    unlocked=await storage.get_achievements(g,str(target.id))
    timezone_info=await storage.get_timezone(g)
    progression=await storage.get_progression(str(target.id))
    progress=achievement_progress_from_events(
        progression.get("xp_events", []),
        timezone_info["name"],
    )

    lines=[]
    for aid,achievement in all_achievements():
        if aid in unlocked:
            stamp=unlocked[aid].get("unlocked_at")
            when=""
            if stamp:
                try:
                    dt=datetime.fromisoformat(stamp.replace("Z","+00:00"))
                    when=f" — <t:{int(dt.timestamp())}:d>"
                except (TypeError,ValueError):
                    pass
            lines.append(f"{achievement['emoji']} **{achievement['name']}** · **+{int(achievement.get('xp', 0)):,} XP**{when}\n{achievement['description']}")
        else:
            current=progress.get(achievement["category"],0)
            lines.append(f"🔒 **{achievement['name']}** · **+{int(achievement.get('xp', 0)):,} XP** — {min(current,achievement['threshold']):,}/{achievement['threshold']:,}\n{achievement['description']}")

    pages=[]
    page=[]
    length=0
    for line in lines:
        if page and length+len(line)+2>3500:
            pages.append(page)
            page=[]
            length=0
        page.append(line)
        length+=len(line)+2
    if page:
        pages.append(page)
    embeds=[]
    for index,chunk in enumerate(pages):
        embed=discord.Embed(title=f"🏆 {target.display_name}'s Achievements · {index+1}/{len(pages)}",
                            description="\n\n".join(chunk),color=0xF1C40F)
        embed.set_footer(text=f"{len(unlocked)}/{len(ACHIEVEMENTS)} unlocked")
        embeds.append(embed)
    if embeds:
        embeds[0].set_thumbnail(url=target.display_avatar.url)
    await i.response.send_message(embeds=embeds)


@bot.tree.command(name="tracker-debug",description="(Admin) Privately preview progression notifications without changing XP.")
@app_commands.default_permissions(manage_guild=True)
@app_commands.choices(
    feature=[
        app_commands.Choice(name="Level up", value="level"),
        app_commands.Choice(name="Rank up", value="rank"),
        app_commands.Choice(name="Achievement unlocked", value="achievement"),
        app_commands.Choice(name="Prestige unlocked", value="prestige"),
    ],
    rank=[app_commands.Choice(name=name,value=minimum) for minimum,name in RANKS],
)
@app_commands.autocomplete(achievement=achievement_autocomplete)
@app_commands.describe(
    feature="Notification to preview",
    achievement="Required for an achievement preview",
    level="Optional target level (2-100) for a level/rank preview",
    rank="Optional rank to preview; a chosen level must belong to it",
    prestige="Optional prestige tier (0-1000) for a level/rank preview, or 1-1000 for prestige",
)
async def simkl_debug(
    i,
    feature: app_commands.Choice[str],
    achievement: str | None = None,
    level: app_commands.Range[int, 2, 100] | None = None,
    rank: app_commands.Choice[int] | None = None,
    prestige: app_commands.Range[int, 0, 1000] | None = None,
):
    if not guild_id(i) or not is_admin(i):
        await i.response.send_message(NOT_ADMIN_MESSAGE,ephemeral=True)
        return
    if feature.value == "achievement":
        if achievement is None or achievement not in ACHIEVEMENTS:
            await i.response.send_message("Choose an achievement to preview.",ephemeral=True)
            return
        if level is not None or rank is not None:
            await i.response.send_message("Level and rank options are only for progression previews.",ephemeral=True)
            return
        if prestige is not None:
            await i.response.send_message("The prestige option is only for a prestige preview.",ephemeral=True)
            return
    elif achievement is not None:
        await i.response.send_message("The achievement option is only for achievement previews.",ephemeral=True)
        return
    elif feature.value not in {"level", "rank", "prestige"}:
        await i.response.send_message("Unknown preview feature.", ephemeral=True)
        return

    if feature.value == "prestige" and (level is not None or rank is not None or prestige == 0):
        await i.response.send_message("For a prestige unlock preview, choose a prestige from 1 to 1000 without a level or rank.",ephemeral=True)
        return

    if feature.value in {"level", "rank"}:
        current_progression=await storage.get_progression(str(i.user.id))
        if level is None:
            current=level_progress(int(current_progression.get("xp", 0)))[0]
            if feature.value == "rank":
                level=rank.value if rank else next((minimum for minimum, _ in RANKS if minimum > current), RANKS[-1][0])
            else:
                level=max(2, rank.value) if rank else min(100, max(2, current + 1))
        if rank and rank_for_level(level) != rank_for_level(rank.value):
            await i.response.send_message(f"Level {level} belongs to {rank_for_level(level)}, not {rank.name}.",ephemeral=True)
            return
        if feature.value == "rank" and rank_for_level(level - 1) == rank_for_level(level):
            await i.response.send_message("Choose a rank boundary: level 10, 20, 30, …, or 90.",ephemeral=True)
            return
        preview_prestige=prestige if prestige is not None else int(current_progression.get("prestige",0))

    # Defer before rendering; GIF generation can exceed Discord's initial response window.
    await i.response.defer(ephemeral=True)
    if feature.value == "achievement":
        sent=await send_achievement_notification(i.followup.send, i.user.mention, achievement, preview=True)
    elif feature.value == "prestige":
        current=await storage.get_progression(str(i.user.id))
        number=prestige if prestige is not None else int(current.get("prestige", 0)) + 1
        sent=await send_prestige_notification(i.followup.send, i.user.mention, number, current.get("lifetime_xp", 0), preview=True)
    else:
        sent=await notify_level_up(
            i.guild.id, str(i.user.id),
            {"xp": xp_for_level(level - 1) if level > 2 else 0,"prestige":preview_prestige},
            {"xp": xp_for_level(level),"prestige":preview_prestige}, i.channel,
            preview_interaction=i,
        )
    if not sent:
        await i.followup.send("Could not send the preview. Check the bot's permissions and logs.", ephemeral=True)

async def show_profile(i,user):
    g=guild_id(i)
    if not g:
        await i.response.send_message("This command must be used in a server.",ephemeral=True); return
    target=user or i.user
    await i.response.defer()
    await evaluate_achievements(g,str(target.id))
    stats=await storage.get_statistics(g,str(target.id))
    source = await storage.get_activity_provider(g, str(target.id)) or 'simkl'
    active_wetrakr = source != 'simkl'
    source_label = provider_registry.get(source).manifest.display_name
    history=await storage.get_history_import_state(g,str(target.id))
    if not active_wetrakr and history["linked"] and not history["complete"] and not (int(stats.get("episodes_watched",0))+int(stats.get("movies_watched",0))):
        await i.followup.send("This SIMKL history import is still pending. Watch totals will appear here when it finishes; the bot retries automatically.",ephemeral=True)
        return
    progression=await storage.get_progression(str(target.id))
    timezone_info=await storage.get_timezone(g)
    unlocked_achievements=await storage.get_achievements(g,str(target.id))
    current,longest=calculate_streaks(stats.get("watch_dates"),timezone_info["name"])
    features=await storage.get_features(g)
    if not features["progression"]:
        embed=discord.Embed(title=f"{target.display_name}'s {source_label} Statistics",color=0x5865F2)
        embed.add_field(name="Watch history",value=f"{stats.get('episodes_watched',0):,} episodes · {stats.get('movies_watched',0):,} movies",inline=False)
        embed.add_field(name="Anime (included above)",value=f"{stats.get('anime_episodes_watched',0):,} episodes · {stats.get('anime_movies_watched',0):,} movies",inline=False)
        embed.add_field(name="Streak",value=f"{current} current · {longest} longest",inline=False)
        await i.followup.send(embed=embed)
        return
    today=datetime.now(ZoneInfo(timezone_info["name"])).date()
    data=profile_snapshot(stats,progression,unlocked_achievements,current,longest,today=today)
    data["provider_label"] = source_label
    data["achievements_enabled"]=features["achievements"]
    embed=discord.Embed(
        title=f"{target.display_name}'s {source_label} Profile",
        description=(f"Level **{data['level']}** · **{data['rank']}** · Prestige **{data['prestige']}**\n"
                     f"**{data['xp']:,} XP** · **{data['total']:,} watches** · "
                     + (f"**{data['achievements']}/{data['achievement_total']} achievements**" if features["achievements"] else "Achievements disabled")),
        color=discord.Color.from_rgb(*accent_for_tier(data["level"],data["prestige"])),
    )
    try:
        image=await asyncio.to_thread(render_profile_png,target.display_name,data)
        embed.set_image(url="attachment://profile.png")
        await i.followup.send(embed=embed,file=discord.File(image,filename="profile.png"))
    except Exception:
        log.exception("Could not send profile image for %s; sending embed fallback.",target.id)
        embed.set_image(url=None)
        embed.add_field(name="Watching",value=f"{data['episodes']:,} episodes · {data['movies']:,} movies · {data['anime_episodes']:,} anime episodes · {data['anime_movies']:,} anime movies",inline=False)
        embed.add_field(name="Streak",value=f"{current} current · {longest} longest",inline=False)
        await i.followup.send(embed=embed)


@bot.tree.command(name="tracker-mapping",description="Privately inspect how your linked trackers match watches and XP.")
async def tracker_mapping(i):
    g=guild_id(i)
    if not g:
        await i.response.send_message("This command must be used in a server.",ephemeral=True)
        return
    uid=str(i.user.id)
    user=await storage.get_user(uid)
    if not user:
        await i.response.send_message("Link a tracking account with /tracker-link first.",ephemeral=True)
        return
    audit=await storage.get_mapping_audit(uid)
    await i.response.send_message(
        "**Your watch mapping**\n"
        f"Verified cross-tracker matches: **{audit['verified']:,}**\n"
        f"Older title-only matches: **{audit['legacy']:,}**\n"
        f"Unpaired watches: **{audit['unpaired']:,}**\n"
        f"Needs review: **{audit['review']:,}**\n"
        f"Possible double XP awards: **{audit['double_awards']:,}**\n\n"
        "This is a read-only preview. Existing XP is not changed by this command.",
        ephemeral=True,
    )


@bot.tree.command(name="tracker-stats",description="Show a visual tracker profile with watches, XP, and achievements.")
@app_commands.describe(user="Optional server member to view")
async def simkl_stats(i,user: discord.Member | None = None):
    await show_profile(i,user)


LEADERBOARD_CHOICES=[
    app_commands.Choice(name="Total watches",value="total"),
    app_commands.Choice(name="Episodes",value="episodes"),
    app_commands.Choice(name="Movies",value="movies"),
    app_commands.Choice(name="Anime",value="anime"),
    app_commands.Choice(name="XP / progression",value="xp"),
    app_commands.Choice(name="Level",value="level"),
    app_commands.Choice(name="Prestige",value="prestige"),
]

@bot.tree.command(name="tracker-leaderboard",description="Show the server's tracker watch leaderboard.")
@app_commands.choices(category=LEADERBOARD_CHOICES)
async def simkl_leaderboard(i,category: app_commands.Choice[str] | None = None):
    g=guild_id(i)
    if not g:
        await i.response.send_message("This command must be used in a server.",ephemeral=True); return
    category=category.value if category else "total"
    progression_enabled=await feature_enabled(g,"progression")
    if not progression_enabled and category in {"xp","level","prestige"}:
        await i.response.send_message("Progression is disabled in this server. Choose a watch category.",ephemeral=True)
        return
    await i.response.defer()
    rows=await storage.get_guild_leaderboard_snapshot(g)
    values=[]
    for row in rows:
        row["total"]=row["episodes"]+row["movies"]
        row["level"]=level_progress(row["xp"])[0]
        value=row[category]
        if value>0 or (category in {"xp","level","prestige"} and (row["total"]>0 or row["xp"]>0 or row["prestige"]>0)):
            member=i.guild.get_member(int(row["discord_user_id"]))
            row["name"]=member.display_name if member else row["simkl_username"]
            values.append(row)
    if category in {"xp","level","prestige"}:
        values.sort(key=lambda row:(-row["prestige"],-row["xp"],row["name"].casefold()))
    else:
        values.sort(key=lambda row:(-row[category],-row["prestige"],-row["xp"],row["name"].casefold()))
    if not values:
        pending=sum(not row.get("history_seeded",True) for row in rows)
        message=(f"History import is pending for **{pending}** linked user(s). Try again after it finishes."
                 if pending else "No leaderboard data has been recorded in this server yet.")
        await i.followup.send(message,ephemeral=True); return
    labels={"total":"Total watches","episodes":"Episodes","movies":"Movies","anime":"Anime","xp":"XP progression","level":"Level","prestige":"Prestige"}
    if not progression_enabled:
        values.sort(key=lambda row:(-row[category],row["name"].casefold()))

    async def render_page(page_rows, start, page, pages):
        embed=discord.Embed(title=f"{i.guild.name} · {labels[category]}",color=0xEFBE69)
        embed.set_footer(text=f"Page {page}/{pages} · {len(values):,} members · Server watch counts · Global XP and prestige" if progression_enabled
                         else f"Page {page}/{pages} · {len(values):,} members · Server watch counts")
        if progression_enabled:
            try:
                image=await asyncio.to_thread(render_leaderboard_png,i.guild.name,labels[category],page_rows,rank_offset=start)
                embed.set_image(url="attachment://leaderboard.png")
                return embed,discord.File(image,filename="leaderboard.png")
            except Exception:
                log.exception("Could not render leaderboard page for guild %s; using embed fallback.",g)
        embed.description="\n".join(
            f"**{n}.** <@{row['discord_user_id']}> · " +
            (f"P{row['prestige']} L{row['level']} · {row['xp']:,} XP · {row['total']:,} watches"
             if progression_enabled else f"**{row[category]:,}**")
            for n,row in enumerate(page_rows,start+1))
        return embed,None

    view=MemberPages(i.user.id,values,render_page)
    embed,file=await view.render()
    kwargs={"embed":embed,"view":view,"allowed_mentions":discord.AllowedMentions.none()}
    if file:
        kwargs["file"]=file
    view.message=await i.followup.send(**kwargs,wait=True)


def build_server_stats(rows, guild_name, *, with_visual=False):
    linked=len(rows)
    names={row["discord_user_id"]:row.get("simkl_username") or row["discord_user_id"] for row in rows}
    episodes=0
    movies=0
    anime_episodes=0
    anime_movies=0
    active_days=set()
    total_watchers=[]
    title_totals={}

    for row in rows:
        stats=row["statistics"]
        user_total=int(stats.get("episodes_watched",0))+int(stats.get("movies_watched",0))
        episodes += int(stats.get("episodes_watched",0))
        movies += int(stats.get("movies_watched",0))
        anime_episodes += int(stats.get("anime_episodes_watched",0))
        anime_movies += int(stats.get("anime_movies_watched",0))
        active_days.update((stats.get("watch_dates") or {}).keys())

        if user_total:
            total_watchers.append((user_total,row["discord_user_id"]))

        for key,record in (stats.get("titles") or {}).items():
            if not isinstance(record,dict):
                continue
            count=int(record.get("count",0))
            if count <= 0:
                continue
            existing=title_totals.get(key)
            if existing is None:
                title_totals[key]={
                    "title": record.get("title") or "Untitled",
                    "type": record.get("type") or "watch",
                    "count": count,
                }
            else:
                existing["count"] += count

    total_watches=episodes + movies
    total_watchers.sort(key=lambda value:(-value[0],value[1]))
    top_titles=sorted(
        title_totals.values(),
        key=lambda value:(-value["count"],value["title"].lower()),
    )

    description=(
        f"👥 **{linked:,}** tracked users\n"
        f"👀 **{total_watches:,}** total watches\n"
        f"📺 **{episodes:,}** episodes\n"
        f"🎬 **{movies:,}** movies\n"
        f"🌸 **{anime_episodes:,}** anime episodes\n"
        f"🎞️ **{anime_movies:,}** anime movies\n"
        f"📅 **{len(active_days):,}** active watch days"
    )

    embed=discord.Embed(
        title=f"📊 {guild_name} · Server Statistics",
        description=description,
        color=0x5865F2,
    )

    if total_watchers:
        uid=total_watchers[0][1]
        embed.add_field(
            name="🔥 Most Active Watcher",
            value=f"<@{uid}> — **{total_watchers[0][0]:,}** watches",
            inline=True,
        )

    if top_titles:
        top=top_titles[0]
        type_emoji={
            "episode":"📺",
            "anime_episode":"🌸",
            "movie":"🎬",
            "anime_movie":"🎞️",
        }.get(top["type"],"🎬")
        embed.add_field(
            name="🏆 Most Watched Title",
            value=f"{type_emoji} **{top['title']}** — **{top['count']:,}**",
            inline=True,
        )

    if total_watches:
        episode_share=(episodes / total_watches) * 100
        movie_share=(movies / total_watches) * 100
        embed.add_field(
            name="🍿 Watch Breakdown",
            value=f"📺 Episodes: **{episode_share:.1f}%**\n🎬 Movies: **{movie_share:.1f}%**",
            inline=True,
        )

    if len(top_titles) > 1:
        lines=[]
        for index,top in enumerate(top_titles[:5],1):
            lines.append(f"**{index}.** {top['title']} — **{top['count']:,}**")
        embed.add_field(
            name="🎞️ Top 5 Titles",
            value="\n".join(lines),
            inline=False,
        )

    embed.set_footer(text="All-time statistics · Server-wide")
    if with_visual:
        metrics=[("Total watches",total_watches),("Episodes",episodes),("Movies",movies),
                 ("Anime episodes",anime_episodes),("Anime movies",anime_movies),("Active days",len(active_days))]
        leaders=[("Tracked users",str(linked))]
        leaders.extend((f"#{n} watcher",f"{names[uid]} · {total:,} watches") for n,(total,uid) in enumerate(total_watchers[:2],1))
        leaders.extend((f"#{n} title",f"{top['title']} · {top['count']:,}") for n,top in enumerate(top_titles[:2],1))
        return embed,metrics,leaders
    return embed


@bot.tree.command(name="tracker-server-stats",description="Show this server's tracker watch statistics.")
async def simkl_server_stats(i):
    g=guild_id(i)
    if not g:
        await i.response.send_message("This command must be used in a server.",ephemeral=True); return
    rows=await storage.get_guild_statistics(g)
    if not any(
        int(row["statistics"].get("episodes_watched",0)) + int(row["statistics"].get("movies_watched",0))
        for row in rows
    ):
        pending=sum(not row.get("history_seeded",True) for row in rows)
        message=(f"History import is pending for **{pending}** linked user(s). Try again after it finishes."
                 if pending else "No watch statistics have been recorded in this server yet.")
        await i.response.send_message(message,ephemeral=True)
        return
    await i.response.defer()
    result=build_server_stats(rows,i.guild.name,with_visual=True),"All-time statistics · Server-wide"
    await send_visual_summary(i.followup.send,i.guild.name,"server statistics",result,"server-stats.png")




RANDOM_TYPE_CHOICES=[
    app_commands.Choice(name="Everything",value="all"),
    app_commands.Choice(name="TV shows",value="shows"),
    app_commands.Choice(name="Anime",value="anime"),
    app_commands.Choice(name="Movies",value="movies"),
]

RANDOM_GENRE_CHOICES=[
    app_commands.Choice(name="Any genre",value=""),
    app_commands.Choice(name="Action",value="action"),
    app_commands.Choice(name="Adventure",value="adventure"),
    app_commands.Choice(name="Animation",value="animation"),
    app_commands.Choice(name="Comedy",value="comedy"),
    app_commands.Choice(name="Crime",value="crime"),
    app_commands.Choice(name="Drama",value="drama"),
    app_commands.Choice(name="Fantasy",value="fantasy"),
    app_commands.Choice(name="Horror",value="horror"),
    app_commands.Choice(name="Mystery",value="mystery"),
    app_commands.Choice(name="Romance",value="romance"),
    app_commands.Choice(name="Science Fiction",value="science fiction"),
    app_commands.Choice(name="Thriller",value="thriller"),
]

def random_picker_title(item, media_type):
    obj=item.get("movie") if media_type=="movies" else item.get("show")
    obj=obj or {}
    return obj.get("title") or "Untitled"

def random_picker_ids(item, media_type):
    obj=item.get("movie") if media_type=="movies" else item.get("show")
    obj=obj or {}
    return obj.get("ids") or {}

def random_picker_added_at(item):
    for key in ("added_to_list_at","date_added","added_at","created_at"):
        value=item.get(key)
        if value:
            return value
    return None

def random_picker_episode_count_from_item(item):
    """Fallback count when TMDB has no series metadata."""
    total=0
    for season in item.get("seasons") or []:
        for episode in season.get("episodes") or []:
            if isinstance(episode,dict):
                total+=1
    return total or None


async def random_picker_media_details(item, media_type):
    """Return the best display title and total episode count for a pick."""
    ids=random_picker_ids(item,media_type)
    title=random_picker_title(item,media_type)
    episode_count=None

    if media_type=="movies":
        tmdb_id=ids.get("tmdb")
        if tmdb_id is not None:
            english_title=await tmdb.get_movie_title(tmdb_id,prefer_english=True)
            if english_title:
                title=english_title
        return title,episode_count,ids

    tmdb_id=ids.get("tmdb")
    if media_type=="anime":
        # SIMKL can store seasonal anime under separate entries. Prefer the
        # TVDB -> canonical TMDB mapping so both the English title and total
        # episode count come from the complete series rather than one season.
        tmdb_id=await resolve_anime_tmdb_id(ids) or tmdb_id

    if tmdb_id is not None:
        english_title=await tmdb.get_tv_title(tmdb_id,prefer_english=(media_type=="anime"))
        if english_title:
            title=english_title
        episode_count=await tmdb.get_tv_episode_count(tmdb_id)

    if episode_count is None:
        episode_count=random_picker_episode_count_from_item(item)

    return title,episode_count,ids

async def random_picker_matches_genre(item, media_type, genre):
    if not genre:
        return True
    ids=random_picker_ids(item,media_type)
    tmdb_id=ids.get("tmdb")
    if tmdb_id is None:
        return False
    try:
        if media_type=="movies":
            data=await tmdb._get_json(
                f"https://api.themoviedb.org/3/movie/{int(tmdb_id)}",
                {"language":"en-US"},
            )
        else:
            data=await tmdb._get_series_details(int(tmdb_id))
    except Exception:
        return False
    genres=data.get("genres") if isinstance(data,dict) else None
    return any(
        str(g.get("name","")).strip().casefold()==genre.casefold()
        for g in (genres or [])
        if isinstance(g,dict)
    )


WATCHING_TYPE_CHOICES=[
    app_commands.Choice(name="Everything",value="all"),
    app_commands.Choice(name="TV",value="shows"),
    app_commands.Choice(name="Anime",value="anime"),
    app_commands.Choice(name="Movies",value="movies"),
]


def _latest_watched_episode(item):
    latest=None
    for season in item.get("seasons") or []:
        season_number=season.get("number")
        for episode in season.get("episodes") or []:
            watched_at=episode.get("watched_at")
            if not watched_at:
                continue
            parsed=parse_iso(watched_at)
            if latest is None or parsed > latest[0]:
                latest=(parsed,season_number,episode.get("number"),episode.get("title"))
    return latest


async def _currently_watching_items(uid,user,token,media_types):
    results=[]
    request_cache={}
    for media_type in media_types:
        items,token=await cached_simkl_items(
            uid,user,token,media_type,
            request_cache=request_cache,
            timeout=HISTORY_FETCH_TIMEOUT_SECONDS,
        )
        for item in items or []:
            if item.get("status") != "watching":
                continue
            if media_type == "movies":
                media=item.get("movie") or {}
                ids=media.get("ids") or {}
                results.append({
                    "media_type":media_type,
                    "title":media.get("title") or "Untitled",
                    "ids":ids,
                    "poster":media.get("poster"),
                    "latest":None,
                })
                continue

            episode_items,movie_items=([item],[]) if media_type != "anime" else await split_anime_items([item])
            for movie_item in movie_items:
                media=movie_item.get("movie") or movie_item.get("show") or {}
                ids=media.get("ids") or {}
                results.append({
                    "media_type":"movies", "anime": True,
                    "title":media.get("title") or "Untitled",
                    "ids":ids,
                    "poster":media.get("poster"),
                    "latest":None,
                })
            for show_item in episode_items:
                media=show_item.get("show") or {}
                ids=media.get("ids") or {}
                latest=_latest_watched_episode(show_item)
                results.append({
                    "media_type":media_type,
                    "title":media.get("title") or "Untitled",
                    "ids":ids,
                    "poster":media.get("poster"),
                    "latest":latest,
                })
    return results,token


async def wetrakr_tracking_rows(uid, status, targets):
    token = await wetrakr_sync.auth.access_token(uid)
    rows = []
    for target in targets:
        async for page in wetrakr.tracking(token, status, target):
            values = page_rows(page)
            rows.extend((target, row) for row in values)
    return rows


@bot.tree.command(
    name="tracker-watching",
    description="Show what you're currently watching on your active tracker.",
)
@app_commands.choices(type=WATCHING_TYPE_CHOICES)
@app_commands.describe(type="Optionally limit the list to TV, anime, or movies.")
async def simkl_watching(i,type: app_commands.Choice[str] | None = None):
    g=guild_id(i)
    if not g:
        await i.response.send_message("This command must be used in a server.",ephemeral=True)
        return

    uid=str(i.user.id)
    await i.response.defer(ephemeral=True)
    try:
        provider, account = await selected_provider(g, uid)
        media_filter = type.value if type else "all"
        items = [item for item in await provider.watching(account) if matches_filter(item, media_filter)]
        if not items:
            await i.followup.send(f"You're not currently watching anything matching that filter on {provider.manifest.display_name}.", ephemeral=True)
            return
        items.sort(key=lambda item: item['title'].casefold())
        lines = []
        for item in items[:15]:
            emoji = '🌸' if item.get('anime') else '🎬' if item['media_type'] == 'movies' else '📺'
            title = f"[{item['title']}]({item['url']})" if item.get('url') else f"**{item['title']}**"
            line = f"{emoji} {title}"
            if item.get('latest'):
                _, season, episode, episode_title = item['latest']
                if episode is not None:
                    line += ' — ' + format_episode_display(season, int(episode), int(episode))
                if episode_title:
                    line += f" · {episode_title}"
            lines.append(line)
        if len(items) > 15:
            lines.append(f"…and **{len(items)-15}** more.")
        embed = discord.Embed(title=f"👀 {i.user.display_name} · Currently Watching",
                              description='\n'.join(lines), color=0x5865F2)
        embed.set_footer(text=f"Live from {provider.manifest.display_name} · Currently watching")
        await i.followup.send(embed=embed, ephemeral=True)
    except ValueError as exc:
        await i.followup.send(str(exc), ephemeral=True)
    except Exception:
        log.exception('Watching lookup failed for %s.', uid)
        await i.followup.send("I couldn't load your selected tracker's watching list right now.", ephemeral=True)


async def _recommendation_sources(uid,user,token,media_filter):
    request_cache={}
    media_types=MEDIA_TYPES if media_filter=="all" else (media_filter,)
    sources=[]
    excluded=set()

    for media_type in media_types:
        items,token=await cached_simkl_items(
            uid,user,token,media_type,
            request_cache=request_cache,
            timeout=HISTORY_FETCH_TIMEOUT_SECONDS,
        )
        for item in items or []:
            if media_type=="movies":
                media=item.get("movie") or {}
                ids=media.get("ids") or {}
                tmdb_id=ids.get("tmdb")
                if tmdb_id is None:
                    continue
                status=item.get("status")
                if status in {"watching","completed","dropped","plantowatch","hold"}:
                    excluded.add(("movie",int(tmdb_id)))
                if status not in {"plantowatch","dropped","hold"} and (status in {"watching","completed"} or item.get("last_watched_at")):
                    sources.append({
                        "kind":"movie",
                        "tmdb_id":int(tmdb_id),
                        "title":media.get("title") or "a watched movie",
                        "watched_at":item.get("last_watched_at") or "",
                        "anime":media_filter=="anime",
                        "media_type":"movies", "rating":item.get("user_rating"),
                        "genres":media.get("genres") or item.get("genres") or [],
                    })
                continue

            episode_items,movie_items=([item],[]) if media_type!="anime" else await split_anime_items([item])

            for movie_item in movie_items:
                media=movie_item.get("movie") or movie_item.get("show") or {}
                ids=media.get("ids") or {}
                tmdb_id=ids.get("tmdb")
                if tmdb_id is None:
                    continue
                status=movie_item.get("status")
                if status in {"watching","completed","dropped","plantowatch","hold"}:
                    excluded.add(("movie",int(tmdb_id)))
                if status not in {"plantowatch","dropped","hold"} and (status in {"watching","completed"} or movie_item.get("last_watched_at")):
                    sources.append({
                        "kind":"movie",
                        "tmdb_id":int(tmdb_id),
                        "title":media.get("title") or "a watched movie",
                        "watched_at":movie_item.get("last_watched_at") or "",
                        "anime":media_filter=="anime" or media_type=="anime",
                        "media_type":"anime", "rating":movie_item.get("user_rating"),
                        "genres":media.get("genres") or movie_item.get("genres") or [],
                    })

            for show_item in episode_items:
                media=show_item.get("show") or {}
                ids=media.get("ids") or {}
                tmdb_id=ids.get("tmdb")
                if tmdb_id is None and ids.get("tvdb") is not None:
                    tmdb_id=await tmdb.find_series_by_tvdb(ids.get("tvdb"))
                if tmdb_id is None:
                    continue
                status=show_item.get("status")
                if status in {"watching","completed","dropped","plantowatch","hold"}:
                    excluded.add(("tv",int(tmdb_id)))
                latest=_latest_watched_episode(show_item)
                if status not in {"plantowatch","dropped","hold"} and (status in {"watching","completed"} or show_item.get("last_watched_at") or latest):
                    watched_at=show_item.get("last_watched_at") or (latest[0].isoformat() if latest else "")
                    sources.append({
                        "kind":"tv",
                        "tmdb_id":int(tmdb_id),
                        "title":media.get("title") or "a watched series",
                        "watched_at":watched_at,
                        "anime":media_type=="anime",
                        "media_type":media_type, "rating":show_item.get("user_rating"),
                        "genres":media.get("genres") or show_item.get("genres") or [],
                    })

    sources=select_sources(sources)
    log.info(
        "Recommendation sources for user %s: %d sources, %d exclusions, filter=%s",
        uid,len(sources),len(excluded),media_filter,
    )
    return sources,excluded,token


async def _get_recommendation_candidates(sources,excluded,media_filter):
    candidates={}
    total_raw=0
    total_excluded=0
    total_filtered=0
    total_invalid=0
    kinds=sorted({source["kind"] for source in sources})
    genre_maps=dict(zip(kinds,await asyncio.gather(*(tmdb.get_genres(kind) for kind in kinds))))

    for source in sources:
        if source["kind"]=="movie":
            recommendation_results=await tmdb.get_movie_recommendations(source["tmdb_id"])
            similar_results=await tmdb.get_movie_similar(source["tmdb_id"])
            kind="movie"
        else:
            recommendation_results=await tmdb.get_tv_recommendations(source["tmdb_id"])
            similar_results=await tmdb.get_tv_similar(source["tmdb_id"])
            kind="tv"

        # Keep both TMDB recommendation and similar-title results. A title can
        # legitimately have recommendation results that are all already in the
        # user's history, while /similar still has fresh candidates.
        results=[]
        seen_ids=set()
        for channel,group in (("recommendation",recommendation_results),("similar",similar_results)):
            for result in group or []:
                try:
                    result_id=int(result.get("id"))
                except (TypeError,ValueError):
                    total_invalid+=1
                    continue
                if result_id in seen_ids:
                    continue
                seen_ids.add(result_id)
                results.append((result,channel))

        log.info(
            "Recommendation lookup: %s TMDB=%s returned %d recommendation(s) + %d similar title(s) = %d unique candidate(s).",
            kind,
            source["tmdb_id"],
            len(recommendation_results or []),
            len(similar_results or []),
            len(results),
        )

        total_raw+=len(results)
        for result,channel in results:
            try:
                result_id=int(result.get("id"))
            except (TypeError,ValueError):
                total_invalid+=1
                continue
            if (kind,result_id) in excluded:
                total_excluded+=1
                continue
            if media_filter=="anime":
                origin=result.get("origin_country") or []
                if ("JP" not in origin and result.get("original_language")!="ja") or 16 not in (result.get("genre_ids") or []):
                    total_filtered+=1
                    continue
            if not result.get("name") and not result.get("title"):
                total_invalid+=1
                continue

            key=(kind,result_id)
            entry=candidates.get(key)
            source_genres={str(genre).casefold() for genre in source.get("genres") or []}
            candidate_genres={genre_maps.get(kind,{}).get(int(genre),"") for genre in result.get("genre_ids") or []}
            shared=sorted(name for name in candidate_genres if name and name.casefold() in source_genres)
            match={"title":source["title"],"rating":rating_value(source.get("rating")),
                   "weight":source_weight(source,channel),"genres":shared,
                   "source_id":(source["kind"],source["tmdb_id"])}
            if entry is None:
                entry=dict(result)
                entry["_recommendation_kind"]=kind
                entry["_matches"]=[match]
                entry["_matched_genres"]=set(shared)
                entry["_anime"]=bool(source.get("anime") and result.get("original_language")=="ja" and 16 in (result.get("genre_ids") or []))
            else:
                if not any(existing["source_id"]==match["source_id"] for existing in entry["_matches"]):
                    entry["_matches"].append(match)
                entry["_matched_genres"].update(shared)
                entry["_anime"]=entry.get("_anime",False) or bool(source.get("anime") and result.get("original_language")=="ja" and 16 in (result.get("genre_ids") or []))
                if float(result.get("vote_average") or 0) > float(entry.get("vote_average") or 0):
                    entry["vote_average"]=result.get("vote_average")
                    entry["vote_count"]=result.get("vote_count")
            candidates[key]=entry

    log.info(
        "Recommendation filtering: %d unique raw, %d excluded by history, %d filtered by media type, %d invalid, %d fresh candidates.",
        total_raw,
        total_excluded,
        total_filtered,
        total_invalid,
        len(candidates),
    )
    log.info(
        "Recommendation candidate map: %d entries, keys=%s",
        len(candidates),
        list(candidates.keys())[:10],
    )

    return rank_candidates(list(candidates.values()))


@bot.tree.command(
    name="tracker-recommend",
    description="Get personalized recommendations based on your active tracker history.",
)
@app_commands.choices(type=[
    app_commands.Choice(name="Everything",value="all"),
    app_commands.Choice(name="TV",value="shows"),
    app_commands.Choice(name="Anime",value="anime"),
    app_commands.Choice(name="Movies",value="movies"),
])
async def simkl_recommend(i,type: app_commands.Choice[str] | None = None):
    g=guild_id(i)
    if not g:
        await i.response.send_message("This command must be used in a server.",ephemeral=True)
        return

    uid=str(i.user.id)
    try:
        provider, account = await selected_provider(g, uid)
    except ValueError as exc:
        await i.response.send_message(str(exc), ephemeral=True)
        return
    await i.response.defer(ephemeral=True)
    media_filter=type.value if type else "all"

    try:
        sources, excluded = await provider.recommendation_sources(account, media_filter)

        if not sources:
            await i.followup.send(
                "I need some watched history with matched TMDB titles before I can make recommendations.",
                ephemeral=True,
            )
            return

        recommendations=await _get_recommendation_candidates(sources,excluded,media_filter)
        log.info(
            "Recommendation candidates for user %s: %d from %d sources",
            uid,len(recommendations),len(sources),
        )
        if not recommendations:
            await i.followup.send(
                f"I couldn't find a fresh recommendation from your current {provider.manifest.display_name} history. Try adding more watched titles.",
                ephemeral=True,
            )
            return

        selected=recommendation_lineup(recommendations)
        detail_cache={}

        async def recommendation_ratings(result):
            if provider.manifest.name=='mdblist':
                ratings=await provider.ratings(account,result.get('_recommendation_kind')=='movie',{'tmdb':result.get('id')})
                return {'imdb':ratings.get('imdb'),'myanimelist':ratings.get('mal')}
            if mdblist is None or result.get("id") is None:
                return {}

            media_type="movie" if result.get("_recommendation_kind")=="movie" else "show"
            try:
                ratings=await mdblist.get_ratings(media_type,result.get("id"))
                if not isinstance(ratings,dict):
                    return {}
                return ratings
            except Exception:
                log.warning(
                    "Recommendation rating lookup failed for TMDB=%s.",
                    result.get("id"),
                    exc_info=True,
                )
                return {}

        async def destination(result):
            kind=result["_recommendation_kind"]
            page=f"https://www.themoviedb.org/{'movie' if kind=='movie' else 'tv'}/{int(result['id'])}"
            try:
                resolved = await provider.resolve_title_url('movies' if kind == 'movie' else 'shows', {'tmdb': result['id']}, account=account) if provider.manifest.name=='mdblist' else await provider.resolve_title_url('movies' if kind == 'movie' else 'shows', {'tmdb': result['id']})
            except Exception:
                log.debug('Recommendation title resolution failed for %s.', result['id'], exc_info=True)
                resolved = None
            return resolved or page, bool(resolved)

        async def render_pick(result,index,total):
            key=(result["_recommendation_kind"],result["id"])
            if key not in detail_cache:
                ratings,link=await asyncio.gather(recommendation_ratings(result),destination(result))
                detail_cache[key]=(ratings,link)
            ratings,(result_url,on_tracker)=detail_cache[key]
            title=result.get("name") or result.get("title") or "Untitled"
            rating_parts=[]
            imdb_rating=ratings.get("imdb")
            if imdb_rating is not None:
                rating_parts.append(f"⭐ IMDb {float(imdb_rating):.1f}/10")
            if result.get("_anime"):
                mal_rating=ratings.get("myanimelist")
                if mal_rating is not None:
                    rating_parts.append(f"🌸 MAL {float(mal_rating):.1f}/10")
            matches=sorted(result.get("_matches") or [],key=lambda match:-match["weight"])
            reasons=[]
            for match in matches[:2]:
                own_rating=match.get("rating")
                if own_rating and own_rating>=8:
                    reasons.append(f"you rated *{match['title']}* {own_rating:g}/10")
                else:
                    reasons.append(f"you watched *{match['title']}*")
            reason="; ".join(reasons) or "Suggested from your watch history"
            if len(matches)>2:
                reason+=f"; plus {len(matches)-2} other watched title(s)"
            shared=sorted(result.get("_matched_genres") or [])
            if shared:
                reason+=f"\nShared genre: {', '.join(shared[:2])}"
            release_date=result.get("first_air_date") or result.get("release_date") or ""
            year=release_date[:4] if release_date else "Year unknown"
            kind_label="Movie" if result["_recommendation_kind"]=="movie" else ("Anime" if result.get("_anime") else "TV")
            votes=int(result.get("vote_count") or 0)
            tmdb_score=float(result.get("vote_average") or 0)
            if votes:
                rating_parts.append(f"TMDB {tmdb_score:.1f}/10 ({votes:,} votes)")
            overview=" ".join((result.get("overview") or "").split())
            if len(overview)>700:
                overview=overview[:697].rsplit(" ",1)[0]+"…"
            label="Close match" if index<=3 else "Broader discovery" if index==4 else "Wildcard" if index==5 else "More to explore"
            embed=discord.Embed(
                title=title,url=result_url,
                description=overview or "No synopsis available for this title.",
                color=0xE91E63 if result.get("_anime") else 0xF1C40F if kind_label=="Movie" else 0x3498DB,
            )
            embed.set_author(name=f"{i.user.display_name}'s picks · {label}")
            embed.add_field(name="Why this pick",value=reason,inline=False)
            embed.add_field(name="Details",value=f"{kind_label} · {year}"+(f"\n{' · '.join(rating_parts)}" if rating_parts else ""),inline=False)
            if not on_tracker:
                provider_name=provider.manifest.display_name
                embed.add_field(name="Link",value=f"This title isn't matched on {provider_name} yet. The title opens its exact TMDB entry.",inline=False)
            poster=result.get("poster_path")
            if poster and poster.startswith("/"):
                embed.set_thumbnail(url=f"https://image.tmdb.org/t/p/w500{poster}")
            embed.set_footer(text=f"{index} of {total} · Based on your {provider.manifest.display_name} watch history")
            return embed

        view=RecommendationView(i.user.id,selected,recommendations,render_pick)
        view.message=await i.followup.send(embed=await render_pick(selected[0],1,len(selected)),
                                           view=view,ephemeral=True,wait=True,
                                           allowed_mentions=discord.AllowedMentions.none())

    except SimklAuthError:
        await i.followup.send(
            "Your SIMKL authentication is no longer valid. Please use /tracker-link again.",
            ephemeral=True,
        )
    except Exception as exc:
        log.error(
            "Recommendation engine failed for user %s: %s: %s",
            uid,type(exc).__name__,exc,
        )
        await i.followup.send(
            "I couldn't generate recommendations right now. Please try again in a moment.",
            ephemeral=True,
        )


@bot.tree.command(name="tracker-random",description="Pick something random from your active tracker's plan to watch list.")
@app_commands.choices(type=RANDOM_TYPE_CHOICES,genre=RANDOM_GENRE_CHOICES)
@app_commands.describe(
    type="Choose what kind of title to pick.",
    genre="Optionally limit the pick to a genre.",
)
async def simkl_random(
    i,
    type: app_commands.Choice[str] | None = None,
    genre: app_commands.Choice[str] | None = None,
):
    g=guild_id(i)
    if not g:
        await i.response.send_message("This command must be used in a server.",ephemeral=True)
        return

    uid = str(i.user.id)
    await i.response.defer()
    try:
        provider, account = await selected_provider(g, uid)
        media_filter = type.value if type else 'all'
        genre_filter = genre.value if genre else ''
        candidates = []
        for item in await provider.planning(account):
            if not matches_filter(item, media_filter):
                continue
            if genre_filter:
                genres = [str(v.get('name') if isinstance(v, dict) else v).casefold() for v in item.get('genres') or []]
                if genre_filter.casefold() not in genres:
                    if not item.get('native_item') or not await random_picker_matches_genre(item['native_item'], item['native_scope'], genre_filter):
                        continue
            candidates.append(item)
        if not candidates:
            await i.followup.send(f"I couldn't find a matching title in your {provider.manifest.display_name} Plan To Watch list.", ephemeral=True)
            return
        item = random.choice(candidates)
        media_type, ids, title = item['media_type'], item['ids'], item['title']
        tmdb_id = ids.get('tmdb')
        if media_type == 'movies':
            image, logo = await tmdb.get_movie_backdrop(tmdb_id), await tmdb.get_movie_logo(tmdb_id)
        else:
            image, logo = await tmdb.get_tv_backdrop(tmdb_id), await tmdb.get_tv_logo(tmdb_id)
        label = '🌸 Anime Movie' if item.get('anime') and media_type == 'movies' else MEDIA_STYLES[media_type][1]
        lines = [f"**{label}**"]
        if item.get('episode_count'):
            lines.append(f"📺 **{item['episode_count']:,}** episode(s) total.")
        if item.get('added_at'):
            added = parse_iso(item['added_at'])
            if added != datetime.min.replace(tzinfo=timezone.utc):
                lines.append(f"📅 Added to Plan To Watch: **<t:{int(added.timestamp())}:D>**")
        if genre_filter:
            lines.append(f"🏷️ Genre filter: **{genre_filter.title()}**")
        embed = build_embed(media_type, '\n'.join(lines), datetime.now(timezone.utc),
                            i.user.display_name, i.user, image, provider.profile_url(account),
                            title=title, title_url=item.get('url'), poster=item.get('poster'), logo=logo,
                            preferences=await storage.get_embed_preferences(g, uid))
        embed.title = f"🎲 Random Pick · {title}"
        embed.set_footer(text=f"{label} · {provider.manifest.display_name} Plan To Watch")
        await i.followup.send(embed=embed)
    except ValueError as exc:
        await i.followup.send(str(exc), ephemeral=True)
    except Exception:
        log.exception('Random picker failed for %s.', uid)
        await i.followup.send("I couldn't pick a title from your selected tracker right now.", ephemeral=True)


async def simkl_link(i):
    g=guild_id(i)
    if not g: await i.response.send_message("This command must be used in a server.",ephemeral=True); return
    uid=str(i.user.id); key=f"{g}:{uid}"
    if key in linking_users: await i.response.send_message("You already have a linking code waiting.",ephemeral=True); return
    linking_users.add(key)
    try:
        await i.response.defer(ephemeral=True); pin=await simkl.start_pin_auth(); code=pin["user_code"]; device=pin["device_code"]; expires=pin.get("expires_in",900); interval=pin.get("interval",5); url=pin.get("verification_uri","https://simkl.com/pin")
        await i.followup.send(f"Go to {url}\nEnter this code: `{code}`\nThe code expires in about {expires//60} minutes.",ephemeral=True)
        elapsed=0; tokens=None
        while elapsed<expires:
            await asyncio.sleep(interval); elapsed+=interval
            try: tokens=await simkl.poll_pin(device)
            except SimklSlowDown: interval+=5; continue
            except SimklAuthError: break
            except Exception: log.warning("PIN poll failed.",exc_info=True); continue
            if tokens: break
        if not tokens: await i.followup.send("The SIMKL linking code expired or was cancelled. Run /tracker-link again.",ephemeral=True); return
        access=tokens["access_token"]; refresh=tokens.get("refresh_token"); exp=calculate_token_expiry(tokens.get("expires_in")); aid=None
        try:
            settings=await simkl.get_user_settings(access); aid=account_id_from_settings(settings); username=settings.get("user",{}).get("name") or settings.get("account",{}).get("id") or "SIMKL user"
        except Exception: username="SIMKL user"
        await storage.link_user(g,uid,access,refresh,username,now_iso(),exp,aid)
        await i.followup.send(f"Linked as {username}. Importing your SIMKL watch history now…",ephemeral=True)
        account={"simkl_token":access,"refresh_token":refresh,"token_expires_at":exp}
        cache={}
        try:
            async with history_backfill_semaphore:
                token=await seed_history(g,uid,account,access,cache)
                await seed_progression_history(uid,account,token,cache)
            stats=await storage.get_statistics(g,uid)
            progression=await storage.get_progression(uid)
            total=int(stats.get("episodes_watched",0))+int(stats.get("movies_watched",0))
            level=level_progress(int(progression.get("xp",0)))[0]
            await i.followup.send(
                f"History import complete: **{total:,} watches**, **{int(progression.get('xp',0)):,} XP**, Level **{level}**. View `/tracker-stats` for details.",
                ephemeral=True,
            )
        except Exception:
            log.exception("Initial history import failed for user %s in guild %s; polling will retry.",uid,g)
            await i.followup.send(
                "Your SIMKL account is linked, but the history import did not finish. The bot will retry automatically; an admin can also run `/tracker-checknow`.",
                ephemeral=True,
            )
    finally: linking_users.discard(key)

async def simkl_unlink(i):
    g=guild_id(i)
    if not g: await i.response.send_message("This command must be used in a server.",ephemeral=True); return
    ok=await storage.unlink_user(g,str(i.user.id)); await i.response.send_message("Your SIMKL account has been unlinked from this server." if ok else "You don't have a linked SIMKL account in this server.",ephemeral=True)


async def wetrakr_link(i):
    g=guild_id(i)
    if not g:
        await i.response.send_message("This command must be used in a server.",ephemeral=True)
        return
    if not wetrakr:
        await i.response.send_message("WeTrakr is not configured on this bot instance yet.",ephemeral=True)
        return
    uid=str(i.user.id)
    key=f"wetrakr:{uid}"
    if key in linking_users:
        await i.response.send_message("A WeTrakr linking code is already waiting for you.",ephemeral=True)
        return
    linking_users.add(key)
    await i.response.defer(ephemeral=True)
    try:
        pin=await wetrakr.device_code()
        device=pin["device_code"]
        interval=max(1,int(pin.get("interval",5)))
        expires=max(interval,int(pin.get("expires_in",900)))
        url=pin.get("verification_url") or "https://wetrakr.com/activate"
        if not url.startswith("https://wetrakr.com/activate"):
            raise ValueError("Unexpected WeTrakr activation URL")
        await i.followup.send(f"Open {url} and enter `{pin['user_code']}`. This code expires in about {expires//60} minutes.",ephemeral=True)
        deadline=time.monotonic()+expires
        tokens=None
        while time.monotonic()+interval<deadline:
            await asyncio.sleep(interval)
            try:
                tokens=await wetrakr.device_token(device)
                break
            except WeTrakrError as exc:
                if exc.status == 400 and exc.code == "authorization_pending":
                    continue
                if exc.status == 429:
                    interval+=5
                    continue
                if exc.status in {404,409,410,418}:
                    break
                raise
        if not tokens:
            await i.followup.send("WeTrakr approval expired or was cancelled. Run `/tracker-link` with provider WeTrakr again.",ephemeral=True)
            return
        account=await wetrakr.account(tokens["access_token"])
        if not isinstance(account,dict) or not account.get("id"):
            raise ValueError("WeTrakr account lookup did not return an account ID")
        tokens["expires_at"]=(datetime.now(timezone.utc)+timedelta(seconds=int(tokens.get("expires_in",604800)))).isoformat()
        await storage.link_wetrakr(g,uid,tokens,account)
        name=account.get("username") or (account.get("info") or {}).get("username") or "WeTrakr user"
        await i.followup.send(
            f"Linked WeTrakr as **{discord.utils.escape_markdown(str(name))}**. "
            "Use `/tracker-source` to choose which service posts activity in this server. "
            "Your existing history will import quietly, then new watches can post and earn shared XP.",
            ephemeral=True)
    except Exception as exc:
        log.error("WeTrakr linking failed for user %s in guild %s: %s",uid,g,type(exc).__name__)
        await i.followup.send("WeTrakr linking failed. Check the app key and try again; no WeTrakr watch history was imported.",ephemeral=True)
    finally:
        linking_users.discard(key)


async def wetrakr_unlink(i):
    g=guild_id(i)
    if not g:
        await i.response.send_message("This command must be used in a server.",ephemeral=True)
        return
    linked=await storage.unlink_wetrakr(g,str(i.user.id))
    await i.response.send_message("WeTrakr has been unlinked from this server." if linked else "You don't have a WeTrakr link in this server.",ephemeral=True)


async def mdblist_link(i):
    g=guild_id(i)
    if not g:
        await i.response.send_message("Use this command in a server.",ephemeral=True);return
    if not mdblist_tracking:
        await i.response.send_message("MDBList OAuth is not configured. The host must set MDBLIST_CLIENT_ID from their registered MDBList app.",ephemeral=True);return
    uid=str(i.user.id);key=f"mdblist:{uid}"
    if key in linking_users:
        await i.response.send_message("An MDBList linking code is already waiting for you.",ephemeral=True);return
    linking_users.add(key)
    await i.response.defer(ephemeral=True)
    try:
        pin=await mdblist_tracking.device_code()
        url=pin['verification_uri']
        from urllib.parse import urlparse
        parsed=urlparse(url)
        if parsed.scheme!='https' or parsed.hostname not in {'mdblist.com','www.mdblist.com'}:
            raise ValueError('Unexpected verification URL')
        interval=max(1,int(pin.get('interval',5)))
        expires=max(interval,int(pin.get('expires_in',300)))
        await i.followup.send(f"Open {url} and enter `{pin['user_code']}`. This code expires in about {expires//60} minutes.",ephemeral=True)
        deadline=time.monotonic()+expires;tokens=None
        while time.monotonic()+interval<deadline:
            await asyncio.sleep(interval)
            try:
                tokens=await mdblist_tracking.device_token(pin['device_code']);break
            except MDBListTrackingError as exc:
                if exc.code=='authorization_pending':continue
                if exc.code=='slow_down':interval+=5;continue
                if exc.code in {'expired_token','access_denied'}:break
                raise
        if not tokens:
            await i.followup.send("MDBList approval expired or was cancelled. Run `/tracker-link` again.",ephemeral=True);return
        account=await mdblist_tracking.account(tokens['access_token'])
        tokens['expires_at']=(datetime.now(timezone.utc)+timedelta(seconds=int(tokens.get('expires_in',2592000)))).isoformat()
        if not tokens.get('refresh_token'):raise ValueError('Missing renewable account token')
        await storage.link_provider_account(g,uid,tokens,{'id':account['user_id'],'username':account['username']},provider='mdblist')
        await i.followup.send(f"Linked MDBList as **{discord.utils.escape_markdown(account['username'])}**. Select MDBList with `/tracker-source`; the first check imports history quietly.",ephemeral=True)
    except Exception as exc:
        log.warning("MDBList link failed user %s guild %s: %s",uid,g,getattr(exc,'code',type(exc).__name__))
        await i.followup.send("MDBList linking failed. Check the registered OAuth app/device grant and try again. No watch history was imported.",ephemeral=True)
    finally:
        linking_users.discard(key)


async def deliver_mdblist_play(ch,gid,uid,name,member,provider,account,play,last_play=None):
    movie='movie' in play['media_type'];anime=play['media_type'].startswith('anime')
    media='movies' if movie else 'anime' if anime else 'shows'
    title=play['title'];ids=play['ids'];tmdb_id=ids.get('tmdb')
    p=await prefs(gid,uid)
    metadata=await provider.title('movies' if movie else 'shows',ids)
    poster=metadata.get('poster_path')
    if poster:poster='https://image.tmdb.org/t/p/w500'+poster
    image=None;episode_title=None;rating=None
    previous=await storage.get_provider_plays(uid,provider='mdblist')
    rewatch=any(old.get('item_key')==play['item_key'] and old.get('source_event_id')!=play['source_event_id'] for old in previous)
    if movie:
        image=await tmdb.get_movie_backdrop(tmdb_id) if tmdb_id else None
        description=f"{'Rewatched' if rewatch else 'Watched'} **{title}**"
        ratings=await provider.ratings(account,True,ids)
    else:
        entry={'tmdb_id':tmdb_id,'tvdb_id':ids.get('tvdb'),'season_num':play['season'],'episode_number':play['episode']}
        image,episode_title,episode_imdb,runtime=await episode_media(media,entry)
        episode_imdb=episode_imdb or (play.get('episode_ids') or {}).get('imdb')
        if episode_imdb and p.get('show_imdb',True):rating=await imdb.get_rating(episode_imdb)
        label=format_episode_display(play['season'],play['episode'],last_play['episode'] if last_play else play['episode'],p.get("episode_code",False))
        description=f"{'Rewatched' if rewatch else 'Watched'} {label} of **{title}**"
        if episode_title:description+=f"\n*{episode_title}*"
        if play['season']==1 and play['episode']==1 and not any(old.get('show_id')==play.get('show_id') and 'episode' in old.get('media_type','') for old in previous):
            description+="\n🆕 Started watching this series."
        ratings={'imdb':rating}
        if last_play:
            last_entry={**entry,'episode_number':last_play['episode']}
            _,_,last_imdb,_=await episode_media(media,last_entry)
            last_rating=await imdb.get_rating(last_imdb) if last_imdb and p.get('show_imdb',True) else None
            parts=[]
            if rating is not None:parts.append(f"E{play['episode']:02} {rating:.1f}/10")
            if last_rating is not None:parts.append(f"E{last_play['episode']:02} {last_rating:.1f}/10")
            if parts:description+='\n⭐ IMDb '+ ' · '.join(parts)
            ratings={}
    if ratings:
        if p.get('show_imdb',True) and ratings.get('imdb') is not None:description+=f"\n⭐ IMDb {ratings['imdb']:.1f}/10"
        if movie and anime and p.get('show_mal',True) and ratings.get('mal') is not None:description+=f" · 🌸 MAL {ratings['mal']:.2f}/10"
    embed=build_embed(media,description,parse_iso(play['watched_at']),name,member,image,provider.profile_url(account),
        title=title,title_url=provider.title_url('movies' if movie else 'shows',ids),poster=poster,preferences=p,provider='MDBList')
    return await send_embed(ch,embed,'MDBList activity')


async def deliver_mdblist_status(ch,gid,uid,name,member,provider,account,status,item):
    media=item['media_type'];movie=media=='movies';anime=item.get('anime')
    description=f"{'Planned to watch' if status=='planning' else 'Dropped'} **{item['title']}**"
    ratings=await provider.ratings(account,movie,item['ids'])
    p=await prefs(gid,uid)
    if ratings:
        if p.get('show_imdb',True) and ratings.get('imdb') is not None:description+=f"\n⭐ IMDb {ratings['imdb']:.1f}/10"
        if anime and p.get('show_mal',True) and ratings.get('mal') is not None:description+=f" · 🌸 MAL {ratings['mal']:.2f}/10"
    embed=build_embed(media,description,datetime.now(timezone.utc),name,member,None,provider.profile_url(account),
        title=item['title'],title_url=item.get('url'),poster=item.get('poster'),preferences=p,status_activity=True,provider='MDBList')
    return await send_embed(ch,embed,'MDBList status')


TRACKER_CHOICES=[app_commands.Choice(name=manifest.display_name,value=manifest.name)
                 for manifest in BUILTIN_TRACKERS]


@bot.tree.command(name="tracker-link", description="Link your SIMKL, WeTrakr or MDBList account in this server.")
@app_commands.choices(provider=TRACKER_CHOICES)
async def tracker_link(i, provider: app_commands.Choice[str]):
    handler = provider_registry.get(provider.value).authorize if provider.value in {m.name for m in provider_registry.manifests()} else None
    if handler is None:
        await i.response.send_message("That tracking provider is not available on this bot.",ephemeral=True)
        return
    await handler(i)


@bot.tree.command(name="tracker-unlink", description="Unlink a tracker account from this server.")
@app_commands.choices(provider=TRACKER_CHOICES)
async def tracker_unlink(i, provider: app_commands.Choice[str]):
    handler = provider_registry.get(provider.value).unlink if provider.value in {m.name for m in provider_registry.manifests()} else None
    if handler is None:
        await i.response.send_message("That tracking provider is not available on this bot.",ephemeral=True)
        return
    await handler(i)


@bot.tree.command(name="tracker-source", description="Choose which linked tracker posts your activity in this server.")
@app_commands.choices(provider=TRACKER_CHOICES)
async def tracker_source(i, provider: app_commands.Choice[str] | None = None):
    g=guild_id(i)
    if not g:
        await i.response.send_message("This command must be used in a server.",ephemeral=True)
        return
    uid=str(i.user.id)
    if provider is None:
        source=await storage.get_activity_provider(g,uid)
        active=provider_registry.get(source).manifest.display_name if source else "none"
        await i.response.send_message(f"Your activity source here is **{active}**. Choose a provider to change it.",ephemeral=True)
        return
    if provider.value=="wetrakr" and not wetrakr_sync:
        await i.response.send_message("WeTrakr is not configured on this bot.",ephemeral=True)
        return
    if provider.value=="mdblist" and not mdblist_tracking:
        await i.response.send_message("MDBList OAuth is not configured on this bot. Set MDBLIST_CLIENT_ID first.",ephemeral=True)
        return
    changed=await storage.set_activity_provider(g,uid,provider.value)
    if not changed:
        await i.response.send_message(f"Link your {provider.name} account in this server first.",ephemeral=True)
        return
    detail=(f"The next check imports {provider.name} watch history, XP and statistics without posting old activity. "
            "Later watches post normally; switching back preserves your progression."
            if provider.value in {"wetrakr","mdblist"} else
            "SIMKL activity resumes from now; its existing XP and statistics remain in place.")
    await i.response.send_message(f"Activity source set to **{provider.name}**. {detail}",ephemeral=True)


@bot.tree.command(name="tracker-style",description="Choose your personal style for episode and movie watch activities.")
@app_commands.choices(style=STYLE_CHOICES,artwork=ARTWORK_CHOICES,activity_text=TEXT_CHOICES,episode_format=EPISODE_FORMAT_CHOICES,show_imdb=RATING_CHOICES,show_mal=RATING_CHOICES)
@app_commands.describe(reset="Reset your personal choices and follow the server default")
async def simkl_style(i,style: app_commands.Choice[str] | None = None,artwork: app_commands.Choice[str] | None = None,activity_text: app_commands.Choice[str] | None = None,episode_format: app_commands.Choice[str] | None = None,show_imdb: app_commands.Choice[str] | None = None,show_mal: app_commands.Choice[str] | None = None,reset: bool | None = None):
    g=guild_id(i)
    if not g: await i.response.send_message("This command must be used in a server.",ephemeral=True); return
    uid=str(i.user.id)

    def settings_text(p, heading):
        episode_label="Code format (S2E04)" if p.get("episode_code", False) else "Plain text (S2E04)"
        return (
            f"{heading}\n"
            f"1. **Style:** **{p['style'].title()}**\n"
            f"2. **Activity text:** **{p['activity_text'].title()}**\n"
            f"3. **Artwork:** **{p['artwork'].title()}**\n"
            f"4. **Episode numbers:** **{episode_label}**\n"
            f"5. **IMDb ratings:** **{'On' if p.get('show_imdb', True) else 'Off'}**\n"
            f"6. **MAL ratings:** **{'On' if p.get('show_mal', True) else 'Off'}**\n"
            f"7. **Status activities:** **Always use posters**"
        )

    if reset is True:
        await storage.reset_embed_preferences(uid)
        p=await prefs(g,uid)
        await i.response.send_message(settings_text(p,"Your personal settings have been reset. You now follow the server default for watch activities:"),ephemeral=True)
        return

    if style is None and artwork is None and activity_text is None and episode_format is None and show_imdb is None and show_mal is None:
        p=await prefs(g,uid)
        await i.response.send_message(settings_text(p,"Your effective watch activity settings:"),ephemeral=True)
        return

    await storage.set_embed_preferences(
        uid,
        style=style.value if style else None,
        artwork=artwork.value if artwork else None,
        activity_text=activity_text.value if activity_text else None,
        episode_code=(episode_format.value == "true") if episode_format else None,
        show_imdb=(show_imdb.value == "true") if show_imdb else None,
        show_mal=(show_mal.value == "true") if show_mal else None,
    )
    p=await prefs(g,uid)
    await i.response.send_message(
        settings_text(p,"Your personal watch activity settings are now:") +
        "\n\nThese settings override the server default. Status activities always use posters.",
        ephemeral=True,
    )

@bot.tree.command(name="tracker-style-server",description="(Admin) Set the default style for episode and movie watch activities.")
@app_commands.choices(style=STYLE_CHOICES,artwork=ARTWORK_CHOICES,activity_text=TEXT_CHOICES,episode_format=EPISODE_FORMAT_CHOICES,show_imdb=RATING_CHOICES,show_mal=RATING_CHOICES)
@app_commands.describe(reset="Reset all server style options to the default settings",force_override="Force everyone to use the server settings, ignoring personal choices")
async def simkl_style_server(i,style: app_commands.Choice[str] | None = None,artwork: app_commands.Choice[str] | None = None,activity_text: app_commands.Choice[str] | None = None,episode_format: app_commands.Choice[str] | None = None,show_imdb: app_commands.Choice[str] | None = None,show_mal: app_commands.Choice[str] | None = None,force_override: bool | None = None,reset: bool | None = None):
    g=guild_id(i)
    if not g or not is_admin(i): await i.response.send_message(NOT_ADMIN_MESSAGE,ephemeral=True); return

    def settings_text(p, heading, forced=None):
        episode_label="Code Block Format" if p.get("episode_code", False) else "Bold Text Format"
        text=(
            f"{heading}\n"
            f"1. **Style:** **{p['style'].title()}**\n"
            f"2. **Activity text:** **{p['activity_text'].title()}**\n"
            f"3. **Artwork:** **{p['artwork'].title()}**\n"
            f"4. **Episode numbers:** **{episode_label}**\n"
            f"5. **IMDb ratings:** **{'On' if p.get('show_imdb', True) else 'Off'}**\n"
            f"6. **MAL ratings:** **{'On' if p.get('show_mal', True) else 'Off'}**\n"
            f"7. **Status activities:** **Always use posters**"
        )
        if forced is not None:
            text += f"\n8. **Force override:** **{'Enabled' if forced else 'Disabled'}**"
        return text

    if reset is True:
        await storage.reset_server_embed_preferences(g)
        p=await storage.get_server_embed_preferences(g)
        forced=await storage.get_server_embed_force_override(g)
        await i.response.send_message(settings_text(p,"Server style settings have been reset to the defaults:",forced),ephemeral=True)
        return

    if style is None and artwork is None and activity_text is None and episode_format is None and show_imdb is None and show_mal is None and force_override is None:
        p=await storage.get_server_embed_preferences(g)
        forced=await storage.get_server_embed_force_override(g)
        await i.response.send_message(settings_text(p,"Server default:",forced),ephemeral=True)
        return

    await storage.set_server_embed_preferences(
        g,
        style=style.value if style else None,
        artwork=artwork.value if artwork else None,
        activity_text=activity_text.value if activity_text else None,
        episode_code=(episode_format.value == "true") if episode_format else None,
        show_imdb=(show_imdb.value == "true") if show_imdb else None,
        show_mal=(show_mal.value == "true") if show_mal else None,
        force_override=force_override,
    )
    p=await storage.get_server_embed_preferences(g)
    forced=await storage.get_server_embed_force_override(g)
    await i.response.send_message(settings_text(p,"Server default updated:",forced),ephemeral=True)

@bot.tree.command(name="tracker-setchannel",description="(Admin) Set the channel where this server's watch activity is posted.")
async def simkl_setchannel(i,channel:discord.TextChannel=None):
    g=guild_id(i)
    if not g or not is_admin(i): await i.response.send_message(NOT_ADMIN_MESSAGE,ephemeral=True); return
    target=channel or i.channel; await storage.set_channel(g,target.id); await i.response.send_message(f"Watch activity for this server will now be posted in {target.mention}.",ephemeral=True)

@bot.tree.command(name="tracker-status",description="(Admin) Show server tracker status or check one member.")
@app_commands.describe(user="Check this member instead of the server summary")
async def simkl_status(i,user: discord.Member | None = None):
    g=guild_id(i)
    if not g or not is_admin(i): await i.response.send_message(NOT_ADMIN_MESSAGE,ephemeral=True); return
    await i.response.defer(ephemeral=True)
    d=await storage.get_all()
    sg=(d.get("guilds") or {}).get(str(g),{})
    users=sg.get("users") or {}
    allu=d.get("users") or {}
    ch=sg.get("channel_id")
    text=f"<#{ch}>" if ch else "**not set**"
    lines=[]
    now=datetime.now(timezone.utc)
    guild=i.guild
    current_count=0
    stale_count=0
    status_users={str(user.id):users[str(user.id)]} if user and str(user.id) in users else ({} if user else users)
    for uid, gu in list(status_users.items())[:PAGE_SIZE]:
        try:
            member=user or guild.get_member(int(uid))
            if member is None:
                member=await guild.fetch_member(int(uid))
        except (discord.NotFound, discord.Forbidden, ValueError):
            member=None
        if member is None:
            stale_count+=1
            continue
        current_count+=1
        u=allu.get(uid) or {}
        selected=gu.get("activity_provider", "simkl")
        simkl_linked=gu.get("simkl_linked", True) and bool(u.get("simkl_token"))
        wetrakr_linked=gu.get("wetrakr_linked", False) and bool(u.get("wetrakr"))
        links=[]
        if simkl_linked:
            links.append(f"SIMKL: **{u.get('simkl_username') or 'unknown'}**")
        if wetrakr_linked:
            links.append(f"WeTrakr: **{u['wetrakr'].get('username') or 'unknown'}**")
        identity=f"• <@{uid}> — active: **{provider_registry.get(selected).manifest.display_name}** · " + (" · ".join(links) or "no linked account")
        if gu.get("mdblist_linked") and u.get("mdblist"):
            identity+=f" · MDBList: **{u['mdblist'].get('username') or 'unknown'}**"
        if selected in {"wetrakr","mdblist"}:
            sync=gu.get(f"{selected}_sync") or {}
            state="ready" if sync.get("seeded") else "first check will seed history"
            last=sync.get("last_activity")
            health=sync.get("health") or {}
            if health.get("error"):
                state+=f" · {health['error']} · {health.get('consecutive_failures',0)} failure(s)"
                if health.get("retry_at"):state+=f" · retry after {health['retry_at']}"
            lines.append(identity+f"\n  {provider_registry.get(selected).manifest.display_name} sync: **{state}**"+(f" · last activity {last}" if last else ""))
            continue
        expires=u.get("token_expires_at")
        if expires:
            remaining=parse_iso(expires)-now
            if remaining.total_seconds() <= 0:
                token_state="expired"
            elif remaining <= timedelta(days=1):
                token_state=f"expires in {max(int(remaining.total_seconds()//3600),0)}h"
            else:
                token_state=f"expires in {remaining.days}d"
        else:
            token_state="expiry unknown"
        last_poll=gu.get("last_poll_at")
        last_success=gu.get("last_success_at")
        last_error=gu.get("last_error")
        failures=max(int(gu.get("consecutive_failures",0) or 0),0)
        if failures>=MAX_CONSECUTIVE_FAILURES:
            health_state=f"paused · {failures} consecutive failure(s); automatic polling skipped until `/tracker-checknow` succeeds or the user relinks"
        elif failures:
            health_state=f"degraded · {failures} consecutive failure(s)"
        elif last_success:
            health_state="healthy"
        else:
            health_state="not checked successfully yet"
        health=f"health: **{health_state}**"
        health += f" · last poll {last_poll}" if last_poll else " · no poll recorded yet"
        health += f" · last success {last_success}" if last_success else " · no successful poll yet"
        if last_error:
            health += f" · last error: {last_error}"
        stats=gu.get("statistics") or {}
        watches=int(stats.get("episodes_watched",0))+int(stats.get("movies_watched",0))
        import_state="complete" if gu.get("history_seeded") else "pending"
        lines.append(identity+f" · SIMKL token: **{token_state}**"
                     f" · history: **{import_state}** ({watches:,} watches)\n  {health}")
    lines=[line[:650] for line in lines]
    if not user and len(users)>PAGE_SIZE:
        lines.append(f"and up to {len(users)-PAGE_SIZE:,} other tracking records · use `/tracker-status user:@username` to check a member")
    linked="\n".join(lines) if lines else ("This member has no tracker linked in this server." if user else "No currently linked accounts.")
    tracking_total=len(users)
    stale_note=f" · **{stale_count} stale record(s)**" if stale_count else ""
    await i.followup.send(embed=discord.Embed(title="Tracker Status",color=0x5865F2,description=
        f"**Posting channel:** {text}\n"
        f"**Poll interval:** every {POLL_INTERVAL_MINUTES} minute(s)\n"
        f"**Tracking records:** {tracking_total} · **Members checked:** {current_count}{stale_note}\n\n"
        f"**{'Selected member' if user else 'Linked accounts (up to five)'}:**\n{linked}"),
        ephemeral=True,allowed_mentions=discord.AllowedMentions.none(),
    )

@bot.tree.command(name="tracker-checknow",description="(Admin) Check selected tracker activity in this server.")
async def tracker_checknow(i):
    global last_checknow_at
    g=guild_id(i)
    if not g or not is_admin(i): await i.response.send_message(NOT_ADMIN_MESSAGE,ephemeral=True); return
    if time.monotonic()-last_checknow_at<CHECKNOW_COOLDOWN_SECONDS:
        await i.response.send_message("Please wait before using /tracker-checknow again.",ephemeral=True); return
    if poll_lock.locked() or wetrakr_poll_lock.locked() or provider_registry.get("mdblist").lock.locked():
        await i.response.send_message("An activity check is already running.",ephemeral=True); return
    last_checknow_at=time.monotonic()
    await i.response.send_message("Checking this server's selected tracker activity now...",ephemeral=True)
    posted = await poll_providers(g, manual=True)
    total_posted=sum(posted.values())
    breakdown=", ".join(f"{provider_registry.get(source).manifest.display_name} {count}" for source,count in posted.items())
    await i.followup.send(
        f"Done. Posted **{total_posted}** new activity item(s) ({breakdown}). "
        "A new provider's first check seeds history without posting older watches.",
        ephemeral=True)

POLL_RETRY_DELAY_SECONDS=60
POLL_MAX_RETRY_DELAY_SECONDS=600

@bot.event
async def on_ready():
    log.info("Logged in as %s.",bot.user)
    for g in bot.guilds:
        await storage.ensure_guild(g.id)
    poll_task = getattr(bot, "_poll_task", None)
    if poll_task is None or poll_task.done():
        bot._poll_task = bot.loop.create_task(polling_loop(), name="tracker-polling")

async def polling_loop():
    await bot.wait_until_ready()
    retry_delay=POLL_RETRY_DELAY_SECONDS
    interval_seconds=POLL_INTERVAL_MINUTES*60
    next_run=time.monotonic()
    while not bot.is_closed():
        try:
            await poll_providers()
            await send_due_weekly_recaps()
            retry_delay=POLL_RETRY_DELAY_SECONDS
            next_run+=interval_seconds
            sleep_for=max(0,next_run-time.monotonic())
            if sleep_for:
                await asyncio.sleep(sleep_for)
            else:
                log.warning("Polling cycle exceeded the configured interval; starting the next cycle immediately.")
                next_run=time.monotonic()
        except Exception:
            log.exception("Polling cycle failed; retrying sooner instead of waiting for the full interval.")
            await asyncio.sleep(retry_delay)
            retry_delay=min(retry_delay*2,POLL_MAX_RETRY_DELAY_SECONDS)
            next_run=time.monotonic()


# Register both integrations once; command handlers route by the selected source.
import sys as _sys
provider_registry = ProviderRegistry()
provider_registry.register(SimklProvider(_sys.modules[__name__]))
provider_registry.register(WeTrakrProvider(_sys.modules[__name__]))
provider_registry.register(MDBListProvider(_sys.modules[__name__]))


async def selected_provider(guild_id, uid):
    source = await storage.get_activity_provider(guild_id, uid)
    if not source:
        raise ValueError('Link your selected tracker in this server with /tracker-link first.')
    provider = provider_registry.get(source)
    targets = await storage.get_provider_targets(source, str(guild_id), active_only=True)
    if not any(str(target['discord_user_id']) == str(uid) for target in targets):
        raise ValueError('Link your selected tracker in this server with /tracker-link first.')
    return provider, await provider.link(str(uid))


async def poll_providers(guild_id=None, *, manual=False):
    posted = {}
    for manifest in provider_registry.manifests():
        posted[manifest.name] = await provider_registry.get(manifest.name).poll(guild_id, manual=manual)
    return posted

if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN, log_handler=None)
