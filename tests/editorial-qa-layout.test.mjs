import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { Script } from "node:vm";

const html=readFileSync("site/editorial-photo.html","utf8");
const between=(a,b)=>html.slice(html.indexOf(a),html.indexOf(b));
const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(x=>x[1]);

test("current user QA is the primary wide workspace, archival V10 is collapsed",()=>{
  const layout=html.indexOf('class="grid editorial-layout"');
  const primary=html.indexOf('class="stack workspace-primary"');
  const qa=html.indexOf('class="panel qa-panel"');
  const archive=html.indexOf('id="legacy-archive"');
  const archiveMaster=html.indexOf('Technical Master V10 · NO HDR · DSC09818');
  const sidebar=html.indexOf('class="stack workspace-rail"');
  assert.ok(layout>=0 && primary>layout && qa>primary && archive>qa);
  assert.ok(archiveMaster>archive);
  assert.ok(sidebar>layout&&sidebar<primary);
  assert.match(html,/grid-template-areas:"primary rail"/);
  assert.match(html,/\.workspace-primary\{grid-area:primary/);
  assert.match(html,/\.workspace-rail\{grid-area:rail/);
  assert.match(html,/<details class="archive-bundle" id="legacy-archive">/);
  assert.doesNotMatch(html,/<details class="archive-bundle" id="legacy-archive" open/);
  assert.ok(html.indexOf('id="intake-preview-frame"')<archive);
});
test("live preview and source comparison are full width and preserve user QA controls",()=>{
  for(const id of ["intake-id","intake-status","intake-preview-frame","intake-image-workspace","intake-source-frame",
    "intake-source-preview","intake-show-source","intake-approve","intake-reject","intake-download",
    "intake-handoff","intake-visual-checks","intake-rework-form","intake-revision-panel","intake-comparison"])
    assert.ok(ids.includes(id),"missing "+id);
  assert.equal(ids.length,new Set(ids).size,"Duplicate element IDs");
  assert.match(html,/qa-image-workspace\.is-compare\{grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
  assert.match(html,/function toggleSourceComparison\(\)/);
  assert.match(html,/fetch\(base\+"\/job\/source\?job="/);
  assert.match(html,/e\("intake-show-source"\)\.addEventListener\("click",toggleSourceComparison\)/);
  assert.match(html,/id="intake-preview-native"/);
  assert.match(html,/id="intake-source-native"/);
  assert.match(html,/id="intake-check-geometry"/);
  assert.match(html,/id="intake-check-materials"/);
  assert.match(html,/id="intake-rework-note"/);
});
test("responsive desktop/tablet/mobile and genuine empty state",()=>{
  assert.match(html,/@media\(max-width:800px\)\{\.grid\.editorial-layout\{grid-template-columns:minmax\(0,1fr\);grid-template-areas:"primary" "rail"\}\}/);
  assert.match(html,/@media\(max-width:600px\)\{\.qa-image-workspace\.is-compare\{grid-template-columns:1fr\}/);
  assert.match(html,/\[hidden\]\{display:none!important\}/);
  assert.match(html,/id="intake-progress"[^>]*value="0" hidden/);
  assert.match(html,/id="intake-empty"/);
  assert.match(html,/bar\.value=0;bar\.hidden=true;/);
  assert.match(html,/if\(recent\)activateIntake\(recent\)/);
});
test("inline page script compiles without requiring browser side effects",()=>{
 const scripts=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(x=>x[1]);
 assert.ok(scripts.length);
 scripts.forEach(s=>new Script(s));
});
