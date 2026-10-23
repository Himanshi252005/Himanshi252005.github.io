import cases from './incidents.json';
import {investigate, validateCase} from './investigator';

/** Local transport: the public demo never uploads imported evidence. */
export async function browserRequest(url:string, init:RequestInit={}):Promise<Response>{
  if(init.signal?.aborted)throw new DOMException('Cancelled','AbortError');
  const path=new URL(url,'https://local.invalid');
  if(path.pathname==='/api/catalog')return Response.json({incidents:cases.map(c=>({...c.incident,records:c.records.length,sources:[...new Set(c.records.map(e=>e.source))]}))});
  if(path.pathname==='/api/evidence')return Response.json(cases.find(c=>c.incident.id===path.searchParams.get('id'))?.records??[]);
  if(path.pathname!=='/api/investigate')return Response.json({error:'Unknown request'},{status:404});
  try{
    const body=String(init.body??'');
    if(body.length>300000)throw new Error('Use an incident file under 300 KB.');
    const input=JSON.parse(body),fixture=validateCase(input.customCase??cases.find(c=>c.incident.id===input.incidentId));
    if(!['multi','single','sequential-adaptive'].includes(input.mode??'multi'))throw new Error('Choose a valid investigation mode.');
    const encoder=new TextEncoder();
    const stream=new ReadableStream<Uint8Array>({
      async start(controller){
        let open=true;
        const stop=()=>{if(open){open=false;controller.error(new DOMException('Cancelled','AbortError'))}};
        init.signal?.addEventListener('abort',stop,{once:true});
        const send=(item:unknown)=>{if(open)controller.enqueue(encoder.encode(JSON.stringify(item)+'\n'))};
        try{const report=await investigate(fixture,input.mode??'multi',event=>send({type:'event',event}));send({type:'report',report});}
        catch(e){send({type:'error',error:e instanceof Error?e.message:'Investigation failed.'});}
        finally{init.signal?.removeEventListener('abort',stop);if(open){open=false;controller.close()}}
      }
    });
    return new Response(stream,{headers:{'Content-Type':'application/x-ndjson'}});
  }catch(e){return Response.json({error:e instanceof Error?e.message:'Invalid incident.'},{status:400})}
}
