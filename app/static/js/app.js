// ── State ────────────────────────────────────────────────────────
let ws = null;
let currentMemKey = 'claude_md';
let tokenData = null;

// ── WebSocket ────────────────────────────────────────────────────
function connectWs() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.onopen = () => {
    document.getElementById('ws-status').className = 'w-2 h-2 rounded-full bg-green-500 inline-block';
  };

  ws.onclose = () => {
    document.getElementById('ws-status').className = 'w-2 h-2 rounded-full bg-red-500 inline-block';
    setTimeout(connectWs, 3000);
  };

  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === 'log') appendLog(msg.msg);
      if (msg.type === 'status') applyStatus(msg.data);
    } catch {}
  };
}

// ── Tab navigation ───────────────────────────────────────────────
document.querySelectorAll('.nav-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.add('hidden'));
    btn.classList.add('active');
    document.getElementById(`tab-${btn.dataset.tab}`).classList.remove('hidden');
    // Auto-load on tab switch
    if (btn.dataset.tab === 'tokens') refreshTokens();
    if (btn.dataset.tab === 'memory') loadMemory(currentMemKey);
    if (btn.dataset.tab === 'mobile') refreshCommands();
  });
});

// ── API helper ───────────────────────────────────────────────────
async function api(method, url, body = null) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

// ── Status apply ─────────────────────────────────────────────────
function applyStatus(data) {
  const w = data.watcher || {};
  const ex = data.executor || {};
  const li = data.listener || {};

  // Badge top bar
  const badge = document.getElementById('claude-state-badge');
  const stateLabels = { running: '🟢 En cours', idle: '💤 Libre', rate_limited: '🚫 Rate limit', unknown: '❓' };
  badge.textContent = stateLabels[w.state] || w.state || '—';
  badge.className = `px-2 py-0.5 rounded text-xs font-semibold state-${w.state || 'unknown'}`;

  // Server time
  if (data.server_time) document.getElementById('server-time').textContent = data.server_time;

  // Dashboard
  const dState = document.getElementById('d-claude-state');
  if (dState) {
    dState.textContent = stateLabels[w.state] || '—';
    document.getElementById('d-processes').textContent = `${w.claude_processes || 0} process(es) Claude`;
    const resetEl = document.getElementById('d-reset');
    if (w.remaining) {
      resetEl.textContent = `Reset dans ${w.remaining}`;
      resetEl.classList.remove('hidden');
    } else {
      resetEl.classList.add('hidden');
    }
  }

  // Executor
  const dExec = document.getElementById('d-exec-mode');
  if (dExec) {
    dExec.textContent = ex.enable_execution ? '✅ Exécution active' : '🔒 Dry-run';
    document.getElementById('d-exec-calls').textContent = `${ex.call_count || 0} / ${ex.max_calls || '?'} appels`;
  }

  // Listener
  const dList = document.getElementById('d-listener-state');
  if (dList) {
    dList.textContent = li.running ? '🟢 Actif' : '🔴 Arrêté';
    const topicEl = document.getElementById('d-listener-topic');
    if (topicEl) topicEl.textContent = li.topic || '';
  }
}

// ── Tokens ───────────────────────────────────────────────────────
async function refreshTokens() {
  try {
    tokenData = await api('GET', '/api/tokens');
    renderTokens(tokenData);
    // Update dashboard mini-view
    const snap = tokenData.snapshot || {};
    setEl('d-cost', snap.last_cost != null ? `$${snap.last_cost.toFixed(4)}` : '—');
    setEl('d-input', fmtNum(snap.input_tokens));
    setEl('d-output', fmtNum(snap.output_tokens));
    setEl('d-model', snap.top_model || '—');
    setEl('d-token-project', `Projet : ${snap.project_key || '—'}`);
    const drift = tokenData.drift || {};
    const driftEl = document.getElementById('d-drift');
    if (driftEl) {
      const colors = { LOW: 'text-green-400', MEDIUM: 'text-amber-400', HIGH: 'text-red-400', UNKNOWN: 'text-gray-400' };
      driftEl.innerHTML = `<span class="${colors[drift.level] || ''}">Dérive contexte : ${drift.level || '—'}</span> ${(drift.reasons || []).join(' · ')}`;
    }
    // Token summary in header
    if (snap.total_tokens) {
      document.getElementById('token-summary').textContent = `${fmtNum(snap.total_tokens)} tok · $${(snap.last_cost || 0).toFixed(4)}`;
    }
  } catch (e) {
    console.error('Token refresh error:', e);
  }
}

function renderTokens(data) {
  const snap = data.snapshot || {};
  const drift = data.drift || {};
  const hist = data.history || [];

  const fullEl = document.getElementById('token-full');
  if (fullEl) {
    const rows = [
      ['Projet', snap.project_key],
      ['Coût dernier run', snap.last_cost != null ? `$${snap.last_cost.toFixed(4)}` : '—'],
      ['Input tokens', fmtNum(snap.input_tokens)],
      ['Output tokens', fmtNum(snap.output_tokens)],
      ['Cache create', fmtNum(snap.cache_create_tokens)],
      ['Cache read', fmtNum(snap.cache_read_tokens)],
      ['Total tokens', fmtNum(snap.total_tokens)],
      ['Durée', snap.duration_ms ? `${(snap.duration_ms / 1000).toFixed(1)}s` : '—'],
      ['Modèle', snap.top_model],
    ];
    fullEl.innerHTML = rows.map(([k, v]) =>
      `<div class="flex justify-between border-b border-gray-800 py-1">
        <span class="text-gray-400">${k}</span>
        <span class="text-gray-100 font-mono">${v || '—'}</span>
       </div>`
    ).join('');
  }

  const driftEl = document.getElementById('token-drift');
  if (driftEl) {
    const colors = { LOW: '#34d399', MEDIUM: '#fbbf24', HIGH: '#f87171', UNKNOWN: '#9ca3af' };
    driftEl.innerHTML = `<div class="font-semibold" style="color:${colors[drift.level] || '#9ca3af'}">Dérive : ${drift.level}</div>
      <div class="text-gray-400 text-xs mt-1">${(drift.reasons || []).join(' · ')}</div>`;
  }

  const histEl = document.getElementById('token-history');
  if (histEl && hist.length) {
    histEl.innerHTML = `<table class="w-full text-xs text-left">
      <thead><tr class="text-gray-500 border-b border-gray-800">
        <th class="py-1 pr-3">Projet</th><th class="pr-3">Tokens</th><th class="pr-3">Coût</th><th>Modèle</th>
      </tr></thead>
      <tbody>
        ${hist.slice().reverse().map(r => `<tr class="border-b border-gray-900 text-gray-300">
          <td class="py-1 pr-3 truncate max-w-32">${r.project_key || '—'}</td>
          <td class="pr-3">${fmtNum(r.total_tokens)}</td>
          <td class="pr-3">$${(r.last_cost || 0).toFixed(4)}</td>
          <td>${(r.top_model || '—').split('-').slice(-2).join('-')}</td>
        </tr>`).join('')}
      </tbody></table>`;
  }
}

async function parseUsage() {
  const text = document.getElementById('parse-input').value;
  const result = await api('POST', '/api/tokens/parse', { text });
  const lines = [];
  if (result.context_percent != null) lines.push(`Contexte : ${result.context_percent}%  (${fmtNum(result.context_used)} / ${fmtNum(result.context_limit)} tokens)`);
  if (result.sonnet_percent != null) lines.push(`Sonnet : ${result.sonnet_percent}%`);
  if (result.haiku_percent != null) lines.push(`Haiku : ${result.haiku_percent}%`);
  if (result.cache_hit != null) lines.push(`Cache hit : ${result.cache_hit}%`);
  lines.push('', '⚠️ ' + (result.warnings || []).join('\n⚠️ '));
  document.getElementById('parse-output').textContent = lines.join('\n');
}

// ── Execute ──────────────────────────────────────────────────────
async function runClaude() {
  const prompt = document.getElementById('exec-prompt').value.trim();
  if (!prompt) return;
  const model = document.getElementById('exec-model').value || null;
  document.getElementById('exec-spinner').classList.remove('hidden');
  document.getElementById('exec-output').textContent = '⏳ Exécution…';
  try {
    const result = await api('POST', '/api/run', { prompt, model });
    document.getElementById('exec-output').textContent = result.output || '(pas de sortie)';
  } catch (e) {
    document.getElementById('exec-output').textContent = `Erreur: ${e.message}`;
  }
  document.getElementById('exec-spinner').classList.add('hidden');
}

// ── Memory ────────────────────────────────────────────────────────
async function loadMemory(key) {
  currentMemKey = key;
  document.querySelectorAll('.memory-btn').forEach(b => b.classList.remove('active-mem'));
  const btn = document.getElementById(`mem-btn-${key}`);
  if (btn) btn.classList.add('active-mem');
  try {
    const data = await api('GET', `/api/memory/${key}`);
    document.getElementById('mem-editor').value = data.content || '';
    document.getElementById('mem-current-name').textContent = data.path.split('/').pop();
    document.getElementById('mem-meta').textContent = `${data.content.length} caractères · ${data.path}`;
  } catch (e) {
    document.getElementById('mem-editor').value = `Erreur: ${e.message}`;
  }
}

async function saveMemory() {
  const content = document.getElementById('mem-editor').value;
  try {
    const result = await api('PUT', `/api/memory/${currentMemKey}`, { content });
    document.getElementById('mem-meta').textContent = `✅ Sauvegardé — ${result.size} caractères`;
  } catch (e) {
    alert(`Erreur sauvegarde: ${e.message}`);
  }
}

// ── History ───────────────────────────────────────────────────────
async function historyAction(action) {
  document.getElementById('history-spinner').classList.remove('hidden');
  document.getElementById('history-output').textContent = '⏳ En cours…';
  try {
    const result = await api('POST', `/api/history/${action}`);
    document.getElementById('history-output').textContent = JSON.stringify(result, null, 2);
  } catch (e) {
    document.getElementById('history-output').textContent = `Erreur: ${e.message}`;
  }
  document.getElementById('history-spinner').classList.add('hidden');
}

// ── Optimization ──────────────────────────────────────────────────
async function runOptimizer() {
  document.getElementById('optimize-spinner').classList.remove('hidden');
  document.getElementById('optimize-output').innerHTML = '';
  try {
    const data = await api('GET', '/api/optimization');
    renderOptimization(data);
  } catch (e) {
    document.getElementById('optimize-output').innerHTML = `<div class="card text-red-400">Erreur: ${e.message}</div>`;
  }
  document.getElementById('optimize-spinner').classList.add('hidden');
}

function renderOptimization(data) {
  const el = document.getElementById('optimize-output');
  const sections = [
    { title: '📊 Score', items: [`Score : ${data.optimization_score}/100`, `Confiance : ${data.history_confidence}`] },
    { title: '⚠️ Avertissements', items: data.warnings },
    { title: '✅ Recommandations', items: data.recommendations },
    { title: '🧠 Modèle', items: data.model_advice },
    { title: '🔌 MCP', items: data.mcp_advice },
    { title: '🔄 Workflow', items: data.workflow_advice },
    { title: '🏷️ Technologies', items: data.detected_technologies },
    { title: '🎯 Intents', items: data.detected_intents },
  ];
  el.innerHTML = sections.filter(s => s.items && s.items.length).map(s => `
    <div class="card">
      <div class="card-title mb-2">${s.title}</div>
      <ul class="space-y-1 text-sm text-gray-300">
        ${s.items.map(i => `<li class="flex gap-2"><span class="text-gray-500 shrink-0">·</span>${i}</li>`).join('')}
      </ul>
    </div>`).join('');
}

// ── Watcher actions ───────────────────────────────────────────────
async function autonomousMode() {
  const prompt = window.prompt('Prompt pour le mode autonome (vide = ouvrir terminal):', '') || null;
  await api('POST', '/api/watcher/autonomous', { prompt });
}

async function cancelRestart() {
  await api('POST', '/api/watcher/cancel');
}

// ── Mobile / notify ───────────────────────────────────────────────
async function sendNotify() {
  const title = document.getElementById('n-title').value.trim() || 'CC';
  const message = document.getElementById('n-msg').value.trim() || '—';
  await api('POST', '/api/notify', { title, message });
}

async function sendNotifyMobile() {
  const title = document.getElementById('mob-title').value.trim() || 'CC';
  const message = document.getElementById('mob-msg').value.trim() || '—';
  const priority = parseInt(document.getElementById('mob-priority').value);
  await api('POST', '/api/notify', { title, message, priority });
}

async function simulateCmd() {
  const msg = document.getElementById('sim-cmd').value.trim();
  if (!msg) return;
  await api('POST', '/api/commands/simulate', { message: msg });
  setTimeout(refreshCommands, 500);
}

async function refreshCommands() {
  try {
    const data = await api('GET', '/api/commands');
    const el = document.getElementById('cmd-history');
    if (!el) return;
    if (!data.commands.length) { el.innerHTML = '<div class="text-gray-500 text-xs">Aucune commande</div>'; return; }
    el.innerHTML = data.commands.map(c => `
      <div class="flex gap-2 text-xs border-b border-gray-800 py-1">
        <span class="text-gray-500 shrink-0">${c.ts.slice(11,19)}</span>
        <span class="text-cyan-300">${c.msg}</span>
      </div>`).join('');
  } catch {}
}

// ── Logs ─────────────────────────────────────────────────────────
function appendLog(msg) {
  const box = document.getElementById('log-box');
  if (!box) return;
  const line = document.createElement('div');
  const lower = msg.toLowerCase();
  let cls = 'log-dim';
  if (lower.includes('error') || lower.includes('erreur')) cls = 'log-error';
  else if (lower.includes('warn') || lower.includes('avert')) cls = 'log-warn';
  else if (lower.includes('ok') || lower.includes('démarr') || lower.includes('✅')) cls = 'log-ok';
  else if (lower.includes('[watcher]') || lower.includes('[mobile]') || lower.includes('[server]')) cls = 'log-info';
  line.className = cls;
  line.textContent = `${new Date().toLocaleTimeString('fr')} ${msg}`;
  box.appendChild(line);
  // Keep last 500 lines
  while (box.children.length > 500) box.removeChild(box.firstChild);
  if (document.getElementById('log-autoscroll')?.checked) {
    box.scrollTop = box.scrollHeight;
  }
}

function clearLogs() {
  const box = document.getElementById('log-box');
  if (box) box.innerHTML = '';
}

// ── Helpers ───────────────────────────────────────────────────────
function setEl(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

function fmtNum(n) {
  if (!n && n !== 0) return '—';
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
  return String(n);
}

// ── Init ──────────────────────────────────────────────────────────
connectWs();

// Auto-refresh tokens every 30s
setInterval(refreshTokens, 30000);
// Auto-refresh commands every 10s when on mobile tab
setInterval(() => {
  if (!document.getElementById('tab-mobile').classList.contains('hidden')) {
    refreshCommands();
  }
}, 10000);

// Initial loads
refreshTokens();
