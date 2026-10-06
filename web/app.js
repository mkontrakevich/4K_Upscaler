"use strict";

const token = document.querySelector('meta[name="mg-token"]').content;
const $ = id => document.getElementById(id);
let latest = null, polling = false, confirmResolve = null, selectingFolder = false;

const statusLabels = {
  READY: "Очередь готова",
  AWAITING_FINAL_APPROVAL: "Результат готов — выберите «Утвердить» или «Отклонить»",
  AWAITING_USER_APPROVAL: "SAFE-результат готов — выберите «Утвердить» или «Отклонить»",
  AWAITING_EXPLICIT_DONOR_GENERATION: "Для текущего кадра требуется новый донор",
  AWAITING_RAW_REVIEW: "Донор сохранён — запустите бесплатную техническую проверку",
  CURRENT_IMAGE_REVIEW_REQUIRED_NEXT_IMAGE_BLOCKED: "Техническая проверка не пройдена — следующий кадр заблокирован",
  CURRENT_IMAGE_FAILED_NEXT_IMAGE_BLOCKED: "Кадр не завершён — можно запросить новую генерацию",
  QUEUE_COMPLETE: "Очередь завершена",
  ALL_IMAGES_COMPLETED: "Очередь завершена",
  APPROVED_NEXT_IMAGE_UNLOCKED: "Кадр утверждён — следующий разблокирован",
  REJECTED_BY_USER_SAME_IMAGE_ACTIVE: "Результат отклонён — текущий кадр остаётся активным"
};

async function api(path, options = {}) {
  options.headers = {"Content-Type": "application/json", "X-MG-Control": token, ...options.headers};
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try { detail = (await response.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return response.json();
}

function toast(message, error = false) {
  const node = $("toast");
  node.textContent = message;
  node.className = `toast show${error ? " error" : ""}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.className = "toast", 4000);
}

function image(nodeId, emptyId, info) {
  const node = $(nodeId), empty = $(emptyId);
  if (info) {
    node.onerror = () => {
      node.classList.remove("visible");
      empty.textContent = "Не удалось загрузить изображение — проверьте файл";
      empty.hidden = false;
    };
    node.onload = () => { node.classList.add("visible"); empty.hidden = true; };
    if (node.dataset.url !== info.url) { node.src = info.url; node.dataset.url = info.url; }
    if (node.complete && node.naturalWidth) { node.classList.add("visible"); empty.hidden = true; }
  } else {
    node.removeAttribute("src"); node.dataset.url = ""; node.classList.remove("visible"); empty.hidden = false;
    empty.textContent = nodeId === "source-image" ? "Выберите папку с изображениями" : "Результат появится после обработки текущего кадра";
  }
}

function renderSceneProfile(profile) {
  const grid = $("scene-profile-grid");
  if (!grid || !profile) return;
  grid.replaceChildren();
  const hard = new Set(profile.hard_lock || []), soft = new Set(profile.soft_lock || []);
  const order = [...(profile.hard_lock || []), ...(profile.soft_lock || [])];
  for (const name of order) {
    const row = document.createElement("div"); row.className = "scene-rule";
    const title = document.createElement("strong"); title.textContent = name;
    const badge = document.createElement("span"); badge.className = hard.has(name) ? "lock-badge hard" : "lock-badge soft";
    badge.textContent = hard.has(name) ? "HARD_LOCK" : soft.has(name) ? "SOFT_LOCK" : "LOCK";
    const desc = document.createElement("small"); desc.textContent = (profile.rules || {})[name] || "";
    row.append(title, badge, desc); grid.append(row);
  }
}

function makeBadge(text, cls = "") {
  const node = document.createElement("span");
  node.className = `reason-badge ${cls}`.trim();
  node.textContent = text;
  return node;
}

function renderFailureDetails(data) {
  const section = $("failure-details"), list = $("failure-list");
  const details = data.failed_checks_detailed || [];
  const show = !!data.technical_hold && details.length > 0;
  section.hidden = !show;
  if (!show) { list.replaceChildren(); return; }
  $("failure-count").textContent = `${details.length} ${details.length === 1 ? "критерий" : "критерия"}`;
  list.replaceChildren();
  details.forEach((item, index) => {
    const card = document.createElement("article");
    card.className = "failure-card";
    const top = document.createElement("div"); top.className = "failure-card-top";
    const badges = document.createElement("div"); badges.className = "failure-badges";
    badges.append(
      makeBadge(item.severity || "FAIL", "fail"),
      makeBadge(item.group || "QUALITY_GATE"),
      makeBadge(`${item.lock_scope || "UNMAPPED"} · ${item.lock_mode || "CHECK"}`, item.lock_mode === "HARD_LOCK" ? "hard" : "")
    );
    const number = document.createElement("span"); number.className = "failure-index"; number.textContent = String(index + 1).padStart(2, "0");
    top.append(badges, number);
    const title = document.createElement("h3"); title.textContent = item.title || item.code;
    const description = document.createElement("p"); description.textContent = item.description || "";
    const impact = document.createElement("p"); impact.className = "failure-impact"; impact.textContent = `Риск: ${item.impact || "требуется ручная оценка"}`;
    const recommendation = document.createElement("p"); recommendation.className = "failure-recommendation"; recommendation.textContent = `Рекомендация: ${item.suggested_action_label || "проверить визуально"}`;
    const footer = document.createElement("div"); footer.className = "failure-card-footer";
    const code = document.createElement("code"); code.textContent = item.code || "—";
    const inspect = document.createElement("button"); inspect.type = "button"; inspect.textContent = item.has_overlay ? "Показать дефект" : "Открыть инспекцию";
    inspect.addEventListener("click", () => openInspection({showDefects: !!item.has_overlay}));
    footer.append(code, inspect);
    card.append(top, title, description, impact, recommendation, footer);
    list.append(card);
  });
}

function render(data) {
  latest = data;
  document.querySelectorAll(".mode-switch button").forEach(button => {
    button.classList.toggle("active", button.dataset.mode === data.mode);
    button.disabled = data.busy;
  });
  const statusBox = document.querySelector(".status-message");
  statusBox.className = `status-message ${data.busy ? "busy" : data.status.includes("FAILED") || data.status.includes("REVIEW_REQUIRED") ? "error" : "ready"}`;
  $("status-text").textContent = data.busy ? `Выполняется: ${data.action}` : (statusLabels[data.status] || data.status.replaceAll("_", " "));
  $("source-folder-path").textContent = data.source_folder || "—";
  $("source-folder-path").title = data.source_folder || "";
  $("select-source-folder").disabled = selectingFolder || !data.can.select_folder;
  $("apply-source-folder").disabled = selectingFolder || !data.can.select_folder;
  if (document.activeElement !== $("source-folder-input")) $("source-folder-input").value = data.source_folder || "";
  $("current-file").textContent = data.current_file || "—";
  $("progress-label").textContent = `${data.approved} / ${data.total} утверждено`;
  $("progress-bar").style.width = `${data.total ? Math.min(100, data.approved / data.total * 100) : 0}%`;
  image("source-image", "source-empty", data.source);
  image("result-image", "result-empty", data.result);
  $("source-size").textContent = data.source ? `${data.source.width} × ${data.source.height}` : "—";
  $("result-size").textContent = data.result ? `${data.result.width} × ${data.result.height}` : "—";
  const hold = !!data.technical_hold && !!data.result;
  $("result-caption").textContent = hold ? "кандидат · AUTO FAIL" : data.can.approve ? "ожидает решения" : data.busy ? "обрабатывается" : data.result ? "доступен для просмотра" : "ещё не создан";
  document.querySelector(".result-card").classList.toggle("technical-fail", hold);
  $("technical-hold").hidden = !hold;
  if (hold) {
    $("technical-decision").textContent = data.technical_decision || "TECHNICAL_REJECTION";
    $("failed-checks").textContent = (data.failed_checks || []).length ? data.failed_checks.join(" · ") : "Причина указана в диагностике";
  }
  renderFailureDetails(data);
  const manual = !!data.can.manual_approve;
  $("action-approve").disabled = !(data.can.approve || manual);
  $("action-approve").classList.toggle("manual-override", manual);
  $("action-approve").querySelector("span:last-child").textContent = manual ? "Утвердить вручную" : "Утвердить и дальше";
  $("action-reject").disabled = !(data.can.reject || data.can.reject_candidate);
  $("action-reject").querySelector("span:last-child").textContent = data.can.reject_candidate ? "Отклонить кандидата" : "Отклонить";
  for (const actionName of ["process", "generate", "pause", "folder"]) $("action-" + actionName).disabled = !data.can[actionName];
  $("action-generate").querySelector("span:last-child").textContent = hold ? "Перегенерировать донор" : "Новая генерация";
  renderSceneProfile(data.scene_profile);
  renderLogs(data.logs);
  if (inspection.open) refreshInspectionAssets();
}

function renderLogs(logs) {
  const panel = $("log-lines"), filter = $("log-filter").value;
  const nearBottom = panel.scrollHeight - panel.scrollTop - panel.clientHeight < 50;
  panel.replaceChildren();
  for (const row of logs) {
    if (filter === "errors" && row.level !== "ERROR") continue;
    const line = document.createElement("div"); line.className = "log-row"; line.dataset.level = row.level;
    for (const [cls, text] of [["log-time", `[${row.time}]`], ["log-level", row.level], ["log-message", row.message]]) {
      const part = document.createElement("span"); part.className = cls; part.textContent = text; line.append(part);
    }
    panel.append(line);
  }
  if (nearBottom) panel.scrollTop = panel.scrollHeight;
}

async function poll() {
  if (polling) return;
  polling = true;
  try { render(await api("/api/status")); }
  catch (error) { toast("Связь с локальным сервером потеряна: " + error.message, true); }
  finally { polling = false; }
}

function ask(title, text, type) {
  const dialog = $("confirm-dialog");
  $("dialog-title").textContent = title; $("dialog-text").textContent = text;
  $("dialog-symbol").textContent = type === "reject" ? "×" : type === "generate" ? "↻" : "✓";
  $("dialog-confirm").className = type === "reject" ? "reject" : type === "generate" ? "generate" : "approve";
  $("dialog-confirm").textContent = type === "generate" ? "Разрешить генерацию" : type === "reject" ? "Отклонить" : "Утвердить";
  dialog.showModal();
  return new Promise(resolve => { confirmResolve = resolve; });
}

$("confirm-dialog").addEventListener("close", () => {
  if (confirmResolve) { confirmResolve($("confirm-dialog").returnValue === "confirm"); confirmResolve = null; }
});

async function action(name) {
  try {
    let paid = false;
    if (name === "approve" && latest && latest.can.manual_approve) {
      if (!await ask(
        "Утвердить отклонённый донор вручную?",
        "Автоматический quality-gate НЕ ПРОЙДЕН.\n\nПричины: " + ((latest.failed_checks || []).join(", ") || "см. диагностику") +
        "\n\nКандидат будет принят как FINAL с MANUAL_OVERRIDE=true, а автоматическая причина отказа сохранится в отчёте.",
        "approve"
      )) return;
      name = "manual_approve";
    } else if (name === "approve" && !await ask("Утвердить результат?", "Этот кадр будет отмечен как завершённый. Очередь перейдёт к следующему изображению.", "approve")) return;
    if (name === "reject" && latest && latest.can.reject_candidate) {
      if (!await ask("Отклонить кандидата?", "Кандидат останется в истории как отклонённый. Текущий кадр останется активным для новой генерации.", "reject")) return;
      name = "reject_candidate";
    } else if (name === "reject" && !await ask("Отклонить результат?", "Кадр останется активным. Следующее изображение не будет обработано.", "reject")) return;
    if (name === "generate") {
      if (!await ask("Новая платная генерация", "Будет выполнен ровно один новый запрос Nano Banana Pro только для текущего изображения. Обычный запуск никогда не делает этот запрос.", "generate")) return;
      paid = true;
    }
    await api(`/api/action/${name}`, {method: "POST", body: JSON.stringify({paid_confirmed: paid})});
    toast("Действие принято"); await poll();
  } catch (error) { toast(error.message, true); }
}

document.querySelectorAll(".mode-switch button").forEach(button => button.addEventListener("click", async () => {
  try { await api("/api/mode", {method: "POST", body: JSON.stringify({mode: button.dataset.mode})}); await poll(); }
  catch (error) { toast(error.message, true); }
}));

async function selectFolder(endpoint, body) {
  if (selectingFolder) return;
  selectingFolder = true;
  $("select-source-folder").disabled = true; $("apply-source-folder").disabled = true;
  try {
    const result = await api(endpoint, {method: "POST", body: JSON.stringify(body)});
    if (result.cancelled) { toast("Выбор папки отменён"); return; }
    toast(`Папка выбрана · найдено файлов: ${result.image_count}`); await poll();
  } catch (error) { toast(error.message, true); }
  finally { selectingFolder = false; await poll(); }
}

$("select-source-folder").addEventListener("click", () => selectFolder("/api/source-folder/select", {}));
$("apply-source-folder").addEventListener("click", () => selectFolder("/api/source-folder/path", {path: $("source-folder-input").value.trim()}));
$("source-folder-input").addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); $("apply-source-folder").click(); } });
for (const name of ["process", "approve", "reject", "generate", "pause", "folder"]) $("action-" + name).addEventListener("click", () => action(name));
$("log-filter").addEventListener("change", () => latest && renderLogs(latest.logs));

// ----- Inspection viewer ----------------------------------------------------
const inspection = {
  open: false,
  mode: "side",
  zoomPreset: "fit",
  customScale: 1,
  centers: {source: {x: .5, y: .5}, result: {x: .5, y: .5}},
  sync: true,
  splitRatio: .5,
  diffMode: "absolute",
  diffOpacity: .72,
  diffThreshold: 24,
  showDefects: false,
  images: {source: null, result: null},
  urls: {source: null, result: null},
  diffCanvas: null,
  diffKey: "",
  dragging: false,
  dragKind: null,
  lastX: 0,
  lastY: 0,
  blinkShowResult: true,
  blinkTimer: null,
  diffTimer: null,
  localizedDefects: [],
  defectLocalizationKey: "",
  defectAnalysisRunning: false,
};

function loadBrowserImage(info) {
  if (!info || !info.url) return Promise.resolve(null);
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("Не удалось загрузить изображение для инспекции"));
    img.src = info.url;
  });
}

async function refreshInspectionAssets(force = false) {
  if (!inspection.open || !latest) return;
  const sourceUrl = latest.source && latest.source.url;
  const resultUrl = latest.result && latest.result.url;
  const jobs = [];
  if (sourceUrl && (force || inspection.urls.source !== sourceUrl)) {
    inspection.urls.source = sourceUrl;
    jobs.push(loadBrowserImage(latest.source).then(img => { inspection.images.source = img; }));
  }
  if (resultUrl && (force || inspection.urls.result !== resultUrl)) {
    inspection.urls.result = resultUrl;
    inspection.localizedDefects = [];
    inspection.defectLocalizationKey = "";
    jobs.push(loadBrowserImage(latest.result).then(img => { inspection.images.result = img; inspection.diffKey = ""; }));
  } else if (!resultUrl) {
    inspection.images.result = null; inspection.urls.result = null; inspection.diffKey = "";
  }
  try { await Promise.all(jobs); }
  catch (error) { toast(error.message, true); }
  updateInspectionUI();
  drawInspection();
}

function resetInspectionView() {
  inspection.zoomPreset = "fit";
  inspection.customScale = 1;
  inspection.centers.source = {x: .5, y: .5};
  inspection.centers.result = {x: .5, y: .5};
  inspection.splitRatio = .5;
  updateInspectionUI();
  drawInspection();
}

function stopBlink() {
  if (inspection.blinkTimer) clearInterval(inspection.blinkTimer);
  inspection.blinkTimer = null;
}

function startBlink() {
  stopBlink();
  if (inspection.mode !== "blink" || !inspection.images.result) return;
  inspection.blinkTimer = setInterval(() => {
    inspection.blinkShowResult = !inspection.blinkShowResult;
    drawInspection();
  }, 600);
}

function setInspectionMode(mode) {
  if (!["side", "split", "diff", "blink"].includes(mode)) return;
  if (!inspection.images.result && mode !== "side") {
    toast("Для этого режима нужен RESULT / candidate", true); return;
  }
  inspection.mode = mode;
  $("diff-controls").hidden = mode !== "diff";
  if (mode === "diff") scheduleDiffBuild(true);
  if (mode === "blink") startBlink(); else stopBlink();
  updateInspectionUI(); drawInspection();
}

function setZoomPreset(value) {
  inspection.zoomPreset = value === "fit" ? "fit" : "numeric";
  if (value !== "fit") inspection.customScale = Math.max(.05, Math.min(8, Number(value)));
  updateInspectionUI(); drawInspection();
}

function paneRects(width, height) {
  if (inspection.mode === "side" && inspection.images.result) {
    const gap = 10, half = (width - gap) / 2;
    return {source: {x: 0, y: 0, w: half, h: height}, result: {x: half + gap, y: 0, w: half, h: height}};
  }
  const full = {x: 0, y: 0, w: width, h: height};
  return {source: full, result: full};
}

function fitScale(rect, width, height) {
  return Math.max(.0001, Math.min(rect.w / width, rect.h / height) * .965);
}

function scaleFor(rect, width, height) {
  return inspection.zoomPreset === "fit" ? fitScale(rect, width, height) : inspection.customScale;
}

function drawNative(ctx, img, rect, center, scale, alpha = 1) {
  if (!img) return null;
  const dw = img.naturalWidth * scale, dh = img.naturalHeight * scale;
  const x = rect.x + rect.w / 2 - center.x * dw;
  const y = rect.y + rect.h / 2 - center.y * dh;
  ctx.save(); ctx.beginPath(); ctx.rect(rect.x, rect.y, rect.w, rect.h); ctx.clip();
  ctx.globalAlpha = alpha; ctx.drawImage(img, x, y, dw, dh); ctx.restore();
  return {x, y, w: dw, h: dh, scale, refW: img.naturalWidth, refH: img.naturalHeight};
}

function referenceDimensions() {
  const img = inspection.images.result || inspection.images.source;
  return img ? {w: img.naturalWidth, h: img.naturalHeight} : {w: 1, h: 1};
}

function drawNormalized(ctx, img, rect, center, scale, refW, refH, alpha = 1) {
  if (!img) return null;
  const dw = refW * scale, dh = refH * scale;
  const x = rect.x + rect.w / 2 - center.x * dw;
  const y = rect.y + rect.h / 2 - center.y * dh;
  ctx.save(); ctx.beginPath(); ctx.rect(rect.x, rect.y, rect.w, rect.h); ctx.clip();
  ctx.globalAlpha = alpha; ctx.drawImage(img, x, y, dw, dh); ctx.restore();
  return {x, y, w: dw, h: dh, scale, refW, refH};
}


function mergeMarkedGrid(marked, cols, rows, cells) {
  const seen = new Set(), groups = [];
  const key = (x, y) => `${x}:${y}`;
  for (const cell of marked) {
    const startKey = key(cell.x, cell.y);
    if (seen.has(startKey)) continue;
    const queue = [cell]; seen.add(startKey);
    let minX = cell.x, minY = cell.y, maxX = cell.x, maxY = cell.y, score = cell.score, count = 0;
    while (queue.length) {
      const cur = queue.shift(); count++;
      minX = Math.min(minX, cur.x); minY = Math.min(minY, cur.y);
      maxX = Math.max(maxX, cur.x); maxY = Math.max(maxY, cur.y);
      score = Math.max(score, cur.score);
      for (let oy = -1; oy <= 1; oy++) for (let ox = -1; ox <= 1; ox++) {
        if (!ox && !oy) continue;
        const nx = cur.x + ox, ny = cur.y + oy;
        if (nx < 0 || ny < 0 || nx >= cols || ny >= rows) continue;
        const nk = key(nx, ny);
        if (seen.has(nk)) continue;
        const next = cells.get(nk);
        if (!next) continue;
        seen.add(nk); queue.push(next);
      }
    }
    groups.push({
      bbox: [
        Math.max(0, minX / cols),
        Math.max(0, minY / rows),
        Math.min(1, (maxX + 1) / cols),
        Math.min(1, (maxY + 1) / rows),
      ],
      score,
      cellCount: count,
    });
  }
  return groups.sort((a, b) => b.score - a.score || b.cellCount - a.cellCount);
}

async function analyzeLocalizedDefects(force = false) {
  const source = inspection.images.source, result = inspection.images.result;
  if (!source || !result || !latest) return [];
  const failed = new Set(latest.failed_checks || []);
  const perimeterLow = failed.has("canvas_integrity.perimeter_low_frequency_continuity");
  const borderSeam = failed.has("canvas_integrity.no_unsupported_long_border_seam");
  if (!perimeterLow && !borderSeam) {
    inspection.localizedDefects = [];
    return [];
  }

  const cacheKey = `${inspection.urls.source}|${inspection.urls.result}|${[...failed].sort().join("|")}`;
  if (!force && inspection.defectLocalizationKey === cacheKey) return inspection.localizedDefects;
  inspection.defectAnalysisRunning = true;
  updateInspectionUI();

  try {
    const maxDim = 800;
    const aspect = result.naturalWidth / Math.max(1, result.naturalHeight);
    let w, h;
    if (aspect >= 1) { w = maxDim; h = Math.max(2, Math.round(maxDim / aspect)); }
    else { h = maxDim; w = Math.max(2, Math.round(maxDim * aspect)); }

    const srcCanvas = document.createElement("canvas"), resCanvas = document.createElement("canvas");
    srcCanvas.width = resCanvas.width = w; srcCanvas.height = resCanvas.height = h;
    const sa = srcCanvas.getContext("2d", {willReadFrequently: true});
    const rb = resCanvas.getContext("2d", {willReadFrequently: true});
    const blurPx = Math.max(2, Math.round(w / 220));
    if ("filter" in sa) sa.filter = `blur(${blurPx}px)`;
    if ("filter" in rb) rb.filter = `blur(${blurPx}px)`;
    sa.drawImage(source, 0, 0, w, h);
    rb.drawImage(result, 0, 0, w, h);
    sa.filter = "none"; rb.filter = "none";

    const a = sa.getImageData(0, 0, w, h).data;
    const b = rb.getImageData(0, 0, w, h).data;
    const gA = grayData(a), gB = grayData(b);
    const eA = borderSeam ? sobel(gA, w, h) : null;
    const eB = borderSeam ? sobel(gB, w, h) : null;

    const cols = 32, rows = Math.max(12, Math.round(cols * h / w));
    const perimeterFrac = .12;
    const cells = new Map(), marked = [];
    const rgbThreshold = 32;
    for (let gy = 0; gy < rows; gy++) for (let gx = 0; gx < cols; gx++) {
      const x0 = Math.floor(gx * w / cols), x1 = Math.max(x0 + 1, Math.floor((gx + 1) * w / cols));
      const y0 = Math.floor(gy * h / rows), y1 = Math.max(y0 + 1, Math.floor((gy + 1) * h / rows));
      const cx = (gx + .5) / cols, cy = (gy + .5) / rows;
      const atPerimeter = cx < perimeterFrac || cx > 1 - perimeterFrac || cy < perimeterFrac || cy > 1 - perimeterFrac;
      if (!atPerimeter) continue;

      let sum = 0, over = 0, edgeSum = 0, n = 0;
      for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) {
        const i = y * w + x, p = i * 4;
        const d = (Math.abs(a[p] - b[p]) + Math.abs(a[p + 1] - b[p + 1]) + Math.abs(a[p + 2] - b[p + 2])) / 3;
        sum += d; if (d > rgbThreshold) over++;
        if (eA) edgeSum += Math.abs(eA[i] - eB[i]);
        n++;
      }
      const mean = sum / Math.max(1, n), fraction = over / Math.max(1, n);
      const edgeMean = eA ? edgeSum / Math.max(1, n) : 0;
      const lowHit = perimeterLow && (fraction >= .16 || (fraction >= .08 && mean >= 22));
      const seamHit = borderSeam && edgeMean >= 24;
      if (lowHit || seamHit) {
        const score = Math.max(mean / 32, fraction * 3, edgeMean / 32);
        const cell = {x: gx, y: gy, score, mean, fraction, edgeMean};
        cells.set(`${gx}:${gy}`, cell); marked.push(cell);
      }
    }

    let regions = mergeMarkedGrid(marked, cols, rows, cells)
      .filter(item => item.cellCount >= 1)
      .slice(0, 8)
      .map((item, index) => ({
        type: "bbox",
        bbox: item.bbox,
        severity: "FAIL",
        precise: true,
        label: `Подозрительная зона ${index + 1}`,
        score: item.score,
        source: "browser_localizer",
      }));

    inspection.localizedDefects = regions;
    inspection.defectLocalizationKey = cacheKey;
    return regions;
  } finally {
    inspection.defectAnalysisRunning = false;
    updateInspectionUI();
  }
}

function focusFirstLocalizedDefect() {
  const first = inspection.localizedDefects && inspection.localizedDefects[0];
  if (!first || !Array.isArray(first.bbox)) return false;
  const [x0, y0, x1, y1] = first.bbox;
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  inspection.zoomPreset = "numeric";
  inspection.customScale = 1.0;
  inspection.centers.source = {x: cx, y: cy};
  inspection.centers.result = {x: cx, y: cy};
  updateInspectionUI();
  drawInspection();
  return true;
}

function drawDefectOverlays(ctx, placement) {
  if (!inspection.showDefects || !placement || !latest) return;
  const localized = inspection.localizedDefects || [];
  const overlays = localized.length ? localized : (latest.defect_overlays || []);
  if (!overlays.length) return;

  ctx.save();
  ctx.font = "600 12px Segoe UI, sans-serif";
  ctx.lineWidth = 3;

  for (let idx = 0; idx < overlays.length; idx++) {
    const overlay = overlays[idx];
    const precise = overlay.precise !== false && overlay.type !== "border";
    ctx.strokeStyle = precise ? "#ff4f68" : "#e4a62a";
    ctx.fillStyle = precise ? "rgba(255,79,104,.18)" : "rgba(228,166,42,.08)";
    ctx.setLineDash(precise ? [] : [8, 6]);

    if (overlay.type === "border") {
      // This is the validator's CHECK AREA, not a localized defect.
      ctx.strokeRect(placement.x + 3, placement.y + 3, placement.w - 6, placement.h - 6);
      const label = "ОБЛАСТЬ ПРОВЕРКИ · точная зона не локализована";
      const tw = Math.min(placement.w - 16, ctx.measureText(label).width + 18);
      ctx.fillStyle = "rgba(28,20,4,.88)";
      ctx.fillRect(placement.x + 8, placement.y + 8, tw, 25);
      ctx.fillStyle = "#ffd16d";
      ctx.fillText(label, placement.x + 16, placement.y + 25);
    } else if (overlay.type === "side_insets") {
      const off = placement.w * Number(overlay.offset || .035);
      ctx.beginPath();
      ctx.moveTo(placement.x + off, placement.y); ctx.lineTo(placement.x + off, placement.y + placement.h);
      ctx.moveTo(placement.x + placement.w - off, placement.y); ctx.lineTo(placement.x + placement.w - off, placement.y + placement.h);
      ctx.stroke();
    } else if (overlay.type === "line" && Array.isArray(overlay.points) && overlay.points.length === 4) {
      const [x0, y0, x1, y1] = overlay.points;
      ctx.beginPath();
      ctx.moveTo(placement.x + placement.w * x0, placement.y + placement.h * y0);
      ctx.lineTo(placement.x + placement.w * x1, placement.y + placement.h * y1);
      ctx.stroke();
    } else if (overlay.type === "bbox" && Array.isArray(overlay.bbox) && overlay.bbox.length === 4) {
      const [x0, y0, x1, y1] = overlay.bbox;
      const x = placement.x + placement.w * x0, y = placement.y + placement.h * y0;
      const w = placement.w * (x1 - x0), h = placement.h * (y1 - y0);
      ctx.fillRect(x, y, w, h);
      ctx.strokeRect(x, y, w, h);
      if (localized.length) {
        ctx.setLineDash([]);
        ctx.fillStyle = "#ff4f68";
        ctx.fillRect(x, y, 26, 22);
        ctx.fillStyle = "#fff";
        ctx.fillText(String(idx + 1), x + 9, y + 16);
      }
    }
  }
  ctx.restore();
}

function canvasMetrics() {
  const stage = $("viewer-stage"), canvas = $("viewer-canvas");
  const rect = stage.getBoundingClientRect();
  const dpr = Math.min(2.5, window.devicePixelRatio || 1);
  const width = Math.max(1, Math.floor(rect.width)), height = Math.max(1, Math.floor(rect.height));
  if (canvas.width !== Math.floor(width * dpr) || canvas.height !== Math.floor(height * dpr)) {
    canvas.width = Math.floor(width * dpr); canvas.height = Math.floor(height * dpr);
    canvas.style.width = width + "px"; canvas.style.height = height + "px";
  }
  const ctx = canvas.getContext("2d", {alpha: false});
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return {ctx, width, height, dpr};
}

function drawBackground(ctx, width, height) {
  ctx.fillStyle = "#02070c"; ctx.fillRect(0, 0, width, height);
}

function drawInspection() {
  if (!inspection.open) return;
  const {ctx, width, height} = canvasMetrics();
  drawBackground(ctx, width, height);
  const source = inspection.images.source, result = inspection.images.result;
  $("viewer-no-result").hidden = !!result || inspection.mode === "side";
  $("viewer-split-labels").classList.toggle("visible", inspection.mode === "split" && !!result);
  if (!source) return;
  const rects = paneRects(width, height);
  if (inspection.mode === "side") {
    const sScale = scaleFor(rects.source, source.naturalWidth, source.naturalHeight);
    const sPlacement = drawNative(ctx, source, rects.source, inspection.centers.source, sScale);
    if (result) {
      const rScale = scaleFor(rects.result, result.naturalWidth, result.naturalHeight);
      const rPlacement = drawNative(ctx, result, rects.result, inspection.centers.result, rScale);
      drawDefectOverlays(ctx, rPlacement);
      ctx.save(); ctx.strokeStyle = "#294054"; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(rects.result.x - 5, 0); ctx.lineTo(rects.result.x - 5, height); ctx.stroke(); ctx.restore();
    } else drawDefectOverlays(ctx, sPlacement);
  } else {
    const ref = referenceDimensions(), rect = rects.source;
    const scale = scaleFor(rect, ref.w, ref.h);
    const center = inspection.centers.source;
    if (inspection.mode === "split") {
      const placement = drawNormalized(ctx, source, rect, center, scale, ref.w, ref.h);
      ctx.save(); ctx.beginPath(); ctx.rect(rect.x + rect.w * inspection.splitRatio, rect.y, rect.w * (1 - inspection.splitRatio), rect.h); ctx.clip();
      drawNormalized(ctx, result, rect, center, scale, ref.w, ref.h); ctx.restore();
      const splitX = rect.x + rect.w * inspection.splitRatio;
      ctx.save(); ctx.strokeStyle = "#16c5f4"; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(splitX, rect.y); ctx.lineTo(splitX, rect.y + rect.h); ctx.stroke();
      ctx.fillStyle = "#16c5f4"; ctx.fillRect(splitX - 2, rect.y + rect.h / 2 - 30, 4, 60); ctx.restore();
      drawDefectOverlays(ctx, placement);
    } else if (inspection.mode === "diff") {
      const placement = drawNormalized(ctx, source, rect, center, scale, ref.w, ref.h, .42);
      if (inspection.diffCanvas) drawNormalized(ctx, inspection.diffCanvas, rect, center, scale, ref.w, ref.h, inspection.diffOpacity);
      drawDefectOverlays(ctx, placement);
    } else if (inspection.mode === "blink") {
      const current = inspection.blinkShowResult ? result : source;
      const placement = drawNormalized(ctx, current, rect, center, scale, ref.w, ref.h);
      drawDefectOverlays(ctx, placement);
    }
  }
  updateInspectionUI();
}

function grayData(data) {
  const out = new Float32Array(data.length / 4);
  for (let i = 0, j = 0; i < data.length; i += 4, j++) out[j] = data[i] * .2126 + data[i + 1] * .7152 + data[i + 2] * .0722;
  return out;
}

function sobel(gray, w, h) {
  const out = new Float32Array(w * h);
  for (let y = 1; y < h - 1; y++) for (let x = 1; x < w - 1; x++) {
    const i = y * w + x;
    const a = gray[i - w - 1], b = gray[i - w], c = gray[i - w + 1];
    const d = gray[i - 1], f = gray[i + 1];
    const g = gray[i + w - 1], hh = gray[i + w], k = gray[i + w + 1];
    const gx = -a + c - 2 * d + 2 * f - g + k;
    const gy = -a - 2 * b - c + g + 2 * hh + k;
    out[i] = Math.min(255, Math.hypot(gx, gy));
  }
  return out;
}

async function buildDiff() {
  const source = inspection.images.source, result = inspection.images.result;
  if (!source || !result) { inspection.diffCanvas = null; return; }
  const key = `${inspection.urls.source}|${inspection.urls.result}|${inspection.diffMode}|${inspection.diffThreshold}`;
  if (inspection.diffKey === key && inspection.diffCanvas) return;
  const refW = result.naturalWidth, refH = result.naturalHeight;
  const factor = Math.min(1, 2048 / Math.max(refW, refH));
  const w = Math.max(2, Math.round(refW * factor)), h = Math.max(2, Math.round(refH * factor));
  const a = document.createElement("canvas"), b = document.createElement("canvas"), out = document.createElement("canvas");
  a.width = b.width = out.width = w; a.height = b.height = out.height = h;
  const ca = a.getContext("2d", {willReadFrequently: true}), cb = b.getContext("2d", {willReadFrequently: true}), co = out.getContext("2d");
  ca.drawImage(source, 0, 0, w, h); cb.drawImage(result, 0, 0, w, h);
  const da = ca.getImageData(0, 0, w, h), db = cb.getImageData(0, 0, w, h), od = co.createImageData(w, h);
  let edgeA = null, edgeB = null;
  if (inspection.diffMode === "edge") { edgeA = sobel(grayData(da.data), w, h); edgeB = sobel(grayData(db.data), w, h); }
  const threshold = inspection.diffThreshold;
  for (let p = 0, px = 0; p < da.data.length; p += 4, px++) {
    let mag;
    if (edgeA) mag = Math.abs(edgeA[px] - edgeB[px]);
    else mag = (Math.abs(da.data[p] - db.data[p]) + Math.abs(da.data[p + 1] - db.data[p + 1]) + Math.abs(da.data[p + 2] - db.data[p + 2])) / 3;
    if (mag < threshold) { od.data[p + 3] = 0; continue; }
    if (inspection.diffMode === "heatmap") {
      const v = Math.min(255, Math.max(0, (mag - threshold) * 4));
      od.data[p] = Math.min(255, 90 + v); od.data[p + 1] = Math.min(255, Math.max(20, v * .72)); od.data[p + 2] = Math.max(0, 150 - v); od.data[p + 3] = Math.min(240, 90 + v);
    } else {
      od.data[p] = 255; od.data[p + 1] = edgeA ? 183 : 82; od.data[p + 2] = edgeA ? 55 : 103; od.data[p + 3] = Math.min(245, 80 + mag * 2.2);
    }
  }
  co.putImageData(od, 0, 0);
  inspection.diffCanvas = out; inspection.diffKey = key;
}

function scheduleDiffBuild(immediate = false) {
  clearTimeout(inspection.diffTimer);
  inspection.diffTimer = setTimeout(async () => { await buildDiff(); drawInspection(); }, immediate ? 0 : 90);
}

function updateInspectionUI() {
  document.querySelectorAll("[data-view-mode]").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.viewMode === inspection.mode);
    btn.disabled = !inspection.images.result && btn.dataset.viewMode !== "side";
  });
  document.querySelectorAll("[data-zoom]").forEach(btn => {
    const value = btn.dataset.zoom;
    const active = inspection.zoomPreset === "fit" ? value === "fit" : Number(value) === inspection.customScale;
    btn.classList.toggle("active", active);
  });
  document.querySelectorAll("[data-diff-mode]").forEach(btn => btn.classList.toggle("active", btn.dataset.diffMode === inspection.diffMode));
  $("viewer-sync").classList.toggle("active", inspection.sync);
  $("viewer-defects").classList.toggle("active", inspection.showDefects);
  const canAnalyzeDefect = !!latest && !!latest.result && (
    (latest.defect_overlays || []).length > 0 || (latest.failed_checks || []).length > 0
  );
  $("viewer-defects").disabled = !canAnalyzeDefect;
  $("viewer-defects").textContent = inspection.defectAnalysisRunning ? "ПОИСК…" : "ДЕФЕКТ";
  $("viewer-zoom-value").textContent = inspection.zoomPreset === "fit" ? "FIT" : `${Math.round(inspection.customScale * 100)}%`;
  $("diff-controls").hidden = inspection.mode !== "diff";
  $("diff-threshold-value").textContent = String(inspection.diffThreshold);
  $("diff-opacity-value").textContent = `${Math.round(inspection.diffOpacity * 100)}%`;
  const sourceInfo = latest && latest.source, resultInfo = latest && latest.result;
  $("viewer-subtitle").textContent = resultInfo ? `${sourceInfo?.width || "?"}×${sourceInfo?.height || "?"} SOURCE · ${resultInfo.width}×${resultInfo.height} RESULT` : sourceInfo ? `${sourceInfo.width}×${sourceInfo.height} SOURCE` : "—";
  const defectNote = inspection.showDefects
    ? (inspection.localizedDefects.length
        ? ` · локализовано зон: ${inspection.localizedDefects.length}`
        : " · показана область проверки; локальный дефект не найден")
    : "";
  $("viewer-footer-left").textContent = (inspection.mode === "split"
    ? "Drag линии — Split · drag кадра — панорама · колесо — масштаб"
    : "Колесо — масштаб · drag — панорама · double click — FIT/100%") + defectNote;
}

async function openInspection(options = {}) {
  if (!latest || !latest.source) return;
  inspection.open = true;
  inspection.showDefects = !!options.showDefects;
  if (!latest.result && inspection.mode !== "side") inspection.mode = "side";
  $("viewer-title").textContent = latest.technical_hold ? "SOURCE ↔ REJECTED CANDIDATE" : "SOURCE ↔ RESULT";
  $("viewer").showModal();
  await refreshInspectionAssets(true);
  resetInspectionView();
  if (inspection.showDefects) {
    const regions = await analyzeLocalizedDefects(true);
    if (regions.length) {
      focusFirstLocalizedDefect();
      toast(`Локализовано подозрительных зон: ${regions.length}. Показана первая на 100%.`);
    } else {
      toast("Точную локальную зону автоматически найти не удалось. Жёлтым показана область, которую проверяет валидатор.");
      drawInspection();
    }
  }
}

function closeInspection() {
  inspection.open = false; stopBlink(); $("viewer").close();
}

$("open-source").addEventListener("click", () => openInspection());
$("open-result").addEventListener("click", () => openInspection());
$("inspect-failure").addEventListener("click", () => openInspection({showDefects: true}));
$("viewer-close").addEventListener("click", closeInspection);
$("viewer").addEventListener("cancel", event => { event.preventDefault(); closeInspection(); });

document.querySelectorAll("[data-view-mode]").forEach(btn => btn.addEventListener("click", () => setInspectionMode(btn.dataset.viewMode)));
document.querySelectorAll("[data-zoom]").forEach(btn => btn.addEventListener("click", () => setZoomPreset(btn.dataset.zoom)));
document.querySelectorAll("[data-diff-mode]").forEach(btn => btn.addEventListener("click", () => { inspection.diffMode = btn.dataset.diffMode; inspection.diffKey = ""; updateInspectionUI(); scheduleDiffBuild(true); }));
$("viewer-sync").addEventListener("click", () => { inspection.sync = !inspection.sync; updateInspectionUI(); });
$("viewer-defects").addEventListener("click", async () => {
  inspection.showDefects = !inspection.showDefects;
  if (inspection.showDefects) {
    const regions = await analyzeLocalizedDefects(true);
    if (regions.length) {
      focusFirstLocalizedDefect();
      toast(`Локализовано подозрительных зон: ${regions.length}. Масштаб 100%.`);
    } else {
      toast("Локальный дефект не найден автоматически; показана только область проверки валидатора.");
      drawInspection();
    }
  } else {
    updateInspectionUI(); drawInspection();
  }
});
$("viewer-reset").addEventListener("click", resetInspectionView);
$("diff-threshold").addEventListener("input", event => { inspection.diffThreshold = Number(event.target.value); inspection.diffKey = ""; updateInspectionUI(); scheduleDiffBuild(); });
$("diff-opacity").addEventListener("input", event => { inspection.diffOpacity = Number(event.target.value) / 100; updateInspectionUI(); drawInspection(); });

const viewerCanvas = $("viewer-canvas");
viewerCanvas.addEventListener("pointerdown", event => {
  if (!inspection.open) return;
  const rect = viewerCanvas.getBoundingClientRect();
  const x = event.clientX - rect.left, y = event.clientY - rect.top;
  if (inspection.mode === "split" && Math.abs(x - rect.width * inspection.splitRatio) < 22) inspection.dragKind = "split";
  else if (inspection.mode === "side" && inspection.images.result) inspection.dragKind = x < rect.width / 2 ? "source" : "result";
  else inspection.dragKind = "source";
  inspection.dragging = true; inspection.lastX = x; inspection.lastY = y; viewerCanvas.setPointerCapture(event.pointerId);
});
viewerCanvas.addEventListener("pointermove", event => {
  if (!inspection.dragging) return;
  const rect = viewerCanvas.getBoundingClientRect();
  const x = event.clientX - rect.left, y = event.clientY - rect.top;
  if (inspection.dragKind === "split") {
    inspection.splitRatio = Math.max(.02, Math.min(.98, x / rect.width));
  } else {
    const dx = x - inspection.lastX, dy = y - inspection.lastY;
    let refImg = inspection.dragKind === "result" ? inspection.images.result : inspection.images.source;
    let paneW = rect.width, paneH = rect.height;
    if (inspection.mode === "side" && inspection.images.result) paneW = (rect.width - 10) / 2;
    const ref = inspection.mode === "side" ? {w: refImg.naturalWidth, h: refImg.naturalHeight} : referenceDimensions();
    const pane = {x: 0, y: 0, w: paneW, h: paneH};
    const scale = scaleFor(pane, ref.w, ref.h);
    const nx = -dx / Math.max(1, ref.w * scale), ny = -dy / Math.max(1, ref.h * scale);
    const apply = key => {
      inspection.centers[key].x = Math.max(0, Math.min(1, inspection.centers[key].x + nx));
      inspection.centers[key].y = Math.max(0, Math.min(1, inspection.centers[key].y + ny));
    };
    if (inspection.sync || inspection.mode !== "side") { apply("source"); apply("result"); }
    else apply(inspection.dragKind);
  }
  inspection.lastX = x; inspection.lastY = y; drawInspection();
});
viewerCanvas.addEventListener("pointerup", event => { inspection.dragging = false; inspection.dragKind = null; try { viewerCanvas.releasePointerCapture(event.pointerId); } catch {} });
viewerCanvas.addEventListener("pointercancel", () => { inspection.dragging = false; inspection.dragKind = null; });
viewerCanvas.addEventListener("wheel", event => {
  event.preventDefault();
  const rect = viewerCanvas.getBoundingClientRect();
  const ref = referenceDimensions();
  const current = inspection.zoomPreset === "fit" ? fitScale({x:0,y:0,w:inspection.mode === "side" && inspection.images.result ? (rect.width - 10)/2 : rect.width,h:rect.height}, ref.w, ref.h) : inspection.customScale;
  inspection.zoomPreset = "numeric";
  inspection.customScale = Math.max(.05, Math.min(8, current * Math.exp(-event.deltaY * .0012)));
  updateInspectionUI(); drawInspection();
}, {passive: false});
viewerCanvas.addEventListener("dblclick", () => setZoomPreset(inspection.zoomPreset === "fit" ? "1" : "fit"));

window.addEventListener("resize", () => { if (inspection.open) drawInspection(); });
window.addEventListener("keydown", event => {
  if (!inspection.open || ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName)) return;
  const key = event.key.toLowerCase();
  if (key === "escape") { event.preventDefault(); closeInspection(); return; }
  if (key === "1") setInspectionMode("side");
  else if (key === "2") setInspectionMode("split");
  else if (key === "3") setInspectionMode("diff");
  else if (key === "4") setInspectionMode("blink");
  else if (key === "f") setZoomPreset("fit");
  else if (key === "z") setZoomPreset("1");
  else if (key === "x") setZoomPreset("2");
  else if (key === "d") { if (!$("viewer-defects").disabled) $("viewer-defects").click(); }
  else if (key === "s") $("viewer-sync").click();
  else if (key === "0") resetInspectionView();
  else if (event.code === "Space" && inspection.mode === "blink") {
    event.preventDefault();
    if (inspection.blinkTimer) stopBlink(); else startBlink();
  }
});

poll(); setInterval(poll, 1000);
