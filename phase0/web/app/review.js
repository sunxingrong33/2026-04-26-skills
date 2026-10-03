import {h, $, $$, api, topbar, mol, tierBadge, external, setStatus, author, fill} from '/app/core.js';

topbar([['复核队列']], 'review');
document.body.classList.add('fill');
const main = $('#main');
main.className = 'review-layout';
const state = {data: null, category: null, sort: 'document', current: null, pdf: null};

const queueBox = h('aside', {class: 'queue', 'aria-label': '待复核记录'});
const viewer = h('section', {class: 'col', 'aria-label': '原文'});
const detail = h('aside', {class: 'detail', 'aria-label': '核对与结论'});
fill(main, queueBox, viewer, detail);

async function load(keep) {
  try {
    state.data = await api('/api/review/queue');
    const counts = state.data.counts;
    if (!state.category || !counts[state.category]) state.category = Object.keys(counts).find(k => counts[k]) || 'cards';
    const items = list();
    if (!keep || !items.some(i => i.id === state.current)) state.current = items.length ? items[0].id : null;
    drawQueue(); drawItem();
  } catch (e) { fill(queueBox, h('div', {class: 'note error'}, e.message)); }
}

function list() {
  const items = state.data.items.filter(i => i.category === state.category);
  if (state.sort === 'measurements') items.sort((a, b) => b.observations.length - a.observations.length);
  if (state.sort === 'mass') items.sort((a, b) => (a.mass && a.mass.status === 'consistent' ? 1 : 0) - (b.mass && b.mass.status === 'consistent' ? 1 : 0));
  return items;
}

function itemLine(i) {
  const s = i.structure_source || {};
  const bits = [];
  if (s.pdf_page) bits.push('结构 p.' + s.pdf_page);
  if (i.activity_pages.length) bits.push('活性 p.' + i.activity_pages.join('、'));
  const measured = i.observations.filter(o => o.status === 'measured').length, missing = i.observations.length - measured;
  bits.push(measured + ' 项测量' + (missing ? ' · ' + missing + ' 项未测' : ''));
  if (i.stereo && /未指定/.test(i.stereo)) bits.push('立体未指定');
  return bits.join(' · ');
}

function drawQueue() {
  const d = state.data, items = list();
  fill(queueBox, 
    h('div', {class: 'col', style: 'gap:8px'}, h('h1', {style: 'font:700 22px var(--serif)'}, '复核队列'),
      h('span', {class: 'muted small'}, '台账中所有“待确认”记录。确认或拒绝都要具名并写理由，审计日志只追加。')),
    h('div', {class: 'qcats', role: 'group', 'aria-label': '来源类别'}, Object.entries(d.labels).map(([k, label]) =>
      h('button', {type: 'button', 'aria-pressed': String(state.category === k), disabled: !d.counts[k], dataset: {category: k},
        onclick: () => { state.category = k; state.current = (list()[0] || {}).id; drawQueue(); drawItem(); }}, label + ' · ' + d.counts[k]))),
    h('label', {class: 'row small muted'}, '排序',
      h('select', {class: 'select', style: 'width:auto;height:32px', onchange: e => { state.sort = e.target.value; drawQueue(); }},
        [['document', '按文档与编号'], ['measurements', '测量多的在前'], ['mass', '质谱需核对在前']].map(([k, v]) => h('option', {value: k, selected: state.sort === k}, v)))),
    !d.writable ? h('p', {class: 'note warn small'}, d.notice) : null,
    h('div', {class: 'qlist', id: 'queue'}, items.length ? items.map(i => h('button', {type: 'button', class: 'qitem', 'aria-current': String(i.id === state.current),
      dataset: {id: i.id}, onclick: () => { state.current = i.id; drawQueue(); drawItem(); }},
      h('b', {}, i.document_id + ' · ' + i.short.replace('Example ', 'Ex ')), h('span', {}, itemLine(i)))) : h('p', {class: 'empty'}, '这一类没有待确认记录。')),
    h('div', {class: 'muted small'}, '快捷键：', h('span', {class: 'kbd'}, 'J'), ' / ', h('span', {class: 'kbd'}, 'K'), ' 切换条目，',
      h('span', {class: 'kbd'}, 'Y'), ' 勾选下一项核对'));
}

function sources(i) {
  const out = [];
  const s = i.structure_source || {};
  if (s.url) out.push({key: 'structure', label: s.pdf_page ? '结构 · PDF p.' + s.pdf_page : '结构来源', url: s.url, pdf: !!s.pdf_page});
  const seen = new Set();
  i.observations.forEach(o => { const u = o.source && o.source.url; if (!u || seen.has(u)) return; seen.add(u);
    out.push({key: 'activity' + seen.size, label: o.source.pdf_page ? '活性表 · PDF p.' + o.source.pdf_page : '测量记录 ' + seen.size, url: u, pdf: !!o.source.pdf_page}); });
  return out.slice(0, 6);
}

function drawViewer(i) {
  const srcs = sources(i);
  // A new record keeps the same tab kind but never the previous record's page.
  const same = state.pdf && srcs.find(x => x.key === state.pdf.key && x.url === state.pdf.url);
  if (!same) state.pdf = srcs[0] ? {...srcs[0], loaded: false} : null;
  const cur = state.pdf;
  const loc = i.structure_source || {};
  const head = h('div', {class: 'sources'}, srcs.map(x => h('button', {type: 'button', class: 'btn small' + (cur && cur.key === x.key ? ' primary' : ''),
    onclick: () => { state.pdf = {...x, loaded: false}; drawViewer(i); }}, x.label)),
    h('span', {class: 'grow'}),
    h('span', {class: 'mono muted small'}, [loc.printed_page ? '印刷页 ' + loc.printed_page : null, loc.locator ? '定位 ' + loc.locator : null].filter(Boolean).join(' · ')));
  let body;
  if (!cur) body = h('div', {class: 'pdf-placeholder'}, '该记录没有可打开的原文链接。');
  else if (cur.loaded) body = h('iframe', {class: 'pdf-frame', src: cur.url, title: cur.label, referrerpolicy: 'no-referrer'});
  else body = h('div', {class: 'pdf-placeholder'},
    h('b', {}, cur.label), h('span', {class: 'small'}, cur.pdf ? '原文是公开的 PDF。加载时由你的浏览器直接从来源网站读取。' : '来源是数据库记录页面。'),
    h('span', {class: 'mono small break'}, cur.url),
    h('div', {class: 'row', style: 'justify-content:center'},
      cur.pdf ? h('button', {type: 'button', class: 'btn primary', id: 'load-pdf', onclick: () => { cur.loaded = true; drawViewer(i); }}, '在此加载原文') : null,
      external(cur.url, '新窗口打开')));
  fill(viewer, head, body);
}

function checklist(i) {
  const measured = i.observations.filter(o => o.status === 'measured');
  const qualified = measured.some(o => o.relation && o.relation !== '=');
  const items = ['连接关系与原图一致'];
  items.push(i.stereo ? '立体信息与原图一致（' + (i.stereo.length > 30 ? i.stereo.slice(0, 30) + '…' : i.stereo) + '）' : '立体信息与原图一致');
  if (i.observations.length) items.push(measured.length + ' 项测量与原表一致' + (qualified ? '，限定符保留' : '') + (i.observations.length > measured.length ? '；未测项未被填值' : ''));
  if (i.observations.length) items.push('实验列对应正确（不是相邻的其他 assay）');
  if (i.mapping_source && i.mapping_source.locator) items.push('编号对应正确：' + i.mapping_source.locator);
  return items;
}

function drawItem() {
  const i = state.data && state.data.items.find(x => x.id === state.current);
  if (!i) { fill(viewer, h('div', {class: 'pdf-placeholder'}, '没有选中的记录。')); fill(detail); return; }
  drawViewer(i);
  const writable = state.data.writable;
  const checks = checklist(i).map((t, n) => h('label', {class: 'check'}, h('input', {type: 'checkbox', class: 'review-check', dataset: {n}}), h('span', {}, t)));
  const reviewer = h('input', {class: 'input', id: 'reviewer', maxlength: 40, placeholder: '你的姓名', value: author.get()});
  const note = h('input', {class: 'input', id: 'review-note', maxlength: 600,
    placeholder: '例如：与 PDF p.' + ((i.structure_source || {}).pdf_page || '?') + ' 结构图及活性表逐项一致'});
  const confirm = h('button', {type: 'button', class: 'btn primary', id: 'confirm', disabled: true}, '勾选并填写后可确认');
  const reject = h('button', {type: 'button', class: 'btn danger', id: 'reject', disabled: !writable}, '拒绝（退出分析，保留审计）');
  const msg = h('p', {class: 'status', role: 'status', id: 'review-status'});
  const sync = () => {
    const ready = writable && $$('.review-check', detail).every(c => c.checked) && reviewer.value.trim() && note.value.trim();
    confirm.disabled = !ready;
    confirm.textContent = ready ? '确认（L3）' : '勾选并填写后可确认';
  };
  detail.oninput = sync; detail.onchange = sync;
  const submit = status => async () => {
    try {
      if (!reviewer.value.trim() || !note.value.trim()) throw new Error('请填写复核人和理由。');
      author.set(reviewer.value.trim());
      confirm.disabled = reject.disabled = true;
      const r = await api('/api/review', {id: i.id, status, reviewer: reviewer.value, note: note.value});
      const done = status === 'confirmed' ? '已确认 ' + i.short + (r.observations ? '（含 ' + r.observations + ' 项测量）' : '') : '已拒绝 ' + i.short + '，退出分析，记录保留';
      const order = list().map(x => x.id), at = order.indexOf(i.id);
      state.current = order[at + 1] || order[at - 1] || null;
      await load(true);
      setStatus($('#review-status') || msg, done + '。');
    } catch (e) { setStatus(msg, e.message, true); sync(); reject.disabled = !writable; }
  };
  confirm.onclick = submit('confirmed');
  reject.onclick = submit('rejected');
  const m = i.mass;
  fill(detail, 
    h('div', {class: 'col', style: 'gap:6px'}, h('b', {class: 'mono', style: 'font-size:15px'}, i.document_id + ' · ' + i.short),
      h('div', {class: 'row', style: 'gap:6px'}, tierBadge(i.tier, 'proposed'), h('span', {class: 'badge'}, i.label.length > 40 ? i.label.slice(0, 40) + '…' : i.label))),
    h('div', {class: 'row', style: 'align-items:flex-start;flex-wrap:nowrap'}, mol(i.smiles, 'l', '转录结构 ' + i.short),
    ),
    h('div', {class: 'col small', style: 'gap:4px'},
      h('span', {class: 'muted'}, '转录 SMILES 重绘，与左侧原图并排核对'),
      i.stereo ? h('span', {}, '立体：' + i.stereo) : null,
      m ? h('span', {class: m.status === 'consistent' ? '' : 'outcome unfavorable', dataset: {massStatus: m.status}}, m.summary.text) : h('span', {class: 'muted'}, '原文没有质谱值，未做质量校验'),
      m ? h('span', {class: 'muted'}, '质谱只能发现原子数错误，查不出同分异构与立体错误') : null),
    h('div', {class: 'smiles'}, i.smiles),
    h('div', {class: 'checklist'}, h('b', {class: 'small'}, '逐项核对'), checks),
    i.observations.length ? h('div', {class: 'kv'}, i.observations.map(o => [h('span', {class: 'k'}, o.assay),
      h('span', {class: 'v'}, o.status === 'measured' ? o.text + (o.unit ? ' ' + o.unit : '') : o.text)]).flat()) : h('p', {class: 'muted small'}, '这条记录没有测量。'),
    h('div', {class: 'grid-2', style: 'gap:12px'}, h('label', {class: 'field'}, h('span', {}, '复核人（具名）'), reviewer),
      h('label', {class: 'field'}, h('span', {}, '理由（必填）'), note)),
    h('div', {class: 'review-actions'}, h('button', {type: 'button', class: 'btn', id: 'skip', onclick: () => step(1)}, '跳过'), reject, confirm),
    msg);
}

function step(d) {
  const items = list(); if (!items.length) return;
  const at = items.findIndex(x => x.id === state.current);
  state.current = items[(at + d + items.length) % items.length].id;
  drawQueue(); drawItem();
  const btn = $(`.qitem[data-id="${CSS.escape(state.current)}"]`); if (btn) btn.scrollIntoView({block: 'nearest'});
}

document.addEventListener('keydown', e => {
  if (e.target.closest('input, textarea, select') || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === 'j' || e.key === 'J') step(1);
  else if (e.key === 'k' || e.key === 'K') step(-1);
  else if (e.key === 'y' || e.key === 'Y') {
    const next = $$('.review-check', detail).find(c => !c.checked);
    if (next) { next.checked = true; next.dispatchEvent(new Event('change', {bubbles: true})); }
  }
});

load(false);
