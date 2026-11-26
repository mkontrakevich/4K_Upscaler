import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext,Script} from "node:vm";

const file=readFileSync("site/editorial-geometry-preview.js","utf8");
const root={};
runInNewContext(file,{window:root,Math,Number,Array,Error,Uint8ClampedArray});
const helper=root.MG4KGeometryPreview;
const quad=[[.18,.23],[.82,.225],[.825,.77],[.175,.775]];

test("browser geometry preview validates four corners without calling an external service",()=>{
 assert.ok(helper);
 const p=helper.plan(quad,660,460);
 assert.ok(p.cropZoom>=1&&p.cropZoom<=1.12);
 assert.ok(p.cropFraction>=0&&p.cropFraction<.21);
 assert.ok(p.maxNormalizedDisplacement>0&&p.maxNormalizedDisplacement<=.18);
 assert.ok(Array.isArray(p.matrix)&&p.matrix.length===9);
 assert.ok(Array.isArray(p.inverse)&&p.inverse.length===9);
 assert.ok(!file.includes("fetch("));
 assert.ok(!file.includes("localStorage"));
 assert.ok(!file.includes("APPROVED"));
 assert.ok(!file.includes("OpenRouter"));
});
test("geometry preview has the same fail-closed corner rules as server",()=>{
 assert.throws(()=>helper.plan(quad.slice(0,3),660,460),/четыре/);
 assert.throws(()=>helper.plan([quad[0],quad[2],quad[1],quad[3]],660,460),/порядок|вырождена/);
 assert.throws(()=>helper.plan([[.4,.4],[.45,.4],[.45,.45],[.4,.45]],660,460),/вырождена|слишком мала/);
 assert.throws(()=>helper.plan(quad,200,200),/разрешение/);
 assert.throws(()=>helper.plan([[0,.23],quad[1],quad[2],quad[3]],660,460),/внутри/);
});
test("browser canvas genuinely renders different corrected pixels without touching original",()=>{
 let sourceImageData=null,output=null;
 function originalCanvas(){
  const canvas={width:0,height:0};
  canvas.getContext=()=>({
   clearRect:()=>{},
   drawImage:()=>{},
   getImageData:()=>{
    const data=new Uint8ClampedArray(canvas.width*canvas.height*4);
    for(let y=0;y<canvas.height;y++)for(let x=0;x<canvas.width;x++){
      const i=(y*canvas.width+x)*4;
      data[i]=(x*23+y*3)%256;
      data[i+1]=(y*13+x*2)%256;
      data[i+2]=(x+y)%256;
      data[i+3]=255;
    }
    sourceImageData=data.slice();
    return {width:canvas.width,height:canvas.height,data};
   }
  });
  return canvas;
 }
 function correctedCanvas(){
  const canvas={width:0,height:0};
  canvas.getContext=()=>({
   createImageData:(w,h)=>({width:w,height:h,data:new Uint8ClampedArray(w*h*4)}),
   putImageData:img=>{output=img.data.slice()}
  });
  return canvas;
 }
 const before=originalCanvas(),after=correctedCanvas();
 const p=helper.render({naturalWidth:660,naturalHeight:460},before,after,quad);
 assert.equal(before.width,after.width);
 assert.equal(before.height,after.height);
 assert.equal(before.width,660);
 assert.ok(output&&sourceImageData&&output.length===sourceImageData.length);
 let changed=0;for(let i=0;i<output.length;i+=4)
  if(output[i]!==sourceImageData[i]||output[i+1]!==sourceImageData[i+1])changed++;
 assert.ok(changed>before.width*before.height*.15,"pixels should visibly differ");
 assert.equal(sourceImageData.length,output.length);
 assert.ok(p.maxNormalizedDisplacement<.18);
});
test("editor has immediate visible entry, reversible preview and explicit final commit",()=>{
 const html=readFileSync("site/editorial-photo.html","utf8");
 const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(x=>x[1]);
 assert.equal(new Set(ids).size,ids.length,"UI IDs must be unique");
 assert.match(html,/id="intake-geometry-first"/);
 assert.match(html,/id="intake-geometry-start"/);
 assert.match(html,/id="intake-geometry-preview"/);
 assert.match(html,/id="intake-geometry-render"/);
 assert.match(html,/id="intake-geometry-preview-range"/);
 assert.match(html,/id="intake-geometry-close"/);
 assert.match(html,/id="intake-geometry-submit"/);
 assert.match(html,/src="\/editorial-geometry-preview\.js"/);
 assert.ok(html.indexOf('id="intake-geometry-first"')<
   html.indexOf('id="intake-source-verification"'));
 assert.ok(html.indexOf('id="intake-geometry-panel"')<
   html.indexOf('id="intake-revision-panel"'));
 assert.match(html,/commit_parent_return:intakeState\?\.can_rework===true/);
 assert.match(html,/geometryPreviewReady/);
 assert.match(html,/Реальный TIFF|реальный TIFF/);
 assert.match(html,/id="intake-tech-details"/);
 const scripts=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
 scripts.forEach(x=>new Script(x));
});

test("rework geometry entry remains usable during polling and import is explicitly separate",()=>{
 const html=readFileSync("site/editorial-photo.html","utf8");
 assert.match(html,/id="intake-geometry-open"/);
 assert.match(html,/e\("intake-geometry-open"\)\.addEventListener\("click",\(\)=>showGeometryEditor/);
 assert.match(html,/e\("intake-rework-action"\)\.hidden=data\.can_geometry_rework!==true/);
 assert.match(html,/if\(!data\.can_geometry_rework\)e\("intake-geometry-panel"\)\.hidden=true/);
 assert.doesNotMatch(html,/legacy four-corner tool is not in standard user flow/);
 assert.match(html,/Резерв: загрузить уже исправленный файл с компьютера/);
 assert.match(html,/id="intake-rework-progress" role="status" aria-live="polite"/);
 assert.match(html,/\[REDACTED\]/);
});
