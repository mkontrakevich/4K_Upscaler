import { WorkflowEntrypoint } from "cloudflare:workers";
import { getContainer } from "@cloudflare/containers";

// Independent container instance: no Internet, no OpenRouter credentials, no
// main MG4K processor recycling, and no accidental paid generation.
const INSTANCE="editorial-tech-v1";
const ROOT=id=>"tropicana-park/editorial-jobs/"+id+"/";
const ID=/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
const SHA=/^[0-9a-f]{64}$/;
const hex=buf=>Array.from(new Uint8Array(buf),v=>v.toString(16).padStart(2,"0")).join("");
const fromHex=s=>Uint8Array.from(s.match(/../g)||[],h=>parseInt(h,16)).buffer;
const endpoint=(id,action)=>"http://container/editorial-tech/jobs/"+id+"/"+action;

async function update(env,id,s){
  await env.TROPICANA_EDITORIAL_R2.put(ROOT(id)+"processing-state.json",
    JSON.stringify({...s,job_id:id,updated_at:new Date().toISOString()}),
    {httpMetadata:{contentType:"application/json"}});
}
async function processor(env){
  const c=getContainer(env.MG4K_PROCESSOR,INSTANCE);
  await c.startAndWaitForPorts({
    ports:[8080],
    startOptions:{enableInternet:false,envVars:{
      MG4K_HEADLESS:"1",MG4K_EDITORIAL_TECH_ROOT:"/tmp/mg4k-editorial-tech",
      MG4K_JOBS_ROOT:"/tmp/mg4k-jobs",MG4K_HEALTH_PORT:"8080",
      MG4K_CONTAINER_BUILD_ID:INSTANCE
    }},
    cancellationOptions:{portReadyTimeoutMS:60000}
  });
  return c;
}
async function start(env,id,job){
  const input=await env.TROPICANA_EDITORIAL_R2.get(job.source_key);
  if(!input||input.size!==job.source_bytes)throw Error("source_missing_or_size_changed");
  const bytes=await input.arrayBuffer();
  if(hex(await crypto.subtle.digest("SHA-256",bytes))!==job.source_sha256)throw Error("source_SHA_mismatch");
  const c=await processor(env);
  const res=await c.fetch(new Request(endpoint(id,"start"),{
    method:"POST",body:bytes,headers:{
      "content-type":job.mime,"x-source-sha256":job.source_sha256,
      "x-source-filename":encodeURIComponent(job.filename),"x-mg4k-source-size":String(bytes.byteLength)
    }
  }));
  const data=await res.json().catch(()=>({}));
  if(!res.ok)throw Error("technical_start_"+res.status+":"+String(data.error||"unknown"));
  return {status:String(data.status||"QUEUED")};
}
async function status(env,id){
  const c=await processor(env);
  const r=await c.fetch(new Request(endpoint(id,"status")));
  if(!r.ok)throw Error("technical_status_http_"+r.status);
  return r.json();
}
async function proof(env,id,job){
  const c=await processor(env);
  const r=await c.fetch(new Request(endpoint(id,"qa")));
  if(!r.ok)throw Error("technical_QA_missing_"+r.status);
  const qa=await r.json();
  if(qa.job_id!==id||qa.source_sha256!==job.source_sha256||
     qa.technical_status!=="TECHNICAL_QA_PASS"||qa.master_bit_depth!==16||
     qa.HDR_applied!==false||qa.virtual_exposures!==false||qa.local_HDR!==false||
     qa.qa_checks?.HDR_ABSENT!=="PASS"||!SHA.test(qa.master_sha256||"")||
     !Number.isInteger(qa.master_bytes)||qa.master_bytes<200000)
    throw Error("technical_QA_proof_HOLD");
  return qa;
}

// ContainerProxy responses expose a Content-Length header, but their ReadableStream
// is not considered length-bearing by the R2 binding. FixedLengthStream restores
// a strict content length without buffering a potentially large 16-bit TIFF.
async function persistKnownLength(env,key,response,expectedSize,options){
  if(!Number.isSafeInteger(expectedSize)||expectedSize<=0||!response.body)
    throw Error("technical_stream_expected_length_invalid");
  const bucket=env.TROPICANA_EDITORIAL_R2;
  const already=await bucket.head(key);
  if(already){
    if(already.size!==expectedSize||
       already.customMetadata?.sha256!==options.customMetadata?.sha256||
       !already.checksums?.sha256||
       hex(already.checksums.sha256)!==options.customMetadata.sha256)
      throw Error("technical_output_collision_integrity_HOLD");
    await response.body.cancel();
    return already;
  }
  const {readable,writable}=new FixedLengthStream(expectedSize);
  const copy=response.body.pipeTo(writable);
  const save=bucket.put(key,readable,options);
  const [,stored]=await Promise.all([copy,save]);
  if(!stored)throw Error("technical_output_conditional_write_HOLD");
  return stored;
}

async function persist(env,id,job,qa){
  const c=await processor(env);
  const master=await c.fetch(new Request(endpoint(id,"master")));
  if(!master.ok||master.headers.get("content-type")!=="image/tiff"||
     Number(master.headers.get("content-length"))!==qa.master_bytes)
    throw Error("master_stream_missing_or_invalid");
  const output=ROOT(id)+"technical/master16.tif";
  await persistKnownLength(env,output,master,qa.master_bytes,{
    onlyIf:new Headers({"If-None-Match":"*"}),sha256:fromHex(qa.master_sha256),
    httpMetadata:{contentType:"image/tiff"},
    customMetadata:{sha256:qa.master_sha256,source_sha256:job.source_sha256,job_id:id}
  });
  const head=await env.TROPICANA_EDITORIAL_R2.head(output);
  if(!head||head.size!==qa.master_bytes||
     head.customMetadata?.sha256!==qa.master_sha256||
     !head.checksums?.sha256||hex(head.checksums.sha256)!==qa.master_sha256)
    throw Error("R2_master_integrity_HOLD");
  const view=await c.fetch(new Request(endpoint(id,"preview")));
  if(!view.ok||view.headers.get("content-type")!=="image/jpeg"||
     Number(view.headers.get("content-length"))!==qa.preview_bytes)
    throw Error("technical_preview_invalid");
  await persistKnownLength(env,ROOT(id)+"technical/fullres-preview.jpg",view,qa.preview_bytes,{
    onlyIf:new Headers({"If-None-Match":"*"}),sha256:fromHex(qa.preview_sha256),
    httpMetadata:{contentType:"image/jpeg"},customMetadata:{sha256:qa.preview_sha256}
  });
  const vh=await env.TROPICANA_EDITORIAL_R2.head(ROOT(id)+"technical/fullres-preview.jpg");
  if(!vh||vh.size!==qa.preview_bytes)throw Error("technical_preview_R2_HOLD");
  await env.TROPICANA_EDITORIAL_R2.put(ROOT(id)+"technical/qa.json",JSON.stringify(qa),{
    onlyIf:new Headers({"If-None-Match":"*"}),httpMetadata:{contentType:"application/json"}
  });
  return {master_sha256:qa.master_sha256,master_bytes:qa.master_bytes,
          signal_bits:qa.source_signal_bits,geometry:qa.geometry};
}

export class EditorialTechWorkflow extends WorkflowEntrypoint{
  async run(event,step){
    const payload=typeof event.payload==="string"?JSON.parse(event.payload):event.payload||{};
    const id=String(payload.job_id||"");
    if(!ID.test(id))throw Error("invalid_technical_job_id");
    const env=this.env,root=ROOT(id);
    try {
      const job=await step.do("verify-intake",async()=>{
        const o=await env.TROPICANA_EDITORIAL_R2.get(root+"job.json");
        if(!o)throw Error("technical_job_missing");
        const j=await o.json();
        if(j.job_id!==id||!SHA.test(j.source_sha256)||j.source_bytes>8*1024*1024)
          throw Error("technical_job_invalid");
        return {source_key:j.source_key,source_sha256:j.source_sha256,source_bytes:j.source_bytes,
                filename:j.filename,mime:j.mime};
      });
      await update(env,id,{status:"PROCESSING",stage:"technical_dispatch",progress:0,
        message:"Сохранённый источник проверяется и передаётся техническому процессору."});
      await step.do("start-technical-processor",()=>start(env,id,job));
      let ready=false;
      for(let n=0;n<96;n++){
        await step.sleep("technical-poll-wait-"+n,"5 seconds");
        const state=await step.do("technical-poll-"+n,async()=>{
          const p=await status(env,id);
          await update(env,id,{status:p.status==="READY"?"TECHNICAL_QA_PENDING":p.status,
            stage:p.stage||"technical_processing",
            progress:typeof p.progress==="number"?p.progress:null,
            message:p.status==="READY"?"Мастер готов, проверяем целостность.":"Техническая обработка без HDR."});
          return {status:p.status,error:p.error||null};
        });
        if(state.status==="READY"){ready=true;break}
        if(state.status==="FAILED")throw Error("technical_processor_failed:"+String(state.error||"unknown"));
        if(state.status==="NOT_FOUND")await step.do("restart-technical-"+n,()=>start(env,id,job));
      }
      if(!ready)throw Error("technical_processing_timeout");
      const qa=await step.do("validate-technical-QA",()=>proof(env,id,job));
      const saved=await step.do("persist-verified-technical-master",()=>persist(env,id,job,qa));
      await update(env,id,{status:"AWAITING_HUMAN_QA",stage:"technical_qa_ready",progress:100,
        message:"Технический 16-битный TIFF готов. Проверьте полный размер и утвердите QA.",
        technical_master_sha256:saved.master_sha256});
      return {job_id:id,status:"AWAITING_HUMAN_QA",...saved};
    }catch(error){
      await update(env,id,{status:"FAILED",stage:"technical_failed",progress:null,
        error:String(error?.message||error).slice(0,1000),
        message:"Техническая обработка завершилась ошибкой. Генерация заблокирована."});
      throw error;
    }
  }
}
