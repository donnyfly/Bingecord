"""Read-only personal rating snapshots; title IDs retain their target namespace."""
import json
import math
from .wetrakr_client import page_rows

KINDS={'movies':'movie','shows':'show','anime':'show','seasons':'season','episodes':'episode'}


def normalize(row, kind, provider):
    if not isinstance(row,dict):raise ValueError('Invalid personal rating row')
    score=row.get("rating_precise") if row.get("rating_precise") is not None else row.get("user_rating",row.get("rating"))
    if isinstance(score,bool):raise ValueError('Invalid personal rating')
    try:score=float(score)
    except (TypeError,ValueError):raise ValueError('Invalid personal rating') from None
    if isinstance(row.get('rating'),bool) or not math.isfinite(score) or not (0<=score<=10 if provider=='wetrakr' else 0<score<=10):
        raise ValueError('Invalid personal rating range')
    nested=row.get(kind) if isinstance(row.get(kind),dict) else {}
    parent=row.get('show') if isinstance(row.get('show'),dict) else nested.get('show') if isinstance(nested.get('show'),dict) else {}
    if kind in {'season','episode'}:
        ids=row.get('show_ids') or parent.get('ids') or nested.get('show_ids') or {}
        if not ids:
            ids={key:row['show_'+key] for key in ('tmdb','tvdb','imdb','simkl') if row.get('show_'+key)}
        native=row.get('show_id') or parent.get('id')
    else:
        entry=nested or (row.get('anime') if isinstance(row.get('anime'),dict) else {})
        ids=entry.get('ids') or row.get('ids') or {}
        native=entry.get('id') or row.get('id')
        if not ids:ids={key:row[key] for key in ('tmdb','tvdb','imdb','simkl') if row.get(key)}
    ids={key:str(value) for key,value in ids.items() if value is not None and key in {'tmdb','tvdb','imdb','mal','simkl','wetrakr','mdblist'}}
    if native and provider in {'wetrakr','mdblist'}:ids.setdefault(provider,str(native))
    if not ids:raise ValueError('Personal rating has no scoped title IDs')
    result={'kind':kind,'ids':ids,'score':score,'scale':10}
    if kind in {'season','episode'}:
        season=row.get('season_number',row.get('season',nested.get('season_number',nested.get('season'))))
        if isinstance(season,dict):season=season.get('number')
        if kind=='season' and season is None:season=row.get('number',nested.get('number'))
        try:result['season']=int(season)
        except (TypeError,ValueError):raise ValueError('Personal rating lacks parent season') from None
        if kind=='episode':
            number=row.get('number',row.get('episode_number',row.get('episode',nested.get('number',nested.get('episode_number')))))
            if isinstance(number,dict):number=number.get('number')
            try:result['episode']=int(number)
            except (TypeError,ValueError):raise ValueError('Personal rating lacks episode') from None
    return result


def signature(activities):
    def find(data,prefix=''):
        rows={}
        for key,value in data.items():
            if isinstance(value,dict):rows.update(find(value,prefix+key+'.'))
            elif 'rat' in key.lower():rows[prefix+key]=value
        return rows
    relevant=find(activities)
    # Some APIs expose only a journal/all invalidation stamp.
    return json.dumps(relevant or {key:activities.get(key) for key in ('journal_at','all')},sort_keys=True)


async def fetch(app, provider, account, previous_signature=None):
    uid=account.discord_user_id
    if provider=='simkl':
        user=await app.storage.get_user(uid)
        token=await app.valid_token(uid,user)
        activities=await app.simkl.get_activities(token)
    elif provider=='wetrakr':
        token=await app.wetrakr_sync.auth.access_token(uid)
        activities=await app.wetrakr.last_activities(token)
    else:
        token=await app.mdblist_auth.access_token(uid)
        activities=await app.mdblist_tracking.last_activities(token)
    mark=signature(activities)
    if previous_signature==mark:return mark,None
    result=[]
    if provider=='simkl':
        for bucket in ('movies','shows','anime'):
            # Without the score segment SIMKL returns the entire library,
            # including unrated entries. Explicit scores give a complete rated
            # snapshot, and {} is the documented successful empty response.
            data=await app.simkl._get(f'/sync/ratings/{bucket}/1,2,3,4,5,6,7,8,9,10',token,
                                      params=app.simkl._params(extended='full'))
            if not isinstance(data,dict) or any(key not in KINDS for key in data):
                raise ValueError('Unsupported SIMKL personal ratings snapshot')
            rows=data.get(bucket) or []
            if not isinstance(rows,list):raise ValueError('Invalid SIMKL ratings bucket')
            result.extend(normalize(row,KINDS[bucket],provider) for row in rows)
    elif provider=='mdblist':
        async for data in app.mdblist_tracking._cursor_pages(token,'/sync/ratings',{'limit':1000},tuple(KINDS)[:2]+('seasons','episodes')):
            for bucket in ('movies','shows','seasons','episodes'):
                result.extend(normalize(row,KINDS[bucket],provider) for row in data.get(bucket) or [])
    else:
        for bucket in ('movies','shows','seasons','episodes'):
            after=None;seen=set()
            while True:
                params={'limit':5000,'compact':'true'}
                if after:params['after']=after
                data,headers=await app.wetrakr._request('GET',f'/sync/ratings/{bucket}',token,params=params)
                result.extend(normalize(row,KINDS[bucket],provider) for row in page_rows(data))
                headers={key.lower():value for key,value in headers.items()}
                following=headers.get('x-pagination-next')
                if not following:break
                if not isinstance(following,str) or following in seen:
                    raise ValueError('WeTrakr ratings pagination repeated')
                seen.add(following);after=following
                if len(seen)>10000:raise ValueError('WeTrakr ratings pagination overflow')
    # A complete validated snapshot makes removals safe to apply.
    return mark,result
