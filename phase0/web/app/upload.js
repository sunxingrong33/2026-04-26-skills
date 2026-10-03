import {h, $, api, topbar, setStatus, studyUrl, fill} from '/app/core.js';

/* Everything on this page runs in the browser: the file is hashed locally and compared with the snapshot
   hashes already recorded in the ledger. No file content is uploaded. Extraction (I3a/I3b) is not wired in,
   and the page says so instead of showing placeholder results. */

topbar([['从 PDF 开始']]);
document.title = '从 PDF 开始 · SAR Atlas';
const main = $('#main');
const files = [];
let current = null, known = [];

const picker = h('input', {type: 'file', accept: 'application/pdf,.pdf', multiple: true, id: 'pdf-input', class: 'sr-only'});
const drop = h('label', {class: 'drop', for: 'pdf-input'}, h('b', {}, '拖入 PDF，或点击选择'), h('span', {}, '支持多份 · 只在本机计算哈希，不上传'), picker);
const list = h('div', {class: 'col', id: 'files', style: 'gap:10px'});
const detail = h('section', {class: 'col', id: 'detail', style: 'gap:20px'});

fill(main,
  h('div', {class: 'row', style: 'align-items:baseline'}, h('a', {href: '/'}, '← 首页'), h('h1', {style: 'font:700 26px var(--serif)'}, '从 PDF 开始'),
    h('span', {class: 'muted small'}, '文件只在本机处理；抽取结果将以“待确认”写入台账，具名复核后才用于结论')),
  h('div', {class: 'split-left', style: 'grid-template-columns:340px minmax(0,1fr)'},
    h('aside', {class: 'col', style: 'gap:12px'}, list, drop,
      h('p', {class: 'note small'}, '当前版本：校验文件、计算 SHA-256，并与台账中已收录的公开 PDF 比对。页面分区与结构 / 活性抽取（迭代 I3a、I3b）尚未接入，不会生成抽取结果。')),
    detail));

drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('over'); });
drop.addEventListener('dragleave', () => drop.classList.remove('over'));
drop.addEventListener('drop', e => { e.preventDefault(); drop.classList.remove('over'); add([...e.dataTransfer.files]); });
picker.onchange = () => { add([...picker.files]); picker.value = ''; };
drawDetail();

api('/api/documents').then(d => { known = d.documents; files.forEach(check); }).catch(() => { known = null; });

function add(fs) {
  for (const f of fs) {
    const item = {file: f, name: f.name, size: f.size, steps: {}, sha: null, match: null, guess: null};
    files.push(item); current = item; check(item);
  }
  drawList(); drawDetail();
}

async function check(item) {
  item.steps.read = {state: 'run', text: '正在读取…'};
  redraw(item);
  try {
    const buf = await item.file.arrayBuffer();
    const head = new TextDecoder('latin1').decode(buf.slice(0, 5));
    if (head !== '%PDF-') { item.steps.read = {state: 'fail', text: '不是 PDF 文件（文件头不是 %PDF-）'}; redraw(item); return; }
    if (!crypto.subtle) throw new Error('当前浏览器环境不支持本机哈希计算（需要 https 或本机地址）');
    const digest = await crypto.subtle.digest('SHA-256', buf);
    item.sha = [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, '0')).join('');
    item.steps.read = {state: 'done', text: 'SHA-256 ' + item.sha.slice(0, 8) + '…' + item.sha.slice(-4) + ' · ' + (item.size / 1048576).toFixed(1) + ' MB'};
  } catch (e) { item.steps.read = {state: 'fail', text: e.message}; redraw(item); return; }
  if (known === null) { item.steps.identify = {state: 'fail', text: '台账文档列表读取失败，无法比对'}; redraw(item); return; }
  if (!known.length) { redraw(item); return; }
  item.match = known.find(d => d.pdf_sha256 && d.pdf_sha256 === item.sha) || null;
  const m = item.name.toUpperCase().replace(/[\s_-]/g, '').match(/(WO|US|EP|CN|JP|KR)\d{6,}[A-Z]\d?/);
  item.guess = m ? m[0] : null;
  if (item.match) {
    item.steps.identify = {state: 'done', text: (item.match.kind === 'patent' ? '专利 · 公开号 ' : '论文 · ') + item.match.id + ' · 与台账记录的公开 PDF 哈希一致'};
    item.steps.record = {state: 'done', text: item.match.kind === 'patent'
      ? '家族 ' + (item.match.family_id || '未记载') + ' · 优先权 ' + (item.match.priority_date || '未记载') + (item.match.studies.length ? ' · 已在调研「' + item.match.studies.map(s => s.name).join('」「') + '」' : ' · 不在任何调研中')
      : '已在台账中' + (item.match.studies.length ? ' · 调研「' + item.match.studies.map(s => s.name).join('」「') + '」' : '')};
  } else {
    item.steps.identify = {state: 'blocked', text: '台账中没有哈希相同的 PDF' + (item.guess ? '；按文件名推测为 ' + item.guess + '（未核实，文件名不作证据）' : '；文件名中未识别出公开号')};
    item.steps.record = {state: 'blocked', text: item.guess ? '可读取该公开号的公开页面，核对家族与日期后加入台账' : '需要人工确定文档身份'};
  }
  redraw(item);
}

function redraw(item) { drawList(); if (item === current) drawDetail(); }

function drawList() {
  fill(list, ...files.map(f => h('button', {type: 'button', class: 'file-card', style: 'text-align:left', 'aria-current': String(f === current),
    onclick: () => { current = f; drawList(); drawDetail(); }},
    h('div', {class: 'row', style: 'gap:8px'}, h('span', {class: 'badge ' + (f.match ? 'ok' : '')}, f.match ? (f.match.kind === 'patent' ? '专利' : '论文') : '未识别'), h('b', {}, f.name)),
    h('span', {class: 'muted small'}, f.steps.read ? f.steps.read.text : '排队中'))));
}

const STEP_DEFS = [
  ['read', '1 读取与校验', '检查 PDF 文件头并在本机计算 SHA-256'],
  ['identify', '2 识别文档', '与台账中已记录的公开 PDF 哈希比对'],
  ['record', '3 公共记录匹配', '家族、优先权日与所在调研'],
  ['partition', '4 页面分区', '识别活性表、实施例与中间体页（控制逻辑已有，只在示例页上测过）'],
  ['extract', '5 逐级抽取', '结构图与活性表抽取、编号核对、冲突保留（抽取器尚未接入，迭代 I3b）'],
  ['write', '6 写入台账', '以“待确认”提交，生成低置信度在前的复核清单'],
];

function drawDetail() {
  if (!current) {
    fill(detail, h('div', {class: 'card empty'}, '选择或拖入专利 / 论文 PDF。文件不会离开本机。'));
    return;
  }
  const f = current;
  const steps = STEP_DEFS.map(([k, title, def], i) => {
    const s = f.steps[k] || (i >= 3 ? {state: 'blocked', text: def} : {state: '', text: def});
    const mark = {done: '✓', fail: '!', run: '…', blocked: '–'}[s.state] || String(i + 1);
    return h('div', {class: 'step ' + s.state, dataset: {step: k}}, h('span', {class: 'mark'}, mark), h('b', {}, title), h('span', {class: 'detail'}, s.text),
      h('span', {class: 'badge ' + (i >= 3 ? 'soon' : s.state === 'done' ? 'have' : s.state === 'fail' ? 'bad' : 'warn')},
        i >= 3 ? '未接入' : s.state === 'done' ? '本机完成' : s.state === 'fail' ? '失败' : s.state === 'blocked' ? '需人工核实' : '等待'));
  });
  const m = f.match;
  const actions = [];
  if (m && m.studies.length) actions.push(h('a', {class: 'btn primary', href: studyUrl(m.studies[0].id, 'evidence')}, '打开「' + m.studies[0].name + '」的证据 →'));
  if (m && m.compounds) actions.push(h('a', {class: 'btn', href: '/review'}, '去复核队列核对'));
  if (!m && f.guess) actions.push(h('a', {class: 'btn primary', href: '/classic?mode=patent&q=' + encodeURIComponent(f.guess)}, '读取 ' + f.guess + ' 的公开页面 →'));
  fill(detail,
    h('div', {class: 'steps'}, steps),
    h('div', {class: 'card col'}, h('div', {class: 'card-head', style: 'margin:0'}, h('h2', {}, '抽取预览'),
      h('span', {class: 'hint'}, m ? '该 PDF 已在台账中，含 ' + m.compounds + ' 条已整理结构记录' : '抽取器接入前不生成预览')),
      m ? h('p', {class: 'muted small', style: 'margin:0'}, '已整理的结构与测量在调研的“证据”页查看；复核在复核队列中具名完成。')
        : h('p', {class: 'muted small', style: 'margin:0'}, '扫描版 PDF 将改用 OCR；识别失败的页面会单独列出，不会被静默跳过。以上功能随迭代 I3a / I3b 接入。')),
    actions.length ? h('div', {class: 'row'}, actions) : null);
}
