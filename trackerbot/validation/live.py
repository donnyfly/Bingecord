"""Bounded, read-only API checks; no Discord connection or token refresh."""
import argparse
import asyncio
import copy
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from trackerbot.core import watch_ledger
from trackerbot.core.tracker_mapping import identity_from_play
from trackerbot.integrations.simkl_client import SimklClient, SimklAuthError
from trackerbot.integrations.wetrakr_client import WeTrakrClient, WeTrakrError, page_rows
from trackerbot.integrations.wetrakr_events import normalize_compact_play, normalize_journal_entry
from trackerbot.integrations.wetrakr_sync import overlap


async def sample_pages(pages, max_pages):
    """At the limit, conservatively report incomplete without an extra request."""
    rows, count = [], 0
    async for page in pages:
        rows.extend(page_rows(page))
        count += 1
        if count >= max_pages:
            return rows, False, count
    return rows, True, count


def local_summary(progression):
    saved = 'occurrence_ledger' in progression
    rows = watch_ledger.ensure(copy.deepcopy(progression))
    audit = watch_ledger.audit(rows)
    awards = [key for row in rows.values() for key in row['award_keys']]
    watch_events = [e for e in progression.get('xp_events', [])
                    if e.get('media_type') in {'episode', 'anime_episode', 'movie', 'anime_movie'}]
    xp_keys = {e.get('event_key') for e in watch_events}
    return {'xp': progression.get('xp', 0), 'lifetime_xp': progression.get('lifetime_xp', 0),
            'watch_xp': sum(int(e.get('amount', 0)) for e in watch_events),
            'ledger_state': 'saved' if saved else 'migration_preview', 'occurrences': len(rows),
            'verified': sum(row['match_reason'] == 'verified_id' for row in audit.values()),
            'legacy': sum(row['match_reason'] == 'legacy_title' for row in audit.values()),
            'needs_review': sum(row['needs_review'] for row in audit.values()),
            'double_award_occurrences': sum(len(row['award_keys']) > 1 for row in rows.values()),
            'award_references_without_xp_event': len(set(awards) - xp_keys),
            'watch_xp_events_without_ledger_award': len(xp_keys - set(awards)),
            'award_references_reused': len(awards) - len(set(awards)),
            'occurrences_without_support': sum(not row['observations'] for row in rows.values())}


async def check_wetrakr(client, link, guild_user, progression, max_pages):
    token = link['access_token']
    activities = await client.last_activities(token)
    if not isinstance(activities, dict) or not activities.get('all'):
        raise WeTrakrError(200, 'INVALID_RESPONSE', 'Missing all activity timestamp')
    stored = {str(play.get('source_event_id') or play_id): play
              for play_id, play in progression.get('wetrakr_plays', {}).items()
              if str(play.get('account_id')) == str(link['account_id'])}
    histories, plays = {}, {}
    first_episode = None
    for target in ('movies', 'episodes'):
        rows, complete, count = await sample_pages(client.compact_history(token, target), max_pages)
        normalized = [normalize_compact_play(row) for row in rows]
        if any(play is None for play in normalized):
            raise WeTrakrError(200, 'INVALID_RESPONSE', 'Unrecognized compact watch identity')
        for play in normalized:
            plays[play['source_event_id']] = play
            if play['media_type'] == 'episode' and first_episode is None:
                first_episode = play
        histories[target] = {'sampled_plays': len(rows), 'pages': count, 'complete': complete,
                             'undated_plays': sum(p.get('watched_at_unknown') or not p.get('watched_at') for p in normalized)}
    all_complete = all(h['complete'] for h in histories.values())
    lists = {}
    for status in ('watching', 'planning'):
        for target in ('movies', 'shows'):
            rows, complete, count = await sample_pages(client.tracking(token, status, target), max_pages)
            lists[f'{status}_{target}'] = {'sampled_titles': len(rows), 'pages': count, 'complete': complete}
    sync = guild_user.get('wetrakr_sync') or {}
    journal = {'status': 'not_seeded'}
    if sync.get('seeded') and sync.get('checkpoint'):
        try:
            rows = await client.journal(token, overlap(sync['checkpoint']),
                category='watched,watching,waiting,planning,dropped,paused', max_pages=max_pages)
            seen = set(sync.get('recent_entry_ids') or [])
            normalized = [normalize_journal_entry(row) for row in rows]
            journal = {'status': 'read', 'rows': len(rows),
                       'visible_until': getattr(rows, 'visible_until', None),
                       'journal_visible_until': activities.get('journal_visible_until'),
                       'unacknowledged_rows': sum(str(r.get('entry_id')) not in seen for r in rows),
                       'changes': dict(Counter(c.get('action') for c in normalized if c))}
        except WeTrakrError as exc:
            journal = {'status': 'blocked', 'http_status': exc.status, 'code': exc.code}
    episode = {'status': 'no_episode_sample'}
    if first_episode and first_episode.get('wetrakr_id'):
        data = await client.episode(first_episode['wetrakr_id'])
        episode = {'status': 'read', 'has_imdb_episode_id': bool((data.get('ids') or {}).get('imdb')),
                   'has_parent_media': bool(data.get('media') or data.get('show')),
                   'source_calendar_season': int(data.get('season_number') or 0) >= 1900}
    return {'status': 'read', 'history': histories, 'lists': lists, 'journal': journal,
            'episode_metadata': episode, 'sampled_live_plays_missing_from_store': len(set(plays) - set(stored)),
            'stored_plays_missing_from_live': len(set(stored) - set(plays)) if all_complete else None,
            'comparison_complete': all_complete,
            'imported_calendar_episode_labels': sum(bool(identity_from_play(p) and
                (identity_from_play(p).season or 0) >= 1900) for p in stored.values()),
            'imported_anime_movies': sum(p.get('media_type') == 'anime_movie' for p in stored.values()),
            'requests': dict(client.request_counts)}


async def check_simkl(client, token):
    settings = await client.get_user_settings(token)
    if not isinstance(settings, dict):
        raise ValueError('Invalid account response')
    catalogs = {}
    for kind in ('shows', 'anime', 'movies'):
        rows = await client.get_all_items(token, kind)
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise ValueError('Invalid catalog response')
        catalogs[kind] = {'titles': len(rows), 'statuses': dict(Counter(r.get('status', 'unknown') for r in rows)),
                          'dated_movie_watches': sum(bool(r.get('last_watched_at')) for r in rows if r.get('movie')),
                          'dated_episode_watches': sum(bool(e.get('watched_at')) for r in rows
                              for season in r.get('seasons') or [] for e in season.get('episodes') or [])}
    return {'status': 'read', 'catalogs': catalogs, 'requests': dict(client.request_counts)}


async def collect_report(snapshot, guild_id, user_id, providers, max_pages, keys, factories=None):
    user = snapshot.get('users', {}).get(user_id)
    guild_user = snapshot.get('guilds', {}).get(guild_id, {}).get('users', {}).get(user_id)
    if not user or not guild_user:
        raise ValueError('The requested user is not linked in this server snapshot')
    progression = user.get('progression') or {}
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'guild_id': guild_id, 'user_id': user_id,
              'mode': 'read_only', 'selected_source': guild_user.get('activity_provider', 'simkl'),
              'local': local_summary(progression), 'providers': {},
              'live_posting_and_reward_checks': 'pending_interactive_discord_checks'}
    factories = factories or {'wetrakr': WeTrakrClient, 'simkl': SimklClient}
    for provider in providers:
        key = keys.get(provider)
        token = (user.get('wetrakr') or {}).get('access_token') if provider == 'wetrakr' else user.get('simkl_token')
        if not key or not token:
            report['providers'][provider] = {'status': 'blocked', 'code': 'MISSING_KEY_OR_LINK'}
            continue
        client = factories[provider](key)
        try:
            report['providers'][provider] = (await check_wetrakr(client, user['wetrakr'], guild_user, progression, max_pages)
                if provider == 'wetrakr' else await check_simkl(client, token))
        except WeTrakrError as exc:
            report['providers'][provider] = {'status': 'blocked', 'http_status': exc.status, 'code': exc.code}
        except SimklAuthError:
            report['providers'][provider] = {'status': 'blocked', 'code': 'AUTHENTICATION_EXPIRED'}
        except Exception as exc:
            # Never include arbitrary API error text: it can contain credentials.
            report['providers'][provider] = {'status': 'blocked', 'code': type(exc).__name__}
        finally:
            await client.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', required=True, type=Path)
    parser.add_argument('--guild-id', required=True)
    parser.add_argument('--user-id', required=True)
    parser.add_argument('--provider', choices=('both', 'simkl', 'wetrakr'), default='both')
    parser.add_argument('--max-pages', type=int, default=2)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.max_pages < 1:
        parser.error('--max-pages must be positive')
    if args.output and args.output.exists():
        parser.error('--output must be a new file; never overwrite the live store')
    load_dotenv()
    try:
        snapshot = json.loads(args.store.read_text())
        providers = ('simkl', 'wetrakr') if args.provider == 'both' else (args.provider,)
        report = asyncio.run(collect_report(snapshot, args.guild_id, args.user_id, providers, args.max_pages,
            {'simkl': os.getenv('SIMKL_CLIENT_ID'), 'wetrakr': os.getenv('WETRAKR_API_KEY')}))
    except Exception as exc:
        parser.exit(2, f'Validation could not start: {type(exc).__name__}\n')
    serialized = json.dumps(report, indent=2) + '\n'
    if args.output:
        with args.output.open('x') as stream:
            stream.write(serialized)
    else:
        print(serialized, end='')
    blocked = any(p['status'] != 'read' for p in report['providers'].values())
    raise SystemExit(2 if blocked else 0)


if __name__ == '__main__':
    main()
