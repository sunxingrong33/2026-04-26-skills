import {h, $, $$, api, loadStudy, mol, tierBadge, valueBox, download, external, pairStore, studyUrl, setStatus, fill} from '/app/core.js';

const {id, data, main} = await loadStudy('evidence', 'evidence', () => [
  h('button', {class: 'btn', type: 'button', onclick: () => download(id + '.evidence.json', JSON.stringify(data, null, 2), 'application/json')}, '下载台账 JSON')]);

const state = {
  tiers: new Set(['L0', 'L1', 'L2', 'L3']), statuses: new Set(['proposed', 'confirmed']), unit: 'nM',
  match: null, open: new Set(data.groups.filter(g => g.kind === 'patent').map(g => g.id)),
  pair: pairStore.get(id),
};
const byId = new Map(data.groups.flatMap(g => g.rows.map(r => [r.id, {...r, group: g}])));
if (state.pair.a && !byId.has(state.pair.a)) state.pair.a = null;
if (state.pair.b && !byId.has(state.pair.b)) state.pair.b = null;

/* ---------- filters */
const toggle = (set, key, label, cls) => h('button', {type: 'button', class: 'toggle ' + cls, 'aria-pressed': String(set.has(key)),
  onclick: e => { set.has(key) ? set.delete(key) : set.add(key); e.currentTarget.setAttribute('aria-pressed', String(set.has(key))); e.currentTarget.textContent = label + (set.has(key) ? ' ✓' : ''); draw(); }},
  label + (set.has(key) ? ' ✓' : ''));
const unitBtn = (u, label) => h('button', {type: 'button', 'aria-pressed': String(state.unit === u), dataset: {unit: u},
  onclick: () => { state.unit = u; $$('[data-unit]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.unit === u))); draw(); }}, label);
const sub = h('input', {class: 'input mono', id: 'substructure', style: 'width:220px;height:32px', placeholder: '例如 Nc1ncccc1O', maxlength: 500,
  'aria-label': '子结构 / SMILES 筛选'});
const subStatus = h('span', {class: 'status small', role: 'status'});
sub.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); filterBySubstructure(); } });

const filters = h('div', {class: 'filters'},
  h('div', {class: 'grp'}, h('span', {}, '来源'), toggle(state.tiers, 'L0', 'L0 索引', 't-l0'), toggle(state.tiers, 'L1', 'L1 数据库', 't-l1'),
    toggle(state.tiers, 'L2', 'L2 转录', 't-l2'), toggle(state.tiers, 'L3', 'L3 确认', 't-l3')),
  h('div', {class: 'grp'}, h('span', {}, '复核状态'), toggle(state.statuses, 'proposed', '待确认', 't-proposed'),
    toggle(state.statuses, 'confirmed', '已确认', 't-confirmed'), toggle(state.statuses, 'rejected', '被拒绝', 't-rejected')),
  h('div', {class: 'grp'}, h('span', {}, '活性单位'), h('div', {class: 'segmented'}, unitBtn('nM', 'nM'), unitBtn('p', 'pKi / pIC50'))),
  h('label', {class: 'grp'}, h('span', {}, '子结构筛选'), sub,
    h('button', {type: 'button', class: 'btn small', onclick: filterBySubstructure}, '筛选'),
    h('button', {type: 'button', class: 'btn small', onclick: () => { sub.value = ''; state.match = null; setStatus(subStatus, ''); draw(); }}, '清除')),
  subStatus,
  h('span', {class: 'grow'}),
  h('span', {class: 'muted small'}, '色阶只在同一文档、同一实验列内比较；跨文档只并列，不算倍数'));

async function filterBySubstructure() {
  const q = sub.value.trim();
  if (!q) { state.match = null; draw(); return; }
  setStatus(subStatus, '正在本机匹配…');
  try {
    const r = await api('/api/discover', {mode: 'smiles', query: q, method: 'substructure', external: false});
    state.match = new Set(r.ledger_matches.rows.map(x => x.compound_id));
    setStatus(subStatus, '台账中 ' + r.ledger_matches.total + ' 个结构含该片段' + (r.ledger_matches.truncated ? '（只列出前 ' + r.ledger_matches.rows.length + ' 个）' : ''));
    data.groups.forEach(g => { if (g.rows.some(x => state.match.has(x.id))) state.open.add(g.id); });
    draw();
  } catch (e) { setStatus(subStatus, e.message, true); }
}

/* ---------- table */
const table = h('div', {class: 'card flush ev-table', id: 'evidence-table'});
const legend = h('div', {class: 'legend'},
  h('span', {}, h('span', {class: 'sw', style: 'background:var(--heat-1)'}), '同列最强'),
  h('span', {}, h('span', {class: 'sw', style: 'background:var(--heat-3);border:1px solid var(--line)'}), '同列最弱'),
  h('span', {}, h('span', {class: 'sw', style: 'border:1.5px dashed var(--accent)'}), '限定值，不参与排序与倍数'),
  h('span', {}, h('span', {class: 'sw', style: 'border:1px dashed var(--line-2)'}), '未测，不填零'),
  h('span', {}, '色阶只用于以 nM 报告的活性列（越低越强）；MW、cLogP、TPSA、RotB 为 RDKit 计算值'));
const bar = h('div', {class: 'abbar', id: 'abbar'});
fill(main, filters, table, legend);
main.after(bar);

export function fixed(k, v) { return k === 'rotb' ? String(v) : v.toFixed(k === 'clogp' ? 2 : 1); }
const DESC = [['mw', 'MW'], ['clogp', 'cLogP'], ['tpsa', 'TPSA'], ['rotb', 'RotB']];
const columns = n => `48px 180px 224px repeat(${n}, minmax(118px, 1fr)) 70px 64px 64px 52px`;

function visible(r) {
  return state.tiers.has(r.tier) && state.statuses.has(r.status) && (!state.match || state.match.has(r.id));
}

function single(vals) { return vals && vals.length === 1 ? vals[0] : null; }

function heatFor(group) {
  // Ranks only within one document and one assay column, exact nM values only.
  const heat = {};
  for (const a of group.assays) {
    if (a.unit !== 'nM') continue;
    const xs = group.rows.map(r => [r.id, single(r.values[a.id])]).filter(([, v]) => v && v.status === 'measured' && !v.qualified && v.value > 0);
    if (xs.length < 2) continue;
    xs.sort((p, q) => p[1].value - q[1].value);
    xs.forEach(([rid], i) => { heat[rid + '|' + a.id] = ['h1', 'h2', 'h3'][Math.min(2, Math.floor(3 * i / xs.length))]; });
  }
  return heat;
}

function pValue(v) {
  if (!v || v.status !== 'measured' || v.unit !== 'nM' || !(v.value > 0)) return v;
  const p = 9 - Math.log10(v.value);
  const flip = {'<': '>', '>': '<', '<=': '>=', '>=': '<=', '~': '~'}[v.relation] || '';
  return {...v, text: (v.relation === '=' ? '' : flip) + p.toFixed(2), unit: ''};
}

function cell(r, a, heat) {
  const vals = r.values[a.id];
  if (!vals || !vals.length) return h('div', {class: 'val none'}, '—');
  if (vals.length > 1) return h('div', {class: 'val multi', title: '重复观测，未取平均'},
    h('span', {class: 'n'}, vals.map(v => (state.unit === 'p' ? pValue(v) : v).text).join(' / ')), h('span', {class: 'u'}, '重复'));
  const v = state.unit === 'p' ? pValue(vals[0]) : vals[0];
  return valueBox(v, heat[r.id + '|' + a.id]);
}

function slotOf(rid) { return state.pair.a === rid ? 'a' : state.pair.b === rid ? 'b' : null; }

function choose(rid, on) {
  const p = state.pair;
  if (!on) { if (p.a === rid) p.a = null; if (p.b === rid) p.b = null; }
  else if (!p.a) p.a = rid;
  else if (!p.b) p.b = rid;
  else p.b = rid;
  if (!p.a && p.b) { p.a = p.b; p.b = null; }
  pairStore.set(id, p);
  draw();
}

function row(r, g, heat) {
  const slot = slotOf(r.id);
  const tags = [tierBadge(r.tier, r.status)];
  if (r.mass) tags.push(h('span', {class: 'badge' + (r.mass.status === 'consistent' ? '' : ' warn'), title: r.mass.summary.text, dataset: {massStatus: r.mass.status}},
    r.mass.status === 'consistent' ? '质谱一致' : '质谱需核对'));
  if (r.stereo) tags.push(h('span', {class: 'badge' + (/未指定|未分配|未为/.test(r.stereo) ? ' warn' : ''), title: r.stereo},
    /未指定/.test(r.stereo) ? '立体未指定' : /未分配/.test(r.stereo) ? '立体未分配' : '立体已注明'));
  const src = r.structure_source || {};
  return h('div', {class: 'ev-grid' + (slot ? ' sel-' + slot : '') + (r.status === 'rejected' ? ' rejected' : ''),
    style: 'grid-template-columns:' + columns(g.assays.length), dataset: {compound: r.id}},
    h('div', {class: 'pick'}, h('input', {type: 'checkbox', checked: !!slot, 'aria-label': '选择 ' + r.short + ' 用于对照',
      onchange: e => choose(r.id, e.target.checked)}), slot ? h('span', {class: 'slot' + (slot === 'b' ? ' b' : '')}, slot.toUpperCase()) : null),
    mol(r.smiles, 'm', g.id + ' ' + r.short + ' 结构'),
    h('div', {class: 'cpd'}, h('span', {class: 'name', title: r.label}, r.short), h('div', {class: 'tags'}, tags),
      src.pdf_page ? external(src.url, '结构 PDF p.' + src.pdf_page) : external(src.url, '结构来源')),
    g.assays.map(a => cell(r, a, heat)),
    DESC.map(([k]) => h('span', {class: 'desc'}, r.descriptors ? fixed(k, r.descriptors[k]) : '—')));
}

function header(g) {
  return h('div', {class: 'ev-grid head', style: 'grid-template-columns:' + columns(g.assays.length)},
    h('span', {}, '对照'), h('span', {}, '结构'), h('span', {}, '化合物 · 来源'),
    g.assays.map(a => h('span', {class: 'assay-h', title: a.protocol || ''}, a.label + (a.unit ? '（' + (state.unit === 'p' && a.unit === 'nM' ? 'p 值' : a.unit) + '）' : ''),
      g.kind === 'paper' ? h('small', {}, a.protocol || '') : null)),
    DESC.map(([, l]) => h('span', {}, l)));
}

function band(g, shown) {
  const open = state.open.has(g.id);
  const pages = [...new Set(g.rows.flatMap(r => Object.values(r.values).flat().map(v => v.source && v.source.pdf_page)).filter(Boolean))];
  const bits = g.kind === 'patent'
    ? ['家族 ' + (g.family_id || '未记载'), '优先权 ' + (g.priority_date || '未记载'), pages.length ? '活性表 PDF p.' + pages.join('、') : '无测量']
    : [g.citation, 'ChEMBL 文档 ' + g.id, g.rows.length + ' 个结构', g.observations + ' 条观测',
       '显示测量最多的 ' + g.assays.length + ' 个实验（共 ' + g.assay_total + ' 个）'];
  return h('div', {class: 'doc-band', dataset: {document: g.id}},
    h('span', {class: 'dot' + (g.kind === 'paper' ? ' paper' : '')}),
    h('b', {}, g.kind === 'patent' ? g.id : g.title),
    h('span', {class: 'muted'}, bits.filter(Boolean).join(' · ')),
    tierBadge(g.tier, g.status), h('span', {class: 'grow'}),
    shown !== g.rows.length ? h('span', {class: 'muted small'}, '筛选后 ' + shown + ' / ' + g.rows.length) : null,
    h('button', {type: 'button', class: 'btn small', 'aria-expanded': String(open),
      onclick: () => { open ? state.open.delete(g.id) : state.open.add(g.id); draw(); }}, open ? '收起 ▴' : '展开 ' + shown + ' 行 ▾'));
}

function drawBar() {
  const a = state.pair.a && byId.get(state.pair.a), b = state.pair.b && byId.get(state.pair.b);
  const who = x => x ? (x.group.kind === 'patent' ? x.group.id + ' · ' : '') + x.short : '未选择';
  const same = a && b && a.group.id === b.group.id;
  fill(bar, h('span', {class: 'slot'}, 'A'), h('span', {class: 'who'}, who(a)), h('span', {class: 'slot b'}, 'B'), h('span', {class: 'who'}, who(b)),
    a && b ? h('span', {class: 'scope' + (same ? ' same' : '')}, same ? '同一文档 · 可给出数值比' : '跨文档 · 只并列原始值') : h('span', {class: 'muted small'}, '在表格左侧勾选两个分子'),
    h('span', {class: 'grow'}),
    h('button', {type: 'button', class: 'btn ghost-dark', disabled: !(a && b), onclick: () => { state.pair = {a: state.pair.b, b: state.pair.a}; pairStore.set(id, state.pair); draw(); }}, '交换 A/B'),
    a && b ? h('a', {class: 'btn bright', href: studyUrl(id, 'compare') + '?a=' + encodeURIComponent(a.id) + '&b=' + encodeURIComponent(b.id), id: 'to-compare'}, '查看 A/B 对照 →')
      : h('span', {class: 'btn bright', 'aria-disabled': 'true', style: 'opacity:.5'}, '查看 A/B 对照 →'));
}

function draw() {
  const out = [];
  for (const g of data.groups) {
    const rows = g.rows.filter(visible);
    out.push(band(g, rows.length));
    if (state.open.has(g.id) && rows.length) {
      const heat = heatFor(g);
      out.push(header(g), ...rows.map(r => row(r, g, heat)));
    }
  }
  if (!data.groups.length) out.push(h('p', {class: 'empty'}, '这个调研还没有文档。'));
  fill(table, ...out);
  drawBar();
}
draw();
