// Exercise the real browser script with a small DOM and transport fixture.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const staticPath = path.join(__dirname, '..', 'static');
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function element() {
  const classes = new Set();
  return {
    hidden: false, checked: false, disabled: false, textContent: '', value: '', style: {}, dataset: {},
    children: [], listeners: {},
    classList: {
      add(name) { classes.add(name); },
      toggle(name, force) { (force ?? !classes.has(name)) ? classes.add(name) : classes.delete(name); },
      contains(name) { return classes.has(name); },
    },
    set innerHTML(value) { this.markup = value; this.children = []; },
    get innerHTML() { return this.markup || ''; },
    addEventListener(type, callback) { this.listeners[type] = callback; },
    appendChild(child) { this.children.push(child); }, scrollIntoView() {},
  };
}

const rows = [
  { Page: 1, 'Reference Line': 1, 'Code (Reference PDF)': '113640', 'Code (OCR Slips)': '113640',
    'Amount (Reference PDF)': '76,82', 'Amount (OCR Slips)': '76,82', 'Code Status': 'OK', 'Amount Status': 'OK',
    'Overall Status': 'OK', Category: '', 'Matched By': 'CODE', 'Code Confidence': 91, 'Amount Confidence': 88,
    'Code Votes': 1, 'Amount Votes': 1, 'Strategies Run': 1, 'Render ms': 400, 'OCR ms': 600, Review: '' },
  { Page: 2, 'Reference Line': 2, 'Code (Reference PDF)': '116110', 'Code (OCR Slips)': '716110',
    'Amount (Reference PDF)': '82,30', 'Amount (OCR Slips)': '82,30', 'Code Status': 'MISMATCH', 'Amount Status': 'OK',
    'Overall Status': 'ERROR', Category: 'LIKELY MISREAD', 'Matched By': 'POSITION', 'Code Confidence': 52,
    'Amount Confidence': 90, 'Code Votes': 5, 'Amount Votes': 8, 'Strategies Run': 8, 'Render ms': 400,
    'OCR ms': 2600, Review: '' },
];
const missing = [{ line: 3, code: '118870', amount: '76,82' }];
const metrics = [{
  dpi: 300, audits: 1, pages: 2, timed_pages: 2, avg_render_ms: 400, avg_ocr_ms: 1600, wall_seconds_per_page: 1.2,
  consensus_pages: 1, matched: 1, flagged: 1, document_discrepancies: 0, ocr_errors: 1, pending_review: 0,
  avg_code_confidence: 71.5, avg_amount_confidence: 89,
}];
const simulationState = {
  simulation_id: 's1', status: 'completed', wall_seconds: 12.4,
  summary: { users: 2, pages: 6, pages_per_minute: 29, failed_users: 0, isolation_passed: true },
  users: ['sim-s1-1', 'sim-s1-2'].map((username) => ({
    username, status: 'completed', job_id: `job-${username}`, total: 3, processed: 3, matched: 2, mismatched: 1,
    login_ms: 210, first_page_seconds: 2.5, seconds: 9.1, isolated: true, isolation_detail: 'blocked (404)', error: null,
  })),
};

function setup() {
  const html = fs.readFileSync(path.join(staticPath, 'index.html'), 'utf8');
  const nodes = new Map([...html.matchAll(/\bid="([^"]+)"/g)].map((match) => [match[1], element()]));
  const requests = [];
  const streams = [];
  const timers = new Map();
  let signedIn = false;

  const respond = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });
  const context = vm.createContext({
    document: {
      getElementById(id) { assert.ok(nodes.has(id), `Unknown DOM ID: ${id}`); return nodes.get(id); },
      createElement: element,
      querySelectorAll(selector) { assert.equal(selector, '#body-table tr'); return nodes.get('body-table').children; },
    },
    FormData: class extends Map {
      constructor() { super([['payment_slips', 'slips.pdf'], ['reference', 'reference.pdf'], ['dpi', '300']]); }
    },
    EventSource: class {
      static CLOSED = 2;
      constructor(url) { this.url = url; streams.push(this); }
      close() { this.closed = true; }
    },
    setInterval(callback) { const id = timers.size + 1; timers.set(id, callback); return id; },
    clearInterval(id) { timers.delete(id); },
    async fetch(url, options = {}) {
      const method = options.method || 'GET';
      requests.push({ method, url, body: options.body });
      if (url === '/api/v1/auth/me') return signedIn ? respond({ username: 'ana' }) : respond({ detail: 'Sign in.' }, 401);
      if (url === '/api/v1/auth/login') {
        const credentials = JSON.parse(options.body);
        if (credentials.password === 'wrong') return respond({ detail: 'Invalid username or password.' }, 401);
        signedIn = true;
        return respond({ username: credentials.username });
      }
      if (url === '/api/v1/auth/logout') { signedIn = false; return respond({}); }
      if (!signedIn) return respond({ detail: 'Sign in to continue.' }, 401);
      if (url === '/api/v1/audits' && method === 'GET') return respond([]);
      if (url.startsWith('/api/v1/audits?dpi=')) return respond({ job_id: 'sample', message: 'Audit started.' });
      if (url === '/api/v1/audits/sample/pages') return respond(rows);
      if (url === '/api/v1/audits/sample/missing') return respond(missing);
      if (url === '/api/v1/audits/sample/pages/2/review') {
        return respond({ ...rows[1], Review: JSON.parse(options.body).verdict || '' });
      }
      if (url === '/api/v1/metrics') return respond(metrics);
      if (url === '/api/v1/simulations') return respond({ simulation_id: 's1' });
      if (url === '/api/v1/simulations/s1') return respond(simulationState);
      throw new Error(`Unexpected request: ${method} ${url}`);
    },
  });
  vm.runInContext(fs.readFileSync(path.join(staticPath, 'app.js'), 'utf8'), context);
  return { nodes, requests, streams, timers, context };
}

test('sign-in gates the application and sign-out returns to it', async () => {
  const { nodes } = setup();
  await flush();
  assert.equal(nodes.get('login-card').hidden, false);
  assert.equal(nodes.get('app-content').hidden, true);

  nodes.get('login-password').value = 'wrong';
  nodes.get('loginForm').listeners.submit({ preventDefault() {} });
  await flush();
  assert.equal(nodes.get('login-notice').textContent, 'Usuário ou senha inválidos.', 'API errors are shown in Portuguese');
  assert.equal(nodes.get('app-content').hidden, true);

  nodes.get('login-username').value = ' ana ';
  nodes.get('login-password').value = 'secret-password';
  nodes.get('loginForm').listeners.submit({ preventDefault() {} });
  await flush();
  assert.equal(nodes.get('login-card').hidden, true);
  assert.equal(nodes.get('app-content').hidden, false);
  assert.equal(nodes.get('user-name').textContent, 'ana');
  assert.equal(nodes.get('login-password').value, '', 'the password field is cleared');

  await nodes.get('logout-button').listeners.click();
  assert.equal(nodes.get('login-card').hidden, false);
  assert.equal(nodes.get('app-content').hidden, true);
});

test('upload, stream, review, missing slips, CSV, and history remain connected', async () => {
  const { nodes, requests, streams, timers, context } = setup();
  await flush();
  nodes.get('login-username').value = 'ana';
  nodes.get('loginForm').listeners.submit({ preventDefault() {} });
  await flush();

  await nodes.get('auditForm').listeners.submit({ preventDefault() {} });
  const upload = requests.find((request) => request.method === 'POST' && request.url.startsWith('/api/v1/audits'));
  assert.equal(upload.url, '/api/v1/audits?dpi=300');
  assert.deepEqual([...upload.body.keys()], ['payment_slips', 'reference']);
  assert.equal(streams[0].url, '/api/v1/audits/sample/events');

  const emit = (event) => streams[0].onmessage({ data: JSON.stringify(event) });
  emit({ type: 'start', total_pages: 2, reference_count: 3, dpi: 300 });
  assert.equal(nodes.get('active-resolution').textContent, '300 DPI');
  for (const row of rows) emit({ type: 'page', page: row.Page, row, strategy: '1. Default' });
  assert.equal(nodes.get('ind-processed').textContent, '2/2');
  assert.equal(nodes.get('ind-match-rate').textContent, '50,0%');
  assert.match(nodes.get('explanation-category').innerHTML, /Provável erro de leitura/);
  assert.match(nodes.get('evidence').textContent, /confiança 52%, 5\/8 estratégias concordam/);
  assert.match(nodes.get('evidence').textContent, /Pareada pela posição com a linha 2 da consulta/);
  assert.equal(nodes.get('review-box').hidden, false, 'mismatches can be reviewed');

  await nodes.get('review-ocr').listeners.click();
  const review = requests.find((request) => request.method === 'PUT');
  assert.equal(review.url, '/api/v1/audits/sample/pages/2/review');
  assert.deepEqual(JSON.parse(review.body), { verdict: 'OCR' });
  assert.match(nodes.get('body-table').children[1].innerHTML, /Erro do OCR/);
  assert.ok(nodes.get('review-ocr').classList.contains('active'));

  nodes.get('mismatch-filter').listeners.change({ target: { checked: true } });
  assert.deepEqual(nodes.get('body-table').children.map((row) => row.hidden), [true, false]);

  emit({ type: 'end', total_pages: 2, model: 'Tesseract OCR', elapsed_seconds: 4.2, missing });
  assert.equal(nodes.get('link-csv').href, '/api/v1/audits/sample/report.csv');
  assert.equal(nodes.get('missing-block').hidden, false);
  assert.match(nodes.get('missing-list').innerHTML, /Linha 3: código <strong>118870<\/strong>/);
  assert.equal(nodes.get('ind-page-time').textContent, '2,1');
  assert.equal(streams[0].closed, true);
  assert.equal(timers.size, 0);

  assert.match(nodes.get('tag-strategy').textContent, /1\. Padrão/);

  await context.openAudit({ job_id: 'sample', total_pages: 2, created_at: '2026-09-30T10:00:00', dpi: 300, duration_seconds: 4.2 });
  assert.match(nodes.get('viewer').innerHTML, /não são guardadas/);
  assert.equal(nodes.get('body-table').children.length, 2);
  assert.equal(nodes.get('missing-block').hidden, false);

  context.followAudit('sample');
  streams[1].onmessage({ data: JSON.stringify({ type: 'error', message: 'Could not read PDF.' }) });
  assert.equal(nodes.get('notice').textContent, 'Erro durante a auditoria: Could not read PDF.');
  assert.equal(streams[1].closed, true);
  assert.equal(timers.size, 0);
});

test('metrics compare resolutions and the simulation reports isolation', async () => {
  const { nodes, requests, timers } = setup();
  await flush();
  nodes.get('loginForm').listeners.submit({ preventDefault() {} });
  await flush();

  nodes.get('tab-metrics').listeners.click();
  await flush();
  assert.equal(nodes.get('view-metrics').hidden, false);
  assert.equal(nodes.get('view-audit').hidden, true);
  assert.equal(nodes.get('metrics-charts').hidden, false);
  assert.match(nodes.get('chart-time').innerHTML, /300 DPI[\s\S]*2,00 s/);
  assert.match(nodes.get('body-metrics').innerHTML, /<strong>300<\/strong>/);
  assert.match(nodes.get('body-metrics').innerHTML, /50,0%/, 'one OCR error in two pages');

  nodes.get('tab-simulation').listeners.click();
  nodes.get('sim-users').value = '2';
  nodes.get('sim-pages').value = '3';
  nodes.get('sim-dpi').value = '200';
  await nodes.get('simulationForm').listeners.submit({ preventDefault() {} });
  await flush();
  const start = requests.find((request) => request.url === '/api/v1/simulations');
  assert.deepEqual(JSON.parse(start.body), { users: 2, pages: 3, dpi: 200 });
  assert.match(nodes.get('body-simulation').innerHTML, /sim-s1-1[\s\S]*Isolado/);
  assert.equal(nodes.get('sim-isolation').textContent, 'Aprovado');
  assert.equal(nodes.get('sim-throughput').textContent, '29,0');
  assert.equal(timers.size, 0, 'polling stops when the simulation ends');
  assert.equal(nodes.get('simulation-button').disabled, false);
});
