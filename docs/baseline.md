# 回归基线（迭代 I0）

后续所有改动以此为回归基线：测试数只增不减，离线验证输出不变（规则或数据有意变更时需同步更新本文件并说明原因）。

## 环境

| 项 | 值 |
|---|---|
| 记录日期 | 2026-09-27 |
| 基线提交 | `9a51715` |
| Python | 3.11.15（CI 覆盖 3.10 / 3.12） |
| 依赖 | 按 `requirements.txt` 固定版本：rdkit 2026.3.6、PyYAML 6.0.3、pytest 9.1.1、beautifulsoup4 4.14.3 |
| 安装方式 | 虚拟环境 `.venv`（系统自带的 PyYAML 由 Debian 安装，直接 `pip install` 会冲突） |

## 结果

### 自动化测试

`python -m pytest -q`：**216 passed**（约 7 秒），测试不需要联网。

> 更新：加入评测框架（`test_eval_metrics`，22 项）后为 **238 passed**。
>
> 更新：加入类型化台账（`test_ledger_schema`，22 项；新增依赖 pydantic 2.13.5）后为 **260 passed**。离线验证输出不变。
>
> 更新：跨专利关系改为数据记录（`test_lineage` 由 11 项增至 25 项）后为 **274 passed**。`/api/lineage` 在原有字段上与改动前逐项一致。
>
> 更新：加入质谱分子量校验（`test_mass_check`，13 项）后为 **287 passed**。`python -m phase0.sar.mass_check`：6 个已整理实施例的 LCMS 报告值与转录结构全部一致（整数报告值比单同位素 [M+H]⁺ 低 0.15–0.19 Da，属于低分辨率名义质量）。
>
> 更新：加入 SQLite 台账存储与统一读取入口（`test_ledger_store`，12 项）后为 **299 passed**。`/api/evidence` 与配对分析改为经类型化台账读取，输出与原实现逐项一致。
>
> 更新：加入“加入台账”入口（`test_ledger_intake`，11 项）后为 **310 passed**。另用 Playwright + 本机 Chromium 离线驱动真实页面（专利夹具页面、模拟 ChEMBL 响应）点击两个入口按钮：写入、重复点击不新增、拒绝原因展示均符合预期，无 JS 错误。该浏览器脚本未纳入仓库测试（Playwright 未加入依赖）。
>
> 更新：加入 agent 工具层与 MCP 服务（`test_tools_core` 12 项、`test_mcp_server` 7 项；新增 `requirements-agent.txt`，mcp==2.2.0，CI 改为安装该文件）后为 **329 passed**。MCP 测试包括以子进程方式经 stdio 启动服务。
>
> 更新：加入分级抽取控制逻辑（`test_extract_cascade`，13 项）后为 **342 passed**。用金标准 v0 构造的桩抽取器端到端验证：抽取正确时评测全部正确；单一来源的数值错误计入错误但为已标记；两个来源给出同一错误同分异构体时被评为 high，评测计为 1 个未标记错误（已知边界）。
>
> 更新：加入两个离线演示命令 `phase0.tools.demo`、`phase0.extract.demo` 及其冒烟测试（`test_demos`，2 项）后为 **344 passed**。
>
> 更新（界面重构，2026-10-03）：按 `重构原型/` 设计稿重做前端（`phase0/web/app/`），新增调研模块 `phase0/sar/study.py` 与对应接口；原页面移至 `/classic`（`/evidence`、`/project` 仍可访问），原有浏览器测试改为打开 `/classic` 下的页面，断言不变。新增 `test_study.py` 24 项（调研视图、来源等级、跨文档不算倍数、具名复核、报告、页面与接口）、`test_workbench_browser.py` 8 项（首页识别、证据表到跨家族对照、同一论文 B/A、SAR 分析与署名进入报告、保存映射、复核队列具名确认、结构检索、PDF 本机哈希）。合计 36 个模块、**491 passed**（含 19 项浏览器测试，未安装 Playwright 或浏览器时这 19 项跳过）。离线验证输出不变。
>
> 更新（I2.8 A、C）：先导结构锚点（标准化、台账匹配，不产生数值、不入台账）；必须保留的片段约束（破坏者归“超出约束范围”，仍显示、不作候选、不进补测排序）；合成限制作为标签；用户填写的当前测量值按文本隔离。新增 `test_lead_constraints.py` 19 项、浏览器测试 11 项。合计 **459 passed**。
>
> 更新（I2.7 第二段）：检索覆盖报告；SureChEMBL 命中作为专利结构入台账（服务端核对）；范围覆盖、分类、补测建议、合成可行性待评估项、Markdown 讨论材料；MCP 工具 17 个（`test_project_sar` 37 项、`test_structure_search` 17 项、`test_surechembl` 8 项、`test_browser` 10 项）。合计 **439 passed**。
>
> 更新（I2.7 第一段补齐）：自定义性质、目标区间方向、按位点汇总（`test_project_sar` 增至 32 项；`test_browser` 9 项）。合计 **431 passed**。台账上 18 个分子对归为 15 种替换、5 个位点。
>
> 更新（I2.7 第一段）：项目目标与多性质 SAR（`test_project_sar`，19 项：MMP 配对含氢替换、实验映射建议、目标校验、验收用例 6f → 6e、阈值、跨文档实验不计为缺失、不可比规则、证据等级、无综合分数、接口）；浏览器测试增加 `/project` 完整路径（`test_browser` 8 项）；`--public-host` 转发主机测试（`test_serve_hosts`，7 项，此前随 Codespaces 支持加入）。合计 **417 passed**。台账上自动配对得到 18 个分子对、15 种替换。
>
> 更新（I2.6 · PubChem）：接入 PubChem 交叉引用（`test_pubchem`，8 项：已整理专利排前、截断与计数、“查不到”缓存可重放、服务繁忙不缓存、只发送标准 InChIKey、断网重放）；浏览器测试增加从检索结构到 PubChem 关联专利再到加载专利的路径（`test_browser` 7 项）；MCP 工具 15 个；在线核对增加一项。合计 **390 passed**。PubChem 在本环境不可达，接口未实连（见 `docs/pubchem-access.md`）。
>
> 更新（I2.6 · SureChEMBL）：接入 SureChEMBL 结构检索与化合物所在专利（`test_surechembl`，7 项，以按接口协议模拟的服务端驱动：异步任务、去重、缓存、错误信封、阈值过滤、断网重放）；浏览器测试增加 SureChEMBL 命中到加载专利的路径（`test_browser` 6 项）；MCP 工具 14 个；重放时同时拦截 SureChEMBL 网络。合计 **381 passed**。SureChEMBL 在本环境不可达，接口未实连（见 `docs/surechembl-access.md`）。
>
> 更新（I2.6）：结构检索（`test_structure_search`，16 项，含模拟 ChEMBL 响应与断网重放）；浏览器测试增加结构相似性检索（`test_browser` 5 项）；MCP 工具 13 个；在线核对脚本增加结构检索两项。合计 **373 passed**。同时修正测试夹具 `fixtures.py` 中手写的洛拉替尼 SMILES：氟原子画在了错误的环位置（区域异构体，分子式与分子量不变，原核对方法查不出），由精确结构检索发现；修正后与台账中 Example 2 / 8k 的 InChIKey 一致并加测试守护，其余测试结果不变。
>
> 更新（R1）：证据卡显示质谱校验结果（`test_mass_check` 由 13 项增至 18 项）；`.mcp.json.example` 与命令行一致性检查（`test_mcp_server` 增至 8 项）；在线路径核对脚本的模拟网络测试（`test_verify_online`，2 项）；浏览器测试入仓（`test_browser`，4 项，需 `requirements-browser.txt`，缺少时跳过）。合计 **356 passed**（未装 Playwright 时为 352 passed、4 skipped）。浏览器测试用本机 Chromium（`/opt/pw-browsers/chromium-1194`，经 `_executables()` 自动发现）运行。

| 测试模块 | 数量 |
|---|---:|
| test_discovery | 26 |
| test_features | 24 |
| test_evidence_pair | 16 |
| test_align | 16 |
| test_regressions | 15 |
| test_programs | 15 |
| test_pipeline | 15 |
| test_narrate | 15 |
| test_rules | 11 |
| test_lineage | 11 |
| test_measures | 10 |
| test_evidence_pipeline | 10 |
| test_patents | 9 |
| test_benchmark | 9 |
| test_patent_evidence | 6 |
| test_sar_workflow | 5 |
| test_evidence_ledger | 3 |

### 离线验证与页面生成

`python -m phase0.sar.demo`：

```
lorlatinib-development: 8 candidates; keyword proxy 1/3; human validation pending
osimertinib-independent: 2 candidates; keyword proxy 0/2; human validation pending
```

生成 `artifacts/sar-explorer.html`（约 1.5 MB）。与 README 记载一致。关键词命中是代理指标，不是真实召回率。

### 本机服务冒烟检查

`python -m phase0.sar.serve --port 8766`：

| 接口 | 结果 |
|---|---|
| `GET /api/health` | 200 |
| `GET /` | 200（新版工作台首页；2026-10-03 前为专利工作台） |
| `GET /search`、`/upload`、`/review` | 200（结构检索、从 PDF 开始、复核队列） |
| `GET /s/alk-pfizer/overview` 等 6 个页签 | 200（预置调研的概览、证据、SAR 分析、时间线与程序、对照、报告） |
| `GET /classic`、`/classic/evidence`、`/classic/project` | 200（原专利工作台、六步工作流、项目目标页；`/evidence`、`/project` 仍可访问） |
| `GET /api/studies`、`/api/study?id=alk-pfizer` | 200；预置调研 2 个专利家族、1 篇论文、35 条结构记录、271 条观测 |
| `GET /examples` | 200（离线文献页，需先运行 demo） |
| `GET /api/evidence` | 200；10 个输入包、320 条观测，与文档一致 |
| `GET /api/patent?id=WO2013132376A1` | 记录环境的网络策略拦截了 Google Patents；接口返回明确错误“专利来源连接失败或超时”，没有用样例替代，符合设计 |

### 抽取评测框架

`python -m phase0.eval.extraction_metrics --pred phase0/data/gold`（金标准自评，用于确认框架本身）：各项比例 100%，未标记错误、编造数值、可比性误判均为 0。金标准 v0 由现有证据包派生（6 个化合物、22 项测量、2 条未测记录），尚未独立复核。尚无真实抽取器的评测结果。

### 在线路径

核对脚本：`python -m phase0.tools.verify_online`（说明见 `phase0/tools/README.md`）。

| 日期 | 环境 | 结论 | 说明 |
|---|---|---|---|
| 2026-09-28 | 开发环境（代理拒绝 patents.google.com、www.ebi.ac.uk） | 失败（预期） | 两份专利、靶点检索、靶点测量均报连接失败；脚本如实记为失败，未用样例替代；重放记为“注意”（只说明失败可复现） |
| 待补 | 项目负责人可联网的本机 | — | 运行后把 `report.md` 的结论与各项结果填入此表 |

## 未覆盖

- 需要联网的在线路径（Google Patents、ChEMBL）未在可联网环境中端到端验证；核对脚本已就绪，待在本机运行。
- 浏览器测试只覆盖主路径（见 `test_browser.py`、`test_workbench_browser.py`）；其余交互演示前仍需人工走查。新版界面尚无化学家走查记录。
