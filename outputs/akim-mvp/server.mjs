import http from 'node:http';
import path from 'node:path';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const PUBLIC = path.join(ROOT, 'public');
const PORT = Number(process.env.PORT || 4173);
const HOST = process.env.HOST || '127.0.0.1';
const TOTAL_KZT = 100_000_000_000;
const SCENARIO = { id: 'new-districts', name: 'Рост новых районов', targets: [25, 15, 25, 15, 20] };
const CATEGORIES = [
  { id: 'transport', name: 'Транспорт', color: '#39c7b6' },
  { id: 'green', name: 'Озеленение', color: '#c6e980' },
  { id: 'social', name: 'Социальная сфера', color: '#ffac78' },
  { id: 'safety', name: 'Безопасность', color: '#7fc5d3' },
  { id: 'services', name: 'Городские сервисы', color: '#aaaebf' },
];

// Load a local .env when present. Never serve it as a static asset.
try {
  const raw = await readFile(path.join(ROOT, '.env'), 'utf8');
  for (const line of raw.split(/\r?\n/)) {
    const match = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
    if (match && !process.env[match[1]]) process.env[match[1]] = match[2].replace(/^['"]|['"]$/g, '');
  }
} catch {}

const json = (res, status, value) => {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
  res.end(JSON.stringify(value));
};
function scoreFor(allocation) {
  const difference = allocation.reduce((total, share, index) => total + Math.abs(share - SCENARIO.targets[index]), 0);
  return Math.max(0, Math.min(100, Math.round((100 - difference / 2) * 10) / 10));
}
function analyze(allocation) {
  const deviations = allocation.map((share, i) => ({
    category: CATEGORIES[i].name,
    actual: share,
    target: SCENARIO.targets[i],
    delta: share - SCENARIO.targets[i],
  }));
  const aligned = deviations.filter(x => Math.abs(x.delta) <= 5).map(x => x.category);
  const mostUnder = [...deviations].filter(x => x.delta < 0).sort((a, b) => a.delta - b.delta).slice(0, 2);
  const mostOver = [...deviations].filter(x => x.delta > 0).sort((a, b) => b.delta - a.delta).slice(0, 2);
  const score = scoreFor(allocation);
  const good = aligned.length
    ? `Ближе всего к игровому ориентиру вы подошли в направлениях: ${aligned.join(', ')}.`
    : 'Средства заметно сместились относительно игрового ориентира во всех направлениях.';
  const gaps = [];
  if (mostUnder.length) gaps.push(`ниже ориентира: ${mostUnder.map(x => `${x.category} (${x.actual}% при цели ${x.target}%)`).join(', ')}`);
  if (mostOver.length) gaps.push(`выше ориентира: ${mostOver.map(x => `${x.category} (${x.actual}% при цели ${x.target}%)`).join(', ')}`);
  const improvement = gaps.length ? `Наибольшие отклонения — ${gaps.join('; ')}.` : 'Распределение совпало с игровым ориентиром.';
  return { score, deviations, good, improvement };
}
function fallbackSummary(result) {
  return `Вы набрали ${result.score} из 100 за соответствие сценарию «${SCENARIO.name}». ${result.good} ${result.improvement} Балл отражает только близость к учебному ориентиру, а не качество реальных решений для города.`;
}
function extractText(response) {
  return (response.output || []).flatMap(item => item.content || [])
    .filter(part => part.type === 'output_text' && typeof part.text === 'string')
    .map(part => part.text).join('\n').trim();
}
async function makeSummary(result, allocation) {
  const key = process.env.OPENAI_API_KEY;
  if (!key) return { text: fallbackSummary(result), source: 'local' };
  try {
    const response = await fetch('https://api.openai.com/v1/responses', {
      method: 'POST',
      headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
      signal: AbortSignal.timeout(15000),
      body: JSON.stringify({
        model: process.env.OPENAI_MODEL || 'gpt-6-astra',
        reasoning: { effort: 'low' },
        instructions: 'Ты кратко подводишь итог учебной игры по городскому бюджету. На русском языке, 2–3 предложения. Число score уже рассчитано сервером и менять его нельзя. Скажи, что получилось лучше, назови главное отклонение от игрового ориентира и предложи один вопрос для следующего раунда. Не называй учебный ориентир реальным бюджетным советом и не делай прогнозов об Астане. Используй только переданные данные.',
        input: JSON.stringify({ scenario: SCENARIO.name, score: result.score, allocation, targetShares: SCENARIO.targets, deviations: result.deviations }),
        max_output_tokens: 180,
      }),
    });
    if (!response.ok) throw new Error(`AI provider returned ${response.status}`);
    const text = extractText(await response.json());
    if (!text) throw new Error('AI provider returned empty text');
    return { text, source: 'ai' };
  } catch (error) {
    console.error('AI summary unavailable; using local analysis:', error.message);
    return { text: fallbackSummary(result), source: 'local' };
  }
}
async function readJson(req) {
  let data = '';
  for await (const chunk of req) {
    data += chunk;
    if (data.length > 16_384) throw new Error('Request too large');
  }
  return JSON.parse(data || '{}');
}
const ASSETS = {
  '/': ['index.html', 'text/html; charset=utf-8'],
  '/styles.css': ['styles.css', 'text/css; charset=utf-8'],
  '/app.js': ['app.js', 'text/javascript; charset=utf-8'],
};
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
  if (req.method === 'GET' && url.pathname === '/api/config') {
    return json(res, 200, { totalKzt: TOTAL_KZT, scenario: SCENARIO, categories: CATEGORIES, defaultAllocation: [20, 20, 20, 20, 20] });
  }
  if (req.method === 'POST' && url.pathname === '/api/session-summary') {
    try {
      const body = await readJson(req);
      const allocation = body.allocation;
      if (!Array.isArray(allocation) || allocation.length !== CATEGORIES.length || allocation.some(v => !Number.isInteger(v) || v < 0 || v > 100) || allocation.reduce((a, b) => a + b, 0) !== 100) {
        return json(res, 400, { error: 'Распределите ровно 100% фиксированного бюджета.' });
      }
      const result = analyze(allocation);
      const summary = await makeSummary(result, allocation);
      return json(res, 200, { ...result, ...summary, totalKzt: TOTAL_KZT, scenario: SCENARIO, categories: CATEGORIES });
    } catch (error) {
      return json(res, 400, { error: error.message === 'Request too large' ? error.message : 'Не удалось разобрать данные сессии.' });
    }
  }
  const asset = req.method === 'GET' ? ASSETS[url.pathname] : null;
  if (asset) {
    try {
      const content = await readFile(path.join(PUBLIC, asset[0]));
      res.writeHead(200, {
        'Content-Type': asset[1], 'Cache-Control': 'no-cache',
        'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'",
        'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
      });
      return res.end(content);
    } catch { return json(res, 404, { error: 'Файл не найден.' }); }
  }
  return json(res, 404, { error: 'Страница не найдена.' });
});
server.listen(PORT, HOST, () => console.log(`Аким на 5 часов доступен на порту ${PORT}`));




