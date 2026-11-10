// Per-upload Editorial Photo records. Historical DSC09818/V9/V10 artifacts are never mutated.
const ROOT = "tropicana-park/editorial-jobs/";
const idOK = id => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(String(id||""));
const json = (x,status=200) => Response.json(x,{status,headers:{"cache-control":"private, no-store","x-content-type-options":"nosniff"}});
const key = (id,name) => ROOT+id+"/"+name;
const SHA = /^[0-9a-f]{64}$/;
const DEFECT_TYPES=Object.freeze(["geometry","exposure","color","detail","materials","other"]);
const MAX_UPLOAD=8*1024*1024;

const LOCKS = Object.freeze({geometry:"HARD",perspective:"HARD",composition:"HARD",architecture:"HARD",materials:"HARD",identity:"HARD",source:"IMMUTABLE",hdr:"PROHIBITED",generation:"HUMAN_QA_FIRST"});
const hash = async bytes => [...new Uint8Array(await crypto.subtle.digest("SHA-256",bytes))].map(v=>v.toString(16).padStart(2,"0")).join("");
const shaBytes=hex=>Uint8Array.from(String(hex).match(/.{2}/g)||[],h=>parseInt(h,16)).buffer;
const hex=bytes=>[...new Uint8Array(bytes)].map(v=>v.toString(16).padStart(2,"0")).join("");


export async function createEditorialIntakeJob(env,session,{filename,mime,source_key,source_sha256,bytes,
  parent_job_id=null,root_job_id=null,revision_num=1,inherit_access_hash=null,
  correction_plan=null}) {
  const id=crypto.randomUUID();
  const token=crypto.randomUUID().replaceAll("-","")+crypto.randomUUID().replaceAll("-","");
  const stamp=new Date().toISOString();
  const record={
    schema:1,job_id:id,owner_session_id:session.id,
    access_hash:inherit_access_hash||await hash(new TextEncoder().encode(token)),
    parent_job_id,root_job_id,revision_num,correction_plan,
    project:"tropicana-park",filename:String(filename||"image").slice(0,128),
    mime,source_key,source_sha256,source_bytes:bytes,created_at:stamp,
    status:"AWAITING_TECH_PROCESSING",stage:"source_stored",progress:null,
    message:"Файл сохранён. Техническая обработка ещё не запущена.",
    locks:LOCKS,auto_generation:false,technical_master_ready:false
  };
  await env.TROPICANA_EDITORIAL_R2.put(key(id,"job.json"),JSON.stringify(record),{httpMetadata:{contentType:"application/json"}});
  return {...record,job_token:inherit_access_hash?null:token};
}

async function owned(request,env,url,authorizeSession,id) {
  if(!idOK(id))return null;
  const obj=await env.TROPICANA_EDITORIAL_R2.get(key(id,"job.json"));
  if(!obj)return null;
  const job=await obj.json();
  if(job.job_id!==id)return null;
  const session=await authorizeSession(request,url,env);
  const sameSession=!!session&&job.owner_session_id===session.id;
  const bearer=String(request.headers.get("x-editorial-job-token")||"");
  const tokenOK=/^[a-f0-9]{64}$/.test(bearer)&&!!job.access_hash&&
    (await hash(new TextEncoder().encode(bearer)))===job.access_hash;
  if(!sameSession&&!tokenOK)return null;
  return {job,session:session&&sameSession?session:{id:job.owner_session_id}};
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
     q.qa_checks?.HDR_ABSENT!=="PASS"||
     q.qa_checks?.SOURCE_SHA256!=="PASS"||
     q.qa_checks?.GEOMETRY_UNCHANGED!=="PASS"||
     q.qa_checks?.TIFF_READBACK!=="PASS"||
     (job.correction_plan&&(
       q.correction_status!=="APPLIED"||
       q.correction?.parent_job_id!==job.correction_plan.parent_job_id||
       q.correction?.parent_master_sha256!==job.correction_plan.parent_master_sha256||
       q.correction?.remarks_sha256!==job.correction_plan.remarks_sha256||
       q.correction?.geometry_transform!=="IDENTITY"||
       q.correction?.generative_processing!==false
     ))||
     q.geometry!=="IDENTITY_NO_CORRECTION"||
     ![8,16].includes(q.source_signal_bits)||
     !Number.isInteger(q.width)||q.width<320||
     !Number.isInteger(q.height)||q.height<320||
     q.master_bit_depth!==16||
     q.master_key!==expected||!SHA.test(String(q.master_sha256||""))||
     !Number.isInteger(q.master_bytes)||q.master_bytes<200000)return null;
  const head=await env.TROPICANA_EDITORIAL_R2.head(expected);
  if(!head||head.size!==q.master_bytes||head.httpMetadata?.contentType!=="image/tiff"||
     head.customMetadata?.sha256!==q.master_sha256||
     !head.checksums?.sha256||hex(head.checksums.sha256)!==q.master_sha256)return null;
  const first=await env.TROPICANA_EDITORIAL_R2.get(expected,{range:{offset:0,length:4}});
  if(!first)return null;
  const signature=new Uint8Array(await first.arrayBuffer());
  const tiff=signature.length===4&&(
    (signature[0]===73&&signature[1]===73&&(signature[2]===42||signature[2]===43)&&signature[3]===0)||
    (signature[0]===77&&signature[1]===77&&signature[2]===0&&(signature[3]===42||signature[3]===43))
  );
  if(!tiff)return null;
  return q;
}
async function revisionLink(env,id){
  const o=await env.TROPICANA_EDITORIAL_R2.get(key(id,"rework/revision.json"));
  return o?o.json():null;
}
async function decision(env,id) {
  const o=await env.TROPICANA_EDITORIAL_R2.get(key(id,"technical/decision.json"));
  return o?o.json():null;
}
async function availability(env,job) {
  const q=await verifiedTech(env,job),d=await decision(env,job.job_id);
  const revision=d?.decision==="REWORK_REQUESTED"?await revisionLink(env,job.job_id):null;
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
  const processObject=await env.TROPICANA_EDITORIAL_R2.get(key(job.job_id,"processing-state.json"));
  let processing=null;
  if(processObject){try{processing=await processObject.json()}catch{}}
  return {q,d,approved,imported,processing,revision};
}
function sourceVerification(job,a){
  const q=a.q,checks=q?.qa_checks||{};
  // This is a truthful record of automated checks, NOT an approval of photo quality.
  const shaOK=checks.SOURCE_SHA256==="PASS"&&q?.source_sha256===job.source_sha256;
  const rasterOK=shaOK&&[8,16].includes(q?.source_signal_bits)&&
    Number.isInteger(q?.width)&&Number.isInteger(q?.height)&&
    q.width>=320&&q.height>=320;
  const geometryUnchanged=rasterOK&&checks.GEOMETRY_UNCHANGED==="PASS"&&
    q.geometry==="IDENTITY_NO_CORRECTION";
  return {
    status:shaOK&&rasterOK?"TECHNICAL_PASS":q?"INCOMPLETE_EVIDENCE":"PENDING",
    sha256_status:shaOK?"PASS":q?"NOT_CONFIRMED":"PENDING",
    raster_status:rasterOK?"PASS":q?"NOT_CONFIRMED":"PENDING",
    geometry_preservation:geometryUnchanged?"PASS":q?"NOT_CONFIRMED":"PENDING",
    mime:job.mime,source_bytes:job.source_bytes,
    source_sha256:job.source_sha256,
    width:rasterOK?q.width:null,height:rasterOK?q.height:null,
    signal_bits:rasterOK?q.source_signal_bits:null,
    alpha_handling:rasterOK?(q.alpha_handling||"none"):null,
    optical_correction:"NOT_PERFORMED",
    perspective_correction:"NOT_PERFORMED",
    visual_review:a.d?.decision==="APPROVED"?"INCLUDED_IN_APPROVED_TECH_QA":
      a.d?.decision==="REWORK_REQUESTED"?"REWORK_REQUESTED":"PENDING_HUMAN_QA",
    verified_proof_sha:q?.master_sha256||null
  };
}
function state(job,a) {
  const status=a.d?.decision==="REWORK_REQUESTED"?(a.revision?"REVISION_CREATED":"REWORK_REQUESTED"):a.d?.decision==="REJECTED"?"REJECTED":a.imported?"MG4K_MASTER_IMPORTED":a.approved?"TECH_MASTER_APPROVED":a.q?"AWAITING_HUMAN_QA":a.processing?.status||job.status;
  return {
    ok:true,job_id:job.job_id,filename:job.filename,project:job.project,
    revision_num:job.revision_num||1,parent_job_id:job.parent_job_id||null,
    root_job_id:job.root_job_id||job.job_id,
    source_sha256:job.source_sha256,source_bytes:job.source_bytes,
    source_verification:sourceVerification(job,a),
    technical_correction:job.correction_plan?{
      requested:true,categories:job.correction_plan.categories,
      status:a.q?.correction_status||a.processing?.status||"QUEUED",
      evidence:a.q?.correction||null,
      geometry_correction:"NOT_PERFORMED",
      source_sha256:job.source_sha256,
      parent_master_sha256:job.correction_plan.parent_master_sha256
    }:null,
    status,stage:a.q?"technical_qa_ready":a.processing?.stage||job.stage,
    progress:a.q?100:typeof a.processing?.progress==="number"?a.processing.progress:null,
    progress_kind:a.q?"TECHNICAL_STAGE_COMPLETE":a.processing?.status==="PROCESSING"?"TECHNICAL_STAGE_MEASURED":"NOT_MEASURED",
    message:a.d?.decision==="REWORK_REQUESTED"?
      "Мастер возвращён на доработку. Для света/цвета доступна измеримая автоматическая коррекция; геометрия без опорных точек — HOLD.":
      a.q?"Технический мастер готов; требуется визуальное утверждение QA.":a.processing?.message||job.message,
    error:a.processing?.error||null,
    technical_master_ready:!!a.q,technical_qa:a.q?{master_sha256:a.q.master_sha256,master_bytes:a.q.master_bytes,bit_depth:16,HDR_applied:false}:null,
    locks:job.locks,approval:a.d?{decision:a.d.decision,at:a.d.at}:null,
    can_approve:!!a.q&&!a.d,can_reject:!!a.q&&!a.d,
    can_rework:!!a.q&&!a.d,
    can_upload_revision:a.d?.decision==="REWORK_REQUESTED"&&!a.revision,
    can_auto_rework:a.d?.decision==="REWORK_REQUESTED"&&!a.revision&&
      Array.isArray(a.d.categories)&&a.d.categories.length>0&&
      a.d.categories.every(c=>c==="exposure"||c==="color"),
    auto_rework_hold_reason:a.d?.decision==="REWORK_REQUESTED"&&
      a.d.categories?.some(c=>c!=="exposure"&&c!=="color")?
      "Автоматическая коррекция перспективы, фактур и деталей требует проверенных опорных точек/профиля. HOLD.":"",
    rework:a.d?.decision==="REWORK_REQUESTED"?{categories:a.d.categories,note:a.d.note,at:a.d.at}:null,
    next_revision_job_id:a.revision?.job_id||null,
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
    onlyIf:new Headers({"If-None-Match":"*"}),sha256:shaBytes(q.master_sha256),
    httpMetadata:{contentType:"image/tiff"},customMetadata:metadata
  });
  const target=await env.TEMP_BUFFER_R7.head(dest);
  if(!target||target.size!==q.master_bytes||target.httpMetadata?.contentType!=="image/tiff"||
      target.customMetadata?.technical_master_sha256!==q.master_sha256||
      target.customMetadata?.editorial_job_id!==job.job_id)
    return {error:"mg4k_import_verification_failed",code:409};
  return {dest,size:target.size,created:!!committed};
}


// Called by the core MG4K paid job intake only on an explicit user click.
// The user-supplied LOCK form is not trusted: return the server-owned profile.
export async function verifyEditorialArtisticInput(env,id,bearer,bytes){
 if(!idOK(id)||!/^[a-f0-9]{64}$/.test(String(bearer||"")))
   throw Error("editorial_import_access_invalid");
 const r=await env.TROPICANA_EDITORIAL_R2.get(key(id,"job.json"));
 if(!r)throw Error("editorial_import_not_found");
 const job=await r.json();
 if(job.job_id!==id||(await hash(new TextEncoder().encode(bearer)))!==job.access_hash)
   throw Error("editorial_import_forbidden");
 const a=await availability(env,job);
 if(!a.approved||!a.imported||!a.q||a.d?.decision!=="APPROVED"||
    !["preview_reviewed","geometry","materials","no_hdr"].every(k=>a.d.visual_qa_checks?.[k]===true))
   throw Error("editorial_import_not_human_QA_approved");
 const preview=await env.TROPICANA_EDITORIAL_R2.head(key(id,"technical/fullres-preview.jpg"));
 if(!preview||preview.size!==a.q.preview_bytes||
    preview.httpMetadata?.contentType!=="image/jpeg"||
    hex(preview.checksums?.sha256||new ArrayBuffer(0))!==a.q.preview_sha256)
   throw Error("editorial_import_preview_lineage_HOLD");
 if((await hash(bytes))!==a.q.preview_sha256)throw Error("editorial_import_bytes_do_not_match_approved_proxy");
 const master=await env.TEMP_BUFFER_R7.head(a.imported.mg4k_master_key);
 if(!master||master.size!==a.q.master_bytes||
    hex(master.checksums?.sha256||new ArrayBuffer(0))!==a.q.master_sha256)
   throw Error("editorial_import_master_hash_HOLD");
 return {locks:{...a.imported.mg4k_locks},job_id:id,project:job.project,
    approved_master_sha256:a.q.master_sha256,original_source_sha256:job.source_sha256,
    approved_proxy_sha256:a.q.preview_sha256,qa_approved_at:a.d.at};
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
  if(path==="/api/editorial/job/source"&&method==="GET"){
    const src=await env.TROPICANA_EDITORIAL_R2.get(job.source_key);
    if(!src||src.size!==job.source_bytes||src.customMetadata?.sha256!==job.source_sha256||
       !["image/jpeg","image/png","image/webp"].includes(src.httpMetadata?.contentType))
      return json({error:"source_integrity_HOLD"},409);
    return new Response(src.body,{headers:{"content-type":job.mime,"cache-control":"private,no-store",
       "x-content-type-options":"nosniff"}});
  }
  if(path==="/api/editorial/job/rework/auto"&&method==="POST"){
    if(a.d?.decision!=="REWORK_REQUESTED"||!a.q)
      return json({error:"rework_decision_and_verified_master_required"},409);
    if(a.revision)return json({error:"revision_already_created",job_id:a.revision.job_id},409);
    if(!Array.isArray(a.d.categories)||!a.d.categories.length)
      return json({error:"invalid_rework_remarks"},409);
    const unsupported=a.d.categories.filter(c=>c!=="exposure"&&c!=="color");
    if(unsupported.length)
      return json({ok:false,status:"HOLD",error:"verified_geometry_or_detail_references_required",
        categories:unsupported,reason:"Нужны достоверные опорные геометрические точки/калибровка или работа специалиста. "+
        "Без них автоматическая коррекция этих замечаний запрещена. Исходник и V1 сохранены.",
        generation_started:false},409);
    if(!env.TROPICANA_TECH_WORKFLOW)
      return json({error:"free_technical_workflow_missing"},503);
    const version=(job.revision_num||1)+1;
    if(version>12)return json({error:"revision_limit_reached"},409);
    const noteHash=await hash(new TextEncoder().encode(a.d.note));
    const plan={schema:1,type:"DETERMINISTIC_TECH_REWORK",
      parent_job_id:id,parent_master_sha256:a.d.master_sha256,
      remarks_sha256:noteHash,categories:[...a.d.categories],
      HDR_allowed:false,geometry_transform:"IDENTITY",
      allowed_algorithm:"BOUNDED_GLOBAL_PHOTOMETRY",human_approval_required:true};
    // V2 derives its pixels from the same original immutable source. This is
    // NOT a second user upload; only the corrected technical output differs.
    const child=await createEditorialIntakeJob(env,{id:job.owner_session_id},{
      filename:job.filename,mime:job.mime,source_key:job.source_key,
      source_sha256:job.source_sha256,bytes:job.source_bytes,
      parent_job_id:id,root_job_id:job.root_job_id||id,revision_num:version,
      inherit_access_hash:job.access_hash,correction_plan:plan
    });
    const link={schema:2,job_id:child.job_id,revision_num:version,
      source_sha256:job.source_sha256,source_unchanged:true,
      correction_type:plan.type,correction_categories:plan.categories,
      remarks_sha256:noteHash,parent_master_sha256:a.d.master_sha256,
      rework_decision_at:a.d.at,created_at:new Date().toISOString()};
    const written=await env.TROPICANA_EDITORIAL_R2.put(
      key(id,"rework/revision.json"),JSON.stringify(link),{
      onlyIf:new Headers({"If-None-Match":"*"}),
      httpMetadata:{contentType:"application/json"}});
    if(!written){
      await env.TROPICANA_EDITORIAL_R2.delete(key(child.job_id,"job.json"));
      const winner=await revisionLink(env,id);
      return json({error:"revision_race_conflict",job_id:winner?.job_id||null},409);
    }
    let started=false,error=null;
    try{
      await env.TROPICANA_TECH_WORKFLOW.create({id:child.job_id,params:{job_id:child.job_id}});
      started=true;
    }catch(err){
      error=String(err?.message||err).slice(0,250);
      await env.TROPICANA_EDITORIAL_R2.put(key(child.job_id,"processing-state.json"),
        JSON.stringify({job_id:child.job_id,status:"HOLD",
          stage:"correction_dispatch_hold",progress:null,error,
          message:"Бесплатный технический процессор не запущен. Без генерации."}),
        {httpMetadata:{contentType:"application/json"}});
    }
    return json({ok:true,job_id:child.job_id,parent_job_id:id,
      revision_num:version,status:started?"QUEUED":"HOLD",
      workflow_started:started,source_unchanged:true,
      correction_type:"DETERMINISTIC_GLOBAL_PHOTOMETRY",
      corrected_master_ready:false,generation_started:false,error},201);
  }

  if(path==="/api/editorial/job/revision"&&method==="POST"){
    if(a.d?.decision!=="REWORK_REQUESTED")return json({error:"rework_decision_required"},409);
    if(a.revision)return json({error:"revision_already_created",job_id:a.revision.job_id},409);
    let form;try{form=await request.formData()}catch{return json({error:"invalid_revision_form"},400)}
    const file=form.get("file");
    if(!file||typeof file.arrayBuffer!=="function"||file.size<10000||file.size>MAX_UPLOAD||
      !["image/jpeg","image/png","image/webp"].includes(file.type))
      return json({error:"valid_revision_image_required_max_8mb"},400);
    const bytes=await file.arrayBuffer(),newSha=await hash(bytes);
    if(newSha===job.source_sha256)return json({error:"revision_source_unchanged"},409);
    const version=(job.revision_num||1)+1;
    if(version>12)return json({error:"revision_limit_reached"},409);
    const sourceKey="tropicana-park/editorial-revisions/"+id+"/"+crypto.randomUUID()+
      (file.type==="image/png"?".png":file.type==="image/webp"?".webp":".jpg");
    await env.TROPICANA_EDITORIAL_R2.put(sourceKey,bytes,{
      httpMetadata:{contentType:file.type},customMetadata:{sha256:newSha,parent_job_id:id}
    });
    const child=await createEditorialIntakeJob(env,{id:job.owner_session_id},{
      filename:file.name,mime:file.type,source_key:sourceKey,source_sha256:newSha,bytes:bytes.byteLength,
      parent_job_id:id,root_job_id:job.root_job_id||id,revision_num:version,
      inherit_access_hash:job.access_hash
    });
    const link={schema:1,job_id:child.job_id,revision_num:version,source_sha256:newSha,
      parent_master_sha256:a.d.master_sha256,rework_decision_at:a.d.at,created_at:new Date().toISOString()};
    const written=await env.TROPICANA_EDITORIAL_R2.put(key(id,"rework/revision.json"),JSON.stringify(link),{
      onlyIf:new Headers({"If-None-Match":"*"}),httpMetadata:{contentType:"application/json"}
    });
    if(!written){
      // Only the successful conditional writer owns the new revision. The
      // losing request has not launched Workflow, so delete its unlinked input.
      await env.TROPICANA_EDITORIAL_R2.delete([sourceKey,key(child.job_id,"job.json")]);
      const winner=await revisionLink(env,id);
      return json({error:"revision_race_conflict",job_id:winner?.job_id||null},409);
    }
    let started=false,error=null;
    try{
      if(!env.TROPICANA_TECH_WORKFLOW)throw Error("technical_workflow_missing");
      await env.TROPICANA_TECH_WORKFLOW.create({id:child.job_id,params:{job_id:child.job_id}});
      started=true;
    }catch(err){
      error=String(err?.message||err).slice(0,180);
      await env.TROPICANA_EDITORIAL_R2.put(key(child.job_id,"processing-state.json"),
        JSON.stringify({job_id:child.job_id,status:"FAILED",stage:"workflow_dispatch_failed",
          progress:null,error,message:"Техническая обработка V"+version+" не запущена."}),
        {httpMetadata:{contentType:"application/json"}});
    }
    return json({ok:true,job_id:child.job_id,parent_job_id:id,root_job_id:job.root_job_id||id,
      revision_num:version,source_sha256:newSha,
      status:started?"QUEUED":"FAILED",workflow_started:started,error,generation_started:false},201);
  }

  if(path==="/api/editorial/job/preview"&&method==="GET"){
    if(!a.q)return json({error:"technical_preview_not_ready"},409);
    const obj=await env.TROPICANA_EDITORIAL_R2.get(key(id,"technical/fullres-preview.jpg"));
    if(!obj||obj.httpMetadata?.contentType!=="image/jpeg"||
       obj.size!==a.q.preview_bytes||obj.customMetadata?.sha256!==a.q.preview_sha256||
       !obj.checksums?.sha256||hex(obj.checksums.sha256)!==a.q.preview_sha256)
      return json({error:"technical_preview_integrity_HOLD"},409);
    return new Response(obj.body,{headers:{"content-type":"image/jpeg",
      "cache-control":"private,no-store","x-content-type-options":"nosniff"}});
  }
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
    if(!["APPROVED","REWORK_REQUESTED"].includes(input?.decision))return json({error:"invalid_decision"},400);
    const categories=input?.categories;
    if(input.decision==="REWORK_REQUESTED"&&
       (!Array.isArray(categories)||!categories.length||categories.length>DEFECT_TYPES.length||
        new Set(categories).size!==categories.length||!categories.every(x=>DEFECT_TYPES.includes(x))||
        typeof input.note!=="string"||input.note.trim().length<12||input.note.trim().length>2000))
      return json({error:"structured_rework_remarks_required"},400);
    if(input.decision==="APPROVED"&&
      !["preview_reviewed","geometry","materials","no_hdr"].every(k=>input.checks?.[k]===true))
      return json({error:"visual_QA_checklist_required"},400);
    if(a.d)return json(a.d.decision===input.decision?{ok:true,idempotent:true,decision:a.d.decision}:{error:"decision_already_recorded"},a.d.decision===input.decision?200:409);
    if(!a.q)return json({error:"technical_master_not_ready_or_failed_QA"},409);
    if(input.master_sha256!==a.q.master_sha256)return json({error:"master_hash_mismatch"},409);
    const d={schema:1,job_id:id,decision:input.decision,source_sha256:job.source_sha256,
      master_sha256:a.q.master_sha256,at:new Date().toISOString(),owner_session_id:owner.session.id,
      visual_qa_checks:input.decision==="APPROVED"?input.checks:null,
      categories:input.decision==="REWORK_REQUESTED"?categories:null,
      note:input.decision==="REWORK_REQUESTED"?input.note.trim():null,
      revision_num:job.revision_num||1};
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
