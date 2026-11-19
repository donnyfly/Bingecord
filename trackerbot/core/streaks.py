"""Read-only streak calculation and supporting-date diagnostics."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def analyze_streaks(watch_dates, timezone_name='UTC', *, today=None):
    try:
        zone=ZoneInfo(timezone_name)
    except (TypeError,ValueError,ZoneInfoNotFoundError):
        zone=ZoneInfo('UTC')
    today=today or datetime.now(zone).date()
    days={};excluded={'empty':0,'invalid':0,'future':0}
    for value,counts in (watch_dates or {}).items():
        try:
            day=date.fromisoformat(value)
            if not isinstance(counts,dict):raise ValueError()
            total=int(counts['total']) if 'total' in counts else sum(int(counts.get(key,0)) for key in
                 ('episodes','movies','anime_episodes','anime_movies'))
        except (TypeError,ValueError,OverflowError):
            excluded['invalid']+=1;continue
        if total<=0:
            excluded['empty']+=1;continue
        if day>today:
            excluded['future']+=1;continue
        days[day]=total
    current=0;cursor=today if today in days else today-timedelta(days=1)
    while cursor in days:
        current+=1;cursor-=timedelta(days=1)
    longest=length=0;previous=start=best_start=best_end=None
    for day in sorted(days):
        if previous is not None and day-previous==timedelta(days=1):length+=1
        else:length=1;start=day
        if length>longest:longest=length;best_start=start;best_end=day
        previous=day
    return {'current':current,'longest':longest,'start':best_start,'end':best_end,
            'days':days,'excluded':excluded,'timezone':zone.key}


def streak_csv(stats,report):
    """Daily counts and up to ten title examples for the longest stored run."""
    import csv
    from io import StringIO
    titles={};zone=ZoneInfo(report['timezone'])
    start,end=report['start'],report['end']
    for event in (stats.get('watch_events') or {}).values():
        if not isinstance(event,dict):continue
        try:
            stamp=datetime.fromisoformat(str(event.get('watched_at') or '').replace('Z','+00:00'))
            if stamp.tzinfo is None:
                from datetime import timezone
                stamp=stamp.replace(tzinfo=timezone.utc)
            day=stamp.astimezone(zone).date()
        except (TypeError,ValueError,OverflowError):continue
        if start and start<=day<=end:
            samples=titles.setdefault(day,[]);title=str(event.get('title') or 'Untitled')[:200]
            if title not in samples and len(samples)<10:samples.append(title)
    output=StringIO();writer=csv.writer(output)
    writer.writerow(['local_date','recorded_watches','sample_titles_up_to_10'])
    for day,count in sorted(report['days'].items()):
        if start<=day<=end:
            # Protect spreadsheet readers from interpreting title text as formulas.
            samples=' | '.join(titles.get(day,[]))
            if samples.startswith(('=','+','-','@')):samples="'"+samples
            writer.writerow([day.isoformat(),count,samples])
    return output.getvalue().encode('utf-8-sig')
