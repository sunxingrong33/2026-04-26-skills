/* Shared helpers for the redesigned workbench. Every source-provided string goes through text nodes:
   nothing from the ledger, a database or a user is ever parsed as HTML. */

export const TABS = [['overview', '概览'], ['evidence', '证据'], ['analysis', 'SAR 分析'],
  ['timeline', '时间线与程序'], ['compare', '对照'], ['report', '报告']];
export const TIER_LABEL = {L0: 'L0 索引', L1: 'L1 数据库', L2: 'L2 转录', L3: 'L3 具名确认'};
export const STATUS_LABEL = {proposed: '待确认', confirmed: '已确认', rejected: '被拒绝'};

/** h('div', {class: 'x', onclick: fn, dataset: {k: v}}, 'text', child, [children]) */
export function h(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else if (k === 'value' || k === 'checked' || k === 'disabled' || k === 'selected' || k === 'hidden') node[k] = v;
    else node.setAttribute(k, v === true ? '' : String(v));
  }
  append(node, children);
  return node;
}

function append(node, children) {
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    if (Array.isArray(c)) append(node, c);
    else node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export async function api(path, body) {
  const init = body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)};
  const r = await fetch(path, init);
  let d;
  try { d = await r.json(); } catch { throw new Error('服务返回的内容无法解析（HTTP ' + r.status + '）。'); }
  if (!r.ok) throw new Error(d.error || '请求失败（HTTP ' + r.status + '）。');
  return d;
}

export function setStatus(node, text, error) {
  node.textContent = text || '';
  node.classList.toggle('error', !!error);
}

export function mol(smiles, size = 'm', alt = '结构') {
  return h('img', {class: 'mol ' + size, alt, loading: 'lazy', decoding: 'async',
    src: '/api/depict?size=' + size + '&smiles=' + encodeURIComponent(smiles)});
}

export function tierBadge(tier, status) {
  const text = (tier || 'L0') + (status ? ' · ' + (STATUS_LABEL[status] || status) : '');
  return h('span', {class: 'badge tier-' + (tier || 'L0'), text});
}

export function external(href, text) {
  return href ? h('a', {href, target: '_blank', rel: 'noopener noreferrer', text: text + ' ↗'}) : h('span', {class: 'muted', text});
}

export function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], {type}));
  const a = h('a', {href: url, download: name});
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function num(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  const a = Math.abs(v);
  if (a === 0) return '0';
  if (a >= 100) return v.toFixed(0);
  if (a >= 10) return v.toFixed(1);
  if (a >= 1) return v.toFixed(2);
  return v.toPrecision(3);
}

export function signed(v) { return (v > 0 ? '+' : v < 0 ? '−' : '') + num(Math.abs(v)); }

let reviewCount = null;
async function pendingCount() {
  if (reviewCount === null) {
    reviewCount = api('/api/studies').then(d => d.review_pending).catch(() => null);
  }
  return reviewCount;
}

/** Dark top bar; crumbs is null on the home page, otherwise [[label, href?], ...]. */
export function topbar(crumbs, active) {
  const pill = h('span', {class: 'count-pill', text: '…', 'aria-label': '待确认记录数'});
  pendingCount().then(n => { pill.textContent = n === null ? '?' : String(n); });
  const queue = h('a', {class: 'queue-link' + (active === 'review' ? ' active' : ''), href: '/review'}, '复核队列', pill);
  const bar = crumbs
    ? h('header', {class: 'topbar compact'}, h('a', {class: 'brand', href: '/'}, 'SAR Atlas'),
        h('nav', {class: 'crumbs', 'aria-label': '位置'}, crumbs.map(([label, href], i) => [
          h('span', {'aria-hidden': 'true'}, '/'),
          href ? h('a', {href}, label) : h('span', {class: 'here', 'aria-current': 'page'}, label)])),
        h('div', {class: 'grow'}), h('a', {class: 'classic', href: '/classic'}, '经典界面'), queue)
    : h('header', {class: 'topbar'}, h('a', {class: 'brand', href: '/'}, 'SAR Atlas'),
        h('span', {class: 'tagline'}, '竞对化学证据工作台'), h('div', {class: 'grow'}),
        h('a', {class: 'classic', href: '/classic'}, '经典界面'), queue);
  document.body.prepend(bar);
  return bar;
}

export function studyId() {
  const m = location.pathname.match(/^\/s\/([a-z0-9-]+)\//);
  return m ? m[1] : null;
}

export function studyUrl(id, tab) { return '/s/' + encodeURIComponent(id) + '/' + tab; }

export function studyLine(s) {
  const parts = [];
  if (s.target) parts.push('靶点 ' + [s.target.label, s.target.organism, s.target.chembl_id].filter(Boolean).join(' · '));
  parts.push(s.patents + ' 个专利家族', s.papers + ' 篇论文');
  return parts.join(' · ');
}

/** Study header with tabs; `actions` are nodes placed right of the tabs. Returns the <main>. */
export function studyShell(tab, summary, actions) {
  document.title = summary.name + ' · ' + TABS.find(t => t[0] === tab)[1] + ' · SAR Atlas';
  const head = h('div', {class: 'studyhead'},
    h('div', {class: 'title'}, h('b', {}, summary.name), h('span', {}, studyLine(summary))),
    h('nav', {class: 'tabs', 'aria-label': '调研页面'}, TABS.map(([key, label]) =>
      h('a', {href: studyUrl(summary.id, key), 'aria-current': key === tab ? 'page' : null}, label))),
    h('div', {class: 'actions'}, actions || []));
  const bar = $('header.topbar');
  bar.after(head);
  return $('main');
}

/** Loads one study view, draws the top bar and header, and reports failures in the page. */
export async function loadStudy(tab, view, actions) {
  const id = studyId();
  topbar([['我的调研', '/'], ['…']]);
  try {
    const data = await api('/api/study?id=' + encodeURIComponent(id) + '&view=' + view);
    const here = $('.crumbs .here'); if (here) here.textContent = data.study.name;
    const main = studyShell(tab, data.study, typeof actions === 'function' ? actions(data) : actions);
    return {id, data, main};
  } catch (e) {
    $('main').replaceChildren(h('div', {class: 'note error'}, '调研读取失败：' + e.message + ' ', h('a', {href: '/'}, '返回首页')));
    return new Promise(() => {});  // the page stops here; the error is already shown
  }
}

/** A/B choice is kept per study for this browser tab only, so the evidence and compare pages agree. */
export const pairStore = {
  key: id => 'sar-atlas-pair:' + id,
  get(id) { try { return JSON.parse(sessionStorage.getItem(this.key(id))) || {}; } catch { return {}; } },
  set(id, pair) { try { sessionStorage.setItem(this.key(id), JSON.stringify(pair)); } catch { /* private mode */ } },
};

/** Per-browser remembered reviewer / author name (convenience only; every save still sends it explicitly). */
export const author = {
  get() { try { return localStorage.getItem('sar-atlas-author') || ''; } catch { return ''; } },
  set(v) { try { localStorage.setItem('sar-atlas-author', v); } catch { /* ignore */ } },
};

export function valueBox(v, heat) {
  if (!v) return h('div', {class: 'val none'}, '—');
  if (v.status === 'not_tested' || v.status === 'blank' || v.status === 'not_reported' || v.status === 'not_applicable')
    return h('div', {class: 'val miss', title: v.reason || ''}, v.text + (v.status === 'not_tested' ? ' · 原表空白' : ''));
  if (v.status === 'grade') return h('div', {class: 'val'}, h('span', {class: 'n'}, v.text), h('span', {class: 'u'}, '等级'));
  return h('div', {class: 'val' + (v.qualified ? ' q' : heat ? ' ' + heat : ''), title: v.raw ? '原文：' + v.raw : ''},
    h('span', {class: 'n'}, v.text), v.unit ? h('span', {class: 'u'}, v.unit) : null);
}

export function fail(node, e) {
  node.replaceChildren(h('div', {class: 'note error'}, e.message || String(e)));
}

export const GOALS = {cell_potency_efflux: '改善细胞活性，同时控制外排',
  potency_metabolic_stability: '保持活性，同时改善代谢稳定性'};

/** What the home search box holds. SMILES are confirmed by the local parser before they are trusted. */
export function detect(text) {
  const t = (text || '').trim();
  if (!t) return {kind: 'empty'};
  const compact = t.replace(/[\s-]/g, '').toUpperCase();
  if (/^(WO|US|EP|CN|JP|KR|DE|GB|FR|CA|AU)\d{6,}[A-Z]\d?$/.test(compact)) return {kind: 'patent', value: compact};
  if (/^CHEMBL\d+$/.test(compact)) return {kind: 'chembl', value: compact};
  if (/^[A-Za-z0-9@+\-\[\]()=#$:\/\\.%*]+$/.test(t) && (/[=()\[\]#@\/\\]/.test(t) || /[a-z]\d/.test(t) || /^[BCNOPSFI][a-zA-Z0-9]*$/.test(t) && /[cnos]1|C\d/.test(t)))
    return {kind: 'smiles', value: t};
  return {kind: 'text', value: t};
}

/** The study's saved analysis set-up, or the template default with every suggested assay (as on the analysis page). */
export async function analysisRequest(study) {
  if (study.analysis && study.analysis.goal) {
    const {saved_at, ...req} = study.analysis;
    return {request: {...req, mode: 'analyse'}, saved: saved_at};
  }
  const focus = study.target ? study.target.label : null;
  const s = await api('/api/project-sar', {mode: 'suggest', template: study.goal_template || 'cell_potency_efflux',
    documents: study.documents, focus});
  const props = s.properties.filter(p => p.suggested.length).map(p => ({id: p.id, label: p.label, direction: p.direction,
    assay_ids: p.suggested.map(a => a.assay_id), threshold: p.threshold}));
  return {request: {mode: 'analyse', goal: {label: s.label, properties: props}, documents: study.documents, max_change: 12},
    suggestion: s, saved: null};
}

/** replaceChildren that skips null/false/undefined and flattens arrays (the native one prints "null"). */
export function fill(node, ...children) {
  node.replaceChildren();
  append(node, children);
  return node;
}
