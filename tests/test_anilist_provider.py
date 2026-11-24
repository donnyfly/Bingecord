import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from trackerbot.core import storage as storage_module
from trackerbot.integrations import anilist_provider
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


def test_authorize_acknowledges_command_and_opens_a_discord_compatible_modal():
    async def run():
        provider = AniListProvider(SimpleNamespace(ANILIST_CLIENT_ID="123", ANILIST_CLIENT_SECRET="secret"))
        response = SimpleNamespace(defer=AsyncMock())

        async def edit_original_response(**kwargs):
            response.defer.assert_awaited_once_with(ephemeral=True)

        user = SimpleNamespace(id=42)
        command = SimpleNamespace(guild_id=1, user=user, response=response,
                                  edit_original_response=AsyncMock(side_effect=edit_original_response))
        await provider.authorize(command)
        view = command.edit_original_response.await_args.kwargs["view"]
        reply = SimpleNamespace(user=user, response=SimpleNamespace(send_modal=AsyncMock()))
        await view.children[1].callback(reply)
        reply.response.send_modal.assert_awaited_once()
        modal = reply.response.send_modal.await_args.args[0]
        assert modal.to_dict()["components"][0]["components"][0]["max_length"] == 4000

    asyncio.run(run())


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
        assert await store.get_activity_provider("1", "2") == "simkl"
        assert await provider.initialize_manga("1", "2")
        assert [call.args[-1] for call in provider._read_list.await_args_list] == ["MANGA"]
        provider._send.assert_not_awaited()

        provider._read_list.side_effect = lambda _, linked, kind: (entry(kind, progress=5),)
        assert await provider.initialize_manga("1", "2")
        assert provider._read_list.await_count == 1
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
        await store.link_provider_account("1", "2", {"access_token": "renewed"},
                                          {"id": 42, "username": "Viewer"}, provider="anilist")
        assert await store.anilist_snapshot("1", "2", "renewed", "MANGA") == (False, None)
        assert await store.get_activity_provider("1", "2") == "anilist"
        assert await store.unlink_provider_account("1", "2", provider="anilist")
        assert await store.get_activity_provider("1", "2") == "simkl"
        assert not await store.get_provider_targets("anilist")

    asyncio.run(run())


def test_link_modal_enables_manga_and_seeds_it_silently(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_module, "DATA_PATH", str(tmp_path / "data.json"))
    store = storage_module.Storage()
    app = SimpleNamespace(
        ANILIST_CLIENT_ID="123", ANILIST_CLIENT_SECRET="secret", storage=store,
        calculate_token_expiry=lambda _: None,
    )
    provider = AniListProvider(app)
    provider._read_list = AsyncMock(return_value=(entry(),))
    provider._send = AsyncMock()
    client = SimpleNamespace(
        exchange_code=AsyncMock(return_value={"access_token": "token"}),
        viewer=AsyncMock(return_value=SimpleNamespace(id=42, name="Viewer")),
    )
    monkeypatch.setattr(anilist_provider, "AniListClient", lambda _: client)

    async def run():
        interaction = SimpleNamespace(
            guild_id=1, user=SimpleNamespace(id=2),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        await anilist_provider.AniListLinkModal(provider).on_submit(interaction)
        assert await store.anilist_snapshot("1", "2", "token", "MANGA") == (
            True, {"10": progress_state(entry())},
        )
        assert provider._read_list.await_args.args[-1] == "MANGA"
        provider._send.assert_not_awaited()
        assert "Manga tracking is on" in interaction.followup.send.await_args.args[0]

    asyncio.run(run())
