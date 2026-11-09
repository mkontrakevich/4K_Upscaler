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
   async head(key){const obj=map.get(key);return obj?{size:obj.bytes.length,httpMetadata:obj.httpMetadata,customMetadata:obj.customMetadata,checksums:obj.checksums}:null},
   async get(key){const obj=map.get(key);return obj?{
     size:obj.bytes.length,body:obj.bytes,httpMetadata:obj.httpMetadata,customMetadata:obj.customMetadata,
     async json(){return JSON.parse(new TextDecoder().decode(obj.bytes))}
   }:null}
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
 return {r2,mg4k,env,session,call};
}
const QA_CHECKS={preview_reviewed:true,geometry:true,materials:true,no_hdr:true};
async function intake(h){
 return createEditorialIntakeJob(h.env,h.session,{filename:"New_photo.jpg",mime:"image/jpeg",source_key:"tropicana-park/DSC09818/uploads/session-allow/test.jpg",source_sha256:"a".repeat(64),bytes:22000});
}
async function tech(h,job){
 const masterKey="tropicana-park/editorial-jobs/"+job.job_id+"/technical/master16.tif";
 const bytes=new Uint8Array(1100000);
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
   const r=await h.call("/api/editorial/job/"+op+"?job="+job.job_id,"POST",op==="decision"?{decision:"APPROVED",master_sha256:"c".repeat(64)}:undefined);
   assert.equal(r.status,409);
 }
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
 r=await h.call("/api/editorial/job/decision?job="+job.job_id,"POST",{decision:"APPROVED",master_sha256:"c".repeat(64)});
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
test("reject is final and cannot be overwritten or handed off",async()=>{
 const h=harness(),job=await intake(h);
 const sha=await tech(h,job);
 let r=await h.call("/api/editorial/job/decision?job="+job.job_id,"POST",{decision:"REJECTED",master_sha256:sha});
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
