# 演示场景

给药物化学家演示用的八个完整用户场景，每个场景附新版工作台截图、操作步骤和讲解要点。

- **完整页面**：用浏览器打开 [page.html](page.html)（本地文件即可，图片在 [shots/](shots/)）。
- **重新截图**：`python docs/demo-scenarios/capture.py`（需要 `requirements-browser.txt`；Pillow 随 RDKit 安装）。脚本在本机空闲端口启动真实工作台，使用临时目录里的新台账数据库，离线运行，先清空再重新生成 `shots/` 下的全部 17 张图。

| 场景 | 化学家的问题 | 截图 |
|---|---|---|
| 1 从一个专利号开始一项竞对调研 | WO2013132376A1 和在跟的系列有什么关系，下一步看什么 | [首页识别](shots/s1-home.png) · [调研概览](shots/s1-overview.png) |
| 2 竞品专利里到底测了什么，两代之间能不能比 | 各实施例测了哪些值；早期 Example 7 与大环 Example 6 能否算倍数 | [证据表](shots/s2-evidence.png) · [跨家族对照](shots/s2-compare.png) |
| 3 活性提高了，其他性质付出了什么代价 | 6f → 6e 酶 Ki 改善，MDR1 外排比却从 7.6 升到 17；缺什么数据 | [目标与映射](shots/s3-mapping.png) · [按替换汇总](shots/s3-tradeoff.png) · [补测建议](shots/s3-followups.png) · [同一论文对照](shots/s3-compare-paper.png) |
| 4 从一个结构找回同一条研发线 | 只有洛拉替尼结构，能否找回前一代化合物和专利 | [检索条件](shots/s4-search.png) · [相似性 ≥ 50%](shots/s4-similarity.png) · [共有片段子结构](shots/s4-substructure.png) |
| 5 两代专利之间改了哪里 | 无环系列到大环系列在哪些位置做了改动 | [时间线与 R 基团对齐](shots/s5-timeline.png) |
| 6 组会前：具名复核，再导出报告 | 哪些证据能放心引用，报告里怎么区分 | [复核队列](shots/s6-review.png) · [报告预览](shots/s6-report.png) |
| 7 AI 助手只能提交“待确认” | 助手会不会自己确认数据或自己算倍数；工具描述（模型读到的提示词）怎么写 | [工具层演示输出](shots/s7-agent-tools.png) |
| 8 从 PDF 开始：抽取出错时会被标出来 | 直接丢 PDF 进来、大量自动抽取时，抽错了怎么知道 | [从 PDF 开始](shots/s8-upload.png) · [分级抽取演示输出](shots/s8-extraction.png) |

## 数据来源与边界

- 截图在离线环境中拍摄，没有查询外部数据库，也没有读取专利公开页面。证据表、跨专利关系和台账都是仓库中已整理的数据（按专利 PDF 转录，尚未经过独立化学家复核）。
- 场景 6 的具名确认在脚本创建的临时台账里完成，不改动 `artifacts/` 或仓库数据；报告因此显示“已确认证据 1 条”。
- 场景 8 的上传截图用的是名为 `WO2011138751A2.pdf` 的测试文件，不是公开 PDF，所以哈希对不上、只按文件名推测；抽取演示的抽取器是模拟的，输出第一行即写明。场景 7、8 的终端截图是 `python -m phase0.tools.demo` 与 `python -m phase0.extract.demo` 的真实输出。
- 需要联网的场景（靶点 → 测量 → 加入台账、SureChEMBL 专利化学检索、PubChem 关联专利、读取公开页面后计算候选程序分组）没有截图：离线只能用虚构的数据库返回来截图。先运行 `python -m phase0.tools.verify_online` 核对在线路径，再现场演示或补拍。
- 旧版六步工作流仍在 `/classic/evidence`，本目录不再为它截图。
