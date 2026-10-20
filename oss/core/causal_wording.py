"""因果措辞两档检查（设计稿 docs/design/2026-09-13-research-types.md 第 4.4 节、附录 A）。

研究类型不是因果的论文（描述 / 测量 / 仿真，以及从因果降级成描述的），正文不下因果结论：

- **硬拦档**：明确的因果说法（导致、造成、下结论的「存在因果关系 / 因果效应」、causes、leads to……）→
  `oss paper check` 与 `oss paper export` 拒绝
- **标出档**：模糊的说法（影响、提高、促进、affect、increase……）→ 写进 `causal_flags.json`，交识别审稿人逐条判

**宁漏勿滥**——误拦多了，人会学会整片忽略这条检查。但「拿不准」不等于「放行」：
上下文拿不准的硬拦**降为标出**、带一个 note，审稿人照样看得见。

整条不报的只有这几种：

- 否定的是**这个说法本身**：同一分句里、词前不远有「无法 / 并非 / 没有 / 毫无 / 从未 / cannot / not / nothing /
  none / no evidence / little evidence / without implying ……」，
  或「不」后面是认定、推断、情态一类的动词（不能、不意味着、不认为……）；「未」总是否定动词（未识别、未充分识别；
  未婚、未来不算）；
  「非」只在紧挨着命中词时算（非因果效应；非农、非常不算）；
  「而非 / rather than」只在直接否定名词（相关关系而非因果关系）或紧挨着动词（而非导致）时算——
  降级声明句本身就是这种句子，拦它等于不许诚实。
  **不算否定**：「不是 A 而是 B」「not A but B」（因果归给了 B）、双重否定（不能否认、cannot deny、
  不会改变……的结论）、「不平等 / 不足 / 不高」这类形容词里的「不」
- 代码、表格行、占位符内部；统计术语：固定 / 随机 / 最小可探测 / 设计 / 主 / 交互 / 边际 / 时期 / 队列 / 年龄效应、
  效应量，fixed / random / minimum detectable / design / main / interaction / marginal / period / cohort / age
  effect(s)、effect size(s)
- 一看就不是因果的英文名词：the results in Table 2、the leads to the lags

降为标出的（`Hit.note`）：

- 否定存疑：「不」后面既不是认定、推断一类的动词，也不是形容词（不控制收入时……）
- 可能：「不能排除 / cannot rule out」——说的是有这种可能
- 名词：英文 cause 的名词用法——common causes of X and Y（说的是混淆）、cause-specific；
  「is the (main) cause of」是判断句，照拦
- 方法：宾语是方法或数据处理（导致样本量减少、leads to biased estimates），或主语是（观测窗口造成……、加权使……）
- 设问：命中所在分句里有「是否 / 能否 / 与否 / 如果 / whether / if」
- 转述：别人的发现——同一句里有引用占位符（`cite` / `citep`），或命中词**之前**有「已有研究 / 文献 / et al. /
  the literature / few studies / studies show / 理论认为 / accounts hold that / on this view」；
  命中词之前或它所在的分句里有「本文 / 我们 / we / our / this analysis」就是自己的结论，照拦；与文献唱反调、断了句的
  （Unlike most studies, … / 与已有研究不同，……）也是自己的结论——who / which 引出的仍是转述
- 引号：引号里的字——受访者原话、别人的说法；「本文发现“……”」是自己的结论加了引号，照常判
- 模型内：**仿真论文**里限定了「在本模型内 / within the model」的句子（与 `SIM_REALITY_LEXICON` 同一口径）
- 名义：名词类的因果词（因果关系 / 因果效应 / 因果影响 / causal effect(s) / causal impact(s)）不在「下结论」的句式里——
  同一分句、命中词之前没有断言或估计动词（存在 / 具有 / 表明 / 发现 / 估计了 / has / shows / found / estimates……；
  不定式 to identify 不算，光有本文 / 我们也不算），也不是紧挨着的「有（着）……的」；或者这个分句里有情态、展望词
  （需要 / 可以 / 未来 / 才能 / 很难 / should / can / may / requires……），或者命中词后面跟着存疑的说法（尚不清楚 / 留给 /
  cannot be identified / remains unclear……）。真稿里这几个词不带否定的用法都是局限、展望句（用户 2026-09-14 定）。
  动词（导致、causes）与「the effect of … on」不走这条；「……的因果影响在城市更大」这种没有句式的真结论只标出，是接受了的漏拦

几条都沾上时取第一条：否定（整条不报）→ 名词 / 可能 / 否定存疑 → 方法 → 设问 → 转述 → 模型内 → 名义。
名义排最后：它只说「没找到下结论的句式」，别的 note 都说出了这句话是什么（谁的话、问句、方法），给审稿人的更多；
研究问题里名义与设问都算在问因果，谁先不影响 `oss run` 的门。引号里的另走一路：否定照样管用，别的一律「引号」。

研究问题问的是不是因果，用 `asks_causal`：硬拦的，加上 note 在 `CAUSAL_QUESTION_NOTES` 里的（设问、名义）。

词表是设计稿附录 A 的草稿，照真跑里的误拦与漏拦调；改词表要同时改 tests/test_causal_wording.py。
"""

from __future__ import annotations

import json
import re
from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass
from pathlib import Path

from oss.core.contracts import RESEARCH_TYPE_LABELS
from oss.core.findings import Finding
from oss.core.manuscript import strip_code_and_quotes
from oss.core.textspan import _DELIM   # 句末表全仓库只此一份；本模块另按行切，粒度不同、表相同

FLAGS_FILE = "causal_flags.json"

#: 研究问题里这些 note 也算在问因果——正文里「是否导致」是设问、「……的因果效应」是名义，研究问题本来就是问句、不下结论
CAUSAL_QUESTION_NOTES: frozenset[str] = frozenset({"设问", "名义"})

#: 升降词。施动句式（使 / 让）后面要跟的那一类词
_UPDOWN = "提高|降低|增加|减少|上升|下降|提升"
#: 结论词。归因句式（由于 / 是因为 / 源于 / 归因于）要挂在一句有升降或高低比较的结论上，才算下因果结论；
#: 单挂一个名词（由于篇幅、由于数据口径）的是方法与局限里的常规说法
_CONCLUDE = _UPDOWN + "|较少|较多|较高|较低|更少|更多|更高|更低"
#: 「由于 X，Y」整句里出现这些字，说的就是方法、数据或局限的理由，不是因果结论。
#: 真稿里五处「由于」全靠这条放行：由于各国上调的时间不同**本文**采用……、由于……假设被拒绝**点估计**混有……、
#: 由于年限**记录**不存在**本文**所说的……。**这一条不能放宽**——「由于篇幅所限」被拦，这门就没人信了
_METHOD_SENT = ("本文|我们|笔者|本研究|篇幅|所限|数据|样本|记录|资料|口径|窗口|抽样|加权|编码|缺失"
                "|观测|估计|系数|标准误|回归|模型|设定|规格|假设|检验|文献|变量|指标|测量")

#: 硬拦档。拒绝理由里报的是**匹配到的原文**，所以这里只存编译好的式子
_HARD: tuple[re.Pattern, ...] = tuple(re.compile(p, re.I) for p in (
    r"导致", r"造成", r"致使", r"因果效应", r"因果影响", r"因果关系",
    # 「引起了学界和政策制定者的广泛关注」不是因果
    r"引起(?![^。；，]{0,20}?(?:关注|重视|讨论|争议|注意|兴趣))",
    # 「使用互联网的人收入提高」「即使收入提高」不是因果；「使得收入提高」「促使……提高」是
    rf"(?<![即假纵])使(?!用|命|者)得?[^。；，,]{{0,10}}?(?:{_UPDOWN})",
    # 「是……的（主要）原因」「……的原因在于」；「不是……的原因」是否定
    r"(?<!不)是[^。；，,]{0,12}?的(?:主要|重要|根本|直接)?原因", r"的(?:主要|重要|根本|直接)?原因在于",
    # ── 注入测试 injection-v2 逐条核出来的八种写法（2026-09-16）。**八个都带句式限定**：
    # 这几个词单用在方法、局限、转述里全是正当说法，限定是照真稿回归里的原句调出来的（tests 第十节逐条钉住）
    #
    # 施动三个要那个「了」：没有「了」的是政策意图（当作**压低**生育率的抓手）、理论复述（两者都**压低**
    # 子女数量）、设问（是否**带来**民生改善）——真稿里三种都有，都不是在下结论
    r"带来了", r"压低了",
    r"引发了(?![^。；，]{0,20}?(?:关注|重视|讨论|争议|注意|兴趣))",     # 「引发了广泛讨论」跟「引起」同一条豁免
    # 「让」单字是常用动词（让我们先看、让读者对照、让估计向零偏），只在「让 + 对象 + 升降」的结论句式里拦；
    # 「让 X 少生了孩子」这种「少 / 多 + 动词 + 了」也是同一个句式——「了」标明说的是已经发生的变化
    rf"让(?!我们|读者|人们|笔者|本文)[^。；，,]{{0,8}}?(?:{_UPDOWN}|[少多][^。；，,]{{0,2}}?了)",
    # 归因：「Y（升降结论）……由于 / 是因为 / 源于 / 归因于 X」。中间那几个字排掉两类：
    # 「把 / 被」是转述别人的归因（前者**把**健康红利归因于选举问责、收入下降**被**广泛地归因于外部环境）；
    # 否定词在这里要自己排——命中从结论词起算，否定落在**匹配之内**，`_negation` 看的是匹配之前，管不到
    # （难以归因于、不能归因于、并非由于、不是因为）。结论词**之前**的否定照旧走 `_negation`
    rf"(?:{_CONCLUDE})(?:(?![把被不未非难无没])[^。；！？]){{0,10}}?(?:由于|是因为|源于|归因于)",
    # 倒过来写的同一句：「由于 X，Y（升降结论）」
    rf"由于(?![^。；！？]*(?:{_METHOD_SENT}))[^。；！？]{{1,30}}[，,][^。；！？]{{0,30}}?(?:{_CONCLUDE})",
    r"\bcaus(?:e|es|ed|ing)\b", r"\bleads? to\b", r"\bled to\b",
    r"\bresults? in\b", r"\bresulted in\b",
    r"\bcausal (?:effects?|impacts?)\b", r"\bthe effects? of\b[^.;]{1,60}?\bon\b",
))

#: 标出档。「效应 / effect」是统计术语的不算（重放验收 2026-09-14：真描述类论文里标出的一半是这些，
#: 审稿人逐条判，白白稀释了真要看的那几条）：固定 / 随机 / 最小可探测 / 设计 / 主 / 交互 / 边际 / 时期 /
#: 队列 / 年龄效应、效应量；fixed / random / minimum（smallest）detectable / design / main / interaction /
#: marginal / period / cohort / age effect(s)、effect size(s)。「主」前面是民、雇、自的不是主效应——
#: 民主效应、雇主效应是实义（真稿里都有）；英文的限定词要整词：average effect 的尾巴不是 age effect
_SOFT: tuple[re.Pattern, ...] = tuple(re.compile(p, re.I) for p in (
    r"影响", r"作用",
    r"(?<!固定)(?<!随机)(?<!最小可探测)(?<!设计)(?<!(?<![民雇自])主)(?<!交互)(?<!边际)(?<!时期)(?<!队列)(?<!年龄)效应(?!量)",
    r"冲击", r"促进", r"抑制", r"提高", r"降低",
    r"增加", r"减少", r"提升", r"改善", r"决定", r"驱动", r"推动", r"取决于",
    r"\baffect(?:s|ed|ing)?\b", r"\binfluenc(?:e|es|ed|ing)\b", r"\bimpacts?\b",
    r"(?<!fixed[ -])(?<!random[ -])(?<!\bminimum[ -]detectable[ -])(?<!\bsmallest[ -]detectable[ -])"
    r"(?<!\bdesign[ -])(?<!\bmain[ -])(?<!\binteraction[ -])(?<!\bmarginal[ -])"
    r"(?<!\bperiod[ -])(?<!\bcohort[ -])(?<!\bage[ -])\beffects?\b(?![ -]sizes?\b)",
    r"\bincreas(?:e|es|ed|ing)\b", r"\bdecreas(?:e|es|ed|ing)\b",
    r"\breduc(?:e|es|ed|ing)\b", r"\bimprov(?:e|es|ed|ing)\b", r"\bdriv(?:e|es|en|ing)\b",
    r"\bdetermin(?:e|es|ed|ing)\b", r"\bcontribut(?:e|es|ed|ing) to\b", r"\bpromot(?:e|es|ed|ing)\b",
    r"\bbecause of\b", r"\bdue to\b", r"\bowing to\b",
))

#: 否定词：命中词前、同一分句里（中文 16 字、英文 60 字符以内；不跨逗号、分号、冒号——冒号前的否定管不到冒号后的断言），
#: 逐个判它否定的是不是这个说法（`_one`）。长的写在前面——「未能 / 尚未」不能被拆成「未」。
#: 「未」总是否定动词，只有「未婚 / 未来 / 未成年……」不是；「非」另判（`_one`）：
#: 「而非 / 并非 / 绝非 / 决非」之外的「非」只在紧挨着时算。
#: 英文要**整词**（enormous 里的 nor 不算）；not only、not surprisingly 不是否定；whether or not 的 not 是设问；
#: 连字符复合词也不是——never-treated、not-yet-treated 是 DiD 术语（真稿里 `\b(?:never|not)-\w`，re.I，39 处、33 行）；
#: no 只在否定证据、主张时算——with no controls、no accident 不是否定；without 只在 without implying 一类里算；
#: little / scant evidence 是说没有证据。真稿 ossrun-a 第 85 行「so nothing here identifies a causal effect」是免责声明
_NEG_ZH = re.compile(r"未能|未必|尚未|从未|无法|难以|没能|并非|没有|无从|而非|并无|毫无|[绝决]非|不"
                     r"|未(?!来|婚|成年|满|知)|(?<![而并绝决])非")
_NEG_EN = re.compile(r"\b(?:cannot|unable to|nothing|none|neither|nobody|no one"
                     r"|without(?=\s+(?:implying|claiming|suggesting|asserting|establishing|identifying|showing"
                     r"|demonstrating))"
                     r"|(?:little|scant|limited|insufficient)(?=\s+(?:[\w-]+\s+)?(?:evidence|support|indication)\b)"
                     r"|\w+n[’']t"
                     r"|(?<!\bor )not(?!\s+(?:only|just|merely|surprisingly|unexpectedly|coincidentally)\b)"
                     r"|no(?=\s+(?:[\w-]+\s+)?(?:evidence|claim|reason|indication|support|basis|grounds|sign"
                     r"|suggestion|conclusion|inference|interpretation|attempt|causal)\b)"
                     r"|never|nor|rather than|instead of|as opposed to)\b(?!-)", re.I)
#: 「不」分三种（「未」不分，总是否定）。后面是认定、推断、情态一类的动词——真否定：
_BU_DROP = re.compile(r"发现|曾|能|会|可|宜|应|必|该|一定|意味|代表|等于|等同|说明|表明|证明|支持|主张|认为|声称|讨论|涉及"
                      r"|考察|检验|回答|识别|推断|构成|作|做|对|是|视为|理解|解读|允许|足以|存在|导致|造成|引起|致使"
                      r"|受|据此|将|把|下|给出|提供|直接")
#: 是形容词——不是否定：「收入不平等导致健康差距扩大」「监管不力导致……」是实打实的因果说法
#: （真稿里 `不(?:平|均|确定|稳定|对称|公|利|良|景气|满(?!足))` 29 处、24 行）。别的「不」拿不准，降为标出（否定存疑）
_BU_NOT = re.compile(r"仅|但|光|止|断|同|少|久|再|过|论|管|平|均|确定|稳定|对称|公|利|良|景气|满(?!足)|足(?!以)"
                     r"|力|善|佳|当(?!作|成|做)|便|畅|够|透明|完善|充分|合理|匹配|一致|协调|健全|规范|到位|高|低")
#: 「而非 / rather than」只直接否定名词（相关关系而非因果关系），或紧挨着的动词（而非导致、rather than causing）——
#: 「教育而非收入导致了生育率下降」是把因果归给教育
_CONTRAST = ("而非", "rather than", "instead of", "as opposed to")
_ADJACENT = re.compile(r"^\s*(?:直接|单纯|仅仅|只是|完全"
                       r"|(?:directly|simply|merely|only|solely|necessarily|primarily|mainly|actually)\s*)?$", re.I)
#: 对比转折：「不是 A 而是 B」「not A but B」——因果归给了 B，前面的否定不算
_TURN = re.compile(r"而是|但是|却|\bbut\b|\binstead\b", re.I)
#: 否定紧跟着「否认 / 改变……」：不能否认、cannot deny、不会改变……的结论——双重否定，照拦；
#: 紧跟着「排除 / rule out」：说的是有这种可能，降为标出。**要紧跟**（中文隔 4 字、英文隔两个词以内）：
#: change 也是名词，「We do not claim that the change in policy causes growth」仍是真否定
_DOUBLE = re.compile(r"^(?:[^，。；]{0,4}?(?:否认(?!为)|怀疑|质疑|置疑|改变|动摇|推翻|削弱)"
                     r"|\s*(?:[\w-]+\s+){0,2}?(?:den(?:y|ies|ied)|doubt\w*|disput\w*|chang(?:e|es|ed|ing)"
                     r"|alter(?:s|ed|ing)?|overturn\w*|undermin\w*|weaken\w*)\b)", re.I)
_POSSIBLE = re.compile(r"^(?:[^，。；]{0,4}?排除|\s*(?:[\w-]+\s+){0,2}?(?:rul(?:e|es|ed|ing)\s+out|exclud\w*)\b)", re.I)

#: 引用占位符（`manuscript.PLACEHOLDER` 里的 cite 与 citep）先换成这个记号、补空格到原长——列号不错位；
#: 其它占位符在 strip_code_and_quotes 里被抹成等长空格
_CITE = "〔引〕"
_CITE_PH = re.compile(r"\{\{(?:cite|citep):[^}]*\}\}")
#: 转述：引用记号在同一句里任何位置都算；文字记号只在命中词**之前**才算——
#: 「互联网普及导致青年学者收入提高」里的学者是宾语，不是出处。英文说文献的（the literature、few studies、
#: studies show、a growing body of research……）跟中文的「文献 / 已有研究」同一口径
_ATTR = re.compile(r"已有研究|既有研究|现有研究|以往研究|文献|学者|等人|理论认为|观点认为|按照这一观点|\bet al\b"
                   r"|\bon this view\b|\bthe literature\b|\bno (?:study|paper)\b"
                   r"|\b(?:existing|prior|previous|recent|earlier|empirical) (?:literature|studies|research|work)\b"
                   r"|\b(?:few|many|several|some|no|most|numerous|other)"
                   r" (?:studies|papers|scholars|researchers|authors)\b"
                   r"|\ba (?:growing|large|substantial|vast) (?:body of (?:work|research|evidence)|literature)\b"
                   r"|\bstudies (?:have|has|show|showed|find|found|suggest|suggested)\b"
                   r"|\bresearch (?:has|shows|showed|finds|found|suggests)\b"
                   r"|\b(?:accounts?|theor(?:y|ies)|models?|views?|hypothes(?:is|es))\s+"
                   r"(?:holds?|argues?|predicts?|suggests?|posits?)\b", re.I)
#: 作者自己：在命中词之前、或命中所在的分句里，就是自己的结论——挂上文献也照拦；
#: 后面另起一个分句（「……，本文在此基础上进一步考察」）不算。this analysis、the present study 也是作者自己
_SELF = re.compile(r"本文|我们|笔者|本研究|\bwe\b|\bour\b|\bthis (?:paper|study|article|analysis|research)\b"
                   r"|\bthe (?:present|current) (?:paper|study|article|analysis|research)\b", re.I)
#: 与文献唱反调、对比词断了句的（Unlike most studies, … / 与已有研究不同，……）是作者拿文献反衬自己的结论，不算转述——
#: 除非句里有引用占位符、命中所在分句里又有文献记号，或那个分句以 who / which 开头（Unlike Smith et al., who find…）
_CONTRA = re.compile(r"(?:\b(?:unlike|contrary to|in contrast (?:to|with)|departing from|as opposed to|different from)\b"
                     r"|不同于|有别于|与[^，,；;。]{0,10}(?:研究|文献)[^，,；;。]{0,4}?(?:不同|相反|不一致))[^，,；;。]*[，,；;]", re.I)
#: 「本文发现“……”」「we find that "…"」：自己的结论加了引号，引号不算数，照常判
_SELF_CLAIM = re.compile(r"(?:本文|我们|笔者|本研究)[^，,；;。]{0,6}?(?:发现|认为|表明|说明|得出|证明|主张|指出|结论是)"
                         r"|\b(?:we|this (?:paper|study|article))\s+(?:\w+\s+)?(?:finds?|found|shows?|showed"
                         r"|conclude[sd]?|argue[sd]?|demonstrate[sd]?|establish(?:es|ed)?)\b", re.I)

#: 方法——宾语是方法或数据处理：命中词后 12 字以内（同一分句）、或命中那一段本身有这些词。
#: 只收专指的：「损失 / 流失 / 覆盖 / 观测 / 样本」单用常有实义（经济损失、人才流失、覆盖率、样本中的女性）
_METHOD = re.compile(r"偏误|偏差|共线|缺失|误差|混淆|截断|偏移|样本量|样本数|样本(?:损失|流失|减少|缩小|规模)"
                     r"|(?:静默|数据|信息|观测值?)损失|覆盖缺口|观测值|观测数|估计值|估计量"
                     r"|\bbias(?:ed)?\b|\bsamples?\b|\bobservations?\b|\bmissing\b|\battenuation\b"
                     r"|\bcollinearity\b|\bmeasurement error\b", re.I)
#: 方法——主语是数据处理：命中词前 8 字以内、同一分句（观测窗口造成……、加权使……）
_METHOD_SUBJ = re.compile(r"(?:窗口|合并|删除|剔除|加权|权重|编码|口径|抽样|跳答|跳问|插补|截断|恒等式|设定)[^，,；;。]{0,8}$")
_CLAUSE_END = re.compile(r"[，；;：:。！？!?]|,(?!\d)")        # 3,021 里的逗号不算
#: 设问与假设：命中所在分句里、命中词之前有这些，或这个分句里有「与否」——
#: 「如果控制收入，教育仍然导致……」「If anything, education causes……」的条件在前一个分句，照拦
_ASK = re.compile(r"是否|能否(?!认(?!为))|如果|假如|倘若|\bwhether\b|\bif\b", re.I)    # 「不能否认」不是「能否」
#: 英文名词用法：（词形，前面是这些，后面是这些，判法）。"drop" 是一看就不是因果、整条不报；
#: 名词的 cause 还在谈因果（common causes of X and Y 说的是混淆），降为标出
_NOUNS: tuple[tuple[tuple[str, ...], re.Pattern, re.Pattern, str], ...] = (
    (("result in", "results in"),
     re.compile(r"\b(?:the|our|these|those|main|similar|same|baseline|key|all)\s+$", re.I),
     re.compile(r"^\s*(?:tables?|columns?|figures?|fig|panels?|sections?|appendix)\b", re.I), "drop"),
    (("lead to", "leads to"),
     re.compile(r"\b(?:the|from)\s+$", re.I), re.compile(r"^\s*(?:the\s+)?lags?\b", re.I), "drop"),
    (("cause", "causes"),
     re.compile(r"\b(?:the|a|an|its|their|these|those|one|common|root|main|major|leading|primary"
                r"|possible|plausible|other|potential|underlying)\s+$", re.I),
     re.compile(r"^(?:-|\s+(?:of|for)\b)", re.I), "名词"),
)
#: 判断句「is the (main) cause of」「one of the leading causes of」照拦，不当名词；
#: there is / are 是存在句，common cause 是混淆的说法——仍按名词
_COPULA = re.compile(r"(?<!there )\b(?:is|are|was|were|be|been|being|remains?|remained|becomes?|became)\s+"
                     r"(?:(?:a|an|the|one of the)\s+)?(?:(?!common\b)[\w-]+\s+){0,2}$", re.I)
#: 仿真的限定语，**只认仿真论文**。**不收「in the model」**：回归论文里「in the model with controls」太常见。
#:
#: **一张表三处用**（2026-09-19 统一）：`scan` 给限定过的句子降为标出、
#: `sim_reality_warnings` 拿它豁免、`missing_scope_limit` 拿它问「全文限定过没有」。
#: 原先后者写的是裸字面量 `"在本模型内" not in s`，跟这条正则**不是同一把尺子**——
#: 作者写「在模型内」，因果措辞那边放行、仿真三查那边照样警告。
#: 「一个判据不会同时在这两个方向上错；只有两张各写各的清单会」。
#:
#: 这张表是**「必须出现」型**的，跟 `SIM_REALITY_LEXICON` 那张查违规的性质相反：
#: 少收一种写法只会误报（作者会来投诉、会被修），不会漏放。所以宁可收得宽些。
SIM_SCOPE = re.compile(
    r"在本?模型(?:内|的设定下|框架内|的边界内)"
    r"|within\s+(?:this|the)\s+(?:simulated\s+|simulation\s+)?model"
    r"|within\s+(?:this|the)\s+model's\s+dynamics",
    re.I)
#: 仿真三查第三条的现实断言警戒词表。**宁漏勿滥，防误伤**——只收明显把模拟结果
#: 说成现实/政策断言的句式，边缘写法不猜；漏掉的那类有跨模型审稿兜底
SIM_REALITY_LEXICON = ("现实中将", "现实世界会", "政策应当立即", "现实中会", "这意味着人们会")
#: 名义：名词类的因果词只在「下结论」的句式里硬拦——同一分句、命中词之前有断言或估计动词（`_FRAME`）或紧挨着的
#: 「有（着）……的」（`_YOU`），且这个分句里没有情态、展望词（`_MODAL`），命中词后面也没有存疑的说法（`_HEDGE_AFTER`）。
#: 光有本文 / 我们不算：「本文对因果关系不作判断」是免责声明。18 篇真稿里这几个词出现 14 次、0 次是真结论。
#: 助动词 have / has / had 不算（have estimated、had been、have to）；不定式也不算（to identify、insufficient to establish）；
#: 过去时与分词照算（showed / found / had a……）：结果一节的常规写法
_NOMINAL = ("因果", "causal ")
_FRAME = re.compile(r"存在|具有|产生|证实|证明|表明|发现|揭示|显示|估计出|估计了|估计的|估计得到|识别出|识别了"
                    r"|(?<!\bto\s)\b(?:ha(?:s|ve|d)(?!\s+(?:been|to|\w+ed)\b)|estimate[sd]?|exert(?:s|ed)?"
                    r"|show(?:s|ed|n)?|demonstrate[sd]?|establish(?:es|ed)?|finds?|found|document(?:s|ed)?"
                    r"|reveal(?:s|ed)?|confirm(?:s|ed)?|identif(?:y|ies|ied))\b", re.I)
#: 「有」只认紧挨着名词的：有因果影响、有着显著而稳健的因果影响；有关、有助于、只有、有效识别、有利于不是断言
_YOU = re.compile(r"(?<![所只已现拥没])有着?(?:[^，,；;。有]{0,6}的)?$")
#: 「效应 / 反应 / 对应 / 相应……」里的「应」不是情态词，「应用」也不是
_MODAL = re.compile(r"需要|可以|可能|有待|未来|能够|才能|方能|很难|较难|难于|(?<![效反响对相适供感顺呼回])应(?!用)"
                    r"|\b(?:should|could|would|might|may|can|future|requir\w*|need(?:s|ed)?|necessary)\b", re.I)
#: 名词后面的存疑：因果关系尚不清楚、留给后续研究、cannot be identified、remains unclear。故意不收单独的「不 / not」——
#: 「因果效应不随时间变化」「a causal effect that does not vary」否定的是别的谓词
_HEDGE_AFTER = re.compile(r"尚不清楚|仍不清楚|不清楚|不明确|不明|难以确定|无法确定|不能确定|难以识别|无法识别|不能识别|未能识别"
                          r"|尚无定论|存疑|有争议|有待|留待|留给"
                          r"|\b(?:cannot|can\'t|could not) be (?:identified|established|estimated|determined|recovered)\b"
                          r"|\b(?:is|are|was|were|remains?|remained) (?:not |un)"
                          r"(?:identified|established|known|clear|resolved)\b"
                          r"|\bremains? (?:unclear|unknown|uncertain|unidentified|unresolved|elusive|contested|open)\b"
                          r"|\bunclear\b|\bunidentified\b", re.I)
_QUOTES = re.compile(r"「[^」\n]*」|“[^”\n]*”|\"[^\"\n]*\"")


@dataclass(frozen=True)
class Hit:
    line: int      #: 去掉代码块之后的行号（跟 `manuscript.process_language` 同一个口径：代码块整段删掉，其后行号前移）
    word: str      #: 命中的原文（连续空白并成一个）
    snippet: str   #: 以命中词为中心的一段：前约 25 字、后约 35 字，截掉处打「…」
    tier: str      #: "hard" | "soft"
    #: 硬拦为什么降成了标出（模糊说法本来就在标出档，恒为空）：
    #:   否定存疑 = 前面有个拿不准的「不」；可能 = 不能排除；名词 = 英文 cause 的名词用法；
    #:   方法 = 宾语或主语是方法、数据处理；设问 = 问句或假设（研究问题里的设问见 `asks_causal`）；
    #:   转述 = 别人的发现；引号 = 在引号里；模型内 = 仿真论文里限定在模型内；
    #:   名义 = 名词类因果词（因果关系、causal effect……）不在下结论的句式里（前面没有断言、估计动词，或有情态、存疑）
    note: str = ""
    start: int = 0  #: 命中处在那一行里的列号（去掉行内代码之后；引用占位符补到了原长，不错位）


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _sentence(ends: list[int], n: int, a: int, b: int) -> tuple[int, int]:
    """命中那一处所在句子的起止。`ends` 是这一行各个句末标点之后的位置——一行只算一次。"""
    k, j = bisect_right(ends, a), bisect_right(ends, b)
    return (ends[k - 1] if k else 0), (ends[j] if j < len(ends) else n)


def _overlaps(taken: list[tuple[int, int]], a: int, b: int) -> bool:
    """`taken` 按起点排好、互不重叠，所以只用看起点在 b 之前的最后一段。"""
    k = bisect_left(taken, (b,))
    return bool(k) and taken[k - 1][1] > a


def _claim(taken: list[tuple[int, int]], a: int, b: int) -> bool:
    """认领 [a, b)。跟已认领的重叠：并成一段、返回 False（同一处只报一次，并掉那段里的模糊词也不另报）；
    不重叠：记下、返回 True。`taken` 始终按起点排好、互不重叠。"""
    lo = bisect_left(taken, (a,))
    if lo and taken[lo - 1][1] > a:
        lo -= 1
    hi = lo
    while hi < len(taken) and taken[hi][0] < b:
        hi += 1
    if hi == lo:
        taken.insert(lo, (a, b))
        return True
    taken[lo:hi] = [(min(a, taken[lo][0]), max(b, taken[hi - 1][1]))]
    return False


def _one(neg: str, between: str, word: str, nounish: bool) -> str:
    """一个否定词对这处命中的判法：`"drop"`、`"可能"`、`"否定存疑"`，或 `""`（不算否定）。
    `between` 是否定词和命中词之间的字。"""
    if neg == "不" and _BU_NOT.match(between + word):
        return ""                                         # 不平等、不足、不高：形容词（未充分识别不是）
    if _TURN.search(between):
        return ""                                         # 不是 A 而是 B
    if _POSSIBLE.match(between):
        return "可能"                                     # 不能排除
    if _DOUBLE.match(between):
        return ""                                         # 不能否认、不会改变……的结论
    if neg == "非":                                       # 非因果效应；「非农就业对收入存在因果效应」不算——
        return "drop" if _ADJACENT.match(between) else ""  # 也不当对比词：「非大学两组的出身效应」不是否定
    if neg.lower() in _CONTRAST:
        return "drop" if nounish or _ADJACENT.match(between) else ""
    if neg == "不" and between and not _BU_DROP.match(between):
        return "否定存疑"
    return "drop"


def _negation(head: str, word: str, nounish: bool) -> str:
    """命中词前面的否定：`"drop"` 否定的就是这个说法；`"可能"` / `"否定存疑"` 降为标出；`""` 没有或被抵消了。"""
    # 占位符、引号抹成的等长空格先并成一个——不然一个 {{result:did.coef}} 就把窗口吃光了
    head = _ws(head).strip()
    found = set()
    # 冒号也截断：「Nothing suggests otherwise: education causes higher income」冒号后面是另一个断言
    for pat, stops, width in ((_NEG_ZH, "。；，：:", 16), (_NEG_EN, ".;,:：", 60)):
        seg = head[max(head.rfind(c) for c in stops) + 1:][-(width + 16):]
        for m in pat.finditer(seg):
            if len(seg) - m.end() <= width:
                found.add(_one(m.group(0), seg[m.end():], word, nounish))
    return next((v for v in ("drop", "可能", "否定存疑") if v in found), "")


def negated(head: str, word: str, nounish: bool = False) -> bool:
    """命中词前面有没有否定它的话。`_negation` 返回分档词，别的检查只需要真假。

    调用方要照 `scan` 的走法先把 `head` 备好，两个前提：

    - `head` 是**同一行、命中词之前**的文本——窗口按分句退，跨行的否定不算数
    - 引号与占位符已经抹成等长空格（见 `scan` 里 `_ws` 那一段）：不抹的话，
      一个 `{{result:did.coef}}` 就把否定词的窗口吃光了

    `nounish` 说命中词是不是名词性的——「而非 / rather than」只直接否定名词。

    **不许再写第二份否定判断**：别的检查要豁免否定语境，调它。
    """
    return _negation(head, word, nounish) != ""


def _noun(word: str, before: str, after: str) -> str | None:
    """英文名词用法：`"drop"` 一看就不是因果；`"名词"` 名词的 cause（判断句除外）；`None` 不是名词用法。"""
    w = word.lower()
    for forms, pre, post, verdict in _NOUNS:
        if w in forms and (pre.search(before) or post.match(after)):
            if verdict == "名词" and _COPULA.search(before) and re.match(r"\s+of\b", after):
                return None                               # Education is the main cause of……：判断句
            return verdict
    return None


def _nounish(word: str, noun: str | None) -> bool:
    """名词性的命中——「而非 / rather than」直接否定的是它。「是……的原因」「is the cause of」是判断句，不算：
    「教育而非收入是生育率下降的主要原因」是在下结论。"""
    return word.lower().startswith(_NOMINAL + ("the effect",)) or noun == "名词"


def _snippet(line: str, s: int, e: int, a: int, b: int) -> str:
    """以命中词为中心：前约 25 字、后约 35 字，截掉的一头打「…」；连续空白并成一个。"""
    pre, word, post = _ws(line[s:a]).lstrip(), _ws(line[a:b]), _ws(line[b:e]).rstrip()
    return ("…" + pre[-25:] if len(pre) > 25 else pre) + word + (post[:35] + "…" if len(post) > 35 else post)


def _judge(line: str, s: int, e: int, a: int, b: int, sim: bool) -> str | None:
    """引号外的一处硬拦：`None` 整条不报；`""` 照拦；别的是降为标出的 note。"""
    word, before, after, sent = line[a:b], line[s:a], line[b:e], line[s:e]
    own = _CLAUSE_END.split(before)[-1]                   # 命中所在分句里、命中词之前
    rest = _CLAUSE_END.split(after, maxsplit=1)[0]        # 命中所在分句里、命中词之后
    noun = _noun(word, before, after)
    neg = _negation(before, word, _nounish(word, noun))
    if noun == "drop" or neg == "drop":
        return None
    if noun or neg:
        return noun or neg
    obj = _METHOD.search(_ws(rest))
    if _METHOD.search(word) or (obj and obj.start() <= 12) or _METHOD_SUBJ.search(_ws(own)):
        return "方法"
    if _ASK.search(own) or "与否" in own + rest:
        return "设问"
    contra = (_CITE not in sent and _CONTRA.search(before) and not _ATTR.search(own)
              and not re.match(r"\s*(?:who|which|whose)\b", own, re.I))
    if (_CITE in sent or _ATTR.search(before)) and not (_SELF.search(before) or _SELF.search(rest) or contra):
        return "转述"
    if sim and SIM_SCOPE.search(sent):
        return "模型内"
    if word.lower().startswith(_NOMINAL) and (not (_FRAME.search(own) or _YOU.search(own))
                                              or _MODAL.search(own) or _MODAL.search(rest)
                                              or _HEDGE_AFTER.search(rest)):
        return "名义"                                     # 最后一步：别的 note 都比它说得多
    return ""


def scan(paper: str, research_type: str = "") -> list[Hit]:
    """逐行找因果说法，分两档。**只找不判**——越不越界是审稿人的事，拦不拦是调用方的事。

    `research_type` 只管一件事：仿真论文里「在本模型内」的句子降为标出，别的类型写这几个字不算数。
    """
    text = _CITE_PH.sub(lambda m: _CITE.ljust(len(m.group(0))), paper)
    body = strip_code_and_quotes(text)
    sim = research_type == "simulation"
    out: list[Hit] = []
    for i, shown in enumerate(body.split("\n"), start=1):
        if shown.lstrip().startswith("|"):
            continue                                      # 表格行
        # 判上下文用的那一行：引号里的字抹掉——除非同一分句里前面是「本文发现 / we find」
        quotes = [q for q in _QUOTES.finditer(shown)
                  if not _SELF_CLAIM.search(_CLAUSE_END.split(shown[:q.start()])[-1])]
        line = shown
        for q in quotes:
            line = line[:q.start()] + " " * (q.end() - q.start()) + line[q.end():]
        ends = [d.end() for d in _DELIM.finditer(line)]
        taken: list[tuple[int, int]] = []                # 硬拦式子认领过的位置（重叠的并成一段）
        # 候选：引号外的（q 为 None），和引号里的
        cands = [(m.start(), m.end(), None) for pat in _HARD for m in pat.finditer(line)]
        cands += [(q.start() + 1 + m.start(), q.start() + 1 + m.end(), q)
                  for q in quotes for pat in _HARD for m in pat.finditer(q.group(0)[1:-1])]
        for a, b, q in cands:
            if not _claim(taken, a, b):
                continue                                  # 「是导致……的原因」：导致已经报过
            s, e = _sentence(ends, len(line), a, b)
            if q is None:
                note = _judge(line, s, e, a, b, sim)
            else:                                         # 引号里：否定照样管用，别的一律「引号」
                head, word = line[s:q.start()] + shown[q.start() + 1:a], shown[a:b]
                noun = _noun(word, head, shown[b:q.end() - 1])
                note = None if _negation(head, word, _nounish(word, noun)) == "drop" else "引号"
            if note is None:
                continue
            out.append(Hit(i, _ws(shown[a:b]), _snippet(shown, s, e, a, b), "soft" if note else "hard", note, a))
        for pat in _SOFT:
            for m in pat.finditer(line):
                a, b = m.span()
                if _overlaps(taken, a, b):
                    continue                              # 已经算在硬拦档那一处里
                s, e = _sentence(ends, len(line), a, b)
                if _negation(line[s:a], m.group(0), True) == "drop":
                    continue
                out.append(Hit(i, m.group(0), _snippet(shown, s, e, a, b), "soft", start=a))
    return out


def asks_causal(hits: list[Hit]) -> list[Hit]:
    """研究问题问的是不是因果（`oss run` 的门用）：硬拦的，加上 note 在 `CAUSAL_QUESTION_NOTES` 里的。"""
    return [h for h in hits if h.tier == "hard" or h.note in CAUSAL_QUESTION_NOTES]


def hard_problems(hits: list[Hit], research_type: str, *, downgraded: bool = False) -> list[str]:
    """硬拦档 → 逐条拒绝理由：行号、那一句、改法。"""
    kind = "从因果降级成的描述" if downgraded else RESEARCH_TYPE_LABELS.get(research_type, research_type)
    way = ("改回相关的说法（「与……相关」「……较高的群体……也较高」）" if downgraded else
           "改成相关的说法（「与……相关」「……较高的群体……也较高」），"
           "或回环节 5 把研究类型改成因果（要过识别审计，并由用户授权 oss anchor switch 重置锚点）")
    return [Finding(f"第 {h.line} 行「{h.snippet}」里的「{h.word}」是明确的因果说法——"
            f"研究类型是{kind}，正文不下因果结论：{way}", "wording.causal_hard")
            for h in hits if h.tier == "hard"]


def soft_warning(hits: list[Hit]) -> Finding | None:
    """标出档汇成一条警告；一处都没有返回 `None`。

    **只此一份**：`oss paper check` 与陷阱集判分器共用它——从前两边各汇一次，
    判分器的注释还自陈是照抄的。
    """
    n_soft = sum(1 for h in hits if h.tier == "soft")
    if not n_soft:
        return None
    return Finding(f"{n_soft} 处模糊的因果说法（影响、提高、促进之类，或转述别人的因果发现）"
                   f"列进了 {FLAGS_FILE}——审稿时交识别审稿人逐条判越界没有；"
                   f"只有确实是因果口气的句子才改成相关说法；学科里的标准术语（固定效应、效应量、"
                   f"冲击之类）照写，由审稿人判", "wording.causal_soft")


def sim_reality_warnings(paper: str) -> list[Finding]:
    """仿真三查第三条：把模拟结果说成现实 / 政策断言、又没限定「在本模型内」的句子，逐句警告。

    门控在调用方（盘上有没有 `sim_state.json`）——这里只判句子。

    **窗口：只按中文句末（。！？!?）与换行切，片段不含标点**，跟 main 一致。
    警戒词与限定语要落在同一片段里才算限定过。这条天生要一个比句子宽的窗口：
    换成 `textspan.sentences()` 会多切分号与英文句点，把句首「在本模型内」的豁免切掉
    （作者写「在本模型内，参数上升；现实中将……」会被误拦），警告正文里也会多出一个句号。
    跟 `manuscript.mde_warnings` 同理，不要「顺手」统一。
    """
    return [Finding(f"仿真结论跨出模型边界：「{s.strip()}」——"
                    f"没限定「在本模型内」就断言现实/政策，措辞收回模型内", "sim.reality")
            for s in re.split(r"[。！？!?\n]", paper)
            if any(w in s for w in SIM_REALITY_LEXICON) and not SIM_SCOPE.search(s)]


def missing_scope_limit(paper: str) -> Finding | None:
    """仿真论文**通篇一次都没限定在本模型内**就返回一条拒绝理由，限定过返回 `None`。

    这一条是 `sim.reality` 那张五个词的清单的**对价**（仿真骨架设计稿第二节）。
    两张清单方向相反，这一点是要害：

    - `SIM_REALITY_LEXICON` 查的是**违规**：作者换个说法（「落到现实中」「对政策的含义是」）
      就漏放，**而漏放一声不响**——这正是 2026-09-16 评测量到 6% 的那个形状
    - 这一条查的是**某样东西必须出现**：作者写了个清单上没有的等价写法，
      结果是**误报**，不是漏放。误报会被投诉、会被修；漏放没人知道

    所以不把违规词表做长（做长也还是 6%），而是换一个问法：
    **模拟跑出来的东西，全文一次都没说过「这是模型内的」，这件事本身就是越界的源头。**

    门控在调用方（盘上有没有 `sim_state.json`、或计划声明为仿真）——这里只判文本。

    **引号里的不算**（跟 `scan` 同一条规矩）：小人说的「在本模型内一切正常」是受访者原话，
    不是作者自己的限定。代码块与占位符内部同理。

    **判的是全文有没有，不是结论那一节有没有。** 限定语当然该落在结果与讨论里，
    但「哪几节算结论」随体例变（`required_sections` 按风格包给），为一条查「有没有做过」
    的门去赌节名识别，换来的是误报——而这条门的整个价值就在于它只会误报、不会漏放。
    节内不节内交审稿人。
    """
    body = _QUOTES.sub(lambda m: " " * len(m.group(0)), strip_code_and_quotes(paper))
    if SIM_SCOPE.search(body):
        return None
    return Finding(
        "仿真论文通篇一次都没有把结论限定在模型内——**模拟跑出来的效应是这个模型、"
        "这套提示词、这批小人在这套条件下的行为反应，不是对现实世界的断言**。"
        "结果与讨论里至少要有一处明写「在本模型内」（英文 within this model / "
        "within the model's dynamics）；「在本模型的设定下」「在本模型框架内」也算。"
        "全文一次都没有，等于默认读者会把模型里的数字当成现实里的数字",
        "sim.no_scope_limit")


def write_flags(rundir: Path, hits: list[Hit], research_type: str) -> Path:
    """标出档写盘交审稿人。**三位审稿人拿同一份**（`oss review --paper` 读它）——只给一位就破了互盲的规矩。"""
    flags = [asdict(h) for h in hits if h.tier == "soft"]
    out = Path(rundir) / FLAGS_FILE
    out.write_text(json.dumps({
        "research_type": research_type, "n": len(flags), "flags": flags,
        "how": "研究类型不是因果：这些是程序标出的模糊因果说法，以及拿不准、没拦下的明确说法"
               "（note 写了为什么没拦：否定存疑 / 可能 / 名词 / 方法 / 设问 / 转述 / 引号 / 模型内 / 名义），逐条判越界没有。"
               "行号对的是 paper.md，不是审稿人在读的 paper_rendered.md——两份文字一样，行号会不一样",
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return out
