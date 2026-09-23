"""Read-only deployment check. Use --live-ai for one paid OpenAI analysis."""
import argparse
import sys
import httpx
from app.config import credentials

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8080')
    parser.add_argument('--live-ai', action='store_true')
    args=parser.parse_args()
    headers={}
    if args.live_ai:
        token=credentials()['admin'] or credentials()['agent']
        if not token:
            raise ValueError('Set ADMIN_API_TOKEN or AGENT_API_TOKEN in .env first.')
        headers['Authorization']='Bearer '+token
    with httpx.Client(base_url=args.url.rstrip('/'), timeout=420, headers=headers) as client:
        response=client.get('/health');response.raise_for_status()
        assert response.json()['status']=='ok'
        print('PASS: HTTP and database')
        response=client.get('/api/bootstrap');response.raise_for_status()
        data=response.json()
        assert len(data['dataset']['data']['categories'])==5
        print('PASS: five directions; dataset version', data['dataset']['version'])
        plan=[{'measure_id':'M2'},{'measure_id':'M3','district_id':'nura'},{'measure_id':'M8','district_id':'nura'},{'measure_id':'M9','district_id':'nura'},{'measure_id':'M14'}]
        response=client.post('/api/forecast',json={'plan':plan,'dataset_version':data['dataset']['version'],'use_ai':args.live_ai})
        response.raise_for_status();result=response.json()
        assert result['valid'] and result['prediction']['remaining_tenge']>=0
        print('PASS: forecast; score',round(result['prediction']['score'],6))
        if args.live_ai:
            assert result['ai']['mode']=='openai', result['ai'].get('status','AI unavailable')
            assert any(a['tool']=='calculate_forecast' and a['success'] for a in result['ai']['actions'])
            print('PASS: live OpenAI and calculation tool')
        response=client.get('/api/bootstrap',params={'event_id':'flood'});response.raise_for_status()
        assert response.json()['event']['reserve_tenge']>0
        print('PASS: city events')
    print('Installation checks completed. Verify external access in a private browser window.')

if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        print('FAIL:',type(exc).__name__, str(exc))
        sys.exit(1)
