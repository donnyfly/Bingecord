import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from trackerbot.core import storage as storage_module
from trackerbot.integrations.anilist_client import AniListEntry
from trackerbot.integrations.anilist_provider import AniListProvider, list_update, progress_state


def entry(kind="MANGA", **changes):
    return replace(
        AniListEntry(1, 10, kind, "Example", "TV", "CURRENT", 2, 0, 1760000000,
                     False, 12, "https://s4.anilist.co/cover.jpg", None),
        **changes,
    )


@pytest.mark.parametrize(("kind", "label"), [("ANIME", "Watched E03-E05"), ("MANGA", "Read Chapters 3–5")])
def test_progress_range_and_status(kind, label):
    previous = progress_state(entry(kind))
    assert list_update(entry(kind, progress=5), previous)[0].startswith(label)
    assert list_update(entry(kind, status="PAUSED"), previous)[0] == "Paused **Example**"
    assert list_update(entry(kind, progress=1), previous) is None
    assert list_update(entry(kind, private=True, progress=5), previous) is None
    assert list_update(entry(kind, progress=5), progress_state(entry(kind, private=True))) is None


def test_manga_and_anime_switch_independently_and_deliver_after_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_module, "DATA_PATH", str(tmp_path / "data.json"))
    store = storage_module.Storage()
    channel = object()
    app = SimpleNamespace(
        ANILIST_CLIENT_ID="123", storage=store,
        bot=SimpleNamespace(get_channel=lambda _: channel),
    )
    provider = AniListProvider(app)
    provider._send = AsyncMock(return_value=True)
    provider._read_list = AsyncMock(side_effect=lambda _, linked, kind: (entry(kind),))

    async def run():
        await store.link_user("1", "2", "simkl", None, "Viewer", storage_module.EPOCH_ISO)
        await store.link_provider_account("1", "2", {"access_token": "anilist"},
                                          {"id": 42, "username": "Viewer"}, provider="anilist")
        await store.set_channel("1", 3)
        assert await store.set_anilist_manga("1", "2", True)
        assert await store.get_activity_provider("1", "2") == "simkl"
        assert await provider.poll("1") == 0
        assert [call.args[-1] for call in provider._read_list.await_args_list] == ["MANGA"]
        provider._send.assert_not_awaited()

        provider._read_list.side_effect = lambda _, linked, kind: (entry(kind, progress=5),)
        provider._send.return_value = False
        assert await provider.poll("1") == 0
        provider._send.return_value = True
        assert await provider.poll("1") == 1
        assert provider._send.await_args.args[-1][0].startswith("Read Chapters 3–5")
        assert await provider.poll("1") == 0

        assert await store.set_activity_provider("1", "2", "anilist")
        provider._read_list.reset_mock()
        assert await provider.poll("1") == 0
        assert {call.args[-1] for call in provider._read_list.await_args_list} == {"MANGA", "ANIME"}
        provider._read_list.side_effect = lambda _, linked, kind: (entry(kind, progress=6),)
        assert await provider.poll("1") == 2
        assert await store.set_anilist_manga("1", "2", False)
        assert await store.get_activity_provider("1", "2") == "anilist"
        assert await store.unlink_provider_account("1", "2", provider="anilist")
        assert await store.get_activity_provider("1", "2") == "simkl"
        assert not await store.get_provider_targets("anilist")

    asyncio.run(run())
