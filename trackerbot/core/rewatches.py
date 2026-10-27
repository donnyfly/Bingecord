"""Classify current provider plays, never deleted local observations."""
from collections import defaultdict
from datetime import datetime, timezone


def rewatch_flags(plays, identity):
    groups=defaultdict(dict)
    for play in plays:
        key=identity(play)
        event=str(play.get('source_event_id') or '')
        if key is not None and event:groups[key][event]=play
    def order(pair):
        event,play=pair
        try:
            stamp=datetime.fromisoformat(play['watched_at'].replace('Z','+00:00'))
            if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=timezone.utc)
        except (KeyError,TypeError,ValueError,AttributeError):stamp=datetime.min.replace(tzinfo=timezone.utc)
        return stamp,event
    return {event:index>0 for group in groups.values()
            for index,(event,_) in enumerate(sorted(group.items(),key=order))}
