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
  images: [], files: [], live: null, statusEl: null, stick: true, info: null, commands: [],
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
async function loadSessions() {
  S.sessions = await api('/api/sessions');
  renderList();
}
async function openSession(id) {
  closeDrawer();
  if (S.abort) S.abort.abort();
  S.streaming = false;
  const d = await api('/api/sessions/' + id);
  S.cur = d.session; S.run = d.run; S.commands = d.commands || [];
  S.pending = {}; for (const p of d.run.pending || []) S.pending[p.id] = p;
  S.partial = d.run.partial || { text: '', reasoning: '' };
  S.lastSeq = d.run.seq || 0;
  S.finished = !d.run.running;
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
  web_fetch: '◎', web_search: '◎', todo_write: '☑', task: '⋔', skill: '★', git_sync: '⎇', create_pr: '⎇', exit_plan_mode: '☰', share_file: '⬇' };
function toolSummary(name, a) {
  a = a || {};
  switch (name) {
    case 'read': case 'write': case 'edit': case 'multiedit': return a.path || '';
    case 'bash': return (a.command || '').split('\n')[0];
    case 'share_file': return a.path || '';
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
  const open = approval ? true : (card.dataset.open === '1' || !!(result && result._file));
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
  const n = h('div.msg-user');
  const c = m.content;
  const text = typeof c === 'string' ? c : (c || []).filter((p) => p.type === 'text').map((p) => p.text).join('\n');
  const imgs = Array.isArray(c) ? c.filter((p) => p.type === 'image_url') : [];
  if (imgs.length) n.append(h('div.imgs', {}, imgs.map((p) => h('img', { src: p.image_url.url, alt: 'attachment' }))));
  n.append(document.createTextNode(text));
  if (m._checkpoint && !S.run.running) n.append(h('button.rewind', { title: 'Rewind to here', 'aria-label': 'Rewind to here', text: '↺', onclick: () => rewind(idx) }));
  return n;
}
function assistantNode(m) {
  const n = h('div.msg-ai');
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
    } else if (m.role === 'assistant') box.append(assistantNode(m));
  });
  renderTodos();
  renderLive();
  for (const ev of Object.values(S.pending)) ensureApprovalCard(ev);
  S.stick = true;
  if (forceScroll !== false) requestAnimationFrame(() => scrollDown(true));
  updateSend();
}
let liveRaf = 0;
function renderLive() {
  if (!S.run.running) { if (S.live) S.live.remove(); S.live = null; if (S.statusEl) S.statusEl.remove(); S.statusEl = null; return; }
  if (!S.live) { S.live = h('div.msg-ai'); msgsEl().append(S.live); }
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
      if (document.hidden && 'Notification' in window && Notification.permission === 'granted') new Notification('Agent needs approval', { body: ev.tool });
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
  updateSend(); applyChips();
}
async function reloadCurrent() {
  const d = await api('/api/sessions/' + S.cur.id);
  S.cur = d.session;
  renderChat(false);
}

/* ============================================================ composer */
function updateSend() {
  const b = $('#btn-send');
  const running = S.run.running;
  b.textContent = running ? '■' : '↑';
  b.classList.toggle('stop', running);
  b.setAttribute('aria-label', running ? 'Stop' : 'Send');
}
function autoGrow() { const t = $('#input'); t.style.height = 'auto'; t.style.height = Math.min(t.scrollHeight, window.innerHeight * 0.38) + 'px'; }
async function send() {
  if (S.run.running) { try { await api(`/api/sessions/${S.cur.id}/stop`, { method: 'POST' }); } catch (e) { toast(e.message, true); } return; }
  const input = $('#input');
  const text0 = input.value.trim();
  if (!text0 && !S.images.length && !S.files.length) return;
  if (S.files.some((f) => f.state === 'uploading')) { toast('Still uploading…'); return; }
  if (!S.cur) { try { await newChat({}); } catch (e) { toast(e.message, true); return; } }
  const images = S.images.slice();
  const notes = S.files.filter((f) => f.state === 'ready').map((f) => f.extracted ? `${f.path} (extracted to ${f.extracted}/, ${f.files} files)` : f.path);
  let text = text0;
  if (notes.length) text = (text ? text + '\n\n' : 'See the uploaded files.\n\n') + `[Files uploaded to the workspace: ${notes.join('; ')}]`;
  input.value = ''; autoGrow(); S.images = []; S.files = []; renderAttach(); hideSlash();
  const msg = { role: 'user', content: images.length ? [{ type: 'text', text }, ...images.map((u) => ({ type: 'image_url', image_url: { url: u } }))] : text };
  S.cur.messages.push(msg);
  msgsEl().append(userNode(msg, S.cur.messages.length - 1));
  $('#empty').hidden = true;
  S.run = { running: true }; S.partial = { text: '', reasoning: '' }; S.finished = false; S.stick = true;
  renderLive(); applyChips(); updateSend(); scrollDown(true);
  try {
    const r = await api(`/api/sessions/${S.cur.id}/messages`, { body: { text, images } });
    if (r.compacted) { S.run.running = false; renderLive(); await reloadCurrent(); updateSend(); toast('Context compacted'); return; }
    S.lastSeq = r.seq;
    attach();
  } catch (e) {
    S.run.running = false; S.finished = true; renderLive(); updateSend();
    toast(e.message, true, 6000);
    if (/still working/.test(e.message)) openSession(S.cur.id);
  }
}
function renderAttach() {
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
async function rewind(idx) {
  if (!(await confirmSheet('Rewind to here?', 'Removes this message and everything after it, and restores your files to how they were before it.', 'Rewind'))) return;
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
        h('button.dl', { 'aria-label': 'Download ' + it.name, text: '⬇', onclick: () => downloadPath(full) })));
    }
    if (!d.items.length) body.append(h('div.dim', { text: 'Empty' }));
  } catch (e) { body.replaceChildren(h('div.status.err', { text: e.message })); }
}
async function fileView(path) {
  const pre = h('div.codeview', { text: 'Loading…' });
  sheet(path, pre, h('button.secondary', { text: '⬇ Download', onclick: () => downloadPath(path) }), h('button.secondary', { text: '← Back', onclick: () => filesSheet(path.split('/').slice(0, -1).join('/')) }));
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
      item('⎇', 'Save workspace to GitHub', async () => { try { const r = await api(`/api/sessions/${c.id}/sync`, { method: 'POST' }); toast(r.result); } catch (er) { toast(er.message, true); } }),
      h('div.msep'));
  }
  rows.push(
    item('✓', 'Capability check', openEval),
    item('ⓘ', 'Server info', serverSheet),
    item('🔔', 'Approval notifications', async () => { if ('Notification' in window) toast((await Notification.requestPermission()) === 'granted' ? 'Notifications on' : 'Blocked'); else toast('Not supported here'); }),
    h('div.msep'),
    item('⎋', 'Sign out', logout, '.danger'));
  menu.replaceChildren(...rows);
  menu.hidden = false; $('#menu-scrim').hidden = false;
}
function serverSheet() {
  const inf = S.info || {};
  sheet('Server info',
    h('dl.kv', {}, h('dt', { text: 'URL' }), h('dd', { text: S.api }), h('dt', { text: 'Providers' }), h('dd', { text: (inf.providers || []).join(', ') || 'none' }),
      h('dt', { text: 'History sync' }), h('dd', { text: inf.state ? (inf.state.enabled ? `on → ${inf.state.repo}` : `off${inf.state.error ? ' (' + inf.state.error.slice(0, 80) + ')' : ' (set GITHUB_TOKEN + STATE_REPO)'}`) : '?' }),
      h('dt', { text: 'GitHub' }), h('dd', { text: inf.github ? 'token set' : 'no token' }), h('dt', { text: 'Search' }), h('dd', { text: inf.search || '?' }),
      h('dt', { text: 'Tools' }), h('dd', { text: `git ${inf.git ? '✓' : '✕'} · ripgrep ${inf.rg ? '✓' : 'fallback'}` })));
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
  $('#chat-search').oninput = renderList;
  $('#btn-send').onclick = send;
  $('#btn-attach').onclick = () => $('#file-img').click();
  $('#file-img').onchange = (e) => { [...e.target.files].forEach((f) => attachAny(f).catch((er) => toast(er.message, true))); e.target.value = ''; };
  const input = $('#input');
  input.addEventListener('input', () => { autoGrow(); slashUpdate(); });
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); send(); } });
  input.addEventListener('paste', (e) => { const f = [...(e.clipboardData?.files || [])][0]; if (f) { e.preventDefault(); attachAny(f); } });
  chat().addEventListener('scroll', () => { S.stick = nearBottom(); $('#btn-jump').hidden = S.stick; });
  $('#btn-jump').onclick = () => { S.stick = true; scrollDown(true); };
  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-copy]');
    if (b) { navigator.clipboard.writeText(b.parentElement.querySelector('code').textContent).then(() => { b.textContent = 'copied'; setTimeout(() => (b.textContent = 'copy'), 1200); }).catch(() => toast('Copy failed')); }
  });
  document.addEventListener('visibilitychange', async () => {
    if (document.hidden || !S.token || !S.cur) return;
    try { await loadSessions(); if (S.run.running || !S.finished) { await openSession(S.cur.id); } } catch { /* offline */ }
  });
  // keep the free-tier server awake while the app is open and visible
  setInterval(() => { if (!document.hidden && S.token) fetch(S.api + '/health').catch(() => {}); }, 4 * 60 * 1000);
  if ('serviceWorker' in navigator && location.protocol.startsWith('http')) navigator.serviceWorker.register('sw.js').catch(() => {});
}

S.api = store.get('url') || window.AGENT_API || '';
S.token = store.get('token');
wire();
if (S.token && S.api) start(); else show('setup');
window.__agent = S; // handy for debugging in devtools
})();
