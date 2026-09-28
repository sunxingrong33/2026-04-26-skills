# 类型化证据台账（迭代 I1）

以 pydantic 定义的台账实体，作为后续所有入口写入、所有分析读取的统一数据结构。现有 `phase0.sar.evidence_ledger.build_ledger()` 及依赖它的页面和接口保持不变；本模块在其之上建立类型化台账，并证明迁移无损。

```sh
python -m phase0.ledger.migrate                   # 构建、校验并输出摘要；无损时返回 0
python -m phase0.ledger.migrate --out ledger.json # 另存类型化台账
python -m phase0.ledger.schema                    # 重新导出 ledger.schema.json
```

## 实体

| 实体 | 要点 |
|---|---|
| `Document` | 论文或专利；标识符、原始快照哈希（html / pdf） |
| `Compound` | 结构、结构来源与编号对应来源（`Location`）、立体说明、原文质谱值；**`role` 必填**：example / intermediate / reference / reagent / unspecified |
| `Assay` | 台账内编号为 `<文档>:<原 assay 编号>`，不同文档的同名 assay 不合并；终点、协议及定位、单位、协议变体、分级定义 |
| `Observation` | **`status`**：measured / not_tested / blank / not_reported / not_applicable；限定符原样保留；分级值用 `relation="grade"` 与 `grade`；原始记录 `raw` 原样保存 |
| `DocumentRelation` | 文档间关系：类型（同族 / 姊妹申请 / 引用 / 发明人重叠 / 相关系列）、可机器核对的展示条件、事实 / 假设 / 缺口分开；见 `phase0/data/relations/README.md` |
| `Review` | 每条记录都有：`record_status`（proposed / confirmed / rejected）、来源处理状态原文、复核人、显式缺口 |

Emax 等激动剂终点作为独立的 `Assay`（例如 endpoint 为 `Emax`、单位 `%`），不另设字段。

## 由模型强制的规则

- 非 measured 的观测不能带数值、等级或限定符；measured 必须有限定符，以及有限数值或等级（二者不能同时出现）。
- 分级值的等级必须在对应 assay 的 `grade_definitions` 中定义。
- `confirmed` 必须写明复核人；迁移产生的记录一律为 `proposed`。
- 编号唯一；观测、化合物、assay 引用的文档和实体必须存在，观测的 assay 必须属于同一文档。
- 未知字段直接拒绝（`extra="forbid"`）。

## 迁移结果（2026-09-27）

| 项 | 数量 |
|---|---:|
| 文档 | 4（论文 2、专利 2） |
| 化合物 | 37（专利实施例 6；论文化合物 31，角色未标注，已列为缺口） |
| assay | 108 |
| 观测 | 320：measured 311（`=` 285、`<` 26）、not_reported 7、not_tested 2 |
| 复核状态 | 全部 proposed |

缺失语义映射：专利证据包的 `missing_measurements` 按其说明（原表空白、表前注明未测试）记为 `not_tested`；ChEMBL 中无数值的记录记为 `not_reported`（论文原表尚未核对，不推断为未测）。

**无损证明**：类型化台账序列化、重新加载后还原为旧格式，与 `build_ledger()` 输出的规范化 SHA-256 完全一致；并逐条核对类型化数值、限定符、单位与原始记录一致。任一字段丢失或被改动都会使校验失败（见 `phase0/tests/test_ledger_schema.py`）。

## 尚未完成

- 存储层（SQLite）与读写接口。
- 现有分析模块（`evidence_pair`、`sar_workflow` 等）改为读取类型化台账。
