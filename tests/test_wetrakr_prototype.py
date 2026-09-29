import asyncio

from trackerbot.integrations.wetrakr_client import WeTrakrClient, WeTrakrError
from trackerbot.integrations.wetrakr_events import normalize_compact_play, normalize_journal_entry


class Response:
    def __init__(self, data, headers=None, status=200):
        self.data = data
        self.headers = headers or {}
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self, content_type=None):
        return self.data


class Session:
    closed = False

    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return next(self.responses)


def test_device_flow_refresh_and_headers():
    async def run():
        session = Session([
            Response({"device_code": "temporary", "interval": 5}),
            Response({"access_token": "access", "refresh_token": "refresh"}),
            Response({"access_token": "new", "new_refresh_token": "rotated"}),
            Response({"movies": {"all": "2026-09-28T00:00:00Z"}}),
        ])
        client = WeTrakrClient("app-key", session)
        assert (await client.device_code())["interval"] == 5
        assert (await client.device_token("temporary"))["refresh_token"] == "refresh"
        assert (await client.refresh_token("refresh"))["refresh_token"] == "rotated"
        assert (await client.last_activities("access"))["movies"]["all"]
        assert session.calls[0][2]["headers"]["wetrakr-api-version"] == "1"
        assert session.calls[-1][2]["headers"]["Authorization"] == "Bearer access"
        assert session.calls[1][2]["json"] == {"client_id": "app-key", "code": "temporary"}
    asyncio.run(run())


def test_journal_pages_compact_cursor_and_quota_error():
    async def run():
        session = Session([
            Response({"journal": [{"entry_id": "a"}]}, {"X-Pagination-Page-Count": "2"}),
            Response({"journal": [{"entry_id": "b"}]}, {"X-Pagination-Page-Count": "2"}),
            Response([{"play_id": "one"}], {"X-Pagination-Next": "opaque"}),
            Response([{"play_id": "two"}]),
            Response({"error": "QUOTA_EXCEEDED", "message": "Daily limit"}, status=429),
        ])
        client = WeTrakrClient("app-key", session)
        assert [e["entry_id"] for e in await client.journal("token", "2026-09-28T00:00:00Z")] == ["a", "b"]
        assert session.calls[1][2]["params"]["page"] == 2
        rows = [page async for page in client.compact_history("token", "episodes")]
        assert rows == [[{"play_id": "one"}], [{"play_id": "two"}]]
        assert session.calls[3][2]["params"]["after"] == "opaque"
        assert "page" not in session.calls[3][2]["params"]
        try:
            await client.last_activities("token")
        except WeTrakrError as error:
            assert error.code == "QUOTA_EXCEEDED"
        else:
            raise AssertionError("Daily quota errors must be surfaced without retry")
        assert len(session.calls) == 5
    asyncio.run(run())


def test_play_ids_survive_date_edits_and_status_rollups_do_not_award_watches():
    play = {"entry_id": "entry-1", "category": "watched", "status": "added",
            "type": "episode", "id": 99, "media_id": 11, "season_number": 2,
            "number": 3, "play_id": "stable-play", "watched_at": "2026-09-28T01:00:00Z"}
    first = normalize_journal_entry(play)
    edited = normalize_journal_entry({**play, "entry_id": "entry-2", "status": "updated",
                                      "watched_at": "2026-09-27T01:00:00Z"})
    removed = normalize_journal_entry({**play, "entry_id": "entry-3", "status": "removed"})
    assert first["source_event_id"] == edited["source_event_id"] == removed["source_event_id"]
    assert [first["action"], edited["action"], removed["action"]] == ["added", "updated", "removed"]
    assert normalize_journal_entry({**play, "type": "show", "play_id": None})["status"] == "completed"
    assert normalize_journal_entry({**play, "category": "ratings"}) is None
    compact = normalize_compact_play({"type": "episode", "id": 99, "play_id": "stable-play",
                                      "show_id": 11, "season_number": 2, "number": 3,
                                      "watched_at": "2026-09-28T01:00:00Z"})
    assert compact["source_event_id"] == first["source_event_id"]
    assert compact["wetrakr_id"] == 99


if __name__ == "__main__":
    test_device_flow_refresh_and_headers()
    test_journal_pages_compact_cursor_and_quota_error()
    test_play_ids_survive_date_edits_and_status_rollups_do_not_award_watches()
