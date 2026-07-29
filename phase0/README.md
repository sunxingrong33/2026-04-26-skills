# 阶段 0：杀死性验证管道

方案里 §1 的三天验证。目的**不是**做出产品，是回答一个问题：

> 把专利里的结构演化算出来、讲成人话之后，化学家会不会说"这我早知道"？

代码只做到能回答这个问题为止。任何超出这个目的的工程（BigQuery 摄取、
程序聚类、前端）都不在这里。

---

## 跑起来

```bash
pip install -r requirements.txt
python -m phase0.sar.cli --program pfizer-alk --dry-run   # 只算，不调 API
python -m phase0.sar.cli --program pfizer-alk             # 需要 ANTHROPIC_API_KEY
python -m pytest phase0/tests -q
```

`--dry-run` 会把喂给模型的 FACTS 块写到 `phase0/out/*.prompt.txt`，
结构化结果写到 `phase0/out/<program>.json`。

---

## ⚠️ 数据可信度：先读这一段

**本仓库自带的 5 个结构和全部专利号/优先权日/活性值，都是凭模型记忆填写的，
没有经过任何数据库核对。** 本次开发环境的网络策略只放行 GitHub，
PubChem / ChEMBL / EBI / Wikipedia 全部不可达，所以无法自动核对。

已经做到的校验：每个结构都声明了分子式，`features.py` 用 RDKit 反算并强制比对，
不一致直接拒绝入库。5 个化合物的分子式与分子量都与公开值一致
（450.35 / 406.42 / 558.15 / 482.63 / 584.11），克唑替尼与洛拉替尼的手性也都算出 (R)。
这能挡住"画错成另一个分子"，**但挡不住同分异构级别的画错**。

**在给任何化学家看之前，必须逐行核对下面这张表**：

| compound_id | 核对什么 | 去哪核对 |
|---|---|---|
| crizotinib | InChIKey | <https://pubchem.ncbi.nlm.nih.gov/compound/crizotinib> |
| ceritinib | InChIKey | <https://pubchem.ncbi.nlm.nih.gov/compound/ceritinib> |
| alectinib | InChIKey、**四环骈合方式** | <https://pubchem.ncbi.nlm.nih.gov/compound/alectinib> |
| brigatinib | InChIKey | <https://pubchem.ncbi.nlm.nih.gov/compound/brigatinib> |
| lorlatinib | InChIKey、**大环连接方式与氟位置** | <https://pubchem.ncbi.nlm.nih.gov/compound/lorlatinib> |
| 全部 | 专利号、优先权日、实施例编号 | Espacenet / Google Patents |
| 全部 | IC50 数值与 assay 条件 | 原始文献 |

核对通过后，把 `compounds.csv` 的 `structure_provenance` 从 `unverified` 改成
`pubchem`，`program_members.csv` 的 `activity_provenance` 同理。管道会自动
撤掉警告横幅——横幅消失就是"这份数据可以拿出去"的信号。

`example_ref` 目前全是"待补"。**方案里承诺的是"可点开看专利号 + 实施例编号"，
实施例编号没有就等于这个承诺没兑现**，这是给化学家看之前必须补上的。

---

## 开发过程中发现的三件事

这三件事都改变了方案里写的做法，值得单独说。

### 1. MCS 覆盖率做不了骨架跃迁判定，会给出自信的错误答案

方案 §3 难点② 的写法是 `rdFMCS` + `completeRingsOnly=True`，覆盖率 < 60%
判为 core hopping。实测在克唑替尼→洛拉替尼上，这套参数给出 **7 个重原子、
覆盖率 0.23** —— 一个高置信度的**假**骨架跃迁判定，而且恰好发生在整个产品
最想讲对的旗舰案例上。

原因有两个。一是 `completeRingsOnly=True` 在大环化面前是反向的：成环之后
原来的吡啶原子同时属于 12 元大环，要求"完整环"就把大环一起拖进匹配，
匹配随即崩塌。二是全分子 MCS 覆盖率被外围取代基主导，它回答的是"两个分子像不像"，
不是"母核是不是同一个"。

在 5 个 ALK 抑制剂构成的 8 个化合物对上（已知母核归属）扫了参数：

| 方法 | 判别间隔 (min正例 − max负例) |
|---|---|
| 全分子 MCS，24 组参数里最好的一组 | **−0.03** |
| Murcko 骨架 MCS | **−0.48** |
| **环系 Jaccard（现在用的）** | **+0.35** |

负数表示**不存在**能把两类分开的阈值。改成"把每个非大环 SSSR 环单独取出来，
比较两代的环集合 Jaccard"之后，正例 0.75/0.75，负例 ≤ 0.40。而且它的输出
本身就是人话：*保留苯环、吡啶、吡唑；消失哌啶* —— 这正是化学家描述母核变化的方式。

MCS 仍然在算，但降级为描述性信息，没有任何规则依赖它。

**这个阈值只在 8 个对、2 个正例上标定过。** 足够证伪 MCS，不足够信任 0.55 这个数。
上了 §5 的 benchmark 必须重新拟合。

### 2. 洛拉替尼的 TPSA 是升的，P-gp 规则不能按 TPSA 写

洛拉替尼 TPSA 从 78 升到 110，却是公认的脑渗透改善案例。真正下降的是
氢键给体（2→1）和碱性中心（1→0）。原先按 `TPSA <= 90` 写的
`pgp_efflux_mitigation` 规则会把这个真阳性挡在门外。

已改为以 HBD + 碱性为判据。这条修改是**看到案例之后做的**，虽然
CNS MPO / Hitchcock-Pennington 那套文献本来就把 HBD 和碱性列为 P-gp 底物特征的
主要驱动因素、TPSA 是次级项，但它仍然带着对单一案例过拟合的风险，
必须在 benchmark 上独立验证。

### 3. 苄位氢的 SMARTS 不能用 `[a]`

`[CX4;!H0][a]` 会把吡唑上的 N-甲基算成苄位氢，克唑替尼→洛拉替尼被算成 2→6。
N-甲基是 N-脱烷基化软点，不是苄位氧化软点，混在一起会让叙述层把一次
N-甲基化说成"封堵苄位"。已拆成 `benzylic_h_count`（接芳碳 `[c]`）和
`n_alkyl_h_count`（接芳氮 `[n]`），实际值 1→3 和 1→3。

---

## 设计原则（和方案一致的部分）

**分析层确定性，LLM 只做翻译。** 所有数字都出自 `features.py` / `deltas.py` /
`rules.py`。模型只拿到 FACTS 块，块里每一条都带 `[Ex]` 编号。

**防幻觉靠代码而不是靠 prompt。** `audit_response()` 做后验校验：

- 引用了不存在的 `[Ex]` → **丢弃该假说**
- 一条引用都没有 → **丢弃该假说**
- 出现 FACTS 里没有的数字 → 默认告警，`--strict-numbers` 下丢弃
- 输出不是合法 JSON → 整轮重试

`test_narrate.py` 把"伪造引用能活下来"当作最严重的失败来测。

**证据不足要能说出来。** 规则引擎在特征缺失时永不发射（`test_missing_feature_never_fires_a_rule`），
prompt 要求证据不足时输出空数组并置 `insufficient_evidence`。

**支撑数少要降级而不是报错。** 每条 delta 带 `n_support`，n=1 时置信度乘 0.8
并在 FACTS 里显式写明"中位值等同于单点值"。跨 assay 的活性比较乘 0.75
并标注不可定量比较——这就是方案 §8 说的"优雅降级"。

---

## 目录

```
phase0/
  data/
    programs.csv          程序定义
    compounds.csv         结构 + 分子式声明 + provenance
    program_members.csv   代际归属 + 专利引用 + 活性
  sar/
    features.py   RDKit 描述符 + 分子式闸门
    align.py      环系比对 / 骨架跃迁判定（含实测记录）
    deltas.py     代际聚合 + delta
    rules.py      规则引擎
    rules.yaml    规则库（20 条）
    narrate.py    FACTS 构建 + Claude 调用 + 引用审计
    cli.py        跑批入口
  tests/          66 个测试，含骨架判定的 golden test
```

---

## 现在缺什么

按重要性排序：

1. **结构与专利数据核对**（见上文表格）。没做完之前不能给化学家看。
2. **实施例编号**。`example_ref` 全是"待补"，产品承诺的可点开证据现在点不开。
3. **中间代**。`pfizer-alk` 现在只有克唑替尼和洛拉替尼两代，中间那些
   无环去哌啶类似物（J. Med. Chem. 2014, 57, 4720）没录。补上之后每代
   n>1，中位值才有意义，`n_support` 惩罚也才会松开。真正的
   "他们试过什么、放弃了什么"藏在中间代里 —— **现在这版讲的是起点和终点，
   最有价值的中间过程是缺失的**。
4. **叙述层没跑过**。环境里没有 `ANTHROPIC_API_KEY`，只验证过 dry-run 和
   审计逻辑的单元测试，没做过真实 LLM 调用。
5. **碱性 pKa**。方案里列了"最强碱性 pKa"，开源没有靠谱的预测器，
   硬造一个数字比没有更糟。现在用 `strong_basic_amine_count`
   （子结构计数，可解释）代替。要真 pKa 就得接 ChemAxon，或者在 CSV 里加一列人工填。
6. **死路规则**（`dead_end`）没实现。它需要按位点的 R 基团拆解，
   阶段 0 的化合物级数据支撑不了。

---

## 第三天怎么用

方案说得对：判据是"三个人里有两个说有意思"。

跑完之后拿到手的是 `phase0/out/pfizer-alk.json` 和终端时间线。给化学家看的时候
**先把数据可信度那段说清楚**，否则他们会去挑数据的错，而不是回答你真正想问的问题。

只问一句：**"这里面有你不知道的吗？"**

如果三个人都说知道，方案 §1 给的动作是往冷门靶点 / 中文专利调，而不是继续做通用的。
