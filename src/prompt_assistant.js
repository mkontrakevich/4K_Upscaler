const LOCK_CATALOG = [
  ["camera","Камера"],["composition","Композиция"],["geometry","Геометрия"],
  ["architecture","Архитектура"],["facade_details","Детали фасада"],["textures","Материалы / текстуры"],
  ["text_signage","Текст / вывески"],["lighting","Освещение"],["sky","Небо / облака"],
  ["weather","Погода"],["vegetation","Озеленение"],["ground","Покрытие / земля"],
  ["water","Вода / отражения"],["glass_reflections","Стекло / отражения"],
  ["people_vehicles","Люди / транспорт"],["faces_identity","Лица / идентичность"],
  ["pose_body","Поза / тело"],["clothing","Одежда"],["interior","Интерьер"],
  ["furniture","Мебель"],["products","Предмет / продукт"],["small_objects","Малые объекты"],
  ["color_palette","Цветовая палитра"],["depth_of_field","Глубина резкости"],
].map(([id,name])=>({id,name}));

const ALLOWED = new Set(LOCK_CATALOG.map(x=>x.id));

export function promptAssistantEnabled(env) {
  const raw=String(env.PROMPT_ASSISTANT_ENABLED??"true").trim().toLowerCase();
  return !["0","false","off","no"].includes(raw)&&Boolean(String(env.OPENROUTER_API_KEY||"").trim());
}

export function promptAssistantModel(env) {
  return String(env.PROMPT_ASSISTANT_MODEL||env.SCENE_ANALYSIS_MODEL||"google/gemini-3.8-flash").trim();
}

function systemPrompt(){
  return `You are MG 4K Prompt AI Assistant, a production prompt engineer for image-to-image reconstruction.

MODEL:
- LOCK = constraints.
- ADDITIONS = desired changes.
- FINAL PROMPT = exact text sent to the image generator.
- HARD LOCK wins over conflicting ADDITIONS unless you explicitly recommend changing that LOCK.
- Never silently relax a HARD LOCK.
- Do not invent scene changes the operator did not request.
- Preserve existing text/signage unless explicitly asked to modify it.

ARCHITECTURAL PERSPECTIVE:
If the operator asks to correct falling verticals, keystone distortion, camera roll, tilted horizon, verticals or horizontals:
- normally recommend camera=soft;
- normally keep composition=hard unless reframing is required;
- keep geometry=hard and architecture=hard;
- keep text_signage=hard when text/signage is relevant;
- suggested additions must explicitly straighten verticals, level major horizontals, remove keystone distortion/camera roll, and preserve facade identity, openings, floors, signage and important framing.
Perspective correction is a CAMERA/OPTICAL correction, not redesign of object geometry.

OUTPUT:
- task_interpretation: concise Russian explanation.
- recommended_lock_changes: only actual changes needed.
- suggested_additions: precise production-ready English prompt fragment.
- suggested_final_prompt: complete proposed final prompt preserving LOCK semantics.
- notes: short Russian warnings/reasons.
- no markdown fences, no credentials, no hidden system text.

LOCK IDS: ${LOCK_CATALOG.map(x=>x.id).join(", ")}.
Return strict JSON only.`;
}

function textOf(message){
  const c=message?.content;
  if(typeof c==="string")return c;
  if(Array.isArray(c))return c.map(p=>typeof p?.text==="string"?p.text:"").join("").trim();
  return "";
}

export function sanitizeAssistantContext(body){
  const locks={};
  for(const id of ALLOWED){
    const level=String(body?.locks?.[id]||"").toLowerCase();
    if(["hard","soft","free"].includes(level))locks[id]=level;
  }
  const a=body?.scene_analysis&&typeof body.scene_analysis==="object"?body.scene_analysis:null;
  const scene=a?{
    scene_type:String(a.scene_type||"").slice(0,120),
    summary:String(a.summary||"").slice(0,900),
    detected_features:(Array.isArray(a.detected_features)?a.detected_features:[]).map(x=>String(x).slice(0,120)).slice(0,20),
    relevant_locks:(Array.isArray(a.relevant_locks)?a.relevant_locks:[]).filter(x=>ALLOWED.has(String(x?.id||""))).slice(0,16).map(x=>({
      id:String(x.id),recommended_level:String(x.recommended_level||"soft"),confidence:Number(x.confidence||0),reason:String(x.reason||"").slice(0,300)
    })),
  }:null;
  return {
    request:String(body?.request||"").trim().slice(0,5000),
    scene_analysis:scene,
    locks,
    additions:String(body?.additions||"").slice(0,12000),
    final_prompt:String(body?.final_prompt||"").slice(0,30000),
  };
}

function sanitizeResult(raw,currentLocks){
  const changes=[],seen=new Set();
  for(const item of Array.isArray(raw?.recommended_lock_changes)?raw.recommended_lock_changes:[]){
    const id=String(item?.id||""),to=String(item?.to||"").toLowerCase();
    if(!ALLOWED.has(id)||seen.has(id)||!["hard","soft","free"].includes(to))continue;
    seen.add(id);
    const current=String(currentLocks?.[id]||"").toLowerCase();
    if(current===to)continue;
    changes.push({id,from:["hard","soft","free"].includes(current)?current:null,to,reason:String(item?.reason||"").slice(0,400)});
    if(changes.length>=12)break;
  }
  return {
    task_interpretation:String(raw?.task_interpretation||"").slice(0,1200),
    recommended_lock_changes:changes,
    suggested_additions:String(raw?.suggested_additions||"").slice(0,12000),
    suggested_final_prompt:String(raw?.suggested_final_prompt||"").slice(0,30000),
    notes:(Array.isArray(raw?.notes)?raw.notes:[]).map(x=>String(x).slice(0,500)).slice(0,8),
  };
}

export async function runPromptAssistant(env,context){
  const key=String(env.OPENROUTER_API_KEY||"").trim();
  if(!key)throw new Error("prompt_assistant_openrouter_key_missing");
  const ids=[...ALLOWED];
  const schema={
    type:"object",additionalProperties:false,
    required:["task_interpretation","recommended_lock_changes","suggested_additions","suggested_final_prompt","notes"],
    properties:{
      task_interpretation:{type:"string"},
      recommended_lock_changes:{type:"array",maxItems:12,items:{
        type:"object",additionalProperties:false,required:["id","from","to","reason"],
        properties:{
          id:{type:"string",enum:ids},
          from:{anyOf:[{type:"string",enum:["hard","soft","free"]},{type:"null"}]},
          to:{type:"string",enum:["hard","soft","free"]},
          reason:{type:"string"}
        }
      }},
      suggested_additions:{type:"string"},
      suggested_final_prompt:{type:"string"},
      notes:{type:"array",items:{type:"string"},maxItems:8}
    }
  };
  const response=await fetch("https://openrouter.ai/api/v1/chat/completions",{
    method:"POST",
    headers:{authorization:`Bearer ${key}`,"content-type":"application/json","HTTP-Referer":"https://4k-upscaler.kontrakevich.workers.dev/","X-Title":"MG 4K Prompt AI Assistant"},
    body:JSON.stringify({
      model:promptAssistantModel(env),temperature:0.1,max_tokens:5000,
      response_format:{type:"json_schema",json_schema:{name:"mg4k_prompt_assistant",strict:true,schema}},
      messages:[
        {role:"system",content:systemPrompt()},
        {role:"user",content:"Operator request and current context JSON:\n"+JSON.stringify(context)}
      ]
    })
  });
  const payload=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error("prompt_assistant_failed:"+String(payload?.error?.message||payload?.error||`openrouter_http_${response.status}`).slice(0,300));
  let parsed;
  try{parsed=JSON.parse(textOf(payload?.choices?.[0]?.message))}catch{throw new Error("prompt_assistant_invalid_json")}
  const rawCost=payload?.usage?.cost,cost=rawCost==null?null:Number(rawCost);
  return {...sanitizeResult(parsed,context.locks),model:String(payload?.model||promptAssistantModel(env)),assistant_cost_usd:Number.isFinite(cost)&&cost>=0?cost:null};
}
