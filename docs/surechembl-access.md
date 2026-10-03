# SureChEMBL 访问方式与许可核实记录

记录日期：2026-09-28 · 用途：迭代 I2.6 结构检索（专利部分）；同时供 I3b（L1 结构化来源）参考。

## 结论

| 项 | 结论 | 依据 | 核实程度 |
|---|---|---|---|
| 是否可用 | 可用：公开 REST API，无需账号或 API key | 第三方客户端文档与源码（2026-09-08 实测记录）；SureChEMBL 文档目录含 API 页 | 间接；**本项目尚未实连** |
| 数据许可 | CC BY 4.0（署名即可，允许商用与再分发） | SureChEMBL 官方 FAQ（经检索摘要）；第三方客户端 README | 官方页面本环境无法打开，摘要与第三方记录一致 |
| 署名要求 | 注明 SureChEMBL / surechembl.org，保留 SCHEMBL 编号 | CC BY 4.0 条款；第三方客户端 README | 同上 |
| 其他条款 | 服务受 EMBL-EBI 网站使用条款约束；对他人提交的数据，EMBL-EBI 不额外加限 | EMBL-EBI Terms of Use | 经检索摘要 |
| 速率限制 | **无公开配额**，响应中也没有限流头；共享服务，需自律 | 第三方客户端实测记录 | 间接 |
| 结构检索能力 | 相似性（Tanimoto，哈希指纹）、子结构（SMILES，文档称也支持 SMARTS）、identical、connectivity | SureChEMBL 文档（Structure search type）；第三方客户端 | 间接 |

**决定**：接入。许可允许本项目使用与展示；以“待在线实测”状态发布，由 `python -m phase0.tools.verify_online` 在可联网环境首次实连验证。

## 接口要点（实现依据）

基址 `https://www.surechembl.org/api`。以下行为不在官方 OpenAPI 说明中，来自第三方客户端 scigantic-surechembl（MIT-0，提交 `014b53f`，作者注明 2026-09-08 实测）：

- 所有 JSON 响应都是信封 `{status, data, error_message}`，`status` 不是 `OK` 即为错误；不同接口对“未命中”的表示不一致。
- 结构检索是**异步任务**：
  1. `POST /search/structure`，请求体必须包成 `{"StructureSearchRequest": {"struct": <SMILES>, "structSearchType": <mode>}}`，返回任务哈希；
  2. `GET /search/{hash}/status`，完成时 `message` 为 “Searching finished.” 并带 `resultCount`；失败时消息含 “error”；
  3. `GET /search/{hash}/results?page=&max_results=` 分页取结果。
- 每次检索服务端**上限 10,000 个命中**；请求里的 `maxResults` 被忽略。
- 相似性命中按服务端顺序返回，**不是按分数排序**；超出末页的页会重复上一页。
- 带立体键 `/` `\` 的 SMILES 不能放进 URL 路径（本项目用 POST JSON，不受影响）。
- 化合物所在专利：`POST /search/documents_for_structures?chemicalIds=<id>&page=&itemsPerPage=`，返回 `results.documents`（`docId` 形如 `WO-2013132376-A1`）与 `total_hits`。
- 第三方记录的事故：单原子或环己烷这类过宽的子结构查询会长时间卡住，并导致此后约一小时内所有人的子结构检索失败。

## 本项目的使用约束

| 约束 | 实现 |
|---|---|
| 不发过宽查询 | 子结构查询至少 6 个重原子（`structure_search.query`，高于第三方客户端的 5 个） |
| 一次只跑一个任务 | 服务端工作锁（`serve.py` 的 `work_lock`）；工具层同步调用 |
| 轮询退避 | 0.5 s 起翻倍，最长 5 s；120 s 超时 |
| 只取首页 | 每次最多 20 个命中、20 份专利；页面写明总数与是否截断，达到 10,000 上限时单独提示 |
| 缓存与重放 | 按查询缓存组装后的结果（不含任务哈希），记录 SHA-256 与读取时间；重放时禁止联网（`tools/core.py` 的 `NETWORK`） |
| 失败不伪装 | 连接失败、任务失败、信封错误都如实提示，并保留其他来源结果；失败不写缓存 |
| 署名 | 页面与工具结果附“数据来自 SureChEMBL（EMBL-EBI，CC BY 4.0）”，保留 SCHEMBL 编号与原页面链接 |

## 结果的含义（写在页面与工具说明中）

- SureChEMBL 的化合物是从专利文本和图像中**自动提取**的：命中只说明该结构出现在专利中，不说明它是实施例、被权利要求覆盖或经过测试，提取本身也可能有误。
- 专利编号经规范化后提供“核实并加载专利”，加载原始专利页面后才进入现有的证据流程。
- 界面位置（2026-10-03 起）：新版工作台的结构检索结果页 `/search`勾选 SureChEMBL 来源后，命中行的“查看含此化合物的专利”；专利编号链接到经典界面 `/classic?mode=patent&q=<编号>` 完成核实与加载。经典界面专利工作台中的原入口不变。
- 相似度使用 SureChEMBL 自身的指纹算法；本项目对每个命中用 RDKit 复核，复核不一致的标红保留。SureChEMBL 自身的相似度下限未公开，本项目按所选阈值再过滤，并显示被过滤的数量。

## 未核实 / 待办

- 本开发环境的网络策略拒绝访问 surechembl.org 与官方文档站（chembl.gitbook.io），以上接口行为**未经本项目实连验证**。首次在可联网环境运行 `verify_online` 后，把结果补入本文件与 `docs/baseline.md`；如接口与上述不符，按实际修正 `phase0/sar/surechembl.py`。
- 相似性检索是否接受阈值参数未核实（当前不传，按返回分数在本地过滤）。
- 子结构检索是否接受 SMARTS 未核实（本项目只发 SMILES）。
- 批量数据（parquet 发布版）可用于全库扫描，暂不需要。

## 来源

- SureChEMBL 文档：API、Structure search type、FAQ（chembl.gitbook.io/surechembl，本环境无法直接打开，经检索摘要）
- EMBL-EBI Terms of Use：https://www.ebi.ac.uk/about/terms-of-use/
- EMBL-EBI 数据资源许可说明：https://www.ebi.ac.uk/licencing
- scigantic-surechembl（MIT-0）：https://github.com/Scigantic/scigantic-surechembl ；PyPI：https://pypi.org/project/scigantic-surechembl/
- Papadatos G. et al. SureChEMBL: a large-scale, chemically annotated patent document database. Nucleic Acids Research 44(D1): D1220 (2016)
