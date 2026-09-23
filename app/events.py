"""Optional synthetic stress tests; never mutate the shared competition dataset."""
from copy import deepcopy
from .model import forecast, calculate, validate_plan

EVENTS = [
    {'id': 'none', 'name': 'Обычные условия', 'description': 'Исходные условия кейса без дополнительных событий.', 'reserve_percent': 0, 'effects': {}},
    {'id': 'snow', 'name': 'Сильный снегопад', 'description': 'Снегопад снижает транспортные показатели на 8, безопасность на 4. 10% бюджета резервируется на экстренную уборку.', 'reserve_percent': 10, 'effects': {'transport': -8, 'safety': -4}},
    {'id': 'flood', 'name': 'Весенний паводок', 'description': 'Паводок снижает показатели сервисов на 10, транспорта на 4. 15% бюджета резервируется на экстренные работы.', 'reserve_percent': 15, 'effects': {'services': -10, 'transport': -4}},
    {'id': 'smog', 'name': 'Период сильного смога', 'description': 'Экологические показатели снижаются на 12, социальные на 3. 5% бюджета резервируется на экстренные меры.', 'reserve_percent': 5, 'effects': {'ecology': -12, 'social': -3}},
]

def event_info(event_id='none'):
    event = next((e for e in EVENTS if e['id'] == event_id), None)
    if event is None:
        raise ValueError('Неизвестное городское событие.')
    return deepcopy(event)

def event_dataset(data, event_id='none'):
    event = event_info(event_id)
    adjusted = deepcopy(data)
    reserve = data['budget_tenge'] * event['reserve_percent'] // 100
    adjusted['budget_tenge'] -= reserve
    for district in adjusted['districts']:
        for metric in adjusted['metrics']:
            key = metric['id']
            district['indicators'][key] = max(0, min(100, district['indicators'][key] + event['effects'].get(metric['category'], 0)))
    event.update(reserve_tenge=reserve, original_budget_tenge=data['budget_tenge'], available_budget_tenge=adjusted['budget_tenge'])
    return adjusted, event

def scenario_forecast(data, plan, partial=False, event_id='none'):
    adjusted, event = event_dataset(data, event_id)
    result = forecast(adjusted, plan, partial)
    if not result['valid']:
        return result
    result['event'] = event
    result['without_decisions'] = result['fact']
    result['fact'] = calculate(data, [], True)
    result['delta'] = result['prediction']['score'] - result['fact']['score']
    result['decision_delta'] = result['prediction']['score'] - result['without_decisions']['score']
    result['changes'] = [{'id': after['id'], 'name': after['name'], 'before': before['score'], 'after': after['score'], 'delta': after['score'] - before['score']} for before, after in zip(result['fact']['districts'], result['prediction']['districts'])]
    result['explanation']['strengths'][0] = f'Вклад решений относительно условий сценария: {result["decision_delta"]:+.2f} балла.'
    if event_id != 'none':
        result['explanation']['risks'].insert(0, event['description'] + ' Параметры события — учебное допущение, не часть исходного датасета.')
        result['explanation']['strengths'].append(f'Итог относительно исходного факта: {result["delta"]:+.2f} балла. Эффект события и решений показан совместно.')
    return result

def transition(data, plan, event_id):
    adjusted, event = event_dataset(data, event_id)
    kept = deepcopy(plan)
    removed = []
    # Remove expensive measures first only if the existing plan is no longer valid.
    while kept and validate_plan(adjusted, kept, True):
        costs = {m['id']: m['cost_tenge'] for m in adjusted['measures']}
        item = max(kept, key=lambda p: costs.get(p['measure_id'], 0))
        kept.remove(item); removed.append(item)
    return {'plan': kept, 'removed': removed, 'event': event}

def recommendations(data, plan, event_id='none', limit=3):
    """Evaluate every valid single replacement. A local recommendation, not a global optimum."""
    current = scenario_forecast(data, plan, False, event_id)
    if not current['valid']:
        raise ValueError(' '.join(current['errors']))
    adjusted, _ = event_dataset(data, event_id)
    candidates = []
    for index in range(len(plan)):
        other = plan[:index] + plan[index+1:]
        selected = {p['measure_id'] for p in other}
        for measure in data['measures']:
            if measure['id'] in selected:
                continue
            for district in ([None] if measure['scope'] == 'city' else [d['id'] for d in data['districts']]):
                item = {'measure_id': measure['id'], **({'district_id': district} if district else {})}
                candidate = other + [item]
                result = calculate(adjusted, candidate)
                if result['valid'] and result['score'] > current['prediction']['score'] + 1e-8:
                    candidates.append({'remove': plan[index], 'add': item, 'plan': candidate, 'score': result['score'], 'gain': result['score'] - current['prediction']['score'], 'cost_tenge': result['cost_tenge']})
    candidates.sort(key=lambda c: (-c['score'], c['cost_tenge']))
    return {'current_score': current['prediction']['score'], 'suggestions': candidates[:limit], 'method': 'single_replacement_search', 'message': 'Проверены допустимые замены одного решения. Это локальные улучшения; для полного поиска используйте «Найти лучший план».'}
