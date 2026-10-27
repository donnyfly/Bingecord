import asyncio
import copy
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from trackerbot.core import storage as module
from trackerbot.core.historical_repair import build_preview,hydrate_simkl,canonicalize
from trackerbot.core.tracker_mapping import WatchIdentity


def progression():
    return {'xp':0,'lifetime_xp':0,'prestige':0,'xp_events':[],'watch_xp_keys':{},'challenge_completions':{},'pending_challenge_notifications':[]}


def event(key,stamp='2026-10-01T00:00:00Z',title='Film',ids=None):
    return {'event_key':key+':'+stamp,'media_type':'movie','at':stamp,'title':title,'amount':400,'ids':ids or {}}


def test_first_account_history_earns_xp_but_second_unmatched_history_is_held():
    p=progression();first=event('movie:movies:1')
    assert module._add_watch_xp_events(p,[first],historical=True)[0]==400
    second=event('mdblist:7/2','2026-10-02T00:00:00Z','Different spelling',{'tmdb':1})
    assert module._add_watch_xp_events(p,[second],historical=True)==(0,[])
    assert p['lifetime_xp']==400 and second['event_key'] in p['pending_history_awards']
    assert module._add_watch_xp_events(p,[second],historical=True)==(0,[])
    live=event('mdblist:7/3','2026-10-03T00:00:00Z',ids={'tmdb':1})
    assert module._add_watch_xp_events(p,[live])[0]==400
    assert p['lifetime_xp']==800


def test_verified_duplicate_import_is_supported_without_new_xp_or_hold():
    p=progression();a=event('movie:movies:1',ids={'tmdb':1});b=event('mdblist:7/2',ids={'tmdb':1})
    module._add_watch_xp_events(p,[a],historical=True)
    assert module._add_watch_xp_events(p,[b],historical=True)==(0,[])
    assert not p['pending_history_awards']


@pytest.mark.parametrize('title,other,season,number,date',[
    ('Naruto Shippuuden','Naruto Shippūden',1,220,'2025-10-15T00:00:00Z'),
    ('Dragon Ball Z','Dragon Ball Z',9,38,'2026-03-20T00:00:00Z'),
    ('One Piece','One Piece',1,148,'1970-01-01T00:00:00Z'),
])
def test_audit_regression_aliases_numbering_and_import_dates_never_create_extra_history_xp(title,other,season,number,date):
    p=progression()
    a={'event_key':'anime_episode:series:anime:99:1:291:2026-03-21T09:35:38Z',
       'media_type':'anime_episode','title':title,'at':'2026-03-21T09:35:38Z','amount':96,'ids':{}}
    b={'event_key':'mdblist:7/2:'+date,'item_key':f'mdblist:episode:series:{season}:{number}',
       'media_type':'anime_episode','title':other,'at':date,'amount':96,'ids':{'tmdb':12971}}
    module._add_watch_xp_events(p,[a],historical=True)
    assert module._add_watch_xp_events(p,[b],historical=True)==(0,[])
    assert p['lifetime_xp']==96 and not p['challenge_completions']


def test_simkl_native_id_hydration_preserves_xp_and_disambiguates_aliases():
    p=progression();a=event('movie:movies:55',title='Romaji')
    module._add_watch_xp_events(p,[a]);before=p['lifetime_xp']
    assert hydrate_simkl(p,[SimpleNamespace(media_type='movie',title_ids={'simkl':55,'tmdb':12})])==1
    identity=next(iter(p['occurrence_ledger'].values()))['observations'][a['event_key']]['identity']
    assert identity['ids']['tmdb']==12 and p['lifetime_xp']==before
    assert p['xp_events'][0]['ids']['tmdb']==12


def test_preview_confirms_only_verified_close_time_duplicate_and_recalculates_projection():
    p=progression();a=event('movie:movies:1',ids={'tmdb':1});b=event('mdblist:7/2',ids={'tmdb':1})
    module._add_watch_xp_events(p,[a])
    module._add_watch_xp_events(p,[b])  # Native ledger already suppresses the exact duplicate.
    # Simulate legacy separate awards before catalog IDs were recovered.
    p['xp_events'].append(b);p['lifetime_xp']+=400;p['xp']+=400
    row=p['occurrence_ledger'][a['event_key']];row['award_keys'].append(b['event_key'])
    before=copy.deepcopy(p)
    report=build_preview(p,module._revoke_unmet_watch_challenges)
    assert report['confirmed_watch_xp_reduction']==400 and report['projected']['lifetime_xp']==400
    assert p==before


def test_confirmed_duplicate_preview_revokes_only_dependent_challenge_reward():
    p=progression();a=event('movie:movies:1',ids={'tmdb':1});b=event('mdblist:7/2',ids={'tmdb':1})
    module._add_watch_xp_events(p,[a]);module._add_watch_xp_events(p,[b])
    p['xp_events'].append(b)
    p['occurrence_ledger'][a['event_key']]['award_keys'].append(b['event_key'])
    p['xp']=p['lifetime_xp']=2000
    p['challenge_completions']={'daily:2026-10-01':{'test':{'xp':1200,'definition':{'kind':'movies','target':2}}}}
    original=copy.deepcopy(p);report=build_preview(p,module._revoke_unmet_watch_challenges)
    assert report['confirmed_watch_xp_reduction']==400 and report['dependent_challenge_xp_reduction']==1200
    assert report['projected']['lifetime_xp']==400 and p==original


def test_preview_preserves_legacy_prestige_offset_without_any_correction():
    p=progression();p.update(prestige=2,xp=100,lifetime_xp=100)
    report=build_preview(p,module._revoke_unmet_watch_challenges)
    assert report['projected']['prestige']==2 and report['projected']['xp']==100


@pytest.mark.parametrize('kind',['different_date','title_only','id_conflict','rewatch_ambiguity'])
def test_uncertain_histories_are_not_automatically_deducted(kind):
    p=progression();a=event('movie:movies:1',ids={'tmdb':1});b=event('mdblist:7/2','2026-10-02T00:00:00Z',ids={'tmdb':1})
    if kind=='title_only':a['ids']={};b['ids']={}
    if kind=='id_conflict':b['ids']={'tmdb':2}
    module._add_watch_xp_events(p,[a]);module._add_watch_xp_events(p,[b])
    if kind=='rewatch_ambiguity':module._add_watch_xp_events(p,[event('mdblist:7/3','2026-10-03T00:00:00Z',ids={'tmdb':1})])
    before=copy.deepcopy(p);report=build_preview(p,module._revoke_unmet_watch_challenges)
    assert report['confirmed_watch_xp_reduction']==0 and report['uncertain_overlaps']
    assert p==before


def test_absolute_crosswalk_requires_complete_run_and_preserves_raw_coordinates():
    async def run():
        p=progression();p['occurrence_ledger']={}
        for number in range(1,5):
            i=WatchIdentity('anime_episode','Anime','2026-10-01T00:00:00Z',1,number,{'tmdb':10})
            ref=f'anime_episode:series:anime:99:1:{number}:2026-10-01T00:00:00Z'
            p['occurrence_ledger'][ref]={'award_keys':[],'observations':{ref:{'provider':'simkl','identity':asdict(i)}}}
        p['occurrence_ledger']['other']={'award_keys':[],'observations':{'mdblist:7/1:2026-10-01T00:00:00Z':{
            'provider':'mdblist','identity':asdict(WatchIdentity('anime_episode','Anime','2026-10-01T00:00:00Z',2,2,{'tmdb':10}))}}}
        async def series(_):return {'seasons':[{'season_number':n,'episode_count':2} for n in (1,2)]}
        async def season(_,n):return {'episodes':[{'episode_number':i,'id':n*10+i} for i in (1,2)]}
        report=await canonicalize(p,SimpleNamespace(_get_series_details=series,_get_season_details=season))
        ref=next(k for k in p['occurrence_ledger'] if ':1:4:' in k)
        obs=p['occurrence_ledger'][ref]['observations'][ref]
        assert obs['identity']['season']==1 and obs['identity']['episode']==4
        assert (obs['canonical_identity']['season'],obs['canonical_identity']['episode'])==(2,2)
        assert report['canonicalized_observations']==4
    asyncio.run(run())


def test_repair_preview_command_is_private_and_never_changes_progression(monkeypatch):
    async def run():
        import json
        from unittest.mock import AsyncMock
        from test_notification_preview import bot
        p=progression();p['xp']=p['lifetime_xp']=400
        before=copy.deepcopy(p)
        monkeypatch.setattr(bot.storage,'get_user',AsyncMock(return_value={'access_token':'SECRET'}))
        monkeypatch.setattr(bot.storage,'get_progression',AsyncMock(return_value=p))
        i=SimpleNamespace(guild_id=1,guild=SimpleNamespace(id=1),user=SimpleNamespace(id=42),
                          response=SimpleNamespace(defer=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()))
        await bot.tracker_mapping.callback(i,repair_preview=True)
        sent=i.followup.send.await_args.kwargs
        payload=sent['file'].fp.read().decode();report=json.loads(payload)
        assert sent['ephemeral'] and report['read_only'] and 'SECRET' not in payload
        assert p==before and report['confirmed_watch_xp_reduction']==0
        sent['file'].close()
    asyncio.run(run())
