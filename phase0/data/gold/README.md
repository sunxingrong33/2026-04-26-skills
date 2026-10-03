# 抽取评测金标准

每份公开文本一个 JSON 文件，用于按字段评测任意抽取方法（人工、开源工具或 agent）。预测结果使用同一格式。

## 当前内容（v0）

| 文件 | 来源 | 化合物 | 测量 | 缺失记录 |
|---|---|---:|---:|---:|
| WO2011138751A2.json | 由 `patent_evidence/` 证据包派生 | 3 | 10 | 2（未测） |
| WO2013132376A1.json | 由 `patent_evidence/` 证据包派生 | 3 | 12 | 0 |

**v0 不是独立金标准**：它继承证据包的状态 `agent_visual_checked_pending_independent_review`（助手对照 PDF 转录，独立化学家复核尚未完成）。评测报告会显示这一提示。只有 `gold_status` 为 `independent_review_complete` 的文件才算正式金标准。

派生文件带 `derived_from`（证据包路径与 SHA-256）。证据包改动后运行 `python -m phase0.eval.gold` 重新生成；测试会检查二者是否一致。人工新增的金标准不要带 `derived_from`。

## 评测命令

```sh
python -m phase0.eval.extraction_metrics --pred <预测目录或文件> [--json 报告.json] [--max-unflagged 0]
```

预测目录中按公开号同名匹配（如 `WO2013132376A1.json`）；缺少文件视为全部漏抽。`--max-unflagged N` 在未标记错误数超过 N 时返回非零，可用于 CI 门槛。

## 格式

```json
{
 "schema_version": 1,
 "publication": "WO2011138751A2",
 "gold_status": "independent_review_complete",
 "compounds": [
  {"example": "6", "role": "example", "smiles": "...", "structure_pdf_page": 127}
 ],
 "observations": [
  {"example": "6", "assay_id": "ALK_WT_Ki", "status": "measured",
   "relation": "=", "value": 0.62, "unit": "nM", "raw": "0.620 nM", "table_pdf_page": 186},
  {"example": "6", "assay_id": "cell_WT_IC50", "status": "not_tested",
   "relation": null, "value": null, "unit": null, "table_pdf_page": 186}
 ]
}
```

| 字段 | 取值 | 说明 |
|---|---|---|
| `role` | `example` / `intermediate` / `reference` / `reagent` | 化合物角色，区分实施例与中间体 |
| `status` | `measured` / `not_tested` / `blank` / `not_reported` / `not_applicable` | 缺失语义分开记录，不统一成 ND |
| `relation` | `=` `<` `<=` `>` `>=` `~` `grade` | 限定符原样保留 |
| `grade` | 字符串 | 仅 `relation` 为 `grade` 时使用；分级值不带连续数值，不参与倍数计算 |
| `structure_pdf_page` / `table_pdf_page` | 整数 | PDF 文件页码（不是印刷页码） |
| `confidence` | `high` / `medium` / `low` | **仅预测使用**；缺省视为 `high` |
| `flags` | 字符串列表 | **仅预测使用**；非空即视为已标记风险 |

## 指标

| 指标 | 含义 |
|---|---|
| **未标记错误数** | 错误且未被标为中 / 低置信度或带 flags 的字段数。核心安全指标 |
| **编造数值数** | 原文为未测 / 空白等，预测却给出数值；或凭空多出的测量 |
| **可比性误判** | 按预测可以计算 B/A、按金标准不可以（或反之）的同实验分子对。规则与 `patent_evidence.compare_measurements` 一致：同一公开文本、同一 assay、双方为 `=` 精确值、单位归一后一致、参比值为正 |
| 数值 + 限定符 + 单位完全正确 | 金标准测量中，预测状态、限定符、数值（相对误差 1e-6，单位先归一）全部一致的比例 |
| 缺失语义正确 | 金标准缺失记录中，预测给出相同 status 的比例 |
| 结构完全一致 / 连接一致 | 标准 InChIKey 全部一致 / 前 14 位一致（忽略立体与质子化层） |
| 化合物召回 / 精确率、角色、页码 | 按实施例编号匹配 |

漏抽项单独计数，不算入未标记错误：没有输出的东西无从标记，它是召回问题。

## 标注新金标准

1. 按上面的格式逐字段转录，页码用 PDF 文件页码。
2. 原文空白、未测、未报告分别记录，不补零、不插值；限定符原样保留。
3. 不确定的立体中心保持未指定。
4. 完成独立复核后把 `gold_status` 设为 `independent_review_complete`，并在提交说明中写明复核人。
5. 运行 `python -m pytest -q phase0/tests/test_eval_metrics.py` 确认格式有效。
