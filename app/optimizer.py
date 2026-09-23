"""Exact enumeration with an explicit time bound and honest completion flag."""
from time import monotonic
from .model import calculate

def optimize(data, max_seconds=25, max_evaluations=1000000):
    started = monotonic()
    measures, districts, metrics = data['measures'], data['districts'], data['metrics']
    keys = {m['id']: k for k, m in enumerate(metrics)}
    vals = [d['indicators'][m['id']] for d in districts for m in metrics]
    weights = [m['weight'] for m in metrics]
    populations = [d['population_share'] for d in districts]
    eff = {m['id']: [(keys[k], v * (8 - m['lag_quarters']) / 8) for k, v in m['effects'].items()] for m in measures}
    chosen, placements, counts = [], {}, {}
    best, best_score, best_cost, evaluated, nodes, exhausted = None, float('-inf'), float('inf'), 0, 0, True
    def score():
        bonuses = []
        for s in data['synergies']:
            if s['first'] in placements and s['second'] in placements:
                index = placements[s['first']] * 10 + keys[s['metric']]
                vals[index] += s['bonus']; bonuses.append((index, s['bonus']))
        average, minimum, critical = 0, 100, 0
        for d in range(5):
            total = 0
            for k in range(10):
                v = min(100, max(0, vals[d * 10 + k])); total += v * weights[k]
                critical += v < 40
            average += total * populations[d]; minimum = min(minimum, total)
        for index, bonus in bonuses:
            vals[index] -= bonus
        return .7 * average + .3 * minimum - critical
    def visit(start, cost):
        nonlocal best, best_score, best_cost, evaluated, nodes, exhausted
        nodes += 1
        if not exhausted:
            return
        if (nodes % 2048 == 0 and monotonic() - started > max_seconds) or evaluated >= max_evaluations:
            exhausted = False; return
        if len(chosen) == 5:
            evaluated += 1; value = score()
            if value > best_score + 1e-9 or (abs(value - best_score) < 1e-9 and cost < best_cost):
                best_score, best_cost = value, cost
                best = [{'measure_id': m, **({'district_id': districts[d]['id']} if d is not None else {})} for m, d in chosen]
            return
        needed = 5 - len(chosen)
        for i in range(start, len(measures) - needed + 1):
            if not exhausted:
                break
            m = measures[i]; cat = m['category']
            if cost + m['cost_tenge'] > data['budget_tenge'] or counts.get(cat, 0) >= 2:
                continue
            for d in ([None] if m['scope'] == 'city' else range(5)):
                conflict = False
                for c in data['conflicts']:
                    other = c['second'] if m['id'] == c['first'] else c['first'] if m['id'] == c['second'] else None
                    if other in placements and (c['scope'] == 'global' or placements[other] == d):
                        conflict = True; break
                if conflict:
                    continue
                targets = range(5) if d is None else [d]
                chosen.append((m['id'], d)); placements[m['id']] = d; counts[cat] = counts.get(cat, 0) + 1
                before = []
                for target in targets:
                    for k, v in eff[m['id']]:
                        index = target * 10 + k; before.append((index, vals[index])); vals[index] += v
                visit(i + 1, cost + m['cost_tenge'])
                for index, original in before:
                    vals[index] = original
                chosen.pop(); placements.pop(m['id']); counts[cat] -= 1
    visit(0, 0)
    return {'plan': best, 'result': calculate(data, best) if best else None, 'evaluated': evaluated,
            'optimality_proven': exhausted, 'elapsed_seconds': monotonic() - started}
