/* Current-job, source-locked QA comparison. Historical DSC09818 viewer is separate.
 * Neither image is altered, resampled offline nor sent to an AI endpoint.
 */
(function (global) {
  "use strict";
  const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
  const aligned=(a,b)=>!!(a&&b&&a.naturalWidth>0&&a.naturalHeight>0&&
    a.naturalWidth===b.naturalWidth&&a.naturalHeight===b.naturalHeight);
  const byId=(root,id)=>root.querySelector("#"+id);

  function create(root) {
    if(!root)throw Error("editorial_split_root_missing");
    const canvas=byId(root,"intake-split-canvas");
    const viewport=byId(root,"intake-split-viewport");
    const range=byId(root,"intake-split-range");
    const handle=byId(root,"intake-split-handle");
    const percent=byId(root,"intake-split-percent");
    const notice=byId(root,"intake-split-notice");
    const zoomLabel=byId(root,"intake-split-zoom-label");
    const nativeSource=byId(root,"intake-split-source-native");
    const nativeTech=byId(root,"intake-split-tech-native");
    const state={
      source:null,tech:null,mode:"split",position:.5,zoom:"fit",
      scale:1,panX:0,panY:0,drag:null,ready:false,aligned:false,
      generation:0,disposed:false
    };
    const buttons=[...root.querySelectorAll("[data-intake-mode]")];
    const context=canvas.getContext("2d",{alpha:false});
    if(!context)throw Error("split_canvas_2d_unavailable");

    function image(url) {
      return new Promise((resolve,reject)=>{
        const img=new Image();img.decoding="async";
        img.onload=()=>resolve(img);
        img.onerror=()=>reject(Error("split_image_decode_failed"));
        img.src=url;
      });
    }
    function fit() {
      const ref=state.mode==="source"?state.source:state.tech||state.source;
      if(!ref)return 1;
      const rect=viewport.getBoundingClientRect();
      return Math.min(Math.max(1,rect.width)/ref.naturalWidth,
        Math.max(1,rect.height)/ref.naturalHeight);
    }
    function dimensionsText(){
      if(!state.ready)return "";
      const a=state.source,b=state.tech;
      return "Источник "+a.naturalWidth+"×"+a.naturalHeight+" · Tech Master "+
        b.naturalWidth+"×"+b.naturalHeight;
    }
    function renderToolbar() {
      root.hidden=!state.ready;
      buttons.forEach(button=>{
        const on=state.mode===button.dataset.intakeMode;
        button.setAttribute("aria-pressed",on?"true":"false");
        button.classList.toggle("active",on);
        button.disabled=button.dataset.intakeMode==="split"&&!state.aligned;
      });
      range.disabled=state.mode!=="split"||!state.aligned;
      range.value=String(Math.round(state.position*100));
      range.setAttribute("aria-valuetext",Math.round(state.position*100)+"% исходника слева");
      percent.textContent=String(Math.round(state.position*100))+"%";
      handle.hidden=state.mode!=="split"||!state.aligned;
      range.parentElement.hidden=state.mode!=="split"||!state.aligned;
      zoomLabel.textContent="Масштаб "+(state.zoom==="fit"?"FIT":Math.round(state.scale*100)+"%")+
        " · "+dimensionsText();
      notice.textContent=state.aligned?
        "Пиксельное совмещение без изменения геометрии. Перетащите разделитель; Shift+перетаскивание — панорама, Ctrl+колесо — масштаб.":
        "Размеры исходника и Tech Master различаются. Точный сплит заблокирован; сравнивайте отдельно, не подтверждая совмещение.";
    }
    function drawImage(img,x,y,w,h){
      context.drawImage(img,x,y,w,h);
    }
    function draw(){
      if(!state.ready||state.disposed||root.hidden)return;
      const rect=viewport.getBoundingClientRect();
      const width=Math.max(1,rect.width),height=Math.max(1,rect.height);
      const pixelRatio=clamp(global.devicePixelRatio||1,1,2);
      const ratio=Math.min(pixelRatio,Math.sqrt(16000000/(width*height)));
      const pw=Math.max(1,Math.round(width*ratio)),ph=Math.max(1,Math.round(height*ratio));
      if(canvas.width!==pw||canvas.height!==ph){canvas.width=pw;canvas.height=ph;}
      context.setTransform(ratio,0,0,ratio,0,0);
      context.fillStyle="#07131f";context.fillRect(0,0,width,height);
      const ref=state.mode==="source"?state.source:state.tech;
      const scale=state.zoom==="fit"?fit():state.scale;
      if(state.zoom==="fit"){state.panX=0;state.panY=0;}
      const dx=(width-ref.naturalWidth*scale)/2+state.panX;
      const dy=(height-ref.naturalHeight*scale)/2+state.panY;
      const w=ref.naturalWidth*scale,h=ref.naturalHeight*scale;
      context.save();
      context.imageSmoothingEnabled=true;
      context.imageSmoothingQuality="high";
      if(state.mode==="source"){
        drawImage(state.source,dx,dy,w,h);
      }else if(state.mode==="tech"||!state.aligned){
        drawImage(state.tech,dx,dy,w,h);
      }else{
        // Identical x/y/scale for both bitmaps: a genuine pixel-aligned overlay.
        drawImage(state.source,dx,dy,w,h);
        context.save();context.beginPath();
        context.rect(width*state.position,0,width*(1-state.position),height);context.clip();
        drawImage(state.tech,dx,dy,w,h);
        context.restore();
        const splitX=width*state.position;
        context.strokeStyle="#e7f9ff";context.lineWidth=2;
        context.beginPath();context.moveTo(splitX,0);context.lineTo(splitX,height);context.stroke();
      }
      context.restore();
      handle.style.left=(state.position*100)+"%";
      renderToolbar();
    }
    function setMode(mode){
      if(!["source","tech","split"].includes(mode)||!state.ready)return;
      if(mode==="split"&&!state.aligned)return;
      state.mode=mode;
      if(state.zoom==="fit"){state.panX=0;state.panY=0;}
      draw();
    }
    function setPosition(p){
      state.position=clamp(Number(p),.02,.98);draw();
    }
    function setZoom(zoom){
      if(!state.ready)return;
      if(zoom==="fit"){
        state.zoom="fit";state.panX=0;state.panY=0;
      }else {
        const previous=state.zoom==="fit"?fit():state.scale;
        state.zoom="manual";
        state.scale=zoom==="native"?1:clamp(previous*Number(zoom),.05,8);
        state.panX=0;state.panY=0;
      }
      draw();
    }
    async function setImages({sourceUrl,techUrl}){
      state.generation++;
      const requestId=state.generation;
      state.ready=false;root.hidden=true;
      try{
        if(!sourceUrl||!techUrl)throw Error("split_images_unavailable");
        const imgs=await Promise.all([image(sourceUrl),image(techUrl)]);
        if(requestId!==state.generation||state.disposed)return false;
        state.source=imgs[0];state.tech=imgs[1];
        state.aligned=aligned(state.source,state.tech);
        state.mode=state.aligned?"split":"tech";state.position=.5;
        state.zoom="fit";state.scale=1;state.panX=0;state.panY=0;state.ready=true;
        nativeSource.href=sourceUrl;nativeTech.href=techUrl;
        renderToolbar();draw();
        return true;
      }catch(error){
        if(requestId===state.generation){notice.textContent=String(error.message||error);}
        return false;
      }
    }
    function reset(){
      state.generation++;state.ready=false;state.source=null;state.tech=null;
      state.drag=null;state.zoom="fit";root.hidden=true;canvas.width=0;canvas.height=0;
    }
    function viewportX(event){
      const r=viewport.getBoundingClientRect();
      return clamp((event.clientX-r.left)/Math.max(1,r.width),0,1);
    }
    function onPointerDown(event){
      if(!state.ready||event.button>1)return;
      const fraction=viewportX(event);
      const nearSplit=Math.abs(fraction-state.position)*viewport.clientWidth<=38;
      const moving=event.shiftKey||event.button===1||
        (state.zoom!=="fit"&&!nearSplit);
      const kind=state.mode==="split"&&state.aligned&&!moving?"split":"pan";
      state.drag={kind,pointerId:event.pointerId,x:event.clientX,y:event.clientY};
      try{canvas.setPointerCapture(event.pointerId)}catch{}
      if(kind==="split")setPosition(fraction);
      event.preventDefault();
    }
    function onPointerMove(event){
      if(!state.drag||state.drag.pointerId!==event.pointerId)return;
      if(state.drag.kind==="split")setPosition(viewportX(event));
      else if(state.zoom!=="fit"){
        state.panX+=event.clientX-state.drag.x;
        state.panY+=event.clientY-state.drag.y;
        draw();
      }
      state.drag.x=event.clientX;state.drag.y=event.clientY;
      event.preventDefault();
    }
    function onPointerEnd(event){
      if(state.drag?.pointerId!==event.pointerId)return;
      state.drag=null;
      try{canvas.releasePointerCapture(event.pointerId)}catch{}
    }
    const onResize=()=>draw();
    const observer=global.ResizeObserver?new global.ResizeObserver(onResize):null;
    if(observer)observer.observe(viewport);
    else global.addEventListener("resize",onResize);
    buttons.forEach(b=>b.addEventListener("click",()=>setMode(b.dataset.intakeMode)));
    range.addEventListener("input",()=>setPosition(Number(range.value)/100));
    byId(root,"intake-split-fit").addEventListener("click",()=>setZoom("fit"));
    byId(root,"intake-split-native").addEventListener("click",()=>setZoom("native"));
    byId(root,"intake-split-minus").addEventListener("click",()=>setZoom(.8));
    byId(root,"intake-split-plus").addEventListener("click",()=>setZoom(1.25));
    const full=byId(root,"intake-split-fullscreen");
    full.addEventListener("click",()=>{
      if(document.fullscreenElement===root)document.exitFullscreen?.();
      else root.requestFullscreen?.().catch(()=>{notice.textContent="Полный экран недоступен в этом браузере";});
    });
    const onFullscreen=()=>{full.textContent=document.fullscreenElement===root?"Выйти из полного экрана":"Полный экран";draw();};
    document.addEventListener("fullscreenchange",onFullscreen);
    canvas.addEventListener("pointerdown",onPointerDown);
    canvas.addEventListener("pointermove",onPointerMove);
    canvas.addEventListener("pointerup",onPointerEnd);
    canvas.addEventListener("pointercancel",onPointerEnd);
    canvas.addEventListener("wheel",e=>{
      if(!e.ctrlKey||!state.ready)return;
      e.preventDefault();
      setZoom(e.deltaY<0?1.1:1/1.1);
    },{passive:false});
    function destroy(){
      reset();state.disposed=true;
      observer?.disconnect();
      if(!observer)global.removeEventListener("resize",onResize);
      document.removeEventListener("fullscreenchange",onFullscreen);
    }
    return {setImages,reset,setMode,setPosition,setZoom,destroy,
      getState:()=>({ready:state.ready,aligned:state.aligned,mode:state.mode,
        position:state.position,zoom:state.zoom})};
  }
  global.MG4KEditorialSplit={create,aligned};
})(window);
