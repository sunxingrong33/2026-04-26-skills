import {h, $, $$, api, loadStudy, mol, download, setStatus, author, pairStore, studyUrl, analysisRequest, fill, STATUS_LABEL} from '/app/core.js';

const {id, data, main} = await loadStudy('report', 'report');
const rep = data;
const s = rep.study;
const KIND = rep.note_kinds;
const opts = {include_l1: false, include_hashes: true};
let analysis = null, analysisReq = null, pair = null;

const article = h('article', {class: 'report', id: 'report'});
const aside = h('aside', {class: 'export card col no-print'});
fill(main, h('div', {class: 'report-layout'}, h('div', {}, article), aside));
const pend = () => s.confirmed ? null : h('span', {class: 'pending-mark'}, '待确认');

drawAside();
draw();
loadExtras();

async function loadExtras() {
  try {
    const {request} = await analysisRequest(s);
    analysisReq = request;
    analysis = await api('/api/project-sar', request);
  } catch (e) { analysis = {error: e.message}; }
  const p = pairStore.get(id);
  if (p.a && p.b) {
    try { pair = await api('/api/study/compare', {a: p.a, b: p.b}); } catch (e) { pair = {error: e.message}; }
  }
  draw();
}

function section(n, title, ...body) { return h('section', {class: 'col', style: 'gap:10px', dataset: {section: String(n)}}, h('h2', {}, n + ' ' + title), body); }

function notesOf(kind) { return rep.notes.filter(x => x.kind === kind); }

function noteLine(x) {
  return h('div', {class: 'next-item' + (x.kind === 'gap' ? ' muted-item' : x.kind === 'hypothesis' ? ' warn' : '')},
    h('b', {}, x.text), h('span', {}, (KIND[x.kind] || x.kind) + ' · ' + x.author + ' · ' + x.created_at.slice(0, 10) + ' · 出处：' + x.context));
}

function summaryForm() {
  const text = h('textarea', {class: 'textarea', rows: 3, maxlength: 600, id: 'summary-text', placeholder: '由你撰写结论摘要。工具不自动生成结论。'});
  const name = h('input', {class: 'input', maxlength: 40, placeholder: '署名', value: author.get(), style: 'width:180px'});
  const msg = h('span', {class: 'status small', role: 'status'});
  return h('div', {class: 'col no-print', style: 'gap:8px'}, text, h('div', {class: 'row'}, name,
    h('button', {class: 'btn small primary', type: 'button', id: 'add-summary', onclick: async () => {
      try {
        if (!name.value.trim()) throw new Error('请填写署名。');
        author.set(name.value.trim());
        const note = await api('/api/study/note', {id, kind: 'summary', text: text.value, author: name.value, context: '报告 · 结论摘要'});
        rep.notes.push(note); draw();
      } catch (e) { setStatus(msg, e.message, true); }
    }}, '署名并写入'), msg));
}

function draw() {
  const summaries = notesOf('summary');
  const signed = rep.notes.filter(x => x.kind !== 'summary');
  const famRows = rep.families.map(f => h('tr', {}, h('td', {class: 'mono'}, f.priority_date || '未记载'), h('td', {class: 'mono'}, f.id),
    h('td', {class: 'mono'}, f.family_id || '未记载'), h('td', {}, f.examples.map(e => e.label.replace('Example ', '')).join('、') || '无')));
  let pairSec;
  if (pair && !pair.error) {
    const rows = pair.same_document
      ? pair.measurements.filter(m => m.a.length && m.b.length).map(m => [(m.endpoint || '') + ' · ' + m.assay_id, m.a.map(x => x.text + ' ' + x.unit).join(' / '), m.b.map(x => x.text + ' ' + x.unit).join(' / '), m.ratio !== null ? 'B/A ' + Number(m.ratio).toPrecision(3) : '不计算'])
      : pair.parallel.filter(p => p.a || p.b).map(p => [p.assay, p.a ? p.a.text + ' ' + p.a.unit : '—', p.b ? p.b.text + ' ' + p.b.unit : '—', '只并列']);
    pairSec = section(3, '关键对照 · ' + pair.a.short + ' 与 ' + pair.b.short,
      h('div', {class: 'pair'}, h('figure', {}, h('img', {class: 'mol l framed', src: pair.a_svg, alt: 'A ' + pair.a.short}), 'A · ' + pair.a.document_id + ' ' + pair.a.short),
        h('figure', {}, h('img', {class: 'mol l framed', src: pair.b_svg, alt: 'B ' + pair.b.short}), 'B · ' + pair.b.document_id + ' ' + pair.b.short)),
      h('table', {class: 'data'}, h('tr', {}, h('th', {}, pair.same_document ? '实验（同一文档）' : '实验（原始值并列，跨文档不算倍数）'), h('th', {}, 'A'), h('th', {}, 'B'), h('th', {}, '比较')),
        rows.map(r => h('tr', {}, r.map((c, i) => h('td', {class: i ? 'mono' : ''}, c))))),
      h('p', {class: 'muted small', style: 'margin:0'}, pair.mcs_note));
  } else {
    pairSec = section(3, '关键对照', h('div', {class: 'placeholder'}, pair && pair.error ? '对照未生成：' + pair.error : '尚未选择 A/B。', ' ',
      h('a', {href: studyUrl(id, 'evidence'), class: 'no-print'}, '在证据页选择两个分子 →')));
  }
  let sarSec;
  if (!analysis) sarSec = section(4, 'SAR 分析 · 讨论材料', h('p', {class: 'status'}, '正在生成…'));
  else if (analysis.error) sarSec = section(4, 'SAR 分析 · 讨论材料', h('div', {class: 'placeholder'}, '分析未生成：' + analysis.error));
  else {
    const key = analysis.transforms.filter(t => ['candidate', 'tradeoff', 'unfavorable'].includes(t.category.key))
      .sort((a, b) => b.pairs.length - a.pairs.length).slice(0, 4);
    sarSec = section(4, 'SAR 分析 · 讨论材料',
      h('span', {class: 'muted small'}, '目标：' + analysis.goal.label + (analysis.pending_defaults.length ? ' · 阈值为默认值（待化学家确认）' : '') + ' · 不合成综合分数'),
      h('table', {class: 'data'}, key.map(t => h('tr', {}, h('td', {class: 'mono', style: 'width:38%'}, t.transform + '（' + t.pairs.length + ' 对）'),
        h('td', {}, t.category.label + '；' + analysis.goal.properties.map(p => p.label + ' ' + t.summary[p.id].grade_label).join('、'))))),
      analysis.followups.items.length ? h('p', {style: 'margin:0'}, h('b', {}, '补测建议　'),
        analysis.followups.items.slice(0, 3).map((i, n) => '①②③'[n] + ' ' + i.compound.split(' ·')[0] + ' ' + i.property + '（' + i.assay_id.split(':').pop() + '）').join('　')) : null);
  }
  fill(article,
    h('div', {class: 'col', style: 'gap:8px'}, h('span', {class: 'kicker'}, '竞对 SAR 调研'), h('h1', {}, s.name),
      h('span', {class: 'byline'}, '作者 ' + (author.get() || '（未署名）') + ' · 生成于 ' + rep.generated_at.slice(0, 10) + ' · ' + s.patents + ' 个专利家族 · ' + s.papers +
        ' 篇论文 · 已确认证据 ' + s.confirmed + ' 条' + (s.pending ? '，其余 ' + s.pending + ' 条标“待确认”' : ''))),
    section(1, '结论摘要', summaries.length ? summaries.map(noteLine)
      : h('div', {class: 'placeholder'}, '由你撰写。工具不自动生成结论；对照页和 SAR 分析页中署名的判断会以“研究假设”列在第 5 节。'), summaryForm()),
    section(2, '家族时间线', h('table', {class: 'data'}, h('tr', {}, h('th', {}, '优先权日'), h('th', {}, '公开号'), h('th', {}, '家族'), h('th', {}, '收录实施例')), famRows)),
    pairSec, sarSec,
    section(5, '事实、假设与缺口',
      rep.relations.map(r => h('div', {class: 'trio'},
        h('div', {class: 'facts'}, h('span', {class: 'h'}, '事实'), r.facts.map(x => h('span', {}, x))),
        h('div', {class: 'hyp'}, h('span', {class: 'h'}, '研究假设'), r.hypothesis ? h('span', {}, r.hypothesis + '（未署名）') : h('span', {}, '无'),
          notesOf('hypothesis').map(x => h('span', {}, x.text + '（' + x.author + '）'))),
        h('div', {class: 'gaps'}, h('span', {class: 'h'}, '证据缺口'), r.gaps.map(x => h('span', {}, x)), notesOf('gap').map(x => h('span', {}, x.text + '（' + x.author + '）'))))),
      !rep.relations.length && !signed.length ? h('div', {class: 'placeholder'}, '尚无已整理关系或署名判断。') : null,
      signed.length ? h('div', {class: 'col', style: 'gap:6px'}, h('b', {class: 'small'}, '署名判断（全部）'), signed.map(noteLine)) : null),
    section('附录', '证据来源',
      h('table', {class: 'data', id: 'appendix'}, h('tr', {}, h('th', {}, '实施例'), h('th', {}, '等级'), h('th', {}, '结构来源'), h('th', {}, '活性来源')),
        rep.appendix.map(a => h('tr', {}, h('td', {class: 'mono'}, a.label), h('td', {}, a.tier, a.status === 'confirmed' ? null : h('span', {class: 'pending-mark'}, STATUS_LABEL[a.status])),
          h('td', {}, a.structure ? 'PDF p.' + a.structure : '未记载'), h('td', {}, a.activity.length ? 'PDF p.' + a.activity.join('、') : '无测量')))),
      opts.include_l1 && rep.papers.length ? h('table', {class: 'data'}, h('tr', {}, h('th', {}, '论文（L1 数据库导入）'), h('th', {}, '结构'), h('th', {}, '观测')),
        rep.papers.map(p => h('tr', {}, h('td', {}, p.title + '（' + p.citation + '）· ' + p.id, pend()), h('td', {class: 'mono'}, String(p.compounds)), h('td', {class: 'mono'}, String(p.observations))))) : null,
      opts.include_hashes ? h('table', {class: 'data', id: 'hashes'}, h('tr', {}, h('th', {}, '文档'), h('th', {}, '快照 SHA-256')),
        rep.documents.map(d => h('tr', {}, h('td', {class: 'mono'}, d.id), h('td', {class: 'mono small break'},
          Object.entries(d.sha256).map(([k, v]) => k + ' ' + v).join('；') || '无（数据库记录）')))) : null,
      s.confirmed ? null : h('p', {class: 'muted small', style: 'margin:0'}, '本报告引用的证据均未经具名确认（待确认）。')));
}

function drawAside() {
  const fmt = (v, label) => h('label', {class: 'check'}, h('input', {type: 'radio', name: 'fmt', value: v, checked: v === 'pdf'}), label);
  const opt = (k, label, disabled) => h('label', {class: 'check'}, h('input', {type: 'checkbox', checked: disabled || opts[k], disabled: !!disabled,
    onchange: e => { opts[k] = e.target.checked; draw(); }}), label);
  const msg = h('p', {class: 'status small', role: 'status'});
  fill(aside, h('h2', {}, '导出'),
    h('fieldset', {}, h('legend', {}, '格式'), fmt('pdf', 'PDF · 用于汇报（浏览器打印）'), fmt('md', 'Markdown 讨论材料'), fmt('json', '证据与分析 JSON')),
    h('fieldset', {}, h('legend', {}, '包含内容'), opt('l2', 'L2 转录证据（标注未复核）', true), opt('include_l1', 'L1 数据库测量（论文）'), opt('include_hashes', '来源快照哈希')),
    s.confirmed ? null : h('div', {class: 'note warn small'}, '报告中的证据均未经具名确认，每一处引用都会带“待确认”标记。'),
    h('button', {class: 'btn primary', type: 'button', id: 'export', onclick: () => exportAs($('input[name=fmt]:checked', aside).value, msg)}, '导出'),
    msg,
    s.pending ? h('a', {href: '/review', class: 'small'}, '先去复核 ' + s.pending + ' 条证据 →') : null);
}

async function exportAs(kind, msg) {
  try {
    if (kind === 'pdf') { setStatus(msg, '在打印对话框中选择“另存为 PDF”。'); window.print(); return; }
    setStatus(msg, '正在生成…');
    const d = await api('/api/study/report', {id, analysis: analysisReq && !(analysis && analysis.error) ? analysisReq : null, options: opts});
    if (kind === 'md') download(id + '-report.md', d.markdown, 'text/markdown;charset=utf-8');
    else download(id + '-report.json', JSON.stringify({report: d.report, analysis: analysis && !analysis.error ? analysis : null, options: opts}, null, 2), 'application/json');
    setStatus(msg, '已导出。');
  } catch (e) { setStatus(msg, e.message, true); }
}
