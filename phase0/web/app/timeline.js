import {h, $, api, loadStudy, mol, setStatus, studyUrl, fill} from '/app/core.js';

const {id, data, main} = await loadStudy('timeline', 'timeline');
const SIGNAL = {assignee: '申请人', assignees: '申请人', inventor: '发明人', inventors: '发明人', scaffold: '骨架', scaffolds: '骨架', target: '靶点', targets: '靶点'};
let showEdges = true;

if (!data.families.length) {
  fill(main, h('div', {class: 'card'}, h('h2', {}, '本调研没有专利家族'),
    h('p', {class: 'muted'}, '时间线按专利优先权日排列。加入专利后再查看。' + (data.papers.length ? '当前只有论文：' + data.papers.map(p => p.title).join('、') + '。' : ''))));
} else draw();

function year(d) { return d ? +d.slice(0, 4) + (+d.slice(5, 7) - 1) / 12 + (+d.slice(8, 10) - 1) / 365 : null; }

function track() {
  const ys = data.families.map(f => year(f.priority_date)).filter(v => v !== null);
  const start = Math.floor(Math.min(...ys)), end = Math.floor(Math.max(...ys)) + 2;
  const years = []; for (let y = start; y < end; y++) years.push(y);
  const box = h('div', {class: 'tl-track', id: 'track'});
  const axis = h('div', {class: 'tl-axis'}, years.map(y => h('span', {style: `left:${(y - start) / (end - start) * 100}%`}, String(y))));
  const cards = data.families.map((f, i) => {
    const pos = f.priority_date ? (year(f.priority_date) - start) / (end - start) * 100 : 0;
    return h('div', {class: 'fam tl-card', dataset: {publication: f.id}, style: `left:min(${pos}%, calc(100% - 320px))`},
      h('div', {class: 'when'}, h('span', {class: 'dot' + (i ? ' late' : '')}), h('b', {}, f.priority_date || '日期未记载'), h('span', {}, '家族 ' + (f.family_id || '未记载'))),
      h('span', {class: 'pub'}, f.id),
      h('span', {class: 'muted small'}, f.examples.length ? f.examples.map(e => e.label).join('、') : '未整理实施例'),
      h('div', {class: 'mols'}, f.examples.map(e => mol(e.smiles, 's', f.id + ' ' + e.label))));
  });
  box.append(axis, ...cards);
  return box;
}

function drawEdges() {
  const box = $('#track'); if (!box) return;
  box.querySelectorAll('svg.tl-edges, .tl-edge-label').forEach(n => n.remove());
  if (!showEdges) return;
  const cites = data.edges.filter(e => e.type === 'cites');
  if (!cites.length) return;
  const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
  const W = box.clientWidth, H = box.clientHeight;
  svg.setAttribute('class', 'tl-edges'); svg.setAttribute('width', W); svg.setAttribute('height', H); svg.setAttribute('aria-hidden', 'true');
  const defs = document.createElementNS(ns, 'defs'), marker = document.createElementNS(ns, 'marker');
  Object.entries({id: 'cite', viewBox: '0 0 10 10', refX: '9', refY: '5', markerWidth: '7', markerHeight: '7', orient: 'auto-start-reverse'}).forEach(([k, v]) => marker.setAttribute(k, v));
  const tip = document.createElementNS(ns, 'path'); tip.setAttribute('d', 'M0 0L10 5L0 10z'); tip.setAttribute('fill', '#0A5F55');
  marker.append(tip); defs.append(marker); svg.append(defs);
  const at = pub => box.querySelector(`.tl-card[data-publication="${CSS.escape(pub)}"]`);
  for (const e of cites) {
    const from = at(e.to), to = at(e.from); // the later patent cites the earlier one
    if (!from || !to) continue;
    const x1 = from.offsetLeft + from.offsetWidth / 2, x2 = to.offsetLeft + to.offsetWidth / 2;
    const y1 = from.offsetTop + from.offsetHeight, y2 = to.offsetTop + to.offsetHeight, y = Math.max(y1, y2) + 40;
    const p = document.createElementNS(ns, 'path');
    p.setAttribute('d', `M ${x1} ${y1} C ${x1} ${y}, ${x2} ${y}, ${x2} ${y2 + 4}`);
    Object.entries({fill: 'none', stroke: '#0A5F55', 'stroke-width': '1.5', 'stroke-dasharray': '5 4', 'marker-end': 'url(#cite)'}).forEach(([k, v]) => p.setAttribute(k, v));
    svg.append(p);
    box.append(h('span', {class: 'tl-edge-label', style: `left:${(x1 + x2) / 2}px;top:${y - 6}px`}, e.label));
  }
  box.append(svg);
}

function alignment() {
  const al = data.alignment;
  if (!al) return h('div', {class: 'card'}, h('h2', {}, 'R 基团对齐'), h('p', {class: 'empty'}, '没有可对齐的已整理实施例。'));
  if (al.error) return h('div', {class: 'card'}, h('h2', {}, 'R 基团对齐'), h('p', {class: 'note error'}, al.error + '；不会用默认位点替代。'));
  const labels = al.labels || [];
  let prev = null;
  const rows = al.rows.map(r => {
    const cid = r.publication + ':example:' + r.example;
    const same = prev && prev.publication === r.publication;
    const cell = data.cell[cid];
    const tr = h('tr', {dataset: {compound: cid}},
      h('td', {class: 'mono'}, (data.families.findIndex(f => f.id === r.publication) === 0 ? 'A' : 'B') + ' · ' + r.label.replace('Example ', 'Ex ')),
      r.matched ? labels.map(l => {
        const v = (r.fragments || {})[l];
        const changed = same && prev.matched && (prev.fragments || {})[l] !== v;
        return h('td', {class: 'mono', style: changed ? 'background:#FBE3C0' : null, title: changed ? '与同家族上一行不同' : null},
          v ? v : '—', (r.bridged_labels || []).includes(l) ? h('span', {class: 'badge warn', style: 'margin-left:4px'}, '成环') : null);
      }) : h('td', {colspan: labels.length, class: 'muted'}, '未能对齐到共同锚点'),
      h('td', {class: 'mono'}, cell ? cell.text + (cell.unit ? ' ' + cell.unit : '') : '—'));
    prev = r;
    return tr;
  });
  const a = al.anchor || {};
  return h('div', {class: 'card col'},
    h('div', {class: 'card-head', style: 'margin:0'}, h('h2', {}, 'R 基团对齐'),
      h('span', {class: 'hint'}, '共同锚点 ' + (a.atoms || '?') + ' 个原子 · 覆盖 ' + al.rows.filter(r => r.matched).length + '/' + al.rows.length +
        (a.coverage_min != null ? ' · 锚点占分子 ' + Math.round(a.coverage_min * 100) + '–' + Math.round(a.coverage_max * 100) + '%' : '')),
      al.status === 'provisional_alignment' ? h('span', {class: 'badge warn'}, '暂定对齐') : null),
    a.labelled_core ? h('div', {class: 'smiles'}, '锚点：' + a.labelled_core) : null,
    h('div', {class: 'table-wrap'}, h('table', {class: 'data', id: 'alignment'},
      h('tr', {}, h('th', {}, '实施例'), labels.map(l => h('th', {}, l)), h('th', {}, 'WT 细胞 IC50')), rows)),
    h('p', {class: 'muted small', style: 'margin:0'}, '黄色标出与同家族上一行不同的位点。R 标签只在本次对齐中有效，不等于专利中的 R 编号；跨家族差异只描述已整理样本，不推断优化因果。'),
    al.reason ? h('p', {class: 'note small', style: 'margin:0'}, al.reason) : null);
}

function grouping() {
  const out = h('div', {class: 'col', id: 'grouping-result'});
  const btn = h('button', {class: 'btn', type: 'button', id: 'run-programs'}, '读取公开页面并计算分组（联网）');
  const st = h('p', {class: 'status small', role: 'status'});
  btn.onclick = async () => {
    btn.disabled = true;
    try {
      const pubs = data.families.map(f => f.id);
      for (const [i, p] of pubs.entries()) {
        setStatus(st, '正在读取 ' + p + '（' + (i + 1) + '/' + pubs.length + '）…');
        await api('/api/patent?id=' + encodeURIComponent(p));
      }
      setStatus(st, '正在计算分组信号…');
      const r = await api('/api/programs', {publications: pubs});
      setStatus(st, '');
      const cards = r.pairs.map(p => h('div', {class: 'col', style: 'gap:8px'},
        h('div', {class: 'next-item' + (p.eligible ? '' : ' warn')}, h('b', {}, (p.eligible ? '可归为候选程序' : '未归为一组') + ' · 分数 ' + p.score),
          h('span', {}, p.reasons.length ? p.reasons.join('；') : '满足申请人与至少两个信号')),
        Object.entries(p.signals).map(([k, v]) => h('div', {class: 'next-item muted-item'}, h('b', {}, (SIGNAL[k] || k) + (v === null ? ' · 缺失' : ' · ' + v)),
          h('span', {}, (p.shared[k] || p.shared[k + 's'] || []).slice(0, 6).join('、') || (v === null ? '来源中没有该信息，不重新归一化' : '无共同项'))))));
      fill(out, ...cards, h('p', {class: 'muted small'}, r.notice));
    } catch (e) { setStatus(st, e.message + ' 离线时无法读取公开页面；时间轴与对齐仍基于已整理数据。', true); }
    finally { btn.disabled = false; }
  };
  return h('div', {class: 'card col'}, h('h2', {}, '为什么归为一组'),
    h('div', {class: 'next-item'}, h('b', {}, '申请人一致 · 必要条件'), h('span', {}, '申请人规范化后必须一致；不会自动推定子公司归属')),
    h('div', {class: 'next-item muted-item'}, h('b', {}, '发明人 · 骨架 · 靶点'), h('span', {}, '三项信号各自的分数与依据；需要读取专利公开页面后计算')),
    h('div', {class: 'next-item muted-item'}, h('b', {}, '优先权日期'), h('span', {}, '只用于排序，不参与打分')),
    btn, st, out,
    h('a', {class: 'small', href: '/classic'}, '拆分分组 / 高级设置（经典界面）'));
}

function draw() {
  const toggle = h('label', {class: 'check small'}, h('input', {type: 'checkbox', checked: showEdges, id: 'show-edges',
    onchange: e => { showEdges = e.target.checked; drawEdges(); }}), '显示引用关系');
  const relation = data.edges.find(e => e.type !== 'cites');
  fill(main, 
    h('section', {class: 'card'},
      h('div', {class: 'card-head'}, h('h2', {}, '专利家族时间轴'),
        h('span', {class: 'hint'}, data.families.length + ' 个家族 · 按优先权日排列' + (relation ? ' · 已整理关系：' + relation.label : '')),
        h('span', {class: 'grow'}), toggle),
      track()),
    h('section', {class: 'grid-side'}, alignment(), grouping()),
    data.papers.length ? h('p', {class: 'muted small'}, '论文不在专利时间轴上：' + data.papers.map(p => p.title + '（' + p.citation + '）').join('、') + '。论文化合物可在 ', h('a', {href: studyUrl(id, 'evidence')}, '证据'), ' 页查看。') : null);
  requestAnimationFrame(drawEdges);
}
window.addEventListener('resize', () => requestAnimationFrame(drawEdges));
