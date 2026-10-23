# 七步操作手册：一段话到合格货箱

**每一步都写清楚做什么、跑哪条命令、什么条件下要停下来问用户。**
按顺序做，不跳步——第 4 步的跑单是照第 1 步的整理稿盲替换出来的，
没有整理稿就没有可信的槽位值。

货箱本身长什么样（`sim.meta.json` 十二项、`states.csv`/`events.csv` 的必需列、
拒收/降级规则）不在这里重复，见 `docs/user/sim-box.md`；模拟器怎么装、
`oss sim doctor` 三条各查什么、样本档案在哪，见各自的安装单——OASIS 见
`docs/user/sim-oasis.md`，AgentSociety 见 `docs/user/sim-agentsociety.md`。

**这是一份手册，不是两份**：七步对两个模拟器是同一套（整理稿→体检→建箱→
跑单→执行→装箱→收尾），只在行为真有差异的地方标「分模拟器」或
「仅 agentsociety」岔路口；没标岔路的段落，两家一样办。

---

## 1. 整理稿

把用户 `design:` 段的自由文本整理成一张表，不改用户的意思，只是把散文变成
能核对的结构：

| 栏 | 内容 |
|---|---|
| 条件×rep 清单 | 每个条件跑几次重复，逐条列出（如 A×3、B×3——共 6 场） |
| 轮数 rounds | 行动轮数，不含访谈波 |
| 人数 agents | 每场取样本档案的前多少人 |
| 访谈波次与问题 | 除第 0 波基线外还访谈几次、在第几轮后、问题原文逐字抄 |
| 并发旋钮（分模拟器） | design 里给了就填那个数；**没给就写"缺省 8"**（两家模板的
  缺省值都是 8）。槽名分家：OASIS 是第八空位 `SEMAPHORE`（不是 32——2026-08-31
  真跑验证 32 在长上下文末波压垮过中转站）；
  agentsociety 是第九空位 `CONCURRENCY`，跑单把它设成环境变量
  `AGENTSOCIETY_LLM_RAY_CONCURRENCY`
  （上游默认 16 且带 AIMD 自适应，但 AIMD 只保证垮了能降、不保证垮之前不疼——
  同一段中转站史主张保守起步 8） |
| CONDITION_KNOB（仅 agentsociety） | 条件差异走哪个旋钮值：缺省旋钮是
  `social_media` 模块的 `feed_source`，只认 `global`（全站帖子池）与
  `following`（只看关注的人+自己），逐条件写明填哪个值；要换成别的 kwarg
  得手改模板 `KNOB_MODULE`/`KNOB_KEY` 常量——那是整理稿层面的改动，不是填空位 |
| TICK（仅 agentsociety） | 每步推进的模拟时钟秒数；design 给了就填那个数，
  **没给就写"缺省 900"**（上游 15 分钟/步档位）；秒换算连波次表一起进配料单
  `interview_plan` |
| 预计费用 | 见下方公式 |

design 里读不出条件、人数、rep 次数、轮数、访谈波次与问题这五样中的任何一样，
**停下来问用户**，不要替用户猜——猜错的规模直接决定花多少钱。

**必问清单（这两项 design 原文基本不会提，也不能替用户猜，直接问）**：

- **`contamination_note`**——这个议题模型有没有可能在训练数据里见过，是研究
  判断，只有用户能声明；design 里没提就直接问，答案哪怕是"不确定"也要用户
  自己说出口，不能替他填一个敷衍的"不确定"
- **`model_provider`**——跑这场模拟走的是哪家网关/中转站，同样问不出就不能瞎填

这两项现在问清楚，第 3 步填 `sim.meta.json` 时就不用回头再问一次。

**预计费用怎么算——分模拟器，两条路径一实测一未实测，不许混用**：

**oasis**（跑单模板 `runner_template.py` 里印出的 cost hint 公式，
按 `docs/user/sim-oasis.md` §七的实测表校正过）：安装单 §七第一行"46 人 ×
10 轮，两个条件各跑一场……两场合计约 3 元"说的是**两场加起来**，不是一场——
单场基准要除二，**约 1.5 元**；时长看并发：并发 32 时约 5 分钟/场（V0），缺省并发 8 时约 13-14 分钟/场（2026-09-01 验收轮 50 人×10 轮实测）。按人数 × 轮数的比例折算单场费用，
再乘以整理稿里的总场次数（= 各条件 rep 数之和，也就是将来 `runs.csv` 的行数）：

```
单场费用估算 ≈ 1.5 元 × (本场人数 × 本场轮数) / (46 × 10)
总费用估算   ≈ 单场费用估算 × 总场次数
```

**这是量级估算不是报价**：拿 §七第二行校验过——100 人 × 20 轮跑 6 场，按这条
公式算出的总费用约 39 元，实测区间是"约二三十元"（20–30 元），估算落在实测
的 2 倍以内，量级对得上但不是精确值；跟用户交代费用时要说清楚这是估算上限
参考，真实账单以中转站网关自己的用量页为准。

**agentsociety（未实测——只给量级框架，不编元数）**：本插头没烧过真钱，
整理稿的费用行**不许出现具体元数**，只写调用量框架加量级预警：模型调用次数
≈ `N_AGENTS × ROUNDS × k`，ReAct 智能体每 agent 每 step 约来 3-10 次模型调用
（感知/反思/行动各来几轮），k 取 3-10；OASIS 同规模每 agent 每轮约 1 次，
所以**同规模成本约为 OASIS 的 3-10 倍量级**——要报区间，拿上面 OASIS 公式的
结果乘 3-10，并明说这是未实测的量级推算不是报价。**先小场铁则**：真跑必须
先小规模试水（如 5 人 × 2 轮）+ 中转站余额确认（跑单开跑会自己打印这条
cost hint），试水通过才许开正式场；试水与正式跑完之后拿中转站用量页的实测
数字回填 `docs/user/sim-agentsociety.md` §七 的费用表——回填前那张表就该空着。

整理稿连同预计费用一起写给用户看，**有 `budget:` 就摆在旁边比对**，超预算要
点名；没有 `budget:` 就只列估算，不做比对。

**停下来的条件**：`checkpoint: none` 才不停；没写 `checkpoint:` 或写了
`checkpoint: design`，整理稿写完都要停一次，等用户点头才能往下走。
**整理稿确认前不花一分钱模型费**——这一步之前不许跑任何会调用模型的命令。

---

## 2. 体检

```bash
oss sim doctor --simulator <名>
```

`<名>` 填整理稿定的模拟器：`oasis` 就跑 `oss sim doctor --simulator oasis`，
`agentsociety` 就跑 `oss sim doctor --simulator agentsociety`——体检哪家，
后面第 6 步 `pack --simulator` 就是哪家，中途不换名。

**记住这条命令的退出码永远是 0**——不管三条体检过没过，都不会非零退出
（唯一的例外是 `--simulator` 拼错成不认识的名字）。**不能看退出码判断，
只能看每一行前面的 `✓`/`✗`。**

- 全部 `✓` → 往下走第 3 步
- 有 `✗` → 按**本模拟器安装单**对应小节的指引处理（oasis 见
  `docs/user/sim-oasis.md`，agentsociety 见 `docs/user/sim-agentsociety.md`；
  包可导入、样本数据在、网关变量在，三条各自的坑都写在安装单里。特别地，
  agentsociety 分环境装法下「包可导入」一条大概率 `✗`——那不是没装好，是
  doctor 在错的环境里找包，见其安装单 §五）；**修完之后要
  重新跑一遍同一条 `oss sim doctor --simulator <名>` 确认这条也变成 `✓` 了，
  再进第 3 步**——不能凭"应该修好了"的印象往下走
- 处理不了（比如用户机器上没装 conda 环境，短时间修不好）→
  **停下来问用户**，不要跳过体检硬着头皮往下跑

---

## 3. 建箱填单

```bash
oss sim init <箱目录>
```

建出的是空箱：空白 `sim.meta.json`（十二项必填，先前定的一项都不能空）+
空 `runs.csv` 表头 + 空 `raw/` 目录。照整理稿把 `sim.meta.json` 十二项逐一填
好——字段名和"大白话"对照表见 `docs/user/sim-box.md` 第二节，不要凭记忆瞎填
字段名。十二项里有三项不用问用户、也不用猜，来源是固定的：

| 字段 | 从哪来 |
|---|---|
| `simulator_version` | 照本模拟器插头 `profile.json` 的 `version_locked` 填（doctor 的打印里没有这个名字；agentsociety 另可对跑目录侧录的 `meta_sidecar.json` 核对包版本） |
| `model` | 本模拟器跑单模板的 `MODEL_TYPE` 常量（两份模板当前都是 `deepseek-v4-flash`——模板没改这个值就一直是它）。agentsociety 的跑单还会把它连同 `api_base`、包版本一起侧录进跑目录的 `meta_sidecar.json`，装箱时照抄核对即可 |
| `temperature` | 写 `provider_default`——OASIS 模板没有单独设温度参数，走的是网关缺省值；agentsociety 上游压根没有温度旋钮（连全局 seed 也没有，设计上不可复现、靠重复次数，见其安装单 §六），同样填 `provider_default` |

其余各项（`contamination_note`/`model_provider` 已在第 1 步问过；`prompts`/
`population`/`conditions`/`n_reps`/`rounds`/`interview_plan` 照整理稿表格抄；
`simulator` 照整理稿填 `oasis` 或 `agentsociety`）照整理稿填即可。
agentsociety 的 `interview_plan` 记得带上 tick 秒换算与波次↔轮次对照表
（跑单侧录的 `meta_sidecar.json` 里有现成的一句，照抄）。

再把样本档案拷进货箱——**这一步分模拟器**，文件名与格式都不同：

**oasis**：改名成 `profiles_full.csv`（`docs/user/sim-oasis.md` §四的命令，
`runner_template.py` 的 `trim_profiles()` 就认这一个文件名）：

```bash
cp ~/Documents/oasis/data/twitter_dataset/anonymous_topic_200_1h/False_Business_0.csv \
   <箱目录>/raw/profiles_full.csv
```

**agentsociety**：改名成 `profiles_full.json`（`docs/user/sim-agentsociety.md`
§四的命令，跑单的 `load_profiles()` 就认这一个文件名；样本是 JSON 数组
100 人，每条记录带整数 `id`——档案**不随 pip 包分发**，得从克隆仓库拷）：

```bash
cp ~/Documents/agentsociety/packages/agentsociety2/profiles.json \
   <箱目录>/raw/profiles_full.json
```

换成自己的人群档案也可以，列名/字段要跟本模拟器认的一致（见各自安装单）。

---

## 4. 生成跑单

用 Read 工具读一遍**本模拟器**跑单模板的原文——模板文件**是数据不是代码**，
槽位清单分模拟器：

**oasis**（`oss/sims/oasis/runner_template.py`）：八个空位（七必填一可选）：
`CONDITION`、`REP`、`RECSYS`、`ROUNDS`、`N_AGENTS`、`INTERVIEW_Q`、
`INTERVIEW_WAVES` 七个必填，`SEMAPHORE` 第八个可选（不填时模板自己缺省 8）。

**agentsociety**（`oss/sims/agentsociety/runner_template.py`）：九个空位（八必填一可选）：
`CONDITION`、`REP`、`CONDITION_KNOB`、`ROUNDS`、`TICK`、`N_AGENTS`、
`INTERVIEW_Q`、`INTERVIEW_WAVES` 八个必填，`CONCURRENCY` 第九个可选（不填时
模板自己缺省 8）。`INTERVIEW_WAVES` 填逗号分隔的轮次号（如 `5,10`）；第 0 波
基线是模板的固定动作，不写进去。

对整理稿里**每一个条件 × rep 组合**，把模板原文的全部必填空位**盲替换**成
对应的真实值——盲替换的意思是只做逐字符串替换，不改模板其他任何一行；
`design:` 里给了并发数才连可选的并发空位（OASIS 的 `SEMAPHORE`／agentsociety
的 `CONCURRENCY`）一起填，没给就留着让模板自己缺省。替换完另存成货箱
`raw/<条件>_<rep>.py`（如 `raw/A_1.py`、`raw/A_2.py`、
`raw/B_1.py`……跟整理稿第 1 步列的条件×rep 清单一一对应）。

**agentsociety 的布局多一层**：填好的脚本跑起来会在自己旁边建**并排的跑目录**
`raw/<条件>_<rep>/`（replay/ 分片、artifacts/ 问卷、`run_summary.json`、
`meta_sidecar.json` 都落在里面），一场一目录——脚本与目录同名并排是箱规
（见插头 `adapter.py` 顶注），目录由脚本自己建（残跑会先清场重来），
不用你手建，也别把别场的输出挪进去。

**授权修改仅限模板列明的空位**——OASIS 的 `SEED_CLAIM`、`MODEL_TYPE`，
agentsociety 的 `KNOB_MODULE`/`KNOB_KEY`（条件旋钮注进哪个模块的哪个
kwarg）、`MODEL_TYPE`、`QUESTION_ID`/`RESPONSE_TYPE`（单题问卷的题号与答案
类型）这类模板里写死的常量，要改必须回到第 1 步整理稿层面，明确写出改动、征得用户同意，
不能在这一步顺手改掉。

每份另存出来的脚本都要语法自检一遍，确认盲替换没有破坏 Python 语法：

```bash
python -c "import ast; ast.parse(open('<箱目录>/raw/A_1.py').read())"
```

对每一份 `raw/<C>_<r>.py` 都跑一遍这条检查，有语法错误就回去看是不是替换
漏了引号或空位符没对齐。

---

## 5. 逐场执行

**一次一个，不许并行**——同时开几场会把中转站的并发请求叠加起来，
2026-08-31 真跑验证就是并发太高才把中转站压垮的（见第 1 步并发旋钮行缺省 8
的理由；agentsociety 的 AIMD 自适应只护它自己一场之内的并发，护不住你同时
开几场叠出来的总量，这条铁律两家一样守）。

```bash
BASE=<中转站 URL> KEY=<中转站 key> python raw/A_1.py
```

`BASE`/`KEY` 从项目根 `.env` 的 `REVIEWER_BASE_URL`/`REVIEWER_API_KEY` 取值
（`.env` 不会自动帮你转成 `BASE`/`KEY` 这两个名字，跑之前自己 export 或在
命令行内联给出）。**这对名字两家跑单通用**：oasis 见 `docs/user/sim-oasis.md`
§三；agentsociety 见 `docs/user/sim-agentsociety.md` §三——它的跑单启动时把
`BASE`/`KEY` 映射成 `AGENTSOCIETY_LLM_API_BASE`/`API_KEY` 再喂给 CLI 子进程，
别自己预先 export `AGENTSOCIETY_LLM_*` 代替这两个名。

**跑的过程中盯什么（分模拟器——两家的进度行长得不一样）**：

- **oasis**：盯终端打印的 steplog 行（`[HH:MM:SS] {"kind": ...,
  "round"/"wave": n, ...}`）和 `[interview-miss]`/`[done]` 这些行；
- **agentsociety**：**没有 steps.jsonl，也没有逐条的 `[interview-miss]` 行**——
  跑单把 CLI 子进程的输出逐行转印出来（上游 logger 的 `[时间] 级别 消息`
  格式行就是进度），跑单自己的行只有 `[cost hint]`（开跑前的未实测费用
  预警）、`[清场]`（有残跑目录才出现）、`[启动]`、钱类横幅、`[run summary]`、
  `[meta sidecar]`，以及**只在 CLI 成功退出后才打印**的
  `[done] condition=... rep=...` 收尾行。它的「访谈缺失」不逐行报：模型都
  答了、答案解析不出的算 parse 失败，跑完结算进 `run_summary.json` 的
  `interview_miss` 字段（见本步末尾）。

不管哪家，实时把进度转述给用户，不要一声不吭等它跑完。

**信号判别（三句，跟下面的失败阶梯统一口径——这是最容易看错的一步）**：

① 单独一行 `[interview-miss] agent N: <类名>: <异常文字前200字>`（这种行
只有 oasis 有；agentsociety 的个体缺失是跑完结算的 parse 失败，不逐行打印）
是模板内置兜底已经接住的个体缺失——该小人这一波记为缺失，访谈照常继续，
照常转述给用户，**不是**触发重试阶梯的条件，看到它不要去重跑或减半。

② 重试阶梯只有一个触发条件：`python raw/X.py` 这个进程在打印 `[done]` 之前退出
（连接死了、进程被杀、终端直接报错退回 shell）——阶梯判的是"这场跑完没完成"，
不是"这场里有没有 miss"。

③ 但每一条 `[interview-miss]` 后面跟的异常文字（agentsociety 则是转印出来
的每一行 CLI 输出）都要读一遍——出现钱类字样，哪怕进程还活着还在继续跑，
也要立即触发铁律③全停，不等进程退出、不等这场跑完。**钱类词表以
agentsociety 跑单 `_MONEY_SIGNS` 的加长版为准，两家场次通用**：402、
Insufficient、"余额不足"/"欠费"、PermissionDenied、"token quota is not
enough"，以及**同一行里 403 配 quota**——2026-09-01 OASIS 扩样实跑撞过的
就是 403 quota 马甲（HTTP 403 报「token quota is not enough」，一个老词都
不含，当时的词表险些放行下一场，见 `docs/user/sim-agentsociety.md` §六
坑表）。agentsociety 跑单见到这些词会自己打醒目横幅并在结尾复吼；oasis
场次没有自动横幅，全靠你照这份词表逐行读。

**失败处置梯度**（用户已批准的三级，触发条件见上面②——不是看到 miss 就触发）：

1. 进程在打印 `[done]` 之前就退出 → **整场重跑一次**
2. 重跑还是没打印 `[done]` → 把并发空位（oasis 的第八空位 `SEMAPHORE`／
   agentsociety 的第九空位 `CONCURRENCY`）填一个更小的数（原值**减半**，
   没填过就从缺省 8 减到 4）另存一份，再跑一次
3. 减半重跑还是没打印 `[done]`（第三次）→ 在 `runs.csv` **标 complete=no 继续**跑
   下一场，不要在一场跑上无限烧钱

**悬挂守卫**：进程既没退出也没报错，但进度行（oasis 看 steplog 行，
agentsociety 看转印的 CLI 输出行）**连续 10 分钟没有新行**（既没
新的进度记录、也没新的 `[interview-miss]`/`[done]`）——这也算卡死，
按**阶梯第 1 级**处置：先确认它是真卡住了（不是恰好在等一次很长的模型请求），
确认后结束这个进程，再走「整场重跑一次」。

**任何 402 或"余额不足"类报错，不管出现在第几次尝试** → 立即全停整个流程
（不只是这一场），告诉用户去中转站充值；已经跑完的场次照实登记
`complete=yes`，不要因为后面几场没跑完就连带否认已完成的部分——
**钱的问题重试无用**，重试只会再烧一次同样烧不动的请求。

每一场跑完，读一遍这场的 `run_summary.json`（跑单模板自动落的，两家都记
`model_errors`/`interview_miss`/`advice` 三个字段；**落点分模拟器**：oasis
在 db 同目录的 `<db 名>.run_summary.json` 侧车，agentsociety 直接落在跑目录
`raw/<条件>_<rep>/run_summary.json` 里）。**字段同名，口径分模拟器**：
agentsociety 的 `model_errors` 是从 CLI 输出扫出来的 ERROR 级日志行数
（启发式，含框架内部重试，不是失败请求数——它的 `advice` 措辞自带这两个
caveat，照念别删）；`interview_miss` 是问卷答案 parse 失败数（模型都答了、
答案解析不出，插头按缺失处理不猜——oasis 数的则是访谈异常）。把 `advice`
那一行转述给用户。`advice` 建议标 `complete=no` 时，跟进去在第 6 步的 `runs.csv`
里按它标；`advice` 说未超经验线时正常按跑完记 `complete=yes`——但仍然要把
这行数字念给用户听，不是只在超线时才提。

**预算复核**（给了 `budget:` 才做这一步；每跑完一场都要复核一次，不是等全部
跑完才算）：用「已经完成的场次数 × 第 1 步算出的单场费用估算」跟 `budget:`
比一次，超了就**停下来问用户**要不要继续，问完再决定跑不跑下一场。**如实说
清楚这只是估算复核，不是真实账单**——跑单模板拿不到真实 token 用量（oasis
的 `[done]` 之后那行"token usage"打印自己就说明读不到这个数；agentsociety
隔着 CLI 子进程边界同样拿不到），复核用的只是"完成
场次数 × 估算单价"的粗略推算；真实费用以中转站网关自己的用量页为准。

---

## 6. 登记、装箱、验货、入库

先把 `runs.csv` 逐场登记好，**`complete` 列按第 5 步的实际结果填**——
重跑成功的算 `yes`，标了 `complete=no` 的就是 `no`，不要为了让后面验货
好看而把没跑完的也写成 `yes`。`file` 列也分模拟器：oasis 填一场跑的 `.db`
文件相对路径；agentsociety 填一场跑的**目录**（`raw/<条件>_<rep>`——它的
原始输出是整个目录，箱规见插头 `adapter.py` 顶注）。列的完整要求见
`docs/user/sim-box.md`。

```bash
oss sim pack --simulator oasis <箱目录>          # 整理稿定哪家就写哪家，
oss sim pack --simulator agentsociety <箱目录>   # 跟第 2 步体检同名——二选一
```

插头把 `raw/` 里的原始输出翻译成 `states.csv`/`events.csv`。配料单 `packed`
里记什么，分模拟器：

- **oasis**：把每场 `run_summary.json` 里的 `model_errors`/`interview_miss`
  汇总进 `packed`（连同 `n_runs_with_summary`，记这两个数是几场跑凑出来的——
  不是每场都会落这份文件，老箱子或某场没落文件时这个数会小于总场次数）；
- **agentsociety**：插头**不搬** `run_summary.json`——`packed` 八项
  （`n_parse_miss`/`n_snap_miss`/`n_runs_no_events`/`file_sha256` 等，清单见
  插头 `adapter.py` 顶注）是它自己从问卷 artifact 与 replay 分片**独立重数**
  出来的；其中 `n_parse_miss` 与跑单 `run_summary.json` 的 `interview_miss`
  判定口径逐字同款——两个独立算出来的数对得上，才说明货真。

```bash
oss sim check <箱目录>
```

`oss sim check` 与后面的 `oss sim import` **不带 `--simulator`，也不需要**——
海关只看两张表加配料单，不认识任何模拟器；两家的箱子过同一道关、同一套
拒收/降级规则（降级章、波内人数覆盖警告的措辞也一模一样），这正是货箱标准
的卖点：**多一个模拟器，不多一套海关**。

**不跑 `oss sim check` 不许 `import`。**check 通过（含警告）才能进下一步；
拒收（must 级问题）就回去修，不许绕过——**但修复尝试至多 2 轮**：改完重跑一遍
`oss sim check`，还拒收就再改一轮再核一遍；**两轮改完还是拒收，就停下来问用户，
把 problems 原样念给用户看**，不要自己继续瞎试第三轮。**check 打出来的每一条
警告都要逐条念给用户听，原文照念，不许省略也不许自己改写成更好听的话**——
尤其两类警告必须逐字念：

- **降级章**（某条件重复次数不足 3 次时）：这条警告的措辞是
  「结果只能作『初步/探索性』表述，局限必写『单次模拟无法区分随机波动与
  真实规律』」——这句话要一字不改地转述，用户将来写论文局限节要直接照抄它
- **波内人数覆盖警告**（T2b，某一波实际到场人数比同组其他波少）：形如
  「B/1 的 wave 1 只有 2/5 个小人——兜底记过 miss 的场次要在局限里说明」，
  同样逐字念。成因按模拟器分道：OASIS 是访谈兜底网救活的场次——被救活的
  小人记了 miss 但不写 trace 行；AgentSociety 没有兜底网，同一警告的成因
  是该问卷步作答的小人变少（视上游落盘方式，缺失或记在 run_summary/packed
  的缺失计数里，或仅由海关这道独立点名——两边不必然对得上）

这几类警告背后是同一件事的**三个信号**，来源不同、互相印证，念的时候可以
把这条讲给用户听：

1. **跑单计数**——每场跑自己在 `run_summary.json` 里数的 `model_errors`/
   `interview_miss`（第 5 步已经转述过一次）
2. **插头这一道**——oasis 是 `oss sim pack` 把有 `run_summary.json` 的场次
   这两个数**搬运**进配料单 `packed`；agentsociety 是插头从原始输出**独立
   重数**出 `n_parse_miss` 等八项，不经手 `run_summary.json`（这一步）
3. **海关点名**——`oss sim check` 从 `states.csv` 实际数据反推出的波次覆盖
   与波内人数覆盖警告（这一步），不依赖前两个数字也能独立发现异常

三者对得上，说明兜底网确实接住了失败、货箱如实反映了发生的事；对不上
（比如 `run_summary` 没报错但 `check` 点了名）也不奇怪——两条通道观察的
角度不同，都值得念给用户听。

check 通过后才能入库：

```bash
oss sim import <箱目录> --run <运行目录>
```

`--run` 给的运行目录必须已经存在（一般是主链 `/social-research` 会用的
那个 `runs/rYYYYMMDD_HHMM` 目录，没有就先 `mkdir -p` 建一个）。**入库这一刻
降级警告会再打印一遍**——凭空造出来的"通过"是最坏的一种，import 的屏幕
输出跟 check 的警告应该一致，再念一遍给用户确认。

---

## 7. 收尾报告

一次性把这几件事讲给用户：

- 货箱路径（`oss sim import` 打出来的那个）
- 每一场的耗时（第 5 步转述过的，这里汇总一遍）
- 每一场的 `model_errors`/`interview_miss` 计数（agentsociety 的
  `interview_miss` 按 parse 失败口径讲：模型答了、答案解析不出，按缺失
  处理），以及 `runs.csv` 里 `complete=no` 的场次列表（如果有）
- 降级状态（`single_condition`/`downgrade` 是否为真，是就把对应的措辞
  要求再提醒一遍）
- **运行目录交接**：把 `oss sim import` 实际用的那个 `<运行目录>` 路径原样
  写进报告，并明说「续接 `/social-research` 时环节 1 **沿用本运行目录**，
  不要新建」——铁律⑤那句"每条命令带同一个 `--run`"由这句话在这里兑现：
  没有它，接手的人（或续接的环节 1）会以为该另起一个新目录
- 下一步命令：

```
/social-research <研究方向> --- data: sim:<箱目录>
```

`sim:` 前缀是仿真数据的入口，`/social-research` 环节 1 认出这个前缀之后
会自动切到仿真体裁的说话规矩（措辞、方法节怎么报、稳健性怎么选）。
