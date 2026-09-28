/* Project goal page. All source-provided strings go through textContent. */
const $ = id => document.getElementById(id);
const OUTCOME = {favorable: '有利', unfavorable: '不利', unchanged: '未变', changed: '有变化（仅参考）', mixed: '各实验不一致',
  not_comparable: '不可比', missing: '缺失', no_assay: '本文档无该实验'};
const DIRECTION = {lower: '越低越好', higher: '越高越好', none: '仅参考'};
const COLUMNS = ['favorable', 'unfavorable', 'unchanged', 'changed', 'mixed', 'not_comparable', 'missing', 'no_assay'];
let busy = false, suggestion = null;

function el(tag, text, cls) { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; }
function chip(kind, text) { return el('span', text ?? OUTCOME[kind] ?? kind, 'chip ' + kind); }
function status(text, error) { $('status').textContent = text; $('status').className = error ? 'error' : 'muted'; }

async function call(body) {
  const r = await fetch('/api/project-sar', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || '请求失败');
  return d;
}

function scope() {
  const picked = [...document.querySelectorAll('#documents input:checked')].map(i => i.value);
  if (!picked.length) throw new Error('请至少选择一个文档。');
  return picked;
}

async function init() {
  try {
    const d = await call({mode: 'templates'});
    d.templates.forEach(t => { const o = el('option', t.label + '（' + t.properties.join('、') + '）'); o.value = t.id; $('template').append(o); });
    $('documents').append(el('span', '范围：', 'muted'));
    d.documents.forEach(doc => { const l = el('label'); const i = el('input'); i.type = 'checkbox'; i.value = doc; i.checked = true; l.append(i, document.createTextNode(' ' + doc)); $('documents').append(l); });
  } catch (e) { status(e.message, true); }
}

function thresholdInput(p) {
  const wrap = el('span');
  const t = p.threshold;
  const input = el('input'); input.type = 'number'; input.step = '0.1'; input.value = t.value; input.style.width = '80px';
  input.min = t.kind === 'fold' ? '1.1' : '0.1'; input.dataset.kind = t.kind; input.className = 'threshold';
  const badge = el('span', '默认值，待化学家确认', 'badge');
  input.oninput = () => { badge.textContent = '已由用户设定'; badge.className = 'badge user'; input.dataset.user = '1'; };
  wrap.append(el('span', t.kind === 'fold' ? '变化倍数 ≥ ' : '差值 ≥ '), input, badge);
  return wrap;
}

function assayRow(a, checked) {
  const row = el('label', undefined, 'assay'); const box = el('input'); box.type = 'checkbox'; box.value = a.assay_id; box.checked = checked;
  const text = el('div'); text.append(el('strong', a.assay_id + ' · ' + a.endpoint), el('span', '  ' + a.measured + ' 条测量', 'muted'), el('br'), el('span', a.description || '实验描述缺失', 'muted'));
  if (a.reason) text.append(el('br'), el('span', '建议依据：' + a.reason, 'muted'));
  row.append(box, text); return row;
}

function drawMapping(s) {
  suggestion = s;
  const box = $('properties'); box.replaceChildren();
  s.properties.forEach(p => {
    const card = el('div', undefined, 'prop'); card.dataset.id = p.id; card.dataset.label = p.label;
    const head = el('div', undefined, 'row'); head.append(el('h3', p.label));
    const dir = el('select'); dir.className = 'direction'; Object.entries(DIRECTION).forEach(([k, v]) => { const o = el('option', v); o.value = k; o.selected = k === p.direction; dir.append(o); });
    head.append(dir, thresholdInput(p)); card.append(head);
    if (!p.suggested.length) card.append(el('p', '范围内没有建议的实验；可从下方未归入的实验中添加。', 'muted'));
    p.suggested.forEach(a => card.append(assayRow(a, true)));
    const extra = el('details'); extra.append(el('summary', '从未归入的实验中添加'));
    s.unmapped.forEach(a => extra.append(assayRow(a, false)));
    card.append(extra); box.append(card);
  });
  const un = $('unmapped'); un.replaceChildren(el('p', s.unmapped.length + ' 个实验未归入任何性质，不参与分析。', 'muted'));
  s.unmapped.forEach(a => un.append(el('p', a.assay_id + ' · ' + a.endpoint + ' · ' + (a.description || ''), 'muted')));
  $('mapping').hidden = false; $('results').hidden = true;
}

function goal() {
  const props = [...document.querySelectorAll('.prop')].map(card => {
    const t = card.querySelector('.threshold');
    return {id: card.dataset.id, label: card.dataset.label, direction: card.querySelector('.direction').value,
      assay_ids: [...new Set([...card.querySelectorAll('.assay input:checked')].map(i => i.value))],
      threshold: {kind: t.dataset.kind, value: Number(t.value), source: t.dataset.user ? 'user' : 'default_pending_chemist_review'}};
  }).filter(p => p.assay_ids.length);
  return {label: suggestion.label, properties: props};
}

function valueText(x) {
  if (x.status !== 'comparable') return x.note || OUTCOME[x.status];
  const unit = x.a.unit || '';
  const change = x.ratio_b_over_a !== undefined ? 'B/A = ' + x.ratio_b_over_a : 'B−A = ' + x.delta_b_minus_a;
  return x.a.value + ' → ' + x.b.value + (unit ? ' ' + unit : '') + '；' + change;
}

function drawResults(r) {
  $('result-head').replaceChildren(el('p', r.notice, 'note'),
    el('p', '范围：' + (Array.isArray(r.scope.documents) ? r.scope.documents.join('、') : r.scope.documents) + ' · ' + r.scope.compounds + ' 个化合物 · 可变部分 ≤ ' + r.scope.max_change_heavy_atoms + ' 个重原子 · ' + r.pair_count + ' 个分子对 · ' + r.transforms.length + ' 种替换'));
  const rules = $('rules'); rules.replaceChildren(); r.grade_rules.forEach(g => rules.append(el('p', g.label + '：' + g.rule)));
  const props = r.goal.properties, box = $('transforms'); box.replaceChildren();
  if (!r.transforms.length) box.append(el('p', '范围内没有找到分子对。可放宽可变部分的上限或扩大文档范围。'));
  r.transforms.forEach(t => {
    const card = el('article', undefined, 'tf'); card.dataset.transform = t.transform;
    card.append(el('h3', '替换 '), el('code', t.transform), el('p', t.pairs.length + ' 个分子对 · 位点（不变部分）' + t.sites.length + ' 种 · 来源 ' + t.documents.join('、'), 'muted'));
    const wrap = el('div', undefined, 'wrap'), table = el('table'), head = el('tr');
    ['性质', '方向与阈值', ...COLUMNS.map(c => OUTCOME[c]), '证据等级'].forEach(h => head.append(el('th', h)));
    table.append(head);
    props.forEach(p => {
      const s = t.summary[p.id], tr = el('tr'); tr.dataset.property = p.id;
      tr.append(el('td', p.label), el('td', DIRECTION[p.direction] + '；' + (p.threshold.kind === 'fold' ? '≥' + p.threshold.value + ' 倍' : '差值 ≥' + p.threshold.value)));
      COLUMNS.forEach(c => { const td = el('td'); if (s.counts[c]) td.append(chip(c, String(s.counts[c]))); tr.append(td); });
      const g = el('td'); g.append(chip(s.grade, s.grade_label)); tr.append(g); table.append(tr);
    });
    wrap.append(table); card.append(wrap);
    if (t.gaps.length) {
      const gaps = el('div'); gaps.append(el('p', '目标性质缺失（可作为补测候选）：', 'muted'));
      t.gaps.forEach(g => gaps.append(el('p', '· ' + g.pair + ' — ' + g.property + '：' + g.lacking.join('、') + ' 无记录', 'muted')));
      card.append(gaps);
    }
    const det = el('details'); det.append(el('summary', '分子对明细'));
    t.pairs.forEach(pr => {
      const block = el('div', undefined, 'prop'); block.dataset.pair = pr.a_label.split(' ·')[0] + '>' + pr.b_label.split(' ·')[0];
      block.append(el('strong', 'A ' + pr.a_label), el('br'), el('strong', 'B ' + pr.b_label), el('p', pr.document + ' · 不变部分 ' + pr.key, 'muted'));
      props.forEach(p => {
        const cell = pr.properties[p.id], line = el('div'); line.dataset.property = p.id;
        line.append(el('span', p.label + '：'), chip(cell.result));
        let neither = 0;
        Object.entries(cell.assays).forEach(([aid, x]) => {
          if (x.lacking === 'A、B 均') { neither++; return; }
          line.append(el('div', aid + ' — ' + valueText(x) + (x.outcome ? '（' + OUTCOME[x.outcome] + '）' : ''), 'muted'));
        });
        if (neither) line.append(el('div', '另有 ' + neither + ' 个实验两者均无记录', 'muted'));
        block.append(line);
      });
      det.append(block);
    });
    card.append(det); box.append(card);
  });
  $('results').hidden = false;
}

async function run(button, body, draw, message) {
  if (busy) return; busy = true; button.disabled = true; status(message);
  try { draw(await call(body)); status('完成。'); } catch (e) { status(e.message, true); }
  finally { busy = false; button.disabled = false; }
}

$('suggest').onclick = () => guard(() => run($('suggest'), {mode: 'suggest', template: $('template').value, focus: $('focus').value || null, documents: scope()},
  drawMapping, '正在生成映射建议…'));
$('analyse').onclick = () => guard(() => run($('analyse'), {mode: 'analyse', goal: goal(), documents: scope(), max_change: Number($('max-change').value)},
  drawResults, '正在寻找分子对并比较…'));
function guard(fn) { try { return fn(); } catch (e) { status(e.message, true); } }
init();
