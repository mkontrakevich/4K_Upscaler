/* Read-only inventory of Editorial Tech Masters imported into MG4K.
 * This page never starts a generation, bypasses QA, or changes persisted locks.
 */
(function(){
"use strict";
const params=new URLSearchParams(location.search);
const id=params.get("editorial_import")||"";
if(!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id))return;
const panel=document.createElement("section");
panel.setAttribute("aria-label","Импорт утвержденного технического мастера");
panel.style.cssText="position:fixed;top:70px;right:18px;z-index:1000;max-width:min(480px,calc(100vw - 36px));padding:20px;background:#091a28;color:#eef7ff;border:1px solid #28617d;border-radius:4px;box-shadow:0 15px 40px #0008;font:13px/1.6 Inter,Arial,sans-serif";
const title=document.createElement("h2");
title.textContent="MG 4K / Импорт Tech Master";
title.style.cssText="font-size:17px;margin:0 0 9px";
const description=document.createElement("p");
description.textContent="Проверка импортированного TIFF и локеров…";
description.setAttribute("role","status");
const details=document.createElement("pre");
details.style.cssText="white-space:pre-wrap;word-break:break-word;color:#b6cddd;max-height:42vh;overflow:auto;font:12px/1.65 ui-monospace,monospace";
const footer=document.createElement("div");
footer.style.cssText="display:flex;gap:9px;flex-wrap:wrap";
const back=document.createElement("a");
back.href="/editorial-photo.html#intake="+encodeURIComponent(id);
back.textContent="Вернуться к утверждению";
back.style.cssText="color:#8fdfff;text-decoration:none;border:1px solid #3d738e;padding:8px";
const close=document.createElement("button");
close.type="button";close.textContent="Закрыть";
close.style.cssText="border:1px solid #3d738e;background:transparent;color:#fff;padding:8px;cursor:pointer";
close.addEventListener("click",()=>panel.remove());
const use=document.createElement("button");
use.type="button";use.textContent="Добавить в художественный MG 4K";
use.disabled=true;use.style.cssText="border:1px solid #4d8cae;background:#163f55;color:#fff;padding:8px;cursor:pointer";
footer.append(use,back,close);
panel.append(title,description,details,footer);
document.body.appendChild(panel);
async function load(){
  let session;
  try{session=JSON.parse(sessionStorage.getItem("mg4k-editorial-session-v1")||"null")}
  catch{}
  let bearer="";
  try{bearer=JSON.parse(localStorage.getItem("mg4k-editorial-job-access-v1")||"{}")[id]?.token||""}catch{}
  if(!bearer&&(!session?.id||!session?.token)){
    description.textContent="Нет ключа доступа к импортированному мастеру. Откройте страницу утверждения в браузере, где выполнялась загрузка.";
    return;
  }
  try{
    const response=await fetch("/api/editorial/job/import-status?job="+encodeURIComponent(id),{
      cache:"no-store",headers:{"x-session-id":session?.id||"","x-session-token":session?.token||"","x-editorial-job-token":bearer}
    });
    const body=await response.json().catch(()=>({}));
    if(!response.ok)throw Error(body.error||"import_status_http_"+response.status);
    if(body.stage!=="MG4K_MASTER_IMPORTED"||body.generation_started!==false||
       body.master_bit_depth!==16||body.locked!==true)throw Error("invalid_import_state");
    description.textContent="Утверждённый 16-битный мастер находится в хранилище MG 4K. Генерация не запускалась.";
    const locks=Object.entries(body.mg4k_locks||{}).map(([k,v])=>k+": "+v).join("\n");
    use.disabled=false;
    use.onclick=async()=>{
      use.disabled=true;
      try{
        const r=await fetch("/api/editorial/job/preview?job="+encodeURIComponent(id),{
          cache:"no-store",headers:{"x-session-id":session?.id||"",
            "x-session-token":session?.token||"","x-editorial-job-token":bearer}
        });
        if(!r.ok)throw Error("approved_preview_unavailable");
        const blob=await r.blob();if(blob.type!=="image/jpeg")throw Error("invalid_model_input_type");
        const name=String(body.filename||"editorial").replace(/\.[^.]+$/,"")+"_TECH_APPROVED.jpg";
        const file=new File([blob],name,{type:"image/jpeg"});
        if(typeof window.MG4KImportEditorial!=="function")throw Error("MG4K import adapter not loaded");
        window.MG4KImportEditorial(file,{jobId:id,token:bearer});
        description.textContent="Утверждённый снимок добавлен в очередь MG 4K. Выберите параметры и отдельно запустите художественную обработку.";
        use.textContent="Добавлено в MG 4K";
      }catch(error){
        description.textContent="Не удалось открыть мастер: "+String(error?.message||error);
        use.disabled=false;
      }
    };
    details.textContent=[
      "Проект: "+body.project,
      "Файл: "+body.filename,
      "Job: "+body.job_id,
      "SHA-256 TECH: "+body.master_sha256,
      "Размер TIFF: "+(body.master_bytes/1048576).toFixed(1)+" MiB",
      "Решение QA: APPROVED · "+body.human_QA_at,
      "Локеры MG4K:\n"+locks,
      "Запуск художественной обработки: BLOCKED до подключения адаптера"
    ].join("\n\n");
  }catch(error){
    description.textContent="Импорт недоступен: "+String(error.message||error);
  }
}
load();
})();