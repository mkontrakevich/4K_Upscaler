import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const main=readFileSync("site/index.html","utf8");
const tech=readFileSync("site/editorial-photo.html","utf8");
const importer=readFileSync("site/editorial-import.js","utf8");
const worker=readFileSync("src/worker.js","utf8");

test("MG4K exposes one continuous SOURCE to FINAL workflow",()=>{
  for(const token of [
    'aria-label="MG 4K pipeline"',
    "<strong>SOURCE</strong>",
    "<strong>TECH MASTER</strong>",
    "<strong>MG4K</strong>",
    "<strong>ИНСПЕКЦИЯ</strong>",
    "<strong>FINAL</strong>",
    "TECH MASTER / RAW",
    'id="action-inspect"',
    'id="actions-panel"',
  ]) assert.ok(main.includes(token),token);
});

test("TECH MASTER remains a non-generative QA-gated stage",()=>{
  for(const token of [
    'class="brand">MG <span>4K</span> / TECH MASTER',
    "NO HDR",
    'id="intake-approve"',
    "Сохранить и передать в выбранный контур",
    "Открыть Tech Master в MG4K",
    'id="intake-check-preview"',
    'id="intake-check-geometry"',
    'id="intake-check-materials"',
    "intakeHandoff",
  ]) assert.ok(tech.includes(token),token);
  assert.match(tech,/<button[^>]*id="intake-approve"[^>]*disabled[^>]*>Утвердить<\/button>/);
  assert.ok(tech.includes('if(decision==="APPROVED"&&!reviewChecksPass())'));
  assert.ok(tech.includes("Утверждённый TIFF сохранён и принят выбранным контуром. Генерация не запускалась."));
});

test("MG4K handoff cannot bypass Tech Master approval and provenance",()=>{
  for(const token of [
    'body.stage!=="MG4K_MASTER_IMPORTED"',
    'body.master_bit_depth!==16',
    'body.locked!==true',
    "window.MG4KImportEditorial",
    "approved_preview_unavailable",
  ]) assert.ok(importer.includes(token),token);

  for(const token of [
    "verifyEditorialArtisticInput",
    "editorial_import_contract_failed",
    "editorial_lineage",
    "MG4K EDITORIAL IMPORT:",
  ]) assert.ok(worker.includes(token),token);
});

test("universal camera RAW intake preserves no-HDR technical boundaries",()=>{
  const editorial=readFileSync("src/editorial_photo.js","utf8");
  const workflow=readFileSync("src/editorial_tech_workflow.js","utf8");
  const service=readFileSync("editorial_tech_service.py","utf8");
  const raster=readFileSync("scripts/editorial_raster_technical_master.py","utf8");
  const requirements=readFileSync("requirements.txt","utf8");

  for(const token of [
    'MAX_RAW_SOURCE=90*1024*1024',
    'RAW_EXTENSIONS=new Set(["arw","dng","nef","nrw","cr2","cr3","raf","orf","rw2","pef","srw","3fr","iiq","x3f"])',
    '"/api/editorial/upload-raw"',
    'new FixedLengthStream(expectedSize)',
    'sha256:expectedSha',
    'source_kind:"CAMERA_RAW"',
  ]) assert.ok(editorial.includes(token),token);

  assert.ok(workflow.includes("j.source_bytes>90*1024*1024"));
  assert.ok(workflow.includes('qa.source_kind==="CAMERA_RAW"'));
  assert.ok(workflow.includes('endpoint(id,"source-preview")'));
  assert.ok(service.includes("MAX_SOURCE=90*1024*1024"));
  assert.ok(service.includes('digest=hashlib.sha256()'));
  assert.ok(service.includes('while received<length'));

  for(const token of [
    "rawpy.imread",
    "no_auto_bright=True",
    "output_bps=16",
    "gamma=(1, 1)",
    "HDR_applied",
    '"generative_processing": False',
    '"source_kind": source_kind',
    '"raw_decoder": raw_decoder',
    '"source_preview_sha256"',
  ]) assert.ok(raster.includes(token),token);
  assert.match(requirements,/rawpy==0\.28\.0/);
  assert.match(requirements,/lensfunpy==1\.18\.0/);
  assert.match(requirements,/ExifRead>=3\.0,<4/);
  assert.ok(raster.includes("source_metadata"));
  assert.ok(raster.includes("AUTO_CA_CORRECTED"));
  assert.ok(raster.includes("AUTO_VIGNETTING_CORRECTED"));
});

test("TECH MASTER UI accepts RAW but keeps explicit human QA",()=>{
  for(const token of [
    ".arw,.dng,.nef,.nrw,.cr2,.cr3,.raf,.orf,.rw2,.pef,.srw,.3fr,.iiq,.x3f",
    "RAW больше 90 MiB",
    'fetch(base+"/upload-raw"',
    "Проверяю SHA-256 RAW",
    "LibRaw",
    "Хроматические аберрации",
    "Виньетирование",
    "Профиль камеры / объектива",
    'id="intake-approve"',
  ]) assert.ok(tech.includes(token),token);
});


import { providerCreditPreflight, canResumeCreditJob } from "../src/provider_credit_guard.js";
test("credit preflight is read-only and fail-closed for missing account/key budget",async()=>{
 const env={OPENROUTER_API_KEY:"test-key",OPENROUTER_MANAGEMENT_KEY:"test-management"};
 let calls=[];
 const mock=(remaining,balance)=>async(url,init)=>{
   calls.push({url,method:init.method||"GET",auth:init.headers.Authorization});
   return new Response(JSON.stringify({data:url.endsWith("/key")?{limit_remaining:remaining,is_free_tier:false}:{total_credits:5,total_usage:5-balance}}));
 };
 assert.equal((await providerCreditPreflight(env,mock(.1,5))).code,"PROVIDER_CREDITS_EXHAUSTED");
 assert.equal((await providerCreditPreflight(env,mock(null,.1))).code,"PROVIDER_CREDITS_EXHAUSTED");
 assert.equal((await providerCreditPreflight(env,mock(2,2))).ok,true);
 assert.equal((await providerCreditPreflight({OPENROUTER_API_KEY:"key"},mock(2,2))).code,"PROVIDER_CREDIT_CHECK_UNAVAILABLE");
 assert.equal((await providerCreditPreflight(env,mock(undefined,2))).ok,false);
 assert.equal((await providerCreditPreflight(env,async()=>{throw Error("network")})).ok,false);
 assert.ok(calls.every(c=>c.method==="GET"&&c.auth===(c.url.endsWith("/key")?"Bearer test-key":"Bearer test-management")||c.auth==="Bearer key"));
});
test("credit resume is restricted to refused requests without paid artifacts",()=>{
 const job={status:"failed",mode:"generative",diagnostic_code:"PROVIDER_CREDITS_EXHAUSTED",diagnostic_http_status:402,provider_request_count:1,provider_cost_usd:0};
 assert.equal(canResumeCreditJob(job),true);
 assert.equal(canResumeCreditJob({...job,provider_cost_usd:.25}),false);
 assert.equal(canResumeCreditJob({...job,provider_request_count:2}),false);
 assert.equal(canResumeCreditJob({...job,archive_donor_keys:["donor"]}),false);
 assert.equal(canResumeCreditJob({...job,result_key:"result"}),false);
 assert.equal(canResumeCreditJob({...job,diagnostic_code:"PROVIDER_TIMEOUT"}),false);
 assert.equal(canResumeCreditJob({...job,status:"processing"}),false);
 assert.ok(worker.indexOf("const credit=await providerCreditPreflight(env)")<worker.indexOf("const container = await ensureCloudProcessor(env, origin);",worker.indexOf("async function dispatchJobToContainer")));
 assert.ok(worker.includes('onlyIf:new Headers({"If-None-Match":"*"})'));
 assert.ok(worker.includes('resumed_from_job_id:parent.id'));
 assert.ok(main.includes('/resume-credit'));
 const py=readFileSync("v8_safe_appearance.py","utf8");
 const processor=readFileSync("cloud_processor_service.py","utf8");
 assert.ok(py.includes('max_retries = 1 if os.environ.get("MG4K_SINGLE_PAID_ATTEMPT")'));
 assert.ok(py.includes('/api/processor/credit-check'));
 assert.ok(processor.includes('PAID_REPLACEMENT_REQUIRES_EXPLICIT_OPERATOR_ACTION'));
});
