import assert from 'node:assert/strict';
import {MISSIONS,evaluateMission,allocation} from '../static/missions.js';
const base={score:50,minimum:40,critical:[{},{}]};
function result(score=52,minimum=42,critical=2){return {fact:{score:60,minimum:50,critical:[]},without_decisions:base,prediction:{score,minimum,critical:Array(critical).fill({})}};}
assert.equal(evaluateMission('quality',result(),'none').success,true);
assert.equal(evaluateMission('quality',result(51.99),'none').success,false);
assert.equal(evaluateMission('quality',result(52,42,3),'none').success,false);
assert.equal(evaluateMission('quality',result(),'snow').success,false);
assert.equal(evaluateMission('equity',result(50,42),'none').success,true);
assert.equal(evaluateMission('equity',result(49,44),'none').success,false);
assert.equal(evaluateMission('flood',result(51),'flood').success,true);
assert.equal(evaluateMission('flood',result(50.9),'flood').success,false);
assert.equal(evaluateMission('flood',result(60),'none').success,false);
assert.equal(evaluateMission(null,result(),'none').status,'СЦЕНАРИЙ РАССЧИТАН');
const split=allocation({categories:[{id:'a'},{id:'b'}],measures:[{id:'M1',category:'a',cost_tenge:100},{id:'M2',category:'b',cost_tenge:200}]},[{measure_id:'M2'}]);
assert.deepEqual(split.map(x=>x.amount),[0,200]);
assert.equal(Object.keys(MISSIONS).length,3);
console.log('Mission goals, event baselines and budget allocation: passed');
