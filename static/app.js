/**
 * RIBBON live audit interface.
 *
 * Sign in, upload PDFs, follow the SSE stream, review discrepancies, and compare
 * resolutions in the metrics view. Visible text is in Portuguese; the API contract
 * (routes, JSON keys, and status values) stays in English.
 */

const $ = (id) => document.getElementById(id);

const auditForm = $("auditForm");
const startButton = $("start-button");
const notice = $("notice");
const panel = $("panel");
const tableBody = $("body-table");
const viewer = $("viewer");
const progressBar = $("progressBar");
const statusText = $("statusText");

// Keep annotated page images available when revisiting report rows.
const pages = new Map();
let counters = { processed: 0, matched: 0, mismatched: 0, total: null };
let eventSource = null;
let currentJobId = null;
let selectedPage = null;

let timerInterval = null;
let elapsedSeconds = 0;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character]
  ));
}

/** Format a number with Brazilian decimal separators, e.g. 1,5. */
function decimal(value, digits = 1) {
  return value.toLocaleString("pt-BR", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

// API error messages are in English; show their Portuguese equivalents.
const API_MESSAGES = {
  "Invalid username or password.": "Usuário ou senha inválidos.",
  "Usernames have 3 to 32 letters, digits, dots, hyphens, or underscores.":
    "O usuário deve ter de 3 a 32 letras, números, pontos, hífens ou sublinhados.",
  "Usernames starting with 'sim-' are reserved for the simulation.":
    "Usuários que começam com 'sim-' são reservados para a simulação.",
  "Passwords need at least 8 characters.": "A senha precisa ter pelo menos 8 caracteres.",
  "This username is already taken.": "Este nome de usuário já está em uso.",
  "Sign in to continue.": "Entre para continuar.",
  "Audit not found.": "Auditoria não encontrada.",
  "No pages processed yet.": "Nenhuma página foi processada ainda.",
  "Page not processed yet.": "Esta página ainda não foi processada.",
  "Page not found.": "Página não encontrada.",
  "A simulation is already running.": "Já existe uma simulação em andamento.",
};

function errorMessage(body, fallback) {
  if (Array.isArray(body.detail)) return "Valores inválidos. Confira os campos e tente novamente.";
  const detail = body.detail;
  if (!detail) return fallback;
  if (API_MESSAGES[detail]) return API_MESSAGES[detail];
  const notPdf = /^'(.*)' is not a PDF\.$/.exec(detail);
  if (notPdf) return `"${notPdf[1]}" não é um PDF.`;
  return detail;
}

/** Fetch from the API, returning to the sign-in screen when the session has ended. */
async function api(url, options) {
  const response = await fetch(url, options);
  if (response.status === 401) showLogin();
  return response;
}

function jsonRequest(method, body) {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

/* =========================================================
   Sign-in
   ========================================================= */

async function initSession() {
  try {
    const response = await fetch("/api/v1/auth/me");
    if (response.ok) {
      const { username } = await response.json();
      showApp(username);
      return;
    }
  } catch {
    // fall through to the sign-in screen
  }
  showLogin();
}

function showLogin() {
  $("login-card").hidden = false;
  $("app-content").hidden = true;
  $("user-box").hidden = true;
}

function showApp(username) {
  $("user-name").textContent = username;
  $("login-card").hidden = true;
  $("app-content").hidden = false;
  $("user-box").hidden = false;
  loadHistory();
}

async function submitCredentials(mode) {
  const notice = $("login-notice");
  notice.textContent = "";
  const credentials = { username: $("login-username").value.trim(), password: $("login-password").value };
  try {
    const response = await fetch(`/api/v1/auth/${mode}`, jsonRequest("POST", credentials));
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(errorMessage(body, `Não foi possível entrar (HTTP ${response.status}).`));
    $("login-password").value = "";
    showApp(body.username);
  } catch (error) {
    notice.textContent = error.message;
  }
}

$("loginForm").addEventListener("submit", (event) => {
  event.preventDefault();
  submitCredentials("login");
});
$("register-button").addEventListener("click", () => submitCredentials("register"));

$("logout-button").addEventListener("click", async () => {
  if (eventSource) finishAudit(currentJobId, false);
  await fetch("/api/v1/auth/logout", { method: "POST" }).catch(() => {});
  panel.hidden = true;
  showLogin();
});

/* =========================================================
   Views
   ========================================================= */

const VIEWS = ["audit", "metrics", "simulation"];

function showView(name) {
  for (const view of VIEWS) {
    $(`view-${view}`).hidden = view !== name;
    $(`tab-${view}`).classList.toggle("active", view === name);
  }
  if (name === "metrics") loadMetrics();
}

for (const view of VIEWS) {
  $(`tab-${view}`).addEventListener("click", () => showView(view));
}

/* =========================================================
   Resolution
   ========================================================= */

const RESOLUTIONS = {
  "200": "≈ 3,9 megapixels por página A4. O mais rápido; dígitos pequenos ou apagados podem perder detalhe.",
  "300": "≈ 8,7 megapixels por página A4, 2,3× o trabalho de 200 DPI. Resolução recomendada pelo Tesseract para texto impresso.",
  "500": "≈ 24 megapixels por página A4, 6× o trabalho de 200 DPI. O mais lento; só ajuda se a digitalização tiver esse nível de detalhe.",
};

function updateDpiHelp() {
  $("dpi-help").textContent = RESOLUTIONS[$("dpi-select").value] || "";
}

$("dpi-select").addEventListener("change", updateDpiHelp);
updateDpiHelp();

/* =========================================================
   Live audit
   ========================================================= */

auditForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  notice.textContent = "";

  const data = new FormData(auditForm);
  const dpi = data.get("dpi") || 500;
  data.delete("dpi");

  startButton.disabled = true;
  startButton.textContent = "Enviando documentos…";

  try {
    const response = await api(`/api/v1/audits?dpi=${dpi}`, { method: "POST", body: data });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(errorMessage(error, `Não foi possível iniciar (HTTP ${response.status}).`));
    }
    const { job_id } = await response.json();
    resetPanel();
    followAudit(job_id);
  } catch (error) {
    notice.textContent = error.message;
    startButton.disabled = false;
    startButton.textContent = "Iniciar auditoria";
  }
});

function resetPanel() {
  clearInterval(timerInterval);
  elapsedSeconds = 0;
  $("ind-time").textContent = "00:00";
  $("ind-page-time").textContent = "—";
  pages.clear();
  selectedPage = null;
  tableBody.innerHTML = "";
  counters = { processed: 0, matched: 0, mismatched: 0, total: null };
  updateIndicators();
  progressBar.style.width = "0%";
  viewer.innerHTML = '<p class="empty">Aguardando a primeira página…</p>';
  $("comparison").hidden = true;
  $("evidence").hidden = true;
  $("review-box").hidden = true;
  $("tag-strategy").hidden = true;
  $("caption-viewer").hidden = true;
  $("link-csv").hidden = true;
  renderMissing([]);

  panel.hidden = false;
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function followAudit(jobId) {
  currentJobId = jobId;
  statusText.textContent = "Convertendo o PDF e lendo a lista de consulta…";
  startButton.textContent = "Auditoria em andamento…";

  timerInterval = setInterval(() => {
    elapsedSeconds++;
    $("ind-time").textContent = formatClock(elapsedSeconds);
    if (counters.processed) {
      $("ind-page-time").textContent = decimal(elapsedSeconds / counters.processed);
    }
  }, 1000);

  eventSource = new EventSource(`/api/v1/audits/${jobId}/events`);

  eventSource.onmessage = (message) => {
    const event = JSON.parse(message.data);

    if (event.type === "start") {
      counters.total = event.total_pages;
      statusText.textContent = `${event.total_pages} guias encontradas · ${event.reference_count} lançamentos na consulta · ${event.dpi} DPI`;
      $("active-resolution").textContent = `${event.dpi} DPI`;
      updateIndicators();
    } else if (event.type === "page") {
      registerPage(event);
    } else if (event.type === "error") {
      statusText.textContent = "";
      notice.textContent = `Erro durante a auditoria: ${event.message}`;
      finishAudit(jobId, false);
    } else if (event.type === "end") {
      const seconds = event.elapsed_seconds;
      statusText.textContent = `Auditoria concluída — ${counters.processed} guias conferidas` +
        (seconds != null ? ` em ${decimal(seconds)} s.` : ".");
      if (seconds != null) {
        // Prefer the server's measurement to the browser's one-second ticks.
        $("ind-time").textContent = formatClock(seconds);
        if (counters.processed) $("ind-page-time").textContent = decimal(seconds / counters.processed);
      }
      renderMissing(event.missing || []);
      finishAudit(jobId, true);
    }
  };

  eventSource.onerror = () => {
    // Close the session explicitly when the event stream has closed.
    if (eventSource && eventSource.readyState === EventSource.CLOSED) {
      finishAudit(jobId, counters.processed > 0);
    }
  };
}

function finishAudit(jobId, hasResults) {
  clearInterval(timerInterval);
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  startButton.disabled = false;
  startButton.textContent = "Iniciar nova auditoria";
  if (hasResults) {
    showCsvLink(jobId);
  }
  loadHistory();
}

function showCsvLink(jobId) {
  const link = $("link-csv");
  link.href = `/api/v1/audits/${jobId}/report.csv`;
  link.hidden = false;
}

function renderMissing(entries) {
  $("missing-block").hidden = entries.length === 0;
  $("missing-list").innerHTML = entries.map((entry) =>
    `<li>Linha ${escapeHtml(entry.line)}: código <strong>${escapeHtml(entry.code)}</strong>, valor ${escapeHtml(entry.amount)}</li>`
  ).join("");
}

/* =========================================================
   History — stored audit results
   ========================================================= */

const STATUS_LABELS = {
  completed: ["Concluída", "ok"],
  processing: ["Em andamento", ""],
  abandoned: ["Interrompida", ""],
  error: ["Falhou", "error"],
};

function formatTimestamp(iso) {
  const data = new Date(iso);
  if (isNaN(data)) return iso;
  const twoDigits = (n) => String(n).padStart(2, "0");
  return `${twoDigits(data.getDate())}/${twoDigits(data.getMonth() + 1)}/${data.getFullYear()}` +
    ` ${twoDigits(data.getHours())}:${twoDigits(data.getMinutes())}`;
}

function formatClock(seconds) {
  const rounded = Math.round(seconds);
  return `${String(Math.floor(rounded / 60)).padStart(2, "0")}:${String(rounded % 60).padStart(2, "0")}`;
}

function formatDuration(seconds) {
  if (seconds == null) return "—";
  if (seconds < 60) return `${decimal(seconds)} s`;
  return `${Math.floor(seconds / 60)} min ${String(Math.round(seconds % 60)).padStart(2, "0")} s`;
}

async function loadHistory() {
  let audits;
  try {
    const response = await api("/api/v1/audits");
    if (!response.ok) return;
    audits = await response.json();
  } catch {
    return; // history failure must not block live verification
  }

  const body = $("body-history");
  body.innerHTML = "";
  $("history-card").hidden = audits.length === 0;

  for (const audit of audits) {
    const evaluated = audit.matched + audit.mismatched;
    const rate = percent(audit.matched, evaluated);
    const [label, className] = STATUS_LABELS[audit.status] || [audit.status, ""];

    const tr = document.createElement("tr");
    tr.className = "row-history";
    tr.innerHTML = `
      <td>${formatTimestamp(audit.created_at)}</td>
      <td><span class="badge ${className}">${escapeHtml(label)}</span></td>
      <td>${audit.dpi ?? "—"}</td>
      <td>${audit.processed_pages}${audit.total_pages ? ` / ${audit.total_pages}` : ""}</td>
      <td>${audit.matched}</td>
      <td class="${audit.mismatched ? "discrepancy" : ""}">${audit.mismatched}</td>
      <td>${rate}</td>
      <td>${formatDuration(audit.duration_seconds)}</td>
    `;
    tr.addEventListener("click", () => openAudit(audit));
    body.appendChild(tr);
  }
}

async function openAudit(audit) {
  if (eventSource) return; // preserve the active audit session

  let rows;
  let missing = [];
  try {
    const response = await api(`/api/v1/audits/${audit.job_id}/pages`);
    if (!response.ok) throw new Error("Não foi possível carregar esta auditoria.");
    rows = await response.json();
    const missingResponse = await api(`/api/v1/audits/${audit.job_id}/missing`);
    if (missingResponse.ok) missing = await missingResponse.json();
  } catch (error) {
    notice.textContent = error.message;
    return;
  }

  resetPanel();
  currentJobId = audit.job_id;
  counters.total = audit.total_pages;
  $("active-resolution").textContent = audit.dpi ? `${audit.dpi} DPI` : "—";

  for (const row of rows) {
    // Historical rows have no images; previews are generated only during a live audit.
    const event = { page: row["Page"], row };
    const mismatch = row["Overall Status"] === "ERROR";

    counters.processed += 1;
    if (row["Overall Status"] === "OK") counters.matched += 1;
    if (mismatch) counters.mismatched += 1;

    pages.set(event.page, event);
    addRow(event, mismatch);
  }

  updateIndicators();
  renderMissing(missing);
  progressBar.style.width = "100%";
  statusText.textContent = `Auditoria de ${formatTimestamp(audit.created_at)} — ` +
    `${counters.processed} guias, reaberta do histórico.`;
  if (audit.duration_seconds != null) {
    $("ind-time").textContent = formatClock(audit.duration_seconds);
    if (counters.processed) {
      $("ind-page-time").textContent = decimal(audit.duration_seconds / counters.processed);
    }
  }

  if (rows.length) {
    showCsvLink(audit.job_id);
    showPage(rows[0]["Page"]);
  }

  // Replace the generic preview message after displaying the first historical row.
  viewer.innerHTML = '<p class="empty">As imagens com os destaques do OCR não são guardadas:<br>' +
    'elas existem apenas durante a conferência ao vivo.</p>';
  $("caption-viewer").hidden = true;
}

$("refresh-history-button").addEventListener("click", loadHistory);

function registerPage(event) {
  const row = event.row;
  const mismatch = row["Overall Status"] === "ERROR";

  counters.processed += 1;
  if (row["Overall Status"] === "OK") counters.matched += 1;
  if (mismatch) counters.mismatched += 1;
  updateIndicators();

  if (counters.total) {
    progressBar.style.width = `${(counters.processed / counters.total) * 100}%`;
    statusText.textContent = `Conferindo… página ${event.page} de ${counters.total}`;
  }

  pages.set(event.page, event);
  addRow(event, mismatch);
  showPage(event.page);
}

function rowMarkup(event) {
  const row = event.row;
  const codeMismatch = row["Code Status"] !== "OK";
  const amountMismatch = row["Amount Status"] !== "OK";
  return `
    <td>${event.page}</td>
    <td>${escapeHtml(row["Code (Reference PDF)"])}</td>
    <td class="${codeMismatch ? "discrepancy" : ""}">${escapeHtml(row["Code (OCR Slips)"])}</td>
    <td>${escapeHtml(row["Amount (Reference PDF)"])}</td>
    <td class="${amountMismatch ? "discrepancy" : ""}">${escapeHtml(row["Amount (OCR Slips)"])}</td>
    <td>${badge(row["Overall Status"])}${categoryBadge(row["Category"])}${reviewBadge(row["Review"])}</td>
  `;
}

function addRow(event, mismatch) {
  const tr = document.createElement("tr");
  tr.dataset.page = event.page;
  tr.dataset.mismatch = mismatch ? "1" : "0";
  if (mismatch) tr.classList.add("mismatch");
  if ($("mismatch-filter").checked && !mismatch) tr.hidden = true;
  tr.innerHTML = rowMarkup(event);
  tr.addEventListener("click", () => showPage(event.page));
  event.element = tr;
  tableBody.appendChild(tr);
}

// Report statuses from the API, with their badge text and style.
const STATUS_BADGES = {
  "OK": ["Conforme", "ok"],
  "ERROR": ["Divergente", "error"],
  "MISMATCH": ["Divergente", "error"],
  "END OF LIST": ["Fim da lista", ""],
};

function badge(status) {
  const [text, className] = STATUS_BADGES[status] || [status, ""];
  return `<span class="badge ${className}">${escapeHtml(text)}</span>`;
}

// Discrepancy categories and suggested operator actions.
const CATEGORIES = {
  "DUPLICATE": ["Duplicada", "severe", "Outra página já foi pareada com esta matrícula. Verifique se a guia está repetida, ou se o código foi lido como outro deste lote."],
  "LIKELY MISREAD": ["Provável erro de leitura", "", "O código não está na lista de consulta, mas o valor confere com a guia esperada nesta posição. O OCR provavelmente leu o código errado — confirme na imagem."],
  "UNREAD": ["Não lido", "", "O OCR não extraiu este dado. Confira a qualidade da digitalização ou tente outro DPI."],
  "REVIEW": ["Verificar", "", "A leitura difere do lançamento da consulta. Compare o campo destacado com a guia original."],
  // Reports created before slips were matched by code.
  "OTHER REGISTRATION": ["Outro cadastro", "severe", "O código lido pertence a outra guia deste lote — pode ser guia trocada."],
};

function categoryBadge(category) {
  const info = CATEGORIES[category];
  if (!info) return "";
  const [text, className, explanation] = info;
  return `<span class="category ${className}" title="${escapeHtml(explanation)}">${text}</span>`;
}

const REVIEWS = {
  DOCUMENT: ["Divergência real", "document"],
  OCR: ["Erro do OCR", "ocr"],
};

function reviewBadge(verdict) {
  const info = REVIEWS[verdict];
  return info ? `<span class="review-badge ${info[1]}">${info[0]}</span>` : "";
}

// Strategy names come from the engine in English.
const STRATEGY_NAMES = {
  "1. Default": "1. Padrão",
  "2. Dark": "2. Escuro",
  "3. Sharpness": "3. Nitidez",
  "4. Original": "4. Original",
  "5. Zoom 2x": "5. Zoom 2x",
  "6. High Threshold": "6. Limiar alto",
  "7. Contrast": "7. Contraste",
  "8. Thicken": "8. Engrossar",
};

function evidenceText(event) {
  const row = event.row;
  const parts = [];
  const strategies = row["Strategies Run"];
  for (const [label, field] of [["Código", "Code"], ["Valor", "Amount"]]) {
    const confidence = row[`${field} Confidence`];
    const votes = row[`${field} Votes`];
    const details = [];
    if (confidence != null) details.push(`confiança ${confidence}%`);
    if (votes != null && strategies) details.push(`${votes}/${strategies} estratégias concordam`);
    if (details.length) parts.push(`${label}: ${details.join(", ")}`);
  }
  if (row["OCR ms"] != null) {
    parts.push(`Tempo da página ${decimal((row["Render ms"] + row["OCR ms"]) / 1000)} s`);
  }
  if (row["Matched By"] === "POSITION" && row["Overall Status"] !== "END OF LIST") {
    parts.push(`Pareada pela posição com a linha ${row["Reference Line"]} da consulta`);
  } else if (row["Reference Line"] != null && row["Reference Line"] !== event.page) {
    parts.push(`Pareada com a linha ${row["Reference Line"]} da consulta`);
  }
  return parts.join(" · ");
}

function showPage(number) {
  const event = pages.get(number);
  if (!event) return;
  selectedPage = number;
  const row = event.row;

  document.querySelectorAll("#body-table tr").forEach((tr) => {
    tr.classList.toggle("selected", Number(tr.dataset.page) === number);
  });

  $("comparison").hidden = false;
  $("cmp-code-expected").textContent = row["Code (Reference PDF)"];
  $("cmp-code-read").textContent = row["Code (OCR Slips)"];
  $("cmp-code-badge").outerHTML = badge(row["Code Status"]).replace('class="badge', 'id="cmp-code-badge" class="badge');
  $("cmp-amount-expected").textContent = row["Amount (Reference PDF)"];
  $("cmp-amount-read").textContent = row["Amount (OCR Slips)"];
  $("cmp-amount-badge").outerHTML = badge(row["Amount Status"]).replace('class="badge', 'id="cmp-amount-badge" class="badge');

  const evidence = evidenceText(event);
  $("evidence").textContent = evidence;
  $("evidence").hidden = !evidence;

  const explanation = $("explanation-category");
  const categoryInfo = CATEGORIES[row["Category"]];
  if (categoryInfo) {
    const [text, className, detail] = categoryInfo;
    explanation.className = `explanation-category ${className}`;
    explanation.innerHTML = `<strong>${text}:</strong> ${escapeHtml(detail)}`;
    explanation.hidden = false;
  } else {
    explanation.hidden = true;
  }

  showReview(row);

  const tag = $("tag-strategy");
  if (event.strategy) {
    tag.textContent = `Estratégia vencedora: ${STRATEGY_NAMES[event.strategy] || event.strategy}`;
    tag.hidden = false;
  } else {
    tag.hidden = true;
  }

  if (event.image) {
    viewer.innerHTML = `<img src="${event.image}" alt="Guia da página ${number} com os campos extraídos destacados">`;
  } else {
    viewer.innerHTML = '<p class="empty">Imagem indisponível para esta página.</p>';
  }

  const caption = $("caption-viewer");
  const notLocated = [];
  if (!event.code_found) notLocated.push("código");
  if (!event.amount_found) notLocated.push("valor");
  if (event.image && notLocated.length) {
    caption.textContent = `Não foi possível localizar: ${notLocated.join(" e ")} — o OCR não encontrou este campo na página.`;
    caption.hidden = false;
  } else {
    caption.hidden = true;
  }
}

/* =========================================================
   Review — separate real discrepancies from OCR errors
   ========================================================= */

function showReview(row) {
  const flagged = row["Overall Status"] === "ERROR";
  $("review-box").hidden = !flagged;
  if (!flagged) return;
  $("review-document").classList.toggle("active", row["Review"] === "DOCUMENT");
  $("review-ocr").classList.toggle("active", row["Review"] === "OCR");
  $("review-status").textContent = row["Review"] ? "Salvo." : "Ainda não revisada.";
}

async function setReview(verdict) {
  const event = pages.get(selectedPage);
  if (!event || !currentJobId) return;
  $("review-status").textContent = "Salvando…";
  try {
    const response = await api(
      `/api/v1/audits/${currentJobId}/pages/${selectedPage}/review`, jsonRequest("PUT", { verdict })
    );
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(errorMessage(body, "Não foi possível salvar a revisão."));
    event.row = body;
    if (event.element) event.element.innerHTML = rowMarkup(event);
    showReview(event.row);
  } catch (error) {
    $("review-status").textContent = error.message;
  }
}

$("review-document").addEventListener("click", () => setReview("DOCUMENT"));
$("review-ocr").addEventListener("click", () => setReview("OCR"));
$("review-clear").addEventListener("click", () => setReview(null));

function updateIndicators() {
  $("ind-processed").textContent = counters.total
    ? `${counters.processed}/${counters.total}`
    : counters.processed;
  $("ind-matched").textContent = counters.matched;
  $("ind-mismatched").textContent = counters.mismatched;
  $("ind-match-rate").textContent = percent(counters.matched, counters.matched + counters.mismatched);
}

$("mismatch-filter").addEventListener("change", (event) => {
  const onlyMismatches = event.target.checked;
  document.querySelectorAll("#body-table tr").forEach((tr) => {
    tr.hidden = onlyMismatches && tr.dataset.mismatch !== "1";
  });
});

/* =========================================================
   Metrics by resolution
   ========================================================= */

function percent(part, total) {
  return total ? `${decimal((part / total) * 100)}%` : "—";
}

function seconds(milliseconds) {
  return milliseconds == null ? "—" : `${decimal(milliseconds / 1000, 2)} s`;
}

function pageMilliseconds(item) {
  return item.avg_render_ms == null || item.avg_ocr_ms == null ? null : item.avg_render_ms + item.avg_ocr_ms;
}

function renderTimeChart(metrics) {
  const timed = metrics.filter((item) => pageMilliseconds(item) != null);
  const longest = Math.max(...timed.map(pageMilliseconds), 1);
  $("chart-time").innerHTML = timed.map((item) => {
    const value = pageMilliseconds(item);
    return `
      <div class="bar-row">
        <span class="bar-label">${item.dpi} DPI</span>
        <div class="bar-track"><div class="bar-fill time" style="width: ${(value / longest) * 100}%"></div></div>
        <span class="bar-value">${seconds(value)}</span>
      </div>`;
  }).join("") || '<p class="empty">Nenhuma página com tempo medido ainda.</p>';
}

function renderFlaggedChart(metrics) {
  const highest = Math.max(...metrics.map((item) => (item.pages ? item.flagged / item.pages : 0)), 0.0001);
  $("chart-flagged").innerHTML = metrics.map((item) => {
    const share = (count) => (item.pages ? (count / item.pages / highest) * 100 : 0);
    return `
      <div class="bar-row">
        <span class="bar-label">${item.dpi} DPI</span>
        <div class="bar-track">
          <div class="bar-fill document" style="width: ${share(item.document_discrepancies)}%"></div>
          <div class="bar-fill ocr" style="width: ${share(item.ocr_errors)}%"></div>
          <div class="bar-fill pending" style="width: ${share(item.pending_review)}%"></div>
        </div>
        <span class="bar-value">${percent(item.flagged, item.pages)}</span>
      </div>`;
  }).join("");
}

async function loadMetrics() {
  let metrics;
  try {
    const response = await api("/api/v1/metrics");
    if (!response.ok) return;
    metrics = await response.json();
  } catch {
    return;
  }

  const empty = metrics.length === 0;
  $("metrics-empty").hidden = !empty;
  $("metrics-charts").hidden = empty;
  $("metrics-table-wrapper").hidden = empty;
  if (empty) return;

  renderTimeChart(metrics);
  renderFlaggedChart(metrics);

  $("body-metrics").innerHTML = metrics.map((item) => {
    const confidence = [item.avg_code_confidence, item.avg_amount_confidence]
      .map((value) => (value == null ? "—" : `${Math.round(value)}%`)).join(" / ");
    const errorRate = percent(item.ocr_errors, item.pages) + (item.pending_review ? " (limite inferior)" : "");
    return `
      <tr>
        <td><strong>${item.dpi ?? "—"}</strong></td>
        <td>${item.audits}</td>
        <td>${item.pages}</td>
        <td>${seconds(pageMilliseconds(item))}</td>
        <td>${item.wall_seconds_per_page == null ? "—" : `${decimal(item.wall_seconds_per_page, 2)} s`}</td>
        <td>${percent(item.consensus_pages, item.pages)}</td>
        <td>${item.flagged}</td>
        <td>${item.document_discrepancies}</td>
        <td class="${item.ocr_errors ? "discrepancy" : ""}">${item.ocr_errors}</td>
        <td>${item.pending_review}</td>
        <td>${errorRate}</td>
        <td>${confidence}</td>
      </tr>`;
  }).join("");
}

$("refresh-metrics-button").addEventListener("click", loadMetrics);

/* =========================================================
   Multi-user simulation — TEMPORARY demo (see api/simulation.py)
   ========================================================= */

let simulationTimer = null;

const SIMULATION_STATUS = {
  waiting: ["Aguardando", ""],
  "signing in": ["Entrando", ""],
  uploading: ["Enviando", ""],
  processing: ["Processando", ""],
  completed: ["Concluído", "ok"],
  error: ["Falhou", "error"],
};

function isolationBadge(user) {
  if (user.isolated == null) return "—";
  const title = user.isolated
    ? "Não conseguiu abrir a auditoria de outro usuário (404), como esperado."
    : "Conseguiu ver dados de outro usuário: falha de isolamento.";
  return `<span class="badge ${user.isolated ? "ok" : "error"}" title="${title}">${user.isolated ? "Isolado" : "Vazamento"}</span>`;
}

function renderSimulation(state) {
  $("simulation-table-wrapper").hidden = false;
  $("body-simulation").innerHTML = state.users.map((user) => {
    const [label, className] = SIMULATION_STATUS[user.status] || [user.status, ""];
    const progress = user.total ? (user.processed / user.total) * 100 : 0;
    return `
      <tr>
        <td>${escapeHtml(user.username)}</td>
        <td><span class="badge ${className}" title="${escapeHtml(user.error || "")}">${label}</span></td>
        <td>
          <div class="mini-track"><div class="mini-fill" style="width: ${progress}%"></div></div>
          <span class="mini-label">${user.processed}/${user.total ?? "?"}</span>
        </td>
        <td>${user.matched}</td>
        <td>${user.mismatched}</td>
        <td>${user.login_ms == null ? "—" : `${user.login_ms} ms`}</td>
        <td>${user.first_page_seconds == null ? "—" : `${decimal(user.first_page_seconds)} s`}</td>
        <td>${user.seconds == null ? "—" : `${decimal(user.seconds)} s`}</td>
        <td>${isolationBadge(user)}</td>
      </tr>`;
  }).join("");

  $("simulation-summary").hidden = false;
  $("sim-total-users").textContent = state.users.length;
  $("sim-total-pages").textContent = state.users.reduce((total, user) => total + user.processed, 0);
  const summary = state.summary;
  $("sim-wall-time").textContent = state.wall_seconds == null ? "…" : formatDuration(state.wall_seconds);
  $("sim-throughput").textContent = summary?.pages_per_minute == null ? "…" : decimal(summary.pages_per_minute);
  $("sim-isolation").textContent = summary ? (summary.isolation_passed ? "Aprovado" : "Reprovado") : "…";
  $("sim-isolation-card").className = `indicator ${summary ? (summary.isolation_passed ? "ok" : "error") : ""}`;
}

async function pollSimulation(simulationId) {
  try {
    const response = await api(`/api/v1/simulations/${simulationId}`);
    if (!response.ok) throw new Error("Situação da simulação indisponível.");
    const state = await response.json();
    renderSimulation(state);
    if (state.status !== "running") {
      stopSimulationPolling();
      $("simulation-notice").textContent = state.error ? `Erro na simulação: ${state.error}` : "";
    }
  } catch (error) {
    stopSimulationPolling();
    $("simulation-notice").textContent = error.message;
  }
}

function stopSimulationPolling() {
  clearInterval(simulationTimer);
  simulationTimer = null;
  $("simulation-button").disabled = false;
  $("simulation-button").textContent = "Rodar simulação";
}

$("simulationForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("simulation-notice").textContent = "";
  const request = {
    users: Number($("sim-users").value),
    pages: Number($("sim-pages").value),
    dpi: Number($("sim-dpi").value),
  };
  $("simulation-button").disabled = true;
  $("simulation-button").textContent = "Simulação em andamento…";
  try {
    const response = await api("/api/v1/simulations", jsonRequest("POST", request));
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(errorMessage(body, "Não foi possível iniciar a simulação."));
    simulationTimer = setInterval(() => pollSimulation(body.simulation_id), 1000);
    pollSimulation(body.simulation_id);
  } catch (error) {
    $("simulation-notice").textContent = error.message;
    stopSimulationPolling();
  }
});

initSession();
