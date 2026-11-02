(() => {
'use strict';
/* ============================================================ helpers */
const $ = (s, r = document) => r.querySelector(s);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function h(tag, props, ...kids) {
  const [t, ...cls] = tag.split('.');
  const e = document.createElement(t || 'div');
  if (cls.length) e.className = cls.join(' ');
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'text') e.textContent = v;
    else if (k === 'html') e.innerHTML = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (k === 'class') e.className += ' ' + v;
    else e.setAttribute(k, v === true ? '' : v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) e.append(k.nodeType ? k : document.createTextNode(k));
  return e;
}
const store = {
  get: (k, d = '') => { try { return localStorage.getItem('agent.' + k) ?? d; } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem('agent.' + k, v); } catch { /* private mode */ } },
  del: (k) => { try { localStorage.removeItem('agent.' + k); } catch { /* ignore */ } },
};
function toast(text, err = false, ms = 3500) {
  const t = h('div.toast' + (err ? '.err' : ''), { text });
  $('#toasts').append(t);
  setTimeout(() => t.remove(), ms);
}
function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return 'now';
  if (s < 3600) return Math.floor(s / 60) + 'm';
  if (s < 86400) return Math.floor(s / 3600) + 'h';
  return Math.floor(s / 86400) + 'd';
}
const shortModel = (ref) => (ref || 'no model').split(':').slice(-1)[0].split('/').slice(-1)[0];

/* ============================================================ markdown */
const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
function inline(s) {
  s = esc(s);
  const codes = [];
  s = s.replace(/`([^`\n]+)`/g, (_, c) => { codes.push(c); return '\u0000' + (codes.length - 1) + '\u0000'; });
  s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>').replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, '$1<em>$2</em>');
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => '<code>' + codes[i] + '</code>');
}
function codeBlock(code, lang) {
  return `<pre${lang ? ' class="has-lang"' : ''}>${lang ? `<span class="lang">${esc(lang)}</span>` : ''}<button class="copy" data-copy>copy</button><code>${esc(code)}</code></pre>`;
}
function md(src) {
  const lines = String(src).replace(/\r\n/g, '\n').split('\n');
  const out = [];
  let i = 0;
  const isBlockStart = (l) => /^\s*```/.test(l) || /^#{1,4}\s/.test(l) || /^\s*>/.test(l) || /^\s*([-*+]|\d+\.)\s+/.test(l) || /^\s*([-*_])\1\1+\s*$/.test(l);
  while (i < lines.length) {
    const l = lines[i];
    let m;
    if ((m = l.match(/^\s*```([\w+-]*)\s*$/))) {
      const buf = [];
      i++;
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) buf.push(lines[i++]);
      i++;
      out.push(codeBlock(buf.join('\n'), m[1]));
    } else if ((m = l.match(/^(#{1,4})\s+(.*)$/))) {
      out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`); i++;
    } else if (/^\s*([-*_])\1\1+\s*$/.test(l)) {
      out.push('<hr>'); i++;
    } else if (/^\s*>/.test(l)) {
      const buf = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ''));
      out.push('<blockquote>' + md(buf.join('\n')) + '</blockquote>');
    } else if (/^\s*([-*+]|\d+\.)\s+/.test(l)) {
      const ordered = /^\s*\d+\./.test(l);
      const items = [];
      while (i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i])) {
        items.push('<li>' + inline(lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, '')) + '</li>'); i++;
      }
      out.push(`<${ordered ? 'ol' : 'ul'}>${items.join('')}</${ordered ? 'ol' : 'ul'}>`);
    } else if (l.includes('|') && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}[\s:|-]*$/.test(lines[i + 1])) {
      const cells = (r) => r.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
      const head = cells(l);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) rows.push(cells(lines[i++]));
      out.push('<table><thead><tr>' + head.map((c) => `<th>${inline(c)}</th>`).join('') + '</tr></thead><tbody>' +
        rows.map((r) => '<tr>' + r.map((c) => `<td>${inline(c)}</td>`).join('') + '</tr>').join('') + '</tbody></table>');
    } else if (!l.trim()) {
      i++;
    } else {
      const buf = [];
      while (i < lines.length && lines[i].trim() && !isBlockStart(lines[i])) buf.push(inline(lines[i++]));
      if (!buf.length) { buf.push(inline(lines[i++])); }
      out.push('<p>' + buf.join('<br>') + '</p>');
    }
  }
  return out.join('');
}

/* ============================================================ state + api */
const S = {
  api: '', token: '', sessions: [], cur: null, run: { running: false }, partial: { text: '', reasoning: '' },
  pending: {}, results: {}, cards: {}, lastSeq: 0, streaming: false, finished: true, abort: null,
  images: [], files: [], live: null, statusEl: null, stick: true, info: null, commands: [], queues: {}, subs: [],
};
const EFFORTS = {
  off: ['Off', 'No reasoning. Fastest and cheapest, weakest on hard problems.'],
  medium: ['Medium', 'Reasons with fewer tokens. Good default for everyday coding.'],
  high: ['High', 'Full reasoning. Best for hard bugs and planning, slower.'],
};
const MODES = {
  ask: ['Ask', 'Asks before edits and commands. Safe read-only commands run freely.'],
  auto: ['Auto-edit', 'Edits files without asking. Still asks before running commands.'],
  plan: ['Plan', 'Read-only. It researches, then shows a plan for you to approve.'],
  yolo: ['YOLO', 'Runs everything without asking. Only for throwaway work.'],
};

async function api(path, opts = {}) {
  let r;
  try {
    r = await fetch(S.api + path, {
      method: opts.method || (opts.body ? 'POST' : 'GET'),
      headers: { Authorization: 'Bearer ' + S.token, 'Content-Type': 'application/json' },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
  } catch (e) { throw new Error('Cannot reach the server. It may be waking up — try again in a moment.'); }
  if (r.status === 401 && path !== '/api/login') { logout(true); throw new Error('Signed out'); }
  let data = null;
  try { data = await r.json(); } catch { /* empty */ }
  if (!r.ok) throw new Error((data && data.detail) || `HTTP ${r.status}`);
  return data;
}
async function wake(onStatus) {
  const t0 = Date.now();
  for (;;) {
    try {
      const c = new AbortController();
      const to = setTimeout(() => c.abort(), 9000);
      const r = await fetch(S.api + '/health', { signal: c.signal });
      clearTimeout(to);
      if (r.ok) return true;
    } catch { /* still sleeping */ }
    const s = Math.round((Date.now() - t0) / 1000);
    if (s > 120) return false;
    onStatus && onStatus(`Waking the server… ${s}s (free tier can take a minute)`);
    await sleep(2500);
  }
}

/* ============================================================ boot / auth */
function show(which) { $('#setup').hidden = which !== 'setup'; $('#main').hidden = which !== 'main'; }
function logout(silent) {
  store.del('token'); S.token = '';
  if (S.abort) S.abort.abort();
  show('setup');
  if (!silent) toast('Signed out');
}
async function connect() {
  const url = $('#in-url').value.trim().replace(/\/+$/, '');
  const pw = $('#in-pw').value;
  const st = $('#setup-status');
  st.className = 'status';
  if (!/^https?:\/\//.test(url)) { st.textContent = 'Enter the full URL, starting with https://'; st.classList.add('err'); return; }
  $('#btn-connect').disabled = true;
  S.api = url;
  try {
    if (!(await wake((t) => { st.textContent = t; }))) throw new Error('Server did not wake up. Check the URL and that the Render service is deployed.');
    st.textContent = 'Signing in…';
    const r = await api('/api/login', { body: { password: pw } });
    S.token = r.token; store.set('token', r.token); store.set('url', url);
    st.textContent = '';
    await start();
  } catch (e) { st.textContent = e.message; st.classList.add('err'); }
  $('#btn-connect').disabled = false;
}
async function start() {
  show('main');
  applyChips();
  let woke = false;
  const slow = setTimeout(() => { if (!woke) toast('Waking the server… up to a minute on the free tier', false, 8000); }, 3000);
  try {
    await wake();
    woke = true;
    S.info = await api('/api/info');
    await loadSessions();
    const last = location.hash.startsWith('#chat=') ? location.hash.slice(6) : store.get('cur');
    if (last && S.sessions.some((s) => s.id === last)) await openSession(last);
    else if (S.sessions.length) await openSession(S.sessions[0].id);
    else renderChat();
  } catch (e) { toast(e.message, true, 6000); }
  clearTimeout(slow);
}

/* ============================================================ sessions */
function skeleton(n, cls = 'skel-msg') { const f = document.createDocumentFragment(); for (let i = 0; i < n; i++) f.append(h('div.skel.' + cls, { style: `width:${55 + ((i * 23) % 40)}%;${cls === 'skel-msg' && i % 2 ? 'align-self:flex-end;' : ''}` })); return f; }
async function loadSessions() {
  if (!S.sessions.length && !S.listLoaded) $('#chat-list').replaceChildren(skeleton(5, 'skel-row'));
  S.listLoaded = true;
  S.sessions = await api('/api/sessions');
  renderList();
}
async function openSession(id) {
  closeDrawer();
  if (S.abort) S.abort.abort();
  S.streaming = false;
  if (!S.cur || S.cur.id !== id) { $('#empty').hidden = true; msgsEl().replaceChildren(skeleton(3)); }
  const d = await api('/api/sessions/' + id);
  S.cur = d.session; S.run = d.run; S.commands = d.commands || [];
  S.pending = {}; for (const p of d.run.pending || []) S.pending[p.id] = p;
  S.partial = d.run.partial || { text: '', reasoning: '' };
  S.lastSeq = d.run.seq || 0;
  S.finished = !d.run.running;
  S.subs = d.run.subs || [];
  store.set('cur', id); history.replaceState(null, '', '#chat=' + id);
  renderChat(true);
  renderList();
  if (d.run.running) attach();
}
async function newChat(opts = {}) {
  const m = await api('/api/sessions', { body: opts });
  await loadSessions();
  await openSession(m.id);
  return m;
}
function applyChips() {
  const c = S.cur;
  const cmod = $('#chip-model');
  const eff = c ? (c.effort || (c.thinking === false ? 'off' : 'high')) : 'high';
  cmod.replaceChildren(shortModel(c ? c.model : S.info && S.info.default_model), h('span.eff', { text: EFFORTS[eff][0] }));
  const mode = c ? c.mode : 'ask';
  const cm = $('#chip-mode');
  cm.textContent = MODES[mode] ? MODES[mode][0] : mode;
  cm.className = 'chip mode-' + mode;
  $('#title').textContent = c ? c.title : 'Pocket Agent';
  const sub = c ? (c.repo ? c.repo : 'scratch workspace') + (c.autopilot ? ' · autopilot' : '') + (S.run.running ? ' · working…' : '') : '';
  $('#subtitle').textContent = sub;
}

/* ============================================================ rendering */
const chat = () => $('#chat');
const msgsEl = () => $('#msgs');
function nearBottom() { const c = chat(); return c.scrollHeight - c.scrollTop - c.clientHeight < 120; }
function scrollDown(force) {
  const c = chat();
  if (force || S.stick) c.scrollTop = c.scrollHeight;
}
const ICONS = { read: '▤', write: '✎', edit: '✎', multiedit: '✎', bash: '$', bash_output: '$', kill_bash: '■', grep: '⌕', glob: '✱',
  web_fetch: '◎', web_search: '◎', todo_write: '☑', task: '⋔', skill: '★', git_sync: '⎇', create_pr: '⎇', exit_plan_mode: '☰', share_file: '⬇', preview: '▶', view_3d: '◈' };
function toolSummary(name, a) {
  a = a || {};
  switch (name) {
    case 'read': case 'write': case 'edit': case 'multiedit': return a.path || '';
    case 'bash': return (a.command || '').split('\n')[0];
    case 'share_file': case 'preview': case 'view_3d': return a.path || '';
    case 'grep': return '/' + (a.pattern || '') + '/' + (a.path ? ' ' + a.path : '');
    case 'glob': return a.pattern || '';
    case 'web_fetch': return a.url || '';
    case 'web_search': return a.query || '';
    case 'todo_write': return 'update task list';
    case 'task': return a.description || '';
    case 'git_sync': return a.message || 'commit + push';
    case 'create_pr': return a.title || '';
    case 'exit_plan_mode': return 'plan ready';
    default: return JSON.stringify(a).slice(0, 90);
  }
}
function parseArgs(call) { try { return JSON.parse(call.function.arguments || '{}'); } catch { return {}; } }
function diffNode(text) {
  const d = h('div.diff');
  for (const l of text.split('\n')) {
    const c = l.startsWith('+++') || l.startsWith('---') ? 'hdr' : l.startsWith('@@') ? 'hunk' : l.startsWith('+') ? 'add' : l.startsWith('-') ? 'del' : '';
    d.append(h('div' + (c ? '.' + c : ''), { text: l || ' ' }));
  }
  return d;
}
function previewDiff(name, a) {
  if (name === 'edit') return '--- old\n+++ new\n' + (a.old_string || '').split('\n').map((l) => '-' + l).join('\n') + '\n' + (a.new_string || '').split('\n').map((l) => '+' + l).join('\n');
  if (name === 'write') return '+++ ' + a.path + '\n' + (a.content || '').split('\n').slice(0, 60).map((l) => '+' + l).join('\n');
  if (name === 'multiedit') return (a.edits || []).map((e, i) => `@@ edit ${i + 1}\n` + e.old_string.split('\n').map((l) => '-' + l).join('\n') + '\n' + e.new_string.split('\n').map((l) => '+' + l).join('\n')).join('\n');
  return null;
}

async function answer(id, body, card) {
  try {
    await api(`/api/sessions/${S.cur.id}/approve`, { body: { id, ...body } });
    delete S.pending[id];
    const call = card._call;
    fillCard(card, call, S.results[id], null);
  } catch (e) { toast(e.message, true); }
}

function fillCard(card, call, result, approval) {
  const name = call.function.name;
  const a = parseArgs(call);
  const open = approval ? true : (card.dataset.open === '1' || !!(result && (result._file || result._preview || result._model)));
  card.className = 'tool' + (result && result._error ? ' err' : '') + (approval ? ' ask' : '');
  card.replaceChildren();
  const state = result ? h('span.tool-state ' + (result._error ? 'bad' : 'ok'), { text: result._error ? '✕' : '✓' })
    : approval ? h('span.tool-state', { text: '?' }) : h('span.tool-state', {}, h('span.spin'));
  card.append(h('button.tool-head', { onclick: () => { card.dataset.open = card.dataset.open === '1' ? '0' : '1'; fillCard(card, call, result, S.pending[call.id] || null); } },
    h('span.tool-ic', { text: ICONS[name] || '⚙' }), h('span.tool-name', { text: name.replace(/^mcp__/, 'mcp:') }),
    h('span.tool-sum', { text: toolSummary(name, a) }), state));
  if (!open) return;
  const body = h('div.tool-body');
  if (name === 'exit_plan_mode') {
    body.append(h('div.plan.prose', { html: md(a.plan || '') }));
  } else if (name === 'bash') {
    body.append(h('div.lbl', { text: 'command' }), h('pre', { text: a.command || '' }));
  } else {
    const d = (result && result._diff) || previewDiff(name, a);
    if (d) body.append(diffNode(d));
    else if (name !== 'todo_write') body.append(h('div.lbl', { text: 'input' }), h('pre', { text: JSON.stringify(a, null, 2) }));
  }
  if (result && name !== 'exit_plan_mode' && !(result._diff && !result._error)) body.append(h('div.lbl', { text: 'output' }), h('pre', { text: result.content || '' }));
  if (result && result._preview) body.append(h('div', { style: 'padding:10px 12px' }, h('button.primary.small', { text: '▶ Open preview', onclick: () => openPreview(result._preview) })));
  if (result && result._model) body.append(h('div', { style: 'padding:10px 12px' }, h('button.primary.small', { text: '◈ Open 3D viewer', onclick: () => openModel(result._model) })));
  if (result && result._file) body.append(h('div', { style: 'padding:10px 12px' }, h('button.primary.small', { text: '⬇ Download ' + result._file.split('/').pop(), onclick: () => downloadPath(result._file) })));
  card.append(body);
  if (approval) {
    const note = h('input', { type: 'text', placeholder: name === 'exit_plan_mode' ? 'What should change? (optional)' : 'Note to the agent if you deny (optional)' });
    const row = h('div.row');
    if (name === 'exit_plan_mode') {
      row.append(h('button.btn-allow', { text: 'Approve · auto-edit', onclick: () => answer(call.id, { allow: true, mode: 'auto' }, card) }),
        h('button.btn-always', { text: 'Approve · ask each', onclick: () => answer(call.id, { allow: true, mode: 'ask' }, card) }),
        h('button.btn-deny', { text: 'Revise', onclick: () => answer(call.id, { allow: false, note: note.value }, card) }));
    } else {
      row.append(h('button.btn-allow', { text: 'Allow', onclick: () => answer(call.id, { allow: true }, card) }),
        h('button.btn-always', { text: 'Always', onclick: () => answer(call.id, { allow: true, always: true }, card) }),
        h('button.btn-deny', { text: 'Deny', onclick: () => answer(call.id, { allow: false, note: note.value }, card) }));
    }
    card.append(h('div.approve', {}, h('div.q', { text: (approval.label ? `[${approval.label}] ` : '') + (approval.reason || (name === 'bash' ? 'Run this command?' : `Allow ${name}?`)) }), row, note));
  }
}
function makeCard(call) {
  const card = h('div.tool');
  card._call = call;
  S.cards[call.id] = card;
  fillCard(card, call, S.results[call.id], S.pending[call.id] || null);
  return card;
}
function userNode(m, idx) {
  const n = h('div.msg-user', { 'data-idx': idx });
  const c = m.content;
  const text = typeof c === 'string' ? c : (c || []).filter((p) => p.type === 'text').map((p) => p.text).join('\n');
  const imgs = Array.isArray(c) ? c.filter((p) => p.type === 'image_url') : [];
  if (imgs.length) n.append(h('div.imgs', {}, imgs.map((p) => h('img', { src: p.image_url.url, alt: 'attachment' }))));
  n.append(document.createTextNode(text));
  return n;
}
function assistantNode(m, idx) {
  const n = h('div.msg-ai', { 'data-idx': idx });
  if (m._reasoning) n.append(h('details.reasoning', {}, h('summary', { text: 'Thinking' }), h('div.rtext', { text: m._reasoning })));
  if (m.content) n.append(h('div.prose', { html: md(m.content) }));
  for (const call of m.tool_calls || []) n.append(makeCard(call));
  return n;
}
function renderChat(forceScroll) {
  const box = msgsEl();
  box.replaceChildren();
  S.cards = {}; S.results = {}; S.live = null; S.statusEl = null;
  applyChips();
  $('#empty').hidden = !!S.cur;
  if (!S.cur) { renderTodos(); return; }
  for (const m of S.cur.messages) if (m.role === 'tool') S.results[m.tool_call_id] = m;
  S.cur.messages.forEach((m, i) => {
    if (m.role === 'user') {
      if (m._synthetic) return;
      if (m._summary) { box.append(h('div.divider', { text: 'earlier conversation summarized' })); return; }
      box.append(userNode(m, i));
    } else if (m.role === 'assistant') box.append(assistantNode(m, i));
  });
  renderTodos(); renderSubs(); renderQueue();
  renderLive();
  for (const ev of Object.values(S.pending)) ensureApprovalCard(ev);
  S.stick = true;
  if (forceScroll !== false) requestAnimationFrame(() => scrollDown(true));
  updateSend();
}
let liveRaf = 0;
function renderLive() {
  if (!S.run.running) { if (S.live) S.live.remove(); S.live = null; if (S.statusEl) S.statusEl.remove(); S.statusEl = null; return; }
  if (!S.live) { S.live = h('div.msg-ai.fresh'); msgsEl().append(S.live); }
  if (liveRaf) return;
  liveRaf = requestAnimationFrame(() => {
    liveRaf = 0;
    if (!S.live) return;
    const p = S.partial;
    S.live.replaceChildren();
    if (p.reasoning) S.live.append(h('details.reasoning', { open: !p.text }, h('summary', { text: 'Thinking…' }), h('div.rtext', { text: p.reasoning })));
    if (p.text) S.live.append(h('div.prose', { html: md(p.text) }));
    if (!p.text && !p.reasoning) S.live.append(h('div.live-dots', {}, h('i'), h('i'), h('i')));
    if (S.statusEl) msgsEl().append(S.statusEl);
    msgsEl().append(S.live);
    if (S.statusEl) msgsEl().append(S.statusEl);
    scrollDown();
  });
}
function setStatus(text) {
  if (!text) { if (S.statusEl) S.statusEl.remove(); S.statusEl = null; return; }
  if (!S.statusEl) S.statusEl = h('div.notice');
  S.statusEl.textContent = text;
  msgsEl().append(S.statusEl);
  scrollDown();
}
function renderTodos() {
  const bar = $('#todobar');
  const todos = (S.cur && S.cur.todos) || [];
  if (!todos.length || todos.every((t) => t.status === 'completed')) { bar.hidden = true; return; }
  const done = todos.filter((t) => t.status === 'completed').length;
  const act = todos.find((t) => t.status === 'in_progress') || todos.find((t) => t.status === 'pending');
  const open = bar.dataset.open === '1';
  bar.hidden = false;
  bar.replaceChildren(h('button.th', { onclick: () => { bar.dataset.open = open ? '0' : '1'; renderTodos(); } },
    h('b', { text: `${done}/${todos.length}` }), h('span', { text: act ? act.content : 'done' }), h('span', { text: open ? '▾' : '▸', class: 'dim' })));
  if (open) bar.append(h('ul', {}, todos.map((t) => h('li.' + t.status, { text: (t.status === 'completed' ? '✓ ' : t.status === 'in_progress' ? '→ ' : '○ ') + t.content }))));
}
function renderSubs() {
  const bar = $('#subbar');
  const subs = S.subs || [];
  if (!subs.length) { bar.hidden = true; return; }
  const running = subs.filter((x) => x.status === 'running').length;
  const open = bar.dataset.open === '1';
  bar.hidden = false;
  bar.replaceChildren(h('button.th', { onclick: () => { bar.dataset.open = open ? '0' : '1'; renderSubs(); } },
    h('b', { text: '⋔' }), h('span', { text: running ? `${running} sub-agent${running > 1 ? 's' : ''} working · ${subs.length} total` : `${subs.length} sub-agent${subs.length > 1 ? 's' : ''} finished` }), h('span', { text: open ? '▾' : '▸', class: 'dim' })));
  if (open) bar.append(h('ul', {}, subs.map((x) => h('li.sub-' + x.status, { style: `padding-left:${(x.depth - 1) * 16}px` },
    x.status === 'running' ? h('span.spin') : h('span', { text: x.status === 'done' ? '✓' : '✕' }), h('span.sl', { text: x.label }), h('small', { text: x.model })))));
}
function renderList() {
  const q = $('#chat-search').value.trim().toLowerCase();
  const box = $('#chat-list');
  box.replaceChildren();
  const list = S.sessions.filter((s) => !q || s.title.toLowerCase().includes(q) || (s.repo || '').toLowerCase().includes(q));
  if (!list.length) box.append(h('div.notice', { text: q ? 'No matches' : 'No chats yet' }));
  for (const s of list) {
    box.append(h('div.chat-item' + (S.cur && S.cur.id === s.id ? '.active' : ''), {},
      h('button.main', { onclick: () => openSession(s.id) },
        h('div.t', {}, s.running ? h('span.dot') : null, s.title),
        h('div.m', { text: `${s.repo || 'scratch'} · ${shortModel(s.model)} · ${ago(s.updated)}` })),
      h('button.more', { 'aria-label': 'Chat actions', text: '⋯', onclick: () => chatActions(s) })));
  }
}

/* ============================================================ streaming */
async function attach() {
  if (S.streaming || !S.cur) return;
  S.streaming = true;
  const sid = S.cur.id;
  while (S.cur && S.cur.id === sid && !S.finished) {
    try {
      S.abort = new AbortController();
      const r = await fetch(`${S.api}/api/sessions/${sid}/events?after=${S.lastSeq}`, { headers: { Authorization: 'Bearer ' + S.token }, signal: S.abort.signal });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
          if (chunk.startsWith('data:')) { try { handle(JSON.parse(chunk.slice(5))); } catch (e) { console.error(e); } }
        }
      }
    } catch (e) { if (e.name === 'AbortError') break; }
    if (!S.finished) await sleep(1500);
  }
  S.streaming = false;
}
function handle(ev) {
  if (ev.seq) S.lastSeq = Math.max(S.lastSeq, ev.seq);
  const c = S.cur;
  if (!c) return;
  switch (ev.type) {
    case 'step_start': S.partial = { text: '', reasoning: '' }; setStatus(''); renderLive(); break;
    case 'delta': S.partial.text += ev.text; renderLive(); break;
    case 'reasoning': S.partial.reasoning += ev.text; renderLive(); break;
    case 'message': {
      const m = ev.message;
      c.messages.push(m);
      S.partial = { text: '', reasoning: '' };
      if (m.role === 'tool') {
        S.results[m.tool_call_id] = m;
        const card = S.cards[m.tool_call_id];
        if (card) fillCard(card, card._call, m, null);
      } else if (m.role === 'assistant') {
        const n = assistantNode(m);
        if (S.live) msgsEl().insertBefore(n, S.live); else msgsEl().append(n);
      }
      renderLive(); scrollDown(); break;
    }
    case 'approval': {
      S.pending[ev.id] = ev;
      ensureApprovalCard(ev);
      scrollDown(true);
      break;
    }
    case 'approval_done': {
      delete S.pending[ev.id];
      const card = S.cards[ev.id];
      if (card && card.dataset.standalone) { card.remove(); delete S.cards[ev.id]; }
      else if (card) fillCard(card, card._call, S.results[ev.id], null);
      break;
    }
    case 'todos': c.todos = ev.todos; renderTodos(); break;
    case 'sub_start': S.subs.push({ id: ev.id, label: ev.label, model: ev.model, depth: ev.depth, status: 'running' }); renderSubs(); break;
    case 'sub_end': { const sb = S.subs.find((x) => x.id === ev.id); if (sb) sb.status = ev.status; renderSubs(); break; }
    case 'mode': c.mode = ev.mode; applyChips(); break;
    case 'notice': setStatus(ev.text); break;
    case 'error': toast(ev.text, true, 8000); msgsEl().append(h('div.notice.err', { text: ev.text })); scrollDown(); break;
    case 'reload': reloadCurrent(); break;
    case 'done': finishRun(); break;
    default: break;
  }
}
function ensureApprovalCard(ev) {
  let card = S.cards[ev.id];
  if (!card) {   // approval for a sub-agent's call: it has no card in the chat, so show a standalone one
    card = makeCard({ id: ev.id, type: 'function', function: { name: ev.tool, arguments: JSON.stringify(ev.args || {}) } });
    card.dataset.standalone = '1';
    if (S.live && S.live.parentNode) msgsEl().insertBefore(card, S.live); else msgsEl().append(card);
  }
  fillCard(card, card._call, null, ev);
}
async function finishRun() {
  S.finished = true; S.run.running = false; S.pending = {};
  setStatus('');
  renderLive();
  try {
    const d = await api('/api/sessions/' + S.cur.id);
    S.cur = d.session; S.commands = d.commands || [];
    renderChat(false);
    await loadSessions();
  } catch { /* offline */ }
  updateSend(); applyChips(); renderSubs();
  drainQueue();
}
async function reloadCurrent() {
  const d = await api('/api/sessions/' + S.cur.id);
  S.cur = d.session;
  renderChat(false);
}

/* ============================================================ composer */
const hasDraft = () => !!$('#input').value.trim() || S.images.length > 0 || S.files.length > 0;
function updateSend() {
  const b = $('#btn-send');
  const stop = S.run.running && !hasDraft();
  b.textContent = stop ? '■' : '↑';
  b.classList.toggle('stop', stop);
  b.setAttribute('aria-label', stop ? 'Stop' : S.run.running ? 'Queue message' : 'Send');
}
function autoGrow() { const t = $('#input'); t.style.height = 'auto'; t.style.height = Math.min(t.scrollHeight, window.innerHeight * 0.38) + 'px'; }
const queueFor = () => (S.queues[S.cur.id] ||= []);
function renderQueue() {
  const bar = $('#queuebar');
  const q = S.cur ? queueFor() : [];
  bar.hidden = !q.length;
  bar.replaceChildren(...q.map((m, i) => h('div.qitem', {}, h('span.qn', { text: 'Queued' }), h('span.qt', { text: m.text.replace(/\s+/g, ' ').slice(0, 80) || '(attachment)' }), h('button', { 'aria-label': 'Remove from queue', text: '✕', onclick: () => { queueFor().splice(i, 1); renderQueue(); } }))));
}
function takeDraft() {
  const input = $('#input');
  const images = S.images.slice();
  const notes = S.files.filter((f) => f.state === 'ready').map((f) => f.extracted ? `${f.path} (extracted to ${f.extracted}/, ${f.files} files)` : f.path);
  let text = input.value.trim();
  if (notes.length) text = (text ? text + '\n\n' : 'See the uploaded files.\n\n') + `[Files uploaded to the workspace: ${notes.join('; ')}]`;
  input.value = ''; autoGrow(); S.images = []; S.files = []; renderAttach(); hideSlash();
  return { text, images };
}
async function send() {
  if (S.run.running && !hasDraft()) { try { await api(`/api/sessions/${S.cur.id}/stop`, { method: 'POST' }); } catch (e) { toast(e.message, true); } return; }
  if (!hasDraft()) return;
  if (S.files.some((f) => f.state === 'uploading')) { toast('Still uploading…'); return; }
  if (!S.cur) { try { await newChat({}); } catch (e) { toast(e.message, true); return; } }
  const draft = takeDraft();
  if (S.run.running) { queueFor().push(draft); renderQueue(); updateSend(); return; }
  await dispatch(draft);
}
async function dispatch({ text, images }) {
  const msg = { role: 'user', content: images.length ? [{ type: 'text', text }, ...images.map((u) => ({ type: 'image_url', image_url: { url: u } }))] : text };
  S.cur.messages.push(msg);
  S.subs = []; renderSubs();
  { const un = userNode(msg, S.cur.messages.length - 1); un.classList.add('fresh'); msgsEl().append(un); }
  $('#empty').hidden = true;
  S.run = { running: true }; S.partial = { text: '', reasoning: '' }; S.finished = false; S.stick = true;
  renderLive(); applyChips(); updateSend(); scrollDown(true);
  try {
    const r = await api(`/api/sessions/${S.cur.id}/messages`, { body: { text, images } });
    if (r.compacted) { S.run.running = false; renderLive(); await reloadCurrent(); updateSend(); toast('Context compacted'); drainQueue(); return; }
    S.lastSeq = r.seq;
    attach();
  } catch (e) {
    S.run.running = false; S.finished = true; renderLive(); updateSend();
    toast(e.message, true, 6000);
    if (/still working/.test(e.message)) openSession(S.cur.id);
  }
}
function drainQueue() {
  if (!S.cur || S.run.running) return;
  const q = queueFor();
  if (!q.length) return;
  const next = q.shift(); renderQueue();
  dispatch(next);
}

/* ---- long-press menu on messages */
let CTX = null;
function closeCtx() { if (!CTX) return; CTX.el.remove(); CTX.scrim.remove(); CTX.node.classList.remove('pressed'); CTX = null; }
function msgPlain(m) {
  const c = m.content;
  return (typeof c === 'string' ? c : (c || []).filter((p) => p.type === 'text').map((p) => p.text).join('\n')) || '';
}
function userBefore(idx) {
  for (let i = idx; i >= 0; i--) { const m = S.cur.messages[i]; if (m.role === 'user' && !m._synthetic && !m._summary) return i; }
  return -1;
}
function lastUserIdx() { return userBefore(S.cur.messages.length - 1); }
function openCtx(node, x, y) {
  closeCtx();
  if (!S.cur) return;
  const idx = +node.dataset.idx, m = S.cur.messages[idx];
  if (!m) return;
  const busy = S.run.running;
  const items = [];
  if (m.role === 'user') items.push(['✎', 'Edit', () => rewind(idx, true), !m._checkpoint || busy]);
  else { const ui = userBefore(idx); items.push(['⟳', 'Regenerate', () => regenerate(ui), ui < 0 || !S.cur.messages[ui]._checkpoint || busy]); }
  const txt = msgPlain(m).trim();
  items.push(['⧉', 'Copy', () => navigator.clipboard.writeText(txt).then(() => toast('Copied')).catch(() => toast('Copy failed')), !txt]);
  items.push(['⌶', 'Select text', () => { node.classList.add('selectable'); toast('Drag to select'); }, !txt]);
  const scrim = h('div.ctx-scrim', { onclick: () => { if (Date.now() - CTX.t > 300) closeCtx(); }, oncontextmenu: (e) => { e.preventDefault(); closeCtx(); } });
  const el = h('div.ctx', { role: 'menu' }, items.map(([ic, label, fn, off]) => h('button.ctx-i' + (off ? '.off' : ''), { role: 'menuitem', disabled: off || false, onclick: () => { closeCtx(); fn(); } }, h('span', { text: ic }), h('span', { text: label }))));
  document.body.append(scrim, el);
  node.classList.add('pressed');
  const w = el.offsetWidth, hh = el.offsetHeight, vw = window.innerWidth, vh = window.innerHeight;
  let top = y - hh - 14; if (top < 60) top = Math.min(y + 18, vh - hh - 10);
  el.style.left = Math.max(10, Math.min(x - w / 2, vw - w - 10)) + 'px';
  el.style.top = top + 'px';
  CTX = { el, scrim, node, t: Date.now() };
}
function initLongPress() {
  const box = msgsEl();
  let timer = 0, sx = 0, sy = 0;
  const cancel = () => { clearTimeout(timer); timer = 0; };
  const target = (e) => e.target.closest && e.target.closest('.msg-user[data-idx], .msg-ai[data-idx]');
  box.addEventListener('pointerdown', (e) => {
    if (e.button || e.target.closest('a, button, summary, input, textarea, pre')) return;
    const n = target(e); if (!n || n.classList.contains('selectable')) return;
    cancel(); sx = e.clientX; sy = e.clientY;
    timer = setTimeout(() => { timer = 0; try { navigator.vibrate && navigator.vibrate(12); } catch { /* ignore */ } openCtx(n, sx, sy); }, 420);
  });
  box.addEventListener('pointermove', (e) => { if (timer && Math.hypot(e.clientX - sx, e.clientY - sy) > 10) cancel(); });
  for (const ev of ['pointerup', 'pointercancel', 'pointerleave']) box.addEventListener(ev, cancel);
  chat().addEventListener('scroll', () => { cancel(); closeCtx(); }, { passive: true });
  box.addEventListener('contextmenu', (e) => { const n = target(e); if (!n) return; e.preventDefault(); cancel(); openCtx(n, e.clientX, e.clientY); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeCtx(); });
  document.addEventListener('pointerdown', (e) => { if (!e.target.closest('.msg-user.selectable, .msg-ai.selectable')) document.querySelectorAll('.selectable').forEach((n) => n.classList.remove('selectable')); });
}
async function regenerate(idx) {
  if (idx !== lastUserIdx() && !(await confirmSheet('Regenerate from here?', 'Removes everything after this question and puts your files back to how they were before it.', 'Regenerate'))) return;
  const m = S.cur.messages[idx];
  const parts = Array.isArray(m.content) ? m.content : [{ type: 'text', text: m.content || '' }];
  const draft = { text: parts.filter((p) => p.type === 'text').map((p) => p.text).join('\n'), images: parts.filter((p) => p.type === 'image_url').map((p) => p.image_url.url) };
  try { await api(`/api/sessions/${S.cur.id}/rewind`, { body: { index: idx } }); await reloadCurrent(); } catch (e) { toast(e.message, true); return; }
  await dispatch(draft);
}
function renderAttach() {
  updateSend();
  const p = $('#attach-preview');
  p.hidden = !S.images.length && !S.files.length;
  p.replaceChildren(...S.files.map((f, i) => h('div.thumb.filechip', {}, h('span', { text: (f.state === 'uploading' ? '⏳ ' : '📎 ') + f.name }), h('button', { text: '✕', 'aria-label': 'Remove', onclick: () => { S.files.splice(i, 1); renderAttach(); } }))), ...S.images.map((u, i) => h('div.thumb', {}, h('img', { src: u, alt: 'attached' }), h('button', { text: '✕', 'aria-label': 'Remove', onclick: () => { S.images.splice(i, 1); renderAttach(); } }))));
}
async function uploadFile(file) {
  if (!S.cur) { try { await newChat({}); } catch (e) { toast(e.message, true); return; } }
  const chip = { name: file.name, state: 'uploading' };
  S.files.push(chip); renderAttach();
  try {
    const r = await fetch(`${S.api}/api/sessions/${S.cur.id}/upload?name=${encodeURIComponent(file.name)}&extract=1`, { method: 'PUT', headers: { Authorization: 'Bearer ' + S.token }, body: file });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || 'upload failed: HTTP ' + r.status);
    Object.assign(chip, { state: 'ready', path: d.path, extracted: d.extracted_to, files: d.files });
  } catch (e) { S.files.splice(S.files.indexOf(chip), 1); toast(e.message, true, 6000); }
  renderAttach();
}
function attachAny(file) { if (!file) return Promise.resolve(); return file.type.startsWith('image/') ? attachImage(file) : uploadFile(file); }
async function downloadPath(path) {
  try {
    const r = await fetch(`${S.api}/api/sessions/${S.cur.id}/download?path=${encodeURIComponent(path)}`, { headers: { Authorization: 'Bearer ' + S.token } });
    if (!r.ok) throw new Error('Download failed: HTTP ' + r.status);
    const blob = await r.blob();
    const m = (r.headers.get('content-disposition') || '').match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
    const name = m ? decodeURIComponent(m[1]) : (path.split('/').pop() || 'workspace') + (blob.type === 'application/zip' ? '.zip' : '');
    const a = h('a', { href: URL.createObjectURL(blob), download: name });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 15000);
  } catch (e) { toast(e.message, true); }
}
async function attachImage(file) {
  if (!file) return;
  const url = await new Promise((res, rej) => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.onerror = rej; fr.readAsDataURL(file); });
  const img = await new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = url; });
  const max = 1280, sc = Math.min(1, max / Math.max(img.width, img.height));
  const cv = document.createElement('canvas');
  cv.width = Math.round(img.width * sc); cv.height = Math.round(img.height * sc);
  cv.getContext('2d').drawImage(img, 0, 0, cv.width, cv.height);
  if (S.images.length >= 4) { toast('Max 4 images'); return; }
  S.images.push(cv.toDataURL('image/jpeg', 0.82));
  renderAttach();
}
const BUILTIN = [['/compact', 'summarize older context to free space']];
function slashUpdate() {
  const v = $('#input').value;
  const box = $('#slashbox');
  if (!v.startsWith('/') || v.includes(' ') || v.includes('\n')) { hideSlash(); return; }
  const all = [...BUILTIN, ...(S.commands || []).map((c) => ['/' + c, 'project command'])].filter(([n]) => n.startsWith(v));
  if (!all.length) { hideSlash(); return; }
  box.hidden = false;
  box.replaceChildren(...all.map(([n, d]) => h('button', { onclick: () => { $('#input').value = n + ' '; hideSlash(); $('#input').focus(); } }, n, h('small', { text: d }))));
}
function hideSlash() { $('#slashbox').hidden = true; }

/* ============================================================ sheets / drawer */
function openDrawer() { $('#drawer').classList.add('open'); $('#scrim').hidden = false; $('#drawer').setAttribute('aria-hidden', 'false'); }
function closeDrawer() { $('#drawer').classList.remove('open'); $('#scrim').hidden = true; $('#drawer').setAttribute('aria-hidden', 'true'); }
function sheet(title, ...content) {
  $('#sheet-title').textContent = title;
  $('#sheet-body').replaceChildren(...content.flat());
  $('#sheet').classList.add('open'); $('#sheet-scrim').hidden = false; $('#sheet').setAttribute('aria-hidden', 'false');
}
function closeSheet() { $('#sheet').classList.remove('open'); $('#sheet-scrim').hidden = true; $('#sheet').setAttribute('aria-hidden', 'true'); }
function confirmSheet(title, text, ok, danger) {
  return new Promise((res) => {
    sheet(title, h('div.dim', { text }), h('button.secondary' + (danger ? '.danger' : ''), { text: ok, onclick: () => { closeSheet(); res(true); } }), h('button.secondary', { text: 'Cancel', onclick: () => { closeSheet(); res(false); } }));
  });
}
/* ---- swipe a bottom sheet down to close it */
function initSheetSwipe() {
  const sh = $('#sheet');
  let y0 = 0, dy = 0, t0 = 0, on = false;
  const down = (e) => { if (e.target.closest('button')) return; on = true; y0 = e.clientY; dy = 0; t0 = e.timeStamp; sh.style.transition = 'none'; e.currentTarget.setPointerCapture(e.pointerId); };
  const move = (e) => { if (!on) return; dy = Math.max(0, e.clientY - y0); sh.style.transform = `translateY(${dy}px)`; $('#sheet-scrim').style.opacity = String(Math.max(0, 1 - dy / sh.offsetHeight)); };
  const up = (e) => {
    if (!on) return; on = false;
    const fast = dy / Math.max(1, e.timeStamp - t0) > 0.5;
    sh.style.transition = 'transform .2s ease';
    $('#sheet-scrim').style.opacity = '';
    if (dy > Math.max(170, sh.offsetHeight * 0.34) || (fast && dy > 100)) { sh.style.transform = 'translateY(104%)'; setTimeout(() => { sh.style.transition = ''; sh.style.transform = ''; closeSheet(); }, 190); }
    else { sh.style.transform = ''; setTimeout(() => { sh.style.transition = ''; }, 210); }
  };
  for (const el of [sh.querySelector('.sheet-grab'), sh.querySelector('.sheet-head')]) {
    el.addEventListener('pointerdown', down); el.addEventListener('pointermove', move);
    el.addEventListener('pointerup', up); el.addEventListener('pointercancel', up);
  }
}
/* ---- swipe in from the left edge to open the drawer, swipe it left to close */
function initEdgeSwipe() {
  let x0 = 0, y0 = 0, mode = '';
  document.addEventListener('touchstart', (e) => {
    const t = e.touches[0]; x0 = t.clientX; y0 = t.clientY;
    mode = x0 < 22 && !$('#drawer').classList.contains('open') && !document.querySelector('.overlay:not([hidden])') ? 'open' : ($('#drawer').classList.contains('open') ? 'close' : '');
  }, { passive: true });
  document.addEventListener('touchmove', (e) => {
    if (!mode) return;
    const t = e.touches[0], dx = t.clientX - x0, dy = Math.abs(t.clientY - y0);
    if (dy > 40 && dy > Math.abs(dx)) { mode = ''; return; }
    if (mode === 'open' && dx > 60) { mode = ''; openDrawer(); }
    else if (mode === 'close' && dx < -60) { mode = ''; closeDrawer(); }
  }, { passive: true });
}
function newChatSheet() {
  closeDrawer();
  const repo = h('input', { type: 'text', placeholder: 'owner/name (optional)', autocapitalize: 'off', autocorrect: 'off', spellcheck: 'false' });
  const go = h('button.primary', { text: 'Start chat', onclick: async () => {
    go.disabled = true;
    try { closeSheet(); await newChat({ repo: repo.value.trim() || null }); $('#input').focus(); } catch (e) { toast(e.message, true); }
    go.disabled = false;
  } });
  sheet('New chat', h('label', {}, 'GitHub repo to work in', repo),
    h('div.dim', { text: 'Leave blank for a scratch workspace. With a repo, it works on its own branch (agent/…) and never touches main without a PR.' }), go);
  setTimeout(() => { if ($('#sheet').classList.contains('open')) repo.focus(); }, 300);
}
async function setEffort(k) {
  if (!S.cur) return;
  await api('/api/sessions/' + S.cur.id, { method: 'PATCH', body: { effort: k } });
  S.cur.effort = k; S.cur.thinking = k !== 'off'; applyChips();
}
async function modelSheet() {
  const list = h('div', { style: 'display:flex;flex-direction:column;gap:8px' }, h('div.dim', { text: 'Loading models…' }));
  const search = h('input.msearch', { type: 'search', placeholder: 'Search models', autocomplete: 'off', autocapitalize: 'off', autocorrect: 'off', spellcheck: 'false' });
  const cur = () => (S.cur && (S.cur.effort || (S.cur.thinking === false ? 'off' : 'high'))) || 'high';
  const note = h('div.seg-note', { text: EFFORTS[cur()][1] });
  const seg = h('div.seg', {}, ...Object.entries(EFFORTS).map(([k, [name]]) => h('button' + (cur() === k ? '.on' : ''), { text: name, onclick: async (e) => {
    const me = e.currentTarget;
    await setEffort(k);
    seg.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b === me));
    note.textContent = EFFORTS[k][1];
  } })));
  const head = [];
  if (S.cur) head.push(h('div.sec', { text: 'Effort (reasoning)' }), seg, note);
  head.push(h('div.sec', { text: 'Model' }), search);
  sheet('Model', ...head, list);
  let models = [];
  const draw = () => {
    const q = search.value.trim().toLowerCase();
    const shown = models.filter((m) => !q || (m.id + ' ' + m.provider).toLowerCase().includes(q));
    list.replaceChildren();
    for (const m of shown) {
      const out = h('small', { text: m.provider });
      list.append(h('div.opt' + (S.cur && S.cur.model === m.ref ? '.sel' : ''), {},
        h('div.ot', { onclick: async () => { if (!S.cur) { await newChat({ model: m.ref }); } else { await api('/api/sessions/' + S.cur.id, { method: 'PATCH', body: { model: m.ref } }); S.cur.model = m.ref; } applyChips(); closeSheet(); }, style: 'cursor:pointer' }, h('b', { text: m.id }), out),
        h('button.test', { text: 'test', onclick: async (e) => {
          e.stopPropagation(); out.textContent = 'testing…';
          const r = await api('/api/models/test', { body: { ref: m.ref } });
          out.textContent = r.ok ? `✓ ${r.ms} ms` : '✕ ' + r.error.slice(0, 120);
        } })));
    }
    if (!shown.length && models.length) list.append(h('div.dim', { text: `No model matches “${search.value}”.` }));
  };
  search.addEventListener('input', draw);
  try {
    const r = await api('/api/models');
    models = r.models.filter((x) => !x.error);
    draw();
    if (S.info) list.prepend(h('div.dim', { text: `${models.length} models · default: ${shortModel(S.info.default_model)} · fallback: ${shortModel(S.info.fallback_model) || 'none'}` }));
    for (const b of r.models.filter((x) => x.error)) list.append(h('div.dim', { text: `${b.provider}: ${b.id}` }));
    if (!r.models.length) list.append(h('div.dim', { text: 'No providers configured. Add NVIDIA_API_KEY in Render.' }));
  } catch (e) { list.replaceChildren(h('div.status.err', { text: e.message })); }
}
function modeSheet() {
  if (!S.cur) return;
  sheet('Permission mode', ...Object.entries(MODES).map(([k, [name, desc]]) => h('button.opt' + (S.cur.mode === k ? '.sel' : ''), { onclick: async () => {
    await api('/api/sessions/' + S.cur.id, { method: 'PATCH', body: { mode: k } }); S.cur.mode = k; applyChips(); closeSheet();
  } }, h('div.ot', {}, h('b', { text: name }), h('small', { text: desc })))));
}
function chatActions(s) {
  sheet(s.title,
    h('button.secondary', { text: 'Rename', onclick: () => {
      const inp = h('input', { type: 'text', value: s.title });
      sheet('Rename chat', inp, h('button.primary', { text: 'Save', onclick: async () => { await api('/api/sessions/' + s.id, { method: 'PATCH', body: { title: inp.value } }); closeSheet(); await loadSessions(); if (S.cur && S.cur.id === s.id) { S.cur.title = inp.value; applyChips(); } } }));
    } }),
    h('button.secondary.danger', { text: 'Delete chat', onclick: async () => {
      if (!(await confirmSheet('Delete this chat?', 'The chat and its workspace are removed from the server.', 'Delete', true))) return;
      await api('/api/sessions/' + s.id, { method: 'DELETE' });
      if (S.cur && S.cur.id === s.id) { S.cur = null; S.run = { running: false }; store.del('cur'); }
      await loadSessions();
      if (!S.cur && S.sessions.length) await openSession(S.sessions[0].id); else renderChat();
    } }));
}
async function rewind(idx, edit) {
  if (!(await confirmSheet(edit ? 'Edit this message?' : 'Rewind to here?', 'Removes this message and everything after it, restores your files to how they were before it, and puts the text back in the box to change and resend.', edit ? 'Edit' : 'Rewind'))) return;
  try {
    const r = await api(`/api/sessions/${S.cur.id}/rewind`, { body: { index: idx } });
    await reloadCurrent();
    $('#input').value = r.text; autoGrow(); $('#input').focus();
    toast(r.note);
  } catch (e) { toast(e.message, true); }
}
async function filesSheet(path = '') {
  if (!S.cur) return;
  const body = h('div', {}, h('div.dim', { text: 'Loading…' }));
  sheet('/' + path, body);
  try {
    const d = await api(`/api/sessions/${S.cur.id}/files?path=${encodeURIComponent(path)}`);
    body.replaceChildren();
    body.append(h('div.evbtns', {}, h('button.secondary', { text: '＋ Upload files', onclick: () => { closeSheet(); $('#file-img').click(); } }), h('button.secondary', { text: '⬇ Download folder (.zip)', onclick: () => downloadPath(path) })));
    if (path) body.append(h('button.fileitem', { text: '↑ ..', onclick: () => filesSheet(path.split('/').slice(0, -1).join('/')) }));
    for (const it of d.items) {
      const full = (path ? path + '/' : '') + it.name;
      body.append(h('div.filerow', {},
        h('button.fileitem', { onclick: () => it.dir ? filesSheet(full) : fileView(full) }, (it.dir ? '▸ ' : '  ') + it.name, it.dir ? '' : h('small', { text: it.size > 1024 ? Math.round(it.size / 1024) + ' KB' : it.size + ' B' })),
        /\.html?$/i.test(it.name) ? h('button.dl', { 'aria-label': 'Preview ' + it.name, text: '▶', onclick: () => openPreview(full) }) : null,
        /\.(stl|obj|glb|gltf)$/i.test(it.name) ? h('button.dl', { 'aria-label': 'View in 3D ' + it.name, text: '◈', onclick: () => openModel(full) }) : null,
        h('button.dl', { 'aria-label': 'Download ' + it.name, text: '⬇', onclick: () => downloadPath(full) })));
    }
    if (!d.items.length) body.append(h('div.dim', { text: 'Empty' }));
  } catch (e) { body.replaceChildren(h('div.status.err', { text: e.message })); }
}
async function fileView(path) {
  const pre = h('div.codeview', { text: 'Loading…' });
  sheet(path, pre, /\.html?$/i.test(path) ? h('button.primary', { text: '▶ Preview', onclick: () => openPreview(path) }) : null, h('button.secondary', { text: '⬇ Download', onclick: () => downloadPath(path) }), h('button.secondary', { text: '← Back', onclick: () => filesSheet(path.split('/').slice(0, -1).join('/')) }));
  try {
    const d = await api(`/api/sessions/${S.cur.id}/file?path=${encodeURIComponent(path)}`);
    pre.textContent = d.binary ? `(binary file, ${d.size} bytes)` : d.text + (d.truncated ? '\n… truncated' : '');
  } catch (e) { pre.textContent = e.message; }
}
function closeMenu() { $('#menu').hidden = true; $('#menu-scrim').hidden = true; }
function renameSheet(s) {
  const inp = h('input', { type: 'text', value: s.title });
  sheet('Rename chat', inp, h('button.primary', { text: 'Save', onclick: async () => { await api('/api/sessions/' + s.id, { method: 'PATCH', body: { title: inp.value } }); closeSheet(); await loadSessions(); if (S.cur && S.cur.id === s.id) { S.cur.title = inp.value; applyChips(); } } }));
}
function openMenu() {
  const c = S.cur;
  const menu = $('#menu');
  const item = (ic, label, fn, cls = '') => h('button.mi' + cls, { onclick: () => { closeMenu(); fn(); } }, h('span.mic', { text: ic }), h('span', { text: label }));
  const rows = [];
  if (c) {
    const tg = h('span.tg' + (c.autopilot ? '.on' : ''));
    rows.push(
      item('✎', 'Rename chat', () => renameSheet(c)),
      item('⇣', 'Compact context', () => { $('#input').value = '/compact'; send(); }),
      h('button.mi', { onclick: async () => {
        const v = !c.autopilot;
        try { await api('/api/sessions/' + c.id, { method: 'PATCH', body: { autopilot: v } }); c.autopilot = v; tg.classList.toggle('on', v); applyChips(); } catch (e) { toast(e.message, true); }
      } }, h('span.mic', { text: '∞' }), h('span', { text: 'Autopilot' }), tg),
      item('⎇', 'Save workspace to GitHub', async () => { try { const r = await api(`/api/sessions/${c.id}/sync`, { method: 'POST' }); toast(r.result); } catch (er) { toast(er.message, true); } }));
  } else rows.push(h('div.dim', { style: 'padding:12px', text: 'Open or start a chat to see chat actions.' }));
  menu.replaceChildren(...rows);
  menu.hidden = false; $('#menu-scrim').hidden = false;
}

/* ============================================================ version + updates */
const VER = { v: window.APP_VERSION || '', c: window.APP_COMMIT || '' };
const verText = () => VER.c ? `v${VER.v} · ${VER.c}` : 'dev build';
const UPD = { available: false, latest: null };
async function checkUpdate(manual) {
  if (!VER.c) return manual ? 'dev' : 'current';
  try {
    const r = await fetch('version.json?t=' + Date.now(), { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const j = await r.json();
    UPD.latest = j;
    UPD.available = !!j.commit && j.commit !== VER.c;
  } catch { if (manual) return 'error'; }
  updateUI();
  return UPD.available ? 'available' : 'current';
}
function updateUI() {
  const on = UPD.available;
  $('#update-banner').hidden = !on || S.updateDismissed;
  $('#update-text').textContent = on ? `Update available${UPD.latest ? ' · v' + UPD.latest.version : ''}` : '';
  $('#upd-dot').hidden = !on;
  const ver = $('#ver');
  ver.textContent = on ? `Update available → v${UPD.latest.version}` : `Pocket Agent ${verText()}`;
  ver.classList.toggle('upd', on);
  if (!$('#settings').hidden) drawSettings();
}
async function applyUpdate() {
  const b = $('#btn-update'); b.disabled = true; b.textContent = 'Updating…';
  try { (await navigator.serviceWorker.getRegistrations()).forEach((r) => r.unregister()); } catch { /* none */ }
  try { (await caches.keys()).forEach((k) => caches.delete(k)); } catch { /* none */ }
  // refresh the browser's own HTTP cache for the shell, otherwise the old files can come straight back
  try { await Promise.all(['./', 'index.html', 'app.js', 'style.css', 'config.js', 'version.json', 'manifest.webmanifest', 'sw.js'].map((f) => fetch(f, { cache: 'reload' }))); } catch { /* offline */ }
  location.replace(location.pathname + '?r=' + Date.now() + location.hash);
}

/* ============================================================ theme */
function applyTheme() {
  const t = store.get('theme') || 'system';
  if (t === 'system') document.documentElement.removeAttribute('data-theme'); else document.documentElement.setAttribute('data-theme', t);
}

/* ============================================================ push notifications + presence */
const urlB64 = (b) => { const p = '='.repeat((4 - b.length % 4) % 4); const r = atob((b + p).replace(/-/g, '+').replace(/_/g, '/')); return Uint8Array.from([...r].map((c) => c.charCodeAt(0))); };
const PUSH = { state: 'unknown', devices: 0, err: '' };
async function pushRefresh() {
  PUSH.err = '';
  if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) { PUSH.state = 'unsupported'; return; }
  if (Notification.permission === 'denied') { PUSH.state = 'blocked'; return; }
  try {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    PUSH.state = sub && Notification.permission === 'granted' ? 'on' : 'off';
    const k = await api('/api/push/key'); PUSH.devices = k.devices; if (!k.available) PUSH.state = 'server-off';
  } catch (e) { PUSH.err = e.message; }
}
async function pushEnable() {
  PUSH.err = '';
  try {
    if (Notification.permission !== 'granted' && (await Notification.requestPermission()) !== 'granted') { PUSH.state = 'blocked'; return; }
    const { key, available } = await api('/api/push/key');
    if (!available) { PUSH.state = 'server-off'; return; }
    const reg = await navigator.serviceWorker.ready;
    let sub = await reg.pushManager.getSubscription();
    if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlB64(key) });
    await api('/api/push/subscribe', { body: { subscription: sub.toJSON() } });
    toast('Notifications on');
  } catch (e) { PUSH.err = /standalone|home screen|not allowed/i.test(e.message) ? 'On iPhone, add the app to your Home Screen first, then turn this on from the installed app.' : e.message; }
  await pushRefresh();
}
async function pushDisable() {
  try {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub) { await api('/api/push/unsubscribe', { body: { endpoint: sub.endpoint } }); await sub.unsubscribe(); }
  } catch (e) { PUSH.err = e.message; }
  await pushRefresh();
}
async function pushTest() { try { const r = await api('/api/push/test', { method: 'POST' }); toast(r.sent ? 'Sent. Lock your phone to see it.' : 'No device is subscribed yet.'); } catch (e) { toast(e.message, true); } }
let lastPresence = null;
function sendPresence() {
  if (!S.token) return;
  const v = !document.hidden;
  lastPresence = v;
  fetch(S.api + '/api/presence', { method: 'POST', headers: { Authorization: 'Bearer ' + S.token, 'Content-Type': 'application/json' }, body: JSON.stringify({ visible: v }), keepalive: true }).catch(() => {});
}

/* ============================================================ routines */
const RT = { list: [], timer: null };
const DAYS = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];
const when = (ts) => { if (!ts) return ''; const d = ts * 1000 - Date.now(); const m = Math.round(Math.abs(d) / 60000); const t = m < 60 ? `${m} min` : m < 1440 ? `${Math.round(m / 60)} h` : `${Math.round(m / 1440)} d`; return d >= 0 ? `in ${t}` : `${t} ago`; };
function schedText(r) {
  const s = r.schedule || {};
  if (s.kind === 'every') return s.minutes % 60 === 0 ? `Every ${s.minutes / 60} h` : `Every ${s.minutes} min`;
  if (s.kind === 'daily') return `Daily at ${s.time}`;
  if (s.kind === 'weekly') return `${(s.days || []).map((d) => 'Mon Tue Wed Thu Fri Sat Sun'.split(' ')[d]).join(', ') || 'Weekly'} at ${s.time}`;
  return 'Webhook / manual only';
}
async function openRoutines() { $('#routinespage').hidden = false; await rtLoad(); clearInterval(RT.timer); RT.timer = setInterval(() => { if (!$('#routinespage').hidden) rtLoad(); }, 8000); }
function closeRoutines() { $('#routinespage').hidden = true; clearInterval(RT.timer); }
async function rtLoad() { try { RT.list = (await api('/api/routines')).routines; } catch (e) { toast(e.message, true); } rtDraw(); }
function rtDraw() {
  const box = $('#rtbody');
  if (!RT.list.length) { box.replaceChildren(h('div.evhead', {}, h('div.big', { text: 'No routines yet' }), h('div.dim', { text: 'A routine is a prompt the agent runs by itself: every few hours, every morning, or when a webhook is called (a smart-home event, a GitHub action, IFTTT). Results land in a chat and can ping you.' })), h('button.primary', { text: 'Create a routine', onclick: () => rtEdit(null) })); return; }
  box.replaceChildren(...RT.list.map((r) => h('div.rtcard' + (r.enabled ? '' : '.off'), {},
    h('div.rttop', {}, h('span.nm', { text: r.name }), r.running ? h('span.badge.run', { text: 'running' }) : r.last_status ? h('span.badge ' + (r.last_status === 'done' ? 'ok' : 'bad'), { text: r.last_status }) : null),
    h('div.rtmeta', { text: schedText(r) + (r.enabled && r.next_run ? ` · next ${when(r.next_run)}` : r.enabled ? '' : ' · paused') }),
    r.last_run ? h('div.rtmeta', { text: `Last run ${when(r.last_run)}` }) : null,
    r.last_text ? h('div.rtlast', { text: r.last_text }) : null,
    h('div.rtbtns', {}, h('button', { text: 'Run now', onclick: async () => { try { const x = await api(`/api/routines/${r.id}/run`, { method: 'POST' }); toast('Started'); await rtLoad(); closeRoutines(); openSession(x.chat); } catch (e) { toast(e.message, true); } } }),
      r.last_chat ? h('button', { text: 'Open chat', onclick: () => { closeRoutines(); closeSettings(); openSession(r.last_chat).catch((e) => toast(e.message, true)); } }) : null,
      h('button', { text: 'Edit', onclick: () => rtEdit(r) })))));
}
function rtEdit(r) {
  const isNew = !r;
  r = r || { name: '', prompt: '', mode: 'ask', chat: 'new', enabled: true, schedule: { kind: 'daily', time: '09:00', days: [0, 1, 2, 3, 4, 5, 6] }, model: '' };
  const f = { kind: (r.schedule || {}).kind || 'none', minutes: (r.schedule || {}).minutes || 60, time: (r.schedule || {}).time || '09:00', days: new Set((r.schedule || {}).days && r.schedule.days.length ? r.schedule.days : [0, 1, 2, 3, 4]), mode: r.mode, chat: r.chat, enabled: r.enabled };
  const name = h('input', { type: 'text', value: r.name, placeholder: 'e.g. Morning briefing' });
  const prompt = h('textarea', { placeholder: 'What should the agent do each time? e.g. Check my repo for failing CI and summarize.' }); prompt.value = r.prompt;
  const model = h('input', { type: 'text', value: r.model || '', placeholder: 'leave empty for the default model', autocapitalize: 'off', spellcheck: 'false' });
  const sched = h('div'); const hook = h('div');
  const seg = (opts, get, set) => { const el = h('div.seg'); const draw = () => el.replaceChildren(...opts.map(([k, n]) => h('button' + (get() === k ? '.on' : ''), { text: n, onclick: () => { set(k); draw(); drawSched(); } }))); draw(); return el; };
  const drawSched = () => {
    const parts = [];
    if (f.kind === 'every') { const n = h('input', { type: 'number', min: 5, value: f.minutes % 60 === 0 ? f.minutes / 60 : f.minutes }); const unit = f.minutes % 60 === 0 && f.minutes >= 60 ? 'h' : 'min'; const u = h('select', {}, h('option', { value: 'min', text: 'minutes', selected: unit === 'min' }), h('option', { value: 'h', text: 'hours', selected: unit === 'h' })); const sync = () => { f.minutes = Math.max(5, Math.round(+n.value * (u.value === 'h' ? 60 : 1)) || 60); }; n.oninput = sync; u.onchange = sync; parts.push(h('label', {}, 'Run every', h('div', { style: 'display:flex;gap:8px' }, n, u))); }
    if (f.kind === 'daily' || f.kind === 'weekly') { const t = h('input', { type: 'time', value: f.time }); t.oninput = () => { f.time = t.value || '09:00'; }; parts.push(h('label', {}, 'At (your local time)', t)); }
    if (f.kind === 'weekly') parts.push(h('div.days', {}, ...DAYS.map((d, i) => h('button' + (f.days.has(i) ? '.on' : ''), { text: d, onclick: (e) => { f.days.has(i) ? f.days.delete(i) : f.days.add(i); e.currentTarget.classList.toggle('on'); } }))));
    if (f.kind === 'none') parts.push(h('div.dim', { text: 'No schedule. Run it by hand or call its webhook.' }));
    sched.replaceChildren(...parts);
  };
  drawSched();
  if (!isNew) {
    const url = S.api + r.hook_path;
    hook.append(h('div.sec', { text: 'Webhook' }), h('div.dim', { style: 'font-size:13px', text: 'Anything that can call a URL (GET or POST) can start this routine. A POST body is passed in as untrusted data. Keep the link secret.' }), h('div.hookbox', { text: url }),
      h('div.rtbtns', {}, h('button', { text: 'Copy link', onclick: () => navigator.clipboard.writeText(url).then(() => toast('Copied')).catch(() => toast('Copy failed')) }), h('button', { text: 'New link', onclick: async () => { const x = await api('/api/routines/' + r.id, { method: 'PATCH', body: { regenerate_hook: true } }); r.hook_path = x.hook_path; toast('Old link no longer works'); rtEdit({ ...r, ...x }); } })));
  }
  const save = h('button.primary', { text: isNew ? 'Create routine' : 'Save', onclick: async () => {
    const body = { name: name.value, prompt: prompt.value, model: model.value.trim(), mode: f.mode, chat: f.chat, enabled: f.enabled, tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
      schedule: f.kind === 'every' ? { kind: 'every', minutes: f.minutes } : f.kind === 'none' ? { kind: 'none' } : { kind: f.kind, time: f.time, days: f.kind === 'weekly' ? [...f.days] : [] } };
    if (!body.prompt.trim()) { toast('Write what it should do', true); return; }
    save.disabled = true;
    try { await api(isNew ? '/api/routines' : '/api/routines/' + r.id, { method: isNew ? 'POST' : 'PATCH', body }); closeSheet(); await rtLoad(); } catch (e) { toast(e.message, true); save.disabled = false; }
  } });
  sheet(isNew ? 'New routine' : 'Edit routine',
    h('label', {}, 'Name', name), h('label', {}, 'Prompt', prompt),
    h('div.sec', { text: 'When' }), seg([['daily', 'Daily'], ['weekly', 'Weekly'], ['every', 'Every…'], ['none', 'Manual']], () => f.kind, (k) => { f.kind = k; }), sched,
    h('div.sec', { text: 'Permissions' }), seg([['plan', 'Read-only'], ['ask', 'Ask me'], ['auto', 'Auto-edit']], () => f.mode, (k) => { f.mode = k; }),
    h('div.dim', { style: 'font-size:12.5px', text: 'Unattended runs stop and ping you when they need approval. Read-only can look things up but not change anything.' }),
    h('div.sec', { text: 'Chat' }), seg([['new', 'New chat each run'], ['same', 'Same chat']], () => f.chat, (k) => { f.chat = k; }),
    h('label', {}, 'Model (optional)', model),
    h('label.switch', {}, h('span', { text: 'Enabled' }), (() => { const c = h('input', { type: 'checkbox', checked: f.enabled }); c.onchange = () => { f.enabled = c.checked; }; return c; })()),
    isNew ? null : hook, save,
    isNew ? null : h('button.secondary.danger', { text: 'Delete routine', onclick: async () => { if (!(await confirmSheet('Delete this routine?', 'Its past chats stay.', 'Delete', true))) return; await api('/api/routines/' + r.id, { method: 'DELETE' }); closeSheet(); await rtLoad(); } }));
}

/* ============================================================ connectors */
async function openConnectors() { $('#connpage').hidden = false; await cnLoad(); }
function closeConnectors() { $('#connpage').hidden = true; }
async function cnLoad(refresh) { try { CN.list = (await api(refresh ? '/api/connectors/refresh' : '/api/connectors', refresh ? { method: 'POST' } : {})).connectors; } catch (e) { toast(e.message, true); } cnDraw(); }
const CN = { list: [] };
function cnDraw() {
  const box = $('#cnbody');
  if (!CN.list.length) { box.replaceChildren(h('div.evhead', {}, h('div.big', { text: 'No connectors yet' }), h('div.dim', { text: 'A connector is a remote MCP server: it gives the agent extra tools, like your calendar, notes, a smart home, or any service that offers one. It needs an https URL that works from the internet.' })), h('button.primary', { text: 'Add a connector', onclick: cnAdd })); return; }
  box.replaceChildren(...CN.list.map((c) => h('div.rtcard', {},
    h('div.rttop', {}, h('span.nm', { text: c.name }), h('span.badge ' + (/^\d+ tools/.test(c.status) ? 'ok' : c.status === 'needs sign-in' ? 'run' : 'bad'), { text: /^\d+ tools/.test(c.status) ? c.status : c.status === 'needs sign-in' ? 'sign in' : 'problem' })),
    h('div.rtmeta', { text: c.url.replace(/^https:\/\//, '').slice(0, 60) + (c.source === 'env' ? ' · set on the server' : '') }),
    /^\d+ tools/.test(c.status) ? h('div.rtlast', { text: c.tools.join(', ') }) : c.status === 'needs sign-in' ? h('div.rtlast', { text: 'Tap Sign in, log in on the page that opens, then come back here.' }) : h('div.rtlast', { style: 'color:var(--bad)', text: c.status }),
    c.source === 'app' ? h('div.rtbtns', {}, c.oauth ? h('button', { text: c.signed_in ? 'Sign in again' : 'Sign in', onclick: () => cnLogin(c.name) }) : null, h('button', { text: 'Remove', onclick: async () => { if (!(await confirmSheet('Remove ' + c.name + '?', 'The agent loses those tools.', 'Remove', true))) return; try { CN.list = (await api('/api/connectors/' + c.name, { method: 'DELETE' })).connectors; cnDraw(); closeSheet(); } catch (e) { toast(e.message, true); } } })) : null)),
    h('button.secondary', { text: 'Reconnect all', onclick: () => cnLoad(true) }));
}
async function cnLogin(name) {
  const body = h('div', {}, h('div.dim', { text: 'Preparing the sign-in page…' }));
  sheet('Sign in to ' + name, body);
  try {
    const { url } = await api(`/api/connectors/${encodeURIComponent(name)}/login`, { body: { base: S.api } });
    body.replaceChildren(h('div.dim', { text: 'Opens the service\'s own login page. After you approve, come back to the app and it will connect by itself.' }),
      h('a.btn-link', { href: url, target: '_blank', rel: 'noopener', text: 'Continue to sign in', onclick: () => setTimeout(closeSheet, 400) }));
  } catch (e) { body.replaceChildren(h('div.dim', { style: 'color:var(--bad)', text: e.message })); }
}
function cnAdd() {
  let mode = 'token';
  const name = h('input', { type: 'text', placeholder: 'short name, e.g. notes', autocapitalize: 'off', spellcheck: 'false' });
  const url = h('input', { type: 'url', placeholder: 'https://example.com/mcp', autocapitalize: 'off', spellcheck: 'false' });
  const auth = h('input', { type: 'text', placeholder: 'e.g. Bearer abc123', autocapitalize: 'off', spellcheck: 'false' });
  const authLabel = h('label', {}, 'Authorization header (optional)', auth);
  const note = h('div.dim', { style: 'font-size:12.5px' });
  const go = h('button.primary', { text: 'Connect' });
  const seg = h('div.seg');
  const draw = () => {
    seg.replaceChildren(...[['token', 'Token / none'], ['oauth', 'Sign in (OAuth)']].map(([k, n]) => h('button' + (mode === k ? '.on' : ''), { text: n, onclick: () => { mode = k; draw(); } })));
    authLabel.hidden = mode === 'oauth';
    go.textContent = mode === 'oauth' ? 'Add and sign in' : 'Connect';
    note.textContent = mode === 'oauth' ? 'For services with a normal login page (Google, Notion, Linear and similar). You approve access on their site; no token to copy.' : 'The header value is kept in your private agent-state repo. Only connect servers you trust: the agent can call their tools.';
  };
  go.onclick = async () => {
    go.disabled = true; go.textContent = 'Connecting…';
    try {
      CN.list = (await api('/api/connectors', { body: { name: name.value, url: url.value, auth: auth.value, oauth: mode === 'oauth' } })).connectors; cnDraw();
      if (mode === 'oauth') { const n = name.value.toLowerCase().replace(/[^a-z0-9_-]/g, '').slice(0, 24); closeSheet(); await cnLogin(n); } else closeSheet();
    } catch (e) { toast(e.message, true); go.disabled = false; draw(); }
  };
  draw();
  sheet('Add connector', seg, h('label', {}, 'Name', name), h('label', {}, 'Server URL (https)', url), authLabel, note, go);
}

/* ============================================================ settings page */
function serverKV() {
  const inf = S.info || {};
  return h('dl.kv', {}, h('dt', { text: 'URL' }), h('dd', { text: S.api }), h('dt', { text: 'Providers' }), h('dd', { text: (inf.providers || []).join(', ') || 'none' }),
    h('dt', { text: 'History sync' }), h('dd', { text: inf.state ? (inf.state.enabled ? `on → ${inf.state.repo}` : `off${inf.state.error ? ' (' + inf.state.error.slice(0, 80) + ')' : ' (set GITHUB_TOKEN + STATE_REPO)'}`) : '?' }),
    h('dt', { text: 'GitHub' }), h('dd', { text: inf.github ? 'token set' : 'no token' }), h('dt', { text: 'Search' }), h('dd', { text: inf.search || '?' }),
    h('dt', { text: 'Tools' }), h('dd', { text: `git ${inf.git ? '✓' : '✕'} · ripgrep ${inf.rg ? '✓' : 'fallback'}` }));
}
let setNote = '';
function openSettings() { closeDrawer(); $('#settings').hidden = false; setNote = ''; drawSettings(); checkUpdate(); pushRefresh().then(drawSettings); }
function closeSettings() { $('#settings').hidden = true; }
function drawSettings() {
  const box = $('#setbody');
  if ($('#settings').hidden) return;
  const inf = S.info || {};
  const theme = store.get('theme') || 'system';
  const row = (ic, label, fn, right, cls = '') => h('button.mi' + cls, { onclick: fn }, h('span.mic', { text: ic }), h('span', { text: label }), right ? h('span.val', { text: right }) : null, h('span.chev', { text: '›' }));
  const seg = h('div.seg', {}, ...[['system', 'System'], ['light', 'Light'], ['dark', 'Dark']].map(([k, n]) => h('button' + (theme === k ? '.on' : ''), { text: n, onclick: () => { store.set('theme', k); applyTheme(); drawSettings(); } })));
  box.replaceChildren(
    h('div.setsec', { text: 'Appearance' }), h('div.setcard', {}, seg),
    h('div.setsec', { text: 'Agent' }), h('div.setcard', {},
      row('⏱', 'Routines', openRoutines),
      row('⚭', 'Connectors', openConnectors),
      row('✓', 'Capability check', openEval),
      row('🔔', 'Push notifications', async () => { if (PUSH.state === 'on') await pushDisable(); else await pushEnable(); drawSettings(); },
        { on: 'On', off: 'Off', blocked: 'Blocked', unsupported: 'Not supported', 'server-off': 'Server off', unknown: '…' }[PUSH.state] || ''),
      PUSH.state === 'on' ? row('✉', 'Send a test notification', pushTest, `${PUSH.devices} device${PUSH.devices === 1 ? '' : 's'}`) : null,
      PUSH.err ? h('div.dim', { style: 'padding:4px 12px 10px;font-size:13px', text: PUSH.err }) : null),
    h('div.setsec', { text: 'Server' }), h('div.setcard', {}, serverKV()),
    h('div.setsec', { text: 'About' }), h('div.setcard', {},
      h('dl.kv', {}, h('dt', { text: 'App' }), h('dd', { text: verText() }), h('dt', { text: 'Server' }), h('dd', { text: inf.version ? inf.version : 'unknown' })),
      UPD.available
        ? h('button.mi', { onclick: applyUpdate }, h('span.mic', { text: '⇣' }), h('span', { text: `Update to v${UPD.latest.version}` }), h('span.updot'))
        : h('button.mi', { onclick: async (e) => { e.currentTarget.disabled = true; const r = await checkUpdate(true); setNote = r === 'current' ? 'You are on the latest version.' : r === 'dev' ? 'Development build: no update check.' : r === 'error' ? 'Could not check. Are you online?' : ''; drawSettings(); } }, h('span.mic', { text: '↻' }), h('span', { text: 'Check for updates' })),
      setNote ? h('div.dim', { style: 'padding:4px 12px 10px;font-size:13px', text: setNote }) : null),
    h('div.setcard', {}, row('⎋', 'Sign out', () => { closeSettings(); logout(); }, '', '.danger')));
}

/* ============================================================ capability check page */
const EV = { ev: null, at: 0, timer: null, poll: null, err: '', busy: false };
const fmtT = (s) => { s = Math.max(0, Math.round(s)); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); };
async function openEval() {
  $('#evalpage').hidden = false;
  EV.err = ''; EV.busy = false;
  evDraw();
  await evLoad();
  clearInterval(EV.timer); EV.timer = setInterval(evDraw, 1000);
  evPoll();
}
function closeEval() { $('#evalpage').hidden = true; clearInterval(EV.timer); clearTimeout(EV.poll); EV.timer = EV.poll = null; }
async function evLoad() {
  try {
    let ev = null;
    const id = store.get('eval');
    if (id) { try { ev = await api('/api/eval/' + id); } catch { ev = null; } }
    if (!ev) ev = (await api('/api/eval-latest')).eval;
    EV.ev = ev; EV.at = Date.now(); EV.err = '';
  } catch (e) { EV.err = e.message; }
  evDraw();
}
function evPoll() {
  clearTimeout(EV.poll);
  if ($('#evalpage').hidden) return;
  EV.poll = setTimeout(async () => {
    if (EV.ev && EV.ev.status === 'running') {
      try { EV.ev = await api('/api/eval/' + EV.ev.id); EV.at = Date.now(); EV.err = ''; }
      catch (e) { EV.err = e.message === 'Not Found' ? 'The server restarted, so this run is gone. Start it again.' : 'Lost contact with the server (still trying): ' + e.message; if (e.message === 'Not Found') EV.ev = null; }
      evDraw();
    }
    evPoll();
  }, 1500);
}
async function evStart() {
  if (EV.busy) return;
  EV.busy = true; EV.err = ''; evDraw();
  try {
    const { id } = await api('/api/eval', { body: { model: (S.cur && S.cur.model) || '' } });
    store.set('eval', id);
    EV.ev = await api('/api/eval/' + id); EV.at = Date.now();
  } catch (e) { EV.err = e.message; }
  EV.busy = false; evDraw();
}
async function evStop() {
  if (!EV.ev) return;
  try { await api(`/api/eval/${EV.ev.id}/stop`, { method: 'POST' }); toast('Stopping after the current step…'); } catch (e) { toast(e.message, true); }
}
function evDraw() {
  const box = $('#evbody');
  if ($('#evalpage').hidden || !box) return;
  const ev = EV.ev;
  const parts = [];
  if (EV.err) parts.push(h('div.evwarn', { text: EV.err }));
  if (!ev) {
    parts.push(h('div.evhead', {}, h('div.big', { text: 'Not run yet' }),
      h('div.dim', { text: `Runs 4 real tasks (write code, debug, explore a codebase, build a package) on ${shortModel(S.cur && S.cur.model || S.info && S.info.default_model)} and checks each result with code. Runs on the server, so you can close this screen and come back.` })),
      h('button.primary', { text: EV.busy ? 'Starting…' : 'Run check', disabled: EV.busy, onclick: evStart }));
    box.replaceChildren(...parts); return;
  }
  const drift = (Date.now() - EV.at) / 1000;
  const running = ev.status === 'running';
  const done = ev.results.length;
  const passed = ev.results.filter((r) => r.passed).length;
  const live = running && ev.live ? ev.live : null;
  const liveFrac = live ? Math.min(0.9, (live.steps || 0) / 12) : 0;
  const pct = Math.round(((done + liveFrac) / ev.total) * 100);
  const head = running
    ? `Task ${Math.min(done + 1, ev.total)} of ${ev.total} · ${fmtT(ev.now - ev.started + drift)} elapsed`
    : (ev.status === 'stopped' ? `Stopped · ${passed}/${done} passed` : `Score ${passed}/${ev.total}`);
  parts.push(h('div.evhead', {}, h('div.big', { text: head }), h('div.dim', { text: shortModel(ev.model) + (running ? ' · running on the server' : '') })));
  parts.push(h('div.pbar' + (running ? '.run' : ''), {}, h('i', { style: `width:${running ? Math.max(pct, 3) : Math.round((done / ev.total) * 100)}%` })));
  ev.tasks.forEach((name, i) => {
    const r = ev.results[i];
    const isNow = running && live && live.index === i;
    const kids = [];
    if (r) {
      kids.push(h('div.evtop', {}, h('span.ic ' + (r.passed ? 'ev-ok' : 'ev-bad'), { text: r.passed ? '✓' : '✕' }), h('span.nm', { text: name }), h('span.tm', { text: `${r.steps} steps · ${r.seconds}s` })));
      if (r.note) kids.push(h('div.evnote', { text: (r.passed ? 'Check: ' : 'Why it failed: ') + r.note }));
    } else if (isNow) {
      kids.push(h('div.evtop', {}, h('span.ic', {}, h('span.spin')), h('span.nm', { text: name }), h('span.tm', { text: fmtT(ev.now - live.started + drift) })));
      kids.push(h('div.evlive', { text: `step ${live.steps || 0} · ${live.last || 'starting'}` }));
    } else {
      kids.push(h('div.evtop', {}, h('span.ic.ev-wait', { text: '○' }), h('span.nm.ev-wait', { text: name }), h('span.tm', { text: ev.status === 'stopped' ? 'skipped' : 'waiting' })));
    }
    parts.push(h('div.evtask' + (r && !r.passed ? '.fail' : '') + (isNow ? '.now' : ''), {}, ...kids));
  });
  if (running) parts.push(h('button.secondary.danger', { text: 'Stop', onclick: evStop }));
  else parts.push(h('button.primary', { text: EV.busy ? 'Starting…' : 'Run again', disabled: EV.busy, onclick: evStart }));
  box.replaceChildren(...parts);
}

/* ============================================================ live preview */
const PV = { path: '', url: '' };
async function openPreview(path) {
  if (!S.cur) return;
  closeSheet();
  PV.path = path;
  $('#previewpage').hidden = false;
  $('#pv-title').textContent = path;
  await loadPreview();
}
async function loadPreview() {
  const frame = $('#pv-frame');
  try {
    const { base } = await api(`/api/sessions/${S.cur.id}/preview-token`, { method: 'POST' });
    const rel = PV.path.replace(/^\/+/, '').split('/').map(encodeURIComponent).join('/');
    PV.url = S.api + base + rel;
    frame.src = PV.url;
  } catch (e) { toast(e.message, true); }
}
function closePreview() { $('#previewpage').hidden = true; $('#pv-frame').src = 'about:blank'; }

/* ============================================================ 3D viewer */
const V3 = { T: null, r: null, scene: null, cam: null, ctl: null, raf: 0, obj: null, path: '', ro: null, wire: false, spin: true };
async function openModel(path) {
  if (!S.cur) return;
  closeSheet();
  V3.path = path;
  const page = $('#modelpage'), wrap = $('#md-wrap');
  page.hidden = false;
  $('#md-title').textContent = path; $('#md-status').textContent = 'Loading…';
  wrap.replaceChildren(h('div.mdmsg', { text: 'Loading viewer…' }));
  try {
    const [T, oc] = await Promise.all([import('three'), import('three/addons/controls/OrbitControls.js')]);
    V3.T = T;
    const ext = path.split('.').pop().toLowerCase();
    const r = await fetch(`${S.api}/api/sessions/${S.cur.id}/download?path=${encodeURIComponent(path)}`, { headers: { Authorization: 'Bearer ' + S.token } });
    if (!r.ok) throw new Error('Could not load the file: HTTP ' + r.status);
    const buf = await r.arrayBuffer();
    let obj;
    if (ext === 'stl') {
      const { STLLoader } = await import('three/addons/loaders/STLLoader.js');
      const g = new STLLoader().parse(buf); g.computeVertexNormals();
      obj = new T.Mesh(g, new T.MeshStandardMaterial({ color: 0xa78bfa, metalness: 0.15, roughness: 0.55 }));
    } else if (ext === 'obj') {
      const { OBJLoader } = await import('three/addons/loaders/OBJLoader.js');
      obj = new OBJLoader().parse(new TextDecoder().decode(buf));
      obj.traverse((o) => { if (o.isMesh) o.material = new T.MeshStandardMaterial({ color: 0xa78bfa, metalness: 0.15, roughness: 0.55 }); });
    } else {
      const { GLTFLoader } = await import('three/addons/loaders/GLTFLoader.js');
      obj = (await new Promise((res, rej) => new GLTFLoader().parse(buf, '', res, rej))).scene;
    }
    wrap.replaceChildren();
    if (V3.r) { cancelAnimationFrame(V3.raf); V3.r.dispose(); }
    V3.r = new T.WebGLRenderer({ antialias: true, alpha: true });
    V3.r.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    V3.r.domElement.id = 'md-canvas';
    wrap.append(V3.r.domElement);
    V3.scene = new T.Scene();
    V3.scene.add(new T.HemisphereLight(0xffffff, 0x444466, 1.1));
    const key = new T.DirectionalLight(0xffffff, 1.6); key.position.set(3, 5, 4); V3.scene.add(key);
    const rim = new T.DirectionalLight(0xaabbff, 0.6); rim.position.set(-4, -2, -3); V3.scene.add(rim);
    // centre the model, put Z up the way CAD/STL files expect
    V3.obj = new T.Group(); V3.obj.add(obj); V3.scene.add(V3.obj);
    obj.rotation.x = ext === 'stl' ? -Math.PI / 2 : 0;
    const box = new T.Box3().setFromObject(V3.obj), size = box.getSize(new T.Vector3()), mid = box.getCenter(new T.Vector3());
    obj.position.sub(mid);
    const dim = Math.max(size.x, size.y, size.z) || 1;
    const grid = new T.GridHelper(dim * 2.4, 12, 0x888899, 0x444455); grid.position.y = -size.y / 2 - dim * 0.001; V3.scene.add(grid);
    V3.cam = new T.PerspectiveCamera(40, 1, dim / 100, dim * 100);
    V3.ctl = new oc.OrbitControls(V3.cam, V3.r.domElement);
    V3.ctl.enableDamping = true; V3.ctl.autoRotate = V3.spin; V3.ctl.autoRotateSpeed = 1.4;
    let tris = 0; V3.obj.traverse((o) => { if (o.isMesh) { const g = o.geometry; tris += (g.index ? g.index.count : g.attributes.position.count) / 3; } });
    $('#md-status').textContent = `${Math.round(tris).toLocaleString()} triangles · ${[size.x, size.y, size.z].map((n) => +n.toFixed(1)).join(' × ')}`;
    const fit = () => { const w = wrap.clientWidth || 1, hh = wrap.clientHeight || 1; V3.r.setSize(w, hh, false); V3.cam.aspect = w / hh; V3.cam.updateProjectionMatrix(); };
    fit(); if (V3.ro) V3.ro.disconnect(); V3.ro = new ResizeObserver(fit); V3.ro.observe(wrap);
    // back the camera off until the whole model fits in whichever direction (portrait phones are the narrow one)
    const vf = T.MathUtils.degToRad(V3.cam.fov), hf = 2 * Math.atan(Math.tan(vf / 2) * V3.cam.aspect);
    const dist = (size.length() / 2) / Math.sin(Math.min(vf, hf) / 2) * 1.12;
    V3.cam.position.set(0.62, 0.45, 0.78).normalize().multiplyScalar(dist);
    V3.ctl.minDistance = dist * 0.2; V3.ctl.maxDistance = dist * 6; V3.ctl.update();
    $('#md-wire').classList.toggle('on', V3.wire); $('#md-spin').classList.toggle('on', V3.spin);
    const loop = () => { V3.raf = requestAnimationFrame(loop); V3.ctl.update(); V3.r.render(V3.scene, V3.cam); };
    loop();
  } catch (e) {
    wrap.replaceChildren(h('div.mdmsg', { text: /import|module|fetch|Failed/i.test(e.message) ? 'The 3D viewer could not load (it needs internet for three.js, and iOS 16.4 or newer). ' + e.message : e.message }));
    $('#md-status').textContent = 'Error';
  }
}
function closeModel() { $('#modelpage').hidden = true; cancelAnimationFrame(V3.raf); if (V3.ro) V3.ro.disconnect(); if (V3.r) { V3.r.dispose(); V3.r = null; } $('#md-wrap').replaceChildren(); }
function toggleWire() { V3.wire = !V3.wire; if (V3.obj) V3.obj.traverse((o) => { if (o.isMesh && o.material) o.material.wireframe = V3.wire; }); $('#md-wire').classList.toggle('on', V3.wire); }
function toggleSpin() { V3.spin = !V3.spin; if (V3.ctl) V3.ctl.autoRotate = V3.spin; $('#md-spin').classList.toggle('on', V3.spin); }

/* ============================================================ wiring */
function wire() {
  $('#in-url').value = S.api || window.AGENT_API || '';
  $('#btn-connect').onclick = connect;
  $('#in-pw').addEventListener('keydown', (e) => { if (e.key === 'Enter') connect(); });
  $('#btn-menu').onclick = openDrawer;
  $('#scrim').onclick = closeDrawer;
  $('#sheet-scrim').onclick = closeSheet;
  $('#sheet-close').onclick = closeSheet;
  $('#btn-new').onclick = newChatSheet;
  $('#btn-empty-new').onclick = newChatSheet;
  $('#chip-model').onclick = modelSheet;
  $('#chip-mode').onclick = modeSheet;
  $('#btn-more').onclick = openMenu;
  $('#btn-files').onclick = () => { if (S.cur) filesSheet(''); else toast('Open a chat first'); };
  $('#menu-scrim').onclick = closeMenu;
  $('#ev-back').onclick = closeEval;
  $('#set-back').onclick = closeSettings;
  $('#rt-back').onclick = closeRoutines;
  $('#rt-new').onclick = () => rtEdit(null);
  $('#cn-back').onclick = closeConnectors;
  $('#cn-new').onclick = cnAdd;
  $('#pv-back').onclick = closePreview;
  $('#md-back').onclick = closeModel;
  $('#md-wire').onclick = toggleWire;
  $('#md-spin').onclick = toggleSpin;
  $('#md-dl').onclick = () => downloadPath(V3.path);
  $('#pv-reload').onclick = loadPreview;
  $('#pv-open').onclick = () => { if (PV.url) window.open(PV.url, '_blank', 'noopener'); };
  $('#dr-settings').onclick = openSettings;
  $('#dr-eval').onclick = () => { closeDrawer(); openEval(); };
  $('#ver').onclick = () => { if (UPD.available) applyUpdate(); else openSettings(); };
  $('#btn-update').onclick = applyUpdate;
  $('#chat-search').oninput = renderList;
  $('#btn-send').onclick = send;
  $('#btn-attach').onclick = () => $('#file-img').click();
  $('#file-img').onchange = (e) => { [...e.target.files].forEach((f) => attachAny(f).catch((er) => toast(er.message, true))); e.target.value = ''; };
  const input = $('#input');
  input.addEventListener('input', () => { autoGrow(); slashUpdate(); updateSend(); });
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); send(); } });
  input.addEventListener('paste', (e) => { const f = [...(e.clipboardData?.files || [])][0]; if (f) { e.preventDefault(); attachAny(f); } });
  chat().addEventListener('scroll', () => { S.stick = nearBottom(); $('#btn-jump').hidden = S.stick; });
  $('#btn-jump').onclick = () => { S.stick = true; scrollDown(true); };
  initLongPress(); initSheetSwipe(); initEdgeSwipe();
  document.addEventListener('visibilitychange', () => { if (!document.hidden && !$('#connpage').hidden) cnLoad(true); });
  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-copy]');
    if (b) { navigator.clipboard.writeText(b.parentElement.querySelector('code').textContent).then(() => { b.textContent = 'copied'; setTimeout(() => (b.textContent = 'copy'), 1200); }).catch(() => toast('Copy failed')); }
  });
  document.addEventListener('visibilitychange', async () => {
    sendPresence();
    if (!document.hidden) checkUpdate();
    if (document.hidden || !S.token || !S.cur) return;
    try { await loadSessions(); if (S.run.running || !S.finished) { await openSession(S.cur.id); } } catch { /* offline */ }
  });
  // keep the free-tier server awake while the app is open and visible
  setInterval(() => { if (!document.hidden && S.token) fetch(S.api + '/health').catch(() => {}); }, 4 * 60 * 1000);
  if ('serviceWorker' in navigator && location.protocol.startsWith('http')) {
    navigator.serviceWorker.register('sw.js').catch(() => {});
    navigator.serviceWorker.addEventListener('message', (e) => { if (e.data && e.data.type === 'open-chat' && e.data.sid && S.token) openSession(e.data.sid).catch(() => {}); });
  }
  setInterval(() => { if (!document.hidden) sendPresence(); }, 25000);
  window.addEventListener('pagehide', sendPresence);
}

applyTheme();
S.api = store.get('url') || window.AGENT_API || '';
S.token = store.get('token');
wire();
if (S.token && S.api) start(); else show('setup');
updateUI();
setTimeout(checkUpdate, 1500);
setTimeout(sendPresence, 800);
setInterval(checkUpdate, 5 * 60 * 1000);
S.checkUpdate = checkUpdate;
window.__agent = S; // handy for debugging in devtools
})();
