import {h, $, api, loadStudy, external, tierBadge, pairStore, setStatus, author, studyUrl , fill} from '/app/core.js';

const {id, main} = await loadStudy('compare', 'overview');
const params = new URLSearchParams(location.search);
const remembered = pairStore.get(id);
let pair = {a: params.get('a') || remembered.a || null, b: params.get('b') || remembered.b || null};

const status = h('p', {class: 'status', role: 'status', id: 'status'}, '正在读取证据…');
fill(main, status);
let options = [];
try {
  const ev = await api('/api/study?id=' + encodeURIComponent(id) + '&view=evidence');
  options = ev.groups.flatMap(g => g.rows.filter(r => r.status !== 'rejected').map(r => ({id: r.id, doc: g.kind === 'patent' ? g.id : g.title, label: r.short})));
  draw();
} catch (e) { setStatus(status, e.message, true); }

function picker() {
  const sel = (slot) => h('select', {class: 'select', id: 'pick-' + slot, 'aria-label': '分子 ' + slot.toUpperCase()},
    h('option', {value: ''}, '选择分子 ' + slot.toUpperCase()),
    options.map(o => h('option', {value: o.id, selected: pair[slot] === o.id}, o.doc + ' · ' + o.label)));
  const a = sel('a'), b = sel('b');
  return h('div', {class: 'card tight picker'},
    h('label', {class: 'field'}, h('span', {}, 'A'), a), h('label', {class: 'field'}, h('span', {}, 'B'), b),
    h('div', {class: 'row'},
      h('button', {class: 'btn', type: 'button', onclick: () => { [a.value, b.value] = [b.value, a.value]; } }, '交换'),
      h('button', {class: 'btn primary', type: 'button', id: 'run-compare', onclick: () => {
        pair = {a: a.value || null, b: b.value || null}; pairStore.set(id, pair);
        history.replaceState(null, '', studyUrl(id, 'compare') + (pair.a && pair.b ? '?a=' + encodeURIComponent(pair.a) + '&b=' + encodeURIComponent(pair.b) : ''));
        draw();
      }}, '对照')));
}

async function draw() {
  const pick = picker();
  if (!pair.a || !pair.b) {
    fill(main, pick, h('div', {class: 'card empty'}, '选择两个不同的分子；也可以在“证据”页勾选 A 与 B。'));
    return;
  }
  const st = h('p', {class: 'status', role: 'status'}, '正在计算最大公共子结构与性质差异…');
  fill(main, pick, st);
  try {
    const d = await api('/api/study/compare', {a: pair.a, b: pair.b});
    fill(main, pick, ...render(d));
  } catch (e) { setStatus(st, e.message, true); }
}

function pane(slot, side, svg) {
  const doc = side.document, src = side.structure_source || {};
  return h('div', {class: 'card ab-pane', dataset: {slot}},
    h('div', {class: 'top'}, h('span', {class: 'slot' + (slot === 'B' ? ' b' : '')}, slot),
      h('span', {class: 'who'}, (doc.kind === 'patent' ? doc.id : doc.title) + ' · ' + side.short), tierBadge(side.tier, side.status),
      h('span', {class: 'grow'}), h('span', {class: 'mono muted small'}, doc.date || '')),
    h('img', {class: 'mol l framed', src: svg, alt: side.short + ' 结构，差异原子高亮'}),
    h('div', {class: 'links'}, external(src.url, src.pdf_page ? '结构：PDF p.' + src.pdf_page : '结构来源'),
      side.stereo ? h('span', {class: 'muted', title: side.stereo}, side.stereo.length > 40 ? side.stereo.slice(0, 40) + '…' : side.stereo) : null));
}

function rawText(v) { return v ? (v.text + (v.unit ? ' ' + v.unit : '')) : '—'; }

function activity(d) {
  if (d.same_document) {
    const rows = d.measurements.filter(m => m.a.length || m.b.length);
    return h('div', {class: 'card'}, h('h2', {style: 'margin-bottom:12px'}, '活性 · 同一文档内比较'),
      h('div', {class: 'table-wrap'}, h('table', {class: 'data', id: 'activity'},
        h('tr', {}, h('th', {}, '实验'), h('th', {}, 'A'), h('th', {}, 'B'), h('th', {}, '比较')),
        rows.map(m => h('tr', {dataset: {assay: m.assay_id}},
          h('td', {title: m.protocol || ''}, (m.endpoint || '') + ' · ' + m.assay_id),
          h('td', {class: 'mono'}, m.a.map(x => x.text + (x.unit ? ' ' + x.unit : '')).join(' / ') || '—'),
          h('td', {class: 'mono'}, m.b.map(x => x.text + (x.unit ? ' ' + x.unit : '')).join(' / ') || '—'),
          h('td', {}, m.ratio !== null ? h('span', {}, h('b', {class: 'mono'}, 'B/A ' + Number(m.ratio).toPrecision(3)), h('span', {class: 'muted small'}, '（数值比，非改善倍数）'))
            : h('span', {class: 'muted small'}, m.reasons.join(' '))))))));
  }
  return h('div', {class: 'card'}, h('h2', {style: 'margin-bottom:12px'}, '活性 · 原始值并列'),
    h('div', {class: 'table-wrap'}, h('table', {class: 'data', id: 'activity'},
      h('tr', {}, h('th', {}, '实验'), h('th', {}, 'A'), h('th', {}, 'B'), h('th', {}, '比较')),
      d.parallel.map(p => h('tr', {}, h('td', {}, p.assay), h('td', {class: 'mono'}, rawText(p.a)), h('td', {class: 'mono'}, rawText(p.b)),
        h('td', {class: 'muted small'}, !p.a || !p.b ? '只有一方有记录' : (p.a.qualified || p.b.qualified) ? (p.a.qualified ? 'A' : 'B') + ' 为限定值，不比较' : '跨文档，只并列'))))),
    h('p', {class: 'muted small', style: 'margin:12px 0 0'}, '实验按名称并列，不代表协议或批次已确认可比；不计算 B/A。'));
}

const DECIMALS = {mw: 1, tpsa: 1, clogp: 2};
function fmt(key, v) { return key in DECIMALS ? v.toFixed(DECIMALS[key]) : String(Math.round(v)); }

function props(d) {
  return h('div', {class: 'card'}, h('h2', {style: 'margin-bottom:12px'}, '计算性质 · RDKit'),
    h('table', {class: 'data', id: 'properties'}, h('tr', {}, h('th', {}, '性质'), h('th', {class: 'num'}, 'A'), h('th', {class: 'num'}, 'B'), h('th', {class: 'num'}, 'Δ (B − A)')),
      d.properties.map(p => h('tr', {}, h('td', {}, p.label), h('td', {class: 'num'}, fmt(p.key, p.a)), h('td', {class: 'num'}, fmt(p.key, p.b)),
        h('td', {class: 'num'}, p.delta === 0 ? '0' : (p.delta > 0 ? '+' : '−') + fmt(p.key, Math.abs(p.delta)))))));
}

function judgement(d) {
  const text = h('textarea', {class: 'textarea', rows: 2, id: 'judgement', maxlength: 600, placeholder: '例如：大环化降低了可旋转键与 cLogP，可能与构象限制有关（待验证）'});
  const name = h('input', {class: 'input', id: 'judge-author', maxlength: 40, placeholder: '你的姓名', value: author.get(), style: 'width:200px'});
  const msg = h('p', {class: 'status', role: 'status'});
  const ctx = '对照 A ' + d.a.document_id + ' ' + d.a.short + ' / B ' + d.b.document_id + ' ' + d.b.short;
  const save = kind => async () => {
    try {
      if (!name.value.trim()) throw new Error('请填写署名。');
      author.set(name.value.trim());
      await api('/api/study/note', {id, kind, text: text.value, author: name.value, context: ctx});
      setStatus(msg, '已署名并加入报告（标为“' + (kind === 'gap' ? '证据缺口' : '研究假设') + '”）。');
      text.value = '';
    } catch (e) { setStatus(msg, e.message, true); }
  };
  return h('section', {class: 'card col'},
    h('div', {class: 'card-head', style: 'margin:0'}, h('h2', {}, '我的判断'), h('span', {class: 'hint'}, '写入报告时标为“研究假设”，并附你的署名与本对照的出处')),
    h('label', {class: 'field'}, h('span', {}, '结论或假设'), text),
    h('div', {class: 'row'}, name, h('span', {class: 'grow'}),
      h('button', {class: 'btn', type: 'button', id: 'mark-gap', onclick: save('gap')}, '标记为证据缺口'),
      h('button', {class: 'btn primary', type: 'button', id: 'sign-hypothesis', onclick: save('hypothesis')}, '署名并加入报告')),
    msg);
}

function render(d) {
  return [
    h('div', {class: 'note ' + (d.same_document ? 'ok' : 'warn'), id: 'scope-note'}, d.notice),
    h('section', {class: 'grid-2'}, pane('A', d.a, d.a_svg), pane('B', d.b, d.b_svg)),
    h('div', {class: 'mcs-note'}, h('span', {class: 'sw'}), h('span', {}, d.mcs_note + (d.mcs_timed_out ? ' 最大公共子结构搜索超时，高亮可能不完整。' : ''))),
    h('section', {class: 'grid-2'}, activity(d), props(d)),
    judgement(d)];
}
