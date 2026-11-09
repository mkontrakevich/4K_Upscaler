// Per-upload Editorial Photo records. Historical DSC09818/V9/V10 artifacts are never mutated.
const ROOT = "tropicana-park/editorial-jobs/";
const idOK = id => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(String(id||""));
const json = (x,status=200) => Response.json(x,{status,headers:{"cache-control":"private, no-store","x-content-type-options":"nosniff"}});
const key = (id,name) => ROOT+id+"/"+name;
const SHA = /^[0-9a-f]{64}$/;
const LOCKS = Object.freeze({geometry:"HARD",perspective:"HARD",composition:"HARD",architecture:"HARD",materials:"HARD",identity:"HARD",source:"IMMUTABLE",hdr:"PROHIBITED",generation:"HUMAN_QA_FIRST"});
const hash = async bytes => [...new Uint8Array(await crypto.subtle.digest("SHA-256",bytes))].map(v=>v.toString(16).padStart(2,"0")).join("");

export async function createEditorialIntakeJob(env,session,{filename,mime,source_key,source_sha256,bytes}) {
  const id=crypto.randomUUID();
  const stamp=new Date().toISOString();
  const record={
    schema:1,job_id:id,owner_session_id:session.id,project:"tropicana-park",filename:String(filename||"image").slice(0,128),
    mime,source_key,source_sha256,source_bytes:bytes,created_at:stamp,
    status:"AWAITING_TECH_PROCESSING",stage:"source_stored",progress:null,
    message:"Файл сохранён. Техническая обработка ещё не запущена.",
    locks:LOCKS,auto_generation:false,technical_master_ready:false
  };
  await env.TROPICANA_EDITORIAL_R2.put(key(id,"job.json"),JSON.stringify(record),{httpMetadata:{contentType:"application/json"}});
  return record;
}

async function owned(request,env,url,authorizeSession,id) {
  const session=await authorizeSession(request,url,env);
  if(!session||!idOK(id))return null;
  const obj=await env.TROPICANA_EDITORIAL_R2.get(key(id,"job.json"));
  if(!obj)return null;
  const job=await obj.json();
  if(job.owner_session_id!==session.id||job.job_id!==id)return null;
  return {job,session};
}

// Only a trusted technical processor with R2 write access can publish QA evidence.
// Uploading a JPEG does not manufacture a 16-bit RAW-derived technical master.
async function verifiedTech(env,job) {
  const proof=await env.TROPICANA_EDITORIAL_R2.get(key(job.job_id,"technical/qa.json"));
  if(!proof)return null;
  let q;try{q=await proof.json()}catch{return null}
  const expected=key(job.job_id,"technical/master16.tif");
  if(q.job_id!==job.job_id||q.source_sha256!==job.source_sha256||
     q.technical_status!=="TECHNICAL_QA_PASS"||q.HDR_applied!==false||
     q.virtual_exposures!==false||q.local_HDR!==false||
     q.qa_checks?.HDR_ABSENT!=="PASS"||q.master_bit_depth!==16||
     q.master_key!==expected||!SHA.test(String(q.master_sha256||""))||
     !Number.isInteger(q.master_bytes)||q.master_bytes<1000000)return null;
  const head=await env.TROPICANA_EDITORIAL_R2.head(expected);
  if(!head||head.size!==q.master_bytes||head.httpMetadata?.contentType!=="image/tiff"||
     head.customMetadata?.sha256!==q.master_sha256)return null;
  return q;
}
async function decision(env,id) {
  const o=await env.TROPICANA_EDITORIAL_R2.get(key(id,"technical/decision.json"));
  return o?o.json():null;
}
async function availability(env,job) {
  const q=await verifiedTech(env,job),d=await decision(env,job.job_id);
  const approved=!!(q&&d&&d.decision==="APPROVED"&&d.master_sha256===q.master_sha256&&d.source_sha256===q.source_sha256);
  let imported=null;
  if(approved&&env.TEMP_BUFFER_R7){
    const record=await env.TROPICANA_EDITORIAL_R2.get(key(job.job_id,"handoff.json"));
    if(record){
      const m=await record.json();
      const target=m.schema===2&&m.master_sha256===q.master_sha256&&m.generation_authorized===false
        ?await env.TEMP_BUFFER_R7.head(m.mg4k_master_key):null;
      if(target&&target.size===q.master_bytes&&target.customMetadata?.technical_master_sha256===q.master_sha256)
        imported=m;
    }
  }
  return {q,d,approved,imported};
}
function state(job,a) {
  const status=a.d?.decision==="REJECTED"?"REJECTED":a.imported?"MG4K_MASTER_IMPORTED":a.approved?"TECH_MASTER_APPROVED":a.q?"AWAITING_HUMAN_QA":job.status;
  return {
    ok:true,job_id:job.job_id,filename:job.filename,project:job.project,
    source_sha256:job.source_sha256,source_bytes:job.source_bytes,
    status,stage:a.q?"technical_qa":job.stage,progress:a.q?100:job.progress,
    progress_kind:a.q?"TECHNICAL_STAGE_COMPLETE":"NOT_MEASURED",
    message:a.q?"Технический мастер готов; требуется визуальное утверждение QA.":job.message,
    technical_master_ready:!!a.q,technical_qa:a.q?{master_sha256:a.q.master_sha256,master_bytes:a.q.master_bytes,bit_depth:16,HDR_applied:false}:null,
    locks:job.locks,approval:a.d?{decision:a.d.decision,at:a.d.at}:null,
    can_approve:!!a.q&&!a.d,can_reject:!!a.q&&!a.d,
    can_download:a.approved,can_handoff:a.approved&&!a.imported,
    mg4k_imported:!!a.imported,
    mg4k_import_url:a.imported?"/?editorial_import="+encodeURIComponent(job.job_id):null,
    auto_generation:false
  };
}


// MG4K holds the approved immutable master in its own R2 storage.  No paid
// processor job is created and the imported TIFF is never passed to a model.
async function importIntoMg4k(env,job,q) {
  if(!env.TEMP_BUFFER_R7)return {error:"mg4k_storage_missing",code:503};
  const dest="editorial-imports/"+job.job_id+"/approved-master16.tif";
  const source=await env.TROPICANA_EDITORIAL_R2.get(q.master_key);
  if(!source||source.size!==q.master_bytes||source.httpMetadata?.contentType!=="image/tiff"||
    source.customMetadata?.sha256!==q.master_sha256)return {error:"technical_master_integrity_mismatch",code:409};
  const metadata={source_project:job.project,editorial_job_id:job.job_id,source_sha256:job.source_sha256,
    technical_master_sha256:q.master_sha256,qa_status:"APPROVED",generation_authorized:"false"};
  const committed=await env.TEMP_BUFFER_R7.put(dest,source.body,{
    onlyIf:new Headers({"If-None-Match":"*"}),
    httpMetadata:{contentType:"image/tiff"},customMetadata:metadata
  });
  const target=await env.TEMP_BUFFER_R7.head(dest);
  if(!target||target.size!==q.master_bytes||target.httpMetadata?.contentType!=="image/tiff"||
      target.customMetadata?.technical_master_sha256!==q.master_sha256||
      target.customMetadata?.editorial_job_id!==job.job_id)
    return {error:"mg4k_import_verification_failed",code:409};
  return {dest,size:target.size,created:!!committed};
}

export async function editorialJobApi(request,env,url,authorizeSession) {
  const path=url.pathname,method=request.method.toUpperCase();
  const id=url.searchParams.get("job")||"";
  if(!env.TROPICANA_EDITORIAL_R2)return json({error:"r2_missing"},503);
  const owner=await owned(request,env,url,authorizeSession,id);
  if(!owner)return json({error:"job_not_found_or_unauthorized"},404);
  const {job}=owner;
  const a=await availability(env,job);

  if(path==="/api/editorial/job/status"&&method==="GET")return json(state(job,a));
  if(path==="/api/editorial/job/import-status"&&method==="GET") {
    if(!a.imported)return json({error:"mg4k_import_not_ready"},409);
    const m=a.imported;
    return json({ok:true,job_id:id,project:job.project,filename:job.filename,
      stage:"MG4K_MASTER_IMPORTED",source_sha256:job.source_sha256,
      master_sha256:m.master_sha256,master_bytes:m.master_bytes,
      master_bit_depth:m.master_bit_depth,locked:true,
      locks:m.locks,mg4k_locks:m.mg4k_locks,
      human_QA_at:m.QA.at,generation_started:false,
      message:"Файл в хранилище MG 4K. Художественная генерация не запускалась."});
  }

  if(path==="/api/editorial/job/decision"&&method==="POST") {
    let input;try{input=await request.json()}catch{return json({error:"invalid_json"},400)}
    if(!["APPROVED","REJECTED"].includes(input?.decision))return json({error:"invalid_decision"},400);
    if(a.d)return json(a.d.decision===input.decision?{ok:true,idempotent:true,decision:a.d.decision}:{error:"decision_already_recorded"},a.d.decision===input.decision?200:409);
    if(!a.q)return json({error:"technical_master_not_ready_or_failed_QA"},409);
    if(input.master_sha256!==a.q.master_sha256)return json({error:"master_hash_mismatch"},409);
    const d={schema:1,job_id:id,decision:input.decision,source_sha256:job.source_sha256,
      master_sha256:a.q.master_sha256,at:new Date().toISOString(),owner_session_id:owner.session.id};
    const result=await env.TROPICANA_EDITORIAL_R2.put(key(id,"technical/decision.json"),JSON.stringify(d),{
      onlyIf:new Headers({"If-None-Match":"*"}),httpMetadata:{contentType:"application/json"}
    });
    if(!result)return json({error:"decision_race_retry_status"},409);
    return json({ok:true,decision:d.decision,at:d.at,job_id:id});
  }

  if(path==="/api/editorial/job/download"&&method==="GET") {
    if(!a.approved)return json({error:"human_QA_approval_required"},409);
    const master=await env.TROPICANA_EDITORIAL_R2.get(a.q.master_key);
    if(!master||master.size!==a.q.master_bytes)return json({error:"master_missing"},409);
    return new Response(master.body,{headers:{
      "content-type":"image/tiff","content-length":String(master.size),
      "content-disposition":'attachment; filename="MG4K_TECH_MASTER_'+id+'.tif"',
      "cache-control":"private, no-store","x-content-type-options":"nosniff"
    }});
  }

  if(path==="/api/editorial/job/handoff"&&method==="POST") {
    if(!a.approved)return json({error:"human_QA_approval_required"},409);
    const transfer=await importIntoMg4k(env,job,a.q);
    if(transfer.error)return json({error:transfer.error},transfer.code);
    const manifest={
      schema:2,type:"MG4K_TECHNICAL_HANDOFF",job_id:id,project:job.project,
      source_key:job.source_key,source_sha256:job.source_sha256,
      master_key:a.q.master_key,master_sha256:a.q.master_sha256,
      master_bytes:a.q.master_bytes,master_bit_depth:16,
      QA:{decision:"APPROVED",at:a.d.at,source_sha256:a.d.source_sha256,master_sha256:a.d.master_sha256},
      locks:LOCKS,mg4k_locks:{camera:"hard",composition:"hard",geometry:"hard",architecture:"hard",facade_details:"hard",textures:"hard",text_signage:"hard",interior:"hard",furniture:"hard"},
      mg4k_master_key:transfer.dest,mg4k_master_bytes:transfer.size,
      generation_authorized:false,stage:"MG4K_MASTER_IMPORTED"
    };
    const result=await env.TROPICANA_EDITORIAL_R2.put(key(id,"handoff.json"),JSON.stringify(manifest),{
      onlyIf:new Headers({"If-None-Match":"*"}),httpMetadata:{contentType:"application/json"}
    });
    // Stages an immutable manifest; does not create or launch an expensive MG4K job.
    if(!result) {
      const old=await env.TROPICANA_EDITORIAL_R2.get(key(id,"handoff.json"));
      if(!old)return json({error:"handoff_conflict"},409);
      const existing=await old.json();
      if(existing.master_sha256!==manifest.master_sha256)return json({error:"handoff_hash_conflict"},409);
    }
    return json({ok:true,job_id:id,stage:"MG4K_MASTER_IMPORTED",
      mg4k_import_url:"/?editorial_import="+encodeURIComponent(id),
      message:"Утверждённый TIFF перенесён в хранилище MG 4K; художественная генерация не запускалась.",
      generation_started:false});
  }
  return json({error:"job_api_not_found"},404);
}
