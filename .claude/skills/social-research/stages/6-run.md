# 环节 6 · 跑分析与处置

## 先分岔：结果是不是已经在别处跑完了

**用户手上已经有结果、拿不出原始数据**时走这条（实验做完了、Stata 也跑完了，
数据是单位的 / 签了协议 / 在保密机器上 / 他就是不想再跑一遍）。
**这种人是最常见的真实用户**：不缺想法、不缺数据、不缺结果，缺的正是写论文这一段。

```bash
oss results import <你转出来的 results.json> --raw <他粘的原文.txt> \
                   [--software "<他说的原话>"] --run <运行目录>
```

三条纪律：

1. **他粘的那张回归表，原样存一份**（`--raw` 必给）。中间隔着一道「你把表转成
   结构化数据」的手工活——**小数点看错一位，整篇论文的数字全错，而且不报错**。
   存了原文，任何一个数都能翻回去对
2. **软件名照他原话**。他说「Stata」就填 Stata，**不许补成「Stata 18」**；
   他没说就别填 `--software`，论文里也不写。**补全就是编造**
3. **不问原始数据，也不问为什么带不出来**——那是他的事，跟论文成不成立无关

走这条时**本环节其余部分整个跳过**（没数据可跑，也没有诊断可开方）：
`oss run` / `oss route` / 各种诊断图都不做，直接进环节 7 写论文。
`oss eval` 会把「功效要在分析之前算」「跑完要开方」那两条明确判为
**不适用——结果是外部导入的**，那是对的，不是漏了。

图仍然画得出来（`fig_coef`、`fig_robustness` 只用系数与标准误）；
`fig_event` 要他也给了逐期系数才画得了。

## 输入
- `<运行目录>/plan.json`（已通过 `oss bind`）
- `<运行目录>/merged.csv` 或 plan 指定的数据文件
- `run_state.stage == "6-run"`

## 产出
- `results.json`（主规格 + 全部稳健性，**同一次跑出**）
- `fig_coef`、`fig_collinearity`、`fig_robustness`（必画）
- `fig_event`（有政策冲击对比时必画）
- `fig_marginal`（有交互项时必画）
- `route.json`，每条处方都有处置

## 完成条件（全部满足才进环节 7）
- `results.json` 存在，且 `数据契约` 无"键不唯一"；左删失的单位已点名
- `样本构建` 读过：原始 → 筛选 → 去缺失 → 估计样本，哪些单位整个掉了、因为哪个变量
- `diagnostics.共线诊断（VIF）` 读过；报"严重"的记下来，环节 7 必须提
- `oss run` 被**计划体检**挡下（退出码 6，处方 `plan_paired` / `plan_nested`）→ 按处方处置后重跑：配对列要么 `--apply`（稳健性里加上另一边）要么 `--dismiss --why`；嵌套的分组列先想清楚题目问的是哪种分法，选定后 `--disclose --why` 写明两种分法怎么对应
- must 级处方全部处置：`power_fail` → 停并回环节 4（描述类不开这条：精度不足开的是 `precision_low` → 已 `--disclose`，见环节 4「描述类题目按精度判」）；`staggered_twfe` → 已 `--apply` 并重跑；`pretrend_fail` → 已改重跑 或 已 `--disclose`
- 其余处方每条为 `applied` / `dismissed(--why)` / `disclosed(--why)` 之一
- `oss route` 复查后无未处置项；`run_state.stage` 推进为 `7-paper`

## 禁止
- 自己写回归脚本；探索只走 `oss run --explore --for <id>`，只许改控制变量 / 样本筛选 / 固定效应 / 聚类 / 交互 / 稳健性清单，**不许换数据**（**完整名单以门为准**，被拒时它会点名；见 `../rules/explore.md`）
- 分开跑主规格与稳健性（后一次会覆盖前一次）
- 改结局或处理变量（那是换题，走 `oss anchor switch`）
- 带着 must 级未处置往下写
- 跳过 `fig_event` / `fig_marginal`
- 一次处置多条处方（**一次一处置**：apply → run → route 复查）

## 命令
```bash
oss run --run <运行目录>
oss figure coef|collinearity|robustness [--lang en] --run <运行目录>
oss figure event --run <运行目录>
oss figure marginal --run <运行目录> --by <列> --split <列> --treatment <变量>
oss route --run <运行目录>
oss route --apply <id> | --dismiss <id> --why "..." | --disclose <id> --why "..."
```

## 卡住时读
[6-run.notes.md](6-run.notes.md)——VIF 与年龄-时期-队列恒等式、左删失、样本构建表的来历、GSS 交互项掩盖三件事的教训、"诊断只是文字，处方是动作"。
