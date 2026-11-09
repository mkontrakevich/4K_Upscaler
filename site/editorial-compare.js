/* MG4K Editorial Photo / native-resolution linked viewer.
 * Pair 1: full genuine unrectified Sony ARW neutral screen image versus 16-bit-derived TECH V9.
 * Pair 2: TECH V9 versus untouched original OpenRouter PNG.
 * No generated image, implicit upscaling, or source reassignment takes place here.
 */
(function(){
"use strict";
const prefix="/api/editorial/view?";
let root=null,stage=null,canvas=null,ctx=null,dialog=null,activeJob="";
const S={source:null,result:null,images:{raw:null,technical:null,editorial:null},pair:"raw-tech",meta:null,open:false,mode:"split",zoom:"fit",scale:1,panX:0,panY:0,split:.5,swap:false,labels:true,single:"result",blinkResult:false,blinkTimer:null,drag:false,dragMode:"pan",lastX:0,lastY:0,loading:false,renderQueued:false,fullNative:false};
const $=(id)=>document.getElementById(id);
const by=(job,kind)=>prefix+"job="+encodeURIComponent(job)+"&kind="+encodeURIComponent(kind);
function mount(){
 if(root)return;
 const wrap=document.createElement("div");
 wrap.innerHTML=[
 '<dialog id="ec-dialog" class="ec-dialog" aria-label="MG 4K Editorial Photo Split Compare">',
 '<div class="ec-shell">',
 '<div class="ec-toolbar">',
 '<div class="ec-title"><strong id="ec-pair-title">DSC09818 / ORIGINAL RAW ↔ TECH V9</strong><span class="ec-subtitle" id="ec-dimensions">Проверка исходных размеров…</span></div>',
 '<div class="ec-controls" aria-label="Выбор пары для проверки">',
 '<button data-ec-pair="raw-tech" class="active" title="Оригинальный RAW без коррекций vs технический RAW V9">01 · RAW / TECH</button>',
 '<button data-ec-pair="tech-editorial" title="Технический мастер V9 vs художественный результат OpenRouter">02 · TECH / EDITORIAL</button>',
 '</div>',
 '<div class="ec-controls" aria-label="Режимы сравнения">',
 '<button data-ec-mode="split" class="active" title="Наложение с перетаскиваемым разделителем">SPLIT</button>',
 '<button data-ec-mode="side" title="Два синхронных изображения рядом">SIDE</button>',
 '<button data-ec-mode="single" title="Просмотр одной версии">SINGLE</button>',
 '<button data-ec-mode="blink" title="Мигание с интервалом 700 мс">BLINK</button>',
 '<span class="ec-control-separator"></span>',
 '<button data-ec-action="swap" title="Поменять местами RAW и EDITORIAL">SWAP</button>',
 '<button data-ec-action="single" title="Переключить левую и правую версии">LEFT / RIGHT</button>',
 '<button data-ec-action="labels" title="Включить и выключить названия">LABELS</button>',
 '</div>',
 '<div class="ec-controls" aria-label="Масштаб">',
 '<button data-ec-zoom="fit" class="active">FIT</button>',
 '<button data-ec-zoom="0.5">50%</button>',
 '<button data-ec-zoom="1">100%</button>',
 '<button data-ec-zoom="2">200%</button>',
 '<button data-ec-zoom="editorial" title="1 пиксель правой версии = 1 пиксель экрана">1:1 RIGHT</button>',
 '<span class="ec-zoom" id="ec-zoom-label">FIT</span>',
 '</div>',
 '<div class="ec-controls">',
 '<button class="ec-screen-btn" data-ec-action="reset" title="Вернуться к центру кадра">RESET</button>',
 '<button class="ec-screen-btn" data-ec-action="fullscreen" title="Полноэкранный режим">FULL</button>',
 '<button id="ec-close" class="ec-close" title="Закрыть сравнение">ЗАКРЫТЬ</button>',
 '</div>',
 '</div>',
 '<div id="ec-stage" class="ec-stage" role="region" tabindex="0" aria-label="Интерактивное сравнение: перетаскивайте разделитель или панорамируйте. Колесо мыши меняет масштаб.">',
 '<canvas id="ec-canvas"></canvas>',
 '<div id="ec-loading" class="ec-loading"><strong>Загружаются оригинальные изображения из приватного хранилища</strong><small>Исходный TIFF остаётся 16-битным, просмотр производится через полноразмерное 8-битное представление</small></div>',
 '</div>',
 '<div class="ec-footer">',
 '<div class="ec-slider"><label for="ec-split-range">РАЗДЕЛИТЕЛЬ</label><input id="ec-split-range" type="range" min="2" max="98" value="50" aria-label="Расположение вертикального разделителя"/><output id="ec-split-percent">50%</output></div>',
 '<span id="ec-help">Перетащите линию — сравнение · Перетащите кадр — панорама · Колесо — масштаб</span>',
 '<span class="ec-warning" id="ec-resolution-warning">Разрешение источника и модели будет показано отдельно</span>',
 '</div>',
 '</div></dialog>'
 ].join("");
 root=wrap.firstElementChild;document.body.appendChild(root);
 dialog=root;stage=$("ec-stage");canvas=$("ec-canvas");ctx=canvas.getContext("2d",{alpha:false});
 root.querySelectorAll("[data-ec-pair]").forEach(b=>b.addEventListener("click",()=>setPair(b.dataset.ecPair)));
 root.querySelectorAll("[data-ec-mode]").forEach(b=>b.addEventListener("click",()=>setMode(b.dataset.ecMode)));
 root.querySelectorAll("[data-ec-zoom]").forEach(b=>b.addEventListener("click",()=>setZoom(b.dataset.ecZoom)));
 root.querySelectorAll("[data-ec-action]").forEach(b=>b.addEventListener("click",()=>action(b.dataset.ecAction)));
 $("ec-close").addEventListener("click",close);
 $("ec-split-range").addEventListener("input",e=>{S.split=Number(e.target.value)/100;updateUI();scheduleDraw()});
 stage.addEventListener("pointerdown",pointerDown);
 stage.addEventListener("pointermove",pointerMove);
 stage.addEventListener("pointerup",pointerUp);
 stage.addEventListener("pointercancel",pointerUp);
 stage.addEventListener("lostpointercapture",()=>{S.drag=false});
 stage.addEventListener("wheel",wheel,{passive:false});
 stage.addEventListener("dblclick",e=>{e.preventDefault();setZoom(S.zoom==="fit"?"1":"fit")});
 dialog.addEventListener("close",cleanup);
 dialog.addEventListener("cancel",()=>cleanup());
 document.addEventListener("keydown",keys);
 if(typeof ResizeObserver==="function")new ResizeObserver(()=>{if(S.open){if(S.zoom==="fit")S.scale=fitScale();scheduleDraw()}}).observe(stage);
 window.addEventListener("resize",()=>{if(S.open){if(S.zoom==="fit")S.scale=fitScale();scheduleDraw()}});
}
function setLoading(text){
 const box=$("ec-loading");if(!box)return;
 box.hidden=false;box.querySelector("strong").textContent=text||"Загрузка…";
}
function image(url){
 return new Promise((resolve,reject)=>{
  const im=new Image();im.decoding="async";im.onload=()=>resolve(im);im.onerror=()=>reject(new Error("Не удалось получить изображение: "+url));im.src=url;
 });
}
async function open(job,requestedPair="raw-tech"){
 mount();
 if(!/^[0-9a-f-]{36}$/i.test(String(job||"")))throw new Error("Некорректный ID задания");
 if(!dialog.open)dialog.showModal();
 S.open=true;S.loading=true;activeJob=job;S.mode="split";S.pair=requestedPair==="tech-editorial"?"tech-editorial":"raw-tech";
 S.split=.5;S.swap=false;S.zoom="fit";S.panX=0;S.panY=0;S.labels=true;S.source=null;S.result=null;
 S.images={raw:null,technical:null,editorial:null};S.single="result";
 stopBlink();setLoading("Загружаются исходный Sony RAW, технический мастер V9 и оригинал OpenRouter…");updateUI();
 try{
  const response=await fetch(by(job,"compare_info"),{cache:"no-store"});
  if(!response.ok)throw new Error("Не удалось получить параметры сравнения ("+response.status+")");
  const info=await response.json();
  if(activeJob!==job||!S.open)return;
  S.meta=info;
  if(info.original_raw_preview?.ready!==true)throw new Error("Не готов полноразмерный исходный ARW: нейтральная проявка должна быть подготовлена без коррекции");
  if(info.fullres_preview?.ready!==true)throw new Error("Не готов полноразмерный RAW Technical V9");
  const [raw,technical,editorial]=await Promise.all([
   image(by(job,"raw_original")),image(by(job,"tech_full")),image(by(job,"candidate"))
  ]);
  if(activeJob!==job||!S.open)return;
  if(raw.naturalWidth!==7028||raw.naturalHeight!==4688)throw new Error("Неверный размер оригинального RAW");
  if(technical.naturalWidth!==6536||technical.naturalHeight!==4360)throw new Error("Неверный размер технического TIFF proxy");
  S.images={raw,technical,editorial};
  S.fullNative=true;S.loading=false;$("ec-loading").hidden=true;
  applyPair();setZoom("fit");updateUI();scheduleDraw();stage.focus({preventScroll:true});
 }catch(err){S.loading=false;setLoading("Ошибка загрузки: "+String(err.message||err));$("ec-resolution-warning").textContent="Сравнение не открыто: "+String(err.message||err)}
}
function applyPair(){
 if(S.pair==="raw-tech"){S.source=S.images.raw;S.result=S.images.technical}
 else{S.source=S.images.technical;S.result=S.images.editorial}
}
function setPair(pair){
 if(pair!=="raw-tech"&&pair!=="tech-editorial")return;
 if(S.pair===pair)return;
 S.pair=pair;S.swap=false;S.single="result";applyPair();stopBlink();
 if(S.mode==="blink")setMode("blink");
 if(S.zoom==="fit"){S.scale=fitScale();S.panX=0;S.panY=0}
 else if(S.zoom==="editorial")S.scale=S.result.naturalWidth/6536;
 updateUI();scheduleDraw();
}
function scaleLimits(){return {min:.015,max:4}}
function view(){const r=stage?.getBoundingClientRect();return {w:Math.max(1,r?.width||1),h:Math.max(1,r?.height||1)}}
function paneWidth(){const v=view();return S.mode==="side"?(v.w-8)/2:v.w}
function fitScale(){if(!S.source)return 1;const v=view();return Math.min((paneWidth()-20)/S.source.naturalWidth,(v.h-20)/S.source.naturalHeight)}
function setZoom(z){
 if(!S.source)return;
 S.zoom=z;
 if(z==="fit")S.scale=fitScale();
 else if(z==="editorial")S.scale=S.result.naturalWidth/S.source.naturalWidth;
 else S.scale=Number(z);
 const lim=scaleLimits();S.scale=Math.min(lim.max,Math.max(lim.min,S.scale));S.panX=0;S.panY=0;updateUI();scheduleDraw()
}
function stopBlink(){if(S.blinkTimer!==null){clearInterval(S.blinkTimer);S.blinkTimer=null}}
function setMode(mode){
 if(!["split","side","single","blink"].includes(mode))return;
 stopBlink();S.mode=mode;
 if(mode==="blink"){S.blinkResult=false;S.blinkTimer=setInterval(()=>{if(!S.open)return;S.blinkResult=!S.blinkResult;scheduleDraw()},700)}
 if(S.zoom==="fit"){S.scale=fitScale();S.panX=0;S.panY=0}
 updateUI();scheduleDraw();
}
function action(name){
 if(name==="swap")S.swap=!S.swap;
 if(name==="single"){S.single=S.single==="result"?"source":"result";if(S.mode!=="single")setMode("single")}
 if(name==="labels")S.labels=!S.labels;
 if(name==="reset"){S.panX=0;S.panY=0;if(S.zoom==="fit")S.scale=fitScale()}
 if(name==="fullscreen"){
  if(document.fullscreenElement)document.exitFullscreen?.();
  else dialog.requestFullscreen?.().catch(()=>{});
 }
 updateUI();scheduleDraw();
}
function updateUI(){
 if(!dialog)return;
 dialog.querySelectorAll("[data-ec-mode]").forEach(b=>b.classList.toggle("active",b.dataset.ecMode===S.mode));
 dialog.querySelectorAll("[data-ec-zoom]").forEach(b=>b.classList.toggle("active",b.dataset.ecZoom===S.zoom));
 const sl=$("ec-split-range");sl.value=String(Math.round(S.split*100));sl.disabled=S.mode!=="split";
 $("ec-split-percent").textContent=Math.round(S.split*100)+"%";
 $("ec-zoom-label").textContent=S.zoom==="fit"?"FIT":S.zoom==="editorial"?"1:1 EDIT":Math.round(S.scale*100)+"% TECH";
 if(S.source&&S.result){
  const a=S.source,b=S.result;
  $("ec-dimensions").textContent="TECH RAW V9 "+a.naturalWidth+"×"+a.naturalHeight+" · EDITORIAL "+b.naturalWidth+"×"+b.naturalHeight;
  $("ec-resolution-warning").textContent=S.fullNative?"Слева 6536px из 16-битного TIFF (8-bit display proxy). Справа native OpenRouter: "+b.naturalWidth+"px; 100% TECH увеличивает его, не создавая деталей.":"Полный источник ещё недоступен. Сейчас используется 2000px техническое превью; статус 100% TECH ограничен.";
 }else $("ec-dimensions").textContent="Ожидание исходных размеров…";
 $("ec-help").textContent=S.mode==="split"?"Перетаскивайте вертикальную линию · drag кадра — панорама · колесо — масштаб":S.mode==="side"?"Синхронный zoom и pan в двух окнах · Fit / 100% / 1:1 EDIT":S.mode==="blink"?"Blink: чередование TECH и EDITORIAL каждые 700 мс":"Single: кнопка TECH / EDIT переключает исходник и художественную версию";
}
function scheduleDraw(){
 if(!S.open||S.renderQueued)return;S.renderQueued=true;
 requestAnimationFrame(()=>{S.renderQueued=false;draw()});
}
function canvasContext(){
 const v=view(),maxPixels=14000000;
 const dpr=Math.max(1,Math.min(window.devicePixelRatio||1,2,Math.sqrt(maxPixels/(v.w*v.h))));
 const cw=Math.max(1,Math.round(v.w*dpr)),ch=Math.max(1,Math.round(v.h*dpr));
 if(canvas.width!==cw||canvas.height!==ch){canvas.width=cw;canvas.height=ch}
 ctx.setTransform(dpr,0,0,dpr,0,0);return v;
}
function drawImage(img,pane,additionalClip){
 if(!img)return;
 ctx.save();ctx.beginPath();ctx.rect(pane.x,pane.y,pane.w,pane.h);ctx.clip();
 if(additionalClip){ctx.beginPath();ctx.rect(additionalClip.x,additionalClip.y,additionalClip.w,additionalClip.h);ctx.clip()}
 const rW=S.source.naturalWidth,rH=S.source.naturalHeight;
 const refW=rW*S.scale,refH=rH*S.scale;
 // Keep own aspect ratio, center-align by source-frame coordinates.
 const fit=Math.min(rW/img.naturalWidth,rH/img.naturalHeight);
 const iw=img.naturalWidth*fit*S.scale,ih=img.naturalHeight*fit*S.scale;
 const x=pane.x+(pane.w-refW)/2+S.panX+(refW-iw)/2;
 const y=pane.y+(pane.h-refH)/2+S.panY+(refH-ih)/2;
 ctx.imageSmoothingEnabled=true;ctx.imageSmoothingQuality="high";
 ctx.drawImage(img,x,y,iw,ih);
 ctx.restore();
}
function label(pane,txt,position="left"){
 if(!S.labels)return;
 ctx.save();ctx.font="12px Consolas,monospace";const w=Math.min(pane.w-16,ctx.measureText(txt).width+22);
 const x=position==="right"?pane.x+pane.w-w-12:pane.x+12,y=pane.y+14;
 ctx.fillStyle="rgba(3,13,23,.85)";ctx.fillRect(x,y,Math.max(60,w),28);
 ctx.fillStyle="#d8edf7";ctx.fillText(txt,x+10,y+19);ctx.restore();
}
function draw(){
 if(!S.open||!canvas||!ctx)return;
 const {w,h}=canvasContext();
 ctx.fillStyle="#02080d";ctx.fillRect(0,0,w,h);
 if(!S.source||!S.result)return;
 const A=S.swap?S.result:S.source,B=S.swap?S.source:S.result;
 const an=S.swap?"EDITORIAL":"TECH RAW V9",bn=S.swap?"TECH RAW V9":"EDITORIAL";
 const full={x:0,y:0,w,h};
 if(S.mode==="split"){
  drawImage(A,full);
  const boundary=w*S.split;
  drawImage(B,full,{x:boundary,y:0,w:w-boundary,h});
  ctx.save();ctx.fillStyle="#e4f9ff";ctx.fillRect(boundary-1.5,0,3,h);
  ctx.fillStyle="#06a8d1";ctx.fillRect(boundary-7,h/2-24,14,48);ctx.fillStyle="#fff";ctx.fillRect(boundary-2,h/2-12,4,24);ctx.restore();
  label(full,an);label(full,bn,"right");
 }else if(S.mode==="side"){
  const pw=(w-8)/2,one={x:0,y:0,w:pw,h},two={x:pw+8,y:0,w:pw,h};
  drawImage(A,one);drawImage(B,two);ctx.fillStyle="#253b4b";ctx.fillRect(pw,0,8,h);label(one,an);label(two,bn);
 }else if(S.mode==="single"){
  const img=S.single==="source"?S.source:S.result;drawImage(img,full);label(full,S.single==="source"?"TECH RAW V9":"EDITORIAL / OPENROUTER");
 }else{
  const img=S.blinkResult?B:A;drawImage(img,full);label(full,S.blinkResult?bn:an);
 }
}
function pointerDown(e){
 if(S.loading||!S.source)return;
 const rect=stage.getBoundingClientRect(),x=e.clientX-rect.left,y=e.clientY-rect.top;
 S.dragMode=S.mode==="split"&&Math.abs(x-rect.width*S.split)<28?"split":"pan";
 S.drag=true;S.lastX=x;S.lastY=y;stage.setPointerCapture(e.pointerId);e.preventDefault();
}
function pointerMove(e){
 if(!S.drag)return;
 const rect=stage.getBoundingClientRect(),x=e.clientX-rect.left,y=e.clientY-rect.top;
 if(S.dragMode==="split"){S.split=Math.max(.02,Math.min(.98,x/rect.width));updateUI()}
 else{S.panX+=x-S.lastX;S.panY+=y-S.lastY}
 S.lastX=x;S.lastY=y;scheduleDraw();e.preventDefault();
}
function pointerUp(e){S.drag=false;if(stage.hasPointerCapture?.(e.pointerId))stage.releasePointerCapture(e.pointerId)}
function wheel(e){
 if(!S.source||S.loading)return;e.preventDefault();
 const rect=stage.getBoundingClientRect(),v=view(),x=e.clientX-rect.left,y=e.clientY-rect.top;
 const pane=S.mode==="side"?{x:x>=v.w/2?(v.w+8)/2:0,w:(v.w-8)/2}:{x:0,w:v.w};
 const px=x-pane.x,oldScale=S.scale;
 const next=Math.max(scaleLimits().min,Math.min(scaleLimits().max,oldScale*Math.exp(-e.deltaY*.0013)));
 const refX=(px-(pane.w-S.source.naturalWidth*oldScale)/2-S.panX)/oldScale;
 const refY=(y-(v.h-S.source.naturalHeight*oldScale)/2-S.panY)/oldScale;
 S.panX=px-(pane.w-S.source.naturalWidth*next)/2-refX*next;
 S.panY=y-(v.h-S.source.naturalHeight*next)/2-refY*next;
 S.scale=next;S.zoom="custom";updateUI();scheduleDraw();
}
function cleanup(){S.open=false;activeJob="";S.drag=false;stopBlink();if(document.fullscreenElement===dialog)document.exitFullscreen?.();S.source=null;S.result=null;S.meta=null;S.fullNative=false}
function close(){if(dialog?.open)dialog.close();else cleanup()}
function keys(e){
 if(!S.open||["INPUT","TEXTAREA","SELECT"].includes(document.activeElement?.tagName))return;
 const k=e.key.toLowerCase();
 if(k==="escape"){e.preventDefault();close()}
 else if(k==="1")setMode("split");else if(k==="2")setMode("side");else if(k==="3")setMode("single");else if(k==="4")setMode("blink");
 else if(k==="f")setZoom("fit");else if(k==="z")setZoom("1");else if(k==="x")setZoom("2");
 else if(k==="s")action("swap");else if(k==="l")action("labels");
 else if(k==="0"||k==="r")action("reset");else if(k===" "){if(S.mode==="single"){e.preventDefault();action("single")}}
}
window.EditorialCompare={open,close};
})();
