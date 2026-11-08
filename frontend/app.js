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
const SIZES = { s: 's', small: 's', m: 'm', medium: 'm', l: 'l', large: 'l', full: 'l' };
function capSize(alt) {
  const m = String(alt).match(/^(.*?)\s*\|\s*(\w+)\s*$/);
  return m && SIZES[m[2].toLowerCase()] ? { cap: m[1], size: SIZES[m[2].toLowerCase()] } : { cap: alt, size: 'm' };
}
const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
function inline(s) {
  s = esc(s);
  const codes = [];
  s = s.replace(/`([^`\n]+)`/g, (_, c) => { codes.push(c); return '\u0000' + (codes.length - 1) + '\u0000'; });
  s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>').replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, '$1<em>$2</em>');
  s = s.replace(/!\[([^\]]*)\]\(ws:([^)\s]+)\)/g, (_, alt, p) => { const c = capSize(alt); return `<span class="ws-inl" data-ws="${p}" data-label="${c.cap}" title="${c.cap}"></span>`; });
  s = s.replace(/\[([^\]]+)\]\(ws:([^)\s]+)\)/g, (_, label, p) => `<a class="ws-chip" data-ws="${p}" data-label="${label}">\uD83D\uDCCE ${label}</a>`);
  s = s.replace(/\[([^\]]+)\]\(app:([^)\s]+)\)/g, (_, label, id) => `<a class="app-chip" data-app="${id}" data-label="${label}">\u25A6 ${label}</a>`);
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
  const isBlockStart = (l) => /^\s*!?\[[^\]]*\]\((?:ws|app):[^)\s]+\)\s*$/.test(l) || /^\s*```/.test(l) || /^#{1,4}\s/.test(l) || /^\s*>/.test(l) || /^\s*([-*+]|\d+\.)\s+/.test(l) || /^\s*([-*_])\1\1+\s*$/.test(l);
  while (i < lines.length) {
    const l = lines[i];
    let m;
    if ((m = l.match(/^\s*```([\w+-]*)\s*$/))) {
      const buf = [];
      i++;
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) buf.push(lines[i++]);
      i++;
      out.push(codeBlock(buf.join('\n'), m[1]));
    } else if (/^\s*!\[[^\]]*\]\(ws:[^)\s]+\)\s*$/.test(l)) {
      const figs = [];
      while (i < lines.length && (m = lines[i].match(/^\s*!\[([^\]]*)\]\(ws:([^)\s]+)\)\s*$/))) { const c = capSize(m[1]); figs.push(`<figure class="ws-img sz-${c.size}" data-ws="${esc(m[2])}" data-label="${esc(c.cap)}"><div class="ws-ph"></div>${c.cap ? `<figcaption>${esc(c.cap)}</figcaption>` : ''}</figure>`); i++; }
      out.push(`<div class="ws-gal n${Math.min(figs.length, 4)}">${figs.join('')}</div>`);
    } else if (/^\s*\[[^\]]+\]\(ws:[^)\s]+\)\s*$/.test(l)) {
      const cards = [];
      while (i < lines.length && (m = lines[i].match(/^\s*\[([^\]]+)\]\(ws:([^)\s]+)\)\s*$/))) { cards.push(`<div class="ws-card" data-ws="${esc(m[2])}" data-label="${esc(m[1])}"></div>`); i++; }
      out.push(cards.join(''));
    } else if (/^\s*\[[^\]]+\]\(app:[^)\s]+\)\s*$/.test(l)) {
      const cards = [];
      while (i < lines.length && (m = lines[i].match(/^\s*\[([^\]]+)\]\(app:([^)\s]+)\)\s*$/))) { cards.push(`<div class="app-card" data-app="${esc(m[2])}" data-label="${esc(m[1])}"></div>`); i++; }
      out.push(cards.join(''));
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

/* ============================================================ files + images embedded in answers (ws: links) */
const WS = { blobs: {}, urls: {}, stats: {} };
const wsPath = (p) => { try { return decodeURIComponent(p); } catch { return p; } };
const fmtSize = (n) => (n == null ? '' : n < 1024 ? n + ' B' : n < 1048576 ? Math.round(n / 1024) + ' KB' : (n / 1048576).toFixed(1) + ' MB');
async function wsBlob(path) {
  const key = S.cur.id + '|' + path;
  if (WS.blobs[key]) return WS.blobs[key];
  const p = fetch(`${S.api}/api/sessions/${S.cur.id}/download?path=${encodeURIComponent(path)}`, { headers: { Authorization: 'Bearer ' + S.token } })
    .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.blob(); }).then((b) => { const u = URL.createObjectURL(b); WS.urls[key] = u; return u; });
  WS.blobs[key] = p;
  p.catch(() => { delete WS.blobs[key]; });
  return p;
}
async function wsStat(paths) {
  const need = paths.filter((p) => !WS.stats[S.cur.id + '|' + p]);
  if (need.length) {
    try {
      const r = await api(`/api/sessions/${S.cur.id}/stat?` + need.map((p) => 'paths=' + encodeURIComponent(p)).join('&'));
      for (const f of r.files) WS.stats[S.cur.id + '|' + f.path] = f;
    } catch { /* offline */ }
  }
  return paths.map((p) => WS.stats[S.cur.id + '|' + p] || { path: p, exists: false });
}
const KIND_IC = { image: '\u25A3', html: '\u25B6', model: '\u25C8', pdf: '\u2630', audio: '\u266A', video: '\u25B7', archive: '\u29C9', sheet: '\u25A6', doc: '\u2630', text: '\u2261', folder: '\u25A4', other: '\u25A2' };
function fileCardNode(path, label, st) {
  const card = h('div.ws-card', { 'data-ws': path });
  const draw = (f) => {
    card.replaceChildren();
    if (!f.exists) { card.append(h('div.ws-ico', { text: '?' }), h('div.ws-meta', {}, h('div.ws-name', { text: label || path.split('/').pop() }), h('div.ws-sub', { text: 'file not found' }))); card.classList.add('missing'); return; }
    const kind = f.kind, btns = h('div.ws-btns');
    if (kind === 'image') btns.append(h('button', { text: 'View', onclick: () => openLightbox([path], 0) }));
    if (kind === 'html') btns.append(h('button', { text: 'Preview', onclick: () => openPreview(path) }));
    if (kind === 'model') btns.append(h('button', { text: 'Open 3D', onclick: () => openModel(path) }));
    if (kind === 'text' || kind === 'sheet') btns.append(h('button', { text: 'View', onclick: () => fileViewer(path) }));
    if (kind === 'audio' || kind === 'video') btns.append(h('button', { text: 'Play', onclick: async (e) => { const b = e.currentTarget; b.disabled = true; try { const u = await wsBlob(path); const m = h(kind === 'audio' ? 'audio' : 'video', { src: u, controls: true, playsinline: true }); card.append(m); m.play().catch(() => {}); b.remove(); } catch { toast('Could not load the file', true); b.disabled = false; } } }));
    btns.append(h('button.dl', { text: kind === 'folder' ? 'Download .zip' : 'Download', onclick: () => downloadPath(path) }));
    card.append(h('div.ws-ico', { text: KIND_IC[kind] || KIND_IC.other }),
      h('div.ws-meta', {}, h('div.ws-name', { text: label || f.name }), h('div.ws-sub', { text: kind === 'folder' ? `folder · ${f.count} files` : [f.name !== (label || f.name) ? f.name : '', fmtSize(f.size)].filter(Boolean).join(' · ') })), btns);
  };
  if (st) draw(st); else { card.append(h('div.ws-ico', { text: '…' }), h('div.ws-meta', {}, h('div.ws-name', { text: label || path.split('/').pop() }))); wsStat([path]).then(([f]) => draw(f)); }
  return card;
}
async function fileViewer(path) {
  const pre = h('div.codeview', { text: 'Loading…' });
  sheet(path.split('/').pop(), pre, h('button.secondary', { text: '\u2B07 Download', onclick: () => downloadPath(path) }));
  try { const d = await api(`/api/sessions/${S.cur.id}/file?path=${encodeURIComponent(path)}`); pre.textContent = d.binary ? `(binary file, ${d.size} bytes)` : d.text + (d.truncated ? '\n… truncated' : ''); } catch (e) { pre.textContent = e.message; }
}
function hydrateWs(root) {
  if (!S.cur) return;
  for (const fig of root.querySelectorAll('figure.ws-img')) {
    const path = wsPath(fig.dataset.ws), key = S.cur.id + '|' + path;
    const done = (u) => {
      const img = h('img', { src: u, alt: fig.dataset.label || path, onclick: () => {
        const group = [...(fig.closest('.ws-gal') || fig).parentNode.querySelectorAll('figure.ws-img')].map((f) => wsPath(f.dataset.ws));
        openLightbox(group, Math.max(0, group.indexOf(path)));
      } });
      const ph = fig.querySelector('.ws-ph'); if (ph) ph.replaceWith(img);
    };
    if (WS.urls[key]) done(WS.urls[key]);
    else wsBlob(path).then(done).catch(() => { const ph = fig.querySelector('.ws-ph'); if (ph) ph.replaceWith(h('div.ws-miss', { text: 'image not found: ' + path })); });
  }
  for (const sp of root.querySelectorAll('span.ws-inl')) {
    const path = wsPath(sp.dataset.ws);
    const miss = () => { sp.classList.add('miss'); sp.textContent = '\u25A3 ' + (sp.dataset.label || path.split('/').pop()); };
    wsStat([path]).then(([f]) => { if (!f.exists || f.kind !== 'image') throw 0; return wsBlob(path); })
      .then((u) => { sp.replaceChildren(h('img', { src: u, alt: sp.dataset.label || path, onclick: () => openLightbox([path], 0) })); })
      .catch(miss);
  }
  const cards = [...root.querySelectorAll('div.ws-card[data-ws]')];
  if (cards.length) wsStat(cards.map((c) => wsPath(c.dataset.ws))).then((fs) => cards.forEach((c, i) => c.replaceWith(fileCardNode(wsPath(c.dataset.ws), c.dataset.label, fs[i]))));
  for (const chip of root.querySelectorAll('a.ws-chip')) {
    chip.onclick = async () => {
      const path = wsPath(chip.dataset.ws), [f] = await wsStat([path]);
      if (!f.exists) { toast('File not found: ' + path, true); return; }
      if (f.kind === 'image') openLightbox([path], 0);
      else sheet(chip.dataset.label || f.name, fileCardNode(path, chip.dataset.label, f));
    };
  }
}
/* ---- apps embedded in answers: [Name](app:ID) (the id or the app's name) */
const APPS = { list: null, at: 0 };
async function appList(force) {
  if (force || !APPS.list || Date.now() - APPS.at > 30000) { try { APPS.list = (await api('/api/apps')).apps; APPS.at = Date.now(); } catch { APPS.list = APPS.list || []; } }
  return APPS.list;
}
const findApp = (list, key) => { const k = String(key).toLowerCase().replace(/[^a-z0-9]+/g, ''); return list.find((a) => a.id === key) || list.find((a) => a.name.toLowerCase().replace(/[^a-z0-9]+/g, '') === k); };
function hydrateApps(root) {
  const cards = [...root.querySelectorAll('div.app-card[data-app]')], chips = [...root.querySelectorAll('a.app-chip')];
  if (!cards.length && !chips.length) return;
  const resolve = async (key) => { let a = findApp(await appList(), key); if (!a) a = findApp(await appList(true), key); return a; };
  for (const c of cards) resolve(c.dataset.app).then((a) => {
    if (!a) { c.replaceChildren(h('div.ws-ico', { text: '?' }), h('div.ws-meta', {}, h('div.ws-name', { text: c.dataset.label }), h('div.ws-sub', { text: 'app not found' }))); c.classList.add('missing'); return; }
    c.replaceChildren(h('div.ws-ico', { text: a.icon || '▦' }), h('div.ws-meta', {}, h('div.ws-name', { text: a.name }), h('div.ws-sub', { text: a.description || 'App' })),
      h('div.ws-btns', {}, h('button', { text: 'Open app', onclick: () => openApp(a.id, a.name) })));
  });
  for (const ch of chips) ch.onclick = async () => { const a = await resolve(ch.dataset.app); if (a) openApp(a.id, a.name); else toast('App not found: ' + ch.dataset.label, true); };
}
function prose(text, extra = '') {
  const n = h('div.prose' + extra, { html: md(text) });
  hydrateWs(n);
  hydrateApps(n);
  return n;
}
/* lightbox: full-screen image viewer, swipe between images, tap to zoom */
const LB = { paths: [], i: 0, x0: 0 };
function openLightbox(paths, i) {
  LB.paths = paths; LB.i = i;
  let el = $('#lightbox');
  if (!el) {
    el = h('div', { id: 'lightbox' }, h('div.lb-top', {}, h('button.lb-x', { 'aria-label': 'Close', text: '\u2715', onclick: closeLightbox }), h('span.lb-count'), h('button.lb-dl', { text: 'Download', onclick: () => downloadPath(LB.paths[LB.i]) })), h('div.lb-stage', {}, h('img.lb-img', { alt: '' })), h('div.lb-cap'));
    el.addEventListener('touchstart', (e) => { LB.x0 = e.touches[0].clientX; }, { passive: true });
    el.addEventListener('touchend', (e) => { const dx = e.changedTouches[0].clientX - LB.x0; if (Math.abs(dx) > 60 && !el.classList.contains('zoom')) lbStep(dx < 0 ? 1 : -1); }, { passive: true });
    el.querySelector('.lb-img').onclick = () => el.classList.toggle('zoom');
    document.addEventListener('keydown', (e) => { if ($('#lightbox') && !$('#lightbox').hidden) { if (e.key === 'Escape') closeLightbox(); if (e.key === 'ArrowRight') lbStep(1); if (e.key === 'ArrowLeft') lbStep(-1); } });
    document.body.append(el);
  }
  el.hidden = false; el.classList.remove('zoom'); lbShow();
}
function lbStep(d) { const n = LB.paths.length; if (n < 2) return; LB.i = (LB.i + d + n) % n; $('#lightbox').classList.remove('zoom'); lbShow(); }
async function lbShow() {
  const el = $('#lightbox'), path = LB.paths[LB.i];
  el.querySelector('.lb-count').textContent = LB.paths.length > 1 ? `${LB.i + 1} / ${LB.paths.length}` : '';
  el.querySelector('.lb-cap').textContent = path.split('/').pop();
  const img = el.querySelector('.lb-img'); img.removeAttribute('src');
  try { const u = await wsBlob(path); if (LB.paths[LB.i] === path) img.src = u; } catch { toast('Could not load the image', true); }
}
function closeLightbox() { const el = $('#lightbox'); if (el) el.hidden = true; }

/* ============================================================ state + api */
const S = {
  api: '', token: '', sessions: [], cur: null, run: { running: false }, partial: { text: '', reasoning: '' },
  pending: {}, results: {}, cards: {}, lastSeq: 0, streaming: false, finished: true, abort: null, activity: [], currentActivity: null,
  images: [], files: [], live: null, statusEl: null, stick: true, info: null, commands: [], queues: {}, subs: [],
};
const EFFORTS = {
  low: ['Low', 'Lower effort for straightforward tasks; faster and lighter.'],
  medium: ['Medium', 'Balanced effort for ordinary implementation and analysis.'],
  high: ['High', 'More effort for difficult implementation, debugging, and planning.'],
  max: ['Max', 'Maximum effort for the hardest tasks; slowest and most expensive.'],
  auto: ['Auto', 'Choose effort from task complexity, independently of model routing.'],
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
/* ============================================================ what's new */
const WN = { list: null };
async function loadChangelog() {
  if (WN.list) return WN.list;
  try { const r = await fetch('changelog.json?t=' + Date.now(), { cache: 'no-store' }); WN.list = r.ok ? await r.json() : []; } catch { WN.list = []; }
  return WN.list;
}
function whatsNewNodes(entries) {
  return entries.flatMap((e) => [h('div', { style: 'margin-top:12px;font-weight:700', text: e.title }), h('div.dim', { style: 'font-size:12.5px;margin-bottom:4px', text: e.date }),
    h('ul', { style: 'margin:4px 0 0;padding-left:20px' }, ...e.items.map((t) => h('li', { style: 'margin:3px 0', text: t })))]);
}
async function maybeWhatsNew() {
  const list = await loadChangelog();
  if (!list.length) return;
  const top = Math.max(...list.map((e) => e.id));
  const seen = parseInt(store.get('seen_changelog') || '0', 10);
  store.set('seen_changelog', String(top));
  if (!seen || seen >= top) return;     // first launch: nothing to compare against
  const fresh = list.filter((e) => e.id > seen).sort((a, b) => b.id - a.id).slice(0, 4);
  if (fresh.length) sheet("What's new", ...whatsNewNodes(fresh), h('button.primary', { text: 'Got it', onclick: closeSheet }));
}
async function openWhatsNew() {
  const list = (await loadChangelog()).slice().sort((a, b) => b.id - a.id);
  sheet("What's new", ...(list.length ? whatsNewNodes(list.slice(0, 8)) : [h('div.dim', { text: 'Nothing to show yet.' })]));
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
    const wl = location.hash.startsWith('#world=') ? location.hash.slice(7) : '';
    if (wl) history.replaceState(null, '', location.pathname);
    const last = location.hash.startsWith('#chat=') ? location.hash.slice(6) : store.get('cur');
    if (last && S.sessions.some((s) => s.id === last)) await openSession(last);
    else if (S.sessions.length) await openSession(S.sessions[0].id);
    else renderChat();
    if (wl) openWorldFromLink(wl);
    refreshWorldDot();
    if (!wl) maybeWhatsNew();
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
  if (!S.cur || S.cur.id !== id) { closeBrowser(); BR.alive = 0; brSyncButton(); }
  if (S.abort) S.abort.abort();
  S.streaming = false;
  if (!S.cur || S.cur.id !== id) { $('#empty').hidden = true; msgsEl().replaceChildren(skeleton(3)); }
  const d = await api('/api/sessions/' + id);
  S.cur = d.session; S.run = d.run; S.commands = d.commands || [];
  S.pending = {}; for (const p of d.run.pending || []) S.pending[p.id] = p;
  S.partial = d.run.partial || { text: '', reasoning: '' };
  S.currentActivity = d.run.current || null;
  S.lastSeq = d.run.seq || 0;
  S.finished = !d.run.running;
  S.subs = d.run.subs || [];
  store.set('cur', id); history.replaceState(null, '', '#chat=' + id);
  brRefreshAlive();
  renderChat(true);
  renderList();
  if (d.run.running) {
    const cur = d.run.current || {};
    setStatus(cur.detail || 'Reconnected to active run…');
    attach();
  }
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
  web_fetch: '◎', web_search: '◎', browser_read: '◫', browser_act: '◫', todo_write: '☑', task: '⋔', skill: '★', git_sync: '⎇', create_pr: '⎇', exit_plan_mode: '☰', share_file: '⬇', preview: '▶', view_3d: '◈', image_search: '⌕', fetch_image: '▣', view_image: '◉', annotate_image: '✐', generate_image: '✦', send_email: '✉', ls_read: '🍋', ls_checkout: '🍋', ls_refund: '🍋', etsy_read: '🛍', etsy_draft: '🛍', etsy_update: '🛍', connector_add: '⚭', routine_list: '⏱', routine_create: '⏱', routine_update: '⏱', world_list: '🌍', world_read: '🌍', world_create: '🌍', world_say: '🌍', create_app: '▦', app_list: '▦', app_read: '▦', forge_tool: '⚒', forge_list: '⚒', pack_list: '⚙', pack_enable: '⚙', serve: '▶', serve_stop: '■', watch_create: '👁', watch_list: '👁', watch_remove: '👁', publish_pages: '🌐', capabilities: '☰', skill_create: '✦', ask_user: '?', repo_map: '⌖', find_symbol: '⌖', models: '⋔', connector_search: '⌕' };
function toolSummary(name, a) {
  a = a || {};
  switch (name) {
    case 'read': case 'write': case 'edit': case 'multiedit': return a.path || '';
    case 'bash': return (a.command || '').split('\n')[0];
    case 'share_file': case 'preview': case 'view_3d': case 'view_image': case 'annotate_image': return a.path || '';
    case 'image_search': return a.query || '';
    case 'fetch_image': return a.url || '';
    case 'generate_image': return (a.prompt || '').slice(0, 80);
    case 'forge_tool': return (a.name || '') + (a.network ? ' (network)' : '') + ' · ' + (a.description || '').slice(0, 50);
    case 'create_app': return (a.icon || '') + ' ' + (a.name || '');
    case 'app_read': return a.id || '';
    case 'routine_create': case 'world_create': return a.name || '';
    case 'pack_enable': return a.name || '';
    case 'serve': return (a.command || '').slice(0, 60);
    case 'publish_pages': return a.repo || '';
    case 'skill_create': return (a.name || '') + ' · ' + (a.description || '').slice(0, 50);
    case 'find_symbol': return a.name || '';
    case 'repo_map': return a.path || '.';
    case 'ask_user': return ((a.questions || [])[0] || {}).question || '';
    case 'routine_update': case 'world_say': case 'world_read': return a.id || '';
    case 'connector_add': return (a.name || '') + ' · ' + (a.url || '');
    case 'send_email': return ((a.to || []).join?.(', ') || a.to || '') + ' · ' + (a.subject || '').slice(0, 60);
    case 'models': return 'list models';
    case 'connector_search': return a.query || '';
    case 'browser_read': return [a.action, a.url].filter(Boolean).join(' ');
    case 'browser_act': return [a.action, a.ref != null ? '#' + a.ref : '', a.text || a.key || (a.js || '').slice(0, 40)].filter(Boolean).join(' ');
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
  const dflt = !!(result && result._app && !result._error);   // an app you just asked for: show its Open button right away
  const open = approval ? true : card.dataset.open ? card.dataset.open === '1' : dflt;
  card.className = 'tool' + (result && result._error ? ' err' : '') + (approval ? ' ask' : '');
  card.replaceChildren();
  const state = result ? h('span.tool-state ' + (result._error ? 'bad' : 'ok'), { text: result._error ? '✕' : '✓' })
    : approval ? h('span.tool-state', { text: '?' }) : h('span.tool-state', {}, h('span.spin'));
  card.append(h('button.tool-head', { onclick: () => { card.dataset.open = open ? '0' : '1'; fillCard(card, call, result, S.pending[call.id] || null); } },
    h('span.tool-ic', { text: ICONS[name] || '⚙' }), h('span.tool-name', { text: name.replace(/^mcp__/, 'mcp:') }),
    h('span.tool-sum', { text: toolSummary(name, a) }), state));
  if (!open) return;
  const body = h('div.tool-body');
  if (name === 'exit_plan_mode') {
    body.append(h('div.plan.prose', { html: md(a.plan || '') }));
  } else if (name === 'bash') {
    body.append(h('div.lbl', { text: 'command' }), h('pre', { text: a.command || '' }));
  } else if (name === 'skill_create') {
    body.append(h('div.lbl', { text: (a.name || '') + ': ' + (a.description || '') }), h('pre', { style: 'white-space:pre-wrap', text: a.instructions || '' }));
  } else {
    const d = (result && result._diff) || previewDiff(name, a);
    if (name === 'create_app' && result && result._app) { /* the HTML is long and useless here: the card below is the result */ }
    else if (d) body.append(diffNode(d));
    else if (name !== 'todo_write') body.append(h('div.lbl', { text: 'input' }), h('pre', { text: JSON.stringify(a, null, 2) }));
  }
  if (result && name !== 'exit_plan_mode' && !(result._diff && !result._error) && !(name === 'create_app' && result._app)) body.append(h('div.lbl', { text: 'output' }), h('pre', { text: result.content || '' }));
  if (result && result._browser) body.append(h('div', { style: 'padding:10px 12px' }, h('button.primary.small', { text: '◫ Open browser', onclick: openBrowser })));
  if (result && result._image) body.append(imgNode(result._image, call.id));
  if (result && result._preview) body.append(h('div', { style: 'padding:10px 12px' }, h('button.primary.small', { text: result._preview.startsWith('live:') ? '▶ Open live server' : '▶ Open preview', onclick: () => openPreview(result._preview) })));
  if (result && result._app) body.append(h('div', { style: 'padding:10px 12px;display:flex;align-items:center;gap:10px' },
    h('div', { style: 'flex:1;min-width:0;font-weight:600', text: ((a.icon || '▦') + ' ' + (a.name || 'App')).trim() }),
    h('button.primary.small', { text: 'Open app', onclick: () => openApp(result._app, a.name) })));
  if (result && result._model) body.append(h('div', { style: 'padding:10px 12px' }, h('button.primary.small', { text: '◈ Open 3D viewer', onclick: () => openModel(result._model) })));
  if (result && result._file) body.append(h('div', { style: 'padding:10px 12px' }, fileCardNode(result._file)));
  card.append(body);
  if (approval && name === 'ask_user') {
    const qs = (approval.args && approval.args.questions) || [];
    const picks = qs.map(() => new Set()), others = qs.map(() => ''), btns = qs.map(() => []);
    const send = h('button.btn-allow', { text: 'Send answers', disabled: true, onclick: () => answer(call.id, { allow: true, answers: qs.map((q, i) => ({ choice: [...picks[i]], other: others[i] })) }, card) });
    const refresh = () => {
      qs.forEach((q, i) => btns[i].forEach(([b, lab]) => b.classList.toggle('on', picks[i].has(lab))));
      send.disabled = !qs.every((q, i) => picks[i].size || others[i].trim());
    };
    const box = h('div.approve.asking', {}, h('div.q', { text: approval.reason || 'Your agent has a question' }));
    qs.forEach((q, i) => {
      box.append(h('div.qtext', {}, q.header ? h('span.qhead', { text: q.header }) : null, document.createTextNode(q.question)));
      for (const o of q.options) {
        const b = h('button.qopt', { type: 'button', onclick: () => {
          if (q.multiSelect) { picks[i].has(o.label) ? picks[i].delete(o.label) : picks[i].add(o.label); } else { picks[i].clear(); picks[i].add(o.label); }
          refresh();
        } }, h('div', { style: 'font-weight:600', text: o.label }), o.description ? h('div.dim', { style: 'font-size:12.5px', text: o.description }) : null);
        btns[i].push([b, o.label]); box.append(b);
      }
      box.append(h('input', { type: 'text', placeholder: 'Other: type your own answer', oninput: (e) => { others[i] = e.target.value; refresh(); } }));
    });
    box.append(h('div.row', {}, send, h('button.btn-deny', { text: 'Skip', onclick: () => answer(call.id, { allow: false, note: 'the user skipped the question' }, card) })));
    card.append(box);
  } else if (approval) {
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
  const calls = m.tool_calls || [];
  const thought = ''; // Never render raw provider reasoning into the conversation UI.
  if (m.content) n.append(prose(m.content));
  if (m._unverified && m.content) n.append(h('div.unverified', { text: '⚠ Not verified: no passing run after the last code change' }));
  if (calls.length) {
    const tg = h('div.tgrp', {}, h('button.tg-head', { onclick: () => openTG(tg) }), h('div.tg-live'), h('div.tg-pin'));
    tg._cards = [];
    for (const [i, call] of calls.entries()) {
      const card = makeCard(call);
      if (i === 0 && thought) card._thought = thought;
      card._tg = tg; tg._cards.push(card);
    }
    n.append(tg); n._tg = tg;
    refreshTG(tg);
  }
  return n;
}
/* ---- tool calls collapse into one "Ran 6 commands" row; tap for the full timeline */
const KIND = { bash: ['Ran', 'command', 'commands'], bash_output: ['Ran', 'command', 'commands'], kill_bash: ['Ran', 'command', 'commands'],
  read: ['Read', 'file', 'files'], glob: ['Searched', 'folder', 'folders'], grep: ['Searched', 'code search', 'code searches'],
  write: ['Edited', 'file', 'files'], edit: ['Edited', 'file', 'files'], multiedit: ['Edited', 'file', 'files'], annotate_image: ['Edited', 'image', 'images'],
  web_fetch: ['Opened', 'page', 'pages'], web_search: ['Searched the web', 'time', 'times'], browser_read: ['Browsed', 'page', 'pages'], browser_act: ['Used the browser', 'time', 'times'], image_search: ['Searched images', 'time', 'times'],
  fetch_image: ['Downloaded', 'image', 'images'], generate_image: ['Generated', 'image', 'images'], send_email: ['Sent', 'email', 'emails'], ls_read: ['Checked', 'store lookup', 'store lookups'], ls_checkout: ['Made', 'checkout link', 'checkout links'], ls_refund: ['Refunded', 'order', 'orders'], etsy_read: ['Checked', 'Etsy lookup', 'Etsy lookups'], etsy_draft: ['Drafted', 'Etsy listing', 'Etsy listings'], etsy_update: ['Changed', 'Etsy listing', 'Etsy listings'], connector_add: ['Proposed', 'connector', 'connectors'], routine_list: ['Listed', 'routines', 'routine lists'], routine_create: ['Scheduled', 'routine', 'routines'], routine_update: ['Updated', 'routine', 'routines'], world_list: ['Listed', 'worlds', 'world lists'], world_read: ['Read', 'world', 'worlds'], world_create: ['Started', 'world', 'worlds'], world_say: ['Stepped into', 'world', 'worlds'], create_app: ['Built', 'app', 'apps'], app_list: ['Listed', 'app list', 'app lists'], app_read: ['Read', 'app', 'apps'], forge_tool: ['Forged', 'tool', 'tools'], forge_list: ['Listed', 'forged tools', 'forged tool lists'], ask_user: ['Asked', 'question', 'questions'], repo_map: ['Mapped', 'repo', 'repos'], find_symbol: ['Looked up', 'symbol', 'symbols'], skill_create: ['Saved', 'skill', 'skills'], pack_list: ['Listed', 'toolchain pack list', 'toolchain pack lists'], pack_enable: ['Installed', 'toolchain pack', 'toolchain packs'], serve: ['Started', 'dev server', 'dev servers'], watch_create: ['Set up', 'watcher', 'watchers'], watch_list: ['Listed', 'watcher list', 'watcher lists'], watch_remove: ['Removed', 'watcher', 'watchers'], serve_stop: ['Stopped', 'dev server', 'dev servers'], publish_pages: ['Published', 'site', 'sites'], capabilities: ['Listed', 'tool list', 'tool lists'], view_image: ['Looked at', 'image', 'images'], task: ['Delegated', 'task', 'tasks'], git_sync: ['Synced', 'time', 'times'], todo_write: ['Updated', 'task list', 'task lists'], skill: ['Loaded', 'skill', 'skills'], models: ['Checked', 'model list', 'model lists'], connector_search: ['Looked up', 'connector tool', 'connector tools'], create_pr: ['Opened', 'pull request', 'pull requests'] };
const isPinned = (c) => { const r = S.results[c._call.id]; return !!(r && (r._file || r._preview || r._model || r._image || r._app)) || c._call.function.name === 'exit_plan_mode'; };
const isDone = (c) => !!S.results[c._call.id] && !S.pending[c._call.id];
function tgLabel(cards) {
  const by = new Map();
  for (const c of cards) {
    const k = KIND[c._call.function.name] || ['Used', 'tool', 'tools'];
    const key = k[0] + '|' + k[2];
    if (!by.has(key)) by.set(key, { k, n: 0 });
    by.get(key).n++;
  }
  const one = ({ k, n }) => (/^(Searched the web|Searched images|Synced|Used the browser)$/.test(k[0]) ? (n === 1 ? k[0] : `${k[0]} ${n} times`) : `${k[0]} ${n === 1 ? 'a' : n} ${n === 1 ? k[1] : k[2]}`);
  const parts = [...by.values()].sort((x, y) => y.n - x.n);
  if (parts.length === 1) return one(parts[0]);
  if (parts.length === 2) return one(parts[0]) + ', ' + one(parts[1]).replace(/^./, (ch) => ch.toLowerCase());
  return `Used ${cards.length} tools`;
}
function refreshTG(tg) {
  const cards = tg._cards;
  const live = tg.querySelector('.tg-live'), pin = tg.querySelector('.tg-pin'), head = tg.querySelector('.tg-head');
  const grouped = cards.filter((c) => !isPinned(c));
  const running = grouped.some((c) => !isDone(c));
  const failed = grouped.filter((c) => isDone(c) && S.results[c._call.id]._error).length;
  for (const c of cards) {
    if (isPinned(c) && isDone(c)) { if (c.parentNode !== pin) pin.append(c); }
    else if (!isDone(c)) { if (c.parentNode !== live) live.append(c); }
    else if (c.parentNode) c.remove();
  }
  const done = grouped.filter(isDone);
  head.hidden = !done.length;
  head.replaceChildren(h('span.tg-ic', { text: '>_' }), h('span.tg-txt', { text: tgLabel(done) + (failed ? ` · ${failed} failed` : '') }), h('span.tg-chev', { text: '›' }));
  head.classList.toggle('bad', !!failed);
  tg._label = tgLabel(done);
}
function openTG(tg) {
  const done = tg._cards.filter((c) => isDone(c) && !isPinned(c));
  const list = h('div.steps');
  const show = () => {
    list.replaceChildren();
    for (const c of done) {
      const call = c._call, name = call.function.name, a = parseArgs(call), r = S.results[call.id];
      if (c._thought) list.append(h('div.thought', { text: c._thought.trim().slice(0, 400) }));
      list.append(h('button.step', { onclick: () => detail(c) }, h('span.step-ic', { text: ICONS[name] || '⚙' }),
        h('span.step-txt', {}, h('span.step-t', { text: a.description || toolSummary(name, a) || name }), h('span.step-s', { text: name })),
        h('span.step-st' + (r._error ? '.bad' : ''), { text: r._error ? '✕' : '' }), h('span.tg-chev', { text: '›' })));
    }
  };
  const detail = (c) => {
    const call = c._call, a = parseArgs(call);
    const view = h('div.tool'); view.dataset.open = '1';
    fillCard(view, call, S.results[call.id], null);
    const full = h('div.detail', {}, h('button.back', { text: '‹ All steps', onclick: () => { sheet(tg._label, list); } }));
    if (a.description) full.append(h('div.lbl', { text: 'Description' }), h('div.dim', { text: a.description }));
    full.append(view);
    sheet(toolSummary(call.function.name, a).slice(0, 60) || call.function.name, full);
  };
  show();
  sheet(tg._label, list);
}
function mergeGroups() {
  let prev = null;
  for (const n of [...msgsEl().children]) {
    if (!n.classList.contains('msg-ai') || !n._tg) { prev = null; continue; }
    if (prev && n.firstElementChild === n._tg) {
      for (const c of n._tg._cards) { prev._tg._cards.push(c); c._tg = prev._tg; }
      n._tg._cards = [];
      n.remove();
      refreshTG(prev._tg);
      continue;
    }
    prev = n.lastElementChild === n._tg ? n : null;
  }
}
function imgNode(path, id) {
  const img = h('img.tool-img', { alt: path });
  S.imgs = S.imgs || {};
  const key = S.cur.id + '|' + id;
  const set = (u) => { img.src = u; };
  if (S.imgs[key]) set(S.imgs[key]);
  else fetch(`${S.api}/api/sessions/${S.cur.id}/download?path=${encodeURIComponent(path)}`, { headers: { Authorization: 'Bearer ' + S.token } })
    .then((r) => { if (!r.ok) throw new Error(); return r.blob(); }).then((b) => { S.imgs[key] = URL.createObjectURL(b); set(S.imgs[key]); }).catch(() => { img.replaceWith(h('div.dim', { text: 'Could not load ' + path })); });
  img.onclick = () => openLightbox([path], 0);
  return img;
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
  mergeGroups();
  renderTodos(); renderSubs(); renderQueue();
  renderLive();
  for (const ev of Object.values(S.pending)) ensureApprovalCard(ev);
  S.stick = true;
  if (forceScroll !== false) requestAnimationFrame(() => scrollDown(true));
  updateSend();
}
let liveRaf = 0;
function renderLive() {
  if (!S.run.running) {
    if (S.live) S.live.remove();
    S.live = null;
    if (S.statusEl) S.statusEl.remove();
    S.statusEl = null;
    return;
  }
  if (!S.live) {
    S.live = h('div.msg-ai.fresh');
    msgsEl().append(S.live);
  }
  if (liveRaf) return;
  liveRaf = requestAnimationFrame(() => {
    liveRaf = 0;
    if (!S.live) return;
    const box = chat();
    const keepAtBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
    const p = S.partial;
    // Keep the live message node stable. Replacing/moving it on every streamed
    // token caused layout flashes and yanked readers away from their position.
    let body = S.live.querySelector('.live-body');
    if (!body) {
      S.live.replaceChildren();
      body = h('div.live-body');
      S.live.append(body);
    }
    if (p.text) {
      body.replaceChildren(prose(p.text));
    } else if (!body.querySelector('.live-dots')) {
      body.replaceChildren(h('div.live-dots', {}, h('i'), h('i'), h('i')));
    }
    // Provider reasoning tokens are deliberately not rendered into the chat.
    // Show concise activity/status instead of exposing raw internal reasoning.
    if (S.statusEl && S.statusEl.parentNode !== msgsEl()) msgsEl().append(S.statusEl);
    if (keepAtBottom) scrollDown(true);
  });
}
const RETRYISH = /HTTP (408|429|5\d\d)|retrying in|keeps failing|network hiccup/;
async function retryOnOtherModel() {
  if (!S.cur) return;
  const fb = S.info && S.info.fallback_model;
  if (!fb || fb === S.cur.model) { toast('Pick a different model, then resend'); modelSheet(); return; }
  try {
    await api(`/api/sessions/${S.cur.id}/stop`, { method: 'POST' });
    for (let i = 0; i < 30 && S.run.running; i++) await new Promise((r) => setTimeout(r, 200));
    await api('/api/sessions/' + S.cur.id, { method: 'PATCH', body: { model: fb } }); S.cur.model = fb; applyChips();
    const ui = lastUserIdx();
    if (ui >= 0) await regenerate(ui);
  } catch (e) { toast(e.message, true); }
}
function setStatus(text) {
  if (!text) { if (S.statusEl) S.statusEl.remove(); S.statusEl = null; return; }
  const box = chat();
  const keepAtBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
  if (!S.statusEl) S.statusEl = h('div.notice');
  S.statusEl.replaceChildren(text, RETRYISH.test(text) ? h('button.notice-btn', { text: 'Try another model', onclick: retryOnOtherModel }) : null);
  if (S.statusEl.parentNode !== msgsEl()) msgsEl().append(S.statusEl);
  if (keepAtBottom) scrollDown(true);
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
        if (card) { fillCard(card, card._call, m, null); if (card._tg) refreshTG(card._tg); }
        if (m._browser) brRefreshAlive();
      } else if (m.role === 'assistant') {
        const n = assistantNode(m);
        if (S.live) msgsEl().insertBefore(n, S.live); else msgsEl().append(n);
        mergeGroups();
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
    case 'messenger_reply': {
      const msg = { role: 'assistant', content: ev.text, _messenger: true };
      c.messages.push(msg);
      const node = assistantNode(msg);
      node.classList.add('messenger-reply');
      if (S.live) msgsEl().insertBefore(node, S.live); else msgsEl().append(node);
      mergeGroups();
      if (S.stick) scrollDown();
      toast('Messenger reply received');
      break;
    }
    case 'phase': S.currentActivity = { phase: ev.phase, detail: ev.detail, since: Date.now() }; setStatus(ev.detail || ev.phase); break;
    case 'tool_start': { S.currentActivity = { phase: 'tool', detail: ev.name, since: Date.now() }; const card = S.cards[ev.tool_id]; if (card) { card.dataset.open = '1'; fillCard(card, card._call, S.results[ev.tool_id], null); } setStatus('Running ' + ev.name + '…'); break; }
    case 'tool_output': { const card = S.cards[ev.tool_id]; if (card) { card._liveOutput = (card._liveOutput || '') + (ev.text || ''); const pre = card.querySelector('.tool-live-output'); if (pre) { pre.textContent = card._liveOutput.slice(-24000); pre.scrollTop = pre.scrollHeight; } else { const body = card.querySelector('.tool-body'); if (body) body.append(h('pre.tool-live-output', { text: card._liveOutput.slice(-24000) })); } } break; }
    case 'tool_end': S.currentActivity = { phase: 'thinking', detail: 'Tool finished · ' + ev.name, since: Date.now() }; setStatus('Finished ' + ev.name + (ev.error ? ' · error' : '')); break;
    case 'heartbeat': S.currentActivity = { phase: ev.phase, detail: ev.detail, since: Date.now() }; setStatus(ev.detail || 'Working…'); break;
    case 'error': toast(ev.text, true, 8000); msgsEl().append(h('div.notice.err', { text: ev.text })); scrollDown(); break;
    case 'reload': reloadCurrent(); break;
    case 'done': finishRun(); brRefreshAlive(); break;
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
  S.finished = true; S.run.running = false; S.pending = {}; S.currentActivity = null;
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
  b.setAttribute('aria-label', stop ? 'Stop' : S.run.running ? 'Queue instruction' : 'Send');
  const now = $('#btn-send-now');
  if (now) now.hidden = !(S.run.running && hasDraft());
}
function autoGrow() { const t = $('#input'); t.style.height = 'auto'; t.style.height = Math.min(t.scrollHeight, window.innerHeight * 0.38) + 'px'; }
const queueFor = () => (S.queues[S.cur.id] ||= []);
function renderQueue() {
  const bar = $('#queuebar');
  const q = S.cur ? queueFor() : [];
  bar.hidden = !q.length;
  bar.replaceChildren(...q.map((m, i) => h('div.qitem', {}, h('span.qn', { text: 'Queued' }), h('span.qt', { text: m.text.replace(/\s+/g, ' ').slice(0, 80) || '(attachment)' }), h('button', { 'aria-label': 'Remove from queue', text: '✕', onclick: () => { queueFor().splice(i, 1); renderQueue(); } }))));
}
async function sendQueuedNow(index) {
  if (!S.cur || !S.run.running) return;
  const q = queueFor(); const item = q[index]; if (!item) return;
  try {
    const r = await api('/api/sessions/' + S.cur.id + '/interrupt', { body: { text: item.text, images: item.images || [], priority: true, tz: (Intl.DateTimeFormat().resolvedOptions().timeZone || '') } });
    q.splice(index, 1); renderQueue(); toast('Sent to the front of the agent queue (' + r.queued + ' pending).');
  } catch (e) { toast(e.message, true, 6000); }
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
  if (S.run.running) {
    const isStatusQuestion = /^(how('?s| is) it going|status|progress|what('?s| is) happening|what are you doing|are you stuck|any update|update me)\b/i.test(draft.text.trim());
    if (isStatusQuestion) {
      try { const r = await api('/api/sessions/' + S.cur.id + '/messenger', { body: { text: draft.text } }); toast('Messenger replied · ' + shortModel(r.model)); }
      catch (e) { $('#input').value = draft.text; toast(e.message, true, 6000); }
      return;
    }
    try {
      const r = await api('/api/sessions/' + S.cur.id + '/interrupt', { body: { text: draft.text, images: draft.images, tz: (Intl.DateTimeFormat().resolvedOptions().timeZone || '') } });
      toast('Instruction queued (' + r.queued + ' pending). It will be checked after the current operation.');
    } catch (e) {
      $('#input').value = draft.text;
      S.images = draft.images;
      renderAttach(); autoGrow();
      toast(e.message, true, 6000);
    }
    return;
  }
  await dispatch(draft);
}
async function sendNow() {
  if (!S.run.running || !hasDraft()) return send();
  if (S.files.some((f) => f.state === 'uploading')) { toast('Still uploading…'); return; }
  const draft = takeDraft();
  try {
    const r = await api('/api/sessions/' + S.cur.id + '/interrupt', { body: { text: draft.text, images: draft.images, priority: true, tz: (Intl.DateTimeFormat().resolvedOptions().timeZone || '') } });
    toast('Sent to the front of the worker queue (' + r.queued + ' pending).');
  } catch (e) { $('#input').value = draft.text; S.images = draft.images; renderAttach(); autoGrow(); toast(e.message, true, 6000); }
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
    const r = await api(`/api/sessions/${S.cur.id}/messages`, { body: { text, images, tz: (Intl.DateTimeFormat().resolvedOptions().timeZone || '') } });
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
  const selfRepo = S.info && S.info.self_repo;
  const self = selfRepo && S.info.github ? h('button.secondary', { text: '⚙ Improve Pocket Agent itself', onclick: async () => {
    try { closeSheet(); await newChat({ repo: selfRepo, title: 'Pocket Agent dev' }); $('#input').focus(); } catch (e) { toast(e.message, true); }
  } }) : null;
  sheet('New chat', h('label', {}, 'GitHub repo to work in', repo),
    h('div.dim', { text: 'Leave blank for a scratch workspace. With a repo, it works on its own branch (agent/…) and never touches main without a PR.' }), go,
    self, self ? h('div.dim', { style: 'font-size:12.5px', text: 'Opens this app\'s own code. The agent can change it, but nothing goes live until you merge its pull request on GitHub.' }) : null);
  setTimeout(() => { if ($('#sheet').classList.contains('open')) repo.focus(); }, 300);
}
async function setEffort(k) {
  if (!S.cur) return;
  await api('/api/sessions/' + S.cur.id, { method: 'PATCH', body: { effort: k } });
  S.cur.effort = k; S.cur.thinking = true; applyChips();
}
async function modelSheet() {
  const list = h('div', { style: 'display:flex;flex-direction:column;gap:8px' }, h('div.dim', { text: 'Loading models…' }));
  const search = h('input.msearch', { type: 'search', placeholder: 'Search models', autocomplete: 'off', autocapitalize: 'off', autocorrect: 'off', spellcheck: 'false' });
  const cur = () => { const v = (S.cur && S.cur.effort) || 'high'; return v === 'off' ? 'low' : v; };
  const note = h('div.seg-note', { text: EFFORTS[cur()][1] });
  const seg = h('div.seg', {}, ...Object.entries(EFFORTS).map(([k, [name]]) => h('button' + (cur() === k ? '.on' : ''), { text: name, onclick: async (e) => {
    const me = e.currentTarget;
    await setEffort(k);
    seg.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b === me));
    note.textContent = EFFORTS[k][1];
  } })));
  const head = [];
  if (S.cur) {
    head.push(h('div.sec', { text: 'Effort' }), seg, note);
    const rv = h('input', { type: 'checkbox', checked: !!S.cur.review, onchange: async (e) => {
      const on = e.target.checked;
      try { await api('/api/sessions/' + S.cur.id, { method: 'PATCH', body: { review: on } }); S.cur.review = on; } catch (er) { e.target.checked = !on; toast(er.message, true); }
    } });
    head.push(h('label.rvrow', {}, rv, h('span', {}, h('b', { text: 'Self-review' }), h('div.dim', { style: 'font-size:12.5px', text: 'After it changes code, a fresh reviewer checks the diff and runs things; real problems go back to the agent once. Slower, uses more of the rate limit.' }))));
  }
  head.push(h('div.sec', { text: 'Model' }), search);
  sheet('Model', ...head, list);
  let models = [];
  let routerBox = null;
  const draw = () => {
    const q = search.value.trim().toLowerCase();
    const shown = models.filter((m) => !q || (m.id + ' ' + m.provider).toLowerCase().includes(q));
    list.replaceChildren();
    if (routerBox) list.append(routerBox);
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
    if (S.cur) {
      const roleNames = { reasoning: 'Reasoning / planning', coding: 'Coding / implementation', reviewer: 'Independent reviewer', fast: 'Fast / simple tasks' };
      const automaticModels = models.filter(x => x.provider !== 'kimi');
      const suggested = (terms) => { const m = automaticModels.find(x => terms.some(t => x.id.toLowerCase().includes(t))); return m ? m.ref : ''; };
      const roles = Object.assign({
        reasoning: suggested(['mistral-large-4', 'nemotron-3-ultra', 'deepseek']),
        coding: suggested(['deepseek-v4', 'qwen3-coder', 'devstral']),
        reviewer: suggested(['deepseek-v4', 'nemotron-3-ultra']),
        fast: suggested(['flash', 'small', 'nano'])
      }, S.cur.model_roles || {});
      for (const key of Object.keys(roles)) if (roles[key] && roles[key].startsWith('kimi:')) roles[key] = '';
      const mode = h('select', { class: 'mroute-mode' },
        ...[['manual','Manual · use selected model'],['auto','Automatic · choose by task'],['hybrid','Hybrid · primary + specialists']].map(([v,t]) => h('option', { value:v, text:t, selected:(S.cur.model_mode || 'manual')===v })));
      mode.onchange = async () => {
        try { await api('/api/sessions/' + S.cur.id, { method:'PATCH', body:{ model_mode:mode.value, model_roles:roles } }); S.cur.model_mode=mode.value; toast('Routing mode saved'); }
        catch(e) { toast(e.message,true); }
      };
      const roleRows = Object.entries(roleNames).map(([key,label]) => {
        const sel = h('select', { class:'mroute-role' }, h('option',{value:'',text:'Not assigned'}), ...automaticModels.map(m=>h('option',{value:m.ref,text:m.provider+' · '+m.id,selected:roles[key]===m.ref})));
        sel.onchange = async () => {
          roles[key]=sel.value;
          try { await api('/api/sessions/' + S.cur.id,{method:'PATCH',body:{model_roles:roles}}); S.cur.model_roles={...roles}; }
          catch(e) { toast(e.message,true); }
        };
        return h('label.mroute-row',{},h('span',{text:label}),sel);
      });
      routerBox = h('div.mroute',{},h('div.sec',{text:'Model routing'}),mode,
        h('div.dim',{text:'Assign specialist models. Automatic and Hybrid modes use these roles when the task matches.'}),
        ...roleRows);
    }
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
      item('◫', 'Browser', openBrowser),
      item('▦', 'Apps', openApps),
      item('🌍', 'Worlds', openWorlds),
      item('⇣', 'Compact context', () => { $('#input').value = '/compact'; send(); }),
      h('button.mi', { onclick: async () => {
        const v = !c.autopilot;
        try { await api('/api/sessions/' + c.id, { method: 'PATCH', body: { autopilot: v } }); c.autopilot = v; tg.classList.toggle('on', v); applyChips(); } catch (e) { toast(e.message, true); }
      } }, h('span.mic', { text: '∞' }), h('span', { text: 'Autopilot' }), tg),
      item('⎇', 'Save workspace to GitHub', async () => { try { const r = await api(`/api/sessions/${c.id}/sync`, { method: 'POST' }); toast(r.result); } catch (er) { toast(er.message, true); } }));
  } else rows.push(item('▦', 'Apps', openApps), item('🌍', 'Worlds', openWorlds), h('div.dim', { style: 'padding:12px', text: 'Open or start a chat to see chat actions.' }));
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

/* ============================================================ browser: docked split view (+ full screen), live screencast */
const BR = { alive: 0, info: null, ctl: null, run: 0, rw: 1000, rh: 760, sentA: 0, lastT: 0, q: Promise.resolve(), errAt: 0, typed: '' };
const brOpen = () => !$('#browserpage').hidden;
function brSyncButton() {
  const b = $('#btn-browser');
  b.hidden = !(BR.alive || brOpen());
  b.classList.toggle('on', brOpen());
  b.classList.toggle('busy', !!(BR.alive && S.run && S.run.running));
}
async function brRefreshAlive() {
  if (!S.cur) { BR.alive = 0; brSyncButton(); return; }
  const id = S.cur.id;
  try { const r = await api(`/api/sessions/${id}/browser`); if (S.cur && S.cur.id === id) { BR.info = r.info; BR.alive = r.info ? 1 : 0; } } catch { /* offline */ }
  if (brOpen() && document.activeElement !== $('#br-url')) $('#br-url').value = BR.info ? BR.info.url : '';
  brSyncButton();
}
// size the picture to fit the view with the remote page's aspect, so nothing is cropped or letterboxed oddly
function brFit() {
  const v = $('#br-view'), st = $('#br-stage'), W = v.clientWidth, H = v.clientHeight;
  if (!W || !H) return;
  const a = BR.rw / BR.rh; let w = W, hh = W / a;
  if (hh > H) { hh = H; w = H * a; }
  st.style.width = Math.floor(w) + 'px'; st.style.height = Math.floor(hh) + 'px';
}
let brResizeT = 0;
function brSendResize() {
  clearTimeout(brResizeT);
  brResizeT = setTimeout(() => {
    const v = $('#br-view'); if (!S.cur || !BR.alive || !v.clientWidth || !v.clientHeight) return;
    const a = v.clientWidth / v.clientHeight;
    if (BR.sentA && Math.abs(a - BR.sentA) / BR.sentA < 0.06) return;
    BR.sentA = a;
    api(`/api/sessions/${S.cur.id}/browser/input`, { body: { type: 'resize', w: v.clientWidth, h: v.clientHeight } }).catch(() => {});
  }, 250);
}
function brCursor(c) {
  const el = $('#br-cur'), st = $('#br-stage');
  if (!c) { el.hidden = true; return; }
  const sw = st.clientWidth, sh = st.clientHeight;
  el.hidden = false; el.classList.toggle('you', c.by === 'you');
  el.style.transform = `translate(${c.x / BR.rw * sw}px, ${c.y / BR.rh * sh}px)`;
  if ((c.k === 'click' || c.k === 'tap') && c.t !== BR.lastT && c.by !== 'you' && Date.now() - c.t < 5000) brRipple(c.x / BR.rw * sw, c.y / BR.rh * sh);
  BR.lastT = c.t;
}
function brRipple(px, py) { const r = h('i.br-ripple'); r.style.left = px + 'px'; r.style.top = py + 'px'; $('#br-stage').append(r); setTimeout(() => r.remove(), 650); }
function brFrame(m) {
  if (m.none) { $('#br-stage').hidden = true; $('#br-empty').hidden = false; $('#br-need').hidden = true; return; }
  if (m.w && (m.w !== BR.rw || m.h !== BR.rh)) { BR.rw = m.w; BR.rh = m.h; brFit(); }
  if (m.f) { $('#br-img').src = 'data:image/jpeg;base64,' + m.f; if ($('#br-stage').hidden) { $('#br-stage').hidden = false; $('#br-empty').hidden = true; brFit(); } }
  if (m.url && document.activeElement !== $('#br-url')) $('#br-url').value = m.url;
  BR.info = Object.assign(BR.info || {}, { url: m.url, w: m.w, h: m.h });
  $('#br-need').hidden = !m.captcha;
  if (m.captcha) $('#br-need-txt').textContent = 'Bot check on this page. Solve it here, then tell the agent.' + (m.fails && m.fails.length ? ' Not loading? Failed: ' + m.fails.join('; ') : '');
  brCursor(m.cur);
}
async function brStream() {
  const my = ++BR.run, id = S.cur && S.cur.id;
  if (BR.ctl) BR.ctl.abort();
  while (id && BR.run === my && brOpen() && S.cur && S.cur.id === id && !document.hidden) {
    const ac = new AbortController(); BR.ctl = ac;
    try {
      const v = $('#br-view');
      BR.sentA = v.clientWidth / (v.clientHeight || 1);
      const r = await fetch(`${S.api}/api/sessions/${id}/browser/stream?w=${v.clientWidth}&h=${v.clientHeight}`, { headers: { Authorization: 'Bearer ' + S.token }, signal: ac.signal });
      if (!r.ok || !r.body) throw new Error('stream');
      const rd = r.body.getReader(), dec = new TextDecoder(); let buf = '';
      for (;;) {
        const { value, done } = await rd.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        let i; while ((i = buf.indexOf('\n')) >= 0) { const line = buf.slice(0, i); buf = buf.slice(i + 1); if (line && BR.run === my) { try { brFrame(JSON.parse(line)); } catch { /* partial */ } } }
      }
    } catch { /* dropped: reconnect below */ }
    if (BR.run !== my) return;
    await new Promise((res) => setTimeout(res, 1500));
  }
}
function brStop() { BR.run++; if (BR.ctl) BR.ctl.abort(); BR.ctl = null; }
// every input goes through one queue so taps, scrolls and keystrokes arrive in order
function brIn(body) {
  BR.q = BR.q.then(() => api(`/api/sessions/${S.cur.id}/browser/input`, { body })).catch((e) => { if (Date.now() - BR.errAt > 4000) { BR.errAt = Date.now(); toast(e.message, true); } });
  return BR.q;
}
async function brAct(body) {
  try { const r = await api(`/api/sessions/${S.cur.id}/browser/act`, { body }); BR.info = r.info; BR.alive = r.info ? 1 : 0; if (document.activeElement !== $('#br-url')) $('#br-url').value = r.info ? r.info.url : ''; }
  catch (e) { toast(e.message, true); }
  brSyncButton();
  if (BR.alive && brOpen()) brStream();   // a page may have just appeared: (re)connect right away
}
function brHeight() { const main = $('#main').clientHeight || window.innerHeight; return Math.max(170, Math.min(+store.get('brh', 0) || Math.round(main * 0.44), Math.round(main * 0.72))); }
function brLayout() { const p = $('#browserpage'); p.style.setProperty('--br-h', brHeight() + 'px'); }
async function openBrowser() {
  if (!S.cur) { toast('Open a chat first', true); return; }
  const p = $('#browserpage');
  p.hidden = false; p.classList.add('dock'); p.classList.remove('full'); brLayout();
  $('#br-full').textContent = '⤢';
  brSyncButton();
  await brRefreshAlive();
  brFit(); brStream();
  if (S.stick) scrollDown(true);
}
function closeBrowser() { $('#browserpage').hidden = true; $('#browserpage').classList.remove('full'); brStop(); brSyncButton(); }
function toggleBrowser() { if (brOpen()) closeBrowser(); else openBrowser(); }
function initBrowser() {
  $('#btn-browser').onclick = toggleBrowser;
  const setFull = (full) => { const p = $('#browserpage'); p.classList.toggle('full', full); p.classList.toggle('dock', !full); $('#br-full').textContent = full ? '⤡' : '⤢'; requestAnimationFrame(() => { brFit(); brSendResize(); }); };
  $('#br-back').onclick = () => { if ($('#browserpage').classList.contains('full')) setFull(false); else closeBrowser(); };
  $('#br-full').onclick = () => setFull(!$('#browserpage').classList.contains('full'));
  $('#br-form').onsubmit = (e) => { e.preventDefault(); const v = $('#br-url').value.trim(); if (!v) return; $('#br-url').blur(); brAct({ type: 'goto', url: /^[\w-]+(\.[\w-]+)+(\/|$)|:\/\//.test(v) ? v : 'https://duckduckgo.com/?q=' + encodeURIComponent(v) }); };
  $('#br-reload').onclick = () => brAct({ type: 'reload' });
  $('#br-prev').onclick = () => brAct({ type: 'back' });
  $('#br-up').onclick = () => brIn({ type: 'wheel', dy: -BR.rh * 0.8 });
  $('#br-down').onclick = () => brIn({ type: 'wheel', dy: BR.rh * 0.8 });
  $('#br-close').onclick = async () => { await brAct({ type: 'close' }); BR.alive = 0; $('#br-stage').hidden = true; $('#br-empty').hidden = false; closeBrowser(); };
  // type straight into the remote field as you type; Enter presses Enter there
  const txt = $('#br-text');
  txt.addEventListener('input', (e) => {
    if (e.isComposing) return;
    const v = txt.value, o = BR.typed; let i = 0; while (i < o.length && i < v.length && o[i] === v[i]) i++;
    for (let k = 0; k < o.length - i; k++) brIn({ type: 'key', key: 'Backspace' });
    if (v.length > i) brIn({ type: 'text', text: v.slice(i) });
    BR.typed = v;
  });
  txt.addEventListener('compositionend', () => txt.dispatchEvent(new Event('input')));
  $('#br-type').onsubmit = (e) => { e.preventDefault(); brIn({ type: 'key', key: 'Enter' }); txt.value = ''; BR.typed = ''; };
  $('#br-need-go').onclick = () => { $('#br-need').hidden = true; const d = { text: "Done, I solved the check. Carry on.", images: [] }; if (S.run.running) { queueFor().push(d); renderQueue(); updateSend(); } else dispatch(d); };
  // tap = click right where you touched (ripple shows instantly), drag = scroll
  const stg = $('#br-stage'); let pd = null, acc = 0, flushT = 0;
  const flush = () => { flushT = 0; const dy = acc * (BR.rw / (stg.clientWidth || 1)); acc = 0; if (Math.abs(dy) >= 1) brIn({ type: 'wheel', dy }); };
  stg.addEventListener('pointerdown', (e) => { pd = { x: e.clientX, y: e.clientY, ly: e.clientY, moved: false }; try { stg.setPointerCapture(e.pointerId); } catch { /* synthetic */ } });
  stg.addEventListener('pointermove', (e) => {
    if (!pd) return;
    if (!pd.moved && Math.hypot(e.clientX - pd.x, e.clientY - pd.y) > 9) pd.moved = true;
    if (pd.moved) { acc += pd.ly - e.clientY; pd.ly = e.clientY; if (!flushT) flushT = setTimeout(flush, 60); }
  });
  stg.addEventListener('pointerup', (e) => {
    const d = pd; pd = null; if (!d || d.moved) return;
    const r = stg.getBoundingClientRect(), fx = (e.clientX - r.left) / r.width, fy = (e.clientY - r.top) / r.height;
    brRipple(fx * r.width, fy * r.height);
    const c = $('#br-cur'); c.hidden = false; c.classList.add('you'); c.style.transform = `translate(${fx * r.width}px, ${fy * r.height}px)`;
    brIn({ type: 'tap', x: Math.round(fx * BR.rw), y: Math.round(fy * BR.rh) });
  });
  stg.addEventListener('pointercancel', () => { pd = null; });
  // drag the grip to resize the split
  const grip = $('#br-grip'); let y0 = 0, h0 = 0, drag = false;
  grip.addEventListener('pointerdown', (e) => { drag = true; y0 = e.clientY; h0 = $('#browserpage').offsetHeight; grip.setPointerCapture(e.pointerId); });
  grip.addEventListener('pointermove', (e) => { if (!drag) return; const main = $('#main').clientHeight; const nh = Math.max(170, Math.min(h0 + e.clientY - y0, Math.round(main * 0.72))); $('#browserpage').style.setProperty('--br-h', nh + 'px'); store.set('brh', String(nh)); });
  const end = () => { drag = false; brFit(); brSendResize(); }; grip.addEventListener('pointerup', end); grip.addEventListener('pointercancel', end);
  if (window.ResizeObserver) new ResizeObserver(() => { if (brOpen()) { brFit(); brSendResize(); } }).observe($('#br-view'));
  document.addEventListener('visibilitychange', () => { if (document.hidden) brStop(); else if (brOpen()) brStream(); });
  setInterval(() => { if (S.cur && !document.hidden && (S.run.running || BR.alive)) brRefreshAlive(); }, 10000);
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
async function cnLoad(refresh) { try { if (!CN.cat.length) CN.cat = (await api('/api/connector-catalog')).apps; } catch { /* the list just won't show */ } try { CN.list = (await api(refresh ? '/api/connectors/refresh' : '/api/connectors', refresh ? { method: 'POST' } : {})).connectors; } catch (e) { toast(e.message, true); } cnDraw(); }
const CN = { list: [], cat: [] };
function cnPopular() {
  const have = new Set(CN.list.map((c) => c.name));
  const rows = CN.cat.filter((a) => !have.has(a.name));
  if (!rows.length) return null;
  return h('div.cnpop', {}, h('div.sec', { text: 'Popular apps' }),
    ...rows.map((a) => h('button.rtcard.cnapp', { onclick: () => cnAdd(a) }, h('div.rttop', {}, h('span.nm', { text: a.title }), h('span.badge', { text: a.oauth ? 'sign in' : 'token' })), h('div.rtmeta', { text: a.about }))));
}
function cnDraw() {
  const box = $('#cnbody');
  if (!CN.list.length) { box.replaceChildren(h('div.evhead', {}, h('div.big', { text: 'No connectors yet' }), h('div.dim', { text: 'A connector is a remote MCP server: it gives the agent extra tools, like your calendar, notes, a smart home, or any service that offers one. It needs an https URL that works from the internet.' })), h('button.primary', { text: 'Add a connector', onclick: () => cnAdd() }), cnPopular()); return; }
  box.replaceChildren(...CN.list.map((c) => h('div.rtcard', {},
    h('div.rttop', {}, h('span.nm', { text: c.name }), h('span.badge ' + (/^\d+ tools/.test(c.status) ? 'ok' : c.status === 'needs sign-in' ? 'run' : 'bad'), { text: /^\d+ tools/.test(c.status) ? c.status : c.status === 'needs sign-in' ? 'sign in' : 'problem' })),
    h('div.rtmeta', { text: c.url.replace(/^https:\/\//, '').slice(0, 60) + (c.source === 'env' ? ' · set on the server' : '') }),
    /^\d+ tools/.test(c.status) ? h('div.rtlast', { text: c.tools.join(', ') }) : c.status === 'needs sign-in' ? h('div.rtlast', { text: 'Tap Sign in, log in on the page that opens, then come back here.' }) : h('div.rtlast', { style: 'color:var(--bad)', text: c.status }),
    /^\d+ tools/.test(c.status) ? cnPermRow(c) : null,
    c.source === 'app' ? h('div.rtbtns', {}, c.oauth ? h('button', { text: c.signed_in ? 'Sign in again' : 'Sign in', onclick: () => cnLogin(c.name) }) : null, h('button', { text: 'Remove', onclick: async () => { if (!(await confirmSheet('Remove ' + c.name + '?', 'The agent loses those tools.', 'Remove', true))) return; try { CN.list = (await api('/api/connectors/' + c.name, { method: 'DELETE' })).connectors; cnDraw(); closeSheet(); } catch (e) { toast(e.message, true); } } })) : null)),
    h('button.secondary', { text: 'Reconnect all', onclick: () => cnLoad(true) }), cnPopular());
}
const PERMS = [['ask', 'Ask before writes'], ['readonly', 'Read-only'], ['trusted', 'Trusted']];
const PERM_HINT = { ask: 'Reading is automatic. Anything that creates, edits or deletes asks you first.', readonly: 'Reading only. Anything that would change data is blocked, even in YOLO.', trusted: 'Everything runs without asking. Only for apps you trust with your data.' };
function cnPermRow(c) {
  const writes = c.tools.length - c.reads;
  return h('div.cnperm', {}, h('div.seg', {}, ...PERMS.map(([k, n]) => h('button' + (c.perm === k ? '.on' : ''), { text: n, onclick: async () => { try { CN.list = (await api('/api/connectors/' + encodeURIComponent(c.name), { method: 'PATCH', body: { perm: k } })).connectors; cnDraw(); } catch (e) { toast(e.message, true); } } }))),
    h('div.rtmeta', { text: `${PERM_HINT[c.perm] || ''} (${c.reads} read tools, ${writes} that change things)` }));
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
function cnAdd(pre) {
  pre = pre && pre.name ? pre : null;
  let mode = pre && pre.oauth ? 'oauth' : 'token';
  const name = h('input', { type: 'text', placeholder: 'short name, e.g. notes', autocapitalize: 'off', spellcheck: 'false' });
  const url = h('input', { type: 'url', placeholder: 'https://example.com/mcp', autocapitalize: 'off', spellcheck: 'false' });
  const auth = h('input', { type: 'text', placeholder: 'e.g. Bearer abc123', autocapitalize: 'off', spellcheck: 'false' });
  if (pre) { name.value = pre.name; url.value = pre.url; if (pre.auth_hint) auth.placeholder = pre.auth_hint; }
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

/* ============================================================ telegram */
async function openTelegram() {
  const body = h('div', {}, h('div.dim', { text: 'Loading…' }));
  sheet('Telegram', body);
  const draw = (t, code) => {
    if (!t.enabled) { body.replaceChildren(h('div.dim', { text: 'Message your agent from Telegram, with replies, files and Allow / Deny buttons for approvals.' }),
      h('ol.dim', { style: 'padding-left:20px;font-size:14px;line-height:1.6' }, h('li', { text: 'In Telegram, open @BotFather and send /newbot. Copy the token it gives you.' }), h('li', { text: 'In Render, add an environment variable TELEGRAM_BOT_TOKEN with that token and redeploy.' }), h('li', { text: 'Come back here and tap Connect.' }))); return; }
    const kids = [h('div.dim', { style: 'font-size:14px', text: `Bot @${t.bot || '…'} · ${t.paired} paired chat${t.paired === 1 ? '' : 's'}${t.webhook ? ' · connected' : ' · not connected yet'}` })];
    if (t.error) kids.push(h('div.dim', { style: 'color:var(--bad);font-size:13px', text: t.error }));
    if (code) kids.push(h('div.paircode', { text: code }), h('div.dim', { style: 'font-size:14px', text: `Open the bot in Telegram${t.bot ? ' (t.me/' + t.bot + ')' : ''} and send:  /pair ${code}   The code works once, for 10 minutes.` }));
    kids.push(h('button.primary', { text: code ? 'New code' : (t.paired ? 'Pair another account' : 'Connect and get a pairing code'), onclick: async (e) => { const b = e.currentTarget; b.disabled = true; try { const r = await api('/api/telegram/connect', { body: { base: S.api } }); draw(r, r.code); } catch (er) { toast(er.message, true); b.disabled = false; } } }));
    if (t.paired) kids.push(h('button.secondary', { text: 'Unpair everything', onclick: async () => { try { draw(await api('/api/telegram/paired', { method: 'DELETE' })); } catch (er) { toast(er.message, true); } } }));
    kids.push(h('div.dim', { style: 'font-size:12.5px', text: 'Only chats you pair can use the agent. In Telegram: /new, /stop, /mode ask|auto|yolo, /status, /unpair.' }));
    body.replaceChildren(...kids);
  };
  try { draw(await api('/api/telegram')); } catch (e) { body.replaceChildren(h('div.dim', { style: 'color:var(--bad)', text: e.message })); }
}

/* ============================================================ instagram */
async function openInstagram() {
  const body = h('div', {}, h('div.dim', { text: 'Loading…' }));
  sheet('Instagram', body);
  const copy = (t) => h('div.hookbox', { text: t, onclick: () => { try { navigator.clipboard.writeText(t); toast('Copied'); } catch { /* selectable anyway */ } } });
  const draw = (t, code) => {
    if (!t.enabled) { body.replaceChildren(h('div.dim', { text: 'Talk to your agent in the DMs of an Instagram professional (Creator or Business) account. It can only reply to people who messaged it first, within 24 hours. Setup is in the README ("Instagram DMs"): create a Meta app, then add INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_APP_SECRET in Render and redeploy.' })); return; }
    const kids = [h('div.dim', { style: 'font-size:14px', text: `${t.username ? '@' + t.username : 'Connected account'} · ${t.paired} paired account${t.paired === 1 ? '' : 's'}` })];
    if (t.error) kids.push(h('div.dim', { style: 'color:var(--bad);font-size:13px', text: t.error }));
    if (t.callback) kids.push(h('div.dim', { style: 'font-size:13px', text: 'Webhook callback URL (paste in the Meta dashboard):' }), copy(t.callback), h('div.dim', { style: 'font-size:13px', text: 'Verify token:' }), copy(t.verify_token));
    if (code) kids.push(h('div.paircode', { text: code }), h('div.dim', { style: 'font-size:14px', text: `From the account you want to control it with, DM your professional account:  pair ${code}   The code works once, for 10 minutes.` }));
    kids.push(h('button.primary', { text: code ? 'New code' : 'Check connection and get a pairing code', onclick: async (e) => { const b = e.currentTarget; b.disabled = true; try { const r = await api('/api/instagram/connect', { body: { base: S.api } }); draw(r, r.code); } catch (er) { toast(er.message, true); b.disabled = false; } } }));
    if (t.paired) kids.push(h('button.secondary', { text: 'Unpair everything', onclick: async () => { try { draw(await api('/api/instagram/paired', { method: 'DELETE' })); } catch (er) { toast(er.message, true); } } }));
    kids.push(h('div.dim', { style: 'font-size:12.5px', text: 'In the DM: /new, /stop, /mode ask|auto|yolo, /status, /unpair. Approvals: reply YES or NO. Only paired accounts can use the agent.' }));
    body.replaceChildren(...kids);
  };
  try { draw(await api('/api/instagram?base=' + encodeURIComponent(S.api))); } catch (e) { body.replaceChildren(h('div.dim', { style: 'color:var(--bad)', text: e.message })); }
}

async function openEtsy() {
  const body = h('div', {}, h('div.dim', { text: 'Loading…' }));
  sheet('Etsy', body);
  const copy = (t) => h('div.hookbox', { text: t, onclick: () => { try { navigator.clipboard.writeText(t); toast('Copied'); } catch { /* selectable anyway */ } } });
  const draw = (t) => {
    const kids = [];
    if (!t.configured) {
      kids.push(h('div.dim', { text: 'Lets the agent run an Etsy shop through Etsy\'s official API: read orders and reviews, write listings as drafts, upload files, and (with your approval) publish and edit. No password is stored.' }),
        h('div.dim', { style: 'font-size:14px;margin-top:8px', text: '1. Create an app at etsy.com/developers (Your Apps). 2. Add this redirect URI to the app:' }), copy(t.redirect_uri),
        h('div.dim', { style: 'font-size:14px', text: '3. In Render, set ETSY_API_KEY (the app keystring, and ETSY_SHARED_SECRET if Etsy shows one) and redeploy. 4. Come back here and tap Connect.' }));
    } else if (!t.connected) {
      kids.push(h('div.dim', { text: 'API key is set. Make sure this redirect URI is registered in your Etsy app:' }), copy(t.redirect_uri));
      if (t.error) kids.push(h('div.dim', { style: 'color:var(--bad);font-size:13px', text: t.error }));
      kids.push(h('button.primary', { text: 'Connect Etsy', onclick: async (e) => { const b = e.currentTarget; b.disabled = true; try { const r = await api('/api/etsy/connect', { body: { base: S.api } }); window.open(r.url, '_blank'); b.disabled = false; toast('Approve in the Etsy tab, then reopen this'); } catch (er) { toast(er.message, true); b.disabled = false; } } }));
    } else {
      kids.push(h('div', {}, h('b', { text: t.shop || 'Shop ' + t.shop_id }), h('div.dim', { style: 'font-size:13px', text: 'Connected. Drafts and reads run on their own; publishing or changing a listing asks you every time.' })),
        h('button.secondary.danger', { text: 'Disconnect', onclick: async () => { if (!(await confirmSheet('Disconnect Etsy?', 'The agent loses access until you connect again.', 'Disconnect', true))) return; try { draw(await api('/api/etsy/disconnect', { method: 'POST', body: {} })); } catch (er) { toast(er.message, true); } } }));
    }
    body.replaceChildren(...kids);
  };
  try { const t = await api('/api/etsy?base=' + encodeURIComponent(S.api)); draw(t); } catch (e) { body.replaceChildren(h('div.dim', { style: 'color:var(--bad)', text: e.message })); }
}

async function openLemon() {
  const body = h('div', {}, h('div.dim', { text: 'Loading…' }));
  sheet('Lemon Squeezy', body);
  try {
    const t = await api('/api/lemonsqueezy');
    const kids = [h('div.dim', { text: 'Lemon Squeezy sells your digital products and handles tax and file delivery. The agent reads your products and orders, makes checkout links, and asks before any refund. Its API cannot create products, so you add each one in their dashboard (the agent writes the name, description and price for you to paste).' })];
    if (!t.configured) kids.push(h('div.dim', { style: 'font-size:14px;margin-top:8px', text: 'Setup: in Lemon Squeezy open Settings → API, create a key, then set LEMONSQUEEZY_API_KEY in Render and redeploy. Optional LEMONSQUEEZY_STORE_ID if you have several stores.' }));
    else if (t.error) kids.push(h('div.dim', { style: 'color:var(--bad);font-size:14px;margin-top:8px', text: t.error }));
    else kids.push(h('div', { style: 'margin-top:8px' }, h('b', { text: t.store || 'Connected' }), h('div.dim', { style: 'font-size:13px', text: t.mode === 'test' ? 'Test mode key: no real money moves.' : 'Live key.' })));
    body.replaceChildren(...kids);
  } catch (e) { body.replaceChildren(h('div.dim', { style: 'color:var(--bad)', text: e.message })); }
}

/* ============================================================ app drawer */
const AH = { id: '', name: '', frame: null, data: null };
async function openApps() {
  $('#appspage').hidden = false;
  const grid = $('#ap-grid'); grid.replaceChildren(h('div.dim.ap-empty', { text: 'Loading…' }));
  let list;
  try { list = (await api('/api/apps')).apps; } catch (e) { grid.replaceChildren(h('div.dim.ap-empty', { style: 'color:var(--bad)', text: e.message })); return; }
  if (!list.length) { grid.replaceChildren(h('div.dim.ap-empty', {}, h('div', { style: 'font-size:34px', text: '▦' }), h('div', { style: 'margin-top:8px', text: 'No apps yet. Ask the agent to build one:' }), h('div', { style: 'margin-top:6px;font-size:13.5px;line-height:1.6', html: '“make me a pomodoro timer that remembers my sessions”<br>“build a unit converter for my wearable project”<br>“a flashcard app that quizzes me with AI”' }))); return; }
  grid.replaceChildren(...list.map((a) => {
    let pressT = 0, long = false;
    const t = h('button.ap-tile', {}, a.ai ? h('span.ap-ai', { text: 'AI' }) : null, h('div.ap-ico', { text: a.icon || '▦' }), h('div.ap-nm', { text: a.name }));
    t.addEventListener('pointerdown', () => { long = false; pressT = setTimeout(() => { long = true; appMenu(a); }, 550); });
    for (const ev of ['pointerup', 'pointerleave', 'pointercancel']) t.addEventListener(ev, () => clearTimeout(pressT));
    t.onclick = () => { if (!long) openApp(a.id, a.name); };
    t.addEventListener('contextmenu', (e) => { e.preventDefault(); appMenu(a); });
    return t;
  }));
}
function appMenu(a) {
  sheet(a.name,
    h('div.dim', { style: 'font-size:13.5px;margin-bottom:8px', text: a.description || 'No description' }),
    h('button.primary', { text: 'Open', onclick: () => { closeSheet(); openApp(a.id, a.name); } }),
    h('button.secondary', { text: 'Rename', onclick: async () => { const n = prompt('New name', a.name); if (!n) return; try { await api('/api/apps/' + a.id, { method: 'PATCH', body: { name: n } }); closeSheet(); openApps(); } catch (e) { toast(e.message, true); } } }),
    h('button.secondary', { text: 'Delete', onclick: async () => { if (!(await confirmSheet('Delete this app?', `${a.name} and its saved data will be removed for good.`, 'Delete'))) return; try { await api('/api/apps/' + a.id, { method: 'DELETE' }); closeSheet(); openApps(); } catch (e) { toast(e.message, true); } } }));
}
async function openApp(id, name) {
  AH.id = id; AH.name = name || id; AH.data = null;
  $('#ah-title').textContent = AH.name; $('#apphost').hidden = false;
  try {
    if (!name) { const a = (await api('/api/apps')).apps.find((x) => x.id === id); if (a) { AH.name = a.name; $('#ah-title').textContent = a.name; } }
    const { url } = await api(`/api/apps/${id}/token`, { method: 'POST' });
    $('#ah-frame').src = S.api + url;
  } catch (e) { toast(e.message, true); closeApp(); }
}
function closeApp() { $('#apphost').hidden = true; $('#ah-frame').src = 'about:blank'; AH.id = ''; }
async function appBridge(e) {
  const f = $('#ah-frame'), m = e.data;
  if (!AH.id || !m || !m.__pa || e.source !== f.contentWindow) return;
  const reply = (o) => { try { f.contentWindow.postMessage({ __pa_r: 1, id: m.id, ...o }, '*'); } catch { /* app closed */ } };
  const id = AH.id;
  try {
    if (m.type === 'ask') { reply({ value: (await api(`/api/apps/${id}/ask`, { body: { prompt: m.prompt, system: m.system } })).text }); return; }
    if (m.type === 'act') {
      const go = () => api(`/api/apps/${id}/act`, { body: { prompt: m.prompt } });
      let r;
      try { r = await go(); } catch (er) {
        if (er.message !== 'NOT_GRANTED') throw er;
        if (!(await confirmSheet(`Let "${AH.name}" use your agent?`, 'This app wants to ask your agent to do things for it, like saving to Notion with your connectors. Each request runs as a visible chat. It can\'t run commands or change files. You can change your mind by deleting the app.', 'Allow'))) throw new Error('not allowed');
        await api(`/api/apps/${id}/grant`, { body: { allow: true } }); r = await go();
      }
      reply({ value: r.text }); return;
    }
    if (AH.data === null || AH.dataFor !== id) { AH.data = (await api(`/api/apps/${id}/data`)).data || {}; AH.dataFor = id; }
    if (m.type === 'get') reply({ value: m.key in AH.data ? AH.data[m.key] : null });
    else if (m.type === 'keys') reply({ value: Object.keys(AH.data) });
    else if (m.type === 'set') { await api(`/api/apps/${id}/data/${encodeURIComponent(m.key)}`, { method: 'PUT', body: { value: m.value } }); AH.data[m.key] = m.value; reply({ value: true }); }
    else if (m.type === 'del') { await api(`/api/apps/${id}/data/${encodeURIComponent(m.key)}`, { method: 'DELETE' }); delete AH.data[m.key]; reply({ value: true }); }
    else reply({ error: 'unknown call' });
  } catch (er) { reply({ error: er.message }); }
}
function initApps() {
  $('#ap-back').onclick = () => { $('#appspage').hidden = true; };
  $('#ah-back').onclick = closeApp;
  $('#ah-reload').onclick = () => { if (AH.id) openApp(AH.id, AH.name); };
  $('#ah-edit').onclick = () => { const id = AH.id, nm = AH.name; closeApp(); $('#appspage').hidden = true; const i = $('#input'); i.value = `Change my app "${nm}" (id: ${id}): `; i.focus(); };
  window.addEventListener('message', appBridge);
}

/* ============================================================ forged tools */
async function openForge() {
  const body = h('div', {}, h('div.dim', { text: 'Loading…' }));
  sheet('Forged tools', body);
  const draw = async () => {
    let list;
    try { list = (await api('/api/forge')).tools; } catch (e) { body.replaceChildren(h('div.dim', { style: 'color:var(--bad)', text: e.message })); return; }
    const kids = [h('div.dim', { style: 'font-size:14px', text: 'Tools the agent wrote for itself. Each was shown to you and approved first, tested in a sandbox, and runs in a throwaway process. Ask the agent to "forge a tool that…" to make one.' })];
    if (!list.length) kids.push(h('div.dim', { style: 'padding:14px 0', text: 'None yet.' }));
    for (const t of list) {
      kids.push(h('div.cnrow', {},
        h('div', { style: 'flex:1;min-width:0' }, h('div', { style: 'font-weight:600', text: 'forged_' + t.name + (t.network ? '  · network' : '') }),
          h('div.dim', { style: 'font-size:13px', text: `${t.description} · used ${t.uses || 0}×${t.last_error ? ' · last error: ' + t.last_error : ''}` })),
        h('button.small', { text: t.enabled ? 'On' : 'Off', onclick: async () => { try { await api('/api/forge/' + t.name, { method: 'PATCH', body: { enabled: !t.enabled } }); draw(); } catch (e) { toast(e.message, true); } } }),
        h('button.small', { text: 'Code', onclick: async () => { try { const r = await api('/api/forge/' + t.name); sheet('forged_' + t.name, h('pre.hookbox', { style: 'white-space:pre-wrap;max-height:60vh;overflow:auto', text: r.code })); } catch (e) { toast(e.message, true); } } }),
        h('button.small', { text: '✕', onclick: async () => { if (!(await confirmSheet('Delete this tool?', 'forged_' + t.name + ' will be removed for good.', 'Delete'))) return; try { await api('/api/forge/' + t.name, { method: 'DELETE' }); draw(); } catch (e) { toast(e.message, true); } } })));
    }
    body.replaceChildren(...kids);
  };
  draw();
}

/* ============================================================ back-tap assistant */
async function openShortcut() {
  const body = h('div', {}, h('div.dim', { text: 'Loading…' }));
  sheet('Back-tap assistant', body);
  const copy = (t) => h('div.hookbox', { text: t, onclick: () => { try { navigator.clipboard.writeText(t); toast('Copied'); } catch { /* selectable anyway */ } } });
  const draw = (st, token) => {
    const kids = [h('div.dim', { style: 'font-size:14px', text: 'Press the Action Button, triple-tap the back of your iPhone, or ask Siri: a screenshot goes to your agent, and the answer comes back as a notification. It also works from the share sheet with text or links.' })];
    if (token) kids.push(h('div.dim', { style: 'font-size:13px;color:var(--warn,#e8b04a)', text: 'Your token (shown once, copy it now):' }), copy(token));
    else if (st.enabled) kids.push(h('div.dim', { style: 'font-size:13px', text: `Token ${st.hint} · used ${st.uses} time${st.uses === 1 ? '' : 's'}` }));
    kids.push(h('div.dim', { style: 'font-size:13px', text: 'Address to POST to:' }), copy(S.api + '/api/shortcut'));
    kids.push(h('button.primary', { text: st.enabled ? 'Make a new token (old one stops working)' : 'Make a token', onclick: async (e) => { const b = e.currentTarget; b.disabled = true; try { const r = await api('/api/shortcut/token', { body: {} }); draw({ enabled: true, hint: '', uses: 0 }, r.token); } catch (er) { toast(er.message, true); b.disabled = false; } } }));
    if (st.enabled) kids.push(h('button.secondary', { text: 'Revoke', onclick: async () => { try { await api('/api/shortcut', { method: 'DELETE' }); draw({ enabled: false }); } catch (er) { toast(er.message, true); } } }));
    const tk = token || 'YOUR_TOKEN';
    const step = (n, t) => h('li', { text: t });
    kids.push(h('div.dim', { style: 'font-size:13px;font-weight:600;margin-top:10px', text: 'Recipe 1 · Talk to it (voice, answer read aloud)' }));
    kids.push(h('ol.dim', { style: 'padding-left:20px;font-size:13.5px;line-height:1.6' },
      step(1, 'Open the Shortcuts app → tap + (top right) → name it "Jarvis".'),
      step(2, 'Add action "Dictate Text" (search for it). Leave the defaults.'),
      step(3, 'Add "Get Contents of URL". Tap URL and paste the address above.'),
      step(4, 'Tap the arrow on that action to expand it: Method = POST. Headers → Add new header: key  Authorization  value  Bearer ' + tk + '  (the word Bearer, a space, then your token).'),
      step(5, 'Request Body = JSON. Tap "Add new field" → Text. Key: text. Value: tap the field, then choose the magic variable "Dictated Text".'),
      step(6, 'Add "Get Dictionary Value". Get: Value, Key: answer, from "Contents of URL".'),
      step(7, 'Add "Speak Text" and give it the Dictionary Value. (Or "Show Notification" if you don\'t want it spoken.)'),
      step(8, 'Test: run it from the Shortcuts app and say "ping". You should hear: Connected, your Shortcut can reach your agent. Ping costs no model call. Then say a real question.'),
      step(9, 'Triggers: Action Button (iPhone 15 Pro+): Settings → Action Button → Shortcut → Jarvis. Any iPhone: Settings → Accessibility → Touch → Back Tap → Double Tap → Jarvis. Or say "Hey Siri, Jarvis".')));
    kids.push(h('div.dim', { style: 'font-size:13px;font-weight:600;margin-top:10px', text: 'Recipe 2 · Show it your screen' }));
    kids.push(h('ol.dim', { style: 'padding-left:20px;font-size:13.5px;line-height:1.6' },
      step(1, 'New shortcut "Look". Add "Take Screenshot".'),
      step(2, 'Add "Get Contents of URL": same address, Method POST, same Authorization header. Request Body = File, and choose the Screenshot.'),
      step(3, 'Then "Get Dictionary Value" (key answer) → "Show Notification". Assign it to Back Tap → Triple Tap.')));
    kids.push(h('div.dim', { style: 'font-size:13px;font-weight:600;margin-top:10px', text: 'Recipe 3 · Share anything to it' }));
    kids.push(h('ol.dim', { style: 'padding-left:20px;font-size:13.5px;line-height:1.6' },
      step(1, 'New shortcut "Ask agent". Tap the (i) button → turn on "Show in Share Sheet".'),
      step(2, 'Add "Get Contents of URL": same address and header, Request Body = JSON, field Text named url (or text) set to "Shortcut Input".'),
      step(3, 'Then notify as above. Now Share → Ask agent works from Safari, Notes, Messages.')));
    kids.push(h('div.dim', { style: 'font-size:12.5px', text: 'Nobody can tap Allow from a Shortcut, so it only uses actions that need no approval. Each use opens a chat called "Back-tap" you can read later. Up to 30 uses an hour.' }));
    body.replaceChildren(...kids);
  };
  try { draw(await api('/api/shortcut/status')); } catch (e) { body.replaceChildren(h('div.dim', { style: 'color:var(--bad)', text: e.message })); }
}

async function openMcpServer() {
  const body = h('div', {}, h('div.dim', { text: 'Loading…' }));
  sheet('Agent as MCP server', body);
  const copy = (t) => h('div.hookbox', { text: t, onclick: () => { try { navigator.clipboard.writeText(t); toast('Copied'); } catch { /* selectable anyway */ } } });
  const draw = (st, token) => {
    const url = token ? `${S.api}/mcp/${token}` : '';
    const kids = [h('div.dim', { style: 'font-size:14px', text: 'Lets other apps (Claude Desktop, Cursor, another agent) use this agent as a tool. They get ask_agent, list_chats and read_chat. Each call opens a chat called "MCP" you can read later.' })];
    if (token) kids.push(h('div.dim', { style: 'font-size:13px;color:var(--warn,#e8b04a)', text: 'Your server address contains the token. It is shown once, copy it now:' }), copy(url));
    else if (st.enabled) kids.push(h('div.dim', { style: 'font-size:13px', text: `Token ${st.hint} · used ${st.uses} time${st.uses === 1 ? '' : 's'}. The address is ${S.api}/mcp/YOUR_TOKEN` }));
    kids.push(h('button.primary', { text: st.enabled ? 'Make a new token (old one stops working)' : 'Make a token', onclick: async (e) => { const b = e.currentTarget; b.disabled = true; try { const r = await api('/api/mcp-server/token', { body: {} }); draw({ enabled: true, hint: '', uses: 0 }, r.token); } catch (er) { toast(er.message, true); b.disabled = false; } } }));
    if (st.enabled) kids.push(h('button.secondary', { text: 'Revoke', onclick: async () => { try { await api('/api/mcp-server', { method: 'DELETE' }); draw({ enabled: false }); } catch (er) { toast(er.message, true); } } }));
    kids.push(h('div.dim', { style: 'font-size:13px;font-weight:600;margin-top:6px', text: 'Add it to a client' }));
    kids.push(h('ol.dim', { style: 'padding-left:20px;font-size:13.5px;line-height:1.6' },
      h('li', { text: 'Claude Desktop / claude.ai: Settings → Connectors → Add custom connector → paste the address above.' }),
      h('li', { text: 'Claude Code: claude mcp add --transport http pocket-agent <address>' }),
      h('li', { text: 'Cursor and others: add a remote (Streamable HTTP) server with the address.' })));
    kids.push(h('div.dim', { style: 'font-size:12.5px', text: 'Nobody can tap Allow for a remote call, so it only uses actions that need no approval. Anyone with the address can use your agent (and your model quota), so treat it like a password. Up to 60 calls an hour. Render free sleeps: the first call after a nap can be slow.' }));
    body.replaceChildren(...kids);
  };
  try { draw(await api('/api/mcp-server/status')); } catch (e) { body.replaceChildren(h('div.dim', { style: 'color:var(--bad)', text: e.message })); }
}

/* ============================================================ world engine */
const worldAgo = (t) => { const m = Math.round((Date.now() / 1000 - t) / 60); return m < 1 ? 'just now' : m < 60 ? m + ' min ago' : m < 1440 ? Math.round(m / 60) + ' h ago' : Math.round(m / 1440) + ' d ago'; };
const WUN = { ids: new Set() };
const worldUnread = (w) => { const seen = worldSeen.get(w.id); return w.log.some((e) => e.who !== 'You' && e.who !== 'Narrator' && e.t > seen); };
async function refreshWorldDot() {
  if (!S.token) return;
  try { WUN.ids = new Set((await api('/api/worlds')).worlds.filter(worldUnread).map((w) => w.id)); } catch { return; }
  $('#wl-dot').hidden = WUN.ids.size === 0;
}
function openWorldFromLink(wid) { closeDrawer(); worldView(wid, openWorlds); }
async function openWorlds() {
  const body = h('div', {}, h('div.dim', { text: 'Loading…' }));
  sheet('World engine', body);
  const draw = async () => {
    let list;
    try { list = (await api('/api/worlds')).worlds; } catch (e) { body.replaceChildren(h('div.dim', { style: 'color:var(--bad)', text: e.message })); return; }
    const kids = [h('div.dim', { style: 'font-size:14px', text: 'Little worlds that keep living while you are away. Give a premise and a cast; every so often the characters act on their own goals, remember what happened, and you get a notification. Step in any time.' })];
    kids.push(h('button.primary', { text: 'New world', onclick: () => worldForm(null, openWorlds) }));
    if (!list.length) kids.push(h('div.dim', { style: 'padding:14px 0', text: 'None yet.' }));
    for (const w of list) {
      const last = w.log[w.log.length - 1];
      kids.push(h('div.cnrow', { style: 'cursor:pointer', onclick: () => worldView(w.id, openWorlds) },
        h('div', { style: 'flex:1;min-width:0' },
          h('div', { style: 'font-weight:600', text: '🌍 ' + w.name + (w.enabled ? '' : '  · paused') + (w.notify ? '' : '  🔕') + (worldUnread(w) ? '  ●' : '') }),
          h('div.dim', { style: 'font-size:13px', text: `${w.cast.map((c) => c.name).join(', ')} · every ${w.minutes} min · ${w.ticks} beats` }),
          last ? h('div.dim', { style: 'font-size:13px;margin-top:2px', text: `${last.who}: ${last.text}`.slice(0, 120) }) : null,
          w.error ? h('div', { style: 'font-size:12.5px;color:var(--bad)', text: w.error.slice(0, 140) }) : null)));
    }
    body.replaceChildren(...kids);
  };
  draw();
}

const worldSeen = { get: (id) => { try { return Number(localStorage.getItem('world-seen-' + id)) || 0; } catch { return 0; } }, set: (id, t) => { try { localStorage.setItem('world-seen-' + id, String(t)); } catch { /* private mode */ } } };
const worldColor = (name) => { let n = 0; for (const ch of name) n = (n * 31 + ch.charCodeAt(0)) % 360; return `hsl(${n} 70% 68%)`; };
async function worldView(id, back = openWorlds) {
  const box = h('div', {}, h('div.dim', { text: 'Loading…' }));
  sheet('World', box);
  const render = (w) => {
    const seen = worldSeen.get(id);
    const rows = [];
    let divided = false;
    for (const e of w.log) {
      if (!divided && seen && e.t > seen && e.who !== 'You') { rows.push(h('div', { style: 'text-align:center;font-size:11.5px;color:var(--accent,#7aa2ff);border-top:1px solid var(--line);margin:6px 0 2px;padding-top:4px', text: 'new since you last looked' })); divided = true; }
      if (e.kind === 'look') { rows.push(h('div', { style: 'align-self:center;font-size:12px;opacity:.65;font-style:italic', text: '🔎 ' + e.text })); continue; }
      const you = e.who === 'You', nar = e.who === 'Narrator';
      rows.push(h('div', { style: `align-self:${you ? 'flex-end' : 'flex-start'};max-width:92%;background:${you ? 'var(--accent,#7aa2ff)' : 'var(--panel2)'};color:${you ? 'var(--accent-ink,#fff)' : 'inherit'};border-radius:14px;padding:7px 11px;font-size:15px;line-height:1.45${nar ? ';font-style:italic;opacity:.85' : ''}` },
        nar || you ? null : h('div', { style: `font-weight:700;font-size:12.5px;color:${worldColor(e.who)}`, text: e.who + (e.kind === 'think' ? ' (thinking)' : e.kind === 'do' ? ' (does)' : '') }),
        h('div', { style: e.kind === 'think' ? 'opacity:.75;font-style:italic' : '', text: e.text }),
        h('div', { style: 'font-size:11px;opacity:.6;margin-top:2px', text: worldAgo(e.t) })));
    }
    if (w.log.length) { worldSeen.set(id, w.log[w.log.length - 1].t); WUN.ids.delete(id); $('#wl-dot').hidden = WUN.ids.size === 0; }
    const log = h('div.worldlog', { style: 'max-height:52vh;overflow:auto;display:flex;flex-direction:column;gap:7px;margin:8px 0' }, ...rows);
    const inp = h('input', { placeholder: 'Step in: "a stranger knocks at the door"', style: 'flex:1', maxlength: 500 });
    const send = async () => { const t = inp.value.trim(); if (!t) return; inp.value = ''; try { render(await api(`/api/worlds/${id}/say`, { body: { text: t } })); toast('Will happen on the next beat'); } catch (e) { toast(e.message, true); } };
    inp.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') send(); });
    const mem = w.cast.map((c) => h('div.dim', { style: 'font-size:13px' }, h('b', { text: c.name + ': ' }), (c.memory.slice(-3).join(' · ') || 'remembers nothing yet')));
    const patch = async (b) => { try { render(await api(`/api/worlds/${id}`, { method: 'PATCH', body: b })); } catch (er) { toast(er.message, true); } };
    box.replaceChildren(
      h('div', { style: 'font-weight:600;font-size:16px', text: '🌍 ' + w.name + (w.notify ? '' : '  🔕') }),
      h('div.dim', { style: 'font-size:13px;white-space:pre-wrap', text: `${w.premise}\nModel ${w.model} (pinned) · every ${w.minutes} min · ${w.enabled ? 'running' : 'paused'} · notifications ${w.notify ? 'on' : 'muted'}${w.tools ? ' · can look things up' : ''}` }),
      w.error ? h('div', { style: 'font-size:13px;color:var(--bad)', text: w.error }) : null,
      log,
      h('div', { style: 'display:flex;gap:8px' }, inp, h('button.small', { text: 'Send', onclick: send })),
      w.pending ? h('div.dim', { style: 'font-size:12.5px', text: `${w.pending} waiting for the next beat` }) : null,
      h('div', { style: 'display:flex;gap:8px;margin-top:10px;flex-wrap:wrap' },
        h('button.primary', { text: 'Next beat now', onclick: async (e) => { e.currentTarget.disabled = true; e.currentTarget.textContent = 'Playing…'; try { const r = await api(`/api/worlds/${id}/tick`, { body: {} }); if (!r.ok) toast(r.error, true); render(r.world); } catch (er) { toast(er.message, true); } } }),
        h('button.secondary', { text: w.notify ? '🔕 Mute' : '🔔 Unmute', onclick: () => patch({ notify: !w.notify }) }),
        h('button.secondary', { text: w.enabled ? 'Pause' : 'Resume', onclick: () => patch({ enabled: !w.enabled }) }),
        h('button.secondary', { text: 'Edit', onclick: () => worldForm(w, () => worldView(id, back)) }),
        h('button.secondary', { text: 'Delete', onclick: async () => { if (!(await confirmSheet('Delete this world?', w.name + ' and its whole story will be removed.', 'Delete'))) return; try { await api('/api/worlds/' + id, { method: 'DELETE' }); closeSheet(); back(); } catch (er) { toast(er.message, true); } } })),
      h('div.dim', { style: 'font-size:12.5px;margin-top:6px', text: 'Muted worlds keep living and writing their story; you just are not pinged. Pause stops them.' }),
      h('div.dim', { style: 'font-size:13px;font-weight:600;margin-top:12px', text: 'What they remember' }), ...mem);
    log.scrollTop = log.scrollHeight;
  };
  try { render(await api('/api/worlds/' + id)); } catch (e) { box.replaceChildren(h('div.dim', { style: 'color:var(--bad)', text: e.message })); }
}
function worldForm(w, done) {
  const f = (ph, v, tag = 'input') => { const el = h(tag, { placeholder: ph, style: 'width:100%;margin:4px 0' }); el.value = v || ''; return el; };
  const name = f('Name', w && w.name), prem = f('Premise: where and when are we, what is going on?', w && w.premise, 'textarea');
  const cast = f('Cast, one per line:\nMara: a stubborn ferry captain | wants to keep her route\nOtto: a nervous clerk | wants to leave town', w && w.cast.map((c) => `${c.name}: ${c.persona} | ${c.goal}`).join('\n'), 'textarea');
  prem.rows = 3; cast.rows = 6;
  const mins = h('input', { type: 'number', value: w ? w.minutes : 60, min: 10, style: 'width:90px' });
  const ntf = h('input', { type: 'checkbox', style: 'width:auto' }); ntf.checked = w ? w.notify : true;
  const ntl = h('input', { type: 'checkbox', style: 'width:auto' }); ntl.checked = w ? !!w.tools : false;
  const form = h('div', {}, name, prem, cast,
    h('div', { style: 'display:flex;align-items:center;gap:8px;margin:6px 0' }, h('span.dim', { text: 'A beat every' }), mins, h('span.dim', { text: 'minutes (10 or more)' })),
    h('label', { style: 'display:flex;align-items:center;gap:8px;margin:6px 0;font-size:14px' }, ntf, h('span', { text: 'Notify me when something happens' })),
    h('label', { style: 'display:flex;align-items:center;gap:8px;margin:6px 0;font-size:14px' }, ntl, h('span', { text: 'Let characters look things up on the web (up to 3 searches a beat; slower, uses more of the model)' })),
    h('div.dim', { style: 'font-size:12.5px', text: w ? 'Editing the cast keeps the memories of anyone who stays.' : 'The current default model gets pinned to this world. If it fails, the world pauses instead of quietly switching to another model.' }),
    h('button.primary', { text: w ? 'Save' : 'Create', onclick: async (e) => {
      const btn = e.currentTarget; btn.disabled = true;
      try {
        const b = { name: name.value, premise: prem.value, cast: cast.value, minutes: Number(mins.value) || 60, notify: ntf.checked, tools: ntl.checked };
        if (w) await api('/api/worlds/' + w.id, { method: 'PATCH', body: b }); else await api('/api/worlds', { body: b });
        closeSheet(); done();
      } catch (er) { toast(er.message, true); btn.disabled = false; }
    } }));
  sheet(w ? 'Edit world' : 'New world', form);
}

/* ============================================================ platform check */
async function openSelftest() {
  const body = h('div', {});
  sheet('Platform check', body);
  const COL = { ok: 'var(--ok,#5cc98a)', warn: 'var(--warn,#e8b04a)', fail: 'var(--bad)', skip: 'var(--dim,#8a8f98)' }, SYM = { ok: '✓', warn: '!', fail: '✕', skip: '–' };
  const draw = (r, busy) => {
    const kids = [h('div.dim', { style: 'font-size:14px', text: 'Tests the server itself, no model tasks: saving, the model, apps, forging, MCP, worlds, routines, connectors, push, memory. Takes a few seconds. Run it after every update.' }),
      h('button.primary', { text: busy ? 'Checking…' : r ? 'Run again' : 'Run the check', disabled: !!busy, onclick: run })];
    if (r) {
      const s = r.summary;
      kids.push(h('div', { style: 'font-weight:600;margin:10px 0 4px', text: `${s.ok} ok · ${s.warn} look at · ${s.fail} broken · ${s.skip} not set up` }));
      for (const x of r.results) kids.push(h('div', { style: 'display:flex;gap:10px;padding:6px 0;border-top:1px solid var(--line)' },
        h('div', { style: `width:20px;font-weight:700;color:${COL[x.status]}`, text: SYM[x.status] }),
        h('div', { style: 'flex:1;min-width:0' }, h('div', { style: 'font-weight:600', text: x.name }), h('div.dim', { style: 'font-size:13px;white-space:pre-wrap', text: x.detail }))));
    }
    body.replaceChildren(...kids);
  };
  async function run() { draw(null, true); try { draw(await api('/api/selftest', { body: {} })); } catch (e) { draw(null); toast(e.message, true); } }
  draw(null);
}

/* ============================================================ saved skills */
async function openSkills() {
  const body = h('div', {});
  sheet('Skills', body);
  const draw = (d) => {
    const kids = [h('div.dim', { style: 'font-size:14px', text: 'Reusable instructions the agent can load in any chat. Ask it to "save this as a skill" and you approve the text first. Built-in skills ship with the app.' })];
    kids.push(h('button.secondary', { style: 'margin-top:10px', text: 'Install from GitHub', onclick: () => installSkills() }));
    kids.push(h('div.sec', { text: 'Yours' }));
    if (!d.skills.length) kids.push(h('div.dim', { style: 'padding:6px 0', text: 'None yet.' }));
    for (const s of d.skills) {
      kids.push(h('details', { style: 'padding:8px 0;border-top:1px solid var(--line)' },
        h('summary', { style: 'cursor:pointer' }, h('b', { text: s.name }), h('div.dim', { style: 'font-size:13px', text: s.description }),
          s.source ? h('div.dim', { style: 'font-size:12px', text: 'from ' + s.source + (s.files.length ? ' · ' + s.files.length + ' bundled file(s)' : '') }) : ''),
        h('pre', { style: 'white-space:pre-wrap;font-size:12.5px;max-height:240px;overflow:auto;margin:8px 0', text: s.instructions }),
        h('button.secondary.danger', { text: 'Delete', onclick: async (e) => {
          const btn = e.currentTarget;
          if (!(await confirmSheet('Delete ' + s.name + '?', 'Future chats will no longer have it.', 'Delete', true))) return;
          btn.disabled = true;
          try { await api('/api/skills/' + encodeURIComponent(s.name), { method: 'DELETE' }); } catch (er) { toast(er.message, true); }
          load();
        } })));
    }
    kids.push(h('div', { id: 'extras-slot' }));
    kids.push(h('div.sec', { style: 'margin-top:14px', text: 'Built in' }));
    for (const s of d.builtin) kids.push(h('div', { style: 'padding:6px 0;border-top:1px solid var(--line)' }, h('b', { text: s.name }), h('div.dim', { style: 'font-size:13px', text: s.description })));
    body.replaceChildren(...kids);
    const slot = body.querySelector('#extras-slot'); const ek = [];
    extrasSection(ek, load).then(() => slot && slot.replaceChildren(...ek));
  };
  async function load() { try { draw(await api('/api/skills')); } catch (e) { toast(e.message, true); } }
  load();
}

function installSkills() {
  const input = h('input', { placeholder: 'owner/repo or a github.com link to a folder', style: 'width:100%', autocapitalize: 'off', autocorrect: 'off' });
  const out = h('div', {});
  const body = h('div', {},
    h('div.dim', { style: 'font-size:14px', text: 'Reads a public GitHub repo (a Claude plugin works) and lists what it ships: skills, slash commands, sub-agents, hooks and MCP servers. You see the full text and any warnings first; only what you tick is saved, pinned to the commit you saw. Hooks arrive switched off.' }),
    input, h('button', { style: 'margin-top:8px', text: 'Look', onclick: look }), out);
  sheet('Install from GitHub', body);
  let found = null;
  async function look(e) {
    const btn = e.currentTarget; btn.disabled = true; out.replaceChildren(h('div.dim', { text: 'Reading the repo…' }));
    try { found = await api('/api/skills/discover', { body: { source: input.value } }); draw(); }
    catch (er) { out.replaceChildren(h('div.dim', { style: 'color:var(--bad,#c33)', text: er.message })); }
    btn.disabled = false;
  }
  function row(boxes, it, o) {
    const ok = !it.problem;
    const cb = h('input', { type: 'checkbox', disabled: !ok, checked: ok && !(it.warnings || []).length && !it.installed });
    boxes.push([cb, o.key(it)]);
    return h('details', { style: 'padding:8px 0;border-top:1px solid var(--line)' },
      h('summary', { style: 'cursor:pointer' }, cb, ' ', h('b', { text: o.title(it) }),
        it.installed ? h('span.dim', { text: ' · already installed, will be replaced' }) : '',
        o.sub(it) ? h('div.dim', { style: 'font-size:13px', text: o.sub(it) }) : '',
        it.problem ? h('div', { style: 'color:var(--bad,#c33);font-size:13px', text: 'Cannot install: ' + it.problem }) : '',
        ...(it.warnings || []).map((w) => h('div', { style: 'color:#c80;font-size:13px', text: '⚠ ' + w })),
        ...(it.notes || []).map((w) => h('div.dim', { style: 'font-size:12px', text: 'note: ' + w })),
        o.extra ? o.extra(it) : ''),
      h('pre', { style: 'white-space:pre-wrap;font-size:12.5px;max-height:260px;overflow:auto;margin:8px 0', text: o.text(it) }));
  }
  function draw() {
    const total = found.skills.length + found.commands.length + found.agents.length + found.hooks.length + found.mcp.length;
    const kids = [h('div.dim', { style: 'padding:8px 0', text: found.owner + '/' + found.repo + ' @ ' + found.sha.slice(0, 10) + ' · ' + total + ' item(s)' + (found.note ? ' · ' + found.note : '') })];
    const groups = { skills: [], commands: [], agents: [], hooks: [], mcp: [] };
    const sect = (label, arr, key, o) => { if (!arr.length) return; kids.push(h('div.sec', { style: 'margin-top:12px', text: label })); for (const it of arr) kids.push(row(groups[key], it, o)); };
    sect('Skills', found.skills.map((x) => ({ ...x, problem: x.problem })), 'skills', {
      key: (it) => it.path, title: (it) => it.clean_name || it.name, sub: (it) => it.description, text: (it) => it.instructions,
      extra: (it) => it.files_list.length ? h('div.dim', { style: 'font-size:12px', text: 'bundled: ' + it.files_list.join(', ') }) : '' });
    sect('Slash commands', found.commands, 'commands', { key: (it) => it.path, title: (it) => '/' + it.name, sub: (it) => it.description, text: (it) => it.body });
    sect('Sub-agents', found.agents, 'agents', { key: (it) => it.path, title: (it) => it.name, sub: (it) => it.description, text: (it) => it.prompt });
    sect('Hooks (installed switched off)', found.hooks, 'hooks', { key: (it) => it.id, title: (it) => it.event + ' · ' + it.matcher, sub: (it) => it.plugin_files.length ? 'ships ' + it.plugin_files.length + ' plugin file(s)' : '', text: (it) => it.preview || it.command });
    sect('MCP servers → Connectors', found.mcp, 'mcp', { key: (it) => it.name, title: (it) => it.name, sub: (it) => it.url || it.command, text: (it) => it.url || it.command });
    if (total) kids.push(h('button', { style: 'margin-top:12px', text: 'Install ticked', onclick: async (e) => {
      const pick = (k) => groups[k].filter(([cb]) => cb.checked).map(([, v]) => v);
      const bodyj = { source: input.value, sha: found.sha, paths: pick('skills'), commands: pick('commands'), agents: pick('agents'), hooks: pick('hooks'), mcp: pick('mcp') };
      if (!Object.values(bodyj).slice(2).some((v) => v.length)) return toast('Tick at least one', true);
      e.currentTarget.disabled = true;
      try {
        const r = await api('/api/skills/install', { body: bodyj });
        toast('Installed ' + r.saved.length + (r.skipped.length ? ' · skipped ' + r.skipped.length + ': ' + r.skipped[0] : ''), !!r.skipped.length && !r.saved.length);
        openSkills();
      } catch (er) { toast(er.message, true); e.currentTarget.disabled = false; }
    } }));
    out.replaceChildren(...kids);
  }
}

async function extrasSection(kids, reload) {
  let x;
  try { x = await api('/api/extras'); } catch (e) { return; }
  const del = (kind, key, label) => h('button.secondary.danger', { text: 'Delete', onclick: async () => {
    if (!(await confirmSheet('Delete ' + label + '?', '', 'Delete', true))) return;
    try { await api('/api/extras/' + kind + '/' + encodeURIComponent(key), { method: 'DELETE' }); } catch (er) { toast(er.message, true); }
    reload();
  } });
  if (x.commands.length) kids.push(h('div.sec', { style: 'margin-top:14px', text: 'Slash commands' }));
  for (const c of x.commands) kids.push(h('details', { style: 'padding:8px 0;border-top:1px solid var(--line)' },
    h('summary', { style: 'cursor:pointer' }, h('b', { text: '/' + c.name }), h('div.dim', { style: 'font-size:13px', text: c.description }), c.source ? h('div.dim', { style: 'font-size:12px', text: 'from ' + c.source }) : ''),
    h('pre', { style: 'white-space:pre-wrap;font-size:12.5px;max-height:220px;overflow:auto;margin:8px 0', text: c.body }), del('commands', c.name, '/' + c.name)));
  if (x.agents.length) kids.push(h('div.sec', { style: 'margin-top:14px', text: 'Sub-agents' }));
  for (const a of x.agents) kids.push(h('details', { style: 'padding:8px 0;border-top:1px solid var(--line)' },
    h('summary', { style: 'cursor:pointer' }, h('b', { text: a.name }), h('div.dim', { style: 'font-size:13px', text: a.description }), a.source ? h('div.dim', { style: 'font-size:12px', text: 'from ' + a.source + (a.can_edit ? ' · can edit' : '') }) : ''),
    h('pre', { style: 'white-space:pre-wrap;font-size:12.5px;max-height:220px;overflow:auto;margin:8px 0', text: a.prompt }), del('agents', a.name, a.name)));
  if (x.hooks.length) kids.push(h('div.sec', { style: 'margin-top:14px', text: 'Hooks (run shell commands around tool calls)' }));
  for (const k of x.hooks) {
    const sw = h('input', { type: 'checkbox', checked: !!k.enabled, onchange: async (e) => {
      const want = e.currentTarget.checked;
      if (want && !(await confirmSheet('Switch this hook on?', 'It will run this shell command around ' + k.event + ' calls matching ' + k.matcher + ':\n\n' + k.command, 'Switch on'))) { e.currentTarget.checked = false; return; }
      try { await api('/api/extras/hooks/' + k.id, { body: { enabled: want } }); } catch (er) { toast(er.message, true); }
    } });
    kids.push(h('div', { style: 'padding:8px 0;border-top:1px solid var(--line)' }, h('label', {}, sw, ' ', h('b', { text: k.event + ' · ' + k.matcher })),
      h('pre', { style: 'white-space:pre-wrap;font-size:12.5px;margin:6px 0', text: k.command }), del('hooks', k.id, 'this hook')));
  }
}

/* ============================================================ toolchain packs */
async function openPacks() {
  const body = h('div', {});
  sheet('Toolchain packs', body);
  let timer = null;
  const draw = (d) => {
    const kids = [h('div.dim', { style: 'font-size:14px', text: `Heavy toolchains the agent switches on only when a task needs them, so the image stays small. They cost disk, not idle memory. Every bash command is also killed above ${d.bash_mem_mb} MB so one runaway script cannot take the server down. Copies are saved to your private GitHub state repo (a release), so after a Render restart a pack comes back with one download. Disk free: ${d.free_mb} MB.` })];
    for (const p of d.packs) {
      const state = p.installed ? `installed · ${p.size_mb} MB` : p.installing ? 'installing…' : `off · needs ~${p.needs_mb} MB`;
      kids.push(h('div', { style: 'padding:10px 0;border-top:1px solid var(--line)' },
        h('div', { style: 'display:flex;justify-content:space-between;gap:8px' }, h('div', { style: 'font-weight:600', text: p.title }), h('div.dim', { style: 'font-size:13px;white-space:nowrap', text: state })),
        h('div.dim', { style: 'font-size:13px;margin:2px 0 6px', text: p.desc }),
        p.cache ? h('div.dim', { style: 'font-size:12.5px;margin-bottom:6px', text: '☁ ' + p.cache }) : null,
        p.error ? h('div', { style: 'font-size:13px;color:var(--bad);margin-bottom:6px;white-space:pre-wrap', text: p.error }) : null,
        p.installed ? h('button.secondary', { text: 'Remove', onclick: async (e) => { const btn = e.currentTarget; btn.disabled = true; try { await api('/api/packs/' + p.name, { method: 'DELETE' }); } catch (er) { toast(er.message, true); } load(); } })
          : h('button', { text: p.installing ? 'Installing…' : 'Install now', disabled: p.installing, onclick: async (e) => { const btn = e.currentTarget; btn.disabled = true; try { await api('/api/packs/' + p.name, { body: {} }); } catch (er) { toast(er.message, true); } load(); } })));
    }
    body.replaceChildren(...kids);
    clearTimeout(timer);
    if (d.packs.some((p) => p.installing) && body.isConnected) timer = setTimeout(load, 4000);
  };
  async function load() { try { draw(await api('/api/packs')); } catch (e) { toast(e.message, true); } }
  load();
}

/* ============================================================ backup & restore */
async function rawPost(path, buf) {
  let r;
  try { r = await fetch(S.api + path, { method: 'POST', headers: { Authorization: 'Bearer ' + S.token, 'Content-Type': 'application/zip' }, body: buf }); } catch { throw new Error('Cannot reach the server.'); }
  let d = null; try { d = await r.json(); } catch { /* empty */ }
  if (!r.ok) throw new Error((d && d.detail) || 'HTTP ' + r.status);
  return d;
}
function openBackup() {
  const body = h('div', {});
  sheet('Backup & restore', body);
  const when = (t) => t ? new Date(t * 1000).toLocaleString() : '?';
  const draw = (state) => {
    const kids = [h('div.dim', { style: 'font-size:14px', text: 'One zip with your chats, apps and their data, routines, worlds, forged tools, memory and the connector list. No passwords, tokens, sign-ins or connector keys go in, so the file is safe to keep around (still, it contains your chats).' }),
      h('button.primary', { text: 'Download backup', onclick: async (e) => {
        const btn = e.currentTarget; btn.disabled = true; btn.textContent = 'Packing…';
        try {
          const r = await fetch(S.api + '/api/backup', { headers: { Authorization: 'Bearer ' + S.token } });
          if (!r.ok) throw new Error('HTTP ' + r.status);
          const blob = await r.blob();
          const a = h('a', { href: URL.createObjectURL(blob), download: `pocket-agent-backup-${new Date().toISOString().slice(0, 10)}.zip` });
          document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 15000);
          toast('Backup downloaded');
        } catch (er) { toast(er.message, true); }
        btn.disabled = false; btn.textContent = 'Download backup';
      } }),
      h('div.dim', { style: 'font-size:13px;font-weight:600;margin-top:10px', text: 'Restore' })];
    const file = h('input', { type: 'file', accept: '.zip,application/zip', style: 'width:100%' });
    file.onchange = async () => {
      const f = file.files[0]; if (!f) return;
      try { const buf = await f.arrayBuffer(); const info = await rawPost('/api/backup/inspect', buf); draw({ buf, info, name: f.name }); } catch (er) { toast(er.message, true); }
    };
    if (!state || (!state.info && !state.done)) kids.push(file);
    if (state && state.info) {
      const c = state.info.counts || {};
      kids.push(h('div', { style: 'font-weight:600', text: state.name }), h('div.dim', { style: 'font-size:13px', text: `Made ${when(state.info.created)}: ` + Object.entries(c).map(([k, v]) => `${v} ${k}`).join(', ') + `.\n${state.info.chats_already_here} chat(s) and ${state.info.apps_already_here} app(s) in it already exist here.` }));
      const ow = h('input', { type: 'checkbox', style: 'width:auto' });
      kids.push(h('label', { style: 'display:flex;align-items:center;gap:8px;margin:6px 0;font-size:14px' }, ow, h('span', { text: 'Replace items that already exist (otherwise they are kept as they are)' })),
        h('div.dim', { style: 'font-size:12.5px', text: 'Restored routines and worlds come back paused, forged tools come back switched off (after being re-tested), chats come back in Ask mode, and saved "always allow" rules do not travel. Turn things back on yourself.' }),
        h('button.primary', { text: 'Restore now', onclick: async (e) => {
          const btn = e.currentTarget; btn.disabled = true; btn.textContent = 'Restoring…';
          try { const r = await rawPost('/api/backup/restore?overwrite=' + (ow.checked ? 1 : 0), state.buf); draw({ done: r }); loadSessions().catch(() => {}); } catch (er) { toast(er.message, true); draw(state); }
        } }),
        h('button.secondary', { text: 'Cancel', onclick: () => draw(null) }));
    }
    if (state && state.done) {
      const r = state.done;
      kids.push(h('div', { style: 'font-weight:600;margin-top:8px', text: 'Restored: ' + (Object.entries(r.restored).filter(([, v]) => v).map(([k, v]) => `${v} ${k}`).join(', ') || 'nothing new (it was all here already)') }));
      if (r.skipped.length) kids.push(h('div.dim', { style: 'font-size:13px;white-space:pre-wrap', text: 'Skipped:\n' + r.skipped.join('\n') }));
      kids.push(h('button.secondary', { text: 'Done', onclick: closeSheet }));
    }
    body.replaceChildren(...kids);
  };
  draw(null);
}

/* ============================================================ usage */
async function openUsage() {
  const body = h('div', {}, h('div.dim', { text: 'Loading…' }));
  sheet('Usage', body);
  try {
    const u = await api('/api/usage');
    const fmt = (n) => n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e3 ? (n / 1e3).toFixed(1) + 'k' : String(n);
    const max = Math.max(1, ...u.days.map((d) => d.in + d.out));
    body.replaceChildren(
      h('div.dim', { style: 'font-size:13px', text: `Last ${u.days.length} day${u.days.length === 1 ? '' : 's'}: ${fmt(u.total.in + u.total.out)} tokens (${fmt(u.total.in)} in, ${fmt(u.total.out)} out), ${u.total.runs} runs, ${u.total.calls} model calls. Counts are for this server only; free tiers have no bill.` }),
      u.days.length ? h('div.usage', {}, ...u.days.map((d) => h('div.urow', {},
        h('div.uday', { text: d.day.slice(5) }),
        h('div.ubar', {}, h('i', { style: `width:${Math.max(2, Math.round(100 * (d.in + d.out) / max))}%` })),
        h('div.unum', { text: fmt(d.in + d.out) }),
        h('div.usub', { text: `${d.runs} runs · ` + Object.entries(d.models).sort((a, b) => (b[1].in + b[1].out) - (a[1].in + a[1].out)).slice(0, 2).map(([m, v]) => `${shortModel(m)} ${fmt(v.in + v.out)}`).join(', ') })))) : h('div.dim', { text: 'Nothing recorded yet. Run something and check back.' }));
  } catch (e) { body.replaceChildren(h('div.dim', { style: 'color:var(--bad)', text: e.message })); }
}

/* ============================================================ memory (notes every chat sees) + import */
const MEM = { notes: [], max: 200 };
async function openMemory() { $('#mempage').hidden = false; await memLoad(); }
function closeMemory() { $('#mempage').hidden = true; }
async function memLoad() { try { const r = await api('/api/memory'); MEM.notes = r.notes; MEM.max = r.max; MEM.auto = (await api('/api/memory/auto')).on; } catch (e) { toast(e.message, true); } memDraw(); }
function memAutoRow() {
  return h('label.rvrow', {}, h('input', { type: 'checkbox', checked: MEM.auto !== false, onchange: async (e) => { try { MEM.auto = (await api('/api/memory/auto', { method: 'PUT', body: { on: e.target.checked } })).on; } catch (er) { toast(er.message, true); e.target.checked = !e.target.checked; } } }),
    h('span', {}, h('b', { text: 'Remember on its own' }), h('div.dim', { style: 'font-size:12.5px', text: 'After your messages it files lasting facts you stated (never secrets) and reminds you of dates you mention. Everything shows up below, and you can edit or forget any of it.' })));
}
function memDraw() {
  const box = $('#membody');
  const actions = h('div.rtbtns', {}, h('button', { text: 'Import', onclick: memImport }), h('button', { text: 'Add a note', onclick: () => memEdit(null) }));
  if (!MEM.notes.length) { box.replaceChildren(memAutoRow(), h('div.evhead', {}, h('div.big', { text: 'Nothing remembered yet' }), h('div.dim', { text: 'Tell the agent "remember that I…", add notes yourself, or import what another assistant already knows about you. Every chat sees these notes.' })), actions); return; }
  box.replaceChildren(memAutoRow(), h('div.dim', { style: 'font-size:13px', text: `${MEM.notes.length} of ${MEM.max} notes. Tap one to edit.` }), actions,
    ...MEM.notes.slice().reverse().map((n) => h('button.rtcard.memnote', { onclick: () => memEdit(n) }, h('div', { class: 'memtxt', text: n.text }), h('div.rtmeta', { text: (n.source === 'auto' ? 'remembered on its own' : n.source === 'agent' ? 'saved by the agent' : n.source === 'import' ? 'imported' : 'added by you') + ' · ' + new Date(n.ts * 1000).toLocaleDateString() }))),
    h('button.secondary', { text: 'Forget everything', onclick: async () => { if (!(await confirmSheet('Forget everything?', 'All saved notes are deleted. Chats keep working, they just stop knowing these facts.', 'Delete all', true))) return; try { MEM.notes = (await api('/api/memory', { method: 'DELETE' })).notes; memDraw(); closeSheet(); } catch (e) { toast(e.message, true); } } }));
}
function memEdit(n) {
  const ta = h('textarea', { placeholder: 'e.g. Prefers short answers. Works on a wearable project.', maxlength: 400 }); ta.value = n ? n.text : '';
  const save = h('button.primary', { text: n ? 'Save' : 'Add note' });
  save.onclick = async () => {
    save.disabled = true;
    try { MEM.notes = (await (n ? api('/api/memory/' + n.id, { method: 'PATCH', body: { text: ta.value } }) : api('/api/memory', { body: { text: ta.value } }))).notes; memDraw(); closeSheet(); } catch (e) { toast(e.message, true); save.disabled = false; }
  };
  const del = n ? h('button.secondary', { text: 'Forget this', onclick: async () => { try { MEM.notes = (await api('/api/memory/' + n.id, { method: 'DELETE' })).notes; memDraw(); closeSheet(); } catch (e) { toast(e.message, true); } } }) : null;
  sheet(n ? 'Edit note' : 'New note', ta, save, del);
}
function memImport() {
  const ta = h('textarea', { placeholder: 'Paste memories, a notes file, or a chat export here…', style: 'min-height:140px' });
  const file = h('input', { type: 'file', accept: '.txt,.md,.json,.csv,text/plain,application/json', hidden: true });
  const pick = h('button.secondary', { text: 'Choose a file', onclick: () => file.click() });
  const dist = h('input', { type: 'checkbox' });
  const go = h('button.primary', { text: 'Preview' });
  file.onchange = async () => { const f = file.files[0]; if (!f) return; if (f.size > 8e6) { toast('That file is too big (8 MB max)', true); return; } ta.value = await f.text(); pick.textContent = f.name; };
  go.onclick = async () => {
    if (!ta.value.trim()) { toast('Paste something or choose a file', true); return; }
    go.disabled = true; go.textContent = dist.checked ? 'Reading with AI…' : 'Reading…';
    try { memPreview(await api('/api/memory/import/preview', { body: { text: ta.value, distill: dist.checked } })); }
    catch (e) { toast(e.message, true); go.disabled = false; go.textContent = 'Preview'; }
  };
  sheet('Import memory',
    h('div.dim', { style: 'font-size:13px', text: 'From ChatGPT: Settings → Personalization → Memory → Manage, copy the list. From any assistant: ask it "list everything you remember about me" and paste the answer. Chat exports (conversations.json) work too.' }),
    ta, h('div.rtbtns', {}, pick), file,
    h('label.chk', {}, dist, h('span', { text: 'Distill with AI (for messy text or chat history)' })),
    h('div.dim', { style: 'font-size:12.5px', text: 'Nothing is saved until you confirm. Imports only add notes; secrets and account numbers are skipped; existing notes are never overwritten.' }), go);
}
function memPreview(r) {
  const rows = r.candidates.map((c) => { const cb = h('input', { type: 'checkbox' }); cb.checked = !c.dup && !c.blocked; cb.disabled = !!(c.dup || c.blocked); return { c, cb }; });
  const count = () => rows.filter((x) => x.cb.checked).length;
  const ok = h('button.primary');
  const upd = () => { ok.textContent = `Import ${count()} note${count() === 1 ? '' : 's'}`; ok.disabled = !count(); };
  rows.forEach((x) => { x.cb.onchange = upd; });
  ok.onclick = async () => {
    ok.disabled = true;
    try { const res = await api('/api/memory/import', { body: { notes: rows.filter((x) => x.cb.checked).map((x) => x.c.text) } }); MEM.notes = res.notes; memDraw(); closeSheet(); toast(`Imported ${res.added} note${res.added === 1 ? '' : 's'}`); } catch (e) { toast(e.message, true); ok.disabled = false; }
  };
  upd();
  const list = h('div.memlist', {}, ...rows.map(({ c, cb }) => h('label.memrow' + (c.dup || c.blocked ? '.off' : ''), {}, cb, h('span', {}, c.text, (c.dup || c.blocked) ? h('em', { text: c.dup ? ' · already saved' : ' · skipped: ' + c.blocked }) : null))));
  sheet(r.candidates.length ? 'Review what to import' : 'Nothing found',
    h('div.dim', { style: 'font-size:13px', text: r.candidates.length ? (r.distilled ? 'Turned into notes by the AI. Untick anything wrong.' : 'Untick anything you do not want.') : 'No usable lines. Try "Distill with AI" for messy text.' }),
    r.candidates.length ? list : null, r.candidates.length ? ok : h('button.secondary', { text: 'Back', onclick: memImport }));
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
      row('◍', 'Memory', openMemory, MEM.notes.length ? String(MEM.notes.length) : ''),
      row('⚭', 'Connectors', openConnectors),
      row('✈', 'Telegram', openTelegram),
      row('◎', 'Instagram DMs', openInstagram),
      row('✦', 'Etsy shop', openEtsy),
      row('🍋', 'Lemon Squeezy', openLemon),
      row('⌘', 'Back-tap assistant', openShortcut),
      row('⚒', 'Forged tools', openForge),
      row('🌍', 'World engine', openWorlds),
      row('⇄', 'Agent as MCP server', openMcpServer),
      row('▤', 'Usage', openUsage),
      row('✓', 'Capability check', openEval),
      row('🩺', 'Platform check', openSelftest),
      row('✨', "What's new", openWhatsNew),
      row('✦', 'Skills', openSkills),
      row('⚙', 'Toolchain packs', openPacks),
      row('💾', 'Backup & restore', openBackup),
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
const EV = { cat: null, ev: null, at: 0, timer: null, poll: null, err: '', busy: false, hist: [] };
const EV_SUITES = { standard: ['Standard', 'quick tasks'], hard: ['Hard', 'bigger multi-file tasks'], all: ['Both', 'everything'] };
const evSkip = () => { try { return JSON.parse(store.get('eval_skip') || '[]'); } catch { return []; } };
function evNames(suite) { const c = EV.cat || {}; return suite === 'all' ? [...(c.standard || []), ...(c.hard || [])] : (c[suite] || []); }
function evChecklist(suite) {
  const names = evNames(suite);
  if (!names.length) return null;
  const skip = new Set(evSkip());
  const set = (arr) => { store.set('eval_skip', JSON.stringify(arr)); evDraw(); };
  const on = names.filter((n) => !skip.has(n)).length;
  return h('div.evsel', {},
    h('div.evselhead', {}, h('span.sec', { text: `Run ${on} of ${names.length}` }),
      h('span.grow'), h('button.linkbtn', { text: 'All', onclick: () => set([...skip].filter((n) => !names.includes(n))) }),
      h('button.linkbtn', { text: 'None', onclick: () => set([...new Set([...skip, ...names])]) })),
    ...names.map((n) => h('label.rvrow.evsel1', {}, h('input', { type: 'checkbox', checked: !skip.has(n), onchange: (e) => { const sk = new Set(evSkip()); if (e.target.checked) sk.delete(n); else sk.add(n); set([...sk]); } }), h('span', { text: n }))));
}
function evRunnable() { const n = evNames(store.get('eval_suite') || 'standard'); return !n.length || n.some((x) => !evSkip().includes(x)); }
function evPick() {
  const effort = store.get('eval_effort') || 'high', suite = store.get('eval_suite') || 'standard';
  const seg = (opts, cur, key, redraw) => h('div.seg', {}, ...Object.entries(opts).map(([k, [name]]) => h('button' + (cur === k ? '.on' : ''), { text: name, onclick: () => { store.set(key, k); redraw(); } })));
  return h('div.evpick', {},
    h('div.sec', { text: 'Effort' }), seg(EFFORTS, effort, 'eval_effort', evDraw), h('div.seg-note', { text: EFFORTS[effort][1] }),
    h('div.sec', { text: 'Tasks' }), seg(EV_SUITES, suite, 'eval_suite', evDraw), h('div.seg-note', { text: EV_SUITES[suite][1] + (suite !== 'standard' ? '. The hard suite takes much longer.' : '') }), evChecklist(suite),
    h('label.rvrow', {}, h('input', { type: 'checkbox', checked: store.get('eval_review') === '1', onchange: (e) => { store.set('eval_review', e.target.checked ? '1' : '0'); evDraw(); } }),
      h('span', {}, h('b', { text: 'Self-review' }), h('div.dim', { style: 'font-size:12.5px', text: 'A fresh reviewer checks each task\'s changes before it counts. Compare scores with and without it.' }))));
}
function evHistory() {
  if (!EV.hist.length) return null;
  const when = (t) => new Date(t * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  return h('div.evhist', {}, h('div.sec', { text: 'Past runs' }), ...EV.hist.slice(0, 12).map((r) => h('div.evhrow', {},
    h('div', { style: 'font-weight:600', text: `${r.passed}/${r.total}` + (r.status === 'stopped' ? ' (stopped)' : '') }),
    h('div.dim', { style: 'font-size:12.5px;flex:1;min-width:0', text: `${shortModel(r.model)} · ${(EFFORTS[r.effort] || [r.effort || '?'])[0]} effort · ${(EV_SUITES[r.suite] || [r.suite || 'standard'])[0]}${r.review ? ' + review' : ''} · ${when(r.at)}` + (r.failed && r.failed.length ? ` · failed: ${r.failed.join(', ')}` : '') }))));
}
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
  if (!EV.cat) { try { EV.cat = (await api('/api/eval-tasks')).suites; evDraw(); } catch { /* the list just won't show */ } }
  try {
    let ev = null;
    const id = store.get('eval');
    if (id) { try { ev = await api('/api/eval/' + id); } catch { ev = null; } }
    if (!ev) ev = (await api('/api/eval-latest')).eval;
    EV.ev = ev; EV.at = Date.now(); EV.err = '';
    try { EV.hist = (await api('/api/eval-history')).history || []; } catch { /* keep old */ }
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
    const { id } = await api('/api/eval', { body: { model: (S.cur && S.cur.model) || '', effort: store.get('eval_effort') || 'high', suite: store.get('eval_suite') || 'standard', review: store.get('eval_review') === '1', skip: evSkip() } });
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
      h('div.dim', { text: `Runs real coding tasks (write code, debug, explore a codebase, build a package, compile C++, build an app; the Hard suite adds bigger ones) on ${shortModel(S.cur && S.cur.model || S.info && S.info.default_model)} and checks each result with code. Runs on the server, so you can close this screen and come back.` })),
      evPick(), h('button.primary', { text: EV.busy ? 'Starting…' : 'Run check', disabled: EV.busy || !evRunnable(), onclick: evStart }));
    const hs = evHistory(); if (hs) parts.push(hs);
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
  parts.push(h('div.evhead', {}, h('div.big', { text: head }), h('div.dim', { text: shortModel(ev.model) + ' · ' + (EFFORTS[ev.effort] || ['High'])[0] + ' effort · ' + (EV_SUITES[ev.suite] || ['Standard'])[0] + (ev.review ? ' + self-review' : '') + (ev.skipped && ev.skipped.length ? ` · ${ev.skipped.length} excluded` : '') + (ev.resumed ? ' · resumed after a restart' : '') + (running ? ' · running on the server' : '') })));
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
  else { parts.push(evPick(), h('button.primary', { text: EV.busy ? 'Starting…' : 'Run again', disabled: EV.busy || !evRunnable(), onclick: evStart })); }
  const hs = !running && evHistory(); if (hs) parts.push(hs);
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
    const { base, live } = await api(`/api/sessions/${S.cur.id}/preview-token`, { method: 'POST' });
    const isLive = PV.path.startsWith('live:');
    const rel = PV.path.replace(/^live:/, '').replace(/^\/+/, '').split('/').map(encodeURIComponent).join('/');
    PV.url = S.api + (isLive ? live : base) + rel;
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
  initBrowser();
  initApps();
  $('#rt-back').onclick = closeRoutines;
  $('#rt-new').onclick = () => rtEdit(null);
  $('#cn-back').onclick = closeConnectors;
  $('#mem-back').onclick = closeMemory;
  $('#mem-new').onclick = () => memEdit(null);
  $('#cn-new').onclick = () => cnAdd();
  $('#pv-back').onclick = closePreview;
  $('#md-back').onclick = closeModel;
  $('#md-wire').onclick = toggleWire;
  $('#md-spin').onclick = toggleSpin;
  $('#md-dl').onclick = () => downloadPath(V3.path);
  $('#pv-reload').onclick = loadPreview;
  $('#pv-open').onclick = () => { if (PV.url) window.open(PV.url, '_blank', 'noopener'); };
  $('#dr-worlds').onclick = () => { closeDrawer(); openWorlds(); };
  $('#dr-settings').onclick = openSettings;
  $('#dr-eval').onclick = () => { closeDrawer(); openEval(); };
  $('#ver').onclick = () => { if (UPD.available) applyUpdate(); else openSettings(); };
  $('#btn-update').onclick = applyUpdate;
  $('#chat-search').oninput = renderList;
  $('#btn-send').onclick = send;
  $('#btn-send-now').onclick = sendNow;
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
  setInterval(() => { if (!document.hidden) refreshWorldDot(); }, 90 * 1000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshWorldDot(); });
  if ('serviceWorker' in navigator && location.protocol.startsWith('http')) {
    navigator.serviceWorker.register('sw.js').catch(() => {});
    navigator.serviceWorker.addEventListener('message', (e) => { if (!e.data || !S.token) return; if (e.data.type === 'open-chat' && e.data.sid) openSession(e.data.sid).catch(() => {}); else if (e.data.type === 'open-world' && e.data.world) openWorldFromLink(e.data.world); });
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
window.__setStatus = setStatus; // used by the UI tests
})();
