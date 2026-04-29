// ── State ────────────────────────────────────────────────────────
let ws = null;
let currentMemKey = 'claude_md';
let tokenData = null;
let term = null;
let fitAddon = null;
let termInitialized = false;

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
      if (msg.type === 'terminal_output' && term) term.write(msg.data);
      if (msg.type === 'terminal_state') termUpdateState(msg.state, msg.reset_at);
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
    if (btn.dataset.tab === 'usage') refreshUsage();
    if (btn.dataset.tab === 'terminal') termInit();
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

  // Claude usage mini-dashboard card
  const cu = data.claude_usage || {};
  setEl('d-plan-type', (cu.subscription || '—').toUpperCase());
  setEl('d-plan-tier', cu.rate_limit_tier || '');
  setEl('d-today-msgs', fmtNum((cu.today || {}).messageCount));
  setEl('d-today-sessions', fmtNum((cu.today || {}).sessionCount));
  const usageStatusEl = document.getElementById('d-usage-status');
  if (usageStatusEl) {
    if (cu.rate_limited) {
      usageStatusEl.innerHTML = `<span class="text-red-400">Quota atteint</span>${cu.reset_at ? ` · Reset ${cu.reset_at}` : ''}${cu.remaining ? ` · dans ${cu.remaining}` : ''}`;
    } else {
      usageStatusEl.textContent = 'Quota disponible';
    }
  }
}

// ── Usage ─────────────────────────────────────────────────────────
async function refreshUsage() {
  try {
    const data = await api('GET', '/api/claude-usage');
    renderUsage(data);
  } catch (e) {
    console.error('Usage refresh error:', e);
  }
}

function renderUsage(data) {
  const plan = data.plan || {};
  const today = data.today || {};
  const recent = data.recent || {};

  // Plan section
  setEl('u-plan-type', (plan.subscription_type || '—').toUpperCase());
  setEl('u-plan-tier', plan.rate_limit_tier || '');
  const detailsEl = document.getElementById('u-plan-details');
  if (detailsEl) {
    const rows = [
      ['Email', plan.email],
      ['Nom', plan.display_name],
      ['Rôle', plan.org_role],
      ['Token OAuth valide', plan.oauth_valid ? '✅ Oui' : '⚠️ Expiré'],
      ['Expiry OAuth', plan.oauth_expires],
      ['Extra usage', plan.has_extra_usage ? '✅ Activé' : '🔒 Désactivé'],
      ['Source données', plan.api_cost],
    ];
    detailsEl.innerHTML = rows.filter(([,v]) => v != null).map(([k, v]) =>
      `<div class="flex justify-between border-b border-gray-800 py-1 text-xs">
        <span class="text-gray-500">${k}</span>
        <span class="text-gray-300">${v}</span>
       </div>`
    ).join('');
  }

  // Today
  setEl('u-today-msgs', fmtNum(today.messageCount));
  setEl('u-today-sessions', fmtNum(today.sessionCount));
  setEl('u-today-tools', fmtNum(today.toolCallCount));

  // Quota status
  const stateEl = document.getElementById('u-quota-state');
  const resetEl = document.getElementById('u-quota-reset');
  const remainEl = document.getElementById('u-quota-remaining');
  if (stateEl) {
    if (data.rate_limited) {
      stateEl.innerHTML = '<span class="text-red-400">🚫 Quota atteint</span>';
      if (resetEl) resetEl.textContent = data.reset_at ? `Reset : ${data.reset_at}` : '';
      if (remainEl) remainEl.textContent = data.remaining_until_reset ? `Reste : ${data.remaining_until_reset}` : '';
    } else {
      stateEl.innerHTML = '<span class="text-green-400">✅ Disponible</span>';
      if (resetEl) resetEl.textContent = '';
      if (remainEl) remainEl.textContent = data.reset_at ? `Prochain reset : ${data.reset_at}` : '';
    }
  }
  const extraEl = document.getElementById('u-extra-usage');
  if (extraEl) {
    extraEl.textContent = plan.has_extra_usage
      ? 'Extra usage activé (tokens supplémentaires payants disponibles)'
      : 'Extra usage désactivé — uniquement le forfait standard';
  }

  // Activity chart (7 days)
  const chartEl = document.getElementById('u-activity-chart');
  const days = (recent.days || []).slice().reverse().slice(0, 7).reverse();
  if (chartEl && days.length) {
    const maxMsgs = Math.max(...days.map(d => d.messageCount || 0), 1);
    chartEl.innerHTML = days.map(d => {
      const pct = Math.round(((d.messageCount || 0) / maxMsgs) * 100);
      const isToday = d.date === new Date().toISOString().slice(0, 10);
      return `<div class="flex items-center gap-3 text-sm">
        <span class="text-gray-500 font-mono text-xs w-24 shrink-0">${d.date}${isToday ? ' <span class="text-violet-400">auj.</span>' : ''}</span>
        <div class="flex-1 bg-gray-800 rounded-full h-3 overflow-hidden">
          <div class="h-full rounded-full ${isToday ? 'bg-violet-500' : 'bg-blue-600'}" style="width:${pct}%"></div>
        </div>
        <span class="text-gray-300 w-20 text-right text-xs">${fmtNum(d.messageCount)} msg · ${d.sessionCount || 0} sess</span>
      </div>`;
    }).join('');
  }

  // 30-day table
  const tableEl = document.getElementById('u-activity-table');
  const allDays = (recent.days || []).slice().reverse();
  if (tableEl && allDays.length) {
    tableEl.innerHTML = `<table class="w-full text-xs text-left">
      <thead><tr class="text-gray-500 border-b border-gray-800">
        <th class="py-1 pr-4">Date</th><th class="pr-4">Messages</th><th class="pr-4">Sessions</th><th>Tool calls</th>
      </tr></thead>
      <tbody>${allDays.map(d => `
        <tr class="border-b border-gray-900 text-gray-300">
          <td class="py-1 pr-4 font-mono">${d.date}</td>
          <td class="pr-4">${fmtNum(d.messageCount)}</td>
          <td class="pr-4">${d.sessionCount || 0}</td>
          <td>${fmtNum(d.toolCallCount)}</td>
        </tr>`).join('')}
      </tbody></table>`;
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

// ── Terminal (xterm.js) ───────────────────────────────────────────
function termInit() {
  if (termInitialized) { if (fitAddon) fitAddon.fit(); return; }
  termInitialized = true;

  term = new Terminal({
    theme: { background: '#000000', foreground: '#f0f0f0', cursor: '#ffffff', selectionBackground: '#444' },
    fontFamily: '"Cascadia Code", "Fira Mono", "Consolas", monospace',
    fontSize: 13,
    lineHeight: 1.2,
    convertEol: false,
    scrollback: 3000,
    cursorBlink: true,
  });

  fitAddon = new FitAddon.FitAddon();
  term.loadAddon(fitAddon);
  term.open(document.getElementById('terminal-container'));
  fitAddon.fit();

  // Raw keystrokes → backend PTY
  term.onData(data => {
    api('POST', '/api/terminal/write', { data }).catch(() => {});
  });

  // Re-fit on container resize
  const ro = new ResizeObserver(() => { if (fitAddon) fitAddon.fit(); });
  ro.observe(document.getElementById('terminal-container'));

  term.writeln('\x1b[90m[Terminal prêt — cliquez sur ▶ Démarrer pour lancer Claude]\x1b[0m');

  // Sync current state
  api('GET', '/api/terminal/status').then(s => termUpdateState(s.state, null)).catch(() => {});
}

function termUpdateState(state, reset_at) {
  const badge = document.getElementById('term-state-badge');
  if (!badge) return;
  const labels = { idle: 'idle', running: '🟢 En cours', rate_limited: '🚫 Rate limit', dead: '💀 Arrêté', waiting: '⏳ Attente' };
  const colors  = {
    running:      'bg-green-900 text-green-300',
    rate_limited: 'bg-red-900 text-red-300',
    dead:         'bg-gray-700 text-gray-400',
    idle:         'bg-gray-700 text-gray-300',
    waiting:      'bg-amber-900 text-amber-300',
  };
  badge.textContent = labels[state] || state;
  badge.className = `px-2 py-0.5 rounded text-xs font-semibold ${colors[state] || 'bg-gray-700 text-gray-300'}`;
  if (term && state === 'rate_limited') {
    term.write(`\r\n\x1b[31m[RATE LIMIT ATTEINT${reset_at ? ` — Reset: ${reset_at}` : ''}]\x1b[0m\r\n`);
  }
  if (term && state === 'dead') {
    term.write('\r\n\x1b[90m[Session terminée]\x1b[0m\r\n');
  }
}

async function termStart() {
  const autonomous = document.getElementById('term-autonomous')?.checked || false;
  try {
    const r = await api('POST', '/api/terminal/start', { autonomous });
    if (r.error && term) term.write(`\r\n\x1b[33m[${r.error}]\x1b[0m\r\n`);
  } catch (e) {
    if (term) term.write(`\r\n\x1b[31m[Erreur démarrage: ${e.message}]\x1b[0m\r\n`);
  }
}

async function termStop() {
  try { await api('POST', '/api/terminal/stop'); } catch {}
}

async function termInterrupt() {
  try { await api('POST', '/api/terminal/interrupt'); } catch {}
}

function termSend() {
  const input = document.getElementById('term-input');
  if (!input || !input.value) return;
  api('POST', '/api/terminal/send', { text: input.value }).catch(() => {});
  input.value = '';
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

// ── Tab helper (programmatic switch) ─────────────────────────────
function switchTab(name) {
  const btn = document.querySelector(`.nav-btn[data-tab="${name}"]`);
  if (btn) btn.click();
}

// ── Init ──────────────────────────────────────────────────────────
connectWs();

// Auto-refresh tokens every 30s
setInterval(refreshTokens, 30000);
// Auto-refresh usage every 60s
setInterval(() => {
  if (!document.getElementById('tab-usage').classList.contains('hidden')) {
    refreshUsage();
  }
}, 60000);
// Auto-refresh commands every 10s when on mobile tab
setInterval(() => {
  if (!document.getElementById('tab-mobile').classList.contains('hidden')) {
    refreshCommands();
  }
}, 10000);

// Initial loads
refreshTokens();
