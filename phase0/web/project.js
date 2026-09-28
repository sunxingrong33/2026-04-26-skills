/* Project goal page. All source-provided strings go through textContent. */
const $ = id => document.getElementById(id);
const OUTCOME = {favorable: '有利', unfavorable: '不利', unchanged: '未变', changed: '有变化（仅参考）', mixed: '各实验不一致',
  not_comparable: '不可比', missing: '缺失', no_assay: '本文档无该实验'};
const DIRECTION = {lower: '越低越好', higher: '越高越好', range: '目标区间', none: '仅参考'};
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

function thresholdInput(t) {
  const wrap = el('span', undefined, 'threshold-box');
  const kind = el('select'); kind.className = 'threshold-kind';
  [['fold', '变化倍数 ≥'], ['delta', '差值 ≥']].forEach(([k, v]) => { const o = el('option', v); o.value = k; o.selected = k === t.kind; kind.append(o); });
  const input = el('input'); input.type = 'number'; input.step = '0.1'; input.value = t.value; input.style.width = '80px'; input.className = 'threshold';
  const badge = el('span', t.source === 'user' ? '已由用户设定' : '默认值，待化学家确认', t.source === 'user' ? 'badge user' : 'badge');
  if (t.source === 'user') input.dataset.user = '1';
  const mark = () => { badge.textContent = '已由用户设定'; badge.className = 'badge user'; input.dataset.user = '1'; };
  input.oninput = mark; kind.onchange = mark;
  wrap.append(kind, input, badge);
  return wrap;
}

function rangeInputs(unit) {
  const wrap = el('span', undefined, 'range-box'); wrap.hidden = true;
  const low = el('input'); low.type = 'number'; low.step = 'any'; low.className = 'range-low'; low.placeholder = '下限'; low.style.width = '90px';
  const high = el('input'); high.type = 'number'; high.step = 'any'; high.className = 'range-high'; high.placeholder = '上限'; high.style.width = '90px';
  const u = el('input'); u.className = 'range-unit'; u.value = unit || ''; u.placeholder = '单位（无则留空）'; u.style.width = '120px';
  wrap.append(el('span', '目标区间 '), low, el('span', ' – '), high, el('span', ' 单位 '), u);
  return wrap;
}

function assayRow(a, checked) {
  const row = el('label', undefined, 'assay'); const box = el('input'); box.type = 'checkbox'; box.value = a.assay_id; box.checked = checked;
  box.dataset.unit = a.unit || '';
  const text = el('div'); text.append(el('strong', a.assay_id + ' · ' + a.endpoint), el('span', '  ' + a.measured + ' 条测量' + (a.unit ? ' · 单位 ' + a.unit : ''), 'muted'), el('br'), el('span', a.description || '实验描述缺失', 'muted'));
  if (a.reason) text.append(el('br'), el('span', '建议依据：' + a.reason, 'muted'));
  row.append(box, text); return row;
}

function assayPicker(pool, title, filter) {
  const box = el('details'); box.append(el('summary', title));
  if (filter) {
    const f = el('input'); f.placeholder = '按编号、终点或描述筛选'; f.style.width = '260px';
    f.oninput = () => box.querySelectorAll('.assay').forEach(r => { r.hidden = !r.textContent.toLowerCase().includes(f.value.toLowerCase()); });
    box.append(f);
  }
  pool.forEach(a => box.append(assayRow(a, false)));
  return box;
}

let customCount = 0;
function propertyCard(p, custom) {
  const card = el('div', undefined, 'prop'); card.dataset.id = p.id; card.dataset.label = p.label;
  const head = el('div', undefined, 'row');
  if (custom) {
    const name = el('input'); name.className = 'prop-name'; name.value = p.label; name.maxLength = 40; name.style.width = '160px';
    name.oninput = () => { card.dataset.label = name.value.trim() || p.label; };
    head.append(el('span', '自定义性质'), name);
  } else head.append(el('h3', p.label));
  const dir = el('select'); dir.className = 'direction';
  Object.entries(DIRECTION).forEach(([k, v]) => { const o = el('option', v); o.value = k; o.selected = k === p.direction; dir.append(o); });
  const firstUnit = (p.suggested[0] || {}).unit;
  const range = rangeInputs(firstUnit);
  dir.onchange = () => { range.hidden = dir.value !== 'range'; };
  const remove = el('button', '移除此性质'); remove.type = 'button'; remove.onclick = () => card.remove();
  head.append(dir, thresholdInput(p.threshold), remove); card.append(head, range);
  if (!p.suggested.length && !custom) card.append(el('p', '范围内没有建议的实验；可从下方添加。', 'muted'));
  p.suggested.forEach(a => card.append(assayRow(a, true)));
  card.append(custom ? assayPicker(suggestion.assays, '选择实验（范围内全部实验）', true)
                     : assayPicker(suggestion.unmapped, '从未归入的实验中添加', false));
  return card;
}

function drawMapping(s) {
  suggestion = s;
  const box = $('properties'); box.replaceChildren();
  s.properties.forEach(p => box.append(propertyCard(p, false)));
  const un = $('unmapped'); un.replaceChildren(el('p', s.unmapped.length + ' 个实验未归入任何性质，不参与分析。', 'muted'));
  s.unmapped.forEach(a => un.append(el('p', a.assay_id + ' · ' + a.endpoint + ' · ' + (a.description || ''), 'muted')));
  $('mapping').hidden = false; $('results').hidden = true;
}

$('add-property').onclick = () => {
  if (!suggestion) return;
  customCount++;
  $('properties').append(propertyCard({id: 'custom_' + customCount, label: '自定义性质 ' + customCount, direction: 'lower',
    suggested: [], threshold: {kind: 'fold', value: 2, source: 'default_pending_chemist_review'}}, true));
};

function numberOrNull(input) { return input.value.trim() === '' ? null : Number(input.value); }

function goal() {
  const props = [...document.querySelectorAll('.prop')].map(card => {
    const t = card.querySelector('.threshold'), direction = card.querySelector('.direction').value;
    const p = {id: card.dataset.id, label: card.dataset.label, direction,
      assay_ids: [...new Set([...card.querySelectorAll('.assay input:checked')].map(i => i.value))],
      threshold: {kind: card.querySelector('.threshold-kind').value, value: Number(t.value), source: t.dataset.user ? 'user' : 'default_pending_chemist_review'}};
    if (direction === 'range') p.range = {low: numberOrNull(card.querySelector('.range-low')), high: numberOrNull(card.querySelector('.range-high')),
      unit: card.querySelector('.range-unit').value.trim() || null};
    return p;
  }).filter(p => p.assay_ids.length);
  if (!props.length) throw new Error('请至少为一个性质勾选实验。');
  return {label: suggestion.label, properties: props};
}

function valueText(x) {
  if (x.status !== 'comparable') return x.note || OUTCOME[x.status];
  const unit = x.a.unit || '';
  const change = x.ratio_b_over_a !== undefined ? 'B/A = ' + x.ratio_b_over_a : 'B−A = ' + x.delta_b_minus_a;
  const range = x.a_in_range === undefined ? '' : '；目标区间内：A ' + (x.a_in_range ? '是' : '否') + ' / B ' + (x.b_in_range ? '是' : '否');
  return x.a.value + ' → ' + x.b.value + (unit ? ' ' + unit : '') + '；' + change + range;
}

function ruleText(p) {
  const t = p.threshold.kind === 'fold' ? '≥' + p.threshold.value + ' 倍' : '差值 ≥' + p.threshold.value;
  if (p.direction !== 'range') return DIRECTION[p.direction] + '；' + t;
  const r = p.range, u = r.unit ? ' ' + r.unit : '';
  const span = r.low !== null && r.high !== null ? r.low + '–' + r.high : r.low !== null ? '≥ ' + r.low : '≤ ' + r.high;
  return '目标区间 ' + span + u + '；' + t;
}

let lastResult = null;
function drawResults(r) {
  lastResult = r;
  $('result-head').replaceChildren(el('p', r.notice, 'note'),
    el('p', '范围：' + (Array.isArray(r.scope.documents) ? r.scope.documents.join('、') : r.scope.documents) + ' · ' + r.scope.compounds + ' 个化合物 · 可变部分 ≤ ' + r.scope.max_change_heavy_atoms + ' 个重原子 · ' + r.pair_count + ' 个分子对 · ' + r.transforms.length + ' 种替换 · ' + r.sites.length + ' 个位点'));
  const rules = $('rules'); rules.replaceChildren(); r.grade_rules.forEach(g => rules.append(el('p', g.label + '：' + g.rule)));
  drawGroups();
  $('results').hidden = false;
}

function drawGroups() {
  const r = lastResult; if (!r) return;
  const bySite = $('group-by').value === 'site';
  const groups = bySite ? r.sites : r.transforms, props = r.goal.properties, box = $('transforms'); box.replaceChildren();
  if (bySite) box.append(el('p', '位点 = 连接点在不变部分中 ' + r.site_radius + ' 个键以内的化学环境。环境相同即视为同一位置；对称或重复的环境可能把远处不同的位置合在一起，请核对每个分子对的完整不变部分。同一位点下汇总的是不同的替换，“结论矛盾”通常说明不同替换效果不同，而不是数据互相冲突；请展开明细或切回“按替换”查看。', 'muted'));
  if (!groups.length) box.append(el('p', '范围内没有找到分子对。可放宽可变部分的上限或扩大文档范围。'));
  groups.forEach(t => {
    const card = el('article', undefined, 'tf');
    if (bySite) {
      card.dataset.site = t.site;
      card.append(el('h3', '位点 '), el('code', t.site), el('p', t.pairs.length + ' 个分子对 · 替换 ' + t.transforms.length + ' 种 · 来源 ' + t.documents.join('、'), 'muted'));
      const list = el('p', undefined, 'muted'); list.append(el('span', '替换：')); t.transforms.forEach((x, i) => { if (i) list.append(el('span', '；')); list.append(el('code', x)); }); card.append(list);
    } else {
      card.dataset.transform = t.transform;
      card.append(el('h3', '替换 '), el('code', t.transform), el('p', t.pairs.length + ' 个分子对 · 不变部分 ' + t.sites.length + ' 种 · 来源 ' + t.documents.join('、'), 'muted'));
    }
    const wrap = el('div', undefined, 'wrap'), table = el('table'), head = el('tr');
    ['性质', '方向与阈值', ...COLUMNS.map(c => OUTCOME[c]), '证据等级'].forEach(h => head.append(el('th', h)));
    table.append(head);
    props.forEach(p => {
      const s = t.summary[p.id], tr = el('tr'); tr.dataset.property = p.id;
      tr.append(el('td', p.label), el('td', ruleText(p)));
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
      block.append(el('strong', 'A ' + pr.a_label), el('br'), el('strong', 'B ' + pr.b_label),
        el('p', pr.document + ' · 替换 ' + pr.transform + ' · 不变部分 ' + pr.key, 'muted'));
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
}
$('group-by').onchange = drawGroups;

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
