import {h, $, $$, api, loadStudy, mol, download, setStatus, author, studyUrl, analysisRequest, fill} from '/app/core.js';

const OUTCOME = {favorable: '有利', unfavorable: '不利', unchanged: '未变', changed: '有变化（仅参考）', mixed: '各实验不一致',
  not_comparable: '不可比', missing: '缺失', no_assay: '本文档无该实验'};
const DIRECTION = {lower: '越低越好', higher: '越高越好', range: '目标区间', none: '仅参考'};
const COLS = [['favorable', '有利'], ['unfavorable', '不利'], ['unchanged', '未变'], ['missing', '缺失'], ['no_assay', '本文档无该实验']];
const OTHER = ['changed', 'mixed', 'not_comparable'];

const {id, data, main} = await loadStudy('analysis', 'overview', () => [
  h('button', {class: 'btn', type: 'button', id: 'export-md', onclick: exportMarkdown}, '导出讨论材料')]);
const study = data.study;
let request = null, suggestion = null, result = null, groupBy = 'transform', saved = null, compounds = new Map();

if (!study.documents.length) {
  fill(main, h('div', {class: 'card'}, h('h2', {}, '这个调研还没有文档'), h('p', {class: 'muted'}, '加入专利或论文后才能自动配对分析。')));
} else init();

async function init() {
  const status = h('p', {class: 'status', role: 'status', id: 'status'}, '正在读取实验映射与分子对…');
  fill(main, status);
  try {
    // One at a time: the server runs one analysis request at a time and answers 429 to the rest.
    const templates = await api('/api/project-sar', {mode: 'templates'});
    const base = await analysisRequest(study);
    const ev = await api('/api/study?id=' + encodeURIComponent(id) + '&view=evidence');
    ev.groups.forEach(g => g.rows.forEach(r => compounds.set(r.id, r)));
    request = base.request; saved = base.saved;
    suggestion = base.suggestion || await api('/api/project-sar', {mode: 'suggest', template: study.goal_template || 'cell_potency_efflux',
      documents: study.documents, focus: study.target ? study.target.label : null});
    layout(templates.templates);
    await analyse(false);
  } catch (e) { setStatus(status, e.message, true); }
}

/* ---------- left column: goal and mapping */
function layout(templates) {
  const tpl = h('select', {class: 'select', id: 'template', 'aria-label': '目标模板'},
    templates.map(t => h('option', {value: t.id, selected: t.id === suggestion.template}, t.label)));
  tpl.onchange = async () => {
    suggestion = await api('/api/project-sar', {mode: 'suggest', template: tpl.value, documents: study.documents, focus: study.target ? study.target.label : null});
    request = {...request, goal: {label: suggestion.label, properties: suggestion.properties.filter(p => p.suggested.length).map(p => ({
      id: p.id, label: p.label, direction: p.direction, assay_ids: p.suggested.map(a => a.assay_id), threshold: p.threshold}))}};
    saved = null; drawMapping(); analyse(false);
  };
  const lead = request.lead || {};
  const leadInput = h('input', {class: 'input mono', id: 'lead-smiles', placeholder: '先导结构 SMILES（可选）', value: lead.smiles || '', maxlength: 2000});
  const leadLabel = h('input', {class: 'input', id: 'lead-label', placeholder: '名称，例如 6e', value: lead.label || '', maxlength: 40});
  const keep = (request.constraints && request.constraints.keep && request.constraints.keep[0]) || {};
  const keepInput = h('input', {class: 'input mono', id: 'keep', placeholder: '例如 2-氨基吡啶：Nc1ncccc1', value: keep.pattern || '', maxlength: 200});
  const notes = h('input', {class: 'input', id: 'synthesis', placeholder: '合成限制，分号分隔（只作标签）', maxlength: 400,
    value: ((request.constraints && request.constraints.synthesis_notes) || []).join('；')});
  const maxc = h('input', {class: 'input', type: 'number', id: 'max-change', min: 1, max: 20, value: request.max_change || 12, style: 'width:80px'});
  const docs = h('div', {class: 'col', style: 'gap:4px'}, study.documents.map(d => h('label', {class: 'check small'},
    h('input', {type: 'checkbox', value: d, checked: !request.documents || request.documents.includes(d), class: 'scope-doc'}), h('span', {class: 'mono'}, d))));

  const aside = h('aside', {class: 'col', style: 'gap:20px'},
    h('section', {class: 'card col', style: 'gap:14px'}, h('h2', {}, '① 项目目标'),
      h('label', {class: 'field'}, h('span', {}, '目标模板'), tpl),
      h('div', {class: 'field'}, h('span', {}, '先导化合物（只作配对锚点，不产生数值）'), leadInput, leadLabel, h('span', {class: 'muted small', id: 'lead-note'})),
      h('label', {class: 'field'}, h('span', {}, '必须保留的片段（SMARTS / SMILES，可选）'), keepInput),
      h('label', {class: 'field'}, h('span', {}, '合成限制（可选）'), notes),
      h('div', {class: 'field'}, h('span', {}, '范围'), docs),
      h('label', {class: 'row small muted'}, '可变部分最多', maxc, '个重原子')),
    h('section', {class: 'card col', style: 'gap:12px', id: 'mapping'}),
    h('button', {class: 'btn primary', type: 'button', id: 'analyse', onclick: () => analyse(true)}, '确认映射并重新分析'),
    h('p', {class: 'status', role: 'status', id: 'status'}));
  const right = h('div', {class: 'col', style: 'gap:20px', id: 'results'});
  fill(main, h('div', {class: 'split-left'}, aside, right));
  drawMapping();
}

function ruleText(p) {
  const t = p.threshold.kind === 'fold' ? '≥ ' + p.threshold.value + ' 倍' : '差值 ≥ ' + p.threshold.value;
  if (p.direction !== 'range') return DIRECTION[p.direction] + ' · ' + t;
  const r = p.range || {}, u = r.unit ? ' ' + r.unit : '';
  const span = r.low != null && r.high != null ? r.low + '–' + r.high : r.low != null ? '≥ ' + r.low : '≤ ' + r.high;
  return '目标区间 ' + span + u + ' · ' + t;
}

function drawMapping() {
  const box = $('#mapping');
  const pool = new Map();
  (suggestion.properties || []).forEach(p => p.suggested.forEach(a => pool.set(a.assay_id, a)));
  (suggestion.unmapped || []).forEach(a => pool.set(a.assay_id, a));
  (suggestion.assays || []).forEach(a => pool.set(a.assay_id, a));
  const rows = request.goal.properties.map(p => {
    const dir = h('select', {class: 'select direction', 'aria-label': p.label + ' 方向'}, Object.entries(DIRECTION).map(([k, v]) => h('option', {value: k, selected: k === p.direction}, v)));
    const thr = h('input', {class: 'input threshold', type: 'number', step: '0.1', min: '0', value: p.threshold.value, style: 'width:70px', 'aria-label': p.label + ' 阈值'});
    const kind = h('select', {class: 'select threshold-kind', 'aria-label': p.label + ' 阈值类型'}, [['fold', '倍数 ≥'], ['delta', '差值 ≥']].map(([k, v]) => h('option', {value: k, selected: k === p.threshold.kind}, v)));
    const low = h('input', {class: 'input range-low', type: 'number', step: 'any', placeholder: '下限', style: 'width:70px', value: p.range && p.range.low != null ? p.range.low : ''});
    const high = h('input', {class: 'input range-high', type: 'number', step: 'any', placeholder: '上限', style: 'width:70px', value: p.range && p.range.high != null ? p.range.high : ''});
    const unitGuess = (p.range && p.range.unit) || ((pool.get(p.assay_ids[0]) || {}).unit) || '';
    const unit = h('input', {class: 'input range-unit', placeholder: '单位', style: 'width:64px', value: unitGuess, maxlength: 20});
    const rangeBox = h('span', {class: 'row', style: 'gap:6px', hidden: p.direction !== 'range'}, '区间', low, '–', high, unit);
    dir.onchange = () => { rangeBox.hidden = dir.value !== 'range'; };
    const mark = () => { thr.dataset.user = '1'; };
    thr.oninput = mark; kind.onchange = mark;
    if (p.threshold.source === 'user') thr.dataset.user = '1';
    const choices = [...new Set([...p.assay_ids, ...((suggestion.properties.find(x => x.id === p.id) || {suggested: []}).suggested.map(a => a.assay_id))])];
    const picks = h('details', {}, h('summary', {}, '实验（' + p.assay_ids.length + '）'),
      choices.map(aid => { const a = pool.get(aid) || {assay_id: aid, endpoint: '', description: ''};
        return h('label', {class: 'assay-opt'}, h('input', {type: 'checkbox', value: aid, checked: p.assay_ids.includes(aid)}),
          h('span', {}, h('b', {class: 'mono'}, aid.split(':').pop()), ' ', a.endpoint || '', a.measured != null ? ' · ' + a.measured + ' 条' : '', h('br'), h('span', {class: 'muted'}, (a.description || '').slice(0, 120))));
      }));
    return h('div', {class: 'prop-row', dataset: {id: p.id, label: p.label}},
      h('div', {class: 'top'}, h('b', {}, p.label), h('span', {class: 'rule'}, ruleText(p))),
      h('span', {class: 'assays'}, p.assay_ids.map(a => a.split(':').pop()).join(' · ')),
      h('div', {class: 'edit'}, dir, kind, thr, rangeBox,
        h('span', {class: 'badge ' + (p.threshold.source === 'user' ? 'ok' : 'warn')}, p.threshold.source === 'user' ? '用户设定' : '默认值，待确认')),
      picks);
  });
  const unmapped = (suggestion.unmapped || []).slice(0, 6).map(a => a.endpoint + ' ' + a.assay_id.split(':').pop()).join('、');
  fill(box, 
    h('div', {class: 'row', style: 'justify-content:space-between'}, h('h2', {}, '② 性质与实验映射'),
      h('span', {class: 'badge ' + (saved ? 'ok' : 'warn')}, saved ? '已由你确认' : '建议映射，未确认')),
    ...rows,
    h('div', {class: 'note small'}, '噪声阈值默认值待化学家确认。' + (unmapped ? '未归入任何性质：' + unmapped + ((suggestion.unmapped || []).length > 6 ? ' 等' : '') + '（不参与分析）。' : '')));
}

function readRequest() {
  const props = $$('.prop-row').map(row => {
    const p = request.goal.properties.find(x => x.id === row.dataset.id);
    const thr = $('.threshold', row), direction = $('.direction', row).value;
    const out = {id: p.id, label: p.label, direction, assay_ids: $$('.assay-opt input:checked', row).map(i => i.value),
      threshold: {kind: $('.threshold-kind', row).value, value: Number(thr.value), source: thr.dataset.user ? 'user' : 'default_pending_chemist_review'}};
    if (direction === 'range') {
      const n = v => v === '' ? null : Number(v);
      out.range = {low: n($('.range-low', row).value), high: n($('.range-high', row).value),
        unit: $('.range-unit', row).value.trim() || null};
    }
    return out;
  }).filter(p => p.assay_ids.length);
  if (!props.length) throw new Error('请至少为一个性质勾选实验。');
  const smiles = $('#lead-smiles').value.trim(), keep = $('#keep').value.trim();
  const notes = $('#synthesis').value.split(/[;；]/).map(x => x.trim()).filter(Boolean);
  const documents = $$('.scope-doc:checked').map(i => i.value);
  if (!documents.length) throw new Error('请至少选择一个文档。');
  return {mode: 'analyse', goal: {label: request.goal.label, properties: props}, documents,
    max_change: Number($('#max-change').value), lead: smiles ? {smiles, label: $('#lead-label').value.trim() || null} : null,
    constraints: keep || notes.length ? {keep: keep ? [{pattern: keep, label: null}] : [], synthesis_notes: notes} : null};
}

async function analyse(confirm) {
  const st = $('#status'), btn = $('#analyse');
  try {
    if (confirm) request = readRequest();
    btn.disabled = true; setStatus(st, '正在寻找分子对并逐项比较…');
    result = await api('/api/project-sar', request);
    if (confirm) {
      const s = await api('/api/study/analysis', {id, analysis: request});
      saved = s.saved_at; study.analysis = s;
      drawMapping();
    }
    const lead = $('#lead-note');
    if (lead) lead.textContent = result.lead ? result.lead.note : '';
    setStatus(st, '完成：' + result.pair_count + ' 个分子对 · ' + result.transforms.length + ' 种替换 · ' + result.sites.length + ' 个位点' + (confirm ? '；设置已保存到本调研。' : ''));
    drawResults();
  } catch (e) { setStatus(st, e.message, true); }
  finally { btn.disabled = false; }
}

/* ---------- right column */
function valueText(x) {
  if (x.status !== 'comparable') return x.note || OUTCOME[x.status] || x.status;
  const change = x.ratio_b_over_a !== undefined ? 'B/A ' + Number(x.ratio_b_over_a).toPrecision(3) : 'B−A ' + x.delta_b_minus_a;
  const range = x.a_in_range === undefined ? '' : ' · 区间内 A ' + (x.a_in_range ? '是' : '否') + ' / B ' + (x.b_in_range ? '是' : '否');
  return change + range;
}

function cnt(kind, n) { return n ? h('span', {class: 'cnt ' + kind}, String(n)) : h('span', {class: 'muted'}, ''); }

function pairBox(pr, props) {
  const a = compounds.get(pr.a), b = compounds.get(pr.b);
  const lines = [];
  for (const p of props) {
    const cell = pr.properties[p.id];
    const shown = Object.entries(cell.assays).filter(([, x]) => x.lacking !== 'A、B 均');
    if (!shown.length) { lines.push(h('span', {}, p.label), h('span', {class: 'v muted'}, '—'), h('span', {class: 'muted', dataset: {property: p.id}}, OUTCOME[cell.result])); continue; }
    shown.forEach(([aid, x], i) => {
      const unit = x.a && x.a.unit ? ' ' + x.a.unit : '';
      const vals = x.status === 'comparable' ? x.a.value + ' → ' + x.b.value + unit : '—';
      lines.push(h('span', {title: aid}, i ? '' : p.label), h('span', {class: 'v'}, vals + ' ', h('span', {class: 'muted small'}, aid.split(':').pop())),
        h('span', {dataset: {property: p.id}}, x.outcome ? h('b', {class: 'outcome ' + x.outcome}, OUTCOME[x.outcome]) : null, x.outcome ? ' · ' : '', valueText(x)));
    });
  }
  const side = (slot, c, label) => h('div', {}, h('b', {}, slot + ' · ' + label), c ? mol(c.smiles, 's', label) : null);
  return h('div', {class: 'pair-box', dataset: {pair: pr.a_label.split(' ·')[0] + '>' + pr.b_label.split(' ·')[0]}},
    h('div', {class: 'mols'}, side('A', a, pr.a_label.split(' ·')[0]), side('B', b, pr.b_label.split(' ·')[0])),
    h('div', {class: 'col', style: 'gap:8px'}, h('span', {class: 'muted small'}, pr.document + ' · 同一文档内比较' + (pr.document.includes(' / ') ? '（跨文档，不比较）' : '')),
      h('div', {class: 'pair-lines'}, lines)));
}

function groupCard(t, props, lead) {
  const title = groupBy === 'site' ? t.site : t.transform;
  const pairsLine = t.pairs.length + ' 个分子对 · 来源 ' + t.documents.join('、');
  const other = props.some(p => OTHER.some(k => t.summary[p.id].counts[k]));
  const table = h('table', {class: 'data'},
    h('tr', {}, h('th', {}, '性质'), h('th', {}, '方向与阈值'), COLS.map(([, l]) => h('th', {}, l)), other ? h('th', {}, '其他') : null, h('th', {}, '证据等级')),
    props.map(p => { const s = t.summary[p.id];
      return h('tr', {dataset: {property: p.id}}, h('td', {}, p.label), h('td', {class: 'muted'}, ruleText(p)),
        COLS.map(([k]) => h('td', {}, cnt(k, s.counts[k]))),
        other ? h('td', {}, OTHER.filter(k => s.counts[k]).map(k => h('span', {class: 'cnt ' + k, title: OUTCOME[k]}, OUTCOME[k].slice(0, 3) + ' ' + s.counts[k]))) : null,
        h('td', {}, h('span', {class: 'grade ' + s.grade}, s.grade_label))); }));
  const first = lead ? t.pairs.slice(0, 1).map(pr => pairBox(pr, props)) : [];
  const rest = lead ? t.pairs.slice(1) : t.pairs;
  return h('section', {class: 'card tf', dataset: groupBy === 'site' ? {site: t.site, category: t.category.key} : {transform: t.transform, category: t.category.key}},
    h('div', {class: 'tf-head'}, h('span', {class: 'cat ' + t.category.key}, t.category.label), h('code', {}, title),
      h('span', {class: 'muted small'}, pairsLine + (groupBy === 'site' ? ' · 替换 ' + t.transforms.length + ' 种' : ''))),
    t.category.key === 'out_of_scope' ? h('p', {class: 'note warn'}, '破坏了必须保留的片段：' + t.category.lost.join('、') + '。仍然显示，但不作为候选方向，也不进入补测排序。') : null,
    h('div', {class: 'table-wrap'}, table),
    t.synthesis_items && ['candidate', 'tradeoff'].includes(t.category.key) ? h('p', {class: 'muted small'}, '合成可行性待评估项：' + t.synthesis_items.join('；')) : null,
    first,
    rest.length ? h('details', {}, h('summary', {class: 'small', style: 'cursor:pointer;color:var(--accent)'}, (lead ? '其余 ' : '分子对明细 · ') + rest.length + ' 个'),
      h('div', {class: 'col', style: 'margin-top:10px'}, rest.map(pr => pairBox(pr, props)))) : null);
}

function drawResults() {
  const r = result, props = r.goal.properties;
  const groups = groupBy === 'site' ? r.sites : r.transforms;
  const seg = h('div', {class: 'segmented', role: 'group', 'aria-label': '分组方式'},
    h('button', {type: 'button', 'aria-pressed': String(groupBy === 'transform'), id: 'by-transform', onclick: () => { groupBy = 'transform'; drawResults(); }}, '按替换'),
    h('button', {type: 'button', 'aria-pressed': String(groupBy === 'site'), id: 'by-site', onclick: () => { groupBy = 'site'; drawResults(); }}, '按位点'));
  const order = {candidate: 0, tradeoff: 1, unfavorable: 2, insufficient: 3, out_of_scope: 4};
  // Most-supported first; within the same support, candidates and trade-offs before the rest.
  const sorted = [...groups].sort((x, y) => y.pairs.length - x.pairs.length || (order[x.category.key] ?? 9) - (order[y.category.key] ?? 9));
  const fu = r.followups;
  const follow = h('section', {class: 'card followups', id: 'followups'},
    h('div', {class: 'card-head'}, h('h2', {}, '补测建议'), h('span', {class: 'hint'}, fu.rule)),
    fu.items.length ? h('ol', {}, fu.items.slice(0, 8).map((i, n) => h('li', {dataset: {compound: i.compound_id, assay: i.assay_id}},
      h('span', {class: 'rank'}, String(n + 1)), h('b', {}, i.compound.split(' ·')[0] + ' · ' + i.property),
      h('span', {class: 'muted'}, i.assay_id.split(':').pop() + ' · ' + i.reason + '；涉及 ' + i.pairs.map(p => p.pair).join('、')))))
      : h('p', {class: 'empty'}, '没有单侧缺失的目标测量。'),
    fu.total > Math.min(8, fu.items.length) ? h('p', {class: 'muted small'}, '共 ' + fu.total + ' 项，显示前 ' + Math.min(8, fu.items.length) + ' 项。') : null);
  fill($('#results'), ...[
    h('div', {class: 'row'}, seg, h('span', {class: 'muted small'}, '自动配对（MMP：单切与氢替换）· 只比较同一文档内的实验 · 不合成综合分数')),
    h('p', {class: 'note small'}, r.notice),
    groupBy === 'site' ? h('p', {class: 'muted small'}, '位点 = 连接点在不变部分中 ' + r.site_radius + ' 个键以内的化学环境；同一位点下汇总的是不同的替换，“结论矛盾”通常说明不同替换效果不同。') : null,
    ...(sorted.length ? sorted.map((t, i) => groupCard(t, props, i === 0)) : [h('div', {class: 'card empty'}, '范围内没有找到分子对。可放宽可变部分的上限或扩大文档范围。')]),
    follow, signCard(r)].filter(Boolean));
}

function signCard(r) {
  const name = h('input', {class: 'input', id: 'sign-author', placeholder: '你的姓名', maxlength: 40, value: author.get()});
  const verdict = h('select', {class: 'select', id: 'sign-verdict'}, [['support', '支持'], ['pending', '待补测'], ['reject', '拒绝']].map(([k, v]) => h('option', {value: k}, v)));
  const text = h('input', {class: 'input', id: 'sign-text', maxlength: 600, placeholder: '例如：去 N-甲基改善酶活性但外排变差，先补测 6f 细胞 IC50 再判断'});
  const msg = h('p', {class: 'status', role: 'status'});
  const pending = study.confirmed === 0;
  async function sign(kind, body) {
    try {
      if (!name.value.trim()) throw new Error('请填写署名。');
      author.set(name.value.trim());
      await api('/api/study/note', {id, kind, author: name.value, context: 'SAR 分析 · ' + r.goal.label, ...body});
      setStatus(msg, '已署名并加入报告（第 5 节，标为“' + (kind === 'followup' ? '补测建议' : '研究假设') + '”）。');
    } catch (e) { setStatus(msg, e.message, true); }
  }
  return h('section', {class: 'card col', id: 'sign'},
    h('div', {class: 'card-head', style: 'margin:0'}, h('h2', {}, '审查与署名'), h('span', {class: 'hint'}, '自动规则与人工判断分开记录；署名后以“研究假设”写入报告')),
    h('div', {class: 'checks'}, h('span', {}, '限定值、重复观测已排除'), h('span', {}, '只比较同一文档'),
      h('span', {class: pending ? 'warn' : ''}, pending ? '所用观测均为待确认' : '含已确认观测')),
    h('div', {class: 'grid-2', style: 'grid-template-columns: 140px 1fr 200px;gap:12px'},
      h('label', {class: 'field'}, h('span', {}, '结论'), verdict), h('label', {class: 'field'}, h('span', {}, '理由'), text),
      h('label', {class: 'field'}, h('span', {}, '署名'), name)),
    h('div', {class: 'row'},
      h('button', {class: 'btn', type: 'button', disabled: !r.followups.items.length, onclick: () => sign('followup', {
        text: '补测建议：' + r.followups.items.slice(0, 3).map((i, n) => (n + 1) + ') ' + i.compound.split(' ·')[0] + ' ' + i.property + '（' + i.assay_id.split(':').pop() + '）').join('；')})}, '把补测建议加入报告'),
      h('button', {class: 'btn primary', type: 'button', id: 'sign-add', onclick: () => sign('hypothesis', {text: text.value, verdict: verdict.value})}, '署名并加入报告'),
      h('a', {href: studyUrl(id, 'report'), class: 'small'}, '查看报告 →')),
    msg);
}

async function exportMarkdown() {
  try {
    const d = await api('/api/project-sar', {...request, mode: 'report'});
    download('sar-discussion.md', d.markdown, 'text/markdown;charset=utf-8');
  } catch (e) { setStatus($('#status'), e.message, true); }
}
