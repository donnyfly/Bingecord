"""Persistent occurrences: observations support awards until the last is removed."""
from dataclasses import asdict
from .tracker_mapping import WatchIdentity, identity_from_event, identity_from_play, match_reason


def observe(rows, provider, key, identity):
    existing_id = next((occurrence for occurrence, row in rows.items()
                        if key in row['observations']), None)
    existing = rows.get(existing_id)
    if existing and len(existing['observations']) > 1:
        existing['observations'][key] = {'provider': provider, 'identity': asdict(identity) if identity else None}
        if identity and not all(match_reason(identity, WatchIdentity(**o['identity']))
                                for ref, o in existing['observations'].items()
                                if ref != key and o['identity']):
            existing['needs_review'] = True
        return existing
    candidates = []
    if identity:
        for occurrence, row in rows.items():
            if occurrence == existing_id:
                continue
            # Distinct events within one provider are distinct occurrences.
            if any(o['provider'] == provider for o in row['observations'].values()):
                continue
            reasons = [match_reason(identity, WatchIdentity(**o['identity']))
                       for o in row['observations'].values() if o['identity']]
            if any(reasons):
                candidates.append((row, 'verified_id' in reasons))
    verified = [row for row, strong in candidates if strong]
    matches = verified or [row for row, _ in candidates]
    if len(matches) == 1:
        row = matches[0]
        if existing:
            row['award_keys'].extend(k for k in existing['award_keys'] if k not in row['award_keys'])
            del rows[existing_id]
    elif existing:
        row = existing
        row['needs_review'] = row['needs_review'] or bool(matches)
    else:
        row = {'observations': {}, 'award_keys': [], 'needs_review': bool(matches)}
        rows[key] = row
    row['observations'][key] = {'provider': provider, 'identity': asdict(identity) if identity else None}
    return row


def ensure(progression):
    if 'occurrence_ledger' in progression:
        return progression['occurrence_ledger']
    rows = {}
    # Migrate conservatively: preserve all prior awards, including legacy doubles.
    for event in progression.get('xp_events', []):
        key = str(event.get('event_key') or '')
        if key.startswith(('wetrakr:','mdblist:')):
            continue
        identity = identity_from_event(event)
        if identity or (key and event.get('media_type') in {'episode', 'anime_episode', 'movie', 'anime_movie'}):
            observe(rows, 'simkl', key, identity)['award_keys'].append(key)
    for provider in ('wetrakr','mdblist'):
        for play_id, play in progression.get(provider+'_plays', {}).items():
            key = f"{provider}:{play_id}:{play['watched_at']}"
            row = observe(rows, provider, key, identity_from_play(play))
            if play.get('xp_key'):
                row['award_keys'].append(play['xp_key'])
    # Retain orphaned legacy awards; migration must never deduct earned XP.
    represented = {k for row in rows.values() for k in row['award_keys']}
    for event in progression.get('xp_events', []):
        key = str(event.get('event_key') or '')
        if key.startswith(('wetrakr:','mdblist:')) and key not in represented:
            observe(rows, key.split(':',1)[0], key, None)['award_keys'].append(key)
    progression['occurrence_ledger'] = rows
    return rows


def remove(progression, observation_key):
    rows = ensure(progression)
    for occurrence, row in list(rows.items()):
        if observation_key not in row['observations']:
            continue
        del row['observations'][observation_key]
        if row['observations']:
            return []
        del rows[occurrence]
        return row['award_keys']
    return []


def audit(rows):
    result = {}
    for key, row in rows.items():
        identities = [WatchIdentity(**o['identity']) for o in row['observations'].values() if o['identity']]
        reasons = [match_reason(a, b) for i, a in enumerate(identities) for b in identities[i+1:]]
        reason = 'verified_id' if 'verified_id' in reasons else ('legacy_title' if 'legacy_title' in reasons else None)
        result[key] = {'observations': [{'provider': o['provider'], 'event_key': ref}
                                       for ref, o in row['observations'].items()],
                       'award_keys': list(row['award_keys']), 'match_reason': reason,
                       'needs_review': row['needs_review'] or len(row['award_keys']) > 1}
    return result
