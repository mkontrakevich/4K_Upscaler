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

async function update(env,id,s,previewOnly=false){
  await env.TROPICANA_EDITORIAL_R2.put(ROOT(id)+(previewOnly?"preview-state.json":"processing-state.json"),
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
async function start(env,id,job,previewOnly=false){
  const input=await env.TROPICANA_EDITORIAL_R2.get(job.source_key);
  if(!input||input.size!==job.source_bytes||!input.body)throw Error("source_missing_or_size_changed");
  if(input.customMetadata?.sha256!==job.source_sha256)throw Error("source_SHA_metadata_mismatch");
  if(input.checksums?.sha256&&hex(input.checksums.sha256)!==job.source_sha256)
    throw Error("source_R2_checksum_mismatch");
  const c=await processor(env);
  const {readable,writable}=new FixedLengthStream(input.size);
  const copy=input.body.pipeTo(writable);
  const request=new Request(endpoint(id,previewOnly?"preview-start":"start"),{
    method:"POST",body:readable,headers:{
      "content-type":job.mime,
      "x-source-sha256":job.source_sha256,
      "x-source-filename":encodeURIComponent(job.filename),"x-mg4k-source-size":String(input.size),
      ...(job.correction_plan?{"x-mg4k-correction-plan":encodeURIComponent(JSON.stringify(job.correction_plan))}:{})
    }
  });
  const fetchPromise=c.fetch(request);
  const [res]=await Promise.all([fetchPromise,copy]);
  const data=await res.json().catch(()=>({}));
  if(!res.ok)throw Error("technical_start_"+res.status+":"+String(data.error||"unknown"));
  return {status:String(data.status||"QUEUED")};
}
async function status(env,id,previewOnly=false){
  const c=await processor(env);
  const r=await c.fetch(new Request(endpoint(id,previewOnly?"preview-status":"status")));
  if(!r.ok)throw Error("technical_status_http_"+r.status);
  return r.json();
}
async function proof(env,id,job){
  const c=await processor(env);
  const r=await c.fetch(new Request(endpoint(id,"qa")));
  if(!r.ok)throw Error("technical_QA_missing_"+r.status);
  const qa=await r.json();
  const geometryPlan=job.correction_plan?.type==="GEOMETRY_FACADE_QUAD_V1";
  const ao=qa.auto_optics_correction,ag=qa.auto_geometry_correction;
  const ap=qa.auto_vertical_perspective_correction,ph=qa.auto_photometric_correction;
  const autoOptics=!job.correction_plan&&ao?.status==="APPLIED";
  const autoRoll=!job.correction_plan&&ag?.status==="APPLIED";
  const autoPerspective=!job.correction_plan&&ap?.status==="APPLIED";
  const autoSpatial=autoOptics||autoRoll||autoPerspective;
  if(qa.job_id!==id||qa.source_sha256!==job.source_sha256||
     qa.technical_status!=="TECHNICAL_QA_PASS"||qa.master_bit_depth!==16||
     qa.HDR_applied!==false||qa.virtual_exposures!==false||qa.local_HDR!==false||
     qa.qa_checks?.HDR_ABSENT!=="PASS"||
     qa.qa_checks?.SOURCE_SHA256!=="PASS"||
     !["RASTER","CAMERA_RAW"].includes(String(qa.source_kind||""))||
     (qa.source_kind==="CAMERA_RAW"&&(
       qa.raw_decoder!=="rawpy-0.28.0/LibRaw"||
       qa.qa_checks?.RAW_CAMERA_WB_APPLIED!=="PASS"||
       !Array.isArray(qa.raw_camera_white_balance)||qa.raw_camera_white_balance.length<3||
       qa.base_color_management?.status!=="CAMERA_WB_APPLIED"||
       !Number.isInteger(qa.source_preview_bytes)||qa.source_preview_bytes<10000||
       !SHA.test(String(qa.source_preview_sha256||""))
     ))||
     qa.qa_checks?.TIFF_READBACK!=="PASS"||
     (geometryPlan?
       (qa.qa_checks?.GEOMETRY_TRANSFORM_EVIDENCED!=="PASS"||
        qa.qa_checks?.GEOMETRY_UNCHANGED!=="NOT_APPLICABLE"||
        qa.geometry!=="CONTROLLED_PROJECTIVE_WARP"):
       autoSpatial?
       (qa.qa_checks?.GEOMETRY_TRANSFORM_EVIDENCED!=="PASS"||
        qa.qa_checks?.GEOMETRY_UNCHANGED!=="NOT_APPLICABLE"||
        !["AUTO_LENS_PROFILE_CORRECTION","AUTO_CAMERA_ROLL_CORRECTION","AUTO_VERTICAL_PERSPECTIVE_CORRECTION"].includes(qa.geometry)):
       (qa.qa_checks?.GEOMETRY_UNCHANGED!=="PASS"||
        qa.geometry!=="IDENTITY_NO_CORRECTION"))||
     (!job.correction_plan&&qa.technical_version!=="AUTO_TECH_MASTER_V2")||
     (!job.correction_plan&&(!ao||!ag||!ap||!ph))||
     (autoOptics&&(
       qa.qa_checks?.AUTO_OPTICS_CORRECTED!=="PASS"||
       ao?.engine!=="AUTO_PROFILED_OPTICS_V1"||ao?.HDR_applied!==false||
       ao?.generative_processing!==false||!ao?.profile_id||
       !Number.isFinite(ao?.crop_area_fraction)||ao.crop_area_fraction<0||ao.crop_area_fraction>.14||
       !Number.isFinite(ao?.invalid_source_fraction)||ao.invalid_source_fraction>.00002||
       !Number.isFinite(ao?.changed_mean_absolute_fraction)||ao.changed_mean_absolute_fraction<.0005
     ))||
     (autoRoll&&(
       qa.qa_checks?.AUTO_GEOMETRY_CORRECTED!=="PASS"||
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
       qa.qa_checks?.AUTO_VERTICAL_PERSPECTIVE_CORRECTED!=="PASS"||
       ap?.engine!=="AUTO_VERTICAL_PERSPECTIVE_V1"||
       ap?.geometry_transform!=="AUTO_VERTICAL_PERSPECTIVE_CORRECTION"||
       ap?.HDR_applied!==false||ap?.generative_processing!==false||
       !Number.isFinite(ap?.crop_area_fraction)||ap.crop_area_fraction<0||ap.crop_area_fraction>.30||
       !Number.isFinite(ap?.invalid_border_fraction)||ap.invalid_border_fraction>.00002||
       !Number.isFinite(ap?.max_corner_drift_fraction)||ap.max_corner_drift_fraction>.19||
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
     !SHA.test(qa.master_sha256||"")||
     !Number.isInteger(qa.master_bytes)||qa.master_bytes<200000||
     (job.correction_plan&&(
       qa.correction_status!=="APPLIED"||
       qa.correction?.parent_job_id!==job.correction_plan.parent_job_id||
       qa.correction?.parent_master_sha256!==job.correction_plan.parent_master_sha256||
       qa.correction?.remarks_sha256!==job.correction_plan.remarks_sha256||
       qa.master_sha256===job.correction_plan.parent_master_sha256||
       (!Number.isFinite(qa.correction?.changed_mean_absolute_fraction)||
        qa.correction.changed_mean_absolute_fraction<.003||
        qa.correction.changed_mean_absolute_fraction>.99)||
       !Array.isArray(qa.correction?.operations)||
       qa.correction.operations.length<1||
       qa.correction?.correction_status!=="APPLIED"||
       qa.correction?.geometry_transform!==(geometryPlan?
         "CONTROLLED_PROJECTIVE_WARP":"IDENTITY")||
       (geometryPlan&&(qa.correction?.perspective_corrected!==true||
         qa.correction?.off_plane_geometry_review_required!==true||
         qa.correction?.source_quad_norm?.length!==4||
         qa.correction?.invalid_border_fraction>.001||
         !Number.isFinite(qa.correction?.max_normalized_displacement)||
         qa.correction.max_normalized_displacement>.18))||
       qa.correction?.HDR_applied!==false||
       qa.correction?.generative_processing!==false
     )))
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

  if(qa.source_kind==="CAMERA_RAW"){
    if(!Number.isInteger(qa.source_preview_bytes)||qa.source_preview_bytes<10000||
       !SHA.test(String(qa.source_preview_sha256||"")))
      throw Error("RAW_source_preview_QA_missing");
    const sourceView=await c.fetch(new Request(endpoint(id,"source-preview")));
    if(!sourceView.ok||sourceView.headers.get("content-type")!=="image/jpeg"||
       Number(sourceView.headers.get("content-length"))!==qa.source_preview_bytes)
      throw Error("RAW_source_preview_invalid");
    await persistKnownLength(env,ROOT(id)+"technical/source-preview.jpg",sourceView,qa.source_preview_bytes,{
      onlyIf:new Headers({"If-None-Match":"*"}),sha256:fromHex(qa.source_preview_sha256),
      httpMetadata:{contentType:"image/jpeg"},
      customMetadata:{sha256:qa.source_preview_sha256,source_sha256:job.source_sha256,kind:"raw_neutral_source_preview"}
    });
  }
  await env.TROPICANA_EDITORIAL_R2.put(ROOT(id)+"technical/qa.json",JSON.stringify(qa),{
    onlyIf:new Headers({"If-None-Match":"*"}),httpMetadata:{contentType:"application/json"}
  });
  return {master_sha256:qa.master_sha256,master_bytes:qa.master_bytes,
          signal_bits:qa.source_signal_bits,geometry:qa.geometry};
}

async function runSourcePreview(env,id,job,step){
  // Thumbnail extraction is a separate, free, non-generative operation.
  // The technical master Workflow must never be invoked on upload.
  if(job.mime!=="application/x-camera-raw"||job.correction_plan)
    throw Error("preview_only_requires_original_camera_raw");
  await update(env,id,{status:"PROCESSING",stage:"RAW_thumbnail_only",
    message:"Извлекается превью RAW; техническая коррекция ещё не запущена."},true);
  await step.do("transfer-source-for-preview",()=>start(env,id,job,true));
  let ready=null;
  for(let n=0;n<48;n++){
    await step.sleep("preview-wait-"+n,"2 seconds");
    const state=await step.do("preview-poll-"+n,()=>status(env,id,true));
    if(state.status==="READY"){ready=state;break}
    if(state.status==="FAILED")throw Error("RAW_thumbnail_failed:"+String(state.error||"unknown"));
  }
  if(!ready)throw Error("RAW_thumbnail_timeout");
  if(!Number.isSafeInteger(ready.preview_bytes)||ready.preview_bytes<10000||
     !SHA.test(String(ready.preview_sha256||""))||
     ready.source_sha256!==job.source_sha256||
     ready.preview_kind!=="RAW_CAMERA_THUMBNAIL_NOT_TECH_MASTER")
    throw Error("RAW_thumbnail_QA_invalid");
  const c=await processor(env);
  const image=await c.fetch(new Request(endpoint(id,"preview-image")));
  if(!image.ok||image.headers.get("content-type")!=="image/jpeg"||
     Number(image.headers.get("content-length"))!==ready.preview_bytes)
    throw Error("RAW_thumbnail_response_invalid");
  const objectKey=ROOT(id)+"preview/source-preview.jpg";
  await persistKnownLength(env,objectKey,image,ready.preview_bytes,{
    onlyIf:new Headers({"If-None-Match":"*"}),sha256:fromHex(ready.preview_sha256),
    httpMetadata:{contentType:"image/jpeg"},
    customMetadata:{sha256:ready.preview_sha256,source_sha256:job.source_sha256,
      kind:"RAW_PREVIEW_ONLY_NO_CORRECTION"}
  });
  const head=await env.TROPICANA_EDITORIAL_R2.head(objectKey);
  if(!head||head.size!==ready.preview_bytes||
     head.customMetadata?.sha256!==ready.preview_sha256||
     !head.checksums?.sha256||hex(head.checksums.sha256)!==ready.preview_sha256)
    throw Error("RAW_preview_R2_integrity_HOLD");
  await update(env,id,{status:"READY",stage:"source_preview_ready",
    source_sha256:job.source_sha256,preview_sha256:ready.preview_sha256,
    preview_bytes:ready.preview_bytes,generation_started:false,
    message:"Превью загружено. Анализ и коррекция — только по вашей кнопке."},true);
  return {job_id:id,status:"SOURCE_PREVIEW_READY",technical_processing_started:false};
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
        if(j.job_id!==id||!SHA.test(j.source_sha256)||j.source_bytes>90*1024*1024)
          throw Error("technical_job_invalid");
        return {source_key:j.source_key,source_sha256:j.source_sha256,source_bytes:j.source_bytes,
                filename:j.filename,mime:j.mime,
                correction_plan:j.correction_plan||null};
      });
      if(payload.preview_only===true)
        return await runSourcePreview(env,id,job,step);
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
        if(state.status==="HOLD"){
          const reason=String(state.error||"technical_correction_requires_review").slice(0,240);
          await update(env,id,{status:"HOLD",stage:"correction_hold",progress:null,
            message:"Безопасная автоматическая коррекция остановлена: "+reason,
            error:reason});
          return {job_id:id,status:"HOLD",reason,generation_started:false};
        }
        if(state.status==="NOT_FOUND")await step.do("restart-technical-"+n,()=>start(env,id,job));
      }
      if(!ready)throw Error("technical_processing_timeout");
      const qa=await step.do("validate-technical-QA",()=>proof(env,id,job));
      const saved=await step.do("persist-verified-technical-master",()=>persist(env,id,job,qa));
      await update(env,id,{status:"AWAITING_HUMAN_QA",stage:"technical_qa_ready",progress:100,
        message:job.correction_plan?.type==="GEOMETRY_FACADE_QUAD_V1"?
          "Перспектива фасада скорректирована по четырём точкам. Проверьте здание и объекты вне фасада перед утверждением.":
          job.correction_plan?
          "Исправленная V2 готова: "+job.correction_plan.categories.join(", ")+
          ". Проверьте изменения визуально, автоматически не утверждается.":
          qa.auto_geometry_correction?.status==="APPLIED"?
          "Камера выровнена автоматически, границы безопасно обрезаны. Проверьте сплит, архитектуру и композицию перед утверждением.":
          "Технический 16-битный TIFF готов. Проверка Auto Perspective не означает выполненную коррекцию.",
        technical_master_sha256:saved.master_sha256});
      return {job_id:id,status:"AWAITING_HUMAN_QA",...saved};
    }catch(error){
      if(payload.preview_only===true){
        await update(env,id,{status:"FAILED",stage:"raw_thumbnail_failed",
          error:String(error?.message||error).slice(0,500),
          message:"Превью RAW недоступно. Коррекция не запускалась."},true);
      }else{
        await update(env,id,{status:"FAILED",stage:"technical_failed",progress:null,
          error:String(error?.message||error).slice(0,1000),
          message:"Техническая обработка завершилась ошибкой. Генерация заблокирована."});
      }
      throw error;
    }
  }
}
