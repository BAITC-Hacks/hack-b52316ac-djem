"""Authoritative deterministic model. LLMs never supply numerical results."""
import math
import re
from collections import Counter
from copy import deepcopy

def numeric(value, lo, hi):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and lo <= value <= hi

def validate_dataset(data):
    if not isinstance(data, dict):
        raise ValueError('Датасет должен быть объектом.')
    for field, count in [('categories', 5), ('metrics', 10), ('districts', 5)]:
        if not isinstance(data.get(field), list) or len(data[field]) != count:
            raise ValueError(f'{field}: требуется {count} записей.')
    if not isinstance(data.get('measures'), list) or not 5 <= len(data['measures']) <= 30:
        raise ValueError('Допустимо от 5 до 30 мероприятий.')
    for field in ['categories', 'metrics', 'districts', 'measures']:
        ids = []
        for item in data[field]:
            if not isinstance(item, dict) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,39}', str(item.get('id', ''))):
                raise ValueError(f'{field}: неверный идентификатор.')
            if not isinstance(item.get('name'), str) or not 1 <= len(item['name']) <= 150:
                raise ValueError(f'{field}: требуется название до 150 символов.')
            ids.append(item['id'])
        if len(ids) != len(set(ids)):
            raise ValueError(f'{field}: повтор идентификатора.')
    categories = {c['id'] for c in data['categories']}
    metrics = {m['id'] for m in data['metrics']}
    for m in data['metrics']:
        if m.get('category') not in categories or not numeric(m.get('weight'), 0, 1):
            raise ValueError('Неверные параметры показателя.')
    if abs(sum(m['weight'] for m in data['metrics']) - 1) > 1e-8:
        raise ValueError('Сумма весов показателей должна быть равна 1.')
    if {m['category'] for m in data['metrics']} != categories:
        raise ValueError('Каждое направление должно иметь показатели.')
    for d in data['districts']:
        if not numeric(d.get('population_share'), 0, 1):
            raise ValueError('Неверная доля населения.')
        if not isinstance(d.get('indicators'), dict) or set(d['indicators']) != metrics or not all(numeric(v, 0, 100) for v in d['indicators'].values()):
            raise ValueError('Каждому району нужны все показатели от 0 до 100.')
        if not numeric(d.get('latitude'), -90, 90) or not numeric(d.get('longitude'), -180, 180):
            raise ValueError('Неверные координаты района.')
    if abs(sum(d['population_share'] for d in data['districts']) - 1) > 1e-8:
        raise ValueError('Сумма долей населения должна быть равна 1.')
    for field in ['budget_tenge', 'tenge_per_unit']:
        if type(data.get(field)) is not int or not 1 <= data[field] <= 10**15:
            raise ValueError(f'{field}: нужно положительное целое число тенге.')
    if data.get('currency') != 'KZT' or data.get('horizon_quarters') != 8 or data.get('required_decisions') != 5 or data.get('max_per_category') != 2:
        raise ValueError('Правила кейса: KZT, 8 кварталов, 5 решений, максимум 2 меры направления.')
    for m in data['measures']:
        if m.get('category') not in categories or m.get('scope') not in ['city', 'district']:
            raise ValueError('Неверная категория или область мероприятия.')
        if type(m.get('cost_tenge')) is not int or not 0 <= m['cost_tenge'] <= 10**15:
            raise ValueError('Стоимость задаётся целым неотрицательным числом тенге.')
        if type(m.get('lag_quarters')) is not int or not 0 <= m['lag_quarters'] < 8:
            raise ValueError('Лаг должен быть от 0 до 7 кварталов.')
        if not isinstance(m.get('effects'), dict) or not m['effects'] or not set(m['effects']) <= metrics or not all(numeric(v, -100, 100) for v in m['effects'].values()):
            raise ValueError('Неверные эффекты мероприятия.')
        if not isinstance(m.get('description', ''), str) or len(m.get('description', '')) > 3000:
            raise ValueError('Описание слишком длинное.')
    measures = {m['id']: m for m in data['measures']}
    for field in ['synergies', 'conflicts']:
        if not isinstance(data.get(field), list) or len(data[field]) > 100:
            raise ValueError(f'Неверный список {field}.')
        for item in data[field]:
            if not isinstance(item, dict) or item.get('first') not in measures or item.get('second') not in measures or item['first'] == item['second']:
                raise ValueError(f'{field}: неизвестная или повторная мера.')
            if field == 'synergies' and (measures[item['first']]['scope'] != 'district' or item.get('metric') not in metrics or not numeric(item.get('bonus'), -100, 100)):
                raise ValueError('Неверная синергия.')
            if field == 'conflicts' and item.get('scope') not in ['global', 'same_district']:
                raise ValueError('Неверная область конфликта.')
    return data

def validate_plan(data, plan, partial=False):
    if not isinstance(plan, list):
        return ['План должен быть списком.']
    errors = []
    if len(plan) > 5 or (not partial and len(plan) != 5):
        errors.append('Нужно ровно 5 решений.')
    measures = {m['id']: m for m in data['measures']}
    districts = {d['id'] for d in data['districts']}
    seen, categories, cost = {}, Counter(), 0
    for item in plan:
        if not isinstance(item, dict) or not isinstance(item.get('measure_id'), str) or item['measure_id'] not in measures:
            errors.append('Неизвестное мероприятие.'); continue
        mid = item['measure_id']; measure = measures[mid]
        if mid in seen:
            errors.append(f'{mid}: повтор запрещён.')
        seen[mid] = item.get('district_id'); cost += measure['cost_tenge']; categories[measure['category']] += 1
        if measure['scope'] == 'city' and item.get('district_id') is not None:
            errors.append(f'{mid}: общегородская мера не требует района.')
        if measure['scope'] == 'district' and (not isinstance(item.get('district_id'), str) or item['district_id'] not in districts):
            errors.append(f'{mid}: выберите район.')
    if cost > data['budget_tenge']:
        errors.append(f'Бюджет превышен на {cost - data["budget_tenge"]:,} ₸.'.replace(',', ' '))
    if any(n > 2 for n in categories.values()):
        errors.append('Максимум 2 меры одного направления.')
    for conflict in data['conflicts']:
        a, b = conflict['first'], conflict['second']
        if a in seen and b in seen and (conflict['scope'] == 'global' or seen[a] == seen[b]):
            errors.append(f'{a} и {b} несовместимы' + (' в одном районе.' if conflict['scope'] == 'same_district' else ' в любом районе.'))
    return list(dict.fromkeys(errors))

def calculate(data, plan, partial=False):
    errors = validate_plan(data, plan, partial)
    if errors:
        return {'valid': False, 'errors': errors}
    measures = {m['id']: m for m in data['measures']}
    values = {d['id']: deepcopy(d['indicators']) for d in data['districts']}
    selected = {item['measure_id']: item.get('district_id') for item in plan}
    cost = 0
    for item in plan:
        m = measures[item['measure_id']]; cost += m['cost_tenge']
        targets = values.keys() if m['scope'] == 'city' else [item['district_id']]
        for target in targets:
            for metric, effect in m['effects'].items():
                values[target][metric] += effect * (8 - m['lag_quarters']) / 8
    synergies = []
    for s in data['synergies']:
        if s['first'] in selected and s['second'] in selected:
            target = selected[s['first']]
            values[target][s['metric']] += s['bonus']
            synergies.append({**s, 'district_id': target})
    critical, districts = [], []
    for d in data['districts']:
        vals = {key: min(100, max(0, value)) for key, value in values[d['id']].items()}
        score = sum(vals[m['id']] * m['weight'] for m in data['metrics'])
        districts.append({'id': d['id'], 'name': d['name'], 'score': score, 'indicators': vals})
        critical.extend({'district_id': d['id'], 'metric': key, 'value': value} for key, value in vals.items() if value < 40)
    average = sum(d['population_share'] * r['score'] for d, r in zip(data['districts'], districts))
    minimum = min(d['score'] for d in districts)
    category_scores = {}
    for category in data['categories']:
        metrics = [m for m in data['metrics'] if m['category'] == category['id']]
        weight = sum(m['weight'] for m in metrics)
        category_scores[category['id']] = sum(d['population_share'] * sum(r['indicators'][m['id']] * m['weight'] for m in metrics) for d, r in zip(data['districts'], districts)) / weight if weight else 0
    return {'valid': True, 'complete': len(plan) == 5, 'score': .7 * average + .3 * minimum - len(critical),
            'average': average, 'minimum': minimum, 'critical': critical, 'districts': districts,
            'category_scores': category_scores, 'cost_tenge': cost, 'remaining_tenge': data['budget_tenge'] - cost,
            'budget_tenge': data['budget_tenge'], 'synergies': synergies}

def score_components(before, after):
    return {'average': .7 * (after['average'] - before['average']),
            'minimum': .3 * (after['minimum'] - before['minimum']),
            'critical': len(before['critical']) - len(after['critical'])}


def impact_chains(data, plan, prediction):
    """Counterfactual effects: keep every other decision and the scenario fixed."""
    measures = {m['id']: m for m in data['measures']}
    chains = []
    for i, item in enumerate(plan):
        measure = measures[item['measure_id']]
        before = calculate(data, plan[:i] + plan[i + 1:], True)
        factor = (8 - measure['lag_quarters']) / 8
        synergies = [s for s in prediction['synergies'] if measure['id'] in (s['first'], s['second'])]
        districts = []
        for original, old, new in zip(data['districts'], before['districts'], prediction['districts']):
            metrics = []
            for metric in data['metrics']:
                key = metric['id']
                direct = measure['effects'].get(key, 0) * factor if measure['scope'] == 'city' or item.get('district_id') == new['id'] else 0
                bonus = sum(s['bonus'] for s in synergies if s['district_id'] == new['id'] and s['metric'] == key)
                a, b = old['indicators'][key], new['indicators'][key]
                if direct or bonus or abs(b - a) > 1e-9:
                    metrics.append({'id': key, 'name': metric['name'], 'weight': metric['weight'],
                                    'before': a, 'after': b, 'delta': b - a,
                                    'weighted_delta': (b - a) * metric['weight'],
                                    'direct_effect': direct, 'synergy_effect': bonus,
                                    'clipped': abs((b - a) - direct - bonus) > 1e-8,
                                    'critical_removed': a < 40 <= b, 'critical_added': b < 40 <= a})
            if metrics:
                districts.append({'id': new['id'], 'name': new['name'], 'before': old['score'],
                                  'after': new['score'], 'delta': new['score'] - old['score'],
                                  'population_share': original['population_share'], 'metrics': metrics})
        chains.append({'measure_id': measure['id'], 'name': measure['name'],
                       'cost_tenge': measure['cost_tenge'], 'lag_quarters': measure['lag_quarters'],
                       'realization_factor': factor, 'score_before': before['score'],
                       'score_after': prediction['score'], 'delta': prediction['score'] - before['score'],
                       'components': score_components(before, prediction), 'districts': districts,
                       'synergies': synergies})
    return chains


def forecast(data, plan, partial=False):
    prediction = calculate(data, plan, partial)
    if not prediction['valid']:
        return prediction
    fact = calculate(data, [], True)
    changes = [{'id': d['id'], 'name': d['name'], 'before': b['score'], 'after': d['score'], 'delta': d['score'] - b['score']} for b, d in zip(fact['districts'], prediction['districts'])]
    worst = min(prediction['districts'], key=lambda x: x['score'])
    strengths = [f'Score изменился на {prediction["score"] - fact["score"]:.2f} балла.', f'Критических показателей: {len(fact["critical"])} → {len(prediction["critical"])}.']
    if prediction['synergies']:
        strengths.append(f'Учтены синергии: {len(prediction["synergies"])}.')
    risks = [f'Слабейший район — {worst["name"]}: {worst["score"]:.2f}. Его результат определяет 30% Score.', 'Это условный прогноз по эффектам кейса, а не статистически подтверждённый прогноз реального города.']
    for item in plan:
        m = next(m for m in data['measures'] if m['id'] == item['measure_id'])
        if any(v < 0 for v in m['effects'].values()):
            risks.append(f'У меры «{m["name"]}» есть отрицательные побочные эффекты: проверьте таблицу показателей.')
    chains = impact_chains(data, plan, prediction)
    contributions = [{'measure_id': c['measure_id'], 'delta': c['delta']} for c in chains]
    return {'valid': True, 'fact': fact, 'prediction': prediction, 'changes': changes, 'delta': prediction['score'] - fact['score'],
            'explanation': {'strengths': strengths, 'risks': risks, 'contributions': contributions, 'impact_chains': chains, 'score_components': score_components(fact, prediction), 'attribution_method': 'leave_one_out'},
            'provenance': 'deterministic_case_model', 'horizon_quarters': 8}
