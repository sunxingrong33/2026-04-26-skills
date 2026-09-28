# 演示场景

给药物化学家演示用的六个完整用户场景，每个场景附工作台截图、操作步骤和讲解要点。

- **完整页面**：用浏览器打开 [page.html](page.html)（本地文件即可，图片在 [shots/](shots/)）。
- **重新截图**：`python docs/demo-scenarios/capture.py`（需要 `requirements-browser.txt`；Pillow 随 RDKit 安装）。脚本在本机空闲端口启动真实工作台，离线运行，覆盖 `shots/` 下的全部 14 张图。

| 场景 | 化学家的问题 | 截图 |
|---|---|---|
| 1 竞品专利里到底测了什么 | 洛拉替尼专利代表性实施例的结构与活性，每个数能回到原文 | [证据卡](shots/s1-evidence-cards.png) · [展开的实验条件](shots/s1-card-detail.png) |
| 2 早期专利和大环化专利是什么关系 | 两代系列有无关系，大环化后活性提高多少 | [跨专利关联](shots/s2-lineage.png) · [结构差异](shots/s2-compare-structures.png) · [测量并列](shots/s2-compare-measurements.png) |
| 3 活性提高了，其他性质付出了什么代价 | 6f → 6e（N,N-二甲酰胺 → N-甲酰胺）酶活性提高，MDR1 外排比却变差 | [② 结构对齐](shots/s3-1-alignment.png) · [③ 可比性](shots/s3-2-comparability.png) · [④ 汇总](shots/s3-3-summary.png) · [⑥ 候选（WT ALK Ki）](shots/s3-4-suggest-lower.png) · [⑥ 候选（MDR1 外排比）](shots/s3-5-suggest-other.png) |
| 4 从一个结构找回同一条研发线 | 只有洛拉替尼结构，能否找回前一代化合物和专利 | [相似性 ≥ 50%](shots/s4-similarity.png) · [共有片段子结构](shots/s4-substructure.png) |
| 5 AI 助手只能提交“待确认” | 助手会不会自己确认数据或自己算倍数 | [工具层演示输出](shots/s5-agent-tools.png) |
| 6 自动抽取出错时会被标出来 | 大量自动抽取时，抽错了怎么知道 | [分级抽取演示输出](shots/s6-extraction.png) |

## 数据来源与边界

- 截图在无法联网的环境中拍摄。专利证据卡、跨专利关系和台账都是仓库中已整理的数据（按专利 PDF 转录，尚未经过独立化学家复核）。专利页面没有在线读取，截图里不含专利标题、摘要和化学实体索引。
- 场景 5、6 是 `python -m phase0.tools.demo` 与 `python -m phase0.extract.demo` 的真实输出；场景 6 的抽取器是模拟的，输出第一行即写明。
- 需要联网的场景（靶点 → 测量 → 加入台账、SureChEMBL 专利化学检索、PubChem 关联专利）没有截图：离线只能用虚构的数据库返回来截图。先运行 `python -m phase0.tools.verify_online` 核对在线路径，再现场演示或补拍。
