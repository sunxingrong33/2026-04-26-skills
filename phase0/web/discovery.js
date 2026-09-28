/* Discovery UI uses textContent for all source-provided strings. */
let discoveryHistory=[];
const exampleSmiles='C[C@H]1Oc2cc(cnc2N)-c2c(nn(C)c2C#N)CN(C)C(=O)c2ccc(F)cc21';
function updateInputMode(){
  const mode=$('input-mode').value;
  $('publication').value='';
  $('publication').maxLength=mode==='smiles'?2000:mode==='target'?120:50;
  $('publication').setAttribute('aria-label',mode==='smiles'?'分子 SMILES':mode==='target'?'靶点名称':'专利公开号');
  $('publication').placeholder=mode==='smiles'?'粘贴完整 SMILES':mode==='target'?'例如 ALK、EGFR 或 CHEMBL4247':'例如 WO2013132376A1';
  $('submit').textContent=mode==='patent'?'检索专利':mode==='smiles'?'检索结构':'检索靶点';
  $('fill').textContent=mode==='patent'?'填入洛拉替尼专利号':mode==='smiles'?'填入洛拉替尼 SMILES':'填入 ALK';
  $('external-option').classList.toggle('hidden',mode!=='smiles');$('structure-options').classList.toggle('hidden',mode!=='smiles');$('surechembl-option').classList.toggle('hidden',mode!=='smiles');
  $('input-hint').textContent=mode==='smiles'?'默认只检索本地证据台账；勾选后同时查询 ChEMBL。':mode==='target'?'查询 ChEMBL；请按物种和靶点类型选择候选。':'输入完整公开号，例如 WO2013132376A1。';
}
$('input-mode').onchange=updateInputMode;
$('fill').onclick=()=>{$('publication').value=$('input-mode').value==='smiles'?exampleSmiles:$('input-mode').value==='target'?'ALK':'WO2013132376A1';$('publication').focus()};
$('lookup').onsubmit=e=>{e.preventDefault();const mode=$('input-mode').value;if(mode==='patent'){lookup($('publication').value);return}discoveryHistory=[];runDiscovery(mode==='smiles'?{mode,query:$('publication').value,external:$('external-search').checked,surechembl:$('surechembl-search').checked,...searchOptions()}:{mode,query:$('publication').value})};
function searchOptions(){const method=$('search-method').value,o={method,standardize:$('search-standardize').checked};if(method==='similarity')o.threshold=Number($('search-threshold').value);return o}
$('search-method').onchange=()=>$('threshold-option').classList.toggle('hidden',$('search-method').value!=='similarity');
const methodNames={exact:'精确',similarity:'相似性',substructure:'子结构'};
const roleNames={example:'实施例',intermediate:'中间体',reference:'对照',reagent:'试剂',unspecified:'未指定'},statusNames={proposed:'待确认',confirmed:'已确认',rejected:'已拒绝'};
function action(label,fn){const b=el('button',label);b.type='button';b.onclick=fn;return b}
async function proposeToLedger(payload,button){if(button)button.disabled=true;setStatus('正在写入台账（待确认）…');try{const r=await fetch('/api/ledger/propose',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const d=await r.json();if(!r.ok)throw new Error(d.error||'写入失败');const names={documents:'文档',compounds:'结构',assays:'实验',observations:'测量'};const added=Object.entries(d.added).map(([k,n])=>(names[k]||k)+' '+n).join('、')||'无新增';let text='已加入台账（待确认）：'+added+'。';const present=Object.entries(d.already_present_counts||{}).map(([k,n])=>(names[k]||k)+' '+n).join('、');if(present)text+=' 已在台账中：'+present+'。';if(d.refused.length)text+=' 未写入 '+d.refused.length+' 条：'+d.refused.slice(0,3).map(x=>x.id+' '+x.reason).join('；')+(d.refused.length>3?' …':'')+'。';setStatus(text+' '+d.notice)}catch(e){setStatus(e.message,true)}finally{if(button)button.disabled=false}}
function recordLink(type,id){return link(id||'来源缺失','https://www.ebi.ac.uk/chembl/api/data/'+type+'/'+encodeURIComponent(id||'')+'.json')}
async function runDiscovery(request,back=false){
  if(busy)return;
  busy=true;$('submit').disabled=true;$('input-mode').disabled=true;
  setStatus(request.surechembl?'正在查询 SureChEMBL（异步检索），可能需要一两分钟…':request.mode==='surechembl_documents'?'正在查询 SureChEMBL 专利…':request.mode==='smiles'&&!request.external?'正在本地核对结构…':'正在查询 ChEMBL，可能需要约 30 秒…');
  $('discovery').classList.remove('hidden');const box=clear('discovery-content');box.append(el('p','正在检索…'));
  try{
    const response=await fetch('/api/discover',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)});
    const result=await response.json();if(!response.ok)throw new Error(result.error);
    if(!back)discoveryHistory.push(request);else if(discoveryHistory.length)discoveryHistory[discoveryHistory.length-1]=request;
    drawDiscovery(result,request);setStatus(result.warning||'检索完成。',!!result.warning);
  }catch(e){box.replaceChildren(el('p',e.message,'error'),action('重试',()=>runDiscovery(request,back)));setStatus(e.message,true)}
  finally{busy=false;$('submit').disabled=false;$('input-mode').disabled=false}
}
function drawDiscovery(d,request){
  const box=clear('discovery-content');
  box.append(el('h2',d.mode==='surechembl_documents'?'SureChEMBL 专利 · '+d.id:d.mode==='documents'?'来源文档与专利候选':d.mode==='target'?'选择靶点':d.mode==='smiles'?'结构检索结果':'测量与来源 · '+d.id),el('p',d.notice,'note'));
  if(discoveryHistory.length>1)box.append(action('← 返回上一组结果',()=>{discoveryHistory.pop();runDiscovery(discoveryHistory[discoveryHistory.length-1],true)}));
  if(d.warning)box.append(el('p',d.warning,'error'));
  if(d.structure){
    const c=el('article',undefined,'card');const img=el('img');img.src=d.structure.svg;img.alt='输入 SMILES 的二维结构';
    c.append(img,el('p',d.structure.formula+' · '+d.structure.inchikey),el('p','MW '+d.structure.features.mw+' · cLogP '+d.structure.features.clogp),el('pre',d.structure.smiles));box.append(c);
    if(d.search){const q=d.search;box.append(el('p','检索方式：'+methodNames[q.method]+(q.method==='similarity'?'（Tanimoto ≥ '+q.threshold+'%，'+q.fingerprint+'）':'')+' · 标准化：'+q.steps.map(x=>x.note).join('；'),'muted'));if(d.original_structure)box.append(el('p','输入结构（标准化前）：','muted'),el('pre',d.original_structure.smiles))}
    if(d.local_matches){box.append(el('h3','本地专利证据卡匹配'));
    if(!d.local_matches.length)box.append(el('p','本地 6 张已整理证据卡中没有相同结构；这不代表没有相关专利。'));
    d.local_matches.forEach(m=>{const row=el('div',undefined,'panel');row.append(el('strong',m.publication+' / '+m.label),el('p','助手转录，待独立复核；打开专利时还需核对来源快照。'),link('原始结构图',m.structure_source.url),action('打开专利证据',async()=>{if(await lookup(m.publication)){showTab('evidence');$('evidence').scrollIntoView({behavior:'smooth'})}}));box.append(row)})}
    if(d.ledger_matches){const L=d.ledger_matches;box.append(el('h3','本地证据台账命中 · '+L.total+' 个（范围：'+L.scope+'）'));
      if(!L.total)box.append(el('p','本地台账中没有命中；未命中不代表不存在。'));
      if(L.truncated)box.append(el('p','仅显示前 '+L.rows.length+' 个，请提高阈值或细化片段。','error'));
      const list=el('div',undefined,'ledger-hits');L.rows.forEach(m=>{const row=el('div',undefined,'panel');row.dataset.compound=m.compound_id;row.append(el('strong',m.document_id+' / '+m.label),el('p',(m.similarity!==undefined?'相似度 '+m.similarity.toFixed(3)+' · ':m.match_atoms?'含查询片段 · ':'')+'角色：'+(roleNames[m.role]||m.role)+' · '+(statusNames[m.record_status]||m.record_status),'muted'),el('pre',m.smiles));if(m.structure_source&&m.structure_source.url)row.append(link('结构来源',m.structure_source.url));list.append(row)});box.append(list)}
    if(d.external_status==='not_requested')box.append(action(request.method&&request.method!=='exact'?'查询 ChEMBL（发送标准化后的 SMILES）':'查询 ChEMBL（发送标准 InChIKey）',()=>runDiscovery({...request,external:true})));
    else if(d.external_status==='failed')box.append(action('重试 ChEMBL',()=>runDiscovery(request,true)));
  }
  if(d.targets){
    box.append(el('p','当前显示 '+d.targets.length+' 项；来源总数：'+(d.total??'未知')));
    if(!d.targets.length)box.append(el('p','没有找到靶点。请尝试英文名称、基因符号或 ChEMBL ID。'));
    d.targets.forEach(t=>{const card=el('article',undefined,'panel');card.append(el('h3',t.pref_name||t.target_chembl_id),el('p',(t.organism||'物种未知')+' · '+(t.target_type||'类型未知')),recordLink('target',t.target_chembl_id),el('p','UniProt：'+(t.target_components||[]).map(c=>c.accession).filter(Boolean).join(' / ')),action('查看此靶点的测量与分子',()=>runDiscovery({mode:'activities',entity:'target',id:t.target_chembl_id,offset:0})));
      if(t.target_chembl_id==='CHEMBL4247')card.append(el('p','另有已整理的 Pfizer ALK 双家族案例；它不是该靶点的完整专利清单。'),action('打开已整理的 ALK 专利案例',()=>$('load-lineage').click()));
      box.append(card)});
  }
  if(d.molecules&&d.external_status==='ok'){
    const method=d.search?d.search.method:'exact';
    box.append(el('h3',method==='exact'?'ChEMBL 标准 InChIKey 命中':'ChEMBL '+methodNames[method]+'检索命中 · 共 '+(d.total??'未知')+' 个'));
    if(d.truncated)box.append(el('p','仅显示前 '+d.molecules.length+' 个（按 ChEMBL 返回顺序）；请提高阈值或细化片段。','error'));
    if(!d.molecules.length)box.append(el('p',method==='exact'?'ChEMBL 未返回该标准 InChIKey；可改用相似性或子结构检索。':'ChEMBL 未返回命中；未命中不代表不存在。'));
    d.molecules.forEach(m=>{const c=el('div',undefined,'panel chembl-hit');c.append(el('h3',m.pref_name||m.molecule_chembl_id),recordLink('molecule',m.molecule_chembl_id));
      if(m.chembl_similarity!=null)c.append(el('p','ChEMBL 相似度 '+m.chembl_similarity+'（ChEMBL 自身指纹算法）'));
      if(m.local_check){const k=m.local_check;c.append(el('p',k.note+(k.similarity!==undefined?'（本地 '+k.similarity.toFixed(3)+'）':''),k.status==='agrees'?'muted':'error'));c.dataset.localCheck=k.status}
      if(m.canonical_smiles)c.append(el('pre',m.canonical_smiles));
      c.append(action('查看此分子的测量与靶点',()=>runDiscovery({mode:'activities',entity:'molecule',id:m.molecule_chembl_id,offset:0})));box.append(c)});
  }
  if(d.surechembl)drawSureChEMBL(box,d,request);
  if(d.patents)drawSureChEMBLPatents(box,d);
  if(d.documents)drawDocuments(box,d,request);
  if(d.activities){
    box.append(el('p','第 '+(d.activities.length?d.offset+1:0)+'–'+(d.offset+d.activities.length)+' 条 / '+(d.total??'未知')+' 条；每页最多 20 条。'));
    if(!d.activities.length)box.append(el('p','未返回测量记录；不代表该对象无活性。'));
    if(d.activities.length)box.append(action('解析本页来源文档与专利候选',()=>runDiscovery({...request,mode:'documents'})));
    if(d.activities.length){const add=action('将本页测量加入台账（待确认）',()=>proposeToLedger({source:'activities',entity:d.entity,id:d.id,offset:d.offset,activity_ids:d.activities.map(a=>a.activity_id)},add));box.append(document.createTextNode(' '),add)}
    d.activities.forEach(a=>{const c=el('article',undefined,'panel');
      let raw=a.standard_value==null?(a.standard_text_value||'数值未报告'):(a.standard_relation||'限定符未报告')+' '+a.standard_value+' '+(a.standard_units||'单位未报告');
      if(a.standard_upper_value!=null)raw+='；区间上界 '+a.standard_upper_value+' '+(a.standard_units||'');
      c.append(el('h3',(a.molecule_pref_name||a.molecule_chembl_id)+' · '+(a.standard_type||'类型未知')+' '+raw),el('p',(a.target_pref_name||a.target_chembl_id)+' · '+(a.target_organism||'物种未知')),el('p',a.assay_description||'协议描述缺失'),recordLink('activity',String(a.activity_id)),document.createTextNode(' · '),recordLink('assay',a.assay_chembl_id),document.createTextNode(' · '),recordLink('document',a.document_chembl_id));
      if(a.data_validity_comment||a.activity_comment||a.potential_duplicate)c.append(el('p','质量/测量说明：'+[a.data_validity_comment,a.activity_comment,a.potential_duplicate?'可能重复':''].filter(Boolean).join('；'),'note'));
      if(a.canonical_smiles)c.append(el('pre',a.canonical_smiles),action('以此 SMILES 查找本地专利证据',()=>runDiscovery({mode:'smiles',query:a.canonical_smiles,external:false})));
      if(request.entity==='molecule'&&a.target_chembl_id)c.append(action('查看此靶点',()=>runDiscovery({mode:'target',query:a.target_chembl_id})));
      box.append(c)});
    const nav=el('div',undefined,'row');if(d.offset>0)nav.append(action('上一页',()=>runDiscovery({...request,offset:d.offset-20},true)));if(d.has_more&&d.offset<10000)nav.append(action('下一页',()=>runDiscovery({...request,offset:d.offset+20},true)));box.append(nav);
  }
  if(d.sources.length){const sources=el('details');sources.append(el('summary','查询来源与缓存快照'));d.sources.forEach(s=>sources.append(link('ChEMBL 原始响应',s.url),el('p','读取时间：'+s.retrieved_at+(s.cache_hit?'（缓存）':'')),el('p','SHA-256 '+s.sha256,'break')));box.append(sources)}
}

const patentCandidates=new Map();
function drawDocuments(box,d,request){
  box.append(el('p','范围：'+d.id+' 的第 '+(d.offset+1)+'–'+Math.min(d.offset+20,d.total??d.offset+20)+' 条测量，共 '+d.documents.length+' 份去重文档。'));
  if(!d.documents.length)box.append(el('p','本页没有可解析的文档关联；没有返回固定专利案例。'));
  let patents=0;
  d.documents.forEach(item=>{
    const r=item.document||{},card=el('article',undefined,'panel');
    const kind={patent:'专利',paper:'论文',other:'其他文档',unresolved:'待核实'}[item.kind];
    card.append(el('h3',kind+' · '+(r.title||item.id)),el('p',[r.authors,r.year,r.journal_full_title||r.journal].filter(Boolean).join(' · ')),recordLink('document',item.id));
    if(r.abstract){const details=el('details');details.append(el('summary','摘要'),el('p',r.abstract));card.append(details)}
    if(r.doi)card.append(el('br'),link('DOI 原文','https://doi.org/'+encodeURIComponent(r.doi)));
    if(r.pubmed_id&&/^\d+$/.test(String(r.pubmed_id)))card.append(el('br'),link('PubMed','https://pubmed.ncbi.nlm.nih.gov/'+r.pubmed_id+'/'));
    const trail={origin:discoveryHistory[0]?{mode:discoveryHistory[0].mode,query:discoveryHistory[0].query}:null,entity:d.entity,id:d.id,document:item.id,activities:item.activities,sources:d.sources};
    drawTrail(card,trail);
    if(item.publication){patents++;const prior=patentCandidates.get(item.publication)||{publication:item.publication,title:r.title,trails:[]};if(!prior.trails.some(t=>JSON.stringify(t)===JSON.stringify(trail)))prior.trails.push(trail);patentCandidates.set(item.publication,prior);card.append(el('p',item.publication+' · '+item.status),action('核实并加载专利',()=>openCandidate(item.publication)))}
    else card.append(el('p',item.kind==='paper'?'此记录为论文，未据此猜测专利号。':item.status));
    box.append(card);
  });
  if(!patents)box.append(el('p','本页未找到可加载的专利公开号；可返回测量列表翻页后继续解析。这不代表该分子或靶点没有专利。','note'));
  drawCandidates();
}
function drawTrail(box,t){
  const details=el('details');details.append(el('summary','为什么找到这份文档 / 专利'),el('p',(t.entity==='target'?'靶点 ':'分子 ')+t.id+' → '+t.activities.length+' 条本页测量 → '+t.document));
  if(t.origin?.query)details.append(el('p','入口：'+t.origin.mode),el('pre',t.origin.query));
  t.activities.forEach(a=>{const row=el('p');row.append(recordLink('molecule',a.molecule_chembl_id),document.createTextNode(' → '),recordLink('activity',String(a.activity_id)),document.createTextNode(' → '),recordLink('document',t.document));details.append(row)});
  details.append(el('p','这条链证明数据库的记录关联；不证明测量分子对应专利中的哪个实施例。'));
  t.sources.forEach(s=>details.append(link('关联来源快照',s.url),el('p',s.retrieved_at+' · SHA-256 '+s.sha256,'break')));box.append(details);
}
async function openCandidate(pid){
  if(busy)return;
  const candidate=patentCandidates.get(pid);
  if(!candidate)return;
  if(!docs.has(pid)&&!await lookup(pid))return;
  current=docs.get(pid);current.discovery_links=candidate.trails;
  $('workspace').classList.remove('hidden');render();showTab('sources');$('workspace').scrollIntoView({behavior:'smooth'});drawCandidates();
}
function drawDiscoveryOrigins(){
  if(!current?.discovery_links)return;
  const box=$('source-list');box.append(el('h3','检索到此专利的来源链'));
  current.discovery_links.forEach(t=>drawTrail(box,t));
}
function drawCandidates(){
  let section=$('patent-candidates');if(!section){section=el('section',undefined,'panel');section.id='patent-candidates';$('discovery').after(section)}
  section.replaceChildren(el('h2','发现的专利候选 · 按已核实家族归组'),el('p','仅积累已解析文档的候选；加载成功后按来源家族 ID 合并展示。同族公开号和各自来源链仍保留，家族未知时不猜测。'));
  const groups=new Map();patentCandidates.forEach(c=>{const loaded=docs.get(c.publication),key=loaded?.family_id?'家族 '+loaded.family_id:'家族待核实 · '+c.publication;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(c)});
  groups.forEach((items,key)=>{const group=el('article',undefined,'panel');group.append(el('h3',key));items.forEach(c=>{const loaded=docs.get(c.publication);group.append(el('p',c.publication+' · '+(loaded?.title||c.title||'标题待核实')+(loaded?' · 优先权 '+(loaded.priority_date||'未知'):'')),action(loaded?'查看已加载专利':'核实并加载专利',()=>openCandidate(c.publication)));c.trails.forEach(t=>drawTrail(group,t))});section.append(group)});
  if(!groups.size)section.append(el('p','尚未从已解析的来源文档中找到有效公开号。'));
}

function drawSureChEMBL(box,d,request){const S=d.surechembl;
  if(S.status==='not_requested'){box.append(action('查询 SureChEMBL（专利中的化学结构，发送标准化后的 SMILES）',()=>runDiscovery({...request,surechembl:true})));return}
  if(S.status==='failed'){box.append(el('p',S.warning,'error'),action('重试 SureChEMBL',()=>runDiscovery(request,true)));return}
  const sec=el('section',undefined,'surechembl');sec.append(el('h3','SureChEMBL 专利化学命中 · 共 '+S.total+' 个'),el('p',S.notice,'note'),el('p',S.attribution,'muted'));
  if(S.truncated)sec.append(el('p','仅取前 '+(S.rows.length+S.below_threshold)+' 个'+(S.capped?'（服务端上限 10000，实际命中可能更多）':'')+'；请提高阈值或细化片段。','error'));
  if(S.below_threshold)sec.append(el('p',S.below_threshold+' 个命中的 SureChEMBL 相似度低于所选阈值，未显示。','muted'));
  if(!S.rows.length)sec.append(el('p','SureChEMBL 未返回可显示的命中；未命中不代表不存在。'));
  S.rows.forEach(m=>{const c=el('div',undefined,'panel surechembl-hit');c.dataset.localCheck=m.local_check.status;c.append(el('h3',m.schembl_id+(m.name?' · '+m.name:'')),link('SureChEMBL 化合物页',m.url));
    if(m.surechembl_similarity!=null)c.append(el('p','SureChEMBL 相似度 '+m.surechembl_similarity+'（SureChEMBL 自身指纹算法）'));
    const k=m.local_check;c.append(el('p',k.note+(k.similarity!==undefined?'（本地 '+k.similarity.toFixed(3)+'）':''),k.status==='agrees'?'muted':'error'));
    if(m.smiles)c.append(el('pre',m.smiles));c.append(action('查看含此化合物的专利',()=>runDiscovery({mode:'surechembl_documents',id:m.schembl_id})));sec.append(c)});
  box.append(sec)}
function drawSureChEMBLPatents(box,d){box.append(el('p','共 '+d.total+' 份专利'+(d.truncated?'；仅显示前 '+d.patents.length+' 份':'')+'。'));
  if(!d.patents.length)box.append(el('p','SureChEMBL 未返回专利；未命中不代表不存在。'));
  d.patents.forEach(p=>{const c=el('div',undefined,'panel surechembl-patent');c.append(el('h3',p.doc_id),el('p',[p.title||'标题缺失',p.publication_date||'日期缺失',p.assignee||'申请人缺失'].join(' · ')),link('SureChEMBL 专利页',p.url));
    if(p.publication)c.append(action('核实并加载专利 '+p.publication,async()=>{if(await lookup(p.publication)){showTab('evidence');$('evidence').scrollIntoView({behavior:'smooth'})}}));else c.append(el('p','编号无法规范化为公开号，未提供加载。','muted'));box.append(c)})}
