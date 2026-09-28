import asyncio

import storage as storage_module


def test_wetrakr_link_preserves_simkl_and_unlink_is_independent(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module,"DATA_PATH",str(tmp_path/"store.json"))
        store=storage_module.Storage()
        await store.link_user("123","42","simkl-token",None,"simkl-user","2026-09-28T00:00:00Z")
        await store.link_wetrakr("123","42",{"access_token":"we-token","refresh_token":"we-refresh",
                                           "expires_at":"2026-10-05T00:00:00Z"},
                                 {"id":19,"username":"we-user"})
        user=await store.get_user("42")
        assert user["simkl_token"]=="simkl-token"
        assert user["wetrakr"]["account_id"]==19
        assert len(await store.get_poll_targets("123"))==1
        await store.unlink_user("123","42")
        user=await store.get_user("42")
        assert user["simkl_token"] is None
        assert user["wetrakr"]["access_token"]=="we-token"
        assert await store.get_poll_targets("123")==[]
        await store.link_user("123","42","simkl-again",None,"simkl-user","2026-09-28T00:00:00Z")
        assert (await store.get_user("42"))["wetrakr"]["access_token"]=="we-token"
        await store.unlink_wetrakr("123","42")
        assert (await store.get_user("42"))["simkl_token"]=="simkl-again"
        assert (await store.get_user("42"))["wetrakr"] is None
        assert len(await store.get_poll_targets("123"))==1
    asyncio.run(run())


def test_wetrakr_only_link_is_not_polled_as_simkl(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(storage_module,"DATA_PATH",str(tmp_path/"store.json"))
        store=storage_module.Storage()
        await store.link_wetrakr("123","42",{"access_token":"we-token","refresh_token":"we-refresh"},
                                 {"id":19,"username":"we-user"})
        assert await store.get_poll_targets("123")==[]
        assert await store.unlink_wetrakr("123","42")
        assert await store.get_user("42") is None
    asyncio.run(run())
