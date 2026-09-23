
const JOB_TTL = 60 * 60 * 24 * 30;
const PAIR_TTL = 60 * 10;
const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

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

async function readJob(env, id) {
  const raw = await env.JOBS.get(`job:${id}`);
  return raw ? JSON.parse(raw) : null;
}

async function writeJob(env, job) {
  job.updated_at = now();
  await env.JOBS.put(`job:${job.id}`, JSON.stringify(job), { expirationTtl: JOB_TTL });
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
    validation: job.validation || null,
    decision: job.decision || null,
  };
}

function jobToken(request, url) {
  return request.headers.get("x-job-token") || url.searchParams.get("token") || "";
}

async function authorizeJob(request, url, job) {
  const token = jobToken(request, url);
  if (!token || !job?.access_hash) return false;
  return (await sha256(token)) === job.access_hash;
}

async function authorizeProcessor(request, env) {
  const auth = request.headers.get("authorization") || "";
  const token = auth.toLowerCase().startsWith("bearer ") ? auth.slice(7).trim() : "";
  if (!token) return false;
  const expected = await env.JOBS.get("processor:token_hash");
  if (!expected) return false;
  return (await sha256(token)) === expected;
}

async function nextQueuedJob(env) {
  const listed = await env.JOBS.list({ prefix: "job:", limit: 100 });
  let candidate = null;
  for (const key of listed.keys) {
    const raw = await env.JOBS.get(key.name);
    if (!raw) continue;
    const job = JSON.parse(raw);
    if (job.status !== "queued") continue;
    if (!candidate || String(job.created_at).localeCompare(String(candidate.created_at)) < 0) {
      candidate = job;
    }
  }
  return candidate;
}

async function serveAsset(request, env) {
  return env.ASSETS.fetch(request);
}

async function handleApi(request, env, ctx, url) {
  const path = url.pathname;
  const method = request.method.toUpperCase();

  if (path === "/api/health" && method === "GET") {
    return json({
      status: "ok",
      service: "MG 4K Cloud Intake",
      version: "cloud-r1",
      worker: "4k-upscaler",
      queue: "r2+kv",
    });
  }

  if (path === "/api/jobs" && method === "POST") {
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
    const id = crypto.randomUUID();
    const accessToken = randomToken();
    const filename = cleanName(file.name || "source-image");
    const sourceKey = `source/${id}/${filename}`;

    await env.FILES.put(sourceKey, file.stream(), {
      httpMetadata: { contentType: type },
      customMetadata: { job_id: id, original_name: filename },
    });

    const job = {
      id,
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
      source_key: sourceKey,
      result_key: null,
      error: null,
      validation: null,
    };

    await writeJob(env, job);

    return json({
      id,
      token: accessToken,
      status: job.status,
      stage: job.stage,
      progress: job.progress,
    }, 201);
  }

  const jobStatusMatch = path.match(/^\/api\/jobs\/([0-9a-f-]+)$/i);
  if (jobStatusMatch && method === "GET") {
    const job = await readJob(env, jobStatusMatch[1]);
    if (!job) return json({ error: "job_not_found" }, 404);
    if (!await authorizeJob(request, url, job)) return json({ error: "forbidden" }, 403);
    return json(publicJob(job));
  }

  const jobDecisionMatch = path.match(/^\/api\/jobs\/([0-9a-f-]+)\/decision$/i);
  if (jobDecisionMatch && method === "POST") {
    const job = await readJob(env, jobDecisionMatch[1]);
    if (!job) return json({ error: "job_not_found" }, 404);
    if (!await authorizeJob(request, url, job)) return json({ error: "forbidden" }, 403);
    if (job.status !== "review") return json({ error: "job_not_waiting_for_review", status: job.status }, 409);
    let body = {};
    try { body = await request.json(); } catch {}
    const action = String(body.action || "").toLowerCase();
    if (!["approve", "reject"].includes(action)) return json({ error: "invalid_decision" }, 400);
    job.decision = action;
    job.status = "decision_pending";
    job.stage = action === "approve" ? "approval_sent" : "rejection_sent";
    job.progress = 95;
    await writeJob(env, job);
    return json({ ok: true, job: publicJob(job) });
  }

  const jobResultMatch = path.match(/^\/api\/jobs\/([0-9a-f-]+)\/result$/i);
  if (jobResultMatch && method === "GET") {
    const job = await readJob(env, jobResultMatch[1]);
    if (!job) return json({ error: "job_not_found" }, 404);
    if (!await authorizeJob(request, url, job)) return json({ error: "forbidden" }, 403);
    if (!job.result_key) return json({ error: "result_not_ready", status: job.status }, 409);

    const object = await env.FILES.get(job.result_key);
    if (!object) return json({ error: "result_missing" }, 404);

    const headers = new Headers();
    object.writeHttpMetadata(headers);
    headers.set("etag", object.httpEtag);
    headers.set("cache-control", "private, no-store");
    headers.set("content-disposition", `attachment; filename="4K_${cleanName(job.filename)}"`);
    return new Response(object.body, { headers });
  }

  if (path === "/api/processor/pair/request" && method === "POST") {
    const code = randomCode();
    const token = randomToken();
    const pair = {
      code,
      token_hash: await sha256(token),
      created_at: now(),
      approved: false,
    };
    await env.JOBS.put(`pair:${code}`, JSON.stringify(pair), { expirationTtl: PAIR_TTL });
    return json({ code, token, expires_in_seconds: PAIR_TTL }, 201);
  }

  if (path === "/api/processor/pair/approve" && method === "POST") {
    let body = {};
    try { body = await request.json(); } catch {}
    const code = String(body.code || "").trim().toUpperCase();
    if (!code) return json({ error: "pair_code_required" }, 400);

    const raw = await env.JOBS.get(`pair:${code}`);
    if (!raw) return json({ error: "pair_code_invalid_or_expired" }, 404);
    const pair = JSON.parse(raw);

    await env.JOBS.put("processor:token_hash", pair.token_hash);
    await env.JOBS.delete(`pair:${code}`);
    return json({ paired: true });
  }

  if (path === "/api/processor/pair/status" && method === "POST") {
    const ok = await authorizeProcessor(request, env);
    return json({ paired: ok }, ok ? 200 : 403);
  }

  if (path === "/api/processor/claim" && method === "POST") {
    if (!await authorizeProcessor(request, env)) {
      const configured = Boolean(await env.JOBS.get("processor:token_hash"));
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
    const object = await env.FILES.get(job.source_key);
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

    const contentType = request.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      let body = {};
      try { body = await request.json(); } catch {}
      job.status = String(body.status || "failed");
      job.stage = String(body.stage || job.status);
      job.progress = Math.max(0, Math.min(100, Number(body.progress ?? (job.status === "failed" ? job.progress : 100))));
      job.error = body.error || null;
      job.validation = body.validation || job.validation || null;
      await writeJob(env, job);
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
    const requestedStatus = String(form.get("status") || "done").toLowerCase();

    const resultName = cleanName(file.name || `4K_${job.filename}`);
    const resultKey = `result/${job.id}/${resultName}`;
    await env.FILES.put(resultKey, file.stream(), {
      httpMetadata: { contentType: String(file.type || "image/jpeg") },
      customMetadata: { job_id: job.id, original_name: resultName },
    });

    job.result_key = resultKey;
    job.status = requestedStatus === "review" ? "review" : "done";
    job.stage = requestedStatus === "review" ? "awaiting_approval" : "final";
    job.progress = requestedStatus === "review" ? 90 : 100;
    job.validation = validation;
    job.error = null;
    if (requestedStatus !== "review") job.decision = "approve";
    await writeJob(env, job);

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
    return serveAsset(request, env);
  },
};
