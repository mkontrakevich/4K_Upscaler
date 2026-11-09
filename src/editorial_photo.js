import { editorialJobApi, createEditorialIntakeJob } from "./editorial_jobs.js";
import { WorkflowEntrypoint } from "cloudflare:workers";
import { NonRetryableError } from "cloudflare:workflows";

const PROJECT="tropicana-park", FRAME="DSC09818";
const ROOT=PROJECT+"/"+FRAME+"/";
const MAX_SOURCE=8*1024*1024;
const MODEL="google/gemini-3.1-flash-image";
const AUDIT_MODEL="google/gemini-3-flash-preview";
const j=(x,s=200)=>Response.json(x,{status:s,headers:{"cache-control":"no-store"}});
const fileKey=(id,kind)=>"runs/"+id+"/"+kind;
const isId=id=>/^[0-9a-f]{8}-[0-9a-f-]{27,40}$/i.test(String(id||""));
const sha256=async buffer=>[...new Uint8Array(await crypto.subtle.digest("SHA-256",buffer))].map(x=>x.toString(16).padStart(2,"0")).join("");
function b64(bytes){let s="";const a=new Uint8Array(bytes);for(let p=0;p<a.length;p+=16384)s+=String.fromCharCode(...a.subarray(p,p+16384));return btoa(s)}
function binary(base64){const s=atob(base64);const arr=new Uint8Array(s.length);for(let i=0;i<s.length;i++)arr[i]=s.charCodeAt(i);return arr}
function imageUrl(b,mime="image/jpeg"){return "data:"+mime+";base64,"+b64(b)}
function sourceName(key){return typeof key==="string"&&key.startsWith(ROOT)&&/^[a-zA-Z0-9/_\-.]{10,150}$/.test(key)&&!key.includes("..")&&/\.(jpe?g|png|webp)$/i.test(key)}
async function object(env,key,maxBytes=MAX_SOURCE){
 const o=await env.TROPICANA_EDITORIAL_R2.get(key);
 if(!o)throw new Error("editorial_source_not_found");
 if(o.size>maxBytes)throw new Error("editorial_source_too_large");
 const bytes=await o.arrayBuffer();
 return {bytes,mime:o.httpMetadata?.contentType||"image/jpeg",sha:await sha256(bytes),size:bytes.byteLength};
}
async function openrouter(env,path,payload){
 if(!env.OPENROUTER_API_KEY)throw new Error("openrouter_key_missing");
 const res=await fetch("https://openrouter.ai/api/v1"+path,{
  method:"POST",headers:{
   Authorization:"Bearer "+env.OPENROUTER_API_KEY,
   "Content-Type":"application/json",
   "HTTP-Referer":"https://4k-upscaler.kontrakevich.workers.dev/",
   "X-Title":"MG 4K — Editorial Photo / Tropicana"
  },body:JSON.stringify(payload)
 });
 const data=await res.json().catch(()=>({}));
 if(!res.ok)throw new Error("openrouter_http_"+res.status+":"+String(data?.error?.message||data?.message||"error").slice(0,180));
 return data;
}
const EDIT_PROMPT=[
"IMAGE-TO-IMAGE EDIT ONLY. The attached DSC09818 source is the technically corrected Sony RAW derivative V10: true RAW neutral display exposure, optical lens correction, PerspectiveLock and controlled ISO3200 denoising. NO HDR, NO virtual exposures, NO HDR-like relighting were applied. Preserve this scene and its geometry precisely. Do not redesign any object.",
"Preserve EXACT camera position and framing; same glass and metal railing, same door, bar stools, tables, floor tiles, brick, ceiling beams, plants, true skyline and harbor lights. Do not add furniture, decorative objects, lighting fixtures, railings or new buildings.",
"Creative direction: premium European hospitality editorial at authentic late-night blue-hour. Retouch only the original recorded warm interior light and the real faint distant city/bay lights. Do not brighten pure-black sky into invented scenery. Keep physically plausible shadow signal and metal, brick and glass detail. Correct muddy cyan cast, reduce chroma noise, protect natural black sky and glass reflections, refine controlled amber-vs-deep-blue balance.",
"Do not synthesize a different sky or sunset, city layout, yachts, flowers, or people. Preserve original weather and honest night conditions. No fantasy architecture, no deformed typography or people. Do not repaint blurred areas into invented detail.",
"Keep physically consistent vanishing lines and original architectural verticals, no new viewpoint. Output a conservative editorial candidate for comparison and manual fidelity QA, NOT a new illustration."
].join("\n");
const AUDIT_PROMPT="Compare the provided REAL source photo (first image) with the EDITORIAL candidate (second). Respond as JSON object with keys changed_architecture (boolean), changed_furniture (boolean), changed_city_view (boolean), changed_geometry (boolean), exposure_quality (string), editorial_quality (string), issues (array strings), recommendation (PASS or HOLD). Be strict; anything unverified should be HOLD. Ignore prettier lighting and assess documentary identity.";
export class EditorialPhotoWorkflow extends WorkflowEntrypoint{
 async run(event,step){
  const params=typeof event.payload==="string"?JSON.parse(event.payload):event.payload||{};
  const id=String(params.job_id||"");
  const sourceKey=String(params.source_key||"");
  // The only source of future generative improvements: TECH V10 neutral with NO HDR.
  // The previous V9 model candidate remains historical, and is never a valid new input.
  if(!isId(id)||sourceKey!==ROOT+"technical-v10/DSC09818_EDITORIAL_INPUT.jpg")
    throw new NonRetryableError("technical_v10_neutral_source_required");
  const qaObject=await this.env.TROPICANA_EDITORIAL_R2.get(ROOT+"technical-v10/DSC09818_TECH_QA.json");
  if(!qaObject)throw new NonRetryableError("technical_v10_QA_missing");
  const masterQA=await qaObject.json();
  if(masterQA.source_sha256!=="1b4344f4b8ca629830254850fbcbaf2931c83c00a158ab87d84e5ee7722046d5"||
     masterQA.technical_version!=="V10_NEUTRAL_NO_HDR"||
     masterQA.technical_status!=="TECHNICAL_QA_PASS"||
     masterQA.qa_checks?.HDR_ABSENT!=="PASS"||
     masterQA.HDR_applied!==false||masterQA.HDR_bracketed!==false||
     masterQA.virtual_exposures!==false||masterQA.local_HDR!==false||
     masterQA.adaptive_tone_recovery!==false||masterQA.no_generated_pixels!==true||
     !masterQA.clean16_sha256||!masterQA.editorial_input_sha256)
    throw new NonRetryableError("technical_v10_HOLDS_or_HDR_detected");
  // Automated processing QA PASS is not owner's visual signoff.
  // Approval must be explicitly recorded after full-resolution RAW vs TECH V10 inspection.
  const approvalObject=await this.env.TROPICANA_EDITORIAL_R2.get(ROOT+"technical-v10/qa-approval.json");
  if(!approvalObject)throw new NonRetryableError("technical_v10_human_QA_approval_required");
  const release=await approvalObject.json();
  if(release.qa_decision!=="APPROVED"||release.approved!==true||
     release.frame!==FRAME||release.technical_version!=="V10_NEUTRAL_NO_HDR"||
     release.scope!=="ONE_ARTISTIC_OPENROUTER_CANDIDATE_ONLY"||
     release.authorized_job_id!==id||release.approved_for_publishing!==false||
     release.original_raw_sha256!==masterQA.source_sha256||
     release.technical_master_sha256!==masterQA.clean16_sha256||
     release.editorial_input_sha256!==masterQA.editorial_input_sha256)
    throw new NonRetryableError("technical_v10_approval_or_sha_invalid");
  const masterHead=await this.env.TROPICANA_EDITORIAL_R2.head(ROOT+"technical-v10/DSC09818_TECH_CLEAN16.tif");
  if(!masterHead||masterHead.size!==masterQA.clean16_size||masterHead.size<50000000)
    throw new NonRetryableError("technical_v10_fullsize_master_missing");
  const runRoot=fileKey(id,"");
  // Enforce exactly one paid candidate; an already saved result must never be purchased again.
  const [priorManifest,priorCandidate]=await Promise.all([
    this.env.TROPICANA_EDITORIAL_R2.head(runRoot+"manifest.json"),
    this.env.TROPICANA_EDITORIAL_R2.head(runRoot+"editorial_candidate.png")
  ]);
  if(priorManifest||priorCandidate)throw new NonRetryableError("one_candidate_already_generated");
  const source=await step.do("validate-source-v10",async()=>{
    const o=await object(this.env,sourceKey);
    if(o.sha!==masterQA.editorial_input_sha256)throw new NonRetryableError("editorial_model_input_sha_mismatch");
    await this.env.TROPICANA_EDITORIAL_R2.put(runRoot+"technical_master_QA.json",JSON.stringify(masterQA),{httpMetadata:{contentType:"application/json"}});
    await this.env.TROPICANA_EDITORIAL_R2.put(runRoot+"editorial_transition_release.json",JSON.stringify(release),{httpMetadata:{contentType:"application/json"}});
    await this.env.TROPICANA_EDITORIAL_R2.put(runRoot+"source_manifest.json",JSON.stringify({
      project:PROJECT,frame:FRAME,technical_master:"TECH_V10_NEUTRAL_NO_HDR",source_key:sourceKey,source_sha256:o.sha,source_bytes:o.size,
      clean_16bit_master_sha256:masterQA.clean16_sha256,original_RAW_sha256:masterQA.source_sha256,
      source_mime:o.mime,created_at:new Date().toISOString(),status:"INPUT_VALIDATED",
      skill:"MARINS-Architectural-RAW-Photo-v1.13",geometry_lock:"HARD",fidelity_lock:"HARD",
      HDR_applied:false,virtual_exposures:false,human_QA_approved:true,owner_authorized_one_candidate:true
    }),{httpMetadata:{contentType:"application/json"}});
    return {sha256:o.sha,size:o.size,mime:o.mime,master_sha256:masterQA.clean16_sha256};
  });
  const analysis=await step.do("scene-editorial-analysis",async()=>{try{
    const o=await object(this.env,sourceKey);
    const res=await openrouter(this.env,"/chat/completions",{
      model:String(this.env.EDITORIAL_VISION_MODEL||AUDIT_MODEL),
      temperature:0.1,max_tokens:1200,
      messages:[{role:"system",content:"You are a senior architectural photo editor. Describe the real image; distinguish objective facts from artistic proposals. No invented architecture. Answer compact Russian."},{role:"user",content:[{type:"text",text:"Analyze original DSC09818, camera geometry, sky, glass, lights, textures; list safe image-to-image editorial improvements."},{type:"image_url",image_url:{url:imageUrl(o.bytes,o.mime)}}]}]
    });
    const text=String(res.choices?.[0]?.message?.content||"");
    await this.env.TROPICANA_EDITORIAL_R2.put(runRoot+"scene_analysis.json",JSON.stringify({model:this.env.EDITORIAL_VISION_MODEL||AUDIT_MODEL,analysis:text,usage:res.usage||null}),{httpMetadata:{contentType:"application/json"}});
    return {chars:text.length};
  }catch(error){throw new NonRetryableError(String(error))}
  });
  const candidate=await step.do("image-to-image-openrouter",async()=>{try{
    const o=await object(this.env,sourceKey);
    const result=await openrouter(this.env,"/images",{
      model:String(this.env.EDITORIAL_IMAGE_MODEL||MODEL),
      prompt:EDIT_PROMPT,
      input_references:[{type:"image_url",image_url:{url:imageUrl(o.bytes,o.mime)}}],
      aspect_ratio:"3:2",output_format:"png",n:1
    });
    const image=result.data?.[0];if(!image?.b64_json)throw new Error("no_image_returned");
    const bytes=binary(image.b64_json);
    if(bytes.byteLength<10000||bytes.byteLength>20*1024*1024)throw new Error("editorial_candidate_size_invalid");
    const key=runRoot+"editorial_candidate.png";
    const sha=await sha256(bytes);
    await this.env.TROPICANA_EDITORIAL_R2.put(key,bytes,{httpMetadata:{contentType:"image/png"},customMetadata:{source_sha256:source.sha256,sha256:sha,model:String(this.env.EDITORIAL_IMAGE_MODEL||MODEL),status:"REVIEW_ONLY"}});
    await this.env.TROPICANA_EDITORIAL_R2.put(runRoot+"model_cost.json",JSON.stringify({usage:result.usage||null,model:this.env.EDITORIAL_IMAGE_MODEL||MODEL,sha256:sha,bytes:bytes.byteLength}),{httpMetadata:{contentType:"application/json"}});
    return {key,sha256:sha,size:bytes.byteLength};
  }catch(error){throw new NonRetryableError(String(error))}
  });
  const audit=await step.do("post-generative-source-fidelity-qa",async()=>{try{
    const a=await object(this.env,sourceKey),b=await object(this.env,candidate.key,20*1024*1024);
    const res=await openrouter(this.env,"/chat/completions",{
      model:String(this.env.EDITORIAL_VISION_MODEL||AUDIT_MODEL),temperature:0.1,max_tokens:1800,
      messages:[{role:"system",content:"Strict architectural photo authenticity QA. Prefer HOLD if uncertain. Output only compact JSON."},{role:"user",content:[{type:"text",text:AUDIT_PROMPT},{type:"image_url",image_url:{url:imageUrl(a.bytes,a.mime)}},{type:"image_url",image_url:{url:imageUrl(b.bytes,b.mime)}}]}]
    });
    const answer=String(res.choices?.[0]?.message?.content||"");
    await this.env.TROPICANA_EDITORIAL_R2.put(runRoot+"fidelity_qa.json",JSON.stringify({model:this.env.EDITORIAL_VISION_MODEL||AUDIT_MODEL,audit:answer,usage:res.usage||null,approved:false}),{httpMetadata:{contentType:"application/json"}});
    return {length:answer.length};
  }catch(error){throw new NonRetryableError(String(error))}
  });
  const manifest={
   project:PROJECT,frame:FRAME,job_id:id,status:"EDITORIAL_REVIEW",final:false,
   source_key:sourceKey,source_sha256:source.sha256,source_bytes:source.size,
   technical_raw_master:"Actual 16bit 6536x4360 real RAW TECH V10 neutral NO HDR stored in isolated R2",
   geometry_input:"One-pass PTLens 16mm + V3 PerspectiveLock from 16bit original Sony RAW; TECH V10 NO HDR",
   candidate_key:candidate.key,candidate_sha256:candidate.sha256,
   gates:{source_integrity:"PASS",raw_technical_v10:"PASS",hdr_absent:"PASS",human_technical_QA:"APPROVED",optical_distortion:"PROFILE_APPLIED_REQUIRES_REVIEW",pre_generation_geometry:"PASS",post_generation_geometry:"PENDING_NUMERIC_QA",editorial_visual:"REVIEW",geometry_fidelity:"REVIEW",manual_approval:"REQUIRED",full_resolution_master:"REQUIRED"},
   steps:{analysis,candidate,audit},skill:"MARINS Architectural RAW Photo v1.13",
   model:this.env.EDITORIAL_IMAGE_MODEL||MODEL,created_at:new Date().toISOString()
  };
  await step.do("save-qa-manifest",async()=>{await this.env.TROPICANA_EDITORIAL_R2.put(runRoot+"manifest.json",JSON.stringify(manifest),{httpMetadata:{contentType:"application/json"}});return {stored:true}});
  return {job_id:id,status:manifest.status,source_sha256:source.sha256,candidate_sha256:candidate.sha256,manifest_key:runRoot+"manifest.json"};
 }
}
export async function editorialApi(request,env,url,authorizeSession){
 const path=url.pathname,method=request.method.toUpperCase();
 if(path.startsWith("/api/editorial/job/"))return editorialJobApi(request,env,url,authorizeSession);
 if(path==="/api/editorial/health"&&method==="GET"){
  return j({ok:true,project:PROJECT,frame:FRAME,module:"Editorial Photo",storage_isolated:!!env.TROPICANA_EDITORIAL_R2,openrouter_configured:!!env.OPENROUTER_API_KEY,job_control:"Cloudflare Workflows API only",skill:"v1.13",technical_master_policy:"V10_NEUTRAL_NO_HDR",generation_guard:"HUMAN_QA_APPROVAL_REQUIRED",image_model:env.EDITORIAL_IMAGE_MODEL||MODEL,workflow:"mg4k-tropicana-editorial",technical_workflow_configured:!!env.TROPICANA_TECH_WORKFLOW});
 }
 if(path==="/api/editorial/upload"&&method==="POST"){
  const session=await authorizeSession(request,url,env);
  if(!session)return j({error:"session_required"},403);
  if(!env.TROPICANA_EDITORIAL_R2)return j({error:"r2_missing"},503);
  const limitKey="editorial:upload:"+session.id;
  const usage=Number(await env.SESSION_STATE_R7.get(limitKey)||0);
  if(usage>=2)return j({error:"upload_session_limit"},429);
  const ip=String(request.headers.get("CF-Connecting-IP")||"unknown");
  const hour=new Date().toISOString().slice(0,13);
  const hashedIp=(await sha256(new TextEncoder().encode(ip))).slice(0,20);
  const ipKey="editorial:upload:ip:"+hour+":"+hashedIp;
  const globalKey="editorial:upload:global:"+hour;
  const [ipCount,globalCount]=await Promise.all([env.SESSION_STATE_R7.get(ipKey),env.SESSION_STATE_R7.get(globalKey)]);
  if(Number(ipCount||0)>=5||Number(globalCount||0)>=30)return j({error:"editorial_upload_hourly_limit"},429);
  let form;try{form=await request.formData()}catch{return j({error:"invalid_form"},400)}
  const file=form.get("file");
  if(!file||typeof file.arrayBuffer!=="function"||file.size<10000||file.size>MAX_SOURCE)return j({error:"image_required_max_8mb"},400);
  if(!["image/jpeg","image/png","image/webp"].includes(String(file.type)))return j({error:"unsupported_image_type"},415);
  const bytes=await file.arrayBuffer();const sha=await sha256(bytes);
  const key=PROJECT+"/editorial-inbox/"+session.id+"/"+crypto.randomUUID()+"."+(file.type==="image/png"?"png":file.type==="image/webp"?"webp":"jpg");
  await env.TROPICANA_EDITORIAL_R2.put(key,bytes,{httpMetadata:{contentType:file.type},customMetadata:{sha256:sha,project:PROJECT,frame:FRAME}});
  await env.SESSION_STATE_R7.put(limitKey,String(usage+1),{expirationTtl:3600});
  await Promise.all([env.SESSION_STATE_R7.put(ipKey,String(Number(ipCount||0)+1),{expirationTtl:7200}),env.SESSION_STATE_R7.put(globalKey,String(Number(globalCount||0)+1),{expirationTtl:7200})]);
  const intake=await createEditorialIntakeJob(env,session,{filename:file.name,mime:file.type,source_key:key,source_sha256:sha,bytes:bytes.byteLength});
  let started=false,workflow_error=null;
  if(env.TROPICANA_TECH_WORKFLOW){
    try{
      await env.TROPICANA_TECH_WORKFLOW.create({id:intake.job_id,params:{job_id:intake.job_id}});
      started=true;
    }catch(err){
      workflow_error=String(err?.message||err).slice(0,250);
      console.error("EDITORIAL_TECH_WORKFLOW_DISPATCH",intake.job_id,workflow_error);
      await env.TROPICANA_EDITORIAL_R2.put("tropicana-park/editorial-jobs/"+intake.job_id+"/processing-state.json",
        JSON.stringify({job_id:intake.job_id,status:"FAILED",stage:"workflow_dispatch_failed",
          progress:null,message:"Технический процессор не запущен.",error:workflow_error}),
        {httpMetadata:{contentType:"application/json"}});
    }
  }else workflow_error="technical_workflow_not_configured";
  return j({ok:true,project:PROJECT,frame:"NEW_UPLOAD",job_id:intake.job_id,job_token:intake.job_token,
    status:started?"QUEUED":workflow_error?"FAILED":intake.status,stage:started?"technical_queued":intake.stage,progress:null,
    source_key:key,sha256:sha,bytes:bytes.byteLength,workflow_control:"automatic_free_technical_only",
    message:started?"Исходник сохранён; технический процессор запущен без HDR и OpenRouter.":"Технический процессор недоступен.",
    workflow_error},201);
 }
 if(path==="/api/editorial/view"&&method==="GET"){
  const id=url.searchParams.get("job")||"",kind=url.searchParams.get("kind")||"manifest";
  if(["tech_v10_full","tech_v10_qa","tech_v10_neutral","tech_v10_clean"].includes(kind)&&isId(id)){
    const historical=await env.TROPICANA_EDITORIAL_R2.get(fileKey(id,"manifest.json"));
    if(!historical)return j({error:"job_not_found"},404);
    const job=await historical.json();
    if(job.project!==PROJECT||job.frame!==FRAME)return j({error:"frame_mismatch"},409);
    const v10Object=await env.TROPICANA_EDITORIAL_R2.get(ROOT+"technical-v10/DSC09818_TECH_QA.json");
    if(!v10Object)return j({error:"technical_v10_not_ready"},404);
    const q=await v10Object.json();
    if(q.source_sha256!=="1b4344f4b8ca629830254850fbcbaf2931c83c00a158ab87d84e5ee7722046d5"||
       q.technical_version!=="V10_NEUTRAL_NO_HDR"||
       q.technical_status!=="TECHNICAL_QA_PASS"||
       q.HDR_applied!==false||q.virtual_exposures!==false||q.local_HDR!==false||
       q.qa_checks?.HDR_ABSENT!=="PASS")return j({error:"technical_v10_HDR_or_QA_HOLD"},409);
    if(kind==="tech_v10_qa"){
      return j({project:PROJECT,frame:FRAME,technical_version:q.technical_version,
        source_sha256:q.source_sha256,master_sha256:q.clean16_sha256,
        qa_checks:q.qa_checks,technical_status:q.technical_status,
        visual_review:"PENDING",HDR_applied:false,
        generation:"BLOCKED_UNTIL_EXPLICIT_HUMAN_QA_APPROVAL"});
    }
    let key;
    if(kind==="tech_v10_full"){
      const previewObject=await env.TROPICANA_EDITORIAL_R2.get(ROOT+"technical-v10/fullres-preview-manifest.json");
      if(!previewObject)return j({error:"technical_v10_fullres_not_prepared"},404);
      const p=await previewObject.json();
      const expected=ROOT+"technical-v10/fullres-review-"+q.clean16_sha256.slice(0,16)+".jpg";
      if(p.preview_key!==expected||p.master16_sha256!==q.clean16_sha256||
         p.original_arw_sha256!==q.source_sha256||
         p.native_width!==6536||p.native_height!==4360||
         p.master_bit_depth!==16||p.display_bit_depth!==8||p.no_upscale!==true)
        return j({error:"technical_v10_fullres_lineage_HOLD"},409);
      const head=await env.TROPICANA_EDITORIAL_R2.head(p.preview_key);
      if(!head||head.size!==p.preview_bytes)return j({error:"technical_v10_fullres_missing"},404);
      key=p.preview_key;
    }else key=ROOT+"technical-v10/"+(kind==="tech_v10_clean"?"DSC09818_TECH_CLEAN.jpg":"DSC09818_TECH_NEUTRAL.jpg");
    const img=await env.TROPICANA_EDITORIAL_R2.get(key);
    if(!img)return j({error:"technical_v10_image_not_found"},404);
    return new Response(img.body,{headers:{"content-type":"image/jpeg","cache-control":"private,no-store","x-content-type-options":"nosniff"}});
  }
  if((kind==="compare_info"||kind==="tech_full"||kind==="raw_original")&&isId(id)){
    const manifestObject=await env.TROPICANA_EDITORIAL_R2.get(fileKey(id,"manifest.json"));
    if(!manifestObject)return j({error:"editorial_job_not_found"},404);
    const manifest=await manifestObject.json();
    const sourceVersion=manifest.source_key===ROOT+"technical-v10/DSC09818_EDITORIAL_INPUT.jpg"?"V10":
      manifest.source_key===ROOT+"technical-v9/DSC09818_EDITORIAL_INPUT.jpg"?"V9":null;
    if(manifest.project!==PROJECT||manifest.frame!==FRAME||!sourceVersion||
       !manifest.candidate_key||manifest.final!==false)
      return j({error:"only_original_RAW_derived_Tech_V9_or_V10_supported"},409);
    const jobTechnicalRoot=ROOT+(sourceVersion==="V10"?"technical-v10/":"technical-v9/");
    const [proofObject,previewManifestObject]=await Promise.all([
      env.TROPICANA_EDITORIAL_R2.get(jobTechnicalRoot+"DSC09818_TECH_QA.json"),
      env.TROPICANA_EDITORIAL_R2.get(jobTechnicalRoot+"fullres-preview-manifest.json")
    ]);
    if(!proofObject)return j({error:"technical_source_lineage_missing"},404);
    const q=await proofObject.json();
    if(q.source_sha256!=="1b4344f4b8ca629830254850fbcbaf2931c83c00a158ab87d84e5ee7722046d5"||
       q.technical_status!=="TECHNICAL_QA_PASS"||
       q.editorial_input_sha256!==manifest.source_sha256||
       (sourceVersion==="V10"&&(q.technical_version!=="V10_NEUTRAL_NO_HDR"||
         q.HDR_applied!==false||q.virtual_exposures!==false||q.qa_checks?.HDR_ABSENT!=="PASS")))
      return j({error:"technical_source_sha_or_status_mismatch"},409);
    let fullKey=null,preview=null;
    if(previewManifestObject){
      const p=await previewManifestObject.json();
      const expected=jobTechnicalRoot+"fullres-review-"+q.clean16_sha256.slice(0,16)+".jpg";
      if(p.preview_key===expected&&p.master16_sha256===q.clean16_sha256&&
         p.original_arw_sha256===q.source_sha256&&
         p.native_width===6536&&p.native_height===4360&&
         p.display_bit_depth===8&&p.master_bit_depth===16&&p.no_upscale===true){
         const head=await env.TROPICANA_EDITORIAL_R2.head(p.preview_key);
         if(head&&head.size===p.preview_bytes){fullKey=p.preview_key;preview=p}
      }
    }
    // ORIGINAL and TECH must have identical SHA-backed original Sony ARW provenance.
    // The original is demosaiced and neutrally toned ONLY, no geometric remapping,
    // no HDR, no denoise, no creative or generative processing.
    let rawPreview=null,rawKey=null;
    const rawManifestObject=await env.TROPICANA_EDITORIAL_R2.get(ROOT+"original-raw/original-raw-manifest.json");
    if(rawManifestObject){
      const p=await rawManifestObject.json();
      const expectedPrefix=ROOT+"original-raw/fullres-neutral-"+q.source_sha256.slice(0,16)+"-"+p.display_sha256.slice(0,16)+".jpg";
      if(p.project===PROJECT&&p.frame===FRAME&&
         p.original_raw_sha256===q.source_sha256&&p.display_key===expectedPrefix&&
         p.original_sensor_width===7028&&p.original_sensor_height===4688&&
         p.display_width===7028&&p.display_height===4688&&
         JSON.stringify(p.reference_center_crop_xywh)===JSON.stringify([246,164,6536,4360])&&
         p.optical_distortion_corrected===false&&p.perspective_corrected===false&&
         p.HDR_applied===false&&p.denoise_applied===false&&
         p.no_spatial_resize===true&&p.no_creative_processing===true&&
         /^[0-9a-f]{64}$/.test(p.display_sha256)){
        const head=await env.TROPICANA_EDITORIAL_R2.head(p.display_key);
        if(head&&head.size===p.display_bytes&&head.size>1000000){
          rawPreview=p;rawKey=p.display_key;
        }
      }
    }
    if(kind==="tech_full"||kind==="raw_original"){
      const key=kind==="tech_full"?fullKey:rawKey;
      if(!key)return j({error:kind==="raw_original"?"original_raw_fullres_preview_not_ready":"full_resolution_preview_not_ready"},404);
      const obj=await env.TROPICANA_EDITORIAL_R2.get(key);
      if(!obj)return j({error:"full_resolution_preview_missing"},404);
      return new Response(obj.body,{headers:{"content-type":"image/jpeg","cache-control":"private,max-age=3600","x-content-type-options":"nosniff","content-disposition":"inline"}});
    }
    let v10Info={ready:false,technical_version:"V10_NEUTRAL_NO_HDR",HDR_applied:false,
      human_QA:"PENDING",generation:"BLOCKED_UNTIL_VISUAL_QA"};
    const v10qaObject=await env.TROPICANA_EDITORIAL_R2.get(ROOT+"technical-v10/DSC09818_TECH_QA.json");
    const v10previewObject=await env.TROPICANA_EDITORIAL_R2.get(ROOT+"technical-v10/fullres-preview-manifest.json");
    if(v10qaObject&&v10previewObject){
      const nq=await v10qaObject.json(),np=await v10previewObject.json();
      const expected=ROOT+"technical-v10/fullres-review-"+String(nq.clean16_sha256||"").slice(0,16)+".jpg";
      if(nq.technical_status==="TECHNICAL_QA_PASS"&&nq.technical_version==="V10_NEUTRAL_NO_HDR"&&
         nq.source_sha256===q.source_sha256&&nq.HDR_applied===false&&
         nq.virtual_exposures===false&&nq.local_HDR===false&&nq.qa_checks?.HDR_ABSENT==="PASS"&&
         np.preview_key===expected&&np.master16_sha256===nq.clean16_sha256&&
         np.original_arw_sha256===nq.source_sha256&&np.native_width===6536&&np.native_height===4360){
        const h=await env.TROPICANA_EDITORIAL_R2.head(np.preview_key);
        if(h&&h.size===np.preview_bytes)
          v10Info={...v10Info,ready:true,width:6536,height:4360,master_sha256:nq.clean16_sha256};
      }
    }
    return j({ok:true,project:PROJECT,frame:FRAME,job_id:id,
      candidate_source_version:sourceVersion,archive_history:sourceVersion==="V9",
      source:"verified genuine ARW -> 16-bit TIFF TECH "+sourceVersion,
      technical_master_sha256:q.clean16_sha256,technical_master_dimensions:{width:6536,height:4360,bit_depth:16},
      technical_preview_kind:"8-bit full-pixel-resolution browser representation (NOT the 16-bit TIFF itself)",
      fullres_preview:{ready:!!fullKey,key:fullKey?undefined:null,width:preview?.native_width||null,height:preview?.native_height||null,
        sha256:preview?.preview_sha256||null},
      technical_v10_preview:v10Info,
      original_raw_preview:{ready:!!rawKey,width:rawPreview?.display_width||null,height:rawPreview?.display_height||null,
        sha256:rawPreview?.display_sha256||null,original_raw_sha256:q.source_sha256,
        reference_center_crop_xywh:rawPreview?.reference_center_crop_xywh||[246,164,6536,4360],
        geometry_corrected:false,hdr_applied:false,denoise_applied:false,
        description:"Original Sony ARW full-pixel neutral display: 7028×4688, NO lens or perspective correction"},
      supported_pairs:["raw-tech","tech-editorial"],
      editorial_native_dimensions:"Read from actual OpenRouter PNG in browser; no false claims about source resolution",
      editorial_candidate_sha256:manifest.candidate_sha256,
      post_generation_geometry:"Refer to independent numeric QA under manifest; visual authenticity remains REVIEW",
      publication_approved:false});
  }
  if(!isId(id)||!["manifest","candidate","analysis","qa","source","optical","technical","tech_clean","tech_neutral","tech_hdr","tech_qa"].includes(kind))return j({error:"invalid_view_request"},400);
  const map={manifest:"manifest.json",candidate:"editorial_candidate.png",analysis:"scene_analysis.json",qa:"fidelity_qa.json"};
  let key=kind==="technical"?ROOT+"optically_locked_model_input.jpg":kind==="optical"?ROOT+"optical_only_input.jpg":fileKey(id,map[kind]||"manifest.json");
  const technicalViews={tech_clean:"DSC09818_TECH_CLEAN.jpg",tech_neutral:"DSC09818_TECH_NEUTRAL.jpg",tech_hdr:"DSC09818_TECH_HDR_TEST.jpg",tech_qa:"DSC09818_TECH_QA.json"};
  if(technicalViews[kind])key=ROOT+"technical-v9/"+technicalViews[kind];
  if(kind==="source"){
    const manifest=await env.TROPICANA_EDITORIAL_R2.get(fileKey(id,"manifest.json"));
    if(!manifest)return j({error:"not_found"},404);
    const info=await manifest.json();
    if(!sourceName(info.source_key))return j({error:"invalid_source"},400);
    key=ROOT+"technical_model_input.jpg"; // unrectified RAW-developed original, deliberately shown for before/after
  }
  const o=await env.TROPICANA_EDITORIAL_R2.get(key);
  if(!o)return j({error:"not_found"},404);
  if(kind==="manifest"){
    const manifest=await o.json();
    manifest.active_skill_version="v1.13";
    manifest.technical_master_version=manifest.source_key===ROOT+"technical-v10/DSC09818_EDITORIAL_INPUT.jpg"?"V10_NEUTRAL_NO_HDR":"V9_HDR_ARCHIVE";
    manifest.technical_master_art_gate=manifest.technical_master_version==="V10_NEUTRAL_NO_HDR"?"QA_APPROVED_FOR_THIS_CANDIDATE":"HISTORICAL_ART_ONLY";
    // Historical persisted manifest is immutable; append a verified numeric QA view.
    const latest=await env.TROPICANA_EDITORIAL_R2.get(fileKey(id,"post_generation_geometry_QA.json"));
    if(latest){
      const qa=await latest.json();
      if(qa.source_sha256===manifest.source_sha256&&qa.candidate_sha256===manifest.candidate_sha256){
        manifest.post_generation_geometry_QA=qa;
        manifest.gates={...manifest.gates,post_generation_geometry:qa.status==="PASS"?"PASS":"HOLD"};
      }
    }
    return j(manifest);
  }
  const media=(kind==="source"||kind==="technical"||kind==="optical"||kind==="tech_clean"||kind==="tech_neutral"||kind==="tech_hdr")?"image/jpeg":kind==="candidate"?"image/png":"application/json";
  return new Response(o.body,{headers:{"content-type":media,"cache-control":"private,no-store","x-content-type-options":"nosniff"}});
 }
 return j({error:"editorial_api_not_found"},404);
}
