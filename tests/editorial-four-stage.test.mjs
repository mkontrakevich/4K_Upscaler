import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {createEditorialIntakeJob,editorialJobApi} from "../src/editorial_jobs.js";

const SHA=s=>[...new Uint8Array(s)].map(x=>x.toString(16).padStart(2,"0")).join("");
const HASH=async bytes=>crypto.subtle.digest("SHA-256",bytes);
function bucket(){
 const map=new Map();
 return {map,
  async put(k,body,opts={}){
   if(opts.onlyIf?.get?.("If-None-Match")==="*"&&map.has(k))return null;
   const bytes=typeof body==="string"?new TextEncoder().encode(body):
      body instanceof Uint8Array?body:new Uint8Array(body);
   const sha=await HASH(bytes);
   map.set(k,{bytes,httpMetadata:opts.httpMetadata||{},
      customMetadata:opts.customMetadata||{},checksums:opts.sha256?{sha256:sha}:{}});
   return {key:k,size:bytes.length};
  },
  async head(k){const x=map.get(k);return x?{size:x.bytes.length,httpMetadata:x.httpMetadata,customMetadata:x.customMetadata,checksums:x.checksums}:null},
  async get(k){const x=map.get(k);return x?{size:x.bytes.length,
    body:new ReadableStream({start(c){c.enqueue(x.bytes);c.close()}}),
    httpMetadata:x.httpMetadata,customMetadata:x.customMetadata,
    checksums:x.checksums,
    async json(){return JSON.parse(new TextDecoder().decode(x.bytes))},
    async arrayBuffer(){return x.bytes.buffer.slice(x.bytes.byteOffset,x.bytes.byteOffset+x.bytes.byteLength)}}:null}
 };
}
async function fixture(raw=false){
 const r2=bucket(),started=[];
 const wf={async create(input){started.push(input);return {id:input.id}}};
 const env={TROPICANA_EDITORIAL_R2:r2,TROPICANA_TECH_WORKFLOW:wf};
 const session={id:"raw-owner-1"};
 const bytes=new Uint8Array(25000);bytes.set([1,2,3,4]);
 const sha=SHA(await HASH(bytes));
 const key="tropicana-park/editorial-inbox/raw-owner-1/test."+ (raw?"arw":"png");
 await r2.put(key,bytes,{sha256:await HASH(bytes),
    httpMetadata:{contentType:raw?"application/octet-stream":"image/png"},
    customMetadata:{sha256:sha}});
 const job=await createEditorialIntakeJob(env,session,
   {filename:"test."+(raw?"arw":"png"),mime:raw?"application/x-camera-raw":"image/png",
    source_key:key,source_sha256:sha,bytes:bytes.length});
 const call=async(path,method="GET",body={},allowed=true)=>{
   const url=new URL("https://editorial.test"+path+"?job="+job.job_id);
   const req=new Request(url,{method,headers:{
      "content-type":"application/json","x-session-id":allowed?session.id:"other",
      "x-session-token":allowed?"auth":"bad"
    },body:method==="POST"?JSON.stringify(body):undefined});
   return editorialJobApi(req,env,url,async r=>
     r.headers.get("x-session-token")==="auth"?session:null);
 };
 return {r2,env,job,started,call,sha,bytes};
}

test("four ordered steps exist in current RAW page and process button is a distinct action",()=>{
 const html=readFileSync("site/editorial-photo.html","utf8");
 assert.deepEqual([...html.matchAll(/data-raw-stage="(\d+)"/g)].map(x=>x[1]),["1","2","3","4"]);
 for(const id of ["intake-source-stage","intake-source-image","intake-process",
    "intake-visual-checks","intake-approve","intake-destination-panel",
    "intake-destination","intake-handoff"])assert.ok(html.includes('id="'+id+'"'),id);
 assert.match(html,/id="intake-process" disabled/);
 assert.ok(html.includes('fetch(base+"/job/process?job="'));
 assert.ok(html.includes('body:JSON.stringify({target_id:chosen})'));
});
test("raster upload is stored and preview-ready but analysis cannot start without click",async()=>{
 const x=await fixture(false);
 const state=await (await x.call("/api/editorial/job/status")).json();
 assert.equal(state.status,"AWAITING_TECH_PROCESSING");
 assert.equal(state.source_preview_ready,true);
 assert.equal(state.can_start_processing,true);
 assert.equal(state.can_approve,false);
 assert.equal(state.can_handoff,false);
 assert.equal(x.started.length,0);
});
test("manual click starts exactly one technical workflow and duplicate clicks are blocked",async()=>{
 const x=await fixture(false);
 const r=await x.call("/api/editorial/job/process","POST");
 assert.equal(r.status,202);
 assert.equal(x.started.length,1);
 assert.equal(x.started[0].params.job_id,x.job.job_id);
 assert.equal(x.started[0].params.preview_only,undefined);
 const second=await x.call("/api/editorial/job/process","POST");
 assert.equal(second.status,409);
 assert.equal(x.started.length,1);
 const state=await (await x.call("/api/editorial/job/status")).json();
 assert.equal(state.can_start_processing,false);
 assert.equal(state.can_handoff,false);
});
test("unverified original and unauthorized user cannot dispatch technical processing",async()=>{
 const x=await fixture(false);
 x.r2.map.delete(x.job.source_key);
 assert.equal((await x.call("/api/editorial/job/process","POST")).status,409);
 assert.equal((await x.call("/api/editorial/job/process","POST",{},false)).status,404);
 assert.equal(x.started.length,0);
});
test("RAW preview is mandatory before process; independent preview metadata allows it later",async()=>{
 const x=await fixture(true);
 let st=await (await x.call("/api/editorial/job/status")).json();
 assert.equal(st.can_start_processing,false);
 assert.equal(st.source_preview_ready,false);
 assert.equal((await x.call("/api/editorial/job/process","POST")).status,409);
 const preview=new Uint8Array(14000);preview.set([255,216,255]);
 const digest=await HASH(preview),digestHex=SHA(digest);
 const prefix="tropicana-park/editorial-jobs/"+x.job.job_id+"/";
 await x.r2.put(prefix+"preview/source-preview.jpg",preview,{
   sha256:digest,httpMetadata:{contentType:"image/jpeg"},
   customMetadata:{sha256:digestHex,source_sha256:x.sha,kind:"RAW_PREVIEW_ONLY_NO_CORRECTION"}
 });
 await x.r2.put(prefix+"preview-state.json",JSON.stringify({
   status:"READY",source_sha256:x.sha,preview_sha256:digestHex,preview_bytes:preview.length
 }));
 st=await (await x.call("/api/editorial/job/status")).json();
 assert.equal(st.source_preview_ready,true);
 assert.equal(st.can_start_processing,true);
 const previewResponse=await x.call("/api/editorial/job/source");
 assert.equal(previewResponse.status,200);
 assert.equal(previewResponse.headers.get("x-mg4k-source-representation"),"RAW_CAMERA_PREVIEW_UNCORRECTED");
 assert.equal((await x.call("/api/editorial/job/process","POST")).status,202);
 assert.equal(x.started.length,1);
});
test("different destination is explicitly refused until a compatible receiver is integrated",async()=>{
 const x=await fixture(false);
 const r=await x.call("/api/editorial/job/handoff","POST",{target_id:"marins-dynamo"});
 assert.equal(r.status,409);
 const j=await r.json();
 assert.equal(j.error,"receiver_not_integrated_for_16bit_TIFF");
 assert.equal(x.started.length,0);
});
test("upload routes do not dispatch a technical Workflow implicitly",()=>{
 const s=readFileSync("src/editorial_photo.js","utf8");
 const raw=s.slice(s.indexOf('if(path==="/api/editorial/upload-raw"'),s.indexOf('if(path==="/api/editorial/upload"',s.indexOf('if(path==="/api/editorial/upload-raw"')+10));
 const raster=s.slice(s.indexOf('if(path==="/api/editorial/upload"'),s.indexOf('if(path==="/api/editorial/view"'));
 assert.match(raw,/preview_only:true/);
 assert.doesNotMatch(raw,/params:\{job_id:intake\.job_id\}\}/);
 assert.doesNotMatch(raster,/TROPICANA_TECH_WORKFLOW\.create/);
 assert.match(raw,/technical_processing_started:false/);
 assert.match(raster,/technical_processing_started:false/);
});

test("owner-only technical diagnostic never leaks credentials and holds missing evidence",async()=>{
 const x=await fixture(false);
 const response=await x.call("/api/editorial/job/diagnostics");
 assert.equal(response.status,200);
 const d=await response.json();
 assert.equal(d.overall,"HOLD");
 assert.equal(d.checks.technical_proof_present,false);
 assert.equal(d.checks.server_technical_qa_verified,false);
 assert.equal(d.checks.range_response_bytes,0);
 assert.equal(d.no_generation_started,true);
 assert.equal(JSON.stringify(d).includes("job_token"),false);
 assert.equal(JSON.stringify(d).includes("source_key"),false);
 assert.equal((await x.call("/api/editorial/job/diagnostics","GET",{},false)).status,404);
});

test("producer-side QA stage cannot override receiver-side integrity HOLD",async()=>{
 const x=await fixture(false);
 await x.r2.put("tropicana-park/editorial-jobs/"+x.job.job_id+"/processing-state.json",
   JSON.stringify({status:"AWAITING_HUMAN_QA",stage:"technical_qa_ready",progress:100}));
 const response=await x.call("/api/editorial/job/status");
 assert.equal(response.status,200);
 const state=await response.json();
 assert.equal(state.status,"QA_INTEGRITY_HOLD");
 assert.equal(state.stage,"technical_qa_integrity_hold");
 assert.equal(state.technical_master_ready,false);
 assert.equal(state.can_approve,false);
 assert.equal(state.can_handoff,false);
 assert.match(state.message,/HOLD/);
});
