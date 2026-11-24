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
  const sourceKind=String(q?.source_kind||"RASTER");
  const expected=key(job.job_id,"technical/master16.tif");
  const geom=job.correction_plan?.type==="GEOMETRY_FACADE_QUAD_V1";
  const ao=q.auto_optics_correction,ag=q.auto_geometry_correction;
  const ap=q.auto_vertical_perspective_correction,ph=q.auto_photometric_correction;
  const autoOptics=!job.correction_plan&&ao?.status==="APPLIED";
  const autoRoll=!job.correction_plan&&ag?.status==="APPLIED";
  const autoPerspective=!job.correction_plan&&ap?.status==="APPLIED";
  const autoSpatial=autoOptics||autoRoll||autoPerspective;
  if(q.job_id!==job.job_id||q.source_sha256!==job.source_sha256||
     q.technical_status!=="TECHNICAL_QA_PASS"||q.HDR_applied!==false||
     q.virtual_exposures!==false||q.local_HDR!==false||
     q.qa_checks?.HDR_ABSENT!=="PASS"||
     q.qa_checks?.SOURCE_SHA256!=="PASS"||
     !["RASTER","CAMERA_RAW"].includes(sourceKind)||
     (sourceKind==="CAMERA_RAW"&&(
       q.raw_decoder!=="rawpy-0.28.0/LibRaw"||
       !SHA.test(String(q.source_preview_sha256||""))||
       !Number.isInteger(q.source_preview_bytes)||q.source_preview_bytes<10000
     ))||
     (geom?
       (q.qa_checks?.GEOMETRY_TRANSFORM_EVIDENCED!=="PASS"||
        q.qa_checks?.GEOMETRY_UNCHANGED!=="NOT_APPLICABLE"):
       autoSpatial?
       (q.qa_checks?.GEOMETRY_TRANSFORM_EVIDENCED!=="PASS"||
        q.qa_checks?.GEOMETRY_UNCHANGED!=="NOT_APPLICABLE"):
       q.qa_checks?.GEOMETRY_UNCHANGED!=="PASS")||
     (!job.correction_plan&&q.technical_version!=="AUTO_TECH_MASTER_V2")||
     (!job.correction_plan&&(!ao||!ag||!ap||!ph))||
     (autoOptics&&(
       ao?.engine!=="AUTO_PROFILED_OPTICS_V1"||ao?.HDR_applied!==false||
       ao?.generative_processing!==false||!ao?.profile_id||
       !Number.isFinite(ao?.crop_area_fraction)||ao.crop_area_fraction<0||ao.crop_area_fraction>.14||
       !Number.isFinite(ao?.invalid_source_fraction)||ao.invalid_source_fraction>.00002||
       !Number.isFinite(ao?.changed_mean_absolute_fraction)||ao.changed_mean_absolute_fraction<.0005
     ))||
     (autoRoll&&(
       ag?.engine!=="AUTO_CAMERA_ROLL_SAFE_CROP_V1"||
       ag?.geometry_transform!=="AUTO_CAMERA_ROLL_CORRECTION"||
       ag?.HDR_applied!==false||ag?.generative_processing!==false||
       !Number.isFinite(ag?.vertical_residual_before_deg)||
       !Number.isFinite(ag?.vertical_residual_after_deg)||
       Math.abs(ag.vertical_residual_after_deg)>.40||
       Math.abs(ag.vertical_residual_after_deg)>=Math.abs(ag.vertical_residual_before_deg)*.40||
       !Number.isFinite(ag?.crop_area_fraction)||ag.crop_area_fraction<0||ag.crop_area_fraction>.15||
       !Number.isFinite(ag?.lost_salient_edge_fraction)||ag.lost_salient_edge_fraction>.055||
       ag?.invalid_border_fraction!==0||
       !Number.isFinite(ag?.changed_mean_absolute_fraction)||
       ag.changed_mean_absolute_fraction<.003||
       !Array.isArray(ag?.operations)||ag.operations.length===0
     ))||
     (autoPerspective&&(
       ap?.engine!=="AUTO_VERTICAL_PERSPECTIVE_V1"||
       ap?.geometry_transform!=="AUTO_VERTICAL_PERSPECTIVE_CORRECTION"||
       ap?.HDR_applied!==false||ap?.generative_processing!==false||
       !Number.isFinite(ap?.crop_area_fraction)||ap.crop_area_fraction<0||ap.crop_area_fraction>.17||
       !Number.isFinite(ap?.invalid_border_fraction)||ap.invalid_border_fraction>.00002||
       !Number.isFinite(ap?.max_corner_drift_fraction)||ap.max_corner_drift_fraction>.16||
       !Number.isFinite(ap?.vertical_residual_after_deg)||Math.abs(ap.vertical_residual_after_deg)>.45||
       !Number.isFinite(ap?.changed_mean_absolute_fraction)||ap.changed_mean_absolute_fraction<.002||
       !Array.isArray(ap?.operations)||ap.operations.length===0
     ))||
     (!job.correction_plan&&(
       !["APPLIED","NO_CHANGE_NEEDED","HOLD"].includes(String(ao?.status||""))||
       !["APPLIED","NO_CHANGE_NEEDED","HOLD"].includes(String(ag?.status||""))||
       !["APPLIED","NO_CHANGE_NEEDED","HOLD"].includes(String(ap?.status||""))||
       !["APPLIED","NO_CHANGE_NEEDED","HOLD"].includes(String(ph?.status||""))||
       ph?.engine!=="AUTO_NEUTRAL_PHOTOMETRY_V1"||
       ph?.HDR_applied!==false||ph?.generative_processing!==false||
       ph?.local_tone_mapping!==false||
       (ph?.status==="APPLIED"&&(
         !Number.isFinite(ph?.changed_mean_absolute_fraction)||ph.changed_mean_absolute_fraction<.0015||
         !Number.isFinite(ph?.clipped_pixel_fraction)||ph.clipped_pixel_fraction>.006||
         !Array.isArray(ph?.operations)||ph.operations.length===0
       ))
     ))||
     q.qa_checks?.TIFF_READBACK!=="PASS"||
     (job.correction_plan&&(
       q.correction_status!=="APPLIED"||
       q.correction?.parent_job_id!==job.correction_plan.parent_job_id||
       q.correction?.parent_master_sha256!==job.correction_plan.parent_master_sha256||
       q.correction?.remarks_sha256!==job.correction_plan.remarks_sha256||
       q.master_sha256===job.correction_plan.parent_master_sha256||
       (!Number.isFinite(q.correction?.changed_mean_absolute_fraction)||
        q.correction.changed_mean_absolute_fraction<.003||
        q.correction.changed_mean_absolute_fraction>.99)||
       !Array.isArray(q.correction?.operations)||
       q.correction.operations.length<1||
       q.correction?.correction_status!=="APPLIED"||
       q.correction?.geometry_transform!==(geom?"CONTROLLED_PROJECTIVE_WARP":"IDENTITY")||
       (geom&&(q.correction?.perspective_corrected!==true||
               q.correction?.off_plane_geometry_review_required!==true||
               q.correction?.invalid_border_fraction>.001||
               !Number.isFinite(q.correction?.max_normalized_displacement)||
               q.correction.max_normalized_displacement>.18))||
       q.correction?.generative_processing!==false
     ))||
     q.geometry!==(geom?"CONTROLLED_PROJECTIVE_WARP":
        autoPerspective?"AUTO_VERTICAL_PERSPECTIVE_CORRECTION":
        autoRoll?"AUTO_CAMERA_ROLL_CORRECTION":
        autoOptics?"AUTO_LENS_PROFILE_CORRECTION":"IDENTITY_NO_CORRECTION")||
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
  const autoHold=!!(job.correction_plan&&processing?.status==="HOLD");
  const revision=q?await revisionLink(env,job.job_id):null;
  return {q,d,approved,imported,processing,revision,autoHold};
}
function sourceVerification(job,a){
  const q=a.q,checks=q?.qa_checks||{};
  // This is a truthful record of automated checks, NOT an approval of photo quality.
  const shaOK=checks.SOURCE_SHA256==="PASS"&&q?.source_sha256===job.source_sha256;
  const rasterOK=shaOK&&[8,16].includes(q?.source_signal_bits)&&
    Number.isInteger(q?.width)&&Number.isInteger(q?.height)&&
    q.width>=320&&q.height>=320;
  const autoLevelled=rasterOK&&checks.AUTO_GEOMETRY_CORRECTED==="PASS";
  const autoPerspective=rasterOK&&checks.AUTO_VERTICAL_PERSPECTIVE_CORRECTED==="PASS";
  const autoOptics=rasterOK&&checks.AUTO_OPTICS_CORRECTED==="PASS";
  const geometryModified=rasterOK&&checks.GEOMETRY_TRANSFORM_EVIDENCED==="PASS"&&
    q.geometry==="CONTROLLED_PROJECTIVE_WARP";
  const geometryUnchanged=rasterOK&&checks.GEOMETRY_UNCHANGED==="PASS"&&
    q.geometry==="IDENTITY_NO_CORRECTION";
  return {
    status:shaOK&&rasterOK?"TECHNICAL_PASS":q?"INCOMPLETE_EVIDENCE":"PENDING",
    sha256_status:shaOK?"PASS":q?"NOT_CONFIRMED":"PENDING",
    raster_status:rasterOK?"PASS":q?"NOT_CONFIRMED":"PENDING",
    geometry_preservation:geometryModified?"CORRECTED_PENDING_HUMAN_QA":
      autoPerspective?"AUTO_PERSPECTIVE_PENDING_HUMAN_QA":
      autoLevelled?"AUTO_LEVELLED_PENDING_HUMAN_QA":
      autoOptics?"AUTO_OPTICS_PENDING_HUMAN_QA":
      geometryUnchanged?"PASS":q?"NOT_CONFIRMED":"PENDING",
    mime:job.mime,source_bytes:job.source_bytes,
    source_sha256:job.source_sha256,
    source_kind:q?.source_kind||"RASTER",
    raw_decoder:q?.raw_decoder||null,
    width:rasterOK?q.width:null,height:rasterOK?q.height:null,
    signal_bits:rasterOK?q.source_signal_bits:null,
    alpha_handling:rasterOK?(q.alpha_handling||"none"):null,
    optical_correction:autoOptics?"PROFILE_APPLIED_PENDING_VISUAL_QA":
      q?.auto_optics_correction?.status==="HOLD"?"HOLD_UNCALIBRATED":
      q?.auto_optics_correction?.status==="NO_CHANGE_NEEDED"?"NO_CHANGE_NEEDED":"NOT_PERFORMED",
    perspective_correction:geometryModified?"CONTROLLED_PROJECTIVE_PENDING_VISUAL_QA":
      autoPerspective?"AUTO_VERTICAL_PERSPECTIVE_PENDING_VISUAL_QA":
      autoLevelled?"AUTO_CAMERA_ROLL_PENDING_VISUAL_QA":"NOT_PERFORMED",
    exposure_correction:q?.auto_photometric_correction?.exposure_status||"NOT_PERFORMED",
    color_correction:q?.auto_photometric_correction?.color_status||"NOT_PERFORMED",
    visual_review:a.d?.decision==="APPROVED"?"INCLUDED_IN_APPROVED_TECH_QA":
      a.d?.decision==="REWORK_REQUESTED"?"REWORK_REQUESTED":"PENDING_HUMAN_QA",
    verified_proof_sha:q?.master_sha256||null
  };
}
function state(job,a) {
  const status=a.autoHold&&a.revision?"REVISION_CREATED":
    a.d?.decision==="REWORK_REQUESTED"?(a.revision?"REVISION_CREATED":"REWORK_REQUESTED"):a.d?.decision==="REJECTED"?"REJECTED":a.imported?"MG4K_MASTER_IMPORTED":a.approved?"TECH_MASTER_APPROVED":a.q?"AWAITING_HUMAN_QA":a.processing?.status||job.status;
  return {
    ok:true,job_id:job.job_id,filename:job.filename,project:job.project,
    revision_num:job.revision_num||1,parent_job_id:job.parent_job_id||null,
    root_job_id:job.root_job_id||job.job_id,
    source_sha256:job.source_sha256,source_bytes:job.source_bytes,
    source_verification:sourceVerification(job,a),
    automatic_geometry_analysis:a.q?.auto_geometry_analysis?{
      status:a.q.auto_geometry_analysis.status,
      intent:a.q.auto_geometry_analysis.scene?.intent||"UNKNOWN",
      confidence:a.q.auto_geometry_analysis.scene?.confidence??null,
      vertical_lines:a.q.auto_geometry_analysis.vertical_inlier_count||0,
      vertical_tilt_deg:a.q.auto_geometry_analysis.verticals?.median_tilt_deg??null,
      tilt_status:a.q.auto_geometry_analysis.verticals?.status||"HOLD",
      correction_applied:a.q?.auto_geometry_correction?.status==="APPLIED",
      correction_status:a.q?.auto_geometry_correction?.status||"NOT_PERFORMED",
      roll_correction_deg:a.q?.auto_geometry_correction?.camera_roll_correction_deg??null,
      residual_after_deg:a.q?.auto_geometry_correction?.vertical_residual_after_deg??null,
      crop_fraction:a.q?.auto_geometry_correction?.crop_area_fraction??null,
      crop_quality:"GEOMETRIC_ONLY_NOT_SEMANTIC",
      hold_reason:a.q?.auto_geometry_correction?.reason||null
    }:null,
    technical_correction:job.correction_plan?{
      requested:true,categories:job.correction_plan.categories,
      status:a.q?.correction_status||a.processing?.status||"QUEUED",
      evidence:a.q?.correction||null,
      geometry_correction:job.correction_plan.type==="GEOMETRY_FACADE_QUAD_V1"?
        (a.q?.correction_status==="APPLIED"?"CONTROLLED_PENDING_VISUAL_QA":"PENDING"):"NOT_PERFORMED",
      source_sha256:job.source_sha256,
      parent_master_sha256:job.correction_plan.parent_master_sha256
    }:null,
    status,stage:a.q?"technical_qa_ready":a.processing?.stage||job.stage,
    progress:a.q?100:typeof a.processing?.progress==="number"?a.processing.progress:null,
    progress_kind:a.q?"TECHNICAL_STAGE_COMPLETE":a.processing?.status==="PROCESSING"?"TECHNICAL_STAGE_MEASURED":"NOT_MEASURED",
    message:a.d?.decision==="REWORK_REQUESTED"?
      "Мастер возвращён на доработку. Для геометрии укажите 4 угла фасада; для света и цвета доступен ограниченный корректор.":
      a.q?"Технический мастер готов; требуется визуальное утверждение QA.":a.processing?.message||job.message,
    error:a.processing?.error||null,
    technical_master_ready:!!a.q,technical_qa:a.q?{master_sha256:a.q.master_sha256,master_bytes:a.q.master_bytes,bit_depth:16,HDR_applied:false,source_kind:a.q.source_kind||"RASTER"}:null,
    locks:job.locks,approval:a.d?{decision:a.d.decision,at:a.d.at}:null,
    can_approve:!!a.q&&!a.d,can_reject:!!a.q&&!a.d,
    can_rework:!!a.q&&!a.d,
    can_upload_revision:(a.d?.decision==="REWORK_REQUESTED"||a.autoHold)&&!a.revision,
    can_auto_geometry_reprocess:!!a.q&&!a.q.auto_geometry_correction&&
      !a.d&&!a.revision&&(job.revision_num||1)===1,
    can_geometry_rework:!!a.q&&!a.revision&&(
      !a.d||(a.d.decision==="REWORK_REQUESTED"&&
      a.d.categories?.length===1&&a.d.categories[0]==="geometry")),
    can_auto_rework:a.d?.decision==="REWORK_REQUESTED"&&!a.revision&&
      Array.isArray(a.d.categories)&&a.d.categories.length>0&&
      a.d.categories.every(c=>c==="exposure"||c==="color"),
    auto_rework_hold_reason:a.d?.decision==="REWORK_REQUESTED"&&
      a.d.categories?.some(c=>c!=="exposure"&&c!=="color")?
      "Автоматическая коррекция перспективы, фактур и деталей требует проверенных опорных точек/профиля. HOLD.":"",
    rework:a.d?.decision==="REWORK_REQUESTED"?{categories:a.d.categories,note:a.d.note,at:a.d.at}:
      a.autoHold?{categories:job.correction_plan?.categories||[],
        note:"Автоматическая коррекция остановлена по защитному HOLD. Загрузите исправленный исходник, чтобы создать новую версию.",
        at:job.created_at}:null,
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
    if(a.q?.source_kind==="CAMERA_RAW"){
      const preview=await env.TROPICANA_EDITORIAL_R2.get(key(id,"technical/source-preview.jpg"));
      if(!preview||preview.size!==a.q.source_preview_bytes||
         preview.httpMetadata?.contentType!=="image/jpeg"||
         preview.customMetadata?.sha256!==a.q.source_preview_sha256||
         !preview.checksums?.sha256||hex(preview.checksums.sha256)!==a.q.source_preview_sha256)
        return json({error:"RAW_source_preview_integrity_HOLD"},409);
      return new Response(preview.body,{headers:{"content-type":"image/jpeg","cache-control":"private,no-store",
        "x-content-type-options":"nosniff","x-mg4k-source-representation":"RAW_NEUTRAL_PREVIEW",
        "x-mg4k-original-source-sha256":job.source_sha256}});
    }
    const src=await env.TROPICANA_EDITORIAL_R2.get(job.source_key);
    if(!src||src.size!==job.source_bytes||src.customMetadata?.sha256!==job.source_sha256||
       !["image/jpeg","image/png","image/webp"].includes(src.httpMetadata?.contentType))
      return json({error:"source_integrity_HOLD"},409);
    return new Response(src.body,{headers:{"content-type":job.mime,"cache-control":"private,no-store",
       "x-content-type-options":"nosniff","x-mg4k-source-representation":"ORIGINAL_RASTER"}});
  }
  if(path==="/api/editorial/job/auto-geometry"&&method==="POST"){
    // Non-destructive, no control points or rejection of the existing V1.
    // Create one independently reviewed child from the immutable source.
    if(!a.q||a.q.auto_geometry_correction||a.d||a.revision||
       (job.revision_num||1)!==1)
      return json({error:"auto_geometry_requires_legacy_V1_pending_QA"},409);
    if((job.revision_num||1)>=12)return json({error:"revision_limit_reached"},409);
    if(!env.TROPICANA_TECH_WORKFLOW)return json({error:"technical_workflow_missing"},503);
    const version=(job.revision_num||1)+1;
    const child=await createEditorialIntakeJob(env,{id:job.owner_session_id},{
      filename:job.filename,mime:job.mime,source_key:job.source_key,
      source_sha256:job.source_sha256,bytes:job.source_bytes,
      parent_job_id:id,root_job_id:job.root_job_id||id,revision_num:version,
      inherit_access_hash:job.access_hash,correction_plan:null
    });
    const link={
      schema:2,type:"AUTO_GEOMETRY_REPROCESS_V1",
      job_id:child.job_id,revision_num:version,
      source_sha256:job.source_sha256,source_unchanged:true,
      original_master_sha256:a.q.master_sha256,
      parent_decision_mutated:false,
      created_at:new Date().toISOString()
    };
    const result=await env.TROPICANA_EDITORIAL_R2.put(
      key(id,"rework/revision.json"),JSON.stringify(link),{
        onlyIf:new Headers({"If-None-Match":"*"}),
        httpMetadata:{contentType:"application/json"}
      });
    if(!result){
      await env.TROPICANA_EDITORIAL_R2.delete(key(child.job_id,"job.json"));
      const existing=await revisionLink(env,id);
      return json({error:"auto_geometry_revision_conflict",job_id:existing?.job_id||null},409);
    }
    let started=false,error=null;
    try{
      await env.TROPICANA_TECH_WORKFLOW.create({id:child.job_id,params:{job_id:child.job_id}});
      started=true;
    }catch(err){
      error=String(err?.message||err).slice(0,250);
      await env.TROPICANA_EDITORIAL_R2.put(key(child.job_id,"processing-state.json"),
        JSON.stringify({job_id:child.job_id,status:"HOLD",stage:"auto_geometry_dispatch_hold",
          progress:null,error,message:"Автокоррекция не запущена. Исходник и V1 сохранены."}),
        {httpMetadata:{contentType:"application/json"}});
    }
    return json({ok:true,job_id:child.job_id,parent_job_id:id,
      revision_num:version,source_unchanged:true,
      parent_decision_mutated:false,status:started?"QUEUED":"HOLD",
      workflow_started:started,generation_started:false,error},201);
  }

  if(path==="/api/editorial/job/rework/geometry"&&method==="POST"){
    const firstGeometryReturn=!a.d&&!!a.q;
    const previouslyReturned=a.d?.decision==="REWORK_REQUESTED"&&
      a.d.categories?.length===1&&a.d.categories[0]==="geometry";
    if(!a.q||(!firstGeometryReturn&&!previouslyReturned))
      return json({error:"geometry_master_not_ready_or_already_decided"},409);
    if(a.revision)return json({error:"revision_already_created",job_id:a.revision.job_id},409);
    let input;try{input=await request.json()}catch{return json({error:"invalid_geometry_input"},400)}
    if(input?.facade_assumption_confirmed!==true)
      return json({error:"rectangular_planar_facade_confirmation_required"},422);
    if(firstGeometryReturn&&(
      input.commit_parent_return!==true||
      input.master_sha256!==a.q.master_sha256||
      typeof input.note!=="string"||
      input.note.trim().length<12||input.note.trim().length>2000))
      return json({error:"explicit_geometry_return_confirmation_and_master_hash_required"},422);
    const pts=input?.source_quad_norm;
    if(!Array.isArray(pts)||pts.length!==4||
       !pts.every(p=>Array.isArray(p)&&p.length===2&&p.every(n=>
         typeof n==="number"&&Number.isFinite(n)&&n>=.015&&n<=.985)))
      return json({error:"four_valid_normalized_facade_corners_required"},422);
    const [tl,tr,br,bl]=pts;
    const cross=(a,b,c)=>(b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0]);
    if(!(tl[0]<tr[0]&&bl[0]<br[0]&&tl[1]<bl[1]&&tr[1]<br[1])||
       pts.some((p,i)=>cross(p,pts[(i+1)%4],pts[(i+2)%4])<=.0025))
      return json({error:"invalid_quad_order_or_nonconvex_geometry"},422);
    const lx=(tl[0]+bl[0])/2,rx=(tr[0]+br[0])/2;
    const ty=(tl[1]+tr[1])/2,by=(bl[1]+br[1])/2;
    const area=Math.abs(pts.reduce((z,p,i)=>z+
      p[0]*pts[(i+1)%4][1]-p[1]*pts[(i+1)%4][0],0)/2);
    if(area<.035||area>.78||rx-lx<.22||by-ty<.18||
       Math.max(Math.abs(tl[0]-bl[0]),Math.abs(tr[0]-br[0]),
        Math.abs(tl[1]-tr[1]),Math.abs(bl[1]-br[1]))>.17)
      return json({error:"facade_quad_outside_safe_limits"},422);
    if(!env.TROPICANA_TECH_WORKFLOW)return json({error:"technical_workflow_missing"},503);
    const v=(job.revision_num||1)+1;
    if(v>12)return json({error:"revision_limit_reached"},409);
    const note=firstGeometryReturn?input.note.trim():a.d.note;
    const noteHash=await hash(new TextEncoder().encode(note));
    let returnRecord=a.d;
    if(firstGeometryReturn){
      returnRecord={
        schema:1,job_id:id,decision:"REWORK_REQUESTED",
        source_sha256:job.source_sha256,master_sha256:a.q.master_sha256,
        at:new Date().toISOString(),owner_session_id:owner.session.id,
        visual_qa_checks:null,categories:["geometry"],note,
        revision_num:job.revision_num||1
      };
      const saved=await env.TROPICANA_EDITORIAL_R2.put(
        key(id,"technical/decision.json"),JSON.stringify(returnRecord),{
          onlyIf:new Headers({"If-None-Match":"*"}),
          httpMetadata:{contentType:"application/json"}
        });
      if(!saved)return json({error:"geometry_return_conflict_retry"},409);
    }
    const plan={schema:1,type:"GEOMETRY_FACADE_QUAD_V1",parent_job_id:id,
      parent_master_sha256:returnRecord.master_sha256,remarks_sha256:noteHash,
      categories:["geometry"],HDR_allowed:false,
      geometry_transform:"CONTROLLED_PROJECTIVE_WARP",
      allowed_algorithm:"HUMAN_ANCHORED_FACADE_HOMOGRAPHY",
      source_quad_norm:pts.map(p=>p.map(x=>Math.round(x*1e7)/1e7)),
      facade_assumption_confirmed:true,
      no_outpainting:true,human_approval_required:true,
      source_dimensions:[a.q.width,a.q.height]};
    const child=await createEditorialIntakeJob(env,{id:job.owner_session_id},{
      filename:job.filename,mime:job.mime,source_key:job.source_key,
      source_sha256:job.source_sha256,bytes:job.source_bytes,
      parent_job_id:id,root_job_id:job.root_job_id||id,revision_num:v,
      inherit_access_hash:job.access_hash,correction_plan:plan
    });
    const link={schema:2,job_id:child.job_id,revision_num:v,
      source_sha256:job.source_sha256,source_unchanged:true,
      correction_type:plan.type,source_quad_norm:plan.source_quad_norm,
      parent_master_sha256:returnRecord.master_sha256,remarks_sha256:noteHash,
      rework_decision_at:returnRecord.at,created_at:new Date().toISOString()};
    const written=await env.TROPICANA_EDITORIAL_R2.put(
      key(id,"rework/revision.json"),JSON.stringify(link),{
        onlyIf:new Headers({"If-None-Match":"*"}),
        httpMetadata:{contentType:"application/json"}
      });
    if(!written){
      await env.TROPICANA_EDITORIAL_R2.delete(key(child.job_id,"job.json"));
      const existing=await revisionLink(env,id);
      return json({error:"revision_race_conflict",job_id:existing?.job_id||null},409);
    }
    let started=false,error=null;
    try{
      await env.TROPICANA_TECH_WORKFLOW.create({id:child.job_id,params:{job_id:child.job_id}});
      started=true;
    }catch(err){
      error=String(err?.message||err).slice(0,250);
      await env.TROPICANA_EDITORIAL_R2.put(key(child.job_id,"processing-state.json"),
        JSON.stringify({job_id:child.job_id,status:"HOLD",stage:"geometry_dispatch_hold",
          progress:null,error,message:"Коррекция геометрии не запущена. Исходник сохранён."}),
        {httpMetadata:{contentType:"application/json"}});
    }
    return json({ok:true,job_id:child.job_id,parent_job_id:id,revision_num:v,
      status:started?"QUEUED":"HOLD",workflow_started:started,
      correction_type:plan.type,source_unchanged:true,
      corrected_master_ready:false,generation_started:false,error},201);
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
          message:"Технический процессор не запущен. Генеративные кредиты не расходуются."}),
        {httpMetadata:{contentType:"application/json"}});
    }
    return json({ok:true,job_id:child.job_id,parent_job_id:id,
      revision_num:version,status:started?"QUEUED":"HOLD",
      workflow_started:started,source_unchanged:true,
      correction_type:"DETERMINISTIC_GLOBAL_PHOTOMETRY",
      corrected_master_ready:false,generation_started:false,error},201);
  }

  if(path==="/api/editorial/job/revision"&&method==="POST"){
    if(a.d?.decision!=="REWORK_REQUESTED"&&!a.autoHold)
      return json({error:"rework_decision_or_auto_hold_required"},409);
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
      parent_master_sha256:a.d?.master_sha256||job.correction_plan?.parent_master_sha256,
      rework_decision_at:a.d?.at||null,
      fallback_from_auto_hold:a.autoHold,
      created_at:new Date().toISOString()};
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
