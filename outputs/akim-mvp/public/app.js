const $ = (selector) => document.querySelector(selector);
let config;
let allocation = [];
let elements = [];
const money = (billions) => `${new Intl.NumberFormat('ru-RU').format(billions)} млрд ₸`;
const pct = (value) => `${value}%`;
const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));

function redistribute(index, nextValue) {
  const priorOthers = allocation.reduce((sum, value, i) => i === index ? sum : sum + value, 0);
  const remaining = 100 - nextValue;
  const others = allocation.map((value, i) => ({ i, raw: i === index ? 0 : (priorOthers ? value / priorOthers * remaining : remaining / (allocation.length - 1)) })).filter((item) => item.i !== index);
  for (const item of others) allocation[item.i] = Math.floor(item.raw);
  allocation[index] = nextValue;
  let spare = 100 - allocation.reduce((sum, value) => sum + value, 0);
  others.sort((a, b) => (b.raw - Math.floor(b.raw)) - (a.raw - Math.floor(a.raw)) || a.i - b.i);
  for (let i = 0; i < spare; i += 1) allocation[others[i % others.length].i] += 1;
}
function renderAllocation() {
  let running = 0;
  for (let i = 0; i < config.categories.length; i += 1) {
    const category = config.categories[i];
    const share = allocation[i];
    const row = elements[i];
    row.input.value = share;
    row.input.setAttribute('aria-valuetext', `${share} процентов, ${money(share)}`);
    row.amount.textContent = money(share);
    row.share.textContent = pct(share);
    row.segment.style.width = `${share}%`;
    row.segment.setAttribute('aria-label', `${category.name}: ${share}%`);
    running += share;
  }
  $('#sum-kzt').textContent = money(running);
  $('#stacked-bar').setAttribute('aria-label', `Все пять направлений: ${running}% бюджета`);
}
function setPie() {
  let start = 0;
  const pieces = config.categories.map((category, i) => {
    const end = start + allocation[i];
    const piece = `${category.color} ${start}% ${end}%`;
    start = end;
    return piece;
  });
  $('#pie-chart').style.background = `conic-gradient(${pieces.join(', ')})`;
  $('#pie-legend').innerHTML = config.categories.map((category, i) => `
    <div class="legend-row"><i style="background:${category.color}"></i><span>${escapeHtml(category.name)}</span><b>${allocation[i]}% · ${money(allocation[i])}</b></div>`).join('');
}
function renderResult(data) {
  $('#final-score').textContent = Number(data.score).toFixed(1);
  $('#score-meter').style.width = `${data.score}%`;
  $('#benchmark').textContent = `${Number(data.score).toFixed(1)}%`;
  $('#ai-summary').textContent = data.text;
  $('#good-feedback').textContent = data.good;
  $('#improve-feedback').textContent = data.improvement;
  $('#target-footnote').textContent = `Ориентир сценария: ${config.categories.map((category, i) => `${category.name} ${config.scenario.targets[i]}%`).join(' · ')}. Учебная игровая модель.`;
  const badge = $('#ai-badge');
  if (data.source === 'ai') {
    badge.classList.remove('local');
    badge.innerHTML = '<span>✳</span> ИИ-АНАЛИЗ';
  } else {
    badge.classList.add('local');
    badge.innerHTML = '<span>✳</span> ДЕМО-АНАЛИЗ';
  }
  setPie();
  $('#result').hidden = false;
  $('#result').scrollIntoView({ behavior: 'smooth', block: 'start' });
}
async function finishSession() {
  const button = $('#finish-button');
  button.disabled = true;
  button.innerHTML = 'Готовим разбор сессии <span>…</span>';
  try {
    const response = await fetch('/api/session-summary', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ allocation }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Не удалось завершить сессию.');
    renderResult(data);
  } catch (error) {
    showToast(error.message || 'Сервер временно недоступен.');
  } finally {
    button.disabled = false;
    button.innerHTML = 'Завершить сессию <span>↗</span>';
  }
}
let toastTimer;
function showToast(message) {
  const toast = $('#toast');
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, 3000);
}
async function init() {
  try {
    const response = await fetch('/api/config');
    if (!response.ok) throw new Error('Не удалось загрузить настройки игры.');
    config = await response.json();
  } catch {
    document.querySelector('.page').innerHTML = '<section class="panel" style="padding:24px;margin-top:40px">Не удалось подключиться к игровому серверу. Обновите страницу.</section>';
    return;
  }
  allocation = [...config.defaultAllocation];
  $('#budget-total').textContent = money(config.totalKzt / 1_000_000_000);
  $('#scenario-name').textContent = config.scenario.name;
  $('#scenario-target').textContent = `Цель: ${config.scenario.targets.join(' · ')}%`;
  $('#target-list').innerHTML = config.categories.map((category, i) => `
    <div class="target-item"><i class="target-dot" style="background:${category.color}"></i><span>${escapeHtml(category.name)}</span><b>${config.scenario.targets[i]}%</b></div>`).join('');
  const rows = $('#allocation-rows');
  const stack = $('#stacked-bar');
  elements = config.categories.map((category, i) => {
    const row = document.createElement('div');
    row.className = 'budget-row';
    row.innerHTML = `
      <div class="category-name"><i class="category-dot" style="background:${category.color}"></i><span>${escapeHtml(category.name)}</span><small class="target-chip">цель ${config.scenario.targets[i]}%</small></div>
      <input class="budget-range" type="range" min="0" max="100" step="1" value="${allocation[i]}" aria-label="${escapeHtml(category.name)}">
      <output class="share-value">${pct(allocation[i])}</output>
      <output class="amount-value">${money(allocation[i])}</output>`;
    rows.appendChild(row);
    const segment = document.createElement('i');
    segment.className = 'stack-segment';
    segment.style.background = category.color;
    segment.setAttribute('aria-label', category.name);
    stack.appendChild(segment);
    const input = row.querySelector('input');
    const refs = { input, amount: row.querySelector('.amount-value'), share: row.querySelector('.share-value'), segment };
    input.addEventListener('input', () => {
      redistribute(i, Number(input.value));
      renderAllocation();
    });
    return refs;
  });
  renderAllocation();
  $('#finish-button').addEventListener('click', finishSession);
  $('#reset-button').addEventListener('click', () => {
    allocation = [...config.defaultAllocation];
    renderAllocation();
    $('#result').hidden = true;
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });
}
init();
