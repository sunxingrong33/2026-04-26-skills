# Phase 0 操作说明

项目状态与科学边界见[根目录说明](../README.md)。以下命令从仓库根目录执行。

## 专利号输入工作台

```sh
python -m pip install -r requirements.txt
python -m phase0.sar.serve
```

打开 http://127.0.0.1:8766/ 。输入框可选择专利号、SMILES 或靶点。支持完整公开号、同族和引用专利继续检索、优先权日期并列展示、实施例文本定位、索引结构筛选及 A/B 结构差异计算。来源失败时显示错误，不返回样例结果。

新来源需要联网，已检索的原始 HTML 与哈希保存在 `artifacts/patent-cache/`。服务只在本机运行。结构索引上限 120 项，超过时明确提示；无可解析结构或实施例时显示缺失，不构造对应关系。结构、实施例、活性与研发意图之间的自动映射仍待完成。

## 离线演示

```sh
python -m phase0.sar.demo
```

先运行冻结规则验证，再生成 `artifacts/sar-explorer.html`。已提交的数据足够生成页面，不需联网。

## 数据导入

```sh
python -m phase0.sar.ingest --plan phase0/plans/lorlatinib.json --out phase0/data/curated/lorlatinib
python -m phase0.sar.ingest --plan phase0/plans/osimertinib.json --out phase0/data/curated/osimertinib
```

只读取 ChEMBL 公开 API，按文献查询；缓存位于 `artifacts/chembl-cache`。已有缓存优先复用；`--offline` 禁止网络且缺少响应时失败。重新读取上游时使用新的 `--cache` 目录。`sources.json` 保存响应 SHA-256、读取时间与 URL，原始缓存未提交到 Git。

`plans/*.json` 明确指定论文分子编号、系列分组和 assay 标签。不会从编号推断历史顺序。原始测量含限定符，保存在 `observations.csv`；限定值不参与精确中位数。结构的 chembl 标记表示可追溯，不表示原文人工核实完成。

## CLI 与可选叙述层

```sh
python -m phase0.sar.cli --data phase0/data/curated/lorlatinib --program lorlatinib-literature --dry-run
python -m pip install -r requirements-llm.txt
python -m phase0.sar.cli --data phase0/data/curated/lorlatinib --program lorlatinib-literature --model YOUR_AVAILABLE_MODEL_ID
```

真实模型调用还需要 `ANTHROPIC_API_KEY`；模型也可通过 `ANTHROPIC_MODEL` 设置。不内置猜测的模型名。未设置密钥会转为 dry-run。输出到 `phase0/out/`，包括 FACTS 文本/JSON、审计结果和通过格式审计的 narrative JSON。

CLI 相邻“代”是计划的分组对照，不是历史先后。页面另外提供固定分子对，避免组中位数替代具体分子差异。

## 验证

```sh
python -m pytest -q
python -m phase0.sar.validate
python -m phase0.sar.report
```

`validation_baseline.json` 固定规则哈希，`validation_cases.json` 声明开发/留出范围和原始摘要中的程序目标，不证明所选分子对的具体研发意图。规则改变时验证命令拒绝沿用未调参留出的表述。

人工复核表要求 supported/recovered 为 yes/no，并填写 reviewer、rationale。未完整复核时真实指标为空。重新运行保留相同 review_id 的内容，变化后旧表归档为 `.archived.csv`。

旧 `python -m phase0.sar.benchmark --narratives phase0/examples` 仍兼容种子样例。它报告编号有效率、自动标记候选比例和关键词代理指标，不是真实引用准确率或幻觉率。缺少 FACTS 快照时会警告编号漂移风险。

## 模块

| 模块 | 作用 |
|---|---|
| schema/features | 数据加载、结构内部一致性、RDKit 描述符 |
| units/deltas/align | 单位、assay 可比性、聚合与结构对照 |
| rules | 既有启发式规则，分值不是概率 |
| narrate/cli | 有限证据叙述、响应校验、快照 |
| ingest | 公开文献数据导入与缓存 |
| validate/benchmark | 冻结规则诊断与人工复核 |
| report/web | 离线证据界面 |

专利模块新增了 Google Patents 元数据、实施例标题/邻接文本和化学实体索引解析。结构 OCR、任意竞对程序聚类及经过证据确认的历史演化时间线尚未实现。


## 跨家族证据案例

工作台点击“加载跨家族证据案例”，加载 WO2011138751A2（家族 44278717，优先权 2010-05-04）和 WO2013132376A1（家族 48142828，优先权 2012-03-06）。共 6 张助手转录证据卡、22 项原始测量，另 2 项细胞测量明确保留未测状态。均待独立化学家复核。

“跨专利关联”合并同族公开，并区分可核查事实、研究假设和缺少的证据。点击预设对照可选择早期 Example 7 与大环 Example 6，查看 MCS、描述符差异及活性原表链接。引用列表和结构关联只支持“相关系列”，不证明直接演化或真实研发动机。跨专利测量只并列展示，不计算倍数；同专利匹配协议继续支持精确值比较。

关系只有在两份来源快照、家族、优先权日期、引用和证据映射均匹配时才展示。该案例是显式整理的关系，不是任意专利间自动识别演化的能力。来源变化时需要重新核对，不以旧卡替代。原始 PDF/HTML 留在本地缓存，不提交到仓库。

## SMILES / 靶点发现接口

`POST /api/discover` 接收 JSON：

- `{"mode":"smiles","query":"CCO","external":false}`：本地解析与证据匹配；仅 `external: true` 时查询 ChEMBL 标准 InChIKey。
- `{"mode":"target","query":"ALK"}`：返回最多 20 个候选，不自动选择物种或融合蛋白。
- `{"mode":"activities","entity":"target","id":"CHEMBL4247","offset":0}`：分页测量；`entity` 也可为 `molecule`，`offset` 为 20 的倍数，范围 0–10000。

每次最多读取 20 条、响应最多 5 MiB、网络请求超时 30 秒，不进行自动全库遍历。来源保存在 `artifacts/discovery-cache/`。本地专利匹配依赖显式整理的 6 张卡，打开专利时仍需核对来源快照。ChEMBL 结果不自动提升为专利实施例或研发路线证据。
