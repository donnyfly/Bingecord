import copy
from trackerbot.core.xp_audit import build_xp_audit


def test_audit_counts_separate_unmatched_awards_and_contains_no_account_credentials():
    source={'xp':300,'lifetime_xp':300,'prestige':0,'xp_events':[
        {'event_key':'movie:movies:1:2026-10-01T00:00:00Z','at':'2026-10-01T00:00:00Z','amount':100,'media_type':'movie','title':'Film','ids':{'tmdb':1},'access_token':'NEVER_EXPORT'},
        {'event_key':'mdblist:7/2:2026-10-02T00:00:00Z','at':'2026-10-02T00:00:00Z','amount':200,'media_type':'movie','title':'Film','ids':{'tmdb':1}}]}
    before=copy.deepcopy(source)
    report=build_xp_audit(source)
    assert source==before
    assert report['xp_by_source_or_reward']=={'simkl':100,'mdblist':200}
    assert report['event_xp_sum']==300 and len(report['occurrences'])==2
    assert report['observation_counts']=={'simkl':1,'mdblist':1}
    assert 'NEVER_EXPORT' not in str(report) and report['read_only']


def test_mapping_export_is_private_and_attaches_only_progression_audit(monkeypatch):
    import asyncio,json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from test_notification_preview import bot
    async def run():
        monkeypatch.setattr(bot.storage,'get_user',AsyncMock(return_value={'access_token':'SECRET'}))
        monkeypatch.setattr(bot.storage,'get_progression',AsyncMock(return_value={'xp_events':[]}))
        monkeypatch.setattr(bot.storage,'get_mapping_audit',AsyncMock(return_value={key:0 for key in ('verified','legacy','unpaired','review','double_awards')}))
        i=SimpleNamespace(guild_id=1,guild=SimpleNamespace(id=1),user=SimpleNamespace(id=42),
                          response=SimpleNamespace(defer=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()))
        await bot.tracker_mapping.callback(i,True)
        i.response.defer.assert_awaited_once_with(ephemeral=True)
        kwargs=i.followup.send.await_args.kwargs
        assert kwargs['ephemeral'] and kwargs['file'].filename=='bingecord-xp-audit.json'
        payload=kwargs['file'].fp.read().decode()
        assert json.loads(payload)['read_only'] and 'SECRET' not in payload
        kwargs['file'].close()
    asyncio.run(run())
