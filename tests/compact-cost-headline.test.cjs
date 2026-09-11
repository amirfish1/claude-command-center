const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app.js','utf8');
const start = source.indexOf('  // RAIL_COST_HEADLINE_START');
const end = source.indexOf('  // RAIL_COST_HEADLINE_END',start);
const ctx=vm.createContext({});
if(start>=0)vm.runInContext(source.slice(start,end),ctx);
test('allocated cost leads with percentage second and API price secondary',()=>{
 const out=ctx.railCostHeadline({state:'ready',allocatedCost:1.64,contributionPct:3.6},66.654);
 assert.equal(out.headline,'$1.64 (3.6%)');
 assert.equal(out.apiLabel,'$66.65 API list-price equivalent');
});
test('missing quota does not promote API cost into the allocated cost position',()=>{
 const out=ctx.railCostHeadline({state:'unavailable'},66.654);
 assert.equal(out.headline,'Unavailable');
 assert.equal(out.apiLabel,'$66.65 API list-price equivalent');
 const pending=ctx.railCostHeadline({state:'calibrating'},null);
 assert.equal(pending.headline,'Calibrating…');
 assert.equal(pending.apiLabel,'API list-price unavailable');
});
