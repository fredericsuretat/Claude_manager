// ── State ────────────────────────────────────────────────────────
let ws = null;
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
    if (btn.dataset.tab === 'memory') memexInit();
    if (btn.dataset.tab === 'mobile') { refreshCommands(); refreshScheduled(); }
    if (btn.dataset.tab === 'usage') refreshUsage();
    if (btn.dataset.tab === 'terminal') termInit();
    if (btn.dataset.tab === 'service') refreshService();
    if (btn.dataset.tab === 'mcp') mcpRefresh();
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

  // ── Usage bar (header) ──────────────────────────────────────────
  updateUsageBar(data.claude_usage || {});
  if ((data.claude_usage || {}).live) renderLiveUsage(data.claude_usage.live);

  // Memory CC economy card
  refreshDashboardMemexCard();

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

// ── Usage bar (header) ───────────────────────────────────────────
function updateUsageBar(cu) {
  const label   = document.getElementById('usage-bar-label');
  const fill    = document.getElementById('usage-bar-fill');
  const right   = document.getElementById('usage-bar-right');
  if (!label || !fill || !right) return;

  const live = cu.live || {};
  const sessionPct = live.session_pct != null ? live.session_pct : null;
  const weekPct    = live.week_pct;
  const resetStr   = live.session_reset_str;

  if (cu.rate_limited || sessionPct === 100) {
    label.textContent = '🚫 Quota atteint';
    label.className = 'text-red-400 shrink-0 w-36 font-semibold';
    fill.style.width = '100%';
    fill.className = 'h-full rounded-full bg-red-500 transition-all duration-500';
    const resetInfo = resetStr || cu.reset_at;
    right.textContent = resetInfo ? `Reset ${resetInfo}` : '';
    right.className = 'text-red-400 shrink-0 text-right min-w-24';
  } else if (sessionPct != null) {
    const color = sessionPct >= 80 ? 'bg-red-500' : sessionPct >= 60 ? 'bg-amber-500' : 'bg-green-500';
    const weekInfo = weekPct != null ? ` · sem. ${weekPct}%` : '';
    label.textContent = `Session ${sessionPct}%${weekInfo}`;
    label.className = 'text-gray-300 shrink-0 w-36';
    fill.style.width = `${sessionPct}%`;
    fill.className = `h-full rounded-full ${color} transition-all duration-500`;
    right.textContent = resetStr ? `↺ ${resetStr}` : '';
    right.className = 'text-gray-500 shrink-0 text-right min-w-24';
  } else {
    // Aucune donnée live — indiquer l'action à faire
    const plan = (cu.subscription || 'pro').toUpperCase();
    label.textContent = `${plan} · /usage ?`;
    label.className = 'text-gray-600 shrink-0 w-36 cursor-pointer hover:text-gray-400';
    label.title = 'Lance /usage dans le terminal pour voir le quota en temps réel';
    fill.style.width = '0%';
    fill.className = 'h-full rounded-full bg-gray-800 transition-all duration-500';
    right.textContent = '';
  }
}

// ── Usage ─────────────────────────────────────────────────────────
async function refreshUsage() {
  try {
    const [data, live] = await Promise.all([
      api('GET', '/api/claude-usage'),
      api('GET', '/api/claude-usage/live').catch(() => null),
    ]);
    renderUsage(data);
    if (live) renderLiveUsage(live);
  } catch (e) {
    console.error('Usage refresh error:', e);
  }
}

async function refreshLiveUsage() {
  const el = document.getElementById('u-live-content');
  if (el) el.innerHTML = '<div class="text-amber-400 text-sm">⏳ Envoi /usage au terminal… (résultat dans ~3s)</div>';
  try {
    const r = await api('POST', '/api/claude-usage/live/refresh');
    if (!r.triggered) {
      if (el) el.innerHTML = '<div class="text-amber-400 text-sm">⚠️ Terminal inactif — démarre une session dans l\'onglet Terminal puis retente.</div>';
      return;
    }
    // Attendre ~3s que le résultat arrive via le flux PTY
    await new Promise(res => setTimeout(res, 3000));
    const live = await api('GET', '/api/claude-usage/live');
    renderLiveUsage(live);
    updateUsageBar({ live, rate_limited: false });
  } catch (e) {
    if (el) el.innerHTML = `<div class="text-red-400 text-sm">Erreur: ${e.message}</div>`;
  }
}

function renderLiveUsage(live) {
  const el = document.getElementById('u-live-content');
  if (!el) return;

  if (!live || live.error || (live.session_pct == null && live.week_pct == null)) {
    el.innerHTML = '<div class="text-gray-500 text-sm">Données non disponibles — cliquez sur Capturer.</div>';
    return;
  }

  const sPct = live.session_pct;
  const wPct = live.week_pct;
  const sColor = sPct >= 80 ? 'bg-red-500' : sPct >= 60 ? 'bg-amber-500' : 'bg-green-500';
  const wColor = wPct >= 80 ? 'bg-red-500' : wPct >= 60 ? 'bg-amber-500' : 'bg-green-500';
  const capturedAt = live.captured_at ? new Date(live.captured_at).toLocaleTimeString('fr') : '?';

  el.innerHTML = `
    <div class="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-3">
      <div>
        <div class="flex justify-between text-sm mb-1">
          <span class="text-gray-400">Session (fenêtre 5h)</span>
          <span class="font-bold ${sPct >= 80 ? 'text-red-400' : sPct >= 60 ? 'text-amber-400' : 'text-green-400'}">${sPct != null ? sPct + '%' : '—'}</span>
        </div>
        <div class="h-3 bg-gray-800 rounded-full overflow-hidden">
          <div class="h-full rounded-full ${sColor}" style="width:${sPct ?? 0}%"></div>
        </div>
        ${live.session_reset_str ? `<div class="text-xs text-gray-500 mt-1">↺ Reset ${live.session_reset_str}</div>` : ''}
      </div>
      <div>
        <div class="flex justify-between text-sm mb-1">
          <span class="text-gray-400">Semaine</span>
          <span class="font-bold ${wPct >= 80 ? 'text-red-400' : wPct >= 60 ? 'text-amber-400' : 'text-green-400'}">${wPct != null ? wPct + '%' : '—'}</span>
        </div>
        <div class="h-3 bg-gray-800 rounded-full overflow-hidden">
          <div class="h-full rounded-full ${wColor ?? 'bg-gray-700'}" style="width:${wPct ?? 0}%"></div>
        </div>
        ${live.week_reset_str ? `<div class="text-xs text-gray-500 mt-1">↺ Reset ${live.week_reset_str}</div>` : ''}
      </div>
    </div>
    <div class="text-xs text-gray-600">Capturé à ${capturedAt} · source: ${live.source || 'pty'}</div>`;
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
    // Token summary + model in header
    if (snap.total_tokens) {
      document.getElementById('token-summary').textContent = `${fmtNum(snap.total_tokens)} tok · $${(snap.last_cost || 0).toFixed(4)}`;
    }
    if (snap.top_model) {
      const modelEl = document.getElementById('header-model');
      if (modelEl) {
        const short = snap.top_model.replace('claude-', '').replace(/-\d{8}$/, '');
        modelEl.textContent = short;
      }
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
  const skip_permissions = document.getElementById('exec-skip-perms')?.checked || false;
  document.getElementById('exec-spinner').classList.remove('hidden');
  document.getElementById('exec-output').textContent = '⏳ Exécution…';
  try {
    const result = await api('POST', '/api/run', { prompt, model, skip_permissions });
    document.getElementById('exec-output').textContent = result.output || '(pas de sortie)';
  } catch (e) {
    document.getElementById('exec-output').textContent = `Erreur: ${e.message}`;
  }
  document.getElementById('exec-spinner').classList.add('hidden');
}

// ── Memory Control Center ─────────────────────────────────────────
const memex = {
  tree: null,                  // dernière réponse /tree
  currentRoot: null,
  currentRel: null,
  currentMtime: null,
  dirty: false,
  collapsed: new Set(),        // root ids repliés
  graph: null,                 // instance vis Network
  graphLoaded: false,
  initialized: false,
  typeFilter: '',              // '' | 'user' | 'feedback' | 'project' | 'reference'
};

const MEMEX_TYPE_ICONS = {
  user: '👤', feedback: '⚠', project: '📦', reference: '🔗',
};

function memexFilterType(btn, type) {
  memex.typeFilter = type;
  document.querySelectorAll('.memex-type-btn').forEach(b => b.classList.toggle('active', b === btn));
  memexRenderTree();
}

async function memexInit() {
  if (!memex.initialized) {
    memex.initialized = true;
    await memexRefreshAll();
    memexRefreshLiveStats();
    // refresh live stats toutes les 5s tant que l'onglet est ouvert
    setInterval(() => {
      if (!document.getElementById('tab-memory').classList.contains('hidden')) {
        memexRefreshLiveStats();
      }
    }, 5000);
  }
}

// Dashboard widget — minimal periodic refresh
let _memexDashLast = 0;
async function refreshDashboardMemexCard() {
  // Throttle to once every 4s (called every status refresh which happens often)
  if (Date.now() - _memexDashLast < 4000) return;
  _memexDashLast = Date.now();
  try {
    const stats = await api('GET', '/api/memory-explorer/stats-live');
    const savedEl = document.getElementById('d-memex-saved');
    const callsEl = document.getElementById('d-memex-calls');
    const ratioEl = document.getElementById('d-memex-ratio');
    if (!savedEl) return;
    savedEl.textContent = (stats.tokens_saved || 0).toLocaleString('fr-FR');
    callsEl.textContent = stats.total_calls || 0;
    const full = (stats.tokens_saved || 0) + (stats.tokens_actual || 0);
    const ratio = full > 0 ? Math.round((stats.tokens_saved / full) * 100) : 0;
    ratioEl.textContent = full > 0 ? `${ratio}%` : '—';
    // Optionally load heatmap top-3
    const hm = await api('GET', '/api/memory-explorer/heatmap?limit=3').catch(() => null);
    const topEl = document.getElementById('d-memex-top');
    if (topEl && hm && hm.items && hm.items.length) {
      topEl.innerHTML = '🔥 Top: ' + hm.items.map(it => `<span class="text-gray-300">${escapeHtml(it.rel)}</span> (${it.calls})`).join(' · ');
    } else if (topEl) {
      topEl.textContent = '';
    }
  } catch (e) {}
}

async function memexRefreshLiveStats() {
  try {
    const d = await api('GET', '/api/memory-explorer/stats-live');
    document.getElementById('memex-live-saved').textContent =
      d.tokens_saved.toLocaleString('fr-FR');
    document.getElementById('memex-live-calls').textContent = d.total_calls;
    const parts = Object.entries(d.calls || {}).map(([k, v]) => `${k}:${v}`).join(' · ');
    document.getElementById('memex-live-breakdown').textContent = parts || '(aucun appel)';
  } catch (e) {}
}

async function memexResetStats() {
  if (!confirm('Reset les compteurs ?')) return;
  await api('POST', '/api/memory-explorer/stats-live/reset');
  memexRefreshLiveStats();
}

async function memexRefreshAll() {
  try {
    await api('POST', '/api/memory-explorer/refresh');
  } catch (e) {}
  await memexLoadTree();
  memexLoadStats();
}

async function memexLoadStats() {
  try {
    const s = await api('GET', '/api/memory-explorer/stats');
    document.getElementById('memex-stats').textContent =
      `${s.total_files} fichiers · ${s.roots_count} sources · ${(s.total_size/1024).toFixed(1)} KiB`;
  } catch (e) {
    document.getElementById('memex-stats').textContent = '—';
  }
}

async function memexLoadTree() {
  try {
    memex.tree = await api('GET', '/api/memory-explorer/tree');
    memexRenderTree();
  } catch (e) {
    document.getElementById('memex-tree').innerHTML =
      `<div class="text-red-400 text-sm p-2">Erreur: ${e.message}</div>`;
  }
}

function memexRenderTree() {
  const filter = (document.getElementById('memex-tree-filter')?.value || '').toLowerCase();
  const typeFilter = memex.typeFilter || '';
  const data = memex.tree;
  const el = document.getElementById('memex-tree');
  if (!data || !data.roots) { el.textContent = 'Aucune donnée'; return; }
  const parts = [];
  for (const root of data.roots) {
    let matchedFiles = root.files;
    if (typeFilter) {
      matchedFiles = matchedFiles.filter(f => (f.type || '') === typeFilter);
    }
    if (filter) {
      matchedFiles = matchedFiles.filter(f =>
        f.rel.toLowerCase().includes(filter) ||
        root.label.toLowerCase().includes(filter));
    }
    if ((filter || typeFilter) && matchedFiles.length === 0) continue;
    const collapsed = memex.collapsed.has(root.id);
    parts.push(`<div class="memex-root ${collapsed ? 'collapsed' : ''}" data-root="${root.id}">`);
    parts.push(`<div class="memex-root-header" onclick="memexToggleRoot('${escapeAttr(root.id)}')">
        <span>${collapsed ? '▸' : '▾'} ${escapeHtml(root.label)}</span>
        <span class="memex-root-count">${matchedFiles.length}</span>
      </div>`);
    parts.push('<div class="memex-root-files">');
    for (const f of matchedFiles) {
      const active = (memex.currentRoot === root.id && memex.currentRel === f.rel) ? 'active' : '';
      const idx = f.is_index ? 'is-index' : '';
      const dt = new Date(f.mtime * 1000);
      const dateStr = `${dt.getMonth()+1}/${dt.getDate()}`;
      const typeBadge = f.type
        ? `<span class="memex-type-badge type-${f.type}" title="${escapeAttr(f.type)}">${MEMEX_TYPE_ICONS[f.type] || '·'}</span>`
        : '';
      parts.push(`<div class="memex-file ${active} ${idx}"
            onclick="memexOpenFile('${escapeAttr(root.id)}', '${escapeAttr(f.rel)}')"
            title="${escapeAttr(f.rel)}${f.type ? ' · type=' + f.type : ''}">
          ${typeBadge}<span class="memex-file-name">${escapeHtml(f.rel)}</span>
          <span class="memex-file-meta">${(f.size/1024).toFixed(1)}K · ${dateStr}</span>
        </div>`);
    }
    parts.push('</div></div>');
  }
  el.innerHTML = parts.join('') || '<div class="text-gray-500 p-4 text-center">Rien à afficher</div>';
}

function memexToggleRoot(rootId) {
  if (memex.collapsed.has(rootId)) memex.collapsed.delete(rootId);
  else memex.collapsed.add(rootId);
  memexRenderTree();
}

async function memexOpenFile(rootId, rel) {
  if (memex.dirty && !confirm('Modifications non sauvegardées. Continuer ?')) return;
  memexCloseOverlay();
  try {
    const data = await api('GET',
      `/api/memory-explorer/file?root=${encodeURIComponent(rootId)}&rel=${encodeURIComponent(rel)}`);
    if (data.error) { alert(`Erreur: ${data.error}`); return; }
    memex.currentRoot = rootId;
    memex.currentRel = rel;
    memex.currentMtime = data.mtime;
    memex.dirty = false;
    const ed = document.getElementById('memex-editor');
    ed.value = data.content || '';
    ed.disabled = false;
    document.getElementById('memex-current-name').textContent = data.name;
    document.getElementById('memex-current-path').textContent = data.path;
    document.getElementById('memex-current-meta').textContent =
      `${data.content.length} car. · ${(data.size/1024).toFixed(1)} KiB`;
    document.getElementById('memex-save-btn').disabled = false;
    document.getElementById('memex-del-btn').disabled = false;
    document.getElementById('memex-skim-btn').disabled = false;
    document.getElementById('memex-idx-btn').classList.toggle('hidden',
      !(data.name === 'MEMORY.md' || data.name === 'CLAUDE.md'));
    document.getElementById('memex-editor-status').textContent = '';
    // Charge le skim en arrière-plan pour récupérer les headings (TOC)
    api('GET', `/api/memory-explorer/skim?root=${encodeURIComponent(rootId)}&rel=${encodeURIComponent(rel)}`)
      .then(s => memexRenderTOC(s.headings || []))
      .catch(() => memexRenderTOC([]));
    memexRenderTree();
  } catch (e) {
    alert(`Erreur: ${e.message}`);
  }
}

async function memexSaveFile() {
  if (!memex.currentRoot || !memex.currentRel) return;
  const content = document.getElementById('memex-editor').value;
  try {
    const r = await api('PUT', '/api/memory-explorer/file', {
      root: memex.currentRoot, rel: memex.currentRel, content,
    });
    memex.currentMtime = r.mtime;
    memex.dirty = false;
    document.getElementById('memex-editor-status').textContent =
      `✅ Sauvegardé (${r.size} octets) à ${new Date().toLocaleTimeString()}`;
    document.getElementById('memex-current-meta').textContent =
      `${content.length} car. · ${(r.size/1024).toFixed(1)} KiB`;
    memexLoadTree();
  } catch (e) {
    alert(`Erreur sauvegarde: ${e.message}`);
  }
}

async function memexDeleteFile() {
  if (!memex.currentRoot || !memex.currentRel) return;
  if (!confirm(`Supprimer ${memex.currentRel} ?`)) return;
  try {
    const r = await api('DELETE',
      `/api/memory-explorer/file?root=${encodeURIComponent(memex.currentRoot)}&rel=${encodeURIComponent(memex.currentRel)}`);
    if (r.ok) {
      memex.currentRoot = null;
      memex.currentRel = null;
      memex.dirty = false;
      const ed = document.getElementById('memex-editor');
      ed.value = '';
      ed.disabled = true;
      document.getElementById('memex-current-name').textContent = 'Aucun fichier sélectionné';
      document.getElementById('memex-current-path').textContent = '';
      document.getElementById('memex-current-meta').textContent = '';
      document.getElementById('memex-save-btn').disabled = true;
      document.getElementById('memex-del-btn').disabled = true;
      memexLoadTree();
    } else {
      alert(`Erreur: ${r.error}`);
    }
  } catch (e) {
    alert(`Erreur: ${e.message}`);
  }
}

async function memexNewFile() {
  if (!memex.tree || !memex.tree.roots.length) return;
  // root selector
  const labels = memex.tree.roots.map((r, i) => `${i}: ${r.label}`).join('\n');
  const idxStr = prompt(`Choisis un root (numéro):\n${labels}`, '0');
  if (idxStr === null) return;
  const idx = parseInt(idxStr, 10);
  if (isNaN(idx) || idx < 0 || idx >= memex.tree.roots.length) return;
  const root = memex.tree.roots[idx];
  const name = prompt('Nom du fichier (ex: notes.md ou docs/sub.md):', 'note.md');
  if (!name || !name.endsWith('.md')) return;
  try {
    const r = await api('POST', '/api/memory-explorer/file', {
      root: root.id, rel: name, content: `# ${name.replace(/\.md$/, '')}\n\n`,
    });
    if (r.ok) {
      await memexLoadTree();
      await memexOpenFile(root.id, name);
    } else {
      alert(`Erreur: ${r.error}`);
    }
  } catch (e) {
    alert(`Erreur: ${e.message}`);
  }
}

function memexCopyPath() {
  const p = document.getElementById('memex-current-path').textContent;
  if (!p) return;
  navigator.clipboard?.writeText(p);
  document.getElementById('memex-editor-status').textContent = `📋 ${p}`;
}

// Sub-tab switching
function memexShowView(view) {
  document.querySelectorAll('.memex-subtab').forEach(b => b.classList.toggle('active', b.dataset.view === view));
  document.querySelectorAll('.memex-view').forEach(v => v.classList.add('hidden'));
  document.getElementById(`memex-view-${view}`).classList.remove('hidden');
  if (view === 'recent') memexLoadRecent();
  if (view === 'graph') memexBuildGraph();
  if (view === 'roadmap') memexLoadRoadmap();
  if (view === 'heatmap') memexLoadHeatmap();
  if (view === 'health') memexLoadHealth();
}

// ── Heatmap panel ─────────────────────────────────────────────────
async function memexLoadHeatmap() {
  const box = document.getElementById('memex-heatmap');
  box.innerHTML = '<div class="text-gray-500 p-2">⏳…</div>';
  try {
    const data = await api('GET', '/api/memory-explorer/heatmap?limit=30');
    if (!data.items || data.items.length === 0) {
      box.innerHTML = `
        <div class="text-gray-500 p-4 text-center">
          <div class="text-3xl mb-2">📊</div>
          <div>Aucune donnée d'usage encore.</div>
          <div class="text-xs mt-2">La heatmap se remplit dès que skim/section/read sont appelés sur des fichiers.</div>
        </div>`;
      return;
    }
    const max = Math.max(...data.items.map(i => i.calls));
    box.innerHTML = data.items.map(it => {
      const pct = max > 0 ? Math.round((it.calls / max) * 100) : 0;
      const endpoints = Object.entries(it.by_endpoint || {})
        .map(([k, v]) => `<span class="memex-heat-badge">${escapeHtml(k)}:${v}</span>`)
        .join('');
      const lastTxt = it.last ? new Date(it.last).toLocaleString() : '—';
      return `
        <div class="memex-heat-item" onclick="memexOpenFile('${escapeAttr(it.root)}','${escapeAttr(it.rel)}')">
          <div class="memex-heat-bar" style="width:${pct}%"></div>
          <div class="memex-heat-content">
            <div class="memex-heat-header">
              <span class="memex-heat-name">${escapeHtml(it.rel)}</span>
              <span class="memex-heat-calls">${it.calls} call${it.calls > 1 ? 's' : ''}</span>
            </div>
            <div class="memex-heat-meta">
              <span class="text-gray-500">${escapeHtml(it.root)}</span>
              <span class="text-green-400">💾 ${it.tokens_saved} tok</span>
            </div>
            <div class="memex-heat-endpoints">${endpoints}</div>
            <div class="memex-heat-last text-gray-600">dernier: ${escapeHtml(lastTxt)}</div>
          </div>
        </div>`;
    }).join('');
  } catch (e) {
    box.innerHTML = `<div class="text-red-400 p-2">Erreur: ${escapeHtml(e.message)}</div>`;
  }
}

// ── Health panel ──────────────────────────────────────────────────
async function memexLoadHealth() {
  const box = document.getElementById('memex-health');
  box.innerHTML = '<div class="text-gray-500 p-2">⏳…</div>';
  try {
    const data = await api('GET', '/api/memory-explorer/index-health');
    const t = data.totals || {};
    let html = `
      <div class="memex-health-summary">
        <div class="memex-health-stat"><span class="lab">Indexes</span> <span class="val">${t.indexes || 0}</span></div>
        <div class="memex-health-stat ${(t.missing||0)>0?'bad':'ok'}"><span class="lab">Liens cassés</span> <span class="val">${t.missing || 0}</span></div>
        <div class="memex-health-stat ${(t.orphans||0)>0?'warn':'ok'}"><span class="lab">Orphelins</span> <span class="val">${t.orphans || 0}</span></div>
      </div>`;
    if (!data.reports || data.reports.length === 0) {
      html += '<div class="text-gray-500 p-4 text-center">Aucun MEMORY.md trouvé.</div>';
    } else {
      html += data.reports.map(r => {
        const status = r.missing_count > 0 ? 'bad' : (r.orphans_count > 0 ? 'warn' : 'ok');
        const missingList = r.missing.length
          ? `<div class="memex-health-list"><b class="text-red-400">Manquants (${r.missing.length}):</b> ${r.missing.map(escapeHtml).join(', ')}</div>` : '';
        const orphansList = r.orphans.length
          ? `<div class="memex-health-list"><b class="text-amber-400">Orphelins (${r.orphans.length}):</b> ${r.orphans.slice(0,15).map(o => `<span class="memex-orphan" onclick="memexOpenFile('${escapeAttr(r.root)}','${escapeAttr(o)}')">${escapeHtml(o)}</span>`).join(' · ')}${r.orphans.length > 15 ? ` <span class="text-gray-500">+${r.orphans.length-15} autres</span>` : ''}</div>` : '';
        return `
          <div class="memex-health-report status-${status}">
            <div class="memex-health-title">
              <span>${escapeHtml(r.root_label || r.root)}</span>
              <span class="text-xs text-gray-500">${escapeHtml(r.index)} · ${r.entries} entries</span>
            </div>
            ${missingList || orphansList ? '' : '<div class="text-green-400 text-xs">✓ Propre</div>'}
            ${missingList}
            ${orphansList}
          </div>`;
      }).join('');
    }
    box.innerHTML = html;
  } catch (e) {
    box.innerHTML = `<div class="text-red-400 p-2">Erreur: ${escapeHtml(e.message)}</div>`;
  }
}

// ── Roadmap panel ─────────────────────────────────────────────────
async function memexLoadRoadmap() {
  const box = document.getElementById('memex-roadmap');
  try {
    const data = await api('GET', '/api/memory-explorer/roadmap');
    box.innerHTML = data.items.map((it, i) => `
      <div class="memex-roadmap-card status-${it.status}" id="rm-card-${it.id}">
        <div class="memex-roadmap-title">
          <span>${escapeHtml(it.title)}</span>
          <span class="memex-roadmap-badge ${it.status}">${it.status}</span>
        </div>
        <div class="memex-roadmap-endpoint">${escapeHtml(it.endpoint)}</div>
        <div class="memex-roadmap-row"><span class="lab">Pourquoi:</span> ${escapeHtml(it.why)}</div>
        <div class="memex-roadmap-row"><span class="lab">Renvoie:</span> ${escapeHtml(it.returns)}</div>
        <div class="memex-roadmap-row"><span class="lab">Économie:</span> <strong class="text-green-400">${escapeHtml(it.savings)}</strong></div>
        <button class="memex-roadmap-test" onclick="memexTestRoadmap('${escapeAttr(it.id)}')">▶ Tester live</button>
        <div class="memex-roadmap-output hidden" id="rm-out-${escapeAttr(it.id)}"></div>
      </div>
    `).join('');
  } catch (e) {
    box.innerHTML = `<div class="text-red-400 p-2">Erreur: ${e.message}</div>`;
  }
}

async function memexTestRoadmap(id) {
  const out = document.getElementById(`rm-out-${id}`);
  out.classList.remove('hidden');
  out.textContent = '⏳…';
  // Choisit un fichier de démo selon le test
  const demoRoot = 'claude:proj:-home-frederic-Documents-Docker';
  const demoRel = 'MEMORY.md';
  try {
    let url, label;
    if (id === 'skim') {
      url = `/api/memory-explorer/skim?root=${encodeURIComponent(demoRoot)}&rel=${encodeURIComponent('project_x402_lab.md')}`;
      label = 'skim(project_x402_lab.md)';
    } else if (id === 'search-meta') {
      url = `/api/memory-explorer/search-meta?q=Docker&limit=5`;
      label = "search-meta('Docker')";
    } else if (id === 'index') {
      url = `/api/memory-explorer/index?root=${encodeURIComponent(demoRoot)}&name=${encodeURIComponent('MEMORY.md')}`;
      label = 'index(MEMORY.md)';
    } else if (id === 'section') {
      url = `/api/memory-explorer/section?root=${encodeURIComponent(demoRoot)}&rel=${encodeURIComponent('project_x402_lab.md')}&heading=${encodeURIComponent('Infrastructure')}`;
      label = "section(project_x402_lab, 'Infrastructure')";
    } else if (id === 'search-headings') {
      url = `/api/memory-explorer/search-headings?q=traefik`;
      label = "search-headings('traefik')";
    } else {
      out.textContent = 'Test non défini';
      return;
    }
    const data = await api('GET', url);
    out.textContent = `→ ${label}\n` + JSON.stringify(data, null, 2);
  } catch (e) {
    out.textContent = `Erreur: ${e.message}`;
  }
}

// ── Skim (peek) ───────────────────────────────────────────────────
function memexShowOverlay(title, body) {
  document.getElementById('memex-overlay-title').textContent = title;
  document.getElementById('memex-overlay-body').textContent = body;
  document.getElementById('memex-overlay').classList.remove('hidden');
}
function memexCloseOverlay() {
  document.getElementById('memex-overlay').classList.add('hidden');
}

async function memexSkimCurrent() {
  if (!memex.currentRoot || !memex.currentRel) return;
  try {
    const data = await api('GET',
      `/api/memory-explorer/skim?root=${encodeURIComponent(memex.currentRoot)}&rel=${encodeURIComponent(memex.currentRel)}`);
    const fm = Object.entries(data.frontmatter || {}).map(([k, v]) => `${k}: ${v}`).join('\n');
    const heads = (data.headings || []).map(h => `${'  '.repeat(h.level-1)}${'#'.repeat(h.level)} ${h.title}`).join('\n');
    const ratio = (data.approx_tokens_full/Math.max(data.approx_tokens_skim,1)).toFixed(1);
    const body = [
      `📄 ${data.name} — ${data.total_lines} lignes, ${(data.size/1024).toFixed(1)} KiB`,
      `💎 Économie: skim ~${data.approx_tokens_skim} tok vs full ~${data.approx_tokens_full} tok (×${ratio})`,
      '',
      '── Frontmatter ──',
      fm || '(aucune)',
      '',
      `── Headings (${(data.headings || []).length}) ──`,
      heads || '(aucun)',
      '',
      '── Preview ──',
      data.preview || '(vide)',
    ].join('\n');
    memexShowOverlay(`👁 Skim · ${data.name}`, body);
    document.getElementById('memex-editor-status').textContent =
      `💎 Skim (×${ratio} économie)`;
    memexRefreshLiveStats();
  } catch (e) {
    memexShowOverlay('Erreur skim', e.message);
  }
}

// ── TOC / sections ────────────────────────────────────────────────
function memexRenderTOC(headings) {
  const tocEl = document.getElementById('memex-toc');
  const listEl = document.getElementById('memex-toc-list');
  if (!headings || headings.length <= 1) {
    tocEl.classList.add('hidden');
    return;
  }
  tocEl.classList.remove('hidden');
  listEl.innerHTML = headings.map(h =>
    `<button class="memex-toc-item level-${h.level}" onclick="memexLoadSection('${escapeAttr(h.title)}')">${escapeHtml(h.title)}</button>`
  ).join('');
}

async function memexLoadSection(heading) {
  if (!memex.currentRoot || !memex.currentRel) return;
  try {
    const data = await api('GET',
      `/api/memory-explorer/section?root=${encodeURIComponent(memex.currentRoot)}&rel=${encodeURIComponent(memex.currentRel)}&heading=${encodeURIComponent(heading)}`);
    if (data.error) {
      memexShowOverlay('Section introuvable', `"${heading}" pas trouvée.\n\nDisponibles:\n${(data.available||[]).map(h => '· '+h).join('\n')}`);
      return;
    }
    document.getElementById('memex-editor').value = data.content;
    const ratio = (data.approx_tokens_full_file / Math.max(data.approx_tokens, 1)).toFixed(1);
    document.getElementById('memex-editor-status').textContent =
      `📑 "${data.heading}" — ~${data.approx_tokens} tok (×${ratio} économie)`;
    memexRefreshLiveStats();
  } catch (e) {
    memexShowOverlay('Erreur section', e.message);
  }
}

// ── Index view (MEMORY.md) ────────────────────────────────────────
async function memexShowIndex() {
  if (!memex.currentRoot) return;
  try {
    const data = await api('GET',
      `/api/memory-explorer/index?root=${encodeURIComponent(memex.currentRoot)}&name=${encodeURIComponent(memex.currentRel || 'MEMORY.md')}`);
    if (data.error) { memexShowOverlay('Index', data.error); return; }
    const lines = [
      `# Index ${data.index_file} — ${data.count} entrées`,
      data.missing.length ? `⚠ ${data.missing.length} liens cassés: ${data.missing.join(', ')}` : '✅ Tous les liens valides',
      data.orphans.length ? `📎 ${data.orphans.length} fichiers orphelins: ${data.orphans.join(', ')}` : '✅ Aucun orphelin',
      '',
      ...data.entries.map(e => `${e.exists ? '✓' : '✗'} [${e.title}](${e.file}) — ${e.hook}`),
    ];
    memexShowOverlay(`📚 Index · ${data.index_file}`, lines.join('\n'));
    memexRefreshLiveStats();
  } catch (e) {
    memexShowOverlay('Erreur index', e.message);
  }
}

// Search — mode = 'full' | 'meta' | 'headings'
async function memexRunSearch(mode = 'full') {
  const q = document.getElementById('memex-search-q').value.trim();
  const box = document.getElementById('memex-search-results');
  if (!q) { box.innerHTML = '<div class="text-gray-500 px-2 py-4 text-center">Tape une requête</div>'; return; }
  box.innerHTML = '<div class="text-amber-400 px-2 py-4 text-center">⏳…</div>';
  const url = ({
    full:     `/api/memory-explorer/search?q=${encodeURIComponent(q)}`,
    meta:     `/api/memory-explorer/search-meta?q=${encodeURIComponent(q)}`,
    headings: `/api/memory-explorer/search-headings?q=${encodeURIComponent(q)}`,
  })[mode];
  const modeLabel = ({full: '🔍 full-text', meta: '📋 meta-only', headings: '🏷 headings'})[mode];
  try {
    const data = await api('GET', url);
    if (!data.results.length) {
      box.innerHTML = `<div class="text-gray-500 px-2 py-4 text-center">Aucun résultat (${modeLabel})</div>`;
      return;
    }
    const head = `<div class="text-xs text-gray-500 px-2 py-1">${data.count} résultats · ${modeLabel} · "${escapeHtml(q)}"</div>`;
    const items = data.results.map(r => {
      const isHeading = mode === 'headings';
      const snippet = mode === 'full'
        ? `<div class="memex-result-snippet">${highlightSnippet(r.snippet, q)}</div>`
        : '';
      const line = isHeading
        ? `<span class="text-amber-300">${'#'.repeat(r.level)} ${escapeHtml(r.heading)}</span>`
        : `<span class="text-gray-600">L${r.line} · ×${r.count}</span>`;
      const clickRel = isHeading
        ? `memexOpenFileAtHeading('${escapeAttr(r.root)}', '${escapeAttr(r.rel)}', '${escapeAttr(r.heading)}')`
        : `memexOpenFile('${escapeAttr(r.root)}', '${escapeAttr(r.rel)}')`;
      return `<div class="memex-result" onclick="${clickRel}">
        <div class="memex-result-head">
          <span class="memex-result-name">${escapeHtml(r.name)} ${line}</span>
          <span class="memex-result-root">${escapeHtml(r.root_label)}</span>
        </div>${snippet}
      </div>`;
    }).join('');
    box.innerHTML = head + items;
    memexRefreshLiveStats();
  } catch (e) {
    box.innerHTML = `<div class="text-red-400 px-2 py-4">Erreur: ${e.message}</div>`;
  }
}

async function memexOpenFileAtHeading(rootId, rel, heading) {
  await memexOpenFile(rootId, rel);
  // charge la section après ouverture
  setTimeout(() => memexLoadSection(heading), 200);
}

function highlightSnippet(text, q) {
  const safe = escapeHtml(text);
  const re = new RegExp(escapeRegex(q), 'gi');
  return safe.replace(re, m => `<mark>${m}</mark>`);
}
function escapeRegex(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function escapeAttr(s) { return escapeHtml(s).replace(/'/g, '&#39;'); }

// Recent
async function memexLoadRecent() {
  const box = document.getElementById('memex-recent');
  box.innerHTML = '<div class="text-gray-500 px-2 py-4 text-center">⏳…</div>';
  try {
    const data = await api('GET', '/api/memory-explorer/recent?limit=50');
    if (!data.files.length) { box.innerHTML = 'Aucun fichier'; return; }
    box.innerHTML = data.files.map(f => `
      <div class="memex-recent-item" onclick="memexOpenFile('${escapeAttr(f.root)}', '${escapeAttr(f.rel)}')">
        <div class="min-w-0 flex-1 mr-2">
          <div class="truncate text-gray-200">${escapeHtml(f.name)}</div>
          <div class="text-xs text-gray-500 truncate">${escapeHtml(f.root_label)} · ${escapeHtml(f.rel)}</div>
        </div>
        <div class="memex-recent-age">${formatAge(f.age_seconds)}</div>
      </div>
    `).join('');
  } catch (e) {
    box.innerHTML = `<div class="text-red-400 p-2">Erreur: ${e.message}</div>`;
  }
}

function formatAge(s) {
  if (s < 60) return 'il y a <1min';
  if (s < 3600) return `il y a ${Math.floor(s/60)}min`;
  if (s < 86400) return `il y a ${Math.floor(s/3600)}h`;
  if (s < 86400*7) return `il y a ${Math.floor(s/86400)}j`;
  return new Date(Date.now() - s*1000).toLocaleDateString();
}

// Graph
async function memexEnsureVis() {
  if (window.vis && window.vis.Network) return true;
  return new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = 'https://cdn.jsdelivr.net/npm/vis-network@9.1.9/standalone/umd/vis-network.min.js';
    s.onload = () => resolve(true);
    s.onerror = () => reject(new Error('Impossible de charger vis-network'));
    document.head.appendChild(s);
  });
}

async function memexBuildGraph() {
  const canvas = document.getElementById('memex-graph-canvas');
  canvas.innerHTML = '<div class="text-gray-500 p-4 text-center text-sm">⏳ Chargement…</div>';
  try {
    await memexEnsureVis();
    const data = await api('GET', '/api/memory-explorer/graph');
    document.getElementById('memex-graph-stats').textContent =
      `${data.nodes.length} fichiers · ${data.edges.length} liens`;
    // Couleur par root
    const rootColors = {};
    const palette = ['#6366f1','#10b981','#f59e0b','#ec4899','#06b6d4','#8b5cf6','#ef4444','#84cc16'];
    let ci = 0;
    const nodes = data.nodes.map(n => {
      if (!(n.root in rootColors)) rootColors[n.root] = palette[ci++ % palette.length];
      const deg = (n.in_degree || 0) + (n.out_degree || 0);
      return {
        id: n.id,
        label: n.label,
        title: `${n.root_label} · ${n.rel}\nin:${n.in_degree} out:${n.out_degree}`,
        color: { background: rootColors[n.root], border: n.is_index ? '#fbbf24' : rootColors[n.root] },
        font: { color: '#e5e7eb', size: 11 },
        shape: n.is_index ? 'star' : 'dot',
        size: 8 + Math.min(deg, 12) * 2,
        _root: n.root, _rel: n.rel,
      };
    });
    const edges = data.edges.map((e, i) => ({
      id: `e${i}`, from: e.from, to: e.to,
      arrows: 'to', color: { color: '#374151', opacity: 0.6 },
    }));
    canvas.innerHTML = '';
    const network = new vis.Network(canvas, { nodes, edges }, {
      physics: { stabilization: { iterations: 150 }, barnesHut: { gravitationalConstant: -3000, springLength: 120 } },
      interaction: { hover: true, tooltipDelay: 200 },
      nodes: { borderWidth: 2 },
    });
    network.on('doubleClick', (params) => {
      if (params.nodes.length) {
        const node = nodes.find(n => n.id === params.nodes[0]);
        if (node) memexOpenFile(node._root, node._rel);
      }
    });
    memex.graph = network;
  } catch (e) {
    canvas.innerHTML = `<div class="text-red-400 p-4 text-sm">Erreur: ${e.message}</div>`;
  }
}

// Track dirty editor
document.addEventListener('DOMContentLoaded', () => {
  const ed = document.getElementById('memex-editor');
  if (ed) {
    ed.addEventListener('input', () => {
      if (memex.currentRoot && !memex.dirty) {
        memex.dirty = true;
        document.getElementById('memex-editor-status').textContent = '● Modifié';
      }
    });
    // Ctrl+S save
    ed.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        memexSaveFile();
      }
    });
  }
});

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

  // Sync PTY dimensions when xterm resizes
  term.onResize(({ rows, cols }) => {
    api('POST', '/api/terminal/resize', { rows, cols }).catch(() => {});
  });

  // Re-fit and notify backend on container resize
  const ro = new ResizeObserver(() => {
    if (fitAddon) fitAddon.fit(); // triggers term.onResize automatically
  });
  ro.observe(document.getElementById('terminal-container'));

  // Sync current state and show appropriate message
  api('GET', '/api/terminal/status').then(s => {
    termUpdateState(s.state, null);
    if (s.alive) {
      term.writeln(`\x1b[32m[Session déjà active — PID ${s.pid} — en attente de sortie…]\x1b[0m`);
    } else {
      term.writeln('\x1b[90m[Terminal prêt — cliquez sur ▶ Démarrer pour lancer Claude]\x1b[0m');
    }
  }).catch(() => {
    term.writeln('\x1b[90m[Terminal prêt — cliquez sur ▶ Démarrer pour lancer Claude]\x1b[0m');
  });
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
  const rows = term ? term.rows : 40;
  const cols = term ? term.cols : 220;
  try {
    const status = await api('GET', '/api/terminal/status');
    if (status.alive) {
      await api('POST', '/api/terminal/stop');
      if (term) term.write('\r\n\x1b[90m[Session précédente arrêtée]\x1b[0m\r\n');
      await new Promise(r => setTimeout(r, 500));
    }
    const r = await api('POST', '/api/terminal/start', { autonomous, rows, cols });
    if (!r.ok && r.error && term) term.write(`\r\n\x1b[33m[${r.error}]\x1b[0m\r\n`);
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

async function scheduleNotif() {
  const title = document.getElementById('sched-title').value.trim() || 'CC';
  const message = document.getElementById('sched-msg').value.trim();
  const date = document.getElementById('sched-date').value;
  const time = document.getElementById('sched-time').value;
  const priority = parseInt(document.getElementById('sched-priority').value);
  if (!message || !date || !time) { alert('Remplis message, date et heure.'); return; }
  const at = `${date}T${time}:00`;
  try {
    await api('POST', '/api/notifications/schedule', { title, message, at, priority });
    document.getElementById('sched-msg').value = '';
    await refreshScheduled();
  } catch (e) { alert(`Erreur: ${e.message}`); }
}

async function refreshScheduled() {
  try {
    const data = await api('GET', '/api/notifications/scheduled');
    const el = document.getElementById('sched-list');
    if (!el) return;
    if (!data.notifications.length) { el.innerHTML = '<div class="text-gray-600">Aucune</div>'; return; }
    el.innerHTML = data.notifications.map((n, i) => {
      const dt = new Date(n.at).toLocaleString('fr', { dateStyle:'short', timeStyle:'short' });
      return `<div class="flex justify-between items-center border-b border-gray-800 py-1 gap-2">
        <div>
          <span class="text-gray-300 font-semibold">${n.title}</span>
          <span class="text-gray-500 ml-2">${n.message.slice(0, 40)}${n.message.length > 40 ? '…' : ''}</span>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <span class="text-violet-400">${dt}</span>
          <button onclick="deleteScheduled(${i})" class="text-red-500 hover:text-red-300 text-xs">✕</button>
        </div>
      </div>`;
    }).join('');
  } catch {}
}

async function deleteScheduled(idx) {
  try {
    await api('DELETE', `/api/notifications/scheduled/${idx}`);
    await refreshScheduled();
  } catch (e) { alert(`Erreur: ${e.message}`); }
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

// ── Service management ───────────────────────────────────────────
function _svcBadge(active) {
  const badge = document.getElementById('svc-badge');
  if (!badge) return;
  badge.textContent = active ? '🟢 Actif' : '🔴 Arrêté';
  badge.className = `px-2 py-0.5 rounded text-xs font-semibold ${active ? 'bg-green-900 text-green-300' : 'bg-red-900 text-red-300'}`;
}

async function refreshService() {
  try {
    const [status] = await Promise.all([
      api('GET', '/api/service/status'),
      refreshServiceLogs(),
    ]);
    _svcBadge(status.active);
  } catch (e) {
    _svcBadge(false);
  }
}

async function refreshServiceLogs() {
  const el = document.getElementById('svc-logs');
  if (!el) return;
  try {
    const data = await api('GET', '/api/service/logs');
    el.innerHTML = (data.lines || []).map(l => {
      const cls = l.includes('ERROR') || l.includes('FAIL') || l.includes('error') ? 'log-error' :
                  l.includes('WARN') ? 'log-warn' :
                  l.includes('INFO') ? 'log-info' : 'log-dim';
      return `<div class="${cls}">${l.replace(/</g, '&lt;')}</div>`;
    }).join('');
    el.scrollTop = el.scrollHeight;
  } catch (e) {
    el.innerHTML = `<div class="text-red-400">Erreur lecture logs: ${e.message}</div>`;
  }
}

async function serviceRestart() {
  const badge = document.getElementById('svc-badge');
  if (badge) { badge.textContent = '⏳ Redémarrage…'; badge.className = 'px-2 py-0.5 rounded text-xs font-semibold bg-amber-900 text-amber-300'; }
  try {
    const r = await api('POST', '/api/service/restart');
    if (!r.ok) alert(`Erreur redémarrage: ${r.error || r.stderr || 'inconnu'}`);
    // Attendre que le service redémarre avant de relire les logs
    await new Promise(res => setTimeout(res, 3000));
    await refreshService();
  } catch (e) {
    alert(`Erreur: ${e.message}`);
    await refreshService();
  }
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

// ── MCP Control ───────────────────────────────────────────────────
let _mcpData = null;

async function mcpRefresh() {
  try {
    _mcpData = await api('GET', '/api/mcp/status');
    _renderMcpServers(_mcpData.servers || {});
    _renderMcpProfiles(_mcpData.profiles || {});
    setEl('mcp-count', Object.keys(_mcpData.servers || {}).length);
  } catch (e) {
    const el = document.getElementById('mcp-servers');
    if (el) el.innerHTML = `<div class="text-red-400 text-sm">Erreur: ${e.message}</div>`;
  }
}

function _renderMcpServers(servers) {
  const el = document.getElementById('mcp-servers');
  if (!el) return;
  const names = Object.keys(servers);
  if (!names.length) {
    el.innerHTML = '<div class="text-gray-500 text-sm">Aucun serveur MCP actif.</div>';
    return;
  }
  el.innerHTML = names.map(name => {
    const cfg = servers[name];
    const cmd = cfg.command ? `${cfg.command} ${(cfg.args || []).join(' ')}`.trim() : JSON.stringify(cfg);
    return `<div class="flex items-center justify-between gap-2 border border-gray-700 rounded-lg px-3 py-2">
      <div>
        <span class="font-mono text-indigo-300">${name}</span>
        <div class="text-gray-500 text-xs truncate max-w-[200px]" title="${cmd}">${cmd}</div>
      </div>
      <button onclick="mcpDisable('${name}')" class="btn-ghost text-xs shrink-0 text-red-400 border-red-900">⏹ Désactiver</button>
    </div>`;
  }).join('');
}

function _renderMcpProfiles(profiles) {
  const el = document.getElementById('mcp-profiles');
  if (!el) return;
  const profileColors = { MINIMAL: 'text-gray-300', DEV: 'text-blue-300', PERSONAL: 'text-violet-300' };
  el.innerHTML = Object.entries(profiles).map(([name, servers]) => {
    const color = profileColors[name] || 'text-cyan-300';
    const count = servers.length;
    const label = count === 0 ? 'Aucun serveur' : `${count} serveur${count > 1 ? 's' : ''}`;
    return `<div class="flex items-center justify-between gap-2 border border-gray-700 rounded-lg px-3 py-2">
      <div>
        <span class="font-semibold ${color}">${name}</span>
        <span class="text-gray-500 text-xs ml-2">${label}${servers.length ? ': ' + servers.join(', ') : ''}</span>
      </div>
      <div class="flex gap-1 shrink-0">
        <button onclick="mcpApplyProfile('${name}')" class="btn-primary text-xs py-1 px-2">▶ Appliquer</button>
        ${!['MINIMAL','DEV','PERSONAL'].includes(name)
          ? `<button onclick="mcpDeleteProfile('${name}')" class="btn-ghost text-xs text-red-400 border-red-900 py-1 px-2">✕</button>`
          : ''}
      </div>
    </div>`;
  }).join('');
}

async function mcpApplyProfile(name) {
  try {
    const r = await api('POST', '/api/mcp/profile/apply', { name });
    if (r.ok) {
      await mcpRefresh();
    } else {
      alert(`Erreur: ${r.error}`);
    }
  } catch (e) { alert(`Erreur: ${e.message}`); }
}

async function mcpSaveProfile() {
  const name = document.getElementById('mcp-save-name').value.trim().toUpperCase();
  if (!name) { alert('Nom de profil requis.'); return; }
  try {
    const r = await api('POST', '/api/mcp/profile/save', { name });
    if (r.ok) {
      document.getElementById('mcp-save-name').value = '';
      await mcpRefresh();
    } else {
      alert(`Erreur: ${r.error}`);
    }
  } catch (e) { alert(`Erreur: ${e.message}`); }
}

async function mcpDeleteProfile(name) {
  if (!confirm(`Supprimer le profil "${name}" ?`)) return;
  try {
    const r = await api('DELETE', `/api/mcp/profile/${name}`);
    if (r.ok) await mcpRefresh();
    else alert(`Erreur: ${r.error}`);
  } catch (e) { alert(`Erreur: ${e.message}`); }
}

async function mcpDisable(name) {
  try {
    const r = await api('POST', '/api/mcp/disable', { name });
    if (r.ok) await mcpRefresh();
    else alert(`Erreur: ${r.error}`);
  } catch (e) { alert(`Erreur: ${e.message}`); }
}

async function mcpAddServer() {
  const name = document.getElementById('mcp-add-name').value.trim();
  const cmd  = document.getElementById('mcp-add-cmd').value.trim();
  const argsRaw = document.getElementById('mcp-add-args').value.trim();
  if (!name || !cmd) { alert('Nom et commande requis.'); return; }
  let args = [];
  if (argsRaw) {
    try { args = JSON.parse(argsRaw); }
    catch { alert('Args invalides — doit être un tableau JSON. Ex: ["--port", "3000"]'); return; }
  }
  const config = { command: cmd, args };
  try {
    const r = await api('POST', '/api/mcp/enable', { name, config });
    if (r.ok) {
      document.getElementById('mcp-add-name').value = '';
      document.getElementById('mcp-add-cmd').value = '';
      document.getElementById('mcp-add-args').value = '';
      await mcpRefresh();
    } else {
      alert(`Erreur: ${r.error}`);
    }
  } catch (e) { alert(`Erreur: ${e.message}`); }
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
