import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from test_notification_preview import bot, storage_module


@pytest.mark.parametrize('grouping,success',[(True,True),(True,False),(False,True)])
def test_poll_groups_first_episode_and_preserves_failed_checkpoints(monkeypatch,tmp_path,grouping,success):
    async def scenario():
        monkeypatch.setattr(storage_module,'_write_lock',asyncio.Lock())
        monkeypatch.setattr(storage_module,'_lock',asyncio.Lock())
        monkeypatch.setattr(storage_module,'DATA_PATH',str(tmp_path/'store.json'))
        store=storage_module.Storage()
        monkeypatch.setattr(bot,'storage',store)
        await store.set_channel('123','55')
        await store.set_features('123',{'watched_together':grouping})
        for uid in ('41','42'):
            await store.link_user('123',uid,'token',None,uid,'2026-09-20T00:00:00Z',simkl_account_id=1)
            store._data['guilds']['123']['users'][uid]['history_seeded']=True
            await store.update_activity_state('123',uid,statuses={'shows:99':'plantowatch'},statuses_seeded=True)
        monkeypatch.setattr(store,'prepare_empty_history_repair',AsyncMock(return_value=False))
        get_progression=store.get_progression
        monkeypatch.setattr(store,'get_progression',AsyncMock(return_value={
            'history_xp_seeded':True,'history_xp_notification_sent':True}))
        monkeypatch.setattr(bot,'valid_token',AsyncMock(return_value='token'))
        stamp='2026-09-27T12:00:00Z'
        item={'status':'watching','show':{'title':'House of the Dragon','poster':'poster','ids':{'simkl':99}},
              'seasons':[{'number':1,'episodes':[{'number':1,'watched_at':stamp}]}]}
        monkeypatch.setattr(bot,'cached_simkl_activities',AsyncMock(return_value=(
            {bot.ACTIVITY_KEYS['shows']:{'all':stamp}},'token')))
        monkeypatch.setattr(bot,'cached_simkl_items',AsyncMock(return_value=([item],'token')))
        monkeypatch.setattr(bot,'reconcile_watch_progression',AsyncMock(return_value=('token',0)))
        monkeypatch.setattr(bot,'evaluate_achievements',AsyncMock(return_value=[]))
        monkeypatch.setattr(bot,'notify_level_up',AsyncMock())
        monkeypatch.setattr(bot,'refresh_community_state',AsyncMock())
        member=SimpleNamespace(display_avatar=SimpleNamespace(url='https://example.com/avatar.png'))
        monkeypatch.setattr(bot,'resolve_member',AsyncMock(return_value=(member,'Tester')))
        monkeypatch.setattr(bot,'prefs',AsyncMock(return_value={'activity_text':'detailed','artwork':'backdrop',
                          'episode_code':False,'show_imdb':False,'show_mal':False}))
        monkeypatch.setattr(bot,'episode_media',AsyncMock(return_value=('https://example.com/still.jpg','Episode title',None,45)))
        channel=SimpleNamespace(id=55,send=AsyncMock())
        monkeypatch.setattr(bot.bot,'get_channel',lambda _:channel)
        sender=AsyncMock(return_value=success)
        monkeypatch.setattr(bot,'send_embed',sender)
        before={uid:await store.get_last_checked('123',uid) for uid in ('41','42')}
        await bot.poll_all('123')
        assert sender.await_count==(1 if grouping else 2)
        embed=sender.await_args.args[1]
        assert embed.image.url.endswith('still.jpg')
        assert ('🆕 Both started this series.' if grouping else '🆕 Started watching this series.') in embed.description
        if grouping:
            assert embed.description.splitlines()[0].endswith('together')
        for uid in ('41','42'):
            state=await store.get_activity_state('123',uid)
            last=await store.get_last_checked('123',uid)
            if success:
                assert last['shows']==stamp
                assert state['watch_times']['shows:99:1:1']==stamp
                assert state['statuses']['shows:99']=='watching'
                assert (await get_progression(uid))['xp']>0
            else:
                assert last==before[uid]
                assert not state['watch_times']
                assert state['statuses']['shows:99']=='watching'
        if success:
            sender.reset_mock()
            await bot.poll_all('123')
            sender.assert_not_awaited()
    asyncio.run(scenario())
