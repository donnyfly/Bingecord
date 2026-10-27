"""Conservative completion labels, independent of provider status names."""
from datetime import datetime, timezone


def aired(episode, now):
    stamp=episode.get('air_datetime') or episode.get('air_date')
    if not stamp:return False
    try:
        if 'T' in stamp:
            value=datetime.fromisoformat(stamp.replace('Z','+00:00'))
            if value.tzinfo is None:value=value.replace(tzinfo=timezone.utc)
            return value<=now
        return datetime.strptime(stamp,'%Y-%m-%d').date()<=now.date()
    except (TypeError,ValueError):return False


def outcome(series, seasons, watched, season, *, now=None, display_season=None):
    """Never equate 'all aired episodes watched' with a confirmed season finale."""
    now=now or datetime.now(timezone.utc)
    watched=set(watched)
    summaries={int(row['season_number']):row for row in series.get('seasons') or []
               if isinstance(row.get('season_number'),int) and row['season_number']>0}
    universe=set();complete_catalog=True
    for number,summary in summaries.items():
        data=seasons.get(number) or {}
        episodes=[row for row in data.get('episodes') or [] if isinstance(row.get('episode_number'),int)]
        expected=summary.get('episode_count')
        if not episodes or not isinstance(expected,int) or expected<=0 or len(episodes)!=expected:
            complete_catalog=False
        if any(not aired(row,now) for row in episodes):complete_catalog=False
        universe.update((number,row['episode_number']) for row in episodes if aired(row,now))
    ended=series.get('status') in {'Ended','Canceled','Cancelled'}
    if ended and summaries and complete_catalog and universe and universe<=watched:
        return {'kind':'series','text':'✅ Completed this series.'}
    data=seasons.get(season) or {}
    episodes=[row for row in data.get('episodes') or [] if isinstance(row.get('episode_number'),int)]
    expected=(summaries.get(season) or {}).get('episode_count')
    full=bool(episodes and isinstance(expected,int) and expected>0 and len(episodes)==expected
              and all(aired(row,now) for row in episodes))
    required={(season,row['episode_number']) for row in episodes}
    last=max(episodes,key=lambda row:row['episode_number']) if episodes else {}
    finale=str(last.get('episode_type') or last.get('milestone') or '').lower() in {'finale','season_finale','series_finale'}
    later=any(number>season and summary.get('air_date') and summary['air_date'][:10]<=now.date().isoformat()
              for number,summary in summaries.items())
    confirmed=ended or finale or later or data.get('is_complete') is True
    if full and confirmed and required<=watched and (display_season is None or display_season==season):
        return {'kind':'season','season':season,'text':f'🏁 Finished **Season {season}**.'}
    # The entire known airing catalogue must be available to claim caught up.
    loaded=set(seasons)>=set(summaries)
    expected_aired=all((seasons.get(number) or {}).get('episodes') is not None
                       and len((seasons.get(number) or {}).get('episodes') or [])==summary.get('episode_count')
                       for number,summary in summaries.items())
    if loaded and summaries and expected_aired and universe and universe<=watched:
        return {'kind':'caught_up','text':'✅ Caught up with all currently released episodes.'}
    return None
