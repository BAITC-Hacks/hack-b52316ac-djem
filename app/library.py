"""User-visible result library with immutable forecast snapshots."""
import hashlib
import json
from . import db, accounts
from .events import scenario_forecast

def decision_labels(version,plan):
    data=db.get_dataset(version)['data']
    measures={m['id']:m for m in data['measures']}
    districts={d['id']:d['name'] for d in data['districts']}
    return [{'name':measures[p['measure_id']]['name'],'district':districts.get(p.get('district_id'),'Весь город'),'cost_tenge':measures[p['measure_id']]['cost_tenge']} for p in plan]

def save(actor,kind,title,plan,version,event_id,payload):
    result=scenario_forecast(db.get_dataset(version)['data'],plan,False,event_id)
    if not result['valid']:return None
    identity=hashlib.sha256(db.dump([actor,kind,title,version,event_id,plan]).encode()).hexdigest()
    summary={'score':result['prediction']['score'],'delta':result['delta'],'cost_tenge':result['prediction']['cost_tenge'],'dataset_version':version,'event_name':result['event']['name']}
    content={'decisions':decision_labels(version,plan),'plan':plan,'dataset_version':version,'event_id':event_id,'forecast':result,**payload}
    with db.connect() as c:
        c.execute('INSERT INTO user_results VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET summary_json=excluded.summary_json,payload_json=excluded.payload_json,created_at=excluded.created_at',(identity,actor,kind,title,db.dump(summary),db.dump(content),db.now()))
    return identity

def owner_aliases(actor):
    if actor.startswith('user:'):
        with db.connect() as c:row=c.execute('SELECT username,role FROM users WHERE id=?',(int(actor.split(':')[1]),)).fetchone()
        if row and row['username']=='admin' and row['role']=='admin':return [actor,'admin']
    return [actor]

def list_results(actor):
    with db.connect() as c:
        rows=c.execute('SELECT id,kind,title,summary_json,created_at FROM user_results WHERE owner=? ORDER BY created_at DESC LIMIT 100',(actor,)).fetchall()
    items=[{'id':r['id'],'source':'result','kind':r['kind'],'title':r['title'],'created_at':r['created_at'],**json.loads(r['summary_json'])} for r in rows]
    for s in db.list_scenarios():
        if s.get('owner') not in owner_aliases(actor):continue
        p=s['forecast']['prediction']
        items.append({'id':s['id'],'source':'scenario','kind':'scenario','title':s['name'],'created_at':s['updated_at'],'score':p['score'],'delta':s['forecast']['delta'],'cost_tenge':p['cost_tenge'],'dataset_version':s['dataset_version'],'event_name':s['forecast'].get('event',{}).get('name','Обычные условия')})
    if actor.startswith('user:'):
        saved=accounts.workspace(int(actor.split(':')[1]))['state']
        if saved.get('plan'):
            items.insert(0,{'id':'current','source':'draft','kind':'draft','title':'Текущий рабочий план','decisions':len(saved['plan']),'dataset_version':saved.get('version'),'has_analysis':bool(saved.get('aiAnswer'))})
    return items

def get_result(actor,source,result_id):
    if source=='draft' and actor.startswith('user:'):
        state=accounts.workspace(int(actor.split(':')[1]))['state']
        return {'decisions':decision_labels(state.get('version'),state.get('plan',[])),'title':'Текущий рабочий план','kind':'draft','plan':state.get('plan',[]),'dataset_version':state.get('version'),'event_id':state.get('eventId','none'),'answer':state.get('aiAnswer',''),'forecast':scenario_forecast(db.get_dataset(state.get('version'))['data'],state.get('plan',[]),True,state.get('eventId','none'))}
    if source=='scenario':
        s=db.get_scenario(result_id)
        if s.get('owner') not in owner_aliases(actor):raise KeyError('Результат не найден.')
        return {'decisions':decision_labels(s['dataset_version'],s['plan']),'title':s['name'],'kind':'scenario',**s}
    if source!='result':raise KeyError('Результат не найден.')
    with db.connect() as c:row=c.execute('SELECT * FROM user_results WHERE id=? AND owner=?',(result_id,actor)).fetchone()
    if not row:raise KeyError('Результат не найден.')
    return {'title':row['title'],'kind':row['kind'],**json.loads(row['payload_json'])}
