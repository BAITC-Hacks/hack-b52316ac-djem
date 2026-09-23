const esc = v => String(v).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const n = v => new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 4}).format(v);
const signed = v => (v >= 0 ? '+' : '') + n(v);
const tone = v => v < -1e-9 ? 'impact-negative' : 'impact-positive';
function components(c) {
  return `<div class="impact-components"><span>Среднее по населению × 70%: <b>${signed(c.average)}</b></span><span>Слабейший район × 30%: <b>${signed(c.minimum)}</b></span><span>Изменение штрафа: <b>${signed(c.critical)}</b></span></div>`;
}
export function renderImpact(result) {
  const root = document.getElementById('impact-chains');
  const explanation = result.explanation;
  const chains = explanation.impact_chains || [];
  const eventDelta = (result.without_decisions || result.fact).score - result.fact.score;
  root.innerHTML = `<p class="muted">Score = 70% среднего балла с учётом населения + 30% балла слабейшего района − 1 за каждый показатель ниже 40.</p>
    <div class="impact-total"><span>Факт <b>${n(result.fact.score)}</b></span><span>→ Событие <b>${signed(eventDelta)}</b></span><span>→ Решения <b>${signed(result.decision_delta ?? result.delta)}</b></span><span>→ Прогноз <b>${n(result.prediction.score)}</b></span></div>
    ${explanation.score_components ? components(explanation.score_components) : ''}
    <p class="muted">Три компонента выше объясняют изменение от решений; событие учитывается отдельно. Значения округлены до 4 знаков.</p>
    ${chains.length ? '<p>Раскройте инициативу: <b>инициатива → показатели → район → Score</b>.</p><p class="muted">В каждой цепочке сравниваем полный план с тем же планом без этой меры. Остальные решения и условия сохраняются. Эффекты отдельных мер нельзя складывать: они могут совместно снимать штрафы и давать синергии.</p>' : '<p class="empty">Добавьте инициативу — здесь появится объяснение её влияния.</p>'}
    ${chains.map(c => `<details class="impact-chain"><summary><span>${esc(c.measure_id)} · ${esc(c.name)}</span><b class="${tone(c.delta)}">${signed(c.delta)} Score</b></summary>
      <div class="impact-content"><p>Стоимость: <b>${n(c.cost_tenge)} ₸</b>. Задержка: ${c.lag_quarters} кв.; за 8 кварталов реализуется ${n(c.realization_factor * 100)}% базового эффекта.</p>
      ${c.districts.map(d => `<div class="impact-district"><h3>→ ${esc(d.name)}</h3><div class="impact-table-wrap"><table><thead><tr><th>Показатель</th><th>Без меры → с мерой</th><th>Почему</th><th>В балл района</th></tr></thead><tbody>${d.metrics.map(m => `<tr><td>${esc(m.name)} <small>${esc(m.id)}</small></td><td>${n(m.before)} → ${n(m.after)}<br><b class="${tone(m.delta)}">${signed(m.delta)}</b></td><td>Эффект с учётом задержки: ${signed(m.direct_effect)}${m.synergy_effect ? `<br>Синергия: ${signed(m.synergy_effect)}` : ''}${m.clipped ? '<br>Учтено ограничение показателя 0–100' : ''}${m.critical_removed ? '<br><strong>Достигнут порог 40: снят штраф −1</strong>' : ''}${m.critical_added ? '<br><strong>Ниже 40: добавлен штраф −1</strong>' : ''}</td><td>${signed(m.delta)} × ${n(m.weight)} = <b>${signed(m.weighted_delta)}</b></td></tr>`).join('')}</tbody></table></div><p>→ Балл района: ${n(d.before)} → <b>${n(d.after)}</b> (${signed(d.delta)}). Доля населения: ${n(d.population_share * 100)}%.</p></div>`).join('')}
      ${c.synergies.length ? `<p>Совместные бонусы: ${c.synergies.map(s => `${esc(s.first)} + ${esc(s.second)} → ${esc(s.metric)} ${signed(s.bonus)}`).join('; ')}.</p>` : ''}
      <p><strong>→ Score города: ${n(c.score_before)} → ${n(c.score_after)} (${signed(c.delta)})</strong></p>${components(c.components)}
      </div></details>`).join('')}`;
}
