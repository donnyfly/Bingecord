from datetime import datetime, timedelta, timezone

from trackerbot.core.manga_events import FIRST_WEEK, ROTATION, new_goal, score


def test_readathon_scales_from_recent_activity_and_requires_teamwork():
    start = datetime.combine(FIRST_WEEK, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=7)
    old = {"at": (start-timedelta(days=1)).isoformat(), "chapters": 1}
    records = {uid: {"events": [old]} for uid in ("1", "2", "3", "4")}
    goal = new_goal(FIRST_WEEK.isoformat(), start, end, records, set(records))
    assert goal["target"] == 600
    assert goal["required"] == 2
    records["1"]["events"].append({"at":start.isoformat(),"chapters":600,"media":"1:10"})
    assert not score(goal, records)["reached"]
    records["2"]["events"].append({"at":start.isoformat(),"chapters":30,"media":"2:10"})
    assert score(goal, records)["reached"]
    assert goal["target"] == 600


def test_manga_rotation_scores_distinct_titles_and_bingo_lines():
    start = datetime.combine(FIRST_WEEK, datetime.min.time(), tzinfo=timezone.utc)
    catalogue = {
        "10": {"title":"A","genres":["Drama","Comedy"],"year":1995,"format":"MANGA"},
        "11": {"title":"B","genres":["Comedy"],"year":2005,"format":"ONE_SHOT"},
        "12": {"title":"C","genres":["Comedy","Fantasy"],"year":2020,"format":"MANGA"},
    }

    for offset, kind in enumerate(ROTATION):
        week = start + timedelta(weeks=offset)
        records = {
            "1": {"catalogue":catalogue,"events":[
                {"at":week.isoformat(),"chapters":5,"media":"1:10","first":1,"pass_key":"1:10:0"},
                {"at":week.isoformat(),"chapters":1,"media":"1:11"},
                {"at":week.isoformat(),"chapters":5,"media":"1:12"},
            ]},
        }
        goal = new_goal(week.date().isoformat(), week, week+timedelta(days=7), records, {"1"})
        assert goal["kind"] == kind
        result = score(goal, records)
        assert result["total"] == (11 if kind == "readathon" else 2 if kind == "manga_decades" else 3 if kind == "manga_bingo" else 2)
        if kind == "manga_bingo":
            assert [o["done"] for o in result["objectives"]] == [True,True,True,False,False,False,False,False,False]
