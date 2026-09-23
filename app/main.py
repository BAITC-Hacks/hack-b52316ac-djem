import asyncio
import hmac
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
from . import config, db
from .model import forecast, calculate
from .agent import run_agent, execute_tool, READ_TOOLS, WRITE_TOOLS
from .optimizer import optimize
from .events import EVENTS, event_dataset, scenario_forecast, transition, recommendations
from .presentation import presentation

@asynccontextmanager
async def lifespan(app):
    db.init_db()
    yield

app = FastAPI(title='Аким на 5 часов API', version='2.0.0', lifespan=lifespan,
              description='Python simulator with SQLite, versioned data and authenticated AI editing tools.')
hosts = [s.strip() for s in os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver').split(',') if s.strip()]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
security = HTTPBearer(auto_error=False)
solver_lock = asyncio.Lock()
solver_cache = {}

@app.middleware('http')
async def headers(request, call_next):
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

def authenticate(credentials: HTTPAuthorizationCredentials | None = Security(security)):
    if not credentials or credentials.scheme.lower() != 'bearer':
        raise HTTPException(401, 'Войдите с ADMIN_API_TOKEN или AGENT_API_TOKEN.')
    for role, token in config.credentials().items():
        if token and len(token) >= 24 and hmac.compare_digest(credentials.credentials.encode(), token.encode()):
            return role
    raise HTTPException(401, 'Неверный токен доступа или токены сервера не настроены.')

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
    return {'role': actor, 'can_edit': True}

@app.post('/api/forecast')
async def make_forecast(body: ForecastRequest, credentials: HTTPAuthorizationCredentials | None = Security(security)):
    dataset = db.get_dataset(body.dataset_version)
    plan = plan_json(body.plan); result = scenario_forecast(dataset['data'], plan, body.partial, body.event_id)
    if not result['valid']:
        raise HTTPException(422, result['errors'])
    result['dataset_version'] = dataset['version']
    if body.use_ai:
        actor = authenticate(credentials)
        if not result['prediction']['complete']:
            raise HTTPException(422, 'AI-анализ доступен для пяти решений.')
        result['ai'] = await run_agent('Вызови calculate_forecast для переданного плана, версии и event_id. Объясни сильные стороны, риски и компромиссы. Вызови suggest_improvements для рекомендаций. Не изменяй данные.', actor, False, {'plan': plan, 'dataset_version': dataset['version'], 'event_id': body.event_id})
    return result

@app.get('/api/optimize')
async def best_plan(dataset_version: int | None = None, event_id: str = 'none'):
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
    return {**solver_cache[cache_key], 'dataset_version': version, 'event': event}

@app.post('/api/event-transition')
def event_transition(body: ForecastRequest):
    dataset = db.get_dataset(body.dataset_version)
    return transition(dataset['data'], plan_json(body.plan), body.event_id)

@app.post('/api/recommendations')
def recommend(body: ForecastRequest):
    dataset = db.get_dataset(body.dataset_version)
    return recommendations(dataset['data'], plan_json(body.plan), body.event_id)

@app.post('/api/presentation')
def export_presentation(body: PresentationRequest):
    dataset = db.get_dataset(body.dataset_version)
    plan = plan_json(body.plan)
    result = scenario_forecast(dataset['data'], plan, False, body.event_id)
    if not result['valid']:
        raise HTTPException(422, result['errors'])
    return {'filename': 'astana-scenario.html', 'html': presentation(dataset['data'], plan, result, body.title, dataset['version'])}

@app.get('/api/datasets/versions')
def versions():
    return db.dataset_versions()

@app.get('/api/datasets/{version}')
def dataset_version(version: int):
    return db.get_dataset(version)

@app.put('/api/dataset')
def dataset_replace(body: DatasetRequest, actor=Depends(authenticate)):
    return db.replace_dataset(body.data, body.expected_version, actor, body.note)

@app.patch('/api/data/{collection}/{record_id}')
def record_edit(collection: str, record_id: str, body: RecordRequest, actor=Depends(authenticate)):
    return db.edit_record(collection, record_id, body.patch, body.expected_version, actor, body.operation)

@app.get('/api/settings')
def settings():
    return db.get_settings()

@app.patch('/api/settings')
def settings_edit(body: SettingsRequest, actor=Depends(authenticate)):
    return db.update_settings(body.patch, body.expected_revision, actor)

@app.get('/api/scenarios')
def scenarios():
    return db.list_scenarios()

@app.get('/api/scenarios/{scenario_id}')
def scenario_get(scenario_id: str):
    return db.get_scenario(scenario_id)

@app.post('/api/scenarios', status_code=201)
def scenario_create(body: ScenarioRequest, actor=Depends(authenticate)):
    return db.save_scenario(body.name, plan_json(body.plan), body.dataset_version, actor, event_id=body.event_id)

@app.put('/api/scenarios/{scenario_id}')
def scenario_update(scenario_id: str, body: ScenarioRequest, actor=Depends(authenticate)):
    return db.save_scenario(body.name, plan_json(body.plan), body.dataset_version, actor, scenario_id, body.expected_revision, body.event_id)

@app.delete('/api/scenarios/{scenario_id}')
def scenario_delete(scenario_id: str, expected_revision: int, actor=Depends(authenticate)):
    return db.delete_scenario(scenario_id, expected_revision, actor)

@app.get('/api/audit')
def audit_log(actor=Depends(authenticate)):
    return db.list_audit()

@app.get('/api/agent/tools')
def agent_tools(actor=Depends(authenticate)):
    return {'tools': READ_TOOLS + WRITE_TOOLS}

@app.post('/api/agent/execute')
def agent_execute(body: ToolRequest, actor=Depends(authenticate)):
    return execute_tool(body.name, body.arguments, actor, True)

@app.post('/api/agent/chat')
async def agent_chat(body: AgentRequest, actor=Depends(authenticate)):
    return await run_agent(body.message, actor, True)

@app.get('/api/admin/export')
def export_data(actor=Depends(authenticate)):
    return {'dataset': db.get_dataset(), 'settings': db.get_settings(), 'scenarios': db.list_scenarios(), 'versions': db.dataset_versions(), 'audit': db.list_audit()}

@app.get('/')
def index():
    return FileResponse(config.ROOT / 'static/index.html')

app.mount('/static', StaticFiles(directory=config.ROOT / 'static'), name='static')
