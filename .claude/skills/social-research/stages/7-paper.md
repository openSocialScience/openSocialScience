# 环节 7 · 写论文

## 输入
- `<运行目录>/results.json`、`power.json`、`literature.json`、`binding.json`、`route.json`、`ideas.json`、`plan.json`
- `run_state.stage == "7-paper"`

## 产出
- `<运行目录>/paper.md`（占位符源稿，由 `social-paper` 写）
- `<运行目录>/paper_rendered.md`、`<运行目录>/paper.docx`

## 完成条件（全部满足才进环节 7.5 或 8）
- 调过 `/social-paper <运行目录> --- discipline: <学科> [--- style: <刊名>] [--- lang: zh]`（主链带了 `lang: zh` 就必须传）
- 局限那一节写了：`power.json` 判 `gray` 的功效（描述类写精度）、`route.json` 里 `disclosed` 的每一条、语料 `notes` 的覆盖缺口、代理变量、识别策略的边界
- 用了抽样调查的（`plan.survey` 写了设计，不是只写 `not_survey_reason`）：数据与方法一节写明**抽样设计**——
  权重、分层、初级抽样单位各是哪一列，标准误怎么算（按抽样设计，还是按哪一层聚类）；
  局限**按总调查误差五类逐类交代**：抽样、覆盖、无应答、测量、加工，评估不了的那类也写出来，写明为什么评估不了。
  只挑评估得了的写，读者就分不出剩下那几类是没问题还是没想过——环节 8 的论文审稿人会逐类点名缺的那类
- `paper_rendered.md` 里的数字跟 `results.json` 对得上
- `paper.docx` 在，且 export 报的"引用 N 张图、嵌入 M 张"两个数**相等**
- 描述类论文（`research_type: descriptive`，估计量 `describe`）：正文引分组描述表 `{{table:groups}}`，**报大小与区间，不只显著性**——
  描述研究的主结果是一张表，不是一个 p 值；两组分不出差别也是结果，照样写。`oss eval` 的 `descriptive-magnitude` 查有没有引这张表。
  **写跟外部基准的对照**：计划 `description.benchmarks` 里的每个已知数，本文的数跟它差多少、差得多为什么（Firebaugh「现实核查」）；
  计划没写基准的（`oss route` 开的 `reality_check_missing`），补上对照或写进局限。精度判 `gray` 或 `fail` 的，局限写明这份数据分不出小于多少的差别。
  **描述类正文的因果措辞照旧硬拦**（下一条）
- 测量类论文（`research_type: measurement`，或 `also` 里有 `measurement`）：**报了哪一类效度证据，就只能说哪一类的话**。
  四类各自支持的说法不一样——`content`（内容）说的是题目覆盖了这个构念的范围，`structural`（结构）说的是这几道题的因子结构
  跟理论对得上，`criterion`（效标关联）说的是量表分跟外部指标的关系合乎预期，`discriminant`（区分）说的是它跟别的构念分得开。
  **报了效标关联就只说效标关联的话，不许顺势说「本量表结构效度良好」**——这是环节 8 测量审稿人专门盯的一条。
  正文引的每一份效度证据都要是 `plan.measurement.evidence_outputs` 里登记过、且这次真跑出来的产出（占位符引用，不手打）
- **只报信度会被硬拦导出**：正文引用了信度那份产出（登记在 `evidence_outputs` 的 `reliability` 一行），
  而**产出里一类效度证据都没有**——`oss paper export` 拒绝，退出码 1，编号 `paper.measurement_reliability_only`。
  **信度不是效度**：信度说的是这几道题彼此一致，不是这几道题量的就是那个构念（Flake 等 2017）。
  这道门**判的是引了哪份产出、产出在不在**，不读措辞——正文写「内部一致性」「Cronbach's α」还是「题目间一致」，
  一个字都不看。出路两条：把至少一类效度证据做出来、登记上、让它真的落进结果里；或者把正文里「这套测量站得住」
  那一路说法收回去，只说信度本身支持的话
- **登记了跨组测量等价检验，那份产出就得真的在**：`evidence_outputs` 的 `equivalence` 写了名字，而这次结果里没有
  任何叫这个名字的产出——同样拒绝导出（退出码 1，`paper.measurement_equivalence_missing`）。
  计划那一侧只核「登记了没有」，**登记一个空壳就能让警告与 must 级处方整套不响**，所以导出前再核一次产出在不在。
  做不出来就把这一行去掉，并把跨组比较**降级为组内描述**——每组各报各的、不比大小，局限里写明为什么不比
- 测量类论文的**主构念要填代理差距三栏**（`binding.json` 的 `construct_def` / `measures` / `gap`），
  绑得再准也要填：「理论要什么 / 实际测什么 / 差距」正是这类研究的核心交代，而 `measurement.construct` 是一句自由文本，
  程序核不了它写得对不对，三栏是唯一的抓手。没填 `oss paper check` 警告（`paper.measurement_construct_gap`）
- 研究类型不是因果的（描述 / 测量 / 仿真，或从因果降级成描述的；识别审计判 FAIL 且替代设计穷尽、仍标因果的论文也算），**正文不下因果结论**：`oss paper check` 拦明确的因果说法（导致、造成、下结论的「存在因果关系 / 因果效应」、causes、leads to……，否定句与转述别人的发现除外），`oss paper export` 退出码 8；模糊的（影响、提高、促进……）列进 `causal_flags.json`，交识别审稿人判
- `oss paper export` 退出码 8**不止管因果措辞**，它是「正文的因果说法一族」：因果论文用了弱工具（有效 F 低于下界）却没在主结果的区间引 `{{diag:AR 区间}}`，同样判退出码 8——收到这个退出码先看 `problems` 说的是哪一条：措辞门要改的是软化说法，AR 那条要改的是**补上 `{{diag:AR 区间}}` 这个引用**，不是改措辞
- `run_state.stage` 推进为 `7.5-identification`（研究类型是因果，**或锚点冻结时是因果、后来降级成描述的**，**或运行目录有 `sim_state.json`**（仿真货箱，不论类型）——以 7.5 的「输入」为准；老计划没写类型的照旧按估计量判）或 `8-review`

## 禁止
- 手打数字（正文一律占位符）
- 把研究过程写进正文（审计、诊断、判词、R3 规格、文件名、`oss` 命令）
- 改渲染稿：改稿改 `paper.md` 再重新渲染
- 校验没过就渲染、渲染没过就导出

## 命令
```
/social-paper <运行目录> --- discipline: <学科> [--- style: <刊名>] [--- lang: zh]
```
```bash
oss paper check  --run <运行目录>
oss paper render --run <运行目录>
oss paper export --run <运行目录>
```

## 数据来自仿真货箱时

调 `/social-paper` 时**明说这是一篇仿真论文**——运行目录里有 `sim_state.json`
不算你说过，指令里要点明。具体措辞怎么改、方法节怎么按配料单报、降级句怎么
写、局限里的五件套是什么，见 `.claude/skills/social-paper/reference/simulation.md`，
这里不重复。

导出前的 `oss paper check` 对仿真论文会额外拦三件事：模拟器有没有被引用、
rep 数有没有报、有没有对现实世界的直接断言——运行目录里有 `sim_state.json`
这道门就开着：前两件缺了按拒收拦，第三件逐句警告不拦死。具体查法、消息
怎么说以 `oss paper check` 的实际输出为准，不在这份说明书里写死。

## 卡住时读
[7-paper.notes.md](7-paper.notes.md)——回来之后要确认的三件事。更细的写作规矩在 `social-paper` 技能自己的 `reference/` 里。
