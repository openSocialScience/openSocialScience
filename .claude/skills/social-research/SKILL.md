---
name: social-research
description: 从一个研究方向和一份或多份数据，产出一篇社科定量论文。串起数据画像、文献检索、找研究空白、可行性检验、计量分析、论文写作全流程。用于「做个社科研究」「帮我从这份数据出一篇论文」「社科选题到成稿」等请求。
argument-hint: "一句话说你想研究什么；不确定就直接回车，我来问你"
allowed-tools: Bash(oss *), Read, Write, Edit, Glob, Grep
---

# 社科定量研究主链 · 路由器

原始输入：**$ARGUMENTS**

如果上面显示的是字面的 `$ARGUMENTS`，说明宿主不替换占位符；原始输入就是用户消息里 `/social-research` 之后的全部内容。

**这份文件只负责路由。**每个环节的契约在 `stages/`，跨环节的硬规矩在 `rules/`，散文、教训、例子在
`stages/<环节>.notes.md`。**用 Read 工具按相对本文件的路径读，不要凭记忆做事。**

## 第一步：拆参数

**用户不必学任何符号。**三种进来的方式都要认——什么都没打（**直接进访谈**，一样一样问）、
一句人话（**自己理解，再复述一行等他点头**）、带 `---` 的完整写法（机械拆）。
访谈怎么问、为什么「你问的是关于国家的事还是关于人的事」比问 `data:` 有用：
读 [rules/input-and-rundir.md](rules/input-and-rundir.md)。

带 `---` 时这么拆：`研究方向 --- data: <文件，逗号分隔，或 pack:<名>> --- discipline: <学科> [--- lang: en|zh] [--- style: <刊名>] [--- refs: <自带文献：题录文件 / PDF / PDF 目录，逗号分隔>] [--- idea: <你已经有的想法原文>] [--- design: <五个键>] [--- type: 因果|描述|测量|仿真] [--- checkpoint: idea,ideas,design,none]`

- **data 没给就停下来问用户**，别猜；`discipline` 缺省 `economics`；**`lang` 缺省 `zh`**——产出**中文论文 + 中文期刊体例的图**（贯穿环节 5、7：稳健性 label 用中文、图 `--lang zh`、`/social-paper` 带 `--- lang: zh`）。**用户说要写英文论文才传 `en`**，那时图也跟着走英文体例。文献检索**不受它影响**（中英文献都要）
- **带 `idea:` 就是自带想法模式**：环节 3 不出题，只把用户那条想法整理成唯一的 `idea_001`；`design:` 给的键锁进 `plan.json`。拆法、`design:` 五个键与 `plan.json` 字段的映射表：读 [rules/input-and-rundir.md](rules/input-and-rundir.md)
- 多份数据怎么写、`pack:` 怎么拿、对照表怎么传：读 [rules/input-and-rundir.md](rules/input-and-rundir.md)
- **一律复述一行给用户确认**：`方向「XXX」｜数据 x.csv + y.csv｜学科 economics｜语言 zh`（语言 en 可省）。
  说人话进来的**必须等他点头再往下**——理解可能读错，读错了他一眼看得见，
  不复述就是跑了七十分钟才发现用错了数据

## 第二步：定运行目录

**每个 `oss` 命令都要带同一个 `--run`；永远不要 `cd`。**理由与两次事故见 [rules/input-and-rundir.md](rules/input-and-rundir.md)。

```bash
mkdir -p runs/r20260829_1530        # 名字你定，之后不再改
```

## 第三步：读状态，能续就续

开工先看运行目录里有没有 `run_state.json`：

```bash
cat <运行目录>/run_state.json 2>/dev/null; oss loop status --run <运行目录>
```

- 有，且 `stage_status` 不是 `done` → 从 `stage` 指的那个环节**续跑**，不从头来
- 有 `anchor` → 那就是本次要回答的问题，**不许改**；换题只能 `oss anchor switch --to <idea_id> --why ... --authorized-by user`
- `review.round` 是真实轮数，**以它为准，不凭记忆**
- 没有 → 新开
- `stage` 是空的（环节 1–5 不写状态）→ 看目录里已有哪些产出（`profile.json` → `literature.json` → `screening.json` → `reading_cards.json` → `ideas.json` → `power.json` → `plan.json` → `results.json`），从最后一份的**下一**环节续，别重跑已有的
- 这一步只看 `阶段` / `锚点` / `轮次` 三行；**`决定` 那一行只在环节 8 之后才作数**

上下文被压缩之后也一样：先读这个文件，再决定下一步。

## 第四步：按 manifest 加载

读 [manifest.yaml](manifest.yaml)，照它做三件事：

1. `always_load` 里的五份 `rules/` **每次都读**——它们是跨环节的硬规矩
2. 按 `run_state.stage` 在 `axes.stage.values` 里查到本环节的契约，**只读这一份**
3. 契约的「完成条件」全满足才推进到下一环节；卡住了再按 `references.on_demand` 读对应的 `.notes.md`

| stage | 环节 | 契约 |
|---|---|---|
| `1-profile` | 数据画像 | [stages/1-profile.md](stages/1-profile.md) |
| `2-literature` | 文献检索与精读 | [stages/2-literature.md](stages/2-literature.md) |
| `3-ideas` | 找空白、想 idea、补数据 | [stages/3-ideas.md](stages/3-ideas.md) |
| `4-feasibility` | 绑构念、算功效 | [stages/4-feasibility.md](stages/4-feasibility.md) |
| `5-plan` | 写分析计划 | [stages/5-plan.md](stages/5-plan.md) |
| `6-run` | 跑分析与处置 | [stages/6-run.md](stages/6-run.md) |
| `7-paper` | 写论文 | [stages/7-paper.md](stages/7-paper.md) |
| `7.5-identification` | 因果设计审计（研究类型是因果、从因果降级来的，或仿真货箱时；细则见该环节「输入」） | [stages/7.5-identification.md](stages/7.5-identification.md) |
| `8-review` | 审稿—改—复审 | [stages/8-review.md](stages/8-review.md) |
| `9-wrapup` | 自评与收尾 | [stages/9-wrapup.md](stages/9-wrapup.md) |

## 跨环节的硬规矩（`always_load`）

| 文件 | 管什么 |
|---|---|
| [rules/iron-laws.md](rules/iron-laws.md) | 四条铁律：判断归你计算归 `oss`、不写死宿主、你只写四个文件、正文不许裸数字 |
| [rules/input-and-rundir.md](rules/input-and-rundir.md) | 参数怎么拆、数据怎么拿、运行目录的两条脾气 |
| [rules/stop-conditions.md](rules/stop-conditions.md) | 循环以 `oss loop status` 第一行的决定为准；穷尽之后保留设计 |
| [rules/ask-user.md](rules/ask-user.md) | 提问白名单；三个 checkpoint 开关 |
| [rules/explore.md](rules/explore.md) | 想试一试只走 `oss run --explore --for`；不许用 python 直接跑回归 |

## 开工前先跑一次

```bash
oss doctor
```

**这个系统还在分阶段建设中，部分环节返回假数据。**它会告诉你哪些环节已经是真的、哪些还是假的。
**不要写死在记忆里**——每完成一个阶段这个名单就变。产出里带 `FAKE_` 前缀的地方就是还没换真的。
