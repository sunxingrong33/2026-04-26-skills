# 文档关系记录

每个 JSON 文件是一条经人工整理的文档间关系，由 `phase0.ledger.schema.DocumentRelation` 校验，`phase0/sar/lineage.py` 读取。代码中不写死任何公开号；新增关系只需新增记录。

## 展示规则

`basis` 中的条件在当前已加载的来源上**全部满足**才展示该关系；任一不满足则放入接口返回的 `withheld`，并列出未满足的条件，页面据此说明原因。只加载了一端时既不展示也不列入 `withheld`。日期先后或引用本身不会产生关系。

| 条件 `check` | 含义 | `expected` |
|---|---|---|
| `family_id` | 已加载文本的专利家族编号一致 | 家族编号 |
| `priority_date` | 优先权日一致 | 日期 |
| `snapshot_matches_evidence` | 来源 HTML 哈希与证据包记录一致 | 不填 |
| `has_evidence_cards` | 已映射出证据卡 | 不填 |
| `cites` | 该文本的引用列表包含另一端 | 被引公开号 |

条件和来源只能引用关系两端的文档。

## 关系类型 `type`

| 类型 | 用于 |
|---|---|
| `same_family` | 同一优先权族 |
| `sibling_application` | 姊妹申请：同日提交、申请号连续，但优先权各自独立，不是同一族 |
| `citation` | 仅有引用关系 |
| `inventor_overlap` | 发明人重叠 |
| `related_series` | 经整理认定的相关系列；**不表示已证实直接演化** |

## 内容字段

`facts`（可核查事实）、`hypothesis`（研究假设，不是原文确认的动机）、`gaps`（仍缺的证据）分开填写。`sources` 可以是固定 URL，也可以是 `publication` + `fragment`（运行时解析为该文本已加载的来源页）。`review.record_status` 默认 `proposed`；改为 `confirmed` 必须写明复核人。

## 当前记录

| 文件 | 类型 | 条件数 |
|---|---|---:|
| `WO2011138751A2__WO2013132376A1.json` | related_series | 9 |

该记录由原 `lineage.py` 中硬编码的内容原样迁移；迁移前后接口输出在原有字段上逐项一致。
