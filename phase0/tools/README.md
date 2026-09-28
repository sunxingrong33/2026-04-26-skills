# Agent 工具层（迭代 I2）

把台账、化学计算和公开来源封装成 agent 可调用的工具，并通过 MCP 提供给 Claude Code、Claude Agent SDK 等客户端。工具核心（`core.py`）不依赖 MCP，也不依赖任何模型；MCP 只是一层转换（`mcp_server.py`）。

```sh
python -m pip install -r requirements-agent.txt        # 核心依赖 + mcp==2.2.0
python -m phase0.ledger.store init --db artifacts/ledger.sqlite
python -m phase0.tools.mcp_server all --ledger-db artifacts/ledger.sqlite   # stdio
python -m phase0.tools.replay artifacts/runs/<run_id>                      # 按轨迹重放
python -m phase0.tools.demo                                                 # 离线演示：脚本化会话 + 重放
python -m phase0.tools.verify_online                                        # 在线路径核对（需联网）
```

## 工具

| 服务 | 工具 | 只读 | 联网 | 用途 |
|---|---|:-:|:-:|---|
| ledger | `ledger_overview` | ✓ | | 台账概况、复核状态分布、常见缺口 |
| | `ledger_search_observations` | ✓ | | 按化合物 / 实验 / 文档 / 状态筛选观测；缺失以 status 表示 |
| | `ledger_get_record` | ✓ | | 读取一条完整记录（来源定位、原始记录、缺口） |
| | `ledger_record_schema` | ✓ | | 提交前查看某类记录的 JSON Schema |
| | `ledger_propose` | | | 提交一条 **proposed** 记录，必须写明依据 |
| | `ledger_propose_chembl_activities` | | ✓ | 把一页 ChEMBL 测量中选定的记录提交为 proposed（数值由服务端重读） |
| | `ledger_propose_patent_index` | | ✓ | 把专利及其化学实体索引提交为 proposed（角色 unspecified，无测量） |
| chem | `chem_describe` | ✓ | | 规范化 SMILES、分子式、InChIKey、描述符 |
| | `chem_mass_check` | ✓ | | 质谱报告值与结构比对（容差随报告精度） |
| | `chem_compare_observations` | ✓ | | 两个分子逐实验可比性检查 / 骨架对齐 |
| sources | `patent_fetch` | ✓ | ✓ | 专利元数据、实施例标题、化学实体索引、已整理证据卡 |
| | `chembl_activities` | ✓ | ✓ | ChEMBL 一页测量记录 |
| | `structure_search` | ✓ | ✓ | 精确 / 相似性 / 子结构检索本地证据台账与 ChEMBL（`external=false` 时只查本地）；ChEMBL 命中经本地 RDKit 复核，标准化步骤写入结果 |

每个工具返回 `{summary, data, preview}`：`summary` 一两句话，`data` 完整结果，`preview` 前几条供快速判断。

## 安全设计

- **没有确认、拒绝、导入工具。** agent 只能写入 `proposed`，写入者记为 `agent-run:<run_id>`，理由写入审计日志；确认只能由具名的人通过 `LedgerStore.review` 完成。
- **数值只由代码产生**：描述符、比值、质量校验都来自确定性计算；工具描述明确要求 agent 不自行估算。
- **参数严格校验**：参数先按函数签名做 pydantic 校验再执行；MCP 发布的参数 schema 带 `additionalProperties: false`，拼错的参数名（如 `compund_id`）直接报错，不会被静默忽略而变成不带过滤的查询。
- **可恢复的错误**：输入错误、记录不存在、被拒绝的提交都以工具错误返回，并说明下一步怎么做。
- **工具描述写明何时调用**，并写明边界（索引结构不是实施例、限定值不能算倍数、比值不是改善倍数）。
- 不提供“用金标准给抽取结果打分”的工具，避免 agent 针对评测集拟合。

MCP 注解：写入工具 `read_only_hint=false`、`destructive_hint=false`（只追加、不覆盖、不删除）；联网工具 `open_world_hint=true`。

## 运行轨迹与重放

每个服务进程是一次运行，目录为 `artifacts/runs/<run_id>/`：

- `run.json`：开始时间、台账路径、缓存目录；
- `ledger.start.sqlite`：运行开始时的台账快照；
- `trace.jsonl`：每次调用一行，包括工具、校验后的参数、是否失败、结果的 SHA-256 和摘要。

`python -m phase0.tools.replay <run_dir>` 复制快照，按顺序重新执行每一次调用（包括写入），逐条比对结果哈希。重放默认禁止联网：专利与 ChEMBL 工具只能从缓存得到结果，缓存缺失时显示为不一致，而不是悄悄取回新数据。运行结束后台账再怎么变化，都不影响重放。

## 连接到 Claude Code / Agent SDK

仓库附带示例 [`.mcp.json.example`](../../.mcp.json.example)，默认不启用。需要时复制为项目根目录的 `.mcp.json`（路径按实际环境调整；Windows 下 `command` 改为 `.venv\Scripts\python.exe`），并先用 `python -m phase0.ledger.store init --db artifacts/ledger.sqlite` 建好台账。内容如下：

```json
{
  "mcpServers": {
    "sar-ledger": {
      "type": "stdio",
      "command": ".venv/bin/python",
      "args": ["-m", "phase0.tools.mcp_server", "ledger", "--ledger-db", "artifacts/ledger.sqlite"]
    },
    "sar-chem": {
      "type": "stdio",
      "command": ".venv/bin/python",
      "args": ["-m", "phase0.tools.mcp_server", "chem", "--ledger-db", "artifacts/ledger.sqlite"]
    },
    "sar-sources": {
      "type": "stdio",
      "command": ".venv/bin/python",
      "args": ["-m", "phase0.tools.mcp_server", "sources"]
    }
  }
}
```

Claude Agent SDK 使用同样的 stdio 服务配置，具体写法见其文档。测试中用官方 `mcp` 客户端以子进程方式启动 CLI（与上述客户端相同的路径）完成验证，无需 API key。

## 在线路径核对

`python -m phase0.tools.verify_online [--out 目录]` 在可联网的机器上核对真实来源，默认输出到 `artifacts/online-check/<时间>/`。它用全新缓存和由已提交数据构建的临时台账，通过本工具层依次：

1. `patent_fetch` 两份专利（WO2011138751A2、WO2013132376A1）：记录页面哈希、索引结构数、证据卡数与质谱校验；
2. 检索靶点“ALK”，核对候选中含 CHEMBL4247；
3. `chembl_activities` 读取一页测量，统计限定符与无数值记录（原样保留）；
4. `ledger_propose_chembl_activities` 提交整页，核对“新增 + 拒绝 + 已存在”等于请求数，列出拒绝原因；再提交一次，核对无新增；
5. `ledger_propose_patent_index` 提交一份专利索引；核对所有新增记录均为 `proposed`；
6. `structure_search`：以洛拉替尼（WO2013132376A1 Example 2）做相似性检索、以两个 ALK 家族共有的氨基吡啶苄醚片段做子结构检索，记录命中数、是否截断、本地复核不一致的记录，以及相似性检索是否命中查询分子本身；
7. 断网重放整个运行，逐条比对。

结果分为通过 / 注意 / 失败。**注意不是失败**：例如专利页面哈希与已整理证据包不一致时，证据卡按设计暂停使用，报告提示需要重新核对映射。任一检查失败时退出码非零。脚本本身由 `test_verify_online.py` 以模拟网络响应测试；真实运行结果补入 `docs/baseline.md`。

## 尚未完成

- 计划中的 SureChEMBL 与 PDF（页面分区、结构图识别、表格抽取）工具，属于 I3 分级抽取。
- 编排者、审查 agent 等（I6）；本层只提供工具，不包含 agent 本身。
- 尚未用真实模型端到端运行过 agent；目前的验证覆盖工具、MCP 协议与重放。
- 在线路径核对脚本尚未在可联网环境中运行。
