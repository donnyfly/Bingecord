import asyncio
from unittest.mock import AsyncMock
from trackerbot.metadata.community_metadata import enrich_community_metadata,canonical_genres


def test_exact_id_metadata_resolution_and_no_fuzzy_fallback():
    async def run():
        class Client:
            find_series_by_tvdb=AsyncMock(return_value=99)
            _get_series_details=AsyncMock(return_value={'id':99,'name':'Series','first_air_date':'2011-01-01',
                'status':'Ended','genres':[{'name':'Sci-Fi & Fantasy'}],'seasons':[{'season_number':1,'episode_count':2}]})
            _get_json=AsyncMock(return_value={'movie_results':[{'id':1},{'id':2}]})
        client=Client()
        entries=[{'meta_key':'tv:tvdb:1','movie':False,'ids':{'tvdb':'1'},'title':'Series'},
                 {'meta_key':'movie:imdb:tt1','movie':True,'ids':{'imdb':'tt1'},'title':'Ambiguous'}]
        result=await enrich_community_metadata(client,entries)
        assert result['tv:tvdb:1']['canonical']=='tv:tmdb:99'
        assert result['tv:tvdb:1']['genres']==['Fantasy','Science Fiction']
        assert result['tv:tvdb:1']['seasons']=={'1':2}
        assert not result['movie:imdb:tt1'].get('canonical')
        client.find_series_by_tvdb.assert_awaited_once_with('1')
    asyncio.run(run())
