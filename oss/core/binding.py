"""把构念绑到数据的真实列上。

**这是「算 / 判 / 回验」三段切最清楚的一处**（DECISIONS.md 第八章）：

    算    词元重合度打分，几十列砍到 8 个候选   ← 这里
    判    在候选里选哪个、要不要派生            ← skill
    回验  模型说的列数据里有没有                ← 这里

从旧实现抢救。保住的两条：

**① 规则层只负责收窄，不负责否决。**
词元重合为零不等于绑不上：构念常是中文（「数字化程度」），列名常是英文
（broadband）——纯词法匹配在这种情况下召回为零，而这**恰恰是中文社科数据的常态**。
命中太少时补上语义上仍有可能的列，让模型去做跨语言的桥接。
**规则给不出答案的时候，就该把问题交出去。**

**② 模型的输出一律回验。**
它说 `income_gini`，数据里没有，就判 `missing`。**模型只有推荐权，没有认定权。**
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from oss.core.contracts import (
    BindingReport,
    DataProfile,
    DID_FAMILY,
    VariableBinding,
    VariableProfile,
    VariableRole,
)

_SPLIT = re.compile(r"[^0-9a-z一-鿿]+")
#: **社科构念的中英对照表。这是「专为社会科学定制」最划算的一处。**
#:
#: 为什么需要它：中文社科数据的常态是**构念用中文说、列名是英文缩写**
#: （`paeduc` = 父亲受教育年限，`sei` = 社会经济地位指数，`hukou` = 户口）。
#: 纯词法匹配对这种情况召回为零——而这恰恰是最常见的情况，不是边角案例。
#:
#: 每一项是「一组同义说法 → 一组列名片段」。片段用**子串**匹配列名与标签，
#: 因为社科数据的列名几乎全是缩写（`educ` / `edu` / `eduyr` / `yrsch`）。
#:
#: **宁可窄不可宽**：过度匹配会让候选名单重新退化成噪声，
#: 而噪声比空白更糟——它会诱导模型在无关列里选一个。
_LEXICON: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    # ── 教育
    (("教育", "学历", "受教育", "文化程度", "念书", "上学", "schooling",
      "education", "educational attainment", "degree"),
     ("educ", "edu", "school", "degree", "grade", "yrsch", "eduyr", "xueli")),
    # ── 收入与财富
    (("收入", "工资", "薪资", "所得", "挣钱", "income", "earnings", "wage", "salary"),
     ("inc", "income", "earn", "wage", "salar", "pay", "shouru")),
    (("财富", "资产", "storage", "储蓄", "房产", "wealth", "assets", "savings"),
     ("wealth", "asset", "saving", "propert", "house", "fangchan")),
    (("消费", "支出", "花费", "consumption", "expenditure", "spending"),
     ("consum", "expend", "spend", "cost", "xiaofei")),
    # ── 职业与地位
    (("职业地位", "社会经济地位", "职业声望", "社会地位", "阶层", "阶级",
      "occupational status", "socioeconomic status", "prestige", "social class", "ses"),
     ("sei", "isei", "prestg", "prestige", "siops", "egp", "class", "occ", "status",
      "zhiye", "jieceng")),
    (("职业", "工作", "就业", "单位", "occupation", "employment", "job", "work"),
     ("occ", "job", "work", "employ", "labor", "labour", "gongzuo", "danwei")),
    # ── 人口学
    (("性别", "男女", "sex", "gender"), ("sex", "gender", "female", "male", "xingbie")),
    (("年龄", "岁数", "age"), ("age", "nianling")),
    (("出生", "队列", "世代", "出生年", "birth cohort", "cohort", "generation"),
     ("cohort", "birth", "yob", "born", "chusheng")),
    (("民族", "种族", "族裔", "race", "ethnicity", "ethnic"),
     ("race", "ethnic", "minzu", "nation")),
    (("婚姻", "结婚", "配偶", "marital", "marriage", "spouse"),
     ("marit", "marri", "spous", "wed", "hunyin")),
    (("户口", "户籍", "hukou", "household registration"),
     ("hukou", "huji", "registration", "residen")),
    (("城乡", "城镇", "农村", "urban", "rural"), ("urban", "rural", "city", "town", "cheng")),
    (("迁移", "流动", "外出", "migration", "mobility", "migrant"),
     ("migra", "mobil", "move", "flow", "liudong")),
    # ── 家庭
    (("父亲", "爸爸", "father", "paternal"), ("pa", "fa", "father", "dad", "fuqin")),
    (("母亲", "妈妈", "mother", "maternal"), ("ma", "mo", "mother", "mom", "muqin")),
    (("父母", "家长", "parent", "parental"), ("pa", "ma", "parent", "fumu")),
    (("家庭背景", "出身", "家庭出身", "社会出身",
      "family background", "social origin", "origin"),
     ("pa", "ma", "parent", "famil", "origin", "hh", "household", "jiating")),
    (("家庭", "户", "household", "family"), ("famil", "hh", "household", "jiating")),
    (("子女", "孩子", "children", "child"), ("child", "kid", "son", "daughter", "zinv")),
    # ── 态度与主观
    (("主观幸福感", "幸福", "快乐", "生活满意度", "满意度",
      "happiness", "life satisfaction", "wellbeing", "well-being", "subjective wellbeing"),
     ("happ", "satisf", "wellbe", "swb", "life", "xingfu")),
    (("信任", "trust"), ("trust", "confid", "xinren")),
    (("公平感", "公平", "正义感", "fairness", "justice", "equity"),
     ("fair", "justic", "equit", "gongping")),
    (("政治", "党派", "投票", "意识形态", "politic", "party", "vote", "ideology"),
     ("polit", "party", "vote", "ideol", "partyid", "zhengzhi")),
    (("宗教", "信仰", "religion", "religious", "faith"),
     ("relig", "church", "faith", "zongjiao")),
    # ── 健康
    (("健康", "身体状况", "患病", "health", "illness", "morbidity"),
     ("health", "sick", "ill", "disease", "chronic", "bmi", "jiankang")),
    (("心理健康", "抑郁", "焦虑", "mental health", "depression", "anxiety"),
     ("depress", "anxi", "cesd", "phq", "mental", "psych")),
    # ── 社会网络与数字
    (("社会资本", "社会网络", "关系", "social capital", "social network", "guanxi"),
     ("network", "capital", "guanxi", "tie", "contact")),
    (("互联网", "上网", "数字", "网络使用", "internet", "online", "digital", "web"),
     ("internet", "online", "digital", "web", "net", "mobile", "shangwang")),
    # ── 论证性词汇。**构念词典原本是为绑变量做的**，绑变量用不到「不平等」
    #    这类词；但转述核对要用——论文里说「他发现不平等扩大了」，
    #    得能映射到摘要里的 inequality。
    (("不平等", "差距", "分化", "inequality", "disparity", "gap"),
     ("inequal", "disparit", "gap", "divide", "bupingdeng")),
    (("流动性", "代际流动", "社会流动", "mobility", "intergenerational"),
     ("mobil", "intergener", "fluid", "liudong")),
    (("扩张", "扩招", "普及", "expansion", "expand", "universal"),
     ("expans", "expand", "universal", "enrol", "kuozhang")),
    (("分层", "阶层化", "stratification"), ("stratif", "fenceng")),
    (("持续", "稳定", "不变", "persistence", "stability", "constant"),
     ("persist", "stabil", "constant", "unchang")),
    (("下降", "减弱", "缩小", "decline", "decrease", "weaken"),
     ("declin", "decreas", "weaken", "reduc", "narrow")),
    (("回报", "收益", "returns"), ("return", "payoff", "reward")),
    (("成就", "获得", "attainment", "achievement"), ("attain", "achiev")),
    (("选择", "筛选", "selection", "sorting"), ("select", "sort", "screen")),
    (("制度", "政策", "改革", "institution", "policy", "reform"),
     ("institut", "polic", "reform", "zhidu")),
    # ── 结构性列
    (("权重", "抽样权重", "weight", "sampling weight"), ("weight", "wt", "wgt")),
    (("地区", "区域", "省份", "城市", "县", "region", "province", "county", "area"),
     ("region", "prov", "city", "count", "area", "dist", "diqu")),
    (("年份", "调查年份", "时间", "期", "year", "wave", "survey year", "period"),
     ("year", "wave", "time", "period", "round", "nian")),
)


#: 「本人」与「亲代」的区分。**这在地位获得研究里是要害。**
#:
#: 把父代和子代的地位搞混——比如拿 `pasei10` 当「本人职业地位」——
#: 会变成用父亲的地位回归父亲的地位，得到一个接近 1 的系数和一篇废论文。
#: 而这种错**不会报任何错**：两列都存在、都是数字、量纲还一样。
_SELF_MARKERS = ("本人", "自己", "受访者", "respondent", "own", "r's", "个人")
_KIN_MARKERS = ("父", "母", "亲代", "家长", "father", "mother", "parent", "paternal",
                "maternal")
_KIN_PREFIXES = ("pa", "ma", "fa", "mo")


def _kin_penalty(construct: str, column: str) -> float:
    """构念明说是「本人」时，把亲代变量压下去。

    只在构念**自称本人、且不提亲代**时才生效——
    「家庭背景（父教育年限）」两个标记都有，不该被压。
    """
    text = (construct or "").lower()
    if not any(m in text for m in _SELF_MARKERS):
        return 1.0
    if any(m in text for m in _KIN_MARKERS):
        return 1.0
    low = column.lower()
    return 0.45 if any(low.startswith(px) for px in _KIN_PREFIXES) else 1.0


def lexicon_fragments(construct: str) -> set[str]:
    """构念 → 可能的列名片段。中文按子串、英文按子串（列名几乎全是缩写）。"""
    text = (construct or "").lower()
    out: set[str] = set()
    for terms, frags in _LEXICON:
        if any(t in text for t in terms):
            out |= set(frags)
    return out

#: 核心角色。**只有这两个绑不上才是致命的**——
#: 控制变量缺了可以降级，处理和结果缺了这个 idea 就做不了。
CORE_ROLES: tuple[str, ...] = ("treatment", "outcome")


def tokens(text: str) -> set[str]:
    return {t for t in _SPLIT.split((text or "").lower()) if len(t) > 1}


# ------------------------------------------------------------------ 算


@dataclass(frozen=True)
class Candidate:
    """一个候选列，**连同它凭什么被选中**。

    「兜底」不是理由。使用者要能判断这个候选值不值得看。
    """

    column: str
    score: float
    why: str


@dataclass(frozen=True)
class Shortlist:
    """规则给出的候选，**或者如实承认给不出**。

    `rule_failed` 为真时 `matches` 是空的——这是有意的：
    **一份跟构念无关的名单会诱导模型在里面选一个**，
    而它本该看到「规则帮不上忙，你自己看全表」。噪声比空白更糟。
    """

    construct: str
    matches: list[Candidate]
    rule_failed: bool
    note: str


def shortlist(
    construct: str,
    profile: DataProfile,
    *,
    operationalizations: list[str] | None = None,
    top_k: int = 8,
) -> Shortlist:
    """规则打分选候选。免费、确定性、每次结果一样。

    存在的理由：把几十上百列砍到 8 个再交给模型。
    否则把整张表塞进 prompt，既贵又容易让模型在无关列上乱选。

    **两条证据来源**：

    ① 词元重合——构念与列名/标签共享词元。对英文数据有效。
    ② 社科构念词典——`_LEXICON`。**对中文构念 × 英文缩写列名，这是唯一有效的一条**，
       而那正是中文社科数据的常态。

    两条都落空时**不兜底**。原先的兜底按「有没有标签、缺失率高低」排序，
    跟要绑的是哪个构念完全无关——GSS 真跑时五个构念拿到了同一份名单。
    （PLAN.md 九之一 ⑥）
    """
    needles = tokens(construct)
    frags = lexicon_fragments(construct)
    for op in operationalizations or []:
        needles |= tokens(op)
        frags |= lexicon_fragments(op)

    scored: list[Candidate] = []
    for var in profile.variables:
        if var.semantic_type in ("identifier", "text", "weight"):
            continue
        hay_tokens = tokens(var.name) | tokens(var.label)
        hay_text = f"{var.name} {var.label}".lower()

        overlap = len(needles & hay_tokens)
        hit_frags = sorted(f for f in frags if f in hay_text)

        if not overlap and not hit_frags:
            continue

        score = overlap / max(len(needles | hay_tokens), 1) if overlap else 0.0
        why_bits = []
        if overlap:
            why_bits.append(f"词元重合（{'、'.join(sorted(needles & hay_tokens))}）")
        if hit_frags:
            # 片段越长、命中越多，越可信；最长片段决定基础分
            score += 0.25 + 0.05 * min(len(hit_frags), 3) + 0.02 * max(map(len, hit_frags))
            why_bits.append(f"构念词典（{'、'.join(hit_frags[:3])}）")
        if var.label:
            score *= 1.2
        if var.missing_rate > 0.5:
            score *= 0.4
            why_bits.append(f"但缺失 {var.missing_rate:.0%}")
        kin = _kin_penalty(construct, var.name)
        if kin < 1.0:
            score *= kin
            why_bits.append("构念说的是本人，这列看着像亲代")
        scored.append(Candidate(var.name, round(score, 4), "；".join(why_bits)))

    scored.sort(key=lambda c: -c.score)

    if not scored:
        return Shortlist(
            construct, [], True,
            "规则给不出候选：构念与列名既无词元重合，也不在社科构念词典里。"
            "**这不等于绑不上**——请自己看全表的列名与标签来判断。"
            "（原先这里会按缺失率补几列充数，那是噪声，比空白更糟）",
        )
    return Shortlist(construct, scored[:top_k], False, "")


# ---------------------------------------------------------------- 回验


def verify(
    construct: str,
    role: VariableRole,
    column: str | None,
    profile: DataProfile,
    *,
    derivation: str = "",
    confidence: float = 1.0,
) -> VariableBinding:
    """回验模型选的列。**模型只有推荐权，没有认定权。**

    它说 `income_gini`，数据里没有，就判 `missing`——不是警告，是判死。
    """
    if derivation:
        return _verify_derived(construct, role, derivation, profile)

    if not column:
        return VariableBinding(
            construct_name=construct, role=role, status="missing",
            note="模型没有给出对应的列",
        )

    var = profile.var(column)
    if var is None:
        near = _nearest(column, profile)
        return VariableBinding(
            construct_name=construct, role=role, column=None, status="missing",
            note=f"数据里没有「{column}」这一列" + (f"。最接近的是 {near}" if near else ""),
        )

    notes = []
    if var.missing_rate > 0.5:
        notes.append(f"缺失 {var.missing_rate:.0%}，超过一半")
    if var.within_variation == 0.0:
        notes.append("组内变异为 0——固定效应会把它整个吃掉，回归里它是常数")
    if var.n_unique <= 1:
        notes.append("取值恒定，进不了回归")

    return VariableBinding(
        construct_name=construct, role=role, column=column,
        status="bound" if confidence >= 0.6 else "proxy",
        coverage=var.coverage,
        note="；".join(notes) or ("按名称与标签匹配" if confidence >= 0.6 else "代理变量，非直接测量"),
    )


def measurement_gap_targets(measurement) -> frozenset[str]:
    """测量类研究里**绑得再准也要填代理差距三栏**的那几个名字（测量骨架设计稿 5.3）。

    代理差距三栏（`construct_def` / `measures` / `gap`）原先只在绑定判成 `proxy` / `derived`
    时才要求填——**绑得准就不用交代**。而 `oss bind` 走下来 `confidence` 缺省是 1.0，
    于是这三栏实际上只对派生变量生效。

    测量研究不能这么放行：「理论要什么 / 实际测什么 / 差距」**正是这类研究的核心交代**，
    绑得再准也省不掉。还有一层——`measurement.construct` 是一句纯自由文本，写得对不对
    程序永远核不了（Task 1 自查），**三栏是它唯一的抓手**。

    返回构念名与指标列名的集合：绑定那一侧拿 `construct_name` 与 `column` 两样去比，
    对上任一样都算。构念名是自由文本、一字不差的时候不多，**指标列名才是硬的那一半**。

    判据挂在**测量块本身**，不挂在 `research_type`——跟造尺子那三道门同一个口径
    （`analysis._measurement_plan_problems`）：这一块是谁写的谁就得对得上，
    主类型写成因果、`also` 里放一个 measurement，同样躲不掉。

    `measurement` 收 `MeasurementSpec`，也收 plan.json 里的那个字典（合规规则那一侧
    读的是生 JSON）；`None` 与形状不对的一律给空集——**这道检查不该让坏文件崩掉**。
    """
    if measurement is None:
        return frozenset()
    if isinstance(measurement, dict):
        construct, indicators = measurement.get("construct"), measurement.get("indicators")
    else:
        construct, indicators = measurement.construct, measurement.indicators
    names = [construct, *(indicators if isinstance(indicators, list) else [])]
    return frozenset(n.strip() for n in names if isinstance(n, str) and n.strip())


def _verify_derived(
    construct: str, role: VariableRole, derivation: str, profile: DataProfile
) -> VariableBinding:
    """派生变量：构造式里每一列都要真的存在。

    `urban_inc / rural_inc` 这种，两列缺一个就构造不出来——
    而这类错误在回归跑起来之前不会暴露。
    """
    used = [t for t in _SPLIT.split(derivation) if t]
    known = {v.name for v in profile.variables}
    missing = [c for c in used if c not in known and not c.replace(".", "").isdigit()]
    if missing:
        return VariableBinding(
            construct_name=construct, role=role, status="missing", derivation=derivation,
            note=f"构造式用到了数据里没有的列：{'、'.join(missing)}",
        )
    cols = [c for c in used if c in known]
    coverage = min((v.coverage for c in cols if (v := profile.var(c))), default=0.0)
    return VariableBinding(
        construct_name=construct, role=role, status="derived", derivation=derivation,
        coverage=coverage, note=f"由 {'、'.join(cols)} 构造",
    )


def _nearest(column: str, profile: DataProfile) -> str | None:
    """列名写错时给个最接近的，省得用户自己翻。"""
    t = tokens(column)
    best, score = None, 0.0
    for v in profile.variables:
        s = len(t & tokens(v.name)) / max(len(t | tokens(v.name)), 1)
        if s > score:
            best, score = v.name, s
    return best if score > 0.3 else None


# ------------------------------------------------------------------ 汇总


def build_report(
    idea_id: str, bindings: list[VariableBinding], profile: DataProfile, *, strategy: str = ""
) -> BindingReport:
    """汇成报告。`core_ok` 只看处理与结果这两个核心角色。"""
    core = [b for b in bindings if b.role in CORE_ROLES]
    core_ok = bool(core) and all(b.is_usable for b in core)

    blocking: list[str] = []
    for b in core:
        if not b.is_usable:
            blocking.append(f"核心变量「{b.construct_name}」（{b.role}）绑不上：{b.note}")
    if core and not blocking and strategy not in DID_FAMILY:
        # 绑上了不等于能用——组内变异为 0 的处理变量，回归里是常数。
        # did 族例外：组间处理正是这类设计的本来形状，识别靠处理×时期交互，
        # 不靠处理变量自身的组内变异（设计稿 2026-08-31-sim-intake 第五节）。
        for b in core:
            if b.column and (v := profile.var(b.column)) and v.within_variation == 0.0:
                blocking.append(
                    f"核心变量「{b.construct_name}」绑到了 {b.column}，"
                    f"但它的组内变异为 0——固定效应会把它吃掉"
                )
    if not core:
        blocking.append("没有任何核心变量（treatment / outcome）——这个 idea 还没成形")

    return BindingReport(
        idea_id=idea_id,
        bindings=bindings,
        core_ok=core_ok and not blocking,
        blocking=blocking,
    )


#: 数据文件自带的变量标签当不当得了图表上那一行的名字。
#:
#: **能当的要用**——`profile.json` 里 CGSS2017 是 786/786 全有标签，而没人去拿，
#: 于是系数图纵轴印的是 `agrihukou`、`age2`、`c_mgr*yearc`，一眼就是机器产物。
#:
#: **当不了的宁可不用**：调查数据的变量标签过半是整句问题（「[年]  您的出生日期是什么？」
#: 「您的民族是：」）。把它截断塞进纵轴，读者看到的是半句话；留着列名，读者至少知道
#: 那是个代号、该去查码本。**半句话比代号更误导**，跟别处「宁可拒绝不可放行」同一条理。
#: 按**显示宽度**卡，不按字符数：汉字占两格，一条规则同时管中英文标签。
#: 24 格 ≈ 12 个汉字 ≈ 24 个英文字母，跟稳健性图上那些人写的标签（「控制户口趋势」
#: 「Excluding the 2006 round」）同一个量级。
_LABEL_WIDTH_MAX = 24
_LABEL_REJECT = ("？", "?", "：", ":", "[", "（请", "(请")


def _display_width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def usable_variable_label(label: str) -> str:
    """数据文件的变量标签，能当图表行名就返回它，当不了返回空串。"""
    s = (label or "").strip()
    if not s or _display_width(s) > _LABEL_WIDTH_MAX or any(p in s for p in _LABEL_REJECT):
        return ""
    return s


def display_labels(report: BindingReport | None, plan_labels: dict[str, str] | None = None,
                   profile_labels: dict[str, str] | None = None) -> dict[str, str]:
    """列名 → 表和图里印的名字。优先级：`plan.labels` > binding 构念名 > 数据文件的变量标签。

    三个来源分工不同，**不是一件事写三遍**：binding 的 `construct_name` 是**理论构念**
    （方法节与代理差距三栏用它，「过劳（周工时≥48 小时）」这种带口径的长名在那里是对的）；
    `plan.labels` 是**表里那一行的短名**，另外还管 binding 覆盖不到的两类——
    哑变量展开（绑的是 class4，进回归的是 cls_mgr / cls_clerk / cls_service）与
    构造项（age2 只在 age 那条的 derivation 里被提到，没有自己的行）。

    第三个是兜底，也是唯一**不要模型动手**的那个：前两个都靠模型填，填漏了图上就是列名，
    而 `.dta` / `.sav` 自带的标签一直躺在 `profile.json` 里没人用。所以它排最低——
    模型写的名字是冲着读者去的，文件里的标签只是恰好可读（还要过 `usable_variable_label`）。

    交互项不在这里拼，那是显示逻辑（`manuscript._term_label`）。
    """
    out: dict[str, str] = {}
    for col, label in (profile_labels or {}).items():
        if col and (usable := usable_variable_label(label)):
            out[col] = usable
    for b in (report.bindings if report is not None else []):
        if b.column and b.construct_name:
            out[b.column] = b.construct_name
    for col, name in (plan_labels or {}).items():
        if col and name:
            out[col] = name
    return out
