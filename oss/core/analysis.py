"""计量执行。OLS + 固定效应 + 聚类稳健标准误。

**这一段的失败全是静默的**，所以每一处都要主动拦：

**① 列名写错不报错。**
计划里写 `incom`，回归照样跑（因为 pandas 会先抛 KeyError……如果你不小心
用了 `df.get()` 就不会），或者写对了列名但用错了变量——**只是量错了东西**。
所以 `validate_plan` 在跑之前逐个核，核不过就拒绝执行。

**② 标准误不聚类会系统性偏小。**
同一个县的人不独立。不聚类的 p 值虚假地显著，而这不会有任何报错。

**③ 组内变异为 0 的变量会被固定效应吃掉。**
列在那儿，系数是 NaN 或者被丢掉——如果直接丢掉且不说，
使用者会以为它进了回归。

**④ 数值算法自己抄容易错。**
上一阶段的教训：手抄的正态分位数逼近让功效被高估三倍，而且不报错。
所以这里的每个公式都配一个能手算复核的测试。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import combinations

import numpy as np
import pandas as pd

from oss.core.contracts import (
    AnalysisPlan,
    AnalysisResults,
    BinnedSlope,
    Descriptive,
    DID_FAMILY,
    EventStudyPoint,
    DataProfile,
    Estimate,
    GroupCell,
    OlsFit,
    PretrendCov,
    RobustnessRun,
    RobustnessSpec,
    SampleStep,
    SpecSummary,
    SurveyDesign,
)
from oss.core.findings import Finding
from oss.core import sim_checks
from oss.core import simbox
from oss.core.power import first_two

SUPPORTED_ESTIMATORS = ("ols_fe", "did", "did_cs", "event_study", "iv", "iptw", "rdd", "describe",
                        "logit", "ologit", "mixed")

#: 交互项的写法：`a*b`、`a*b*c`
INTERACTION_SEP = "*"


def parts_of(spec: str) -> list[str]:
    """把 `a*b*c` 拆成分量。"""
    return [t.strip() for t in spec.split(INTERACTION_SEP) if t.strip()]


def is_interaction(spec: str) -> bool:
    return INTERACTION_SEP in spec


def _expand_interactions(df: pd.DataFrame, specs: list[str]) -> pd.DataFrame:
    """把交互项现场乘出来，列名就是那个式子本身。

    **不要让使用者在数据里预先乘好。**预先乘好的话，
    `paeduc * eduq`（打错一个字母）会静静地产出一列 NaN，
    或者更糟——乘到另一个存在但不相干的列上。没有任何校验能发现。
    进了契约之后，`validate_plan` 就能逐个分量核。
    """
    out = df
    for spec in specs:
        if spec in out.columns:
            continue
        cols = parts_of(spec)
        missing = [c for c in cols if c not in out.columns]
        if missing:
            raise PlanRejected([f"交互项「{spec}」的分量不在数据里：{'、'.join(missing)}"])
        prod = out[cols[0]].astype(float)
        for c in cols[1:]:
            prod = prod * out[c].astype(float)
        out = out.assign(**{spec: prod})
    return out


class PlanRejected(RuntimeError):
    """计划过不了校验。**这是拒绝执行，不是警告。**

    列名写错了还硬跑，跑出来的数字看着完全正常——
    这正是「静默失败」里最贵的一种。
    """

    def __init__(self, problems: list[str]) -> None:
        super().__init__("分析计划过不了校验：\n  · " + "\n  · ".join(problems))
        self.problems = problems


class WeightsVaryWithinUnit(PlanRejected):
    """did_cs 的权重在单位内会变（StatsPAI 只吃单位层面的权重）。

    主规格是 did_cs 时照样是拒绝；伴随主规格的分批事件研究（`_attach_event_study`）靠这个类认出这一种，
    不加权照跑 Callaway–Sant'Anna，不退回 TWFE。
    """


def _why(exc: Exception) -> str:
    """一次失败的理由，给诊断用：拒绝就只写问题本身，不贴「分析计划过不了校验」那个表头与异常类名。"""
    if isinstance(exc, PlanRejected):
        return "；".join(exc.problems)
    return f"{type(exc).__name__}: {exc}"


# ------------------------------------------------------------------ 校验


def _causal_plan_problems(plan: AnalysisPlan) -> list[str]:
    """声明为因果的计划，先写清问的是什么（因果骨架设计稿 3.1–3.3）。三道都是硬门。"""
    out: list[str] = []
    if plan.estimand is None:
        out.append(Finding("声明为因果的计划要写估计量三步（plan.estimand）：单位层面的量、目标总体、识别假设、"
                   "经验估计量怎么对上——先说问的是什么，再挑方法（因果骨架设计稿 3.1）", "plan.causal.estimand"))
    elif miss := plan.estimand.missing():
        out.append(Finding(f"估计量三步没写全：缺{'、'.join(miss)}（plan.estimand）", "plan.causal.estimand_incomplete"))
    unlabeled = [c for c in plan.controls if c not in plan.control_roles]
    if unlabeled:
        out.append(Finding(f"控制变量没标角色：{'、'.join(unlabeled)}——每个都标 confounder / precision / other"
                   f"（plan.control_roles，因果骨架设计稿 3.3）", "plan.causal.control_roles"))
    bad = [f"{c}（{r}）" for c, r in plan.control_roles.items() if r in ("mediator", "collider")]
    if bad:
        out.append(Finding(f"这些控制变量标的是中介或碰撞：{'、'.join(bad)}——中介与碰撞变量不该控制，"
                   f"控了会把估计推离真实的因果效应（Rohrer 2018）。从 controls 里拿掉，或改标角色并说明理由", "plan.causal.mediator_control"))
    if plan.sample_filter and plan.outcome and re.search(
            rf"(?<!\w){re.escape(plan.outcome)}(?!\w)", plan.sample_filter):
        out.append(Finding(f"样本限制 `{plan.sample_filter}` 用到了结果变量「{plan.outcome}」——按结果筛样本会引入选择偏误"
                   f"（资格标准不能用基线之后发生的事筛人，Hernán 与 Robins 2016）。改用处理前就确定的变量筛", "plan.causal.outcome_filter"))
    return out


#: 调查信号那条拒绝里给的例子，跟说明书 `stages/5-plan.md` 里的是同一行
_SURVEY_EXAMPLE = ('"survey": {"strata": "", "psu": "county", "weight_use": "descriptive", '
                   '"design_source": "用户说：区县是初级抽样单位"}')


def _survey_plan_problems(plan: AnalysisPlan, profile: DataProfile) -> tuple[list[str], list[str]]:
    """调查层的门（设计稿第六节）。返回 `(拒绝理由, 警告)`。

    ① 画像有调查信号、计划没声明设计也没写理由：声明了研究类型的计划拒绝，老计划只警告。
       调查层之前的老画像没有 `survey` 这一栏（如 `runs/gss20260824_v2`），它的 `weight_column` 就算信号
    ② 写了不按调查处理的理由，又写了抽样设计：拒绝，二选一
    ③ 计划写的权重列、抽样设计写的分层 / 抽样单位列不在数据里，或整列缺失：拒绝
    """
    from oss.core.contracts import SurveySignals

    problems: list[str] = []
    warnings: list[str] = []
    sig = profile.survey
    old_profile = "survey" not in profile.model_fields_set and bool(profile.weight_column)
    if old_profile:
        sig = SurveySignals(likely=True, weight_candidates=[profile.weight_column])
    if sig.likely and not (plan.survey is not None and plan.survey.declared()):
        bits = [("权重候选", sig.weight_candidates), ("分层候选", sig.strata_candidates),
                ("初级抽样单位候选", sig.psu_candidates), ("插补标记", list(sig.imputation_flags))]
        listed = "；".join(f"{k} {'、'.join(v)}" for k, v in bits if v)
        msg = (f"画像有调查信号（{listed}），计划里没声明抽样设计：plan.survey 写设计来源（design_source）"
               f"与权重用法（weight_use，只能是 descriptive 或 model：descriptive 描述总体——均值、比例、分布；"
               f"model 行为或因果模型——加权与不加权两版都报），有分层 / 初级抽样单位就写上，例："
               f"`{_SURVEY_EXAMPLE}`；不按调查处理就只写 not_survey_reason（调查层设计稿第六节）"
               + ("。这是调查层之前的老画像（没有 survey 这一栏），信号按它的 weight_column 算；"
                  "重跑 oss profile 能认全分层与抽样单位" if old_profile else ""))
        if plan.research_type:
            problems.append(Finding(msg, "plan.survey.design_missing"))
        else:
            warnings.append(Finding("老计划没写研究类型，这次只警告：" + msg, "plan.survey.design_missing_legacy"))
    if plan.survey is not None and plan.survey.not_survey_reason.strip():
        filled = [f for f in ("strata", "psu", "weight_use", "design_source")
                  if str(getattr(plan.survey, f) or "").strip()]
        if filled:
            problems.append(Finding(f"写了不按调查处理的理由，就不要再写抽样设计——二选一"
                            f"（not_survey_reason 之外还写了 {'、'.join(filled)}）", "plan.survey.reason_and_design"))
    known = {v.name for v in profile.variables}
    empty = set(profile.all_missing_columns)
    cols = [("权重", plan.weights, "计划")]
    if plan.survey is not None:
        cols += [("分层", plan.survey.strata, "抽样设计"), ("初级抽样单位", plan.survey.psu, "抽样设计")]
    for label, col, who in cols:
        if col and (col not in known or col in empty):
            where = "不在数据里" if col not in known else "整列缺失"
            problems.append(Finding(f"{label}列「{col}」{where}——{who}写的列要真的在（调查层设计稿第六节）", "plan.survey.column_missing"))
    if plan.survey is not None and plan.survey.psu and plan.cluster_level \
            and plan.cluster_level != plan.survey.psu:
        warnings.append(Finding(f"cluster_level（{plan.cluster_level}）跟初级抽样单位（{plan.survey.psu}）不同：主规格的标准误"
                        f"按抽样设计算，cluster_level 不用；要看按 {plan.cluster_level} 聚类的结果，锁一项稳健性", "plan.survey.cluster_vs_psu"))
    if plan.survey is not None and plan.survey.strata and not plan.survey.psu:
        warnings.append(Finding(f"只写了分层没写初级抽样单位：分层（{plan.survey.strata}）不会进方差（按设计的标准误要有抽样单位）"
                        f"——码本里有初级抽样单位就写上 psu", "plan.survey.strata_without_psu"))
    return problems, warnings


def _describe_extras(plan: AnalysisPlan) -> list[str]:
    """`describe` 计划写了、却用不上的回归项：处理、控制、固定效应、交互、借力变量、驱动变量、分箱。

    `run_describe` 一个都不读——不拦的话它们被静默忽略：表注照抄「固定效应：prov」，稳健性加了控制
    原样照抄主结果，论文读起来就是「稳健性过了」。门（`_descriptive_plan_problems`）与 `run_describe`
    开头都查，后者让稳健性补丁加了控制的那一项记为出错。换结局、换样本筛选、换权重照旧合法。
    """
    return [f for f, v in (("treatment", plan.treatment), ("controls", plan.controls),
                           ("fixed_effects", plan.fixed_effects), ("interactions", plan.interactions),
                           ("instruments", plan.instruments), ("running", plan.running),
                           ("binned", plan.binned)) if v]


def _describe_extras_problem(extras: list[str]) -> str:
    return (f"describe 只做不带控制的分组描述，计划却写了 {'、'.join(extras)}——控制了别的变量走 ols_fe"
            f"（kind: association）；要比较的组写进 description.groups")


def _descriptive_plan_problems(plan: AnalysisPlan, profile: DataProfile) -> list[str]:
    """描述骨架的两道门（设计稿第六节），外加描述块写得对不对。

    ① 声明为描述的计划要写描述量与目标总体（`estimand` 的两栏；Lundberg 等 2021：描述性研究同样要写估计量）
    ② 问的是个体变化、数据却不是面板（截面、时间序列、重复截面）：拒（Firebaugh：个体变化用面板、社会变化用重复截面）；
       画像没判出结构（空或 unknown）不拒
    `description` 块只在估计量是 `describe` 时必填——走 `ols_fe` 的描述计划（条件关联、从因果降级来的）不卡。
    `describe` 带了处理、控制、固定效应之类（`_describe_extras`）不论研究类型都拒。
    """
    out: list[str] = []
    if plan.estimator == "describe" and plan.research_type == "causal":
        out.append(Finding("describe 只做分组描述，不下因果结论——因果计划换 ols_fe / did / iv 等，或把研究类型改成 descriptive", "plan.descriptive.describe_causal"))
    if plan.estimator == "describe" and (extras := _describe_extras(plan)):
        out.append(Finding(_describe_extras_problem(extras), "plan.descriptive.describe_extras"))
    if plan.research_type != "descriptive":
        return out
    e = plan.estimand
    miss = [label for label, v in (("描述量（estimand.unit_quantity）", e.unit_quantity if e else ""),
                                   ("目标总体（estimand.target_population）", e.target_population if e else ""))
            if not str(v).strip()]
    if miss:
        out.append(Finding(f"描述类计划要先写清描述的是什么：缺{'、'.join(miss)}——描述哪个量、对哪个总体（描述骨架设计稿第三节）", "plan.descriptive.estimand"))
    d = plan.description
    if plan.estimator == "describe":
        if d is None or not d.kind:
            out.append(Finding("估计量是 describe，要写 description.kind：level / distribution / gap / trend"
                       "（控制了别的变量以后的关系是 association，走 ols_fe）", "plan.descriptive.kind_missing"))
        elif d.kind == "association":
            out.append(Finding("description.kind 是 association（控制了别的变量以后的关系）——走 ols_fe，不走 describe", "plan.descriptive.kind_association"))
        else:
            if d.kind == "gap" and not d.groups:
                out.append(Finding("组间差（kind: gap）要写 description.groups：比较哪几组", "plan.descriptive.gap_groups"))
            if d.kind == "trend" and not d.over:
                out.append(Finding("趋势（kind: trend）要写 description.over：看哪段时间", "plan.descriptive.trend_over"))
            if len(d.groups) > 2:
                out.append(Finding("description.groups 至多两列——再多，分组表就读不下去了，先合并", "plan.descriptive.groups_too_many"))
    if d is not None:
        known = {v.name for v in profile.variables}
        for col in [*d.groups, *([d.over] if d.over else [])]:
            if col not in known:
                out.append(Finding(f"描述块写的列「{col}」不在数据里", "plan.descriptive.column_missing"))
        # 个体变化只认面板：画像判出了结构、又不是面板就拒（截面、时间序列、重复截面都看不到同一个人的变化）；
        # 没判出结构（空或 unknown）不拒——拿不准的不替使用者下结论
        kind = profile.structure.kind
        if d.change == "individual" and kind and kind not in ("panel_balanced", "panel_unbalanced", "unknown"):
            what = {"repeated_cross_section": "重复截面——每期是另一批人",
                    "cross_section": "截面——只有一个时点",
                    "time_series": "时间序列——每期只有一个总体数、没有可追的个体"}.get(kind, f"「{kind}」结构（不是面板）")
            alt = ("改问这一时点的水平与组间差（kind: level / gap" if kind == "cross_section"
                   else "把问题改成总体随时间的变化（change: social")
            out.append(Finding(f"问的是个体变化（description.change: individual），数据却是{what}，看不到任何一个人的变化。"
                       f"换面板数据，或{alt}；Firebaugh：个体变化用面板、社会变化用重复截面）", "plan.descriptive.individual_change"))
    return out


#: 「造尺子才会有」的两类产出：因子结构 / 载荷矩阵（structural）、信度系数（reliability）。
#: 拿现成量表做实质研究的人不会去做这两样——他要么引量表原作者的，要么根本不报。
#: **效标关联与区分不在其中**：用现成量表的论文报「本样本里它跟捐款行为的相关」是常事，
#: 进了表就成了误拦（设计稿第十二节：五道门太紧会把正当的测量研究拦住）。
_INSTRUMENT_ONLY_OUTPUTS = ("structural", "reliability")


#: 「结局这一列就是这套题的量表分」的判据：跟这几道题的均分相关到这个程度。
#: 量表分是这几道题**算出来的**——均分与总分是 1.00、标准化的因子得分 0.98 上下；
#: 别的变量再沾边也到不了这里（陷阱集那份数据里，效标「捐款金额」跟题目均分只有 0.58）。
_SCALE_SCORE_CORR = 0.95
#: 算相关至少要这么多完整个案——比这还少，相关系数本身就不稳，判不出来就不报。
_SCALE_SCORE_MIN_N = 30


def _is_scale_score(frame, column: str, indicators: list[str]) -> float | None:
    """`column` 是不是这几道题算出来的量表分——是就给相关系数，**判不出来给 None**。

    量表分（均分、总分、因子得分）跟题目均分的相关接近 1，这是一条**从数据算出来的事实**，
    不认列名、不认标签：叫 `scale_all` 还是 `v99` 是同一件事。
    """
    if frame is None or column not in frame.columns:
        return None
    cols = [c for c in dict.fromkeys(indicators) if c in frame.columns and c != column]
    if len(cols) < 2:
        return None
    sub = frame[[column, *cols]].apply(pd.to_numeric, errors="coerce").dropna()
    if len(sub) < _SCALE_SCORE_MIN_N:
        return None
    mean = sub[cols].mean(axis=1)
    if not mean.std() or not sub[column].std():
        return None
    r = abs(float(sub[column].corr(mean)))
    return r if r >= _SCALE_SCORE_CORR else None


def _outcome_on_measurement_block(plan: AnalysisPlan, spec, frame, binding) -> str:
    """结局落在这个测量块上没有——落了就返回**凭什么这么说**，没落或判不出来给空串。

    三条路，任一条走得通就算，都不读一个字的措辞：

    - 结局就是 `indicators` 里的一道题——名单上写着，最硬的一条
    - `binding.json` 把结局那一列绑给了这个构念——处方那一步手上只有这一条
    - 结局是这几道题算出来的量表分（`_is_scale_score`）——只有拿得到帧的时候算得出

    **判不出来就不报。**没有帧、没有绑定、结局又不在 `indicators` 里时，
    「它是不是这套题的量表分」无从判起——这时宁可漏掉，也不拿一条猜的判据去拦。
    """
    col = plan.outcome.strip()
    if not col:
        return ""
    if col in spec.indicators:
        return f"结果变量「{col}」就是 indicators 里的一道题"
    construct = spec.construct.strip()
    if binding is not None and construct and any(
            b.column == col and b.construct_name.strip() == construct for b in binding.bindings):
        return f"binding.json 把结果变量「{col}」绑给了构念「{construct}」"
    if (r := _is_scale_score(frame, col, spec.indicators)) is not None:
        return f"结果变量「{col}」跟这几道题的均分相关 {r:.2f}——它就是这套题的量表分"
    return ""


def measurement_source_signals(plan: AnalysisPlan, *, profile: DataProfile | None = None,
                               frame=None) -> list[str]:
    """`source_quality` 留空时，**别处**有没有「指标是自报的」的迹象。

    这一栏原先的激励方向是反的：填 `self_report` 会多挨一道门（M5 查自报的量
    有没有按代理处理），**留空什么都不多**——诚实的人受罚，空着的人免检。
    这跟 `groups_compared` 那个洞完全同形，修法也照那一处：
    **判据从「这一栏怎么写」改成从别处对账**。

    自报有一个算得出来的迹象：**这份数据是不是问卷调查**。画像的 `survey.likely`
    由权重、分层、初级抽样单位这些列判出来，跟作者怎么声明无关。
    问卷里的题目按定义就是受访者自己答的——行政记录与后台日志不会长成
    「一份带抽样权重的横截面 + 一堆五级量表题」。

    **只在这一栏留空时看**：填了 `administrative` / `logged` 的照信，
    那是作者知道自己的数据从哪来，这里不替他推翻。
    """
    spec = plan.measurement
    if spec is None or (spec.source_quality or "").strip():
        return []
    hits: list[str] = []
    if profile is not None and getattr(profile.survey, "likely", False):
        why = []
        if profile.survey.weight_candidates:
            why.append(f"权重列 {profile.survey.weight_candidates[0]}")
        if profile.survey.psu_candidates:
            why.append(f"抽样单位列 {profile.survey.psu_candidates[0]}")
        extra = f"（{'、'.join(why)}）" if why else ""
        hits.append("画像判这份数据是问卷调查" + extra)
    # 指标是不是量表题：取值少、整数、范围小——问卷里的李克特题就是这个形状
    if frame is not None:
        scale = [c for c in spec.indicators if c in frame.columns and _looks_like_scale(frame[c])]
        if len(scale) >= 2:
            hits.append(f"指标里有 {len(scale)} 列长得像量表题（{'、'.join(scale[:3])}）")
    return hits


def _looks_like_scale(s) -> bool:
    """像不像一道李克特量表题：取值不超过十档、都是整数、跨度不超过十。"""
    import pandas as pd

    v = pd.to_numeric(s, errors="coerce").dropna()
    if len(v) < 30:
        return False
    u = v.unique()
    if not (2 <= len(u) <= 10):
        return False
    return bool((v == v.round()).all() and (v.max() - v.min()) <= 10)


def measurement_group_signals(plan: AnalysisPlan, results=None, *,
                              profile: DataProfile | None = None, frame=None, binding=None) -> list[str]:
    """**别处**有没有在跨组比较的迹象——返回看到的那几处，没有就是空表。

    实施计划「零之二」第 ③ 条那个洞：跨组比较只要不声明 `measurement.groups_compared`，
    M4 整道门绕过，而论文照样跨组比较，**代价为零**。修法跟前两批一样——
    **判据从「计划说什么」改成从别处对账**：

    - 描述块的分组（`description.groups`）——描述量本来就按组分开报
    - 计划里的交互项——一个构念的关系按另一个变量分开看，就是在比组
    - 结果里真排出来的分组表（`results.groups`）——`describe` 跑完才有，所以只有处方那一侧看得到
    - **处理变量本身就是分组变量**——结局是这个测量块的量表、处理变量是一个分类或二值的列，
      回归算的就是跨组比较（第四个绕行洞，2026-09-17 补）

    **边界**：交互项的调节变量是连续的（例如年龄）时，这里也会算作一处迹象，那不是跨组比较。
    留着它是因为**漏掉交互项这一路，绕行的代价就又回到零**；而误判的代价只是一条警告加一条
    驳回得掉的处方——`--dismiss` 写一句「这几个交互项的调节变量是连续的」即可。
    **不按列名认**：叫 `urban` 还是 `v12` 是同一件事，认名字在没见过的写法上是 6%。

    第四处窄在两头，**宁可漏、不可误报**：结局要落在这个测量块上
    （`_outcome_on_measurement_block`，判不出来就不报），处理变量要由**画像**判成
    `binary` 或 `categorical`。漏掉的是——存成 1/2/3/4 的地区码（画像判它 ordinal）、
    没给画像或没给帧也没给绑定的那几处调用。
    """
    spec = plan.measurement
    if spec is None or [g for g in spec.groups_compared if g.strip()]:
        return []
    out: list[str] = []
    if plan.description is not None and plan.description.groups:
        out.append(f"描述块的分组（description.groups）：{'、'.join(plan.description.groups)}")
    if plan.interactions:
        out.append(f"计划里的交互项：{'、'.join(plan.interactions)}")
    if results is not None and results.groups:
        out.append(f"结果里的分组表有 {len(results.groups)} 格："
                   + "、".join(c.label for c in results.groups[:4]))
    t = plan.treatment.strip()
    var = profile.var(t) if profile is not None and t and not is_interaction(t) else None
    if (var is not None and var.semantic_type in ("binary", "categorical") and t not in spec.indicators
            and (why := _outcome_on_measurement_block(plan, spec, frame, binding))):
        out.append(f"处理变量「{t}」本身就是分组变量（画像判它 {var.semantic_type}），而{why}")
    return out


def _looks_like_quantity(var) -> bool:
    """这一列像不像一个**量**——时长、频次、金额（M5 要认的那三样）。

    **四条都从画像里那一列的形状判，一个字的词表都没有**：名字叫 `hours_week`、`q12a`
    还是 `v301` 是同一件事，而认名字在没见过的写法上只有 6%（评测结果第二版第二节）。

    ① 画像判成 `continuous`——数值、取值多于十个、没有取值标签。**李克特题、是非题、
       带标签的分类题在画像里是 ordinal / binary / categorical，这一条把它们全挡在外面**：
       态度、认同这类东西没有客观对应物，自报的就是它本身，没有「代理」可言
    ② 取值非负——时长、频次、金额都有一个真实的零点；出现负数的是差值或标准化分数，不是量
    ③ 上尾拖得远（最大值大于中位数的两倍）——量没有上限，最大值是中位数的好几倍
       （一周上网六十小时、年收入五十万）；评分量表按设计有界，最大值只比中位数高一点点。
       这一条顺手也把年龄、教育年限挡在外面：它们确实有客观对应物，但自报的误差小到不值一道门
    ④ 取值在自己的区间里有空档——0–10 的单题评分取值多于十个、也会被画像判成 continuous，
       但它把 0 到 10 每一格都占满了：**那是一把有界的尺子，不是一个量**

    **边界：宁可漏，不可误报。**漏掉的是——自报的量被分成档存成序数（收入分七档）、
    上尾不够长的量（只有在业者的周工作小时）、`.sav` 里带取值标签因而判成 ordinal 的连续量。
    还剩一类误报：底部堆积的有界量表（对某个群体的好感度温度计，中位数 30、上限 100）；
    要把它也挡住，就得把「量表的上界」写成一张数字表——那又是一张会越长越大的表，不做。
    """
    d = var.distribution
    if var.semantic_type != "continuous" or "median" not in d:
        return False
    lo, mid, hi = float(d["min"]), float(d["median"]), float(d["max"])
    return lo >= 0 and hi > 2 * mid and var.n_unique < hi - lo + 1


def _treated_as_proxy(binding, column: str) -> bool:
    """这一列在绑定里**按代理处理**了没有。

    用的是现成的机制，不另造一套（`binding.py`）：标成了 `proxy`，**或者**代理差距三栏
    （理论要什么 / 实际测什么 / 差距朝哪偏）填齐了。两样认一样就算——宁可漏，不可误报。
    """
    if binding is None:
        return False
    return any(
        b.column == column and (b.status == "proxy" or (b.construct_def and b.measures and b.gap))
        for b in binding.bindings)


def _measurement_plan_warnings(plan: AnalysisPlan, profile: DataProfile, binding, frame=None) -> list[str]:
    """用尺子的两道门（测量骨架设计稿 5.2）+ 第三个绕行洞。**都只警告，不停。**

    - **M4 跨组比较前先验测量等价**：声明了 `groups_compared`，而产出登记里没有等价检验那一行。
      **不直接停**：等价检验做不出来时正确答案是把跨组比较降级为「组内描述」，不是不做研究——
      堵死了作者只能造假或换题。所以这里只警告，另开一条 must 级处方给出路（`route.py`）
    - **跨组比较不声明**（实施计划「零之二」第 ③ 条 + 第四个绕行洞）：`groups_compared` 空着，
      而别处有分组的迹象。判据不看这一栏怎么写，从别处对账（`measurement_group_signals`）——
      连「处理变量本身就是分组变量」那一路也认，那一路要帧才算得出结局是不是量表分
    - **M5 自报数据按高误差代理**：来源标成 `self_report`，某个指标是一个**量**
      （形状判据见 `_looks_like_quantity`），而这一列没按代理处理。自报的时长、频次、金额
      跟后台日志的系统性差异是元分析级别的事实（Parry 等 2021）

    三条都只核结构：**声明了什么、登记了什么、绑定里标了什么**，一条都不读论文里的论证。
    """
    spec = plan.measurement
    if spec is None:
        return []
    out: list[str] = []
    declared = [g for g in spec.groups_compared if g.strip()]
    if declared and not (spec.evidence_outputs.get("equivalence") or "").strip():
        out.append(Finding(
            f"跨组比较（measurement.groups_compared：{'、'.join(declared)}），而产出登记"
            "（measurement.evidence_outputs）里没有 equivalence 那一行——**没验过等价，跨组的数不可比**："
            "同一批题在两组里量的可能根本不是同一个东西，算出来的组间差里混着「题目在两组含义不同」。"
            "两条出路选一条：① 做测量等价检验，把那份产出登记进 evidence_outputs 的 equivalence 一行；"
            "② 把跨组比较**降级为组内描述**——每组各报各的、不比大小，并在局限里写明为什么不比。"
            "**这项检测只核登记了没有，不读论文里怎么论证。**", "plan.measurement.no_equivalence"))
    if signals := measurement_group_signals(plan, profile=profile, frame=frame, binding=binding):
        out.append(Finding(
            f"measurement.groups_compared 空着，别处却有分组的迹象：{'；'.join(signals)}。"
            "**不声明，跨组那项检测就整个绕过**，而论文照样跨组比较，代价为零——所以这里不看这一栏怎么写，"
            "看别处有没有分组。真在跨组比较就把分组变量写进 groups_compared（环节 4 会核这些列在不在数据里、"
            "够不够分两组），并按上一条给出等价检验；这几处不是跨组比较"
            "（例如交互项的调节变量是连续的）就照跑，处方那一条 `--dismiss` 写明理由",
            "plan.measurement.groups_undeclared"))
    # **留空不该免检。**填 `self_report` 会多挨这一道门，留空什么都不多——
    # 诚实的人受罚、空着的人免检，激励方向是反的（跟 `groups_compared` 那个洞同形）。
    # 所以留空时不看这一栏怎么写，看**别处有没有「这是自报数据」的迹象**：
    # 画像判这份数据是问卷调查，是一个算得出来的事实，跟作者怎么声明无关。
    src_signals = measurement_source_signals(plan, profile=profile, frame=frame)
    if src_signals:
        out.append(Finding(
            f"measurement.source_quality 空着，而别处看得出这是自报数据："
            f"{'；'.join(src_signals)}。**留空不等于不用管**——问卷题按定义就是受访者自己答的，"
            f"自报的时长、频次、金额跟后台日志、行政记录的差异是系统性的。"
            f"把这一栏按实际情况填上（self_report / administrative / logged / mixed）；"
            f"确实不是自报的（行政记录、后台日志）就填那一项，这条不再报",
            "plan.measurement.source_undeclared"))
    if spec.source_quality == "self_report" or src_signals:
        for col in spec.indicators:
            var = profile.var(col)
            if var is None or not _looks_like_quantity(var) or _treated_as_proxy(binding, col):
                continue
            out.append(Finding(
                f"指标「{col}」是自报的**量**（画像判它连续、非负、上尾拖到中位数的两倍以上——"
                f"时长、频次、金额就是这个形状），而 binding.json 里这一列没按代理处理："
                f"既没标成 proxy，代理差距三栏也没填齐。**自报的时长、频次、金额跟后台日志、行政记录的差异是"
                f"系统性的，不是随机噪声**（Parry 等 2021 的元分析）：拿它当真值，系数会朝一个说不清的方向偏。"
                f"把这一列按代理处理——`construct_def`（理论要的是什么）/ `measures`（这一列实际测的是什么）"
                f"/ `gap`（差距会让估计偏向哪边）三栏填上，方法节照这三栏写。"
                f"这一列确实没有客观对应物（它本身就是一个自我报告）就在三栏里写明这一点，写了就不再报",
                "plan.measurement.self_report_proxy"))
    return out


def _measurement_plan_problems(plan: AnalysisPlan) -> list[str]:
    """测量骨架挂在计划上的四道门。**一道都不读论文里的论证**（设计稿第二节）——
    效度、信度、构念全是语义判断，做成词表就是一道换个说法就失灵的门（评测量出来：6%）。
    这里只核两样结构上的事：**声明了什么**（`validity_evidence`）、**产出登记了什么**（`evidence_outputs`）。

    ① **声明了测量类型就必填测量块**（实施计划「零之二」第 ① 条）。跟因果的 `estimand`、
       描述的「描述量 + 目标总体」同一个口径。原先 `research_type == "measurement"` 而
       `measurement = None` 完全合法：**整块不写，一道门都不响，代价为零**。
       **`also` 与主类型一样算声明**：判据只看 `research_type` 时，主类型写 causal、
       把 measurement 搁进 `also`、测量块留空，就是同一个洞换了一栏声明。
    ② **M1 效度证据不能只有信度**（设计稿 5.1）：造尺子而产出登记里只有 reliability。
       信度说的是这几道题彼此一致，不是这几道题量的就是那个构念——只报信度不算给了效度证据。
    ③ **M2 声明了却一个产出都没有**：`validity_evidence` 非空，而声明的那几类在产出登记里一类都找不到。
       没有这一条，`validity_evidence` 就是一行走过场的字（设计稿第十二节的风险表）。
    ④ **kind 要跟产出对账**（实施计划「零之二」第 ② 条）：产出里出现**造尺子才会有**的东西
       （因子结构 / 载荷矩阵 / 信度系数），而计划标的是 `application`——报冲突。
       补这一条是因为把新编量表标成 `application`，M1/M2 与「题目数 < 3 就停」全部不触发，**代价为零**；
       修法跟上一轮修分批实施那个洞一样：**判据从「计划说什么」改成从产出算出来的事实**，
       只认产出的存在性，不认它长什么样、也不认论文怎么说。

    ②③④ 挂在**测量块本身**，不挂在 `research_type`：这一块是谁写的谁就得对得上——
    主类型写成因果、`also` 里放一个 measurement，同样躲不掉。
    栏与栏之间的必填（construct / indicators 缺一不可、造尺子要声明效度证据类别、
    登记产出要给名字）由 `MeasurementSpec` 自己管，这里只管跨栏的对账。
    """
    spec = plan.measurement
    if spec is None:
        if plan.research_type != "measurement" and "measurement" not in plan.also:
            return []
        return [Finding(
            "声明为测量的计划要写测量块（plan.measurement）：kind（instrument 造尺子 / application 用尺子）、"
            "construct（要测的那个看不见的东西）、indicators（题目或变量的列名）、validity_evidence（要给哪几类效度证据）、"
            "groups_compared、source_quality——先说清凭什么说这几道题量的是那个东西，再谈做不做得出来"
            "（测量骨架设计稿第六节）", "plan.measurement.spec_missing")]

    out: list[str] = []
    logged = {k for k, v in spec.evidence_outputs.items() if v.strip()}
    validity = sorted(logged - {"reliability"})
    declared = sorted(set(spec.validity_evidence))

    if spec.kind == "instrument" and logged and not validity:
        # M1。只在「登记了东西、而登记的全是信度」时报——登记表整个空着是 ③ 管的事，
        # 两条说的不是同一件事（一个是「只报了信度」，一个是「什么都没报」）
        out.append(Finding(
            f"造尺子（kind=instrument）声明要给 {'、'.join(declared)} 类效度证据，"
            f"产出登记（measurement.evidence_outputs）里却只有信度（reliability: "
            f"{spec.evidence_outputs.get('reliability', '')}）——**只报信度不算给了效度证据**："
            f"信度说的是这几道题彼此一致，不是这几道题量的就是那个构念（Flake 等 2017）。"
            f"把声明的那几类里至少一类的产出做出来、登记上；做不出来就把这一类从 validity_evidence 里去掉，"
            f"论文里也只说做得出来的那几类的话", "plan.measurement.reliability_only"))
    elif declared and not (set(declared) & set(validity)):
        # M2
        out.append(Finding(
            f"声明要给 {'、'.join(declared)} 类效度证据，产出登记（measurement.evidence_outputs）里"
            + (f"只有 {'、'.join(sorted(logged))}" if logged else "一类都没有")
            + "——**声明不等于做过**：每一类声明了的证据都要登记它的产出叫什么名字"
            "（`{{result:<名>.coef}}` 的名、`{{diag:<名>}}` 的名、稳健性的 label、或图名），"
            "检测会拿这个名字去结果里找它在不在。做不出来的那几类从 validity_evidence 里去掉",
            "plan.measurement.evidence_missing"))

    if spec.kind == "application" and (hit := [k for k in _INSTRUMENT_ONLY_OUTPUTS if k in logged]):
        out.append(Finding(
            "计划标的是用现成量表（kind=application），产出登记里却有 "
            + "、".join(f"{k}（{spec.evidence_outputs[k]}）" for k in hit)
            + "——这是**造尺子才会有的产出**（因子结构 / 载荷矩阵 / 信度系数）。"
            "新编量表标成 application，效度证据那几项检测与「题目数至少三道」全都不触发，代价为零；"
            "所以这里不看计划怎么标，只看产出里有什么。要么把 kind 改成 instrument 并按造尺子补齐效度证据，"
            "要么把这几项产出去掉、这篇只做用尺子的研究", "plan.measurement.kind_conflict"))
    return out


def _declares_simulation(plan: AnalysisPlan) -> bool:
    """这份计划有没有说自己在做仿真。**两栏哪一栏都算**——主类型写 causal、把 simulation
    搁进 `also`，是测量那一批第八、九个洞换个字段的同一条路（先自建指数再估影响就是这种）。"""
    return plan.research_type == "simulation" or "simulation" in plan.also


def _looks_like_sim_output(frame) -> bool:
    """这份数据是不是仿真跑出来的：**货箱契约的四根钥匙列同时在**就算。

    列名不是猜的，就是 `simbox.STATE_KEYS`——`states.csv` 靠这四列定位一行
    （哪个条件、第几次重复、第几波、哪个小人）。真实调查数据同时有这四列的可能性极低，
    而少一列就不算，宁可漏认也不冤枉。
    """
    return frame is not None and set(simbox.STATE_KEYS) <= set(frame.columns)


def _simulation_plan_problems(plan: AnalysisPlan, sim_state: dict | None, frame=None) -> list[str]:
    """仿真骨架的接轴门（设计稿 4.1）。**判据优先看盘上有没有货箱，那是事实，不是声明。**

    ① **声明为仿真就必须写仿真块**（S1）：跟因果的 `estimand`、描述的「描述量 + 目标总体」、
       测量的测量块同一个口径。`research_type` 与 `also` 哪一栏声明的都算。
    ② **声明为仿真就必须有入库的货箱**（S1'）：没有 `sim_state.json`，论文期那五道仿真门
       （模拟器与重复次数要报、降级要收紧措辞、结论要限定在模型内）**一道都不会响**——
       把模拟输出当普通 CSV 直接喂进来，整条验货链就绕过去了。
    ③ **有货箱就必须声明为仿真**（S2）：这一条**对作者有利**——不声明成仿真，
       措辞检查不给「在本模型内」开豁免，作者反而会被因果措辞门误拦；识别审计也会拿
       真实世界的标准去查一个模型。拿仿真数据当辅助材料的写 `also: ["simulation"]`。
    ④ **声明要比较的条件得在货箱里**：`conditions_compared` 写了货箱没有的条件名，
       论文里那句比较就是凭空的——打错一个字母不会有任何别的地方报错。

    ⑤ **数据本身长得像仿真输出**（`_looks_like_sim_output`）：既没声明、盘上也没货箱，
       但数据里货箱契约的四根钥匙列齐了——**这是整个骨架唯一的入口漏洞**（①②③ 都得先有
       一句声明或一份档案）。把 `states.csv` 当普通 CSV 直接画像、直接跑，
       上面四条一条都不响，五道论文期门也一道不响。判据只认数据的列，不认任何声明。

    「货箱里不止一个条件、计划却一个都没声明」只警告，见 `_simulation_plan_warnings`。
    """
    declared, spec = _declares_simulation(plan), plan.simulation
    has_box = isinstance(sim_state, dict)
    out: list[str] = []

    if not declared and not has_box and _looks_like_sim_output(frame):
        out.append(Finding(
            f"数据里 {'、'.join(simbox.STATE_KEYS)} 四列齐全——**这是仿真货箱 states.csv 的钥匙列**，"
            f"而计划没声明这是仿真研究、运行目录里也没有入库的货箱。"
            f"把模拟输出当普通 CSV 直接跑，验货（波次全不全、小人覆盖全不全、重复够不够）"
            f"与论文期那五项仿真检测**全部绕过去**，正文就能拿模型里的数字去说现实。"
            f"走正路：`oss sim check` 验货、`oss sim import` 入库、计划里 research_type 写 simulation。"
            f"这四列确实是真实数据里本来就有的（那得多巧），把其中一列改个名再跑",
            "plan.simulation.output_undeclared"))

    if declared and spec is None:
        out.append(Finding(
            "声明为仿真的计划要写仿真块（plan.simulation）：mechanism（要检验的机制，"
            "模型里什么导致什么）、pattern（该重现的已知模式——模型跑对了应当出现的那个规律）、"
            "conditions_compared、stands_in_for_humans——先说清检验的是哪套机制、"
            "该看见什么，再谈跑出来的数（仿真骨架设计稿第七节）",
            "plan.simulation.spec_missing"))
    if declared and not has_box:
        out.append(Finding(
            "声明为仿真，运行目录里却没有入库的货箱（sim_state.json）——**论文期那五项仿真检测"
            "一道都不会响**：模拟器与重复次数要不要报、降级要不要收紧措辞、结论有没有限定在"
            "模型内，全都挂在这份档案上。把模拟输出当普通 CSV 直接喂进来，整条验货链就绕过去了。"
            "先 `oss sim check` 验货、`oss sim import --box <货箱> --run <运行目录>` 入库，再跑",
            "plan.simulation.box_missing"))
    if has_box and not declared:
        out.append(Finding(
            "运行目录里有入库的货箱（sim_state.json），计划却没声明这是仿真研究"
            f"（research_type 写的是 {plan.research_type or '空'}，also 里也没有）——"
            "**这一条对你有利**：不声明成仿真，措辞检查不给「在本模型内」开豁免，"
            "你会被因果措辞检测反复误拦；识别审计也会拿真实世界的标准去查一个模型。"
            "主结论就是仿真跑出来的，research_type 写 simulation；"
            "仿真只是辅助材料、主结论在真实数据上的，写 `also: [\"simulation\"]`；"
            "这个货箱其实不用了，就把 sim_state.json 删掉",
            "plan.simulation.undeclared"))

    if spec is not None and has_box:
        known = sim_state.get("meta", {})
        known = set(known.get("conditions") or {}) if isinstance(known, dict) else set()
        if known and (unknown := [c for c in spec.conditions_compared if c.strip() and c not in known]):
            out.append(Finding(
                f"要比较的条件「{'、'.join(unknown)}」不在货箱里"
                f"（配料单 conditions 有：{'、'.join(sorted(known))}）——**打错一个字母不会有"
                f"任何别的地方报错**，论文里那句条件间比较就是凭空的。改成货箱里的条件名，"
                f"或者补跑这个条件、重新入库",
                "plan.simulation.condition_unknown"))
    return out


def _degenerate_columns(plan: AnalysisPlan, frame) -> list[str]:
    """**合成数据的方差被压扁**：分析真会用到的列里，众数占比高到不像人答的，逐列提醒。

    Bisbee（2024）的发现——一百个小人回答同一道题，分布比真人窄得多。拿它当调查数据用，
    标准误会假小、显著性会假强，而**现在没有任何一步会发现**：验货只查结构
    （列齐不齐、波次全不全、小人覆盖全不全），一个统计量都不看。

    门槛与它的经验依据见 `sim_checks.DEGENERATE_MODE_SHARE`。**只警告**——
    真人问卷里本来就有大量合法的偏态题，误报多了人会整片忽略这条。

    整列一个值不在这里报：画像的 `constant_columns` 早就认出来了，再写一遍是冗余。
    """
    out: list[str] = []
    for label, col in _value_columns(plan):
        if col not in frame.columns:
            continue
        s = frame[col].dropna()
        if len(s) < 10 or s.nunique() <= 1:
            continue                       # 太少判不动；整列一个值归画像管
        share = float(s.value_counts(normalize=True).iloc[0])
        if share < sim_checks.DEGENERATE_MODE_SHARE:
            continue
        top = s.value_counts().index[0]
        out.append(Finding(
            f"{label}「{col}」里 {share:.0%} 的小人答的是同一个值（{top}）——"
            f"**合成样本的方差会被压扁**（Bisbee 2024）：一百个小人回答同一道题，"
            f"分布比真人窄得多。拿这样一列当调查数据用，标准误会假小、显著性会假强。"
            f"三条路：换个问法重跑（提示词里别把答案暗示进去）；"
            f"跟真实调查对一下这一列的方差（计划里填 real_benchmark）；"
            f"确实该这么集中（例如问的是一件几乎人人都会做的事），就在局限里写明这一列的方差有多窄",
            "plan.simulation.degenerate_column"))
    return out


def _benchmark_findings(spec, frame, benchmark) -> list[str]:
    """跟真实调查对账（仿真骨架设计稿 4.3 乙）：**方差比**与**关系方向**。

    Argyle 等（2023）的「算法保真度」是拿合成样本跟同一批背景条件下的真实调查比分布；
    Bisbee（2024）补了一刀：**只比分布相关不够，方差会被压扁**。这里两样都查：

    - **方差比**：合成 SD ÷ 真实 SD 落在 `VARIANCE_RATIO_BAND` 外就报。**两头都报**——
      压扁是 Bisbee 的发现，夸大同样说明这批小人不像真人
    - **关系方向**：配了至少两对列时，第一对与第二对在两边的相关系数符号反了就报。
      一个合成人群里「学历越高越信任」而真人里相反，分布再像也没用

    判的是**作者自己配的那几对列**，不替他挑；配对里有一边不在数据里就说清哪一边缺，
    不猜、不跳过——「核不了」不等于「过了」。
    """
    out: list[str] = []
    pairs = [(a, b) for a, b in spec.real_benchmark.pairs if a.strip() and b.strip()]
    lo, hi = sim_checks.VARIANCE_RATIO_BAND
    usable: list[tuple[str, str]] = []

    for syn, real in pairs:
        miss = [n for n, f in ((syn, frame), (real, benchmark)) if n not in f.columns]
        if miss:
            out.append(Finding(
                f"对照列配对（{syn} ↔ {real}）里「{'、'.join(miss)}」不在数据里——"
                f"这一对对不了账。改成两边都有的列名，或者把这一对从 real_benchmark.pairs 去掉",
                "plan.simulation.benchmark_column_missing"))
            continue
        x = pd.to_numeric(frame[syn], errors="coerce").dropna()
        y = pd.to_numeric(benchmark[real], errors="coerce").dropna()
        if len(x) < 10 or len(y) < 10 or y.std() == 0 or x.std() == 0:
            continue                       # 判不动的不猜
        usable.append((syn, real))
        ratio = float(x.std() / y.std())
        if lo <= ratio <= hi:
            continue
        which = "压扁" if ratio < lo else "夸大"
        out.append(Finding(
            f"合成的「{syn}」跟真实的「{real}」方差{which}了：标准差之比 {ratio:.2f}"
            f"（可接受区间 {lo}–{hi}）。**合成样本的方差被压扁是已知现象**（Bisbee 2024）——"
            f"两批真实的人答同一套题，标准差之比的中位数是 0.99、九成落在 0.88–1.09。"
            f"方差假小，标准误就假小、显著性就假强。"
            f"要么改提示词重跑（别把答案的范围暗示进去）、要么把 stands_in_for_humans 改成 false "
            f"并在正文里只说「在本模型内」的话",
            "plan.simulation.variance_mismatch"))

    if len(usable) >= 2 and benchmark is not None:
        (a1, r1), (a2, r2) = usable[0], usable[1]
        syn_r = pd.to_numeric(frame[a1], errors="coerce").corr(
            pd.to_numeric(frame[a2], errors="coerce"))
        real_r = pd.to_numeric(benchmark[r1], errors="coerce").corr(
            pd.to_numeric(benchmark[r2], errors="coerce"))
        if pd.notna(syn_r) and pd.notna(real_r) and syn_r * real_r < 0:
            out.append(Finding(
                f"合成数据里「{a1}」与「{a2}」的关系方向跟真实数据相反："
                f"合成 r = {syn_r:.2f}，真实（{r1} 与 {r2}）r = {real_r:.2f}。"
                f"**分布再像也没用**——条件关系反了，说明这批小人的行为逻辑跟真人不是一回事"
                f"（Bisbee 2024 查的正是这一层：只比分布相关不够）。"
                f"改提示词重跑，或者把 stands_in_for_humans 改成 false",
                "plan.simulation.relation_flipped"))
    return out


def _simulation_plan_warnings(plan: AnalysisPlan, sim_state: dict | None, frame=None,
                              benchmark=None) -> list[str]:
    """仿真类的警告：条件那一栏空着、以及合成数据的列退化。

    **「声明的条件」那一栏不是白填的**：货箱里明明不止一个条件、计划一个都没声明，
    论文里的条件间比较就没有任何一处说得清比的是哪两条——跟测量那边
    「别处有分组的迹象，跨组比较那一栏却空着」（`groups_undeclared`）同一个形状。

    退化列那一条**只要盘上有货箱就查**，不看计划声明了什么：数据长什么样是事实。

    跟真实调查对账那一条（`_benchmark_findings`）**只在作者自己配了对照列时才查**：
    很多 ABM 本来就不主张小人等于真人，它做的是「机制能不能产生这个已知模式」——
    逼这类研究去找真人数据是错的。**触发看的是 `real_benchmark` 配没配对，
    不是 `stands_in_for_humans` 那个开关**：配了对照列却把开关关着是自相矛盾，
    照查（少填一栏就免检是零代价的，这一批只堵得了这一半，另一半照实记进待办）。
    """
    out: list[str] = []
    if isinstance(sim_state, dict) and frame is not None:
        out.extend(_degenerate_columns(plan, frame))

    spec = plan.simulation
    if spec is None or not isinstance(sim_state, dict):
        return out
    if (frame is not None and benchmark is not None
            and spec.real_benchmark and spec.real_benchmark.pairs):
        out.extend(_benchmark_findings(spec, frame, benchmark))
    meta = sim_state.get("meta")
    conds = set(meta.get("conditions") or {}) if isinstance(meta, dict) else set()
    if len(conds) < 2 or [c for c in spec.conditions_compared if c.strip()]:
        return out
    out.append(Finding(
        f"货箱里有 {len(conds)} 个条件（{'、'.join(sorted(conds))}），计划的 conditions_compared "
        f"却空着——空着的意思是「这篇只做单条件的模式展示」，可货箱里明明跑了对照条件。"
        f"要比较就把比的那几条写进来（论文里的条件间比较要说得出比的是哪两条）；"
        f"确实只报其中一条，就在正文里写明另外几条跑了但不进这篇的分析",
        "plan.simulation.conditions_undeclared"))
    return out


def _value_columns(plan: AnalysisPlan) -> list[tuple[str, str]]:
    """**带着取值**进回归的列：`(说法, 列名)`，按出现顺序去重。

    固定效应、聚类层级、单位与时间列、权重与抽样设计的列**不在其中**：
    那些列里的数字是一个类别号或一个标识（编号为 99 的省份就长这样），
    不是一个被算进系数的数值——照缺失码去判它们，误拦的代价远大于收益。
    """
    named = [("结果变量", plan.outcome), ("处理变量", plan.treatment)]
    named += [("控制变量", c) for c in plan.controls]
    named += [("工具变量", c) for c in plan.instruments]
    if plan.running:
        named.append(("断点变量", plan.running))
    if plan.description is not None:
        named += [("分组变量", g) for g in plan.description.groups]

    out: dict[str, tuple[str, str]] = {}
    for label, name in named:
        if not name:
            continue
        # 交互项 `a*b` 不是数据里的列，进回归的是它的分量
        for col in parts_of(name) if is_interaction(name) else [name]:
            out.setdefault(col, (label, col))
    return list(out.values())


def _missing_code_findings(
    plan: AnalysisPlan, profile: DataProfile, frame: pd.DataFrame,
    value_labels: Mapping[str, Mapping[object, object]] | None,
) -> tuple[list[str], list[str]]:
    """进了回归的列里还留着缺失值代码——`(拒绝理由, 警告)`。

    99 = 拒答、9999997 = 不适用，**在文件里就是普通数字**：算进回归就是错的，
    而且不报任何错。画像早就认出来写进了 `profile.json`，可是一直**没有一处代码读它**——
    一整列 99 能一路算进回归、写进论文。

    **为什么在这里现查，而不是把画像的字段传下来。**画像跑在原始数据上，
    分析跑在 `prep.py` 处理过的帧上。传字段只能知道「原始数据里有」，
    不知道「处理之后还在不在」——作者很可能已经在 `prep.py` 里把 99 改成缺失了，
    那就不该再拦。**在分析帧上现查，判的才是事实。**

    取值标签是例外：「99 是什么意思」只有数据文件说得出，所以含义先取分析这份文件
    自带的标签（`.sav` / `.dta` 才有），没有才退回画像里记的那份——**两者都是文件的原话，
    不是画像的推断**；「这个码还在不在」只看帧。

    **两处都要，不是冗余**：`prep.py` 写出来的 CSV 带不走标签（只剩画像那份），
    而画像给每个变量留的标签有条数上限（`profile_table` 的 `max_unique_for_labels`），
    职业码这种几百个标签的列在画像里是空的——正是最该拦的那种列。

    判据一律复用 `profiler.missing_codes`，这里不另写一套。
    """
    from oss.core import profiler

    labels: dict[str, Mapping[object, object]] = {
        v.name: v.value_labels for v in profile.variables if v.value_labels}
    labels.update({c: vl for c, vl in (value_labels or {}).items() if vl})
    if plan.sample_filter:
        # 判的是**估计样本**：作者拿 sample_filter 把那些行排掉了，就不该再拦。
        # 筛不动（写法不合法之类）就按整份帧判——那条写法本身的问题由别处报
        try:
            frame = frame.query(plan.sample_filter)
        except Exception:                # noqa: BLE001
            pass
    problems: list[str] = []
    warnings: list[str] = []
    for label, name in _value_columns(plan):
        if name not in frame.columns:
            continue                    # 列名不在数据里，上面那道门已经拒了
        for code, n, text in profiler.missing_codes(frame[name], labels.get(name)):
            shown = f"{code:.0f}" if float(code).is_integer() else f"{code}"
            fix = f"`df.loc[df['{name}'] == {shown}, '{name}'] = pd.NA`"
            if text:
                # 文件的原话，没有「猜得准不准」的问题：算进回归就是把「没有答案」当成了数值
                problems.append(Finding(
                    f"{label}「{name}」里有 {n} 行的取值是 {shown}，而数据文件的取值标签写着"
                    f"「{text}」——**那不是一个数值，是「没有答案」**。这样进回归，"
                    f"这 {n} 行会被当成真值算进系数，而且不会报任何错。"
                    f"先在 prep.py 里改成缺失再重跑：{fix}", "plan.columns.missing_code_labeled"))
            else:
                warnings.append(Finding(
                    f"{label}「{name}」里 {shown} 出现了 {n} 次，位置像缺失值代码"
                    f"（在分布的一头、跟其余取值之间有空档）——**也可能是真值**："
                    f"99 岁、99 分都是真的。两条路二选一：确认是代码就在 prep.py 里"
                    f"改成缺失重跑（{fix}）；确认是真值就照跑，并在数据与方法一节写明这一列的 "
                    f"{shown} 是实测值。核对的办法：查码本，或算一遍剔除后的均值跟画像里的对一下",
                    "plan.columns.missing_code_suspected"))
    return problems, warnings


def validate_plan(plan: AnalysisPlan, profile: DataProfile, frame: pd.DataFrame | None = None,
                  value_labels: Mapping[str, Mapping[object, object]] | None = None,
                  binding=None, sim_state: dict | None = None, benchmark=None,
                  prelocked: set[str] | None = None,
                  ) -> tuple[list[str], list[str]]:
    """跑之前逐项核。返回 `(拒绝理由, 警告)`。

    **拒绝和警告必须分开。**「列名不存在」是拒绝——跑了也是错的；
    「没指定聚类层级」是警告——截面数据本来就可能没有聚类结构，
    硬拦住等于逼使用者编一个出来。

    **每个列名都要在数据里真的存在。**这是 PLAN.md §8 ⑤ 记下的那条验收。

    `frame` 是**分析真正要跑的那一份数据**（`prep.py` 处理之后的），`value_labels` 是它自带的
    取值标签。给了帧才查缺失值代码：那一条判的是「码还在不在」，只有帧说得准，
    见 `_missing_code_findings`。

    `binding` 是 `binding.json`（有就给）：测量类的 M5 要知道那一列**按代理处理了没有**，
    而「按代理处理」的现成机制就在绑定里（`status="proxy"` 与代理差距三栏）。没有绑定也判得动，
    只是「没按代理处理」这件事无从被推翻。

    `sim_state` 是 `sim_state.json` 读出来的入库档案（有就给）：仿真类的接轴门要知道
    **盘上到底有没有货箱**——那是事实，而「这是不是仿真研究」只是一句声明。
    跟 `binding` 同一个办法：读文件归 api 层，这里只判。

    `benchmark` 是仿真类的**真实对照数据**（`simulation.real_benchmark.data` 指的那一份，
    有就给）：合成样本的方差被压扁、条件关系反向，只有拿真人数据对一遍才看得出来。
    """
    problems: list[str] = []
    warnings: list[str] = []
    known = {v.name for v in profile.variables}

    if plan.estimator not in SUPPORTED_ESTIMATORS:
        problems.append(
            Finding(f"估计量 {plan.estimator!r} 还不支持。当前只有：{'、'.join(SUPPORTED_ESTIMATORS)}", "plan.estimator.unsupported")
        )

    survey_problems, survey_warnings = _survey_plan_problems(plan, profile)
    problems.extend(survey_problems)
    warnings.extend(survey_warnings)
    problems.extend(_descriptive_plan_problems(plan, profile))
    problems.extend(_measurement_plan_problems(plan))
    warnings.extend(_measurement_plan_warnings(plan, profile, binding, frame))
    problems.extend(_simulation_plan_problems(plan, sim_state, frame))
    warnings.extend(_simulation_plan_warnings(plan, sim_state, frame, benchmark))

    def check(label: str, name: str | None, *, required: bool) -> None:
        if not name:
            if required:
                problems.append(Finding(f"{label}没填", "plan.columns.required_missing"))
            return
        if is_interaction(name):
            return  # 交互项不是数据里的列，它的分量在下面单独核
        if name not in known:
            near = _nearest(name, known)
            problems.append(
                Finding(f"{label}「{name}」不在数据里" + (f"，最接近的是 {near}" if near else ""), "plan.columns.not_in_data")
            )

    check("结果变量", plan.outcome, required=True)
    # did / event_study 的处理变量是从「处理起始时间」推出来的，不用单独填
    check("处理变量", plan.treatment, required=plan.estimator == "ols_fe")
    for c in plan.controls:
        check("控制变量", c, required=False)
    for f in plan.fixed_effects:
        check("固定效应", f, required=False)
    check("聚类层级", plan.cluster_level, required=False)
    # 测量块的指标也是「计划写的列」：跟结局、控制变量走同一路核，不另造机制。
    # 原先 indicators 只核非空，列名写错（q1 打成 ql）一声不响——测量研究的全部证据都建在这几列上
    for c in (plan.measurement.indicators if plan.measurement is not None else []):
        check("测量块的指标", c, required=False)

    # 交互项：逐个分量核。**这是交互项进契约的全部意义。**
    for spec in plan.interactions:
        for c in parts_of(spec):
            if c not in known:
                near = _nearest(c, known)
                problems.append(
                    Finding(f"交互项「{spec}」的分量「{c}」不在数据里"
                    + (f"，最接近的是 {near}" if near else ""), "plan.interaction.component_missing")
                )
    if is_interaction(plan.treatment) and plan.treatment not in plan.interactions:
        problems.append(
            Finding(f"处理变量「{plan.treatment}」是个交互项，但没有列进 interactions", "plan.interaction.treatment_unlisted")
        )
    # **iv 下的交互项原先是被静默吞掉的。**`run_iv` 取列时压根没把 interactions 算进去
    # （只取 outcome / treatment / controls / instruments），于是写了交互项既不报错、也不生效，
    # 跑出来的还是没有交互项的那个规格——**声明了却不生效**，跟 ols_fe+post 绕过分批门同形。
    # 真做要两个内生变量（处理，以及处理×调节），两阶段最小二乘本身可以，
    # 但有效 F（Olea–Pflueger）、AR 区间反演、tF 区间三样诊断都是按单内生变量建的，
    # 改成多内生要连它们一起重做。**没重做之前明说做不了，比悄悄少算一项强。**
    if plan.estimator == "iv" and (plan.interactions or is_interaction(plan.treatment)):
        which = "处理变量本身是交互项" if is_interaction(plan.treatment) else \
                f"interactions 里有「{'、'.join(plan.interactions)}」"
        problems.append(Finding(
            f"iv（两阶段最小二乘）现在不支持交互项：{which}。"
            "交互项与处理变量同样内生，需要第二个内生变量和配套的借力变量，"
            "而本工具的有效 F、AR 区间、tF 区间都是按单个内生变量算的，多内生下这三个数会变味。"
            "要做异质性，用 sample_filter 分样本各估一次（差异不可作正式检验，要在论文里说明）；"
            "要的是正式的组间检验，本工具目前做不了",
            "plan.interaction.iv_unsupported"))

    warnings.extend(_missing_lower_order(plan))

    # **上限只管事前锁定的那些。**每一项都要跑、都要写进论文。验证跑 12 项，
    # 正文被稳健性撑满、审稿人逐项追问。上限 12：真跑里 8–10 项是常态，而那些检验
    # 正是发现自己结论站不住的地方；再多就成了没人读的一墙。
    #
    # 但**审稿人指名要求补做的诊断不该算进这个上限**——原先算，于是照要求补做反被罚，
    # 方向是反的（因果那篇按编辑的「改完标准」补了 23 项，合规检查就此判不过）。
    # 「哪些是事前锁定的」挂在留痕上（`loop.prelocked_robustness`）：
    # 第一次成功跑分析时清单里就有的才算。留痕没记（老目录）就退回按总数判。
    n_locked = len(plan.robustness if prelocked is None
                   else [r for r in plan.robustness if r.label in prelocked])
    if n_locked > 12:
        extra = len(plan.robustness) - n_locked
        tail = f"（另有 {extra} 项是跑过分析之后才加的，不算进这个上限）" if extra else ""
        warnings.append(
            Finding(f"事前锁定的稳健性清单有 {n_locked} 项{tail}——每项都要跑、都要写进论文，"
            f"正文会被稳健性撑满。**超过 12 项**就成了没人读的一墙，挑真正威胁结论的那些",
            "plan.robustness.too_many")
        )

    if plan.estimator == "did_cs":
        from oss.core import staggered
        if not staggered.available():
            problems.append(Finding(f"did_cs 需要 StatsPAI，没装。装：{staggered.INSTALL_HINT}", "plan.estimator.statspai_missing"))

    # 不同单位在不同时间被处理 = 分批实施。**这是这套方法最有名的坑。**
    # 验收跑：17 批、I² = 93%，执行者把异质性诊断出来了却没换估计量——评审第一条致命问题。
    # **判据是数据，不是计划里写的估计量名字**：见 `_adoption_cohorts` 与 `_twfe_staggered_cohorts`。
    n_cohorts, cohort_col = 0, ""
    if plan.estimator in DID_FAMILY:
        for label, name in (("个体标识", plan.unit), ("时间列", plan.time),
                            ("处理起始时间列", plan.treat_time)):
            if not name:
                problems.append(Finding(f"{plan.estimator} 需要指定{label}", "plan.did.field_missing"))
            elif name not in known:
                near = _nearest(name, known)
                problems.append(
                    Finding(f"{label}「{name}」不在数据里" + (f"，最接近的是 {near}" if near else ""), "plan.did.column_missing"))
        if plan.treat_time:
            cohort_col = plan.treat_time
            # 帧在手就按**分析真要跑的那份数据**算；读不进来（`oss run` 里数据读不了、
            # 老调用只给画像）才退回画像的取值数——两条算的是同一件事
            n_cohorts = (_adoption_cohorts(frame, plan.unit, plan.time, plan.treat_time)
                         if frame is not None else
                         (v.n_unique if (v := profile.var(plan.treat_time)) else 0))
    elif (n := _twfe_staggered_cohorts(frame, plan)) > 1:
        # 分批实施不一定写成 did：`ols_fe` + 一个 post 哑变量 + 单位与时间双向固定效应，
        # 算的跟 did 一模一样，坑也一模一样，而门原先认的是估计量的名字，换个写法就全放行
        n_cohorts, cohort_col = n, plan.treat_time or plan.treatment
    if n_cohorts > 1 and plan.estimator != "did_cs":
        # 警告必须直接指向药，不能只描述病
        warnings.append(
            Finding(f"「{cohort_col}」有 {n_cohorts} 个不同的处理起始时间，"
            f"说明政策是**分批实施**的。分批 + 各批效应不同时，"
            f"双向固定效应的双重差分会把**早处理组当成晚处理组的对照**，"
            f"估计量**可能有偏**，极端情况下连符号都不对。"
            f"**分批实施用 `did_cs`（Callaway–Sant'Anna）作主规格**，"
            f"`did` 只能作对比；逐年展开图照看，论文里说明为什么换", "plan.did.staggered")
        )

    # 组内变异为 0 的核心变量：跑得出来，但被固定效应吃掉了
    for label, name in (("处理变量", plan.treatment), ("结果变量", plan.outcome)):
        if name and not is_interaction(name) and name in known and (v := profile.var(name)):
            if plan.fixed_effects and v.within_variation == 0.0:
                problems.append(
                    Finding(f"{label}「{name}」的组内变异为 0——"
                    f"加了固定效应之后它是常数，系数估不出来", "plan.variation.within_zero")
                )
            if v.n_unique <= 1:
                problems.append(Finding(f"{label}「{name}」取值恒定，进不了回归", "plan.variation.constant"))

    # 进了回归的列里还留着 99 = 拒答这类缺失值代码：标签写明的拒跑，只是分布上像的警告
    if frame is not None:
        mc_problems, mc_warnings = _missing_code_findings(plan, profile, frame, value_labels)
        problems.extend(mc_problems)
        warnings.extend(mc_warnings)

    # 写了初级抽样单位，主规格的标准误按抽样设计算：不再催 cluster_level，少于 30 的那条数抽样单位
    psu = plan.survey.psu if plan.survey is not None else ""
    level = psu or plan.cluster_level
    if not level:
        warnings.append(
            Finding("没指定聚类层级。社科数据几乎总是聚类的（同一个县的人不独立），"
            "不聚类的标准误会系统性偏小、p 值虚假显著。"
            "确实是独立抽样的截面数据才可以不聚类", "plan.cluster.missing")
        )
    elif level in known:
        n_clusters = next(
            (v.n_unique for v in profile.variables if v.name == level), 0
        )
        if n_clusters < 30:
            warnings.append(
                Finding(f"只有 {n_clusters} 个{'初级抽样单位' if psu else '聚类单位'}。**聚类数少于约 30 时，"
                f"聚类稳健标准误本身是有偏的**——小样本校正只能缓解，不能消除。"
                f"考虑 wild bootstrap", "plan.cluster.few")
            )

    if plan.research_type == "causal":
        problems.extend(_causal_plan_problems(plan))
    elif not plan.research_type and plan.estimand is None and (
            plan.estimator in (*DID_FAMILY, "iv", "rdd") or plan.treat_time):
        warnings.append(Finding("老计划没写估计量三步（plan.estimand）与控制变量角色（plan.control_roles）——"
                        "因果研究照因果骨架设计稿 3.1–3.3 补上，补了之后 oss run 会逐项核", "plan.causal.legacy_no_estimand"))

    if not plan.research_type:
        warnings.append(
            Finding("计划没声明研究类型（research_type：causal / descriptive / measurement / simulation）——"
            "要不要做识别审计暂按估计量判，论文措辞也不查；新跑的计划照输入的 `--- type:` 或出题时定的类型写上；"
            "第一次 oss run 之后才写上的类型，会记为「看过结果才补」（合规检查判不过）", "plan.research_type.missing"))
    return problems, warnings


def _missing_lower_order(plan: AnalysisPlan) -> list[str]:
    """交互项缺低阶项 → 系数不可解读。**这是最常见的规格错误之一。**

    三重交互 `a*b*c` 需要三个主效应和三个二阶项都在场。缺了的话，
    交互系数吸收了本该由低阶项承担的变异，解读就完全错了。

    **警告不拒绝**：偶尔有人是故意的（比如已经中心化过、或有理论理由），
    但绝大多数情况是忘了。
    """
    present: set[frozenset[str]] = set()
    for name in [plan.treatment, *plan.controls, *plan.interactions]:
        if name:
            present.add(frozenset(parts_of(name)))

    out: list[str] = []
    for spec in plan.interactions:
        comp = parts_of(spec)
        if len(comp) < 2:
            continue
        need = [
            sub for r in range(1, len(comp))
            for sub in combinations(comp, r)
            if frozenset(sub) not in present
        ]
        if need:
            shown = "、".join(INTERACTION_SEP.join(x) for x in need)
            out.append(
                Finding(f"交互项「{spec}」缺低阶项：{shown}。"
                f"**缺了低阶项，交互系数就不可解读**——它吸收了本该由低阶项承担的变异", "plan.interaction.lower_order")
            )
    return out


def _nearest(name: str, known: set[str]) -> str | None:
    """列名写错时给个最接近的。"""
    import difflib

    m = difflib.get_close_matches(name, known, n=1, cutoff=0.6)
    return m[0] if m else None


# ------------------------------------------------------------------ 估计


def _sig(x: float, decimals: int = 6, digits: int = 6) -> float:
    """存系数用的取整：**至少 6 位小数，且至少 6 位有效数字**，两者取更精确的。

    原先一律 `round(x, 6)`。对社科常见的量级那没问题，
    但变量跨到十亿量级时真实系数是 `1e-11`——**round 到 6 位小数就是 `0.0`**。

    存成 0.0 不是「精度差一点」，是**把一个真实存在的效应记成了完全没有效应**，
    而且看不出是舍入造成的：`0.0` 长得就像 0.0。

    反过来只按有效数字也不行——`-1.1320851` 取 6 位有效数字是 `-1.13209`，
    **比原来还粗**，会让「跟 pandas 的 OLS 对得上」这类回验失败。
    所以两条一起要：小数位保底 6，有效数字保底 6。
    """
    if not np.isfinite(x) or x == 0:
        return float(x)
    from math import floor, log10

    return float(round(x, max(decimals, digits - 1 - int(floor(log10(abs(x)))))))


def _demean(
    X: np.ndarray,
    groups: list[np.ndarray],
    *,
    w: np.ndarray | None = None,
    max_iter: int = 100,
    tol: float = 1e-10,
):
    """按组去均值，吸收固定效应。

    多维固定效应用交替投影（Frisch-Waugh-Lovell 的迭代版）——
    对两维以上，一次去均值不够，要迭代到收敛。

    带权重时减的是**加权组均值** `Σwy/Σw`，不是算术均值。
    减错了不会报错：固定效应仍然被吸收掉，但吸收掉的是另一个东西，
    系数照样算得出来、照样是错的。
    """
    Z = X.astype(float).copy()
    if not groups:
        return Z
    for _ in range(max_iter):
        before = Z.copy()
        for g in groups:
            df = pd.DataFrame(Z)
            if w is None:
                Z = (df - df.groupby(g).transform("mean")).to_numpy()
            else:
                ws = pd.Series(w)
                denom = ws.groupby(g).transform("sum").to_numpy()
                num = df.mul(ws, axis=0).groupby(g).transform("sum").to_numpy()
                Z = Z - num / denom[:, None]
        if np.max(np.abs(Z - before)) < tol:
            break
    return Z


def _effective_n(w: np.ndarray) -> float:
    """Kish 有效样本量 `(Σw)²/Σw²`。

    权重越不均，同样的行数含的信息越少。3 万人的加权样本可能只值 2 万人，
    而置信区间照 3 万人算就是**假的精确**——论文里报的样本量该是哪一个，
    读者有权知道两个都是多少。
    """
    s1, s2 = float(w.sum()), float((w ** 2).sum())
    return s1 * s1 / s2 if s2 > 0 else 0.0


def _positive_weights(df: pd.DataFrame, col: str) -> pd.Series:
    """权重列转成数值并查非正（`_ols_rows`、`run_iv`、`run_rdd`、分箱、`staggered._prepare` 共用）。

    **权重缺失的行不能悄悄按 1 处理**——那等于给这些人一个凭空的权重，而且没有任何人会发现。
    缺失照旧是缺失，由调用者去掉；0 与负数拒绝。返回转成数值的那一列。
    """
    wcol = pd.to_numeric(df[col], errors="coerce")
    if wcol.isna().all():
        raise PlanRejected([f"权重列 `{col}` 全是缺失或非数值，加权跑不了"])
    if (bad := int((wcol <= 0).sum())):
        raise PlanRejected([
            f"权重列 `{col}` 有 {bad} 行不是正数。"
            f"权重必须为正——0 或负值多半是缺失码没处理干净，先在 prep.py 里查清楚"
        ])
    return wcol


def _weights_text(w: np.ndarray | None, col: str, n: int) -> str:
    """诊断里「抽样权重」那一条。**永远报，不只在加权时报**：只在加权时才出现的诊断项，
    看不见它的人不会想到去找它。"""
    if w is None:
        return ("**未加权。**这一条永远报，不只在加权时报——"
                "读者无从分辨“这份数据没有权重”和“有权重但没用”")
    n_eff = _effective_n(w)
    # **名字要带口径。**这里的有效样本量只折算权重的设计效应（Kish），
    # 而 `oss power` 报的那个还要再折算整群设计效应，两个数不一样。
    # 真跑时论文里一处写 3606、一处写 2541，都叫「有效样本量」，审稿人当场指出读者对不上。
    return (f"按 `{col}` 加权（取值 {w.min():.4g}–{w.max():.4g}）。"
            f"加权有效样本量 {n_eff:.0f}（实际 {n} 行，权重设计效应（Kish）{n / n_eff:.2f}）——"
            f"**置信区间该按它读，不是按行数**。"
            f"注意这个数只折算了权重；`oss power` 的有效样本量还折算了整群设计效应，比它更小，两者不可混用")


def _design_of(plan: AnalysisPlan, df: pd.DataFrame) -> SurveyDesign | None:
    """声明了初级抽样单位、且那一列在表里：标准误按抽样设计算。"""
    s = plan.survey
    return s if s is not None and s.psu and s.psu in df.columns else None


def _design_se_text(design: SurveyDesign, stratified: bool, info: dict[str, int], cluster_level: str) -> str:
    """按抽样设计时「标准误」那一条（`run_ols_fe`、`run_iv` 共用）。"""
    return (f"按抽样设计（{'分层 ' + design.strata if stratified else '未分层'}，"
            f"初级抽样单位 {design.psu}：{info['psu']} 个抽样单位、{info['strata']} 层；泰勒线性化）"
            + (f"；{info['singletons']} 层只有一个抽样单位，按全体均值中心化" if info["singletons"] else "")
            + (f"。cluster_level（{cluster_level}）没用上：主规格按抽样设计"
               if cluster_level and cluster_level != design.psu else ""))


def _design_diag(info: dict[str, int]) -> dict[str, str]:
    """按抽样设计时的三条数值诊断。只放数：`run_plan` 拿自由度算区间的 t 临界值，渲染与合规检查按数读。"""
    return {"抽样单位数": str(info["psu"]), "单抽样单位的层": str(info["singletons"]),
            "抽样设计自由度": str(info["dof"])}


def _psu_ids(df: pd.DataFrame, design: SurveyDesign) -> np.ndarray:
    """初级抽样单位的编号，嵌在层里（同一个编号出现在两层算两个单位，跟 `_design_vcov` 同一口径）。
    GSS 那种每层都编 1、2 的，不嵌就只剩两个簇。"""
    if design.strata and design.strata in df.columns:
        return df.groupby([design.strata, design.psu], sort=False).ngroup().to_numpy()
    return df[design.psu].to_numpy()


def _t_crit_of(diagnostics: dict[str, str]) -> float:
    """按抽样设计时区间与逐期判据的 t 临界值，自由度取诊断里的「抽样设计自由度」（`_design_diag` 只放数）。
    没按设计算（没有这一条）返回 0，调用方按正态 1.96。主规格（`spec.t_crit`）、每一项稳健性
    （`RobustnessRun.t_crit`）、逐年展开的平行趋势都从这里取，各用各的自由度。"""
    dof = diagnostics.get("抽样设计自由度", "")
    if not dof.isdigit():
        return 0.0
    from oss.core.causal_stats import t_ppf
    return round(t_ppf(0.975, int(dof)), 6)


def _weight_design_cols(plan: AnalysisPlan) -> list[str]:
    """回归变量之外、按缺失去行时也要算上的列：权重、初级抽样单位、分层（写了才算）。

    `_ols_rows`、`run_iv` 按它去行；表 1（`_descriptives`）与样本构建日志（`_sample_log`）也按它去——
    主回归因为这几列缺失去掉的行，表 1 不去的话，表 1 的 N 就跟回归的 n 对不上。
    """
    design = (plan.survey.psu, plan.survey.strata) if plan.survey is not None else ()
    return [c for c in (plan.weights, *design) if c]


def _weight_design_missing(df: pd.DataFrame, plan: AnalysisPlan) -> pd.DataFrame:
    """表 1 与样本构建日志用：逐列标出「这一行因为权重或抽样设计列进不了主回归」。

    跟 `_ols_rows` 同一口径：权重先转成数值（转不成的算缺失），抽样设计列看原值缺不缺。权重不为正的也标上——
    主回归碰到那种权重整个拒跑、走不到这里，标上只是不让它悄悄混进表 1。列不在表里的不管。
    """
    out: dict[str, pd.Series] = {}
    for c in _weight_design_cols(plan):
        if c not in df.columns:
            continue
        if c == plan.weights:
            w = pd.to_numeric(df[c], errors="coerce")
            out[c] = w.isna() | (w <= 0)
        else:
            out[c] = df[c].isna()
    return pd.DataFrame(out, index=df.index)


def _cluster_robust_vcov(
    X: np.ndarray, resid: np.ndarray, clusters: np.ndarray, xtx_inv: np.ndarray,
    k_absorbed: int = 0,
) -> np.ndarray:
    """聚类稳健方差。

        V = (X'X)^-1 · Σ_g (X_g' u_g)(X_g' u_g)' · (X'X)^-1

    带小样本校正 `G/(G-1) · (N-1)/(N-K)`——聚类数少时不校正会明显低估。

    **`K` 要把固定效应吸收掉的自由度算进去。**去均值把参数从矩阵里拿掉了，
    但它们仍然是估出来的参数——不算进 K，标准误就偏小，而偏小的方向
    是往显著里偏。同一个函数里 HC1 那条路一直算了，聚类这条路没算：
    两套账并存，谁也不会发现，因为两条路不会同时跑。

    省份聚类那种场合差不到 1%（31 个省 / 一万多行），
    个体固定效应那种场合能差出十几个百分点——**而那正是社科最常见的设定**。
    """
    n, k = X.shape                    # k 还兼着 meat 的维度，别覆盖它
    meat = np.zeros((k, k))
    codes = pd.Categorical(clusters).codes
    for g in np.unique(codes):
        m = codes == g
        Xg_ug = X[m].T @ resid[m]
        meat += np.outer(Xg_ug, Xg_ug)
    G = len(np.unique(codes))
    correction = (G / max(G - 1, 1)) * ((n - 1) / max(n - k - k_absorbed, 1))
    return correction * (xtx_inv @ meat @ xtx_inv)


def _design_vcov(
    X: np.ndarray, resid: np.ndarray, strata: np.ndarray | None, psu: np.ndarray,
    xtx_inv: np.ndarray, k_absorbed: int = 0,
) -> tuple[np.ndarray, dict[str, int]]:
    """按抽样设计的方差（泰勒线性化，调查层设计稿 5.2）。返回 `(方差, {psu, strata, singletons, dof})`。

    每层里把各初级抽样单位的得分减去该层均值，乘 `n_h/(n_h − 1)` 再加总；抽样单位嵌在层里
    （同一个编号出现在两层算两个单位）。

    - **不给分层就直接调 `_cluster_robust_vcov`**——跟现在逐位一致，老结果不变
    - 小样本校正的后半截 `(N−1)/(N−K)` 跟聚类稳健方差同一口径；前半截 `G/(G−1)` 由各层的
      `n_h/(n_h−1)` 代替。只有一层时两者相同：正规方程保证得分之和为 0，减均值不改变任何东西。
      **这后半截是有意跟 Stata `svy` 与 R `survey::svyglm` 不同的**：它们不乘 `(N−1)/(N−K)`。
      留着它，只有一层时才逐位还原聚类稳健方差（CR1）；代价是偏保守——个体固定效应嵌在
      抽样单位里时，标准误会大出几个百分点
    - 只有一个抽样单位的层：得分减**全体抽样单位得分的均值**（Stata `singleunit(centered)` 的做法），
      个数进返回值，诊断里写明
    - 自由度 = 抽样单位数 − 层数，给 t 分布用。**不到 1 就拒**（多半是同一列同时写成了分层和
      抽样单位，每层都只有一个抽样单位）：原先悄悄抬成 1，t 临界值 12.7，z≈12 的系数都不显著
    """
    if strata is None:
        G = int(pd.Series(psu).nunique())
        _check_design_dof(G, 1)
        return (_cluster_robust_vcov(X, resid, psu, xtx_inv, k_absorbed),
                {"psu": G, "strata": 1, "singletons": 0, "dof": G - 1})
    n, k = X.shape
    scores = pd.DataFrame(X * resid[:, None])
    scores["_h"] = pd.Categorical(strata).codes
    scores["_i"] = pd.Categorical(psu).codes
    per_psu = scores.groupby(["_h", "_i"], sort=True).sum()
    h_of = per_psu.index.get_level_values("_h").to_numpy()
    Z = per_psu.to_numpy()
    grand = Z.mean(axis=0)
    meat = np.zeros((k, k))
    singles = 0
    for h in np.unique(h_of):
        Zh = Z[h_of == h]
        if len(Zh) >= 2:
            D = Zh - Zh.mean(axis=0)
            meat += len(Zh) / (len(Zh) - 1) * (D.T @ D)
        else:
            d = Zh[0] - grand
            meat += np.outer(d, d)
            singles += 1
    G, H = len(Z), int(len(np.unique(h_of)))
    _check_design_dof(G, H)
    correction = (n - 1) / max(n - k - k_absorbed, 1)
    return correction * (xtx_inv @ meat @ xtx_inv), {"psu": G, "strata": H, "singletons": singles,
                                                     "dof": G - H}


def _check_design_dof(G: int, H: int) -> None:
    """按设计的自由度 = 抽样单位数 − 层数。不到 1 就拒，不悄悄抬成 1。

    每层至少有一个抽样单位，所以 G ≥ H；G ≤ H 就是每层都只剩一个。单抽样单位的层是容忍的
    （`_design_vcov` 按全体均值中心化），只拒这一种。
    """
    if G - H < 1:
        raise PlanRejected([
            f"初级抽样单位不比层多（{G} 个抽样单位、{H} 层，G ≤ H）：每层都只剩一个抽样单位，按设计的方差算不出来——"
            f"多半是把同一列同时写成了分层和抽样单位"])


@dataclass(frozen=True)
class _SE:
    """标准误三路（`_vcov_three_way`）的结果。`run_ols_fe` 与分组描述的 `_fit_cells` 共用：p 值、区间的临界值、
    诊断里「标准误」那一条与三个设计数值键都从这里取，两处不各写一套。"""

    vcov: np.ndarray                      # 跟传进来的 X 同一个刻度（run_ols_fe 传标准化空间的，自己换算回原刻度）
    design: SurveyDesign | None           # 真按抽样设计算了才非空
    info: dict[str, int] | None           # `_design_vcov` 的 {psu, strata, singletons, dof}
    text: str                             # 诊断里「标准误」那一条
    n_clusters: int = 0                   # 这个协方差是拿多少个聚类估的（未聚类时是观测数）。处理前联合检验的小样本校正靠它

    @property
    def dof(self) -> int:
        """按设计时 t 分布的自由度（抽样单位数 − 层数）；0 表示按正态。"""
        return self.info["dof"] if self.info is not None else 0

    def p(self, z: float) -> float:
        """双侧 p。按抽样设计时用 t（自由度 = 抽样单位数 − 层数）：几十个抽样单位时正态会偏乐观。"""
        if self.info is not None:
            from oss.core.causal_stats import t_sf
            return 2 * t_sf(abs(z), self.info["dof"])
        from statistics import NormalDist
        return 2 * (1 - NormalDist().cdf(abs(z)))

    def crit(self) -> float:
        """95% 区间的临界值：按设计用 t 分布的 0.975 分位（自由度 = 抽样单位数 − 层数），否则按正态 1.96（跟图的口径一致）。"""
        if self.info is not None:
            from oss.core.causal_stats import t_ppf
            return t_ppf(0.975, self.info["dof"])
        return 1.96

    def diag(self) -> dict[str, str]:
        """按设计时的三条数值诊断（`_design_diag`）；没按设计算是空的。"""
        return _design_diag(self.info) if self.info is not None else {}


def _vcov_three_way(
    X: np.ndarray, resid: np.ndarray, xtx_inv: np.ndarray, k_absorbed: int,
    df: pd.DataFrame, plan: AnalysisPlan,
) -> _SE:
    """标准误三路：抽样设计 → 聚类 → HC1（`run_ols_fe`、`_fit_cells` 共用）。

    声明了初级抽样单位（且那一列在表里），主规格就按设计算，cluster_level 让位——按它聚类的那版进稳健性
    （`_patched` 让只改聚类层的那一项不带设计）；否则有聚类层级时用聚类稳健，再否则用 HC1 异方差稳健。
    `X`、`resid`、`xtx_inv` 要是同一个刻度；`k_absorbed` 是固定效应吸收掉的自由度，进小样本校正。
    """
    n, k = X.shape
    design = _design_of(plan, df)
    if design is not None:
        strata = df[design.strata].to_numpy() if design.strata and design.strata in df.columns else None
        vcov, info = _design_vcov(X, resid, strata, df[design.psu].to_numpy(), xtx_inv, k_absorbed)
        return _SE(vcov, design, info, _design_se_text(design, strata is not None, info, plan.cluster_level),
                   info["psu"])
    if plan.cluster_level and plan.cluster_level in df.columns:
        n_cl = int(df[plan.cluster_level].nunique())
        vcov = _cluster_robust_vcov(X, resid, df[plan.cluster_level].to_numpy(), xtx_inv, k_absorbed)
        return _SE(vcov, None, None, f"聚类到 {plan.cluster_level}（{n_cl} 个簇）", n_cl)
    # HC1 异方差稳健。**不建 n×n 的 diag(u²)**：2 万行时那一个矩阵就要 3 GB。
    # `(X·u²)'X` 跟 `X' diag(u²) X` 逐位相同；别写成 `(X·u)'(X·u)`——那个在末位上差出 1e-13
    meat = (X * (resid ** 2)[:, None]).T @ X
    # 未聚类时「一个观测就是一个簇」，联合检验的小样本校正照这个数走
    return _SE((n / max(n - k - k_absorbed, 1)) * (xtx_inv @ meat @ xtx_inv), None, None,
               "HC1 异方差稳健（未聚类）", n)


def _varies_within(
    frame: pd.DataFrame | None, col: str, by: list[str]
) -> bool:
    """这一列在固定效应组内有变异吗？用来分辨「筛选造成的」和「本来就没有」。

    **必须逐个维度查，不能把多维固定效应合起来分组。**
    合起来分组的话每格只剩一行，`nunique` 恒为 1，这个函数就永远返回 False。

    多维固定效应下，一个变量要活下来，得在**每一个**维度的组内都有变异——
    在个体内随时间变、同时在同一年份内跨个体也变。任一维度上不变就会被吸收。
    """
    if frame is None or col not in frame.columns:
        return False
    keys = [b for b in by if b in frame.columns]
    if not keys:
        return False
    return all(
        bool((frame.groupby(k, dropna=False)[col].nunique(dropna=True) > 1).any())
        for k in keys
    )


#: 多项式项的常见写法：`age2` `age_2` `agesq` `age_sq` `age_squared` `age^2`
_POLY_SUFFIX = re.compile(r"(?:[_^]?\d+|_?sq(?:uared)?)$", re.I)


def _poly_base(name: str, others: set[str]) -> str | None:
    """`age2` 是不是 `age` 的幂次项？是就返回底数名。"""
    base = _POLY_SUFFIX.sub("", name)
    return base if base and base != name and base in others else None


def _benign_interaction(labels: list[str], bad: list[tuple[str, float]]) -> bool:
    """高 VIF 是不是「派生项与它自己的底」造成的？

    两种情况都算良性，因为都能靠中心化消除、都不影响那个系数本身的可解释性：

    - **交互项与它的分量**（`a*b` 与 `a`、`b`）
    - **多项式项与它的底**（`age2` 与 `age`）

    不区分的话，凡是带交互或带二次项的模型都会拉响警报——
    **而警报天天响就等于没有警报**，真正的共线（比如年龄-时期-队列）就会被忽略。

    多项式那一半是**回归验证时发现的**：GSS v2 主规格报出
    「偏高：age=35、age2=34」，而那只是二次项的常态。
    """
    flagged = {n for n, _ in bad}
    if not flagged:
        return False
    pool = set(labels)
    own: set[str] = set()
    for n in flagged:
        if is_interaction(n):
            own |= {n, *parts_of(n)}
        elif (b := _poly_base(n, pool)) is not None:
            own |= {n, b}
    return bool(own) and flagged <= own


def _collinearity(X: np.ndarray, names: list[str]) -> tuple[str, str, float]:
    """方差膨胀因子。**秩检验只挡精确共线，这里挡的是「差一点点」的那种。**

    真数据里最常见的形态：年龄-时期-队列恒等式 `cohort = year - age`。
    它本该让矩阵降秩，但只要有少数行因生日时点差 1，矩阵就是满秩的——
    `matrix_rank` 放行，回归照跑照出星号，而 `age` 的系数已经不可解读。
    （GSS 真跑时 293 行精确成立、12 行差 1，就足以让检验失效。）

    VIF_j = 1/(1-R²_j)：把第 j 列对其余列回归，拟合得越好说明它越冗余。
    用相关矩阵求逆的对角线一次算完，比逐列跑回归快得多。

    返回 `(诊断文本, 最严重的变量, 最大 VIF)`。
    """
    cols = [(i, n) for i, n in enumerate(names) if n != "_const"]
    if len(cols) < 2:
        return "", "", 1.0
    idx = [i for i, _ in cols]
    labels = [n for _, n in cols]
    sd = X[:, idx].std(axis=0)
    live = sd > 1e-12
    if live.sum() < 2:
        return "", "", 1.0
    Z = X[:, [idx[i] for i in range(len(idx)) if live[i]]]
    labels = [labels[i] for i in range(len(labels)) if live[i]]

    R = np.corrcoef(Z, rowvar=False)
    try:
        vif = np.diag(np.linalg.inv(R))
    except np.linalg.LinAlgError:
        vif = np.diag(np.linalg.pinv(R))
    vif = np.clip(vif, 1.0, None)

    order = np.argsort(-vif)
    worst, worst_v = labels[order[0]], float(vif[order[0]])
    bad = [(labels[i], float(vif[i])) for i in order if vif[i] >= 10]
    if not bad:
        return f"最大 VIF {worst_v:.1f}（{worst}），无共线问题", worst, worst_v

    listed = "、".join(f"{n}={v:.0f}" for n, v in bad[:5])
    if _benign_interaction(labels, bad):
        return (
            f"偏高但**属正常**：{listed}——"
            f"高 VIF 只来自交互项与它自己的分量，这种共线可以靠中心化消除，"
            f"不影响交互系数本身的估计",
            worst, worst_v,
        )
    sev = "**严重**" if worst_v >= 100 else "偏高"
    tail = (
        "——这些系数不可单独解读。最常见的成因是年龄-时期-队列恒等式"
        "（cohort = year − age，三者不能同时线性进入回归）或重复的控制变量"
        if worst_v >= 100 else "——解读这些系数时要谨慎"
    )
    return f"{sev}：{listed}{tail}", worst, worst_v


def vif_table(frame: pd.DataFrame, plan: AnalysisPlan) -> list[tuple[str, float]]:
    """按主计划把回归元准备好，返回每个回归元的 VIF。给诊断图用。

    走的是跟 `run_ols_fe` 同一条准备路径（交互项、筛选、去缺失、去均值），
    否则图上的 VIF 跟结果里的对不上——**两个数不一致比没有数更糟**。
    """
    frame = _expand_interactions(frame, [
        *plan.interactions,
        *([plan.treatment] if is_interaction(plan.treatment) else []),
    ])
    extra = [i for i in plan.interactions if i != plan.treatment]
    cols = [plan.outcome, plan.treatment, *plan.controls, *extra]
    df = frame.query(plan.sample_filter) if plan.sample_filter else frame
    needed = [c for c in {*cols, *plan.fixed_effects, plan.cluster_level} if c]
    df = df[[c for c in needed if c in df.columns]].dropna(subset=[c for c in cols if c])
    regressors = [plan.treatment, *plan.controls, *extra]
    X = df[regressors].to_numpy(dtype=float)
    groups = [df[f].to_numpy() for f in plan.fixed_effects if f in df.columns]
    if groups:
        X = _demean(X, groups)
    else:
        X = np.column_stack([np.ones(len(X)), X])
        regressors = ["_const", *regressors]

    cols_idx = [(i, n) for i, n in enumerate(regressors) if n != "_const"]
    sd = X[:, [i for i, _ in cols_idx]].std(axis=0)
    keep = [(i, n) for (i, n), s_ in zip(cols_idx, sd) if s_ > 1e-12]
    if len(keep) < 2:
        return []
    Z = X[:, [i for i, _ in keep]]
    R = np.corrcoef(Z, rowvar=False)
    try:
        v = np.diag(np.linalg.inv(R))
    except np.linalg.LinAlgError:
        v = np.diag(np.linalg.pinv(R))
    return [(n, float(max(x, 1.0))) for (_, n), x in zip(keep, np.clip(v, 1.0, None))]


def _bin_adjusted(chunk: pd.DataFrame, outcome: str, treatment: str, by: str,
                  controls: tuple[str, ...], fixed_effects: tuple[str, ...], cluster: str,
                  weights: str = "", survey: SurveyDesign | None = None,
                  ) -> tuple[float, float] | None:
    """一箱之内按主规格跑：同样的控制变量、同样的固定效应、同样的聚类、同样的权重与抽样设计。
    跑不动就跳过这一箱。**权重与设计都要带上**：边际效应图拿主规格的 t 临界值（按设计的自由度）
    去乘这里的标准误，箱内不按设计算，那个区间就是拿设计的尺子量了别的东西。"""
    global _NESTED
    ctrl = [c for c in controls if c not in (treatment, by) and not is_interaction(c)]
    # 箱内平均每个固定效应组不到 2 个观测时（按期分箱 + 个体固定效应就是这样：
    # 每个人在这一箱里只有一行），固定效应会吸掉全部变异——这一箱丢掉那个固定效应
    fe = [f for f in fixed_effects if f in chunk.columns and chunk[f].nunique() * 2 <= len(chunk)]
    plan = AnalysisPlan(plan_id="bin", idea_id="bin", estimator="ols_fe", outcome=outcome,
                        treatment=treatment, controls=ctrl, fixed_effects=fe,
                        cluster_level=cluster if cluster in chunk.columns else "",
                        weights=weights, survey=survey)
    _NESTED += 1
    try:
        r = run_ols_fe(chunk, plan)
    except Exception:  # noqa: BLE001 —— 某一箱秩亏 / 样本太小，跳过，不让整张图挂掉
        return None
    finally:
        _NESTED -= 1
    e = r.estimates.get("main")
    if e is None or not np.isfinite(e.coef) or not np.isfinite(e.se) or e.se <= 0:
        return None
    return float(e.coef), float(e.se)


def binned_slopes(
    frame: pd.DataFrame,
    outcome: str,
    treatment: str,
    by: str,
    *,
    split: str = "",
    bins: int = 6,
    controls: tuple[str, ...] | list[str] = (),
    fixed_effects: tuple[str, ...] | list[str] = (),
    cluster: str = "",
    weights: str = "",
    survey: SurveyDesign | None = None,
) -> list[tuple[str, list[tuple[float, float, float, int]]]]:
    """分箱估计 `outcome ~ treatment` 的斜率，看它随 `by` 怎么变。

    **这是用来验证交互项的函数形式的。**写一个 `背景 × 队列` 的交互项，
    等于假定「斜率随队列**线性**变化」——而这个假定几乎从来没人验证过。
    GSS 那篇论文就是这样：假定线性衰减，从没验过是平滑下滑、断崖、还是非单调。

    给了 `controls` / `fixed_effects` 就**各箱内按主规格跑**（同样的控制、固定效应、聚类）。
    评审第十次验收跑：「分箱斜率不含控制变量和固定效应，然后拿它去否证主模型的线性假设——
    这是拿一个双变量截面相关去质疑一个双向固定效应加控制变量的估计，两者根本不是同一个量。」
    分箱要跟主规格比，就得用同一个量。不给就退回二元回归（看原始形状，图用）。

    给了 `weights` 就加权：按主规格跑的那条路把权重与抽样设计（`survey`）带进每一箱的回归；
    二元回归那条路按 √w 缩放后再做最小二乘（加权最小二乘的精确等价）。

    返回 `[(分组名, [(箱中心, 斜率, 标准误, n), ...]), ...]`。
    """
    controls = tuple(c for c in controls if c)
    fixed_effects = tuple(c for c in fixed_effects if c)
    adjusted = bool(controls or fixed_effects)
    core = [c for c in (outcome, treatment, by, split) if c]
    if weights and weights not in frame.columns:
        raise PlanRejected([f"权重列 `{weights}` 不在数据里"])
    design_cols = [c for c in ((survey.psu, survey.strata) if survey is not None else ()) if c]
    need = list(dict.fromkeys([*core, *controls, *fixed_effects, *([cluster] if cluster else []),
                               *([weights] if weights else []), *design_cols]))
    need = [c for c in need if c in frame.columns and not is_interaction(c)]
    df = frame[need]
    if weights:
        df = df.assign(**{weights: _positive_weights(df, weights)})
    df = df.dropna(subset=[c for c in (*core, weights) if c and c in need])
    if df.empty:
        return []

    uniq = df[by].nunique()
    if uniq <= bins:
        edges = sorted(df[by].unique())
        labels = pd.Series(df[by].values, index=df.index)
        centers = {v: float(v) for v in edges}
    else:
        q = pd.qcut(df[by], bins, duplicates="drop")
        labels = q
        centers = {iv: float((iv.left + iv.right) / 2) for iv in q.cat.categories}

    groups = df[split].unique() if split else [None]
    out = []
    for g in sorted(groups, key=lambda x: (x is None, x)):
        sub = df if g is None else df[df[split] == g]
        sub_labels = labels.loc[sub.index]
        pts = []
        for key, chunk in sub.groupby(sub_labels, observed=True):
            if len(chunk) < 10 or chunk[treatment].std() < 1e-12:
                continue
            if adjusted:
                got = _bin_adjusted(chunk, outcome, treatment, by, controls, fixed_effects, cluster,
                                    weights, survey)
                if got is None:
                    continue
                slope, se = got
                pts.append((centers.get(key, float(len(pts))), slope, se, len(chunk)))
                continue
            x = chunk[treatment].to_numpy(float)
            y = chunk[outcome].to_numpy(float)
            X = np.column_stack([np.ones(len(x)), x])
            if weights:                    # 加权最小二乘 = 对 √w 缩放后的数据做最小二乘
                rt = np.sqrt(chunk[weights].to_numpy(float))
                X, y = X * rt[:, None], y * rt
            beta, *_ = np.linalg.lstsq(X, y, rcond=None)
            resid = y - X @ beta
            dof = max(len(x) - 2, 1)
            xtx_inv = np.linalg.inv(X.T @ X)
            se = float(np.sqrt((resid @ resid) / dof * xtx_inv[1, 1]))
            pts.append((centers.get(key, float(len(pts))), float(beta[1]), se, len(chunk)))
        if pts:
            out.append((f"{split}={g}" if g is not None else "全样本", sorted(pts)))
    return out


def _attrition(df: pd.DataFrame, plan: AnalysisPlan) -> str | None:
    """面板流失：有多少人答满了全部轮次。

    **「因缺失丢弃 51.8%」看不出这 51.8% 是什么。**
    整个人不见了（流失）和这个人某一轮没答某道题（条目缺失）含义完全不同：
    **流失往往不是随机的**——身体变差的人更可能退出，而那正是研究的对象。

    只在两维固定效应（个体 + 时间）时算，因为只有那时「个体」才有意义。
    """
    if len(plan.fixed_effects) < 2:
        return None
    unit, time = plan.fixed_effects[0], plan.fixed_effects[1]
    if unit not in df.columns or time not in df.columns:
        return None
    per = df.groupby(unit)[time].nunique()
    total_waves = int(df[time].nunique())
    if total_waves < 2 or len(per) < 10:
        return None
    full = int((per == total_waves).sum())
    share = full / len(per)
    head = ""
    if share < 0.7:
        head = ("**流失明显**——" if share < 0.5 else "流失偏多——")
    # 用列名说「单位」和「期」，不说「人」和「轮」——国家面板里写成
    # 「107 人答满了 61 轮」，验证跑的诊断原话就是这样，读者会以为是调查数据
    return (
        f"{head}{len(per)} 个「{unit}」单位进入回归，其中 {full} 个（{share:.0%}）在全部 "
        f"{total_waves} 个「{time}」期都有观测。**流失往往不是随机的**"
        f"（状况变差的单位更容易退出），若与研究对象相关会造成选择性偏误"
    )


def _col_scale(X: np.ndarray) -> np.ndarray:
    """每列的量纲尺度。**用列范数，不用标准差。**

    第一版用的是标准差，`sd <= 0` 时换成 1。**那个保护条件不够**：
    两阶段最小二乘投影之后，常数列的标准差是 `4.7e-17`——不是 0，
    只是接近 0，于是它被**除以 4.7e-17、放大到 1e16**，
    条件数从 3 变成 8.1e32，秩判成 1/3。

    **一个用来修数值灾难的改动，自己制造了同一类灾难。**
    是已有的借力变量法测试抓到的。

    列范数没有这个问题：常数列的范数是 `|c|·√n`，本身就是个健康的数；
    居中列的范数约等于 `sd·√(n−1)`。两种情形一个公式覆盖。
    """
    nrm = np.linalg.norm(X, axis=0)
    nrm[~np.isfinite(nrm) | (nrm <= 0)] = 1.0
    return nrm


def _scaled(X: np.ndarray) -> np.ndarray:
    """每列除以自己的量纲尺度。**只改刻度，不改任何变量之间的关系。**"""
    return X / _col_scale(X)


def _full_rank(X: np.ndarray) -> bool:
    """秩检验**要在标准化之后做**。

    `matrix_rank` 的容差是按矩阵最大奇异值定的。真数据上量过一次：
    `pop` 从九千跨到十四亿，`X'X` 的元素因此跨十八个数量级，
    条件数 7.28e15——**容差直接吞掉两个真实维度，满秩的矩阵被判成 3/5**。

    然后系统去跑归因，报出「某某与某某完全共线」——**点的是无辜的变量**。
    使用者会照着提示删掉一个本该留下的控制变量。
    「点名了却归错因，比不说更糟」这条纪律，在这里被自己的容差破坏了。

    标准化之后同一份数据条件数 5.4、秩 5/5。**秩不随刻度改变**，
    所以这么判没有任何代价：数学上等价，数值上稳得多。
    """
    Xs = _scaled(X)
    return bool(np.linalg.matrix_rank(Xs.T @ Xs) >= X.shape[1])


def _rank_diagnosis(
    X: np.ndarray,
    names: list[str],
    plan: AnalysisPlan,
    raw: pd.DataFrame | None = None,
    unfiltered: pd.DataFrame | None = None,
) -> list[str]:
    """回归矩阵不满秩时，点名是哪个变量出了问题**并且说对是为什么**。

    「不满秩」没法行动。但**点名了却归错因，比不说更糟**——
    使用者会照着提示去动固定效应，而该动的是样本筛选。

    所以要分清**三种**全零，它们要改的地方各不相同：

    ========================================  ================================
    筛选后全局就只剩一个值                      改筛选条件，跟固定效应无关
    筛选前组内有变异、筛选后没有                **筛选经由固定效应造成的**
    不管筛不筛，组内本来就没变异                真的是固定效应吸收掉了
    ========================================  ================================

    中间那一种最容易被说错。GSS 真跑时踩到的是第一种（`female == 1` 让它全局恒定），
    被报成了第三种；而 `tiny.csv` 上的 `year >= 2018` 是第二种。
    （PLAN.md 九之一 ⑨）

    GSS 真跑时踩到：`sample_filter="female == 1"` 让 `female` 变成常数，
    却被报成「被固定效应（region）吃光了」。（PLAN.md 九之一 ⑨）
    """
    problems: list[str] = []
    fe = "、".join(plan.fixed_effects)

    for i, name in enumerate(names):
        if name == "_const":
            continue
        col = X[:, i]
        if not np.allclose(col, 0, atol=1e-10):
            continue

        # 去均值之前就已经是常数吗？是的话，锅不在固定效应
        const_before = (
            raw is not None and name in raw.columns and raw[name].nunique(dropna=True) <= 1
        )
        if const_before:
            if plan.sample_filter:
                why = (
                    f"在样本筛选 `{plan.sample_filter}` 之后它只剩一个取值。"
                    f"**这跟固定效应无关**——要改的是筛选条件，"
                    f"或者把「{name}」从回归里去掉"
                )
            else:
                why = f"它在整个样本里取值恒定，进不了回归"
        elif plan.fixed_effects and plan.sample_filter and _varies_within(
            unfiltered, name, plan.fixed_effects
        ):
            why = (
                f"样本筛选 `{plan.sample_filter}` 之后，它在每个固定效应组（{fe}）内"
                f"都不再变化，于是被完全吸收。**筛选之前它是有组内变异的**——"
                f"要改的是筛选条件"
            )
        elif plan.fixed_effects:
            why = f"被固定效应（{fe}）完全吸收——它在每个组内本来就不变化"
        else:
            why = "去均值后无变异"
        problems.append(f"「{name}」在回归里没有任何变异——{why}")

    # 两两完全相关的
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = X[:, i], X[:, j]
            if np.std(a) > 1e-12 and np.std(b) > 1e-12:
                if abs(np.corrcoef(a, b)[0, 1]) > 1 - 1e-9:
                    problems.append(f"「{names[i]}」与「{names[j]}」完全共线")

    return problems or [
        "回归矩阵不满秩，但查不出具体是哪个变量——检查变量之间有没有线性依赖"
    ]


def _run_binned(frame: pd.DataFrame, plan: AnalysisPlan) -> dict[str, list[BinnedSlope]]:
    """把分箱估计**落进产出物**，不只是画进图里。

    交互项 `a × b` 假定「a 的斜率随 b 线性变化」。分箱估计是这个假定的实际形状，
    而论文一旦拿它做事实断言（「最晚一箱两组已无实质差别」），
    读者就必须能查到每一箱的点估计、标准误和样本量。

    **图里有、文件里没有，等于「相信我」。**
    """
    spec = plan.binned
    if spec is None or not spec.by:
        return {}
    treatment = spec.treatment or plan.treatment
    if not spec.treatment and is_interaction(treatment):
        # 缺省是「用主处理变量」。而主处理变量在交互设定里**是一个乘积**——
        # 算「乘积的斜率」在实质上没有意义，可它在数值上算得出来，
        # 于是就出了数、进了图、写进论文，被称作「民主的斜率」。
        #
        # 真跑一次时正是这样：初稿把 4.144/0.238/0.070 写成民主的斜率，
        # 补上 treatment 重跑才拿到 7.700/6.190/3.059/2.831/2.404。
        # **是审稿模型抓到的，不是这里拦下的。**
        parts = "、".join(f"`{p}`" for p in treatment.split(INTERACTION_SEP))
        raise PlanRejected([
            f"`binned.treatment` 留空时用主处理变量，而主处理变量 `{treatment}` "
            f"**是一个交互项（乘积）**——「乘积的分箱斜率」算得出来但没有实质含义，"
            f"而它长得跟真的一样，会被当成某个变量的斜率写进论文。\n"
            f"  在 `binned` 里写明要对哪个分量算斜率：{parts}"
        ])
    df = _expand_interactions(frame, [
        *plan.interactions,
        *([treatment] if is_interaction(treatment) else []),
    ])
    if plan.sample_filter:
        df = df.query(plan.sample_filter)
    out: dict[str, list[BinnedSlope]] = {}
    for name, pts in binned_slopes(
        df, plan.outcome, treatment, spec.by, split=spec.split, bins=spec.bins,
        controls=() if spec.bivariate else tuple(plan.controls),
        fixed_effects=() if spec.bivariate else tuple(plan.fixed_effects),
        cluster="" if spec.bivariate else plan.cluster_level,
        weights=plan.weights,
        survey=None if spec.bivariate else plan.survey,
    ):
        out[name] = [
            BinnedSlope(group=name, bin_index=i, center=c, slope=b, se=se, n=n)
            for i, (c, b, se, n) in enumerate(pts, start=1)
        ]
    return out


def _patched(plan: AnalysisPlan, spec: RobustnessSpec) -> AnalysisPlan:
    """把稳健性补丁盖到主计划上。`None` 的字段沿用主计划。

    主计划的 `survey` 跟着抄过来，只有一个例外：补丁**明写了** `cluster_level`，而它跟初级抽样
    单位不同。主规格按抽样设计时，`validate_plan` 的警告与说明书都叫你把「按 cluster_level
    聚类的那版」锁进稳健性——带着设计，`run_ols_fe` 会先按设计算，那一项就悄悄不是按它聚类的。
    所以那一项去掉 `survey`，权重照旧；没写 `cluster_level` 的（比如「不加权」那项）照旧按设计。
    """
    data = plan.model_dump()
    for f in ("outcome", "treatment", "controls", "fixed_effects",
              "cluster_level", "weights", "sample_filter", "interactions"):
        v = getattr(spec, f)
        if v is not None:
            data[f] = v
    if (spec.cluster_level is not None and plan.survey is not None and plan.survey.psu
            and plan.survey.psu != spec.cluster_level):
        data["survey"] = None
    data["plan_id"] = spec.label
    data["robustness"] = []          # 稳健性不套娃
    data["binned"] = None            # 分箱只对主规格算，不对每个稳健性重算
    return AnalysisPlan(**data)


def _run_robustness(
    frame: pd.DataFrame, plan: AnalysisPlan
) -> dict[str, RobustnessRun]:
    """把锁定的稳健性清单**跟主规格同一次**全部跑掉。

    分开跑的话 `results.json` 会被后一次覆盖，论文里就报不出具体数值——
    GSS 真跑时七项稳健性只能用「同号同显著」一句话带过，就是这么来的。

    **跑不动也要留下来。**一项稳健性跑不出来是信息，不是可以忽略的意外：
    静默跳过的话，读者分不出「这项没做」和「做了但结果不好看」。
    """
    global _NESTED
    out: dict[str, RobustnessRun] = {}
    _NESTED += 1
    try:
        for spec in plan.robustness:
            if not spec.executable:
                out[spec.label] = RobustnessRun(
                    label=spec.label,
                    error="散文式条目，跑不了——写成规格补丁才能执行与核对",
                )
                continue
            try:
                # **按主计划的估计量跑**，不是一律 OLS。验收跑（event_study）的七项
                # 稳健性全跑在了 OLS 下——论文只能写「事件研究路径没有自己的稳健性检验」。
                # 补丁计划的 robustness 已清空，所以这里不会递归。
                patched = _patched(plan, spec)
                r = run_plan(frame, patched)
                out[spec.label] = RobustnessRun(
                    label=spec.label, estimates=r.estimates, n=r.n, diagnostics=r.diagnostics,
                    # 分组的格照存：这一项就是按主计划的分组跑的，扔掉它，读者只剩一个合并值
                    groups=r.groups,
                    # 换了结局的不能跟主结果同轴比较（单位都不同），森林图靠这一格分辨
                    outcome=patched.outcome if patched.outcome != plan.outcome else "",
                    # 森林图逐行取这一项自己的 t 临界值：换了样本的抽样单位少，去掉设计的按正态
                    t_crit=_t_crit_of(r.diagnostics),
                )
            except Exception as exc:  # noqa: BLE001 —— 任何失败都要如实留下
                out[spec.label] = RobustnessRun(label=spec.label, error=str(exc))
    finally:
        _NESTED -= 1
    return out


#: 大于 0 表示正在稳健性 / 分批诊断的**内层**调用里。
#: 伴随诊断（逐年展开、批次异质性）只对最外层的主规格算一次，不套娃。
#: 原先靠 `if not plan.robustness` 判「内层」，判反了：主规格带着稳健性清单，
#: 所以**主规格从来算不到批次异质性**，倒是每一项稳健性都各算一遍——
#: 验证跑里执行者写「工具没有产出批次异质性诊断」，就是这么来的。
_NESTED = 0


def _attach_event_study(frame: pd.DataFrame, plan: AnalysisPlan, res: AnalysisResults) -> None:
    """ols_fe / did 计划写了 unit / time / treat_time，就自动附带一次逐年展开。

    验证跑里主规格是带交互项的 ols_fe，事件研究只能另开一个运行目录跑——
    于是图要跨目录复制、因果审计的 FAIL 留在另一个目录里，
    主目录的审计门看不见它，判了「none」。**一个题目一个目录**，
    伴随的逐年展开就该进同一份 results.json。

    伴随规格 = 主规格去掉交互项，处理按 treat_time 展开；控制变量与样本筛选照旧。
    它是诊断，不是主结果：`main` 仍是主规格的。
    """
    es_plan = AnalysisPlan(**{
        **plan.model_dump(),
        "estimator": "event_study", "treatment": "",
        "interactions": [], "robustness": [], "binned": None,
    })
    # 伴随的这一份跟主规格同一口径：双向固定效应的事件研究与 Callaway–Sant'Anna 都吃权重。
    # 后者要单位层面的权重，单位内会变时它**不加权照跑**并写明——不退回 TWFE
    # 分批实施时伴随的事件研究用 Callaway–Sant'Anna，不用 TWFE——
    # 验收跑的 17 个批次 TWFE 事件研究正是评审第一条致命问题；主规格保留交互项，
    # 伴随的这一份换成对的估计量，各批效应和成员名单一起进结果。
    fallback = ""
    n_cohorts = int(pd.to_numeric(frame[plan.treat_time], errors="coerce").nunique()) \
        if plan.treat_time in frame.columns else 0
    if n_cohorts > 1:
        from oss.core import staggered
        if staggered.available():
            cs_plan = AnalysisPlan(**{**es_plan.model_dump(), "estimator": "did_cs"})
            cs_note = ""
            try:
                try:
                    cs = staggered.run_cs(frame, cs_plan)
                except WeightsVaryWithinUnit:
                    # 逐期（逐轮）的权重在单位内会变，StatsPAI 吃不了。退回 TWFE 事件研究正是分批实施下最该避免的，
                    # 所以伴随的这一份**不加权照跑** Callaway–Sant'Anna，并明说
                    cs_plan = AnalysisPlan(**{**cs_plan.model_dump(), "weights": ""})
                    cs = staggered.run_cs(frame, cs_plan)
                    cs_note = (
                        f"**未加权**（主规格按 `{plan.weights}` 加权，但 `{plan.weights}` 在单位内会变——分批时伴随的"
                        f"事件研究用 Callaway–Sant'Anna，它只吃单位层面的权重；两者口径不同，放在一起比要记住。"
                        f"在 prep.py 里取基期的权重造一列单位层面的权重，伴随估计就能跟主规格同样加权）。")
            except Exception as exc:  # noqa: BLE001
                fallback = f"（Callaway–Sant'Anna 伴随估计算不出来：{_why(exc)}，退回 TWFE）"
            else:
                res.event_study = cs.event_study
                for key in ("平行趋势", "对照组", "处理前平衡", "批次异质性", "批次成员",
                            "识别假设", "算不了的格子", "预检功效", "预检功效说明",
                            "breakdown 值", "breakdown 说明"):
                    if key in cs.diagnostics:
                        res.diagnostics.setdefault(key, cs.diagnostics[key])
                for key, e in cs.estimates.items():
                    if key.startswith("cohort_"):
                        res.estimates[key] = e
                m = cs.estimates["main"]
                res.diagnostics["逐年展开"] = (
                    f"{cs_note}伴随主规格自动跑的**分批**事件研究（Callaway–Sant'Anna，{n_cohorts} 批）："
                    f"处理时点「{plan.treat_time}」，不含交互项，前 {plan.leads} 期 / 后 {plan.lags} 期，"
                    f"n={cs.n}；处理后平均效应 {m.coef:+.3f}（{m.se:.3f}，p {m.p:.3f}）；"
                    f"各批效应见 estimates.cohort_<年>（论文用 {{{{table:cohorts}}}}）。"
                    f"**处理前各期是判断这套设计能不能信的依据**，画图：oss figure event")
                # 各批效应表、逐期表与逐期图照伴随估计自己的口径：StatsPAI 按抽样单位聚类（分层不进）、区间按正态
                res.companion_spec = _spec_summary(cs_plan)
                if "抽样单位数" not in cs.diagnostics:
                    res.companion_spec.psu = res.companion_spec.strata = ""
                return
        else:
            fallback = f"（{n_cohorts} 批分批实施，应当用 Callaway–Sant'Anna，但没装 StatsPAI：{staggered.INSTALL_HINT}；退回 TWFE）"
    try:
        es = run_event_study(frame, es_plan)
    except Exception as exc:  # noqa: BLE001 —— 伴随诊断算不出来不该让主结果挂掉
        res.diagnostics["逐年展开"] = f"算不出来（{_why(exc)}）{fallback}"
        return
    res.event_study = es.event_study
    res.pretrend_cov = es.pretrend_cov
    for key in ("平行趋势", "对照组", "处理前平衡", "预检功效", "预检功效说明", "breakdown 值", "breakdown 说明"):
        if key in es.diagnostics:
            res.diagnostics.setdefault(key, es.diagnostics[key])
    # 分批铺开的处理还要看各批效应差多少——验证跑的论文只能写
    # 「The analysis tooling did not return a cohort-heterogeneity diagnostic」。
    # 用同一个去交互项的规格按 did 跑分批诊断；算不出来就说算不出来。
    if "批次异质性" not in res.diagnostics:
        try:
            did_plan = AnalysisPlan(**{**es_plan.model_dump(), "estimator": "did"})
            res.diagnostics["批次异质性"] = _cohort_heterogeneity(frame, did_plan)
        except Exception as exc:  # noqa: BLE001
            res.diagnostics["批次异质性"] = f"算不出来（{type(exc).__name__}: {exc}）"
    m = es.estimates.get("main")
    pooled = f"；处理后合并效应 {m.coef:+.3f}（{m.se:.3f}，p {m.p:.3f}）" if m else ""
    res.diagnostics["逐年展开"] = (
        f"伴随主规格自动跑的事件研究：处理时点「{plan.treat_time}」，"
        f"不含交互项，前 {plan.leads} 期 / 后 {plan.lags} 期，n={es.n}{pooled}{fallback}。"
        f"**处理前各期是判断这套设计能不能信的依据**，画图：oss figure event"
    )


def _treat_cols(frame: pd.DataFrame, plan: AnalysisPlan) -> pd.DataFrame:
    """按 `treat_time` 造出「此刻是否已被处理」和「相对处理的期数」两列。

    用「每个单位何时开始被处理」而不是「处理组×后期」，是因为
    **现实里的政策几乎都是分批铺开的**——有的省 2010 年改、有的 2014 年改。
    写成两个二值变量表达不了分批。
    """
    df = frame.copy()
    t0 = pd.to_numeric(df[plan.treat_time], errors="coerce")
    t = pd.to_numeric(df[plan.time], errors="coerce")
    df["_treated_now"] = ((t0.notna()) & (t >= t0)).astype(float)
    df["_k"] = (t - t0)
    return df


def _inner_cluster(plan: AnalysisPlan) -> str:
    """did / event_study 转交 `run_ols_fe` 时的聚类层。

    没声明设计时缺省按单位聚类；声明了初级抽样单位，主规格按抽样设计算，**不替用户补 unit**——
    补了 `run_ols_fe` 就报「cluster_level（unit）没用上」，可用户根本没写。用户自己写的照旧传下去。
    """
    if plan.survey is not None and plan.survey.psu:
        return plan.cluster_level
    return plan.cluster_level or plan.unit


def run_did(frame: pd.DataFrame, plan: AnalysisPlan) -> AnalysisResults:
    """政策冲击对比法（双向固定效应形式的双重差分）。

    比较「已被处理」与「尚未／从未被处理」的差距，同时吸收单位与时间的固定效应。

    **它有一个有名的坑**：政策分批铺开、且各批效应不同时，
    这个做法会把「早处理组」当成「晚处理组」的对照，
    算出来的东西可能连符号都不对。`validate_plan` 会就此警告——
    **工具不该假装没这回事**。要真正解决得换更新的估计量，那是后话。
    """
    df = _treat_cols(frame, plan)
    inner = AnalysisPlan(
        plan_id=plan.plan_id, idea_id=plan.idea_id, estimator="ols_fe",
        outcome=plan.outcome, treatment="_treated_now",
        controls=list(plan.controls),
        fixed_effects=[plan.unit, plan.time],
        cluster_level=_inner_cluster(plan),
        sample_filter=plan.sample_filter,
        weights=plan.weights,
        survey=plan.survey,
        interactions=list(plan.interactions),
    )
    res = run_ols_fe(df, inner)
    res.plan_id = plan.plan_id
    res.diagnostics["估计量"] = "双重差分（单位与时间双向固定效应）"
    res.diagnostics["处理定义"] = (
        f"「{plan.treat_time}」给出每个单位开始被处理的时间；缺失视为从未处理"
    )
    n_cohorts = int(pd.to_numeric(df[plan.treat_time], errors="coerce").nunique())
    res.diagnostics["实施批次"] = f"{n_cohorts} 批"
    res.diagnostics["对照组"] = _control_group(df, plan)
    res.diagnostics["处理前平衡"] = _pre_balance(df, plan)
    if _NESTED == 0:             # 只对最外层主规格算；分批估计内部会递归调用，别让它套娃
        res.diagnostics["批次异质性"] = _cohort_heterogeneity(frame, plan)
        if n_cohorts > 1:
            # 分批时 TWFE 的系数是哪些两两比较拼出来的——「为什么要换估计量」的证据
            from oss.core import staggered
            res.diagnostics["Bacon 分解"] = staggered.bacon_text(frame, plan)
    res.robustness = _run_robustness(frame, plan)
    return res


def _control_group(df: pd.DataFrame, plan: AnalysisPlan) -> str:
    """对照组是谁。**「跟谁比」是这套方法的第一个问题。**

    有「从未被处理」的单位时，它们是最干净的对照。
    全都最终被处理时，比较完全依赖各批次的早晚之差——
    **而那正是分批偏误最重的场合**。
    """
    # 按估计样本算，不按全文件算。验收跑的论文不得不写一段解释
    # 「诊断说 159 个从未处理国，估计样本其实是 66 个」——诊断说的和回归用的不是一批
    if plan.sample_filter:
        df = df.query(plan.sample_filter)
    per_unit = df.groupby(plan.unit)[plan.treat_time].first()
    never = int(per_unit.isna().sum())
    ever = int(per_unit.notna().sum())
    if never == 0:
        return (
            f"**没有从未被处理的单位**（{ever} 个单位全部最终被处理）。"
            f"比较完全依赖各批次的早晚之差，而那正是分批偏误最重的场合——"
            f"**估计量可能有偏，结论要谨慎**"
        )
    return f"{never} 个单位从未被处理（可作干净对照），{ever} 个单位在某个时点被处理"


def _pre_balance(df: pd.DataFrame, plan: AnalysisPlan) -> str:
    """处理组与对照组在**处理之前**可不可比。

    两组本来就是两种单位的话，「效应」里混着它们的固有差异。
    判据是标准化差值 `(处理组均值 − 对照组均值) / 合并标准差`，
    经验上超过 0.25 算明显不平衡。

    **注意这不是平行趋势检验的替代**：平行趋势看的是「走势」平不平，
    这里看的是「水平」差不差。两组水平差很多但走势平行时，双重差分仍然成立；
    但水平差太多往往意味着两组根本不可比，值得看一眼。
    """
    cols = [c for c in plan.controls if c in df.columns and not is_interaction(c)]
    if not cols:
        return "没有控制变量，平衡性无从比较"
    if plan.sample_filter:            # 同上：只看估计样本
        df = df.query(plan.sample_filter)
    pre = df[(df["_treated_now"] == 0)]
    treated_ever = df.groupby(plan.unit)[plan.treat_time].first().notna()
    pre = pre.assign(_ever=pre[plan.unit].map(treated_ever))
    bits, bad = [], []
    for c in cols:
        a = pd.to_numeric(pre.loc[pre["_ever"], c], errors="coerce").dropna()
        b = pd.to_numeric(pre.loc[~pre["_ever"].astype(bool), c], errors="coerce").dropna()
        if len(a) < 2 or len(b) < 2:
            continue
        sd = np.sqrt((a.var(ddof=1) + b.var(ddof=1)) / 2)
        smd = float((a.mean() - b.mean()) / sd) if sd > 0 else 0.0
        bits.append(f"{c} 标准化差值 {smd:+.3f}")
        if abs(smd) > 0.25:
            bad.append(c)
    if not bits:
        return "样本不足，平衡性算不了"
    head = (f"**不平衡**（{'、'.join(bad)} 超过 0.25）——两组处理前本来就不像，"
            f"「效应」里可能混着固有差异；" if bad else "各控制变量均衡（标准化差值均在 0.25 以内）；")
    return head + "；".join(bits)


def _cohort_heterogeneity(frame: pd.DataFrame, plan: AnalysisPlan) -> str:
    """**分批偏误是理论担忧；各批效应差多少是实证征兆。**

    只警告「可能有偏」而不给征兆，使用者无从判断这个担忧在自己数据上重不重。
    这里对每一批单独跑一次（只用该批 + 从未被处理的单位），报出各自的效应。
    """
    df = _treat_cols(frame, plan)
    per_unit = df.groupby(plan.unit)[plan.treat_time].first()
    never_units = set(per_unit[per_unit.isna()].index)
    cohorts = sorted(per_unit.dropna().unique())
    if len(cohorts) < 2:
        return "只有一批，不存在分批偏误"
    if not never_units:
        return "没有从未被处理的单位，分批估计做不了——**这本身就是分批偏误最重的情形**"

    global _NESTED
    got: list[tuple[float, float, float, int]] = []
    _NESTED += 1
    try:
        for c in cohorts:
            members = set(per_unit[per_unit == c].index)
            units = members | never_units
            sub = frame[frame[plan.unit].isin(units)]
            try:
                r = run_did(sub, AnalysisPlan(**{**plan.model_dump(), "robustness": []}))
                e = r.estimates["main"]
                if e.se > 0:
                    got.append((float(c), e.coef, e.se, len(members)))
            except Exception:  # noqa: BLE001 —— 某一批跑不动不该让整个诊断挂掉
                continue
    finally:
        _NESTED -= 1
    if len(got) < 2:
        return "分批估计跑不出来"
    return _heterogeneity_text(got)


def _heterogeneity_text(got: list[tuple[float, float, float, int]]) -> str:
    """各批效应 `(批, 效应, 标准误, 单位数)` → 「差异大不大」的判词。TWFE 分批诊断和
    Callaway–Sant'Anna 的按批 ATT 用同一把尺子。"""
    # **判据必须把各批的精度算进去。**只看散布的话，
    # 「批次多而小」的数据会无脑报警——QoG 那份有 28 个批次、其中 17 个只有一个国家，
    # 一两个单位算出来的效应标准误极大，散布全是噪声。
    # 用元分析里的 Cochran Q / I²：问「散布是不是超过了纯抽样噪声能解释的范围」。
    w = np.array([1.0 / (se ** 2) for _, _, se, _ in got])
    b = np.array([v for _, v, _, _ in got])
    mean = float((w * b).sum() / w.sum())
    q = float((w * (b - mean) ** 2).sum())
    dof = len(got) - 1
    i2 = max(0.0, (q - dof) / q) if q > 0 else 0.0

    shown = "；".join(f"{c:.0f} 批 {v:+.3f}（{se:.3f}，{n} 个单位）"
                      for c, v, se, n in got[:8])
    more = f"……共 {len(got)} 批" if len(got) > 8 else ""
    tail = f"加权平均 {mean:+.3f}。{shown}{more}"

    # **批次太少时 I² 本身不可靠**（元分析里通常要 5–10 个才谈得上）。
    # 干净夹具上只有 2 批、真实效应相同，却被判成「差异大」——
    # 又是一次「判不了却给了判词」。判不了就说判不了。
    if len(got) < 4:
        return (f"只有 {len(got)} 批，**异质性判不可靠**——"
                f"批次太少时这个指标本身不稳定，既不能说有也不能说没有。"
                f"请直接看下面各批的估计值自行判断。{tail}")

    if i2 > 0.5:
        return (f"**各批效应差异大**（I² = {i2:.0%}，超出抽样噪声能解释的范围）——"
                f"分批偏误不只是理论担忧，在这份数据上是实际存在的。"
                f"汇总的那个系数**不可直接当作平均效应**。{tail}")
    return (f"各批效应的散布在抽样**噪声**范围内（I² = {i2:.0%}），"
            f"分批偏误在这份数据上不严重。{tail}")


def run_event_study(frame: pd.DataFrame, plan: AnalysisPlan) -> AnalysisResults:
    """逐年展开：分别估计相对处理时点每一期的效应。

    **处理前那几期是这套方法的命根子。**政策实施之前两组本该没有差异；
    处理前就已经在分化的话，双重差分会把那个趋势当成效应，
    **算出来的「效应」是假的，而表格上看不出来、图上一眼可见**。

    第 −1 期作为参照被略去（所有效应都相对于它衡量）。
    """
    df = _treat_cols(frame, plan)
    ks = [k for k in range(-plan.leads, plan.lags + 1) if k != -1]
    cols: list[str] = []
    for k in ks:
        name = f"_k{'m' if k < 0 else 'p'}{abs(k)}"
        df[name] = (df["_k"] == k).astype(float)
        cols.append(name)
    # 窗口之外的已处理期也要控制住，否则它们会被算进参照组
    df["_k_far"] = ((df["_k"].notna()) & (~df["_k"].isin(ks)) & (df["_k"] != -1)).astype(float)

    inner = AnalysisPlan(
        plan_id=plan.plan_id, idea_id=plan.idea_id, estimator="ols_fe",
        outcome=plan.outcome, treatment=cols[0],
        controls=[*cols[1:], "_k_far", *plan.controls],
        fixed_effects=[plan.unit, plan.time],
        cluster_level=_inner_cluster(plan),
        sample_filter=plan.sample_filter,
        weights=plan.weights,
        survey=plan.survey,
    )
    res = run_ols_fe(df, inner)
    res.plan_id = plan.plan_id

    by_name = {e.term: e for e in res.estimates.values()}
    points = []
    for k, name in zip(ks, cols):
        e = by_name.get(name)
        if e is None:
            continue
        points.append(EventStudyPoint(
            k=k, coef=e.coef, se=e.se, n=int((df[name] == 1).sum())))
    res.event_study = sorted(points, key=lambda p: p.k)

    from oss.core import causal_checks as cc
    res.pretrend_cov = cc.pretrend_cov(res._fit, [p.k for p in res.event_study if p.k < 0])

    # **`main` 必须是一个效应，不能是随便哪个系数。**
    # 上面那次拟合把第一个事件期哑变量（k = −leads，**处理前最远的那一期**）
    # 当了 treatment，于是 `estimates["main"]` 存的是处理前第 −5 期的系数。
    # 真跑时执行者自己抓到的：console 打出的「R3 … 0.1179」是各规格的
    # 处理前系数，照抄进论文就把处理前趋势写成了效应；`{{robust:…/main.coef}}`
    # 和稳健性森林图引的也是它。
    #
    # 事件研究的「主效应」按惯例是**处理后各期的合并效应**：
    # 同一设计，把 k ≥ 0 的哑变量换成一个 post 指示，处理前各期与窗外照旧。
    # 这样 main 有自己的标准误（合并系数直接估，不用拼协方差）。
    df["_post"] = (df["_k"].notna() & (df["_k"] >= 0) & df["_k"].isin(ks)).astype(float)
    lead_cols = [c for c, k in zip(cols, ks) if k < 0]
    pooled = AnalysisPlan(
        plan_id=plan.plan_id, idea_id=plan.idea_id, estimator="ols_fe",
        outcome=plan.outcome, treatment="_post",
        controls=[*lead_cols, "_k_far", *plan.controls],
        fixed_effects=[plan.unit, plan.time],
        cluster_level=_inner_cluster(plan),
        sample_filter=plan.sample_filter,
        weights=plan.weights,
        survey=plan.survey,
    )
    try:
        pooled_res = run_ols_fe(df, pooled)
        first_lead = res.estimates.pop("main", None)
        if first_lead is not None:
            res.estimates[first_lead.term] = first_lead     # 第 −leads 期系数保留在自己的名下
        m = pooled_res.estimates["main"]
        res.estimates = {"main": Estimate(
            term=f"post（第 0–{plan.lags} 期合并）", coef=m.coef, se=m.se, p=m.p, dof=m.dof,
        ), **res.estimates}
    except PlanRejected as exc:
        res.diagnostics["合并后效应"] = f"算不出来（{exc}）——main 缺省，请引用逐期系数"
        first_lead = res.estimates.pop("main", None)
        if first_lead is not None:
            res.estimates[first_lead.term] = first_lead
    res.diagnostics["估计量"] = "逐年展开（相对处理时点，第 −1 期为参照）"
    # 逐期判据的临界值跟 `figures.event` 标红的同一把尺子：按抽样设计时用 t，否则 1.96
    res.diagnostics["平行趋势"] = _parallel_trends_joint(res.event_study, res.pretrend_cov,
                                                    _t_crit_of(res.diagnostics) or 1.96)
    m = res.estimates.get("main")
    if m is not None and m.se > 0:
        res.diagnostics.update(cc.pretrend_texts(res.event_study, res.pretrend_cov, m.coef, m.se, plan.lags))
    res.diagnostics["对照组"] = _control_group(df, plan)
    res.diagnostics["处理前平衡"] = _pre_balance(df, plan)
    res.robustness = _run_robustness(frame, plan)
    return res


def _parallel_trends(points: list[EventStudyPoint], crit: float = 1.96) -> str:
    """处理**之前**两组走势平不平行。

    **这是判断整个结论能不能信的那道门。**处理前就在分化的话，
    双重差分把那个趋势当成了效应——结论是假的。

    判据是机械的：处理前每一期的系数，有没有显著偏离零。`crit` 是临界值：缺省按正态 1.96；
    按抽样设计时用 t 的临界值（自由度 = 抽样单位数 − 层数），跟 `figures.event` 标红的同一把尺子。
    """
    pre = [p for p in points if p.k < 0]
    if not pre:
        return "没有处理前的期，检验不了——**这不等于通过**"
    bad = [p for p in pre if p.se > 0 and abs(p.coef / p.se) > crit]
    detail = "；".join(f"第 {p.k} 期 {p.coef:+.3f}（{p.coef / p.se:+.1f} 个标准误）"
                      for p in pre)
    if crit != 1.96:
        detail += f"（按抽样设计，临界值取 t 分布的 {crit:.2f}）"
    if not bad:
        return f"处理前 {len(pre)} 期均未显著偏离零，平行趋势未被推翻。{detail}"
    return (
        f"**不成立**：处理前有 {len(bad)}/{len(pre)} 期显著偏离零——"
        f"两组在政策实施**之前**就已经在分化。"
        f"**这意味着算出来的「效应」里混着本来就存在的趋势，结论不可信。**{detail}"
    )


def _parallel_trends_joint(points: list[EventStudyPoint], cov: PretrendCov | None, crit: float = 1.96) -> str:
    """处理前走势平不平行：**以联合检验为准**，逐期只作细节——跟分批路径（`staggered._trend_verdict`）同一把尺子。

    逐期规则在 3 个处理前期上约有 14% 的误报；联合检验问的是「这几期合起来有没有趋势」。
    没有协方差时退回逐期规则（临界值 `crit`，按抽样设计时是 t 的）。**未被推翻不等于成立**（Roth 2023），
    所以通过时也要提预检功效。
    """
    if cov is None or not cov.ks:
        return _parallel_trends(points, crit)
    from oss.core import causal_checks as cc

    pre = [p for p in points if p.k < 0]
    W, p, df = cc.joint_pretest(points, cov)
    how = cc.joint_pretest_label(df, cov.n_clusters)
    detail = "；".join(f"第 {q.k} 期 {q.coef:+.3f}（{q.coef / q.se:+.1f} 个标准误）" for q in pre if q.se > 0)
    if p < cc.ALPHA:
        return (f"**不成立**：处理前 {df} 期的联合检验（{how}）p = {p:.3f}，拒绝「处理前平行」——"
                f"两组在政策实施**之前**就已经在分化。"
                f"**这意味着算出来的「效应」里混着本来就存在的趋势，结论不可信。**{detail}")
    return (f"处理前 {df} 期的联合检验（{how}）p = {p:.3f}，平行趋势未被推翻——"
            f"**这不等于平行趋势成立**，要看预检功效。{detail}")


def run_iv(frame: pd.DataFrame, plan: AnalysisPlan) -> AnalysisResults:
    """借力变量法（两阶段最小二乘）。

    **这个方法有一个别的方法都没有的问题：它的核心假设永远无法被检验。**

    「借力变量只通过处理影响结果」（排他性）在数学上无法用数据验证——
    它只能靠讲道理。所以工具能做的只有一件事：**每次都把这句话说出来**，
    不让它安静地产出一个数。

    能检验的那一半是**第一阶段强度**：借力变量跟处理到底相不相关。
    弱的时候估计会剧烈失真，**而且不报错**——那是必须硬拦的。
    """
    cols = [plan.outcome, plan.treatment, *plan.controls, *plan.instruments]
    df = frame.query(plan.sample_filter) if plan.sample_filter else frame
    keys = _weight_design_cols(plan)
    need = [c for c in {*cols, *plan.fixed_effects, plan.cluster_level, *keys} if c]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise PlanRejected([f"数据里没有这些列：{'、'.join(missing)}"])
    df = df[need].copy()
    if plan.weights:
        df[plan.weights] = _positive_weights(df, plan.weights)
    # 权重与抽样设计列缺失的行也去掉，跟 `_ols_rows` 同一口径
    df = df.dropna(subset=[c for c in (*cols, *keys) if c])
    if len(df) == 0:
        raise PlanRejected(["去掉缺失之后一行都不剩"])

    y = df[plan.outcome].to_numpy(float)
    x_endog = df[[plan.treatment]].to_numpy(float)
    exog = df[list(plan.controls)].to_numpy(float) if plan.controls else np.empty((len(df), 0))
    inst = df[list(plan.instruments)].to_numpy(float)
    w = df[plan.weights].to_numpy(float) if plan.weights else None

    groups = [df[f].to_numpy() for f in plan.fixed_effects if f in df.columns]
    if groups:
        y = _demean(y.reshape(-1, 1), groups, w=w).ravel()
        x_endog, exog, inst = (_demean(m, groups, w=w) if m.size else m
                               for m in (x_endog, exog, inst))
        const = np.empty((len(df), 0))
    else:
        const = np.ones((len(df), 1))

    # **加权两阶段最小二乘 = 对 √w 缩放后的数据做两阶段最小二乘**，跟 `run_ols_fe` 同一个精确等价。
    # 常数列也一起缩放；下面的投影、秩检验、方差与弱工具诊断看到的就是加权之后的那个回归
    if w is not None:
        rt = np.sqrt(w)
        y = y * rt
        x_endog, exog, inst, const = (m * rt[:, None] for m in (x_endog, exog, inst, const))

    X = np.column_stack([const, x_endog, exog]) if exog.size else np.column_stack([const, x_endog])
    Z = np.column_stack([const, inst, exog]) if exog.size else np.column_stack([const, inst])
    names = ["_const"] * const.shape[1] + [plan.treatment, *plan.controls]

    if Z.shape[1] < X.shape[1]:
        raise PlanRejected([
            f"借力变量不够：{len(plan.instruments)} 个借力变量配 1 个内生变量，"
            f"至少要一样多"
        ])

    ztz = Z.T @ Z
    if not _full_rank(Z):
        raise PlanRejected(_rank_diagnosis(Z, ["_const"] * const.shape[1]
                                           + [*plan.instruments, *plan.controls], plan, df))
    pz_x = Z @ np.linalg.solve(ztz, Z.T @ X)          # 投影后的 X
    xpx = X.T @ pz_x
    # **判的是投影后的设计矩阵，不是 `xpx`。**`xpx` 已经是乘积，
    # 元素跨的数量级是原矩阵的平方——量纲问题在它身上会被放大一倍。
    if not _full_rank(pz_x):
        raise PlanRejected(["两阶段最小二乘的矩阵不满秩——多半是借力变量与处理几乎无关"])
    xpx_inv = np.linalg.inv(xpx)
    beta = xpx_inv @ (pz_x.T @ y)
    resid = y - X @ beta                               # **用真实的 X，不是投影后的**

    n, k = X.shape
    # 吸收的自由度跟 `run_ols_fe` 同一口径：各维度的组数减 1，再加上被吸收的截距（有固定效应时常数列拿掉了）。
    # 设计、聚类、HC1 三路与有效 F、AR 都用它
    k_abs = sum(pd.Series(g).nunique() - 1 for g in groups) + (1 if groups else 0)
    # 标准误三路同 `run_ols_fe`：抽样设计 → 聚类 → HC1
    design = _design_of(plan, df)
    design_info: dict[str, int] | None = None
    if design is not None:
        strata = df[design.strata].to_numpy() if design.strata and design.strata in df.columns else None
        vcov, design_info = _design_vcov(pz_x, resid, strata, df[design.psu].to_numpy(), xpx_inv, k_abs)
        se_kind = _design_se_text(design, strata is not None, design_info, plan.cluster_level)
    elif plan.cluster_level and plan.cluster_level in df.columns:
        vcov = _cluster_robust_vcov(pz_x, resid, df[plan.cluster_level].to_numpy(), xpx_inv, k_abs)
        se_kind = f"聚类到 {plan.cluster_level}（{df[plan.cluster_level].nunique()} 个簇）"
    else:
        meat = (pz_x * (resid ** 2)[:, None]).T @ pz_x     # 不建 n×n 的 `diag(u²)`，跟 `_vcov_three_way` 同一写法
        vcov = (n / max(n - k - k_abs, 1)) * (xpx_inv @ meat @ xpx_inv)
        se_kind = "HC1 异方差稳健（未聚类）"
    se = np.sqrt(np.clip(np.diag(vcov), 0, None))

    from statistics import NormalDist

    from oss.core.causal_stats import t_sf

    estimates: dict[str, Estimate] = {}
    for i, name in enumerate(names):
        if name == "_const":
            continue
        z_ = beta[i] / se[i] if se[i] > 0 else 0.0
        # 按抽样设计时 p 值用 t（自由度 = 抽样单位数 − 层数），跟 `run_ols_fe` 同一口径
        p = (2 * t_sf(abs(z_), design_info["dof"]) if design_info is not None
             else 2 * (1 - NormalDist().cdf(abs(z_))))
        estimates["main" if name == plan.treatment else name] = Estimate(
            term=name, coef=_sig(float(beta[i])), se=_sig(float(se[i])), p=round(float(p), 6),
            dof=design_info["dof"] if design_info is not None else 0)

    from oss.core import causal_checks as cc

    ex = np.column_stack([const, exog]) if exog.size else const        # 去均值时 const 是 (n, 0)
    # 有效 F 与 AR 区间按初级抽样单位聚类（编号嵌在层里），分层进不了这两项（调查层计划第零节第 7 条）
    if design is not None:
        clusters = _psu_ids(df, design)
    else:
        clusters = (df[plan.cluster_level].to_numpy()
                    if plan.cluster_level and plan.cluster_level in df.columns else None)
    ols = run_ols_fe(df, AnalysisPlan(
        plan_id=plan.plan_id, idea_id=plan.idea_id, estimator="ols_fe", outcome=plan.outcome,
        treatment=plan.treatment, controls=list(plan.controls), fixed_effects=list(plan.fixed_effects),
        cluster_level=plan.cluster_level, weights=plan.weights, survey=plan.survey)).estimates["main"]
    main = estimates["main"]
    iv_diag = cc.iv_diagnostics(y, x_endog.ravel(), inst, ex, clusters, k_abs,
                                main.coef, main.se, ols.coef, ols.se)

    diagnostics = {
        "估计量": "加权两阶段最小二乘（借力变量法）" if w is not None else "两阶段最小二乘（借力变量法）",
        "借力变量": "、".join(plan.instruments),
        "标准误": se_kind,
        "固定效应": "、".join(plan.fixed_effects) or "无",
        **(_design_diag(design_info) if design_info is not None else {}),
        "抽样权重": _weights_text(w, plan.weights, n),
        **iv_diag,
    }
    if design is not None:
        diagnostics["有效 F 与 AR 的聚类"] = (
            f"有效 F 与 AR 区间按初级抽样单位 {design.psu} 聚类（{design_info['psu']} 个）"
            + (f"，分层 {design.strata} 没进——主结果的标准误按抽样设计算，这两项只按整群近似"
               if design.strata else ""))
    diagnostics["排他性"] = (
        "**无法用数据检验。**“借力变量只通过处理影响结果”这个假设"
        "在数学上无法验证——它只能靠讲道理。"
        f"本文必须论证为什么「{'、'.join(plan.instruments)}」"
        f"不会绕过「{plan.treatment}」直接影响「{plan.outcome}」，"
        "而这个论证的说服力决定了整篇论文的成立与否"
    )
    return AnalysisResults(
        plan_id=plan.plan_id, estimates=estimates, n=int(n),
        diagnostics=diagnostics,
        robustness=_run_robustness(frame, plan),
        log=f"两阶段最小二乘，{n} 个观测，{se_kind}",
    )


def run_rdd(frame: pd.DataFrame, plan: AnalysisPlan) -> AnalysisResults:
    """断点法：看分数线上的跳跃。

    线两侧的人除了「过没过线」几乎一样，所以线上的跳跃就是效应。

    **两个假设都能检验，这跟借力变量法很不一样：**
    ① 没人卡线（线下的人挤到线上）——看线附近的人数分布
    ② 该连续的东西确实连续——看控制变量在线上跳不跳
    """
    run_col, c = plan.running, plan.cutoff
    if not run_col:
        raise PlanRejected(["rdd 需要指定 running（决定过没过线的那个变量）"])
    df = frame.query(plan.sample_filter) if plan.sample_filter else frame
    for col in (plan.outcome, run_col, *plan.controls, plan.weights):
        if col and col not in df.columns:
            raise PlanRejected([f"数据里没有列「{col}」"])
    if plan.weights:
        df = df.assign(**{plan.weights: _positive_weights(df, plan.weights)})
    df = df.dropna(subset=[plan.outcome, run_col, *([plan.weights] if plan.weights else [])]).copy()
    h = plan.bandwidth or (df[run_col].std() / 2)
    df["_r"] = df[run_col] - c
    df["_pass"] = (df["_r"] >= 0).astype(float)
    win = df[df["_r"].abs() <= h]
    if len(win) < 20:
        raise PlanRejected([f"带宽 {h} 之内只有 {len(win)} 个观测，估不出跳跃"])

    # 主回归、协变量连续性带权重与抽样设计（窗口内的加权最小二乘）；带宽敏感性只看系数，只带权重
    inner = AnalysisPlan(
        plan_id=plan.plan_id, idea_id=plan.idea_id, estimator="ols_fe",
        outcome=plan.outcome, treatment="_pass",
        controls=["_r", "_rx", *plan.controls],
        cluster_level=plan.cluster_level,
        weights=plan.weights, survey=plan.survey,
    )
    w = win.assign(_rx=win["_r"] * win["_pass"])       # 两侧各自的斜率
    res = run_ols_fe(w, inner)
    res.plan_id = plan.plan_id
    res.diagnostics["估计量"] = f"断点法（局部线性，分数线 {c:g}，带宽 {h:g}）"
    res.diagnostics["窗口内样本"] = f"{len(win)} 个观测（线下 {int((win._r < 0).sum())}，线上 {int((win._r >= 0).sum())}）"
    # 卡线检验数的是线两侧的观测个数，问的是「有没有人挤过线」，不是总体量——不加权（调查层计划第零节第 6 条）
    res.diagnostics["卡线检验"] = _manipulation(df, h) + ("（数的是观测个数，不加权）" if plan.weights else "")
    if plan.controls:
        res.diagnostics["协变量连续性"] = _covariate_jump(w, plan)
    res.diagnostics["带宽敏感性"] = _bandwidth_sensitivity(df, plan, h)
    res.robustness = _run_robustness(frame, plan)
    return res


def _manipulation(df: pd.DataFrame, h: float) -> str:
    """有没有人卡线。**线下的人挤到线上，断点法就失效了，而它不报错。**

    判据是机械的：分数线紧邻两侧的人数应当差不多。
    差太多说明有人在操纵那个变量。
    """
    near = h / 3
    lo = int(((df["_r"] < 0) & (df["_r"] >= -near)).sum())
    hi = int(((df["_r"] >= 0) & (df["_r"] <= near)).sum())
    if lo + hi < 30:
        return f"线附近只有 {lo + hi} 个观测，**判不了**——这不等于通过"
    ratio = hi / max(lo, 1)
    detail = f"线下 {lo} 人，线上 {hi} 人（比 {ratio:.2f}）"
    if ratio > 1.5 or ratio < 1 / 1.5:
        return (f"**疑似卡线，不通过**：{detail}。线两侧人数明显不对称，"
                f"说明有人能操纵这个变量把自己挪到线的另一边——"
                f"**那样断点法就失效了，结论不可信**")
    return f"线两侧人数对称，未见卡线迹象。{detail}"


def _covariate_jump(w: pd.DataFrame, plan: AnalysisPlan) -> str:
    """该连续的东西在线上跳没跳。跳了说明跳的不只是处理。"""
    bits, bad = [], []
    for c in plan.controls:
        try:
            r = run_ols_fe(w, AnalysisPlan(
                plan_id="x", idea_id="x", estimator="ols_fe",
                outcome=c, treatment="_pass", controls=["_r", "_rx"],
                weights=plan.weights, survey=plan.survey))
            e = r.estimates["main"]
            bits.append(f"{c} 跳跃 {e.coef:+.3f}（p={e.p:.3f}）")
            if e.p < 0.05:
                bad.append(c)
        except Exception:  # noqa: BLE001
            continue
    if not bits:
        return "算不了"
    head = (f"**{'、'.join(bad)} 在线上也跳了**——跳的不只是处理，断点法的前提受损；"
            if bad else "各控制变量在线上均连续；")
    return head + "；".join(bits)


def _bandwidth_sensitivity(df: pd.DataFrame, plan: AnalysisPlan, h: float) -> str:
    """**带宽是人选的**，所以必须报出「换个带宽会怎样」。"""
    out = []
    for mult in (0.5, 0.75, 1.0, 1.5, 2.0):
        hh = h * mult
        win = df[df["_r"].abs() <= hh]
        if len(win) < 20:
            continue
        try:
            w = win.assign(_rx=win["_r"] * win["_pass"])
            # 只用系数：带上权重（系数跟着变），不带抽样设计（它只动标准误；窄窗口里每层只剩一个抽样单位时
            # G ≤ H 会被拒，那一档带宽就悄悄没了）
            r = run_ols_fe(w, AnalysisPlan(
                plan_id="x", idea_id="x", estimator="ols_fe",
                outcome=plan.outcome, treatment="_pass",
                controls=["_r", "_rx", *plan.controls],
                weights=plan.weights))
            out.append((hh, r.estimates["main"].coef, len(win)))
        except Exception:  # noqa: BLE001
            continue
    if len(out) < 2:
        return "带宽敏感性算不了"
    vals = [v for _, v, _ in out]
    spread = max(vals) - min(vals)
    ref = max(abs(v) for v in vals) or 1.0
    detail = "；".join(f"带宽 {hh:g} → {v:+.3f}（{n} 个观测）" for hh, v, n in out)
    if spread / ref > 0.5:
        return f"**换带宽结论会变**（最大相差 {spread:.3f}）——结论对带宽敏感。{detail}"
    return f"换带宽结论稳定（最大相差 {spread:.3f}）。{detail}"


def _plan_variables(plan: AnalysisPlan) -> list[str]:
    """进表的变量：结果、处理（交互项拆成分量）、控制、借力变量、驱动变量、分组描述（describe）的分组列。去重保序。"""
    raw = [plan.outcome, plan.treatment, *plan.controls, *plan.interactions,
           *plan.instruments, plan.running, *_describe_by(plan)]
    out: list[str] = []
    for spec in raw:
        for c in (parts_of(spec) if spec and is_interaction(spec) else [spec]):
            if c and c not in out:
                out.append(c)
    return out


def _descriptives(frame: pd.DataFrame, plan: AnalysisPlan) -> list[Descriptive]:
    """估计样本（筛选后、回归变量无缺失）的描述统计。**Table 1 的数字从这里来。**

    只算数值列；字符串列（国家代码之类）不进表。算不出来就给空表，
    渲染 `{{table:descriptives}}` 时会说「重跑 oss run」而不是静默空白。

    **跟主回归同一批行**：权重、初级抽样单位、分层缺失的行，主回归去掉了（`_ols_rows`），这里也去掉
    （`_weight_design_missing`）——不然表 1 的 N 跟回归的 n 对不上。计划加权时均值与标准差按权重算
    （标准差 = √加权均方离差），跟主规格同一口径；N 仍是未加权的行数。
    """
    try:
        df = frame.query(plan.sample_filter) if plan.sample_filter else frame
        cols = [c for c in _plan_variables(plan) if c in df.columns]
        if not cols:
            return []
        num = df[cols].apply(pd.to_numeric, errors="coerce")
        # 分组描述的分组列可以是文字（east / west）：转成数值整列都缺，按原值判缺失，跟 `_ols_rows` 同一口径——
        # 不然文字分组列有缺失时，表 1 不去那几行，N 就跟分组表对不上
        by = [c for c in _describe_by(plan) if c in cols]
        keep = num[[c for c in cols if c not in by and num[c].notna().any()]].notna().all(axis=1)
        for c in by:
            keep &= df[c].notna()
        keep &= ~_weight_design_missing(df, plan).any(axis=1)
        num = num[keep]
        w = (pd.to_numeric(df.loc[keep, plan.weights], errors="coerce")
             if plan.weights and plan.weights in df.columns else None)
        out = []
        for c in cols:
            col = num[c].dropna()
            if len(col) == 0:
                continue
            if w is not None:
                wc = w.loc[col.index]
                mean = float(np.average(col, weights=wc))
                sd = float(np.sqrt(np.average((col - mean) ** 2, weights=wc)))
            else:
                mean = float(col.mean())
                sd = float(col.std(ddof=1)) if len(col) > 1 else 0.0
            out.append(Descriptive(
                name=c, n=int(len(col)), mean=mean, sd=sd,
                min=float(col.min()), max=float(col.max()), weighted=w is not None))
        return out
    except Exception:  # noqa: BLE001 —— 描述统计算不出来不该让主结果挂掉
        return []


def _unit_time(plan: AnalysisPlan, frame: pd.DataFrame) -> tuple[str, str]:
    """这个计划在这份数据上的单位列与时间列；认不出来返回两个空串。

    **两条路，只有这两条**：计划显式写了 `unit` / `time` 就用它们；没写才拿前两个
    固定效应当候选——**而且要这份数据真按这两列构成面板**（`profiler.is_panel_key`，
    跟画像挑个体标识用的是同一条判据）才认。

    加这道核验是因为原先的猜法在真夹具上判错：`collinearity_apc` 的固定效应写的是
    `["year", "region"]`，于是「单位」= 年份、「时间」= 地区，每格几百人、压根不是面板，
    `_inject_flags` 照样分组算「处理反转」——gss_like、cgss_like 上满屏乱报，
    而照那条处方的补丁 `_reversal == 0` 走会把样本删空。

    **宁可认不出，不可认错**：认不出来时上层什么都不加、什么都不报。
    """
    from oss.core.profiler import is_panel_key

    if plan.unit and plan.time:
        # 写了但数据里没有这两列，照旧当认不出来（调用方原先各自查一遍，收到这里）
        both = plan.unit in frame.columns and plan.time in frame.columns
        return (plan.unit, plan.time) if both else ("", "")
    if len(plan.fixed_effects) >= 2:
        unit = plan.unit or plan.fixed_effects[0]
        time = plan.time or plan.fixed_effects[1]
        if is_panel_key(frame, unit, time):
            return unit, time
    return "", ""


def _binary_treatments(frame: pd.DataFrame, plan: AnalysisPlan) -> list[str]:
    """处理变量里取值是 0/1 的那些列（交互项按分量拆）。

    **只看处理变量。**控制变量原先也算进来，于是一个在单位内变一下的 0/1 控制变量
    （`female`、`hukou_urban`）就能把整份数据报成「处理有反转」——而反转说的是
    **处理**从 1 变回 0。
    """
    cols: list[str] = []
    spec = plan.treatment
    for c in (parts_of(spec) if spec and is_interaction(spec) else [spec]):
        if c and c in frame.columns and c not in cols:
            vals = set(pd.to_numeric(frame[c], errors="coerce").dropna().unique())
            if len(vals) == 2 and vals <= {0.0, 1.0}:
                cols.append(c)
    return cols


def _adoption_cohorts(frame: pd.DataFrame | None, unit: str, time: str, treat: str) -> int:
    """各单位**首次被处理的那一期**有几个不同取值——「分了几批」的唯一判据。

    `treat` 两种写法都吃，因为它们说的是同一件事：

    - **处理起始时间列**（`treat_time`）：这个单位的第一个非空值就是它那一批；
      从未处理的单位整列是缺失，不算一批。
    - **0/1 的处理指示**（`post` 这类哑变量）：该单位头一次取 1 的那一期就是它那一批。
      这一支要单位与时间列，认不出来就返回 0。

    列不在数据里、或没有帧，返回 0——**批次是数据的事实，光看计划看不出来**。
    """
    if frame is None or not treat or treat not in frame.columns:
        return 0
    col = pd.to_numeric(frame[treat], errors="coerce")
    vals = set(col.dropna().unique().tolist())
    if vals and vals <= {0.0, 1.0}:
        if not unit or not time or unit not in frame.columns or time not in frame.columns:
            return 0
        on = frame.assign(_x=col, _t=pd.to_numeric(frame[time], errors="coerce"))
        on = on.dropna(subset=["_x", "_t"])
        on = on[on["_x"] == 1.0]
        return int(on.groupby(unit)["_t"].min().nunique()) if len(on) else 0
    if unit and unit in frame.columns:
        return int(frame.assign(_g=col).groupby(unit)["_g"].first().nunique())
    return int(col.nunique())


def _twfe_staggered_cohorts(frame: pd.DataFrame | None, plan: AnalysisPlan) -> int:
    """写成 `ols_fe` 的双向固定效应双重差分分了几批（0 或 1 表示判不了 / 不是分批）。

    分批实施不一定写成 `did`——`ols_fe` + 一个 post 哑变量 + 单位与时间两个固定效应，
    算的跟 `did` 一模一样，坑也一模一样：考卷 A 实测主系数 2.129，真实平均处理效应 3.56，
    偏了四成，而门原先认的是「计划里写的估计量名字」，一道都不响。

    **边界，宁可漏不可误报**（四条缺一不可）：

    1. 只判 `ols_fe`——`did` / `event_study` / `did_cs` 那一路自己数，见 `validate_plan`；
    2. 没有帧不判：批次是数据的事实；
    3. 单位与时间列认不出来不判——**含数据压根不是面板**（`_unit_time` 会核），
       而且两列都得真上了固定效应：只吸收单位不吸收时间的，不是双向固定效应；
    4. 处理变量不是 0/1 不判：连续的处理强度没有「何时开始被处理」可言。
    """
    if frame is None or plan.estimator != "ols_fe":
        return 0
    unit, time = _unit_time(plan, frame)
    if not unit or not time or unit not in plan.fixed_effects or time not in plan.fixed_effects:
        return 0
    if plan.treatment not in _binary_treatments(frame, plan):
        return 0
    return _adoption_cohorts(frame, unit, time, plan.treatment)


def _inject_flags(frame: pd.DataFrame, plan: AnalysisPlan) -> tuple[pd.DataFrame, dict[str, str]]:
    """给数据加两列标志，让「剔除反转单位」「只留平衡面板」这两项稳健性**能直接写成筛选**：

    - `_reversal`：这个单位的某个二值处理变量曾从 1 变回 0（民主 → 威权回潮那种）。
      评审：「Boix 指标会 0→1→0……有反转的国家在事件研究里是被污染的处理组，
      至少要报剔除反转国家的稳健性」——以前写不出来，因为没有那一列。
    - `_balanced`：这个单位在全部期都有观测。面板流失的稳健性用它。

    返回加了列的表和一条诊断（有反转时）。单位 / 时间列认不出来——包括数据压根不是
    面板（见 `_unit_time`）——就什么都不加。
    """
    unit, time = _unit_time(plan, frame)
    if not unit or not time:
        return frame, {}
    df = frame.copy()
    notes: dict[str, str] = {}
    per = df.groupby(unit)[time].nunique()
    total = int(df[time].nunique())
    df["_balanced"] = df[unit].map(per == total).astype(float)
    t = pd.to_numeric(df[time], errors="coerce")
    rev_units: set = set()
    details: list[tuple[str, list]] = []
    for c in _binary_treatments(df, plan):
        order = df.assign(_x=pd.to_numeric(df[c], errors="coerce"), _t=t)
        order = order.dropna(subset=["_x", "_t"]).sort_values([unit, "_t"])
        flip = order.groupby(unit)["_x"].cummax() > order["_x"]
        units = sorted(set(order.loc[flip, unit]), key=str)
        if units:
            rev_units |= set(units)
            details.append((c, units))
    df["_reversal"] = df[unit].isin(rev_units).astype(float)
    if details:
        notes["处理反转"] = "；".join(
            f"「{c}」在 {len(u)} 个单位里从 1 变回 0（{'、'.join(map(str, u[:12]))}{'……' if len(u) > 12 else ''}）"
            for c, u in details
        ) + ("。反转的单位在处理组里是被污染的：稳健性里加一项剔除它们——"
             "`{\"label\": \"Exclude units with treatment reversals\", \"sample_filter\": \"_reversal == 0\"}`"
             "（`_reversal` 列由 oss run 自动加）")
    return df, notes


def _sample_log(frame: pd.DataFrame, plan: AnalysisPlan) -> tuple[list[SampleStep], str]:
    """样本构建日志：原始 → 样本筛选 → 去缺失 → 估计样本。每步剩多少行、多少单位、谁整个掉了。

    评审第 10 条：「107 国变成 99 个 cluster，中间 8 个国家去哪了没有交代」。
    答案几乎总在去缺失那一步：某几个单位在某个控制变量上整段缺失。这里把名单和肇事变量列出来。
    """
    unit, _ = _unit_time(plan, frame)
    cols = [c for c in _plan_variables(plan) if c in frame.columns]

    def units_of(df: pd.DataFrame) -> set:
        return set(df[unit].dropna().unique()) if unit and unit in df.columns else set()

    def n_units(df: pd.DataFrame) -> int:
        return len(units_of(df)) if unit else 0

    steps: list[SampleStep] = []
    cur = frame
    steps.append(SampleStep(step="原始", rows=int(len(cur)), units=n_units(cur)))
    if plan.sample_filter:
        try:
            nxt = cur.query(plan.sample_filter)
        except Exception as exc:  # noqa: BLE001
            return steps, f"样本筛选 `{plan.sample_filter}` 算不了（{exc}）"
        gone = sorted(units_of(cur) - units_of(nxt), key=str)
        steps.append(SampleStep(step=f"样本筛选 `{plan.sample_filter}`", rows=int(len(nxt)), units=n_units(nxt),
                                dropped_units=[str(u) for u in gone[:20]],
                                note=f"整个掉出去 {len(gone)} 个单位" if gone else ""))
        cur = nxt
    if cols:
        num = cur[cols].apply(pd.to_numeric, errors="coerce")
        # 权重、抽样设计列缺失的行主回归也去掉了（`_ols_rows`）：算进这一步，最后一步才等于回归的 n
        bad = num.isna()
        # 分组描述的分组列按原值判缺失：文字分组列转成数值整列都缺，原先估计样本报 0 行
        for c in _describe_by(plan):
            if c in bad.columns:
                bad[c] = cur[c].isna()
        for c, flag in _weight_design_missing(cur, plan).items():
            bad[c] = (bad[c] | flag) if c in bad.columns else flag
        keep = ~bad.any(axis=1)
        nxt = cur[keep]
        gone = sorted(units_of(cur) - units_of(nxt), key=str)
        culprit = ""
        if gone and unit:
            miss = bad[cur[unit].isin(gone)].mean().sort_values(ascending=False)
            top = miss[miss > 0].head(3)
            culprit = "；主要因为 " + "、".join(f"{c}（这些单位缺 {v:.0%}）" for c, v in top.items()) if len(top) else ""
        steps.append(SampleStep(step="去掉回归变量缺失的行", rows=int(len(nxt)), units=n_units(nxt),
                                dropped_units=[str(u) for u in gone[:20]],
                                note=(f"整个掉出去 {len(gone)} 个单位" + culprit) if gone else ""))
        cur = nxt
    steps.append(SampleStep(step="估计样本", rows=int(len(cur)), units=n_units(cur)))
    text = " → ".join(
        f"{st.step}：{st.rows:,} 行" + (f" / {st.units} 个单位" if unit else "")
        + (f"（掉 {', '.join(st.dropped_units[:8])}{'…' if len(st.dropped_units) > 8 else ''}{st.note and '，' + st.note.split('；')[-1] if '；' in st.note else ''}）"
           if st.dropped_units else "")
        for st in steps)
    return steps, text


def _data_contract(frame: pd.DataFrame, plan: AnalysisPlan) -> tuple[pd.DataFrame, dict[str, str]]:
    """数据契约：跑之前先查数据撑不撑得起这个设计。借自 AERS 的 data contract（五项 go/no-go）。

    - 单位-时间键唯一（分批设计里重复键会让固定效应和事件时间全错——**拒绝**）
    - 处理单位有没有处理前观测（没有的叫左删失：事件研究看不到它的「前」，
      平行趋势对它无从检验）——列名单，加 `_has_pre` 列让稳健性能剔掉
    - 处理单位有没有处理后观测
    - 从未处理的单位有多少
    - 结果变量在处理组处理后的缺失率
    """
    notes: dict[str, str] = {}
    unit, time = _unit_time(plan, frame)
    if not unit or not time:
        return frame, notes
    df = frame
    if plan.estimator in DID_FAMILY:
        dup = int(df.duplicated(subset=[unit, time]).sum())
        if dup:
            raise PlanRejected([
                f"数据契约不满足：「{unit}」×「{time}」有 {dup} 行重复键——分批设计要求每个单位每期一行，"
                f"重复键会让固定效应和事件时间全错。先在 prep.py 里去重或聚合"])
    if plan.treat_time and plan.treat_time in df.columns:
        t = pd.to_numeric(df[time], errors="coerce")
        g = pd.to_numeric(df[plan.treat_time], errors="coerce")
        per = df.assign(_t=t, _g=g).groupby(unit).agg(g=("_g", "first"), tmin=("_t", "min"), tmax=("_t", "max"))
        treated = per[per["g"].notna()]
        never = int(per["g"].isna().sum())
        no_pre = sorted(treated[treated["tmin"] >= treated["g"]].index, key=str)
        no_post = sorted(treated[treated["tmax"] < treated["g"]].index, key=str)
        has_pre = set(treated[treated["tmin"] < treated["g"]].index) | set(per[per["g"].isna()].index)
        df = df.copy()
        df["_has_pre"] = df[unit].isin(has_pre).astype(float)
        y_post = pd.to_numeric(df.loc[(g.notna()) & (t >= g), plan.outcome], errors="coerce") \
            if plan.outcome in df.columns else pd.Series(dtype=float)
        miss_post = float(y_post.isna().mean()) if len(y_post) else 0.0
        bits = [
            f"单位-时间键唯一" if plan.estimator in DID_FAMILY else "",
            f"{len(treated)} 个处理单位、{never} 个从未处理",
            (f"**{len(no_pre)} 个处理单位没有处理前观测（左删失）**：{'、'.join(map(str, no_pre[:12]))}"
             f"{'…' if len(no_pre) > 12 else ''}——它们的平行趋势无从检验；稳健性剔掉它们：`sample_filter: \"_has_pre == 1\"`"
             if no_pre else "处理单位都有处理前观测"),
            (f"{len(no_post)} 个处理单位没有处理后观测：{'、'.join(map(str, no_post[:12]))}" if no_post else ""),
            (f"结果变量在处理组处理后缺失 {miss_post:.0%}" if miss_post > 0.05 else ""),
        ]
        notes["数据契约"] = "；".join(b for b in bits if b)
    return df, notes


def _spec_summary(plan: AnalysisPlan) -> SpecSummary:
    fe = list(plan.fixed_effects)
    if plan.estimator in DID_FAMILY and not fe:
        fe = [c for c in (plan.unit, plan.time) if c]
    return SpecSummary(
        estimator=plan.estimator, outcome=plan.outcome, treatment=plan.treatment,
        controls=list(plan.controls), fixed_effects=fe,
        cluster_level=plan.cluster_level or (plan.unit if plan.estimator in DID_FAMILY else ""),
        sample_filter=plan.sample_filter, unit=plan.unit, time=plan.time, treat_time=plan.treat_time,
        weights=plan.weights,
        strata=plan.survey.strata if plan.survey else "",
        psu=plan.survey.psu if plan.survey else "",
    )


def _sensitivity(frame: pd.DataFrame, plan: AnalysisPlan, res: AnalysisResults) -> dict[str, str]:
    """因果类 ols_fe 的敏感性（设计稿 4.3）：主回归、去掉控制变量的短回归、处理对控制变量的辅助回归。

    短回归、辅助回归必须跟主回归用**同一批行**：控制变量（或结果变量）有缺失时，主回归已经
    把那些行去掉了；短/辅助回归不重新筛就会在跟主回归不同的样本上跑——Oster 比的就成了两批
    不同的人。先按主回归的选行逻辑（`_ols_rows`）算出主回归实际用的那批行，再把 `frame` 收窄
    到那批行——**不按固定效应或聚类层去缺失**（`treat_time` 这类列对从未处理的单位本来就是
    NaN），跟主回归同一套口径。调用者保证 `plan.interactions` 为空（`run_plan` 的触发条件），
    这里的 `cols` 不必管交互项分量。
    """
    from oss.core import causal_checks as cc

    main_rows, _, _ = _ols_rows(frame, plan, [plan.outcome, plan.treatment, *plan.controls])
    frame = frame.loc[main_rows.index]

    bare = {**plan.model_dump(), "interactions": [], "robustness": [], "binned": None}
    short = run_ols_fe(frame, AnalysisPlan(**{**bare, "controls": [], "control_roles": {}}))
    aux = (run_ols_fe(frame, AnalysisPlan(**{**bare, "outcome": plan.treatment, "treatment": plan.controls[0],
                                             "controls": plan.controls[1:], "control_roles": {}}))
           if plan.controls else None)
    sd_y = next((d.sd for d in res.descriptives if d.name == plan.outcome), 0.0)
    main = res.estimates["main"]
    return cc.sensitivity_texts(res._fit, main.coef, main.se, plan.treatment, short._fit,
                                aux._fit if aux else None, list(plan.controls), sd_y)


def run_plan(frame: pd.DataFrame, plan: AnalysisPlan) -> AnalysisResults:
    """按计划里声明的方法跑。**唯一的入口。**

    ols_fe / did 计划写了 unit / time / treat_time 的，最外层那次会**自动附带**
    一次逐年展开（见 `_attach_event_study`）——事件研究不必另开运行目录。
    最外层那次还会附上描述统计和规格摘要——三张表（描述统计、主结果、稳健性）
    的数字与表注都从结果文件来，不靠手写；并加上 `_reversal` / `_balanced` 两列标志，
    稳健性可以直接 `sample_filter: "_reversal == 0"`。
    """
    flags: dict[str, str] = {}
    if _NESTED == 0:
        frame, flags = _inject_flags(frame, plan)
        frame, contract = _data_contract(frame, plan)
        flags.update(contract)
    if plan.estimator == "event_study":
        res = run_event_study(frame, plan)
    elif plan.estimator == "did_cs":
        from oss.core import staggered
        res = staggered.run_cs(frame, plan)      # 自带逐期效应与按批效应，不再附带 TWFE 的
    elif plan.estimator == "iv":
        res = run_iv(frame, plan)
    elif plan.estimator == "rdd":
        res = run_rdd(frame, plan)
    elif plan.estimator == "describe":
        res = run_describe(frame, plan)
    elif plan.estimator == "iptw":
        res = run_iptw(frame, plan)
    elif plan.estimator == "logit":
        res = run_logit(frame, plan)
    elif plan.estimator == "ologit":
        res = run_ologit(frame, plan)
    elif plan.estimator == "mixed":
        res = run_mixed(frame, plan)
    else:
        res = run_did(frame, plan) if plan.estimator == "did" else run_ols_fe(frame, plan)
        if _NESTED == 0 and (n := _twfe_staggered_cohorts(frame, plan)) > 1:
            # 写成 ols_fe 的分批 TWFE：把批次数记进诊断，处方就跟 did 那一路走**同一条**
            # （`route` 的 staggered_twfe 按这一条数批次）。`did` 自己在 run_did 里已经写过
            res.diagnostics.setdefault("实施批次", f"{n} 批")
        if _NESTED == 0 and plan.treat_time and plan.unit and plan.time:
            _attach_event_study(frame, plan, res)
    if _NESTED == 0:
        res.spec = _spec_summary(plan)
        # 按抽样设计时区间用 t 的临界值（自由度 = 抽样单位数 − 层数）；图与表都从这里取
        if (t_crit := _t_crit_of(res.diagnostics)):
            res.spec.t_crit = t_crit
        elif not (plan.estimator == "did_cs" and "抽样单位数" in res.diagnostics):
            # 表注那句「按抽样设计」只在真按设计算了（有抽样设计自由度）时才写：声明了设计、这条路却退回了
            # 聚类或 HC1，就不能那么说。did_cs 例外——StatsPAI 按抽样单位聚类、分层不进，表注照那样写
            res.spec.psu = res.spec.strata = ""
        res.descriptives = _descriptives(frame, plan)
        try:
            res.sample_log, log_text = _sample_log(frame, plan)
            flags["样本构建"] = log_text
        except Exception as exc:  # noqa: BLE001 —— 日志算不出来不该让主结果挂掉
            flags["样本构建"] = f"算不出来（{type(exc).__name__}: {exc}）"
        for k, v in flags.items():
            res.diagnostics.setdefault(k, v)
        if (plan.research_type == "causal" and plan.estimator == "ols_fe" and res._fit is not None
                and "main" in res.estimates and not plan.interactions):
            try:
                res.diagnostics.update(_sensitivity(frame, plan, res))
            except Exception as exc:  # noqa: BLE001 —— 敏感性算不出来不该让主结果挂掉，但要说出来
                res.diagnostics["敏感性分析"] = f"算不出来（{type(exc).__name__}: {exc}）"
    return res


def _ols_rows(frame: pd.DataFrame, plan: AnalysisPlan, cols: list[str]) -> tuple[pd.DataFrame, int, int]:
    """`run_ols_fe` 真正拿去跑回归的那批行：`sample_filter` 筛、按 `cols`（该跑的列，不含固定
    效应与聚类层）、权重与抽样设计列砍列、权重转数值、按 `cols`、权重与抽样设计列（初级抽样
    单位、分层）去缺失。**不按固定效应或聚类层去缺失**——`treat_time` 这类列对从未处理的单位
    本来就是 NaN。设计列缺失的行进不了按设计的方差，丢掉的照样算进「因缺失丢弃」。

    `_sensitivity` 靠这个函数拿主回归**实际用到的那批行**：短回归、辅助回归不重新走一遍
    这道筛选就会在跟主回归不同的样本上跑——控制变量有缺失时，Oster 比的就成了两批不同的人。

    返回 `(df, before, dropped)`：`df` 只剩 `needed` 那些列，`before`/`dropped` 供
    「因缺失丢弃」诊断用。
    """
    # 筛选要在**整张表**上做，不能先砍列。
    # 先砍列的话，`age >= 30` 这种引用了非回归变量的筛选会报
    # UndefinedVariableError——而筛选条件本来就常常用到不进回归的变量。
    df = frame.query(plan.sample_filter) if plan.sample_filter else frame

    keys = _weight_design_cols(plan)
    needed = [c for c in {*cols, *plan.fixed_effects, plan.cluster_level, *keys} if c]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise PlanRejected([f"数据里没有这些列：{'、'.join(missing)}"])
    df = df[needed].copy()

    # 权重要在去缺失之前查。**权重缺失的行不能悄悄按 1 处理**——
    # 那等于给这些人一个凭空的权重，而且没有任何人会发现。
    if plan.weights:
        df[plan.weights] = _positive_weights(df, plan.weights)

    if len(df) == 0:
        raise PlanRejected([
            f"样本筛选 `{plan.sample_filter}` 之后一行都不剩——检查筛选条件"
            if plan.sample_filter else "数据是空的"
        ])
    before = len(df)
    df = df.dropna(subset=[c for c in (*cols, *keys) if c])
    dropped = before - len(df)
    if len(df) == 0:
        need = "、".join(c for c in cols if c)
        raise PlanRejected([
            f"去掉缺失之后一行都不剩。回归用到的列：{need}"
            + (f"（样本筛选 `{plan.sample_filter}` 之后）" if plan.sample_filter else "")
            + "——多半是其中某一列在这个子样本里整段缺失"
        ])
    return df, before, dropped


def run_ols_fe(frame: pd.DataFrame, plan: AnalysisPlan) -> AnalysisResults:
    """跑一次 OLS + 固定效应。

    固定效应用去均值吸收（不建虚拟变量矩阵），所以维度多了也不会爆内存。
    标准误三路：声明了抽样设计（`plan.survey.psu`）就按设计算（分层 + 整群，泰勒线性化，
    p 值用自由度 = 抽样单位数 − 层数的 t 分布）；否则有聚类层级时用聚类稳健，再否则用 HC1
    异方差稳健——**绝不用同方差标准误**，社科数据几乎没有满足那个假设的。
    """
    # 交互项现场构造。必须在筛选和砍列之前——分量本身可能不进回归。
    frame = _expand_interactions(frame, [
        *plan.interactions,
        *([plan.treatment] if is_interaction(plan.treatment) else []),
    ])
    extra = [i for i in plan.interactions if i != plan.treatment]
    cols = [plan.outcome, plan.treatment, *plan.controls, *extra]
    df, before, dropped = _ols_rows(frame, plan, cols)

    y = df[plan.outcome].to_numpy(dtype=float)
    regressors = [plan.treatment, *plan.controls, *extra]
    X = df[regressors].to_numpy(dtype=float)
    w = df[plan.weights].to_numpy(dtype=float) if plan.weights else None

    groups = [df[f].to_numpy() for f in plan.fixed_effects if f in df.columns]
    if groups:
        y = _demean(y.reshape(-1, 1), groups, w=w).ravel()
        X = _demean(X, groups, w=w)
    else:
        # 没有固定效应就得自己加截距
        X = np.column_stack([np.ones(len(X)), X])
        regressors = ["_const", *regressors]

    # **加权最小二乘 = 对 √w 缩放后的数据做普通最小二乘。**
    # 这个等价是精确的，所以后面的秩检验、标准化求解、聚类稳健方差
    # 一行都不用改——它们看到的就是加权之后该看到的那个回归。
    if w is not None:
        rt = np.sqrt(w)
        y = y * rt
        X = X * rt[:, None]

    n, k = X.shape
    # 固定效应吸收掉的自由度要算进去，否则标准误偏小
    # 固定效应吸收掉的自由度。**截距也被吸收了，要算进去**——
    # 一维固定效应吃掉的是 G 个参数（G-1 个组间差 + 那个截距），不是 G-1；
    # 两维是 G₁+G₂-1。没有固定效应时 X 自带一列常数，那一维已经算在 `k` 里了。
    k_absorbed = sum(pd.Series(g).nunique() - 1 for g in groups) + (1 if groups else 0)

    # **整个回归在标准化空间里做，最后把系数换算回原刻度。**
    #
    # 真数据上量过一次：`pop` 从九千跨到十四亿，`X'X` 的元素跨十八个数量级，
    # 条件数 7.28e15——`matrix_rank` 的容差吞掉两个真实维度，
    # 满秩的矩阵被判成 3/5，然后系统去报「某某与某某完全共线」，**点的是无辜的变量**。
    #
    # 只把秩检验挪到标准化空间是不够的：检验放行之后，
    # **对同一个病态矩阵求逆得到的仍然是垃圾**。所以求解也一起挪。
    #
    # 换算是精确的，不是近似：`X_s = X / sd`，于是 `β = β_s / sd`、
    # `Var(β) = Var(β_s) / sd²`。**标准化只改刻度，不改任何统计量。**
    sd = _col_scale(X)
    Xs = X / sd
    raw_deficient = np.linalg.matrix_rank(X.T @ X) < k

    xtx = Xs.T @ Xs
    if np.linalg.matrix_rank(xtx) < k:
        # 「不满秩」没法行动。点名是哪个变量出了问题才有用——
        # 最常见的两种：被固定效应吃光了，或者跟别的变量完全共线。
        # 走到这里说明标准化之后**仍然**不满秩，那就是真的共线。
        raise PlanRejected(_rank_diagnosis(X, regressors, plan, df, frame))
    xtx_inv = np.linalg.inv(xtx)
    beta_s = xtx_inv @ Xs.T @ y
    resid = y - Xs @ beta_s

    # 同方差经典协方差 σ̂²·(X'X)⁻¹（标准化空间），只给敏感性分析的稳健值、偏 R² 算经典 t 用——
    # 那两个公式的恒等式只在经典 t 下成立，见 `causal_checks.sensitivity_texts`
    dof_resid = max(n - k - k_absorbed, 1)
    rss = float(resid @ resid)
    vcov_iid_s = (rss / dof_resid) * xtx_inv

    # 标准误三路：抽样设计 → 聚类 → HC1（`_vcov_three_way`；分组描述的 `_fit_cells` 调同一个）
    se3 = _vcov_three_way(Xs, resid, xtx_inv, k_absorbed, df, plan)
    vcov_s, design, design_info, se_kind = se3.vcov, se3.design, se3.info, se3.text

    beta = beta_s / sd
    vcov = vcov_s / np.outer(sd, sd)
    vcov_iid = vcov_iid_s / np.outer(sd, sd)
    se = np.sqrt(np.clip(np.diag(vcov), 0, None))

    estimates: dict[str, Estimate] = {}
    for i, name in enumerate(regressors):
        if name == "_const":
            continue
        z = beta[i] / se[i] if se[i] > 0 else 0.0
        key = "main" if name == plan.treatment else name
        estimates[key] = Estimate(
            term=name,
            coef=_sig(float(beta[i])),
            se=_sig(float(se[i])),
            p=round(float(se3.p(z)), 6),       # 按抽样设计时用 t（自由度 = 抽样单位数 − 层数）
            dof=se3.dof,                        # 最小可探测效应也按 t 算
        )

    collin_text, collin_var, collin_max = _collinearity(X, regressors)

    ss_res = rss
    ss_tot = float(((y - y.mean()) ** 2).sum())
    diagnostics = {
        "估计量": ("加权最小二乘 + 固定效应（去均值吸收）" if w is not None
                else "OLS + 固定效应（去均值吸收）"),
        "标准误": se_kind,
        "固定效应": "、".join(plan.fixed_effects) or "无",
        "吸收的自由度": str(k_absorbed),
        "组内 R²": f"{1 - ss_res / ss_tot:.4f}" if ss_tot > 0 else "不可算",
    }
    diagnostics.update(se3.diag())
    diagnostics["抽样权重"] = _weights_text(w, plan.weights, n)
    if collin_text:
        # **永远报，不只在出问题时报。**只在出问题时才出现的诊断项，
        # 使用者看不到它就不会想到去找它。
        diagnostics["共线诊断（VIF）"] = collin_text
    # 主效应和交互项的最小可探测效应。**零结果的论文靠这一行活着**：
    # 「交互项全不显著」到底是没有调节，还是这个样本根本测不出 2 年以内的调节——
    # 验证跑里执行者是手算的，审稿人第一句就问这个。
    mde_keys = [k for k, e in estimates.items() if k == "main" or "*" in e.term]
    if mde_keys:
        from oss.core.causal_stats import mde_multiplier
        basis = ("≈2.8×标准误" if design_info is None else
                 f"按 t 分布、自由度 {design_info['dof']}，≈{mde_multiplier(design_info['dof']):.2f}×标准误")
        diagnostics["最小可探测效应"] = (
            f"80% 功效、5% 双侧下（{basis}）："
            + "；".join(f"{k} {estimates[k].mde:.3f}" for k in mde_keys)
            + "。**不显著的系数只有跟它比过才知道是“没有效应”还是“测不出来”**"
        )
    if raw_deficient:
        # 原刻度下会被判成不满秩，标准化之后满秩——**不是共线，是量纲**。
        # 这里不再报错（回归已在标准化空间里跑完，结果是对的），
        # 但要说出来：跨这么多数量级的列，**系数本身也不好读**
        # （「人口每增加 1 人」这种解读没有意义），而且别的软件会直接罢工。
        worst = [f"`{regressors[i]}`" for i in np.argsort(-sd)[:3] if regressors[i] != "_const"]
        diagnostics["量纲"] = (
            f"{'、'.join(worst)} 的取值跨度远大于其余变量"
            f"（条件数 {np.linalg.cond(X.T @ X):.2g}）。本次回归已在标准化刻度下求解，"
            f"**估计值不受影响**；但这类变量建议取对数或换单位——"
            f"原刻度下的秩检验会把它误判成共线，而那个提示会让人删掉本该留下的变量"
        )
    if dropped:
        diagnostics["因缺失丢弃"] = f"{dropped} 行（{dropped / max(before, 1):.1%}）"
    if (att := _attrition(df, plan)) is not None:
        diagnostics["面板流失"] = att
    if plan.sample_filter:
        diagnostics["样本筛选"] = plan.sample_filter

    binned = _run_binned(frame, plan)
    if binned:
        # 键叫「分箱口径」，不叫「分箱」——「分箱」是「没算」那条警报的键，声明了就该消失
        spec = plan.binned
        se_bit = (f"，标准误按抽样设计（初级抽样单位 {design.psu}）" if design is not None
                  else f"，标准误按 {plan.cluster_level} 聚类" if plan.cluster_level else "")
        diagnostics["分箱口径"] = (
            f"各箱内只跑二元回归（{'按 ' + plan.weights + ' 加权；' if plan.weights else ''}"
            f"原始形状，**不含控制变量与固定效应**；不能拿来跟主规格比）"
            if spec and spec.bivariate else
            f"各箱内按主规格跑：控制变量 {'、'.join(c for c in plan.controls if c not in (spec.treatment if spec else '', spec.by if spec else '')) or '无'}"
            f"，固定效应 {'、'.join(plan.fixed_effects) or '无'}"
            f"{'，按 ' + plan.weights + ' 加权' if plan.weights else ''}{se_bit}——"
            f"跟主规格是同一个量，可以直接比"
        )
    if plan.interactions and not binned:
        # **写了交互项却没声明分箱。**交互项 `a × b` 的全部含义就是
        # 「a 的斜率随 b **线性**变化」——那是一个假定，而分箱是它的实际形状。
        #
        # 不自动补算：没在计划里声明就不跑，是事前锁定的规矩。
        # 但也不能不吭声——真跑两次都是同一个下场：论文照写「分箱显示呈非单调」，
        # 而那几个数字只存在于图片里，读者复核不了。
        # **等到写完论文才发现，代价是整节重写。**
        diagnostics["分箱"] = (
            f"**没算**——计划里有交互项（{'、'.join(plan.interactions)}）却没声明 `binned`。"
            f"交互项假定斜率随分组**线性**变化，分箱是这个假定的实际形状。"
            f"要验就在 plan.json 里补 `binned`（`by` 写斜率随哪一列变）后重跑；"
            f"**不补的话，正文不许拿分箱图做形状断言**——图里有、文件里没有等于「相信我」"
        )

    res = AnalysisResults(
        plan_id=plan.plan_id,
        estimates=estimates,
        n=int(n),
        diagnostics=diagnostics,
        binned=binned,
        robustness=_run_robustness(frame, plan),
        log=f"{len(regressors)} 个回归元，{n} 个观测，{se_kind}",
    )
    # 拟合信息不落盘，给同一次运行里的诊断用：事件研究的协方差、敏感性分析的自由度与 R²
    keep = [i for i, r in enumerate(regressors) if r != "_const"]
    res._fit = OlsFit(
        terms=[regressors[i] for i in keep],
        beta=[float(beta[i]) for i in keep],
        vcov=[[float(vcov[i, j]) for j in keep] for i in keep],
        vcov_iid=[[float(vcov_iid[i, j]) for j in keep] for i in keep],
        dof=int(dof_resid),
        r2=float(1 - ss_res / ss_tot) if ss_tot > 0 else float("nan"),
        n=int(n),
        n_clusters=int(se3.n_clusters),
    )
    return res


# ------------------------------------------------------------------ 分组描述（describe）

#: 分布（kind: distribution）报的加权分位数
_QUANTILES = (10, 25, 50, 75, 90)

#: 0/1 结局的诊断键（`run_describe` 写，结局只取 0 与 1 时才有）。稳健性表逐行按它定区间的尺度——
#: 稳健性那一项存的是诊断，没有分组表，看不到 `GroupCell.proportion`；换了结局的那一项也不一定还是 0/1
PROPORTION_DIAG = "比例结局"


def _describe_by(plan: AnalysisPlan) -> list[str]:
    """`describe` 的分组列：`description.groups`，`kind` 是 trend 时再加上 `over`（分期）。别的估计量返回空。

    `run_describe` 按它分格、去缺失；表 1 与样本构建日志（`_plan_variables`）也按它去——不然表 1 的 N 跟分组表对不上。
    """
    d = plan.description
    if plan.estimator != "describe" or d is None:
        return []
    return [c for c in (*d.groups, *([d.over] if d.kind == "trend" else [])) if c]


def _level_text(v) -> str:
    """分组标签里的取值。整数值的浮点写成整数：分组列有缺失时 pandas 会把整列读成浮点，标签别写成 `urban=1.0`。"""
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _weighted_quantiles(y: np.ndarray, w: np.ndarray | None, qs: tuple[int, ...]) -> dict[str, float]:
    """加权分位数，inverted CDF：累计权重份额第一次到 q 的那个值（不插值）。不加权时跟 numpy 的
    `method="inverted_cdf"` 一致。比较时留一点浮点余量，免得份额恰好等于 q 时差一个末位就跳到下一个值。"""
    order = np.argsort(y, kind="mergesort")
    ys = y[order]
    cum = np.cumsum(np.ones(len(ys)) if w is None else w[order])
    out: dict[str, float] = {}
    for q in qs:
        i = int(np.searchsorted(cum, q / 100 * cum[-1] * (1 - 1e-12), side="left"))
        out[f"p{q}"] = float(ys[min(i, len(ys) - 1)])
    return out


#: 中位数的自助法重抽次数。够让 2.5% / 97.5% 分位稳下来；再多的收益远小于一千次排序的代价
MEDIAN_BOOT = 1000


def _median_boot(y: np.ndarray, w: np.ndarray | None, codes: np.ndarray, n_cells: int,
                 units: np.ndarray, strata: np.ndarray | None, seed: int
                 ) -> np.ndarray:
    """各格中位数的自助法重抽分布，形状 `(次数, 格数)`；某一次重抽里没有行的格记 NaN。

    **抽的是方差单位，不是行**（`_variance_units` 给的那个单位：按设计是初级抽样单位、只聚类是簇、
    都没有是每行自己），跟标准误那三条路同一个口径。整群抽样的数据按行重抽会把区间算窄——
    而「区间算窄」正是那种不报错的错。声明了分层就**在层内抽**。

    种子由调用方按计划与数据定死：同一份数据同一份计划跑两次，区间要一字不差。
    """
    rng = np.random.default_rng(seed)
    uniq, inv = np.unique(units, return_inverse=True)
    rows_of = [np.flatnonzero(inv == i) for i in range(len(uniq))]
    if strata is None:
        pools = [np.arange(len(uniq))]
    else:                                   # 层内抽：每层抽出跟原来一样多的单位
        by_stratum: dict = {}
        for i in range(len(uniq)):
            by_stratum.setdefault(strata[rows_of[i][0]], []).append(i)
        pools = [np.asarray(v) for v in by_stratum.values()]

    out = np.full((MEDIAN_BOOT, n_cells), np.nan)
    for b in range(MEDIAN_BOOT):
        picked = np.concatenate([rng.choice(pool, size=len(pool), replace=True) for pool in pools])
        rows = np.concatenate([rows_of[i] for i in picked]) if len(picked) else np.array([], dtype=int)
        if not len(rows):
            continue
        yb, cb = y[rows], codes[rows]
        wb = w[rows] if w is not None else None
        for c in range(n_cells):
            m = cb == c
            if m.any():
                out[b, c] = _weighted_quantiles(yb[m], wb[m] if wb is not None else None, (50,))["p50"]
    return out


def _fit_cells(df: pd.DataFrame, plan: AnalysisPlan, y: np.ndarray, D: np.ndarray) -> tuple[np.ndarray, _SE]:
    """y 对一组互斥的指示变量（每格一列、不带截距）做（加权）最小二乘：系数就是各格的均值。

    **用全样本加指示变量，不把每一格切出来单算**——按设计的方差这样才是正确的子总体（domain）估计：
    某一格在某个抽样单位里没有人，那个抽样单位照样留在方差里。方差三路（设计 → 聚类 → HC1）照 `run_ols_fe`，
    调同一个抽出来的函数 `_vcov_three_way`。返回 `(系数, 三路的结果)`：协方差、设计信息、「标准误」的说明、
    p 值与区间的临界值都在后者里。
    """
    y = np.asarray(y, dtype=float)
    X = np.asarray(D, dtype=float)
    if plan.weights:
        # 加权最小二乘 = 对 √w 缩放后的数据做普通最小二乘，跟 `run_ols_fe` 同一个精确等价
        rt = np.sqrt(df[plan.weights].to_numpy(dtype=float))
        y = y * rt
        X = X * rt[:, None]
    xtx_inv = np.linalg.inv(X.T @ X)
    beta = xtx_inv @ (X.T @ y)
    return beta, _vcov_three_way(X, y - X @ beta, xtx_inv, 0, df, plan)


def _trend_slope(rows: pd.DataFrame, plan: AnalysisPlan, over: str) -> Estimate:
    """趋势的主结果：`y ~ over` 的斜率。再调一次 `run_ols_fe`（同一批行，权重、抽样设计、聚类层照带）；
    系数与标准误取没取整的拟合值（`_fit`），p 值与自由度取它的 `main`。"""
    if pd.to_numeric(rows[over], errors="coerce").isna().any():
        raise PlanRejected([f"趋势的时间列「{over}」不全是数值，算不了斜率——在 prep.py 里转成数值（例如年份）"])
    if rows[over].nunique() < 2:
        raise PlanRejected([f"趋势的时间列「{over}」在估计样本里只有一期，算不了趋势"])
    r = run_ols_fe(rows, AnalysisPlan(
        plan_id=plan.plan_id, idea_id=plan.idea_id, estimator="ols_fe", outcome=plan.outcome, treatment=over,
        cluster_level=plan.cluster_level, weights=plan.weights, survey=plan.survey))
    fit, est = r._fit, r.estimates["main"]
    i = fit.terms.index(over)
    return Estimate(term=over, coef=float(fit.beta[i]), se=float(np.sqrt(max(fit.vcov[i][i], 0.0))),
                    p=est.p, dof=est.dof)


def _variance_units(df: pd.DataFrame, plan: AnalysisPlan, se3: _SE | None = None) -> tuple[np.ndarray, str, str]:
    """每行进方差的那个单位，跟 `_vcov_three_way` 实际走的那一路同一口径：按设计是初级抽样单位（编号嵌在层里，
    `_psu_ids`），只写聚类层是簇，都没有（HC1）是每行自己。返回三样：单位编号、单位的叫法、「只落在一个单位里」的说法。

    **一格只落在一个单位里，这一格的方差是假的 0**：正规方程让它在那个单位里的得分之和恰好为 0，
    单位之间没有任何变异可算——标准误 6e-17、区间缩成均值那一点，比哪一格都「精确」。
    """
    # 设计从计划直接判（`_design_of`，跟 `_vcov_three_way` 同一个判据）：这样功效那一步不用先拟合
    # 就能问「该抽哪个单位」，两处不各写一套
    design = se3.design if se3 is not None else _design_of(plan, df)
    if design is not None:
        return _psu_ids(df, design), "初级抽样单位", "整格落在一个初级抽样单位里"
    if plan.cluster_level and plan.cluster_level in df.columns:
        return df[plan.cluster_level].to_numpy(), "聚类单位", "整格落在一个聚类单位里"
    return np.arange(len(df)), "观测", "整格只有一个观测"


def mean_ci(mean: float, se: float, crit: float, proportion: bool = False) -> tuple[float, float]:
    """均值或比例的 95% 区间。连续结局：均值 ± 临界值 × 标准误。

    0/1 结局（`proportion`）按 logit 尺度（Stata `svy: proportion` 的缺省）：`se_l = se / (p(1−p))`，
    区间 = `expit(logit(p) ± 临界值 × se_l)`——Wald 区间在比例贴近 0 或 1 时会出 [0, 1]（1% 的比例、几百人，
    下限就是负的）。比例恰是 0 或 1：区间就是那一点 [p, p]。组间差照旧按 Wald。
    分组表的每一格（`_cell_ci`）与主结果表里水平、分布的全样本均值（`manuscript._mean_ci`）都用它，两处一个口径。
    """
    if not proportion:
        return mean - crit * se, mean + crit * se
    if not 0 < mean < 1:
        return mean, mean
    lg, sl = float(np.log(mean / (1 - mean))), se / (mean * (1 - mean))
    return float(1 / (1 + np.exp(-(lg - crit * sl)))), float(1 / (1 + np.exp(-(lg + crit * sl))))


def _cell_ci(mean: float, se: float, crit: float, y_cell: np.ndarray | None) -> tuple[float, float]:
    """一格的 95% 区间（`mean_ci`）。连续结局 `y_cell` 是 None；0/1 结局传这一格的 y：全是 0 或全是 1 时区间就是那一点——
    直接看 y，不靠均值恰好算成 0 或 1（加权最小二乘的均值可能差一个末位）。"""
    if y_cell is not None and y_cell.min() == y_cell.max():
        return mean, mean
    return mean_ci(mean, se, crit, proportion=y_cell is not None)


def median_halfwidth(frame: pd.DataFrame, plan: AnalysisPlan) -> tuple[float, str] | None:
    """中位数计划的**事前精度**：主要比较的 95% 区间半宽（结局原单位）加一句说明；算不了返回 None。

    **跟 `oss run` 里那条自助法同一段代码**——同样的选行、同样的抽样单位、同样的次数与种子。
    计划写中位数、功效却按均值的解析公式算半宽，就是把门挂在声明上而不是挂在算得出的事实上
    （2026-09-23 一整天都在修这一类洞）。

    **只回半宽，不回点估计**：事前这一步不该把答案先算给人看。
    """
    d = plan.description
    if d is None or d.statistic != "median":
        return None
    try:
        by = _describe_by(plan)
        df, _, _ = _ols_rows(frame, plan, [plan.outcome, *by])
        y = df[plan.outcome].to_numpy(dtype=float)
        w = df[plan.weights].to_numpy(dtype=float) if plan.weights else None
        if by:
            grouped = df.groupby(by, sort=True, observed=True)
            codes = grouped.ngroup().to_numpy()
            n_cells = int(grouped.ngroup().max()) + 1
        else:
            codes, n_cells = np.zeros(len(df), dtype=int), 1
        units, unit_word, _ = _variance_units(df, plan)
        design = _design_of(plan, df)
        strata = (df[design.strata].to_numpy() if design is not None and design.strata
                  and design.strata in df.columns else None)
        seed = int(hashlib.sha256(f"{plan.plan_id}|{plan.outcome}|{len(df)}".encode()).hexdigest()[:8], 16)
        boot = _median_boot(y, w, codes, n_cells, units, strata, seed)
        if d.kind == "gap" and n_cells >= 2:
            pair = first_two([int((codes == c).sum()) for c in range(n_cells)])
            draws = boot[:, pair[1]] - boot[:, pair[0]]
            what = "组间中位数差"
        else:
            draws = boot[:, 0]
            what = "中位数"
        draws = draws[~np.isnan(draws)]
        if len(draws) < 2 or draws.min() == draws.max():
            return None
        half = float(np.percentile(draws, 97.5) - np.percentile(draws, 2.5)) / 2
        return half, (f"{what}的半宽由 {MEDIAN_BOOT} 次自助法算出（重抽{unit_word}，跟 oss run 同一段代码），"
                      f"不是均值的解析公式——中位数的标准误没有解析式")
    except (PlanRejected, KeyError, ValueError):
        return None


def run_describe(frame: pd.DataFrame, plan: AnalysisPlan) -> AnalysisResults:
    """分组描述（`describe`；描述骨架设计稿第五节）：各格的均值 / 比例（带区间）、对参照组的差、趋势斜率、分布的分位数。

    - 格子：`description.groups`（至多两列）交叉；`kind: trend` 再按 `over` 分期。标签写成 `列=值` 或 `列=值 × 列=值`，
      没有分组时只有一格「全样本」。类别列只取出现过的类。参照组与主要比较的那一格是头两个有行的格
      （`oss.core.power.first_two`，环节 4 的精度用同一个）——每一格都有行，就是排序后的头两格（数值列按数值排）
    - 行：跟主回归同一套选行（`_ols_rows`：样本筛选、结局与分组列与权重与设计列去缺失、非正权重拒）
    - 均值与协方差：`_fit_cells`；组间差 = 该格 − 参照格，方差 V_cc + V_rr − 2V_cr；区间用设计自由度的 t（没声明设计用 1.96）
    - `main`：level → 全样本均值；gap → 排序后第二格对参照格的差；trend → `y ~ over` 的斜率（再调一次 `run_ols_fe`，
      权重与设计照带）；distribution → 全样本均值，另把 p10 / p25 / p50 / p75 / p90 的加权分位数（inverted CDF：累计权重
      份额第一次到 q 的那个值）写进 `quantiles`
    - 每格的有效样本量：按抽样设计或聚类时是 样本量 ÷ 设计效应（`min(kish, s²_w / se²)`，Kish 的 deff；s² 或 se 为 0
      退回 Kish），封顶在这一格自己的 Kish 有效样本量，不是行数——只落在两三个抽样单位里的格，标准误只靠一两个自由度，
      碰巧很小时 s²/se² 会冲过 Kish，小格判据就不开了。跟环节 4 的最小分组同一个概念（样本量 ÷ 设计效应），都连整群
      一起算；但环节 4 是事前按假定的组内相关算的，这里是按实际的设计标准误算的，两者可以不同。不按设计也不聚类时，
      加权是 Kish 的 (Σw)²/Σw²、不加权是行数
    - 一格只落在一个方差单位里（`_variance_units`：初级抽样单位 / 簇 / 观测）：方差是假的 0，区间与组间差留空、
      有效样本量记所跨的单位数、诊断「区间留空的分组」点名；参照格留空时各格的差全空；主结果要用它就拒
    - 0/1 结局：各格的比例区间按 logit 尺度（`_cell_ci`，同 Stata `svy: proportion`），组间差照旧 Wald
    - 诊断：`估计量`（「分组描述（加权均值 / 比例，按设计的区间）」之类）、`标准误`、三个设计数值键（有设计时）、`抽样权重`（`_weights_text`）、
      `主结果`（main 是哪个量）；有缺失丢弃、样本筛选时照 `run_ols_fe` 各报一条
    - `robustness` 照旧跑（`_run_robustness`）
    - 带了处理、控制、固定效应之类（`_describe_extras`）就拒——稳健性补丁加了控制的那一项因此记为出错，
      不再原样照抄主结果
    """
    if extras := _describe_extras(plan):
        raise PlanRejected([_describe_extras_problem(extras)])
    d = plan.description
    kind = d.kind if d is not None else ""
    if kind not in ("level", "distribution", "gap", "trend"):
        raise PlanRejected([f"describe 要写 description.kind：level / distribution / gap / trend（现在是「{kind or '空'}」；"
                            f"控制了别的变量以后的关系是 association，走 ols_fe）"])
    if kind == "gap" and not d.groups:
        raise PlanRejected(["组间差（kind: gap）要写 description.groups：比较哪几组"])
    if kind == "trend" and not d.over:
        raise PlanRejected(["趋势（kind: trend）要写 description.over：看哪段时间"])
    by = _describe_by(plan)
    df, before, dropped = _ols_rows(frame, plan, [plan.outcome, *by])
    y = df[plan.outcome].to_numpy(dtype=float)
    w = df[plan.weights].to_numpy(dtype=float) if plan.weights else None
    n = len(df)

    # 格子：排序后编号（数值列按数值排）。类别列只取样本里出现过的类（observed=True）：pandas 3 以前缺省是 False，
    # 没用到的类也出一格，空格让 X'X 奇异
    if by:
        grouped = df.groupby(by, sort=True, observed=True)
        codes = grouped.ngroup().to_numpy()
        keys = [k if isinstance(k, tuple) else (k,) for k in grouped.size().index]
        labels = [" × ".join(f"{c}={_level_text(v)}" for c, v in zip(by, key)) for key in keys]
    else:
        codes, labels = np.zeros(n, dtype=int), ["全样本"]
    # 参照格与主要比较的那一格：头两个有行的格（`first_two`），跟环节 4 的精度同一个定义。
    # 上面只取出现过的类，每一格都有行，所以就是排序后的头两格
    pair = first_two(np.bincount(codes, minlength=len(labels)).tolist())
    ref_c = pair[0]
    D = np.zeros((n, len(labels)))
    D[np.arange(n), codes] = 1.0
    beta, se3 = _fit_cells(df, plan, y, D)
    V, crit, many = se3.vcov, se3.crit(), len(labels) >= 2

    # 一格只落在一个方差单位里（`_variance_units`）：方差是假的 0——区间与组间差留空，有效样本量记所跨的单位数
    units, unit_word, lone_where = _variance_units(df, plan, se3)
    n_units = [int(pd.unique(units[codes == c]).size) for c in range(len(labels))]
    lone = [c for c in range(len(labels)) if n_units[c] < 2]
    # 按设计或聚类时，有效样本量 = 样本量 ÷ 设计效应（s²_w / se²），封顶在这一格的 Kish；不然照旧 Kish
    by_design = se3.design is not None or bool(plan.cluster_level and plan.cluster_level in df.columns)
    binary = bool(np.isin(y, (0.0, 1.0)).all())          # 0/1 结局：均值是比例，区间按 logit 尺度（`_cell_ci`）

    # 中位数：点估计用加权分位数，区间靠自助法（设计稿 docs/design/2026-09-23-median-gap.md）。
    # **抽的是方差单位**（跟标准误同一个口径），声明了分层就在层内抽
    stat = (d.statistic if d is not None else "mean") or "mean"
    boot = None
    if stat == "median":
        if binary:
            raise PlanRejected([
                f"0/1 结局不许用中位数：`{plan.outcome}` 只取 0 与 1，它的中位数不是 0 就是 1，说明不了任何事。"
                "比例问题用 statistic: mean（均值即比例）"])
        if kind not in ("level", "gap"):
            raise PlanRejected([f"中位数这一版只支持 level 与 gap（现在是 {kind}）：趋势要的是分位数回归，另一套估计量"])
        strata = getattr(se3.design, "strata_ids", None) if se3.design is not None else None
        seed = int(hashlib.sha256(f"{plan.plan_id}|{plan.outcome}|{n}".encode()).hexdigest()[:8], 16)
        boot = _median_boot(y, w, codes, len(labels), units, strata, seed)

    cells: list[GroupCell] = []
    for c, label in enumerate(labels):
        m = codes == c
        mean = float(beta[c]) if boot is None else _weighted_quantiles(
            y[m], w[m] if w is not None else None, (50,))["p50"]
        se_c = gap = gap_se = gap_p = None
        ci: tuple[float | None, float | None] = (None, None)
        n_eff = float(n_units[c])
        if boot is not None and c not in lone:
            # 重抽分布恒定 = 这一格没有可算的变异，照均值那条路的规矩留空
            draws = boot[:, c][~np.isnan(boot[:, c])]
            if len(draws) and draws.min() < draws.max():
                se_c = float(draws.std(ddof=1))
                ci = (float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5)))
            n_eff = _effective_n(w[m]) if w is not None else float(m.sum())
            if many and c != ref_c and ref_c not in lone:
                gaps = boot[:, c] - boot[:, ref_c]
                gaps = gaps[~np.isnan(gaps)]
                ref_m = codes == ref_c
                gap = mean - _weighted_quantiles(
                    y[ref_m], w[ref_m] if w is not None else None, (50,))["p50"]
                if len(gaps) and gaps.min() < gaps.max():
                    gap_se = float(gaps.std(ddof=1))
                    # 自助法的 p：两尾里小的那个乘二（差恰好为 0 的重抽算进两边，宁可保守）
                    lo = float(np.mean(gaps <= 0))
                    hi = float(np.mean(gaps >= 0))
                    gap_p = round(min(1.0, 2 * min(lo, hi)), 6)
        elif c not in lone:
            se_c = float(np.sqrt(max(V[c, c], 0.0)))
            ci = _cell_ci(mean, se_c, crit, y[m] if binary else None)
            n_eff = _effective_n(w[m]) if w is not None else float(m.sum())
            if by_design:
                s2 = float(np.average((y[m] - mean) ** 2, weights=w[m] if w is not None else None))
                if se_c > 0 and s2 > 0:
                    # 封顶在这一格自己的 Kish（上面刚算的 n_eff），不是行数：只落在两三个抽样单位里的格，标准误只靠
                    # 一两个自由度，碰巧很小时 s²/se² 会冲到行数、比 Kish 还大，小格判据（small_cells）就不开了
                    n_eff = min(n_eff, s2 / se_c ** 2)
            if many and c != ref_c and ref_c not in lone:  # 参照格留空：各格对它的差都算不出来
                gap = mean - float(beta[ref_c])
                gap_se = float(np.sqrt(max(V[c, c] + V[ref_c, ref_c] - 2 * V[c, ref_c], 0.0)))
                gap_p = round(float(se3.p(gap / gap_se if gap_se > 0 else 0.0)), 6)
        cells.append(GroupCell(
            label=label, n=int(m.sum()), n_eff=n_eff, mean=mean, se=se_c, ci_lo=ci[0], ci_hi=ci[1],
            statistic=stat, proportion=binary and stat == "mean",
            reference=many and c == ref_c, gap=gap, gap_se=gap_se, gap_p=gap_p))

    quantiles: dict[str, float] = {}
    if kind == "gap":
        if not many:
            raise PlanRejected([f"组间差只剩一组（{labels[0]}）：分组列在估计样本里只有一个取值，没有可比的"])
        if bad := [c for c in pair if c in lone]:
            raise PlanRejected([
                f"组间差的主结果要用的分组{'、'.join(f'「{labels[c]}」' for c in bad)}{lone_where}："
                f"方差算不出来（只有一个{unit_word}，{unit_word}之间没有变异，标准误会是假的 0）"
                f"——在 prep.py 里把它并进相邻的组（重编码分组列），或换一个分组"])
        ref, other = cells[pair[0]], cells[pair[1]]
        main = Estimate(term=other.label, coef=other.gap, se=other.gap_se, p=other.gap_p, dof=se3.dof)
        what = f"排序后第二组（{other.label}）减参照组（{ref.label}）的差"
    elif kind == "trend":
        main = _trend_slope(df, plan, d.over)
        what = f"{plan.outcome} 对 {d.over} 的线性斜率（每一单位 {d.over} 的变化；权重与抽样设计照带）"
    else:
        if pd.unique(units).size < 2:                   # 主结果是全样本均值：全样本只落在一个单位里，同样是假的 0
            raise PlanRejected([
                f"全样本只落在一个{unit_word}里：方差算不出来（{unit_word}之间没有变异，标准误会是假的 0）"
                f"——主结果（全样本均值）没有区间可言；换一个聚类层级，或不聚类"])
        if by:
            b1, s1 = _fit_cells(df, plan, y, np.ones((n, 1)))
            mean, se_m = float(b1[0]), float(np.sqrt(max(s1.vcov[0, 0], 0.0)))
        else:
            mean, se_m = cells[0].mean, cells[0].se
        main = Estimate(term=plan.outcome, coef=mean, se=se_m,
                        p=round(float(se3.p(mean / se_m if se_m > 0 else 0.0)), 6), dof=se3.dof)
        what = "全样本均值"
        if kind == "distribution":
            quantiles = _weighted_quantiles(y, w, _QUANTILES)
            what += f"；分布另报{'加权' if w is not None else ''}分位数 {'、'.join(quantiles)}"

    diagnostics = {
        "估计量": (f"分组描述（{'加权' if w is not None else ''}"
                 + (f"中位数，{MEDIAN_BOOT} 次自助法区间（重抽{unit_word}）" if stat == "median"
                    else f"均值 / 比例{'，按设计的区间' if se3.info is not None else ''}") + "）"),
        "标准误": se3.text,
        **se3.diag(),
        "抽样权重": _weights_text(w, plan.weights, n),
        "主结果": what,
    }
    if binary:          # 稳健性表逐行按这一条定区间的尺度（`manuscript._robust_mean_ci`）
        diagnostics[PROPORTION_DIAG] = "结局只取 0 与 1：均值是比例，区间按 logit 尺度（同 Stata svy: proportion）"
    if lone:
        diagnostics["区间留空的分组"] = (
            f"{'、'.join(labels[c] for c in lone)}：{lone_where}，方差算不出来（{unit_word}之间没有变异，"
            f"标准误会是假的 0）——均值照报，区间、组间差与 p 留空，有效样本量记为所跨的{unit_word}数"
            + ("；参照组也在其中，各组对它的差全部留空" if many and 0 in lone else "")
            + "。要比较就在 prep.py 里把它并进相邻的组")
    if by_design:
        diagnostics["分组有效样本量"] = (
            ("分组的有效样本量按抽样设计算（样本量 ÷ 设计效应）" if se3.design is not None
             else f"分组的有效样本量按聚类（{plan.cluster_level}）算（样本量 ÷ 设计效应）")
            + "：设计效应 = 这一格的方差 ÷ 同样本量简单随机抽样的方差（Kish 的定义；NCHS 的比例标准用同一个有效样本量），"
              "整群与权重的折扣都在里面，封顶在这一格的 Kish 有效样本量。跟环节 4 同一个概念（样本量 ÷ 设计效应）；"
              "环节 4 是事前按假定的组内相关算的，这里是按实际的设计标准误算的，两者可以不同。"
              "分组表不列它，小格判据（small_cells）读它")
    if dropped:
        diagnostics["因缺失丢弃"] = f"{dropped} 行（{dropped / max(before, 1):.1%}）"
    if plan.sample_filter:
        diagnostics["样本筛选"] = plan.sample_filter
    return AnalysisResults(
        plan_id=plan.plan_id, estimates={"main": main}, n=int(n), groups=cells, quantiles=quantiles,
        diagnostics=diagnostics, robustness=_run_robustness(frame, plan),
        log=f"分组描述，{len(labels)} 格，{n} 个观测，{se3.text}",
    )


# ────────────────────────────────────────────── ⑱ 二值与有序 Logit（2026-09-25）
#
# 为什么补：中文社科的因变量大量是 1–5 分的态度量表（公平感、幸福感、阶层认同），
# 标准做法是有序 Logit。找复现论文时筛了九篇，**三篇都用了我们跑不了的估计量**。
# 设计稿 `docs/design/2026-09-25-logit-multilevel.md`。
#
# 算在 `oss/core/discrete.py`（手写牛顿法，不加依赖），**门在这里**。

#: 固定效应 logit 的每组最少观测数。低于它拒绝——incidental parameters 问题：
#: 组内观测少时**点估计本身有偏**（组内 2 个观测时偏 100%），不是标准误的问题。
#: 0/1 结局的 `ols_fe` 就是线性概率模型，固定效应在里面是干净的，所以有现成的去处。
_LOGIT_MIN_PER_GROUP = 10


def _logit_fe_problem(df: pd.DataFrame, plan: AnalysisPlan) -> Finding | None:
    """门 L2：固定效应太多就拒。**判据是算得出来的事实**（每组平均观测数），
    不是作者声明用了几个固定效应。"""
    if not plan.fixed_effects:
        return None
    cells = df.groupby(list(plan.fixed_effects), dropna=False).ngroups
    per = len(df) / max(cells, 1)
    if per >= _LOGIT_MIN_PER_GROUP:
        return None
    return Finding(
        f"固定效应 {'、'.join(plan.fixed_effects)} 展开后有 {cells} 组、每组平均 {per:.1f} 个观测"
        f"（少于 {_LOGIT_MIN_PER_GROUP}）。**logit 加固定效应有 incidental parameters 问题**："
        f"组内观测少时点估计本身有偏，不是标准误的问题，而且偏的方向不定。\n"
        f"  改用 estimator: ols_fe——0/1 结局的 OLS 就是**线性概率模型**，"
        f"固定效应在里面是干净的，系数还能直接读成「概率高 X 个百分点」",
        "plan.logit.fixed_effects")


def _parallel_lines_diag(X: np.ndarray, y: np.ndarray, w: np.ndarray | None,
                         terms: list[str]) -> tuple[str, bool]:
    """门 L3：有序 logit 的平行线（比例优势）假定。返回 (诊断文字, 是否不成立)。

    **有序 logit 的核心假定是：同一个自变量对「≥2 分」和「≥4 分」的影响相同。**
    几乎没人验它，而它不成立时报出来的那一个系数是几段不同效应的某种平均，
    对哪一段都不成立——**这是发表文献里普遍存在、审稿人很少问的洞**。

    算法在 `discrete.parallel_lines`（Brant 检验）。第一版把各切点当独立处理，
    在平行线**明明成立**的模拟数据上也给 p≈0——**会误报的门比没有门更糟**，
    修法是把统计量算对，不是调阈值。
    """
    from oss.core import discrete

    try:
        stat, dof, betas = discrete.parallel_lines(X, y, w)
    except discrete.NotConverged as exc:
        return f"检验不了：{exc}", False
    if dof <= 0:
        return "检验不了：切点不足两个", False
    p = _chi2_sf(stat, dof)
    bad = p < 0.05
    spread = "；".join(
        f"{t}：{'、'.join(f'{float(b[i]):+.3f}' for b in betas)}"
        for i, t in enumerate(terms) if i < len(betas[0]))
    head = ("**平行线假定不成立**" if bad else "平行线假定未被推翻")
    return (f"{head}（Brant χ²={stat:.2f}，自由度 {dof}，p={p:.4f}）。"
            f"各切点分别估的系数：{spread}。"
            + ("**不成立意味着报出来的那一个系数对哪一段都不成立**——"
               "要么换成分段的设定（广义有序 logit，本工具不做），"
               "要么降成二值 logit，要么写进局限并说明为什么可以接受"
               if bad else "")), bad


def _chi2_sf(stat: float, dof: int) -> float:
    """卡方上尾概率（Wilson–Hilferty 近似）。**只用来判量级**——
    门按 0.05 判，近似在那一带的误差远小于判据本身的模糊度。"""
    if dof <= 0 or stat <= 0:
        return 1.0
    from math import erf, sqrt
    z = ((stat / dof) ** (1 / 3) - (1 - 2 / (9 * dof))) / sqrt(2 / (9 * dof))
    return float(1 - 0.5 * (1 + erf(z / sqrt(2))))


def _logit_common(frame: pd.DataFrame, plan: AnalysisPlan) -> tuple:
    """logit / ologit 共用的备料：交互项展开、选行、取 y 与 X、权重。

    **跟 `run_ols_fe` 走同一套选行**（`_ols_rows`：样本筛选、结局与权重与设计列去缺失、
    非正权重拒）——两个估计量的样本口径不一致的话，稳健性表里换个估计量
    样本量就跳，而那看起来像数据问题。
    """
    frame = _expand_interactions(frame, [
        *plan.interactions,
        *([plan.treatment] if is_interaction(plan.treatment) else []),
    ])
    extra = [i for i in plan.interactions if i != plan.treatment]
    cols = [plan.outcome, plan.treatment, *plan.controls, *extra]
    df, before, dropped = _ols_rows(frame, plan, cols)
    terms = [plan.treatment, *plan.controls, *extra]
    y = df[plan.outcome].to_numpy(dtype=float)
    X = df[terms].to_numpy(dtype=float)
    w = df[plan.weights].to_numpy(dtype=float) if plan.weights else None
    return frame, df, before, dropped, terms, y, X, w


def run_logit(frame: pd.DataFrame, plan: AnalysisPlan) -> AnalysisResults:
    """二值 logit。**主结果是平均边际效应，不是系数。**

    系数是对数几率，读者判断不了量级；odds ratio 也不行——「几率高 1.8 倍」
    在基线概率 2% 和 40% 的组里是完全不同的两回事。AME 读成「概率高 X 个百分点」，
    跟线性概率模型可比，也符合摘要规范里已经写下的立场。

    **这不是「终于能做 0/1 结局」**：`ols_fe` 跑 0/1 结局就是线性概率模型，
    而 LPM 在经济学里是主流（系数可读、固定效应干净）。logit 是多一个选择，
    说明书写清怎么选。

    标准误走 `_vcov_three_way` 同一条路——夹心的「面包」是逆海森，「肉」是得分
    （OLS 那边是残差）；形状一样，所以复用。
    """
    from oss.core import discrete

    frame, df, before, dropped, terms, y, X, w = _logit_common(frame, plan)
    vals = set(np.unique(y).tolist())
    if not vals <= {0.0, 1.0}:
        raise PlanRejected([Finding(
            f"logit 的结局必须是 0/1，「{plan.outcome}」有 {len(vals)} 个取值"
            + ("——三个以上的有序取值用 estimator: ologit" if len(vals) > 2 else ""),
            "plan.logit.outcome")])
    if (bad := _logit_fe_problem(df, plan)) is not None:
        raise PlanRejected([bad])

    Xc = np.column_stack([np.ones(len(X)), X])      # logit 要截距
    try:
        fit = discrete.binary_logit(Xc, y, w)
    except discrete.Separated as exc:
        cols = [([" 截距"] + terms)[i] for i in exc.cols]
        raise PlanRejected([Finding(
            f"{exc}\n  涉及的列：{'、'.join(cols)}", "plan.logit.separated")]) from exc
    except discrete.NotConverged as exc:
        raise PlanRejected([Finding(str(exc), "plan.logit.not_converged")]) from exc

    scores = discrete.score_matrix(Xc, y, fit.beta, w)
    se3 = _vcov_three_way(scores, np.ones(len(y)), fit.vcov, 0, df, plan)
    eff, eff_se = discrete.ame(Xc, fit.beta, se3.vcov, w)

    n = len(y)
    estimates: dict[str, Estimate] = {}
    for i, term in enumerate(terms, start=1):       # 跳过截距
        key = "main" if term == plan.treatment else term
        z = eff[i] / eff_se[i] if eff_se[i] > 0 else 0.0
        estimates[key] = Estimate(
            term=term, coef=_sig(float(eff[i])), se=_sig(float(eff_se[i])),
            p=round(float(se3.p(z)), 6), dof=se3.dof)
    diagnostics = {
        "估计量": ("加权二值 logit（最大似然）" if w is not None else "二值 logit（最大似然）")
                 + "；**报的是平均边际效应，不是系数**（系数是对数几率，读者判断不了量级）",
        "标准误": se3.text,
        "固定效应": "、".join(plan.fixed_effects) or "无",
        "对数似然": f"{fit.llf:.2f}",
        "迭代": f"{fit.n_iter} 轮",
        "logit 系数": "；".join(
            f"{t} {float(fit.beta[i]):+.4f}" for i, t in enumerate(terms, start=1)),
    }
    diagnostics.update(se3.diag())
    diagnostics["抽样权重"] = _weights_text(w, plan.weights, n)
    # 丢行与样本筛选照 `run_ols_fe` 的写法记进诊断——**两个估计量的口径要一样**
    if dropped:
        diagnostics["因缺失丢弃"] = f"{dropped} 行（{dropped / max(before, 1):.1%}）"
    if plan.sample_filter:
        diagnostics["样本筛选"] = plan.sample_filter
    res = AnalysisResults(plan_id=plan.plan_id, estimates=estimates, n=n,
                          diagnostics=diagnostics)
    res.robustness = _run_robustness(frame, plan)
    return res


def run_iptw(frame: pd.DataFrame, plan: AnalysisPlan) -> AnalysisResults:
    """逆概率加权（IPTW），**双稳健**：倾向得分加权 + 结局方程照样带控制变量。

    做法：把处理变量对控制变量做 logit 得到倾向得分，取稳定化的逆概率权重，
    乘上抽样权重，然后**走已有的加权最小二乘那条路**——于是聚类稳健、
    按抽样设计算标准误、固定效应吸收这一整套一行都不用改。
    结局方程里保留控制变量是有意的：**得分模型与结局模型有一个对了就一致**
    （双稳健），两个都赌在得分上没有好处。

    **只做加权，不做配对。**倾向得分配对在社科里用得更多，但 King 与 Nielsen（2019）
    的结果是：配到一定程度之后**再往下配反而让协变量更不平衡**——最近邻配对是拿一个
    一维的得分去近似多维的平衡，得分相近不等于协变量相近，而每删一对就多丢一次信息。
    加权不丢样本，平衡程度还能逐列量出来。

    **这个方法不解决未观测的混淆。**平衡表好看与因果成立是两件事——
    跟工具变量那边「第一阶段强不强」与「排他性成不成立」是两件事完全同构。
    所以识别审计（环节 7.5）照样要过，诊断里也照实写这一句。
    """
    from oss.core import discrete as discrete_mod
    from oss.core import propensity

    frame = _expand_interactions(frame, [
        *plan.interactions,
        *([plan.treatment] if is_interaction(plan.treatment) else []),
    ])
    if not plan.controls:
        raise PlanRejected([Finding(
            "iptw 至少要有一个控制变量——倾向得分是「处理对控制变量的 logit」，"
            "没有控制变量就没有得分可言，加权退化成不加权", "plan.iptw.no_controls")])

    extra = [i for i in plan.interactions if i != plan.treatment]
    cols = [plan.outcome, plan.treatment, *plan.controls, *extra]
    df, before, dropped = _ols_rows(frame, plan, cols)

    t_vals = set(np.unique(df[plan.treatment].to_numpy(dtype=float)).tolist())
    if not t_vals <= {0.0, 1.0}:
        raise PlanRejected([Finding(
            f"iptw 的处理变量必须是 0/1，「{plan.treatment}」有 {len(t_vals)} 个取值——"
            f"连续的处理强度没有「被处理的概率」可言（那要广义倾向得分，本工具没有）",
            "plan.iptw.treatment_not_binary")])

    tt = df[plan.treatment].to_numpy(dtype=float)
    X = df[list(plan.controls)].to_numpy(dtype=float)
    base = df[plan.weights].to_numpy(dtype=float) if plan.weights else None
    try:
        ps = propensity.fit(X, tt, list(plan.controls), base)
    except discrete_mod.Separated as exc:
        raise PlanRejected([Finding(
            f"倾向得分的 logit 完全可分：{exc}——有控制变量能完全预测谁被处理，"
            f"那部分人根本没有可比的对照，加权也救不了", "plan.iptw.separated")]) from exc

    lo_t, hi_t, lo_c, hi_c = ps.overlap
    if min(hi_t, hi_c) <= max(lo_t, lo_c):
        raise PlanRejected([Finding(
            f"两组的倾向得分没有重叠（处理组 [{lo_t:.3f}, {hi_t:.3f}]、"
            f"对照组 [{lo_c:.3f}, {hi_c:.3f}]）——**没有共同支撑域就没有可比的人**，"
            f"这时候加权算出来的数不对应任何真实的比较", "plan.iptw.no_overlap")])

    # 把最终权重挂到帧上，走已有的加权最小二乘那条路
    wcol = "_iptw_weight"
    df = df.copy()
    df[wcol] = ps.weight
    sub = plan.model_copy(update={"weights": wcol, "sample_filter": ""})
    res = run_ols_fe(df, sub)

    bad = propensity.worst(ps.balance)
    n = len(df)
    res.diagnostics["估计量"] = (
        "逆概率加权（IPTW，稳定化权重）+ 结局方程保留控制变量（**双稳健**："
        "得分模型与结局模型有一个对了就一致）。"
        "**只做加权不做配对**：配对到一定程度反而让协变量更不平衡（King 与 Nielsen 2019）")
    res.diagnostics["倾向得分重叠"] = (
        f"处理组 [{lo_t:.3f}, {hi_t:.3f}]，对照组 [{lo_c:.3f}, {hi_c:.3f}]；"
        f"落在共同支撑域之外的 {ps.n_off_support} 人（{ps.n_off_support / max(n, 1):.1%}）"
        + (f"；{ps.n_clipped} 人的得分被截到 {propensity.PS_FLOOR} / "
           f"{1 - propensity.PS_FLOOR}（太靠近 0 或 1 的得分会产生巨大的权重）"
           if ps.n_clipped else ""))
    res.diagnostics["加权前后的平衡"] = (
        "标准化均值差（越小越平；常用上界 "
        f"{propensity.SMD_MAX}）：" + "；".join(
            f"{b.name} {b.smd_before:.3f}→{b.smd_after:.3f}" for b in ps.balance)
        + ("。**" + "、".join(f"{b.name}（{b.smd_after:.3f}）" for b in bad[:4])
           + f" 加权之后仍然没调平**——IPTW 只调得平它看得见的东西，"
             f"这几列没调平说明得分模型设得不对（缺了项、函数形式不对，或者两组本来就不可比）"
           if bad else "。全部调到上界以内"))
    res.diagnostics["权重的集中度"] = (
        f"加权之后的有效样本量 {ps.ess:.0f}（实际 {n} 行）；"
        f"单个观测最多占总权重的 {ps.max_share:.2%}。"
        f"**有效样本量掉得多、或者单点占比高，说明估计靠少数几个人撑着**——"
        f"区间要按有效样本量读")
    res.diagnostics["未观测的混淆"] = (
        "**加权不解决它。**IPTW 属于「可观测选择」那一族：它把你测到的协变量调平，"
        "对没测到的东西一点办法都没有。平衡表好看与因果成立是两件事——"
        "跟工具变量那边「第一阶段强不强」与「排他性成不成立」是两件事完全同构")
    if dropped:
        res.diagnostics["因缺失丢弃"] = f"{dropped} 行（{dropped / max(before, 1):.1%}）"
    if plan.sample_filter:
        res.diagnostics["样本筛选"] = plan.sample_filter
    res.robustness = _run_robustness(frame, plan)
    return res


def run_ologit(frame: pd.DataFrame, plan: AnalysisPlan) -> AnalysisResults:
    """有序 logit（比例优势模型）。**主结果是系数，并且必须验平行线假定。**

    有序的边际效应要按每个取值算（J 个，不是一个），放进 `main` 反而说不清，
    所以这里报系数，**而平行线假定的判词进诊断**——那才是这个模型能不能信的关键。

    **X 不含截距**（跟切点不可识别）；切点存进诊断。
    """
    from oss.core import discrete

    frame, df, before, dropped, terms, y, X, w = _logit_common(frame, plan)
    if len(np.unique(y)) < 3:
        raise PlanRejected([Finding(
            f"有序 logit 要至少三个有序取值，「{plan.outcome}」只有 {len(np.unique(y))} 个"
            f"——两个取值用 estimator: logit", "plan.ologit.outcome")])
    if plan.fixed_effects:
        raise PlanRejected([Finding(
            f"有序 logit 暂不支持固定效应（{'、'.join(plan.fixed_effects)}）："
            f"切点与组内截距不可识别，硬做出来的系数没有意义。\n"
            f"  把分组当控制变量的哑变量放进 controls，或改用 estimator: logit / ols_fe",
            "plan.ologit.fixed_effects")])
    try:
        fit = discrete.ordered_logit(X, y, w)
    except (discrete.NotConverged, ValueError) as exc:
        raise PlanRejected([Finding(str(exc), "plan.ologit.fit")]) from exc

    n = len(y)
    estimates = {}
    for i, term in enumerate(terms):
        key = "main" if term == plan.treatment else term
        s = float(fit.se[i])
        z = float(fit.beta[i]) / s if s > 0 else 0.0
        estimates[key] = Estimate(term=term, coef=_sig(float(fit.beta[i])), se=_sig(s),
                                  p=round(float(_normal_sf(abs(z)) * 2), 6), dof=n - len(terms))
    pl_text, pl_bad = _parallel_lines_diag(X, y, w, terms)
    diagnostics = {
        "估计量": ("加权有序 logit（比例优势）" if w is not None else "有序 logit（比例优势）"),
        "标准误": "模型标准误（观测信息矩阵）",
        "对数似然": f"{fit.llf:.2f}",
        "切点": "、".join(f"{c:+.3f}" for c in fit.cuts),
        # **永远报，不只在不成立时报。**只在出问题时才出现的诊断项，
        # 使用者看不到它就不会想到去找它——这条在别处已经栽过
        "平行线假定": pl_text,
    }
    diagnostics["抽样权重"] = _weights_text(w, plan.weights, n)
    # 丢行与样本筛选照 `run_ols_fe` 的写法记进诊断——**两个估计量的口径要一样**
    if dropped:
        diagnostics["因缺失丢弃"] = f"{dropped} 行（{dropped / max(before, 1):.1%}）"
    if plan.sample_filter:
        diagnostics["样本筛选"] = plan.sample_filter
    res = AnalysisResults(plan_id=plan.plan_id, estimates=estimates, n=n,
                          diagnostics=diagnostics)
    res.robustness = _run_robustness(frame, plan)
    return res


def _normal_sf(z: float) -> float:
    from math import erf, sqrt
    return float(1 - 0.5 * (1 + erf(z / sqrt(2))))


# ────────────────────────────────────────────── ⑲ 多层模型（2026-09-25）
#
# 算在 `oss/core/multilevel.py`（statsmodels，自己的 extra），**门在这里**。
# 设计稿第三节。**最要紧的一道是 M1：先问「你要它干什么」。**


def run_mixed(frame: pd.DataFrame, plan: AnalysisPlan) -> AnalysisResults:
    """随机截距的多层模型。**主结果是 ICC，不是某个回归系数。**

    方差分解就是这个模型存在的理由——「省际差异占总变异的几成」。
    要是只想把聚类处理掉，`ols_fe` + `cluster_level` 更稳（不假设随机效应
    服从正态、不假设它与自变量不相关），门 M1 会拦下来并指过去。

    **不加权，而且要说出来**（门 M3）：多层模型的加权要 level-1 与 level-2
    两层权重，调查数据通常只给一层。硬按一层跑出来的方差分量是错的，
    **而它看起来是对的**——所以降级照跑但写进诊断，论文的局限要照抄。
    """
    from oss.core import multilevel

    spec = plan.mixed
    # ── 门 M1：为什么用它。**这一道挡的不是技术错误，是用错工具**
    if spec is None or not spec.group:
        raise PlanRejected([Finding(
            "estimator: mixed 要写 plan.mixed：group（二层单位那一列）与 rationale（为什么用它）",
            "plan.mixed.spec_missing")])
    if not spec.rationale:
        raise PlanRejected([Finding(
            "plan.mixed.rationale 没写。**多层模型不是「处理聚类」的工具**——"
            "只为修标准误的话，estimator: ols_fe + cluster_level 更稳，"
            "它不假设随机效应服从正态、也不假设它与自变量不相关。\n"
            "  真要用多层模型，写清是哪一种：variance（方差分解本身是研究问题）"
            "／shrinkage（要各组的经验贝叶斯估计）",
            "plan.mixed.rationale_missing")])
    if spec.rationale == "se":
        raise PlanRejected([Finding(
            "plan.mixed.rationale 写的是 se——**只想处理聚类就不该用多层模型**。\n"
            f"  改用 estimator: ols_fe、cluster_level: {spec.group}："
            "聚类稳健标准误不假设随机效应的分布，也不假设它与自变量不相关，"
            "在这件事上比多层模型更稳",
            "plan.mixed.rationale_se")])
    if not multilevel.available():
        raise PlanRejected([Finding(
            f"多层模型要 statsmodels，当前没装。{multilevel.INSTALL_HINT}",
            "plan.mixed.unavailable")])

    frame = _expand_interactions(frame, [
        *plan.interactions,
        *([plan.treatment] if is_interaction(plan.treatment) else []),
    ])
    extra = [i for i in plan.interactions if i != plan.treatment]
    terms = [plan.treatment, *plan.controls, *extra]
    df, before, dropped = _ols_rows(frame, plan, [plan.outcome, *terms, spec.group])

    # ── 门 M2：二层单位够不够。**判据是算得出来的事实**
    n_groups = multilevel.group_count(df, spec.group)
    group_text, group_bad = multilevel.group_problem(n_groups, spec.group)
    if group_bad:
        raise PlanRejected([Finding(group_text, "plan.mixed.few_groups")])

    fit = multilevel.fit_random_intercept(df, plan.outcome, terms, spec.group)

    n = fit.n
    estimates = {"main": Estimate(
        term="ICC", coef=_sig(fit.icc),
        se=_sig(fit.icc_se) if fit.icc_se == fit.icc_se else 0.0,
        p=1.0, dof=max(n_groups - 1, 1))}
    for t, b, s in zip(fit.terms, fit.beta, fit.se):
        z = b / s if s > 0 else 0.0
        estimates[t] = Estimate(term=t, coef=_sig(float(b)), se=_sig(float(s)),
                                p=round(float(_normal_sf(abs(z)) * 2), 6), dof=n - len(terms))
    diagnostics = {
        "估计量": f"多层模型·随机截距（{fit.method}）；**主结果是 ICC**，不是某个回归系数",
        "用它的理由": {"variance": "方差分解本身是研究问题",
                   "shrinkage": "要各组的经验贝叶斯估计"}.get(spec.rationale, spec.rationale),
        "二层单位": f"{spec.group}（{n_groups} 个）",
        "方差分量": f"组间 {fit.var_group:.4f}、组内 {fit.var_resid:.4f}",
        "组内相关（ICC）": (f"{fit.icc:.4f}"
                      + (f"（标准误 {fit.icc_se:.4f}）" if fit.icc_se == fit.icc_se
                         else "（标准误算不出来，不猜）")
                      + f"——{spec.group}之间的差异占总变异的 {fit.icc:.1%}"),
        "收敛": "是" if fit.converged else "**否**——结果不可信",
    }
    if group_text:
        diagnostics["二层单位数偏少"] = group_text
    # ── 门 M3：权重。**不假装能做**
    if plan.weights:
        diagnostics["抽样权重"] = (
            f"**未加权**（计划里写了 `{plan.weights}`）。多层模型的加权要 level-1 与 "
            f"level-2 两层权重，而这份数据只有一层——硬按一层跑出来的方差分量是错的，"
            f"**而它看起来是对的**。所以这一次按未加权算：**结论限于样本，不外推到总体**。"
            f"这一条要写进论文的局限")
    else:
        diagnostics["抽样权重"] = "未加权（计划里没写权重）"
    if dropped:
        diagnostics["因缺失丢弃"] = f"{dropped} 行（{dropped / max(before, 1):.1%}）"
    if plan.sample_filter:
        diagnostics["样本筛选"] = plan.sample_filter
    res = AnalysisResults(plan_id=plan.plan_id, estimates=estimates, n=n,
                          diagnostics=diagnostics)
    res.robustness = _run_robustness(frame, plan)
    return res
