import { Container, ContainerProxy, getContainer } from "@cloudflare/containers";
import { promptAssistantEnabled, promptAssistantModel, sanitizeAssistantContext, runPromptAssistant } from "./prompt_assistant.js";

export { ContainerProxy };

export class MG4KProcessor extends Container {
  defaultPort = 8080;
  sleepAfter = "15m";
  enableInternet = true;

  onStart() {
    console.log("MG4K_PROCESSOR_STARTED");
  }

  onStop() {
    console.log("MG4K_PROCESSOR_STOPPED");
  }

  onError(error) {
    console.error("MG4K_PROCESSOR_ERROR", error);
  }

  async shutdownContainer(reason = "retired") {
    const wasRunning = Boolean(this.ctx?.container?.running);
    if (wasRunning) {
      await this.ctx.container.destroy(String(reason || "retired").slice(0, 120));
    }
    return { ok: true, was_running: wasRunning };
  }
}

const SESSION_TTL = 60 * 10;
const PAIR_TTL = 60 * 10;
const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
const WORKER_BUILD_ID = "pid1-tini-r11b";
const CONTAINER_BUILD_ID = "result-persist-r11b";
const CONTAINER_INSTANCE_NAME = "primary-result-persist-r11b";
const LEGACY_CONTAINER_INSTANCE_NAMES = [
  "primary-result-persist-r10a",
  "primary",
  "primary-result-persist-r10",
  "primary-result-persist-r10b",
];

const SCENE_LOCK_CATALOG = [
  { id:"camera", name:"Камера", description:"viewpoint, perspective, focal length, crop and horizon" },
  { id:"composition", name:"Композиция", description:"subject placement, framing balance, negative space and visual hierarchy" },
  { id:"geometry", name:"Геометрия", description:"object contours, proportions, massing, terrain and spatial relationships" },
  { id:"architecture", name:"Архитектура", description:"building identity, floors, openings, rooflines and design language" },
  { id:"facade_details", name:"Детали фасада", description:"mouldings, joints, cornices, railings, decorative and facade micro-elements" },
  { id:"textures", name:"Материалы / текстуры", description:"material category, finish, joints, texture direction, roughness and scale" },
  { id:"text_signage", name:"Текст / вывески", description:"letters, numerals, logos, signage, road markings and readable symbols" },
  { id:"lighting", name:"Освещение", description:"time of day, exposure, light direction, shadows, contrast and mood" },
  { id:"sky", name:"Небо / облака", description:"sky structure, clouds, atmosphere and horizon tone" },
  { id:"weather", name:"Погода", description:"rain, snow, fog, haze and atmospheric weather state" },
  { id:"vegetation", name:"Озеленение", description:"trees, shrubs, grass, planting positions, mass and character" },
  { id:"ground", name:"Земля / покрытие", description:"paving, asphalt, soil, curbs, paths and ground boundaries" },
  { id:"water", name:"Вода / отражения", description:"water bodies, reflections, puddles, shadow/reflection causal logic" },
  { id:"glass_reflections", name:"Стекло / отражения", description:"glazing, transparent surfaces, reflections and interior/exterior visibility" },
  { id:"people_vehicles", name:"Люди / транспорт", description:"count, position, scale, orientation and identity of people and vehicles" },
  { id:"faces_identity", name:"Лица / идентичность", description:"recognizable face identity, facial geometry, age cues and expression" },
  { id:"pose_body", name:"Поза / тело", description:"body pose, anatomy, hands, gesture, silhouette and proportions" },
  { id:"clothing", name:"Одежда", description:"garments, colors, cut, logos, accessories and fabric identity" },
  { id:"interior", name:"Интерьер", description:"room layout, walls, ceilings, floor, built-ins and spatial identity" },
  { id:"furniture", name:"Мебель", description:"furniture count, placement, dimensions, model and finish" },
  { id:"products", name:"Предмет / продукт", description:"hero product identity, packaging, geometry, branding and surface finish" },
  { id:"small_objects", name:"Малые объекты", description:"props, lamps, signs, street furniture and secondary scene objects" },
  { id:"color_palette", name:"Цветовая палитра", description:"dominant colors, white balance and brand/product color identity" },
  { id:"depth_of_field", name:"Глубина резкости", description:"focus plane, blur distribution, bokeh and optical depth cues" },
];

function sceneAnalysisEnabled(env) {
  const raw = String(env.SCENE_ANALYSIS_ENABLED ?? "true").trim().toLowerCase();
  return !["0","false","off","no"].includes(raw) && Boolean(String(env.OPENROUTER_API_KEY || "").trim());
}

function sceneAnalysisModel(env) {
  return String(env.SCENE_ANALYSIS_MODEL || "google/gemini-3.8-flash").trim();
}


function sceneAnalysisPrompt() {
  const catalog = SCENE_LOCK_CATALOG.map(item => `- ${item.id}: ${item.name} — ${item.description}`).join("\n");
  return `You are the MG 4K visual scene analyzer. Inspect the SOURCE image and select only LOCK parameters that are materially relevant to this exact image.

GOAL:
- hide irrelevant controls from the operator;
- recommend conservative HARD / SOFT / FREE defaults for relevant controls;
- protect identity-critical content;
- do not invent objects that are not visibly present.

RULES:
1. Return between 2 and 14 relevant locks.
2. CAMERA and COMPOSITION are normally relevant to every image.
3. If a building/exterior is visible, include GEOMETRY and ARCHITECTURE.
4. If any facade cladding, paving, tile, lattice, perforation, relief, ornament, joint rhythm or other visible surface pattern exists, include TEXTURES and recommend HARD. A changed motif is factually wrong, not a creative refinement.
5. Include TEXT_SIGNAGE only when visible text, logos, signs, numbers or markings matter.
6. Include FACES_IDENTITY only when one or more recognizable human faces are visible.
7. Include POSE_BODY / CLOTHING only when people are visually important enough that drift would matter.
8. Include PRODUCTS only when a product/object is the primary subject.
9. Use HARD for identity, geometry, text/logo, recognizable faces, hero products, or other elements whose change would make the image factually wrong.
10. Use SOFT where controlled refinement is useful.
11. Use FREE only where creative variation is safe.
12. Give a concise Russian reason for every suggested lock.
13. Confidence is 0.0 to 1.0.
14. Do not return any id outside this catalog.

LOCK CATALOG:
${catalog}

Return JSON matching the supplied schema only.`;
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, Math.min(bytes.length, i + chunk)));
  }
  return btoa(binary);
}

function openRouterMessageText(message) {
  const content = message?.content;
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content.map(part => typeof part?.text === "string" ? part.text : "").join("").trim();
  }
  return "";
}

function sanitizeSceneAnalysis(raw) {
  const allowed = new Set(SCENE_LOCK_CATALOG.map(item => item.id));
  const seen = new Set();
  const relevant = [];
  for (const item of Array.isArray(raw?.relevant_locks) ? raw.relevant_locks : []) {
    const id = String(item?.id || "");
    if (!allowed.has(id) || seen.has(id)) continue;
    seen.add(id);
    const recommended = ["hard","soft","free"].includes(String(item?.recommended_level || "").toLowerCase())
      ? String(item.recommended_level).toLowerCase()
      : "soft";
    relevant.push({
      id,
      recommended_level: recommended,
      confidence: Math.max(0, Math.min(1, Number(item?.confidence || 0))),
      reason: String(item?.reason || "").slice(0, 300),
    });
    if (relevant.length >= 14) break;
  }
  if (!seen.has("camera")) relevant.unshift({id:"camera",recommended_level:"hard",confidence:1,reason:"Ракурс и перспектива определяют соответствие исходнику."});
  if (!relevant.some(item => item.id === "composition")) relevant.splice(1,0,{id:"composition",recommended_level:"hard",confidence:1,reason:"Композицию и положение главного объекта следует сохранить."});
  return {
    scene_type: String(raw?.scene_type || "unknown").slice(0, 120),
    summary: String(raw?.summary || "").slice(0, 700),
    relevant_locks: relevant.slice(0, 14),
    detected_features: (Array.isArray(raw?.detected_features) ? raw.detected_features : []).map(x=>String(x).slice(0,120)).slice(0,16),
  };
}

async function consumeSceneAnalysisBudget(env, sessionId) {
  const sessionKey = `scene-analysis:session:${sessionId}`;
  const current = Number(await env.SESSION_STATE_R7.get(sessionKey) || 0);
  if (current >= 20) return { ok:false, error:"scene_analysis_session_limit" };
  await env.SESSION_STATE_R7.put(sessionKey, String(current + 1), { expirationTtl: SESSION_TTL });

  const hour = new Date().toISOString().slice(0, 13);
  const globalKey = `scene-analysis:hour:${hour}`;
  const globalCurrent = Number(await env.SESSION_STATE_R7.get(globalKey) || 0);
  const globalMax = Math.max(10, Math.min(1000, Number(env.SCENE_ANALYSIS_MAX_PER_HOUR || 120)));
  if (globalCurrent >= globalMax) return { ok:false, error:"scene_analysis_hourly_budget" };
  await env.SESSION_STATE_R7.put(globalKey, String(globalCurrent + 1), { expirationTtl: 7200 });
  return { ok:true };
}

async function consumePromptAssistantBudget(env, sessionId) {
  const sessionKey=`prompt-assistant:session:${sessionId}`;
  const current=Number(await env.SESSION_STATE_R7.get(sessionKey)||0);
  if(current>=60)return {ok:false,error:"prompt_assistant_session_limit"};
  await env.SESSION_STATE_R7.put(sessionKey,String(current+1),{expirationTtl:SESSION_TTL});
  const hour=new Date().toISOString().slice(0,13);
  const globalKey=`prompt-assistant:hour:${hour}`;
  const globalCurrent=Number(await env.SESSION_STATE_R7.get(globalKey)||0);
  const globalMax=Math.max(20,Math.min(2000,Number(env.PROMPT_ASSISTANT_MAX_PER_HOUR||300)));
  if(globalCurrent>=globalMax)return {ok:false,error:"prompt_assistant_hourly_budget"};
  await env.SESSION_STATE_R7.put(globalKey,String(globalCurrent+1),{expirationTtl:7200});
  return {ok:true};
}

async function analyzeSceneWithOpenRouter(env, file) {
  const key = String(env.OPENROUTER_API_KEY || "").trim();
  if (!key) throw new Error("scene_analysis_openrouter_key_missing");
  const bytes = await file.arrayBuffer();
  if (bytes.byteLength > 8 * 1024 * 1024) throw new Error("scene_analysis_file_too_large");
  const mime = String(file.type || "image/jpeg");
  const dataUrl = `data:${mime};base64,${arrayBufferToBase64(bytes)}`;

  const schema = {
    type:"object",
    additionalProperties:false,
    required:["scene_type","summary","relevant_locks","detected_features"],
    properties:{
      scene_type:{type:"string"},
      summary:{type:"string"},
      detected_features:{type:"array",items:{type:"string"},maxItems:16},
      relevant_locks:{
        type:"array",
        minItems:2,
        maxItems:14,
        items:{
          type:"object",
          additionalProperties:false,
          required:["id","recommended_level","confidence","reason"],
          properties:{
            id:{type:"string",enum:SCENE_LOCK_CATALOG.map(item=>item.id)},
            recommended_level:{type:"string",enum:["hard","soft","free"]},
            confidence:{type:"number",minimum:0,maximum:1},
            reason:{type:"string"},
          },
        },
      },
    },
  };

  const response = await fetch("https://openrouter.ai/api/v1/chat/completions", {
    method:"POST",
    headers:{
      authorization:`Bearer ${key}`,
      "content-type":"application/json",
      "HTTP-Referer":"https://4k-upscaler.kontrakevich.workers.dev/",
      "X-Title":"MG 4K Scene LOCK Analyzer",
    },
    body:JSON.stringify({
      model:sceneAnalysisModel(env),
      temperature:0.1,
      max_tokens:1800,
      response_format:{type:"json_schema",json_schema:{name:"mg4k_scene_lock_analysis",strict:true,schema}},
      messages:[
        {role:"system",content:sceneAnalysisPrompt()},
        {role:"user",content:[
          {type:"text",text:"Analyze this SOURCE image and propose only the relevant LOCK controls and their initial levels."},
          {type:"image_url",image_url:{url:dataUrl}},
        ]},
      ],
    }),
  });
  const payload = await response.json().catch(()=>({}));
  if (!response.ok) {
    const detail = String(payload?.error?.message || payload?.error || `openrouter_http_${response.status}`).slice(0,300);
    throw new Error("scene_analysis_failed:"+detail);
  }
  const text = openRouterMessageText(payload?.choices?.[0]?.message);
  let parsed;
  try { parsed = JSON.parse(text); } catch { throw new Error("scene_analysis_invalid_json"); }
  const result = sanitizeSceneAnalysis(parsed);
  const rawCost = payload?.usage?.cost;
  const cost = rawCost === null || rawCost === undefined ? null : Number(rawCost);
  return {
    ...result,
    model: String(payload?.model || sceneAnalysisModel(env)),
    analysis_cost_usd: Number.isFinite(cost) && cost >= 0 ? cost : null,
  };
}

function processorContainer(env) {
  return getContainer(env.MG4K_PROCESSOR, CONTAINER_INSTANCE_NAME);
}

function processorContainerForJob(env, job) {
  const instanceName = String(job?.container_instance_name || CONTAINER_INSTANCE_NAME);
  return {
    instanceName,
    container: getContainer(env.MG4K_PROCESSOR, instanceName),
  };
}

function persistedReviewIsDurable(job) {
  return String(job?.status || "") === "review"
    && Boolean(job?.result_key)
    && Number(job?.result_bytes || 0) > 0
    && Boolean(job?.result_persisted_at);
}

function persistedDecisionIsDurable(job) {
  return String(job?.status || "") === "decision_pending"
    && Boolean(job?.result_key)
    && Number(job?.result_bytes || 0) > 0
    && ["approve", "reject", "skip"].includes(String(job?.decision || ""));
}

async function hasInFlightProcessorJob(env, instanceNames) {
  const targets = new Set(Array.from(instanceNames || []).map(String));
  if (!targets.size) return null;

  const listed = await env.SESSION_STATE_R7.list({ prefix: "job:", limit: 1000 });
  for (const key of listed.keys) {
    const raw = await env.SESSION_STATE_R7.get(key.name);
    if (!raw) continue;
    const job = JSON.parse(raw);

    const session = await readSession(env, job.session_id);
    if (!session) {
      await env.SESSION_STATE_R7.delete(key.name);
      continue;
    }

    if (!job.container_dispatched) continue;
    if (persistedReviewIsDurable(job) || persistedDecisionIsDurable(job)) continue;

    const status = String(job.status || "");
    if (!["processing", "review", "decision_pending"].includes(status)) continue;

    // Versioned jobs must only protect the exact Container instance that owns
    // them. Legacy jobs without an instance name are conservatively mapped to
    // the original "primary" instance only.
    const instanceName = String(job.container_instance_name || "primary");
    if (!targets.has(instanceName)) continue;

    return {
      id: job.id,
      status,
      stage: String(job.stage || ""),
      instance_name: instanceName,
    };
  }
  return null;
}

async function retirePreviousProcessorInstances(env) {
  const remembered = await env.SESSION_STATE_R7.get("processor:active_instance");
  const candidates = new Set([
    ...(remembered ? [remembered] : []),
    ...LEGACY_CONTAINER_INSTANCE_NAMES,
  ]);
  candidates.delete(CONTAINER_INSTANCE_NAME);

  if (!candidates.size) return { retired: [], blocked: false };
  const blocker = await hasInFlightProcessorJob(env, candidates);
  if (blocker) {
    console.log("MG4K_PROCESSOR_RETIRE_BLOCKED_ACTIVE_JOB", { candidates: [...candidates], blocker });
    return { retired: [], blocked: true, blocker };
  }

  const retired = [];
  for (const name of candidates) {
    try {
      const stub = getContainer(env.MG4K_PROCESSOR, name);
      const result = await stub.shutdownContainer(`retired_for_${CONTAINER_BUILD_ID}`);
      retired.push({ name, was_running: Boolean(result?.was_running) });
    } catch (error) {
      console.warn("MG4K_PROCESSOR_RETIRE_WARNING", name, error?.message || String(error));
    }
  }
  return { retired, blocked: false };
}

function json(data, status = 200, headers = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      ...headers,
    },
  });
}

function now() {
  return new Date().toISOString();
}

function cleanName(name = "image") {
  return String(name)
    .replace(/[\\/]+/g, "_")
    .replace(/[^\p{L}\p{N}._ -]+/gu, "_")
    .slice(0, 160) || "image";
}

function sanitizeProviderCostSummary(value) {
  if (!value || typeof value !== "object") return null;
  const requestCount = Math.max(0, Math.min(20, Math.trunc(Number(value.request_count || 0))));
  const reportedCount = Math.max(0, Math.min(requestCount, Math.trunc(Number(value.reported_cost_count || 0))));
  let costUsd = null;
  if (value.cost_usd !== null && value.cost_usd !== undefined && value.cost_usd !== "") {
    const parsed = Number(value.cost_usd);
    if (!Number.isFinite(parsed) || parsed < 0 || parsed > 100) return null;
    costUsd = Math.round(parsed * 100000000) / 100000000;
  }
  return {
    provider: String(value.provider || "openrouter").slice(0, 40),
    currency: String(value.currency || "USD").slice(0, 8),
    request_count: requestCount,
    reported_cost_count: reportedCount,
    cost_usd: costUsd,
    complete: Boolean(value.complete) && requestCount > 0 && reportedCount === requestCount && costUsd !== null,
  };
}

function randomToken() {
  const bytes = new Uint8Array(24);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
}

function randomCode() {
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const bytes = new Uint8Array(8);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, b => alphabet[b % alphabet.length]).join("");
}

async function sha256(value) {
  const data = new TextEncoder().encode(String(value));
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, "0")).join("");
}

function billingMode(env) {
  const mode = String(env.BILLING_MODE || "off").toLowerCase();
  return ["sandbox", "live"].includes(mode) ? mode : "off";
}

function billingEnabled(env) {
  return billingMode(env) !== "off"
    && Boolean(String(env.BILLING_GATEWAY_URL || "").trim())
    && Boolean(String(env.BILLING_PERMIT_HMAC_SECRET || "").trim())
    && Boolean(String(env.BILLING_WORKER_SHARED_SECRET || "").trim());
}

function base64urlToBytes(value) {
  const normalized = String(value || "").replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized + "=".repeat((4 - normalized.length % 4) % 4);
  const binary = atob(padded);
  return Uint8Array.from(binary, ch => ch.charCodeAt(0));
}

function bytesToBase64url(bytes) {
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function constantTimeTextEqual(a, b) {
  const left = new TextEncoder().encode(String(a || ""));
  const right = new TextEncoder().encode(String(b || ""));
  let diff = left.length ^ right.length;
  const size = Math.max(left.length, right.length);
  for (let i = 0; i < size; i++) diff |= (left[i % Math.max(1, left.length)] || 0) ^ (right[i % Math.max(1, right.length)] || 0);
  return diff === 0;
}

async function billingHmac(value, secret) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(String(secret)),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(String(value))));
  return bytesToBase64url(signature);
}

async function verifyBillingPermit(token, env) {
  const [version, body, signature] = String(token || "").split(".");
  if (version !== "v1" || !body || !signature) throw new Error("billing_permit_invalid");
  const expected = await billingHmac(version + "." + body, String(env.BILLING_PERMIT_HMAC_SECRET || ""));
  if (!constantTimeTextEqual(signature, expected)) throw new Error("billing_permit_signature_invalid");
  let payload;
  try {
    payload = JSON.parse(new TextDecoder().decode(base64urlToBytes(body)));
  } catch {
    throw new Error("billing_permit_payload_invalid");
  }
  if (payload.typ !== "generation_permit" || payload.aud !== "mg4k-worker") throw new Error("billing_permit_type_invalid");
  if (Number(payload.exp || 0) < Math.floor(Date.now() / 1000)) throw new Error("billing_permit_expired");
  if (!payload.sub || !payload.reservation_id || !payload.jti || Number(payload.credits || 0) !== 1) throw new Error("billing_permit_fields_invalid");
  return payload;
}

async function billingGatewayRequest(env, path, payload) {
  const base = String(env.BILLING_GATEWAY_URL || "").replace(/\/$/, "");
  if (!base) throw new Error("billing_gateway_missing");
  const response = await fetch(base + path, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-mg4k-worker-secret": String(env.BILLING_WORKER_SHARED_SECRET || ""),
    },
    body: JSON.stringify(payload),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || ("billing_gateway_http_" + response.status));
  return body;
}

async function settleBilling(env, job, { resultAvailable = false, hardSystemFailure = false } = {}) {
  if (!billingEnabled(env) || !job?.billing_reservation_id) return null;
  try {
    const outcome = await billingGatewayRequest(env, "/api/internal/generation/settle", {
      reservation_id: job.billing_reservation_id,
      job_id: job.id,
      provider: job.provider_cost_provider || "openrouter",
      provider_request_count: Number(job.provider_request_count || 0),
      provider_cost_usd: job.provider_cost_usd ?? null,
      provider_cost_complete: Boolean(job.provider_cost_complete),
      result_available: Boolean(resultAvailable || job.result_key),
      hard_system_failure: Boolean(hardSystemFailure),
    });
    job.billing_settlement = outcome.status || null;
    job.billing_settlement_warning = null;
    job.billing_settled_at = now();
    await writeJob(env, job);
    return outcome;
  } catch (error) {
    job.billing_settlement_warning = error?.message || String(error);
    await writeJob(env, job);
    console.warn("MG4K_BILLING_SETTLEMENT_WARNING", job.id, job.billing_settlement_warning);
    return null;
  }
}

async function readSession(env, id) {
  if (!id) return null;
  const raw = await env.SESSION_STATE_R7.get(`session:${id}`);
  return raw ? JSON.parse(raw) : null;
}

async function writeSession(env, session) {
  session.updated_at = now();
  await env.SESSION_STATE_R7.put(`session:${session.id}`, JSON.stringify(session), { expirationTtl: SESSION_TTL });
}

async function readJob(env, id) {
  const raw = await env.SESSION_STATE_R7.get(`job:${id}`);
  return raw ? JSON.parse(raw) : null;
}

async function writeJob(env, job) {
  job.updated_at = now();
  await env.SESSION_STATE_R7.put(`job:${job.id}`, JSON.stringify(job), { expirationTtl: SESSION_TTL });
}

function publicJob(job) {
  if (!job) return null;
  return {
    id: job.id,
    status: job.status,
    created_at: job.created_at,
    updated_at: job.updated_at,
    filename: job.filename,
    content_type: job.content_type,
    bytes: job.bytes,
    locks: job.locks,
    mode: job.mode,
    progress: job.progress || 0,
    stage: job.stage || "queued",
    result_available: Boolean(job.result_key),
    error: job.error || null,
    diagnostic_excerpt: job.diagnostic_excerpt || null,
    result_sync_warning: job.result_sync_warning || null,
    result_sync_attempts: Number(job.result_sync_attempts || 0),
    result_bytes: Number(job.result_bytes || 0),
    result_content_type: job.result_content_type || null,
    result_checksum_sha256: job.result_checksum_sha256 || null,
    result_persist_source: job.result_persist_source || null,
    result_persisted_at: job.result_persisted_at || null,
    container_build_id: job.container_build_id || null,
    container_instance_name: job.container_instance_name || null,
    manual_retry_required: Boolean(job.manual_retry_required),
    validation: job.validation || null,
    decision: job.decision || null,
    approval_mode: job.approval_mode || null,
    review_detached_from_container: Boolean(job.review_detached_from_container),
    provider_cost_usd: job.provider_cost_usd ?? null,
    provider_request_count: Number(job.provider_request_count || 0),
    provider_cost_complete: Boolean(job.provider_cost_complete),
    provider_cost_provider: job.provider_cost_provider || null,
    billing_account_id: job.billing_account_id || null,
    billing_reservation_id: job.billing_reservation_id || null,
    billing_settlement: job.billing_settlement || null,
    billing_settlement_warning: job.billing_settlement_warning || null,
    prompt_override: job.prompt_override || null,
    prompt_sha256: job.prompt_sha256 || null,
    diagnostic_code: job.diagnostic_code || null,
    diagnostic_layer: job.diagnostic_layer || null,
    diagnostic_summary: job.diagnostic_summary || null,
    diagnostic_action: job.diagnostic_action || null,
    diagnostic_retryable: job.diagnostic_retryable ?? null,
    diagnostic_http_status: job.diagnostic_http_status ?? null,
    diagnostic_provider_model: job.diagnostic_provider_model || null,
    diagnostic_routing_step: job.diagnostic_routing_step || null,
  };
}

function jobToken(request, url) {
  return request.headers.get("x-job-token") || url.searchParams.get("token") || "";
}

function sessionCredentials(request, url, body = null) {
  return {
    id: request.headers.get("x-session-id") || url.searchParams.get("session_id") || body?.session_id || "",
    token: request.headers.get("x-session-token") || url.searchParams.get("session_token") || body?.session_token || "",
  };
}

async function authorizeJob(request, url, job) {
  const token = jobToken(request, url);
  if (!token || !job?.access_hash) return false;
  return (await sha256(token)) === job.access_hash;
}

async function authorizeSession(request, url, env, body = null) {
  const { id, token } = sessionCredentials(request, url, body);
  if (!id || !token) return null;
  const session = await readSession(env, id);
  if (!session?.access_hash) return null;
  if ((await sha256(token)) !== session.access_hash) return null;
  return session;
}

async function authorizeProcessor(request, env) {
  const auth = request.headers.get("authorization") || "";
  const token = auth.toLowerCase().startsWith("bearer ") ? auth.slice(7).trim() : "";
  if (!token) return false;

  const tokenHash = await sha256(token);

  const sharedSecret = String(env.PROCESSOR_SHARED_SECRET || "").trim();
  if (sharedSecret && tokenHash === await sha256(sharedSecret)) {
    return true;
  }

  const expected = await env.SESSION_STATE_R7.get("processor:token_hash");
  if (!expected) return false;
  return tokenHash === expected;
}

async function listAllObjects(env, prefix) {
  const objects = [];
  let cursor;
  do {
    const page = await env.TEMP_BUFFER_R7.list({ prefix, cursor, limit: 1000 });
    objects.push(...page.objects);
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);
  return objects;
}

async function deletePrefix(env, prefix) {
  const objects = await listAllObjects(env, prefix);
  const keys = objects.map(item => item.key);
  for (let i = 0; i < keys.length; i += 1000) {
    await env.TEMP_BUFFER_R7.delete(keys.slice(i, i + 1000));
  }
  return keys.length;
}

function archiveTimestamp() {
  return new Date().toISOString().replace(/[:.]/g, "-");
}

async function archiveBytes(env, {
  job,
  bytes,
  contentType,
  name,
  kind,
  status,
  checksum,
  persistSource,
}) {
  const safeName = cleanName(name || "artifact");
  const stamp = archiveTimestamp();
  const digest = checksum || await sha256(bytes);
  const key = `archive/jobs/${job.id}/${kind}/${stamp}_${digest.slice(0,16)}_${safeName}`;
  await env.TEMP_BUFFER_R7.put(key, bytes, {
    httpMetadata: { contentType: contentType || "application/octet-stream" },
    customMetadata: {
      session_id: job.session_id,
      job_id: job.id,
      kind,
      status: String(status || ""),
      original_name: safeName,
      sha256: digest,
      persist_source: persistSource || "unknown",
      archived_at: new Date().toISOString(),
    },
  });
  const eventKey = `archive/jobs/${job.id}/events/${stamp}_${kind}_${digest.slice(0,16)}.json`;
  await env.TEMP_BUFFER_R7.put(eventKey, JSON.stringify({
    job_id: job.id,
    session_id: job.session_id,
    archived_at: new Date().toISOString(),
    kind,
    status: status || null,
    name: safeName,
    sha256: digest,
    bytes: bytes.byteLength ?? null,
    content_type: contentType || null,
    persist_source: persistSource || null,
    archive_key: key,
    source_filename: job.filename || null,
    mode: job.mode || null,
    locks: job.locks || {},
    prompt_sha256: job.prompt_sha256 || null,
  }, null, 2), {
    httpMetadata: { contentType: "application/json; charset=utf-8" },
    customMetadata: {
      job_id: job.id,
      kind: "archive_event",
      artifact_kind: kind,
      sha256: digest,
    },
  });
  return key;
}

async function endSession(env, session) {
  let deletedObjects = 0;
  try {
    deletedObjects = await deletePrefix(env, `sessions/${session.id}/`);
  } catch (error) {
    console.error("SESSION_R2_DELETE_ERROR", session.id, error);
  }

  for (const jobId of session.jobs || []) {
    try { await env.SESSION_STATE_R7.delete(`job:${jobId}`); } catch {}
  }
  await env.SESSION_STATE_R7.delete(`session:${session.id}`);
  return deletedObjects;
}

async function nextQueuedJob(env) {
  const listed = await env.SESSION_STATE_R7.list({ prefix: "job:", limit: 1000 });
  let candidate = null;
  for (const key of listed.keys) {
    const raw = await env.SESSION_STATE_R7.get(key.name);
    if (!raw) continue;
    const job = JSON.parse(raw);
    if (job.status !== "queued") continue;

    const session = await readSession(env, job.session_id);
    if (!session) {
      await env.SESSION_STATE_R7.delete(key.name);
      continue;
    }

    if (!candidate || String(job.created_at).localeCompare(String(candidate.created_at)) < 0) {
      candidate = job;
    }
  }
  return candidate;
}

async function cleanupOrphanedR2(env) {
  const objects = await listAllObjects(env, "sessions/");
  const sessionIds = new Set();
  for (const object of objects) {
    const parts = object.key.split("/");
    if (parts.length >= 2 && parts[0] === "sessions") sessionIds.add(parts[1]);
  }

  let sessionsCleaned = 0;
  let objectsDeleted = 0;
  for (const sessionId of sessionIds) {
    const active = await readSession(env, sessionId);
    if (active) continue;
    objectsDeleted += await deletePrefix(env, `sessions/${sessionId}/`);
    sessionsCleaned++;
  }
  console.log("MG4K_CLEANUP", { sessionsCleaned, objectsDeleted });
}

async function ensureCloudProcessor(env, origin) {
  const processorToken = String(env.PROCESSOR_SHARED_SECRET || "").trim();
  const openRouterKey = String(env.OPENROUTER_API_KEY || "").trim();

  if (!processorToken) {
    throw new Error("PROCESSOR_SHARED_SECRET is not configured.");
  }
  if (!openRouterKey) {
    throw new Error("OPENROUTER_API_KEY is not configured.");
  }

  const retirement = await retirePreviousProcessorInstances(env);
  if (retirement.blocked) {
    const b = retirement.blocker;
    throw new Error(`processor_rollout_waiting_for_inflight_job:${b?.id||"unknown"}:${b?.instance_name||"unknown"}:${b?.status||"unknown"}`);
  }

  const container = processorContainer(env);
  const startConfig = {
    ports: [8080],
    startOptions: {
      envVars: {
        MG4K_CLOUD_URL: "http://mg4k.worker",
        MG4K_PUBLIC_ORIGIN: origin,
        MG4K_PROCESSOR_TOKEN: processorToken,
        OPENROUTER_API_KEY: openRouterKey,
        MG4K_HEADLESS: "1",
        MG4K_RUNTIME_DIR: "/runtime/bridge",
        MG4K_JOBS_ROOT: "/tmp/mg4k-jobs",
        MG4K_HEALTH_PORT: "8080",
        MG4K_EXIT_WHEN_IDLE_SECONDS: "45",
        MG4K_PAID_START_STABILIZATION_SECONDS: "90",
        MG4K_CONTAINER_BUILD_ID: CONTAINER_BUILD_ID,
      },
      enableInternet: true,
    },
    cancellationOptions: {
      portReadyTimeoutMS: 60_000,
    },
  };

  const assertSafeToRecycleCurrentInstance = async (reason) => {
    const blocker = await hasInFlightProcessorJob(env, new Set([CONTAINER_INSTANCE_NAME]));
    if (blocker) {
      throw new Error(
        `processor_recycle_waiting_for_inflight_job:${blocker.id}:${blocker.instance_name}:${blocker.status}:${reason}`
      );
    }
  };

  const recycleCurrentInstance = async (reason) => {
    await assertSafeToRecycleCurrentInstance(reason);
    const result = await container.shutdownContainer(reason);
    console.log("MG4K_PROCESSOR_PRESTART_RECYCLE", {
      instance: CONTAINER_INSTANCE_NAME,
      reason,
      was_running: Boolean(result?.was_running),
    });
    return result;
  };

  const startWithCapacityRecovery = async () => {
    let lastError = null;
    for (let attempt = 1; attempt <= 3; attempt++) {
      try {
        await container.startAndWaitForPorts(startConfig);
        return;
      } catch (error) {
        lastError = error;
        const message = String(error?.message || error || "");
        const capacityError = message.includes("Maximum number of running container instances exceeded");
        if (!capacityError || attempt >= 3) throw error;

        await recycleCurrentInstance(`capacity_recovery_${CONTAINER_BUILD_ID}_attempt_${attempt}`);
        await new Promise(resolve => setTimeout(resolve, 750 * attempt));
      }
    }
    throw lastError || new Error("processor_start_failed");
  };

  // IMPORTANT: do not call startAndWaitForPorts before stale-slot recovery.
  // Cloudflare enforces max_instances before health can be inspected, so an
  // old Container can otherwise block the very code that would replace it.
  const rememberedBuild = String(
    await env.SESSION_STATE_R7.get("processor:active_build_id") || ""
  );
  if (rememberedBuild !== CONTAINER_BUILD_ID) {
    await recycleCurrentInstance(`prestart_upgrade_to_${CONTAINER_BUILD_ID}`);
  }

  const readHealth = async () => {
    const response = await container.fetch(new Request("http://container/healthz"));
    const health = await response.json().catch(() => ({}));
    return { response, health };
  };

  await startWithCapacityRecovery();
  let { response: healthResponse, health } = await readHealth();

  if (!healthResponse.ok || String(health.build_id || "") !== CONTAINER_BUILD_ID) {
    await recycleCurrentInstance(`health_mismatch_upgrade_to_${CONTAINER_BUILD_ID}`);
    await startWithCapacityRecovery();
    ({ response: healthResponse, health } = await readHealth());
  }

  if (!healthResponse.ok || String(health.build_id || "") !== CONTAINER_BUILD_ID) {
    throw new Error(
      `container_build_mismatch:expected=${CONTAINER_BUILD_ID}:actual=${String(health.build_id || "unknown")}`
    );
  }

  await env.SESSION_STATE_R7.put("processor:active_instance", CONTAINER_INSTANCE_NAME);
  await env.SESSION_STATE_R7.put("processor:active_build_id", CONTAINER_BUILD_ID);
  return container;
}

function applyContainerState(job, state) {
  if (!state) return job;
  if (state.status) job.status = String(state.status);
  if (state.stage) job.stage = String(state.stage);
  if (state.progress !== undefined) job.progress = Math.max(0, Math.min(100, Number(state.progress || 0)));
  if (state.error !== undefined) job.error = state.error || null;
  if (state.diagnostic_excerpt !== undefined) job.diagnostic_excerpt = state.diagnostic_excerpt || null;
  if (state.validation !== undefined) job.validation = state.validation || null;
  if (state.decision !== undefined) job.decision = state.decision || null;
  if (state.provider_cost_usd !== undefined) job.provider_cost_usd = state.provider_cost_usd === null ? null : Number(state.provider_cost_usd);
  if (state.provider_request_count !== undefined) job.provider_request_count = Math.max(0, Number(state.provider_request_count || 0));
  if (state.provider_cost_complete !== undefined) job.provider_cost_complete = Boolean(state.provider_cost_complete);
  if (state.provider_cost_provider !== undefined) job.provider_cost_provider = state.provider_cost_provider || null;
  if (state.diagnostic_code !== undefined) job.diagnostic_code = state.diagnostic_code || null;
  if (state.diagnostic_layer !== undefined) job.diagnostic_layer = state.diagnostic_layer || null;
  if (state.diagnostic_summary !== undefined) job.diagnostic_summary = state.diagnostic_summary || null;
  if (state.diagnostic_action !== undefined) job.diagnostic_action = state.diagnostic_action || null;
  if (state.diagnostic_retryable !== undefined) job.diagnostic_retryable = state.diagnostic_retryable;
  if (state.diagnostic_http_status !== undefined) job.diagnostic_http_status = state.diagnostic_http_status ?? null;
  if (state.diagnostic_provider_model !== undefined) job.diagnostic_provider_model = state.diagnostic_provider_model || null;
  if (state.diagnostic_routing_step !== undefined) job.diagnostic_routing_step = state.diagnostic_routing_step || null;
  job.container_result_available = Boolean(state.result_available);
  if (state.build_id) job.container_build_id = String(state.build_id);
  job.container_synced_at = now();
  return job;
}

async function storeContainerResult(env, container, job) {
  const response = await container.fetch(new Request(`http://container/jobs/${job.id}/result`));
  if (!response.ok) {
    let detail = "";
    try { detail = (await response.text()).slice(0, 500); } catch {}
    throw new Error(`container_result_http_${response.status}${detail ? ":" + detail : ""}`);
  }

  const resultName = cleanName(response.headers.get("x-mg4k-filename") || `4K_${job.filename}`);
  const contentType = response.headers.get("content-type") || "image/jpeg";
  const declaredBytes = Number(response.headers.get("content-length") || 0);

  // Buffer the Container response before R2. This avoids keeping a cross-runtime
  // response stream open while R2 consumes it and gives us deterministic size
  // validation and diagnostics.
  const bytes = await response.arrayBuffer();
  const actualBytes = bytes.byteLength;
  if (!actualBytes) {
    throw new Error("container_result_empty");
  }
  if (declaredBytes > 0 && declaredBytes !== actualBytes) {
    throw new Error(`container_result_size_mismatch:declared=${declaredBytes}:actual=${actualBytes}`);
  }
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const checksum = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, "0")).join("");

  const resultKey = `sessions/${job.session_id}/jobs/${job.id}/result/${resultName}`;
  if (job.result_key && job.result_key !== resultKey) {
    try { await env.TEMP_BUFFER_R7.delete(job.result_key); } catch {}
  }

  await env.TEMP_BUFFER_R7.put(resultKey, bytes, {
    httpMetadata: { contentType },
    customMetadata: {
      session_id: job.session_id,
      job_id: job.id,
      kind: "result",
      original_name: resultName,
      bytes: String(actualBytes),
      sha256: checksum,
      persist_source: "container_pull",
    },
  });
  job.archive_result_key = await archiveBytes(env, {
    job,
    bytes,
    contentType,
    name: resultName,
    kind: "RESULT",
    status: job.status || "processing",
    checksum,
    persistSource: "container_pull",
  });

  job.result_key = resultKey;
  job.result_bytes = actualBytes;
  job.result_content_type = contentType;
  job.result_checksum_sha256 = checksum;
  job.result_persist_source = "container_pull";
  job.result_persisted_at = now();
  job.result_sync_attempts = 0;
  job.result_sync_warning = null;
  return job;
}

async function dispatchJobToContainer(env, job, origin) {
  const container = await ensureCloudProcessor(env, origin);
  const source = await env.TEMP_BUFFER_R7.get(job.source_key);
  if (!source) {
    throw new Error("source_missing_before_container_dispatch");
  }
  const bytes = await source.arrayBuffer();
  const headers = new Headers({
    "content-type": job.content_type || "application/octet-stream",
    "x-mg4k-filename": encodeURIComponent(job.filename),
    "x-mg4k-locks": JSON.stringify(job.locks || {}),
    "x-mg4k-mode": job.mode || "generative",
    "x-mg4k-source-size": String(bytes.byteLength),
    "x-mg4k-prompt-b64": job.prompt_override ? bytesToBase64url(new TextEncoder().encode(job.prompt_override)) : "",
  });
  const response = await container.fetch(new Request(
    `http://container/jobs/${job.id}/start`,
    { method: "POST", headers, body: bytes },
  ));
  const state = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(state.error || `container_dispatch_http_${response.status}`);
  }
  job.container_dispatched = true;
  job.container_dispatched_at = now();
  job.container_instance_name = CONTAINER_INSTANCE_NAME;
  applyContainerState(job, state);
  await writeJob(env, job);
  return { job, container };
}

async function syncJobFromContainer(env, job, origin) {
  const owner = processorContainerForJob(env, job);
  let container = owner.container;

  const preDispatchStages = new Set([
    "uploaded",
    "processor_starting",
    "processor_ready_waiting_claim",
    "container_dispatch_pending",
  ]);
  const needsDispatch = job.status === "queued"
    && preDispatchStages.has(String(job.stage || ""))
    && !job.result_key;

  if (needsDispatch) {
    return (await dispatchJobToContainer(env, job, origin)).job;
  }

  if (!job.container_dispatched && !needsDispatch) {
    job.status = "failed";
    job.stage = "manual_retry_required";
    job.error = "Container state is unavailable. Automatic redispatch is blocked to prevent an unintended paid regeneration.";
    job.manual_retry_required = true;
    await writeJob(env, job);
    return job;
  }

  let response;
  try {
    response = await container.fetch(new Request(`http://container/jobs/${job.id}/status`));
  } catch (error) {
    if (owner.instanceName !== CONTAINER_INSTANCE_NAME) {
      throw new Error(`container_owner_unavailable:${owner.instanceName}:${error?.message || String(error)}`);
    }
    container = await ensureCloudProcessor(env, origin);
    response = await container.fetch(new Request(`http://container/jobs/${job.id}/status`));
  }

  if (response.status === 404) {
    job.container_dispatched = false;
    const safeToRedispatch = job.status === "queued"
      && preDispatchStages.has(String(job.stage || ""))
      && !job.result_key;

    if (safeToRedispatch) {
      job.stage = "container_dispatch_pending";
      job.progress = Math.max(Number(job.progress || 0), 8);
      await writeJob(env, job);
      return (await dispatchJobToContainer(env, job, origin)).job;
    }

    const hadReachedReview = job.status === "review"
      || job.container_review_status === "review"
      || Number(job.progress || 0) >= 90;
    job.status = "failed";
    job.stage = hadReachedReview ? "result_lost_after_container_restart" : "manual_retry_required";
    job.error = hadReachedReview
      ? "The generated RESULT is no longer available after the temporary Container restarted. Automatic regeneration was blocked to prevent a duplicate paid API call."
      : "Container state was lost during processing. Automatic redispatch was blocked to prevent an unintended paid API call.";
    job.manual_retry_required = true;
    job.result_key = null;
    await writeJob(env, job);
    return job;
  }

  const state = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(state.error || `container_status_http_${response.status}`);
  }

  const hadResult = Boolean(job.result_key);
  const previousStatus = job.status;
  applyContainerState(job, state);

  // Persist authoritative Container state before copying large result bytes.
  // A transient R2/result transfer failure must never rewrite a valid REVIEW
  // state into a contradictory terminal-looking sync error.
  await writeJob(env, job);

  if (state.result_available && (!hadResult || (state.status === "done" && previousStatus !== "done"))) {
    try {
      await storeContainerResult(env, container, job);
      job.result_sync_warning = null;
      job.error = state.error || null;
      await writeJob(env, job);
    } catch (error) {
      job.container_review_status = String(state.status || job.status || "");
      job.result_sync_attempts = Number(job.result_sync_attempts || 0) + 1;
      job.result_sync_warning = error?.message || String(error);
      job.error = null;

      if (job.result_sync_attempts >= 5) {
        job.status = "failed";
        job.stage = "result_sync_failed";
        job.manual_retry_required = true;
        job.progress = Math.min(89, Math.max(1, Number(state.progress || job.progress || 0)));
        job.error = "RESULT was generated but could not be copied to R2 after 5 attempts. Paid regeneration was not started.";
      } else {
        job.status = "processing";
        job.stage = "result_sync_retry";
        job.progress = Math.min(89, Math.max(1, Number(state.progress || job.progress || 0)));
      }
      await writeJob(env, job);
    }
  }

  if (job.billing_reservation_id && ["review", "done", "failed", "skipped"].includes(String(job.status || ""))) {
    await settleBilling(env, job, {
      resultAvailable: Boolean(job.result_key || state.result_available),
      hardSystemFailure: String(job.status || "") === "failed",
    });
  }

  return job;
}

async function sendContainerDecision(env, job, action) {
  const { container, instanceName } = processorContainerForJob(env, job);
  const response = await container.fetch(new Request(
    `http://container/jobs/${job.id}/decision`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ action }),
    },
  ));
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `container_decision_http_${response.status}:${instanceName}`);
  }
  return payload;
}

MG4KProcessor.outboundByHost = {
  "mg4k.worker": async (request, env) => {
    const url = new URL(request.url);
    if (!url.pathname.startsWith("/api/processor/")) {
      return json({ error: "container_internal_route_forbidden" }, 403);
    }
    return handleApi(request, env, null, url);
  },
};

async function handleApi(request, env, ctx, url) {
  const path = url.pathname;
  const method = request.method.toUpperCase();

  if (path === "/api/health" && method === "GET") {
    return json({
      status: "ok",
      service: "MG 4K Cloud Intake",
      version: "cloud-r2-ephemeral",
      worker: "4k-upscaler",
      worker_build_id: WORKER_BUILD_ID,
      processor_build_expected: CONTAINER_BUILD_ID,
      processor_instance_expected: CONTAINER_INSTANCE_NAME,
      processor_lifecycle: "tini-pid1-group-forwarding",
      session_ttl_seconds: SESSION_TTL,
      persistence: "ephemeral-session-only",
      persistent_user_database: false,
      session_store: "Cloudflare KV with sliding TTL",
      image_buffer: "private Cloudflare R2, session-scoped",
      billing_mode: billingMode(env),
      billing_enabled: billingEnabled(env),
      scene_analysis_enabled: sceneAnalysisEnabled(env),
      scene_analysis_model: sceneAnalysisEnabled(env) ? sceneAnalysisModel(env) : null,
      prompt_assistant_enabled: promptAssistantEnabled(env),
      prompt_assistant_model: promptAssistantEnabled(env) ? promptAssistantModel(env) : null,
      prompt_assistant_enabled: promptAssistantEnabled(env),
      prompt_assistant_model: promptAssistantEnabled(env) ? promptAssistantModel(env) : null,
    });
  }

  if (path === "/api/scene-analysis" && method === "POST") {
    if (!sceneAnalysisEnabled(env)) return json({ error:"scene_analysis_disabled" }, 503);
    const session = await authorizeSession(request, url, env);
    if (!session) return json({ error:"session_required" }, 403);

    const budget = await consumeSceneAnalysisBudget(env, session.id);
    if (!budget.ok) return json({ error:budget.error }, 429);

    let form;
    try { form = await request.formData(); } catch { return json({ error:"invalid_form_data" }, 400); }
    const file = form.get("file");
    if (!file || typeof file !== "object" || typeof file.arrayBuffer !== "function") {
      return json({ error:"image_file_required" }, 400);
    }
    if (Number(file.size || 0) > 8 * 1024 * 1024) {
      return json({ error:"scene_analysis_file_too_large", max_bytes:8*1024*1024 }, 413);
    }
    const type = String(file.type || "");
    if (!type.startsWith("image/")) return json({ error:"unsupported_scene_analysis_type" }, 415);

    try {
      const analysis = await analyzeSceneWithOpenRouter(env, file);
      return json({
        ok:true,
        ...analysis,
        catalog_version:"scene-locks-v1",
        analyzed_at:now(),
      });
    } catch (error) {
      console.warn("MG4K_SCENE_ANALYSIS_ERROR", error?.message || String(error));
      return json({ error:String(error?.message || "scene_analysis_failed").slice(0,400) }, 502);
    }
  }

  if (path === "/api/prompt-assistant" && method === "POST") {
    if (!promptAssistantEnabled(env)) return json({ error:"prompt_assistant_disabled" }, 503);
    const session = await authorizeSession(request, url, env);
    if (!session) return json({ error:"session_required" }, 403);
    const budget = await consumePromptAssistantBudget(env, session.id);
    if (!budget.ok) return json({ error:budget.error }, 429);
    let body = {};
    try { body = await request.json(); } catch { return json({ error:"invalid_json" }, 400); }
    const context = sanitizeAssistantContext(body);
    if (!context.request) return json({ error:"prompt_assistant_request_required" }, 400);
    try {
      return json({ ok:true, ...(await runPromptAssistant(env, context)), generated_at:now() });
    } catch (error) {
      console.warn("MG4K_PROMPT_ASSISTANT_ERROR", error?.message || String(error));
      return json({ error:String(error?.message || "prompt_assistant_failed").slice(0,400) }, 502);
    }
  }

  if (path === "/api/billing/config" && method === "GET") {
    const enabled = billingEnabled(env);
    return json({
      enabled,
      mode: billingMode(env),
      gateway_url: enabled ? String(env.BILLING_GATEWAY_URL || "").replace(/\/$/, "") : null,
      credits_per_generation: 1,
      trial_credits: 2,
    });
  }

  if (path === "/api/session/start" && method === "POST") {
    const id = crypto.randomUUID();
    const token = randomToken();
    const session = {
      id,
      access_hash: await sha256(token),
      created_at: now(),
      updated_at: now(),
      jobs: [],
    };
    await writeSession(env, session);
    return json({ id, token, ttl_seconds: SESSION_TTL }, 201);
  }

  if (path === "/api/session/heartbeat" && method === "POST") {
    let body = {};
    try { body = await request.json(); } catch {}
    const session = await authorizeSession(request, url, env, body);
    if (!session) return json({ error: "session_unauthorized" }, 403);
    await writeSession(env, session);

    for (const jobId of session.jobs || []) {
      const job = await readJob(env, jobId);
      if (job) await writeJob(env, job);
    }

    return json({ ok: true, ttl_seconds: SESSION_TTL });
  }

  if (path === "/api/session/end" && method === "POST") {
    let body = {};
    try { body = await request.json(); } catch {}
    const session = await authorizeSession(request, url, env, body);
    if (!session) return json({ ok: true });
    const deleted = await endSession(env, session);
    return json({ ok: true, deleted_objects: deleted });
  }

  if (path === "/api/jobs" && method === "POST") {
    const session = await authorizeSession(request, url, env);
    if (!session) return json({ error: "session_required" }, 403);

    const commercial = billingEnabled(env);
    let billingPermitToken = "";
    let billingPermit = null;
    if (commercial) {
      billingPermitToken = request.headers.get("x-mg4k-entitlement") || "";
      try {
        billingPermit = await verifyBillingPermit(billingPermitToken, env);
      } catch (error) {
        return json({ error: error?.message || "billing_permit_required" }, 402);
      }
    }

    let form;
    try {
      form = await request.formData();
    } catch {
      return json({ error: "invalid_form_data" }, 400);
    }

    const file = form.get("file");
    if (!file || typeof file !== "object" || typeof file.stream !== "function") {
      return json({ error: "image_file_required" }, 400);
    }

    if (file.size > MAX_UPLOAD_BYTES) {
      return json({ error: "file_too_large", max_bytes: MAX_UPLOAD_BYTES }, 413);
    }

    const type = String(file.type || "application/octet-stream");
    if (!type.startsWith("image/") && !/\.(heic|heif)$/i.test(String(file.name || ""))) {
      return json({ error: "unsupported_file_type" }, 415);
    }

    let locks = {};
    try {
      locks = JSON.parse(String(form.get("locks") || "{}"));
    } catch {
      return json({ error: "invalid_lock_profile" }, 400);
    }

    const mode = String(form.get("mode") || "generative");
    const promptOverride = String(form.get("prompt") || "").trim();
    if (promptOverride.length > 30000) return json({ error: "prompt_too_long", max_chars: 30000 }, 413);
    const id = crypto.randomUUID();
    const accessToken = randomToken();
    const filename = cleanName(file.name || "source-image");
    const sourceKey = `sessions/${session.id}/jobs/${id}/source/${filename}`;

    const sourceBytes = await file.arrayBuffer();
    await env.TEMP_BUFFER_R7.put(sourceKey, sourceBytes, {
      httpMetadata: { contentType: type },
      customMetadata: {
        session_id: session.id,
        job_id: id,
        kind: "source",
        original_name: filename,
      },
    });
    const sourceChecksum = await sha256(sourceBytes);
    await archiveBytes(env, {
      job: { id, session_id: session.id, filename, mode, locks, prompt_sha256: promptOverride ? await sha256(promptOverride) : null },
      bytes: sourceBytes,
      contentType: type,
      name: filename,
      kind: "SOURCE",
      status: "uploaded",
      checksum: sourceChecksum,
      persistSource: "worker_upload",
    });

    let billingClaim = null;
    if (commercial) {
      try {
        billingClaim = await billingGatewayRequest(env, "/api/internal/generation/claim", {
          permit: billingPermitToken,
          job_id: id,
        });
      } catch (error) {
        try { await env.TEMP_BUFFER_R7.delete(sourceKey); } catch {}
        return json({ error: error?.message || "billing_permit_claim_failed" }, 402);
      }
    }

    const job = {
      id,
      session_id: session.id,
      access_hash: await sha256(accessToken),
      status: "queued",
      stage: "uploaded",
      progress: 5,
      created_at: now(),
      updated_at: now(),
      filename,
      content_type: type,
      bytes: Number(file.size || 0),
      mode,
      locks,
      prompt_override: promptOverride || null,
      prompt_sha256: promptOverride ? await sha256(promptOverride) : null,
      source_key: sourceKey,
      result_key: null,
      error: null,
      validation: null,
      decision: null,
      billing_account_id: commercial ? String(billingClaim?.account_id || billingPermit?.sub || "") : null,
      billing_reservation_id: commercial ? String(billingClaim?.reservation_id || billingPermit?.reservation_id || "") : null,
      billing_permit_jti: commercial ? String(billingPermit?.jti || "") : null,
      billing_settlement: commercial ? "reserved" : null,
    };

    await writeJob(env, job);
    if (!session.jobs.includes(id)) session.jobs.push(id);
    await writeSession(env, session);

    try {
      const starting = await readJob(env, id);
      if (starting && starting.status === "queued") {
        starting.stage = "processor_starting";
        starting.progress = Math.max(Number(starting.progress || 0), 6);
        await writeJob(env, starting);
        await dispatchJobToContainer(env, starting, url.origin);
      }
    } catch (error) {
      console.error("MG4K_CONTAINER_DISPATCH_FAILED", error);
      const current = await readJob(env, id);
      if (current && ["queued", "processing"].includes(current.status)) {
        current.status = "failed";
        current.stage = "container_dispatch_failed";
        current.error = error?.message || String(error);
        await writeJob(env, current);
        await settleBilling(env, current, { resultAvailable: false, hardSystemFailure: true });
      }
    }

    const created = await readJob(env, id) || job;
    return json({
      id,
      token: accessToken,
      status: created.status,
      stage: created.stage,
      progress: created.progress,
    }, 201);
  }

  const jobStatusMatch = path.match(/^\/api\/jobs\/([0-9a-f-]+)$/i);
  if (jobStatusMatch && method === "GET") {
    let job = await readJob(env, jobStatusMatch[1]);
    if (!job) return json({ error: "job_not_found" }, 404);
    if (!await authorizeJob(request, url, job)) return json({ error: "forbidden" }, 403);
    const durableReview = persistedReviewIsDurable(job);
    if (!["done", "failed", "skipped"].includes(job.status) && !durableReview) {
      try {
        job = await syncJobFromContainer(env, job, url.origin);
      } catch (error) {
        console.error("MG4K_CONTAINER_SYNC_FAILED", job.id, error);
        job.result_sync_warning = error?.message || String(error);
        if (!["review", "done"].includes(job.status)) {
          job.stage = "container_sync_retry";
        }
        await writeJob(env, job);
      }
    }
    return json(publicJob(job));
  }

  const jobDecisionMatch = path.match(/^\/api\/jobs\/([0-9a-f-]+)\/decision$/i);
  if (jobDecisionMatch && method === "POST") {
    const job = await readJob(env, jobDecisionMatch[1]);
    if (!job) return json({ error: "job_not_found" }, 404);
    if (!await authorizeJob(request, url, job)) return json({ error: "forbidden" }, 403);
    if (job.status !== "review") return json({ error: "job_not_waiting_for_review", status: job.status }, 409);
    if (!persistedReviewIsDurable(job)) {
      return json({ error: "review_result_not_persisted", status: job.status }, 409);
    }

    let body = {};
    try { body = await request.json(); } catch {}
    const action = String(body.action || "").toLowerCase();
    if (!["approve", "reject", "skip"].includes(action)) return json({ error: "invalid_decision" }, 400);

    // The review pixels are already durable in R2. Container notification is
    // best-effort only, so a retired/stale Container can never hold the user's
    // decision hostage or block the next job.
    if (job.container_instance_name) {
      const notify = sendContainerDecision(env, job, action).catch(error => {
        console.warn("MG4K_REVIEW_DECISION_CONTAINER_NOTIFY_WARNING", job.id, error?.message || String(error));
      });
      if (ctx?.waitUntil) ctx.waitUntil(notify);
    }

    job.decision = action;
    job.container_dispatched = false;
    job.review_detached_from_container = true;
    job.review_decided_at = now();
    job.manual_retry_required = false;

    if (action === "approve") {
      job.status = "done";
      job.stage = "final";
      job.progress = 100;
      job.error = null;
      const validationPassed = Boolean(job.validation?.passed);
      job.approval_mode = validationPassed ? "explicit_user_approval" : "explicit_user_manual_override";
    } else if (action === "reject") {
      job.status = "failed";
      job.stage = "rejected";
      job.progress = 100;
      job.error = "rejected_by_user";
      job.approval_mode = "rejected_by_user";
    } else {
      job.status = "skipped";
      job.stage = "skipped_by_user";
      job.progress = 100;
      job.error = null;
      job.approval_mode = "skipped_by_user";
    }

    await writeJob(env, job);
    return json({ ok: true, job: publicJob(job) });
  }

  const jobResetMatch = path.match(/^\/api\/jobs\/([0-9a-f-]+)\/reset$/i);
  if (jobResetMatch && method === "POST") {
    const job = await readJob(env, jobResetMatch[1]);
    if (!job) return json({ error: "job_not_found" }, 404);
    if (!await authorizeJob(request, url, job)) return json({ error: "forbidden" }, 403);

    const status = String(job.status || "");
    if (["queued", "processing", "decision_pending"].includes(status)) {
      return json({
        error: "job_busy_cannot_reset",
        status,
        stage: job.stage || null,
      }, 409);
    }

    let resetWarning = null;
    if (status === "review" && job.container_instance_name) {
      const notify = sendContainerDecision(env, job, "skip").catch(error => {
        resetWarning = error?.message || String(error);
        console.warn("MG4K_RESET_CONTAINER_NOTIFY_WARNING", job.id, resetWarning);
      });
      if (ctx?.waitUntil) ctx.waitUntil(notify);
    }

    job.decision = "reset";
    job.status = "skipped";
    job.stage = "operator_reset";
    job.progress = 100;
    job.error = null;
    job.manual_retry_required = false;
    job.container_dispatched = false;
    job.reset_at = now();
    job.reset_warning = resetWarning;
    await writeJob(env, job);
    return json({ ok: true, job: publicJob(job), reset_warning: resetWarning });
  }

  const jobSourceMatch = path.match(/^\/api\/jobs\/([0-9a-f-]+)\/source$/i);
  if (jobSourceMatch && method === "GET") {
    const job = await readJob(env, jobSourceMatch[1]);
    if (!job) return json({ error: "job_not_found" }, 404);
    if (!await authorizeJob(request, url, job)) return json({ error: "forbidden" }, 403);
    const object = await env.TEMP_BUFFER_R7.get(job.source_key);
    if (!object) return json({ error: "source_missing" }, 404);

    const headers = new Headers();
    object.writeHttpMetadata(headers);
    headers.set("etag", object.httpEtag);
    headers.set("cache-control", "private, no-store");
    headers.set("content-disposition", "inline");
    return new Response(object.body, { headers });
  }

  const jobResultMatch = path.match(/^\/api\/jobs\/([0-9a-f-]+)\/result$/i);
  if (jobResultMatch && method === "GET") {
    let job = await readJob(env, jobResultMatch[1]);
    if (!job) return json({ error: "job_not_found" }, 404);
    if (!await authorizeJob(request, url, job)) return json({ error: "forbidden" }, 403);
    if (!job.result_key && !["failed", "skipped"].includes(job.status)) {
      try { job = await syncJobFromContainer(env, job, url.origin); } catch {}
    }
    if (!job.result_key) return json({ error: "result_not_ready", status: job.status }, 409);

    let object = await env.TEMP_BUFFER_R7.get(job.result_key);
    let servedFromArchive = false;
    if (!object && job.archive_result_key) {
      object = await env.TEMP_BUFFER_R7.get(job.archive_result_key);
      if (object) {
        servedFromArchive = true;
        job.result_recovered_from_archive_at = now();
        job.result_recovery_warning = null;
        await writeJob(env, job);
        console.warn("MG4K_RESULT_RECOVERED_FROM_ARCHIVE", job.id, job.archive_result_key);
      }
    }
    if (!object) {
      job.result_recovery_warning = job.archive_result_key
        ? "Both live RESULT and archived RESULT are missing from R2."
        : "Live RESULT is missing from R2 and no archive_result_key is recorded.";
      await writeJob(env, job);
      return json({
        error: "result_missing",
        archive_available: Boolean(job.archive_result_key),
      }, 404);
    }

    const headers = new Headers();
    object.writeHttpMetadata(headers);
    if (servedFromArchive) headers.set("x-mg4k-result-source", "archive");
    headers.set("etag", object.httpEtag);
    headers.set("cache-control", "private, no-store");
    if (url.searchParams.get("download") === "1") {
      headers.set("content-disposition", `attachment; filename="4K_${cleanName(job.filename)}"`);
    } else {
      headers.set("content-disposition", "inline");
    }
    return new Response(object.body, { headers });
  }

  if (path === "/api/processor/pair/request" && method === "POST") {
    const code = randomCode();
    const token = randomToken();
    const pair = {
      code,
      token_hash: await sha256(token),
      created_at: now(),
    };
    await env.SESSION_STATE_R7.put(`pair:${code}`, JSON.stringify(pair), { expirationTtl: PAIR_TTL });
    return json({ code, token, expires_in_seconds: PAIR_TTL }, 201);
  }

  if (path === "/api/processor/pair/approve" && method === "POST") {
    let body = {};
    try { body = await request.json(); } catch {}
    const code = String(body.code || "").trim().toUpperCase();
    if (!code) return json({ error: "pair_code_required" }, 400);

    const raw = await env.SESSION_STATE_R7.get(`pair:${code}`);
    if (!raw) return json({ error: "pair_code_invalid_or_expired" }, 404);
    const pair = JSON.parse(raw);

    await env.SESSION_STATE_R7.put("processor:token_hash", pair.token_hash);
    await env.SESSION_STATE_R7.delete(`pair:${code}`);
    return json({ paired: true });
  }

  if (path === "/api/processor/pair/status" && method === "POST") {
    const ok = await authorizeProcessor(request, env);
    return json({ paired: ok }, ok ? 200 : 403);
  }

  if (path === "/api/processor/claim" && method === "POST") {
    if (!await authorizeProcessor(request, env)) {
      const configured = Boolean(
        String(env.PROCESSOR_SHARED_SECRET || "").trim()
        || await env.SESSION_STATE_R7.get("processor:token_hash")
      );
      return json({ error: configured ? "processor_unauthorized" : "processor_not_paired" }, configured ? 403 : 428);
    }

    const job = await nextQueuedJob(env);
    if (!job) return json({ job: null }, 200);

    job.status = "processing";
    job.stage = "claimed";
    job.progress = Math.max(Number(job.progress || 0), 10);
    job.claimed_at = now();
    await writeJob(env, job);

    return json({
      job: {
        ...publicJob(job),
        source_url: `/api/processor/jobs/${job.id}/source`,
      },
    });
  }

  const processorSourceMatch = path.match(/^\/api\/processor\/jobs\/([0-9a-f-]+)\/source$/i);
  if (processorSourceMatch && method === "GET") {
    if (!await authorizeProcessor(request, env)) return json({ error: "processor_unauthorized" }, 403);
    const job = await readJob(env, processorSourceMatch[1]);
    if (!job) return json({ error: "job_not_found" }, 404);
    const object = await env.TEMP_BUFFER_R7.get(job.source_key);
    if (!object) return json({ error: "source_missing" }, 404);

    const headers = new Headers();
    object.writeHttpMetadata(headers);
    headers.set("cache-control", "private, no-store");
    headers.set("x-mg4k-filename", encodeURIComponent(job.filename));
    return new Response(object.body, { headers });
  }

  const processorProgressMatch = path.match(/^\/api\/processor\/jobs\/([0-9a-f-]+)\/progress$/i);
  if (processorProgressMatch && method === "POST") {
    if (!await authorizeProcessor(request, env)) return json({ error: "processor_unauthorized" }, 403);
    const job = await readJob(env, processorProgressMatch[1]);
    if (!job) return json({ error: "job_not_found" }, 404);
    let body = {};
    try { body = await request.json(); } catch {}
    job.status = String(body.status || job.status || "processing");
    job.stage = String(body.stage || job.stage || "processing");
    job.progress = Math.max(0, Math.min(100, Number(body.progress ?? job.progress ?? 0)));
    if (body.validation !== undefined) job.validation = body.validation;
    if (body.error !== undefined) job.error = body.error;
    await writeJob(env, job);
    return json({ ok: true, job: publicJob(job) });
  }

  const processorDecisionMatch = path.match(/^\/api\/processor\/jobs\/([0-9a-f-]+)\/decision$/i);
  if (processorDecisionMatch && method === "GET") {
    if (!await authorizeProcessor(request, env)) return json({ error: "processor_unauthorized" }, 403);
    const job = await readJob(env, processorDecisionMatch[1]);
    if (!job) return json({ error: "job_not_found" }, 404);
    return json({ decision: job.decision || null, status: job.status, stage: job.stage });
  }

  const processorResultMatch = path.match(/^\/api\/processor\/jobs\/([0-9a-f-]+)\/result$/i);
  if (processorResultMatch && method === "POST") {
    if (!await authorizeProcessor(request, env)) return json({ error: "processor_unauthorized" }, 403);

    const job = await readJob(env, processorResultMatch[1]);
    if (!job) return json({ error: "job_not_found" }, 404);

    const session = await readSession(env, job.session_id);
    if (!session) {
      await env.SESSION_STATE_R7.delete(`job:${job.id}`);
      return json({ error: "session_expired" }, 410);
    }

    const contentType = request.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      let body = {};
      try { body = await request.json(); } catch {}
      job.status = String(body.status || "failed");
      job.stage = String(body.stage || job.status);
      job.progress = Math.max(0, Math.min(100, Number(body.progress ?? (job.status === "failed" ? job.progress : 100))));
      job.error = body.error || null;
      job.validation = body.validation || job.validation || null;
      const providerCost = sanitizeProviderCostSummary(body.provider_cost);
      if (providerCost) {
        job.provider_cost_usd = providerCost.cost_usd;
        job.provider_request_count = providerCost.request_count;
        job.provider_cost_complete = providerCost.complete;
        job.provider_cost_provider = providerCost.provider;
        job.provider_cost_recorded_at = now();
      }
      await writeJob(env, job);
      if (job.billing_reservation_id && ["failed", "skipped", "done"].includes(job.status)) {
        await settleBilling(env, job, {
          resultAvailable: Boolean(job.result_key),
          hardSystemFailure: job.status === "failed",
        });
      }
      return json({ ok: true, job: publicJob(job) });
    }

    let form;
    try { form = await request.formData(); } catch { return json({ error: "invalid_form_data" }, 400); }
    const file = form.get("file");
    if (!file || typeof file !== "object" || typeof file.stream !== "function") {
      return json({ error: "result_file_required" }, 400);
    }

    let validation = null;
    try { validation = JSON.parse(String(form.get("validation") || "null")); } catch {}
    let providerCost = null;
    try { providerCost = sanitizeProviderCostSummary(JSON.parse(String(form.get("provider_cost") || "null"))); } catch {}
    const requestedStatus = String(form.get("status") || "done").toLowerCase();

    const resultName = cleanName(file.name || `4K_${job.filename}`);
    const resultKey = `sessions/${job.session_id}/jobs/${job.id}/result/${resultName}`;
    const resultContentType = String(file.type || "image/jpeg");
    const bytes = await file.arrayBuffer();
    const actualBytes = bytes.byteLength;
    if (!actualBytes) return json({ error: "result_file_empty" }, 400);
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    const checksum = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, "0")).join("");

    if (job.result_key && job.result_key !== resultKey) {
      try { await env.TEMP_BUFFER_R7.delete(job.result_key); } catch {}
    }
    await env.TEMP_BUFFER_R7.put(resultKey, bytes, {
      httpMetadata: { contentType: resultContentType },
      customMetadata: {
        session_id: job.session_id,
        job_id: job.id,
        kind: "result",
        original_name: resultName,
        bytes: String(actualBytes),
        sha256: checksum,
        persist_source: "container_push",
      },
    });

    const archiveKind = requestedStatus === "review" ? "DONOR_RAW" : "FINAL";
    const archiveKey = await archiveBytes(env, {
      job,
      bytes,
      contentType: resultContentType,
      name: resultName,
      kind: archiveKind,
      status: requestedStatus,
      checksum,
      persistSource: "container_push",
    });
    if (requestedStatus === "review") {
      job.archive_donor_keys = Array.from(new Set([...(job.archive_donor_keys || []), archiveKey]));
    } else {
      job.archive_final_key = archiveKey;
    }

    job.result_key = resultKey;
    job.result_bytes = actualBytes;
    job.result_content_type = resultContentType;
    job.result_checksum_sha256 = checksum;
    job.result_persist_source = "container_push";
    job.result_persisted_at = now();
    job.result_sync_attempts = 0;
    job.result_sync_warning = null;
    if (providerCost) {
      job.provider_cost_usd = providerCost.cost_usd;
      job.provider_request_count = providerCost.request_count;
      job.provider_cost_complete = providerCost.complete;
      job.provider_cost_provider = providerCost.provider;
      job.provider_cost_recorded_at = now();
    }
    job.status = requestedStatus === "review" ? "review" : "done";
    job.stage = requestedStatus === "review" ? "awaiting_approval" : "final";
    job.progress = requestedStatus === "review" ? 90 : 100;
    job.validation = validation;
    job.error = null;
    if (requestedStatus === "review") {
      job.container_dispatched = false;
      job.review_detached_from_container = true;
      job.container_review_released_at = now();
    } else {
      job.decision = "approve";
    }
    await writeJob(env, job);
    await writeSession(env, session);
    if (job.billing_reservation_id) {
      await settleBilling(env, job, { resultAvailable: true, hardSystemFailure: false });
    }

    return json({ ok: true, job: publicJob(job) });
  }

  return json({ error: "api_not_found" }, 404);
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/api/")) {
      try {
        return await handleApi(request, env, ctx, url);
      } catch (error) {
        console.error("MG4K_WORKER_ERROR", error);
        return json({
          error: "internal_error",
          message: error?.message || String(error),
        }, 500);
      }
    }
    return env.ASSETS.fetch(request);
  },

  async scheduled(controller, env, ctx) {
    ctx.waitUntil(cleanupOrphanedR2(env));
  },
};
