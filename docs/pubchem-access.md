# PubChem 交叉引用：访问方式与使用条款核实记录

记录日期：2026-09-28 · 用途：迭代 I2.6 结构检索的补充来源（从一个具体分子找关联专利与文献）。

## 结论

| 项 | 结论 | 依据 | 核实程度 |
|---|---|---|---|
| 是否可用 | 可用：PUG-REST，无需账号或 API key | PubChem PUG-REST 文档（经检索摘要）；IUPAC FAIR Chemistry Cookbook 示例 | 间接；**本项目尚未实连**（本环境网络策略拒绝访问 pubchem.ncbi.nlm.nih.gov） |
| 速率限制 | 每秒不超过 5 次、每分钟不超过 400 次；超限返回 `PUGREST.ServerBusy` | PUG-REST 使用政策（经检索摘要） | 间接 |
| 使用条款 | NCBI 对 PubChem 数据的使用和分发不加限制（含商业用途）；但部分提交者可能对其提交的数据主张专利、版权等权利；鼓励注明来源 | NCBI 网站与数据使用政策（经检索摘要） | 间接 |
| 决定 | 接入，作为补充来源单独标注；页面与工具结果注明 PubChem 与 CID，并提示提交者权利 | — | — |

## 使用的接口

| 目的 | 路径 | 返回 |
|---|---|---|
| InChIKey → CID | `GET /rest/pug/compound/inchikey/{InChIKey}/cids/JSON` | `IdentifierList.CID`；查不到时 HTTP 404 + `Fault.Code = PUGREST.NotFound` |
| 关联专利 | `GET /rest/pug/compound/cid/{cid[,cid…]}/xrefs/PatentID/JSON` | `InformationList.Information[].PatentID`（形如 `US-7803790-B2`、`WO-2013132376-A1`） |
| 关联文献 | `GET /rest/pug/compound/cid/{cid[,cid…]}/xrefs/PubMedID/JSON` | `InformationList.Information[].PubMedID` |

## 本项目的使用约束

| 约束 | 实现 |
|---|---|
| 请求量 | 每次查询最多 3 个请求（CID、专利、文献），一次只进行一个查询（服务端工作锁）；远低于 5 次/秒 |
| 只发标准 InChIKey | 参数必须符合标准 InChIKey 格式，不发送结构本身；最多查询 5 个 CID |
| 缓存与重放 | 按 URL 缓存原始响应并记录 SHA-256 与读取时间；“查不到”也是答案，一并缓存以便重放；服务繁忙等错误不缓存；重放时禁止联网 |
| 截断如实 | 专利、文献各显示前 20 条并给出总数；已整理证据包中的专利排在最前并单独列出；文献按 PMID 从新到旧 |
| 失败不伪装 | PubChem 不可用时报错，不返回样例 |

## 结果的含义（写在页面与工具说明中）

- 交叉引用是提交者登记的关联：只说明化合物记录与专利或文献有关，不说明它是实施例、被权利要求覆盖或经过测试。
- PubChem 的专利关联与 SureChEMBL 可能同源（都来自专利的自动化学标注），两者并列显示，不合并、不去重，也不当作相互独立的佐证。
- 按标准 InChIKey 精确查找：盐型、其他互变异构或立体异构体可能在别的 CID 下。
- 专利编号规范化后可“核实并加载专利”，加载原始专利页面后才进入现有证据流程。

## 未核实 / 待办

- 以上接口与返回格式未经本项目实连验证；首次在可联网环境运行 `python -m phase0.tools.verify_online`（含“PubChem 交叉引用”一项：洛拉替尼的关联专利中是否包含已整理的两份专利）后，把结果补入本文件与 `docs/baseline.md`。
- PubChem 的专利数据也可通过 PUG-View 的注释获取（更完整，含标题与日期），但响应较大，暂不使用。

## 来源

- PUG REST 文档：https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest （本环境无法直接打开，经检索摘要）
- IUPAC FAIR Chemistry Cookbook，Accessing PubChem through PUG-REST：https://iupac.github.io/WFChemCookbook/datasources/pubchem_pugrest1.html
- NCBI Website and Data Usage Policies and Disclaimers：https://www.ncbi.nlm.nih.gov/home/about/policies/
- Kim S. et al. Exploring Chemical Information in PubChem. Current Protocols (2021)：https://pmc.ncbi.nlm.nih.gov/articles/PMC8363119/
