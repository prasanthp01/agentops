'use strict';
const API = '/api/projects/agentops';
const $ = id => document.getElementById(id);
const mode = 'SANDBOX_DETERMINISTIC';
const modelNames = ['cedar-eu', 'harbor-eu', 'swift-us', 'vault-eu'];
const failureNames = ['healthy', '429', '503', 'timeout', 'invalid_json', 'context_overflow', 'restricted_region'];
let presetList = [], csrf = '', currentRun = null, events = [], selectedStep = null, selectedView = 'overview';
let stream = null, pollTimer = null, busy = false, latestResult = null, runs = [], generation = 0;
let ready = false, initializing = false, completing = false;

function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}
// Older cached pages lack the new recovery controls. Upgrade their alert in place.
if (!$('error-message')) {
  const message = node('span'); message.id = 'error-message';
  const retry = node('button', 'Retry connection'); retry.id = 'retry-connection';
  retry.type = 'button'; retry.hidden = true;
  $('error').replaceChildren(message, retry); $('error').tabIndex = -1;
}
function showError(message) {
  $('error-message').textContent = message; $('error').hidden = false;
  $('error').focus({ preventScroll: true }); $('error').scrollIntoView({ block: 'nearest' });
}
function setBusy(value) {
  busy = value; $('run').disabled = value || !ready; $('cancel').hidden = !value;
  $('cancel').disabled = !value || !currentRun;
  $('run').textContent = value ? 'Running…' : 'Run simulation →';
  $('run-form').setAttribute('aria-busy', String(value));
  $('reset').disabled = value || !ready; $('preset').disabled = value || !ready;
}
class ApiError extends Error {
  constructor(code, status = 0, retryAfter = 60) {
    const messages = {
      NETWORK_ERROR: 'Connection lost. Check your connection and try again.',
      REQUEST_TIMEOUT: 'The request timed out. Please try again.',
      INVALID_RESPONSE: 'The service returned an unreadable response. Please try again.',
      SESSION_REQUIRED: 'Your session is missing. Run the simulation again to reconnect.',
      SESSION_EXPIRED: 'Your session expired. Run the simulation again to reconnect.',
      CSRF_REJECTED: 'Your session changed. Run the simulation again to reconnect.',
      INVALID_INPUT: 'Check the input values and select at least one required capability.',
      RUN_NOT_FOUND: 'This run is no longer available in your session. Start a new simulation.'
    };
    super(status === 429 ? `Too many requests. Please wait ${retryAfter} seconds and try again.`
      : status >= 500 ? 'The service is temporarily unavailable. Please try again.'
      : messages[code] || `The request could not be completed (${status}). Please try again.`);
    this.code = code; this.retryable = !status || status === 429 || status >= 500;
  }
}
async function api(path, options = {}) {
  const headers = { ...options.headers };
  if (options.body) headers['Content-Type'] = 'application/json';
  if (options.method === 'POST') headers['X-CSRF-Token'] = csrf;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(API + path, { ...options, headers, credentials: 'same-origin', signal: controller.signal });
    let data;
    try { data = JSON.parse(await response.text()); }
    catch (error) { if (controller.signal.aborted) throw error; }
    if (!response.ok) {
      const delay = Number(response.headers.get('Retry-After'));
      throw new ApiError(data?.error?.code || 'REQUEST_FAILED', response.status, delay > 0 && delay <= 3600 ? Math.ceil(delay) : 60);
    }
    if (!data) throw new ApiError('INVALID_RESPONSE');
    return data;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(controller.signal.aborted ? 'REQUEST_TIMEOUT' : 'NETWORK_ERROR');
  } finally { clearTimeout(timeout); }
}
async function session() { csrf = (await api('/session', { method: 'POST' })).csrf_token; }
function buildProviderControls() {
  modelNames.forEach(name => {
    const row = node('div', undefined, 'provider-row');
    const label = node('label', name); label.htmlFor = `failure-${name}`;
    const select = node('select'); select.id = `failure-${name}`;
    failureNames.forEach(f => { const option = node('option', f === 'healthy' ? 'Healthy' : f.replaceAll('_', ' ')); option.value = f; select.append(option); });
    const healthLabel = node('label', undefined, 'health-toggle');
    const input = node('input'); input.type = 'checkbox'; input.id = `offline-${name}`;
    input.setAttribute('aria-label', `Mark ${name} unavailable`);
    healthLabel.append(input, document.createTextNode('Offline'));
    row.append(label, select, healthLabel); $('provider-controls').append(row);
  });
}
function applyPreset() {
  const preset = presetList.find(p => p.id === $('preset').value);
  if (!preset) return;
  const input = preset.input;
  for (const [key, value] of Object.entries(input)) { if ($(key)) $(key).value = String(value); }
  $('region').value = input.allowed_regions.join(','); $('budget').value = input.available_funds_microusd / 1e6;
  document.querySelectorAll('[name=capability]').forEach(c => { c.checked = input.required_capabilities.includes(c.value); });
  modelNames.forEach(name => {
    $(`failure-${name}`).value = input.provider_failures[name] || 'healthy';
    $(`offline-${name}`).checked = input.unavailable_models.includes(name);
  });
  $('preset-description').textContent = preset.description;
  if (Object.keys(input.provider_failures).length || !input.control_plane_available || input.policy_version === 'expired-policy') $('advanced').open = true;
}
function collectInput() {
  const input = {};
  ['task_type', 'data_classification', 'strategy', 'language', 'policy_version'].forEach(k => { input[k] = $(k).value; });
  ['quality_floor', 'input_tokens', 'output_tokens', 'latency_deadline_ms', 'max_fallbacks', 'max_retries', 'retry_after_ms', 'seed'].forEach(k => { input[k] = Number($(k).value); });
  input.allowed_regions = $('region').value.split(',');
  input.available_funds_microusd = Math.round(Number($('budget').value) * 1e6);
  input.required_capabilities = Array.from(document.querySelectorAll('[name=capability]:checked')).map(c => c.value);
  input.control_plane_available = $('control_plane_available').value === 'true';
  input.provider_failures = Object.fromEntries(modelNames.map(n => [n, $(`failure-${n}`).value]));
  input.unavailable_models = modelNames.filter(n => $(`offline-${n}`).checked);
  if (!input.required_capabilities.length) throw new Error('Select at least one required capability.');
  return input;
}
function stopWatching() { if (stream) stream.close(); stream = null; clearTimeout(pollTimer); }
function inspect(event) {
  selectedStep = event; $('inspector').hidden = false; $('step-title').textContent = event.title;
  document.querySelectorAll('.timeline button').forEach(b => b.classList.toggle('active', Number(b.dataset.sequence) === event.sequence));
  renderInspector();
}
function renderInspector() {
  if (!selectedStep) return;
  document.querySelectorAll('[data-view]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.view === selectedView)));
  $('step-overview').hidden = selectedView !== 'overview'; $('step-json').hidden = selectedView === 'overview';
  $('step-overview').textContent = selectedStep.overview;
  const values = { technical: selectedStep.technical, json: selectedStep,
    evidence: { event_id: selectedStep.event_id, sequence: selectedStep.sequence, occurred_at: selectedStep.occurred_at,
      trace_id: selectedStep.trace_id, mode: selectedStep.mode, usage: selectedStep.usage, provenance: 'Actual routing code with synthetic fixtures' } };
  if (selectedView !== 'overview') $('step-json').textContent = JSON.stringify(values[selectedView], null, 2);
}
function addEvents(incoming) {
  const known = new Set(events.map(e => e.sequence));
  const next = incoming.filter(e => !known.has(e.sequence)).sort((a, b) => a.sequence - b.sequence);
  next.forEach(event => {
    events.push(event); const li = node('li');
    if (['FAILED', 'BLOCKED'].includes(event.status)) li.classList.add('failed');
    const button = node('button'); button.type = 'button'; button.dataset.sequence = event.sequence;
    button.append(node('span', event.title), node('small', event.status));
    button.addEventListener('click', () => inspect(event)); li.append(button); $('timeline').append(li);
  });
  if (events.length) {
    $('trace-empty').hidden = true;
    if (!selectedStep) inspect(events[0]);
  }
}
function metric(label, value, detail) {
  const el = node('div', undefined, 'metric'); el.append(node('span', label), node('strong', value), node('span', detail)); return el;
}
function renderResult(result) {
  latestResult = result; const out = result.output;
  $('result').hidden = false; $('result-title').textContent = out.selected_model ? `Selected ${out.selected_model}` : (out.outcome || result.status).replaceAll('_', ' ');
  $('result-reason').textContent = out.reason || 'This run ended without a completed routing result.';
  $('result-metrics').replaceChildren(
    metric('Modeled provider cost', result.cost.value == null ? '—' : `$${Number(result.cost.value).toFixed(6)}`, 'Synthetic USD · never billed'),
    metric('Modeled latency', `${out.modeled_latency_ms ?? 0} ms`, 'Virtual provider time & backoff'),
    metric('Measured processing', result.duration_ms == null ? '—' : `${result.duration_ms.toFixed(1)} ms`, 'Actual local engine elapsed time'),
    metric('Provider attempts', String(out.attempt_count ?? out.usage?.length ?? 0), '$0 external LLM spend')
  );
  $('candidates').replaceChildren();
  (out.candidate_decisions || []).forEach(c => {
    const el = node('div', undefined, 'candidate'); const title = node('strong', c.model);
    title.append(node('small', c.eligible ? 'Eligible' : 'Excluded'));
    el.append(title, node('p', c.eligible ? `Synthetic quality ${c.quality_score}/100 · $${(c.predicted_cost_microusd / 1e6).toFixed(6)} predicted` : c.reasons.map(s => s.replaceAll('_', ' ').toLowerCase()).join(' · ')));
    $('candidates').append(el);
  });
  $('fallback-note').textContent = result.status === 'CANCELLED' ? 'Run cancelled. Only settled synthetic usage is shown; no business action was completed.' : out.workflow_fallback ? 'Workflow fallback required: the calling application must use deterministic processing or human review. AgentOps has not completed a business action.' : out.model_fallback ? `Model fallback completed after ${out.fallback_hops} compatible hop(s). Policy remained enforced.` : 'No model fallback was needed. The result is a synthetic fixture, not generated model prose.';
  runs = runs.filter(r => r.result.run_id !== result.run_id);
  runs.push({ result, preset: $('preset').selectedOptions[0]?.textContent || 'Custom' }); runs = runs.slice(-2);
  if (runs.length === 2) {
    $('comparison').hidden = false; $('comparison-content').replaceChildren();
    runs.forEach((r, i) => {
      const box = node('div', undefined, 'comparison-card');
      box.append(node('strong', `${i === 0 ? 'Previous' : 'Latest'} · ${r.preset}`),
        node('p', `${r.result.output.selected_model || r.result.output.outcome} · ${r.result.output.attempt_count ?? 0} attempts`),
        node('p', `$${Number(r.result.cost.value || 0).toFixed(6)} simulated · ${r.result.output.modeled_latency_ms ?? 0} ms modeled`));
      $('comparison-content').append(box);
    });
  }
}
async function complete(run, gen) {
  if (gen !== generation || !busy || completing) return;
  completing = true; stopWatching();
  try {
    const result = await api(`/simulations/${run}/result`);
    if (gen !== generation) return;
    if (['RUNNING', 'ACCEPTED'].includes(result.status)) { pollTimer = setTimeout(() => poll(run, gen), 250); return; }
    renderResult(result); $('run-status').textContent = result.status;
    $('run-announcement').textContent = `Run ${result.status.toLowerCase()}. ${result.output.reason || ''}`;
    setBusy(false);
  } catch (error) { failRun(error, gen); }
  finally { if (gen === generation) completing = false; }
}
function failRun(error, gen) {
  if (gen !== generation || !busy) return;
  stopWatching(); setBusy(false); $('run-status').textContent = error.retryable ? 'DISCONNECTED' : 'ERROR';
  $('retry-connection').hidden = !error.retryable; $('retry-connection').textContent = 'Reconnect to run';
  showError(error.message);
}
async function poll(run, gen) {
  if (gen !== generation || !busy) return;
  try {
    const after = events.length ? Math.max(...events.map(e => e.sequence)) : 0;
    const data = await api(`/simulations/${run}/events?transport=poll&after=${after}`);
    if (gen !== generation || !busy) return;
    addEvents(data.events); const state = await api(`/simulations/${run}`);
    if (gen !== generation || !busy) return;
    $('run-status').textContent = state.status;
    if (!['ACCEPTED', 'RUNNING'].includes(state.status)) await complete(run, gen);
    else pollTimer = setTimeout(() => poll(run, gen), 300);
  } catch (error) { failRun(error, gen); }
}
function watch(run, gen) {
  if (!window.EventSource) { poll(run, gen); return; }
  stream = new EventSource(API + `/simulations/${run}/events`);
  stream.addEventListener('step', event => {
    if (gen !== generation || !busy) return;
    try { addEvents([JSON.parse(event.data)]); $('run-status').textContent = 'RUNNING'; }
    catch { stopWatching(); poll(run, gen); }
  });
  stream.addEventListener('done', () => complete(run, gen));
  stream.onerror = () => { if (gen === generation && busy) { stopWatching(); poll(run, gen); } };
}
$('run-form').addEventListener('submit', async event => {
  event.preventDefault(); if (busy || !ready) return;
  $('error').hidden = true; $('retry-connection').hidden = true;
  try {
    const input = collectInput(); const gen = ++generation;
    currentRun = null; completing = false;
    setBusy(true); stopWatching(); events = []; selectedStep = null; latestResult = null;
    $('timeline').replaceChildren(); $('trace-empty').hidden = false; $('inspector').hidden = true; $('result').hidden = true;
    $('run-status').textContent = 'SUBMITTING';
    const payload = { schema_version: '1.0', project_id: 'agentops', mode, preset_id: $('preset').value, input };
    const key = crypto.randomUUID();
    let run;
    try { run = await api('/simulations', { method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify(payload) }); }
    catch (error) {
      if (!['SESSION_EXPIRED', 'SESSION_REQUIRED', 'CSRF_REJECTED'].includes(error.code)) throw error;
      await session(); run = await api('/simulations', { method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify(payload) });
    }
    currentRun = run.run_id; $('cancel').disabled = false;
    $('run-status').textContent = run.status; watch(currentRun, gen);
  } catch (error) { showError(error.message); $('run-status').textContent = 'ERROR'; setBusy(false); }
});
$('run-form').addEventListener('invalid', event => {
  // Reveal a collapsed field before native validation tries to focus it.
  const section = event.target.closest('details');
  if (section) section.open = true;
}, true);
$('cancel').addEventListener('click', async () => {
  if (!currentRun || !busy) return;
  const run = currentRun, gen = generation; $('cancel').disabled = true;
  try { await api(`/simulations/${run}/cancel`, { method: 'POST' }); await poll(run, gen); }
  catch (error) { if (gen === generation && busy) showError(error.message); }
  finally { if (gen === generation && busy) $('cancel').disabled = false; }
});
$('retry-connection').addEventListener('click', () => {
  if (!ready || !currentRun) { init(); return; }
  $('error').hidden = true; $('retry-connection').hidden = true;
  completing = false; setBusy(true); $('run-status').textContent = 'RECONNECTING';
  watch(currentRun, ++generation);
});
$('preset').addEventListener('change', applyPreset); $('reset').addEventListener('click', applyPreset);
document.querySelectorAll('[data-view]').forEach(b => b.addEventListener('click', () => { selectedView = b.dataset.view; renderInspector(); }));
$('export').addEventListener('click', () => {
  if (!latestResult) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify({ result: latestResult, events }, null, 2)], { type: 'application/json' }));
  const a = node('a'); a.href = url; a.download = 'agentops-synthetic-run.json'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
function menu(open) {
  $('sidebar').classList.toggle('open', open); $('scrim').hidden = !open;
  $('menu-close').hidden = !open; $('menu-open').setAttribute('aria-expanded', String(open));
  document.body.classList.toggle('menu-open', open);
  if (open) { $('sidebar').setAttribute('role', 'dialog'); $('sidebar').setAttribute('aria-modal', 'true'); $('main').inert = true; $('menu-close').focus(); }
  else { $('sidebar').removeAttribute('role'); $('sidebar').removeAttribute('aria-modal'); $('main').inert = false; $('menu-open').focus(); }
}
$('menu-open').addEventListener('click', () => menu(true)); $('menu-close').addEventListener('click', () => menu(false)); $('scrim').addEventListener('click', () => menu(false));
$('sidebar').addEventListener('click', e => { if (e.target.closest('a') && $('sidebar').classList.contains('open')) menu(false); });
document.addEventListener('keydown', event => {
  if (!$('sidebar').classList.contains('open')) return;
  if (event.key === 'Escape') { event.preventDefault(); menu(false); }
  if (event.key === 'Tab') {
    const focusable = [...$('sidebar').querySelectorAll('a,button')].filter(el => !el.hidden);
    const first = focusable[0], last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
});
window.matchMedia('(min-width:801px)').addEventListener('change', event => { if (event.matches && $('sidebar').classList.contains('open')) menu(false); });
window.addEventListener('beforeunload', stopWatching);
async function init() {
  if (initializing) return;
  initializing = true; ready = false; setBusy(false);
  $('retry-connection').hidden = true; $('error').hidden = true; $('run-status').textContent = 'CONNECTING';
  try {
    if (!presetList.length) {
      const data = await api('/simulation/presets'); presetList = data.presets;
      $('preset').replaceChildren(...presetList.map(p => { const option = node('option', p.title); option.value = p.id; return option; }));
      applyPreset();
    }
    await session(); ready = true; setBusy(false); $('run-status').textContent = 'READY';
  } catch (error) {
    $('retry-connection').textContent = 'Retry connection'; $('retry-connection').hidden = false;
    $('run-status').textContent = 'UNAVAILABLE'; showError(error.message);
  } finally { initializing = false; }
}
async function loadEvidence() {
  try {
    const response = await fetch('/projects/agentops/assets/verification.json');
    if (response.ok) {
      const proof = await response.json();
      if (proof.test_summary) $('verification-status').textContent = proof.test_summary;
      if (proof.benchmark_summary) $('benchmark-status').textContent = proof.benchmark_summary;
    }
  } catch { /* The evidence file is optional; no claims are invented. */ }
}
buildProviderControls();
init();
loadEvidence();
