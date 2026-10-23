import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import ts from 'typescript';
const root=process.cwd(),runtime=path.join(root,'.sites-runtime','tests');
fs.mkdirSync(runtime,{recursive:true});
const src=fs.readFileSync('lib/investigator.ts','utf8');
let js=ts.transpileModule(src,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
js=js.replace("from './domain.json'",`from ${JSON.stringify(pathToFileURL(path.join(root,'lib/domain.json')).href)} with { type: 'json' }`);
fs.writeFileSync(path.join(runtime,'engine.mjs'),js);
const {investigate,validateCase,rankEvidence}=await import(pathToFileURL(path.join(runtime,'engine.mjs')).href);
const cases=JSON.parse(fs.readFileSync('lib/incidents.json','utf8')),expected=JSON.parse(fs.readFileSync('tests/expected.json','utf8'));
let checked=0;
for(const mode of ['multi','single','sequential-adaptive'])for(const c of cases){
  const result=await investigate(c,mode,()=>{},{latency:0});
  for(const field of ['root_cause','status','rounds','termination','hypotheses'])assert.deepEqual(result[field],expected[mode][c.incident.id][field],`${mode} ${c.incident.id} ${field}`);
  assert(result.tool_calls.length<=40);assert(result.facts.every(e=>c.records.some(record=>record.id===e.id)));checked++;
}
const unknown=structuredClone(cases[0]);unknown.incident.id='NEVER-SEEN';unknown.incident.title='Different title';
assert.equal((await investigate(unknown,'multi',()=>{},{latency:0})).root_cause,'deployment_regression');
assert.throws(()=>validateCase({}),/incident/);
const duplicate=structuredClone(cases[0]);duplicate.records.push(duplicate.records[0]);assert.throws(()=>validateCase(duplicate),/unique/);
const invalid=structuredClone(cases[0]);invalid.records[0].reliability=2;assert.throws(()=>validateCase(invalid),/reliability/);
const limited=await investigate(cases[16],'multi',()=>{},{latency:0,maxRounds:1});assert.equal(limited.status,'inconclusive');
const budget=await investigate(cases[3],'multi',()=>{},{latency:0,maxCalls:3});assert.equal(budget.status,'inconclusive');assert.equal(budget.tool_calls.length,3);
const injection=await investigate(cases[14],'multi',()=>{},{latency:0});assert.equal(injection.root_cause,'disk_exhaustion');
const duplicated=[...cases[0].records,{...cases[0].records[1],id:'another-log'}];assert.equal(rankEvidence(duplicated)[0].score,rankEvidence(cases[0].records)[0].score);
console.log(`PASS: ${checked} Python parity cases and 8 validation, budget, identity and evidence checks.`);
