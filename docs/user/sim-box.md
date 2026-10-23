# 仿真货箱标准 v1

OSS 读取模拟结果只用一种格式，叫货箱。货箱是一个目录，里面放数据表、运行清单、配料单和驱动模拟的脚本。

OASIS、AgentSociety 这些模拟器的原始输出，先用各自的插头转成货箱（`oss sim pack`），再验货（`oss sim check`）。
验货通过后，用 `oss sim import` 导入研究流程，论文会自动按仿真论文的体例写。

---

## 一、箱子里要有什么

| 文件 | 是否必需 | 内容 |
|---|---|---|
| `states.csv` | 和 `events.csv` 至少有一张 | 每个智能体在每次访谈各占一行，记立场分这类状态 |
| `events.csv` | 和 `states.csv` 至少有一张 | 每个动作一行，如发帖、点赞、关注 |
| `runs.csv` | 必需 | 运行清单：每次跑的条件、第几次重复、原始文件在哪、有没有跑完 |
| `sim.meta.json` | 必需 | 配料单，见第二节 |
| 跑脚本 | 必需，两种放法任选 | 根目录放一份 `runner.py`，或在 `raw/` 下放至少一份 `*.py` |
| `raw/` | 建议有 | 每次跑的原始输出（如 `.db`），留着复查，插头装箱时读的就是这里的文件 |

**`states.csv`** 的钥匙列是 `condition,rep,wave,agent_id`。`wave` 是访谈的序号，按先后从 0 编起；
每次访谈落在模拟的第几轮，写在配料单的 `interview_plan` 里。

**`events.csv`** 必须有 8 列：`condition,rep,step,seq,sim_time,agent_id,action,target`。
判断重复只看其中 5 列：`condition,rep,step,seq,agent_id`。

插头还会多写一列 `target_kind`，说明 `target` 里的编号指的是帖子还是人。取值由各插头自己定：
OASIS 用 `post`、`user` 或空，AgentSociety 用 `post`、`agent` 或空。一个箱子只装一个模拟器的输出，箱内不会混；
跨箱比较之前要自己对齐。验货不要求这一列存在。

**跑脚本**用跑单模板生成时，是每个条件一份，放在 `raw/` 里（如 `raw/A_1.py`），这样就够了，根目录可以不放 `runner.py`。

**`raw/`** 里如果有和 `.db` 同名的 `<db 名>.run_summary.json`（跑单模板会自动生成，记模型报错次数和访谈缺失次数），
插头会把各次跑的 `model_errors`、`interview_miss` 分别加总，写进配料单的 `packed`，
同时写一个 `n_runs_with_summary`，记这两个数来自几次跑。有的跑缺这个文件时，从这个数能看出汇总少了几次。
没有这个文件也不报错。

缺了必需的文件，验货直接报错，见第三节开头。

---

## 二、配料单 `sim.meta.json`

配料单分两部分：跑之前你填的 12 项，跑完以后插头补的 `packed`。

### 跑之前填的 12 项

每项都要填。`0` 和 `false` 算填了，`null`、空字符串、空列表、空对象算没填。
缺一项验货就拒收，因为论文的方法部分要逐项报告这些信息。

| 字段 | 填什么 |
|---|---|
| `simulator` | 模拟器名，如 `oasis` |
| `simulator_version` | 模拟器版本，和 `oss sim doctor` 锁定的版本一致，如 `0.2.5` |
| `model` | 驱动智能体的 LLM，如 `deepseek-v4-flash` |
| `model_provider` | 模型厂商，以及走的哪个 API 网关 |
| `temperature` | 温度参数；没有手动设过就填 `provider_default` |
| `prompts` | 给模型的提示词原文，包括访谈问题和角色设定，逐字照抄 |
| `population` | 智能体从哪来、有多少：用的哪份档案文件，取了多少人 |
| `conditions` | 每个条件具体是什么，如「A=热度推荐，B=随机推荐」，只写代号不够 |
| `n_reps` | 每个条件计划重复几次，正整数 |
| `rounds` | 计划跑几轮行动 |
| `interview_plan` | 什么时候访谈、访谈几次、问什么，包括开跑前的基线访谈 |
| `contamination_note` | 模型训练时有没有可能见过这个议题；拿不准就写「不确定」 |

### 跑完以后插头补的 `packed`

`oss sim pack --simulator oasis` 跑完，会在 `sim.meta.json` 里加一个 `packed` 对象，下面 5 项不用你填：

| 字段 | 内容 |
|---|---|
| `n_state_rows` | `states.csv` 的行数 |
| `n_event_rows` | `events.csv` 的行数 |
| `n_blank_targets` | 应该有 `target`、却没解析出来的行数。`create_post`、`do_nothing` 这类动作本来就没有对象，不计入 |
| `n_unplaced` | 落不进任何一轮或一波的行数，多半是行号日志漏记了这一段 |
| `file_sha256` | `raw/` 里每个原始数据库文件的 SHA-256，一个文件一个 |

跑的时间范围、token 用量、跑脚本的哈希、时钟倍数和 `start_time`，插头不会自动写。
需要的话自己写进上面 12 项或论文的局限部分，跑脚本的哈希尤其值得记上。

---

## 三、验货规则（`oss sim check`）

验货会把所有拒收理由一次列全，照着改完再跑一遍就行。
有缺陷但还能用的箱子照收，同时打出警告，论文的措辞要按警告收着写。

### 箱子不完整：直接报错，不出验货单

- 货箱目录不存在，缺 `sim.meta.json` 或 `runs.csv`，跑脚本两种放法都没有
  （根目录没有 `runner.py`，`raw/` 下也没有 `*.py`），或者 `states.csv` 和 `events.csv` 都没有，退出码是 2。
  这说明路径或文件有问题，箱子还没到验货那一步。
- `sim.meta.json` 不是合法 JSON（最常见的是多了结尾的逗号，或用了全角引号），或者最外层不是一个 `{…}` 对象，退出码是 1。

### 拒收（must 级，退出码 7）

- 配料单缺任何一项必填项（第二节的 12 项）。
- `states.csv` 缺钥匙列（`condition`、`rep`、`wave`、`agent_id` 任一），或 `events.csv` 缺 8 列必需列中的任一列。
- 钥匙组合有重复：`states.csv` 里同一组 `condition×rep×wave×agent_id` 出现两行，或 `events.csv` 里同一组 5 列钥匙出现两行。多半是插头转换出了错。
- `runs.csv` 是空文件，连表头都没有。
- `runs.csv` 缺 `file`、`condition`、`rep` 中的任一列。
- `runs.csv` 只有表头，没有一次跑的记录。
- `runs.csv` 某行的 `file`、`condition`、`rep` 有空格子，通常是行被截断或没填完。
- `runs.csv` 里同一个 `condition×rep` 登记了两次。
- `runs.csv` 里的文件路径指到了箱子外面（绝对路径或带 `..`），或者箱子里找不到这个文件。箱子要自带全部文件。
- `runs.csv` 和配料单对不上：配料单说每个条件跑 `n_reps` 次，清单里最多只登记了 `m` 次；或者 `n_reps` 不是正整数。
- `states.csv` 或 `events.csv` 里出现了 `runs.csv` 没登记的 `condition×rep`。
- `states.csv` 和 `events.csv` 一行数据都没有，`runs.csv` 却登记了好几次跑。
- 标为完成（`complete`）的几次跑，访谈波次不一致。比较的基准是这些跑里最常见的那组波次。
  报错会点出是哪几组，处理办法有两个：补跑，或者在 `runs.csv` 里把它标成 `complete=no`。
- 没有一次跑标为完成（`complete` 是 `yes`、`true` 或 `1` 才算），数据表也全是空的。这个箱子没有一行能用的数据。

### 收下，但打出警告（退出码 0）

| 情形 | 警告内容 |
|---|---|
| 某个条件重复不到 3 次 | 结果只能写成初步的、探索性的发现，局限里要写明「单次模拟无法区分随机波动与真实规律」 |
| 整个箱子只有一个条件 | 做不了机制对比，论文类型和措辞会受限，这一条记入 `run_state` |
| `runs.csv` 有 `complete` 列，有的跑没标完成 | 这些跑的数据默认不进主分析，要写进局限 |
| `runs.csv` 登记了某个 `condition×rep`，数据表里却没有它的行（数据表里有其他行时才查这一条） | 列出这些跑，提醒去 `raw/` 查是漏跑了还是漏装了 |
| 没跑完的跑缺了基准里的某些波次 | 列出缺的波次，如「B/1 缺 wave 1、2」；这些跑的数据默认不进主分析 |
| 同一组（`condition×rep`）里，某些波次的智能体比这组常见的人数少 | 写出是哪一组哪一波、到了几个人，如「B/1 的 wave 1 只有 2/5」，要在局限里说明 |

最后一条要多看一眼。跑单模板有兜底机制，智能体出错时会被救回来，但那一步不写 trace 行，所以这一波看上去少了人。
少几个人比整波缺失更不容易发现。要判断是不是这种情况，看 `run_summary.json` 和 `packed` 里的 `n_runs_with_summary`。

---

## 四、命令

```bash
oss sim init runs/我的仿真箱             # 建空箱：空白配料单和只有表头的 runs.csv
oss sim check runs/我的仿真箱            # 验货：列出全部拒收理由，通过时打出警告
```

```bash
oss sim pack --simulator oasis runs/我的仿真箱   # 插头装箱：把 raw/ 里的原始输出转成两张表，补上配料单的 packed
oss sim import runs/我的仿真箱 --run runs/r20260831_xxx   # 验货通过后导入研究流程，留下记录
```

`sim import` 导入时，降级警告会以 `⚠` 开头逐条打在屏幕上。
