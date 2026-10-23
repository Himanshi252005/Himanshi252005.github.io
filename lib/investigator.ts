import domain from './domain.json';

export type Incident = {id:string; title:string; service:string; start:string; end:string; description:string};
export type Evidence = {id:string; source:string; service:string; at:string; code:string; value:unknown; text:string; reliability:number};
export type Case = {incident:Incident; records:Evidence[]; faults?:Record<string,string[]>; agent_faults?:Record<string,string[]>};
export type Hypothesis = {cause:string; score:number; confidence:number; supporting:string[]; contradicting:string[]; explanation:string; assumptions:string[]};
export type Event = {sequence:number; elapsed_ms:number; agent:string; event:string; round:number; detail?:string; [key:string]:unknown};
export type Report = {incident:Incident; mode:string; provider:string; status:string; root_cause:string|null; confidence:number; termination:string; rounds:number; hypotheses:Hypothesis[]; facts:Evidence[]; actions:{action:string;verification:string;evidence_ids:string[]}[]; trace:Event[]; tool_calls:Record<string,unknown>[]; latency_ms:number; issues:string[]; unresolved_sources:string[]; assumptions:string[]; tokens:{total_tokens:number}; created_at:string};
const sources = ['logs','metrics','deployments','dependencies','history'];
const wait = (ms:number) => new Promise(resolve=>setTimeout(resolve,ms));

export function validateCase(input:unknown): Case {
  if(!input || typeof input!=='object') throw new Error('Upload an incident JSON object with incident and records fields.');
  const c=input as Case;
  if(!c.incident || !Array.isArray(c.records) || c.records.length>500) throw new Error('Include an incident and up to 500 evidence records.');
  for(const key of ['id','title','service','start','end','description'] as const) if(typeof c.incident[key]!=='string'||!c.incident[key].trim()||c.incident[key].length>3000) throw new Error(`Incident ${key} is missing or too long.`);
  const validTime=(s:unknown)=>typeof s==='string'&&/(Z|[+-]\d{2}:\d{2})$/.test(s)&&Number.isFinite(Date.parse(s));
  if(!validTime(c.incident.start)||!validTime(c.incident.end)||Date.parse(c.incident.start)>Date.parse(c.incident.end)) throw new Error('Use a valid time window with timezone-aware timestamps.');
  const ids=new Set();
  for(const r of c.records){
    if(!r||!sources.includes(r.source)||!validTime(r.at)||!['id','service','code','text'].every(k=>typeof (r as any)[k]==='string'&&(r as any)[k].length<=5000)||!r.id||!r.service||!r.code||!Number.isFinite(r.reliability)||r.reliability<0||r.reliability>1) throw new Error('Each evidence record needs valid identifiers, source, timestamp, text and reliability (0–1).');
    if(ids.has(r.id)) throw new Error('Evidence IDs must be unique.');
    ids.add(r.id);
  }
  for(const [field,allowed] of [['faults',['ok','error','timeout','partial']],['agent_faults',['ok','error']]] as const){
    const scripts=c[field];
    if(scripts && (typeof scripts!=='object'||Array.isArray(scripts))) throw new Error(`Invalid ${field} map.`);
    if(scripts) for(const values of Object.values(scripts)) if(!Array.isArray(values)||values.length>20||values.some(v=>!(allowed as readonly string[]).includes(v))) throw new Error(`Invalid ${field} script.`);
  }
  return c;
}

export function rankEvidence(evidence:Evidence[]):Hypothesis[]{
  const causes=[...new Set(domain.rules.map(r=>r.cause))].sort();
  const ranked:Hypothesis[]=[];
  for(const cause of causes){
    const votes=new Map<string,{weight:number;e:Evidence}>();
    for(const e of evidence){
      if(e.source==='history')continue;
      for(const r of domain.rules){
        if(r.cause!==cause||r.code!==e.code)continue;
        const match=r.operator==='eq'? e.value===r.threshold : typeof e.value==='number' && (r.operator==='ge'?e.value>=Number(r.threshold):e.value<=Number(r.threshold));
        if(!match)continue;
        const weight=r.weight*e.reliability,key=`${e.service}|${e.code}|${weight>0}`;
        if(!votes.has(key)||Math.abs(weight)>Math.abs(votes.get(key)!.weight))votes.set(key,{weight,e});
      }
    }
    const plus=[...votes.values()].filter(v=>v.weight>0),minus=[...votes.values()].filter(v=>v.weight<0);
    if(!plus.length)continue;
    const score=Number([...votes.values()].reduce((s,v)=>s+v.weight,0).toFixed(4));
    const families=new Set(plus.map(v=>v.e.source));
    let confidence=Math.max(0,Math.min(.97,.30+.065*score));
    if(families.size<2)confidence=Math.min(confidence,.59);
    ranked.push({cause,score,confidence:Number(confidence.toFixed(3)),supporting:plus.map(v=>v.e.id).sort(),contradicting:minus.map(v=>v.e.id).sort(),explanation:`${plus.length} supporting signals across ${families.size} source families; ${minus.length} counter-signals. Net weighted support ${score}.`,assumptions:['Temporal correlation may not establish causation; validate the proposed mitigation.']});
  }
  return ranked.sort((a,b)=>b.score-a.score||a.cause.localeCompare(b.cause));
}

type Query={tool:string;service:string;limit:number};
type Result={query:Query;evidence:Evidence[];complete:boolean;error?:string};

export async function investigate(fixture:Case,mode='multi',onEvent:(e:Event)=>void=()=>{},options:{latency?:number;maxRounds?:number;maxCalls?:number}={}):Promise<Report>{
  const c=validateCase(structuredClone(fixture)),start=performance.now(),latency=options.latency??55,maxRounds=options.maxRounds??3,maxCalls=options.maxCalls??40;
  if(!['multi','single','sequential-adaptive'].includes(mode))throw new Error('Unknown investigation mode.');
  const facts=new Map<string,Evidence>(),queries=new Map<string,Result>(),trace:Event[]=[],calls:Record<string,unknown>[]=[],issues:string[]=[],counts=new Map<string,number>();
  let round=0,termination='not_started',hypotheses:Hypothesis[]=[];
  function emit(agent:string,event:string,detail:string,extra:Record<string,unknown>={}){const e={sequence:trace.length+1,elapsed_ms:Math.round((performance.now()-start)*1000)/1000,agent,event,round,detail,...extra};trace.push(e);onEvent(e);}
  const key=(q:Query)=>`${q.tool}:${q.service}`;
  async function query(q:Query):Promise<Result>{
    for(let attempt=1;attempt<=2;attempt++){
      if(calls.length>=maxCalls)return {query:q,evidence:[],complete:false,error:'tool_budget_exhausted'};
      const n=counts.get(key(q))??0;counts.set(key(q),n+1);
      const script=c.faults?.[key(q)]??c.faults?.[q.tool]??[],outcome=script.length?script[Math.min(n,script.length-1)]:'ok';
      const call:Record<string,unknown>={tool:q.tool,service:q.service,limit:q.limit,attempt,status:'running'};calls.push(call);
      await wait(latency);
      if(outcome==='error'||outcome==='timeout'){
        call.status='error';call.error=outcome==='timeout'?'timeout':'Simulated source unavailable';
        emit(q.tool==='logs'||q.tool==='metrics'?'observability':'dependency','tool_retry',`${q.tool} source ${outcome==='timeout'?'timed out':'unavailable'}${attempt===1?' · retrying':' · attempts exhausted'}`);
        if(attempt===2)return {query:q,evidence:[],complete:false,error:String(call.error)};
        continue;
      }
      const all=c.records.filter(e=>e.source===q.tool&&e.service===q.service&&Date.parse(e.at)>=Date.parse(c.incident.start)&&Date.parse(e.at)<=Date.parse(c.incident.end)).sort((a,b)=>Date.parse(a.at)-Date.parse(b.at)||a.id.localeCompare(b.id));
      const selected=all.slice(0,outcome==='partial'?Math.min(q.limit,1):q.limit),complete=outcome!=='partial'&&selected.length===all.length;
      call.status=complete?'ok':'partial';call.count=selected.length;
      return {query:q,evidence:selected,complete};
    }
    throw new Error('Tool attempts exhausted.');
  }
  async function agent(name:string,work:()=>Promise<void>):Promise<boolean>{
    for(let attempt=1;attempt<=2;attempt++){
      emit(name,'started',attempt===1?'Working with the available evidence': 'Recovering from the previous attempt');
      try{
        if(c.agent_faults?.[name]?.shift()==='error')throw new Error('Simulated agent failure');
        await work();emit(name,'completed','Investigation step complete');return true;
      }catch(e){emit(name,'failed',e instanceof Error?e.message:'Agent failed');if(attempt===2)issues.push(`${name} failed after two attempts`);}
    }
    return false;
  }
  async function collect(name:string,list:Query[],parallel:boolean){
    const results:Result[]=[];
    if(parallel)results.push(...await Promise.all(list.map(query)));else for(const q of list)results.push(await query(q));
    for(const r of results){queries.set(key(r.query),r);r.evidence.forEach(e=>facts.set(e.id,e));emit(name,'tool_result',`${r.query.tool} · ${r.query.service} · ${r.evidence.length} observations${!r.complete?' · incomplete':''}`,{evidence_ids:r.evidence.map(e=>e.id),complete:r.complete});}
  }
  function sufficient(){if(!hypotheses.length)return false;const h=hypotheses[0];return h.score>=5&&h.confidence>=.65&&new Set(h.supporting.map(id=>facts.get(id)?.source)).size>=2&&(hypotheses.length===1||h.score-hypotheses[1].score>=2);}
  emit('orchestrator','planned','Investigation plan ready · five sources, six specialists');
  if(mode==='single'){
    round=1;const ok=await agent('single',async()=>{await collect('single',sources.map(tool=>({tool,service:c.incident.service,limit:3})),false);hypotheses=rankEvidence([...facts.values()]);});termination=ok?'single_pass_complete':'reasoning_failed';
  }else{
    let requests:Query[]|null=null,previous='';
    for(round=1;round<=maxRounds;round++){
      const wanted=requests??sources.map(tool=>({tool,service:c.incident.service,limit:3}));
      const obs=wanted.filter(q=>['logs','metrics'].includes(q.tool)),dep=wanted.filter(q=>!['logs','metrics'].includes(q.tool));
      if(mode==='multi')await Promise.all([agent('observability',()=>collect('observability',obs,true)),agent('dependency',()=>collect('dependency',dep,true))]);
      else {await agent('observability',()=>collect('observability',obs,false));await agent('dependency',()=>collect('dependency',dep,false));}
      if(!await agent('reasoning',async()=>{hypotheses=rankEvidence([...facts.values()]);emit('reasoning','ranked_hypotheses',`${hypotheses.length} competing explanations considered`);})) {termination='reasoning_failed';break;}
      const follow=new Map<string,Query>();let accepted=false;
      if(!await agent('critic',async()=>{
        for(const r of queries.values())if(!r.complete)follow.set(key(r.query),{...r.query,limit:100});
        for(const tool of sources){const q={tool,service:c.incident.service,limit:100};if(!queries.has(key(q)))follow.set(key(q),q);}
        const known=new Set([c.incident.service]);let changed=true;
        while(changed){changed=false;for(const e of facts.values())if(e.source==='dependencies'&&e.code==='depends_on'&&known.has(e.service)&&typeof e.value==='string'&&!known.has(e.value)){known.add(e.value);changed=true;}}
        for(const service of known)if(service!==c.incident.service)for(const tool of ['logs','metrics','dependencies']){const q={tool,service,limit:100};if(!queries.has(key(q)))follow.set(key(q),q);}
        accepted=sufficient()&&follow.size===0;
        emit('critic','critique',accepted?'Evidence meets the support and coverage thresholds':follow.size?`${follow.size} targeted follow-up queries requested`:'Evidence remains insufficient or conflicting',{accepted,requests:[...follow.keys()]});
      })){termination='critic_failed';break;}
      if(accepted){termination='evidence_sufficient';break;}
      if(!follow.size){termination='insufficient_evidence';break;}
      if(calls.length>=maxCalls){termination='tool_budget_exhausted';break;}
      const fingerprint=JSON.stringify([[...facts.keys()].sort(),[...follow.keys()].sort(),[...queries].map(([k,r])=>[k,r.complete,r.error??null]).sort()]);
      if(fingerprint===previous){termination='no_progress';break;}
      previous=fingerprint;requests=[...follow.values()];emit('orchestrator','feedback_dispatched',`Round ${round+1} · collecting missing evidence`);
      if(round===maxRounds)termination='round_limit';
    }
    round=Math.min(round,maxRounds);
  }
  const probable=sufficient()&&['evidence_sufficient','single_pass_complete'].includes(termination),best=hypotheses[0];
  const action=best?(domain.actions as Record<string,string[]>)[best.cause]:null;
  emit('reporting','completed',probable?'Evidence-backed report ready':'Inconclusive report ready · further evidence needed');
  return {incident:c.incident,mode,provider:'offline_rules',status:probable?'probable':'inconclusive',root_cause:probable?best.cause:null,confidence:probable?best.confidence:0,termination,rounds:round,hypotheses,facts:[...facts.values()].sort((a,b)=>Date.parse(a.at)-Date.parse(b.at)||a.id.localeCompare(b.id)),actions:action?[{action:probable?action[0]:`Validate this tentative hypothesis first. ${action[0]}`,verification:action[1],evidence_ids:best.supporting}]:[{action:'Collect missing observations and resolve contradictions before selecting a root cause.',verification:'Obtain independent, time-aligned evidence.',evidence_ids:[]}],trace,tool_calls:calls,latency_ms:Math.round((performance.now()-start)*100)/100,issues,unresolved_sources:[...queries].filter(([,r])=>!r.complete).map(([k])=>k),assumptions:[...new Set(hypotheses.flatMap(h=>h.assumptions))],tokens:{total_tokens:0},created_at:new Date().toISOString()};
}
