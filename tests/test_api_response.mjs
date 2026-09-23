import assert from 'node:assert/strict';
import {parseApiResponse} from '../static/api-response.js';
assert.deepEqual(await parseApiResponse(new Response('{"ok":true}'),'health'),{ok:true});
await assert.rejects(()=>parseApiResponse(new Response('<!DOCTYPE html><h1>Timeout</h1>',{status:504}),'forecast'),/HTTP 504.*не дождался/);
await assert.rejects(()=>parseApiResponse(new Response('<html>route missing</html>',{status:404}),'library'),/Маршрут API не найден/);
await assert.rejects(()=>parseApiResponse(new Response('{"detail":"Войдите"}',{status:401}),'workspace'),/Войдите/);
await assert.rejects(()=>parseApiResponse(new Response('<!DOCTYPE html>'),'bootstrap'),/вместо данных API/);
console.log('API JSON, HTML proxy failures and auth errors: passed');
