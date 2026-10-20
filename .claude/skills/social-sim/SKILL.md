---
name: social-sim
description: 从一段设计描述到验收合格的仿真货箱：整理成表确认一次、体检模拟器环境、建箱填配料单、生成跑单逐场执行、装箱验货入库。用于「跑仿真」「仿真实验」「/social-sim」「用 OASIS 跑一场模拟」「多智能体仿真」等请求。产出可直接接 `/social-research`（数据段写 sim:<箱>）走完整论文全流程。
argument-hint: "<方向>；simulator: oasis；design: <一段话> [budget: <元>] [checkpoint: design|none]"
allowed-tools: Bash(oss *), Bash(python *), Read, Write
---

# 一段话到合格货箱 · 路由器

原始输入：**$ARGUMENTS**

如果上面显示的是字面的 `$ARGUMENTS`，说明宿主不替换占位符；原始输入就是用户消息里 `/social-sim` 之后的全部内容。

**这份文件只负责路由。**完整的七步操作手册在 [reference/run-order.md](reference/run-order.md)，
**用 Read 工具按这个路径读，不要凭记忆做事**——每一步该跑哪条 `oss` 命令、
该在什么条件下停下来问用户，写在那份文件里，这里不重复。

## 第一步：拆参数

用 `---` 分段：

```
<方向> --- simulator: oasis --- design: <一段话> [--- budget: <元>] [--- checkpoint: design|none]
```

- **方向** = 第一个 `---` 之前的全部内容，一句话说清这场仿真想看什么
- **simulator** 必给，且必须在 `oss sim doctor --simulator <名>` 认得的插头名单里
  （目前有 `oasis` 和 `agentsociety` 两个插头；名单以 `oss/sims/__init__.py` 的
  `KNOWN` 为准，再加新插头也是先进那份名单）
- **design** 必给，自由文本，至少要能读出**条件、人数、每组重复次数（rep）、
  轮数、访谈波次与问题**这五样。design 里读不出的项，**停下来问用户**，
  不要替用户猜一个数字——猜错的规模直接决定花多少钱
- **budget** 可选，单位元，给了就在整理稿里跟预计费用比对；不给就只列估算，
  不做比对
- **checkpoint** 可选，只认两个值：`design`（缺省——整理稿写完停一次，等用户点头）、
  `none`（一次都不停，直接往下跑）。**不给 `checkpoint:` 时按 `design` 处理**

拆完先复述一行给用户确认：

> 方向「XXX」｜模拟器 <名>｜N 个条件 × M 次重复｜预算 Y 元（未给则省略这一项）

## 第二步：走七步操作手册

打开 [reference/run-order.md](reference/run-order.md)，从第 1 步（整理稿）开始，
按顺序做，每一步做完再看下一步要不要停下来问用户。**不要跳步**——第 4 步的跑单
是照第 1 步的整理稿盲替换出来的，跳过整理稿直接生成跑单等于拿用户没确认过的数字
去花钱。

## 铁律

① **整理稿确认前不花一分钱模型费**——第 1 步整理稿写完缺省要停一次等用户点头，
只有 `checkpoint: none` 才不停；`oss sim doctor` 是体检不是跑模拟，不算花钱，
不受这条约束。

② **不跑 `oss sim check` 不许 `import`**——第 6 步 pack 完必须先 check，
警告逐条念给用户听完，再 import；没 check 过的箱子不许进运行目录。

③ **402/余额类错误后立即全停不重试**——中转站断粮是钱的问题，重试无用；
见到 402 或"余额不足"类报错，立刻停整个流程，告诉用户去充值，
已经跑完的场次照实登记，不要因为一场没跑完就把已完成的也扔了。

④ **模板只许填槽位**——第 4 步生成跑单时，`raw/<C>_<r>.py` 里能改的只是
本模拟器模板列明的空位（OASIS 八个：七必填加可选 `SEMAPHORE`；agentsociety
九个：八必填加可选 `CONCURRENCY`——逐槽清单见手册第 4 步）；`SEED_CLAIM`、
`MODEL_TYPE`、`KNOB_MODULE` 这类写死在模板里的常量要改，必须回到第 1 步
整理稿层面，明确写出改动、征得用户同意，不能在生成跑单这一步顺手改掉。

⑤ **入库后每条 `oss` 命令带同一 `--run`**——第 6 步 `sim import` 用的是哪个
运行目录，后续（包括交给 `/social-research` 续接）的每条命令都带这同一个
`--run`，不要中途换目录。

⑥ **入库之后接回主链，计划里要声明 `research_type: simulation`**——货箱进了
运行目录只是把数据备好了；**不声明成仿真，一整套仿真专属的检查都不响**：
方法节要不要按配料单报全、降级要不要收紧措辞、结论有没有限定在模型内。
反过来，盘上有 `sim_state.json` 而计划没声明，`oss run` 会拒
（`plan.simulation.undeclared`）——**这一条对你有利**：不声明成仿真，
因果措辞检查不给「在本模型内」开豁免，你会被反复误拦。仿真只是辅助材料、
主结论在真实数据上的，写 `also: ["simulation"]`。

计划里还要写 `plan.simulation`（机制、该重现的模式），写法见
`/social-research` 环节 5；环节 4 的 `oss power` 对仿真类不算功效，
改判「这个模型跑得出可判的东西吗」，三条都只警告不停。
