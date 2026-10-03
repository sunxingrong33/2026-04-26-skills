import {h, $, $$, api, topbar, mol, setStatus, studyUrl, fill, external} from '/app/core.js';

const main = $('#main');
const params = new URLSearchParams(location.search);
const METHOD = {exact: '精确 · 标准 InChIKey', similarity: '相似性', substructure: '子结构'};
const STATUS = {ok: ['已查询', 'st-ok'], not_requested: ['未查询', 'st-off'], failed: ['查询失败', 'st-fail'], per_compound: ['按结构单独查询', 'st-off']};
let ctx = {studies: [], documents: []};

topbar([['结构检索']]);
document.title = '结构检索 · SAR Atlas';

const opts = {
  smiles: params.get('smiles') || '', method: params.get('method') || 'exact', threshold: +(params.get('threshold') || 70),
  standardize: params.get('standardize') !== '0', chembl: params.get('chembl') === '1', surechembl: params.get('surechembl') === '1',
};

init();
async function init() {
  try { ctx = await api('/api/studies'); } catch { /* the search still works without the study list */ }
  if (params.get('run') === '1' && opts.smiles) results(); else options();
}

function query() {
  const q = new URLSearchParams({smiles: opts.smiles, method: opts.method, threshold: String(opts.threshold),
    standardize: opts.standardize ? '1' : '0', chembl: opts.chembl ? '1' : '0', surechembl: opts.surechembl ? '1' : '0'});
  return q;
}

/* ================= options (design 1a) */
function options() {
  const input = h('input', {class: 'input mono', id: 'smiles', value: opts.smiles, maxlength: 2000, spellcheck: 'false',
    placeholder: '粘贴 SMILES，例如 C[C@H]1Oc2cc(cnc2N)-c2c(nn(C)c2C#N)CN(C)C(=O)c2ccc(F)cc21', style: 'height:48px;font-size:14px'});
  const file = h('input', {type: 'file', accept: '.mol,.sdf,.sd,chemical/x-mdl-molfile', hidden: true, id: 'molfile'});
  const preview = h('section', {class: 'card col', id: 'parsed'}, h('h2', {}, '解析结果 · 本地 RDKit'), h('p', {class: 'muted small'}, '输入 SMILES 后显示结构、分子式与立体信息。'));
  const st = h('p', {class: 'status', role: 'status'});
  file.onchange = async () => {
    const f = file.files[0]; if (!f) return;
    try {
      const r = await api('/api/molfile', {text: await f.text()});
      input.value = r.smiles; opts.smiles = r.smiles; setStatus(st, f.name + '：' + r.note + '。'); parse();
    } catch (e) { setStatus(st, e.message, true); }
  };
  let ticket = 0;
  async function parse() {
    const t = ++ticket, smi = input.value.trim();
    opts.smiles = smi;
    if (!smi) return;
    try {
      const r = await api('/api/smiles-info?smiles=' + encodeURIComponent(smi));
      if (t !== ticket) return;
      fill(preview, h('h2', {}, '解析结果 · 本地 RDKit'), mol(r.canonical, 'l', '输入结构'),
        h('div', {class: 'kv'}, h('span', {class: 'k'}, '分子式'), h('span', {class: 'v'}, r.formula), h('span', {class: 'k'}, 'MW'), h('span', {class: 'v'}, String(r.mw)),
          h('span', {class: 'k'}, 'InChIKey'), h('span', {class: 'v'}, r.inchikey), h('span', {class: 'k'}, '立体'), h('span', {}, r.stereo),
          h('span', {class: 'k'}, '组分'), h('span', {}, r.components)),
        h('div', {class: 'note ok small'}, '解析成功 · ' + r.heavy_atoms + ' 个重原子 · 结构未离开本机'));
    } catch (e) { if (t === ticket) fill(preview, h('h2', {}, '解析结果 · 本地 RDKit'), h('div', {class: 'note error small'}, e.message)); }
  }
  let timer; input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(parse, 300); });

  const radio = (v, title, sub, extra, disabled) => h('label', {class: 'option'},
    h('input', {type: 'radio', name: 'method', value: v, checked: opts.method === v, disabled: !!disabled, onchange: () => { opts.method = v; summary(); }}),
    h('span', {class: 't grow'}, h('b', {}, title), h('span', {}, sub)), extra);
  const thr = h('input', {class: 'input', type: 'number', min: 40, max: 100, step: 1, value: opts.threshold, id: 'threshold', style: 'width:72px', 'aria-label': '相似度阈值（%）',
    oninput: e => { opts.threshold = +e.target.value; summary(); }});
  const check = (k, label, tags, disabled) => h('label', {class: 'check'}, h('input', {type: 'checkbox', checked: disabled ? false : (k ? opts[k] : true), disabled: !!disabled || !k,
    id: k ? 'src-' + k : null, onchange: e => { opts[k] = e.target.checked; summary(); }}), h('span', {class: 'grow'}, label), tags);
  const bar = h('span', {class: 'mono small muted', id: 'summary'});
  function summary() {
    const src = ['本地台账', opts.chembl ? 'ChEMBL' : null, opts.surechembl ? 'SureChEMBL' : null].filter(Boolean).join(' + ');
    bar.textContent = METHOD[opts.method] + (opts.method === 'similarity' ? ' ≥ ' + opts.threshold + '%' : '') + ' · ' + src + (opts.standardize ? ' · 标准化' : '');
  }
  const run = () => {
    if (!input.value.trim()) { setStatus(st, '请先输入 SMILES。', true); return; }
    opts.smiles = input.value.trim();
    const q = query(); q.set('run', '1'); location.href = '/search?' + q;
  };
  fill(main,
    h('div', {class: 'row', style: 'align-items:baseline'}, h('a', {href: '/'}, '← 首页'), h('h1', {style: 'font:700 26px var(--serif)'}, '结构检索')),
    h('div', {class: 'row', style: 'align-items:flex-end;flex-wrap:nowrap'},
      h('label', {class: 'field grow'}, h('span', {}, 'SMILES（最多 2000 字符 / 200 个原子）'), input),
      h('button', {class: 'btn', type: 'button', style: 'height:48px', onclick: () => file.click()}, '上传 MOL / SDF'), file),
    st,
    h('div', {class: 'split-left', style: 'grid-template-columns: 420px minmax(0,1fr)'}, preview,
      h('section', {class: 'card col', style: 'gap:24px'},
        h('fieldset', {}, h('legend', {}, '1 匹配方式'),
          radio('exact', '精确', '标准化后按标准 InChIKey 匹配（含立体层）；会合并盐型与互变异构形式'),
          radio('substructure', '子结构', '以输入为片段，至少 6 个重原子；不做互变异构标准化'),
          radio('similarity', '相似性', 'Morgan 半径 2、2048 位 · Tanimoto；只排序，不作证据', h('span', {class: 'row', style: 'gap:6px'}, '≥', thr, '%')),
          radio('exact_nostereo', '精确 · 忽略立体', 'InChIKey 前 14 位一致；尚未支持', h('span', {class: 'badge soon'}, '尚未支持'), true)),
        h('fieldset', {}, h('legend', {}, '2 检索范围'),
          check(null, '本地证据台账 · ' + ctx.documents.length + ' 份文档 · 离线', h('span', {class: 'badge have'}, '始终检索')),
          check('chembl', 'ChEMBL：分子、测量与来源文档', h('span', {class: 'badge pending-online'}, '联网')),
          check('surechembl', 'SureChEMBL：专利中自动提取的结构，异步检索', h('span', {class: 'badge pending-online'}, '联网')),
          check(null, '上传 PDF 抽取出的结构', h('span', {class: 'badge soon'}, '抽取器尚未接入'), true),
          h('div', {class: 'note small'}, '将发送：ChEMBL 精确检索发送标准 InChIKey，相似性与子结构发送标准化后的 SMILES；SureChEMBL 发送标准化后的 SMILES。PubChem 交叉引用在结果页按结构单独查询。未公开的内部结构请只查本地。')),
        h('div', {class: 'grid-2', style: 'gap:24px'},
          h('fieldset', {}, h('legend', {}, '3 标准化'),
            h('label', {class: 'check'}, h('input', {type: 'checkbox', id: 'standardize', checked: opts.standardize, onchange: e => { opts.standardize = e.target.checked; summary(); }}), '去盐和溶剂、中和、统一互变异构'),
            h('span', {class: 'muted small'}, '实际做了哪些改动逐步记录；互变异构改变立体信息时提示')),
          h('fieldset', {}, h('legend', {}, '4 命中后'),
            h('span', {class: 'small'}, '先预览全部命中，再选择加入调研；不会自动写入台账。'))),
        h('div', {class: 'row', style: 'border-top:1px solid var(--line);padding-top:16px'}, bar, h('span', {class: 'grow'}),
          h('button', {class: 'btn primary', type: 'button', id: 'run-search', onclick: run}, '检索 →')))));
  summary();
  if (opts.smiles) parse();
}

/* ================= results (design 1b) */
let res = null;
const sel = new Set();
const facets = {local: true, chembl: true, surechembl: true, patent: true, paper: true, hideIn: false};

function docKind(docId) { return /^(WO|US|EP|CN|JP|KR)/.test(docId) ? 'patent' : 'paper'; }
function studyOf(docId) { return ctx.studies.find(s => s.documents.includes(docId)); }

async function results() {
  const st = h('p', {class: 'status', role: 'status', id: 'status'}, '正在检索…');
  fill(main, st);
  try {
    res = await api('/api/discover', {mode: 'smiles', query: opts.smiles, method: opts.method,
      threshold: opts.method === 'similarity' ? opts.threshold : undefined, standardize: opts.standardize,
      external: opts.chembl, surechembl: opts.surechembl});
    drawResults();
  } catch (e) {
    fill(main, h('div', {class: 'note error'}, e.message + ' ', h('a', {href: '/search?' + query()}, '修改条件')));
  }
}

function band() {
  const q = res.search;
  const changed = q.original_smiles !== q.searched_smiles;
  const steps = changed ? (q.steps || []).map(s => s.note).filter(Boolean) : [];
  const edit = '/search?' + query();
  return h('div', {class: 'query-band'}, mol(q.searched_smiles, 's', '查询结构'),
    h('div', {class: 'q'}, h('span', {class: 's'}, q.input),
      h('div', {class: 'row', style: 'gap:8px'}, h('span', {class: 'badge mono'}, METHOD[q.method] + (q.threshold ? ' ≥ ' + q.threshold + '%' : '')),
        h('span', {class: 'badge'}, ['本地台账', opts.chembl ? 'ChEMBL' : null, opts.surechembl ? 'SureChEMBL' : null].filter(Boolean).join(' + ')),
        h('span', {class: 'badge' + (changed ? ' warn' : '')}, q.standardize ? (changed ? '已标准化：' + (steps.join('；') || '结构有改动') : '已标准化 · 结构未改变') : '未标准化'))),
    h('a', {class: 'btn', href: edit}, '修改条件'),
    q.method === 'exact' ? h('a', {class: 'btn', href: '/search?' + new URLSearchParams({...Object.fromEntries(query()), method: 'similarity', threshold: '70', run: '1'})}, '放宽为相似性 ≥ 70%') : null);
}

function coverage() {
  const c = res.coverage;
  return h('div', {class: 'card', id: 'coverage'},
    h('div', {class: 'card-head'}, h('h2', {}, '检索覆盖报告'), h('span', {class: 'hint'}, '未命中不代表不存在：范围仅限所列来源，受阈值、标准化和返回上限影响')),
    h('div', {class: 'coverage'}, h('span', {class: 'h'}, '来源'), h('span', {class: 'h'}, '状态'), h('span', {class: 'h'}, '命中 / 显示'), h('span', {class: 'h'}, '说明'),
      c.sources.map(s => { const [label, cls] = STATUS[s.status] || [s.status, ''];
        return [h('b', {}, s.source), h('span', {class: cls}, label),
          h('span', {class: 'mono'}, s.total == null ? '—' : s.total + ' / ' + s.returned + (s.truncated ? '（截断）' : '')), h('span', {class: 'muted small'}, s.note || s.scope || '')]; })),
    c.gaps.length ? h('details', {style: 'margin-top:12px'}, h('summary', {class: 'small', style: 'cursor:pointer;color:var(--accent)'}, '已知缺口 ' + c.gaps.length + ' 项'),
      h('ul', {class: 'small muted'}, c.gaps.map(g => h('li', {}, g)))) : null,
    res.warning ? h('p', {class: 'note warn small'}, res.warning) : null);
}

function localRows() {
  return (res.ledger_matches ? res.ledger_matches.rows : []).map(r => ({...r, kind: docKind(r.document_id), study: studyOf(r.document_id)}));
}

function facetBox() {
  const local = localRows();
  const cb = (k, label, n) => h('label', {class: 'check'}, h('input', {type: 'checkbox', checked: facets[k], onchange: e => { facets[k] = e.target.checked; drawList(); }}), label, n != null ? h('span', {class: 'n'}, String(n)) : null);
  return h('aside', {class: 'facet'},
    h('fieldset', {}, h('legend', {}, '来源'), cb('local', '本地台账', local.length), cb('chembl', 'ChEMBL', (res.molecules || []).length), cb('surechembl', 'SureChEMBL', ((res.surechembl || {}).rows || []).length)),
    h('fieldset', {}, h('legend', {}, '文档类型（本地）'), cb('patent', '专利', local.filter(r => r.kind === 'patent').length), cb('paper', '论文', local.filter(r => r.kind === 'paper').length)),
    cb('hideIn', '隐藏已在调研中的命中'),
    h('div', {class: 'card tight col', style: 'gap:8px'}, h('b', {class: 'small'}, 'PubChem 交叉引用'),
      h('span', {class: 'muted small'}, '查询结构的关联专利与 PubMed 文献（发送标准 InChIKey）。可能与 SureChEMBL 同源，并列显示。'),
      h('button', {class: 'btn small', type: 'button', id: 'pubchem', onclick: pubchem}, '查询 PubChem'), h('div', {id: 'pubchem-result', class: 'col', style: 'gap:6px'})));
}

async function pubchem() {
  const box = $('#pubchem-result'), btn = $('#pubchem');
  btn.disabled = true; fill(box, h('span', {class: 'status small'}, '正在查询…'));
  try {
    const r = await api('/api/discover', {mode: 'pubchem_xrefs', inchikey: res.structure.inchikey});
    fill(box, h('span', {class: 'small'}, '关联专利 ' + r.patents.total + ' 份' + (r.patents.curated_matches.length ? '（含已整理 ' + r.patents.curated_matches.join('、') + '）' : '') + ' · PubMed ' + r.literature.total + ' 篇'),
      r.patents.rows.slice(0, 8).map(p => h('span', {class: 'small mono'}, p.curated ? '★ ' : '', p.publication
        ? h('a', {href: '/classic?mode=patent&q=' + encodeURIComponent(p.publication)}, p.publication) : p.patent_id)),
      h('span', {class: 'muted small'}, r.attribution || ''));
  } catch (e) { fill(box, h('span', {class: 'status error small'}, e.message)); }
  finally { btn.disabled = false; }
}

function hitRow(r) {
  const inStudy = r.study;
  const src = r.structure_source || {};
  return h('div', {class: 'hit', dataset: {compound: r.compound_id, document: r.document_id}},
    h('input', {type: 'checkbox', checked: inStudy ? true : sel.has(r.document_id), disabled: !!inStudy, 'aria-label': inStudy ? '已在调研中' : '选择 ' + r.document_id,
      onchange: e => { e.target.checked ? sel.add(r.document_id) : sel.delete(r.document_id); drawAdd(); }}),
    h('div', {class: 'when'}, h('b', {}, r.document_id.slice(0, 14)), h('span', {}, r.kind === 'patent' ? '专利' : '论文')),
    h('div', {class: 'what'}, h('span', {class: 't'}, r.label.split(' · ')[0]),
      h('span', {class: 's'}, (r.measured_observations ? '本地台账 ' + r.measured_observations + ' 条测量' : '只有结构，没有测量') + (src.pdf_page ? ' · 结构 PDF p.' + src.pdf_page : ''))),
    h('div', {class: 'how'}, h('span', {class: 'badge mono'}, r.similarity != null ? '相似度 ' + r.similarity.toFixed(3) : METHOD[res.search.method].split(' ')[0]),
      h('span', {class: 'badge tier-' + (r.kind === 'patent' ? 'L2' : 'L1')}, (r.kind === 'patent' ? 'L2' : 'L1') + ' · 本地台账')),
    h('div', {class: 'act'}, inStudy ? [h('span', {class: 'muted'}, '已在「' + inStudy.name + '」'), h('a', {href: studyUrl(inStudy.id, 'evidence')}, '打开证据 →')]
      : h('span', {class: 'muted'}, '勾选后可加入调研')));
}

function chemblRow(m) {
  const c = m.local_check || {};
  return h('div', {class: 'hit chembl-hit', dataset: {localCheck: c.status || ''}},
    h('span', {}), h('div', {class: 'when'}, h('b', {}, m.molecule_chembl_id), h('span', {}, 'ChEMBL 分子')),
    h('div', {class: 'what'}, h('span', {class: 't'}, m.pref_name || '无通用名'), h('span', {class: 's mono break'}, m.canonical_smiles || '')),
    h('div', {class: 'how'}, m.chembl_similarity != null ? h('span', {class: 'badge mono'}, 'ChEMBL ' + m.chembl_similarity) : null,
      h('span', {class: 'badge ' + (c.status === 'agrees' ? 'ok' : 'bad')}, c.note || '未复核')),
    h('div', {class: 'act'}, external('https://www.ebi.ac.uk/chembl/compound_report_card/' + encodeURIComponent(m.molecule_chembl_id) + '/', 'ChEMBL 页面'),
      h('a', {href: '/classic?mode=target&q=' + encodeURIComponent(m.molecule_chembl_id)}, '查看测量（经典界面）')));
}

function surechemblRow(m) {
  const c = m.local_check || {};
  const box = h('div', {class: 'trail', hidden: true});
  return [h('div', {class: 'hit surechembl-hit', dataset: {localCheck: c.status || ''}},
    h('span', {}), h('div', {class: 'when'}, h('b', {}, m.schembl_id), h('span', {}, 'SureChEMBL · L0 自动提取')),
    h('div', {class: 'what'}, h('span', {class: 't'}, m.name || '无名称'), h('span', {class: 's mono break'}, m.smiles || '')),
    h('div', {class: 'how'}, m.surechembl_similarity != null ? h('span', {class: 'badge mono'}, '相似度 ' + m.surechembl_similarity) : null,
      h('span', {class: 'badge ' + (c.status === 'agrees' ? 'ok' : 'bad')}, c.note || '未复核')),
    h('div', {class: 'act'}, h('button', {class: 'btn small', type: 'button', onclick: async e => {
      e.target.disabled = true; box.hidden = false; fill(box, '正在查询含此化合物的专利…');
      try {
        const r = await api('/api/discover', {mode: 'surechembl_documents', id: m.schembl_id});
        fill(box, h('span', {}, '共 ' + r.total + ' 份专利' + (r.truncated ? '，显示前 ' + r.patents.length + ' 份' : '') + '；是否为实施例待核实：'),
          r.patents.map(p => p.publication ? h('a', {href: '/classic?mode=patent&q=' + encodeURIComponent(p.publication)}, '核实并加载 ' + p.publication + (p.publication_date ? '（' + p.publication_date + '）' : ''))
            : h('span', {}, p.doc_id)));
      } catch (err) { fill(box, h('span', {class: 'st-fail'}, err.message)); }
      finally { e.target.disabled = false; }
    }}, '查看含此化合物的专利'), external(m.url, 'SureChEMBL'))), box];
}

function drawList() {
  const out = [];
  const local = localRows().filter(r => facets.local && facets[r.kind] && !(facets.hideIn && r.study));
  const patents = local.filter(r => r.kind === 'patent'), papers = local.filter(r => r.kind === 'paper');
  if (patents.length) out.push(h('div', {class: 'hit-group'}, '专利 · 本地台账', h('span', {class: 'hint'}, '实施例编号不跨公开继承')), ...patents.map(hitRow));
  if (papers.length) out.push(h('div', {class: 'hit-group'}, '论文 · 本地台账', h('span', {class: 'hint'}, '编号来自 ChEMBL 化合物记录，原文定位待核对')), ...papers.map(hitRow));
  const sc = res.surechembl || {};
  if (facets.surechembl && sc.status === 'ok') out.push(h('div', {class: 'hit-group'}, 'SureChEMBL 专利化学命中 · 共 ' + sc.total + ' 个', h('span', {class: 'hint'}, (sc.below_threshold ? sc.below_threshold + ' 个低于所选阈值未显示 · ' : '') + (sc.attribution || ''))),
    ...(sc.rows || []).flatMap(surechemblRow));
  if (facets.surechembl && sc.status === 'failed') out.push(h('p', {class: 'note warn small', style: 'margin:12px'}, sc.warning));
  if (facets.chembl && (res.molecules || []).length) out.push(h('div', {class: 'hit-group'}, 'ChEMBL 分子 · 共 ' + (res.total ?? res.molecules.length) + ' 个', h('span', {class: 'hint'}, '远程命中逐个经本地 RDKit 复核，不一致标红不删除')),
    ...res.molecules.map(chemblRow));
  if (!out.length) out.push(h('p', {class: 'empty'}, '当前筛选下没有命中。零命中只说明所列来源中没有，不代表不存在。'));
  fill($('#hits'), ...out);
}

function drawAdd() {
  const bar = $('#addbar');
  const writable = ctx.studies.length > 0;
  const target = h('select', {class: 'select', id: 'target-study', 'aria-label': '目标调研'}, ctx.studies.map(s => h('option', {value: s.id}, s.name)));
  const msg = h('span', {class: 'small', role: 'status', id: 'add-status'});
  fill(bar, h('span', {}, '已选 ' + sel.size + ' 份文档'),
    h('span', {class: 'muted small', style: 'color:var(--head-muted)'}, '只能加入已在本地台账中的文档；外部命中请先核实并加入台账（经典界面）'),
    h('span', {class: 'grow'}), writable ? h('label', {class: 'row small'}, '加入到', target) : null,
    h('button', {class: 'btn bright', type: 'button', id: 'add-to-study', disabled: !sel.size || !writable, onclick: async () => {
      try {
        const r = await api('/api/study/documents', {id: target.value, documents: [...sel]});
        location.href = studyUrl(target.value, 'overview') + '?added=' + r.added.length;
      } catch (e) { setStatus(msg, e.message, true); }
    }}, '加入调研 →'), msg);
}

function drawResults() {
  document.title = '检索结果 · SAR Atlas';
  fill(main, band(), coverage(),
    h('div', {class: 'results-layout'}, facetBox(), h('section', {class: 'card flush', id: 'hits'})),
    h('p', {class: 'muted small'}, res.notice || ''));
  main.after(h('div', {class: 'addbar', id: 'addbar'}));
  drawList(); drawAdd();
}
