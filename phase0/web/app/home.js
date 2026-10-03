import {h, $, api, topbar, detect, setStatus, studyUrl, studyLine, GOALS, fail, fill} from '/app/core.js';

const LORLATINIB = 'C[C@H]1Oc2cc(cnc2N)-c2c(nn(C)c2C#N)CN(C)C(=O)c2ccc(F)cc21';
const main = $('#main');
let state = {studies: [], examples: []};

topbar(null);

const input = h('input', {class: 'input hero', id: 'q', type: 'text', autocomplete: 'off', spellcheck: 'false',
  maxlength: 2000, placeholder: 'WO2013132376A1 · ALK · CHEMBL4247 · SMILES', 'aria-describedby': 'detect'});
const go = h('button', {class: 'btn primary big', type: 'submit', id: 'go'}, '开始检索');
const detectBox = h('div', {class: 'detect', id: 'detect', role: 'status', hidden: true});
const status = h('p', {class: 'status', role: 'status'});

const chip = (label, value) => h('button', {type: 'button', class: 'chip-btn', onclick: () => { input.value = value; update(); input.focus(); }}, label);

fill(main, 
  h('section', {class: 'hero col', style: 'gap:16px'},
    h('h1', {}, '从一个靶点、分子、专利或 PDF 开始一项竞对调研'),
    h('p', {}, '输入框自动识别类型；也可以按结构检索，或直接拖入手头的专利 / 论文 PDF。结果都汇入同一个调研，每条结构和活性都能回到原文页码。'),
    h('form', {class: 'search-form', onsubmit: e => { e.preventDefault(); run(); }},
      h('label', {class: 'field'}, h('span', {}, '专利公开号 / SMILES / 靶点 / ChEMBL ID'), input),
      h('a', {class: 'btn big', href: '/search', style: 'font-size:15px;padding:0 18px'}, hexIcon(), '结构检索'),
      go),
    h('a', {class: 'dropzone', href: '/upload'}, docIcon(),
      h('span', {class: 'col', style: 'gap:2px'}, h('b', {}, '拖入专利或论文 PDF，从文件开始调研'),
        h('span', {class: 'sub'}, '支持多份 · 在本机解析，不上传外部服务 · 目前可校验文件并识别已收录的公开文本；自动抽取尚未接入')),
      h('span', {class: 'grow'}), h('span', {class: 'btn primary small'}, '选择文件')),
    detectBox,
    h('div', {class: 'chips'}, h('span', {}, '试试：'), chip('ALK', 'ALK'), chip('CHEMBL4247', 'CHEMBL4247'),
      chip('洛拉替尼 SMILES', LORLATINIB), chip('WO2011138751A2', 'WO2011138751A2')),
    status),
  h('section', {class: 'grid-3'},
    h('div', {class: 'span-2 col', id: 'studies'}, h('h2', {class: 'section-title'}, '我的调研'), h('p', {class: 'status'}, '正在读取…')),
    h('div', {class: 'col', id: 'examples'}, h('h2', {class: 'section-title'}, '示例数据 · 已整理文献'))));

function hexIcon() {
  const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('width', '20'); svg.setAttribute('height', '20'); svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none'); svg.setAttribute('stroke', 'currentColor'); svg.setAttribute('stroke-width', '1.6'); svg.setAttribute('aria-hidden', 'true');
  for (const d of ['M12 3l7.8 4.5v9L12 21l-7.8-4.5v-9z', 'M12 7.5l3.9 2.25']) { const p = document.createElementNS(ns, 'path'); p.setAttribute('d', d); svg.append(p); }
  return svg;
}
function docIcon() {
  const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('width', '28'); svg.setAttribute('height', '28'); svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none'); svg.setAttribute('stroke', '#0A5F55'); svg.setAttribute('stroke-width', '1.6'); svg.setAttribute('aria-hidden', 'true');
  for (const d of ['M14 3H6v18h12V7z', 'M14 3v4h4', 'M12 17v-6', 'M9.5 13.5L12 11l2.5 2.5']) { const p = document.createElementNS(ns, 'path'); p.setAttribute('d', d); svg.append(p); }
  return svg;
}

/* ---------- detection */
let primary = null, smilesCheck = 0;
function studyWith(doc) { return state.studies.find(s => s.documents.includes(doc)); }

function showDetect(kind, text, actions, unknown) {
  detectBox.hidden = false;
  detectBox.dataset.kind = kind;
  fill(detectBox, h('span', {class: 'kind' + (unknown ? ' unknown' : '')}, kind), h('span', {class: 'break'}, text), h('span', {class: 'grow'}), actions);
}

function update() {
  const d = detect(input.value);
  primary = null;
  if (d.kind === 'empty') { detectBox.hidden = true; return; }
  if (d.kind === 'patent') {
    const s = studyWith(d.value);
    const classic = '/classic?mode=patent&q=' + encodeURIComponent(d.value);
    if (s) {
      primary = studyUrl(s.id, 'overview');
      showDetect('识别为专利公开号', '已在调研「' + s.name + '」中', [
        h('a', {href: primary, style: 'font-weight:600'}, '打开该调研 →'),
        h('a', {class: 'btn small', href: classic}, '查看公开页面与同族')]);
    } else {
      primary = classic;
      showDetect('识别为专利公开号', '不在任何调研中；读取 Google Patents 公开页面后可加入台账', [
        h('a', {href: classic, style: 'font-weight:600'}, '读取公开页面 →')]);
    }
  } else if (d.kind === 'chembl' || d.kind === 'text') {
    primary = '/classic?mode=target&q=' + encodeURIComponent(d.value);
    showDetect(d.kind === 'chembl' ? '识别为 ChEMBL ID' : '按靶点名称检索',
      d.kind === 'chembl' ? '按靶点 ID 查询 ChEMBL，选择记录后查看分子与测量'
        : 'ChEMBL 靶点名称 / 基因符号；药物名请改用结构（SMILES）检索', [h('a', {href: primary, style: 'font-weight:600'}, '在 ChEMBL 中查询 →')], d.kind === 'text');
  } else if (d.kind === 'smiles') {
    const ticket = ++smilesCheck;
    primary = '/search?smiles=' + encodeURIComponent(d.value);
    showDetect('可能是 SMILES', '正在用本机 RDKit 解析…', []);
    api('/api/smiles-info?smiles=' + encodeURIComponent(d.value)).then(info => {
      if (ticket !== smilesCheck) return;
      showDetect('识别为 SMILES', info.formula + ' · MW ' + info.mw + ' · ' + info.stereo,
        [h('a', {href: primary, style: 'font-weight:600'}, '设置检索条件 →')]);
    }).catch(e => {
      if (ticket !== smilesCheck) return;
      primary = '/classic?mode=target&q=' + encodeURIComponent(d.value);
      showDetect('无法解析为结构', e.message + ' 如果这是靶点名称，可直接查询。', [h('a', {href: primary}, '按靶点名称查询 →')], true);
    });
  }
}
let timer = null;
input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(update, 250); });

function run() {
  update();
  if (!primary) { setStatus(status, '请输入专利公开号、SMILES、靶点名称或 ChEMBL ID。', true); return; }
  location.href = primary;
}

/* ---------- studies */
function studyCard(s) {
  const goal = GOALS[s.goal_template];
  return h('a', {class: 'card study-card', href: studyUrl(s.id, 'overview'), dataset: {study: s.id}},
    h('div', {class: 'row', style: 'align-items:baseline'}, h('span', {class: 'name'}, s.name),
      s.target ? h('span', {class: 'meta'}, [s.target.label, s.target.organism, s.target.chembl_id].filter(Boolean).join(' · ')) : null,
      s.builtin ? h('span', {class: 'badge'}, '预置示例') : null),
    h('div', {class: 'nums'},
      h('span', {}, h('b', {}, String(s.patents)), '个专利家族'), h('span', {}, h('b', {}, String(s.papers)), '篇论文'),
      h('span', {}, h('b', {}, String(s.compounds)), '条结构记录'), h('span', {}, h('b', {}, String(s.observations)), '条观测')),
    h('div', {class: 'row', style: 'font-size:13px;gap:10px'},
      goal ? h('span', {class: 'muted'}, '目标：' + goal) : null,
      h('span', {class: s.confirmed ? 'badge ok' : 'badge warn'},
        s.confirmed ? '已确认 ' + s.confirmed + ' · 待确认 ' + s.pending : '已确认 0 · 全部待确认')));
}

function newStudyForm(box) {
  const name = h('input', {class: 'input', maxlength: 40, placeholder: '调研名称，例如 EGFR 第三代 · 阿斯利康', 'aria-label': '调研名称'});
  const msg = h('p', {class: 'status', role: 'status'});
  const form = h('form', {class: 'card tight col', onsubmit: async e => {
    e.preventDefault();
    try {
      const s = await api('/api/studies', {name: name.value});
      location.href = studyUrl(s.id, 'overview');
    } catch (err) { setStatus(msg, err.message, true); }
  }}, h('label', {class: 'field'}, h('span', {}, '新调研名称'), name),
    h('div', {class: 'row'}, h('button', {class: 'btn primary', type: 'submit'}, '创建'),
      h('button', {class: 'btn', type: 'button', onclick: () => form.replaceWith(button)}, '取消'),
      h('span', {class: 'muted small'}, '空白调研不含任何文档；可从结构检索结果加入。')), msg);
  const button = h('button', {type: 'button', class: 'new-study', onclick: () => { button.replaceWith(form); name.focus(); }}, '＋ 新建空白调研');
  box.append(button);
}

async function init() {
  try {
    state = await api('/api/studies');
    const box = $('#studies');
    fill(box, h('h2', {class: 'section-title'}, '我的调研'), ...state.studies.map(studyCard));
    newStudyForm(box);
    const ex = $('#examples');
    ex.append(...state.examples.map(e => h('div', {class: 'card tight col', style: 'gap:6px'},
      h('span', {style: 'font-size:15px;font-weight:600'}, e.title),
      h('span', {class: 'muted', style: 'font-size:13px'}, e.citation + ' · ' + e.compounds + ' 个结构 · ' + e.observations + ' 项测量'),
      h('span', {class: 'row', style: 'gap:8px'}, h('span', {class: 'badge outline'}, e.tier + ' 数据库'),
        h('a', {href: '/classic/evidence', class: 'small'}, '在证据台账中查看')))));
    if (!state.ledger_writable) ex.append(h('p', {class: 'note small'}, '当前未启用台账数据库：可以浏览，不能保存复核结论或加入新记录。用 ',
      h('code', {}, '--ledger-db artifacts/ledger.sqlite'), ' 启动服务。'));
    if (input.value) update();
  } catch (e) { fail($('#studies'), e); }
}
init();
