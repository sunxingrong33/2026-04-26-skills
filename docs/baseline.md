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
| `GET /` | 200（专利工作台） |
| `GET /evidence` | 200（六步工作流） |
| `GET /examples` | 200（离线文献页，需先运行 demo） |
| `GET /api/evidence` | 200；10 个输入包、320 条观测，与文档一致 |
| `GET /api/patent?id=WO2013132376A1` | 记录环境的网络策略拦截了 Google Patents；接口返回明确错误“专利来源连接失败或超时”，没有用样例替代，符合设计 |

## 未覆盖

- 需要联网的在线路径（Google Patents、ChEMBL）未在记录环境中端到端验证，需在可联网的机器上补测。
- 浏览器界面交互未做自动化测试；演示前需人工走查。
