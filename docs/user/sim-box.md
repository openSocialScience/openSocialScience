# 仿真货箱标准 v1

OSS 不认识任何模拟器（OASIS、AgentSociety……），只认一种**标准货箱**。
插头把模拟器的原始输出翻译成货箱，货箱验货通过就能 `sim: import` 进主链，
体裁自动切成仿真论文。标准定在设计稿 `docs/design/2026-08-31-sim-intake.md` 第三节，
落地代码在 `oss/core/simbox.py`；这份是它的用户版。

---

## 一、箱子里要有什么

| 文件 | 必需吗 | 装的是什么 |
|---|---|---|
| `states.csv` | 跟 `events.csv` 至少要有一张 | 每个小人每个访谈波次一行：立场分之类的"状态"。钥匙列 `condition,rep,wave,agent_id`；wave 按访谈发生顺序编号 0,1,2…，不是轮次号，真实对应第几轮记在配料单 `interview_plan` 里 |
| `events.csv` | 跟 `states.csv` 至少要有一张 | 每个动作一行：发帖、点赞、关注……必需的 8 列是 `condition,rep,step,seq,sim_time,agent_id,action,target`；去重只看其中 5 列钥匙 `condition,rep,step,seq,agent_id`。插头可另加一列 `target_kind`，标 target 这个数字是哪条命名空间的——**词表各插头自定义**（OASIS：`post`/`user`/空；AgentSociety：`post`/`agent`/空），一箱只装一个模拟器所以不相撞，跨箱比较先自己对齐；货箱契约不强求这一列在 |
| `runs.csv` | 必需 | 运行清单：这次跑是哪个条件、第几次重复、原始文件在哪、跑完了没 |
| `sim.meta.json` | 必需 | 配料单（见下节） |
| `runner.py` | 根目录 `runner.py` **或** `raw/` 下至少一份 `*.py`，二选一 | 真正驱动模拟的那份脚本；模板另存出来的样子是逐条件一份丢进 `raw/`（如 `raw/A_1.py`），这也算数，不强求根目录必须有一份通用脚本 |
| `raw/` | 建议但不强制 | 各次跑的原始输出（如 `.db`），供复查，插头装箱就读这里面的文件；`.db` 同名的 `<db 名>.run_summary.json`（跑单模板自动落的可选产物，记模型错误数与访谈缺失数）如果在，插头会把 `model_errors` 与 `interview_miss` 两个数跨场求和汇总进配料单 `packed`，并另落第三个键 `n_runs_with_summary` 记这两个数是几场跑凑出来的（不是每场都落了这份文件时，读者一眼能看出汇总不齐全），不在也不报错——老箱子没有它 |

`sim check` 认这几样是硬性的：`sim.meta.json`、`runs.csv` 缺一个都不算箱子；
跑脚本要么根目录 `runner.py`、要么 `raw/` 下至少一份 `*.py`，二选一即可；
`states.csv`/`events.csv` 两个必须至少存在一个——这一层缺了东西**直接报错，连验货报告都出不了**
（`oss sim check` 退出码 2：路径错了或箱子没搭完整，不是"验货没通过"）。

---

## 二、配料单 `sim.meta.json`：分两半

### 跑前你要填的 12 项

一项都不能空（`0`、`false` 算合法值，只有 `null`／空字符串／空列表／空对象算"空"）。
少填任何一项，验货直接拒收——「论文方法节报不出它就是不诚实」。

| 字段 | 大白话 |
|---|---|
| `simulator` | 用的哪个模拟器，如 `oasis` |
| `simulator_version` | 模拟器版本号，跟 `doctor` 锁定的那个版本一致（如 `0.2.5`） |
| `model` | 驱动小人的是哪个 LLM，如 `deepseek-v4-flash` |
| `model_provider` | 这个模型是谁家的、走哪个网关/中转站 |
| `temperature` | 温度旋钮的值；没手动设就填 `provider_default`，别留空 |
| `prompts` | 给模型看的提示词**原文**——访谈问题、角色设定，逐字抄，不是转述 |
| `population` | 小人群体从哪来、多大规模（哪份档案文件、取了多少人） |
| `conditions` | 各条件具体差在哪，别只写代号——比如「A=热度推荐 vs B=随机推荐」 |
| `n_reps` | 每个条件打算重复跑几次（必须是正整数） |
| `rounds` | 打算跑几轮行动 |
| `interview_plan` | 什么时候访谈、访谈几次、问什么——含开跑前那次基线访谈 |
| `contamination_note` | 老实说模型有没有可能见过这个议题；答案是"不确定"也要写，不能留空 |

### 跑完插头自动补的 `packed`

`oss sim pack --simulator oasis` 跑完，会往 `sim.meta.json` 里加一个 `packed` 对象，这几项不用你填：

| 字段 | 内容 |
|---|---|
| `n_state_rows` | `states.csv` 总行数 |
| `n_event_rows` | `events.csv` 总行数 |
| `n_blank_targets` | 有多少行**本该有** target 却解不出来——`create_post`/`do_nothing` 这类动作压根没有"指着谁"这回事，target 本来就是空的，不算进这个数，混进去才是不诚实 |
| `n_unplaced` | 有多少行落不进任何一轮/一波区间（行号日志没盖住这一段，多半是行号日志记漏了） |
| `file_sha256` | `raw/` 里每个原始库文件的 SHA-256 校验码，一个文件一个哈希 |

> 设计稿第三节原本设想"跑后插头补"还包含时间范围、token 用量、跑脚本哈希、时钟倍数（Clock k）
> 与 start_time——那是全项目的目标清单。**OASIS 插头 V1 目前只自动补上表这 5 项**，
> 其余那些（尤其跑脚本哈希）暂时要靠你自己写进跑前 12 项或局限里，插头不会替你补。

---

## 三、验货规则（`oss sim check`）

验货只做两件事：**该拒的一次报全**（不会修一处、再跑、再被下一处拒），**该收的收但把话说软**。

### 箱子搭得不完整，验都验不了（直接报错，不出验货单）

- 货箱目录不存在，或缺 `sim.meta.json`／`runs.csv`，或两种布局的跑脚本一份都没有（根目录 `runner.py`
  与 `raw/*.py` 都不在），或 `states.csv`／`events.csv` 两个都没有
  → 退出码 **2**，这是路径/文件错了，不是"验货没通过"
- `sim.meta.json` 不是合法 JSON（尾逗号、全角引号最常见），或顶层不是一个 `{…}` 对象
  → 退出码 **1**

### 拒收（must 级，逐条列全，退出码 7）

| 情形 |
|---|
| 配料单缺任意一项必填项（见上节 12 项） |
| `states.csv` 钥匙列不齐（`condition`/`rep`/`wave`/`agent_id` 缺一个），或 `events.csv` 缺 8 列必需列中的任意一列（`condition`/`rep`/`step`/`seq`/`sim_time`/`agent_id`/`action`/`target`；去重只看其中 5 列钥匙） |
| `states.csv`／`events.csv` 里钥匙组合不唯一——同一 `condition×rep×wave(或 step)×agent_id` 出现两行，翻译大概率出了错 |
| `runs.csv` 是空文件，连表头都没有 |
| `runs.csv` 列不齐，缺 `file`/`condition`/`rep` 之一 |
| `runs.csv` 里一条跑都没有——只有表头的清单不算运行记录 |
| `runs.csv` 某行 `file`/`condition`/`rep` 有空格子——行截断或没填全 |
| `runs.csv` 里同一 `condition×rep` 登记了两遍 |
| `runs.csv` 里的文件路径指到箱子外面（绝对路径或带 `..`），或箱子里根本找不到这个文件——箱子必须自含 |
| `runs.csv` 与配料单对不上：配料单说每条件跑 `n_reps` 次，清单里最多只登记了 `m` 次；或 `n_reps` 不是正整数 |
| `states.csv`／`events.csv` 里出现了 `runs.csv` 没登记的 `condition×rep` |
| `states.csv`／`events.csv` 一行数据都没有，而 `runs.csv` 登记了好几次跑 |
| 标 `complete` 的跑之间访谈波次对不齐——消息点名到组：「各次跑的访谈波次对不齐：多数跑有 wave 0、1、2，B/1 只有 wave 0——断粮或中途崩的跑要么补跑、要么在 runs.csv 标 complete=no」（参照集取 complete 组的众数集；不占严格多数时不说「多数跑」，逐个点名持有组） |
| 所有跑都标了 `complete` 不是 `yes`/`true`/`1`，且收集到的数据表**全部**为空——不是「收但降级能糊过去」，这个箱子没有一行可用数据 |

### 收但打警告（退出码仍是 0，但话必须说软）

| 情形 | 警告怎么说 |
|---|---|
| 某条件重复次数 < 3（`REP_FLOOR`，V1 写死，以后可能开放配置） | 「结果只能作『初步/探索性』表述，局限必写『单次模拟无法区分随机波动与真实规律』」——**这是 2026-08-31 用户拍的板**：诚实的方式不是拒收而是把话说软 |
| 全箱只有一个条件 | 「做不了机制型对比，论文类型与措辞受限」，记入 `run_state` |
| `runs.csv` 有 `complete` 列，某些行不是 `yes`/`true`/`1` | 「未完整结束的跑，数据默认不进主分析，写进局限」 |
| `runs.csv` 登记了某个 `condition×rep`，但数据表（在有其他行的前提下）没有它 | 「这些登记的跑在 `states.csv`/`events.csv` 里一行数据都没有」——提醒去 `raw/` 复查是不是漏跑或漏装箱 |
| 标了未完整的跑缺参照波次集里的某些波 | 「未完成的跑 B/1 缺 wave 1、2——其数据默认不进主分析」——诚实标了 `complete=no` 的跑缺波不拒收，只把缺口记在明面上 |
| 同一组（`condition×rep`）里某些波的小人数低于组内众数 | 「B/1 的 wave 1 只有 2/5 个小人——兜底记过 miss 的场次要在局限里说明」——跑单兜底网救活的小人那一步不写 trace 行，半波比全灭更隐蔽；这数字是不是隐形 miss，证据链在 `run_summary.json` 与 `packed` 的 `n_runs_with_summary` |

---

## 四、命令

```bash
oss sim init runs/我的仿真箱             # 建空箱：空白配料单 + 空 runs.csv 表头，等你填
oss sim check runs/我的仿真箱            # 验货：拒收逐条点名；通过时把警告如实打出来
```

```bash
oss sim pack --simulator oasis runs/我的仿真箱   # 插头装箱：raw/ 原始输出 → 两张表 + 配料单后半
oss sim import runs/我的仿真箱 --run runs/r20260831_xxx   # 验货通过后入库、留痕
```

`sim import` 是入库落章的一刻——**降级警告会在这一刻打在屏幕上**（`⚠` 开头逐条打），
不是悄悄记进文件里就算完事：凭空造出来的"通过"是最坏的一种。
