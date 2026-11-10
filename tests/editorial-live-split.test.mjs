import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext,Script} from "node:vm";
const html=readFileSync("site/editorial-photo.html","utf8");
const src=readFileSync("site/editorial-split.js","utf8");
function getFixture(){
 const elements=new Map(),buttons=[];
 function element(id){
  if(elements.has(id))return elements.get(id);
  const attrs={},listeners={},cls=new Set();
  const el={id,hidden:false,disabled:false,value:"50",textContent:"",href:"#",style:{},dataset:{},
    clientWidth:900,clientHeight:500,rect:{width:900,height:500,left:0,top:0},
    parentElement:{hidden:false},listeners,attrs,
    classList:{add:k=>cls.add(k),remove:k=>cls.delete(k),contains:k=>cls.has(k),toggle:(k,v)=>v?cls.add(k):cls.delete(k)},
    setAttribute:(k,v)=>{attrs[k]=v},
    addEventListener:(k,fn)=>{listeners[k]=fn},
    getBoundingClientRect(){return this.rect},
    setPointerCapture:()=>{},releasePointerCapture:()=>{}
  };
  elements.set(id,el);return el;
 }
 const root=element("intake-split-viewer");
 for(const mode of ["source","tech","split"]){
  const btn=element("mode-"+mode);btn.dataset.intakeMode=mode;buttons.push(btn);
 }
 root.querySelector=s=>element(s.slice(1));
 root.querySelectorAll=s=>s==="[data-intake-mode]"?buttons:[];
 root.requestFullscreen=async()=>{};
 const canvas=element("intake-split-canvas");
 const calls={drawImage:0,clip:0,moveTo:[],lineTo:[]};
 canvas.getContext=()=>({
  setTransform:()=>{},fillRect:()=>{},save:()=>{},restore:()=>{},
  beginPath:()=>{},rect:()=>{},clip:()=>calls.clip++,
  moveTo:x=>calls.moveTo.push(x),lineTo:x=>calls.lineTo.push(x),
  stroke:()=>{},drawImage:()=>calls.drawImage++,
  fillStyle:"",strokeStyle:"",lineWidth:0,
  imageSmoothingEnabled:true,imageSmoothingQuality:"high"
 });
 const doc={fullscreenElement:null,addEventListener:()=>{},removeEventListener:()=>{},exitFullscreen:()=>{}};
 const win={devicePixelRatio:1,ResizeObserver:class{observe(){}disconnect(){}},
  addEventListener:()=>{},removeEventListener:()=>{}};
 class TestImage{
  naturalWidth=2561;naturalHeight=1287;
  set src(url){
   if(url.includes("odd"))this.naturalWidth=2000;
   if(url.includes("bad")){this.onerror?.();return}
   this.onload?.();
  }
 }
 runInNewContext(src,{window:win,Image:TestImage,document:doc,Error,Math,Number,String,Promise,Uint8Array});
 const viewer=win.MG4KEditorialSplit.create(root);
 return {viewer,win,root,canvas,buttons,calls,elements,element};
}
test("inline current-photo split uses one pixel coordinate space and three modes",async()=>{
 const f=getFixture();const ok=await f.viewer.setImages({sourceUrl:"source",techUrl:"tech"});
 assert.equal(ok,true);
 assert.deepEqual({...f.viewer.getState()},{ready:true,aligned:true,mode:"split",position:.5,zoom:"fit"});
 assert.ok(f.calls.drawImage>=2,"split should draw both source and tech");
 assert.ok(f.calls.clip>=1,"technical half requires clipping");
 f.viewer.setMode("source");assert.equal(f.viewer.getState().mode,"source");
 f.viewer.setMode("tech");assert.equal(f.viewer.getState().mode,"tech");
 f.viewer.setMode("split");assert.equal(f.viewer.getState().mode,"split");
 f.viewer.setPosition(.72);assert.equal(f.viewer.getState().position,.72);
 assert.equal(f.element("intake-split-range").value,"72");
 assert.equal(f.element("intake-split-percent").textContent,"72%");
 f.viewer.setZoom("native");assert.equal(f.viewer.getState().zoom,"manual");
 assert.match(f.element("intake-split-zoom-label").textContent,/100%/);
 f.viewer.setZoom("fit");assert.equal(f.viewer.getState().zoom,"fit");
 f.viewer.reset();assert.equal(f.viewer.getState().ready,false);
 assert.equal(f.root.hidden,true);
});
test("dimensions mismatch blocks pixel overlay rather than inventing homography",async()=>{
 const f=getFixture();await f.viewer.setImages({sourceUrl:"source",techUrl:"odd-tech"});
 assert.equal(f.viewer.getState().aligned,false);
 assert.equal(f.viewer.getState().mode,"tech");
 assert.equal(f.buttons[2].disabled,true);
 f.viewer.setMode("split");assert.equal(f.viewer.getState().mode,"tech");
 assert.match(f.element("intake-split-notice").textContent,/сплит заблокирован/i);
});
test("bad decode does not produce a false ready state",async()=>{
 const f=getFixture();const ok=await f.viewer.setImages({sourceUrl:"source",techUrl:"bad"});
 assert.equal(ok,false);assert.equal(f.viewer.getState().ready,false);
});
test("split drag and keyboard-accessible native slider update same separator",async()=>{
 const f=getFixture();await f.viewer.setImages({sourceUrl:"source",techUrl:"tech"});
 const ev=(clientX,pointerId=1)=>({clientX,clientY:20,pointerId,button:0,shiftKey:false,preventDefault:()=>{}});
 f.canvas.listeners.pointerdown(ev(450));
 f.canvas.listeners.pointermove(ev(612));
 f.canvas.listeners.pointerup(ev(612));
 assert.equal(f.viewer.getState().position,.68);
 const slider=f.element("intake-split-range");slider.value="30";slider.listeners.input();
 assert.equal(f.viewer.getState().position,.3);
 assert.equal(slider.attrs["aria-valuetext"],"30% исходника слева");
});
test("current QA UI has split tabs, native fullscreen, honest preview and unchanged QA gates",()=>{
 const pageIds=[...html.matchAll(/\bid="([^"]+)"/g)].map(x=>x[1]);
 assert.equal(new Set(pageIds).size,pageIds.length,"duplicate IDs");
 assert.ok(!pageIds.includes("intake-show-source"));
 assert.doesNotMatch(html,/Показать два кадра рядом|toggleSourceComparison/);
 for(const id of ["intake-split-viewer","intake-split-canvas","intake-split-range","intake-split-native",
  "intake-split-fit","intake-split-fullscreen","intake-split-handle","intake-source-verification",
  "intake-approve","intake-reject","intake-download","intake-handoff","intake-check-geometry",
  "intake-check-materials","intake-rework-form"])assert.ok(pageIds.includes(id),id);
 for(const mode of ["source","tech","split"])
   assert.ok(html.includes('data-intake-mode="'+mode+'"'));
 assert.match(html,/src="\/editorial-split\.js"/);
 assert.match(html,/startInlineSplit\(id,sha\)/);
 assert.match(html,/fetch\(base\+"\/job\/source\?job="/);
 assert.match(html,/fetch\(base\+"\/job\/preview\?job="/);
 assert.match(html,/qa-image-workspace" id="intake-image-workspace" hidden/);
 assert.match(html,/aria-label="Положение вертикальной границы сплита"/);
 assert.match(src,/requestFullscreen/);
 assert.match(src,/setPointerCapture/);
 assert.match(src,/range\.addEventListener\("input"/);
 assert.match(src,/context\.rect\(width\*state\.position/);
 assert.match(src,/drawImage\(state\.source,dx,dy,w,h\)/);
 assert.match(src,/drawImage\(state\.tech,dx,dy,w,h\)/);
 assert.doesNotMatch(src,/OpenRouter|fetch\(|paid|post\(/i);
 const scripts=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(x=>x[1]);
 scripts.forEach(s=>new Script(s));
});
