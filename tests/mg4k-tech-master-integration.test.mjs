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
    "MG 4K / TECH MASTER",
    "NO HDR",
    "Утвердить Tech Master",
    "Передать утверждённый Tech Master в MG4K",
    "Открыть Tech Master в MG4K",
    'id="intake-check-preview"',
    'id="intake-check-geometry"',
    'id="intake-check-materials"',
    "intakeHandoff",
  ]) assert.ok(tech.includes(token),token);
  assert.ok(tech.includes("16-битный TIFF перенесён в MG 4K. Генерация не запускалась."));
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
