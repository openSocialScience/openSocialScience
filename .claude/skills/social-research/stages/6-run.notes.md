# 环节 6 的散文：诊断怎么读、图为什么必画、诊断→处置

**主规格和全部稳健性是同一次跑完的**，结果一起写进 `results.json`。
分开跑的话后一次会覆盖前一次，论文里就报不出具体数值了。

读 `results.json` 时先看两条新的：

- **`数据契约`**（分批设计）——单位-时间键唯一（不唯一直接拒绝）、几个处理单位**没有处理前观测（左删失）**
  并点名、几个从未处理。左删失的单位平行趋势无从检验，`oss route` 会开「剔掉再估」的稳健性
- **`样本构建`**——原始 → 筛选 → 去缺失 → 估计样本，每步剩多少行 / 多少单位、**哪些单位整个掉了、因为哪个变量**。
  评审第十条：「107 国变 99 个 cluster，8 个去哪了没交代」——现在答案在这一行里，论文数据节用
  `{{table:sample}}` 排成表，或按它写一段

然后看三样：

- `diagnostics` 的 **`共线诊断（VIF）`** —— 报「严重」就说明有系数不可单独解读。
  最常见的成因是**年龄-时期-队列恒等式**（`cohort = year − age`，
  三者不能同时线性进入回归）。交互项与自己分量造成的高 VIF 是正常的，工具会区分
- `robustness` 里每一项的符号与量级 —— **跑不动的那几项也在里面**，
  它们没有结果本身就是信息
- `因缺失丢弃` 的比例

```bash
oss figure coef         --run <运行目录>                        # 主系数与置信区间
oss figure collinearity --run <运行目录>                        # VIF 条形图
oss figure robustness   --run <运行目录>                        # 主结果与各项并排
```

**做了政策冲击对比就必须画这张，不能跳过：**

```bash
oss figure event --run <运行目录>
```

看竖虚线**左边**：政策实施之前那几期本该贴着零线。偏离了就说明结论不可信。
`results.json` 的 `diagnostics.平行趋势` 给出机械判定，但**图更直观**。

**写了交互项就必须画这张，不能跳过：**

```bash
oss figure marginal --run <运行目录> --by <队列/年份列> --split <分组列> \
                    --treatment <要看斜率的变量>
```

**交互项 `a × b` 假定「a 的斜率随 b 线性变化」。这个假定几乎从来没人验证过，
而回归表看不出来、图一眼就看得出。**

真跑时的教训：GSS 那篇论文写「均衡作用随队列线性衰减，到最晚队列所剩无几」，
画出分箱斜率才发现——早期队列是平的甚至为负、中间有跳跃、
而最晚队列两条线**完全重合**。「所剩无几」低估了，真相是彻底消失。
**一个交互系数掩盖了三件事。**

代码报错就改了重跑，这很正常。

## 诊断 → 处置（oss route）

跑完分析**先开方，再写论文**。评审对验收跑的判词：「它把自己所有的病症都准确地描述了一遍，
然后没有治任何一个。」诊断只是文字，处方是动作：

```bash
oss route --run <运行目录>
```

它读 `results.json` / `power.json` / 因果审计，开出处方，每条一个动作：

| 处方 | 动作 | 怎么处置 |
|---|---|---|
| `power_fail` | **停** | 换结局 / 样本 / 题目（见环节 4）。驳回不了 |
| `precision_low` | **不停**（描述类精度不足，代替 `power_fail`） | 收窄题目或局限写明分不出多小的差别，`oss route --disclose precision_low --why "选了哪条"`（见环节 4「描述类题目按精度判」） |
| `power_mode_mismatch` | **停**（must 级，驳回不了，也不能写进局限了事） | 功效报告跟计划对不上（研究类型不同，或算的是另一道题）：重跑 `oss power --idea <计划的 idea_id>`（会按计划的分组、整群与权重算），再 `oss route` 复查；换了题、报告已按新题算而计划还是换题前的，先给新题写计划（见环节 4） |
| `staggered_twfe` | 换估计量 → `did_cs` | `oss route --apply staggered_twfe` |
| `pretrend_fail` | 设计级 | 缩窗口 / 剔抢跑期 / 降级为关联；改了重跑，或 `--disclose` 写进局限并附理由 |
| `reversal` / `attrition` | 加一项稳健性 | `oss route --apply reversal`（列 `_reversal` / `_balanced` 由 `oss run` 自动加） |
| `cohort_hetero` `imbalance` `robustness_flip` `audit_N` …… | 写进局限 / 正文 | `oss route --disclose <id> --why "写在哪、怎么写的"` |

**规矩：一次一处置。**应用一条 → `oss run` 重跑 → `oss route` 复查（触发消失的自动标「已解决」）。
不吃的药要说为什么：`oss route --dismiss <id> --why "<理由>"`，理由留在 `route.json` 里，审稿人会看。
`must` 级（功效 FAIL、功效报告跟计划对不上、分批用 TWFE、平行趋势不成立）没处置，`paper check` 和 `paper export` 都拦。描述类没有功效 FAIL：精度不足开的 `precision_low` 同样是 must 级，`--disclose` 写明处置了就不拦。
