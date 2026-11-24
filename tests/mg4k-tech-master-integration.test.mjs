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
    "Утвердить Tech Master",
    "Сохранить и передать в выбранный контур",
    "Открыть Tech Master в MG4K",
    'id="intake-check-preview"',
    'id="intake-check-geometry"',
    'id="intake-check-materials"',
    "intakeHandoff",
  ]) assert.ok(tech.includes(token),token);
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
    'RAW_EXTENSIONS=new Set(["arw","dng","nef","cr2","cr3","raf","orf","rw2"])',
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
});

test("TECH MASTER UI accepts RAW but keeps explicit human QA",()=>{
  for(const token of [
    ".arw,.dng,.nef,.cr2,.cr3,.raf,.orf,.rw2",
    "RAW больше 90 MiB",
    'fetch(base+"/upload-raw"',
    "Проверяю SHA-256 RAW",
    "LibRaw",
    "Утвердить Tech Master",
  ]) assert.ok(tech.includes(token),token);
});
