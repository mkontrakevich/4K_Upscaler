import test from "node:test";
import assert from "node:assert/strict";
import {createEditorialIntakeJob,editorialJobApi,verifyEditorialArtisticInput} from "../src/editorial_jobs.js";

function mockR2(){
 const map=new Map();
 const toBytes=x=>typeof x==="string"?new TextEncoder().encode(x):x instanceof Uint8Array?x:new Uint8Array(x);
 return {
   map,
   async put(key,body,opts={}){
     if(opts.onlyIf?.get?.("If-None-Match")==="*"&&map.has(key))return null;
     const bytes=toBytes(body);
     const digest=await crypto.subtle.digest("SHA-256",bytes);
     const valid=!opts.sha256||[...new Uint8Array(opts.sha256)].every((b,i)=>b===new Uint8Array(digest)[i]);
     if(!valid)throw Error("r2_sha256_mismatch");
     map.set(key,{bytes,httpMetadata:opts.httpMetadata||{},customMetadata:opts.customMetadata||{},
       checksums:opts.sha256?{sha256:digest}:{}});
     return {key,size:bytes.length};
   },
   async delete(keys){for(const key of Array.isArray(keys)?keys:[keys])map.delete(key)},
   async head(key){const obj=map.get(key);return obj?{size:obj.bytes.length,httpMetadata:obj.httpMetadata,customMetadata:obj.customMetadata,checksums:obj.checksums}:null},
   async get(key,opts={}){const obj=map.get(key);if(!obj)return null;
     const part=opts.range?obj.bytes.subarray(opts.range.offset||0,(opts.range.offset||0)+(opts.range.length||obj.bytes.length)):obj.bytes;
     return {
       size:obj.bytes.length,body:part,httpMetadata:obj.httpMetadata,customMetadata:obj.customMetadata,
       async arrayBuffer(){return part.buffer.slice(part.byteOffset,part.byteOffset+part.byteLength)},
       async json(){return JSON.parse(new TextDecoder().decode(part))}
     };
   }
 };
}
function harness(){
 const r2=mockR2(),mg4k=mockR2(),env={TROPICANA_EDITORIAL_R2:r2,TEMP_BUFFER_R7:mg4k};
 const session={id:"session-allow"};
 const authorize=async request=>request.headers.get("x-session-id")==="session-allow"&&request.headers.get("x-session-token")==="secret"?session:null;
 const call=(path,method="GET",body,allow=true,editorialBearer="")=> {
   const url=new URL("https://app.example"+path);
   const headers=allow?{"x-session-id":"session-allow","x-session-token":"secret"}:{"x-session-id":"intruder","x-session-token":"wrong"};
   if(editorialBearer)headers["x-editorial-job-token"]=editorialBearer;
   if(body)headers["content-type"]="application/json";
   const request=new Request(url,{method,headers,body:body?JSON.stringify(body):undefined});
   return editorialJobApi(request,env,url,authorize);
 };
 const formCall=(path,fileBytes,allow=true,editorialBearer="")=>{
   const url=new URL("https://app.example"+path);
   const headers=allow?{"x-session-id":"session-allow","x-session-token":"secret"}:
                       {"x-session-id":"intruder","x-session-token":"wrong"};
   if(editorialBearer)headers["x-editorial-job-token"]=editorialBearer;
   const form=new FormData();
   form.append("file",new Blob([fileBytes],{type:"image/png"}),"corrected-version.png");
   return editorialJobApi(new Request(url,{method:"POST",headers,body:form}),env,url,authorize);
 };
 return {r2,mg4k,env,session,call,formCall};
}
const QA_CHECKS={preview_reviewed:true,geometry:true,materials:true,no_hdr:true};
async function intake(h){
 return createEditorialIntakeJob(h.env,h.session,{filename:"New_photo.jpg",mime:"image/jpeg",source_key:"tropicana-park/DSC09818/uploads/session-allow/test.jpg",source_sha256:"a".repeat(64),bytes:22000});
}
async function tech(h,job){
 const masterKey="tropicana-park/editorial-jobs/"+job.job_id+"/technical/master16.tif";
 const bytes=new Uint8Array(1100000);bytes.set([73,73,43,0]); // Genuine BigTIFF magic for negative file-type gate.
 const digest=await crypto.subtle.digest("SHA-256",bytes);
 const sha=[...new Uint8Array(digest)].map(v=>v.toString(16).padStart(2,"0")).join("");
 await h.r2.put(masterKey,bytes,{
   sha256:digest,httpMetadata:{contentType:"image/tiff"},customMetadata:{sha256:sha}
 });
 const proxyBytes=new TextEncoder().encode("REAL_JPEG_PROXY_TEST_BYTES_LONG_ENOUGH");
 const proxyDigest=await crypto.subtle.digest("SHA-256",proxyBytes);
 const proxySHA=[...new Uint8Array(proxyDigest)].map(v=>v.toString(16).padStart(2,"0")).join("");
 await h.r2.put("tropicana-park/editorial-jobs/"+job.job_id+"/technical/fullres-preview.jpg",proxyBytes,{
   sha256:proxyDigest,httpMetadata:{contentType:"image/jpeg"},customMetadata:{sha256:proxySHA}
 });
 await h.r2.put("tropicana-park/editorial-jobs/"+job.job_id+"/technical/qa.json",JSON.stringify({
   job_id:job.job_id,source_sha256:job.source_sha256,
   master_key:masterKey,master_sha256:sha,master_bytes:1100000,
   preview_sha256:proxySHA,preview_bytes:proxyBytes.byteLength,
   master_bit_depth:16,technical_status:"TECHNICAL_QA_PASS",
   HDR_applied:false,virtual_exposures:false,local_HDR:false,qa_checks:{HDR_ABSENT:"PASS"}
 }),{httpMetadata:{contentType:"application/json"}});
 return sha;
}
test("new upload is awaiting processing and never pretends to have a progress percentage",async()=>{
 const h=harness(),job=await intake(h);
 const resp=await h.call("/api/editorial/job/status?job="+job.job_id);
 const data=await resp.json();
 assert.equal(resp.status,200);
 assert.equal(data.status,"AWAITING_TECH_PROCESSING");
 assert.equal(data.progress,null);
 assert.equal(data.can_approve,false);
 assert.equal(data.can_handoff,false);
 assert.equal(data.auto_generation,false);
 for(const op of ["decision","handoff"]){
   const r=await h.call("/api/editorial/job/"+op+"?job="+job.job_id,"POST",op==="decision"?{decision:"APPROVED",master_sha256:"c".repeat(64),checks:QA_CHECKS}:undefined);
   assert.equal(r.status,409);
 }
});
test("mislabelled binary cannot be approved as 16-bit TIFF",async()=>{
 const h=harness(),job=await intake(h);const sha=await tech(h,job);
 const k="tropicana-park/editorial-jobs/"+job.job_id+"/technical/master16.tif";
 const bad=new Uint8Array(1100000);
 const digest=await crypto.subtle.digest("SHA-256",bad);
 const checksum=[...new Uint8Array(digest)].map(v=>v.toString(16).padStart(2,"0")).join("");
 await h.r2.put(k,bad,{sha256:digest,httpMetadata:{contentType:"image/tiff"},customMetadata:{sha256:checksum}});
 const qaKey="tropicana-park/editorial-jobs/"+job.job_id+"/technical/qa.json";
 const qa=await (await h.r2.get(qaKey)).json();qa.master_sha256=checksum;
 await h.r2.put(qaKey,JSON.stringify(qa),{httpMetadata:{contentType:"application/json"}});
 const data=await (await h.call("/api/editorial/job/status?job="+job.job_id)).json();
 assert.equal(data.can_approve,false);
 assert.equal(data.technical_master_ready,false);
});
test("per-job access is bound to authenticated session",async()=>{
 const h=harness(),job=await intake(h);
 for(const op of ["status","download"]){
   const r=await h.call("/api/editorial/job/"+op+"?job="+job.job_id,"GET",null,false);
   assert.equal(r.status,404);
 }
});
test("opaque durable job token restores QA status after short session expires",async()=>{
 const h=harness(),job=await intake(h);
 assert.match(job.job_token,/^[a-f0-9]{64}$/);
 const record=await (await h.r2.get("tropicana-park/editorial-jobs/"+job.job_id+"/job.json")).json();
 assert.equal(record.job_token,undefined);
 assert.match(record.access_hash,/^[0-9a-f]{64}$/);
 let r=await h.call("/api/editorial/job/status?job="+job.job_id,"GET",null,false,job.job_token);
 assert.equal(r.status,200);
 r=await h.call("/api/editorial/job/status?job="+job.job_id,"GET",null,false,"a".repeat(64));
 assert.equal(r.status,404);
});
test("only verified technical NO HDR evidence unlocks QA; approval enables download and staged handoff",async()=>{
 const h=harness(),job=await intake(h);
 const sha=await tech(h,job);
 let r=await h.call("/api/editorial/job/status?job="+job.job_id);
 let d=await r.json();
 assert.equal(d.status,"AWAITING_HUMAN_QA");assert.equal(d.can_approve,true);
 r=await h.call("/api/editorial/job/decision?job="+job.job_id,"POST",{decision:"APPROVED",master_sha256:"c".repeat(64),checks:QA_CHECKS});
 assert.equal(r.status,409);
 r=await h.call("/api/editorial/job/decision?job="+job.job_id,"POST",{decision:"APPROVED",master_sha256:sha});
 assert.equal(r.status,400);
 r=await h.call("/api/editorial/job/decision?job="+job.job_id,"POST",{decision:"APPROVED",master_sha256:sha,checks:QA_CHECKS});
 assert.equal(r.status,200);
 r=await h.call("/api/editorial/job/status?job="+job.job_id);d=await r.json();
 assert.equal(d.can_download,true);assert.equal(d.can_handoff,true);
 r=await h.call("/api/editorial/job/download?job="+job.job_id);
 assert.equal(r.status,200);assert.equal(r.headers.get("content-type"),"image/tiff");
 r=await h.call("/api/editorial/job/handoff?job="+job.job_id,"POST");
 d=await r.json();
 assert.equal(d.stage,"MG4K_MASTER_IMPORTED");assert.equal(d.generation_started,false);
 const o=await h.r2.get("tropicana-park/editorial-jobs/"+job.job_id+"/handoff.json");
 const manifest=await o.json();
 assert.equal(manifest.locks.geometry,"HARD");assert.equal(manifest.mg4k_locks.camera,"hard");
 assert.equal(manifest.generation_authorized,false);
 assert.equal(manifest.mg4k_master_key,"editorial-imports/"+job.job_id+"/approved-master16.tif");
 const original=await h.r2.get(manifest.master_key), imported=await h.mg4k.get(manifest.mg4k_master_key);
 assert.equal(imported.size,original.size);
 assert.equal(imported.httpMetadata.contentType,"image/tiff");
 assert.equal(imported.bytes?.byteLength||imported.size,1100000);
 let view=await h.call("/api/editorial/job/import-status?job="+job.job_id);
 assert.equal(view.status,200);
 const summary=await view.json();
 assert.equal(summary.generation_started,false);
 assert.equal(summary.master_sha256,sha);
 assert.equal(summary.mg4k_locks.geometry,"hard");
 view=await h.call("/api/editorial/job/import-status?job="+job.job_id,"GET",null,false);
 assert.equal(view.status,404);
 const repeat=await h.call("/api/editorial/job/handoff?job="+job.job_id,"POST");
 assert.equal(repeat.status,200);
 assert.equal((await h.mg4k.get(manifest.mg4k_master_key)).size,1100000);
 const proxyObj=await h.r2.get("tropicana-park/editorial-jobs/"+job.job_id+"/technical/fullres-preview.jpg");
 const accepted=await verifyEditorialArtisticInput(h.env,job.job_id,job.job_token,proxyObj.body);
 assert.equal(accepted.locks.camera,"hard");
 assert.equal(accepted.approved_master_sha256,sha);
 await assert.rejects(
   verifyEditorialArtisticInput(h.env,job.job_id,job.job_token,new Uint8Array([1,2,3])),
   /editorial_import_bytes_do_not_match/
 );
 await assert.rejects(
   verifyEditorialArtisticInput(h.env,job.job_id,"f".repeat(64),proxyObj.body),
   /editorial_import_forbidden/
 );
});
test("rework decision is immutable and cannot be overwritten or handed off",async()=>{
 const h=harness(),job=await intake(h);
 const sha=await tech(h,job);
 let r=await h.call("/api/editorial/job/decision?job="+job.job_id,"POST",{decision:"REWORK_REQUESTED",master_sha256:sha,
   categories:["geometry"],note:"Straighten verticals and retain unchanged architectural geometry"});
 assert.equal(r.status,200);
 r=await h.call("/api/editorial/job/decision?job="+job.job_id,"POST",{decision:"APPROVED",master_sha256:sha,checks:QA_CHECKS});
 assert.equal(r.status,409);
 r=await h.call("/api/editorial/job/handoff?job="+job.job_id,"POST");
 assert.equal(r.status,409);
});

test("invalid MG4K storage leaves no falsely completed handoff",async()=>{
 const h=harness(),job=await intake(h);const sha=await tech(h,job);
 await h.call("/api/editorial/job/decision?job="+job.job_id,"POST",{decision:"APPROVED",master_sha256:sha,checks:QA_CHECKS});
 delete h.env.TEMP_BUFFER_R7;
 const r=await h.call("/api/editorial/job/handoff?job="+job.job_id,"POST");
 assert.equal(r.status,503);
 const record=await h.r2.get("tropicana-park/editorial-jobs/"+job.job_id+"/handoff.json");
 assert.equal(record,null);
});

test("structured rework is an immutable QA decision; approval and handoff stay blocked",async()=>{
 const h=harness(),job=await intake(h),master=await tech(h,job);
 const endpoint="/api/editorial/job/decision?job="+job.job_id;
 let r=await h.call(endpoint,"POST",{decision:"REWORK_REQUESTED",master_sha256:master,categories:[],note:"Please fix geometry precisely"});
 assert.equal(r.status,400);
 r=await h.call(endpoint,"POST",{decision:"REWORK_REQUESTED",master_sha256:master,
   categories:["geometry","materials"],note:"Correct verticals without changing any facade detail"});
 assert.equal(r.status,200);
 const d=await r.json();assert.equal(d.decision,"REWORK_REQUESTED");
 const status=await (await h.call("/api/editorial/job/status?job="+job.job_id)).json();
 assert.equal(status.status,"REWORK_REQUESTED");
 assert.equal(status.revision_num,1);
 assert.equal(status.can_approve,false);
 assert.equal(status.can_rework,false);
 assert.equal(status.can_upload_revision,true);
 assert.deepEqual(status.rework.categories,["geometry","materials"]);
 assert.equal(status.rework.note,"Correct verticals without changing any facade detail");
 r=await h.call(endpoint,"POST",{decision:"APPROVED",master_sha256:master,checks:QA_CHECKS});
 assert.equal(r.status,409);
 r=await h.call("/api/editorial/job/download?job="+job.job_id);
 assert.equal(r.status,409);
 r=await h.call("/api/editorial/job/handoff?job="+job.job_id,"POST");
 assert.equal(r.status,409);
 const saved=await (await h.r2.get("tropicana-park/editorial-jobs/"+job.job_id+"/technical/decision.json")).json();
 assert.equal(saved.master_sha256,master);
 assert.equal(saved.decision,"REWORK_REQUESTED");
});
test("revised upload creates V2 with original+V1 immutable and repeats Tech QA before MG4K",async()=>{
 const h=harness(),job=await intake(h),master=await tech(h,job);
 const dispatched=[];
 h.env.TROPICANA_TECH_WORKFLOW={async create(info){dispatched.push(info);return {id:info.id}}};
 await h.call("/api/editorial/job/decision?job="+job.job_id,"POST",{
   decision:"REWORK_REQUESTED",master_sha256:master,
   categories:["geometry"],note:"Correct verticals and perspective with strict architectural locks"
 });
 const bytes=new Uint8Array(34000);bytes.set([137,80,78,71,13,10,26,10]);bytes[101]=43;
 const revise="/api/editorial/job/revision?job="+job.job_id;
 let r=await h.formCall(revise,bytes,false);
 assert.equal(r.status,404);
 r=await h.formCall(revise,bytes,false,job.job_token);
 assert.equal(r.status,201);
 const created=await r.json();
 assert.equal(created.revision_num,2);
 assert.equal(created.parent_job_id,job.job_id);
 assert.equal(created.root_job_id,job.job_id);
 assert.equal(created.status,"QUEUED");
 assert.equal(created.generation_started,false);
 assert.equal(dispatched.length,1);
 assert.equal(dispatched[0].id,created.job_id);
 assert.notEqual(created.job_id,job.job_id);
 const parent=await (await h.r2.get("tropicana-park/editorial-jobs/"+job.job_id+"/job.json")).json();
 const child=await (await h.r2.get("tropicana-park/editorial-jobs/"+created.job_id+"/job.json")).json();
 assert.equal(parent.parent_job_id,null);
 assert.equal(parent.revision_num,1);
 assert.equal(child.parent_job_id,job.job_id);
 assert.equal(child.revision_num,2);
 assert.equal(child.access_hash,parent.access_hash);
 assert.notEqual(child.source_key,parent.source_key);
 const revisionLink=await (await h.r2.get("tropicana-park/editorial-jobs/"+job.job_id+"/rework/revision.json")).json();
 assert.equal(revisionLink.job_id,created.job_id);
 const statusParent=await (await h.call("/api/editorial/job/status?job="+job.job_id)).json();
 assert.equal(statusParent.status,"REVISION_CREATED");
 assert.equal(statusParent.next_revision_job_id,created.job_id);
 const statusChild=await (await h.call("/api/editorial/job/status?job="+created.job_id,"GET",null,false,job.job_token)).json();
 assert.equal(statusChild.status,"AWAITING_TECH_PROCESSING");
 assert.equal(statusChild.can_approve,false);
 assert.equal(statusChild.parent_job_id,job.job_id);
 const source=await h.call("/api/editorial/job/source?job="+created.job_id,"GET",null,false,job.job_token);
 assert.equal(source.status,200);
 assert.equal(source.headers.get("content-type"),"image/png");
 const a=await source.arrayBuffer();
 assert.equal(a.byteLength,34000);
 r=await h.formCall(revise,bytes);
 assert.equal(r.status,409);
 assert.equal((await h.r2.get("tropicana-park/editorial-jobs/"+job.job_id+"/technical/master16.tif")).size,1100000);
 const childMaster=await tech(h,child);
 r=await h.call("/api/editorial/job/decision?job="+created.job_id,"POST",{
   decision:"APPROVED",master_sha256:childMaster,checks:QA_CHECKS},false,job.job_token);
 assert.equal(r.status,200);
 r=await h.call("/api/editorial/job/handoff?job="+created.job_id,"POST",null,false,job.job_token);
 assert.equal(r.status,200);
 const handoff=await (await h.r2.get("tropicana-park/editorial-jobs/"+created.job_id+"/handoff.json")).json();
 assert.equal(handoff.master_sha256,childMaster);
 assert.notEqual(handoff.master_sha256,undefined);
 assert.equal(handoff.generation_authorized,false);
 const originalDecision=await (await h.r2.get("tropicana-park/editorial-jobs/"+job.job_id+"/technical/decision.json")).json();
 assert.equal(originalDecision.decision,"REWORK_REQUESTED");
});
test("unchanged revised source is held; already rejected job requires new version",async()=>{
 const h=harness(),job=await intake(h),master=await tech(h,job);
 await h.call("/api/editorial/job/decision?job="+job.job_id,"POST",{
   decision:"REWORK_REQUESTED",master_sha256:master,categories:["color"],note:"Fix white balance while preserving actual material colors"
 });
 const bytes=new Uint8Array(20000);
 const digest=await crypto.subtle.digest("SHA-256",bytes);
 const identicalSHA=[...new Uint8Array(digest)].map(v=>v.toString(16).padStart(2,"0")).join("");
 // Simulate a correction identical to the registered source (no-op).
 const k="tropicana-park/editorial-jobs/"+job.job_id+"/job.json";
 const stored=await (await h.r2.get(k)).json();
 stored.source_sha256=identicalSHA;
 await h.r2.put(k,JSON.stringify(stored),{httpMetadata:{contentType:"application/json"}});
 const r=await h.formCall("/api/editorial/job/revision?job="+job.job_id,bytes);
 assert.equal(r.status,409);
 const result=await r.json();assert.equal(result.error,"revision_source_unchanged");
});

test("concurrent corrected-source submissions allow exactly one version and clean up losers",async()=>{
 const h=harness(),job=await intake(h),sha=await tech(h,job);
 await h.call("/api/editorial/job/decision?job="+job.job_id,"POST",{
   decision:"REWORK_REQUESTED",master_sha256:sha,categories:["detail"],
   note:"Correct image detail without sharpening architectural edges unnaturally"
 });
 const launched=[];
 h.env.TROPICANA_TECH_WORKFLOW={async create(x){launched.push(x.id);return {id:x.id}}};
 const image=new Uint8Array(25000);image[0]=137;image[1]=80;image[88]=73;
 const path="/api/editorial/job/revision?job="+job.job_id;
 const [a,b]=await Promise.all([h.formCall(path,image),h.formCall(path,image)]);
 const statuses=[a.status,b.status].sort();
 assert.deepEqual(statuses,[201,409]);
 assert.equal(launched.length,1);
 const root="tropicana-park/editorial-jobs/"+job.job_id+"/";
 const revision=await (await h.r2.get(root+"rework/revision.json")).json();
 assert.equal(launched[0],revision.job_id);
 const childRecords=[...h.r2.map.keys()].filter(k=>k.startsWith("tropicana-park/editorial-jobs/")&&
   k.endsWith("/job.json")&&k!==root+"job.json");
 assert.equal(childRecords.length,1);
 const revisedInputs=[...h.r2.map.keys()].filter(k=>k.startsWith("tropicana-park/editorial-revisions/"));
 assert.equal(revisedInputs.length,1);
});
