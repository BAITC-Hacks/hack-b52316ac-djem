import json
import pytest
from fastapi.testclient import TestClient
from app import config, db
from app.main import app
from app.model import calculate
from app.agent import execute_tool, run_agent
from app.optimizer import optimize

PLAN = [{'measure_id':'M2'}, {'measure_id':'M3','district_id':'nura'}, {'measure_id':'M8','district_id':'nura'}, {'measure_id':'M9','district_id':'nura'}, {'measure_id':'M14'}]
TOKEN = 'test-token-only-not-a-production-secret'

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DATABASE_PATH', tmp_path/'test.sqlite3')
    monkeypatch.setenv('ADMIN_API_TOKEN', TOKEN)
    monkeypatch.setenv('OPENAI_API_KEY', '')
    with TestClient(app) as c:
        yield c

def auth():
    return {'Authorization':'Bearer '+TOKEN}

def test_forecast_and_money(client):
    b=client.get('/api/bootstrap').json()
    assert b['baseline']['score']==pytest.approx(52.55768)
    assert b['dataset']['data']['budget_tenge']==10_000_000_000
    r=client.post('/api/forecast',json={'plan':PLAN}).json()
    assert r['prediction']['score']==pytest.approx(57.236735)
    assert r['prediction']['cost_tenge']==9_800_000_000
    assert r['prediction']['critical']==[]
    assert client.get('/').status_code==200
    assert client.get('/static/app.js').status_code==200
    assert client.get('/.env').status_code==404

def test_constraints(client):
    for plan in [PLAN[:4], PLAN+[PLAN[0]], [PLAN[0]]*5,
                 [{'measure_id':m,'district_id':'nura'} for m in ['M3','M5','M7','M8','M13']]]:
        assert client.post('/api/forecast',json={'plan':plan}).status_code==422
    p=[{'measure_id':'M1','district_id':'esil'},*PLAN[1:]]
    assert client.post('/api/forecast',json={'plan':p}).status_code==422
    assert client.post('/api/forecast',json={'plan':[],'partial':True}).status_code==200

def test_auth_versions_and_scenarios(client):
    body={'name':'Команда','plan':PLAN,'dataset_version':1}
    assert client.post('/api/scenarios',json=body).status_code==401
    s=client.post('/api/scenarios',json=body,headers=auth()).json()
    change={'patch':{'indicators':{'T1':90}},'expected_version':1}
    assert client.patch('/api/data/districts/nura',json=change).status_code==401
    assert client.patch('/api/data/districts/nura',json=change,headers=auth()).status_code==200
    assert client.patch('/api/data/districts/nura',json=change,headers=auth()).status_code==409
    old=client.get('/api/scenarios/'+s['id'],headers=auth()).json()
    assert old['forecast']==s['forecast']
    assert client.get('/api/bootstrap').json()['dataset']['version']==2
    r=client.post('/api/agent/execute',json={'name':'restore_dataset','arguments':{'version':1,'expected_version':2}},headers=auth())
    assert r.json()['version']==3
    assert client.delete('/api/scenarios/'+s['id']+'?expected_revision=99',headers=auth()).status_code==409
    assert client.delete('/api/scenarios/'+s['id']+'?expected_revision=1',headers=auth()).status_code==200
    assert len(client.get('/api/audit',headers=auth()).json())==4

def test_agent_permissions_and_missing_key(client):
    with pytest.raises(ValueError):
        execute_tool('update_site_settings',{},allow_write=False)
    assert client.post('/api/agent/chat',json={'message':'Измени заголовок'}).status_code==401
    answer=client.post('/api/agent/chat',json={'message':'Привет'},headers=auth()).json()
    assert answer['mode']=='unavailable'
    assert client.post('/api/forecast',json={'plan':PLAN,'use_ai':True}).status_code==401

def test_solver_reports_incomplete(client):
    d=db.get_dataset()['data']
    r=optimize(d,max_evaluations=10)
    assert not r['optimality_proven']
    assert r['evaluated']==10
    assert calculate(d,r['plan'])['valid']

def test_responses_tool_roundtrip(client,monkeypatch):
    import asyncio, httpx
    monkeypatch.setenv('OPENAI_API_KEY','fake-key-for-mocked-transport')
    seen=[]
    def handler(request):
        payload=json.loads(request.content); seen.append(payload)
        if len(seen)==1:
            return httpx.Response(200,json={'output':[{'type':'function_call','name':'get_city_state','arguments':'{}','call_id':'call_1'}]})
        assert payload['input'][-1]['type']=='function_call_output'
        assert payload['input'][-1]['call_id']=='call_1'
        return httpx.Response(200,json={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'Данные прочитаны.'}]}]})
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
            result=await run_agent('Прочитай данные','admin',False,client=transport)
            assert result['mode']=='openai'
            assert result['actions']==[{'tool':'get_city_state','success':True}]
    asyncio.run(check())

def test_events_preserve_shared_data_and_budget(client):
    original=client.get('/api/bootstrap').json()['dataset']
    event=client.get('/api/bootstrap?event_id=flood').json()
    assert event['dataset']['data']['budget_tenge']==8_500_000_000
    assert event['event']['reserve_tenge']==1_500_000_000
    assert client.get('/api/bootstrap').json()['dataset']==original
    assert client.post('/api/forecast',json={'plan':PLAN,'event_id':'flood'}).status_code==422
    transition=client.post('/api/event-transition',json={'plan':PLAN,'event_id':'flood'}).json()
    assert transition['removed']==[{'measure_id':'M3','district_id':'nura'}]
    result=client.post('/api/forecast',json={'plan':transition['plan'],'event_id':'flood','partial':True}).json()
    assert result['fact']['score']==pytest.approx(52.55768)
    assert result['prediction']['cost_tenge']<=8_500_000_000
    assert result['decision_delta']>result['delta']
    assert client.get('/api/bootstrap?event_id=invented').status_code==422

def test_event_scenario_and_presentation(client):
    plan=[{'measure_id':'M9','district_id':'nura'},{'measure_id':'M11','district_id':'almaty'},{'measure_id':'M10','district_id':'nura'},{'measure_id':'M12'},{'measure_id':'M4','district_id':'saryarka'}]
    body={'name':'Паводок','plan':plan,'dataset_version':1,'event_id':'flood'}
    r=client.post('/api/scenarios',json=body,headers=auth())
    assert r.status_code==201
    scenario=client.get('/api/scenarios/'+r.json()['id'],headers=auth()).json()
    assert scenario['event_id']=='flood'
    assert scenario['forecast']['prediction']['cost_tenge']==6_100_000_000
    deck=client.post('/api/presentation',headers=auth(),json={'title':'<script>alert(1)</script>','plan':plan,'dataset_version':1,'event_id':'flood'}).json()
    assert deck['html'].count('<section>')==5
    assert '<script>alert(1)</script>' not in deck['html']
    assert '&lt;script&gt;' in deck['html']
    assert '1 500 000 000 ₸' in deck['html']
    assert 'OPENAI_API_KEY' not in deck['html']
    assert client.post('/api/presentation',headers=auth(),json={'plan':plan[:3]}).status_code==422

def test_recommendations_are_valid_and_improve(client):
    plan=[{'measure_id':'M9','district_id':'esil'},{'measure_id':'M11','district_id':'esil'},{'measure_id':'M10','district_id':'esil'},{'measure_id':'M12'},{'measure_id':'M4','district_id':'esil'}]
    r=client.post('/api/recommendations',headers=auth(),json={'plan':plan,'event_id':'snow'}).json()
    assert r['suggestions']
    for suggestion in r['suggestions']:
        calculated=client.post('/api/forecast',json={'plan':suggestion['plan'],'event_id':'snow'}).json()
        assert calculated['valid']
        assert calculated['prediction']['score']==pytest.approx(suggestion['score'])
        assert suggestion['score']>r['current_score']
        assert suggestion['cost_tenge']<=9_000_000_000

def test_agent_event_tools(client):
    plan=[{'measure_id':'M9','district_id':'nura'},{'measure_id':'M11','district_id':'nura'},{'measure_id':'M10','district_id':'nura'},{'measure_id':'M12'},{'measure_id':'M4','district_id':'saryarka'}]
    result=execute_tool('calculate_forecast',{'plan':plan,'dataset_version':1,'event_id':'snow'},allow_write=False)
    assert result['event']['id']=='snow'
    saved=execute_tool('save_scenario',{'name':'Агент со событием','plan':plan,'dataset_version':1,'event_id':'snow'})
    assert saved['event_id']=='snow'
    with pytest.raises(ValueError):
        execute_tool('edit_data_record',{'collection':'districts','record_id':'nura','patch':[],'expected_version':1,'operation':'update'})

def test_openai_error_does_not_fake_success(client, monkeypatch):
    import asyncio, httpx
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-for-mocked-transport')
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(429, json={'error':{'type':'rate_limit_error'}}))) as transport:
            result=await run_agent('Проверь прогноз', 'admin', False, client=transport)
            assert result['mode']=='fallback'
            assert result['status']=='api_error'
            assert result['actions']==[]
            assert '429' in result['answer']
    asyncio.run(check())
    assert client.post('/api/forecast', json={'plan':PLAN}).status_code==200

def test_invalid_edits_are_atomic(client):
    before=client.get('/api/bootstrap').json()['dataset']
    invalid={'patch':{'indicators':{'T1':101}}, 'expected_version':before['version']}
    assert client.patch('/api/data/districts/nura',json=invalid,headers=auth()).status_code==422
    assert client.get('/api/bootstrap').json()['dataset']==before
    assert client.get('/api/audit',headers=auth()).json()==[]


def test_impact_chains_reconcile_and_threshold(client):
    r = client.post('/api/forecast', json={'plan': PLAN}).json()
    assert sum(r['explanation']['score_components'].values()) == pytest.approx(r['decision_delta'])
    for chain in r['explanation']['impact_chains']:
        assert sum(chain['components'].values()) == pytest.approx(chain['delta'])
        for district in chain['districts']:
            assert sum(m['weighted_delta'] for m in district['metrics']) == pytest.approx(district['delta'])
    m9 = next(c for c in r['explanation']['impact_chains'] if c['measure_id'] == 'M9')
    s1 = next(m for m in m9['districts'][0]['metrics'] if m['id'] == 'S1')
    assert s1['before'] == 38
    assert s1['after'] == 40.625
    assert s1['critical_removed']
    assert m9['components']['critical'] == 1


def test_impact_synergy_clipping_and_event(client):
    from copy import deepcopy
    from app.model import forecast
    from app.events import scenario_forecast
    data = client.get('/api/bootstrap').json()['dataset']['data']
    plan = [{'measure_id': 'M1', 'district_id': 'nura'}, {'measure_id': 'M2'}]
    r = forecast(data, plan, True)
    city = next(c for c in r['explanation']['impact_chains'] if c['measure_id'] == 'M2')
    assert len(city['districts']) == 5
    nura = next(d for d in city['districts'] if d['id'] == 'nura')
    assert next(m for m in nura['metrics'] if m['id'] == 'T1')['synergy_effect'] == 2
    capped = deepcopy(data)
    next(d for d in capped['districts'] if d['id'] == 'nura')['indicators']['T1'] = 99
    r = forecast(capped, plan[:1], True)
    metric = next(m for m in r['explanation']['impact_chains'][0]['districts'][0]['metrics'] if m['id'] == 'T1')
    assert metric['delta'] == 1 and metric['clipped']
    r = scenario_forecast(data, plan, True, 'snow')
    event_delta = r['without_decisions']['score'] - r['fact']['score']
    assert event_delta + sum(r['explanation']['score_components'].values()) == pytest.approx(r['delta'])
    empty = forecast(data, [], True)
    assert empty['explanation']['impact_chains'] == []


def test_accounts_roles_workspaces_and_logout(client):
    assert client.get('/api/dashboard').status_code == 200
    assert client.get('/api/users').status_code == 401
    assert client.get('/api/optimize').status_code == 401
    assert client.post('/api/auth/login',json={'username':'admin','password':'wrong'}).status_code == 401
    response=client.post('/api/auth/login',json={'username':'admin','password':'admin'})
    assert response.status_code == 200
    assert 'HttpOnly' in response.headers['set-cookie']
    assert client.get('/api/auth/me').json()['role'] == 'admin'
    assert client.post('/api/users',json={'username':'staff','password':'staff-password','display_name':'Сотрудник','role':'employee'}).status_code == 201
    assert 'password_hash' not in client.get('/api/users').text
    with db.connect() as c:
        assert c.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()[0] != 'admin'
    assert client.post('/api/auth/logout',json={}).status_code == 200
    assert client.get('/api/auth/me').status_code == 401
    assert client.post('/api/auth/login',json={'username':'staff','password':'staff-password'}).status_code == 200
    assert client.get('/api/users').status_code == 403
    assert client.patch('/api/data/districts/nura',json={'patch':{'name':'oops'},'expected_version':1}).status_code == 403
    assert client.post('/api/agent/chat',json={'message':'Измени бюджет'}).status_code == 403
    state={'plan':PLAN,'version':1,'eventId':'none','activeView':'planner','aiAnswer':'Сохранённый анализ'}
    assert client.put('/api/workspace',json={'state':state,'revision':0}).json()['revision'] == 1
    assert client.put('/api/workspace',json={'state':state,'revision':0}).status_code == 409
    assert client.get('/api/workspace').json()['state'] == state
    mine=client.post('/api/scenarios',json={'name':'Мой план','plan':PLAN,'dataset_version':1}).json()
    assert mine['owner'].startswith('user:')
    client.post('/api/auth/logout',json={})
    client.post('/api/auth/login',json={'username':'admin','password':'admin'})
    client.post('/api/users',json={'username':'staff2','password':'staff-password','display_name':'Другой','role':'employee'})
    client.post('/api/auth/logout',json={})
    client.post('/api/auth/login',json={'username':'staff2','password':'staff-password'})
    assert client.get('/api/scenarios').json()==[]
    assert client.get('/api/scenarios/'+mine['id']).status_code == 403
    assert client.delete('/api/scenarios/'+mine['id']+'?expected_revision=1').status_code == 403
    assert client.get('/api/workspace').json()['state']=={}
    assert client.post('/api/auth/password',json={'current_password':'staff-password','new_password':'changed-password'}).status_code==200
    assert client.get('/api/auth/me').status_code==401
    assert client.post('/api/auth/login',json={'username':'staff2','password':'changed-password'}).status_code==200
    assert client.put('/api/workspace',json={'state':{},'revision':0},headers={'Origin':'https://evil.example'}).status_code==403


def test_accounts_migration_does_not_reset_password(client):
    from app import accounts
    with db.connect() as c:c.execute("UPDATE users SET password_hash=? WHERE username='admin'",(accounts.password_hash('changed-password'),))
    accounts.init_accounts()
    assert client.post('/api/auth/login',json={'username':'admin','password':'admin'}).status_code==401
    assert client.post('/api/auth/login',json={'username':'admin','password':'changed-password'}).status_code==200


def test_login_rate_limit(client):
    for _ in range(10):
        assert client.post('/api/auth/login',json={'username':'admin','password':'wrong'}).status_code==401
    assert client.post('/api/auth/login',json={'username':'admin','password':'wrong'}).status_code==429


def test_cached_ai_is_user_specific(client,monkeypatch):
    from app import main
    calls=[]
    async def fake_agent(message,actor,allow_write,context):
        calls.append((actor,context))
        return {'mode':'openai','answer':'Подробный разбор','actions':[]}
    monkeypatch.setattr(main,'run_agent',fake_agent)
    body={'plan':PLAN,'dataset_version':1,'use_ai':True}
    assert client.post('/api/forecast',json=body,headers=auth()).status_code==200
    assert client.post('/api/forecast',json=body,headers=auth()).json()['ai']['answer']=='Подробный разбор'
    assert len(calls)==1
    client.post('/api/auth/login',json={'username':'admin','password':'admin'})
    assert client.post('/api/forecast',json=body).status_code==200
    assert len(calls)==2
    assert not calls[0][0]==calls[1][0]


def test_legacy_scenario_migration(client):
    from app import accounts
    s=client.post('/api/scenarios',json={'name':'Старый','plan':PLAN,'dataset_version':1},headers=auth()).json()
    with db.connect() as c:c.execute('ALTER TABLE scenarios DROP COLUMN owner')
    accounts.init_accounts()
    migrated=db.get_scenario(s['id'])
    assert migrated['forecast']==s['forecast']
    assert migrated['owner']=='admin'


def test_result_library_persistence_and_isolation(client,monkeypatch):
    from app import main
    assert client.get('/api/library').status_code==401
    client.post('/api/auth/login',json={'username':'admin','password':'admin'})
    client.post('/api/users',json={'username':'person1','password':'password-one','display_name':'Первый','role':'employee'})
    client.post('/api/users',json={'username':'person2','password':'password-two','display_name':'Второй','role':'employee'})
    client.post('/api/auth/logout',json={})
    client.post('/api/auth/login',json={'username':'person1','password':'password-one'})
    body={'plan':PLAN,'dataset_version':1,'title':'Мой отчёт'}
    deck=client.post('/api/presentation',json=body).json()
    assert deck['result_id']
    again=client.post('/api/presentation',json=body).json()
    assert again['result_id']==deck['result_id']
    entries=client.get('/api/library').json()
    assert len(entries)==1 and entries[0]['kind']=='presentation'
    assert 'html' not in entries[0]
    detail=client.get('/api/library/result/'+deck['result_id']).json()
    assert detail['html']==deck['html'] and detail['plan']==PLAN
    assert detail['forecast']['prediction']['score']==pytest.approx(57.236735)
    async def fake_agent(*args,**kwargs):return {'mode':'openai','answer':'Риски и рекомендации','actions':[]}
    monkeypatch.setattr(main,'run_agent',fake_agent)
    client.post('/api/forecast',json={'plan':PLAN,'dataset_version':1,'use_ai':True})
    client.post('/api/recommendations',json={'plan':PLAN,'dataset_version':1})
    kinds={r['kind'] for r in client.get('/api/library').json()}
    assert kinds=={'presentation','analysis','recommendations'}
    client.post('/api/auth/logout',json={})
    client.post('/api/auth/login',json={'username':'person2','password':'password-two'})
    assert client.get('/api/library').json()==[]
    assert client.get('/api/library/result/'+deck['result_id']).status_code==404
    client.post('/api/auth/logout',json={})
    client.post('/api/auth/login',json={'username':'person1','password':'password-one'})
    assert len(client.get('/api/library').json())==3
