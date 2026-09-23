import asyncio
import hmac
import hashlib
import json
import os
from contextlib import asynccontextmanager
from typing import Any
from fastapi import FastAPI, Depends, HTTPException, Request, Security
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, Field
from . import config, db, accounts, library
from .model import forecast, calculate
from .agent import run_agent, execute_tool, READ_TOOLS, WRITE_TOOLS
from .optimizer import optimize
from .events import EVENTS, event_dataset, scenario_forecast, transition, recommendations
from .presentation import presentation

@asynccontextmanager
async def lifespan(app):
    db.init_db()
    accounts.init_accounts()
    yield

app = FastAPI(title='Аким на 5 часов API', version='2.0.0', lifespan=lifespan,
              description='Python simulator with SQLite, versioned data and authenticated AI editing tools.')
hosts = [s.strip() for s in os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver').split(',') if s.strip()]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
security = HTTPBearer(auto_error=False)
solver_lock = asyncio.Lock()
solver_cache = {}
analysis_tasks = {}

@app.middleware('http')
async def headers(request, call_next):
    if request.method in ['POST', 'PUT', 'PATCH', 'DELETE'] and request.headers.get('origin'):
        from urllib.parse import urlsplit
        if urlsplit(request.headers['origin']).netloc != request.headers.get('host'):
            return JSONResponse({'detail':'Недопустимый источник запроса.'},status_code=403)
    if request.method in ['POST', 'PUT', 'PATCH']:
        try:
            length = int(request.headers.get('content-length', '0'))
        except ValueError:
            return JSONResponse({'detail': 'Invalid Content-Length'}, status_code=400)
        if length > 262144:
            return JSONResponse({'detail': 'Request too large'}, status_code=413)
        body = await request.body()
        if len(body) > 262144:
            return JSONResponse({'detail': 'Request too large'}, status_code=413)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response

def authenticate(request: Request, credentials: HTTPAuthorizationCredentials | None = Security(security)):
    if credentials and credentials.scheme.lower() == 'bearer':
        for role, token in config.credentials().items():
            if token and len(token) >= 24 and hmac.compare_digest(credentials.credentials.encode(), token.encode()):
                return role
    user = accounts.session_user(request.cookies.get(accounts.COOKIE))
    if user:
        return 'user:' + str(user['id'])
    raise HTTPException(401, 'Войдите в аккаунт.')

def actor_role(actor):
    if actor in ['admin','agent']:return 'admin'
    with db.connect() as c:
        row=c.execute('SELECT role FROM users WHERE id=? AND active=1',(int(actor.split(':')[1]),)).fetchone()
    if not row:raise HTTPException(401,'Сессия недействительна.')
    return row['role']

def administrator(actor=Depends(authenticate)):
    if actor_role(actor)!='admin':raise HTTPException(403,'Доступ только для администратора.')
    return actor

def own_scenario(scenario_id,actor):
    s=db.get_scenario(scenario_id)
    if actor_role(actor)!='admin' and s.get('owner')!=actor:raise HTTPException(403,'Это сценарий другого пользователя.')
    return s

@app.exception_handler(db.ConflictError)
async def conflict_error(request, exc):
    return JSONResponse({'detail': str(exc)}, status_code=409)

@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse({'detail': str(exc)}, status_code=422)

@app.exception_handler(TypeError)
async def type_error(request, exc):
    return JSONResponse({'detail': 'Неверная структура данных.'}, status_code=422)

@app.exception_handler(KeyError)
async def key_error(request, exc):
    return JSONResponse({'detail': str(exc)}, status_code=404)

class Body(BaseModel):
    model_config = ConfigDict(extra='forbid')

class PlanItem(Body):
    measure_id: str = Field(min_length=1, max_length=40)
    district_id: str | None = Field(default=None, max_length=40)

class ForecastRequest(Body):
    plan: list[PlanItem] = Field(max_length=5)
    dataset_version: int | None = Field(default=None, ge=1)
    partial: bool = False
    use_ai: bool = False
    event_id: str = Field(default='none', max_length=40)

class ScenarioRequest(Body):
    name: str = Field(min_length=1, max_length=120)
    plan: list[PlanItem] = Field(max_length=5)
    dataset_version: int = Field(ge=1)
    expected_revision: int | None = Field(default=None, ge=1)
    event_id: str = Field(default='none', max_length=40)

class PresentationRequest(ForecastRequest):
    title: str = Field(default='План развития Астаны', min_length=1, max_length=120)

class DatasetRequest(Body):
    data: dict[str, Any]
    expected_version: int = Field(ge=1)
    note: str = Field(default='Редактирование', max_length=300)

class RecordRequest(Body):
    patch: dict[str, Any]
    expected_version: int = Field(ge=1)
    operation: str = 'update'

class SettingsRequest(Body):
    patch: dict[str, str]
    expected_revision: int = Field(ge=1)

class AgentRequest(Body):
    message: str = Field(min_length=1, max_length=6000)

class ToolRequest(Body):
    name: str = Field(min_length=1, max_length=80)
    arguments: dict[str, Any]

def plan_json(items):
    return [p.model_dump(exclude_none=True) for p in items]

class LoginRequest(Body):
    username: str = Field(min_length=1,max_length=40)
    password: str = Field(min_length=1,max_length=200)

class UserRequest(LoginRequest):
    display_name: str = Field(min_length=1,max_length=120)
    role: str = 'employee'

class PasswordRequest(Body):
    current_password: str = Field(max_length=200)
    new_password: str = Field(min_length=8,max_length=200)

class WorkspaceRequest(Body):
    state: dict[str,Any]
    revision: int = Field(ge=0)

@app.post('/api/auth/login')
def account_login(body: LoginRequest, request: Request):
    raw,user=accounts.login(body.username,body.password,request.client.host if request.client else 'unknown')
    response=JSONResponse(user)
    response.set_cookie(accounts.COOKIE,raw,httponly=True,samesite='lax',max_age=604800,secure=request.url.scheme=='https' or request.headers.get('x-forwarded-proto')=='https')
    return response

@app.post('/api/auth/logout')
def account_logout(request: Request):
    accounts.logout(request.cookies.get(accounts.COOKIE))
    response=JSONResponse({'ok':True});response.delete_cookie(accounts.COOKIE)
    return response

@app.post('/api/auth/password')
def change_password(body: PasswordRequest, actor=Depends(authenticate)):
    if not actor.startswith('user:'):raise HTTPException(400,'Войдите по логину и паролю.')
    uid=int(actor.split(':')[1])
    with db.connect() as c:
        row=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
        if not accounts.verify(body.current_password,row['password_hash']):raise HTTPException(403,'Текущий пароль неверен.')
        c.execute('UPDATE users SET password_hash=? WHERE id=?',(accounts.password_hash(body.new_password),uid))
        c.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
        db.audit(c,actor,'change_password','account')
    return {'ok':True}

@app.get('/api/users')
def list_users(actor=Depends(administrator)):
    with db.connect() as c:return [accounts.public_user(r) for r in c.execute('SELECT * FROM users ORDER BY id')]

@app.post('/api/users',status_code=201)
def add_user(body: UserRequest,actor=Depends(administrator)):
    return accounts.create_user(body.username,body.password,body.display_name,body.role,actor)

@app.get('/api/workspace')
def read_workspace(actor=Depends(authenticate)):
    if not actor.startswith('user:'):raise HTTPException(400,'Войдите по логину и паролю.')
    return accounts.workspace(int(actor.split(':')[1]))

@app.put('/api/workspace')
def write_workspace(body: WorkspaceRequest,actor=Depends(authenticate)):
    if not actor.startswith('user:'):raise HTTPException(400,'Войдите по логину и паролю.')
    state=body.state
    if len(db.dump(state))>180000:raise HTTPException(413,'Черновик слишком большой.')
    if state.get('version'):
        calculated=scenario_forecast(db.get_dataset(state['version'])['data'],state.get('plan',[]),True,state.get('eventId','none'))
        if not calculated['valid']:raise ValueError('Недопустимый план в черновике.')
    return accounts.save_workspace(int(actor.split(':')[1]),state,body.revision)

@app.get('/api/dashboard')
def dashboard():
    dataset=db.get_dataset();fact=calculate(dataset['data'],[],True)
    return {'dataset':dataset,'fact':fact,'settings':db.get_settings(),'updated_at':db.now()}

@app.get('/health')
def health():
    with db.connect() as c:
        c.execute('SELECT 1')
    return {'status': 'ok'}

@app.get('/api/bootstrap')
def bootstrap(dataset_version: int | None = None, event_id: str = 'none'):
    dataset = db.get_dataset(dataset_version)
    adjusted, event = event_dataset(dataset['data'], event_id)
    return {'dataset': {**dataset, 'data': adjusted}, 'event': event, 'events': EVENTS, 'settings': db.get_settings(), 'baseline': calculate(dataset['data'], [], True),
            'llm_configured': bool(config.openai_config()['key']), 'editing_configured': any(len(x) >= 24 for x in config.credentials().values())}

@app.get('/api/auth/me')
def me(actor=Depends(authenticate)):
    if actor.startswith('user:'):
        with db.connect() as c:user=accounts.public_user(c.execute('SELECT * FROM users WHERE id=?',(int(actor.split(':')[1]),)).fetchone())
        return {**user, 'can_edit': user['role']=='admin'}
    return {'id':actor,'username':actor,'role':'admin','can_edit':True}

@app.post('/api/forecast')
async def make_forecast(body: ForecastRequest, request: Request, credentials: HTTPAuthorizationCredentials | None = Security(security)):
    dataset = db.get_dataset(body.dataset_version)
    plan = plan_json(body.plan); result = scenario_forecast(dataset['data'], plan, body.partial, body.event_id)
    if not result['valid']:
        raise HTTPException(422, result['errors'])
    result['dataset_version'] = dataset['version']
    if body.use_ai:
        actor = authenticate(request, credentials)
        if not result['prediction']['complete']:
            raise HTTPException(422, 'AI-анализ доступен для пяти решений.')
        cache_key=hashlib.sha256(db.dump(['analysis-v2',actor,plan,dataset['version'],body.event_id,config.openai_config()['model']]).encode()).hexdigest()
        with db.connect() as c:cached=c.execute('SELECT result_json FROM analysis_cache WHERE cache_key=?',(cache_key,)).fetchone()
        async def generate():
            answer = await run_agent('''Вызови calculate_forecast и suggest_improvements для переданного плана, версии и event_id. Подготовь подробный управленческий разбор на русском, 600–900 слов, с 7 разделами: 1. Итог и бюджет; 2. Почему каждая из пяти мер полезна (цепочка мера → показатели → район → Score из impact_chains); 3. Положительные последствия; 4. Отрицательные эффекты и риски; 5. Что остаётся нерешённым и кому достанется меньше улучшений; 6. Практические шаги внедрения по этапам и что проверять; 7. Как улучшить план и какие компромиссы принять. Для рисков укажи причину, возможное последствие и меру снижения. Отделяй численные эффекты модели от гипотез о реализации: не выдумывай вероятности, реальные сметы или срок строительства. Эффекты отдельных мер не складываются. Если замены не улучшают Score, объясни почему всё равно нужны мониторинг и проверка предположений. Не объявляй оптимальность без подтверждения полного поиска. Не изменяй данные.''' , actor, False, {'plan': plan, 'dataset_version': dataset['version'], 'event_id': body.event_id})
            if answer.get('mode')=='openai':
                with db.connect() as c:c.execute('INSERT OR REPLACE INTO analysis_cache VALUES(?,?,?)',(cache_key,db.dump(answer),db.now()))
            return answer
        if cached:result['ai']=json.loads(cached['result_json'])
        else:
            if cache_key not in analysis_tasks:
                task=asyncio.create_task(generate());analysis_tasks[cache_key]=task
                task.add_done_callback(lambda done: analysis_tasks.pop(cache_key,None))
            result['ai']=await asyncio.shield(analysis_tasks[cache_key])

    if body.use_ai and result.get('ai',{}).get('mode')=='openai':
        library.save(actor,'analysis','AI-разбор сценария',plan,dataset['version'],body.event_id,{'answer':result['ai']['answer']})
    return result

@app.get('/api/optimize')
async def best_plan(dataset_version: int | None = None, event_id: str = 'none', actor=Depends(authenticate)):
    dataset = db.get_dataset(dataset_version); version = dataset['version']
    adjusted, event = event_dataset(dataset['data'], event_id)
    cache_key = (version, event_id)
    if cache_key not in solver_cache:
        if solver_lock.locked():
            raise HTTPException(429, 'Поиск уже выполняется. Повторите запрос чуть позже.')
        async with solver_lock:
            result = await asyncio.to_thread(optimize, adjusted)
            if len(solver_cache) >= 20:
                solver_cache.pop(next(iter(solver_cache)))
            solver_cache[cache_key] = result
    best=solver_cache[cache_key]
    if best.get('plan'):
        library.save(actor,'optimization','Лучший сценарий' if best.get('optimality_proven') else 'Лучший найденный сценарий',best['plan'],version,event_id,{'search':best})
    return {**best, 'dataset_version': version, 'event': event}

@app.post('/api/event-transition')
def event_transition(body: ForecastRequest):
    dataset = db.get_dataset(body.dataset_version)
    return transition(dataset['data'], plan_json(body.plan), body.event_id)

@app.post('/api/recommendations')
def recommend(body: ForecastRequest, actor=Depends(authenticate)):
    dataset = db.get_dataset(body.dataset_version)
    result=recommendations(dataset['data'], plan_json(body.plan), body.event_id)
    library.save(actor,'recommendations','Как улучшить план',plan_json(body.plan),dataset['version'],body.event_id,{'recommendations':result})
    return result

@app.post('/api/presentation')
def export_presentation(body: PresentationRequest, actor=Depends(authenticate)):
    dataset = db.get_dataset(body.dataset_version)
    plan = plan_json(body.plan)
    result = scenario_forecast(dataset['data'], plan, False, body.event_id)
    if not result['valid']:
        raise HTTPException(422, result['errors'])
    html=presentation(dataset['data'], plan, result, body.title, dataset['version'])
    result_id=library.save(actor,'presentation',body.title,plan,dataset['version'],body.event_id,{'html':html})
    return {'filename': 'astana-scenario.html', 'html':html,'result_id':result_id}

@app.get('/api/library')
def result_library(actor=Depends(authenticate)):
    return library.list_results(actor)

@app.get('/api/library/{source}/{result_id}')
def result_detail(source: str,result_id: str,actor=Depends(authenticate)):
    return library.get_result(actor,source,result_id)

@app.get('/api/datasets/versions')
def versions():
    return db.dataset_versions()

@app.get('/api/datasets/{version}')
def dataset_version(version: int):
    return db.get_dataset(version)

@app.put('/api/dataset')
def dataset_replace(body: DatasetRequest, actor=Depends(administrator)):
    return db.replace_dataset(body.data, body.expected_version, actor, body.note)

@app.patch('/api/data/{collection}/{record_id}')
def record_edit(collection: str, record_id: str, body: RecordRequest, actor=Depends(administrator)):
    return db.edit_record(collection, record_id, body.patch, body.expected_version, actor, body.operation)

@app.get('/api/settings')
def settings():
    return db.get_settings()

@app.patch('/api/settings')
def settings_edit(body: SettingsRequest, actor=Depends(administrator)):
    return db.update_settings(body.patch, body.expected_revision, actor)

@app.get('/api/scenarios')
def scenarios(actor=Depends(authenticate)):
    return [s for s in db.list_scenarios() if actor_role(actor)=='admin' or s.get('owner')==actor]

@app.get('/api/scenarios/{scenario_id}')
def scenario_get(scenario_id: str, actor=Depends(authenticate)):
    return own_scenario(scenario_id, actor)

@app.post('/api/scenarios', status_code=201)
def scenario_create(body: ScenarioRequest, actor=Depends(authenticate)):
    return db.save_scenario(body.name, plan_json(body.plan), body.dataset_version, actor, event_id=body.event_id)

@app.put('/api/scenarios/{scenario_id}')
def scenario_update(scenario_id: str, body: ScenarioRequest, actor=Depends(authenticate)):
    own_scenario(scenario_id,actor)
    return db.save_scenario(body.name, plan_json(body.plan), body.dataset_version, actor, scenario_id, body.expected_revision, body.event_id)

@app.delete('/api/scenarios/{scenario_id}')
def scenario_delete(scenario_id: str, expected_revision: int, actor=Depends(authenticate)):
    own_scenario(scenario_id,actor)
    return db.delete_scenario(scenario_id, expected_revision, actor)

@app.get('/api/audit')
def audit_log(actor=Depends(administrator)):
    return db.list_audit()

@app.get('/api/agent/tools')
def agent_tools(actor=Depends(administrator)):
    return {'tools': READ_TOOLS + WRITE_TOOLS}

@app.post('/api/agent/execute')
def agent_execute(body: ToolRequest, actor=Depends(administrator)):
    return execute_tool(body.name, body.arguments, actor, True)

@app.post('/api/agent/chat')
async def agent_chat(body: AgentRequest, actor=Depends(administrator)):
    return await run_agent(body.message, actor, True)

@app.get('/api/admin/export')
def export_data(actor=Depends(administrator)):
    return {'dataset': db.get_dataset(), 'settings': db.get_settings(), 'scenarios': db.list_scenarios(), 'versions': db.dataset_versions(), 'audit': db.list_audit()}

@app.get('/')
def index():
    return FileResponse(config.ROOT / 'static/index.html')

app.mount('/static', StaticFiles(directory=config.ROOT / 'static'), name='static')
