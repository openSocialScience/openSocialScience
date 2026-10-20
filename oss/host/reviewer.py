"""跨模型审稿。

**这是整套架构的核心契约**（DECISIONS.md 二）：

> 循环可以驱动自己，但**不能给自己判无罪**。
> executor 与 reviewer 优先用不同模型家族——同一家的模型互审不构成独立验证（ARIS 称之为「非功能」）。

三条实操规矩，全部在这里落地：

**① 家族检查是第一道门。**
缺省只判档次、不拦人：跨家族记「跨模型」，同家族或判不出家族记「自审」，差别照实写进判词、论文必须披露
（2026-09-07 起，见 `check_family`）；`REQUIRE_CROSS_MODEL=1` 时同家族拒绝执行。
**优先跨家族是风险控制，不是已证实的必要条件**（2026-09-13 用户定；依据见 docs/research/related-work.md 第七节）。

**② 只传文件路径的内容，不传摘要或解读。**
一总结，reviewer 就照着你的口径判了。所以这里读原文，不让调用方传摘要。

**③ 每次都是全新对话。**
无状态 API 调用天然满足；但要在这里写明白，免得以后有人加「复用会话省 token」的优化——
ARIS 实测：**叙事累积会抬高分数**。

放在 `host/` 而不是 `core/`：它依赖外部服务和运行时配置，
迁移到自建循环时**换的就是这一层**（DECISIONS.md 1.4）。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from oss.hosts import detect as detect_host
from oss.hosts.executor import hint as executor_hint, resolve as resolve_executor

#: 模型家族的识别规则。判不出家族的一律当成 "unknown"，
#: 而 unknown 不能与任何家族构成「跨家族」——宁可拒绝，不可放行。
_FAMILIES: dict[str, tuple[str, ...]] = {
    "anthropic": ("claude", "opus", "sonnet", "haiku"),
    "openai": ("gpt", "o1", "o3", "o4", "chatgpt", "codex"),
    "deepseek": ("deepseek",),
    "google": ("gemini", "gemma", "palm"),
    "meta": ("llama",),
    "qwen": ("qwen", "tongyi"),
    "moonshot": ("kimi", "moonshot"),
    "zhipu": ("glm", "chatglm"),
    "minimax": ("minimax", "abab"),
    "mistral": ("mistral", "mixtral"),
    "xai": ("grok",),
    #: 腾讯混元。WorkBuddy 的缺省模型就是它（界面上写 `Hy3`，模型表里是
    #: `tencent/hy3` 与 `hy3-free`），认不出来就是 unknown，而 unknown 一律拒审——
    #: 在 WorkBuddy 里跑整条链会停在审稿这一步，报的还是「判不出家族」这种看不懂的错
    "tencent": ("hunyuan", "hy3"),
}


def family_of(model: str) -> str:
    """从模型标识推断家族。判不出就是 unknown。"""
    m = (model or "").lower()
    for fam, hints in _FAMILIES.items():
        if any(h in m for h in hints):
            return fam
    return "unknown"


def require_cross_model(env: dict | None = None) -> bool:
    """要不要坚持「审稿人必须是别家」。缺省不要求——见 `Reviewer.check_family`。

    做正式验收跑时打开（`.env` 里 `REQUIRE_CROSS_MODEL=1`），
    这样「这一次是不是真让别家审的」就不是靠事后翻判词，而是跑不起来就知道。
    """
    import os

    raw = (env if env is not None else os.environ).get("REQUIRE_CROSS_MODEL", "")
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def validation_label(exe_fam: str, rev_fam: str, en: bool = False) -> str:
    """这一次审稿算哪一档，一句人话。**判词文件与论文局限用的是同一句。**"""
    if exe_fam != rev_fam and "unknown" not in (exe_fam, rev_fam):
        return (f"cross-model ({exe_fam} reviewed by {rev_fam})" if en
                else f"跨模型（{exe_fam} 的稿子由 {rev_fam} 审）")
    if exe_fam == rev_fam and exe_fam != "unknown":
        return (f"self-review (both {exe_fam}; not independent validation)" if en
                else f"自审（审稿人与执行者同为 {exe_fam} 家族，不构成独立验证）")
    return ("self-review (model family undetermined; not independent validation)" if en
            else "自审（判不出模型家族，不构成独立验证）")


class SameFamilyRefused(RuntimeError):
    """跨家族这件事没有得到证实。**这是拒绝，不是警告。**

    两种情况都拒绝，但要说清是哪一种：
    - 确认同家族
    - **判不出家族**——「不知道是不是同一家」和「确定不是同一家」在契约上不等价
    """

    def __init__(self, executor: str, reviewer: str, family: str, hint: str = "") -> None:
        if family == "unknown":
            msg = (
                f"拒绝执行：判不出模型家族。\n"
                f"  executor = {executor}\n"
                f"  reviewer = {reviewer}\n"
                f"**判不出家族就不能声称跨家族。**「不知道是不是同一家」"
                f"和「确定不是同一家」在契约上不等价，所以宁可拒绝。\n"
                f"改成 oss/host/reviewer.py 的 _FAMILIES 里认得出的模型名，"
                f"或往那张表里加一条。"
            )
        else:
            msg = (
                f"拒绝执行：executor（{executor}）与 reviewer（{reviewer}）"
                f"都属于 {family} 家族。同家族互审不构成跨模型验证。\n"
                f"改配 REVIEWER_MODEL 为别家的模型，"
                f"或改 EXECUTOR_MODEL 说明宿主实际跑的是什么。"
            )
        if hint:
            # **第一行不许变**：doctor 只打 str(exc).splitlines()[0]
            msg = msg + "\n" + hint
        super().__init__(msg)
        self.executor = executor
        self.reviewer = reviewer
        self.family = family
        # 单独留一份：doctor 要把「怎么设」那几行缩进打出来，而它只该打这几行。
        # 从 str(exc) 里切 [1:] 的话，「同家族」那支（没有 hint）的第二行
        # ——「改配 REVIEWER_MODEL……」——也会被当成提示多打一遍。
        self.hint = hint


class ReviewerUnavailable(RuntimeError):
    """审稿后端用不了。

    **与「审稿通过」和「审稿不通过」都必须区分**——
    它是 `ERROR`，不是判词。拿它当通过是最危险的失败模式。
    """


@dataclass
class ReviewVerdict:
    """六态判词（DECISIONS.md 2.6 的 assurance 契约）。"""

    verdict: str  # PASS | WARN | FAIL | BLOCKED | ERROR | NOT_APPLICABLE
    reason: str = ""
    findings: list[str] = field(default_factory=list)
    reviewer_model: str = ""
    executor_model: str = ""
    reviewer_family: str = ""
    executor_family: str = ""
    cross_family: bool = False
    #: 这一次审稿算哪一档，一句人话（`validation_label` 生成）。
    #: **不许只留 cross_family 一个布尔**：读判词的人（和写论文的模型）需要的是
    #: 「这意味着什么」，而不是一个真假值——「自审不构成独立验证」这句话要能直接抄进局限。
    validation: str = ""
    file: str = ""
    level: str = ""
    emphasis: str = ""   # 本轮侧重。多审稿人互盲时，报告要能追溯到是哪个透镜下产出的
    usage: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "findings": self.findings,
            "reviewer_model": self.reviewer_model,
            "executor_model": self.executor_model,
            "reviewer_family": self.reviewer_family,
            "executor_family": self.executor_family,
            "cross_family": self.cross_family,
            "validation": self.validation,
            "emphasis": self.emphasis,
            "file": self.file,
            "level": self.level,
            "usage": self.usage,
        }


VALID_VERDICTS = {"PASS", "WARN", "FAIL", "BLOCKED", "ERROR", "NOT_APPLICABLE"}

#: 审稿深度。推理模型会先花掉一批 token 想，正文才开始。
#:
#: **这个数不好定**：reasoning 用多少取决于输入，跟深度档位没关系。
#: 实测同一个模型审一个几百字的 JSON 就烧掉 1500——所以光调大数字不够，
#: 还要在拿到空正文时自动加预算重试（见 `_RETRY_MULTIPLIER`）。
#: quick 原来 4000：验收跑里陪审团第一次调用两次都返回空正文（推理就吃掉了 4000，
#: 加到 12000 还空），执行者只好再跑一次。推理模型的「快」也得给够想的预算。
LEVELS: dict[str, int] = {"quick": 8000, "normal": 12000, "deep": 16000}

#: 空正文时加多少预算重试。只重试一次——再空就是真有问题，不是预算不够。
_RETRY_MULTIPLIER = 3


#: 「新发现」段的标题。**工具按这个字符串把这一段的意见排除在 FAIL 依据之外。**
FOLLOWUP_HEADING = "新发现（不作为 FAIL 依据）"

#: 只有论文类审稿有「上一轮意见」可判。桌审是送审前的门，因果审计与选题陪审是别的环节。
FOLLOWUP_KINDS = frozenset({"paper", "alternatives", "editor"})

#: 认「新发现」段标题用的正则。**首尾都锚。**`_parse` 先把行首的 `#*• ` 和行尾的
#: `*：: ` 剥掉（所以 `## 新发现（…）`、`**新发现（…）**`、`新发现（…）：` 都认），
#: 归一化之后剩下的部分必须**整个**就是这个标题：
#: - 少了行首锚：REASON 里顺口提一句「新发现（不作为 FAIL 依据）有两条」，
#:   从那行往下的**全部**意见就都被打上 [新发现]，一整份审稿意见凭空作废。
#: - 少了行尾锚：`新发现（不作为 FAIL 依据）：无` 这种带正文的行也会开段，
#:   后面那几条真义务全被当成新发现放过去——正是这个功能要防的事。
#: 全半角括号、中间有没有空格都认——模型不会一字不差地照抄标题。
_FOLLOWUP_HEADING_RE = re.compile(r"新发现\s*[（(]\s*不作为\s*FAIL\s*依据\s*[)）]\s*$", re.I)


def followup_brief(kind: str, round_: int, previous: list[str]) -> str:
    """第 2 轮起追加在提示词后面的简报。第 1 轮返回空串——**老用法一个字都不变**。

    事故（2026-08-28，DeepSeek 真跑）：五轮审稿全 FAIL，而每一轮 FAIL 的理由
    都跟上一轮不同——作者改一条，审稿人换一条新的提。**那不是收敛，是移动的球门。**
    第二轮起把任务钉死：只判上一轮那几条处置了没有。
    """
    if round_ < 2 or kind not in FOLLOWUP_KINDS:
        return ""
    if previous:
        listed = "\n".join(f"{i}. {f}" for i, f in enumerate(previous, start=1))
        head = f"上一轮（第 {round_ - 1} 轮）的意见，共 {len(previous)} 条：\n\n{listed}"
    else:
        head = ("**上一轮没有留下任何意见**（报告丢了或后端当时不可用）。"
                "这种情况如实说明，判 BLOCKED，不要拿这一轮新看到的问题判 FAIL。")

    if kind == "editor":
        return (
            f"\n\n---\n\n**这是第 {round_} 轮的综合。你综合的是「上一轮的意见处置了没有」，"
            f"不是重新给一封新的决定信。**\n\n{head}\n\n"
            f"每一条都要在决定信里出现，并注明审稿人判的是 `处置了` / `写进局限了` / "
            f"`不同意` / `没交代`；几位判得不一样就裁，明说怎么裁。\n\n"
            f"逐条判的结果写进 FINDINGS，一条一行，**保留原意见的方括号标签，编号放在标签后面**：\n\n"
            f"```\n"
            f"- [设计] 1 处置了 —— 改在 4.2 节，…\n"
            f"- [文字] 2 没交代\n"
            f"```\n\n"
            f"**判 FAIL 只能依据「没交代」的那几条。**审稿人标了 `[新发现]` 的条目照抄进决定信、"
            f"排在最后，注明**不作为 FAIL 依据**、本轮不要求作者解决；值得做的写进论文的 Open questions。\n"
        )

    return (
        f"\n\n---\n\n**这是第 {round_} 轮复审。你的任务是判「上一轮的意见处置了没有」，"
        f"不是重新审一遍这篇稿子。**\n\n{head}\n\n"
        f"逐条判，每条只能是这四种之一，写在意见编号后面：\n\n"
        f"- `处置了` —— 稿子里改了；指出改在哪一节、改成了什么\n"
        f"- `写进局限了` —— 没改设计，但局限（或 Open questions）里有对应的一句；把那句话引出来\n"
        f"- `不同意` —— 作者给了理由不改。**判理由成不成立**：成立就按「写进局限了」对待，"
        f"不成立按「没交代」算。\n"
        f"- `没交代` —— 既没改也没写进去。**只有「没交代」能构成 FAIL。**\n\n"
        f"逐条判的结果写进 FINDINGS，一条一行，**保留原意见的方括号标签，编号放在标签后面**：\n\n"
        f"```\n"
        f"- [设计] 1 处置了 —— 改在 4.2 节，…\n"
        f"- [文字] 2 没交代\n"
        f"```\n\n"
        f"判词按这条规则给：有「没交代」→ FAIL；全是「处置了」/「写进局限了」→ PASS 或 WARN。\n"
        f"**不许因为这一轮新看到的问题判 FAIL。**\n\n"
        f"这一轮新看到的问题写在最后，单起一段，标题原样写成这一行：\n\n"
        f"{FOLLOWUP_HEADING}\n"
        f"- [新发现] <问题>\n\n"
        f"**这一段里每一条都必须以 `[新发现]` 开头**——工具按这个标记把它们排除在 FAIL 依据之外。\n"
        f"没有新发现就不写这一段。\n"
    )


_PROMPTS = {
    "ideas": """你是一位资深的定量社会科学审稿人。下面是一份研究 idea 候选清单。

**你的任务是收窄它，不是夸它。**产出这份清单的模型不能给自己判无罪，所以由你来判。

逐个评估，重点看六件事：

1. **理论对话**——它说得出自己在推进或挑战哪个理论吗？说不出的不是 idea。
2. **识别策略**——外生变异从哪来？「用面板固定效应」在经济学里通常不够。
3. **新颖性**——这个问题是不是已经被做过了？注意：同一个问题**用更强的识别**做一遍
   是正当贡献，不算重复。
4. **结果为空时是否仍成立**——价值绑在结果方向上的 idea 是 p-hacking 的根源。
5. **研究类型标得对不对**——每个候选的 `research_type`（causal / descriptive / measurement / simulation）
   按「想下什么结论」定：想说「X 影响 Y」就是 causal，哪怕只打算用普通回归；只描述分布与趋势是 descriptive。
   **标成描述、却在问因果的，点名**——那是在躲识别审计。
6. **描述类候选新在哪**——`research_type` 是 descriptive 的，说得出新在哪才留：新数据、新事实、新测量，
   或模式出人意料、跟现有理论对不上。说不出的描述题砍掉。**经济学门槛最高**：纯描述要有新数据、新事实或新测量。

判词只能是这六个之一：PASS / WARN / FAIL / BLOCKED / ERROR / NOT_APPLICABLE

按这个格式回答，不要有别的内容：

VERDICT: <判词>
REASON: <一句话总评>
FINDINGS:
- <idea_id>: <保留还是砍掉，为什么>
- <idea_id>: ...
""",
    "paper": """你是一位资深的定量社会科学审稿人。下面是一篇论文初稿，
连同它的分析计划、结果、功效报告与文献语料的原文。

**每条意见开头必须打一个类别标签**，方括号，三选一：
- `[设计]` —— 要改分析设计才能解决：换估计量、换样本、换对照组、补检验、换识别策略
- `[数据]` —— 要补数据或重新构造变量才能解决
- `[文字]` —— 改措辞、补说明、补局限就能解决
没打标签的意见会被当成 `[设计]` 处理。**判不了就按最严重的算。**

**产出这篇稿子的模型不能给自己判无罪，所以由你来判。找问题，不要找优点。**

**正文里的数字已经由程序从结果文件填入，不需要你核数字。**
你要核的是**话**——机械规则查不了的那一层：

1. **结论有没有超出设计支持的范围。**
   最常见的形态是**方向说反**：作者说「某个作用在增强」，
   而系数的符号说的是相反的事。注意交互项——
   一个正的交互系数配一个负的主效应，含义往往是「作用在减弱」，
   写成「增强」就是错的，但符号本身对不上这个错。
   另一种形态是把关联说成因果：没有外生变异却写「导致」「使得」「效应」。

2. **局限写全没有。**对着 `power.json` 的判词、`results.json` 的诊断、
   `literature.json` 的 notes 逐条看：
   功效（描述类是精度）判 fail 或 gray 写了吗？共线报「严重」提了吗？
   语料覆盖的缺口（比如中文文献未覆盖）说了吗？
   代理变量交代了吗？**该写没写的比写错更严重，因为读者不会知道有这回事。**
   如果材料里有 `review_plan.json`（因果设计的独立审计），它的每一条意见
   都要在局限里找得到对应的交代——审计判 FAIL 不要求作者换设计，
   但**要求如实写出来**；漏了哪条就指出哪条。

3. **稳健性是不是事后补的。**对着 `plan.json` 里**事前锁定**的 `robustness` 清单看：
   正文报告的检验，是不是清单里的那些？清单里有而正文没报的，有没有说明为什么？
   **事后挑好看的规格报告，是这个领域最常见的不端。**

4. **有没有把统计显著说成证明。**观察性设计尤其谈不上。

5. **理论对话站不站得住。**结论跟它声称在推进/挑战的理论，逻辑上接得上吗？

6. **对比句里的两个数是不是同一个量。**含交互项的模型里，处理变量的系数是
   **参照组的条件效应**，不是平均效应；把它跟另一个不含交互项的规格的平均效应
   放进一句话比（「限制样本后效应从 1.5 掉到 0.7」），比的是两个不同的量。
   每个被对比的数，看它来自 `results.json` 里哪个规格、那个规格有没有交互项、样本是不是同一个。

7. **用了抽样调查数据的**（`plan.json` 里有 `survey` 这一块，或论文说数据来自 CGSS、CFPS、GSS 这类调查）多问三件：
   - **权重校准了哪些维度？**无应答跟结局相关的维度（比如学历）进没进权重——没进就要写进局限。
     2016 年美国大选民调失准，主要原因之一就是没按学历调整大学毕业生的过度代表
   - **总调查误差逐类交代了没有**：抽样、覆盖、无应答、测量、加工五类，缺哪类点名
   - **目标总体与抽样框对得上吗？**推论写到抽样框以外的总体（例如只抽了城镇却推到全国）就点名

8. **描述类论文**（`plan.json` 的 `research_type` 是 descriptive）多问三件：
   - **意外的可能**：结果是不是由测量方式注定的——换一种合理的测量，结论还会不会一样
   - **现象先站住了没有**：正文要解释的现象，本数据先证明它存在了吗；先确立现象，再解释它
   - **推导链哪一环最松**：从概念、测量、函数形式、边界条件到比较的口径，哪一步最经不起推敲

判词只能是这六个之一：PASS / WARN / FAIL / BLOCKED / ERROR / NOT_APPLICABLE

**没有问题就判 PASS，不要为了显得认真而编问题。**
但**方向说反、该写没写、稳健性事后补**这三类，只要出现就至少 WARN。

按这个格式回答，不要有别的内容：

VERDICT: <判词>
REASON: <一句话总评>
FINDINGS:
- <哪一节、哪句话、什么问题、依据是哪个文件里的什么>
- ...
""",
    "identification": """你是一位资深的定量社会科学审稿人。
下面是一份因果研究的分析计划与结果，包含机器已经算出的若干诊断。

**每条意见开头必须打一个类别标签**，方括号，三选一：
- `[设计]` —— 要改分析设计才能解决：换估计量、换样本、换对照组、补检验、换识别策略
- `[数据]` —— 要补数据或重新构造变量才能解决
- `[文字]` —— 改措辞、补说明、补局限就能解决
没打标签的意见会被当成 `[设计]` 处理。**判不了就按最严重的算。**

**机器已经查过这几件事，你不必重复：**处理前走势平不平（平行趋势，联合检验）、预检对有意义的违背有多大把握（预检功效）、
违背要多大结论才翻（breakdown 值）、对照组是谁、两组处理前可不可比、各批次的效应差多少；
工具变量的强弱（有效 F）与对弱工具稳健的区间（AR 区间、tF 区间）、2SLS 与 OLS 差多少；
用普通回归下因果结论的，要多强的未观测混淆才推翻结论（稳健值、Oster δ、E 值）。
诊断结果就在文件里，**直接引用它们，不要另算**；写着「没算」的就是没算，不许当成通过。

**你只判机器判不了的那一半——「这个因果设计站得住吗」：**

1. **为什么是这些单位先被处理？**
   如果它们是因为**本来就在变**才被选中（经济已在增长的省份先试点、
   已在改善的国家先民主化），那么「效应」里混着选择本身。
   平行趋势通过**不等于**外生——它只说明处理前几期没显著分化，
   而选择可以发生在更早、或基于不可观测的东西。

2. **对照组合适吗？**
   「从未被处理」的单位是不是根本就是另一类单位？
   （从未民主化的国家可能系统性地不同）
   「尚未被处理」的单位当对照时，它们后来也会被处理，
   这在分批设定下会引入偏误。

3. **有没有溢出？**
   处理一个单位会不会影响到它的邻居？（政策示范、人口流动、贸易）

0. **样本是不是用结果变量的处理前走势筛出来的？**看 `sample_filter` 和派生变量：
   「只留处理前没有下滑的单位」「只留前置期平坦的转型」这类筛选，
   会让平行趋势检验**机械地通过**——那不是识别，是挑样本过检验。
   如果是，判它：估计对象因此变成了谁？论文有没有把估计对象的变化说成主结论的一部分？
   这一条要**先于**平行趋势那条看，因为它决定了平行趋势通过还算不算证据。
   有溢出的话对照组也被影响了，效应会被低估甚至符号反转。

4. **有没有抢跑？**
   单位会不会在政策**正式实施之前**就开始反应（预期到要改）？
   逐年展开图上处理前几期的轨迹能看出端倪，但要不要紧要靠判断。

5. **结果变量的测量方式随时间变过吗？**
   统计口径改过、覆盖范围变过的话，「效应」可能只是口径变了。

6. **问的量与目标总体对得上吗？**看 `plan.json` 的 `estimand`：估出来的是谁的、什么量；
   样本（`sample_filter`）与权重跟写的目标总体对不对得上。

7. **控制变量里有没有中介或碰撞？**对着 `control_roles` 逐个看：被处理影响的变量（中介）、
   同时受处理与结果影响的变量（碰撞）不该控制，控了会把估计推离因果效应。标错了就点名。

8. **排他性（工具变量）**：借力变量凭什么只通过处理影响结果？有效 F 与 AR 区间只管「工具强不强」，
   不管这一条；这一条只能靠论证。

9. **敏感性数字够不够撑结论？**稳健值、Oster δ、E 值大，只说明「要多强的混淆才推翻」，**不说明没有混淆**；
   对着「敏感性基准值」（最强的已观测控制变量）看，论文要是把这些数写成「证明了没有遗漏变量」，点名。

判词只能是这六个之一：PASS / WARN / FAIL / BLOCKED / ERROR / NOT_APPLICABLE

- 设计站得住、上述几条都没有明显问题 → PASS
- 有一条讲不通但不致命 → WARN
- **处理时点明显内生、或对照组明显不可比** → FAIL

**不要因为「这是观察性数据所以不能做因果」就一律判 FAIL。**
政策冲击对比本来就是为观察性数据设计的；要判 FAIL 得指出**具体**哪里站不住。

按这个格式回答，不要有别的内容：

VERDICT: <判词>
REASON: <一句话总评>
FINDINGS:
- <上述各条中的哪一条>: <具体问题，以及它会让效应偏向哪个方向>
- ...
""",
    "desk": """你是一家定量社会科学期刊的编辑，正在做桌审：**十分钟之内**决定这篇稿子是直接退回，
还是送外审。下面是论文成稿（数字已由程序从结果文件填入，不用核数字）。

桌审看的不是细节，是**一个编辑三十秒内会形成的印象**。逐条判：

1. **贡献一句话说得清吗？**读完摘要和引言第一段，能不能用一句话复述「这篇比已有研究多知道了什么」。
2. **问题和设计对得上吗？**它问的是因果问题，用的设计能不能回答因果；问的是「效应多大」，
   设计能不能测出那个量级（摘要里有点估计、不确定性、样本量吗）。
3. **识别策略有名字、假设有陈述吗？**「双重差分」「工具变量」总得有一个；那个策略成立的前提，
   正文有没有用一句话说出来并给出证据。
4. **有表吗？**描述统计表、主结果表、稳健性表。系数全用散文报的稿子，编辑会直接退。
5. **正文里有没有研究过程的痕迹？**「审计」「诊断工具报告」「R3 规格」「判词」——
   这些属于附录或回应信，不属于正文。出现一处就是负面印象。
6. **话有没有超出设计？**没有外生变异却写「导致」「效应」；不显著却说「没有效应」。
7. **文献是不是只引了一边？**理论对话要有对立面；只引支持自己的一派，编辑会怀疑作者不知道另一派。
8. **局限写的是局限，还是免责声明？**好的局限说清「这个问题会让估计偏向哪边、偏多少」；
   坏的局限只说「未来研究可以……」。
9. **摘要有具体数字吗？**没有系数、没有样本量、没有最小可探测效应的摘要，编辑判「没结果」。
10. **写作能不能读？**第一页有没有让人停下来重读三遍的句子。

**每条意见开头打一个类别标签**，方括号，三选一：`[设计]`（要改分析设计）、
`[数据]`（要补数据或重构变量）、`[文字]`（改措辞、补说明、补表）。没打标签按 `[设计]` 算。

判词只能是这六个之一：PASS / WARN / FAIL / BLOCKED / ERROR / NOT_APPLICABLE

- 会送外审 → PASS；会送但要作者先补两三处 → WARN；**会直接退回** → FAIL。
- 第 4、5、9 条任一条不过，至少 WARN；第 1、2 条不过，FAIL。

按这个格式回答，不要有别的内容：

VERDICT: <判词>
REASON: <一句话：退回还是送审，为什么>
FINDINGS:
- <第几条、哪一节、什么问题>
- ...
""",
    "alternatives": """你是一位以「找替代解释」出名的定量社会科学审稿人。下面是一篇论文成稿，
连同它的分析计划与结果（含各批次的效应、各批次的成员名单、稳健性各项的结果）。

**你的任务只有一件：这篇论文的主要结果，还能被什么解释？**
不是挑措辞，不是核数字（数字已由程序填入）。你要做的是审稿人最值钱的那件事——
想出作者没想到的那个故事。

方法：

1. **先看结果的形状**，不看作者的解释。哪一批、哪一段时间、哪一组的效应最极端？
   对着成员名单看：**效应最负 / 最正的那几批，是不是同一类单位、同一段历史？**
   （例：效应最负的批次全是某一地区在某个年代转型的国家——那段历史里发生过什么
   跟结果变量直接相关、跟处理无关的事？）
2. **同期发生了什么？**处理铺开的那些年，有没有别的政策、技术、疫病、危机也在铺开，
   而且落在同一批单位上？年份固定效应吸不掉「只落在处理组身上」的同期冲击。
3. **测量在变吗？**结果变量、处理变量、调节变量的统计口径、覆盖范围在样本期内改过吗？
4. **谁进了样本、谁掉了？**合并丢掉的、缺失剔掉的、筛选排除的，是不是系统性地偏向某一类？
5. **反过来行不行？**结果变量的变化会不会反过来引起处理（或调节变量）的变化？
6. **调节变量是不是别的东西的代理？**它在跨单位比较里跟什么高度相关（发展水平、地区）？
   「调节效应」会不会只是那个东西的效应？

**每一条替代解释都必须写成四段**，缺一段就不算：
- **故事**：什么机制 / 冲击 / 混淆；
- **它会产生什么形状**：在哪些批次 / 时段 / 子样本上，符号如何、量级如何——要跟结果里的形状对得上；
- **怎么排除**：一个用**手头这份数据**就能做的检查——一条 `sample_filter`（剔掉哪一批 / 哪一段）、
  一个控制变量、一个固定效应（地区×年份）、一个安慰剂结果；写清具体列名或批次；
- **现有结果排除了没有**：稳健性里哪一项已经碰到了它，碰到了多少。

**每条意见开头打一个类别标签**，方括号：`[设计]`（要加检查或改设计才能排除）、
`[数据]`（要补数据才能排除）、`[文字]`（现有结果已排除，只需正文说明）。没打标签按 `[设计]` 算。

判词只能是这六个之一：PASS / WARN / FAIL / BLOCKED / ERROR / NOT_APPLICABLE

- 想不出有分量的替代解释 → PASS（**不要为了显得认真而编一个**）
- 有替代解释，但现有稳健性已大致排除，只差正文说明 → WARN
- **有一条替代解释既讲得通、又没被排除、而且排除后主结果可能翻转** → FAIL

按这个格式回答，不要有别的内容：

VERDICT: <判词>
REASON: <一句话：最要紧的那个替代解释是什么>
FINDINGS:
- [标签] <故事> ｜ 形状：<…> ｜ 排除：<具体检查> ｜ 现有结果：<…>
- ...
""",
    "editor": """你是这篇稿子的责任编辑。下面是论文成稿，以及**几位互盲审稿人各自独立写的报告**
（他们互相看不到对方的报告）。你的任务是综合成一封给作者的决定信。

**你不是第四位审稿人。**不要另起炉灶提新问题；你的工作是：

1. **去重**：几位提的是同一件事的，合成一条，注明是几位都提了（多位独立提到的分量更重）。
2. **排序**：先列**必须改**的（会推翻结论、或投稿会被拒的），再列**应该改**的，最后是**可选**的。
   必须改的通常是设计级：识别不成立、估计量不对、替代解释没排除、结论超出设计。
3. **裁分歧**：审稿人之间意见相反的（一位说要加控制变量，一位说那是坏控制），
   **明说两边各说了什么、你怎么裁、为什么**——不许含糊过去。
4. **核对稿子**：审稿人说「没写」的东西，稿子里其实写了的，指出来（审稿人也会看漏）。
5. **给作者一条路**：每一条必须改的，写清「改什么、改到什么程度算改完」。

**每条开头打一个类别标签**，方括号，沿用审稿人的：`[设计]` / `[数据]` / `[文字]`。
没打标签按 `[设计]` 算。

判词只能是这六个之一：PASS / WARN / FAIL / BLOCKED / ERROR / NOT_APPLICABLE

- 没有必须改的 → PASS；有必须改但都是文字 / 数据级 → WARN；**有设计级的必须改** → FAIL。
- 审稿人有 FAIL 而你判 PASS，必须在 REASON 里说明为什么不采纳。

按这个格式回答，不要有别的内容：

VERDICT: <判词>
REASON: <一句话：整体决定与最要紧的一条>
FINDINGS:
- [标签] 必须改 ｜ <合并后的意见>（几位提到） ｜ 改到什么程度算完
- [标签] 应该改 ｜ ...
- [标签] 分歧 ｜ A 说… / B 说… ｜ 裁定：…
- ...
""",
    "citations": """你是一位严格的社会科学审稿人。下面是一篇论文里的若干处引用，
每一处都给出了**论文原话**和**被引文献的标题与摘要原文**。

**你只判一件事：这句话，是这篇文献支持的吗？**

不要评论文笔，不要评论研究设计，不要核数字。只回答四种情况之一：

- **支持**——文献确实说了这个意思
- **过度引申**——文献说了相关的事，但作者把它推得更远（比如把相关说成因果、
  把某一国的结论说成普遍规律、把「部分下降」说成「消失」）
- **说反了**——文献的结论与作者转述的方向相反
- **张冠李戴**——这篇文献根本不是讲这件事的
- **判不了**——摘要信息不足，无法判断。**判不了就说判不了，不要猜**

> ⚠️ **只有摘要不等于读过全文。**摘要没提到的，不代表文中没有；
> 所以「摘要里没说」应当判「判不了」，而不是「张冠李戴」——
> 除非摘要显示这篇文献讲的完全是另一个主题。

判词只能是这六个之一：PASS / WARN / FAIL / BLOCKED / ERROR / NOT_APPLICABLE

- 全部「支持」或「判不了」→ PASS
- 有「过度引申」→ 至少 WARN
- 有「说反了」或「张冠李戴」→ FAIL

按这个格式回答，不要有别的内容：

VERDICT: <判词>
REASON: <一句话总评>
FINDINGS:
- <引用编号>: <四种情况之一> —— <理由，指出摘要里的哪句话支持或不支持>
- ...
""",
    "generic": """你是一位严格的审稿人。审下面这份文件。

**产出它的模型不能给自己判无罪，所以由你来判。**找问题，不要找优点。

判词只能是这六个之一：PASS / WARN / FAIL / BLOCKED / ERROR / NOT_APPLICABLE

按这个格式回答，不要有别的内容：

VERDICT: <判词>
REASON: <一句话总评>
FINDINGS:
- <具体问题>
- <具体问题>
""",
}


class Reviewer:
    """OpenAI 兼容端点的审稿后端。"""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        executor_model: str | None = None,
        host: str | None = None,
        rundir: Path | str | None = None,
    ) -> None:
        # 用 None 而不是 "" 当「没传」的哨兵。
        # 写成 `x or os.getenv(...)` 的话，**显式传空字符串会被环境变量悄悄顶掉**——
        # 调用方想说「我不知道 executor 是谁」，却拿到了 .env 里的值，
        # 家族检查于是放行了一次本该被拒绝的调用。这类静默回退正是契约的漏点。
        def _cfg(given: str | None, env: str) -> str:
            return os.getenv(env, "") if given is None else given

        self.base_url = _cfg(base_url, "REVIEWER_BASE_URL").rstrip("/")
        self.model = _cfg(model, "REVIEWER_MODEL")
        self.api_key = _cfg(api_key, "REVIEWER_API_KEY")
        # 宿主只影响两件事：执行者从哪儿问、拒绝时怎么说。探测本身没有副作用。
        self.host = host or detect_host()
        if executor_model is None:
            found = resolve_executor(self.host, rundir)
            self.executor_model, self.executor_source = found.model, found.source
        else:
            # 显式传了（哪怕是空串）就用它。调用方说「我不知道 executor 是谁」，
            # 就不该从环境或状态里替他找一个出来——那正是家族检查的漏点。
            self.executor_model, self.executor_source = executor_model, "explicit"

    # ------------------------------------------------------------ 家族检查

    def check_family(self) -> tuple[str, str]:
        """跑在任何调用之前。**缺省只判档次，不拦人**；要拦得显式开严格档。

        2026-09-07 改：原先同家族（或判不出家族）一律拒绝执行。那条规矩对
        「验证的可信度」是对的，但它把**门槛压在了任何人跑起来之前**——
        新用户得先凑齐两家模型的钥匙才能看见这套东西好在哪，绝大多数人卡在这儿。
        项目要开源，这个成本换不来相应的收益。

        改成两档，**差别照实记进判词，不静默等同**（`cross_family` 与 `validation`）：
        - **跨模型**：两家不同 → 独立验证
        - **自审**：同家族，或判不出家族 → 有用（审稿人只看产出物、上下文是隔离的、
          拿的是对抗性清单），但**不构成独立验证**：同家族的模型倾向于认同自己写的东西，
          而「这个新不新」「因果表述站不站得住」正是最吃这一口的判断

        要恢复老行为（比如做正式验收跑）：`.env` 里 `REQUIRE_CROSS_MODEL=1`。
        """
        rev = family_of(self.model)
        exe = family_of(self.executor_model)
        if not require_cross_model():
            return exe, rev
        if rev == exe and rev != "unknown":
            raise SameFamilyRefused(self.executor_model, self.model, rev)
        if rev == "unknown" or exe == "unknown":
            # 判不出家族就不能声称跨家族。宁可拒绝，不可放行——
            # 「不知道是不是同一家」和「确定不是同一家」在契约上不等价。
            raise SameFamilyRefused(
                self.executor_model or "(未配置 EXECUTOR_MODEL)",
                self.model or "(未配置 REVIEWER_MODEL)",
                "unknown",
                hint=executor_hint(self.host),
            )
        return exe, rev

    @staticmethod
    def cross_family(exe_fam: str, rev_fam: str) -> bool:
        """这一对算不算跨家族。判不出家族的**不算**——「不知道是不是同一家」
        和「确定不是同一家」在契约上不等价，这一条没有因为放松拒绝而改变。"""
        return exe_fam != rev_fam and "unknown" not in (exe_fam, rev_fam)

    def is_configured(self) -> bool:
        return bool(self.base_url and self.model and self.api_key)

    # ---------------------------------------------------------------- 审

    def _post(self, system: str, path: Path, content: str, max_tokens: int) -> dict:
        """一次无状态调用 = 一次全新对话。

        **不要改成复用会话**——叙事累积会抬高分数（ARIS 实测）。
        重试时也必须重新发全部内容，不能接着上一轮说。

        **走流式。**验证跑里审稿阶段占了整场 127 分钟里的 77 分钟，
        七次论文审稿调用五次撞 HTTP 524、三次没拿到判词：那是网关（Cloudflare 类）在等不到源站
        **第一个字节**约 100 秒后自己挂断——推理模型先想几分钟再吐正文，
        非流式请求在它开口之前就被切了，跟包多大、后端好不好没关系。
        流式下源站边想边发（`reasoning_content` 分片），连接就不会被判死。
        拼回来的结构跟非流式一样：`choices[0].message.content` + `usage`。
        """
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": f"文件：{path.name}\n\n{content}"},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        url = f"{self.base_url}/chat/completions"
        # 连 60 秒、每次读 300 秒：流式下「读」是分片间隔，推理再久也有心跳
        timeout = httpx.Timeout(300.0, connect=60.0)
        text_parts: list[str] = []
        usage: dict = {}
        model = ""
        try:
            with httpx.stream("POST", url, headers=headers, json=payload, timeout=timeout) as resp:
                if resp.status_code != 200:
                    body = resp.read().decode("utf-8", "replace")
                    raise ReviewerUnavailable(f"HTTP {resp.status_code}: {body[:200]}")
                ctype = resp.headers.get("content-type", "")
                if "text/event-stream" not in ctype:
                    # 网关不认 stream 参数、直接回了整包 JSON——照旧解析
                    raw = resp.read().decode("utf-8", "replace")
                    try:
                        return json.loads(raw)
                    except json.JSONDecodeError as exc:
                        raise ReviewerUnavailable(f"回复不是 JSON 也不是事件流：{raw[:200]}") from exc
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    model = chunk.get("model") or model
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    for ch in chunk.get("choices") or []:
                        delta = ch.get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            text_parts.append(piece)
        except httpx.RequestError as exc:
            raise ReviewerUnavailable(f"{type(exc).__name__}: {exc}") from exc
        return {
            "model": model,
            "choices": [{"message": {"content": "".join(text_parts)}}],
            "usage": usage,
        }



    def review(
        self,
        path: Path | str | list[Path | str],
        level: str = "normal",
        kind: str = "generic",
        emphasis: str = "",
        max_bytes: int = 120_000,
        round_: int = 1,
        previous_findings: list[str] | None = None,
    ) -> ReviewVerdict:
        """审一个或多个文件。

        **审论文必须跨产出物比对**：「稳健性是不是事后补的」只看 `paper.md`
        回答不了，要对着 `plan.json` 里事前锁定的清单看；
        「局限写全没有」要对着 `power.json` 与 `literature.json` 的 notes 看。

        多文件时是**机械拼接原文**，每段标明来自哪个文件。
        **不做摘要**——一总结就带上了调用方的口径，那正是这条契约要防的。

        `emphasis` 是**本轮的侧重简报**，用于互盲的多审稿人
        （借自 `nature-reviewer`：每个审稿人只拿到同一份不可变材料 + 自己的侧重，
        **不给别人的意见、不给共享的问题清单、不给草稿综合**）。
        侧重是**工作透镜，不是审稿人身份或履历**。
        第 2 轮起的例外：论文类复审会收到上一轮全部意见（含编辑综合），
        因为判「处置了没有」必须看见那份清单；这不是独立发现，是核对。

        `max_bytes` 是硬上限。实测踩到过：把 369 KB 的语料原文整个发出去，
        网关直接 524 超时——**而超时的表现是「审稿后端不可用」，
        看起来像后端坏了，其实是包太大**。宁可提前拒绝并说清楚。
        """
        if not self.is_configured():
            raise ReviewerUnavailable(
                "审稿后端未配置。需要 REVIEWER_BASE_URL / REVIEWER_MODEL / REVIEWER_API_KEY"
            )
        exe_fam, rev_fam = self.check_family()

        paths = [Path(p) for p in (path if isinstance(path, list) else [path])]
        if not paths:
            raise ReviewerUnavailable("没有给要审的文件")
        for p in paths:
            if not p.exists():
                raise ReviewerUnavailable(f"要审的文件不存在：{p}")

        if len(paths) == 1:
            content = paths[0].read_text(encoding="utf-8")
        else:
            content = "\n\n".join(
                f"===== 文件：{p.name} =====\n{p.read_text(encoding='utf-8')}"
                for p in paths
            )
        path = paths[0]

        if len(content.encode("utf-8")) > max_bytes:
            sizes = "、".join(
                f"{p.name} {len(p.read_text(encoding='utf-8').encode('utf-8')):,}B"
                for p in paths
            )
            raise ReviewerUnavailable(
                f"要审的内容 {len(content.encode('utf-8')):,} 字节，超过上限 {max_bytes:,}。"
                f"各文件：{sizes}。"
                f"**发出去多半会网关超时，而超时看起来像后端坏了**——"
                f"先挑出真正要比对的部分再审"
            )

        system = _PROMPTS.get(kind, _PROMPTS["generic"])
        system += followup_brief(kind, round_, list(previous_findings or []))
        if emphasis:
            system += (
                f"\n\n---\n\n**本轮侧重**：{emphasis}\n\n"
                f"其余方面照常看，但**这一轮请把注意力放在上面这条上**。\n"
                f"你是独立审的：没有别人的意见给你参考，也不要猜别人会说什么。"
            )
        data = self._post(system, path, content, LEVELS.get(level, LEVELS["normal"]))

        text = (data["choices"][0]["message"]["content"] or "").strip()

        if not text:
            # 推理模型把 token 全花在 reasoning 上，正文为空。加预算重试一次。
            reasoning = (data.get("usage", {})
                         .get("completion_tokens_details", {}).get("reasoning_tokens"))
            budget = LEVELS.get(level, LEVELS["normal"]) * _RETRY_MULTIPLIER
            data = self._post(system, path, content, budget)
            text = (data["choices"][0]["message"]["content"] or "").strip()
            if not text:
                # 再空就不是预算问题了。**这是 ERROR，不是 PASS**——
                # 空回复绝不能当成「没意见 = 通过」。
                raise ReviewerUnavailable(
                    f"审稿模型两次都返回空正文"
                    f"（第一次 reasoning 用了 {reasoning}，第二次预算给到 {budget}）"
                )

        v = _parse(text)
        v.reviewer_model = data.get("model") or self.model
        v.executor_model = self.executor_model
        v.reviewer_family = rev_fam
        v.executor_family = exe_fam
        # **照实记，不许一律 True。**缺省不再拦同家族，所以「走到这里」不再等于跨家族了；
        # 写死 True 会让自审的判词长得跟跨模型的一模一样，事后翻账分不出来。
        v.cross_family = self.cross_family(exe_fam, rev_fam)
        v.validation = validation_label(exe_fam, rev_fam)
        v.file = "、".join(p.name for p in paths)
        v.level = level
        v.emphasis = emphasis
        v.usage = data.get("usage", {})
        return v




#: 结束「新发现」段的行。**「任何非条目行」不能当结束**——条目换行续写的第二行
#: 也是非条目行，那会让这一段之后的新发现全部回落成设计级意见，正是本功能要防的事。
_SECTION_BREAK_RE = re.compile(r"(^#)|(^\*\*)|(^[A-Z_]{3,}\s*[:：])|([:：]\s*$)")


def _ends_followup(raw: str, s: str) -> bool:
    """这一行是不是把「新发现」段结束掉的那种行。

    只认四种：Markdown 标题、整行加粗的小标题、`FINDINGS:` 这类全大写字段、
    以及以冒号收尾的引导句（`本轮必须改：`）。
    """
    if raw[:1].isspace():          # 缩进 = 上一条的续行
        return False
    return bool(_SECTION_BREAK_RE.search(s))


def _is_bullet(s: str) -> bool:
    """这一行是不是一条意见。**`- x` / `• x` / `* x` 是；`**加粗**` 不是。**

    按 `*` 一刀切会把 `**新发现（不作为 FAIL 依据）**` 这种标题当成条目——
    于是标题认不出来、整段新发现回落成普通意见（正是这个功能要防的事），
    还会多出一条 `新发现（不作为 FAIL 依据）**` 的假意见。
    简报本身满篇 `**…**`，模型照着这个语气写标题是常态，不是边角情况。
    """
    return s.startswith(("-", "•")) or bool(re.match(r"\*(?!\*)", s))


def _parse(text: str) -> ReviewVerdict:
    """从回复里抽判词。抽不出判词就是 ERROR，不是默认通过。"""
    m = re.search(r"VERDICT:\s*([A-Z_]+)", text)
    verdict = m.group(1) if m and m.group(1) in VALID_VERDICTS else "ERROR"

    reason = ""
    if r := re.search(r"REASON:\s*(.+)", text):
        reason = r.group(1).strip()
    if verdict == "ERROR" and not reason:
        reason = "回复里没有可识别的判词——**不能当作通过**"

    findings: list[str] = []
    in_followup = False
    for line in text.splitlines():
        s = line.strip()
        bullet = _is_bullet(s)
        # 标题可能写成 `## 新发现（…）`、`**新发现（…）**`、`新发现（…）：`
        core = s.lstrip("#*• ").strip().rstrip("*：: ")
        if _FOLLOWUP_HEADING_RE.match(core) and not bullet:
            in_followup = True
            continue
        if in_followup and s and not bullet and _ends_followup(line, s):
            # 下一个标题**结束**「新发现」段。不结束的话，模型在这一段后面
            # 又补一句「本轮必须改：」再列几条，那几条也会被当成新发现放过去。
            in_followup = False
        if not bullet:
            continue
        item = s.lstrip("-*• ").strip()
        if not item:
            continue      # `---` / `***` 这类分隔线，lstrip 完是空串，不是意见
        # 「新发现」段里漏打标签是常态。**标题之后的都算新发现**——
        # 宽一点没关系，紧了会把新问题算成遗留的设计级意见，凭空多一轮。
        if in_followup and not item.startswith("[新发现]"):
            item = f"[新发现] {item}"
        findings.append(item)
    return ReviewVerdict(verdict=verdict, reason=reason, findings=findings)
