import {h, $, api, loadStudy, mol, studyUrl, GOALS, analysisRequest, fill} from '/app/core.js';

const {id, data, main} = await loadStudy('overview', 'overview', d => [
  h('a', {class: 'btn', href: '/search'}, '＋ 添加专利 / 分子'),
  h('a', {class: 'btn primary', href: studyUrl(d.study.id, 'report')}, '生成报告')]);
const s = data.study;

if (!s.documents.length) {
  fill(main, h('div', {class: 'card col'}, h('h2', {}, '这个调研还没有文档'),
    h('p', {class: 'muted'}, '用结构检索找到专利或论文后，在结果页底部选择“加入到”本调研；也可以先把专利加入台账（经典界面），再回到这里。'),
    h('div', {class: 'row'}, h('a', {class: 'btn primary', href: '/search'}, '结构检索'), h('a', {class: 'btn', href: '/classic'}, '经典界面：专利与靶点检索'))));
} else draw();

function stat(k, v, d, opts = {}) {
  const tag = opts.href ? 'a' : 'div';
  return h(tag, {class: 'card tight stat' + (opts.warn ? ' warn' : ''), href: opts.href || null},
    h('span', {class: 'k'}, k), h('span', {class: 'v' + (opts.text ? ' text' : '')}, v), d);
}

function draw() {
  const dates = data.families.map(f => f.priority_date).filter(Boolean).sort();
  const nCards = data.families.reduce((n, f) => n + f.examples.length, 0);
  const goal = (s.analysis && s.analysis.goal && s.analysis.goal.label) || GOALS[s.goal_template] || '未设定';
  const nProps = s.analysis && s.analysis.goal ? s.analysis.goal.properties.length : null;
  const stats = h('section', {class: 'grid-4'},
    stat('专利家族', String(s.patents), h('span', {class: 'd'}, dates.length ? '优先权 ' + dates[0] + (dates.length > 1 ? ' 至 ' + dates[dates.length - 1] : '') : '优先权日未记载')),
    stat('台账范围（结构记录 / 观测）', s.compounds + ' / ' + s.observations,
      h('span', {class: 'd'}, '专利证据卡 ' + nCards + ' · 论文 ' + (s.compounds - nCards) + (s.not_tested ? '；观测含 ' + s.not_tested + ' 项未测' : ''))),
    stat('项目目标', goal, h('span', {class: 'go'}, (nProps ? nProps + ' 个性质 · ' : (s.analysis ? '' : '模板默认 · ')) + '查看 SAR 分析 →'),
      {href: studyUrl(id, 'analysis'), text: true}),
    stat('具名确认（L3）', String(s.confirmed), h('span', {class: 'go'}, (s.pending ? s.pending + ' 条记录待复核' : '没有待复核记录') + ' · 去复核队列 →'),
      {href: '/review', warn: s.pending > 0}));

  const years = [];
  if (dates.length) for (let y = +dates[0].slice(0, 4); y <= +dates[dates.length - 1].slice(0, 4); y++) years.push(y);
  const timeline = h('div', {class: 'card span-2'},
    h('div', {class: 'card-head'}, h('h2', {}, '家族时间线'), h('span', {class: 'hint'}, '按来源记载的优先权日排列 · 同族公开已合并'),
      h('span', {class: 'grow'}), h('a', {href: studyUrl(id, 'timeline'), class: 'small'}, '展开时间线与程序 →')),
    years.length > 1 ? h('div', {class: 'years'}, years.map(y => h('span', {}, String(y)))) : null,
    data.families.length ? h('div', {class: 'fam-row', style: `grid-template-columns:repeat(${Math.max(data.families.length, 1)},minmax(0,1fr))`},
      data.families.map((f, i) => h('div', {class: 'fam', dataset: {publication: f.id}},
        h('div', {class: 'when'}, h('span', {class: 'dot' + (i ? ' late' : '')}), h('b', {}, f.priority_date || '日期未记载'),
          h('span', {}, '家族 ' + (f.family_id || '未记载')), (f.cites || []).length ? h('span', {class: 'badge'}, '引用较早家族') : null),
        h('span', {class: 'pub'}, f.id),
        h('span', {class: 'muted small'}, (f.examples.length ? f.examples.map(e => e.label.replace('Example ', '')).join('、') + ' 号实施例' : '未整理实施例') + ' · ' + f.measurements + ' 项测量'),
        h('div', {class: 'mols'}, f.examples.slice(0, 3).map(e => mol(e.smiles, 's', f.id + ' ' + e.label + ' 结构'))))))
      : h('p', {class: 'empty'}, '本调研没有专利文档。'),
    data.papers.length ? h('p', {class: 'muted small', style: 'margin:14px 0 0'}, '另含论文：' + data.papers.map(p => (p.title || p.id) + '（' + (p.citation || p.year || '') + '）').join('、')) : null);

  const next = h('div', {class: 'card col', id: 'next'}, h('h2', {}, '下一步'),
    h('p', {class: 'status', id: 'next-status'}, '正在计算补测建议…'),
    s.pending ? h('a', {class: 'next-item warn', href: '/review'}, h('b', {}, s.pending + ' 条记录等待独立复核'), h('span', {}, '具名确认后才能作为对外结论引用')) : null,
    h('div', {class: 'next-item muted-item'}, h('b', {}, '外部来源在线核对'), h('span', {}, 'ChEMBL、SureChEMBL、PubChem 的在线结果核对前，新专利候选可能不全；记录见 docs/baseline.md')));

  const rels = h('section', {class: 'card'},
    h('div', {class: 'card-head'}, h('h2', {}, '跨家族关系'), h('span', {class: 'hint'}, '事实、研究假设与证据缺口分栏；只有你可以把假设写成结论')),
    data.relations.length ? data.relations.map(r => h('div', {class: 'col'},
      h('b', {class: 'small muted'}, r.from + ' → ' + r.to + ' · ' + r.label),
      h('div', {class: 'trio'},
        h('div', {class: 'facts'}, h('span', {class: 'h'}, '可核查事实 · ' + r.facts.length), r.facts.map(x => h('span', {}, x))),
        h('div', {class: 'hyp'}, h('span', {class: 'h'}, '研究假设 · ' + (r.hypothesis ? 1 : 0)), r.hypothesis ? h('span', {}, r.hypothesis) : h('span', {class: 'muted'}, '无'),
          h('span', {class: 'muted small'}, '未署名 · 等待你的判断（在对照页署名后写入报告）')),
        h('div', {class: 'gaps'}, h('span', {class: 'h'}, '证据缺口 · ' + r.gaps.length), r.gaps.map(x => h('span', {}, x))))))
      : h('p', {class: 'empty'}, '本调研中没有已整理的跨文档关系。关系须有可自动核对的展示条件，不会由工具推断。'),
    data.notes.length ? h('div', {class: 'col', style: 'margin-top:16px'}, h('b', {}, '已署名的判断'),
      data.notes.map(n => h('div', {class: 'next-item'}, h('b', {}, n.text), h('span', {}, n.author + ' · ' + n.created_at.slice(0, 10) + ' · ' + n.context)))) : null);

  fill(main, stats, h('section', {class: 'grid-3'}, timeline, next), rels);
  followup();
}

async function followup() {
  const st = $('#next-status');
  try {
    const {request} = await analysisRequest(s);
    const r = await api('/api/project-sar', request);
    const box = $('#next');
    const first = r.followups.items[0];
    const items = [];
    if (first) items.push(h('a', {class: 'next-item', href: studyUrl(id, 'analysis')},
      h('b', {}, '补测建议第 1 位：' + first.compound.split(' ·')[0] + ' 的' + first.property),
      h('span', {}, first.reason + '；涉及 ' + first.pairs.map(p => p.pair).join('、'))));
    if (r.pending_defaults.length) items.push(h('a', {class: 'next-item warn', href: studyUrl(id, 'analysis')},
      h('b', {}, '确认默认噪声阈值'), h('span', {}, r.pending_defaults.join('、') + ' 使用默认阈值，均标为待化学家确认')));
    st.replaceWith(...(items.length ? items : [h('div', {class: 'next-item muted-item'}, h('b', {}, '没有单侧缺失的目标测量'), h('span', {}, '当前目标下没有补测建议'))]));
    void box;
  } catch (e) {
    st.textContent = '补测建议未生成：' + e.message;
    st.classList.add('error');
  }
}
