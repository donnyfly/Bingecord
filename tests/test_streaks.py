from datetime import date
import csv
import io
from trackerbot.core.streaks import analyze_streaks,streak_csv


def test_best_streak_dates_skip_empty_invalid_and_future_entries():
    report=analyze_streaks({'2026-11-01':{'total':1},'2026-11-02':{'total':2},
        '2026-11-03':{'total':0},'2026-11-04':{'total':1},'2026-11-05':{},
        'bad':{'total':2},'2026-11-20':{'total':1}},'Asia/Singapore',today=date(2026,11,19))
    assert (report['longest'],report['start'],report['end'])==(2,date(2026,11,1),date(2026,11,2))
    assert report['current']==0
    assert report['excluded']=={'empty':2,'invalid':1,'future':1}


def test_current_streak_survives_until_end_of_today():
    days={'2026-11-17':{'episodes':1},'2026-11-18':{'anime_episodes':2}}
    assert analyze_streaks(days,today=date(2026,11,19))['current']==2
    days['2026-11-19']={'total':1}
    assert analyze_streaks(days,today=date(2026,11,19))['current']==3
    assert analyze_streaks(days,today=date(2026,11,21))['current']==0


def test_streak_csv_uses_local_watch_dates_and_escapes_titles():
    stats={'watch_dates':{'2026-11-19':{'total':2}},'watch_events':{
        'a':{'title':'=unsafe','watched_at':'2026-11-18T17:00:00Z'},
        'b':{'title':'Second title','watched_at':'2026-11-19T01:00:00Z'},
        'c':{'title':'Another day','watched_at':'2026-11-18T01:00:00Z'}}}
    report=analyze_streaks(stats['watch_dates'],'Asia/Singapore',today=date(2026,11,19))
    rows=list(csv.DictReader(io.StringIO(streak_csv(stats,report).decode('utf-8-sig'))))
    assert rows==[{'local_date':'2026-11-19','recorded_watches':'2',
                  'sample_titles_up_to_10':"'=unsafe | Second title"}]
