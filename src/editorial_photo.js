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
"IMAGE-TO-IMAGE EDIT ONLY. The attached photograph is real DSC09818 taken on a narrow rooftop terrace above the bay. Never redesign the scene.",
"Preserve EXACT camera position and framing; same glass and metal railing, same door, bar stools, tables, floor tiles, brick, ceiling beams, plants, true skyline and harbor lights. Do not add furniture, decorative objects, lighting fixtures, railings or new buildings.",
"Creative direction: premium European hospitality editorial at authentic late-night blue-hour. The real warm interior lighting should reveal brick, flooring, metal and glass textures without clipping; maintain the existing bay view and distant geography. Carefully correct muddy cyan cast, reduce chroma noise, protect natural black sky and glass reflections, refine controlled amber-vs-deep-blue balance.",
"Do not synthesize a different sky or sunset, city layout, yachts, flowers, or people. Preserve original weather and honest night conditions. No fantasy architecture, no deformed typography or people. Do not repaint blurred areas into invented detail.",
"Keep physically consistent vanishing lines and original architectural verticals, no new viewpoint. Output a conservative editorial candidate for comparison and manual fidelity QA, NOT a new illustration."
].join("\n");
const AUDIT_PROMPT="Compare the provided REAL source photo (first image) with the EDITORIAL candidate (second). Respond as JSON object with keys changed_architecture (boolean), changed_furniture (boolean), changed_city_view (boolean), changed_geometry (boolean), exposure_quality (string), editorial_quality (string), issues (array strings), recommendation (PASS or HOLD). Be strict; anything unverified should be HOLD. Ignore prettier lighting and assess documentary identity.";
export class EditorialPhotoWorkflow extends WorkflowEntrypoint{
 async run(event,step){
  const params=typeof event.payload==="string"?JSON.parse(event.payload):event.payload||{};
  const id=String(params.job_id||"");
  const sourceKey=String(params.source_key||"");
  if(!isId(id)||sourceKey!==ROOT+"geometry_locked_model_input.jpg")throw new NonRetryableError("perspective_locked_input_required");
  const runRoot=fileKey(id,"");
  const source=await step.do("validate-source",async()=>{
    const o=await object(this.env,sourceKey);
    const proofObject=await this.env.TROPICANA_EDITORIAL_R2.get(ROOT+"geometry_locked_QA.json");
    if(!proofObject)throw new NonRetryableError("geometry_proof_missing");
    const proof=await proofObject.json();
    if(proof.frame!==FRAME||proof.geometry_status!=="PASS"||proof.result_sha256!==o.sha||
       !proof.after||Math.abs(Number(proof.after.median_slope||999))>=0.015)throw new NonRetryableError("geometry_proof_invalid");
    await this.env.TROPICANA_EDITORIAL_R2.put(runRoot+"geometry_source_QA.json",JSON.stringify(proof),{httpMetadata:{contentType:"application/json"}});
    await this.env.TROPICANA_EDITORIAL_R2.put(runRoot+"source_manifest.json",JSON.stringify({
      project:PROJECT,frame:FRAME,source_key:sourceKey,source_sha256:o.sha,source_bytes:o.size,
      source_mime:o.mime,created_at:new Date().toISOString(),status:"INPUT_VALIDATED",
      skill:"MARINS-Architectural-RAW-Photo-v1.3",geometry_lock:"HARD",fidelity_lock:"HARD"
    }),{httpMetadata:{contentType:"application/json"}});
    return {sha256:o.sha,size:o.size,mime:o.mime};
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
   technical_raw_master:"RAW PerspectiveLock V3 is a geometric reference (full original 6536x4360 not yet transferred to Workflow)",
   geometry_input:"PASS — measured homography against approved RAW V3; proof preserved in R2",
   candidate_key:candidate.key,candidate_sha256:candidate.sha256,
   gates:{source_integrity:"PASS",pre_generation_geometry:"PASS",post_generation_geometry:"PENDING_NUMERIC_QA",editorial_visual:"REVIEW",geometry_fidelity:"REVIEW",manual_approval:"REQUIRED",full_resolution_master:"REQUIRED"},
   steps:{analysis,candidate,audit},skill:"MARINS Architectural RAW Photo v1.3",
   model:this.env.EDITORIAL_IMAGE_MODEL||MODEL,created_at:new Date().toISOString()
  };
  await step.do("save-qa-manifest",async()=>{await this.env.TROPICANA_EDITORIAL_R2.put(runRoot+"manifest.json",JSON.stringify(manifest),{httpMetadata:{contentType:"application/json"}});return {stored:true}});
  return {job_id:id,status:manifest.status,source_sha256:source.sha256,candidate_sha256:candidate.sha256,manifest_key:runRoot+"manifest.json"};
 }
}
export async function editorialApi(request,env,url,authorizeSession){
 const path=url.pathname,method=request.method.toUpperCase();
 if(path==="/api/editorial/health"&&method==="GET"){
  return j({ok:true,project:PROJECT,frame:FRAME,module:"Editorial Photo",storage_isolated:!!env.TROPICANA_EDITORIAL_R2,openrouter_configured:!!env.OPENROUTER_API_KEY,job_control:"Cloudflare Workflows API only",skill:"v1.3",image_model:env.EDITORIAL_IMAGE_MODEL||MODEL,workflow:"mg4k-tropicana-editorial"});
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
  const key=ROOT+"uploads/"+session.id+"/"+crypto.randomUUID()+"."+(file.type==="image/png"?"png":file.type==="image/webp"?"webp":"jpg");
  await env.TROPICANA_EDITORIAL_R2.put(key,bytes,{httpMetadata:{contentType:file.type},customMetadata:{sha256:sha,project:PROJECT,frame:FRAME}});
  await env.SESSION_STATE_R7.put(limitKey,String(usage+1),{expirationTtl:3600});
  await Promise.all([env.SESSION_STATE_R7.put(ipKey,String(Number(ipCount||0)+1),{expirationTtl:7200}),env.SESSION_STATE_R7.put(globalKey,String(Number(globalCount||0)+1),{expirationTtl:7200})]);
  return j({ok:true,project:PROJECT,frame:FRAME,source_key:key,sha256:sha,bytes:bytes.byteLength,workflow_control:"manual",message:"Input stored. Paid editorial workflow requires authorized management launch."},201);
 }
 if(path==="/api/editorial/view"&&method==="GET"){
  const id=url.searchParams.get("job")||"",kind=url.searchParams.get("kind")||"manifest";
  if(!isId(id)||!["manifest","candidate","analysis","qa","source","technical"].includes(kind))return j({error:"invalid_view_request"},400);
  const map={manifest:"manifest.json",candidate:"editorial_candidate.png",analysis:"scene_analysis.json",qa:"fidelity_qa.json"};
  let key=kind==="technical"?ROOT+"geometry_locked_model_input.jpg":fileKey(id,map[kind]||"manifest.json");
  if(kind==="source"){
    const manifest=await env.TROPICANA_EDITORIAL_R2.get(fileKey(id,"manifest.json"));
    if(!manifest)return j({error:"not_found"},404);
    const info=await manifest.json();
    if(!sourceName(info.source_key))return j({error:"invalid_source"},400);
    key=info.source_key;
  }
  const o=await env.TROPICANA_EDITORIAL_R2.get(key);
  if(!o)return j({error:"not_found"},404);
  const media=(kind==="source"||kind==="technical")?"image/jpeg":kind==="candidate"?"image/png":"application/json";
  return new Response(o.body,{headers:{"content-type":media,"cache-control":"private,no-store","x-content-type-options":"nosniff"}});
 }
 return j({error:"editorial_api_not_found"},404);
}
