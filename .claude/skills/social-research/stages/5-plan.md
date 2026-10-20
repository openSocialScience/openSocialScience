# 环节 5 · 写分析计划

## 输入
- `<运行目录>/binding.json`、`power.json`、`profile.json`
- `run_state.stage == "5-plan"`

## 产出
- `<运行目录>/plan.json`（**你写**；先跑 `oss schema plan --example`）

## 完成条件（全部满足才进环节 6）
- 交互项写在 `interactions` 里（**不在数据里预先乘好**），低阶项写全（三重交互要 3 个主效应 + 3 个二阶项）
- 写了交互项就同时声明 `binned`（`by` 写斜率随哪一列变）
- 数据里有"不同单位在不同时间被处理"的结构时用因果设计；`treat_time` 有两个以上取值时**主规格用 `did_cs`，不用 `did`**
- 用了 `did_cs` 的，已把 Callaway & Sant'Anna (2021) 抓进语料：`oss lit add 10.1016/j.jeconom.2020.12.001`
- **处理是 0/1、没有外生变异、只能靠可观测的协变量调平时用 `iptw`**（逆概率加权，双稳健）。三件事要跟着做：① 论文必须报**加权前后的标准化均值差**（诊断 `加权前后的平衡`，常用上界 0.1）；② 必须报**倾向得分的重叠**与有效样本量（诊断 `倾向得分重叠`、`权重的集中度`）——没有共同支撑域时 `oss run` 直接拒；③ **识别审计照样要过**：加权只调得平你测到的东西，未观测的混淆一点办法都没有，这跟「第一阶段强不强」与「排他性成不成立」是两件事完全同构。
- **不提供倾向得分配对（PSM）。**配到一定程度之后再往下配反而让协变量更不平衡（King & Nielsen 2019）——最近邻配对是拿一个一维的得分去近似多维的平衡，得分相近不等于协变量相近，而每删一对就多丢一次信息。要这一族就用 `iptw`。
- **`profile.json` 的 `survey.likely` 为真时，计划必须写 `plan.survey`**，声明抽样设计，例：
  `"survey": {"strata": "", "psu": "county", "weight_use": "descriptive", "design_source": "用户说：区县是初级抽样单位"}`。
  设计至少写全设计来源（`design_source`）与权重用法（`weight_use`）；公开版没给分层变量的，`strata` 空着、在 `design_source` 写明。
  确实不按调查处理的（比如那份其实是普查的完整抽取），**只写** `not_survey_reason` 说理由。
  **两样都没有，`oss run` 拒绝**（没写 `research_type` 的老计划只警告）。
  调查数据（CGSS/CFPS/GSS/CHARLS）按复杂抽样设计采集，各人入样概率不同，
  不加权算出来的是「样本里的中国」而不是「中国」。**这个错在系数上看不出来**，
  没有任何统计量会告诉你算错了对象——所以设计要在跑之前写明，不能指望结果来提醒
- 权重列照旧写在 `plan.weights`（`plan.survey` 里没有权重这一栏）。留空的话 `oss route` 会开 `weights_unused`，
  只能 `--apply` 加权重跑、或 `--dismiss --why` 写明为什么，两条路都不许沉默
- **权重分两路**（Deaton 1997 第 2 章 2.1），`weight_use` 二选一：
  - `descriptive`（均值、比例、分布）——**必须加权**：描述的是总体长什么样，不加权描述的只是样本。
    `oss eval` 的 `survey-descriptive-weighted` 查这一条
  - `model`（行为、因果模型）——**加权与不加权两版都报**：照旧锁下面那项「不加权复核」；
    两版差得多，`oss route` 会开 `survey_weight_gap`，局限里要解释为什么差（不自动换主规格）
- **多层模型（`mixed`）什么时候才用**（2026-09-25 加）：

  **先回答一个问题：你要它干什么？** 多层模型常被拿来「把聚类处理掉」，
  但**只为修标准误的话，`ols_fe` + `cluster_level` 更稳**——聚类稳健不假设
  随机效应服从正态，也不假设它与自变量不相关。

  所以 `plan.mixed.rationale` 必填，而且：

  | 写什么 | 结果 |
  |---|---|
  | `variance` | 放行。**方差分解本身是研究问题**（「省际差异占总变异的几成」） |
  | `shrinkage` | 放行。要各组的经验贝叶斯估计 |
  | `se` | **直接拒**，并指去 `ols_fe` + `cluster_level` |
  | 留空 | **直接拒** |

  另外两条：**二层单位少于 10 个拒跑**（方差分量在那么少的组上基本是噪声），
  少于 30 个警告；**写了抽样权重会降级成未加权并写进诊断**——多层模型的加权要
  level-1 与 level-2 两层权重，调查数据通常只给一层，硬按一层跑出来的方差分量
  是错的**而且看起来是对的**。降级那句话要照抄进论文的局限。

  主结果是 **ICC**，不是某个回归系数——那就是这个模型存在的理由。

- **十种估计量都真加权**（`ols_fe` / `did` / `did_cs` / `event_study` / `iv` / `iptw` / `rdd` / `describe` / `logit` / `ologit`）。`iptw` 的最终权重是**抽样权重 × 逆概率权重**——两者管的不是一回事：抽样权重管「这批人代表谁」，逆概率权重管「谁该跟谁比」。
  **`mixed` 不在其中**——它根本不加权，见上一条。
  `did_cs` 多一条：**权重要在单位内不变**——StatsPAI 按单位层面的抽样权重算，单位内会变的（比如逐轮的权重）
  `oss run` 拒跑；在 `prep.py` 里取基期那一轮的权重造一列单位层面的权重，`plan.weights` 写这一列
- **结局是 0/1 或有序量表时怎么选**（2026-09-25 加的 `logit` / `ologit`）：

  | 结局 | 首选 | 什么时候才换 |
  |---|---|---|
  | **0/1** | **`ols_fe`** | 它跑出来**就是线性概率模型**：系数直接读成「概率高 X 个百分点」，固定效应也干净。**这是首选，不是退而求其次**——复现关那篇十一轮 CGSS 的过劳研究用的就是加权 LPM |
  | 0/1，且要非线性的形状 | `logit` | 报的是**平均边际效应**不是系数（系数是对数几率，读者判断不了量级；odds ratio 也不行——「几率高 1.8 倍」在基线概率 2% 和 40% 的组里是完全不同的两回事）。**固定效应每组平均少于 10 个观测就拒**（incidental parameters：点估计本身有偏） |
  | **三个以上有序取值**（1–5 分的态度量表） | **`ologit`** | 这是中文社科最常见的一类结局。**必验平行线假定**——同一个自变量对「≥2 分」和「≥4 分」的影响是不是真的一样。判词永远进诊断（`平行线假定`），**不成立时报出来的那一个系数对哪一段都不成立**，要写进局限或换设定。有序 logit 不支持固定效应（切点与组内截距不可识别），把分组当哑变量放 `controls` |

  **不要以为 logit 比 OLS 高级。**0/1 结局的主流做法在经济学里就是 LPM，理由很硬：
  系数可读、固定效应干净。选 logit 要说得出为什么。

- 写了 `plan.survey.psu` 又写了 `cluster_level`、两者不同：**主规格的标准误按抽样设计算**，`cluster_level` 不用；
  按 `cluster_level` 聚类的那版锁进 `robustness`——一份计划里不许有两个互相矛盾的标准误口径却不说哪个是主
- 声明了抽样设计时，`sample_filter` 是**直接去掉不符合条件的行**（相当于 Stata 的 `if`），**不是子总体分析**（`subpop`）：会丢掉整个初级抽样单位、造出只剩一个抽样单位的层，按设计的标准误跟着变。只分析某个子人群时，局限里写明这一点
- 加权了就顺手锁一项**「不加权复核」**进 `robustness`（`{"label": "不加权", "weights": ""}`）——
  这是调查数据最常见的一项稳健性。注意 `""` 是「这一项不加权」、`null` 是「沿用主计划」
- `robustness` 事前锁定 **至少 3 项、至多 12 项**，每项写成**可执行**的规格（不是散文），`label` 用**论文的语言**（`lang: zh` 用中文，否则英文——它会原样出现在森林图和稳健性表上），**不要写 R1 / R3 这种规格代号**：写这一项换了什么（`R1 Outcome: under-5 mortality` → `Outcome: under-5 mortality`），期刊的表里不出现代号
- `labels` 填上：**表和图里印的是它，不是列名**。`{"cls_mgr": "管理与专业阶级", "age2": "年龄²/100"}`——
  发表用的表不该出现 `cls_mgr`、`age2`。`binding.json` 里已有构念名的列会自动取，**不用重复填**；
  要你填的是 binding 盖不到的两类：**哑变量展开**（绑的是 `class4`，进回归的是 `cls_mgr`/`cls_clerk`/`cls_service`）
  与**构造项**（`age2`）。交互项不用填，`a*b` 自动拼成「A × B」。语言跟论文走（`lang: zh` 填中文）。
  漏了不拦，`oss paper check` 会把还印着列名的那几列报出来
- **挑真正会威胁结论的那几项，别凑数也别怕多。**三次互不相通的真跑各自锁了 8/9/10 项；其中一次拿到了显著的主结果，**正是靠自己的稳健性套件发现**「加上教育控制就不显著、只留受雇者连符号都翻」，于是在摘要里放弃了那个发现。**清单短一点，那次发现就不会发生**
- **输入带 `design:` 时**：用户给的每个键**原样**进 `plan.json` 的对应字段（映射表见 [../rules/input-and-rundir.md](../rules/input-and-rundir.md)），你只填他没给的——控制变量、稳健性清单、样本限制
- **输入带 `design:` 时**：同一批键值再抄一份进顶层 `design_locked`，键名用 `plan.json` 那一列（`fixed_effects` / `cluster_level`，**不是** `fe` / `cluster`）；写完逐个核对一遍两处相等，不等就是你把用户的设计改了
- `plan.json` 写了 `research_type`：输入带 `--- type:` 就原样照抄（中文换成英文值）；否则抄环节 3 选中那条 idea 的 `research_type`。**一篇论文一个主类型**（主结论属哪类就是哪类）；其它部分的类型写进 `also`（例：先自建指数再估影响——主类型 `causal`，`also: ["measurement"]`）。**因果不进 `also`**：论文只要有一部分下因果结论，主类型就是 `causal`（非因果类型的正文不许有因果说法）
- **声明为因果的计划**（`research_type: causal`）写了 `estimand`（估计量三步）：`unit_quantity` 单位层面的量、
  `target_population` 目标总体（用了抽样调查就写明权重怎么用）、`assumptions` 识别假设逐条、`link` 经验估计量怎么对上——
  **先说问的是什么，再挑方法**；估计方法的选择回到这里说理由。`oss schema plan --example` 里有样例
- **声明为描述的计划**（`research_type: descriptive`）同样先说问的是什么：`estimand` 的 `unit_quantity` 写**描述量**
  （描述哪个量，例：每天上网的人口比例，城乡之差）、`target_population` 写**目标总体**（对哪个总体，跟权重的用法对上）——
  **两栏必填，缺一栏 `oss run` 拒绝**（没写 `research_type` 的老计划不查）；`assumptions` 写代表性假设，`link` 写样本量怎么对上总体量。
  不写清对哪个总体，读者分不出算的是「样本里的人」还是「那个总体」
- **描述类的估计量**：不带控制变量的分组描述用 **`describe`**——管水平、分布、组间差、趋势，出分组描述表
  （各格均值 / 比例带 95% 区间、对参照组的差；趋势另给一条线性斜率，分布另给分位数），跟其余六种一样真加权、区间按抽样设计算。
  **条件关联**（「控制了 A 以后 X 与 Y 的关系」）照旧走 `ols_fe`。`describe` 只做描述：因果计划配 `describe`，`oss run` 拒绝
- 估计量是 `describe` 的，**必须写 `plan.description`**（`kind` 必填；走 `ols_fe` 的描述计划——条件关联、从因果降级来的——可以不写），例：

  ```json
  "description": {"kind": "gap", "groups": ["urban"], "change": "none", "meaningful_difference": "城乡差 5 个百分点（出处：…）", "benchmarks": [{"quantity": "…", "value": 46.3, "unit": "小时", "source": "…"}]}
  ```

  - `kind`：`level`（水平 / 比例）/ `distribution`（分布）/ `gap`（组间差）/ `trend`（趋势）。
    组间差写 `groups`（比较的组，列名，**至多两列**），趋势写 `over`（时间列）；写的列不在数据里，`oss run` 拒绝
  - `statistic`：`mean`（缺省）/ `median`。**收入、财富、工时这类右偏的量报中位数**——均值被尾部拉走，
    报均值收入本身常常就是个错误的选择（实测：同一份数据中位数 22,696、均值 34,521，差近五成）。
    中位数的区间没有解析公式，按抽样设计做自助法（重抽初级抽样单位 / 簇），`oss power` 的精度也走同一条。
    只支持 `level` 与 `gap`；0/1 结局拒绝（它的中位数不是 0 就是 1，比例问题用 `mean`）
  - **组间差的参照组是排序后的第一类**（数值列按数值排）；要换参照组，就在数据里（`prep.py`）重编码
  - `change`：`social`（总体随时间变）/ `individual`（个人随时间变）/ `none`。**问个体变化要面板数据**：重复截面每期是另一批人，
    看不到任何一个人的变化，`change: individual` 配重复截面 `oss run` 拒绝。反过来拿面板描述总体随时间的变化（`change: social`），
    `oss route` 开 `panel_for_social_change`——面板的人会变老、会流失，越往后越不代表当期的总体
  - `meaningful_difference`：有意义的最小差别，带单位与出处或理由，写给论文与审稿看；环节 4 的精度按 `oss power --effect` 算，这一栏不参与计算
  - `benchmarks`：外部已知数（普查、官方统计年鉴），**每条带出处**（`source` 必填），现实核查用——论文里报本文的数跟它差多少。
    **没写，`oss route` 开 `reality_check_missing`**
- **声明为测量的计划**（`research_type: measurement`，或 `also` 里有 `measurement`）**必须写 `plan.measurement`**——
  跟因果的 `estimand`、描述的「描述量 + 目标总体」同一个口径，**不写 `oss run` 直接拒**（`plan.measurement.spec_missing`）。
  主类型写成因果、把 `measurement` 搁进 `also`（先自建指数再估影响），躲不掉这一条：两栏哪一栏声明的都算。
  测量研究先说清凭什么说这几道题量的是那个东西，再谈做不做得出来。七栏：

  ```json
  "measurement": {"kind": "application", "construct": "对陌生人的一般信任", "indicators": ["q12a", "q12b", "q12c"], "validity_evidence": ["criterion"], "evidence_outputs": {"criterion": "与捐款行为的相关", "equivalence": "城乡测量等价检验"}, "groups_compared": ["urban"], "source_quality": "self_report"}
  ```

  - `kind`：`instrument` **造尺子**（新编量表、新建指数、新造指标）/ `application` **用尺子**（拿现成量表做实质研究，
    但测量本身是主张的一部分）。两种的门不一样，**这一栏必填**——留空读都读不进来
    （造尺子那几道门全要 `kind: instrument` 才触发，留空等于自己把门关了）
  - `construct`：要测的那个看不见的东西，一句话；`indicators`：题目或变量的**列名**。**两栏缺一，计划读都读不进来**；
    列名不在数据里，`oss run` 跟结局、控制变量走同一路拒
  - `validity_evidence`：要给哪几类效度证据，**按类给**——`content`（内容）/ `structural`（结构）/ `criterion`（效标关联）/
    `discriminant`（区分）。**「效度良好」「量表成熟可靠」这种整体判断不收**：它是结论不是证据，写了契约直接报错。
    **造尺子必填**（`kind: instrument` 时空着读不进来）
  - `evidence_outputs`：**哪一类证据的产出叫什么名字**。键是上面四类，外加 `reliability`（信度系数）与
    `equivalence`（跨组测量等价检验）；值是这份产出在结果里的名字——`{{result:<名>.coef}}` 的名、`{{diag:<名>}}` 的名、
    稳健性的 `label`、或图名。**oss 不替你做因子分析**：产出在别处算，算完在这里登记一行。
    名字留空等于没登记，而「登记了个空名字」读起来像登记过了，**比不登记更糟**，所以契约直接拒
  - `groups_compared`：跨组比较的分组变量（列名），没有就留空。环节 4 会核这些列在不在数据里、够不够分两组
  - `source_quality`：`self_report` 自报 / `administrative` 行政记录 / `logged` 后台日志 / `mixed` 混合
- **测量类的五道门各在什么条件下响**。**每一道都只核「声明了什么 + 产出在不在」，一个字都不读论文里的论证**——
  效度、信度、构念全是语义判断，做成词表就是一道换个说法就失灵的门：

  | 门 | 什么条件下响 | 级别 |
  |---|---|---|
  | 没写测量块 | 声明为测量（`research_type` 或 `also`），而 `plan.measurement` 是空的 | 拒跑 `plan.measurement.spec_missing` |
  | **M1 效度证据不能只有信度** | `kind: instrument`，`evidence_outputs` 登记了东西、而登记的**全是 `reliability`** | 拒跑 `plan.measurement.reliability_only` |
  | **M2 声明了就要有产出** | `validity_evidence` 非空，而声明的那几类在 `evidence_outputs` 里**一类都找不到** | 拒跑 `plan.measurement.evidence_missing` |
  | **kind 跟产出对账** | `kind: application`，`evidence_outputs` 里却有 `structural` 或 `reliability` | 拒跑 `plan.measurement.kind_conflict` |
  | **M4 跨组比较前先验等价** | `groups_compared` 非空，而 `evidence_outputs` 里没有 `equivalence` 那一行 | 警告 `plan.measurement.no_equivalence` + **must 级处方** `measurement_equivalence` |
  | **跨组比较不声明** | `groups_compared` 空着，而**别处有分组的迹象** | 警告 `plan.measurement.groups_undeclared` + 同一条 must 级处方 |
  | **M5 自报数据按高误差代理** | `source_quality: self_report`，某个指标在画像里是一个**量**（连续、非负、上尾拖到中位数两倍以上——时长 / 频次 / 金额就是这个形状），而 `binding.json` 里这一列既没标 `proxy`、代理差距三栏也没填齐 | 警告 `plan.measurement.self_report_proxy` |

  几条最容易踩的：

  - **声明了测量类型就必须写测量块**：不写不是「没有门」，是直接拒跑
  - **效度证据按类给**，「效度良好」不收——**它是结论不是证据**。信度也不是效度：信度说的是这几道题彼此一致，
    不是这几道题量的就是那个构念（Flake 等 2017），所以只登记 `reliability` 不算给了效度证据
  - **登记了产出名，那份产出就得真的存在**：M2 在计划那一步核声明与登记对不对得上；导出前还要核登记的名字
    在不在这次跑出来的结果里（M4 的论文期那一半，见环节 7）。**登记一个空壳能让整套门不响**，所以两头都核
  - **新编量表标成 `application` 会被查出来**：因子结构与信度系数是**造尺子才会有的产出**，登记了它们而 `kind` 写
    `application`，门报冲突——判据不看计划怎么标，只看产出里有什么。要么改成 `instrument` 并按造尺子补齐效度证据，
    要么去掉这几项产出、这篇只做用尺子的研究。（效标关联与区分**不在**这张表里：用现成量表报「本样本里它跟捐款行为的相关」是常事）
  - **「别处有分组的迹象」是从计划与数据算出来的，不看 `groups_compared` 这一栏怎么写**：描述块的 `description.groups`、
    计划里的交互项、结果里真排出来的分组表，以及**处理变量本身就是分组变量**（结局是这个测量块的量表、
    处理变量在画像里是分类或二值）。**不声明并不省事**——该给等价检验还是要给，或者把跨组比较降级为组内描述、
    每组各报各的、不比大小并在局限里写明。这几处确实不是跨组比较（例如交互项的调节变量是连续的），
    `oss route --dismiss measurement_equivalence --why "<理由>"` 写明理由
- **声明为仿真的计划**（`research_type: simulation`，或 `also` 里有 `simulation`）**必须写 `plan.simulation`**——
  跟上面几类同一个口径，**不写 `oss run` 直接拒**（`plan.simulation.spec_missing`）。仿真研究答的是
  「这个模型里，这套机制会不会产生这个模式」，先说清检验的是哪套机制、该看见什么，再谈跑出来的数：

  ```json
  "simulation": {"mechanism": "邻里同质化偏好使住户迁出异质社区", "pattern": "隔离指数随偏好阈值单调上升，且在阈值 0.3 附近出现拐点", "conditions_compared": ["threshold_low", "threshold_high"], "stands_in_for_humans": false, "real_benchmark": null}
  ```

  **不写货箱路径**：那是 `oss sim import` 写进 `sim_state.json` 的，计划里再抄一遍就是两处真相。

  | 门 | 什么条件下响 | 级别 |
  |---|---|---|
  | 没写仿真块 | 声明为仿真（`research_type` 或 `also`），而 `plan.simulation` 是空的 | 拒跑 `plan.simulation.spec_missing` |
  | **声明了就得有货箱** | 声明为仿真，运行目录里却没有 `sim_state.json` | 拒跑 `plan.simulation.box_missing` |
  | **有货箱就得声明** | 盘上有 `sim_state.json`，`research_type` 与 `also` 都没提仿真 | 拒跑 `plan.simulation.undeclared` |
  | **数据长得像仿真输出** | 数据里 `condition`/`rep`/`wave`/`agent_id` 四列齐全，而既没声明也没入库 | 拒跑 `plan.simulation.output_undeclared` |
  | **要比较的条件得在货箱里** | `conditions_compared` 写了配料单 `conditions` 里没有的名字 | 拒跑 `plan.simulation.condition_unknown` |
  | 货箱里不止一个条件却一栏没声明 | `conditions_compared` 空着，而配料单里有两个以上条件 | 警告 `plan.simulation.conditions_undeclared` |
  | **合成数据的方差被压扁** | 分析用到的某一列里，**95% 以上的小人答的是同一个值** | 警告 `plan.simulation.degenerate_column` |
  | **跟真实调查对不上账** | 配了 `real_benchmark.pairs`，而合成 SD ÷ 真实 SD 落在 0.70–1.43 外，或两对列的关系方向反了 | 警告 `plan.simulation.variance_mismatch` / `relation_flipped` + **must 级处方** `simulation_fidelity` |

  几条最容易踩的：

  - **`mechanism` 与 `pattern` 必填**。`pattern` 写的是「模型跑对了该出现哪个**现实里已经观察到的**规律」
    （Grimm 等 2020 的面向模式验收）——**事前就要定下来**，看完结果反过来挑一个对得上的等于没验收
  - **「代表真人」这个开关一开，就得给对照数据**：`stands_in_for_humans: true` 而 `real_benchmark` 空着，
    契约当场拒。合成样本的方差会被压扁（Bisbee 2024），拿它当调查数据用，标准误会假小、显著性会假强；
    没有对照就核不出这件事
  - **很多 ABM 本该填 `false`**：做的是「这套机制能不能产生这个已知模式」，本来就不主张小人等于真人——
    逼这类研究去找真人数据是错的，对账那两条一个字都不会响
  - **但配了对照列就照查，不看那个开关**：配了 `real_benchmark.pairs` 却把 `stands_in_for_humans` 关着
    是自相矛盾，照样对账
  - **把模拟输出当普通 CSV 直接跑是拦得住的**：判据只认数据的列——货箱 `states.csv` 的四根钥匙列齐了
    就报，不认任何声明。走正路：`oss sim check` 验货、`oss sim import` 入库
- 每个控制变量在 `control_roles` 里标了角色：`confounder`（混杂）/ `precision`（提高精度）/ `other`。
  **中介与碰撞变量不许控制**——标了 `mediator` / `collider` 的 `oss run` 拒绝；拿不准的回因果图想（谁影响谁）
- `run_state.stage` 推进为 `6-run`

## 禁止
- 在数据里预先乘好交互项（`paeduc*eduq` 打错一个字母会静静产出一列 NaN）
- 写了交互项不声明 `binned`——那样正文不许拿分箱图做形状断言
- 分批实施用 `did`（TWFE）当主规格：夹具上真实 3.56，`did` 算出 2.13，`did_cs` 算出 3.51
- 另开一个运行目录跑事件研究（**分目录等于绕过门**）
- 稳健性写成一串字符串，或超过 12 项（那就成了没人读的一墙检验）
- 用中文写 `label`（它会原样出现在英文论文的森林图上）
- 第一阶段 F 低于 10 还用借力变量法（工具变量）
- **改掉用户锁定的键**（`design_locked` 里非空的那几个）：`oss run` 跑之前会逐键比对，不等就拒绝，**退出码 6**。审计判 FAIL 之后的「换设计」循环对锁定的键**不生效**——只能在没锁的键上找出路，找不到就按 [../rules/stop-conditions.md](../rules/stop-conditions.md)「穷尽 → 保留设计、降级为关联、逐条写进局限」
- **没有 `design:` 却写 `design_locked`**：那是给自己上一把用户没要的锁
- 把用户的写法（`fe` / `cluster`）原样当成 `design_locked` 的键名——schema 禁未知键，`oss run` 会连计划都读不进去
- 跑过 `oss run` 之后改 `research_type`：只有识别站不住（审计判 FAIL 且穷尽留痕，或平行趋势不成立）才能从 `causal` 降为 `descriptive`，**结果不显著不是理由**；其它改法 `oss run` 退出码 6、`oss paper check` 拒绝（研究类型门）。往大改或平移要回本环节重写计划，并由用户授权 `oss anchor switch`；**冻结后也不许把 `research_type` 删掉或改成空**。**顺序：先改 `plan.json` 的 `research_type`，再请用户授权 `oss anchor switch --to <当前 idea_id>`**——反过来锚点会冻回旧类型，`oss run` 照样退出码 6。**第一次 `oss run` 之前就要写明 `research_type`**——之后才补的，会记为「看过结果才补」（合规检查判不过）；补上之后要重跑一次 `oss run` 才锁定，没重跑的，论文检查与导出都会拦
- 样本限制（`sample_filter`）里用结果变量筛人：按结果筛样本引入选择偏误，因果计划 `oss run` 直接拒绝

## 命令
```bash
oss schema plan --example
oss lit add 10.1016/j.jeconom.2020.12.001 --run <运行目录>
oss run --run <运行目录>      # 退出码 6 = plan.json 改掉了用户锁定的设计（它会点名是哪几个键），或研究类型没按规矩改（研究类型门）；退出码 1 = 研究类型标非因果、题目却在问因果
```

## 数据来自仿真货箱时

- **主处理形状默认前后对照 / DID**：`wave` 就是期数，`wave 0` 是处理前基线——
  跑单模板默认开跑前先访谈一轮正是为了留出这个基线，没有它 DID 无从做起，
  不要另起一套时间轴
- **`plan.unit` 必须用 `prep.py` 造的复合 `unit` 列（`condition×rep×agent_id`），
  不是 `agent_id`**——环节 1 已经交代过 `agent_id` 跨场重复编号，A/1 的 0 号
  和 B/1 的 0 号是两个不同的模拟人；这里再申一遍：写 `plan.json` 的这一刻，
  正是「个体标识错了」变成「估计出来的数还能不能信」的分界点，用错这一列，
  固定效应吸收的是两个不相干的小人，不是同一个体的前后变化
- 画像若报告"`condition` 在 100% 的个体内保持不变"，那不是数据坏，是**组间
  处理的机器自证**——每个复合 `unit` 从建组起就只属于一个 `condition`，这正
  是仿真设计本身的形状。`treatment` 走 **did 族**识别（`did` / `did_cs` /
  `event_study`），不要因为这句自证去找别的估计量；绑定门对组间处理的一般
  拦法在这里有例外，例外按整理稿里的 `identification_strategy` 是否落在 did
  族来判——**留空会退回旧拦法**，策略空等于从严，这是有意的缺省，不是漏填
  也放行
- `robustness` 清单**必含跨 rep 留一**（`rep ≥ 2` 时）：留一去一重跑主设计，
  看效应方向和显著性还在不在——这是仿真论文能做的、最接近"跨样本稳健性"的
  一项
- **`leads/lags` ≤ 波次数 − 1**：波次数以货箱 `interview_plan` 实际访谈的次
  数为准，超过这个数的 lead/lag 项在仿真数据里天然全是缺失，声明了也是摆设

## 卡住时读
[5-plan.notes.md](5-plan.notes.md)——交互项与分箱为什么要一起声明、`did_cs` 附带什么、借力变量的排他性、断点法的三个检验、"先看逐期效应再看汇总系数"。
